"""Synthetic set-up for the Tally connector tests (M6; ADR-0032; synthetic data only).

Loaded by path (``--import-mode=importlib``). Builds on ``tests/api/world.py`` (schools A and B,
one member per role) and ``tests/students/student_world.py`` (students through the real service).

- :func:`set_flag`: the per-school ``tally.connector.enabled`` flag (a school override row; the
  control plane owns flags, this is test set-up through the admin engine).
- :class:`Agent`: an edge agent over the TestClient. It signs requests with its OWN copy of the
  canonical string (not the server's helper), so a drift between agent and server fails here.
- :func:`enrol`: owner creates a code through the API, the agent enrols with it.
- :func:`catalog`, :func:`select`, :func:`snapshot`: the agent reports groups, the accountant
  selects some, the agent sends parties.

Party ledger names are synthetic ("Synthetic Party ..."); amounts are made up.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import importlib.util
import json
import secrets
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
from sqlalchemy import Engine, text

TESTS = Path(__file__).resolve().parents[1]
FLAG = "tally.connector.enabled"
COMPANY = "Synthetic Model School 2026-27"
AGENT_VERSION = "0.1.0"


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


W = _load("sos_test_api_world", TESTS / "api" / "world.py")
SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")


def set_flag(admin: Engine, tenant_id: uuid.UUID, *, enabled: bool) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO platform.feature_flags (id, key, tenant_id, enabled) "
                "VALUES (:i, :k, :t, :e) ON CONFLICT (key, tenant_id) "
                "DO UPDATE SET enabled = EXCLUDED.enabled"
            ),
            {"i": uuid.uuid4(), "k": FLAG, "t": tenant_id, "e": enabled},
        )


# --- signing (the agent's side, written out independently of app.tally.agent_auth) ----------------


def canonical(method: str, path: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    lines = [
        "SOS-EDGE-HMAC-SHA256",
        method.upper(),
        path,
        timestamp,
        nonce,
        hashlib.sha256(body).hexdigest(),
    ]
    return "\n".join(lines).encode("utf-8")


def signature(
    secret: bytes, method: str, path: str, *, timestamp: str, nonce: str, body: bytes
) -> str:
    mac = hmac.new(secret, canonical(method, path, timestamp, nonce, body), hashlib.sha256)
    return "v1=" + mac.hexdigest()


def b64(secret: str) -> bytes:
    return base64.urlsafe_b64decode(secret + "=" * (-len(secret) % 4))


@dataclass
class Agent:
    tenant_id: uuid.UUID
    device_id: uuid.UUID
    key_id: str
    secret: bytes
    version: str = AGENT_VERSION
    sent: list[str] = field(default_factory=list)

    def headers(
        self,
        method: str,
        path: str,
        body: bytes,
        *,
        nonce: str | None = None,
        timestamp: int | None = None,
        secret: bytes | None = None,
        key_id: str | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> dict[str, str]:
        ts = str(timestamp if timestamp is not None else int(time.time()))
        n = nonce or secrets.token_urlsafe(18)
        return {
            "X-SOS-Tenant": str(tenant_id or self.tenant_id),
            "X-SOS-Device": str(self.device_id),
            "X-SOS-Key-Id": key_id or self.key_id,
            "X-SOS-Timestamp": ts,
            "X-SOS-Nonce": n,
            "X-SOS-Agent-Version": self.version,
            "X-SOS-Signature": signature(
                secret or self.secret, method, path, timestamp=ts, nonce=n, body=body
            ),
            "Content-Type": "application/json",
        }

    def call(
        self,
        api: Any,
        method: str,
        path: str,
        payload: Any = None,
        **kw: Any,
    ) -> httpx.Response:
        body = b"" if payload is None else json.dumps(payload).encode()
        headers = self.headers(method, path, body, **kw)
        res: httpx.Response = api.client.request(method, path, content=body, headers=headers)
        self.sent.append(path)
        return res


def enrol_headers(tenant_id: uuid.UUID, *, nonce: str | None = None) -> dict[str, str]:
    return {
        "X-SOS-Tenant": str(tenant_id),
        "X-SOS-Timestamp": str(int(time.time())),
        "X-SOS-Nonce": nonce or secrets.token_urlsafe(18),
        "Content-Type": "application/json",
    }


def new_code(api: Any, school: Any, *, name: str = "Office PC") -> dict[str, Any]:
    res = api.call(
        school.people["owner"],
        "POST",
        "/api/v1/tally/enrolment-codes",
        json={"device_name": name},
        tenant=school.tenant_id,
    )
    assert res.status_code == 201, res.text
    out: dict[str, Any] = res.json()
    return out


def enrol_with(api: Any, tenant_id: uuid.UUID, code: str) -> httpx.Response:
    body = {"code": code, "agent_version": AGENT_VERSION, "platform": "windows-10"}
    res: httpx.Response = api.client.post(
        "/api/v1/edge/tally/enrol", json=body, headers=enrol_headers(tenant_id)
    )
    return res


def enrol(api: Any, school: Any, *, name: str = "Office PC") -> Agent:
    code = new_code(api, school, name=name)["code"]
    res = enrol_with(api, school.tenant_id, code)
    assert res.status_code == 201, res.text
    out = res.json()
    return Agent(
        tenant_id=school.tenant_id,
        device_id=uuid.UUID(out["device_id"]),
        key_id=out["key_id"],
        secret=b64(out["secret"]),
    )


def revoke_all(api: Any, school: Any) -> None:
    """Revoke every active agent of the school (tests start from a clean device list)."""
    owner = school.people["owner"]
    res = api.call(owner, "GET", "/api/v1/tally/devices", tenant=school.tenant_id)
    assert res.status_code == 200, res.text
    for device in res.json():
        if device["status"] == "active":
            r = api.call(
                owner,
                "POST",
                f"/api/v1/tally/devices/{device['id']}/revoke",
                headers={"If-Match": f'W/"{device["version"]}"'},
                tenant=school.tenant_id,
            )
            assert r.status_code == 200, r.text


def catalog(
    api: Any, agent: Agent, groups: list[tuple[str, str | None]], *, company: str = COMPANY
) -> httpx.Response:
    return agent.call(
        api,
        "PUT",
        "/api/v1/edge/tally/catalog",
        {
            "company": company,
            "tally_product": "TallyPrime 5.1",
            "groups": [{"name": n, "parent": p} for n, p in groups],
        },
    )


def select(
    api: Any, school: Any, names: list[str], *, company: str = COMPANY, role: str = "accountant"
) -> list[dict[str, Any]]:
    who = school.people[role]
    res = api.call(who, "GET", "/api/v1/tally/groups", tenant=school.tenant_id)
    assert res.status_code == 200, res.text
    ids = [g["id"] for g in res.json() if g["company"] == company and g["name"] in names]
    res = api.call(
        who,
        "PUT",
        "/api/v1/tally/groups/selection",
        json={"company": company, "group_ids": ids},
        tenant=school.tenant_id,
    )
    assert res.status_code == 200, res.text
    out: list[dict[str, Any]] = res.json()
    return out


def party(
    name: str, balance: str, *, group: str = "Sundry Debtors", guid: str | None = None
) -> dict[str, Any]:
    return {"guid": guid, "name": name, "group": group, "closing_balance": balance}


def snapshot(
    api: Any,
    agent: Agent,
    parties: list[dict[str, Any]],
    *,
    batch_id: uuid.UUID | None = None,
    company: str = COMPANY,
    groups: list[str] | None = None,
    as_of: dt.date | None = None,
) -> httpx.Response:
    return agent.call(
        api,
        "POST",
        "/api/v1/edge/tally/syncs",
        {
            "batch_id": str(batch_id or uuid.uuid4()),
            "company": company,
            "as_of": (as_of or dt.date(2026, 9, 28)).isoformat(),
            "groups": groups if groups is not None else sorted({p["group"] for p in parties}),
            "parties": parties,
        },
    )


def fresh_school(admin: Engine, *, flag: bool = True) -> Any:
    """A new synthetic school with the world's structure and one member per role (so device
    limits, groups and parties of one test never meet another's)."""
    tenant_id = W.provision_school()
    school = W.School(tenant_id=tenant_id)
    school.people["owner"] = W.add_member(admin, tenant_id, ["owner"])
    W.build_structure(school, school.people["owner"])
    for role in W.ROLES:
        if role == "owner":
            continue
        scopes: list[tuple[str, uuid.UUID | None]] = []
        if role == "class_teacher":
            scopes = [("section", school.ids["section_9a"])]
        elif role == "teacher":
            scopes = [("class", school.ids["class_x"])]
        school.people[role] = W.add_member(admin, tenant_id, [role], scopes=scopes)
    if flag:
        set_flag(admin, tenant_id, enabled=True)
    return school


