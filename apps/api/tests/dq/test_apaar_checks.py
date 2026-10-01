"""APAAR rules on in-memory synthetic facts (ADR-0037; FR-DQ-021, FR-DQ-022, DQ-009).

DQ-009 is raised only for students without a verified APAAR ID; DQ-021 flags an APAAR ID that is
not 12 digits and one APAAR ID on two students; DQ-022 compares UDISE+ with Aadhaar-as-printed
for students without a verified APAAR ID. No database; synthetic values only.
"""

from __future__ import annotations

import random
import sys
import uuid
from typing import Any

from app.devtools.fake_ids import synthetic_apaar_id
from app.dq.checks import CanonicalFact, build_checks, evaluate, fingerprint_of
from app.dq.explanations import Language, load_explanations
from app.dq.masking import DATE_MASK
from app.dq.rules import Finding, load_rules

CS = sys.modules["sos_test_dq_checks_support"]
REG = "admission_register"
AAD = "aadhaar_as_printed"
UDISE = "udise_plus"
CHECKS_ALL = build_checks(load_rules())
RNG = random.Random(2026)
APAAR = synthetic_apaar_id(RNG)


def run(*students: Any, **kw: Any) -> list[Finding]:
    return evaluate(CS.context(students, **kw), CHECKS_ALL)


def of(findings: list[Finding], rule_id: str) -> list[Finding]:
    return [f for f in findings if f.rule_id == rule_id]


def identity(extra: dict[tuple[str, str], str] | None = None) -> dict[tuple[str, str], str]:
    return {
        ("full_name", REG): "Kommineni Venkata Sai",
        ("dob", REG): "2012-03-14",
        ("gender", REG): "male",
        ("father_name", REG): "Kommineni Ramana",
        ("mother_name", REG): "Kommineni Sarada",
        ("admission_no", REG): "2019/0101",
        **(extra or {}),
    }


def canon(values: dict[tuple[str, str], str], apaar: CanonicalFact | None) -> dict[str, Any]:
    out: dict[str, Any] = {k: v for (k, s), v in values.items() if s == REG}
    if apaar is not None:
        out["apaar_id"] = apaar
    return out


def verified(value: str = APAAR, source: str = UDISE) -> CanonicalFact:
    return CanonicalFact(value, source, False, verified=True)


def unverified(value: str = APAAR, source: str = UDISE) -> CanonicalFact:
    return CanonicalFact(value, source, True, verified=False)


def student(
    apaar: CanonicalFact | None = None,
    extra: dict[tuple[str, str], str] | None = None,
    sid: uuid.UUID | None = None,
    admission_no: str = "2019/0101",
) -> Any:
    values = identity(extra)
    if apaar is not None:
        values[("apaar_id", apaar.source or UDISE)] = apaar.value or ""
    return CS.facts(
        student_id=sid,
        values=values,
        canonical=canon(values, apaar),
        admission_no=admission_no,
    )


def test_FR_DQ_001_catalogue_has_the_apaar_rules_with_bilingual_text() -> None:
    rules = load_rules()
    catalog = load_explanations()
    assert rules["DQ-009"].version == 2, "behaviour changed: bump the version"
    assert rules["DQ-021"].severity.level is not None
    assert rules["DQ-021"].severity.level.value == "blocker"
    assert rules["DQ-022"].severity.level is not None
    assert rules["DQ-022"].severity.level.value == "high"
    for code in ("DQ-021", "DQ-021-DUPLICATE", "DQ-022", "ROUTE-APAAR"):
        entry = catalog.get(code)
        assert entry.text(Language.EN)
        assert entry.text(Language.TE)
    assert catalog.render("DQ-021-DUPLICATE", Language.EN, student="2019/0007") == (
        "Same APAAR ID as 2019/0007. One of them is wrong."
    )
    assert "APAAR generation will fail" in catalog.render("DQ-022", Language.EN)


# --- DQ-009 only without a verified APAAR ID ---------------------------------------------------


def test_DQ_009_raised_without_an_apaar_id_and_with_an_unverified_one() -> None:
    for apaar in (None, unverified()):
        found = of(run(student(apaar), profiles=["udise-plus"]), "DQ-009")
        assert len(found) == 1, apaar


def test_DQ_009_not_raised_once_the_apaar_id_is_verified() -> None:
    assert of(run(student(verified()), profiles=["udise-plus"]), "DQ-009") == []


# --- DQ-021 format and duplicate ---------------------------------------------------------------


def test_FR_DQ_021_twelve_digit_apaar_ids_raise_nothing() -> None:
    assert of(run(student(verified())), "DQ-021") == []


