"""Talking to n8n, and the fallback when n8n isn't there.

n8n owns the workflow: receive email, triage, route, wait for review, send,
update the CRM. This module covers the two places the app calls n8n: handing
over a new email, and resuming a paused workflow after a person decides.

If n8n isn't configured, or can't be reached when a reviewer approves, the app
finishes the job itself so an approved reply is never stuck.
"""

import logging

import httpx

from app.db import Database

log = logging.getLogger(__name__)


def send_to_n8n(url: str, email: dict, token: str) -> None:
    response = httpx.post(url, json=email, headers={"X-Workflow-Token": token}, timeout=15)
    response.raise_for_status()


def finish_directly(db: Database, ticket_id: int) -> None:
    """Do what the n8n workflow would do after approval."""
    db.record_sent(ticket_id, via="direct")
    db.add_lead(ticket_id)


def resume_after_review(db: Database, ticket: dict, decision: str) -> str:
    """Tell n8n the decision. Returns how the ticket was finished: n8n, direct or none."""
    url = ticket.get("resume_url")
    if url:
        try:
            response = httpx.post(url, json={"decision": decision, "ticket_id": ticket["id"]}, timeout=15)
            response.raise_for_status()
            return "n8n"
        except httpx.HTTPError as exc:
            log.warning("Could not resume n8n for ticket %s: %s", ticket["id"], exc)
            db.log_event(ticket["id"], "system", "workflow_unreachable", "n8n didn't respond, finished by the app instead")

    if decision == "approved":
        finish_directly(db, ticket["id"])
        return "direct"
    return "none"
