import os

import psycopg
import pytest

from app.db import Database

TEST_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/inboxgate_test")


@pytest.fixture
def db():
    try:
        with psycopg.connect(TEST_URL, connect_timeout=3) as conn:
            conn.execute("drop table if exists leads, outbox, events, tickets cascade")
    except psycopg.OperationalError as exc:
        pytest.skip(f"Postgres not available at TEST_DATABASE_URL: {exc}")
    database = Database(TEST_URL)
    database.migrate()
    return database
