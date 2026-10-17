"""Parent language per school and per guardian (ADR-0044; FR-LANG-001, FR-LANG-003, FR-LANG-005,
FR-LANG-006; US-2301..US-2303). Synthetic data only.

The school setting ``parent_language`` (step-up, audited with its value), the guardian's own
``preferred_language`` (student scope, audited), the APAAR consent form language that now follows
the school unless set, and the order guardian -> school -> English, all with
``SOS_TELUGU_ENABLED`` off: the staff UI languages stay English.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest
from sqlalchemy import Engine

from app.apaar import service as apaar
from app.core.db import tenant_session
from app.core.languages import telugu_enabled
from app.tenancy import service as tenancy

pytestmark = pytest.mark.db
P = sys.modules["sos_test_public_support"]
SW = sys.modules["sos_test_student_world"]
W = sys.modules["sos_test_api_world"]


@pytest.fixture
def school(admin_engine: Engine) -> Any:
    """A fresh school so the shared world's settings stay untouched."""
    tid = W.provision_school()
    s = W.School(tid)
    for role in ("owner", "office_admin", "principal", "class_teacher"):
        s.people[role] = W.add_member(admin_engine, tid, [role])
    W.build_structure(s, s.people["owner"])
    return s


def _patch(api: Any, who: Any, body: dict[str, Any], etag: str, **kw: Any) -> Any:
    return api.call(who, "PATCH", "/api/v1/tenant", json=body, headers={"If-Match": etag}, **kw)


def test_FR_LANG_001_school_parent_language_is_step_up_audited_and_independent(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    assert telugu_enabled() is False
    owner = school.people["owner"]
    got = api.call(owner, "GET", "/api/v1/tenant")
    assert got.status_code == 200
    assert got.json()["settings"]["parent_language"] == "en"
    assert got.json()["parent_language_unreviewed"] == []
    etag = got.headers["ETag"]
    denied = _patch(api, school.people["office_admin"], {"parent_language": "te"}, etag)
    assert denied.status_code == 403
    assert _patch(api, owner, {"parent_language": "te"}, etag, auth_age_s=301).status_code == 428
    assert _patch(api, owner, {"parent_language": "hi"}, etag).status_code == 422
    res = _patch(api, owner, {"parent_language": "te"}, etag)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["settings"]["parent_language"] == "te"
    # Staff UI languages are untouched: English only while Telugu is hidden (ADR-0036).
    assert body["settings"]["languages"] == ["en"]
    assert "apaar.consent_form" in body["parent_language_unreviewed"]
    assert "public.shell" in body["parent_language_unreviewed"]
    events = W.audit_events(admin_engine, school.tenant_id, "tenant.settings_updated")
    assert events[-1]["summary"] == {"fields": ["parent_language"], "parent_language": "te"}
    with tenant_session(school.tenant_id) as db:
        assert tenancy.parent_language(db) == "te"


def test_FR_LANG_003_guardian_language_overrides_and_is_audited(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    student, guardian = P.student_with_guardian(school)
    office = school.people["office_admin"]
    path = f"/api/v1/students/{student}/guardians/{guardian}"
    version = SW.version(admin_engine, "sis.guardians", guardian)
    res = api.call(
        office,
        "PATCH",
        path,
        json={"preferred_language": "te"},
        headers={"If-Match": f'W/"{version}"'},
    )
    assert res.status_code == 200, res.text
    assert res.json()["preferred_language"] == "te"
    listed = api.call(office, "GET", f"/api/v1/students/{student}/guardians").json()
    assert [g["preferred_language"] for g in listed if g["id"] == str(guardian)] == ["te"]
    events = [
        e
        for e in W.audit_events(admin_engine, school.tenant_id, "guardian.updated")
        if e["resource_id"] == guardian
    ]
    assert events[-1]["summary"]["fields"] == ["preferred_language"]
    assert events[-1]["summary"]["preferred_language"] == "te"
    # null goes back to the school's choice.
    version = SW.version(admin_engine, "sis.guardians", guardian)
    cleared = api.call(
        office,
        "PATCH",
        path,
        json={"preferred_language": None},
        headers={"If-Match": f'W/"{version}"'},
    )
    assert cleared.status_code == 200
    assert cleared.json()["preferred_language"] is None
    version = SW.version(admin_engine, "sis.guardians", guardian)
    bad = api.call(
        office,
        "PATCH",
        path,
        json={"preferred_language": "fr"},
        headers={"If-Match": f'W/"{version}"'},
    )
    assert bad.status_code == 422


def test_FR_LANG_006_apaar_form_language_follows_the_school_unless_set(
    school: Any, api: Any, admin_engine: Engine
) -> None:
    owner = school.people["owner"]
    settings = api.call(owner, "GET", "/api/v1/apaar/settings")
    assert settings.status_code == 200, settings.text
    assert settings.json() == {"form_language": None, "effective_language": "en", "version": 0}
    etag = api.call(owner, "GET", "/api/v1/tenant").headers["ETag"]
    assert _patch(api, owner, {"parent_language": "te"}, etag).status_code == 200
    follows = api.call(owner, "GET", "/api/v1/apaar/settings").json()
    assert (follows["form_language"], follows["effective_language"]) == (None, "te")
    with tenant_session(school.tenant_id) as db:
        assert apaar.parent_language(db) == "te"
    own = api.call(
        owner,
        "PUT",
        "/api/v1/apaar/settings",
        json={"form_language": "en"},
        headers={"If-Match": 'W/"0"'},
    )
    assert own.status_code == 200, own.text
    assert (own.json()["form_language"], own.json()["effective_language"]) == ("en", "en")
    with tenant_session(school.tenant_id) as db:
        assert apaar.parent_language(db) == "en"
    back = api.call(
        owner,
        "PUT",
        "/api/v1/apaar/settings",
        json={"form_language": None},
        headers={"If-Match": f'W/"{own.json()["version"]}"'},
    )
    assert back.status_code == 200, back.text
    assert (back.json()["form_language"], back.json()["effective_language"]) == (None, "te")
    events = W.audit_events(admin_engine, school.tenant_id, "apaar.settings.updated")
    assert events[-1]["summary"] == {"form_language": None, "previous_form_language": "en"}
