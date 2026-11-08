"""Breach register retention (lead decision D58; ADR-0050, PRV-036; FR-DPDP-034; migration
0075_breach_retention). Synthetic data only.

A breach closed more than ``retention.closed_years`` (3) ago is deleted by the nightly
``privacy.breach_purge`` with its affected list and sent reminders, per school in its own
``tenant_session`` (active, suspended and offboarding schools). An open, contained or reopened
breach is never deleted. One audit event per school per run with the counts. The database lets
``sos_app`` delete only expired closed breaches and their reminders.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url

from app.core.db import tenant_session
from app.core.subjects import Subject
from app.privacy import breach_repository as repo
from app.privacy import breach_service, breach_subjects, breach_tasks
from app.privacy.breach_config import load_config, parse_config
from app.tenancy import service as tenancy

REVISION = "0075_breach_retention"
PREVIOUS = "0074_tenant_audit_retention"
DB = "schoolos_breach_retention"
ACTION = "privacy.breach.retention_purged"
W = sys.modules["sos_test_api_world"]
CP = sys.modules["sos_test_consent_support"]


@pytest.fixture
def engines(app_engine: Engine, platform_engine: Engine) -> None:
    """Bind core.db engines."""


def _tenant(admin: Engine, status: str = "active") -> uuid.UUID:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text("INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'S', :s)"),
            {"i": tid, "c": f"t-{uuid.uuid4().hex[:12]}", "s": status},
        )
    return tid


def _breach(
    admin: Engine,
    tenant: uuid.UUID,
    *,
    closed_days_ago: int | None,
    status: str | None = None,
    reopened_days_ago: int | None = None,
    reminders: bool = True,
) -> uuid.UUID:
    """A synthetic SchoolOS notice breach (no author needed); ``closed_days_ago=None`` = not
    closed. ``reopened_days_ago`` records a SchoolOS reopen. Optionally both reminders sent."""
    bid = uuid.uuid4()
    state = status or ("closed" if closed_days_ago is not None else "open")
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.breach_records (id, tenant_id, origin, platform_incident_id, "
                "noticed_at, categories, status, close_reason, closed_at, platform_reopen_seq, "
                "platform_reopened_at) VALUES (:i, :t, 'processor_notice', :p, "
                "now() - interval '2000 days', ARRAY['attendance'], :s, "
                "'Duties not needed for this synthetic case.', "
                "CASE WHEN CAST(:d AS int) IS NULL THEN NULL "
                "ELSE now() - make_interval(days => CAST(:d AS int)) END, "
                "CASE WHEN CAST(:r AS int) IS NULL THEN 0 ELSE 1 END, "
                "CASE WHEN CAST(:r AS int) IS NULL THEN NULL "
                "ELSE now() - make_interval(days => CAST(:r AS int)) END)"
            ),
            {
                "i": bid,
                "t": tenant,
                "p": uuid.uuid4(),
                "s": state,
                "d": closed_days_ago,
                "r": reopened_days_ago,
            },
        )
        if reminders:
            for key in ("board_24h", "report_60h"):
                c.execute(
                    text(
                        "INSERT INTO ops.breach_reminders (id, tenant_id, breach_id, reminder) "
                        "VALUES (:i, :t, :b, :k)"
                    ),
                    {"i": uuid.uuid4(), "t": tenant, "b": bid, "k": key},
                )
    return bid


def _affected(admin: Engine, tenant: uuid.UUID, breach: uuid.UUID, student: uuid.UUID) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.breach_affected (id, tenant_id, breach_id, student_id) "
                "VALUES (:i, :t, :b, :s)"
            ),
            {"i": uuid.uuid4(), "t": tenant, "b": breach, "s": student},
        )


def _ids(admin: Engine, tenant: uuid.UUID) -> set[uuid.UUID]:
    with admin.connect() as c:
        return set(
            c.execute(
                text("SELECT id FROM ops.breach_records WHERE tenant_id = :t"), {"t": tenant}
            ).scalars()
        )


def _children(admin: Engine, breach: uuid.UUID) -> tuple[int, int]:
    with admin.connect() as c:
        reminders = int(
            c.execute(
                text("SELECT count(*) FROM ops.breach_reminders WHERE breach_id = :b"),
                {"b": breach},
            ).scalar_one()
        )
        affected = int(
            c.execute(
                text("SELECT count(*) FROM ops.breach_affected WHERE breach_id = :b"),
                {"b": breach},
            ).scalar_one()
        )
    return reminders, affected


# --- configuration ------------------------------------------------------------------------------


def test_D58_retention_period_comes_from_versioned_config() -> None:
    cfg = load_config()
    assert cfg.retention.closed_years == 3
    raw = cfg.model_dump()
    raw["retention"] = {"closed_years": 2}
    with pytest.raises(ValueError, match="greater than or equal to 3"):
        parse_config(raw)  # never below the database policy's floor


def test_D58_nightly_beat_entry() -> None:
    entry = breach_tasks.beat_schedule()["privacy-breach-purge"]
    assert entry["task"] == "privacy.breach_purge"
    assert breach_tasks.PURGE_TENANT_STATUSES == ("active", "suspended", "offboarding")


# --- the purge ----------------------------------------------------------------------------------


@pytest.mark.db
def test_FR_DPDP_034_D58_only_breaches_closed_over_3_years_ago_are_deleted_with_children(
    admin_engine: Engine, engines: None
) -> None:
    tenant, other = _tenant(admin_engine), _tenant(admin_engine)
    expired = _breach(admin_engine, tenant, closed_days_ago=3 * 366 + 2)
    recent = _breach(admin_engine, tenant, closed_days_ago=3 * 365 - 5)
    still_open = _breach(admin_engine, tenant, closed_days_ago=None)
    contained = _breach(admin_engine, tenant, closed_days_ago=None, status="contained")
    # SchoolOS reopened a breach the school had closed 5 years ago: open again, closed_at cleared.
    reopened = _breach(admin_engine, tenant, closed_days_ago=None, reopened_days_ago=1500)
    # Reopened 4 years ago and closed again last year: the clock restarted at the new closing.
    reclosed = _breach(admin_engine, tenant, closed_days_ago=365, reopened_days_ago=1460)
    other_expired = _breach(admin_engine, other, closed_days_ago=4 * 365)

    with tenant_session(tenant) as s:
        assert breach_service.purge_expired(s) == 1

    assert _ids(admin_engine, tenant) == {recent, still_open, contained, reopened, reclosed}
    assert _children(admin_engine, expired) == (0, 0)
    assert _children(admin_engine, recent) == (2, 0)
    assert _ids(admin_engine, other) == {other_expired}  # other school untouched
    assert _children(admin_engine, other_expired) == (2, 0)

    events = W.audit_events(admin_engine, tenant, ACTION)
    assert len(events) == 1
    assert events[0]["resource_type"] == "tenant"
    assert events[0]["resource_id"] == tenant
    assert events[0]["actor_id"] is None
    assert events[0]["summary"] == {
        "breaches": 1,
        "affected": 0,
        "reminders": 2,
        "closed_years": 3,
    }
    assert W.audit_events(admin_engine, other, ACTION) == []

    # Nothing left to delete: no second event.
    with tenant_session(tenant) as s:
        assert breach_service.purge_expired(s) == 0
    assert len(W.audit_events(admin_engine, tenant, ACTION)) == 1


@pytest.mark.db
def test_D58_nightly_run_covers_active_suspended_and_offboarding_schools(
    admin_engine: Engine, engines: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    schools = {
        status: _tenant(admin_engine, status) for status in ("active", "suspended", "offboarding")
    }
    kept = {s: _breach(admin_engine, t, closed_days_ago=None) for s, t in schools.items()}
    for t in schools.values():
        _breach(admin_engine, t, closed_days_ago=4 * 365)
    asked: list[Any] = []
    real = tenancy.list_tenant_ids

    def listing(session: Any, statuses: Any) -> list[uuid.UUID]:
        asked.append(statuses)
        return [t for t in real(session, statuses) if t in schools.values()]

    monkeypatch.setattr("app.tenancy.service.list_tenant_ids", listing)
    assert breach_tasks.purge.run() == {"tenants": 3, "purged": 3, "failed": 0}
    assert asked == [("active", "suspended", "offboarding")]
    for status, t in schools.items():
        assert _ids(admin_engine, t) == {kept[status]}
        assert len(W.audit_events(admin_engine, t, ACTION)) == 1


@pytest.mark.db
def test_D58_a_failing_school_does_not_stop_the_others(
    admin_engine: Engine, engines: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _tenant(admin_engine), _tenant(admin_engine)
    first_expired = _breach(admin_engine, first, closed_days_ago=4 * 365)
    _breach(admin_engine, second, closed_days_ago=4 * 365)
    monkeypatch.setattr("app.tenancy.service.list_tenant_ids", lambda s, statuses: [first, second])
    real = repo.purge_closed
    seen: list[uuid.UUID] = []

    def purge(session: Any, *, years: int) -> dict[str, int]:
        tid = repo.current_tenant_id(session)
        seen.append(tid)
        result = real(session, years=years)
        if tid == first:
            raise RuntimeError("boom")  # after deleting: the whole school rolls back
        return result

    monkeypatch.setattr(repo, "purge_closed", purge)
    assert breach_tasks.purge_all() == {"tenants": 2, "purged": 1, "failed": 1}
    assert seen == [first, second]
    assert _ids(admin_engine, first) == {first_expired}  # rolled back, due again next night
    assert _children(admin_engine, first_expired) == (2, 0)
    assert W.audit_events(admin_engine, first, ACTION) == []
    assert _ids(admin_engine, second) == set()


@pytest.mark.db
def test_D58_app_role_cannot_delete_open_or_recent_breaches(
    admin_engine: Engine, engines: None
) -> None:
    """Defence in depth (0075 restrictive policies): even unfiltered DELETEs as ``sos_app``
    remove only expired closed breaches and their reminders of the current school; a closed
    breach's affected list is frozen until it expires."""
    tenant, other = _tenant(admin_engine), _tenant(admin_engine)
    expired = _breach(admin_engine, tenant, closed_days_ago=4 * 365)
    recent = _breach(admin_engine, tenant, closed_days_ago=30)
    still_open = _breach(admin_engine, tenant, closed_days_ago=None)
    reopened = _breach(admin_engine, tenant, closed_days_ago=None, reopened_days_ago=1500)
    other_expired = _breach(admin_engine, other, closed_days_ago=4 * 365)
    with tenant_session(tenant) as s:
        s.execute(text("DELETE FROM ops.breach_reminders"))
        s.execute(text("DELETE FROM ops.breach_records"))
    assert _ids(admin_engine, tenant) == {recent, still_open, reopened}
    assert _children(admin_engine, recent) == (2, 0)
    assert _children(admin_engine, still_open) == (2, 0)
    assert expired not in _ids(admin_engine, tenant)
    assert _ids(admin_engine, other) == {other_expired}
    assert _children(admin_engine, other_expired) == (2, 0)


