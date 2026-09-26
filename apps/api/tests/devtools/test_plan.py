"""The synthetic dataset plan is pure and deterministic (docs/12 §3; NFR-MNT-002)."""

from __future__ import annotations

import re
import uuid

import pytest

from app.authz.catalog import system_roles
from app.devtools import plan as synth

SUBJECT_RE = re.compile(r"^synthetic\|[a-z][a-z0-9-]+\|[a-z_]+\|\d+$")


def test_docs12_s3_same_seed_same_plan_different_seed_different_names() -> None:
    a = synth.build_plan(seed=7)
    assert a == synth.build_plan(seed=7)
    b = synth.build_plan(seed=8)
    names_a = [s.display_name for t in a.tenants for s in t.staff]
    names_b = [s.display_name for t in b.tenants for s in t.staff]
    assert names_a != names_b
    # identities are keyed by code/role/ordinal, not by seed
    assert [s.subject for t in a.tenants for s in t.staff] == [
        s.subject for t in b.tenants for s in t.staff
    ]


def test_docs12_s3_names_do_not_depend_on_code_prefix_or_tenant_count() -> None:
    a = synth.build_plan(seed=7, code_prefix="synth")
    b = synth.build_plan(seed=7, code_prefix="other", tenants=3)
    for ta, tb in zip(a.tenants, b.tenants[:2], strict=True):
        assert [(s.key, s.display_name, s.preferred_language) for s in ta.staff] == [
            (s.key, s.display_name, s.preferred_language) for s in tb.staff
        ]


def test_docs12_s3_default_tenants_codes_ids_and_names() -> None:
    plan = synth.build_plan()
    assert plan.dataset_version == "v1"
    assert plan.seed == synth.DEFAULT_SEED
    assert [t.code for t in plan.tenants] == ["synth-a", "synth-b"]
    for t in plan.tenants:
        assert t.tenant_id == uuid.uuid5(synth.NAMESPACE, f"v1/tenant/{t.code}")
        assert "Synthetic" in t.name
        assert t.boards
    assert plan.tenants[0].name == "Sri Venkateswara Synthetic High School"
    assert len({t.name for t in plan.tenants}) == len(plan.tenants)


def test_docs12_s3_structure_layout() -> None:
    t = synth.build_plan().tenants[0]
    assert [(y.label, y.is_current) for y in t.years] == [("2026-27", True), ("2025-26", False)]
    assert t.current_year.label == "2026-27"
    for class_code in ("NUR", "LKG", "UKG"):
        assert [n for c, n in t.sections if c == class_code] == ["A", "B"]
    for class_code in ("I", "V", "X", "XII"):
        assert [n for c, n in t.sections if c == class_code] == ["A", "B", "C", "D"]
    assert len(t.sections) == 3 * 2 + 12 * 4


def test_docs12_s3_staff_cover_every_system_role_with_synthetic_identities() -> None:
    for t in synth.build_plan().tenants:
        roles = {s.role for s in t.staff}
        assert roles == set(system_roles())
        assert len({s.subject for s in t.staff}) == len(t.staff)
        assert len({s.email for s in t.staff}) == len(t.staff)
        for s in t.staff:
            assert SUBJECT_RE.match(s.subject), s.subject
            assert s.subject == f"synthetic|{t.code}|{s.role}|{s.ordinal}"
            assert s.email.endswith(f"@{t.code}.example.invalid")
            assert s.preferred_language in ("en", "te")
        assert t.owner.role == "owner"
        assert {s.preferred_language for s in t.staff} == {"en", "te"}


def test_docs12_s3_one_class_teacher_per_current_section_and_teacher_class_scopes() -> None:
    t = synth.build_plan().tenants[0]
    class_teachers = [s for s in t.staff if s.role == "class_teacher"]
    assert [s.section for s in class_teachers] == list(t.sections)
    assert all(not s.classes for s in class_teachers)
    teachers = [s for s in t.staff if s.role == "teacher"]
    assert teachers
    for s in teachers:
        assert s.section is None
        assert len(s.classes) == 2
        assert set(s.classes) <= set(synth.TEACHER_CLASSES)
    for s in t.staff:
        if s.role not in ("class_teacher", "teacher"):
            assert s.section is None
            assert not s.classes


def test_docs12_s4_4_tenants_have_deliberately_overlapping_names() -> None:
    a, b = synth.build_plan().tenants
    names_a = {s.display_name for s in a.staff}
    names_b = {s.display_name for s in b.staff}
    shared = {s.display_name for s in a.staff if s.shared_name}
    assert shared
    assert shared <= names_b
    assert a.owner.display_name == b.owner.display_name
    # ... and plenty of names that belong to one school only
    assert len(names_a - names_b) > 10


def test_plan_rejects_bad_inputs() -> None:
    with pytest.raises(synth.PlanError, match="dataset version"):
        synth.build_plan(dataset_version="v0")
    with pytest.raises(synth.PlanError, match="tenants"):
        synth.build_plan(tenants=0)
    with pytest.raises(synth.PlanError, match="tenants"):
        synth.build_plan(tenants=synth.MAX_TENANTS + 1)
    with pytest.raises(synth.PlanError, match="prefix"):
        synth.build_plan(code_prefix="Bad_Prefix")


def test_plan_supports_many_tenants_with_unique_codes_and_names() -> None:
    plan = synth.build_plan(tenants=synth.MAX_TENANTS)
    assert len({t.code for t in plan.tenants}) == synth.MAX_TENANTS
    assert len({t.name for t in plan.tenants}) == synth.MAX_TENANTS
    assert plan.tenants[-1].code == "synth-z"
