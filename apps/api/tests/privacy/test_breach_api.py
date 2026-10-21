"""The school's breach register over HTTP (R3-1B; ADR-0050; US-2803, US-2804;
FR-DPDP-033..038, PRV-008, invariant 5). Synthetic data only."""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core.config import DeploymentMode, get_settings
from app.core.db import tenant_session
from app.core.redaction import verhoeff_check_digit

pytestmark = pytest.mark.db
P = sys.modules["sos_test_consent_support"]
W = sys.modules["sos_test_api_world"]
BASE = "/api/v1/privacy/breaches"


def test_FR_DPDP_033_record_a_breach_with_the_description_encrypted(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    principal = world.person("principal")
    out = P.create_breach(api, principal, containment="Asked the bus depot to search.")
    assert out["origin"] == "school"
    assert out["description"] == P.DESCRIPTION
    assert out["containment"] == "Asked the bus depot to search."
    row = P.breach_row(admin_engine, out["id"])
    assert row["description_ciphertext"] is not None
    assert P.DESCRIPTION.encode() not in bytes(row["description_ciphertext"])
    (event,) = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "privacy.breach.recorded")
        if str(e["resource_id"]) == out["id"]
    ]
    assert event["summary"]["categories"] == ["attendance", "identity"]
    assert event["summary"]["approx_children"] == 42
    assert "bus" not in str(event["summary"])


def test_FR_DPDP_033_writes_need_manage_with_step_up(world: Any, api: Any) -> None:
    body = P.breach_body()
    stale = api.call(world.person("owner"), "POST", BASE, json=body, auth_age_s=301)
    assert stale.status_code == 428
    for role in ("office_admin", "auditor_readonly", "class_teacher", "accountant"):
        res = api.call(world.person(role), "POST", BASE, json=body)
        assert res.status_code == 403, (role, res.text)
    auditor = api.call(world.person("auditor_readonly"), "GET", BASE)
    assert auditor.status_code == 200
    assert api.call(world.person("office_admin"), "GET", BASE).status_code == 403


def test_FR_DPDP_033_refusals(world: Any, api: Any) -> None:
    owner = world.person("owner")
    future = (dt.datetime.now(dt.UTC) + dt.timedelta(days=1)).isoformat()
    res = api.call(owner, "POST", BASE, json=P.breach_body(noticed_at=future))
    assert res.status_code == 422
    assert "date_in_future" in res.text
    base = "34567890123"
    aadhaar = base + verhoeff_check_digit(base)
    res = api.call(
        owner, "POST", BASE, json=P.breach_body(description=f"Aadhaar {aadhaar} was printed.")
    )
    assert res.status_code == 422
    assert "aadhaar_full_number_rejected" in res.text
    repeat = api.call(owner, "POST", BASE, json=P.breach_body(categories=["identity", "identity"]))
    assert repeat.status_code == 422


def test_FR_DPDP_038_processor_notice_by_hand_only_without_a_control_plane(
    world: Any, api: Any
) -> None:
    owner = world.person("owner")
    body = P.breach_body(origin="processor_notice")
    res = api.call(owner, "POST", BASE, json=body)
    if get_settings().deployment_mode is DeploymentMode.SHARED:
        assert res.status_code == 422
        assert "pulled_automatically" in res.text
    else:  # pragma: no cover - the suite runs shared by default
        assert res.status_code == 201


def test_FR_DPDP_034_duties_clocks_and_next_duty(world: Any, api: Any) -> None:
    owner = world.person("owner")
    out = P.create_breach(api, owner, noticed_at=P.noticed(30))
    duties = {d["key"]: d for d in out["duties"]}
    assert duties["board_notice"]["overdue"] is True
    assert duties["board_notice"]["default_rule"] is True
    assert duties["board_report"]["overdue"] is False
    assert duties["people_notified"]["due_at"] is None
    assert out["next_duty"]["key"] == "board_notice"
    assert out["next_duty"]["hours_left"] < 0
    path = f"{BASE}/{out['id']}"
    done = dt.datetime.now(dt.UTC).isoformat()
    res = api.call(
        owner,
        "PATCH",
        path,
        json={"board_notice": {"done_at": done, "method": "email"}},
        headers={"If-Match": f'W/"{out["version"]}"'},
    )
    assert res.status_code == 200, res.text
    after = res.json()
    assert after["next_duty"]["key"] == "board_report"
    assert 40 <= after["next_duty"]["hours_left"] <= 42
    listed = api.call(owner, "GET", BASE).json()
    statuses = [b["status"] for b in listed]
    assert statuses == sorted(statuses, key=lambda s: s == "closed"), "open breaches first"


def test_FR_DPDP_034_closing_needs_the_duties_or_a_reason(world: Any, api: Any) -> None:
    owner = world.person("owner")
    out = P.create_breach(api, owner)
    path = f"{BASE}/{out['id']}"
    res = api.call(owner, "PATCH", path, json={"status": "closed"}, headers={"If-Match": 'W/"1"'})
    assert res.status_code == 409
    assert res.json()["code"] == "duties_open"
    reason = "The register was found within the hour; nothing was read."
    res = api.call(
        owner,
        "PATCH",
        path,
        json={"status": "closed", "close_reason": reason},
        headers={"If-Match": 'W/"1"'},
    )
    assert res.status_code == 200, res.text
    assert res.json()["closed_at"] is not None
    assert res.json()["next_duty"] is None
    again = api.call(owner, "PATCH", path, json={"approx_staff": 2}, headers={"If-Match": 'W/"2"'})
    assert again.status_code == 409
    assert again.json()["code"] == "breach_closed"
    stale = P.create_breach(api, owner)
    res = api.call(
        owner,
        "PATCH",
        f"{BASE}/{stale['id']}",
        json={"approx_staff": 3},
        headers={"If-Match": 'W/"9"'},
    )
    assert res.status_code == 412


