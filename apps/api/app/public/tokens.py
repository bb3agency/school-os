"""Public keys: generation, shape checks and hashing (ADR-0046 decision 3, ADR-0047 decision 1;
FR-PUB-005, SEC-037).

Pure module. A parent link token is ``parent_token_bytes`` (32) bytes from the OS CSPRNG
(:func:`secrets.token_bytes`) written base64url without padding: 43 characters, no structure, no
school or student identifier, no expiry inside it. A Razorpay route value is
``route_token_length`` (24) characters of ``[A-Za-z0-9]`` from the same source. Only
``SHA-256(key)`` is ever stored or compared (:func:`key_hash`); the raw key exists in memory
while it is issued or looked up and is never logged.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import string
from typing import Final

from app.public.config import HandleKind, is_parent_kind, rules

_ROUTE_ALPHABET: Final = string.ascii_letters + string.digits
# Shapes a lookup must have before it reaches the database; anything else is a failed lookup.
_CERT_CODE: Final = re.compile(r"^[A-Z0-9]{8,32}$")
_WABA_ID: Final = re.compile(r"^[0-9]{1,32}$")


def _b64url_length(n_bytes: int) -> int:
    return (4 * n_bytes + 2) // 3


def new_parent_token() -> str:
    """A fresh parent link token (43 base64url characters for 32 bytes)."""
    raw = secrets.token_bytes(rules().parent_token_bytes)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_route_token() -> str:
    """A fresh Razorpay webhook route value (24 letters and digits)."""
    length = rules().route_token_length
    return "".join(secrets.choice(_ROUTE_ALPHABET) for _ in range(length))


def is_well_formed(kind: HandleKind, key: str) -> bool:
    """Whether ``key`` has the shape of a key of ``kind`` (checked before any lookup)."""
    if not isinstance(key, str) or not key.isascii():
        return False
    if is_parent_kind(kind):
        length = _b64url_length(rules().parent_token_bytes)
        return len(key) == length and re.fullmatch(r"[A-Za-z0-9_-]+", key) is not None
    if kind == "razorpay_route":
        return len(key) == rules().route_token_length and key.isalnum()
    if kind == "cert_verify":
        return _CERT_CODE.fullmatch(key) is not None
    return _WABA_ID.fullmatch(key) is not None  # meta_waba


def key_hash(key: str) -> bytes:
    """``SHA-256(key)``: the only form of a key that is stored or sent to the resolver."""
    return hashlib.sha256(key.encode("ascii")).digest()


__all__ = ["is_well_formed", "key_hash", "new_parent_token", "new_route_token"]
