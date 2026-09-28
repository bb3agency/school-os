"""The caller's document-visibility keys for retrieval (``AclKeys``; docs/05 §6.1, FR-KB-002).

Built from the :class:`UserContext` exactly like the documents service's visibility rule
(``documents.service._visibility``): ``sees_all`` for holders of ``document.manage_acl`` at
school scope; ``school_wide`` for school-wide ``document.read`` holders; otherwise the sections
and classes a scoped ``document.read`` grant reaches through the academic structure (a class
scope covers its sections, a section scope makes its class match class-level ACL entries).
Roles and the membership always count. ``read_sensitive`` (C3 documents) follows
``student.read_sensitive``, as the documents service does for opening C3 files.
``tests/knowledge/test_ask_service.py`` checks that the index filter built from these keys
agrees with ``documents.service.is_visible``.

Tenant isolation stays RLS (invariant 1): these keys only narrow within the school.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Final

from app.knowledge.domain import AclKeys
from app.tenancy import service as tenancy

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.authz.context import UserContext

READ: Final = "document.read"
MANAGE: Final = "document.manage_acl"
SENSITIVE: Final = "student.read_sensitive"
"""Restricted (C3) documents also need this (docs/05 §6.1, like the documents service)."""


def acl_keys(session: Session, ctx: UserContext) -> AclKeys | None:
    """The caller's keys, or None when the caller may not read documents at all."""
    sees_all = ctx.has(MANAGE) and ctx.scope_for(MANAGE).school_wide
    read = ctx.has(READ)
    if not (read or sees_all):
        return None
    grant = ctx.scope_for(READ)
    school_wide = read and grant.school_wide
    sections: set[uuid.UUID] = set()
    classes: set[uuid.UUID] = set()
    if read and not grant.school_wide:
        all_sections = tenancy.list_sections(session)
        sections = {
            s.id for s in all_sections if s.id in grant.section_ids or s.class_id in grant.class_ids
        }
        classes = set(grant.class_ids) | {
            s.class_id for s in all_sections if s.id in grant.section_ids
        }
    return AclKeys(
        roles=frozenset(ctx.roles),
        section_ids=frozenset(sections),
        class_ids=frozenset(classes),
        membership_id=ctx.membership_id,
        school_wide=school_wide,
        sees_all=sees_all,
        read_sensitive=ctx.has(SENSITIVE),
    )


__all__ = ["MANAGE", "READ", "SENSITIVE", "acl_keys"]
