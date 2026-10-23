import logging
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.db import Database, InvalidTransition, TicketNotFound
from app.llm import get_provider
from app.samples import load_samples
from app.triage import InboundEmail, triage_email
from app.workflow import describe_failure, resume_after_review, send_to_n8n

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("inboxgate")

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=HERE / "templates")

VIEWS = {
    "review": ("Needs review", ("pending_review",)),
    "sent": ("Sent", ("sent", "approved")),
    "archived": ("Archived as spam", ("archived",)),
    "rejected": ("Rejected", ("rejected",)),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.settings = settings
    app.state.db = Database(settings.database_url)
    app.state.db.migrate()
    app.state.llm = get_provider(settings)
    app.state.samples = {s.id: s for s in load_samples()}
    app.state.simulate_hits = defaultdict(deque)
    yield


app = FastAPI(title="InboxGate", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# Auth --------------------------------------------------------------------------

def require_workflow_token(x_workflow_token: str = Header(default=""), settings: Settings = Depends(get_settings)):
    if not settings.workflow_token:
        raise HTTPException(503, "WORKFLOW_TOKEN is not set.")
    if not secrets.compare_digest(x_workflow_token, settings.workflow_token):
        raise HTTPException(401, "Invalid workflow token.")


basic = HTTPBasic(auto_error=False)


def require_reviewer(credentials: HTTPBasicCredentials | None = Depends(basic), settings: Settings = Depends(get_settings)):
    if not settings.review_password:
        return  # open demo
    ok = credentials is not None and secrets.compare_digest(credentials.password, settings.review_password)
    if not ok:
        raise HTTPException(401, "Reviewer password required.", headers={"WWW-Authenticate": "Basic"})


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")


# Workflow API, called by n8n ------------------------------------------------------

class WaitingBody(BaseModel):
    resume_url: str


class EventBody(BaseModel):
    action: str
    detail: str = ""


workflow = [Depends(require_workflow_token)]


def triage_and_store(request: Request, email: InboundEmail) -> dict:
    s: Settings = request.app.state.settings
    llm = request.app.state.llm
    result = triage_email(email, llm)
    ticket = request.app.state.db.create_ticket(email, result, llm.model, s.auto_archive_confidence)
    return ticket


@app.post("/api/workflow/triage", dependencies=workflow)
def workflow_triage(email: InboundEmail, request: Request) -> dict:
    ticket = triage_and_store(request, email)
    return {
        "ticket_id": ticket["id"],
        "status": ticket["status"],
        "needs_review": ticket["status"] == "pending_review",
        "category": ticket["category"],
        "urgency": ticket["urgency"],
        "route_to": ticket["route_to"],
        "summary": ticket["summary"],
    }


@app.post("/api/workflow/tickets/{ticket_id}/waiting", dependencies=workflow)
def workflow_waiting(ticket_id: int, body: WaitingBody, request: Request) -> dict:
    try:
        request.app.state.db.set_resume_url(ticket_id, body.resume_url)
    except TicketNotFound:
        raise HTTPException(404, "Ticket not found.")
    return {"ok": True}


@app.post("/api/workflow/tickets/{ticket_id}/sent", dependencies=workflow)
def workflow_sent(ticket_id: int, request: Request) -> dict:
    try:
        ticket = request.app.state.db.record_sent(ticket_id, via="n8n")
    except TicketNotFound:
        raise HTTPException(404, "Ticket not found.")
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    return {"status": ticket["status"]}


@app.post("/api/workflow/tickets/{ticket_id}/lead", dependencies=workflow)
def workflow_lead(ticket_id: int, request: Request) -> dict:
    try:
        lead = request.app.state.db.add_lead(ticket_id)
    except TicketNotFound:
        raise HTTPException(404, "Ticket not found.")
    return {"added": lead is not None}


@app.post("/api/workflow/tickets/{ticket_id}/events", dependencies=workflow)
def workflow_event(ticket_id: int, body: EventBody, request: Request) -> dict:
    try:
        request.app.state.db.get_ticket(ticket_id)
    except TicketNotFound:
        raise HTTPException(404, "Ticket not found.")
    request.app.state.db.log_event(ticket_id, "workflow", body.action[:60], body.detail[:300])
    return {"ok": True}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# Review screens -------------------------------------------------------------------

reviewer = [Depends(require_reviewer)]


def render(request: Request, name: str, **context) -> HTMLResponse:
    s: Settings = request.app.state.settings
    context.update(
        counts=request.app.state.db.counts(),
        n8n_connected=bool(s.n8n_inbound_url),
        model=request.app.state.llm.model,
    )
    return templates.TemplateResponse(request, name, context)


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/inbox", status_code=303)


@app.get("/inbox", response_class=HTMLResponse, dependencies=reviewer)
def inbox(request: Request, view: str = "review", notice: str = "") -> HTMLResponse:
    title, statuses = VIEWS.get(view, VIEWS["review"])
    tickets = request.app.state.db.list_tickets(statuses)
    return render(
        request,
        "inbox.html",
        view=view if view in VIEWS else "review",
        title=title,
        tickets=tickets,
        samples=list(request.app.state.samples.values()),
        notice=notice,
    )


@app.get("/tickets/{ticket_id}", response_class=HTMLResponse, dependencies=reviewer)
def ticket_page(ticket_id: int, request: Request, notice: str = "") -> HTMLResponse:
    db: Database = request.app.state.db
    try:
        ticket = db.get_ticket(ticket_id)
    except TicketNotFound:
        raise HTTPException(404, "Ticket not found.")
    return render(request, "ticket.html", ticket=ticket, events=db.events(ticket_id), notice=notice)


def _decide(request: Request, ticket_id: int, decision: str, reply: str, note: str) -> RedirectResponse:
    db: Database = request.app.state.db
    try:
        ticket = db.approve(ticket_id, reply, note) if decision == "approved" else db.reject(ticket_id, note)
    except TicketNotFound:
        raise HTTPException(404, "Ticket not found.")
    except InvalidTransition as exc:
        return RedirectResponse(f"/tickets/{ticket_id}?notice={exc}", status_code=303)
    finished_by = resume_after_review(db, ticket, decision)
    messages = {
        "n8n": "Approved. The workflow is sending the reply." if decision == "approved" else "Rejected. The workflow was told to stop.",
        "direct": "Approved and sent.",
        "none": "Rejected. No reply will be sent.",
    }
    return RedirectResponse(f"/tickets/{ticket_id}?notice={messages[finished_by]}", status_code=303)


@app.post("/tickets/{ticket_id}/approve", dependencies=reviewer)
def approve(ticket_id: int, request: Request, reply: str = Form(...), note: str = Form("")) -> RedirectResponse:
    return _decide(request, ticket_id, "approved", reply, note)


@app.post("/tickets/{ticket_id}/reject", dependencies=reviewer)
def reject(ticket_id: int, request: Request, note: str = Form("")) -> RedirectResponse:
    return _decide(request, ticket_id, "rejected", "", note)


@app.get("/leads", response_class=HTMLResponse, dependencies=reviewer)
def leads(request: Request) -> HTMLResponse:
    return render(request, "leads.html", leads=request.app.state.db.leads())


@app.get("/outbox", response_class=HTMLResponse, dependencies=reviewer)
def outbox(request: Request) -> HTMLResponse:
    return render(request, "outbox.html", messages=request.app.state.db.outbox())


@app.post("/simulate", dependencies=reviewer)
def simulate(
    request: Request,
    sample_id: str = Form(""),
    from_name: str = Form(""),
    from_email: str = Form(""),
    subject: str = Form(""),
    body: str = Form(""),
) -> RedirectResponse:
    s: Settings = request.app.state.settings
    hits = request.app.state.simulate_hits[client_key(request)]
    now = time.monotonic()
    while hits and now - hits[0] > 60:
        hits.popleft()
    if len(hits) >= s.simulate_rate_limit:
        return RedirectResponse("/inbox?notice=That's a lot of emails in one minute. Please wait a moment.", status_code=303)
    hits.append(now)

    sample = request.app.state.samples.get(sample_id)
    try:
        email = InboundEmail(**sample.email_fields()) if sample else InboundEmail(
            from_name=from_name, from_email=from_email or "someone@example.com", subject=subject, body=body
        )
    except ValueError:
        return RedirectResponse("/inbox?notice=Write a message before sending it.", status_code=303)

    if s.n8n_inbound_url:
        try:
            send_to_n8n(s.n8n_inbound_url, email.model_dump(), s.workflow_token)
        except httpx.HTTPError as exc:
            log.warning("n8n inbound webhook failed: %s", exc)
            ticket = triage_and_store(request, email)
            request.app.state.db.log_event(ticket["id"], "system", "workflow_unreachable", f"n8n didn't respond ({describe_failure(exc)}), triaged by the app instead")
            return RedirectResponse(f"/tickets/{ticket['id']}", status_code=303)
        return RedirectResponse("/inbox?notice=Email handed to the n8n workflow. Refresh in a few seconds.", status_code=303)

    ticket = triage_and_store(request, email)
    return RedirectResponse(f"/tickets/{ticket['id']}", status_code=303)