def _closed_at(admin: Engine, breach: uuid.UUID) -> Any:
    with admin.connect() as c:
        return c.execute(
            text("SELECT closed_at FROM ops.breach_records WHERE id = :b"), {"b": breach}
        ).scalar_one()


@pytest.mark.db
def test_TI_04_D58_closed_at_cannot_be_back_dated_into_the_purge(
    admin_engine: Engine, engines: None
) -> None:
    """Audit 2026-11-08 TI-04: the 3-year delete floor (0075) trusts ``closed_at``. A closed
    breach keeps its ``closed_at``; closing stamps the time of closing; only leaving ``closed``
    (the SchoolOS reopen path) clears it. Holds for every role (``sos_app`` and the owner)."""
    tenant = _tenant(admin_engine)
    recent = _breach(admin_engine, tenant, closed_days_ago=30)
    still_open = _breach(admin_engine, tenant, closed_days_ago=None)
    before = _closed_at(admin_engine, recent)
    back_date = "UPDATE ops.breach_records SET closed_at = now() - interval '4 years' WHERE id = :b"
    with pytest.raises(Exception, match="closed_at"), tenant_session(tenant) as s:
        s.execute(text(back_date), {"b": recent})
    with pytest.raises(Exception, match="closed_at"), admin_engine.begin() as c:
        c.execute(text(back_date), {"b": recent})
    assert _closed_at(admin_engine, recent) == before
    # Closing with a back-dated time is refused; closing now works.
    close = (
        "UPDATE ops.breach_records SET status = 'closed', close_reason = 'Synthetic close.', "
        "closed_at = {} WHERE id = :b"
    )
    with pytest.raises(Exception, match="closed_at"), tenant_session(tenant) as s:
        s.execute(text(close.format("now() - interval '4 years'")), {"b": still_open})
    with tenant_session(tenant) as s:
        s.execute(text(close.format("now()")), {"b": still_open})
        s.execute(text("DELETE FROM ops.breach_records WHERE id = :b"), {"b": still_open})
    assert still_open in _ids(admin_engine, tenant)
    # The reopen path (status leaves closed) clears it; closing again restarts the clock.
    with tenant_session(tenant) as s:
        s.execute(
            text("UPDATE ops.breach_records SET status = 'open', closed_at = NULL WHERE id = :b"),
            {"b": recent},
        )
    assert _closed_at(admin_engine, recent) is None


