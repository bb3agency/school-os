"""Data-principal access report contribution of support access (FR-DPDP-009, PRV-031).

A fresh synthetic campus per test (tests/breakglass/conftest.py); grants are pulled from the
control plane with the real sync.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from app.breakglass import service, subjects
from app.core.db import tenant_session
from app.core.subjects import Subject

pytestmark = pytest.mark.db

_YAML = Path(service.__file__).resolve().parents[1] / "privacy" / "subjects.yaml"


def _objects() -> ModuleType:
    name = "sos_test_breakglass_objects"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name("objects.py"))
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _classes() -> set[str]:
    data = yaml.safe_load(_YAML.read_text(encoding="utf-8"))
    return set(data["contributors"]["breakglass"]["classes"])


def _report(tenant_id: uuid.UUID, subject: Subject) -> list[Any]:
    with tenant_session(tenant_id) as s:
        return subjects.subject_report(s, subject)


def test_FR_DPDP_009_breakglass_report_lists_grants_the_member_approved(campus: Any) -> None:
    objects = _objects()
    tenant = campus.school.tenant_id
    owner = campus.person("owner")
    objects.active_grant(tenant, owner)
    objects.pending_grant(tenant)  # nobody at the school decided this one yet

    sections = _report(tenant, Subject("member", owner.membership_id))

    assert len(sections) == 1
    sec = sections[0]
    assert sec.class_key in _classes()
    assert len(sec.rows) == 1
    row = dict(zip(sec.columns, sec.rows[0], strict=True))
    assert row["This staff member's part"] == "Approved"
    assert row["Reason"] == "Support request"
    assert row["Length (minutes)"] == "30"
    dumped = repr(sections)
    assert "Synthetic support reason" not in dumped  # the operator's free text
    assert "Synthetic Operator" not in dumped  # the operator's name


def test_FR_DPDP_009_breakglass_report_empty_for_unrelated_or_other_kinds(campus: Any) -> None:
    objects = _objects()
    tenant = campus.school.tenant_id
    objects.active_grant(tenant, campus.person("owner"))
    teacher = campus.person("teacher")
    assert _report(tenant, Subject("member", teacher.membership_id)) == []
    assert _report(tenant, Subject("student", uuid.uuid4())) == []
    assert _report(tenant, Subject("guardian", uuid.uuid4())) == []


def test_FR_DPDP_008_breakglass_is_not_an_eraser() -> None:
    assert not hasattr(service, "erase_for_subject")


def test_FR_DPDP_008_breakglass_service_exposes_subject_report() -> None:
    assert service.subject_report is subjects.subject_report
