"""All database access. Plain SQL with psycopg, one short connection per call."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.triage import InboundEmail, Triage

MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"

OPEN_STATUSES = ("pending_review",)


class TicketNotFound(LookupError):
    pass


class InvalidTransition(ValueError):
    pass


class Database:
    def __init__(self, url: str) -> None:
        self.url = url

    @contextmanager
    def connect(self):
        # prepare_threshold=None keeps this working behind Supabase's pooler.
        with psycopg.connect(self.url, row_factory=dict_row, prepare_threshold=None) as conn:
            yield conn

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.execute(MIGRATION.read_text())

    # Writes -------------------------------------------------------------------

    def _event(self, conn, ticket_id: int, actor: str, action: str, detail: str = "") -> None:
        conn.execute(
            "insert into events (ticket_id, actor, action, detail) values (%s, %s, %s, %s)",
            (ticket_id, actor, action, detail),
        )

    def create_ticket(self, email: InboundEmail, triage: Triage, model: str, auto_archive_confidence: float) -> dict:
        archive = triage.category == "spam" and triage.confidence >= auto_archive_confidence
        status = "archived" if archive else "pending_review"
        with self.connect() as conn, conn.transaction():
            row = conn.execute(
                """insert into tickets (from_name, from_email, subject, body, category, urgency, confidence,
                       summary, route_to, lead, draft_reply, review_notes, model, status)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   returning *""",
                (
                    email.from_name,
                    email.from_email,
                    email.subject,
                    email.body,
                    triage.category,
                    triage.urgency,
                    triage.confidence,
                    triage.summary,
                    triage.route_to,
                    Jsonb(triage.lead.model_dump()) if triage.lead else None,
                    triage.draft_reply,
                    Jsonb(triage.review_notes),
                    model,
                    status,
                ),
            ).fetchone()
            self._event(conn, row["id"], "system", "received", f"From {email.from_email}")
            self._event(
                conn,
                row["id"],
                "ai",
                "triaged",
                f"{triage.category.capitalize()}, {triage.urgency} urgency, {triage.confidence:.0%} confidence, route to {triage.route_to}",
            )
            if archive:
                self._event(conn, row["id"], "system", "archived", "Spam with high confidence, no review needed")
        return row

    def set_resume_url(self, ticket_id: int, url: str) -> None:
        with self.connect() as conn, conn.transaction():
            result = conn.execute("update tickets set resume_url = %s where id = %s", (url, ticket_id))
            if result.rowcount == 0:
                raise TicketNotFound(ticket_id)
            self._event(conn, ticket_id, "workflow", "waiting_for_review", "Workflow paused until a person decides")

    def _review(self, ticket_id: int, new_status: str, final_reply: str | None, note: str) -> dict:
        with self.connect() as conn, conn.transaction():
            row = conn.execute("select * from tickets where id = %s for update", (ticket_id,)).fetchone()
            if row is None:
                raise TicketNotFound(ticket_id)
            if row["status"] not in OPEN_STATUSES:
                raise InvalidTransition(f"Ticket is already {row['status']}")
            row = conn.execute(
                """update tickets set status = %s, final_reply = %s, reviewer_note = %s, reviewed_at = now()
                   where id = %s returning *""",
                (new_status, final_reply, note or None, ticket_id),
            ).fetchone()
            if new_status == "approved":
                edited = final_reply.strip() != row["draft_reply"].strip()
                self._event(conn, ticket_id, "reviewer", "approved", "Reply edited before approval" if edited else "Draft approved as written")
            else:
                self._event(conn, ticket_id, "reviewer", "rejected", note or "No reply will be sent")
        return row

    def approve(self, ticket_id: int, final_reply: str, note: str = "") -> dict:
        if not final_reply.strip():
            raise InvalidTransition("The reply is empty")
        return self._review(ticket_id, "approved", final_reply, note)

    def reject(self, ticket_id: int, note: str = "") -> dict:
        return self._review(ticket_id, "rejected", None, note)

    def record_sent(self, ticket_id: int, via: str) -> dict:
        with self.connect() as conn, conn.transaction():
            row = conn.execute("select * from tickets where id = %s for update", (ticket_id,)).fetchone()
            if row is None:
                raise TicketNotFound(ticket_id)
            if row["status"] == "sent":
                return row  # n8n may retry; sending twice must not duplicate
            if row["status"] != "approved":
                raise InvalidTransition(f"Can't send a ticket that is {row['status']}")
            subject = row["subject"] if row["subject"].lower().startswith("re:") else f"Re: {row['subject']}"
            conn.execute(
                "insert into outbox (ticket_id, to_email, subject, body, via) values (%s, %s, %s, %s, %s)",
                (ticket_id, row["from_email"], subject, row["final_reply"], via),
            )
            row = conn.execute("update tickets set status = 'sent' where id = %s returning *", (ticket_id,)).fetchone()
            self._event(conn, ticket_id, "workflow" if via == "n8n" else "system", "sent", f"Reply sent to {row['from_email']} via {via}")
        return row

    def add_lead(self, ticket_id: int) -> dict | None:
        with self.connect() as conn, conn.transaction():
            row = conn.execute("select * from tickets where id = %s", (ticket_id,)).fetchone()
            if row is None:
                raise TicketNotFound(ticket_id)
            if row["category"] != "lead" or row["status"] not in ("approved", "sent"):
                return None
            lead = row["lead"] or {}
            created = conn.execute(
                """insert into leads (ticket_id, name, email, company, services, budget, timeline, score)
                   values (%s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (ticket_id) do nothing returning *""",
                (
                    ticket_id,
                    row["from_name"],
                    row["from_email"],
                    lead.get("company"),
                    Jsonb(lead.get("services") or []),
                    lead.get("budget"),
                    lead.get("timeline"),
                    lead.get("score") or 0,
                ),
            ).fetchone()
            if created:
                self._event(conn, ticket_id, "workflow", "added_to_crm", f"Lead score {created['score']}")
        return created

    def log_event(self, ticket_id: int, actor: str, action: str, detail: str = "") -> None:
        with self.connect() as conn:
            self._event(conn, ticket_id, actor, action, detail)

    # Reads --------------------------------------------------------------------

    def get_ticket(self, ticket_id: int) -> dict:
        with self.connect() as conn:
            row = conn.execute("select * from tickets where id = %s", (ticket_id,)).fetchone()
        if row is None:
            raise TicketNotFound(ticket_id)
        return row

    def list_tickets(self, statuses: tuple[str, ...], limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            return conn.execute(
                """select id, received_at, from_name, from_email, subject, category, urgency, summary, status, lead
                   from tickets where status = any(%s)
                   order by (urgency = 'high') desc, received_at desc limit %s""",
                (list(statuses), limit),
            ).fetchall()

    def counts(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute("select status, count(*) as n from tickets group by status").fetchall()
            leads = conn.execute("select count(*) as n from leads").fetchone()["n"]
        result = {r["status"]: r["n"] for r in rows}
        result["leads"] = leads
        return result

    def events(self, ticket_id: int) -> list[dict]:
        with self.connect() as conn:
            return conn.execute("select * from events where ticket_id = %s order by at, id", (ticket_id,)).fetchall()

    def outbox(self, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            return conn.execute("select * from outbox order by sent_at desc limit %s", (limit,)).fetchall()

    def leads(self, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            return conn.execute("select * from leads order by score desc, created_at desc limit %s", (limit,)).fetchall()
