"""The dedicated Postgres has a server-wide statement_timeout (audit 2026-10-05 hardening "Role
timeouts"; SEC-002, docs/10 §15).

The app sets ``statement_timeout`` per transaction and the login roles carry their own defaults
(``infra/db/bootstrap.sql``, ``tests/security/test_role_timeouts.py``); the server-wide value is the
backstop for every other session on the host. ``sos_migrator`` is exempt (role setting 0) because
migrations build indexes; pg_dump sets its own 0.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
COMPOSE = REPO / "deploy" / "dedicated" / "compose.yaml"


def _postgres_settings() -> dict[str, str]:
    db = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["db"]
    command = [str(part) for part in db["command"]]
    assert command[0] == "postgres"
    settings: dict[str, str] = {}
    for flag, value in zip(command[1::2], command[2::2], strict=True):
        assert flag == "-c", flag
        key, _, val = value.partition("=")
        settings[key] = val
    return settings


def test_SEC_002_dedicated_postgres_has_a_server_statement_timeout() -> None:
    settings = _postgres_settings()
    assert settings.get("statement_timeout") == "300000"
    assert settings.get("idle_in_transaction_session_timeout") == "60000"
