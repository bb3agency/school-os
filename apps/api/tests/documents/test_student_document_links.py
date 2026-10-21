"""The pinned list of tables that tie a document to a student (R3-1A; FR-DPDP-009; lead
decision: no runtime catalog discovery). Every ``(tenant_id, <col>)`` foreign key to
``kb.documents`` on a table with ``student_id`` must be in
``app.documents.repository.STUDENT_DOCUMENT_LINKS``, and nothing else: a new link table is added
there deliberately (and recorded in docs/05 §16.1)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from app.documents.repository import STUDENT_DOCUMENT_LINKS

pytestmark = pytest.mark.db

CATALOG_SQL = text(
    """
SELECT n.nspname || '.' || t.relname AS relation, a.attname AS column_name
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace n ON n.oid = t.relnamespace
JOIN LATERAL unnest(c.conkey) AS k(attnum) ON true
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
WHERE c.contype = 'f'
  AND c.confrelid = 'kb.documents'::regclass
  AND array_length(c.conkey, 1) = 2
  AND a.attname <> 'tenant_id'
  AND NOT t.relispartition
  AND EXISTS (SELECT 1 FROM pg_attribute s WHERE s.attrelid = c.conrelid
              AND s.attname = 'student_id' AND NOT s.attisdropped)
"""
)


def test_FR_DPDP_009_student_document_links_match_the_catalog(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        found = {(r.relation, r.column_name) for r in c.execute(CATALOG_SQL)}
    pinned = {(rel, col) for rel, col, _ in STUDENT_DOCUMENT_LINKS}
    assert sorted(found) == sorted(pinned), f"catalog: {sorted(found)}"
    assert all(student == "student_id" for _, _, student in STUDENT_DOCUMENT_LINKS)
