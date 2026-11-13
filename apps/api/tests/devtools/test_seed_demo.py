"""``make seed-demo`` and ``make demo-reset`` against a real database (FA-12; FR-DEMO-004..012).
Synthetic data only (invariant 11): no phone numbers, nothing sent anywhere.

One module-scoped run seeds generation N of the demo school twice (idempotency), then resets it:
generation N is purged through the offboarding purge and generation N + 1 is seeded. The session
database is shared, so the test reads the generation from the summary instead of assuming 1.
"""

from __future__ import annotations

import importlib.util
import io
import json
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

from app.authz.kv import InMemoryKV, set_kv_store
from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper
from app.devtools import demo, seeder
from app.devtools import seed_demo as cli
from app.documents import storage
from app.students import crypto

pytestmark = pytest.mark.db

STUDENTS = 300
CI_SETTINGS = Settings(
    env=Environment.CI,
    key_wrapper=KeyWrapperKind.LOCAL_DEV,
    local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
)
# What the scenario must leave in one demo school (at least).
EXPECTED_MIN = {
    "fin.fee_addons": 3,
    "fin.fee_addon_assignments": 10,
    "ops.compliance_certificates": 4,
    "sis.school_calendars": 1,
    "sis.calendar_entries": 4,
    "sis.dq_findings": 1,
    "sis.dq_followups": 1,
    "ops.disclosure_drafts": 1,
    "ops.weekly_briefs": 1,
    "fin.receipts": 4,
    "sis.exams": 2,
    "sis.exam_marks": 100,
    "sis.insight_flags": 1,
    "sis.tc_requests": 4,
}


@dataclass
class Seeded:
    first: dict[str, Any]
    second: dict[str, Any]
    reset: dict[str, Any]
    printed: str

    @property
    def tenant_id(self) -> uuid.UUID:
        return uuid.UUID(self.first["tenants"][0]["tenant_id"])

    @property
    def live_tenant_id(self) -> uuid.UUID:
        return uuid.UUID(self.reset["tenants"][0]["tenant_id"])