def audit_actions(
    admin: Engine, tenant_id: uuid.UUID, prefix: str = "tally."
) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT action, resource_type, resource_id, actor_type, summary "
                    "FROM audit.events WHERE tenant_id = :t AND action LIKE :p ORDER BY seq"
                ),
                {"t": tenant_id, "p": prefix + "%"},
            )
        ]


@contextmanager
def flag_on(admin: Engine, tenant_id: uuid.UUID) -> Iterator[None]:
    """The connector flag on for one block (security suites share schools with other tests)."""
    set_flag(admin, tenant_id, enabled=True)
    try:
        yield
    finally:
        set_flag(admin, tenant_id, enabled=False)


# --- rows written as the test superuser (setup for the security suites) ---------------------------


def seed_device(admin: Engine, tenant_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    """An active agent (and its used code); the key is a placeholder, never verified."""
    code, device = uuid.uuid4(), uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.tally_enrolment_codes (id, tenant_id, code_hash, device_name, "
                "created_by, expires_at, used_at) VALUES (:i, :t, :h, 'Office PC', :u, "
                "now() + interval '30 minutes', now())"
            ),
            {"i": code, "t": tenant_id, "h": hashlib.sha256(code.bytes).digest(), "u": user_id},
        )
        c.execute(
            text(
                "INSERT INTO ops.tally_devices (id, tenant_id, name, enrolment_code_id, key_id, "
                "key_ciphertext, enrolled_by) VALUES (:i, :t, 'Office PC', :c, :k, :w, :u)"
            ),
            {
                "i": device,
                "t": tenant_id,
                "c": code,
                "k": "tdk-" + "".join(chr(97 + b % 26) for b in (device.bytes * 2)[:20]),
                "w": b"\x09" * 40,
                "u": user_id,
            },
        )
    return device


