"""Test support for imports: synthetic spreadsheets, import documents and a synchronous pipeline.

Loaded by path (pytest runs with ``--import-mode=importlib``) from ``tests/imports`` and from
the security suites. Synthetic data only: names are invented, Aadhaar-like numbers are built in
the test process (valid Verhoeff only where a test needs the rejection path).
"""

from __future__ import annotations

import contextlib
import csv
import datetime as dt
import hashlib
import importlib.util
import io
import itertools
import sys
import uuid
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from openpyxl import Workbook
from sqlalchemy import Engine, text

from app.core.redaction import verhoeff_check_digit

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MIME = "text/csv"
_adm = itertools.count(1)


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


TESTS = Path(__file__).resolve().parents[1]
SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")
W = SW.W


def adm(prefix: str = "IMP") -> str:
    """A unique synthetic admission number."""
    return f"{prefix}{next(_adm):05d}{uuid.uuid4().hex[:4].upper()}"


def valid_aadhaar(body: str = "23456789012") -> str:
    """A 12-digit number passing Verhoeff, built here only to test rejection (never stored)."""
    return body + verhoeff_check_digit(body)


# --- spreadsheets ---------------------------------------------------------------------------------


def xlsx_bytes(rows: Sequence[Sequence[Any]], *, sheet_title: str = "Class list") -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = sheet_title
    for row in rows:
        ws.append(list(row))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def csv_bytes(rows: Sequence[Sequence[Any]], *, delimiter: str = ",", bom: bool = False) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=delimiter, lineterminator="\n")
    for row in rows:
        writer.writerow(["" if v is None else v for v in row])
    data = buf.getvalue().encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if bom else data


HEADER = ["Adm No", "Name of the Student", "Father Name", "DOB", "Gender", "Class", "Section"]


def class_list(
    n: int = 3, *, prefix: str = "IMP", section: str = "A", klass: str = "IX"
) -> tuple[list[list[Any]], list[str]]:
    """A synthetic class list (header + ``n`` rows) and its admission numbers."""
    rows: list[list[Any]] = [list(HEADER)]
    numbers = []
    for i in range(n):
        number = adm(prefix)
        numbers.append(number)
        rows.append(
            [
                number,
                f"Synthetica Import Student {i:04d}",
                f"Synthetica Import Father {i:04d}",
                f"{(i % 27) + 1:02d}/{(i % 12) + 1:02d}/2012",
                "M" if i % 2 else "F",
                klass,
                section,
            ]
        )
    return rows, numbers


# --- import documents -----------------------------------------------------------------------------


@contextlib.contextmanager
def _writer(admin: Engine | None, tenant_id: uuid.UUID) -> Iterator[Any]:
    if admin is not None:
        with admin.begin() as c:
            yield c
        return
    from app.core.db import tenant_session

    with tenant_session(tenant_id) as s:
        yield s


def import_document(
    admin: Engine | None,
    tenant_id: uuid.UUID,
    created_by: uuid.UUID,
    data: bytes,
    *,
    kind: str = "xlsx",
    status: str = "ready",
) -> uuid.UUID:
    """A scanned ``import_file`` document with its raw object in the in-memory store (the
    same shape documents.register_document produces: ``t/<tenant>/imports/<batch>/raw.<ext>``).
    Without ``admin`` the rows are written as ``sos_app`` inside the school's tenant session."""
    store = D.memory_store()
    doc_id, version_id, batch_key = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mime = XLSX_MIME if kind == "xlsx" else CSV_MIME
    key = f"t/{tenant_id}/imports/{batch_key}/raw.{kind}"
    with _writer(admin, tenant_id) as c:
        c.execute(
            text(
                "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, sensitivity, "
                "current_version_id, created_by) VALUES (:d, :t, 'import_file', 'import_file', "
                "'Synthetic class list', 'C2', :v, :u)"
            ),
            {"d": doc_id, "t": tenant_id, "v": version_id, "u": created_by},
        )
        c.execute(
            text(
                "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                "(:v, :t, :d, 1, :k, :h, :m, :n, :st, :u)"
            ),
            {
                "v": version_id,
                "t": tenant_id,
                "d": doc_id,
                "k": key,
                "h": hashlib.sha256(data).digest(),
                "m": mime,
                "n": len(data),
                "st": status,
                "u": created_by,
            },
        )
    store.put(key, data, mime)
    return doc_id


# --- synchronous pipeline -------------------------------------------------------------------------


def ctx(school: Any, role: str = "office_admin", **kw: Any) -> Any:
    return SW.ctx_for(school.tenant_id, school.people[role], role, **kw)


