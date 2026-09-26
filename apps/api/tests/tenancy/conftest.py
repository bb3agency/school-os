"""Fixtures for tenancy tests. Synthetic data only; setup uses the admin engine."""

from __future__ import annotations

import uuid
from collections.abc import Callable

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, text

from app.core.config import Environment, KeyWrapperKind, Settings
from app.core.crypto import LocalDevKeyWrapper

MakeTenant = Callable[..., uuid.UUID]
MakeMember = Callable[..., tuple[uuid.UUID, uuid.UUID]]


@pytest.fixture
def make_tenant(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> MakeTenant:
    def _make(status: str = "active") -> uuid.UUID:
        tid = uuid.uuid4()
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO core.tenants (id, code, name, status) "
                    "VALUES (:i, :c, 'Synthetic School', :s)"
                ),
                {"i": tid, "c": f"t-{uuid.uuid4().hex[:12]}", "s": status},
            )
        return tid

    return _make


@pytest.fixture
def make_member(admin_engine: Engine) -> MakeMember:
    """Create a synthetic user with a membership; return (user_id, membership_id)."""

    def _make(tenant_id: uuid.UUID, status: str = "active") -> tuple[uuid.UUID, uuid.UUID]:
        uid, mid = uuid.uuid4(), uuid.uuid4()
        with admin_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO core.users (id, idp_subject, display_name) "
                    "VALUES (:u, :s, 'Synthetic Staff')"
                ),
                {"u": uid, "s": f"sub-{uuid.uuid4().hex}"},
            )
            c.execute(
                text(
                    "INSERT INTO core.memberships (id, tenant_id, user_id, status) "
                    "VALUES (:m, :t, :u, :st)"
                ),
                {"m": mid, "t": tenant_id, "u": uid, "st": status},
            )
        return uid, mid

    return _make


@pytest.fixture
def local_wrapper() -> LocalDevKeyWrapper:
    return LocalDevKeyWrapper(
        Settings(
            env=Environment.CI,
            key_wrapper=KeyWrapperKind.LOCAL_DEV,
            local_dev_master_key=SecretStr("synthetic-ci-master-key-0123456789abcdef"),
        )
    )
