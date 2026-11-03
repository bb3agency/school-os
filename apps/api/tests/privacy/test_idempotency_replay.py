"""Idempotency replay records keep no decrypted C3 text (data-protection H-01 for the R3 privacy
routes; FR-DPDP-005, FR-DPDP-033; invariant 5, SEC-008). ``POST /privacy/requests`` and
``POST /privacy/breaches`` answer with texts that are stored only as ciphertext (requester name,
request text, notes, nominee; breach description and containment). The Valkey record of an
``Idempotency-Key`` must hold status, the id and headers only, so the plaintext does not sit in
Valkey for 24 hours (and survive an erasure or an offboarding). Synthetic data only."""

from __future__ import annotations

import hashlib
import sys
import uuid
from typing import Any

import pytest

from app.authz.kv import kv_store

pytestmark = pytest.mark.db
P = sys.modules["sos_test_privacy_support"]
CP = sys.modules["sos_test_consent_support"]


def _idem_record(tenant_id: Any, user_id: Any, path: str, key: str) -> bytes:
    """The stored Idempotency-Key record of a POST (app/authz/http.py)."""
    scope = hashlib.sha256(f"POST {path} {key}".encode()).hexdigest()
    raw = kv_store().get(f"sos:idem:{tenant_id}:{user_id}:{scope}")
    assert raw is not None
    return raw


def test_FR_DPDP_005_request_replay_keeps_no_request_text_in_valkey(school: Any, api: Any) -> None:
    admin = school.people["office_admin"]
    sid = P.student(school)
    secret = f"Kothapalli Synthetica asks {uuid.uuid4().hex}"
    body = P.body(student_id=sid, text_=secret)
    key = f"privacy-{uuid.uuid4().hex}"
    first = P.log(api, admin, body, headers={"Idempotency-Key": key})
    assert first.status_code == 201, first.text
    assert first.json()["request_text"] == secret
    raw = _idem_record(school.tenant_id, admin.user_id, P.BASE, key)
    for plaintext in (secret, "Synthetica Requester"):
        assert plaintext.encode() not in raw
    assert b'"body"' not in raw
    again = P.log(api, admin, body, headers={"Idempotency-Key": key})
    assert again.status_code == 201
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.headers["ETag"] == first.headers["ETag"]
    assert again.json() == first.json()


def test_FR_DPDP_033_breach_replay_keeps_no_description_in_valkey(world: Any, api: Any) -> None:
    principal = world.person("principal")
    path = "/api/v1/privacy/breaches"
    containment = f"Kothapalli depot searched {uuid.uuid4().hex}"
    body = CP.breach_body(containment=containment)
    key = f"breach-{uuid.uuid4().hex}"
    first = api.call(principal, "POST", path, json=body, headers={"Idempotency-Key": key})
    assert first.status_code == 201, first.text
    assert first.json()["containment"] == containment
    raw = _idem_record(world.a.tenant_id, principal.user_id, path, key)
    for plaintext in (CP.DESCRIPTION, containment):
        assert plaintext.encode() not in raw
    assert b'"body"' not in raw
    again = api.call(principal, "POST", path, json=body, headers={"Idempotency-Key": key})
    assert again.status_code == 201
    assert again.headers.get("Idempotent-Replayed") == "true"
    assert again.headers["ETag"] == first.headers["ETag"]
    assert again.json() == first.json()
