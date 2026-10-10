"""Rule checks DQ-001..DQ-012 on in-memory synthetic facts (FR-DQ-001, FR-DQ-003, FR-DQ-004,
FR-DQ-006, docs/02 §5). No database: the engine tests cover loading and persistence."""

from __future__ import annotations

import datetime as dt
import json
import sys
import uuid
from typing import Any

import pytest

from app.dq.checks import (
    CHECKS,
    CanonicalFact,
    build_checks,
    evaluate,
    fingerprint_of,
    format_issues,
)
from app.dq.explanations import load_explanations
from app.dq.masking import DATE_MASK, mask_name
from app.dq.profiles import load_engine_config, load_profiles
from app.dq.rules import CheckKind, Finding, load_rules

CS = sys.modules["sos_test_dq_checks_support"]
REG = "admission_register"
AAD = "aadhaar_as_printed"
CHECKS_ALL = build_checks(load_rules())


def det(finding: Finding) -> dict[str, Any]:
    return dict(finding.details)


def run(*students: Any, **kw: Any) -> list[Finding]:
    return evaluate(CS.context(students, **kw), CHECKS_ALL)


def of(findings: list[Finding], rule_id: str) -> list[Finding]:
    return [f for f in findings if f.rule_id == rule_id]


def identity(
    name: str = "Kommineni Venkata Sai", extra: dict[tuple[str, str], str] | None = None
) -> dict[tuple[str, str], str]:
    return {
        ("full_name", REG): name,
        ("dob", REG): "2012-03-14",
        ("gender", REG): "male",
        ("father_name", REG): "Kommineni Ramana",
        ("mother_name", REG): "Kommineni Sarada",
        **(extra or {}),
    }


def test_FR_DQ_001_every_catalog_rule_has_a_check() -> None:
    rules = load_rules()
    assert set(CHECKS) == set(CheckKind)
    assert [c.rule.id for c in CHECKS_ALL] == list(rules)
    assert len(CHECKS_ALL) == 14


# --- DQ-001 name: register vs Aadhaar-as-printed ------------------------------------------------


@pytest.mark.parametrize(
    ("aadhaar_name", "match_class", "severity"),
    [
        ("Kommineni Venkata Sai", None, None),
        ("Venkata Sai Kommineni", "ORDER", "info"),
        ("Kommineni VenkataSai", "SPACING", "low"),
        ("K. Venkata Sai", "INITIALS", "medium"),
        ("Komineni Venkat Sai", "VARIANT", "medium"),
        ("Kommineni Venkata Saikumar", "DIFFERENT", "blocker"),
        ("Yarlagadda Durga Bhavani", "DIFFERENT", "blocker"),
        ("కొమ్మినేని వెంకట సాయి", None, None),
    ],
)
def test_FR_DQ_003_DQ_001_severity_follows_the_match_class(
    aadhaar_name: str, match_class: str | None, severity: str | None
) -> None:
    f = CS.facts(values=identity(extra={("aadhaar_name_as_printed", AAD): aadhaar_name}))
    found = of(run(f), "DQ-001")
    if match_class is None:
        assert found == []
        return
    (finding,) = found
    assert (finding.match_class, finding.severity.value) == (match_class, severity)
    assert finding.attribute_key == "full_name"
    assert finding.sources == (AAD, REG)
    assert finding.route_codes == ("ROUTE-UIDAI", "ROUTE-SCHOOL-CR")
    assert det(finding)["match"]["explanation_code"] == f"NM-{match_class}"


def test_FR_DQ_003_DQ_001_typo_is_high() -> None:
    f = CS.facts(values=identity(extra={("aadhaar_name_as_printed", AAD): "Komminemi Venkata Sai"}))
    (finding,) = of(run(f), "DQ-001")
    assert (finding.match_class, finding.severity.value) == ("TYPO", "high")


def test_FR_DQ_006_DQ_001_stores_masked_values_only() -> None:
    secret = "Pinnamaneni Lakshmana Chaitanya"
    f = CS.facts(values=identity(extra={("aadhaar_name_as_printed", AAD): secret}))
    (finding,) = of(run(f), "DQ-001")
    blob = json.dumps(dict(finding.details), ensure_ascii=False)
    for word in (*secret.split(), "Kommineni", "Venkata"):
        assert word.upper() not in blob.upper()
    values = {v["source"]: v for v in det(finding)["values"]}
    assert values[AAD]["masked"] == "P••• L••• C•••"
    assert values[AAD]["attribute_key"] == "aadhaar_name_as_printed"
    assert values[REG]["masked"] == mask_name("Kommineni Venkata Sai")
    assert uuid.UUID(values[AAD]["value_id"])


