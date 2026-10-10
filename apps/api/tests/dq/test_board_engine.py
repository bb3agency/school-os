"""R1 board profiles and the ERP refresh on the real database (FR-DQ-030, FR-DQ-033,
FR-TEN-020, FR-TEN-021; ADR-0041). Synthetic data only."""

from __future__ import annotations

import json
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.dq import service as dq
from app.students import service as students

pytestmark = pytest.mark.db
DS = sys.modules["sos_test_dq_support"]


@pytest.fixture(autouse=True)
def _keys(keyring: None) -> None:
    return None


@contextmanager
def _boards(
    admin: Engine, tenant_id: uuid.UUID, boards: list[str], class_boards: dict[str, str]
) -> Iterator[None]:
    """The school's boards for one block (the shared world is used by other tests)."""
    with admin.begin() as c:
        row = c.execute(
            text("SELECT boards, settings FROM core.tenants WHERE id = :t"), {"t": tenant_id}
        ).one()
        c.execute(
            text(
                "UPDATE core.tenants SET boards = :b, "
                "settings = settings || jsonb_build_object('class_boards', CAST(:cb AS jsonb)) "
                "WHERE id = :t"
            ),
            {"b": boards, "cb": json.dumps(class_boards), "t": tenant_id},
        )
    try:
        yield
    finally:
        with admin.begin() as c:
            c.execute(
                text("UPDATE core.tenants SET boards = :b, settings = :s WHERE id = :t"),
                {
                    "b": list(row.boards),
                    "s": json.dumps(row.settings),
                    "t": tenant_id,
                },
            )


def _profiles(world: Any, *, include_all: bool = False) -> dict[str, Any]:
    owner = world.a.people["owner"]
    with tenant_session(world.a.tenant_id, owner.user_id) as db:
        return {p.key: p for p in dq.school_profiles(db, include_all=include_all)}


def test_FR_TEN_020_profiles_are_listed_for_the_schools_boards(
    world: Any, admin_engine: Engine
) -> None:
    with _boards(admin_engine, world.a.tenant_id, ["CBSE", "BSEAP"], {"XI": "BSEAP"}):
        shown = _profiles(world)
        assert "udise-plus" in shown  # a portal: every school
        assert {"cbse-registration-2027", "cbse-loc-2027"} <= set(shown)
        assert not [k for k in shown if k.startswith("cisce-")]
        # FR-TEN-021: Class XI follows the State Board here, so CBSE registers only Class IX.
        assert shown["cbse-registration-2027"].applies_to_classes == ["IX"]
        assert shown["cbse-loc-2027"].applies_to_classes == ["X", "XII"]
        everything = _profiles(world, include_all=True)
        assert everything["cisce-registration-2027"].superseded is False
        assert everything["cisce-registration-2026"].superseded is True
    with _boards(admin_engine, world.a.tenant_id, [], {}):
        assert "cisce-registration-2027" in _profiles(world), "no board declared: all listed"


def test_FR_DQ_030_cbse_profile_run_needs_category_and_apaar_details(
    world: Any, admin_engine: Engine
) -> None:
    sid = DS.student(world.a, admission_no=f"CB{uuid.uuid4().hex[:6]}")
    DS.run(world.a, sid, profile_key="cbse-registration-2027")
    rows = DS.findings(admin_engine, sid)
    dq5 = {(r["attribute_key"], r["severity"]) for r in rows if r["rule_id"] == "DQ-005"}
    assert ("category", "blocker") in dq5
    assert ("apaar_id", "blocker") not in dq5, "refused consent must not block (SC 2026)"
    assert any(r["rule_id"] == "DQ-009" for r in rows), "APAAR details are checked"
    assert {r["profile_key"] for r in rows if r["rule_id"] in ("DQ-005", "DQ-009")} == {
        "cbse-registration-2027"
    }


def test_FR_DQ_033_an_erp_refresh_value_never_replaces_the_register_and_becomes_a_finding(
    world: Any, admin_engine: Engine
) -> None:
    name = DS.unique_name("Bommireddy Sai")
    sid = DS.student(world.a, name=name)
    other = name.replace("Sai", "Saii")
    DS.call(world.a, students.record_value, sid, "full_name", "manual_entry", other)
    DS.call(world.a, students.record_value, sid, "gender", "manual_entry", "female")
    DS.run(world.a, sid)
    findings = [r for r in DS.findings(admin_engine, sid, "DQ-030") if r["status"] == "open"]
    assert {r["attribute_key"] for r in findings} == {"full_name", "gender"}
    assert all(r["severity"] == "medium" for r in findings)
    assert all(r["route_codes"] == ["ROUTE-ERP", "ROUTE-SCHOOL-CR"] for r in findings)
    assert other not in str([r["details"] for r in findings]), "values are masked"
    # Invariant 6 / BR-01: the canonical value is still the register's.
    owner = world.a.people["owner"]
    with tenant_session(world.a.tenant_id, owner.user_id) as db:
        canonical = students.canonical_values(db, [sid], ["full_name", "gender"])[sid]
    assert canonical["full_name"].value == name
    assert canonical["gender"].value == "male"
