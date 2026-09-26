"""Pydantic v2 IO models for identity: me, users (memberships), roles, scopes, permissions.

Inputs forbid unknown fields and NFC-normalise text (docs/05 §1). Outputs never include the IdP
subject (an opaque ID, but not needed by the UI).
"""

from __future__ import annotations

import datetime as dt
import unicodedata
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


def _nfc(value: Any) -> Any:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value).strip()
    return value


_NO_CONTROL = r"^[^\x00-\x1f\x7f]+$"
DisplayName = Annotated[
    str, BeforeValidator(_nfc), StringConstraints(min_length=1, max_length=200, pattern=_NO_CONTROL)
]
Email = Annotated[
    str,
    BeforeValidator(_nfc),
    StringConstraints(
        max_length=254, pattern=r"^[^@\s\x00-\x1f]+@[^@\s\x00-\x1f]+\.[^@\s\x00-\x1f]+$"
    ),
]
Subject = Annotated[
    str, StringConstraints(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._:@|+=-]+$")
]
RoleKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,63}$")]
Language = Literal["en", "te"]
MembershipStatus = Literal["invited", "active", "suspended", "removed"]
ScopeType = Literal["school", "class", "section"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True, frozen=True)


# --- scopes ---------------------------------------------------------------------------------


class ScopeIn(_In):
    """``school`` (no ref), ``class`` (class id) or ``section`` (section id)."""

    type: ScopeType
    ref: uuid.UUID | None = None

    @model_validator(mode="after")
    def _ref_matches_type(self) -> Self:
        if (self.type == "school") != (self.ref is None):
            raise ValueError("ref is required for class/section scopes and forbidden for school")
        return self


def _unique(items: list[Any]) -> list[Any]:
    if len(set(items)) != len(items):
        raise ValueError("items must be unique")
    return items


class ScopesIn(_In):
    scopes: list[ScopeIn] = Field(default_factory=list, max_length=200)

    @field_validator("scopes")
    @classmethod
    def _unique_scopes(cls, v: list[ScopeIn]) -> list[ScopeIn]:
        _unique([(s.type, s.ref) for s in v])
        return v


class ScopeOut(_Out):
    type: ScopeType
    ref: uuid.UUID | None


# --- users ----------------------------------------------------------------------------------


class InviteIn(_In):
    """Invite a staff member. ``idp_subject`` is the identity provider's ``sub`` of the account
    created for them (admin-created username; docs/07 §5.1)."""

    idp_subject: Subject
    display_name: DisplayName
    email: Email | None = None
    preferred_language: Language = "en"
    roles: list[RoleKey] = Field(min_length=1, max_length=20)
    scopes: list[ScopeIn] = Field(default_factory=list, max_length=200)

    @field_validator("roles")
    @classmethod
    def _unique_roles(cls, v: list[str]) -> list[str]:
        return _unique(v)

    @field_validator("scopes")
    @classmethod
    def _unique_scopes(cls, v: list[ScopeIn]) -> list[ScopeIn]:
        _unique([(s.type, s.ref) for s in v])
        return v


class MembershipStatusIn(_In):
    """Activate, suspend or remove a staff member's access to this school."""

    status: Literal["active", "suspended", "removed"]


class RolesIn(_In):
    roles: list[RoleKey] = Field(max_length=20)

    @field_validator("roles")
    @classmethod
    def _unique_roles(cls, v: list[str]) -> list[str]:
        return _unique(v)


class UserOut(_Out):
    id: uuid.UUID
    membership_id: uuid.UUID
    display_name: str
    email: str | None
    preferred_language: Language
    status: MembershipStatus
    expires_at: dt.datetime | None
    roles: list[str]
    scopes: list[ScopeOut]
    last_login_at: dt.datetime | None
    created_at: dt.datetime
    version: int


# --- roles and permissions ------------------------------------------------------------------


class RoleOut(_Out):
    id: uuid.UUID
    key: str
    name_en: str
    name_te: str
    is_system: bool
    permissions: list[str]


class PermissionOut(_Out):
    key: str
    description: str
    sensitivity: str
    step_up: bool


# --- me -------------------------------------------------------------------------------------


class ActiveTenantIn(_In):
    tenant_id: uuid.UUID


class SchoolChoiceOut(_Out):
    """A school the signed-in user may work in (school picker; no personal data)."""

    tenant_id: uuid.UUID
    code: str
    name: str
    status: str


class SchoolChoicesOut(_Out):
    data: list[SchoolChoiceOut]


class MeOut(_Out):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    display_name: str
    preferred_language: Language
    roles: list[str]
    permissions: list[str]
    scopes: list[ScopeOut]
    mfa: bool
    tenant_ids: list[uuid.UUID] = Field(
        description="Schools this user can switch to (active memberships)."
    )


class LoginEventOut(_Out):
    recorded: bool
    tenant_id: uuid.UUID


# --- internal transfer objects (authz resolver) ---------------------------------------------


@dataclass(frozen=True, slots=True)
class LoginChoice:
    """An active, unexpired membership of an active user (``core.resolve_login``)."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    membership_id: uuid.UUID
    tenant_status: str


@dataclass(frozen=True, slots=True)
class RoleAccess:
    key: str
    is_system: bool
    permissions: frozenset[str]


@dataclass(frozen=True, slots=True)
class MembershipAccess:
    """What a membership may do (loaded inside the tenant; cached by authz)."""

    roles: tuple[RoleAccess, ...]
    scopes: tuple[tuple[str, uuid.UUID | None], ...]
    mfa_required: bool
