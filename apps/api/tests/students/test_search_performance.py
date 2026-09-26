"""Search performance on a 2,000-student school (FR-STU-011: p95 <= 300 ms, NFR-PERF-001).

A synthetic school gets 2,000 students (names from ``app.devtools.names``, projection keys from
the same functions the service uses), bulk-inserted with the admin engine for speed; three
other schools hold 2,000 profile rows each, as in a shared database. The test then

- runs a mix of real searches through the service (name, Telugu, section tokens, admission
  prefix, scoped class teacher) and asserts the p95 stays under the 300 ms budget, and
- captures the SQL the service sends and checks with EXPLAIN that it reaches the school's
  profiles through an index (RLS adds ``tenant_id = core.current_tenant()``; pg_trgm operators
  are not leakproof, so the planner uses the tenant-leading btree, never a full-table scan).

Runs in a few seconds; synthetic data only.
"""

from __future__ import annotations

import random
import statistics
import sys
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, event, text

from app.core.db import tenant_session
from app.core.textnorm import comparison_key
from app.devtools.names import generate_name
from app.students import service as students
from app.students.schemas import SearchFilters
from app.students.search import translit_key

pytestmark = pytest.mark.db
SW = sys.modules["sos_test_student_world"]
W = SW.W
N = 2000
BUDGET_MS = 300.0


def _noise_school(admin: Engine) -> None:
    tid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.tenants (id, code, name, status) VALUES (:i, :c, 'Noise', 'active')"
            ),
            {"i": tid, "c": f"n-{uuid.uuid4().hex[:12]}"},
        )
        c.execute(
            text(
                "INSERT INTO sis.students (id, tenant_id) SELECT gen_random_uuid(), :t FROM generate_series(1, :n)"
            ),
            {"t": tid, "n": N},
        )
        c.execute(
            text(
                "INSERT INTO sis.student_profiles (tenant_id, student_id, full_name, full_name_norm, "
                "full_name_translit) SELECT tenant_id, id, 'Noise Venkata Sai', 'NOISE VENKATA SAI', "
                "'NOIS VENKAT SAI' FROM sis.students WHERE tenant_id = :t"
            ),
            {"t": tid},
        )


@pytest.fixture(scope="module")
def big(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    SW.configure_keyring()
    school = W.School(W.provision_school())
    owner = W.add_member(admin_engine, school.tenant_id, ["owner"])
    school.people["owner"] = owner
    W.build_structure(school, owner)
    sections = [school.ids[k] for k in ("section_9a", "section_9c", "section_10a")]
    rng = random.Random(20260926)
    students_rows, profiles, enrolments = [], [], []
    for i in range(N):
        sid = uuid.uuid4()
        name = generate_name(rng)
        father = generate_name(rng, gender="m")
        full = name.register_form if i % 2 else name.initials_form
        section = sections[i % len(sections)]
        students_rows.append({"id": sid, "t": school.tenant_id, "adm": f"2019/{i:04d}"})
        profiles.append(
            {
                "t": school.tenant_id,
                "s": sid,
                "full": full,
                "norm": comparison_key(full),
                "translit": translit_key([full, name.canonical]),
                "father": comparison_key(father.canonical),
                "section": section,
            }
        )
        enrolments.append(
            {
                "id": uuid.uuid4(),
                "t": school.tenant_id,
                "s": sid,
                "sec": section,
                "y": school.ids["year"],
            }
        )
    with admin_engine.begin() as c:
        c.execute(
            text("INSERT INTO sis.students (id, tenant_id, admission_no) VALUES (:id, :t, :adm)"),
            students_rows,
        )
        c.execute(
            text(
                "INSERT INTO sis.student_profiles (tenant_id, student_id, full_name, full_name_norm, "
                "full_name_translit, father_name_norm, current_section_id, status, search_tsv) "
                "VALUES (:t, :s, :full, :norm, :translit, :father, :section, 'active', "
                "to_tsvector('simple', :norm))"
            ),
            profiles,
        )
        c.execute(
            text(
                "INSERT INTO sis.enrollments (id, tenant_id, student_id, section_id, "
                "academic_year_id) VALUES (:id, :t, :s, :sec, :y)"
            ),
            enrolments,
        )
    for _ in range(3):
        _noise_school(admin_engine)
    with admin_engine.begin() as c:
        for table in ("sis.students", "sis.student_profiles", "sis.enrollments"):
            c.execute(text(f"ANALYZE {table}"))
    ct = W.add_member(
        admin_engine, school.tenant_id, ["class_teacher"], scopes=[("section", sections[0])]
    )
    return {
        "school": school,
        "admin": SW.ctx_for(school.tenant_id, owner, "office_admin"),
        "teacher": SW.ctx_for(
            school.tenant_id, ct, "class_teacher", section_ids=frozenset({sections[0]})
        ),
        "sample": profiles[7]["full"],
    }


QUERIES = [
    "venkata sai",
    "venkat sai 9a",
    "lakshmi 10a",
    "వెంకట సాయి",
    "2019/01",
    "gorantla",
    "sri",
    "IX-C",
    None,
]


def test_FR_STU_011_search_p95_within_budget(big: Any) -> None:
    school = big["school"]
    timings: list[float] = []
    queries = [*QUERIES, big["sample"]]
    for round_ in range(3):
        for query in queries:
            ctx = big["teacher"] if round_ == 2 else big["admin"]
            start = time.perf_counter()
            with tenant_session(school.tenant_id, ctx.user_id) as db:
                page = students.search(db, ctx, SearchFilters(query=query), limit=50)
            timings.append((time.perf_counter() - start) * 1000)
            assert len(page.data) <= 50
    with tenant_session(school.tenant_id, big["admin"].user_id) as db:
        found = students.search(db, big["admin"], SearchFilters(query=big["sample"]), limit=5)
    assert found.data and found.data[0].display_name == big["sample"]
    p95 = statistics.quantiles(timings, n=20)[18]
    assert p95 < BUDGET_MS, f"p95 {p95:.1f} ms over {len(timings)} searches"


def test_FR_STU_011_search_plan_uses_an_index(big: Any, app_engine: Engine) -> None:
    school, ctx = big["school"], big["admin"]
    captured: list[tuple[str, Any]] = []

    def capture(
        conn: Any, cursor: Any, statement: str, params: Any, context: Any, many: bool
    ) -> None:
        if "word_similarity" in statement:
            captured.append((statement, params))

    event.listen(app_engine, "before_cursor_execute", capture)
    try:
        with tenant_session(school.tenant_id, ctx.user_id) as db:
            students.search(db, ctx, SearchFilters(query="venkat sai 9a"), limit=50)
            statement, params = captured[-1]
            plan = "\n".join(
                row[0]
                for row in db.connection().exec_driver_sql("EXPLAIN " + statement, params).all()
            )
    finally:
        event.remove(app_engine, "before_cursor_execute", capture)
    assert "Seq Scan on student_profiles" not in plan, plan
    assert "Seq Scan on students" not in plan, plan
    assert "Index" in plan and ("sp_" in plan or "student_profiles_pkey" in plan), plan
