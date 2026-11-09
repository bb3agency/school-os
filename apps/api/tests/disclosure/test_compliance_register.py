"""CBSE Appendix IX and the compliance certificate register (FC-01; FR-COMP-020..FR-COMP-022).
Real database (RLS), synthetic data only.

The draft shows the register's current certificate for the items it backs (fire safety,
building safety, affiliation ...) with its state; publishing with an expired one warns
(default, lead decision D85) or refuses (``disclosure: refuse``).
"""

from __future__ import annotations

import datetime as dt
import sys
import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.compliance import config as cfg

pytestmark = pytest.mark.db
S = sys.modules["sos_test_disclosure_support"]
BASE = S.BASE


def _certificate(
    admin: Engine, school: Any, kind: str, valid_until: dt.date, doc: uuid.UUID
) -> uuid.UUID:
    cid = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.compliance_certificates (id, tenant_id, kind, valid_until, "
                "document_id, recorded_by) VALUES (:i, :t, :k, :v, :d, :u)"
            ),
            {
                "i": cid,
                "t": school.tenant_id,
                "k": kind,
                "v": valid_until,
                "d": doc,
                "u": school.people["owner"].user_id,
            },
        )
    return cid


def test_FR_COMP_020_draft_shows_register_certificates(
    api: Any, school: Any, admin_engine: Engine
) -> None:
    doc = S.document(admin_engine, school, purpose="other")
    today = dt.datetime.now(dt.UTC).date()
    cid = _certificate(admin_engine, school, "fire_noc", today - dt.timedelta(days=3), doc)
    res = api.call(school.people["office_admin"], "GET", f"{BASE}/draft")
    assert res.status_code == 200, res.text
    body = res.json()
    (fire,) = body["register"]["fire_safety"]
    assert fire["certificate_id"] == str(cid)
    assert fire["state"] == "expired"
    assert fire["document_id"] == str(doc)
    assert fire["publishable"] is True
    assert "building_safety" not in body["register"]
    assert body["expired_policy"] == "warn"


def test_FR_COMP_021_publish_with_expired_certificate_warns_and_audits(
    api: Any, school: Any, admin_engine: Engine, renderer: Any
) -> None:
    doc = S.document(admin_engine, school, purpose="other")
    today = dt.datetime.now(dt.UTC).date()
    _certificate(admin_engine, school, "fire_noc", today - dt.timedelta(days=3), doc)
    owner = school.people["owner"]
    saved = S.save_draft(api, owner, documents={"fire_safety": [str(doc)]})
    assert saved.status_code == 200, saved.text
    res = S.publish(api, owner)
    assert res.status_code == 201, res.text
    events = S.W.audit_events(admin_engine, school.tenant_id, "disclosure.published")
    assert events[-1]["summary"]["expired_certificates"] == ["fire_safety"]


def test_FR_COMP_022_publish_refuses_when_configured(
    api: Any, school: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = S.document(admin_engine, school, purpose="other")
    today = dt.datetime.now(dt.UTC).date()
    _certificate(admin_engine, school, "building_safety", today - dt.timedelta(days=1), doc)
    refuse = cfg.catalogue().model_copy(update={"disclosure": "refuse"})
    monkeypatch.setattr(cfg, "catalogue", lambda: refuse)
    owner = school.people["owner"]
    assert S.save_draft(api, owner).status_code == 200
    res = S.publish(api, owner)
    assert res.status_code == 422, res.text
    assert res.json()["code"] == "certificate_expired"
    assert {e["field"] for e in res.json()["errors"]} == {"documents.building_safety"}
