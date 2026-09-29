"""Request signing (ADR-0032 §3). The vectors are ALSO pinned by the API
(``apps/api/tests/tally/test_agent_auth.py``): agent and server build the same canonical string.
"""

from __future__ import annotations

import uuid

import pytest

from sos_edge_agent import signing

SECRET = bytes(range(32))
TIMESTAMP = "1790000000"
NONCE = "nonce-0123456789abcdef"


@pytest.mark.parametrize(
    ("method", "path", "body", "expected"),
    [
        (
            "POST",
            "/api/v1/edge/tally/syncs",
            b'{"batch_id":"x"}',
            "v1=9d04c53f43df1df7bcf0e13794a2cde9105e51c739d538917d55cfa59403b8fd",
        ),
        (
            "GET",
            "/api/v1/edge/tally/config",
            b"",
            "v1=f701e185f20469cc0b9f65d4bceb6c68fc73e7c322ded938f475bc2c0b024dbd",
        ),
    ],
)
def test_ADR_0032_signing_vector_is_pinned(
    method: str, path: str, body: bytes, expected: str
) -> None:
    assert (
        signing.sign(SECRET, method=method, path=path, timestamp=TIMESTAMP, nonce=NONCE, body=body)
        == expected
    )


def test_ADR_0032_signed_headers_carry_everything_the_server_checks() -> None:
    tenant, device = uuid.uuid4(), uuid.uuid4()
    headers = signing.signed_headers(
        secret=SECRET,
        tenant_id=tenant,
        device_id=device,
        key_id="tdk-abcdefghijklmnopqrst",
        method="GET",
        path="/api/v1/edge/tally/config",
        body=b"",
        agent_version="0.1.0",
        now=1790000000.9,
        nonce=NONCE,
    )
    assert headers == {
        "X-SOS-Tenant": str(tenant),
        "X-SOS-Device": str(device),
        "X-SOS-Key-Id": "tdk-abcdefghijklmnopqrst",
        "X-SOS-Timestamp": TIMESTAMP,
        "X-SOS-Nonce": NONCE,
        "X-SOS-Agent-Version": "0.1.0",
        "X-SOS-Signature": "v1=f701e185f20469cc0b9f65d4bceb6c68fc73e7c322ded938f475bc2c0b024dbd",
    }
    nonces = {signing.new_nonce() for _ in range(50)}
    assert len(nonces) == 50
    assert all(16 <= len(n) <= 64 for n in nonces)
