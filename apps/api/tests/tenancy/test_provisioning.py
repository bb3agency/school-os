"""Tenant provisioning and lifecycle (US-201, FR-TEN-001, FR-TEN-003, ADR-0013)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator

import pytest
from app.core.crypto import LocalDevKeyWrapper
from app.core.db import context_free_session, platform_session, tenant_session
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.tenancy import repository as repo
from app.tenancy import service
from app.tenancy.schemas import TenantProvisionIn
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.db

MakeTenant = Callable[..., uuid.UUID]


def _code() -> str:
    return f"s-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def hooks() -> Iterator[list[uuid.UUID]]:
    calls: list[uuid.UUID] = []

    def record(session: Session, tenant_id: uuid.UUID) -> None:
        assert session.execute(text("SELECT core.current_tenant()")).scalar() == tenant_id
        calls.append(tenant_id)

    service.POST_PROVISION_HOOKS.append(record)
    yield calls
    service.POST_PROVISION_HOOKS.remove(record)


def test_US_201_AC1_provision_creates_tenant_and_wrapped_keys(
    local_wrapper: LocalDevKeyWrapper,
    admin_engine: Engine,
    app_engine: Engine,
    platform_engine: Engine,
    hooks: list[uuid.UUID],
) -> None:
    result = service.provision_tenant(
        code=_code(), name="  Synthetic High School ", boards=["CISCE"], wrapper=local_wrapper
    )
    assert result.tenant_id.version == 7
    assert (result.status, result.key_version, result.key_id) == (
        "provisioning",
        1,
        local_wrapper.key_id,
    )
    assert hooks == [result.tenant_id]
    with admin_engine.connect() as c:
        row = c.execute(
            text("SELECT name, status, boards FROM core.tenants WHERE id = :t"),
            {"t": result.tenant_id},
        ).one()
    assert tuple(row) == ("Synthetic High School", "provisioning", ["CISCE"])
    with tenant_session(result.tenant_id) as s:
        keys = repo.list_tenant_keys(s)
    assert len(keys) == 1
    dek = local_wrapper.unwrap(keys[0].wrapped_dek, tenant_id=result.tenant_id)
    hmac_key = local_wrapper.unwrap(keys[0].wrapped_hmac, tenant_id=result.tenant_id)
    assert len(dek) == len(hmac_key) == 32
    assert dek != hmac_key


def test_US_201_AC2_provisioned_tenant_invisible_to_other_tenants(
    local_wrapper: LocalDevKeyWrapper, make_tenant: MakeTenant
) -> None:
    result = service.provision_tenant(code=_code(), name="School A", wrapper=local_wrapper)
    other = make_tenant()
    with tenant_session(other) as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM core.tenants WHERE id = :t"), {"t": result.tenant_id}
            ).scalar()
            == 0
        )
        assert repo.list_tenant_keys(s) == []
    with context_free_session() as s:
        assert s.execute(text("SELECT count(*) FROM core.tenant_keys")).scalar() == 0


def test_FR_TEN_003_initialise_is_idempotent_and_keeps_the_key(
    local_wrapper: LocalDevKeyWrapper, platform_engine: Engine, app_engine: Engine
) -> None:
    result = service.provision_tenant(code=_code(), name="School", wrapper=local_wrapper)
    again = service.initialise_tenant(result.tenant_id, wrapper=local_wrapper)
    assert again == (1, local_wrapper.key_id)
    with tenant_session(result.tenant_id) as s:
        assert len(repo.list_tenant_keys(s)) == 1


def test_FR_TEN_003_lifecycle_through_service(
    local_wrapper: LocalDevKeyWrapper, platform_engine: Engine, app_engine: Engine
) -> None:
    result = service.provision_tenant(code=_code(), name="School", wrapper=local_wrapper)
    with platform_session() as p:
        change = service.activate_tenant(p, result.tenant_id)
    assert (change.previous, change.current) == ("provisioning", "active")
    with pytest.raises(Conflict):
        service.initialise_tenant(result.tenant_id, wrapper=local_wrapper)
    with platform_session() as p:
        assert service.suspend_tenant(p, result.tenant_id).current == "suspended"
    with platform_session() as p:
        assert service.reactivate_tenant(p, result.tenant_id).current == "active"
    with pytest.raises(Conflict), platform_session() as p:
        service.set_tenant_status(p, result.tenant_id, "deleted")
    with platform_session() as p:
        assert service.begin_offboarding(p, result.tenant_id).current == "offboarding"
    with pytest.raises(NotFound), platform_session() as p:
        service.activate_tenant(p, uuid.uuid4())


def test_FR_TEN_003_register_in_callers_platform_transaction_rolls_back(
    platform_engine: Engine, admin_engine: Engine
) -> None:
    """The control plane can bundle register_tenant with its own writes atomically."""
    code = _code()

    def register_then_fail() -> None:
        with platform_session() as p:
            service.register_tenant(p, TenantProvisionIn(code=code, name="School"))
            raise RuntimeError("later control-plane step failed")

    with pytest.raises(RuntimeError):
        register_then_fail()
    with admin_engine.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM core.tenants WHERE code = :c"), {"c": code}
            ).scalar()
            == 0
        )


def test_FR_TEN_003_duplicate_code_is_a_conflict(
    local_wrapper: LocalDevKeyWrapper, platform_engine: Engine, app_engine: Engine
) -> None:
    code = _code()
    service.provision_tenant(code=code, name="School", wrapper=local_wrapper)
    with pytest.raises(Conflict, match="already exists"):
        service.provision_tenant(code=code, name="School 2", wrapper=local_wrapper)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"code": "Bad Code"},
        {"name": ""},
        {"boards": ["cisce"]},
        {"boards": ["CISCE", "CISCE"]},
        {"plan_tier": "gold"},
    ],
)
def test_FR_TEN_003_invalid_input_rejected(
    kwargs: dict[str, object], local_wrapper: LocalDevKeyWrapper
) -> None:
    args: dict[str, object] = {"code": _code(), "name": "School", "wrapper": local_wrapper}
    args.update(kwargs)
    with pytest.raises(ValidationFailed):
        service.provision_tenant(**args)  # type: ignore[arg-type]


def test_ADR_0013_list_tenant_ids_and_usage(
    local_wrapper: LocalDevKeyWrapper, platform_engine: Engine, app_engine: Engine
) -> None:
    result = service.provision_tenant(code=_code(), name="School", wrapper=local_wrapper)
    with context_free_session() as s:
        assert result.tenant_id in service.list_tenant_ids(s, ["provisioning"])
        assert result.tenant_id not in service.list_tenant_ids(s)
        assert result.tenant_id in service.list_tenant_ids(s, None)
    with platform_session() as p:
        usage = service.tenant_usage(p, result.tenant_id)
    assert usage.model_dump() == {
        "active_memberships": 0,
        "users": 0,
        "sections": 0,
        "academic_years": 0,
    }
