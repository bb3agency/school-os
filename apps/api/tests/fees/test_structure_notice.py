"""Fee structure notice and year-on-year change summary (FM-10; FR-FEE-113..125; D86).

A school of its own (module fixture) so the plans of both years are exactly these:

- 2026-27 Class IX (active, from ``support.ensure_setup``): TUI 6,000, EXM 1,000, BUS 2,000.
- 2025-26 Class IX (active): TUI 5,000, EXM 1,000, LIB 500.
- 2026-27 Class X (draft only): TUI 7,000.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import Engine

from app.core.errors import NotFound, ValidationFailed
from app.fees import service as fees
from app.fees.schemas import FeePlanCreate, HeadCreate, InstalmentIn, LateFeeRule, PlanLineIn

pytestmark = pytest.mark.db

F: Any = __import__("sys").modules["sos_test_fees_support"]
W = F.W
D = Decimal
BASE = "/api/v1/fees/structure"


@pytest.fixture(scope="module")
def notice_school(world: Any, admin_engine: Engine) -> Any:
    F.install()
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin_engine, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ("accountant", "office_staff", "principal", "auditor_readonly"):
        s.people[role] = W.add_member(admin_engine, s.tenant_id, [role])
    s.people["class_teacher"] = W.add_member(
        admin_engine, s.tenant_id, ["class_teacher"], scopes=[("section", s.ids["section_9a"])]
    )
    F.enable(admin_engine, s.tenant_id)
    setup = F.ensure_setup(s)
    lib = F.call(
        s,
        "accountant",
        fees.create_head,
        HeadCreate(code="LIB", name="Library fee", kind="other", gst_exempt=True),
    ).id
    old = F.call(
        s,
        "accountant",
        fees.create_plan,
        FeePlanCreate(
            academic_year_id=s.ids["old_year"],
            class_id=s.ids["class_ix"],
            name="Class IX 2025-26",
            lines=[
                PlanLineIn(head_id=setup.tui, annual_amount=D("5000.00")),
                PlanLineIn(head_id=setup.exm, annual_amount=D("1000.00")),
                PlanLineIn(head_id=lib, annual_amount=D("500.00")),
            ],
            instalments=[
                InstalmentIn(
                    instalment_no=1,
                    label="Year",
                    due_on=F.dt.date(2025, 7, 15),
                    amounts={setup.tui: D("5000.00"), setup.exm: D("1000.00"), lib: D("500.00")},
                )
            ],
            late_fee_rule=LateFeeRule(),
        ),
    )
    F.call(s, "accountant", fees.activate_plan, old.id, expected_version=old.version)
    F.call(
        s,
        "accountant",
        fees.create_plan,
        FeePlanCreate(
            academic_year_id=s.ids["year"],
            class_id=s.ids["class_x"],
            name="Class X 2026-27",
            lines=[PlanLineIn(head_id=setup.tui, annual_amount=D("7000.00"))],
            instalments=[
                InstalmentIn(
                    instalment_no=1,
                    label="Year",
                    due_on=F.dt.date(2026, 7, 15),
                    amounts={setup.tui: D("7000.00")},
                )
            ],
            late_fee_rule=LateFeeRule(kind="flat_per_instalment", amount=D("50.00"), grace_days=7),
        ),
    )
    s.ids["lib"] = lib
    return s


# --- notice (FR-FEE-113..117) --------------------------------------------------------------------


def test_FR_FEE_113_notice_lists_each_class_plan(notice_school: Any) -> None:
    s = notice_school
    out = F.call(s, "accountant", fees.fee_structure_notice, s.ids["year"])
    assert out.title.startswith("Fee structure ")
    assert "approved" not in (out.title + out.disclaimer).lower()
    by_class = {k.class_id: k for k in out.classes}
    ix = by_class[s.ids["class_ix"]]
    assert ix.plan_status == "active"
    assert [h.code for h in ix.heads] == ["TUI", "EXM", "BUS"]
    assert ix.annual_total == D("9000.00")
    assert [i.amount for i in ix.instalments] == [D("4500.00"), D("4500.00")]
    assert ix.late_fee_text == "No late fee is charged."
    x = by_class[s.ids["class_x"]]
    assert x.plan_status == "draft"
    assert "₹50.00" in x.late_fee_text
    assert "7 days" in x.late_fee_text


def test_FR_FEE_114_notice_for_one_class(notice_school: Any) -> None:
    s = notice_school
    out = F.call(
        s, "principal", fees.fee_structure_notice, s.ids["year"], class_id=s.ids["class_x"]
    )
    assert [k.class_id for k in out.classes] == [s.ids["class_x"]]
    with pytest.raises(ValidationFailed):
        F.call(s, "accountant", fees.fee_structure_notice, s.ids["year"], class_id=uuid.uuid4())
    with pytest.raises(NotFound):
        F.call(s, "accountant", fees.fee_structure_notice, uuid.uuid4())


def test_FR_FEE_115_print_has_a_page_per_class_and_marks_drafts(
    notice_school: Any, admin_engine: Engine
) -> None:
    s = notice_school
    page = F.call(s, "accountant", fees.fee_structure_notice_print, s.ids["year"])
    assert page.count("<section>") == 2
    assert page.count("DRAFT") == 1
    assert "9,000.00" in page
    assert "<script" not in page
    events = W.audit_events(admin_engine, s.tenant_id, "fee.structure_notice.printed")
    assert any(e["resource_id"] == s.ids["year"] for e in events)


# --- change summary (FR-FEE-118..122) ------------------------------------------------------------


def test_FR_FEE_118_change_summary_against_last_year(notice_school: Any) -> None:
    s = notice_school
    out = F.call(s, "accountant", fees.fee_structure_changes, s.ids["year"])
    assert out.previous_year_id == s.ids["old_year"]
    assert out.warn_percent == D("10.0")
    assert out.warn_percent_verified is False
    rows = {r.class_id: r for r in out.classes}
    ix = rows[s.ids["class_ix"]]
    heads = {h.code: h for h in ix.heads}
    assert heads["TUI"].change == "changed"
    assert heads["TUI"].difference == D("1000.00")
    assert heads["TUI"].percent == D("20.0")
    assert heads["TUI"].above_threshold is True
    assert heads["EXM"].change == "unchanged"
    assert heads["EXM"].percent == D("0.0")
    assert heads["BUS"].change == "added"
    assert heads["BUS"].percent is None
    assert heads["LIB"].change == "removed"
    assert heads["LIB"].difference == D("-500.00")
    assert ix.previous_total == D("6500.00")
    assert ix.current_total == D("9000.00")
    assert ix.percent == D("38.5")
    assert ix.above_threshold is True
    x = rows[s.ids["class_x"]]
    assert x.has_previous is False
    assert x.plan_status == "draft"
    assert x.percent is None
    assert out.classes_above_threshold == 1


def test_FR_FEE_119_threshold_for_one_view(notice_school: Any) -> None:
    s = notice_school
    out = F.call(s, "accountant", fees.fee_structure_changes, s.ids["year"], warn_percent=D("50"))
    ix = next(r for r in out.classes if r.class_id == s.ids["class_ix"])
    assert ix.above_threshold is False
    assert out.classes_above_threshold == 0


def test_FR_FEE_120_first_year_has_nothing_to_compare(notice_school: Any) -> None:
    s = notice_school
    out = F.call(s, "accountant", fees.fee_structure_changes, s.ids["old_year"])
    assert out.previous_year_id is None
    ix = next(r for r in out.classes if r.class_id == s.ids["class_ix"])
    assert ix.has_previous is False
    assert {h.change for h in ix.heads} == {"added"}


# --- over HTTP (FR-FEE-123/124) -------------------------------------------------------------------


def test_FR_FEE_123_authz(notice_school: Any, other_school: Any, api: Any) -> None:
    s = notice_school
    year = s.ids["year"]
    paths = [
        f"{BASE}-notice?academic_year_id={year}",
        f"{BASE}-notice/print?academic_year_id={year}",
        f"{BASE}-changes?academic_year_id={year}&warn_percent=12.5",
    ]
    for path in paths:
        assert api.call(s.people["accountant"], "GET", path).status_code == 200, path
        assert api.call(s.people["principal"], "GET", path).status_code == 200, path
        assert api.call(s.people["class_teacher"], "GET", path).status_code == 403, path
        assert api.call(s.people["office_staff"], "GET", path).status_code == 403, path
        res = api.call(other_school.people["accountant"], "GET", path)
        assert res.status_code == 404, path


def test_FR_FEE_124_print_headers(notice_school: Any, api: Any) -> None:
    s = notice_school
    res = api.call(
        s.people["accountant"], "GET", f"{BASE}-notice/print?academic_year_id={s.ids['year']}"
    )
    assert res.headers["content-type"].startswith("text/html")
    assert "style-src 'sha256-" in res.headers["content-security-policy"]
    assert "script-src" not in res.headers["content-security-policy"]
    assert res.headers["cache-control"] == "no-store"
