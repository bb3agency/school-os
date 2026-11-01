"""The public per-school feature-flag override (FR-PLT-022; docs/16 §5.11): other modules and dev
tooling set or clear one school's override through ``platform.service`` (system actor, platform
audit chain), also for a shared-tier school without a fleet ``deployments`` row. Synthetic only.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.crypto import LocalDevKeyWrapper
from app.core.db import platform_session
from app.core.errors import NotFound, ValidationFailed
from app.platform import flags
from app.platform import service as control_plane
from app.tenancy import service as tenancy

pytestmark = pytest.mark.db

SOURCE = "synthetic_test"


@pytest.fixture
def school(wrapper: LocalDevKeyWrapper, platform_engine: Engine, app_engine: Engine) -> uuid.UUID:
    """A school with no platform.deployments row (as seed-synthetic makes them)."""
    return tenancy.provision_tenant(
        code=f"s-{uuid.uuid4().hex[:12]}", name="Synthetic Flag School", wrapper=wrapper
    ).tenant_id


def _key() -> str:
    return f"synthetic.flag_{uuid.uuid4().hex[:6]}"


def _events(tenant_id: uuid.UUID, key: str) -> list[dict[str, Any]]:
    with platform_session() as s:
        rows = s.execute(
            text(
                "SELECT action, actor_type, summary FROM platform.audit_events "
                "WHERE subject_tenant_id = :t AND summary->>'key' = :k ORDER BY seq"
            ),
            {"t": tenant_id, "k": key},
        ).mappings()
        return [dict(r) for r in rows]


def test_FR_PLT_022_set_and_clear_a_school_override_without_a_deployment(school: uuid.UUID) -> None:
    key = _key()
    with platform_session() as s:
        assert (
            s.execute(
                text("SELECT count(*) FROM platform.deployments WHERE tenant_id = :t"),
                {"t": school},
            ).scalar_one()
            == 0
        )
    assert flags.is_enabled(key, school) is False

    assert control_plane.set_school_flag_override(key, school, True, source=SOURCE) is True
    assert flags.is_enabled(key, school) is True
    # The same value again changes nothing and audits nothing.
    assert control_plane.set_school_flag_override(key, school, True, source=SOURCE) is False
    assert control_plane.set_school_flag_override(key, school, False, source=SOURCE) is True
    assert flags.is_enabled(key, school) is False

    assert control_plane.clear_school_flag_override(key, school, source=SOURCE) is True
    assert control_plane.clear_school_flag_override(key, school, source=SOURCE) is False

    events = _events(school, key)
    assert [e["action"] for e in events] == [
        "flag.override_set",
        "flag.override_set",
        "flag.override_removed",
    ]
    assert {e["actor_type"] for e in events} == {"system"}
    assert events[0]["summary"]["before"] == {"exists": False}
    assert events[0]["summary"]["after"]["enabled"] is True
    assert {e["summary"]["source"] for e in events} == {SOURCE}


def test_FR_PLT_022_override_refuses_a_malformed_flag_key(school: uuid.UUID) -> None:
    for bad in ("nodot", "Upper.case", "fees.enabled; drop", "a." + "b" * 120):
        with pytest.raises(ValidationFailed):
            control_plane.set_school_flag_override(bad, school, True, source=SOURCE)
        with pytest.raises(ValidationFailed):
            control_plane.clear_school_flag_override(bad, school, source=SOURCE)


def test_FR_PLT_022_override_refuses_an_unknown_school(platform_engine: Engine) -> None:
    unknown = uuid.uuid4()
    with pytest.raises(NotFound):
        control_plane.set_school_flag_override(_key(), unknown, True, source=SOURCE)
    with pytest.raises(NotFound):
        control_plane.clear_school_flag_override(_key(), unknown, source=SOURCE)
