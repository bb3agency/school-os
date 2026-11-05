"""The control plane CHECK translation answers like the D55 handler (D64; docs/09 §3).

``platform.repository.translate_db_error`` runs before the central handler (``common.db_errors``).
An unmapped CHECK refusal (SQLSTATE 23514) used to be a field-level 422 ``invalid`` whose
``field`` named the constraint; it is now 422 ``invalid_value`` on ``body`` with no constraint or
table name in the response, and the constraint name is logged at warning. The known mappings
(two-person, GSTIN, duplicates) are unchanged. No database needed.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError as SAIntegrityError

from app.core.config import Settings
from app.core.errors import Conflict, ValidationFailed, install_error_handlers
from app.core.logging import ALLOWED_FIELDS, clear_context, setup_logging
from app.core.middleware import install_middleware
from app.platform import common
from app.platform import repository as repo

_UNKNOWN = "subscriptions_seats_positive"
_SECRET = "Synthetica Vidyalaya"  # synthetic; must never reach the body or the logs


class _Diag:
    def __init__(self, constraint: str) -> None:
        self.constraint_name = constraint
        self.table_name = "subscriptions"
        self.schema_name = "platform"


def _error(cls: type[psycopg.Error], constraint: str) -> SAIntegrityError:
    class _WithDiag(cls):  # type: ignore[valid-type,misc]
        @property
        def diag(self) -> _Diag:
            return _Diag(constraint)

    orig = _WithDiag(f'new row for relation "subscriptions" violates "{constraint}"')
    return SAIntegrityError("UPDATE platform.subscriptions SET name = %(n)s", {"n": _SECRET}, orig)


@pytest.fixture
def log_lines() -> Iterator[io.StringIO]:
    stream = io.StringIO()
    setup_logging(Settings(env="ci"), stream=stream)
    clear_context()
    yield stream
    clear_context()
    setup_logging(Settings(env="ci"))


def _app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_middleware(app, Settings(env="ci"))

    @app.post("/api/v1/platform/check")
    def check() -> None:
        with common.db_errors():
            raise _error(psycopg.errors.CheckViolation, _UNKNOWN)

    @app.post("/api/v1/platform/gstin")
    def gstin() -> None:
        with common.db_errors():
            raise _error(psycopg.errors.CheckViolation, "billing_accounts_gstin_state")

    return app


def test_D64_an_unmapped_check_violation_is_422_invalid_value_without_the_constraint(
    log_lines: io.StringIO,
) -> None:
    res = TestClient(_app(), raise_server_exceptions=False).post("/api/v1/platform/check")
    assert res.status_code == 422, res.text
    assert res.headers["content-type"].startswith("application/problem+json")
    body = res.json()
    assert body["code"] == "invalid_value"
    assert body["errors"] == [
        {"field": "body", "code": "invalid_value", "message_key": "errors.invalid_value"}
    ]
    for leak in (_UNKNOWN, "subscriptions", "platform.", "UPDATE", _SECRET, "constraint"):
        assert leak not in res.text, leak


def test_D64_the_constraint_name_is_logged_at_warning_and_nothing_else(
    log_lines: io.StringIO,
) -> None:
    TestClient(_app(), raise_server_exceptions=False).post("/api/v1/platform/check")
    lines = [json.loads(x) for x in log_lines.getvalue().splitlines() if x.strip()]
    refusals = [line for line in lines if line["event"] == "db.check_violation"]
    assert len(refusals) == 1, lines
    line: dict[str, Any] = refusals[0]
    assert line["level"] == "WARN"
    assert line["constraint"] == _UNKNOWN
    assert line["error_code"] == "invalid_value"
    assert set(line) <= set(ALLOWED_FIELDS)
    text = log_lines.getvalue()
    for leak in (_SECRET, "UPDATE", "violates"):
        assert leak not in text, leak


def test_D64_known_check_mappings_are_kept() -> None:
    res = TestClient(_app(), raise_server_exceptions=False).post("/api/v1/platform/gstin")
    assert res.status_code == 422
    assert res.json()["errors"] == [
        {"field": "gstin", "code": "invalid", "message_key": "errors.gstin"}
    ]
    two_person = repo.translate_db_error(
        _error(psycopg.errors.CheckViolation, "deployments_offboard_two_person")
    )
    assert isinstance(two_person, Conflict)
    assert two_person.code == "same_operator"
    duplicate = repo.translate_db_error(
        _error(psycopg.errors.UniqueViolation, "plans_code_version_key")
    )
    assert isinstance(duplicate, Conflict)
    assert duplicate.code == "duplicate"


def test_D64_translate_db_error_returns_the_d55_shape_directly() -> None:
    mapped = repo.translate_db_error(_error(psycopg.errors.CheckViolation, ""))
    assert isinstance(mapped, ValidationFailed)
    assert mapped.status == 422
    assert mapped.code == "invalid_value"
    assert mapped.errors == [
        {"field": "body", "code": "invalid_value", "message_key": "errors.invalid_value"}
    ]
