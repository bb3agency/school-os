"""``make seed-synthetic --profile small`` against a real database: students, guardians,
enrolments, per-source values with injected mismatches, register pages and circulars, then a
school-scale data-quality run scored against the manifest (docs/12 §3; NFR-MNT-002;
FR-STU-001..008, FR-DQ-001..006, FR-DOC-001; invariant 4; SEC-008).

One school is seeded per module with a unique code prefix (the session database is shared with
other suites) and an in-memory object store (tests/documents/support.py). Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.audit import service as audit
from app.authz.kv import InMemoryKV, set_kv_store
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import tenant_session
from app.core.redaction import contains_full_aadhaar, verhoeff_valid
from app.devtools import dq_eval, seeder
from app.devtools import seed_synthetic as cli
from app.devtools.plan import build_plan
from app.devtools.students import SchoolStudents, build_students
from app.documents.schemas import UploadOut
from app.documents.storage import set_object_store
from app.dq import service as dq
from app.students import crypto
from app.students import service as students

pytestmark = pytest.mark.db

SEED = 11
PROFILES = (None, "cisce-registration-2026", "udise-plus")
CI_SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
DIGITS_12 = re.compile(r"(?<!\d)(?:\d[\s-]?){11}\d(?!\d)")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _aadhaar_like(text: str) -> list[str]:
    """12-digit runs (spaces or hyphens allowed) outside UUIDs: random IDs are not Aadhaar."""
    return DIGITS_12.findall(UUID_RE.sub(" ", text))


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


DOCS = _load(
    "sos_test_documents_support", Path(__file__).resolve().parents[1] / "documents" / "support.py"
)


@dataclass
class Seeded:
    prefix: str
    tenant_id: uuid.UUID
    school: SchoolStudents
    first: dict[str, Any]
    second: dict[str, Any]
    printed: str
    out_dir: Path
    store: Any


def _run(argv: list[str], store: Any) -> tuple[int, str, str]:
    def upload(presigned: UploadOut, data: bytes, content_type: str, filename: str) -> None:
        assert store.browser_post(presigned.fields, data, content_type) == 204

    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        argv,
        settings=CI_SETTINGS,
        stdout=out,
        stderr=err,
        wrapper=LocalDevKeyWrapper(CI_SETTINGS),
        owner_bootstrap=seeder.PlatformOwnerBootstrap(),
        uploader=upload,
    )
    return code, out.getvalue(), err.getvalue()


@pytest.fixture(scope="module")
def seeded(
    admin_engine: Engine,
    app_engine: Engine,
    platform_engine: Engine,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Seeded]:
    set_kv_store(InMemoryKV())
    crypto.set_key_wrapper(LocalDevKeyWrapper(CI_SETTINGS))
    store = DOCS.MemoryStore()
    set_object_store(store)
    capture = io.StringIO()
    prefix = f"s{uuid.uuid4().hex[:8]}"
    out_dir = tmp_path_factory.mktemp("synthetic")
    argv = ["--code-prefix", prefix, "--tenants", "1", "--seed", str(SEED), "--profile", "small"]
    argv += ["--out-dir", str(out_dir)]
    try:
        code, out, err = _run(argv, store)
        assert code == 0, err
        capture.write(out + err)
        code2, out2, err2 = _run(argv, store)
        assert code2 == 0, err2
        capture.write(out2 + err2)
        plan = build_plan(seed=SEED, tenants=1, code_prefix=prefix)
        school = build_students(plan.tenants[0], dataset_version="v1", seed=SEED, profile="small")
        yield Seeded(
            prefix,
            plan.tenants[0].tenant_id,
            school,
            json.loads(out),
            json.loads(out2),
            capture.getvalue(),
            out_dir,
            store,
        )
    finally:
        set_object_store(None)
        set_kv_store(None)


def _office_admin(tenant_id: uuid.UUID) -> tuple[uuid.UUID, Any]:
    plan_staff = build_plan(seed=SEED, tenants=1).tenants[0].staff
    spec = next(s for s in plan_staff if s.role == "office_admin")
    with tenant_session(tenant_id) as session:
        members = seeder._members_by_email(session)
    member = next(m for m in members.values() if m.email and m.email.startswith("office-admin.1@"))
    assert spec.role == "office_admin"
    return member.id, seeder._member_context(tenant_id, member)


# --- seeding -----------------------------------------------------------------------------------


def test_docs12_s3_small_profile_creates_students_guardians_documents(seeded: Seeded) -> None:
    tenant = seeded.first["tenants"][0]
    assert tenant["outcome"] == "created"
    counts, created = tenant["counts"], tenant["created"]
    school = seeded.school
    assert counts["students"] == created["students"] == len(school.students) == 400
    assert created["guardians"] == sum(len(s.guardians) for s in school.students)
    assert created["extra_enrolments"] == sum(1 for s in school.students if s.previous_section)
    assert created["register_pages"] == 2
    assert created["documents"] == 6
    assert counts["documents"] == 8
    assert seeded.first["manifests"] == [str(seeded.out_dir / f"manifest-{seeded.prefix}-a.json")]


def test_docs12_s3_rerun_adds_nothing(seeded: Seeded) -> None:
    a, b = seeded.first["tenants"][0], seeded.second["tenants"][0]
    assert b["outcome"] == "unchanged"
    assert b["created"] == {}
    assert a["counts"] == b["counts"]


def test_docs12_s3_database_matches_the_plan(seeded: Seeded, admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT s.admission_no, count(DISTINCT e.id) FILTER (WHERE e.status = 'active') "
                "AS active, count(DISTINCT sg.guardian_id) AS guardians "
                "FROM sis.students s "
                "LEFT JOIN sis.enrollments e ON e.tenant_id = s.tenant_id AND e.student_id = s.id "
                "LEFT JOIN sis.student_guardians sg "
                "ON sg.tenant_id = s.tenant_id AND sg.student_id = s.id "
                "WHERE s.tenant_id = :t GROUP BY s.admission_no"
            ),
            {"t": seeded.tenant_id},
        ).all()
        values: int = c.execute(
            text("SELECT count(*) FROM sis.attribute_values WHERE tenant_id = :t"),
            {"t": seeded.tenant_id},
        ).scalar_one()
    by_no = {r.admission_no: r for r in rows}
    assert len(by_no) == len(seeded.school.students)
    for s in seeded.school.students:
        row = by_no[s.admission_no]
        assert row.active == (2 if s.previous_section else 1), s.admission_no
        assert row.guardians == len(s.guardians)
    assert values == sum(len(s.values) for s in seeded.school.students)


def test_docs12_s3_manifest_file_matches_the_plan(seeded: Seeded) -> None:
    path = seeded.out_dir / f"manifest-{seeded.prefix}-a.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = seeded.school.manifest()
    for key in ("students", "kinds", "injections", "profile"):
        assert data[key] == expected[key], key
    assert data["tenant_id"] == str(seeded.tenant_id)
    assert len(data["register_pages"]) == 2
    assert sum(p["has_aadhaar_like_number"] for p in data["register_pages"]) == 1
    assert not _aadhaar_like(path.read_text(encoding="utf-8"))


def test_docs12_s3_register_pages_and_circulars_are_stored(seeded: Seeded) -> None:
    prefix = f"t/{seeded.tenant_id}/"
    stored = [k for k in seeded.store.objects if k.startswith(prefix) and "/uploads/" not in k]
    types = sorted(seeded.store.objects[k].content_type for k in stored)
    assert types.count("image/png") == 2
    assert (
        types.count("application/vnd.openxmlformats-officedocument.wordprocessingml.document") == 6
    )


# --- school-scale data-quality run ---------------------------------------------------------


@pytest.fixture(scope="module")
def db_findings(seeded: Seeded, admin_engine: Engine) -> list[dq_eval.Observed]:
    user_id, ctx = _office_admin(seeded.tenant_id)
    for profile in PROFILES:
        with tenant_session(seeded.tenant_id, user_id) as session:
            run = dq.run_checks(session, ctx, profile_key=profile)
        assert run.status == "completed"
        assert run.stats is not None
        assert run.stats["students"] == len(seeded.school.students)
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT s.admission_no, f.rule_id, f.severity, f.explanation_code "
                "FROM sis.dq_findings f JOIN sis.students s "
                "ON s.tenant_id = f.tenant_id AND s.id = f.student_id "
                "WHERE f.tenant_id = :t AND f.status IN ('open', 'reopened')"
            ),
            {"t": seeded.tenant_id},
        ).all()
    return [
        dq_eval.Observed(r.admission_no, r.rule_id, r.severity in ("blocker", "high"),
                         r.explanation_code)
        for r in rows
    ]  # fmt: skip


def test_FR_DQ_003_school_scale_db_run_meets_the_precision_gate(
    seeded: Seeded, db_findings: list[dq_eval.Observed]
) -> None:
    scores = dq_eval.score(db_findings, seeded.school.expected())
    assert set(scores) == {*(f"DQ-{n:03d}" for n in range(1, 13)), "DQ-022"}
    for rule, s in scores.items():
        assert s.precision >= dq_eval.MIN_PRECISION, (rule, s)
        assert s.unexpected == [], (rule, s.unexpected[:5])
        assert s.any_recall == 1.0, (rule, s)
        assert s.recall >= 0.9, (rule, s)


def test_db_run_reproduces_the_in_memory_run(
    seeded: Seeded, db_findings: list[dq_eval.Observed]
) -> None:
    """The pure evaluation (manifest ``expected_scores``) predicts the database run exactly."""
    memory = dq_eval.observed_in_memory(seeded.school, dq_eval.evaluate_in_memory(seeded.school))

    def key(items: list[dq_eval.Observed]) -> set[tuple[str, str, bool, str | None]]:
        return {(o.admission_no, o.rule_id, o.serious, o.explanation_code) for o in items}

    assert key(db_findings) == key(memory)


# --- invariants --------------------------------------------------------------------------------


def test_invariant_4_no_full_aadhaar_stored_anywhere(seeded: Seeded, admin_engine: Engine) -> None:
    queries = {
        "values": "SELECT coalesce(value_text, '') || ' ' || coalesce(value_norm, '') "
        "FROM sis.attribute_values WHERE tenant_id = :t",
        "profiles": "SELECT row_to_json(p)::text FROM sis.student_profiles p WHERE tenant_id = :t",
        "guardians": "SELECT full_name FROM sis.guardians WHERE tenant_id = :t",
        "audit": "SELECT summary::text FROM audit.events WHERE tenant_id = :t",
        "documents": "SELECT title FROM kb.documents WHERE tenant_id = :t",
        "findings": "SELECT details::text FROM sis.dq_findings WHERE tenant_id = :t",
    }
    with admin_engine.connect() as c:
        for name, sql in queries.items():
            for (value,) in c.execute(text(sql), {"t": seeded.tenant_id}):
                assert not contains_full_aadhaar(value or ""), name
                for match in _aadhaar_like(value or ""):
                    digits = re.sub(r"\D", "", match)
                    assert not verhoeff_valid(digits), name
    user_id, ctx = _office_admin(seeded.tenant_id)
    with tenant_session(seeded.tenant_id, user_id) as session:
        ids = students.list_students_in_scope(session, ctx)
        last4 = students.source_values(session, ids, ["aadhaar_last4"], include_sensitive=True)
    stored = [v["aadhaar_last4"]["aadhaar_as_printed"].value for v in last4.values()]
    assert stored
    assert all(v is not None and re.fullmatch(r"\d{4}", v) for v in stored)


def test_SEC_008_output_holds_no_student_names(seeded: Seeded) -> None:
    names = {
        v
        for s in seeded.school.students[:100]
        for k, _src, v in s.values
        if k in ("full_name", "father_name", "mother_name")
    }
    assert names
    for name in names:
        assert name not in seeded.printed


def test_FR_AUD_001_audit_chain_verifies_after_students(
    seeded: Seeded, admin_engine: Engine
) -> None:
    with tenant_session(seeded.tenant_id) as session:
        result = audit.verify_chain(session, seeded.tenant_id)
    assert result.ok, result
    with admin_engine.connect() as c:
        actions: set[str] = set(
            c.execute(
                text("SELECT DISTINCT action FROM audit.events WHERE tenant_id = :t"),
                {"t": seeded.tenant_id},
            ).scalars()
        )
    assert {
        "student.created",
        "enrollment.created",
        "guardian.created",
        "document.registered",
    } <= actions


def test_cli_rejects_a_student_count_out_of_range() -> None:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(["--students", "99999"], settings=CI_SETTINGS, stdout=out, stderr=err)
    assert code == cli.EXIT_REFUSED
    assert "--students" in err.getvalue()


def test_docs12_s3_aadhaar_check_ignores_digit_heavy_ids_only() -> None:
    # Random UUIDs can hold 12+ digits within or across groups (about 1 in 100 tenant IDs).
    digit_heavy = "0192f3a4-0000-7000-8123-123456789012"
    assert _aadhaar_like(f'{{"tenant_id": "{digit_heavy}"}}') == []
    assert _aadhaar_like("id 0192f3a4-1111-2222-3333-44445555a666 ok") == []
    # Real 12-digit runs are still found, with or without separators, next to an ID too.
    assert _aadhaar_like("2345 6789 0123") != []
    assert _aadhaar_like("234567890123") != []
    assert _aadhaar_like(f"{digit_heavy} 2345-6789-0123") != []