def test_FR_DQ_006_masking_keeps_initials_and_hides_telugu_words() -> None:
    assert mask_name("K. VENKATA SAI") == "K. V••• S•••"
    assert mask_name("కొమ్మినేని వెంకట") == "కొ••• వెం•••"  # anusvara stays with its letter
    assert mask_name("  ") == "••••"


# --- DQ-002 / DQ-003 --------------------------------------------------------------------------


def test_DQ_002_dob_mismatch_is_a_blocker_with_differing_parts() -> None:
    f = CS.facts(values=identity(extra={("aadhaar_dob_as_printed", AAD): "2012-04-14"}))
    (finding,) = of(run(f), "DQ-002")
    assert finding.severity.value == "blocker"
    assert det(finding)["differs_in"] == ["month"]
    assert {v["masked"] for v in det(finding)["values"]} == {DATE_MASK}
    same = CS.facts(values=identity(extra={("aadhaar_dob_as_printed", AAD): "2012-03-14"}))
    assert of(run(same), "DQ-002") == []


def test_DQ_003_gender_mismatch_is_high_and_case_insensitive() -> None:
    f = CS.facts(values=identity(extra={("aadhaar_gender_as_printed", AAD): "female"}))
    (finding,) = of(run(f), "DQ-003")
    assert finding.severity.value == "high"
    same = CS.facts(values=identity(extra={("aadhaar_gender_as_printed", AAD): "MALE"}))
    assert of(run(same), "DQ-003") == []


# --- DQ-004 parents ---------------------------------------------------------------------------


def test_DQ_004_parent_names_are_clamped_to_medium_high() -> None:
    f = CS.facts(
        values=identity(
            extra={
                ("father_name", "birth_certificate"): "Ramana Kommineni",  # ORDER -> info -> medium
                ("mother_name", "parent_form"): "Gorantla Padma",  # DIFFERENT -> blocker -> high
                ("father_name", "tc_incoming"): "Kommineni Ramana",  # EXACT -> none
            }
        )
    )
    found = {(x.attribute_key, x.sources): x for x in of(run(f), "DQ-004")}
    assert set(found) == {
        ("father_name", ("admission_register", "birth_certificate")),
        ("mother_name", ("admission_register", "parent_form")),
    }
    assert found["father_name", (REG, "birth_certificate")].severity.value == "medium"
    assert found["mother_name", (REG, "parent_form")].severity.value == "high"


# --- DQ-005 / DQ-006 / DQ-009 (profiles) ----------------------------------------------------


def test_DQ_005_missing_required_fields_are_blockers_per_profile() -> None:
    f = CS.facts(values=identity(extra={("admission_no", REG): "2019/0001"}))
    del f.canonical["mother_name"]
    found = of(run(f, profiles=["cisce-registration-2026"]), "DQ-005")
    assert [(x.attribute_key, x.severity.value) for x in found] == [("mother_name", "blocker")]
    (finding,) = found
    assert det(finding)["params"] == {
        "profile": "cisce-registration-2026",
        "field": "mother_name",
    }
    assert det(finding)["reason"] == "missing"
    assert finding.explanation_code == "DQ-005"
    assert of(run(f), "DQ-005") == []  # profile rules need a profile


def test_DQ_005_provisional_identity_values_get_the_profile_severity() -> None:
    values = identity(extra={("admission_no", REG): "2019/0002"})
    canonical: dict[str, Any] = {k: v for (k, _), v in values.items()}
    canonical["dob"] = CanonicalFact("2012-03-14", "birth_certificate", provisional=True)
    f = CS.facts(values=values, canonical=canonical)
    (finding,) = of(run(f, profiles=["cisce-registration-2026"]), "DQ-005")
    assert finding.attribute_key == "dob"
    assert finding.severity.value == "low"
    assert finding.explanation_code == "DQ-005-UNVERIFIED"
    assert det(finding)["canonical_source"] == "birth_certificate"
    assert load_explanations().has("DQ-005-UNVERIFIED")


