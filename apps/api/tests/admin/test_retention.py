"""Retention settings per data category within bounds (US-1201; FR-ADM-002, BR-08; docs/05 §13,
docs/08 §7) and their effect on the existing retention jobs. Synthetic data only."""

from __future__ import annotations

import dataclasses
import datetime as dt
import sys
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.admin import service as admin
from app.admin.schemas import RetentionUpdate
from app.core import retention
from app.core.db import tenant_session
from app.core.errors import Forbidden, PreconditionFailed, StepUpRequired, ValidationFailed
from app.exports import service as exports
from app.imports import service as imports
from app.notifications import service as notifications

pytestmark = pytest.mark.db
AD = sys.modules["sos_test_admin_support"]


def _get(school: Any, role: str = "owner") -> Any:
    person = school.people[role]
    with tenant_session(school.tenant_id, person.user_id) as db:
        return admin.get_retention(db, AD.ctx(school, person, role))


def _put(school: Any, rules: dict[str, int], version: int, role: str = "owner", **kw: Any) -> Any:
    person = school.people[role]
    c = kw.pop("as_ctx", None) or AD.ctx(school, person, role)
    with tenant_session(school.tenant_id, person.user_id) as db:
        return admin.update_retention(db, c, RetentionUpdate(rules=rules), expected_version=version)


@pytest.fixture
def defaults(school: Any) -> Iterator[None]:
    """Every test starts and ends with the school on the defaults."""
    current = _get(school)
    if any(not c.is_default for c in current.categories):
        _put(school, {}, current.version)
    yield
    current = _get(school)
    if any(not c.is_default for c in current.categories):
        _put(school, {}, current.version)


def test_FR_ADM_002_defaults_and_bounds_are_listed_per_category(
    school: Any, defaults: None
) -> None:
    out = _get(school)
    by_key = {c.key: c for c in out.categories}
    assert set(by_key) >= {"import_raw_files", "exports", "notifications_read", "audit_events"}
    raw = by_key["import_raw_files"]
    assert (raw.days, raw.default_days, raw.min_days, raw.max_days) == (90, 90, 7, 90)
    assert raw.configurable
    assert raw.enforced
    assert raw.is_default
    audit_events = by_key["audit_events"]
    assert not audit_events.configurable
    assert audit_events.days >= 395
    assert not by_key["kb_queries"].enforced  # no purge job yet: reported, not hidden


def test_FR_ADM_002_owner_and_principal_change_retention_audited(
    school: Any, admin_engine: Engine, defaults: None
) -> None:
    start = _get(school).version
    out = _put(school, {"import_raw_files": 30, "exports": 3}, start)
    by_key = {c.key: c for c in out.categories}
    assert by_key["import_raw_files"].days == 30
    assert by_key["exports"].days == 3
    assert not by_key["exports"].is_default
    assert by_key["notifications_read"].days == 90
    assert out.version == start + 1
    assert out.updated_by is not None
    assert out.updated_by.membership_id == school.people["owner"].membership_id
    # The principal holds tenant.settings.manage too; leaving a category out restores it.
    again = _put(school, {"import_raw_files": 45}, out.version, role="principal")
    assert {c.key: c.days for c in again.categories}["exports"] == 7
    events = [
        e
        for e in AD.W.audit_events(admin_engine, school.tenant_id)
        if e["action"] == "admin.retention.updated"
    ]
    assert events[-2]["summary"]["changes"] == [
        {"category": "import_raw_files", "from_days": 90, "to_days": 30},
        {"category": "exports", "from_days": 7, "to_days": 3},
    ]
    assert events[-1]["summary"]["changes"] == [
        {"category": "import_raw_files", "from_days": 30, "to_days": 45},
        {"category": "exports", "from_days": 3, "to_days": 7},
    ]
    with admin_engine.connect() as c:
        stored = c.execute(
            text("SELECT rules FROM ops.retention_settings WHERE tenant_id = :t"),
            {"t": school.tenant_id},
        ).scalar_one()
    assert stored == {"import_raw_files": 45}  # only differences from the defaults


@pytest.mark.parametrize(
    ("rules", "code"),
    [
        ({"import_raw_files": 120}, "out_of_bounds"),
        ({"import_raw_files": 3}, "out_of_bounds"),
        ({"exports": 8}, "out_of_bounds"),
        ({"notifications_read": 29}, "out_of_bounds"),
        ({"audit_events": 30}, "not_configurable"),
        ({"tenant_exports": 7}, "not_configurable"),
        ({"students_forever": 30}, "unknown_category"),
    ],
)
def test_FR_ADM_002_values_outside_the_legal_bounds_are_refused(
    school: Any, defaults: None, rules: dict[str, int], code: str
) -> None:
    version = _get(school).version
    with pytest.raises(ValidationFailed) as exc:
        _put(school, rules, version)
    assert [e["code"] for e in exc.value.errors] == [code]
    assert exc.value.errors[0]["field"] == f"rules.{next(iter(rules))}"


