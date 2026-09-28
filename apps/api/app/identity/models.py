"""Identity tables in schema ``core`` (migration 0003_core_schema, docs/05 §4).

FKs that point into another module's tables (``core.tenants``) are enforced by the database only;
they are not declared here so this module never imports another module's models.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.model_base import Base

SCHEMA = "core"

USER_STATUSES = ("active", "disabled")
MEMBERSHIP_STATUSES = ("invited", "active", "suspended", "removed")
SCOPE_TYPES = ("school", "class", "section")
SENSITIVITIES = ("normal", "sensitive", "critical")
LANGUAGES = ("en", "te")


class User(Base):
    """Global identity: one person may hold memberships in several schools (FR-IAM-013).

    Visible to the app only while the user has a membership in the current tenant. Created only via
    ``core.create_user_for_invite`` (SECURITY DEFINER).
    """

    __tablename__ = "users"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    # The identity is (idp_issuer, idp_subject) (ADR-0023; migration 0027). Nullable until the
    # contract step; the column default is the staff issuer. Not editable by the app.
    idp_issuer: Mapped[str | None] = mapped_column(Text)
    idp_subject: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)  # citext in the database
    phone_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    preferred_language: Mapped[str] = mapped_column(Text, server_default=text("'en'"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'active'"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    last_login_at: Mapped[dt.datetime | None]
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Membership(Base):
    """A user's membership in one tenant; carries status, expiry and MFA requirement."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "user_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("core.users.id"))
    status: Mapped[str] = mapped_column(Text, server_default=text("'invited'"))
    expires_at: Mapped[dt.datetime | None]
    mfa_required: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class Permission(Base):
    """Global permission catalog (read-only to the app; seeded by migrations)."""

    __tablename__ = "permissions"
    __table_args__ = {"schema": SCHEMA}  # noqa: RUF012

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str] = mapped_column(Text)
    sensitivity: Mapped[str] = mapped_column(Text, server_default=text("'normal'"))
    step_up: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    is_platform: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        UniqueConstraint("tenant_id", "key"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    key: Mapped[str] = mapped_column(Text)
    name_en: Mapped[str] = mapped_column(Text)
    name_te: Mapped[str] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))


class RolePermission(Base):
    __tablename__ = "role_permissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "role_id"], ["core.roles.tenant_id", "core.roles.id"], ondelete="CASCADE"
        ),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    permission_key: Mapped[str] = mapped_column(
        Text, ForeignKey("core.permissions.key"), primary_key=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class MembershipRole(Base):
    __tablename__ = "membership_roles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["core.memberships.tenant_id", "core.memberships.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(["tenant_id", "role_id"], ["core.roles.tenant_id", "core.roles.id"]),
        {"schema": SCHEMA},
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    membership_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class MembershipScope(Base):
    """``school`` (no ref), ``class`` (class id) or ``section`` (section id) of the same tenant."""

    __tablename__ = "membership_scopes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id"),
        ForeignKeyConstraint(
            ["tenant_id", "membership_id"],
            ["core.memberships.tenant_id", "core.memberships.id"],
            ondelete="CASCADE",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    tenant_id: Mapped[uuid.UUID]
    membership_id: Mapped[uuid.UUID]
    scope_type: Mapped[str] = mapped_column(Text)
    scope_ref: Mapped[uuid.UUID | None]
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
