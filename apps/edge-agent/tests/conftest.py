"""Fakes for the edge agent tests: Tally's XML server and the SchoolOS agent API (synthetic data).

- :class:`FakeTally` answers export envelopes from ``fixtures/`` by collection type (and the
  ``CHILDOF`` group for ledgers), and records every body it received.
- :class:`FakeServer` plays ``/api/v1/edge/tally/*``: it checks every signed request with its OWN
  HMAC code (so a drift in the agent's signing fails here), records the JSON bodies and answers
  like the API (config, catalog, idempotent syncs by ``batch_id``, key rotation).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from defusedxml import ElementTree as SafeET  # type: ignore[import-untyped]

from sos_edge_agent.client import HttpResponse

FIXTURES = Path(__file__).with_name("fixtures")
TENANT = uuid.UUID("01920000-0000-7000-8000-00000000a001")
DEVICE = uuid.UUID("01920000-0000-7000-8000-00000000d001")
SECRET = bytes(range(32))
NEW_SECRET = bytes(range(32, 64))


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeTally:
    def __init__(self, *, down: bool = False) -> None:
        self.bodies: list[bytes] = []
        self.down = down

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> HttpResponse:
        from sos_edge_agent.client import TransientError

        if self.down:
            raise TransientError("network error")
        assert url == "http://127.0.0.1:9000"
        assert body is not None
        self.bodies.append(body)
        root = SafeET.fromstring(body)
        kind = root.findtext(".//COLLECTION/TYPE")
        if kind == "Company":
            return HttpResponse(200, {}, fixture("companies.xml"))
        if kind == "Group":
            return HttpResponse(200, {}, fixture("groups.xml"))
        group = (root.findtext(".//COLLECTION/CHILDOF") or "").lower().replace(" ", "_")
        path = FIXTURES / f"ledgers_{group}.xml"
        data = path.read_bytes() if path.is_file() else b"<ENVELOPE><BODY/></ENVELOPE>"
        return HttpResponse(200, {}, data)


def _sign(secret: bytes, method: str, path: str, *, ts: str, nonce: str, body: bytes) -> str:
    text = "\n".join(
        ["SOS-EDGE-HMAC-SHA256", method, path, ts, nonce, hashlib.sha256(body).hexdigest()]
    )
    return "v1=" + hmac.new(secret, text.encode(), hashlib.sha256).hexdigest()


class FakeServer:
    def __init__(
        self,
        *,
        groups: list[str] | None = None,
        company: str | None = "Synthetic Model School 2026-27",
        min_version: str = "0.1.0",
    ) -> None:
        self.keys = {"tdk-abcdefghijklmnopqrst": SECRET}
        self.groups = groups if groups is not None else ["Sundry Debtors", "Class IX Fees"]
        self.company = company
        self.min_version = min_version
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.syncs: dict[str, dict[str, Any]] = {}
        self.status_override: dict[str, int] = {}

    def _json(
        self, status: int, data: Any, headers: Mapping[str, str] | None = None
    ) -> HttpResponse:
        return HttpResponse(status, dict(headers or {}), json.dumps(data).encode())

    def request(  # noqa: PLR0911 - one answer per fake route
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> HttpResponse:
        path = url.removeprefix("https://school.synthetic.test")
        payload = json.loads(body) if body else None
        self.calls.append((method, path, payload))
        route = path.removeprefix("/api/v1/edge/tally")
        if route in self.status_override:
            code = self.status_override[route]
            return self._json(code, {"code": "x"}, {"Retry-After": "120"} if code == 429 else None)
        if route == "/enrol":
            assert headers["X-SOS-Tenant"] == str(TENANT)
            if payload is None or payload["code"] != "ABCD-EFGH-JKLM":
                return self._json(401, {"code": "unauthenticated"})
            return self._json(
                201,
                {
                    "device_id": str(DEVICE),
                    "key_id": "tdk-abcdefghijklmnopqrst",
                    "secret": base64.urlsafe_b64encode(SECRET).decode().rstrip("="),
                    "config": {},
                },
            )
        secret = self.keys.get(headers.get("X-SOS-Key-Id", ""))
        expected = (
            _sign(
                secret,
                method,
                path,
                ts=headers["X-SOS-Timestamp"],
                nonce=headers["X-SOS-Nonce"],
                body=body or b"",
            )
            if secret
            else None
        )
        if expected is None or not hmac.compare_digest(expected, headers["X-SOS-Signature"]):
            return self._json(401, {"code": "unauthenticated"})
        if route == "/config":
            return self._json(
                200,
                {
                    "company": self.company,
                    "groups": self.groups,
                    "sync_interval_minutes": 30,
                    "max_parties": 5000,
                    "min_agent_version": self.min_version,
                    "rotate_after_days": 90,
                    "server_time": "2026-09-29T10:00:00Z",
                },
            )
        if route == "/catalog":
            assert payload is not None
            return self._json(200, {"groups": len(payload["groups"]), "selected": 2})
        if route == "/syncs":
            assert payload is not None
            batch = payload["batch_id"]
            repeat = batch in self.syncs
            self.syncs.setdefault(batch, payload)
            return self._json(200, {"batch_id": batch, "repeat": repeat, "parties": 0})
        if route == "/key-rotation":
            self.keys["tdk-zyxwvutsrqponmlkjihg"] = NEW_SECRET
            return self._json(
                200,
                {
                    "key_id": "tdk-zyxwvutsrqponmlkjihg",
                    "secret": base64.urlsafe_b64encode(NEW_SECRET).decode().rstrip("="),
                },
            )
        return self._json(404, {"code": "not_found"})


@pytest.fixture
def tally() -> FakeTally:
    return FakeTally()


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()