def _documents_support() -> ModuleType:
    name = "sos_test_documents_support_demo"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "documents" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _run(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(
        argv,
        settings=CI_SETTINGS,
        stdout=out,
        stderr=err,
        wrapper=LocalDevKeyWrapper(CI_SETTINGS),
        owner_bootstrap=seeder.PlatformOwnerBootstrap(),
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
    storage.set_object_store(_documents_support().MemoryStore())
    argv = ["--students", str(STUDENTS), "--no-documents"]
    argv += ["--out-dir", str(tmp_path_factory.mktemp("demo"))]
    try:
        code, out, err = _run(argv)
        assert code == 0, err
        code2, out2, err2 = _run(argv)
        assert code2 == 0, err2
        code3, out3, err3 = _run([*argv, "--reset"])
        assert code3 == 0, err3
        yield Seeded(json.loads(out), json.loads(out2), json.loads(out3), out + out2 + out3)
    finally:
        storage.set_object_store(None)
        set_kv_store(None)


def _counts(admin: Engine, tenant_id: uuid.UUID) -> dict[str, int]:
    with admin.connect() as c:
        return {
            table: int(
                c.execute(
                    text(f"SELECT count(*) FROM {table} WHERE tenant_id = :t"), {"t": tenant_id}
                ).scalar_one()
            )
            for table in EXPECTED_MIN
        }


def test_FR_DEMO_005_seeds_the_demo_school(seeded: Seeded, admin_engine: Engine) -> None:
    school = seeded.first["tenants"][0]
    generation = seeded.first["generation"]
    assert school["code"] == demo.code_for(generation)
    with admin_engine.connect() as c:
        name: str = c.execute(
            text("SELECT name FROM core.tenants WHERE id = :t"), {"t": seeded.live_tenant_id}
        ).scalar_one()
    assert name == demo.DEMO_SCHOOL_NAME
    assert school["counts"]["students"] == STUDENTS


def test_FR_DEMO_006_to_012_every_feature_has_rows(seeded: Seeded, admin_engine: Engine) -> None:
    counts = _counts(admin_engine, seeded.live_tenant_id)
    assert all(counts[t] >= n for t, n in EXPECTED_MIN.items()), counts
    created = seeded.first["tenants"][0]["created"]
    assert created["compliance_certificates"] == len(demo.CERTIFICATES)
    assert created["calendar_entries"] == len(demo.CALENDAR_ENTRIES)
    assert created["disclosure_drafts"] == 1
    assert created["weekly_briefs"] == 1
    assert created["dq_runs"] == 1


def test_FR_DEMO_006_earlier_year_dues(seeded: Seeded, admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT a.status, count(*) FROM fin.student_fee_accounts AS a "
                "JOIN core.academic_years AS y ON y.tenant_id = a.tenant_id "
                "AND y.id = a.academic_year_id "
                "WHERE a.tenant_id = :t AND y.label = '2025-26' GROUP BY 1"
            ),
            {"t": seeded.live_tenant_id},
        ).all()
        closed: int = c.execute(
            text(
                "SELECT count(*) FROM sis.enrollments AS e "
                "JOIN core.academic_years AS y ON y.tenant_id = e.tenant_id "
                "AND y.id = e.academic_year_id "
                "WHERE e.tenant_id = :t AND y.label = '2025-26' AND e.status = 'completed' "
                "AND e.ended_on = :d"
            ),
            {"t": seeded.live_tenant_id, "d": demo.ARREARS_ENDED_ON},
        ).scalar_one()
    by_status: dict[str, int] = {str(r[0]): int(r[1]) for r in rows}
    assert by_status.get("open", 0) == 6, by_status
    # Last year's placements are closed, so the demo plants no "enrolled twice" finding.
    assert closed == 6


def test_FR_DEMO_009_certificate_states(seeded: Seeded, admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        rows = c.execute(
            text(
                "SELECT kind, valid_until IS NULL AS no_expiry, valid_until < current_date AS gone "
                "FROM ops.compliance_certificates WHERE tenant_id = :t AND status = 'current'"
            ),
            {"t": seeded.live_tenant_id},
        ).all()
    kinds = {r.kind: (r.no_expiry, r.gone) for r in rows}
    assert kinds["water_safety"] == (False, True)
    assert kinds["fire_noc"] == (False, False)
    assert kinds["trust_registration"][0] is True


def test_FR_DEMO_016_017_exams_marks_and_flags(seeded: Seeded, admin_engine: Engine) -> None:
    created = seeded.first["tenants"][0]["created"]
    assert created["exams"] == len(demo.EXAMS)
    assert created["marks"] > 0
    assert created["insight_flags"] > 0
    with admin_engine.connect() as c:
        per_exam = {
            str(r.name): (int(r.sections), int(r.subjects))
            for r in c.execute(
                text(
                    "SELECT e.name, count(DISTINCT m.section_id) AS sections, "
                    "count(DISTINCT m.subject) AS subjects FROM sis.exams AS e "
                    "JOIN sis.exam_marks AS m ON m.tenant_id = e.tenant_id AND m.exam_id = e.id "
                    "WHERE e.tenant_id = :t GROUP BY e.name"
                ),
                {"t": seeded.live_tenant_id},
            )
        }
        rules = {
            str(r[0])
            for r in c.execute(
                text("SELECT DISTINCT rule FROM sis.insight_flags WHERE tenant_id = :t"),
                {"t": seeded.live_tenant_id},
            )
        }
    first, second = (per_exam[e.name] for e in demo.EXAMS)
    assert first[1] == len(demo.MARKS_SUBJECTS)
    # FT-14: the second exam is unfinished (sections not started).
    assert second[0] < first[0]
    assert rules & {"course_low", "course_decline"}


def test_FR_DEMO_018_019_tc_requests_and_one_family(seeded: Seeded, admin_engine: Engine) -> None:
    created = seeded.first["tenants"][0]["created"]
    assert created["tc_requests"] == len(demo.TC_REQUESTS)
    assert created["sibling_links"] == 1
    with admin_engine.connect() as c:
        statuses = sorted(
            str(r[0])
            for r in c.execute(
                text("SELECT status FROM sis.tc_requests WHERE tenant_id = :t"),
                {"t": seeded.live_tenant_id},
            )
        )
        shared: int = c.execute(
            text(
                "SELECT count(*) FROM (SELECT guardian_id FROM sis.student_guardians "
                "WHERE tenant_id = :t GROUP BY guardian_id HAVING count(*) >= 2) AS g"
            ),
            {"t": seeded.live_tenant_id},
        ).scalar_one()
    assert statuses == ["in_preparation", "received", "received", "received"]
    assert shared >= 1


def test_FR_DEMO_003_rerun_is_idempotent(seeded: Seeded) -> None:
    a, b = seeded.first["tenants"][0], seeded.second["tenants"][0]
    assert seeded.second["generation"] == seeded.first["generation"]
    assert b["outcome"] == "unchanged"
    assert b["created"] == {}
    assert a["counts"] == b["counts"]


def test_FR_DEMO_004_reset_purges_and_seeds_the_next_generation(
    seeded: Seeded, admin_engine: Engine
) -> None:
    assert seeded.reset["generation"] == seeded.first["generation"] + 1
    purged = [r for r in seeded.reset["reset"] if r["tenant_id"] == str(seeded.tenant_id)]
    assert purged
    assert purged[0]["rows_deleted"] > 0
    with admin_engine.connect() as c:
        status: str = c.execute(
            text("SELECT status FROM core.tenants WHERE id = :t"), {"t": seeded.tenant_id}
        ).scalar_one()
    assert status == "offboarding"
    assert all(n == 0 for n in _counts(admin_engine, seeded.tenant_id).values())
    fresh = _counts(admin_engine, seeded.live_tenant_id)
    assert all(fresh[t] >= n for t, n in EXPECTED_MIN.items()), fresh
    assert seeded.reset["tenants"][0]["outcome"] == "created"


def test_FR_DEMO_004_reset_touches_only_demo_schools(seeded: Seeded) -> None:
    touched = {r["code"] for r in seeded.reset["reset"]}
    assert touched
    assert all(demo.generation_of(code) is not None for code in touched)


def test_FR_DEMO_005_summary_has_no_names_or_contacts(seeded: Seeded) -> None:
    assert demo.DEMO_SCHOOL_NAME not in seeded.printed
    assert "@" not in seeded.printed
    assert "DEMO/" not in seeded.printed
