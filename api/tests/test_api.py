import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.conftest import TEST_URL

TOKEN = {"X-Workflow-Token": "wf-secret"}
EMAIL = {
    "from_name": "Priya",
    "from_email": "priya@example.com",
    "subject": "Shopify redesign",
    "body": "We need a Shopify redesign, budget $6,000.",
}


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "fake")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("WORKFLOW_TOKEN", "wf-secret")
    monkeypatch.setenv("N8N_INBOUND_URL", "")
    monkeypatch.setenv("REVIEW_PASSWORD", "")
    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def test_workflow_endpoints_need_the_token(client):
    assert client.post("/api/workflow/triage", json=EMAIL).status_code == 401
    assert client.post("/api/workflow/triage", json=EMAIL, headers={"X-Workflow-Token": "nope"}).status_code == 401


def test_workflow_triage_creates_a_ticket(client):
    body = client.post("/api/workflow/triage", json=EMAIL, headers=TOKEN).json()
    assert body["category"] == "lead"
    assert body["needs_review"] is True
    assert body["route_to"] == "sales"


def test_full_n8n_path(client, monkeypatch):
    ticket_id = client.post("/api/workflow/triage", json=EMAIL, headers=TOKEN).json()["ticket_id"]
    assert client.post(f"/api/workflow/tickets/{ticket_id}/waiting", json={"resume_url": "http://n8n/resume/1"}, headers=TOKEN).status_code == 200

    calls = []
    monkeypatch.setattr("app.workflow.httpx.post", lambda url, **kw: calls.append((url, kw["json"])) or httpx.Response(200, request=httpx.Request("POST", url)))
    response = client.post(f"/tickets/{ticket_id}/approve", data={"reply": "Hi Priya, happy to help."}, follow_redirects=False)
    assert response.status_code == 303
    assert "workflow is sending" in response.headers["location"].replace("%20", " ")
    assert calls == [("http://n8n/resume/1", {"decision": "approved", "ticket_id": ticket_id})]

    # n8n then reports back.
    assert client.post(f"/api/workflow/tickets/{ticket_id}/sent", headers=TOKEN).json() == {"status": "sent"}
    assert client.post(f"/api/workflow/tickets/{ticket_id}/lead", headers=TOKEN).json() == {"added": True}
    assert "Hi Priya" in client.get("/outbox").text
    assert ">Priya</a>" in client.get("/leads").text


def test_unreachable_n8n_falls_back_to_direct_send(client, monkeypatch):
    ticket_id = client.post("/api/workflow/triage", json=EMAIL, headers=TOKEN).json()["ticket_id"]
    client.post(f"/api/workflow/tickets/{ticket_id}/waiting", json={"resume_url": "http://n8n/resume/2"}, headers=TOKEN)

    def boom(url, **kw):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("app.workflow.httpx.post", boom)
    client.post(f"/tickets/{ticket_id}/approve", data={"reply": "Hi"})
    page = client.get(f"/tickets/{ticket_id}").text
    assert "n8n didn&#39;t respond (ConnectError)" in page or "n8n didn't respond (ConnectError)" in page
    assert client.post(f"/api/workflow/tickets/{ticket_id}/sent", headers=TOKEN).json() == {"status": "sent"}


def test_failure_reasons_are_short_and_specific():
    from app.workflow import describe_failure

    request = httpx.Request("POST", "http://n8n/webhook")
    not_found = httpx.HTTPStatusError("nope", request=request, response=httpx.Response(404, request=request))
    assert describe_failure(not_found) == "HTTP 404"
    assert describe_failure(httpx.ReadTimeout("slow", request=request)) == "timed out"
    assert describe_failure(httpx.ConnectError("down", request=request)) == "ConnectError"


def test_direct_mode_simulate_and_approve(client):
    response = client.post("/simulate", data={"sample_id": "support-outage"}, follow_redirects=False)
    assert response.status_code == 303
    ticket_url = response.headers["location"]
    page = client.get(ticket_url).text
    assert "URGENT our website is down" in page

    ticket_id = int(ticket_url.rsplit("/", 1)[1])
    client.post(f"/tickets/{ticket_id}/approve", data={"reply": "We're on it."})
    assert "We&#39;re on it." in client.get("/outbox").text


def test_reject_sends_nothing(client):
    ticket_id = client.post("/api/workflow/triage", json=EMAIL, headers=TOKEN).json()["ticket_id"]
    client.post(f"/tickets/{ticket_id}/reject", data={"note": "Not a fit"})
    assert "Nothing sent yet" in client.get("/outbox").text
    assert client.post(f"/tickets/{ticket_id}/sent".replace("/tickets", "/api/workflow/tickets"), headers=TOKEN).status_code == 409


def test_custom_email_needs_a_body(client):
    response = client.post("/simulate", data={"body": ""}, follow_redirects=False)
    assert "notice=" in response.headers["location"]


def test_simulate_is_rate_limited(client, monkeypatch):
    statuses = [client.post("/simulate", data={"sample_id": "spam-crypto"}, follow_redirects=False).headers["location"] for _ in range(7)]
    assert "notice=" in statuses[-1]


def test_pages_render(client):
    for path in ["/inbox", "/inbox?view=sent", "/inbox?view=archived", "/inbox?view=rejected", "/leads", "/outbox"]:
        assert client.get(path).status_code == 200, path


def test_review_password(client, monkeypatch):
    monkeypatch.setenv("REVIEW_PASSWORD", "letmein")
    get_settings.cache_clear()
    assert client.get("/inbox").status_code == 401
    assert client.get("/inbox", auth=("reviewer", "letmein")).status_code == 200
