"""Staff accounts in the data principal's access report (FR-DPDP-009, PRV-031; R3-1A).
Synthetic schools only."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.core.subjects import ReportSection, Subject
from app.identity import service

pytestmark = pytest.mark.db

SUBJECTS_YAML = Path(__file__).resolve().parents[2] / "app" / "privacy" / "subjects.yaml"


def _load_world() -> ModuleType:
    name = "sos_test_api_world"
    module = sys.modules.get(name)
    if module is None:
        path = Path(__file__).resolve().parents[1] / "api" / "world.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


W = _load_world()


def _classes() -> set[str]:
    data = yaml.safe_load(SUBJECTS_YAML.read_text(encoding="utf-8"))
    return set(data["contributors"]["identity"]["classes"])


@pytest.fixture(scope="module")
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    school = W.School(W.provision_school())
    owner = W.add_member(admin_engine, school.tenant_id, ["owner"])
    school.people["owner"] = owner
    W.build_structure(school, owner)
    school.people["teacher"] = W.add_member(
        admin_engine,
        school.tenant_id,
        ["class_teacher", "teacher"],
        scopes=[("section", school.ids["section_9a"]), ("class", school.ids["class_x"])],
        display_name="Synthetic Teacher Ravi",
        email=f"ravi-{uuid.uuid4().hex[:8]}@example.test",
    )
    school.people["other"] = W.add_member(
        admin_engine,
        school.tenant_id,
        ["accountant"],
        display_name="Synthetic Accountant Other",
        email=f"other-{uuid.uuid4().hex[:8]}@example.test",
    )
    return school


def _report(tenant_id: uuid.UUID, subject: Subject) -> list[ReportSection]:
    with tenant_session(tenant_id) as s:
        return service.subject_report(s, subject)


def _text(sections: list[ReportSection]) -> str:
    return "\n".join(
        " | ".join([s.title, s.note or "", *(" ".join(r) for r in s.rows)]) for s in sections
    )


def test_FR_DPDP_009_member_report_has_account_roles_and_scopes(school: Any) -> None:
    teacher = school.people["teacher"]
    sections = _report(school.tenant_id, Subject("member", teacher.membership_id))
    assert [s.title for s in sections] == ["Staff account", "Roles", "Access scope"]
    account = {r[0]: r[1] for r in sections[0].rows}
    assert account["Name"] == "Synthetic Teacher Ravi"
    assert account["Email"].startswith("ravi-")
    assert account["Status"] == "Active"
    assert len(account["Added to the school on"].split("/")) == 3
    roles = {r[0] for r in sections[1].rows}
    assert len(roles) == 2
    scopes = {r[0] for r in sections[2].rows}
    assert scopes == {"Section: Class IX A", "Class: Class X"}


def test_FR_DPDP_009_member_report_has_no_secrets_or_other_members(school: Any) -> None:
    teacher = school.people["teacher"]
    text = _text(_report(school.tenant_id, Subject("member", teacher.membership_id)))
    assert teacher.subject not in text  # the sign-in identity never appears
    assert str(teacher.user_id) not in text
    assert "Synthetic Accountant Other" not in text
    assert "other-" not in text


def test_FR_DPDP_009_class_keys_are_the_modules_classes(school: Any) -> None:
    sections = _report(school.tenant_id, Subject("member", school.people["teacher"].membership_id))
    assert sections
    assert {s.class_key for s in sections} <= _classes()


def test_FR_DPDP_009_unsupported_kind_or_unknown_member_is_empty(school: Any) -> None:
    member_id = school.people["teacher"].membership_id
    assert _report(school.tenant_id, Subject("student", member_id)) == []
    assert _report(school.tenant_id, Subject("guardian", member_id)) == []
    assert _report(school.tenant_id, Subject("member", uuid.uuid4())) == []


def test_FR_DPDP_009_other_schools_member_is_not_reported(
    school: Any, admin_engine: Engine
) -> None:
    other = W.provision_school()
    member = W.add_member(admin_engine, other, ["teacher"])
    assert _report(school.tenant_id, Subject("member", member.membership_id)) == []


def test_FR_DPDP_008_identity_is_not_an_eraser() -> None:
    assert not hasattr(service, "erase_for_subject")
