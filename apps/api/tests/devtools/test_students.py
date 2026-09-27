"""Synthetic students with injected mismatches at known rates (docs/12 §3; NFR-MNT-002;
FR-DQ-001, FR-DQ-003; invariant 4). Pure: no database. Synthetic data only.

The school-scale DQ run here evaluates every rule and export profile over a whole synthetic
school (small profile, 400 students) and holds it to the precision gate of
``tests/dq/test_precision.py`` (>= 0.95 for blocker/high findings per rule).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import io
import json
import re
import zipfile
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml
from PIL import Image
from pydantic import SecretStr

from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.redaction import contains_full_aadhaar, verhoeff_valid
from app.devtools import dq_eval
from app.devtools import seed_synthetic as cli
from app.devtools import students as st
from app.devtools.corpus import build_corpus
from app.devtools.plan import build_plan
from app.devtools.register_pages import FAKE_SCRIPT_KEYWORD, build_register_pages
from app.devtools.students import INJECTIONS, SchoolStudents, build_students, target_count
from app.documents import filetypes
from app.extraction.providers import FakeExtractionProvider
from app.extraction.sanitize import page_has_aadhaar
from app.students.definitions import AttributeDef, CanonicalPolicy, validate_value

SEED = 20260926
RULES = [f"DQ-{n:03d}" for n in range(1, 13)]
DIGIT_RUN = re.compile(r"\d[\d\s-]{10,}\d")
ATTRIBUTES_YAML = Path(__file__).resolve().parents[2] / "app" / "students" / "attributes.yaml"
TODAY = dt.date(2026, 9, 27)


@cache
def _plan(tenants: int = 2) -> Any:
    return build_plan(seed=SEED, tenants=tenants)


@cache
def _school(profile: str = "small", index: int = 0, seed: int = SEED) -> SchoolStudents:
    plan = build_plan(seed=seed, tenants=2)
    return build_students(plan.tenants[index], dataset_version="v1", seed=seed, profile=profile)


def _digit_runs(text: str) -> list[str]:
    return [
        m.group(0) for m in DIGIT_RUN.finditer(text) if len(re.sub(r"\D", "", m.group(0))) >= 12
    ]


# --- determinism and shape -----------------------------------------------------------------------


def test_docs12_s3_same_seed_same_students() -> None:
    plan = _plan()
    first = build_students(plan.tenants[0], dataset_version="v1", seed=SEED, profile="small")
    again = build_students(plan.tenants[0], dataset_version="v1", seed=SEED, profile="small")
    assert first == again
    assert first.manifest() == again.manifest()
    other_seed = build_students(
        plan.tenants[0], dataset_version="v1", seed=SEED + 1, profile="small"
    )
    assert other_seed.students != first.students
    other_school = _school("small", 1)
    assert [s.values for s in other_school.students] != [s.values for s in first.students]


@pytest.mark.parametrize(("profile", "size"), [("small", 400), ("full", 2000), ("none", 0)])
def test_docs12_s3_profile_sizes(profile: str, size: int) -> None:
    school = _school(profile)
    assert len(school.students) == size
    assert len({s.admission_no for s in school.students}) == size
    if size:
        per_section = Counter(s.section for s in school.students)
        assert set(per_section) == set(_plan().tenants[0].sections)
        assert max(per_section.values()) - min(per_section.values()) <= 1


def test_count_overrides_profile_and_is_bounded() -> None:
    tenant = _plan().tenants[0]
    assert len(build_students(tenant, dataset_version="v1", seed=SEED, count=37).students) == 37
    with pytest.raises(ValueError, match="between 0 and"):
        build_students(tenant, dataset_version="v1", seed=SEED, count=st.MAX_STUDENTS + 1)
    with pytest.raises(ValueError, match="unknown profile"):
        build_students(tenant, dataset_version="v1", seed=SEED, profile="huge")


def test_every_student_has_register_identity_guardians_and_one_enrolment() -> None:
    school = _school("full")
    for s in school.students:
        assert s.value("full_name", st.REG)
        assert s.value("dob", st.REG)
        assert s.value("admission_no", st.REG) == s.admission_no
        assert s.guardians
        assert s.guardians[0].relationship == "father"
        assert s.guardians[0].is_primary
        assert all(g.full_name for g in s.guardians)
        assert (s.previous_section is not None) == (s.injection == "enrolled_twice")
        assert len(s.values) <= 40  # StudentCreate limit
        keys = [(k, src) for k, src, _ in s.values]
        assert len(keys) == len(set(keys))
        if s.section[0] in st.BOARD_CLASSES and s.injection not in ("duplicate_of",):
            assert s.value("full_name", st.BOARD)
        elif s.section[0] not in st.BOARD_CLASSES:
            assert s.value("full_name", st.BOARD) is None


def _definitions() -> dict[str, AttributeDef]:
    raw = yaml.safe_load(ATTRIBUTES_YAML.read_text(encoding="utf-8"))
    return {
        key: AttributeDef(
            key=key,
            data_type=a["data_type"],
            classification=a["classification"],
            is_identity=a["is_identity"],
            policy=CanonicalPolicy.from_json(a["canonical_policy"]),
            label_en=a["label_en"],
            label_te=a["label_te"],
            validation=a.get("validation", {}),
        )
        for key, a in raw["attributes"].items()
    }


def test_FR_STU_002_every_value_passes_the_attribute_catalog() -> None:
    """The students service would accept every synthetic value (source allowed, format, dates)."""
    defs = _definitions()
    for s in _school("full").students:
        for key, source, value in s.values:
            validate_value(defs[key], source, value, today=TODAY)


def test_canonical_precedence_in_dq_eval_matches_the_catalog() -> None:
    """dq_eval rebuilds canonical values like app.students.canonical (docs/05 §10)."""
    defs = _definitions()
    used: dict[str, set[str]] = {}
    for s in _school("small").students:
        for key, src, _ in s.values:
            used.setdefault(key, set()).add(src)
    for key in ("full_name", "dob", "gender", "father_name", "mother_name", "mother_tongue"):
        expected = tuple(src for src in defs[key].policy.precedence if src in used[key])
        got = dq_eval._PRECEDENCE.get(key, dq_eval._DEFAULT_PRECEDENCE)
        assert got == expected, key
    identity = {k for k, d in defs.items() if d.is_identity}
    assert identity == dq_eval.IDENTITY_KEYS


def test_dates_of_birth_fit_the_class_band_and_are_unique() -> None:
    school = _school("full")
    band_breakers = {"age_out_of_band"}
    seen: Counter[str] = Counter()
    for s in school.students:
        dob = dt.date.fromisoformat(s.value("dob", st.REG) or "")
        age = st.YEAR_START.year - dob.year - ((dob.month, dob.day) > (6, 1))
        if s.injection in band_breakers:
            assert age == st.CLASS_AGE[s.section[0]] + 2
        else:
            assert age == st.CLASS_AGE[s.section[0]], s.admission_no
        if s.injection != "duplicate_of":
            seen[s.value("dob", st.REG) or ""] += 1
    duplicates = {d for d, n in seen.items() if n > 1}
    # Only a moved date of birth (DQ-007) may land on another child's date.
    assert len(duplicates) <= len([s for s in school.students if s.injection in band_breakers])


# --- rates --------------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["small", "full"])
def test_docs12_s3_injection_rates_are_exact_and_cover_every_rule(profile: str) -> None:
    school = _school(profile)
    total = len(school.students)
    counts = Counter(s.injection for s in school.students if s.injection)
    for kind in INJECTIONS:
        want = target_count(kind, total) * (2 if kind.key == "duplicate_record" else 1)
        got = counts[kind.key] + (counts["duplicate_of"] if kind.key == "duplicate_record" else 0)
        assert got == want == school.counts[kind.key], kind.key
        if profile == "full":
            share = got / total / (2 if kind.key == "duplicate_record" else 1)
            assert abs(share - kind.rate) <= 0.0005, kind.key  # rounding only
    covered = {e.rule_id for k in INJECTIONS for e in k.expects if school.counts.get(k.key)}
    assert covered == set(RULES)
    injected = sum(1 for s in school.students if s.injection)
    assert 0.2 <= injected / total <= 0.35


def test_duplicate_pairs_share_class_date_and_parents() -> None:
    school = _school("full")
    by_no = {s.admission_no: s for s in school.students}
    pairs = [s for s in school.students if s.injection == "duplicate_of"]
    assert pairs
    for second in pairs:
        assert second.related is not None
        first = by_no[second.related]
        assert first.injection == "duplicate_record"
        assert first.related == second.admission_no
        assert first.section[0] == second.section[0]
        for key in ("dob", "father_name", "mother_name", "gender"):
            assert first.value(key, st.REG) == second.value(key, st.REG), key
        assert first.value("full_name", st.REG) != second.value("full_name", st.REG)
        assert {src for _, src, _ in second.values} == {st.REG}


# --- the school-scale DQ run ---------------------------------------------------------------------


@pytest.mark.parametrize("index", [0, 1])
def test_FR_DQ_003_school_scale_precision_gate_on_the_small_profile(index: int) -> None:
    school = _school("small", index)
    scores = dq_eval.school_score(school)
    assert set(scores) == set(RULES)
    for rule, s in scores.items():
        assert s.precision >= dq_eval.MIN_PRECISION, (rule, s)
        assert s.unexpected == [], (rule, s.unexpected[:5])
        assert s.any_recall == 1.0, (rule, s)
        assert s.recall >= 0.9, (rule, s)
    serious = sum(s.flagged_serious for s in scores.values())
    assert serious >= 50


def test_score_counts_false_positives_and_misses() -> None:
    expected = {
        "SYN-00001": (st.Expect("DQ-001", True),),
        "SYN-00002": (st.Expect("DQ-001", False),),
    }
    observed = [
        dq_eval.Observed("SYN-00002", "DQ-001", serious=True),  # benign variant flagged high
        dq_eval.Observed("SYN-00003", "DQ-002", serious=True),  # not injected at all
        dq_eval.Observed("SYN-00003", "DQ-005", False, "DQ-005-UNVERIFIED"),  # baseline: ignored
    ]
    scores = dq_eval.score(observed, expected)
    assert scores["DQ-001"].precision == 0.0
    assert scores["DQ-001"].recall == 0.0
    assert scores["DQ-001"].any_recall == 0.5
    assert scores["DQ-002"].unexpected == ["SYN-00003"]
    assert "DQ-005" not in scores


# --- no Aadhaar numbers, no names in the manifest ------------------------------------------


def test_invariant_4_no_full_aadhaar_in_values_or_manifest() -> None:
    school = _school("full")
    for s in school.students:
        for key, _source, value in s.values:
            assert not contains_full_aadhaar(value), key
            assert not _digit_runs(value), key
            if key == "aadhaar_last4":
                assert re.fullmatch(r"\d{4}", value)
        for g in s.guardians:
            assert not _digit_runs(g.full_name + (g.address or ""))
    text = json.dumps(school.manifest(), ensure_ascii=False)
    assert not _digit_runs(text)
    assert not contains_full_aadhaar(text)


def test_SEC_008_manifest_holds_ids_and_codes_only() -> None:
    school = _school("small")
    text = json.dumps(school.manifest(), ensure_ascii=False)
    for s in school.students:
        for key, _source, value in s.values:
            if key in ("full_name", "father_name", "mother_name", "dob"):
                assert value not in text, key


# --- register pages and corpus --------------------------------------------------------------------


def _pages(profile: str = "small") -> list[Any]:
    school = _school(profile)
    return build_register_pages(
        school, dataset_version="v1", seed=SEED, tenant_index=0, batches=2, pages_per_batch=2
    )


def test_register_pages_follow_the_fake_provider_conventions() -> None:
    pages = _pages()
    assert [(p.batch_no, p.page_no) for p in pages] == [(1, 1), (1, 2), (2, 1), (2, 2)]
    assert len({p.title for p in pages}) == len(pages)
    school = _school("small")
    by_no = {s.admission_no: s for s in school.students}
    provider = FakeExtractionProvider(Settings(env=Environment.CI))
    for page in pages:
        assert filetypes.check_head(filetypes.PNG, page.png[:4096]) is None
        assert filetypes.check_tail(filetypes.PNG, page.png[-64:]) is None
        with Image.open(io.BytesIO(page.png)) as img:
            assert FAKE_SCRIPT_KEYWORD in img.info
        result = provider.extract(page.png, language_hints=["en", "te"])
        assert [r["admission_no"].value for r in result.rows] == list(page.admission_nos)
        for row in result.rows:
            student = by_no[row["admission_no"].value]
            assert row["dob"].value == student.value("dob", st.REG)
            assert row["full_name"].value == (student.value("full_name", st.REG) or "").upper()
        assert result.spans
        assert all(span.box is not None for span in result.spans)


def test_PRV_016_register_page_look_alike_is_not_a_valid_aadhaar() -> None:
    pages = _pages()
    flagged = [p for p in pages if p.has_aadhaar_like]
    assert len(flagged) == 1
    provider = FakeExtractionProvider(Settings(env=Environment.CI))
    for page in pages:
        result = provider.extract(page.png, language_hints=["en"])
        numbers = [m for span in result.spans for m in _digit_runs(span.text)]
        if page.has_aadhaar_like:
            assert numbers
            assert all(not verhoeff_valid(re.sub(r"\D", "", n)) for n in numbers)
        else:
            assert numbers == []
        # Not a Verhoeff-valid number, so the redaction pipeline leaves the page as it is.
        assert not page_has_aadhaar(result)


def test_docs12_s3_corpus_documents_are_valid_docx_in_en_and_te() -> None:
    docs = build_corpus("Sri Venkateswara Synthetic High School")
    assert {d.language for d in docs} == {"en", "te", "mixed"}
    assert {d.doc_type for d in docs} == {"circular", "minutes", "policy"}
    assert len({d.title for d in docs}) == len(docs)
    for doc in docs:
        data = doc.docx()
        assert data == doc.docx()  # byte-for-byte deterministic
        assert filetypes.check_head(filetypes.DOCX, data[:4096]) is None
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            text = archive.read("word/document.xml").decode("utf-8")
        assert "Synthetic" in text
        assert not _digit_runs(text)
        if doc.language == "te":
            assert re.search(r"[ఀ-౿]", text)  # Telugu block


def test_student_specs_are_frozen() -> None:
    s = _school("small").students[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.roll_no = "99"  # type: ignore[misc]


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_docs12_s3_student_profiles_refuse_outside_local_and_ci(env: Environment) -> None:
    """The environment check runs before any student is built (invariant 11)."""
    settings = Settings(
        env=env,
        key_wrapper=KeyWrapperKind.KMS,
        database_url=SecretStr("postgresql+psycopg://sos_app:secret@db.internal/schoolos"),
        platform_database_url=SecretStr(
            "postgresql+psycopg://sos_platform:secret@db.internal/schoolos"
        ),
        service_token_key=SecretStr("x" * 48),
    )
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["--profile", "full"], settings=settings, stdout=out, stderr=err)
    assert code == cli.EXIT_REFUSED
    assert "disabled outside local/ci" in err.getvalue()
    assert out.getvalue() == ""
