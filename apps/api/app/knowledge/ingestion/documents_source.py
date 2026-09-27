""":class:`DocumentSource` over ``documents.service`` (CLAUDE.md §4: public functions only).

Ingestion runs in a worker with no user. Like ``dq.engine.system_context``, it reads with a
system context holding ``document.read`` and ``document.manage_acl`` at school scope: the
documents visibility rule gives "every document of the school" only to ``manage_acl`` holders
(a school-wide reader would miss role- and membership-restricted documents). The context is
passed to ``documents.get_document`` and nothing else, so it never changes an ACL; that call
returns any document of the CURRENT school (RLS: the worker's ``tenant_session``) with its
metadata, ACL and versions. Bytes come from
``documents.document_object`` + ``read_document_object``, which serve only ``ready``
(malware-scanned) versions and check the SHA-256 (FR-DOC-002). Nothing here writes.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Final

from app.authz.context import Scopes, UserContext
from app.core.errors import Conflict, NotFound
from app.documents import service as documents
from app.knowledge.ingestion.ports import DocumentFacts, DocumentNotReady, VersionFacts

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SYSTEM_ID: Final = uuid.UUID(int=0)
READ_PERMISSIONS: Final = frozenset({"document.read", "document.manage_acl"})


def system_context(tenant_id: uuid.UUID) -> UserContext:
    """Whole-school document read context for ingestion jobs (only ever used to read)."""
    return UserContext(
        user_id=SYSTEM_ID,
        tenant_id=tenant_id,
        membership_id=SYSTEM_ID,
        roles=frozenset({"system"}),
        permissions=READ_PERMISSIONS,
        scopes=Scopes(school=True),
        mfa=False,
        auth_time=None,
    )


class DocumentsServiceSource:
    """Reads through ``documents.service`` in the caller's tenant session."""

    def document(
        self, session: Session, tenant_id: uuid.UUID, document_id: uuid.UUID
    ) -> DocumentFacts | None:
        try:
            doc = documents.get_document(session, system_context(tenant_id), document_id)
        except NotFound:
            return None
        return DocumentFacts(
            id=doc.id,
            purpose=doc.purpose,
            doc_type=doc.doc_type,
            title=doc.title,
            issuer=doc.issuer,
            issued_on=doc.issued_on,
            academic_year_id=doc.academic_year_id,
            sensitivity=doc.sensitivity,
            current_version_id=doc.current_version.id if doc.current_version else None,
            acl=tuple((a.principal_type, a.principal_ref) for a in doc.acl),
            versions=tuple(
                VersionFacts(
                    id=v.id, version_no=v.version_no, mime_type=v.mime_type, status=v.status
                )
                for v in doc.versions
            ),
        )

    def read_version(self, session: Session, document_id: uuid.UUID, version_no: int) -> bytes:
        try:
            obj = documents.document_object(session, document_id, version_no)
            return documents.read_document_object(session, obj)
        except (Conflict, NotFound) as exc:
            raise DocumentNotReady(getattr(exc, "code", "document_not_ready")) from exc


__all__ = ["DocumentsServiceSource", "system_context"]