def test_DQ_005_and_DQ_009_findings_differ_per_profile() -> None:
    f = CS.facts(values=identity(extra={("admission_no", REG): "2019/0003"}))
    found = run(f, profiles=["cisce-registration-2026", "udise-plus"])
    dq5 = {(det(x)["profile_key"], x.attribute_key) for x in of(found, "DQ-005")}
    assert dq5 == {
        ("udise-plus", "mother_tongue"),
        ("udise-plus", "category"),
        ("udise-plus", "admission_date"),  # 2026-27 profile (ADR-0039)
    }
    (dq9,) = of(found, "DQ-009")
    assert det(dq9)["profile_key"] == "udise-plus"
    assert det(dq9)["missing"] == list(load_engine_config().apaar_attributes)
    assert dq9.severity.value == "medium"
    fps = [fingerprint_of(x) for x in found]
    assert len(fps) == len(set(fps))


def test_DQ_009_complete_aadhaar_details_raise_nothing() -> None:
    aadhaar = {
        ("aadhaar_last4", AAD): "4821",
        ("aadhaar_name_as_printed", AAD): "Kommineni Venkata Sai",
        ("aadhaar_dob_as_printed", AAD): "2012-03-14",
        ("aadhaar_gender_as_printed", AAD): "male",
    }
    f = CS.facts(values=identity(extra=aadhaar))
    assert of(run(f, profiles=["udise-plus"]), "DQ-009") == []


@pytest.mark.parametrize(
    ("name", "issues"),
    [
        ("Kommineni Venkata Sai", []),
        ("K. Venkata Sai", []),
        ("D'Souza Ravi", []),
        ("Kommineni Venkata Sai " * 3, ["too_long"]),
        ("కొమ్మినేని వెంకట సాయి", ["not_latin"]),
        ("Venkata Sai 2", ["digits"]),
        ("Venkata_Sai@", ["symbols"]),
        ("K", ["too_short"]),
    ],
)
def test_DQ_006_name_format_issues(name: str, issues: list[str]) -> None:
    nf = load_profiles()["cisce-registration-2026"].name_format
    assert nf is not None
    assert format_issues(name, nf) == issues


def test_DQ_006_finding_names_the_first_issue_and_masks_the_value() -> None:
    f = CS.facts(values=identity("కొమ్మినేని వెంకట సాయి 2", {("admission_no", REG): "A1"}))
    (finding,) = of(run(f, profiles=["cisce-registration-2026"]), "DQ-006")
    assert finding.attribute_key == "full_name"
    assert det(finding)["params"] == {"profile": "cisce-registration-2026", "issue": "not_latin"}
    assert det(finding)["issues"] == ["not_latin", "digits"]
    assert "వెంకట" not in json.dumps(dict(finding.details), ensure_ascii=False)


# --- DQ-007 age band ---------------------------------------------------------------------------


@pytest.mark.parametrize(("dob", "flagged"), [("2012-03-14", False), ("2006-01-01", True)])
def test_DQ_007_age_outside_the_class_band(dob: str, flagged: bool) -> None:
    f = CS.facts(values=identity(extra={("dob", REG): dob}), enrolments=[CS.enrolment("IX")])
    found = of(run(f), "DQ-007")
    assert bool(found) is flagged
    if flagged:
        assert det(found[0])["params"] == {"n": 20, "c": "IX"}
        assert found[0].severity.value == "medium"


def test_DQ_007_only_current_year_enrolments_count() -> None:
    old = CS.enrolment("IX", current=False)
    f = CS.facts(values=identity(extra={("dob", REG): "2006-01-01"}), enrolments=[old])
    assert of(run(f), "DQ-007") == []


# --- DQ-008 duplicates -------------------------------------------------------------------------


def test_DQ_008_possible_duplicate_is_raised_for_both_students() -> None:
    a = CS.facts(values=identity(), admission_no="2019/0100")
    b = CS.facts(values=identity("K. Venkata Sai"), admission_no="2019/0101")
    found = of(run(a, population=[b]), "DQ-008")
    assert {(x.student_id, det(x)["related_student_id"]) for x in found} == {
        (a.student_id, str(b.student_id)),
        (b.student_id, str(a.student_id)),
    }
    mine = next(x for x in found if x.student_id == a.student_id)
    assert det(mine)["params"] == {"student": "2019/0101"}
    assert mine.severity.value == "high"
    assert mine.match_class == "INITIALS"


