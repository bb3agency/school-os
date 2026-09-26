"""ORM models match migration 0003 (columns, nullability, primary keys)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, Table, inspect

from app.core.model_base import Base
from app.identity import models as identity_models
from app.tenancy import models as tenancy_models

pytestmark = pytest.mark.db

MODELS = [
    tenancy_models.Tenant,
    tenancy_models.TenantKey,
    tenancy_models.AcademicYear,
    tenancy_models.SchoolClass,
    tenancy_models.Section,
    identity_models.User,
    identity_models.Membership,
    identity_models.Permission,
    identity_models.Role,
    identity_models.RolePermission,
    identity_models.MembershipRole,
    identity_models.MembershipScope,
]


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__tablename__)
def test_FR_TEN_001_model_matches_database(model: type[Base], admin_engine: Engine) -> None:
    table = model.__table__
    assert isinstance(table, Table)
    insp = inspect(admin_engine)
    db_cols = {c["name"]: c for c in insp.get_columns(table.name, schema=table.schema)}
    assert set(db_cols) == {c.name for c in table.columns}
    for col in table.columns:
        assert db_cols[col.name]["nullable"] == col.nullable, f"{table.name}.{col.name}"
    pk = insp.get_pk_constraint(table.name, schema=table.schema)["constrained_columns"]
    assert sorted(pk) == sorted(c.name for c in table.primary_key.columns)