@pytest.mark.db
def test_D58_affected_lists_purged_and_subject_report_still_works(
    school: Any, admin_engine: Engine, engines: None
) -> None:
    """The affected list goes with the expired breach; the access report (subject report) of the
    person keeps listing the breaches that are kept, and the affected list of an open breach can
    still be replaced (``set_affected``'s delete is allowed by the policy)."""
    student = CP.student(school)
    expired = _breach(admin_engine, school.tenant_id, closed_days_ago=4 * 365)
    kept_closed = _breach(admin_engine, school.tenant_id, closed_days_ago=100)
    still_open = _breach(admin_engine, school.tenant_id, closed_days_ago=None)
    for b in (expired, kept_closed, still_open):
        _affected(admin_engine, school.tenant_id, b, student)
    subject = Subject(kind="student", id=student)
    with tenant_session(school.tenant_id) as s:
        before = breach_subjects.subject_report(s, subject)
    assert len(before[0].rows) == 3

    with tenant_session(school.tenant_id) as s:
        assert breach_service.purge_expired(s) >= 1
    assert _children(admin_engine, expired) == (0, 0)
    assert _children(admin_engine, kept_closed) == (2, 1)

    with tenant_session(school.tenant_id) as s:
        after = breach_subjects.subject_report(s, subject)
        # A closed breach's affected list is frozen (policy); an open one's may be replaced.
        s.execute(text("DELETE FROM ops.breach_affected"))
    assert len(after[0].rows) == 2
    assert after[0].class_key == "breach_records"
    assert _children(admin_engine, kept_closed) == (2, 1)
    assert _children(admin_engine, still_open) == (2, 0)