def start(
    admin: Engine | None,
    school: Any,
    data: bytes,
    *,
    role: str = "office_admin",
    source: str = "admission_register",
    kind: str = "xlsx",
    parse: bool = True,
) -> uuid.UUID:
    """Create a batch through the service as ``role`` and (optionally) run the parse worker."""
    from app.core.db import tenant_session
    from app.imports import service
    from app.imports.schemas import ImportCreate

    SW.configure_keyring()
    person = school.people[role]
    doc = import_document(admin, school.tenant_id, person.user_id, data, kind=kind)
    with tenant_session(school.tenant_id, person.user_id) as s:
        out = service.create_import(
            s,
            ctx(school, role),
            ImportCreate(document_id=doc, source=source),
        )
    if parse:
        run_parse(school, out.id, role=role)
    return out.id


def run_parse(school: Any, batch_id: uuid.UUID, *, role: str = "office_admin") -> str:
    from app.imports import service

    person = school.people[role]
    return service.run_parse(
        school.tenant_id,
        batch_id,
        job_id=job_of(school, batch_id),
        user_id=person.user_id,
        membership_id=person.membership_id,
    )


def run_validate(school: Any, batch_id: uuid.UUID, *, role: str = "office_admin") -> str:
    from app.imports import service

    person = school.people[role]
    return service.run_validate(
        school.tenant_id,
        batch_id,
        job_id=job_of(school, batch_id),
        user_id=person.user_id,
        membership_id=person.membership_id,
    )


def run_commit(
    school: Any, batch_id: uuid.UUID, *, role: str = "office_admin", skip_error_rows: bool = False
) -> str:
    from app.imports import service

    person = school.people[role]
    return service.run_commit(
        school.tenant_id,
        batch_id,
        job_id=job_of(school, batch_id),
        user_id=person.user_id,
        membership_id=person.membership_id,
        skip_error_rows=skip_error_rows,
    )


def request_commit(
    school: Any, batch_id: uuid.UUID, *, role: str = "office_admin", skip_error_rows: bool = False
) -> Any:
    from app.core.db import tenant_session
    from app.imports import service
    from app.imports.schemas import CommitIn

    with tenant_session(school.tenant_id, school.people[role].user_id) as s:
        return service.request_commit(
            s, ctx(school, role), batch_id, CommitIn(skip_error_rows=skip_error_rows)
        )


def commit(
    school: Any, batch_id: uuid.UUID, *, role: str = "office_admin", skip_error_rows: bool = False
) -> str:
    request_commit(school, batch_id, role=role, skip_error_rows=skip_error_rows)
    return run_commit(school, batch_id, role=role, skip_error_rows=skip_error_rows)


def imported(
    admin: Engine,
    school: Any,
    data: bytes,
    *,
    role: str = "office_admin",
    source: str = "admission_register",
    kind: str = "xlsx",
) -> uuid.UUID:
    """Upload, parse, validate and commit; returns the committed batch id."""
    batch_id = start(admin, school, data, role=role, source=source, kind=kind)
    assert batch(admin, batch_id)["status"] == "validated", batch(admin, batch_id)
    assert commit(school, batch_id, role=role) == "committed", batch(admin, batch_id)
    return batch_id


# --- inspection (admin engine) --------------------------------------------------------------------


def batch(admin: Engine, batch_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        row = c.execute(
            text("SELECT * FROM sis.import_batches WHERE id = :i"), {"i": batch_id}
        ).one()
    return dict(row._mapping)


def rows(admin: Engine, batch_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        result = c.execute(
            text("SELECT * FROM sis.import_rows WHERE batch_id = :i ORDER BY row_no"),
            {"i": batch_id},
        )
        return [dict(r._mapping) for r in result]


def job_of(school: Any, batch_id: uuid.UUID) -> uuid.UUID | None:
    from app.core.db import tenant_session

    with tenant_session(school.tenant_id) as s:
        value = s.execute(
            text("SELECT job_id FROM sis.import_batches WHERE id = :i"), {"i": batch_id}
        ).scalar_one_or_none()
    return uuid.UUID(str(value)) if value else None


def student_by_adm(admin: Engine, tenant_id: uuid.UUID, number: str) -> uuid.UUID | None:
    with admin.connect() as c:
        value = c.execute(
            text("SELECT id FROM sis.students WHERE tenant_id = :t AND admission_no = :a"),
            {"t": tenant_id, "a": number},
        ).scalar_one_or_none()
    return uuid.UUID(str(value)) if value else None


def count(admin: Engine, sql: str, **params: Any) -> int:
    with admin.connect() as c:
        return int(c.execute(text(sql), params).scalar_one())


def age_batch(admin: Engine, batch_id: uuid.UUID, **shift: dt.timedelta) -> None:
    """Move a batch's timestamps into the past (revert window / retention tests)."""
    sets = ", ".join(f"{col} = {col} - :{col}" for col in shift)
    with admin.begin() as c:
        c.execute(
            text(f"UPDATE sis.import_batches SET {sets} WHERE id = :i"), {"i": batch_id, **shift}
        )
