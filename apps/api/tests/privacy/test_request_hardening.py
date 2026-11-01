"""R3-2F hardening of privacy requests and the breach register (FR-DPDP-009, FR-DPDP-010,
FR-DPDP-011, FR-DPDP-013, FR-DPDP-026, FR-DPDP-034). Synthetic data only."""

from __future__ import annotations

import ast
import datetime as dt
import sys
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from app.privacy import breach_repository, service
from app.privacy import repository as privacy_repo

pytestmark = pytest.mark.db
P = sys.modules["sos_test_privacy_support"]
CP = sys.modules["sos_test_consent_support"]
BASE = P.BASE
BREACHES = "/api/v1/privacy/breaches"


def _released(school: Any, api: Any) -> dict[str, Any]:
    admin = school.people["office_admin"]
    out = P.logged(api, admin, P.body(student_id=P.student(school)))
    res = api.call(admin, "POST", f"{BASE}/{out['id']}/report")
    assert res.status_code == 202, res.text
    assert P.run_report(school, res.json()["report"]["export_id"]) == "ready"
    rel = api.call(school.people["principal"], "POST", f"{BASE}/{out['id']}/report/release")
    assert rel.status_code == 200, rel.text
    released: dict[str, Any] = rel.json()
    return released


# --- 3b: a released report stays released -----------------------------------------------------


def test_FR_DPDP_010_released_report_cannot_be_rebuilt(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    req = _released(school, api)
    res = api.call(school.people["office_admin"], "POST", f"{BASE}/{req['id']}/report")
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "request_decided"
    row = P.request_row(admin_engine, req["id"])
    assert row["status"] == "decided"
    assert row["report_released_at"] is not None
    assert row["report_released_by"] is not None
    assert str(row["report_export_id"]) == req["report"]["export_id"]
    url = api.call(school.people["office_admin"], "GET", f"{BASE}/{req['id']}/report/download-url")
    assert url.status_code == 200, url.text


# --- 3f: no download from a withdrawn or unreleased closed request ----------------------------


def test_FR_DPDP_010_withdrawn_request_has_no_report_download(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    req = _released(school, api)
    # A request withdrawn before the R3 fix (decided -> withdrawn) still carries the release
    # marks; the link must not be handed out for it.
    with admin_engine.begin() as c:
        c.execute(
            text(
                "UPDATE ops.privacy_requests SET status = 'withdrawn', closed_at = now() "
                "WHERE id = :i"
            ),
            {"i": req["id"]},
        )
    res = api.call(school.people["office_admin"], "GET", f"{BASE}/{req['id']}/report/download-url")
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "request_closed"


def test_FR_DPDP_010_closed_request_without_release_has_no_download(school: Any, api: Any) -> None:
    admin = school.people["office_admin"]
    out = P.logged(api, admin, P.body(student_id=P.student(school)))
    res = api.call(admin, "POST", f"{BASE}/{out['id']}/report")
    assert P.run_report(school, res.json()["report"]["export_id"]) == "ready"
    closed = api.call(school.people["principal"], "POST", f"{BASE}/{out['id']}/close", json={})
    assert closed.status_code == 200, closed.text
    url = api.call(admin, "GET", f"{BASE}/{out['id']}/report/download-url")
    assert url.status_code == 409, url.text
    assert url.json()["code"] == "request_closed"


def test_FR_DPDP_010_closed_after_release_still_downloads(school: Any, api: Any) -> None:
    req = _released(school, api)
    closed = api.call(school.people["principal"], "POST", f"{BASE}/{req['id']}/close", json={})
    assert closed.status_code == 200, closed.text
    url = api.call(school.people["office_admin"], "GET", f"{BASE}/{req['id']}/report/download-url")
    assert url.status_code == 200, url.text


# --- 3c: constraint errors map to coded errors, never a 500 -----------------------------------


def _update_calls_outside_db_errors() -> list[int]:
    """Line numbers of ``repo.update_request`` calls in privacy/service.py that are not inside a
    ``with _db_errors():`` block."""
    tree = ast.parse(Path(service.__file__).read_text("utf-8"))
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With) and any(
            isinstance(i.context_expr, ast.Call)
            and isinstance(i.context_expr.func, ast.Name)
            and i.context_expr.func.id == "_db_errors"
            for i in node.items
        ):
            guarded |= {n.lineno for n in ast.walk(node) if hasattr(n, "lineno")}
    calls = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "update_request"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "repo"
    ]
    assert calls, "privacy/service.py no longer calls repo.update_request"
    return [line for line in calls if line not in guarded]


def test_FR_DPDP_011_every_request_update_maps_constraint_errors() -> None:
    assert _update_calls_outside_db_errors() == []


def test_FR_DPDP_009_build_report_maps_a_constraint_race_to_a_code(
    school: Any, api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = school.people["office_admin"]
    out = P.logged(api, admin, P.body(student_id=P.student(school)))

    def racing(*_a: Any, **_k: Any) -> Any:
        raise DBAPIError("UPDATE", {}, Exception("privacy_request_events_seq_key"))

    monkeypatch.setattr(privacy_repo, "update_request", racing)
    res = api.call(admin, "POST", f"{BASE}/{out['id']}/report")
    assert res.status_code == 412, res.text


# --- 3d: an old open breach is never dropped from the register --------------------------------


def test_FR_DPDP_034_old_open_breach_stays_listed_behind_many_closed(
    world: Any, api: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = world.person("owner")
    old = (dt.datetime.now(dt.UTC) - dt.timedelta(days=300)).isoformat()
    open_old = CP.create_breach(api, owner, noticed_at=old)
    reason = "Found within the hour; nothing was read."
    for _ in range(2):
        b = CP.create_breach(api, owner)
        res = api.call(
            owner,
            "PATCH",
            f"{BREACHES}/{b['id']}",
            json={"status": "closed", "close_reason": reason},
            headers={"If-Match": 'W/"1"'},
        )
        assert res.status_code == 200, res.text
    monkeypatch.setattr(breach_repository, "CLOSED_LIMIT", 1)
    listed = api.call(owner, "GET", BREACHES).json()
    assert open_old["id"] in [b["id"] for b in listed]
    assert sum(1 for b in listed if b["status"] == "closed") == 1
    statuses = [b["status"] for b in listed]
    assert statuses == sorted(statuses, key=lambda s: s == "closed")


# --- 3e: a close reason only comes with closing ------------------------------------------------


def test_FR_DPDP_034_close_reason_on_an_open_breach_is_refused(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    b = CP.create_breach(api, owner)
    res = api.call(
        owner,
        "PATCH",
        f"{BREACHES}/{b['id']}",
        json={"close_reason": "Not needed", "approx_staff": 4},
        headers={"If-Match": 'W/"1"'},
    )
    assert res.status_code == 422, res.text
    assert "only_when_closing" in res.text
    row = CP.breach_row(admin_engine, b["id"])
    assert row["close_reason"] is None
    assert row["status"] != "closed"
    assert row["version"] == 1
