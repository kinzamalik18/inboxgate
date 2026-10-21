import pytest

from app.db import InvalidTransition, TicketNotFound
from app.triage import InboundEmail, LeadDetails, Triage

EMAIL = InboundEmail(from_name="Priya", from_email="priya@example.com", subject="Shop redesign", body="Hello")


def triage(**overrides) -> Triage:
    data = dict(
        category="lead",
        urgency="normal",
        confidence=0.9,
        summary="Wants a redesign",
        route_to="sales",
        lead=LeadDetails(company="Loom", services=["Shopify"], budget="$6k", score=80),
        draft_reply="Hi Priya",
    )
    data.update(overrides)
    return Triage(**data)


def test_new_ticket_waits_for_review(db):
    ticket = db.create_ticket(EMAIL, triage(), "test-model", 0.85)
    assert ticket["status"] == "pending_review"
    assert [e["action"] for e in db.events(ticket["id"])] == ["received", "triaged"]


def test_confident_spam_is_archived_without_review(db):
    ticket = db.create_ticket(EMAIL, triage(category="spam", lead=None, route_to="none", draft_reply=""), "m", 0.85)
    assert ticket["status"] == "archived"


def test_unsure_spam_still_goes_to_review(db):
    ticket = db.create_ticket(EMAIL, triage(category="spam", confidence=0.6, lead=None, route_to="none"), "m", 0.85)
    assert ticket["status"] == "pending_review"


def test_approve_send_and_add_lead(db):
    ticket = db.create_ticket(EMAIL, triage(), "m", 0.85)
    db.set_resume_url(ticket["id"], "http://n8n/resume/1")
    approved = db.approve(ticket["id"], "Hi Priya, edited")
    assert approved["status"] == "approved"

    sent = db.record_sent(ticket["id"], via="n8n")
    assert sent["status"] == "sent"
    assert db.outbox()[0]["subject"] == "Re: Shop redesign"

    lead = db.add_lead(ticket["id"])
    assert lead["score"] == 80 and lead["company"] == "Loom"

    actions = [e["action"] for e in db.events(ticket["id"])]
    assert actions == ["received", "triaged", "waiting_for_review", "approved", "sent", "added_to_crm"]
    assert "edited" in db.events(ticket["id"])[3]["detail"]


def test_sending_twice_does_not_duplicate(db):
    ticket = db.create_ticket(EMAIL, triage(), "m", 0.85)
    db.approve(ticket["id"], "Hi")
    db.record_sent(ticket["id"], via="n8n")
    db.record_sent(ticket["id"], via="n8n")
    assert len(db.outbox()) == 1
    db.add_lead(ticket["id"])
    assert db.add_lead(ticket["id"]) is None
    assert len(db.leads()) == 1


def test_cannot_review_twice_or_send_unapproved(db):
    ticket = db.create_ticket(EMAIL, triage(), "m", 0.85)
    with pytest.raises(InvalidTransition):
        db.record_sent(ticket["id"], via="n8n")
    db.reject(ticket["id"], "Not a fit")
    with pytest.raises(InvalidTransition):
        db.approve(ticket["id"], "Hi")


def test_empty_reply_cannot_be_approved(db):
    ticket = db.create_ticket(EMAIL, triage(), "m", 0.85)
    with pytest.raises(InvalidTransition):
        db.approve(ticket["id"], "   ")


def test_non_leads_are_not_added_to_crm(db):
    ticket = db.create_ticket(EMAIL, triage(category="support", lead=None, route_to="support"), "m", 0.85)
    db.approve(ticket["id"], "Hi")
    assert db.add_lead(ticket["id"]) is None


def test_missing_ticket(db):
    with pytest.raises(TicketNotFound):
        db.get_ticket(999)


def test_urgent_tickets_list_first(db):
    db.create_ticket(EMAIL, triage(), "m", 0.85)
    urgent = db.create_ticket(EMAIL, triage(category="support", urgency="high", lead=None, route_to="support"), "m", 0.85)
    assert db.list_tickets(("pending_review",))[0]["id"] == urgent["id"]
    assert db.counts()["pending_review"] == 2
