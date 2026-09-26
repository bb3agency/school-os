"""Scope filters (FR-IAM-012, SEC-015; docs/07 §6.1 enforcement point 3).

A permission granted only as "S" (07 §6.2) reaches the membership's class and section scopes.
These helpers decide object visibility for the academic structure; out-of-scope objects are
reported as 404 (never reveal existence). Student/document repositories (M1) apply the same
:class:`app.authz.context.ScopeGrant` inside their SQL.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from typing import Protocol

from app.authz.context import ScopeGrant, UserContext
from app.core.errors import NotFound


class SectionLike(Protocol):
    @property
    def id(self) -> uuid.UUID: ...

    @property
    def class_id(self) -> uuid.UUID: ...


class ClassLike(Protocol):
    @property
    def id(self) -> uuid.UUID: ...


def _section_in(grant: ScopeGrant, section: SectionLike) -> bool:
    return (
        grant.school_wide or section.id in grant.section_ids or section.class_id in grant.class_ids
    )


def section_visible(ctx: UserContext, permission: str, section: SectionLike) -> bool:
    return ctx.has(permission) and _section_in(ctx.scope_for(permission), section)


def visible_sections[S: SectionLike](
    ctx: UserContext, permission: str, sections: Iterable[S]
) -> list[S]:
    if not ctx.has(permission):
        return []
    grant = ctx.scope_for(permission)
    return [s for s in sections if _section_in(grant, s)]


def ensure_section_visible[S: SectionLike](ctx: UserContext, permission: str, section: S) -> S:
    if not section_visible(ctx, permission, section):
        raise NotFound("Section not found")
    return section


def visible_class_ids(
    ctx: UserContext, permission: str, sections: Sequence[SectionLike]
) -> frozenset[uuid.UUID] | None:
    """Class ids the caller may see (``None`` = all): scoped classes plus classes of scoped
    sections."""
    if not ctx.has(permission):
        return frozenset()
    grant = ctx.scope_for(permission)
    if grant.school_wide:
        return None
    via_sections = {s.class_id for s in sections if s.id in grant.section_ids}
    return frozenset(grant.class_ids | via_sections)


def visible_classes[C: ClassLike](
    ctx: UserContext, permission: str, classes: Iterable[C], sections: Sequence[SectionLike]
) -> list[C]:
    allowed = visible_class_ids(ctx, permission, sections)
    return [c for c in classes if allowed is None or c.id in allowed]


def ensure_class_visible[C: ClassLike](
    ctx: UserContext, permission: str, klass: C, sections: Sequence[SectionLike]
) -> C:
    allowed = visible_class_ids(ctx, permission, sections)
    if allowed is not None and klass.id not in allowed:
        raise NotFound("Class not found")
    return klass
