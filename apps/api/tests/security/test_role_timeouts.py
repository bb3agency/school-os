"""Login roles carry their own timeouts (audit 2026-10-05 hardening "Role timeouts"; SEC-002).

``infra/db/bootstrap.sql`` sets them with ``ALTER ROLE ... SET``, so they apply on both tiers (RDS
and the dedicated Postgres) at login, whatever the server default:

- ``sos_readonly`` (people and reporting tools) had no limit at all: a forgotten query or an open
  transaction could hold locks and connections for ever.
- ``sos_app`` and ``sos_platform`` set ``statement_timeout`` per transaction (``core.db``); the role
  default is a backstop for any statement outside that path.
- ``sos_migrator`` runs long DDL (index builds), so it is exempt from the dedicated host's
  server-wide ``statement_timeout`` (``deploy/dedicated/compose.yaml``).
"""

from __future__ import annotations

from sqlalchemy import Engine, text


def _role_settings(admin_engine: Engine, role: str) -> dict[str, str]:
    with admin_engine.connect() as conn:
        config = conn.execute(
            text("SELECT rolconfig FROM pg_roles WHERE rolname = :r"), {"r": role}
        ).scalar_one()
    return dict(item.split("=", 1) for item in (config or []))


def test_SEC_002_readonly_role_has_statement_and_idle_timeouts(admin_engine: Engine) -> None:
    settings = _role_settings(admin_engine, "sos_readonly")
    assert settings.get("statement_timeout") == "60s"
    assert settings.get("idle_in_transaction_session_timeout") == "60s"
    assert settings.get("idle_session_timeout") == "30min"
    # Defence in depth on top of the SELECT-only grants.
    assert settings.get("default_transaction_read_only") == "on"


def test_SEC_002_app_roles_have_a_statement_timeout_backstop(admin_engine: Engine) -> None:
    for role in ("sos_app", "sos_platform"):
        settings = _role_settings(admin_engine, role)
        assert settings.get("statement_timeout") == "5min", role
        assert settings.get("idle_in_transaction_session_timeout") == "30s", role


def test_SEC_002_migrator_is_exempt_from_the_server_statement_timeout(admin_engine: Engine) -> None:
    assert _role_settings(admin_engine, "sos_migrator").get("statement_timeout") == "0"
