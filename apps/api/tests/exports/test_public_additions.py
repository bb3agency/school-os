"""Small additive public functions other modules gained for exports (CLAUDE.md §4: modules meet
only at service.py):

- ``app.dq.service.findings_for_students`` (US-501 AC4): unresolved findings of many students,
  limited to the caller's reach, most severe first.
- ``app.students.service.active_enrolments`` now carries ``roll_no``.
- ``app.documents.service.store_export_file`` / ``export_download_url`` /
  ``delete_export_files`` (docs/04 §8.2 ``t/<tenant>/exports/<export_id>/<file>``).
"""

from __future__ import annotations

import dataclasses
import sys
import uuid
from typing import Any

import pytest

from app.authz.context import Scopes
from app.core.db import tenant_session
from app.core.errors import NotFound
from app.documents import service as documents
from app.dq import service as dq
from app.students import service as students
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
EX = sys.modules["sos_test_exports_objects"]


def test_findings_for_students_scope_order_and_profile(school: Any) -> None:
    a = EX.student(school, section_key="section_9a", parents=False)
    b = EX.student(
        school,
        section_key="section_9c",
        extra=[
            ValueIn(
                attribute_key="aadhaar_gender_as_printed", source="aadhaar_as_printed", value="male"
            )
        ],
    )
    admin = EX.ctx(school, school.people["office_admin"], "office_admin")
    with tenant_session(school.tenant_id, admin.user_id) as db:
        dq.run_checks(db, admin, student_ids=[a, b], profile_key="cisce-registration-2026")
    with tenant_session(school.tenant_id, admin.user_id) as db:
        found = dq.findings_for_students(db, admin, [a, b], profile_key="cisce-registration-2026")
        base_only = dq.findings_for_students(db, admin, [a, b], profile_key="udise-plus")
    ranks = [dq_rank(f.severity) for f in found]
    assert ranks == sorted(ranks, reverse=True)
    assert {f.student.id for f in found} == {a, b}
    assert any(f.rule_id == "DQ-005" for f in found)
    assert not any(f.profile_key == "cisce-registration-2026" for f in base_only)
    for f in found:
        for v in f.values:
            if v.sensitive:
                assert v.value is None
    # A class teacher of 9A sees only 9A students' findings (SEC-015).
    ct = dataclasses.replace(
        EX.ctx(school, school.people["class_teacher"], "class_teacher"),
        scopes=Scopes(school=False, section_ids=frozenset({school.ids["section_9a"]})),
    )
    with tenant_session(school.tenant_id, ct.user_id) as db:
        scoped = dq.findings_for_students(db, ct, [a, b])
    assert {f.student.id for f in scoped} == {a}


def dq_rank(severity: str) -> int:
    return {"blocker": 5, "high": 4, "medium": 3, "low": 2, "info": 1}[severity]


def test_active_enrolments_carry_roll_no(school: Any) -> None:
    sid = EX.student(school, roll_no="17")
    with tenant_session(school.tenant_id) as db:
        enrolments = students.active_enrolments(db, [sid])[sid]
    assert [e.roll_no for e in enrolments] == ["17"]


def test_export_files_are_stored_under_the_school_prefix(school: Any, world: Any) -> None:
    store = EX.D.memory_store()
    export_id = uuid.uuid4()
    with tenant_session(school.tenant_id) as db:
        key = documents.store_export_file(db, export_id, "students.csv", b"a,b\r\n", "text/csv")
        assert key == f"t/{school.tenant_id}/exports/{export_id}/students.csv"
        # FR-EXP-003, docs/05 §13: tagged for the 7-day lifecycle rule (infra/terraform).
        assert store.objects[key].lifecycle == "export-7d"
        url, _ = documents.export_download_url(
            db, export_id, key, content_type="text/csv", filename="x.csv", ttl_s=3600
        )
        assert store.gets[-1]["ttl"] <= 300, "FR-DOC-004: never longer than 5 minutes"
        assert f"/exports/{export_id}/" in url
        with pytest.raises(ValueError, match="generated"):
            documents.store_export_file(db, export_id, "../evil.csv", b"x", "text/csv")
        # Another export's or another school's key is refused (404).
        with pytest.raises(NotFound):
            documents.export_download_url(
                db, uuid.uuid4(), key, content_type="text/csv", filename="x.csv", ttl_s=60
            )
    with tenant_session(world.b.tenant_id) as db, pytest.raises(NotFound):
        documents.export_download_url(
            db, export_id, key, content_type="text/csv", filename="x.csv", ttl_s=60
        )
    with tenant_session(school.tenant_id) as db:
        assert documents.delete_export_files(db, export_id) == 1
    assert key not in store.objects
