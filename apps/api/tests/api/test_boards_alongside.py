"""Boards per school and per class, and the "alongside your current ERP" mode over HTTP
(FR-TEN-020, FR-TEN-021, FR-TEN-022; ADR-0041). Synthetic data only."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.db
W = sys.modules["sos_test_api_world"]


def _tally() -> ModuleType:
    """tests/tally/support.py (the connector's per-school flag)."""
    name = "sos_test_tally_support"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[1] / "tally" / "support.py"
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


@pytest.fixture
def school(api: Any, admin_engine: Engine) -> Any:
    """A fresh school (owner, office admin, office staff) so the shared world is untouched."""
    tid = W.provision_school()
    s = W.School(tid)
    s.people["owner"] = W.add_member(admin_engine, tid, ["owner"])
    s.people["office_admin"] = W.add_member(admin_engine, tid, ["office_admin"])
    s.people["office_staff"] = W.add_member(admin_engine, tid, ["office_staff"])
    return s


def _patch(api: Any, who: Any, body: dict[str, Any], etag: str, **kw: Any) -> Any:
    return api.call(who, "PATCH", "/api/v1/tenant", json=body, headers={"If-Match": etag}, **kw)


def _boards_in_db(admin: Engine, tenant_id: uuid.UUID) -> list[str]:
    with admin.connect() as c:
        return list(
            c.execute(
                text("SELECT boards FROM core.tenants WHERE id = :t"), {"t": tenant_id}
            ).scalar_one()
        )


def test_FR_TEN_020_owner_declares_boards_and_class_boards(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    owner = school.people["owner"]
    got = api.call(owner, "GET", "/api/v1/tenant")
    assert got.status_code == 200
    body = got.json()
    assert body["settings"]["class_boards"] == {}
    assert body["settings"]["operating_mode"] == "full"
    assert body["modules_hidden"] == []
    etag = got.headers["ETag"]

    # 403 for a role without tenant.settings.manage; 428 without a recent MFA sign-in.
    denied = _patch(api, school.people["office_admin"], {"boards": ["CBSE"]}, etag)
    assert denied.status_code == 403
    assert _patch(api, owner, {"boards": ["CBSE"]}, etag, auth_age_s=301).status_code == 428
    for bad in (
        {"boards": ["SSC"]},
        {"boards": ["CBSE", "CBSE"]},
        {"class_boards": {"ix": "CBSE"}},
        {"class_boards": {"IX": "NIOS"}},
    ):
        assert _patch(api, owner, bad, etag).status_code == 422, bad
    undeclared = _patch(api, owner, {"boards": ["CBSE"], "class_boards": {"XI": "BSEAP"}}, etag)
    assert undeclared.status_code == 422
    assert undeclared.json()["errors"][0]["code"] == "class_board_not_declared"
    assert W.audit_events(admin_engine, school.tenant_id, "tenant.settings_updated") == []

    ok = _patch(
        api,
        owner,
        {"boards": ["CBSE", "BSEAP"], "class_boards": {"XI": "BSEAP", "XII": "BSEAP"}},
        etag,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["boards"] == ["CBSE", "BSEAP"]
    assert ok.json()["settings"]["class_boards"] == {"XI": "BSEAP", "XII": "BSEAP"}
    assert _boards_in_db(admin_engine, school.tenant_id) == ["CBSE", "BSEAP"]
    (event,) = W.audit_events(admin_engine, school.tenant_id, "tenant.settings_updated")
    assert event["summary"] == {"fields": ["boards", "class_boards"], "boards": ["CBSE", "BSEAP"]}

    # The profile lists follow the boards: CBSE (Class IX/X here) and UDISE+, no CISCE.
    res = api.call(school.people["office_admin"], "GET", "/api/v1/export-profiles")
    assert res.status_code == 200, res.text
    profiles = res.json()
    keys = {p["key"] for p in profiles}
    assert {"cbse-registration-2027", "cbse-loc-2027", "udise-plus"} <= keys
    assert not [k for k in keys if k.startswith("cisce-")]
    staff = school.people["office_staff"]
    dq_profiles = {p["key"]: p for p in api.call(staff, "GET", "/api/v1/dq/profiles").json()}
    assert dq_profiles["cbse-registration-2027"]["applies_to_classes"] == ["IX"]
    assert dq_profiles["cbse-registration-2027"]["verified"] is False
    assert dq_profiles["cbse-registration-2027"]["source"]
    every = api.call(staff, "GET", "/api/v1/dq/profiles", params={"all": "true"}).json()
    assert "cisce-registration-2027" in {p["key"] for p in every}

    # Removing a board a class still follows is refused; removing both together works.
    etag = ok.headers["ETag"]
    assert _patch(api, owner, {"boards": ["CBSE"]}, etag).status_code == 422
    both = _patch(api, owner, {"boards": ["CBSE"], "class_boards": {}}, etag)
    assert both.status_code == 200, both.text
    assert _boards_in_db(admin_engine, school.tenant_id) == ["CBSE"]


def test_FR_TEN_022_alongside_mode_hides_tally_and_is_audited(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    owner = school.people["owner"]
    got = api.call(owner, "GET", "/api/v1/tenant")
    ok = _patch(
        api,
        owner,
        {"operating_mode": "alongside", "current_erp_name": "Synthetic ERP"},
        got.headers["ETag"],
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["settings"]["operating_mode"] == "alongside"
    assert ok.json()["settings"]["current_erp_name"] == "Synthetic ERP"
    assert ok.json()["modules_hidden"] == ["tally"]
    (event,) = W.audit_events(admin_engine, school.tenant_id, "tenant.settings_updated")
    assert event["summary"] == {
        "fields": ["current_erp_name", "operating_mode"],
        "operating_mode": "alongside",
    }
    # Every member sees the mode (the web hides the modules for everyone).
    staff_view = api.call(school.people["office_staff"], "GET", "/api/v1/tenant").json()
    assert staff_view["modules_hidden"] == ["tally"]

    # No new Tally agent can be set up while the mode hides the connector.
    with _tally().flag_on(admin_engine, school.tenant_id):
        refused = api.call(
            owner, "POST", "/api/v1/tally/enrolment-codes", json={"device_name": "Office PC"}
        )
        assert refused.status_code == 409, refused.text
        assert refused.json()["code"] == "module_hidden"
        back = _patch(api, owner, {"operating_mode": "full"}, ok.headers["ETag"])
        assert back.status_code == 200
        assert back.json()["modules_hidden"] == []
        allowed = api.call(
            owner, "POST", "/api/v1/tally/enrolment-codes", json={"device_name": "Office PC"}
        )
        assert allowed.status_code == 201, allowed.text

    bad_name = _patch(api, owner, {"current_erp_name": "<script>"}, back.headers["ETag"])
    assert bad_name.status_code == 422


def test_SEC_001_settings_change_only_the_callers_school(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    other = W.provision_school()
    before = _boards_in_db(admin_engine, other)
    owner = school.people["owner"]
    got = api.call(owner, "GET", "/api/v1/tenant")
    res = _patch(api, owner, {"boards": ["CISCE"]}, got.headers["ETag"])
    assert res.status_code == 200
    assert res.json()["id"] == str(school.tenant_id)
    assert _boards_in_db(admin_engine, other) == before
    # Naming the other school as the active tenant gives no access to it.
    cross = api.call(owner, "GET", "/api/v1/tenant", tenant=other)
    assert cross.status_code in (403, 404)