def test_FR_DQ_021_wrong_format_from_any_source_is_a_blocker() -> None:
    bad = CanonicalFact("12345678901", "parent_form", True, verified=False)
    (finding,) = of(run(student(bad)), "DQ-021")
    assert finding.severity.value == "blocker"
    assert finding.attribute_key == "apaar_id"
    assert finding.sources == ("parent_form",)
    assert finding.explanation_code == "DQ-021"
    details: dict[str, Any] = dict(finding.details)
    assert details["reason"] == "format"
    assert details["values"][0]["masked"] == "••••"
    assert "12345678901" not in repr(finding)


def test_FR_DQ_021_one_apaar_id_on_two_students_flags_the_checked_one_without_the_id() -> None:
    a = student(verified(), sid=uuid.uuid4(), admission_no="2019/0101")
    b = student(unverified(), sid=uuid.uuid4(), admission_no="2019/0202")
    (finding,) = of(run(a, population=[b]), "DQ-021")
    assert finding.student_id == a.student_id
    assert finding.severity.value == "blocker"
    assert finding.explanation_code == "DQ-021-DUPLICATE"
    assert finding.sources == ("canonical",)
    details = dict(finding.details)
    assert details["reason"] == "duplicate"
    assert details["others"] == 1
    assert details["other_student_id"] == str(b.student_id)
    assert details["params"] == {"student": b.admission_no}
    assert "related_student_id" not in details, "only DQ-008 findings pair students"
    assert APAAR not in repr(finding)
    # Both checked together: one finding each, distinct fingerprints.
    both = of(run(a, b), "DQ-021")
    assert {f.student_id for f in both} == {a.student_id, b.student_id}
    assert len({fingerprint_of(f) for f in both}) == 2


def test_FR_DQ_021_three_students_name_the_first_other_record_and_count_the_rest() -> None:
    a = student(verified(), sid=uuid.uuid4(), admission_no="2019/0101")
    b = student(unverified(), sid=uuid.uuid4(), admission_no="2019/0303")
    c = student(unverified(), sid=uuid.uuid4(), admission_no="2019/0202")
    (finding,) = of(run(a, population=[b, c]), "DQ-021")
    details = dict(finding.details)
    assert details["others"] == 2
    assert details["params"] == {"student": "2019/0202"}


def test_FR_DQ_021_different_ids_are_not_duplicates() -> None:
    a = student(verified(), sid=uuid.uuid4())
    b = student(verified(synthetic_apaar_id(RNG)), sid=uuid.uuid4())
    assert of(run(a, b), "DQ-021") == []


# --- DQ-022 UDISE+ vs Aadhaar-as-printed without a verified APAAR ID ---------------------------


def mismatch() -> dict[tuple[str, str], str]:
    return {
        ("full_name", UDISE): "Kommineni Venkata Sai",
        ("aadhaar_name_as_printed", AAD): "Yarlagadda Durga Bhavani",
        ("dob", UDISE): "2012-03-14",
        ("aadhaar_dob_as_printed", AAD): "2012-04-14",
        ("gender", UDISE): "male",
        ("aadhaar_gender_as_printed", AAD): "male",
    }


def test_FR_DQ_022_udise_differs_from_aadhaar_without_an_apaar_id() -> None:
    found = {f.attribute_key: f for f in of(run(student(None, mismatch())), "DQ-022")}
    assert set(found) == {"full_name", "dob"}
    name, dob = found["full_name"], found["dob"]
    assert name.sources == (AAD, UDISE)
    assert name.severity.value == "high"
    assert name.match_class == "DIFFERENT"
    assert name.route_codes[0] == "ROUTE-UIDAI"
    dob_details: dict[str, Any] = dict(dob.details)
    assert dob_details["differs_in"] == ["month"]
    assert {v["masked"] for v in dob_details["values"]} == {DATE_MASK}


def test_FR_DQ_022_still_raised_with_an_unverified_apaar_id() -> None:
    assert len(of(run(student(unverified(), mismatch())), "DQ-022")) == 2


def test_FR_DQ_022_not_raised_once_the_apaar_id_is_verified() -> None:
    assert of(run(student(verified(), mismatch())), "DQ-022") == []


def test_FR_DQ_022_matching_details_raise_nothing() -> None:
    same = {
        ("full_name", UDISE): "Kommineni Venkata Sai",
        ("aadhaar_name_as_printed", AAD): "Kommineni Venkata Sai",
        ("dob", UDISE): "2012-03-14",
        ("aadhaar_dob_as_printed", AAD): "2012-03-14",
    }
    assert of(run(student(None, same)), "DQ-022") == []
