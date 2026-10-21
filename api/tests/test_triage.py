import json

import pytest

from app.llm.fake import FakeTriageProvider
from app.triage import InboundEmail, build_prompt, triage_email

EMAIL = InboundEmail(from_name="Priya", from_email="priya@example.com", subject="Redesign", body="We need a new Shopify site.")


class Scripted:
    name = "scripted"
    model = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def generate_json(self, system, prompt):
        self.prompts.append(prompt)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def valid(**overrides):
    data = {
        "category": "lead",
        "urgency": "normal",
        "confidence": 0.9,
        "summary": "Wants a Shopify redesign.",
        "contact_name": "Priya",
        "lead": {"company": "Loom", "services": ["Shopify"], "budget": "$6k", "timeline": "8 weeks", "score": 80, "score_reasons": ["Clear scope"]},
        "route_to": "sales",
        "draft_reply": "Hi Priya, thanks! [proposed call times]\n\nThe Kestrel Studio team",
        "review_notes": ["Add call times"],
    }
    data.update(overrides)
    return json.dumps(data)


def test_valid_output_is_parsed():
    result = triage_email(EMAIL, Scripted(valid()))
    assert result.category == "lead"
    assert result.lead.score == 80


def test_invalid_output_is_retried_with_the_error():
    llm = Scripted("not json", valid())
    result = triage_email(EMAIL, llm)
    assert result.category == "lead"
    assert "previous reply was invalid" in llm.prompts[1]


def test_two_invalid_outputs_fall_back_to_human_review():
    result = triage_email(EMAIL, Scripted('{"category": "banana"}', "[]"))
    assert result.category == "other"
    assert result.confidence == 0
    assert result.draft_reply == ""
    assert "invalid twice" in result.review_notes[0]


def test_api_failure_falls_back():
    result = triage_email(EMAIL, Scripted(RuntimeError("quota exceeded")))
    assert result.category == "other"
    assert "quota exceeded" in result.review_notes[0]


def test_spam_never_gets_a_draft_or_route():
    result = triage_email(EMAIL, Scripted(valid(category="spam", draft_reply="Buy now", route_to="sales")))
    assert result.draft_reply == ""
    assert result.route_to == "none"
    assert result.lead is None


def test_lead_details_dropped_for_non_leads():
    result = triage_email(EMAIL, Scripted(valid(category="support", route_to="support")))
    assert result.lead is None


def test_lead_without_details_gets_zero_score():
    result = triage_email(EMAIL, Scripted(valid(lead=None)))
    assert result.lead.score == 0


@pytest.mark.parametrize("score", [-1, 101])
def test_out_of_range_score_is_rejected(score):
    bad = json.loads(valid())
    bad["lead"]["score"] = score
    result = triage_email(EMAIL, Scripted(json.dumps(bad), json.dumps(bad)))
    assert result.category == "other"


def test_prompt_wraps_email_as_data():
    prompt = build_prompt(EMAIL)
    assert prompt.startswith("Triage this email.")
    assert "<email>" in prompt and "</email>" in prompt


def test_fake_provider_handles_samples():
    from app.samples import load_samples

    llm = FakeTriageProvider()
    for sample in load_samples():
        result = triage_email(InboundEmail(**sample.email_fields()), llm)
        assert result.category in {"lead", "support", "billing", "partnership", "spam", "other"}
