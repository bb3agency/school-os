"""The break-glass role and guard (docs/07 §6.4, SEC-021, T17, FR-IAM-010).

- ``platform_support`` holds only scoped, normal read permissions; it is not a system role.
- A break-glass session can never use a state-changing route, even one guarded by a read
  permission, and every guarded call lands in the school's audit chain.
- docs/07 §6.4 lists exactly the role's permissions.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.authz import catalog
from app.authz.context import Scopes, UserContext
from app.authz.dependencies import get_user_context, require
from app.core.errors import install_error_handlers
from app.identity.principal import Principal, get_principal

from .conftest import Campus, W

DOC = Path(__file__).resolve().parents[4] / "docs" / "07-security-architecture.md"
Reader = Annotated[UserContext, Depends(require("document.read"))]


def test_FR_OPS_004_platform_support_is_read_only_and_not_a_system_role() -> None:
    role = catalog.breakglass_role()
    assert role.key == catalog.BREAKGLASS_ROLE == "platform_support"
    assert role.permission_keys == {"student.read_basic", "dq.findings.read", "document.read"}
    assert all(g.scoped and not g.step_up for g in role.grants)
    assert role.mfa_required
    assert role.membership_ttl is None
    assert not role.assign_any_role
    assert "platform_support" not in catalog.system_roles()
    assert "platform_support" not in catalog.mfa_roles()
    perms = catalog.permission_catalog()
    assert all(perms[p].sensitivity == "normal" for p in role.permission_keys)
    assert "student.read_sensitive" not in role.permission_keys
    assert "audit.read" not in role.permission_keys


def test_FR_OPS_004_docs_07_lists_the_platform_support_permissions() -> None:
    text = DOC.read_text("utf-8").split("### 6.4 Break-glass", 1)[1].split("### 6.5", 1)[0]
    rows = [ln for ln in text.splitlines() if ln.startswith("| `")]
    documented = {re.findall(r"`([a-z_.]+)`", ln)[0] for ln in rows}
    assert documented == set(catalog.breakglass_role().permission_keys)


def test_FR_OPS_004_catalog_rejects_a_writing_breakglass_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = catalog._load_yaml("roles.yaml")
    raw["breakglass_role"]["platform_support"]["grants"] = {"student.create": "scoped"}
    monkeypatch.setattr(catalog, "_load_yaml", lambda name: raw)
    catalog.breakglass_role.cache_clear()
    try:
        with pytest.raises(catalog.CatalogError):
            catalog.breakglass_role()
    finally:
        monkeypatch.undo()
        catalog.breakglass_role.cache_clear()
    assert catalog.breakglass_role().permission_keys


@pytest.mark.db
def test_SEC_021_breakglass_sessions_cannot_write_and_are_audited(
    admin_engine: Engine, app_engine: Engine, platform_engine: Engine
) -> None:
    tid = W.provision_school()
    member = W.add_member(admin_engine, tid, ["teacher"])
    ctx = UserContext(
        user_id=member.user_id,
        tenant_id=tid,
        membership_id=member.membership_id,
        roles=frozenset({"platform_support"}),
        permissions=frozenset({"session.authenticated", "document.read"}),
        scopes=Scopes(school=True),
        mfa=True,
        auth_time=dt.datetime.now(dt.UTC),
        via_breakglass=True,
    )
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/probe/{thing_id}")
    def read(ctx: Reader, thing_id: uuid.UUID) -> dict[str, str]:
        return {"ok": "read"}

    @app.post("/probe/{thing_id}")
    def write(ctx: Reader, thing_id: uuid.UUID) -> dict[str, str]:
        return {"ok": "written"}

    now = dt.datetime.now(dt.UTC)
    app.dependency_overrides[get_user_context] = lambda: ctx
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="op-sub",
        issuer="https://idp.synthetic.test/pool",
        kind="user",
        auth_time=now,
        mfa=True,
        session_id=None,
        expires_at=now + dt.timedelta(minutes=5),
    )
    thing = uuid.uuid4()
    with TestClient(app) as client:
        assert client.get(f"/probe/{thing}").status_code == 200
        res = client.post(f"/probe/{thing}")
    assert res.status_code == 403
    assert res.json()["code"] == "breakglass_read_only"
    events: list[dict[str, Any]] = W.audit_events(admin_engine, tid, "breakglass.access")
    assert [(e["summary"]["method"], e["summary"]["outcome"]) for e in events] == [
        ("GET", "allowed"),
        ("POST", "refused_read_only"),
    ]
    assert all(e["resource_id"] == thing for e in events)
    assert all(e["summary"]["route"] == "/probe/{thing_id}" for e in events)


@pytest.mark.db
def test_FR_OPS_004_close_refuses_ordinary_memberships(campus: Campus) -> None:
    from app.core.db import tenant_session
    from app.core.errors import Conflict
    from app.identity import service as identity

    with pytest.raises(Conflict), tenant_session(campus.tenant_id) as s:
        identity.close_breakglass_membership(
            s, None, campus.person("teacher").membership_id, reason="revoked"
        )
