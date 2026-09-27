"""Per-tenant field encryption for C3 student data (SEC-012, FR-STU-007, docs/05 §9, 07 §8).

- :class:`TenantKeyring` reads the tenant's wrapped keys from ``core.tenant_keys`` inside the
  caller's ``tenant_session`` (RLS: only the current school's rows), unwraps them with the
  configured :class:`app.core.crypto.KeyWrapper` (KMS in deployed environments) and caches the
  unwrapped DEK + HMAC key in process memory for at most 15 minutes per
  ``(tenant_id, key_version)``.
- :func:`encrypt_value` / :func:`decrypt_value` use the fixed ciphertext format of
  ``app.core.crypto`` with associated data ``tenant_id|table|column|row_id``: a ciphertext copied
  to another row, column or school fails authentication.
- :func:`blind_index` is HMAC-SHA256 with the tenant HMAC key over a normalised value, for
  equality lookups only (guardian phone).

Nothing here logs keys, plaintext or ciphertext. ``core.tenant_keys`` is read with a plain
statement (no import of the tenancy models); see the report for the requested
``tenancy.service`` accessor.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import (
    KEY_CACHE_MAX_S,
    CryptoError,
    KeyWrapper,
    aead_decrypt,
    aead_encrypt,
    ciphertext_key_version,
    field_aad,
    get_key_wrapper,
    reencrypt,
)

__all__ = [
    "CACHE_TTL_S",
    "CryptoError",
    "KeyMaterialMissing",
    "TenantKeyring",
    "blind_index",
    "decrypt_value",
    "encrypt_value",
    "get_keyring",
    "reencrypt_value",
    "set_key_wrapper",
]

CACHE_TTL_S: Final = KEY_CACHE_MAX_S
_BLIND_INDEX_CONTEXT: Final = b"schoolos/blind-index/v1|"

_KEYS_SQL = text(
    "SELECT key_version, wrapped_dek, wrapped_hmac, retired_at IS NULL AS active "
    "FROM core.tenant_keys WHERE tenant_id = core.current_tenant() ORDER BY key_version"
)


class KeyMaterialMissing(CryptoError):
    """The school has no usable data encryption key (never provisioned or crypto-shredded)."""


@dataclass(frozen=True, slots=True)
class _Keys:
    dek: bytes
    hmac_key: bytes
    loaded_at: float


def _current_tenant(session: Session) -> uuid.UUID:
    value: object = session.execute(text("SELECT core.current_tenant()")).scalar_one()
    if value is None:
        raise RuntimeError("tenant context is not set; use core.db.tenant_session()")
    return uuid.UUID(str(value))


class TenantKeyring:
    """Unwrapped tenant keys, cached in memory for at most :data:`CACHE_TTL_S` seconds."""

    def __init__(
        self,
        wrapper: KeyWrapper | None = None,
        *,
        ttl_s: float = CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 0 < ttl_s <= CACHE_TTL_S:
            raise ValueError("the key cache TTL must be positive and at most 15 minutes")
        self._wrapper = wrapper
        self._ttl = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._cache: dict[tuple[uuid.UUID, int], _Keys] = {}
        self._active: dict[uuid.UUID, tuple[int, float]] = {}
        self.unwrap_count = 0  # observable in tests (cache hits vs KMS calls)

    @property
    def wrapper(self) -> KeyWrapper:
        if self._wrapper is None:
            self._wrapper = get_key_wrapper(get_settings())
        return self._wrapper

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._active.clear()

    def forget(self, tenant_id: uuid.UUID) -> None:
        """Drop one school's cached keys (after its key versions changed in this process)."""
        with self._lock:
            for cached in [k for k in self._cache if k[0] == tenant_id]:
                del self._cache[cached]
            self._active.pop(tenant_id, None)

    def _fresh(self, loaded_at: float) -> bool:
        return self._clock() - loaded_at < self._ttl

    def _load(self, session: Session, tenant_id: uuid.UUID) -> None:
        rows = session.execute(_KEYS_SQL).all()
        now = self._clock()
        active: int | None = None
        loaded: dict[tuple[uuid.UUID, int], _Keys] = {}
        for row in rows:
            version = int(row.key_version)
            dek = self.wrapper.unwrap(bytes(row.wrapped_dek), tenant_id=tenant_id)
            mac = self.wrapper.unwrap(bytes(row.wrapped_hmac), tenant_id=tenant_id)
            self.unwrap_count += 1
            loaded[(tenant_id, version)] = _Keys(dek, mac, now)
            if row.active:
                active = version if active is None else max(active, version)
        with self._lock:
            self._cache.update(loaded)
            if active is not None:
                self._active[tenant_id] = (active, now)
            else:
                self._active.pop(tenant_id, None)

    def active_version(self, session: Session) -> int:
        """The newest non-retired key version of the current tenant (used for new ciphertext)."""
        tenant_id = _current_tenant(session)
        with self._lock:
            hit = self._active.get(tenant_id)
        if hit is None or not self._fresh(hit[1]):
            self._load(session, tenant_id)
            with self._lock:
                hit = self._active.get(tenant_id)
        if hit is None:
            raise KeyMaterialMissing("the school has no active data encryption key")
        return hit[0]

    def _keys(self, session: Session, key_version: int) -> tuple[uuid.UUID, _Keys]:
        tenant_id = _current_tenant(session)
        with self._lock:
            keys = self._cache.get((tenant_id, key_version))
        if keys is None or not self._fresh(keys.loaded_at):
            self._load(session, tenant_id)
            with self._lock:
                keys = self._cache.get((tenant_id, key_version))
        if keys is None:
            raise KeyMaterialMissing("the data encryption key version is not available")
        return tenant_id, keys

    def dek(self, session: Session, key_version: int) -> tuple[uuid.UUID, bytes]:
        tenant_id, keys = self._keys(session, key_version)
        return tenant_id, keys.dek

    def hmac_key(self, session: Session, key_version: int) -> bytes:
        return self._keys(session, key_version)[1].hmac_key


