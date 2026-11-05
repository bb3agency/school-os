"""D55: a CHECK refusal from PostgreSQL itself (SQLSTATE 23514) the input checks missed.

The request gets 422 ``invalid_value`` (docs/09 §3), the transaction is rolled back with the
audit event written earlier in it (invariant 7: same transaction), and the warning line carries
the constraint name only. Synthetic values only (invariant 11).
"""

from __future__ import annotations

import io
import json
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.audit.service import record
from app.core.config import Settings
from app.core.db import tenant_session
from app.core.errors import install_error_handlers
from app.core.logging import clear_context, setup_logging
from app.core.middleware import install_middleware

pytestmark = pytest.mark.db

SCHEMA = "sos_test_check_violation"
CONSTRAINT = "marks_score_range"
SYNTHETIC_SCORE = 987654


@pytest.fixture(scope="module")
def marks_table(admin_engine: Engine, app_engine: Engine) -> Iterator[str]:
    """A throwaway tenant table with the standard policy and one CHECK constraint."""
    with admin_engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        conn.execute(text(f"GRANT USAGE ON SCHEMA {SCHEMA} TO sos_app"))
        conn.execute(
            text(
                f"CREATE TABLE {SCHEMA}.marks (id uuid PRIMARY KEY, tenant_id uuid NOT NULL, "
                f"score int NOT NULL CONSTRAINT {CONSTRAINT} CHECK (score BETWEEN 0 AND 100))"
            )
        )
        conn.execute(text(f"ALTER TABLE {SCHEMA}.marks ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {SCHEMA}.marks FORCE ROW LEVEL SECURITY"))
        conn.execute(
            text(
                f"CREATE POLICY tenant_isolation ON {SCHEMA}.marks "
                "USING (tenant_id = core.current_tenant()) "
                "WITH CHECK (tenant_id = core.current_tenant())"
            )
        )
        conn.execute(text(f"GRANT SELECT, INSERT ON {SCHEMA}.marks TO sos_app"))
    yield f"{SCHEMA}.marks"
    with admin_engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))


def _app(table: str, tenant: uuid.UUID) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app, Settings(env="ci"))

    @app.post("/api/v1/scores")
    def create_mark(score: int) -> dict[str, str]:
        with tenant_session(tenant) as s:
            record(
                s,
                action="marks.recorded",
                resource_type="marks",
                resource_id=uuid.uuid4(),
                summary={"fields": ["score"], "source": "manual_entry"},
                actor_type="system",
            )
            s.execute(
                text(f"INSERT INTO {table} (id, tenant_id, score) VALUES (:i, :t, :v)"),
                {"i": uuid.uuid4(), "t": tenant, "v": score},
            )
        return {"status": "created"}

    return app


def _counts(tenant: uuid.UUID, table: str) -> tuple[int, int]:
    with tenant_session(tenant) as s:
        rows: int = s.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()
        events: int = s.execute(
            text("SELECT count(*) FROM audit.events WHERE tenant_id = :t"), {"t": tenant}
        ).scalar_one()
    return int(rows), int(events)


def test_D55_a_database_check_refusal_is_422_and_rolls_back_the_audit_event(
    marks_table: str, app_engine: Engine
) -> None:
    tenant = uuid.uuid4()
    stream = io.StringIO()
    setup_logging(Settings(env="ci"), stream=stream)
    clear_context()
    try:
        client = TestClient(_app(marks_table, tenant), raise_server_exceptions=False)
        res = client.post("/api/v1/scores", params={"score": SYNTHETIC_SCORE})
    finally:
        clear_context()
        setup_logging(Settings(env="ci"))

    assert res.status_code == 422, res.text
    assert res.json()["code"] == "invalid_value"
    for leak in (CONSTRAINT, "marks", SCHEMA, str(SYNTHETIC_SCORE)):
        assert leak not in res.text, leak
    # Nothing from the failed transaction survives: no row, no audit event.
    assert _counts(tenant, marks_table) == (0, 0)

    lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
    refusals = [line for line in lines if line["event"] == "db.check_violation"]
    assert len(refusals) == 1, lines
    assert refusals[0]["level"] == "WARN"
    assert refusals[0]["constraint"] == CONSTRAINT
    assert str(SYNTHETIC_SCORE) not in stream.getvalue()
    assert "INSERT" not in stream.getvalue()


def test_D55_an_allowed_value_still_commits_with_its_audit_event(
    marks_table: str, app_engine: Engine
) -> None:
    tenant = uuid.uuid4()
    res = TestClient(_app(marks_table, tenant)).post("/api/v1/scores", params={"score": 42})
    assert res.status_code == 200, res.text
    assert _counts(tenant, marks_table) == (1, 1)
