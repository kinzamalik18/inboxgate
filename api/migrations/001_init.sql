-- InboxGate schema. Runs on startup and is safe to run more than once.

create table if not exists tickets (
    id              bigserial primary key,
    received_at     timestamptz not null default now(),
    from_name       text not null default '',
    from_email      text not null,
    subject         text not null default '',
    body            text not null,

    category        text not null,
    urgency         text not null,
    confidence      real not null,
    summary         text not null,
    route_to        text not null,
    lead            jsonb,
    draft_reply     text not null default '',
    review_notes    jsonb not null default '[]',
    model           text not null,

    -- pending_review, approved, sent, rejected, archived
    status          text not null,
    final_reply     text,
    reviewer_note   text,
    reviewed_at     timestamptz,
    -- n8n Wait node URL that resumes the workflow after review
    resume_url      text
);

create index if not exists tickets_status_idx on tickets (status, received_at desc);

-- Audit trail: one row per step, so anyone can see what the AI decided and who approved what.
create table if not exists events (
    id          bigserial primary key,
    ticket_id   bigint not null references tickets(id) on delete cascade,
    at          timestamptz not null default now(),
    actor       text not null,   -- ai, workflow, reviewer, system
    action      text not null,
    detail      text not null default ''
);

create index if not exists events_ticket_idx on events (ticket_id, at);

-- Demo transport: approved replies are stored here instead of being emailed.
create table if not exists outbox (
    id          bigserial primary key,
    ticket_id   bigint not null references tickets(id) on delete cascade,
    to_email    text not null,
    subject     text not null,
    body        text not null,
    sent_at     timestamptz not null default now(),
    via         text not null
);

-- A minimal CRM table that approved leads are added to.
create table if not exists leads (
    id          bigserial primary key,
    ticket_id   bigint not null unique references tickets(id) on delete cascade,
    name        text not null default '',
    email       text not null,
    company     text,
    services    jsonb not null default '[]',
    budget      text,
    timeline    text,
    score       int not null,
    created_at  timestamptz not null default now()
);