def seed_party(
    admin: Engine,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    ledger: str = "Synthetic Other School Ledger",
) -> uuid.UUID:
    """One synced ledger (with the agent and sync record it came from)."""
    device = seed_device(admin, tenant_id, user_id)
    sync, party = uuid.uuid4(), uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.tally_syncs (id, tenant_id, device_id, batch_id, company, as_of, "
                "groups, parties, created, updated, missing, total_due) VALUES (:i, :t, :d, :b, "
                ":co, '2026-09-28', 1, 1, 1, 0, 0, 900.00)"
            ),
            {"i": sync, "t": tenant_id, "d": device, "b": uuid.uuid4(), "co": COMPANY},
        )
        c.execute(
            text(
                "INSERT INTO ops.tally_parties (id, tenant_id, company, ledger_name, group_name, "
                "closing_balance, as_of, last_sync_id) VALUES (:i, :t, :co, :n, "
                "'Sundry Debtors', 900.00, '2026-09-28', :s)"
            ),
            {"i": party, "t": tenant_id, "co": COMPANY, "n": ledger, "s": sync},
        )
    return party


def seed_link(
    admin: Engine,
    tenant_id: uuid.UUID,
    party_id: uuid.UUID,
    student_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO ops.tally_party_links (id, tenant_id, party_id, student_id, "
                "linked_by) VALUES (:i, :t, :p, :s, :u) "
                "ON CONFLICT ON CONSTRAINT tally_party_links_pair_key DO NOTHING"
            ),
            {"i": uuid.uuid4(), "t": tenant_id, "p": party_id, "s": student_id, "u": user_id},
        )


def seed_objects(admin: Engine, tenant_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """An active agent and a synced ledger of a school (cross-school tests)."""
    party = seed_party(admin, tenant_id, user_id)
    return {"device": seed_device(admin, tenant_id, user_id), "party": party}
