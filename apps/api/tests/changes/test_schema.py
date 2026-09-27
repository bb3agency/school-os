"""``sis.change_requests`` in the database (SEC-014, SEC-001, SEC-012, docs/05 §5, docs/12 §4.7).

Direct SQL as ``sos_app`` inside a school's ``tenant_session``: the database itself refuses
self-approval, changes to decided requests, deletes, cross-school references and rows of other
schools, whatever the service does.
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError

from app.changes import service as changes
from app.changes.schemas import ApproveIn
from app.core.db import context_free_session, tenant_session

pytestmark = pytest.mark.db
CR = sys.modules["sos_test_changes_objects"]


def _constraint(exc: DBAPIError) -> str:
    return str(getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or "")


def _pending(admin: Engine, school: Any) -> Any:
    return CR.submit(admin, school, school.people["office_admin"], "office_admin")


def test_SEC_014_database_refuses_self_approval_by_direct_update(
    school: Any, admin_engine: Engine
) -> None:
    req = _pending(admin_engine, school)
    with pytest.raises(IntegrityError) as exc, tenant_session(school.tenant_id) as s:
        s.execute(
            text(
                "UPDATE sis.change_requests SET status = 'rejected', decided_by = requested_by, "
                "decided_at = now(), decision_note = 'Direct SQL self decision' WHERE id = :i"
            ),
            {"i": req.id},
        )
    assert _constraint(exc.value) == "change_requests_no_self_approval"
    assert CR.row(admin_engine, req.id)["status"] == "pending"


def test_SEC_014_database_refuses_self_approval_even_for_the_owner_role(
    school: Any, admin_engine: Engine
) -> None:
    """The CHECK holds for the migration owner too (not only for the app role)."""
    req = _pending(admin_engine, school)
    with admin_engine.connect() as c, c.begin():
        c.execute(text("SET LOCAL ROLE sos_owner"))
        c.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(school.tenant_id)}
        )
        with pytest.raises(IntegrityError) as exc:
            c.execute(
                text(
                    "UPDATE sis.change_requests SET status = 'rejected', "
                    "decided_by = requested_by, decided_at = now(), "
                    "decision_note = 'Direct SQL self decision' WHERE id = :i"
                ),
                {"i": req.id},
            )
    assert _constraint(exc.value) == "change_requests_no_self_approval"


def test_FR_CR_004_database_requires_a_note_on_rejection(school: Any, admin_engine: Engine) -> None:
    req = _pending(admin_engine, school)
    with pytest.raises(IntegrityError) as exc, tenant_session(school.tenant_id) as s:
        s.execute(
            text(
                "UPDATE sis.change_requests SET status = 'rejected', decided_by = :d, "
                "decided_at = now() WHERE id = :i"
            ),
            {"i": req.id, "d": school.people["principal"].membership_id},
        )
    assert _constraint(exc.value) == "change_requests_reject_needs_note"


def test_FR_CR_003_decided_requests_are_frozen(school: Any, admin_engine: Engine) -> None:
    req = _pending(admin_engine, school)
    principal = school.people["principal"]
    with tenant_session(school.tenant_id, principal.user_id) as s:
        changes.approve(
            s, CR.ctx(school, principal, "principal"), req.id, ApproveIn(), expected_version=1
        )
    with pytest.raises(IntegrityError) as exc, tenant_session(school.tenant_id) as s:
        s.execute(
            text(
                "UPDATE sis.change_requests SET status = 'pending', decided_by = NULL, "
                "decided_at = NULL, applied_value_id = NULL WHERE id = :i"
            ),
            {"i": req.id},
        )
    assert _constraint(exc.value) == "change_requests_frozen"


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM sis.change_requests WHERE id = :i",
        "UPDATE sis.change_requests SET reason = 'Rewritten reason text' WHERE id = :i",
        "UPDATE sis.change_requests SET requested_by = requested_by WHERE id = :i",
        "UPDATE sis.change_requests SET new_value_date = '2001-01-01' WHERE id = :i",
    ],
)
def test_FR_CR_001_app_role_cannot_delete_or_rewrite_requests(
    school: Any, admin_engine: Engine, sql: str
) -> None:
    req = _pending(admin_engine, school)
    with pytest.raises(ProgrammingError) as exc, tenant_session(school.tenant_id) as s:
        s.execute(text(sql), {"i": req.id})
    assert "permission denied" in str(exc.value.orig)


def test_SEC_001_other_school_never_sees_or_writes_requests(
    school: Any, world: Any, admin_engine: Engine
) -> None:
    req = _pending(admin_engine, school)
    with tenant_session(world.b.tenant_id) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM sis.change_requests WHERE id = :i"), {"i": req.id}
            ).scalar_one()
            == 0
        )
        updated = s.execute(
            text("UPDATE sis.change_requests SET status = 'cancelled' WHERE id = :i RETURNING id"),
            {"i": req.id},
        ).all()
        assert updated == []
    with context_free_session() as s:
        assert s.execute(text("SELECT count(*) FROM sis.change_requests")).scalar_one() == 0


def test_ADR_0013_cannot_reference_another_schools_evidence_or_member(
    school: Any, world: Any, admin_engine: Engine
) -> None:
    sid = CR.student(school)
    b_doc = CR.evidence(admin_engine, world.b, world.b.people["owner"])
    a_doc = CR.evidence(admin_engine, school, school.people["owner"])
    cases = {
        "change_requests_evidence_fk": (b_doc, school.people["office_admin"].membership_id),
        "change_requests_requested_by_fk": (a_doc, world.b.people["owner"].membership_id),
    }
    for constraint, (doc, member) in cases.items():
        with pytest.raises(IntegrityError) as exc, tenant_session(school.tenant_id) as s:
            s.execute(
                text(
                    "INSERT INTO sis.change_requests (id, tenant_id, student_id, attribute_key, "
                    "new_value_text, reason, evidence_document_id, requested_by, expires_at) "
                    "VALUES (:id, :t, :s, 'full_name', 'Synthetic Name', "
                    "'Synthetic direct insert', :d, :m, now() + interval '1 day')"
                ),
                {"id": uuid.uuid4(), "t": school.tenant_id, "s": sid, "d": doc, "m": member},
            )
        assert _constraint(exc.value) == constraint


def test_docs_05_attribute_values_reference_change_requests(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT convalidated, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'attribute_values_change_request_fk'"
            )
        ).one()
    assert row[0] is True
    assert "(tenant_id, change_request_id)" in row[1]
    assert "sis.change_requests(tenant_id, id)" in row[1]


def test_SEC_014_constraint_catalog(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        defs: dict[str, str] = dict(
            c.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'sis.change_requests'::regclass"
                )
            ).all()
        )
    assert "(decided_by <> requested_by)" in defs["change_requests_no_self_approval"]
    assert "REFERENCES core.memberships(tenant_id, id)" in defs["change_requests_decided_by_fk"]
    assert "REFERENCES kb.documents(tenant_id, id)" in defs["change_requests_evidence_fk"]