# --- migration ----------------------------------------------------------------------------------


def _grants(engine: Engine, table: str) -> set[str]:
    with engine.connect() as c:
        return set(
            c.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE grantee = 'sos_app' AND table_schema = 'ops' AND table_name = :t"
                ),
                {"t": table},
            ).scalars()
        )


def _policies(engine: Engine) -> dict[str, str]:
    with engine.connect() as c:
        return {
            r.policyname: r.permissive
            for r in c.execute(
                text(
                    "SELECT policyname, permissive FROM pg_policies WHERE schemaname = 'ops' "
                    "AND tablename LIKE 'breach_%'"
                )
            )
        }


NEW_POLICIES = {
    "breach_records_delete_expired",
    "breach_reminders_delete_expired",
    "breach_affected_delete_open_or_expired",
}


@pytest.mark.db
def test_D58_migration_0075_round_trip(
    test_database: Any, make_alembic_config: Callable[[str], Config]
) -> None:
    url = test_database.create_fresh(DB)
    cfg = make_alembic_config(url)
    command.upgrade(cfg, REVISION)
    engine = create_engine(make_url(test_database.admin_url).set(database=DB))
    try:
        assert "DELETE" in _grants(engine, "breach_records")
        assert "DELETE" in _grants(engine, "breach_reminders")
        policies = _policies(engine)
        assert {p: policies.get(p) for p in NEW_POLICIES} == dict.fromkeys(
            NEW_POLICIES, "RESTRICTIVE"
        )
        assert {"tenant_isolation", "offboarding_purge"} <= set(policies)

        command.downgrade(cfg, PREVIOUS)
        assert "DELETE" not in _grants(engine, "breach_records")
        assert "DELETE" not in _grants(engine, "breach_reminders")
        assert "DELETE" in _grants(engine, "breach_affected")  # 0067's grant stays
        assert not NEW_POLICIES & set(_policies(engine))

        command.upgrade(cfg, "head")
        assert set(_policies(engine)) >= NEW_POLICIES
    finally:
        engine.dispose()