def test_FR_DPDP_033_affected_ids_of_this_school_only(world: Any, api: Any) -> None:
    owner = world.person("owner")
    ids = P.SW.ensure_students(world)
    out = P.create_breach(api, owner)
    path = f"{BASE}/{out['id']}/affected"
    body = {
        "student_ids": [str(ids["s9a"]), str(ids["s9c"])],
        "guardian_ids": [str(ids["g9a"])],
        "membership_ids": [str(world.person("teacher").membership_id)],
    }
    res = api.call(owner, "PUT", path, json=body)
    assert res.status_code == 200, res.text
    assert len(res.json()["student_ids"]) == 2
    detail = api.call(owner, "GET", f"{BASE}/{out['id']}").json()
    assert detail["affected"] == {"students": 2, "guardians": 1, "staff": 1}
    other = api.call(owner, "PUT", path, json={"student_ids": [str(ids["b_sb"])]})
    assert other.status_code == 422
    assert "student_ids" in other.text
    # The failed replacement changed nothing.
    assert len(api.call(owner, "GET", path).json()["student_ids"]) == 2


def test_FR_DPDP_036_drafts_are_filled_from_the_record(world: Any, api: Any) -> None:
    owner = world.person("owner")
    out = P.create_breach(api, owner)
    family = api.call(
        world.person("auditor_readonly"), "GET", f"{BASE}/{out['id']}/drafts/family_notice"
    )
    assert family.status_code == 200, family.text
    draft = family.json()
    assert draft["template_version"] == "0.1 (2026-10-21)"
    assert P.DESCRIPTION in draft["text"]
    assert "names and identity details" in draft["text"]
    assert "[not set]" in draft["text"], "an unset privacy contact prints [not set]"
    board = api.call(owner, "GET", f"{BASE}/{out['id']}/drafts/board_outline")
    assert board.status_code == 200
    assert "72 hours" in board.json()["text"]
    assert api.call(owner, "GET", f"{BASE}/{out['id']}/drafts/other").status_code == 422


def test_FR_DPDP_037_family_notice_becomes_an_m4_draft_without_the_description(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    owner = world.person("owner")
    out = P.create_breach(api, owner)
    res = api.call(owner, "POST", f"{BASE}/{out['id']}/notice-draft")
    assert res.status_code == 201, res.text
    notice_id = res.json()["notice_id"]
    with admin_engine.connect() as c:
        row = c.execute(
            text("SELECT source, status, source_text FROM ops.parent_notices WHERE id = :i"),
            {"i": notice_id},
        ).one()
    assert row.source == "staff_text"
    assert row.status in ("drafting", "draft")
    assert P.DESCRIPTION not in row.source_text
    assert "[describe what happened in plain words]" in row.source_text
    # Nothing was sent: no message rows for this notice.
    with admin_engine.connect() as c:
        sent: int = c.execute(
            text("SELECT count(*) FROM msg.messages WHERE tenant_id = :t AND source_id = :n"),
            {"t": world.a.tenant_id, "n": notice_id},
        ).scalar_one()
    assert sent == 0


def test_FR_DPDP_038_other_school_and_random_ids_are_404(world: Any, api: Any) -> None:
    out = P.create_breach(api, world.person("owner"))
    b_owner = world.b.people["owner"]
    assert api.call(b_owner, "GET", f"{BASE}/{out['id']}").status_code == 404
    res = api.call(
        b_owner,
        "PATCH",
        f"{BASE}/{out['id']}",
        json={"approx_staff": 1},
        headers={"If-Match": 'W/"1"'},
    )
    assert res.status_code == 404
    assert api.call(world.person("owner"), "GET", f"{BASE}/{uuid.uuid4()}").status_code == 404


def test_SEC_008_breach_texts_never_reach_logs_or_audit(
    world: Any, api: Any, admin_engine: Engine, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "Synthetica Kothapalli list was photographed on a phone"
    owner = world.person("owner")
    capsys.readouterr()
    out = P.create_breach(api, owner, description=secret, containment=secret + " again")
    api.call(owner, "GET", f"{BASE}/{out['id']}")
    api.call(owner, "GET", f"{BASE}/{out['id']}/drafts/family_notice")
    api.call(
        owner,
        "PATCH",
        f"{BASE}/{out['id']}",
        json={"description": secret + " (updated)"},
        headers={"If-Match": 'W/"1"'},
    )
    logs = "".join(capsys.readouterr())
    assert "Kothapalli" not in logs
    summaries = " ".join(str(e["summary"]) for e in W.audit_events(admin_engine, world.a.tenant_id))
    assert "Kothapalli" not in summaries


def test_SEC_012_breach_texts_are_in_the_key_rotation_census() -> None:
    from app.students import rotation

    assert ("ops.breach_records", "description_ciphertext") in rotation.CIPHERTEXT_COLUMNS
    assert ("ops.breach_records", "containment_ciphertext") in rotation.CIPHERTEXT_COLUMNS
    assert "privacy_breach" in rotation.reencryptor_names()


def test_FR_DPDP_018_breach_rows_are_purged_with_the_school(world: Any) -> None:
    from app.privacy import breach_service, consents_service

    with tenant_session(world.a.tenant_id) as db:
        assert set(breach_service.tenant_data_counts(db)) == {
            "ops.breach_reminders",
            "ops.breach_affected",
            "ops.breach_records",
        }
        assert set(consents_service.tenant_data_counts(db)) == {
            "sis.consents",
            "sis.consent_purposes",
        }