_lock = threading.Lock()
_keyring: TenantKeyring | None = None


def get_keyring() -> TenantKeyring:
    """The process-wide keyring (wrapper from settings on first use)."""
    global _keyring  # noqa: PLW0603 - process-wide cache by design
    with _lock:
        if _keyring is None:
            _keyring = TenantKeyring()
        return _keyring


def set_key_wrapper(wrapper: KeyWrapper | None) -> TenantKeyring:
    """Replace the process-wide keyring (worker bootstrap, tests); clears the cache."""
    global _keyring  # noqa: PLW0603 - process-wide cache by design
    with _lock:
        _keyring = TenantKeyring(wrapper)
        return _keyring


def encrypt_value(
    session: Session,
    plaintext: str,
    *,
    table: str,
    column: str,
    row_id: uuid.UUID,
    keyring: TenantKeyring | None = None,
) -> tuple[bytes, int]:
    """Encrypt ``plaintext`` for one cell; return ``(ciphertext, key_version)``."""
    ring = keyring or get_keyring()
    version = ring.active_version(session)
    tenant_id, dek = ring.dek(session, version)
    aad = field_aad(tenant_id, table, column, row_id)
    return aead_encrypt(dek, plaintext.encode("utf-8"), aad, key_version=version), version


def decrypt_value(
    session: Session,
    blob: bytes,
    *,
    table: str,
    column: str,
    row_id: uuid.UUID,
    keyring: TenantKeyring | None = None,
) -> str:
    """Decrypt one cell; :class:`CryptoError` if it was tampered with or moved."""
    ring = keyring or get_keyring()
    tenant_id, dek = ring.dek(session, ciphertext_key_version(blob))
    aad = field_aad(tenant_id, table, column, row_id)
    return aead_decrypt(dek, blob, aad).decode("utf-8")


def reencrypt_value(
    session: Session,
    blob: bytes,
    *,
    table: str,
    column: str,
    row_id: uuid.UUID,
    key_version: int,
    keyring: TenantKeyring | None = None,
) -> bytes:
    """Re-encrypt one cell under ``key_version`` (key rotation, SEC-012); same AAD, same cell.

    :class:`CryptoError` if the stored value fails authentication (it is then left unchanged).
    """
    ring = keyring or get_keyring()
    tenant_id, old_dek = ring.dek(session, ciphertext_key_version(blob))
    _, new_dek = ring.dek(session, key_version)
    aad = field_aad(tenant_id, table, column, row_id)
    return reencrypt(old_dek, new_dek, blob, aad, key_version=key_version)


def blind_index(
    session: Session,
    normalised: str,
    *,
    purpose: str,
    key_version: int | None = None,
    keyring: TenantKeyring | None = None,
) -> tuple[bytes, int]:
    """HMAC-SHA256(tenant HMAC key, context | purpose | value); returns ``(digest, version)``.

    ``purpose`` (e.g. ``"guardian_phone"``) separates index domains so equal strings in
    different fields do not produce equal digests.
    """
    if not purpose or "|" in purpose:
        raise ValueError("purpose must be non-empty and must not contain '|'")
    ring = keyring or get_keyring()
    version = key_version if key_version is not None else ring.active_version(session)
    key = ring.hmac_key(session, version)
    message = _BLIND_INDEX_CONTEXT + purpose.encode("ascii") + b"|" + normalised.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).digest(), version