def test_DQ_008_twins_and_strangers_are_not_duplicates() -> None:
    a = CS.facts(values=identity())
    twin = CS.facts(values=identity("Kommineni Surya Teja"))  # same DOB and parents
    stranger = CS.facts(
        values=identity(
            extra={("father_name", REG): "Tummala Prasad", ("mother_name", REG): "Tummala Vani"}
        )
    )  # same name and DOB, other parents
    assert of(run(a, population=[twin, stranger]), "DQ-008") == []


# --- DQ-010 / DQ-011 ---------------------------------------------------------------------------


def test_DQ_010_board_record_differs_from_register() -> None:
    f = CS.facts(
        values=identity(
            extra={
                ("full_name", "board_registration"): "K. Venkata Sai",
                ("dob", "board_registration"): "2012-03-15",
                ("gender", "board_registration"): "male",
            }
        )
    )
    found = {x.attribute_key: x for x in of(run(f), "DQ-010")}
    assert set(found) == {"full_name", "dob"}
    assert found["full_name"].match_class == "INITIALS"
    assert found["full_name"].severity.value == "high"
    assert det(found["dob"])["differs_in"] == ["day"]
    assert found["dob"].route_codes[0] == "ROUTE-BOARD"


def test_DQ_011_udise_differs_from_register() -> None:
    f = CS.facts(values=identity(extra={("mother_name", "udise_plus"): "Kommineni Saradha"}))
    (finding,) = of(run(f), "DQ-011")
    assert (finding.attribute_key, finding.severity.value) == ("mother_name", "medium")
    assert finding.route_codes[0] == "ROUTE-UDISE"


# --- DQ-012 ------------------------------------------------------------------------------------


def test_DQ_012_two_active_enrolments() -> None:
    f = CS.facts(
        values=identity(), enrolments=[CS.enrolment("IX"), CS.enrolment("VIII", current=False)]
    )
    (finding,) = of(run(f), "DQ-012")
    assert finding.severity.value == "high"
    assert len(det(finding)["enrollment_ids"]) == 2
    single = CS.facts(values=identity(), enrolments=[CS.enrolment("IX")])
    assert of(run(single), "DQ-012") == []


# --- fingerprints (FR-DQ-004) ------------------------------------------------------------------


def test_FR_DQ_004_fingerprints_are_stable_and_value_free() -> None:
    sid = uuid.uuid4()
    values = identity(extra={("aadhaar_name_as_printed", AAD): "K. Venkata Sai"})
    first = of(run(CS.facts(student_id=sid, values=values)), "DQ-001")[0]
    values[("aadhaar_name_as_printed", AAD)] = "Yarlagadda Durga Bhavani"
    second = of(run(CS.facts(student_id=sid, values=values)), "DQ-001")[0]
    assert fingerprint_of(first) == fingerprint_of(second)
    assert first.severity != second.severity


def test_every_finding_explanation_renders_in_english_and_telugu() -> None:
    f = CS.facts(
        values=identity(
            "కొమ్మినేని 2",
            {
                ("aadhaar_name_as_printed", AAD): "Yarlagadda Durga",
                ("dob", REG): "2004-01-01",
            },
        ),
        enrolments=[CS.enrolment("IX"), CS.enrolment("X", current=False)],
    )
    twin = CS.facts(values=identity("కొమ్మినేని 2", {("dob", REG): "2004-01-01"}))
    catalog = load_explanations()
    found = run(f, population=[twin], profiles=["cisce-registration-2026", "udise-plus"])
    assert {x.rule_id for x in found} >= {"DQ-001", "DQ-005", "DQ-006", "DQ-007", "DQ-008"}
    for x in found:
        params = {k: str(v) for k, v in det(x)["params"].items()}
        texts = catalog.bilingual(x.explanation_code, **params)
        assert all(texts.values())


def test_age_reference_uses_the_academic_year_start() -> None:
    # 15 on 2026-06-01 (born 2011-06-01) is inside IX [13, 15]; one day younger is still 14.
    for dob in ("2011-06-01", "2011-06-02"):
        f = CS.facts(
            values=identity(extra={("dob", REG): dob}),
            enrolments=[CS.enrolment("IX", starts_on=dt.date(2026, 6, 1))],
        )
        assert of(run(f), "DQ-007") == []
