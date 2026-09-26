"""``UserContext``: who is acting, in which school, with which permissions and scopes.

Built once per request by the resolver (docs/04 §5 step 3) and passed to services and scoped
repositories (docs/07 §6.1 enforcement points 2-3). Frozen: nothing downstream can widen it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

ScopeType = Literal["school", "class", "section"]


@dataclass(frozen=True, slots=True)
class Scopes:
    """A membership's scopes (FR-IAM-012): the whole school and/or listed classes/sections."""

    school: bool = False
    class_ids: frozenset[uuid.UUID] = frozenset()
    section_ids: frozenset[uuid.UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class ScopeGrant:
    """How far one permission reaches for this user.

    ``school_wide`` is true when any role grants the permission school-wide (07 §6.2 ✓) or the
    membership carries the ``school`` scope; otherwise only ``class_ids``/``section_ids``
    (07 §6.2 S). An empty scoped grant sees nothing.
    """

    school_wide: bool
    class_ids: frozenset[uuid.UUID] = frozenset()
    section_ids: frozenset[uuid.UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class UserContext:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    roles: frozenset[str]
    permissions: frozenset[str]
    scopes: Scopes
    mfa: bool
    auth_time: datetime | None
    request_id: str | None = None
    session_id: str | None = None
    # Permissions held only through scoped ("S") grants.
    scoped_permissions: frozenset[str] = field(default_factory=frozenset)

    def has(self, permission: str) -> bool:
        return permission in self.permissions

    def scope_for(self, permission: str) -> ScopeGrant:
        """The reach of ``permission`` (callers must check :meth:`has` first)."""
        if permission not in self.permissions:
            return ScopeGrant(school_wide=False)
        if permission not in self.scoped_permissions or self.scopes.school:
            return ScopeGrant(school_wide=True)
        return ScopeGrant(
            school_wide=False,
            class_ids=self.scopes.class_ids,
            section_ids=self.scopes.section_ids,
        )
