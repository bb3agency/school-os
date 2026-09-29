"""Public functions other modules added for the full data export (FR-ADM-001; CLAUDE.md §4:
the admin module reads every module only through its service.py). Synthetic data only."""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from app.audit import export as audit_export
from app.audit.viewer import AuditFilters
from app.changes import service as changes
from app.core.db import tenant_session
from app.core.errors import Conflict, ValidationFailed
from app.documents import service as documents
from app.dq import service as dq
from app.imports import service as imports
from app.students import service as students
from app.students.schemas import ValueIn

pytestmark = pytest.mark.db
AD = sys.modules["sos_test_admin_support"]
SW = AD.SW
D = AD.D
WITHHELD = ("aadhaar_name_as_printed", "aadhaar_dob_as_printed", "aadhaar_gender_as_printed")


@pytest.fixture(scope="module")
def student(school: Any) -> uuid.UUID:
    AD.install()
    sid: uuid.UUID = SW.create(
        school,
        name="Synthetica Public Additions",
        section_key="section_9c",
        extra=[
            ValueIn(attribute_key="religion", source="parent_form", value="Synthetic faith"),
            ValueIn(attribute_key="aadhaar_last4", source="aadhaar_as_printed", value="1357"),
            ValueIn(
                attribute_key="aadhaar_gender_as_printed", source="aadhaar_as_printed", value="male"
            ),
        ],
    )
    return sid


def _values(tables: list[Any], student_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    table = next(t for t in tables if t.name == "student_values")
    rows = [dict(zip(table.columns, r, strict=True)) for r in table.rows]
    return {r["attribute_key"]: r for r in rows if r["student_id"] == student_id}


def test_FR_ADM_001_students_export_records_masks_c3_unless_asked(
    school: Any, student: uuid.UUID
) -> None:
    with tenant_session(school.tenant_id) as s:
        masked = students.export_records(s, include_sensitive=False, withheld=WITHHELD)
        clear = students.export_records(s, include_sensitive=True, withheld=WITHHELD)
        fields = students.sensitive_export_fields(s, withheld=WITHHELD)
    assert [t.name for t in masked] == [
        "students",
        "student_values",
        "enrollments",
        "guardians",
        "student_guardians",
        "promotion_runs",
        "promotion_items",
    ]
    m = _values(masked, student)
    assert m["religion"]["value"] == "••••"
    assert m["religion"]["value_state"] == "masked"
    assert m["full_name"]["value"] == "Synthetica Public Additions"
    assert m["aadhaar_gender_as_printed"]["value"] is None
    assert m["aadhaar_gender_as_printed"]["value_state"] == "withheld"
    c = _values(clear, student)
    assert c["religion"]["value"] == "Synthetic faith"
    assert c["aadhaar_last4"]["value"] == "XXXX XXXX 1357"
    assert c["aadhaar_gender_as_printed"]["value_state"] == "withheld"
    assert "religion" in fields
    assert "guardian_phone" in fields
    assert "guardian_address" in fields
    assert not set(WITHHELD) & set(fields)
    # tenant_id is never a column (the archive is one school's) and ciphertext never leaves.
    for table in masked:
        assert "tenant_id" not in table.columns
        assert not any("ciphertext" in c or "blind_index" in c for c in table.columns)


def test_FR_ADM_001_other_modules_export_their_tables_without_ciphertext(
    school: Any, admin_engine: Engine
) -> None:
    D.make_document(admin_engine, school.tenant_id, school.people["owner"].user_id)
    with tenant_session(school.tenant_id) as s:
        tables = [
            *changes.export_records(s, include_sensitive=False, withheld=WITHHELD),
            *dq.export_records(s),
            *imports.export_records(s),
            *documents.export_records(s),
        ]
        files = documents.export_files(s)
    assert [t.name for t in tables] == [
        "change_requests",
        "dq_runs",
        "dq_findings",
        "import_batches",
        "import_mapping_templates",
        "documents",
        "document_versions",
        "document_acl",
    ]
    for table in tables:
        assert "tenant_id" not in table.columns
        assert not any("ciphertext" in c for c in table.columns), table.name
    assert files
    assert all(f.status == "ready" for f in files)
    assert all(f.object_key.startswith(f"t/{school.tenant_id}/") for f in files)


def test_FR_ADM_001_change_request_values_are_masked_unless_asked(
    school: Any, admin_engine: Engine
) -> None:
    changes_objects = AD._load(
        "sos_test_changes_objects", AD._HERE.parents[1] / "changes" / "objects.py"
    )
    school.people.setdefault("office_admin", school.people["owner"])
    request_id = changes_objects.pending(school)
    with tenant_session(school.tenant_id) as s:
        table = changes.export_records(s, include_sensitive=False, withheld=WITHHELD)[0]
    rows = {r[0]: dict(zip(table.columns, r, strict=True)) for r in table.rows}
    row = rows[request_id]
    # dob is C2: shown; a C3 correction would show ••••.
    assert row["new_value"] == "2012-03-15"
    assert row["value_state"] == "value"
    assert row["status"] == "pending"


def test_FR_ADM_001_changed_document_bytes_are_refused(school: Any, admin_engine: Engine) -> None:
    doc = D.make_document(admin_engine, school.tenant_id, school.people["owner"].user_id)
    with tenant_session(school.tenant_id) as s:
        obj = next(f for f in documents.export_files(s) if f.document_id == doc)
    assert b"".join(documents.iter_export_file(school.tenant_id, obj))
    other_school = uuid.uuid4()
    with pytest.raises(Exception, match="not found"):
        list(documents.iter_export_file(other_school, obj))
    store = D.memory_store()
    original = store.objects[obj.object_key]
    store.objects[obj.object_key] = D.StoredObj(original.data + b"x", original.content_type)
    try:
        with pytest.raises(Conflict) as exc:
            list(documents.iter_export_file(school.tenant_id, obj))
        assert exc.value.code == "integrity_mismatch"
    finally:
        store.objects[obj.object_key] = original


def test_FR_AUD_005_audit_export_limit_can_be_raised_by_storage_callers(school: Any) -> None:
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id) as s, pytest.raises(ValidationFailed):
        audit_export.start(
            s,
            tenant_id=school.tenant_id,
            user_id=owner.user_id,
            filters=AuditFilters(),
            max_rows=0,
        )
    with tenant_session(school.tenant_id, owner.user_id) as s:
        plan = audit_export.start(
            s,
            tenant_id=school.tenant_id,
            user_id=owner.user_id,
            filters=AuditFilters(),
            max_rows=10_000_000,
        )
    assert plan.rows >= 1
