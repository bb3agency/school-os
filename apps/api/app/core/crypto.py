"""Envelope encryption primitives (docs/05 §9, 07 §8, FR-TEN-003, SEC-012).

- Each tenant has a random 256-bit data encryption key (DEK) and a separate 256-bit HMAC key for
  blind indexes. Both are stored only *wrapped* in ``core.tenant_keys``.
- Wrapping uses a :class:`KeyWrapper`: AWS KMS in every deployed environment
  (``EncryptionContext = {"tenant_id": ...}``), or a local-dev AES-256-GCM wrapper that refuses to
  run in staging/prod.
- Field ciphertext format (fixed now, used from M1):
  ``version(1) | key_version(2, big-endian) | nonce(12) | ciphertext | tag(16)``.
  The GCM associated data is ``header || aad`` where ``aad = tenant_id|table|column|row_id``
  (:func:`field_aad`), so a ciphertext cannot be moved to another row, column or tenant, and its
  header cannot be edited.

Never log keys, plaintext or ciphertext.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import KeyWrapperKind, Settings

if TYPE_CHECKING:
    from mypy_boto3_kms import KMSClient

KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
CIPHERTEXT_VERSION = 1
MAX_KEY_VERSION = 0xFFFF
_HEADER_BYTES = 1 + 2
_LOCAL_WRAP_VERSION = 1
_MIN_MASTER_KEY_BYTES = 32


class CryptoError(Exception):
    """Decryption/unwrapping failed (wrong key, wrong context, tampering or bad format).

    The message never contains key material or plaintext.
    """


@runtime_checkable
class KeyWrapper(Protocol):
    """Wraps/unwraps per-tenant key material, bound to the tenant id."""

    @property
    def key_id(self) -> str:
        """Identifier of the wrapping key (stored in ``core.tenant_keys.kms_key_arn``)."""
        ...

    def wrap(self, dek: bytes, *, tenant_id: uuid.UUID) -> bytes: ...

    def unwrap(self, wrapped: bytes, *, tenant_id: uuid.UUID) -> bytes: ...


def _tenant_context(tenant_id: uuid.UUID) -> dict[str, str]:
    return {"tenant_id": str(tenant_id)}


class KmsKeyWrapper:
    """AWS KMS wrapper: ``Encrypt``/``Decrypt`` with ``EncryptionContext={"tenant_id": ...}``.

    The encryption context is logged by CloudTrail (tenant id only; never personal data) and must
    match on decrypt, binding every wrapped key to its tenant.
    """

    def __init__(self, client: KMSClient, key_arn: str) -> None:
        if not key_arn:
            raise ValueError("a KMS key ARN is required")
        self._client = client
        self._key_arn = key_arn

    @property
    def key_id(self) -> str:
        return self._key_arn

    def wrap(self, dek: bytes, *, tenant_id: uuid.UUID) -> bytes:
        try:
            response = self._client.encrypt(
                KeyId=self._key_arn, Plaintext=dek, EncryptionContext=_tenant_context(tenant_id)
            )
        except (ClientError, BotoCoreError) as exc:
            raise CryptoError("KMS encrypt failed") from exc
        return response["CiphertextBlob"]

    def unwrap(self, wrapped: bytes, *, tenant_id: uuid.UUID) -> bytes:
        try:
            response = self._client.decrypt(
                CiphertextBlob=wrapped,
                KeyId=self._key_arn,
                EncryptionContext=_tenant_context(tenant_id),
            )
        except (ClientError, BotoCoreError) as exc:
            raise CryptoError("KMS decrypt failed") from exc
        return response["Plaintext"]


class LocalDevKeyWrapper:
    """AES-256-GCM wrapper for local development and CI only.

    The key-encryption key is derived with HKDF-SHA256 from ``SOS_LOCAL_DEV_MASTER_KEY``; the
    tenant id is the associated data. Wrapped format: ``0x01 | nonce(12) | ciphertext | tag``.
    Construction fails in staging/prod even if settings validation was bypassed.
    """

    def __init__(self, settings: Settings) -> None:
        if settings.is_production_like:
            raise RuntimeError("the local-dev key wrapper is not allowed in staging/prod")
        if settings.local_dev_master_key is None:
            raise ValueError("SOS_LOCAL_DEV_MASTER_KEY is required for the local-dev key wrapper")
        master = settings.local_dev_master_key.get_secret_value().encode("utf-8")
        if len(master) < _MIN_MASTER_KEY_BYTES:
            raise ValueError(
                f"SOS_LOCAL_DEV_MASTER_KEY must be at least {_MIN_MASTER_KEY_BYTES} bytes"
            )
        self._aead = AESGCM(_hkdf(master, b"schoolos/local-dev/tenant-key-wrap/v1"))
        fingerprint = _hkdf(master, b"schoolos/local-dev/key-id/v1")[:8].hex()
        self._key_id = f"local-dev:{fingerprint}"

    @property
    def key_id(self) -> str:
        return self._key_id

    def wrap(self, dek: bytes, *, tenant_id: uuid.UUID) -> bytes:
        nonce = secrets.token_bytes(NONCE_BYTES)
        sealed = self._aead.encrypt(nonce, dek, str(tenant_id).encode("ascii"))
        return bytes([_LOCAL_WRAP_VERSION]) + nonce + sealed

    def unwrap(self, wrapped: bytes, *, tenant_id: uuid.UUID) -> bytes:
        if len(wrapped) < 1 + NONCE_BYTES + TAG_BYTES or wrapped[0] != _LOCAL_WRAP_VERSION:
            raise CryptoError("wrapped key has an unknown format")
        nonce, sealed = wrapped[1 : 1 + NONCE_BYTES], wrapped[1 + NONCE_BYTES :]
        try:
            return self._aead.decrypt(nonce, sealed, str(tenant_id).encode("ascii"))
        except InvalidTag as exc:
            raise CryptoError("wrapped key failed authentication") from exc


def _hkdf(master: bytes, info: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=hashlib.sha256(b"schoolos-local-dev-salt").digest(),
        info=info,
    ).derive(master)


def get_key_wrapper(settings: Settings) -> KeyWrapper:
    """Return the configured wrapper (``SOS_KEY_WRAPPER``)."""
    if settings.key_wrapper is KeyWrapperKind.KMS:
        if not settings.kms_data_key_arn:
            raise ValueError("SOS_KMS_DATA_KEY_ARN is required when SOS_KEY_WRAPPER=kms")
        client: KMSClient = boto3.client("kms", region_name=settings.aws_region)
        return KmsKeyWrapper(client, settings.kms_data_key_arn)
    return LocalDevKeyWrapper(settings)


def generate_tenant_keys(tenant_id: uuid.UUID, wrapper: KeyWrapper) -> tuple[bytes, bytes]:
    """Generate a random DEK and HMAC key for ``tenant_id``; return them wrapped.

    The plaintext keys exist only in this function's frame.
    """
    dek = secrets.token_bytes(KEY_BYTES)
    hmac_key = secrets.token_bytes(KEY_BYTES)
    return wrapper.wrap(dek, tenant_id=tenant_id), wrapper.wrap(hmac_key, tenant_id=tenant_id)


# --- field ciphertext format (docs/05 §9) ---------------------------------------------------


def field_aad(tenant_id: uuid.UUID, table: str, column: str, row_id: uuid.UUID) -> bytes:
    """Associated data ``tenant_id|table|column|row_id`` binding a value to its cell."""
    for part in (table, column):
        if not part or "|" in part:
            raise ValueError("table and column must be non-empty and must not contain '|'")
    return f"{tenant_id}|{table}|{column}|{row_id}".encode()


def _header(key_version: int) -> bytes:
    if not 1 <= key_version <= MAX_KEY_VERSION:
        raise ValueError(f"key_version must be between 1 and {MAX_KEY_VERSION}")
    return bytes([CIPHERTEXT_VERSION]) + key_version.to_bytes(2, "big")


def aead_encrypt(dek: bytes, plaintext: bytes, aad: bytes, *, key_version: int = 1) -> bytes:
    """Encrypt with AES-256-GCM into the fixed ciphertext format."""
    if len(dek) != KEY_BYTES:
        raise ValueError("the data encryption key must be 32 bytes")
    header = _header(key_version)
    nonce = secrets.token_bytes(NONCE_BYTES)
    return header + nonce + AESGCM(dek).encrypt(nonce, plaintext, header + aad)


def ciphertext_key_version(blob: bytes) -> int:
    """Read the key version from a ciphertext header (to pick the DEK before decrypting)."""
    if len(blob) < _HEADER_BYTES + NONCE_BYTES + TAG_BYTES:
        raise CryptoError("ciphertext is too short")
    if blob[0] != CIPHERTEXT_VERSION:
        raise CryptoError("unknown ciphertext format version")
    return int.from_bytes(blob[1:_HEADER_BYTES], "big")


def aead_decrypt(dek: bytes, blob: bytes, aad: bytes) -> bytes:
    """Decrypt a value produced by :func:`aead_encrypt`; raise :class:`CryptoError` on failure."""
    ciphertext_key_version(blob)
    if len(dek) != KEY_BYTES:
        raise CryptoError("the data encryption key must be 32 bytes")
    header = blob[:_HEADER_BYTES]
    nonce = blob[_HEADER_BYTES : _HEADER_BYTES + NONCE_BYTES]
    sealed = blob[_HEADER_BYTES + NONCE_BYTES :]
    try:
        return AESGCM(dek).decrypt(nonce, sealed, header + aad)
    except InvalidTag as exc:
        raise CryptoError("ciphertext failed authentication") from exc
