"""In-app notifications in the data principal's access report and erasure (FR-DPDP-009,
FR-DPDP-013, PRV-031, PRV-032; R3-1A). Synthetic schools only."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.subjects import ErasureOutcome, ReportSection, Subject
from app.notifications import service

from .conftest import Staff, W

pytestmark = pytest.mark.db

SUBJECTS_YAML = Path(__file__).resolve().parents[2] / "app" / "privacy" / "subjects.yaml"


def _classes() -> set[str]:
    data = yaml.safe_load(SUBJECTS_YAML.read_text(encoding="utf-8"))
    return set(data["contributors"]["notifications"]["classes"])


def _member(person: object) -> uuid.UUID:
    value = person.membership_id  # type: ignore[attr-defined]
    assert isinstance(value, uuid.UUID)
    return value


def _notify(tenant_id: uuid.UUID, member: uuid.UUID, times: int = 1) -> None:
    with tenant_session(tenant_id) as s:
        for _ in range(times):
            service.notify(
                s,
                tenant_id=tenant_id,
                recipients=[member],
                template_key="import.committed",
                params={"import_id": str(uuid.uuid4()), "rows": 3},
            )


def _count(admin: Engine, member: uuid.UUID) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text("SELECT count(*) FROM ops.notifications WHERE recipient_membership_id = :m"),
                {"m": member},
            ).scalar_one()
        )


def _report(tenant_id: uuid.UUID, subject: Subject) -> list[ReportSection]:
    with tenant_session(tenant_id) as s:
        return service.subject_report(s, subject)


def _erase(tenant_id: uuid.UUID, subject: Subject, *, dry_run: bool) -> list[ErasureOutcome]:
    with tenant_session(tenant_id) as s:
        return service.erase_for_subject(s, subject, {"notifications"}, dry_run=dry_run)


@pytest.fixture
def people(school: Staff, admin_engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    """Two fresh members of the school: the subject and someone else."""
    subject = W.add_member(admin_engine, school.tenant_id, ["teacher"])
    other = W.add_member(admin_engine, school.tenant_id, ["teacher"])
    return _member(subject), _member(other)


def test_FR_DPDP_009_member_report_counts_notifications(
    school: Staff, people: tuple[uuid.UUID, uuid.UUID]
) -> None:
    member, other = people
    _notify(school.tenant_id, member, times=3)
    _notify(school.tenant_id, other, times=5)
    with tenant_session(school.tenant_id) as s:
        first: Any = s.execute(
            text("SELECT id FROM ops.notifications WHERE recipient_membership_id = :m LIMIT 1"),
            {"m": member},
        ).scalar_one()
        s.execute(text("UPDATE ops.notifications SET read_at = now() WHERE id = :i"), {"i": first})
    sections = _report(school.tenant_id, Subject("member", member))
    assert len(sections) == 1
    section = sections[0]
    assert section.class_key in _classes()
    assert section.columns == ("Kind", "Notifications", "Unread", "Latest on")
    assert section.rows == ((section.rows[0][0], "3", "2", section.rows[0][3]),)
    assert section.rows[0][0] == "Import committed"


def test_FR_DPDP_009_empty_or_unsupported_subject_is_empty(
    school: Staff, people: tuple[uuid.UUID, uuid.UUID]
) -> None:
    member, _ = people
    assert _report(school.tenant_id, Subject("member", member)) == []
    _notify(school.tenant_id, member)
    assert _report(school.tenant_id, Subject("student", member)) == []
    assert _report(school.tenant_id, Subject("guardian", member)) == []
    assert _erase(school.tenant_id, Subject("student", member), dry_run=False) == []
    with tenant_session(school.tenant_id) as s:
        assert (
            service.erase_for_subject(s, Subject("member", member), {"other"}, dry_run=False) == []
        )


def test_FR_DPDP_013_dry_run_counts_and_changes_nothing(
    school: Staff, people: tuple[uuid.UUID, uuid.UUID], admin_engine: Engine
) -> None:
    member, _ = people
    _notify(school.tenant_id, member, times=2)
    outcome = _erase(school.tenant_id, Subject("member", member), dry_run=True)
    assert outcome == [ErasureOutcome("notifications", 2)]
    assert _count(admin_engine, member) == 2


def test_FR_DPDP_013_erasure_deletes_only_the_members_notifications(
    school: Staff, people: tuple[uuid.UUID, uuid.UUID], admin_engine: Engine
) -> None:
    member, other = people
    _notify(school.tenant_id, member, times=2)
    _notify(school.tenant_id, other, times=4)
    outcome = _erase(school.tenant_id, Subject("member", member), dry_run=False)
    assert outcome == [ErasureOutcome("notifications", 2)]
    assert _count(admin_engine, member) == 0
    assert _count(admin_engine, other) == 4
    with admin_engine.connect() as c:
        summary: Any = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE tenant_id = :t AND action = :a "
                "AND resource_id = :m"
            ),
            {"t": school.tenant_id, "a": "notification.subject_erased", "m": member},
        ).scalar_one()
    assert summary == {"class": "notifications", "count": 2}
