"""Keyword rules that return the same JSON shape as the model.

Used by the tests and for running the app with no API key. It is not a model,
and its drafts are deliberately plain.
"""

import json
import re

RULES = [
    ("spam", ["guarantee", "10,000%", "passive income", "first page", "ignore all previous", "claim your"]),
    ("support", ["down", "error", "not sending", "broken", "update to", "add our", "bug"]),
    ("billing", ["invoice", "refund", "pay by card", "payment", "po number"]),
    ("partnership", ["partnership", "referral", "refer clients"]),
    ("lead", ["website", "redesign", "portal", "shopify", "build", "budget", "ads"]),
]
ROUTES = {"lead": "sales", "support": "support", "billing": "billing", "partnership": "sales"}


class FakeTriageProvider:
    name = "fake"
    model = "keyword-rules"

    def generate_json(self, system: str, prompt: str) -> str:
        match = re.search(r"<email>(.*)</email>", prompt, re.S)
        text = (match.group(1) if match else prompt).lower()
        category = next((cat for cat, words in RULES if any(w in text for w in words)), "other")
        urgent = any(w in text for w in ["urgent", "asap", "down"])
        lead = None
        if category == "lead":
            budget = re.search(r"\$[\d,]+(?:\s*(?:to|-)\s*\$?[\d,]+)?", text)
            lead = {
                "company": None,
                "services": [],
                "budget": budget.group(0) if budget else None,
                "timeline": None,
                "score": 70 if budget else 35,
                "score_reasons": ["Budget stated"] if budget else ["No budget or scope given"],
            }
        return json.dumps(
            {
                "category": category,
                "urgency": "high" if urgent else "normal",
                "confidence": 0.9 if category != "other" else 0.5,
                "summary": f"Email classified as {category} by keyword rules.",
                "contact_name": None,
                "lead": lead,
                "route_to": ROUTES.get(category, "none"),
                "draft_reply": "" if category == "spam" else "Hi,\n\nThanks for getting in touch. We'll reply properly soon.\n\nThe Kestrel Studio team",
                "review_notes": [],
            }
        )
