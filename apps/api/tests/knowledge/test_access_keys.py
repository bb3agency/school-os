"""The caller's retrieval keys (``tools.access.acl_keys``; docs/05 §6.1, FR-KB-002, SEC-018).

``read_sensitive`` follows ``student.read_sensitive`` exactly: holders may retrieve restricted
(C3) documents their ACL reaches, everyone else never (the SQL side is in
``test_retrieval_acl.py``). No database: school-wide grants need no academic structure.
"""

from __future__ import annotations

import uuid
from typing import Any, cast

from app.authz.context import Scopes, UserContext
from app.knowledge.tools.access import acl_keys


def ctx(*permissions: str) -> UserContext:
    return UserContext(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        roles=frozenset({"principal"}),
        permissions=frozenset(permissions),
        scopes=Scopes(school=True),
        mfa=True,
        auth_time=None,
    )


NO_SESSION = cast(Any, object())


def test_SEC_018_read_sensitive_flag_follows_the_permission() -> None:
    plain = acl_keys(NO_SESSION, ctx("document.read"))
    holder = acl_keys(NO_SESSION, ctx("document.read", "student.read_sensitive"))
    assert plain is not None
    assert holder is not None
    assert plain.read_sensitive is False
    assert holder.read_sensitive is True
    assert holder.school_wide is True


def test_SEC_018_read_sensitive_alone_gives_no_document_access() -> None:
    assert acl_keys(NO_SESSION, ctx("student.read_sensitive")) is None
