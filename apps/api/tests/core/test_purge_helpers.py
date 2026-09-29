"""Offboarding purge helpers run constant SQL only (SEC-001, FR-PLT-005, ADR-0029; docs/13 §4).

``purge_role`` switches to ``sos_purger`` with a constant statement and back; ``defer_constraint``
checks the name and sends it as quoted identifiers (psycopg composition), so nothing from the
caller is ever interpolated into SQL text.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from sqlalchemy import text

from app.core import purge
from app.core.db import tenant_session

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("app_engine")]

TENANT = uuid.UUID("0192f3a4-0000-7000-8000-00000000e501")
# The deferrable key the offboarding purge defers (app/tenancy/offboarding.yaml, migration 0032).
DEFERRABLE = "sis.attribute_values_change_request_fk"


def test_SEC_001_purge_role_switches_to_sos_purger_and_back() -> None:
    with tenant_session(TENANT) as s:
        with purge.purge_role(s, TENANT, flag="app.purge_tenant"):
            assert s.execute(text("SELECT current_user")).scalar_one() == purge.PURGE_ROLE
            flag: str = s.execute(text("SELECT current_setting('app.purge_tenant')")).scalar_one()
            assert flag == str(TENANT)
        assert s.execute(text("SELECT current_user")).scalar_one() == "sos_app"
        s.rollback()


def test_SEC_001_defer_constraint_defers_a_deferrable_key() -> None:
    with tenant_session(TENANT) as s:
        purge.defer_constraint(s, DEFERRABLE)
        # Same transaction, same connection: the session is still usable afterwards.
        assert s.execute(text("SELECT 1")).scalar_one() == 1
        s.rollback()


def test_SEC_001_defer_constraint_quotes_the_name_it_sends() -> None:
    # Well formed but unknown: the database is asked for exactly that (quoted) name.
    with (
        tenant_session(TENANT) as s,
        pytest.raises(psycopg.errors.UndefinedObject, match="does not exist"),
    ):
        purge.defer_constraint(s, "sis.no_such_constraint_fk")


@pytest.mark.parametrize(
    "name",
    [
        "attribute_values_change_request_fk",  # not schema-qualified
        "platform.plans_pkey",  # not a tenant schema
        "sis.x; RESET ROLE",
        'sis."quoted"',
        "sis.Upper_case",
        "sis.x DEFERRED; SET CONSTRAINTS ALL",
    ],
)
def test_SEC_001_defer_constraint_refuses_names_that_are_not_identifiers(name: str) -> None:
    with pytest.raises(ValueError, match="not a constraint name"):
        purge.defer_constraint(None, name)  # type: ignore[arg-type]  # refused before any SQL