def test_FR_ADM_002_fixed_category_at_its_value_is_accepted(school: Any, defaults: None) -> None:
    version = _get(school).version
    out = _put(school, {"audit_events": 395, "import_raw_files": 60}, version)
    assert {c.key: c.days for c in out.categories}["import_raw_files"] == 60


def test_FR_ADM_002_stale_version_is_412_and_changes_nothing(school: Any, defaults: None) -> None:
    version = _get(school).version
    with pytest.raises(PreconditionFailed):
        _put(school, {"import_raw_files": 30}, version + 5)
    assert _get(school).version == version


def test_FR_ADM_002_permission_and_step_up(school: Any, defaults: None) -> None:
    for role in ("office_admin", "accountant", "office_staff"):
        with pytest.raises(Forbidden):
            _get(school, role)
    owner = school.people["owner"]
    stale = dataclasses.replace(
        AD.ctx(school, owner, "owner"),
        auth_time=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=10),
    )
    with pytest.raises(StepUpRequired):
        _put(school, {"import_raw_files": 30}, _get(school).version, as_ctx=stale)


def test_FR_ADM_002_jobs_read_the_school_setting_and_other_schools_keep_defaults(
    school: Any, world: Any, defaults: None
) -> None:
    assert admin._retention_provider in retention.providers()
    _put(
        school,
        {"import_raw_files": 30, "exports": 2, "notifications_read": 45},
        _get(school).version,
    )
    with tenant_session(school.tenant_id) as s:
        assert imports.raw_file_retention_days(s) == 30
        assert retention.days(s, exports.RETENTION_CATEGORY, default=7) == 2
        assert retention.days(s, notifications.READ_RETENTION_CATEGORY, default=90) == 45
        assert retention.days(s, "audit_events", default=395) == 395  # not configurable
        assert admin.retention_days(s, "import_raw_files") == 30
    with tenant_session(world.b.tenant_id) as s:
        assert imports.raw_file_retention_days(s) == 90
        assert retention.days(s, exports.RETENTION_CATEGORY, default=7) == 7


def test_FR_ADM_002_a_stored_value_outside_new_bounds_is_clamped(
    school: Any, admin_engine: Engine, defaults: None
) -> None:
    _put(school, {"import_raw_files": 30}, _get(school).version)
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.retention_settings SET rules = '{\"import_raw_files\": 400}'::jsonb "
                "WHERE tenant_id = :t"
            ),
            {"t": school.tenant_id},
        )
    with tenant_session(school.tenant_id) as s:
        assert imports.raw_file_retention_days(s) == 90
    assert {c.key: c.days for c in _get(school).categories}["import_raw_files"] == 90


def _read_notification(admin_engine: Engine, school: Any, read_days_ago: int) -> uuid.UUID:
    key = f"retention:{uuid.uuid4()}"
    with tenant_session(school.tenant_id) as s:
        notifications.notify(
            s,
            tenant_id=school.tenant_id,
            recipients=[school.people["owner"].membership_id],
            template_key="import.committed",
            params={"import_id": str(uuid.uuid4()), "rows": 1},
            dedupe_key=key,
        )
    with admin_engine.begin() as c:
        return uuid.UUID(
            str(
                c.execute(
                    text(
                        "UPDATE ops.notifications "
                        "SET created_at = now() - make_interval(days => :d + 1), "
                        "read_at = now() - make_interval(days => :d) "
                        "WHERE dedupe_key = :k RETURNING id"
                    ),
                    {"d": read_days_ago, "k": key},
                ).scalar_one()
            )
        )


def test_FR_ADM_002_notification_purge_uses_the_school_setting(
    school: Any, admin_engine: Engine, defaults: None
) -> None:
    kept = _read_notification(admin_engine, school, 40)
    purged = _read_notification(admin_engine, school, 50)
    _put(school, {"notifications_read": 45}, _get(school).version)
    with tenant_session(school.tenant_id) as s:
        notifications.purge_read(s)
    with admin_engine.connect() as c:
        left = set(
            c.execute(
                text("SELECT id FROM ops.notifications WHERE id IN (:a, :b)"),
                {"a": kept, "b": purged},
            ).scalars()
        )
    assert left == {kept}


def test_FR_ADM_002_export_files_expire_after_the_school_setting(
    school: Any, admin_engine: Engine, defaults: None
) -> None:
    ex = AD._load("sos_test_exports_objects", AD._HERE.parents[1] / "exports" / "objects.py")
    AD.SW.create(school, name="Synthetica Retention Student", section_key="section_10a")
    _put(school, {"exports": 2}, _get(school).version)
    export_id = ex.ready_export(school, "office_admin", section_keys=("section_10a",))
    row = ex.export_row(admin_engine, export_id)
    assert row["expires_at"] - row["finished_at"] == dt.timedelta(days=2)
