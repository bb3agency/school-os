"""Signatures for daily audit archives (FR-AUD-004, SEC-007).

- ``KmsSigner``: AWS KMS asymmetric key (ECC_NIST_P256), ``ECDSA_SHA_256`` over the SHA-256
  digest of the message. The private key never leaves KMS. Staging/production.
- ``LocalDevSigner``: Ed25519 key derived from ``SOS_LOCAL_DEV_MASTER_KEY``; refuses to run in
  staging/production. Local and CI only.

``verify_signature`` checks either kind with the public key (used by tests and the ops runbook).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final, Protocol

import boto3
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import Settings

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.types import PublicKeyTypes
    from mypy_boto3_kms import KMSClient

ALG_KMS_ECDSA: Final = "ECDSA_SHA_256"
ALG_ED25519 = "Ed25519"
_LOCAL_DEV_INFO = b"schoolos/audit-archive-signing/v1"


class Signer(Protocol):
    """Signs archive manifests. ``algorithm`` and ``key_id`` are written next to signatures."""

    @property
    def algorithm(self) -> str: ...

    @property
    def key_id(self) -> str: ...

    def sign(self, message: bytes) -> bytes: ...


class SignerRefused(RuntimeError):
    """The signer is not allowed in this environment or is misconfigured."""


class KmsSigner:
    """AWS KMS asymmetric signing (the key ARN comes from the caller, e.g. deployment config)."""

    algorithm = ALG_KMS_ECDSA

    def __init__(
        self, key_arn: str, *, client: KMSClient | None = None, region: str | None = None
    ) -> None:
        if not key_arn:
            raise SignerRefused("KmsSigner needs a KMS key ARN")
        self._key_arn = key_arn
        if client is None:
            client = boto3.client("kms", region_name=region)
        self._client: KMSClient = client

    @property
    def key_id(self) -> str:
        return self._key_arn

    def sign(self, message: bytes) -> bytes:
        digest = hashlib.sha256(message).digest()
        response = self._client.sign(
            KeyId=self._key_arn,
            Message=digest,
            MessageType="DIGEST",
            SigningAlgorithm=ALG_KMS_ECDSA,
        )
        return response["Signature"]

    def public_key(self) -> PublicKeyTypes:
        """The signing key's public half (``kms:GetPublicKey``), to check archived manifests."""
        der = self._client.get_public_key(KeyId=self._key_arn)["PublicKey"]
        return serialization.load_der_public_key(der)


class LocalDevSigner:
    """Ed25519 signer for local/CI only; the key is derived from the local dev master key."""

    algorithm = ALG_ED25519

    def __init__(self, settings: Settings) -> None:
        if settings.is_production_like:
            raise SignerRefused("LocalDevSigner is not allowed in staging/production; use KMS")
        if settings.local_dev_master_key is None:
            raise SignerRefused("SOS_LOCAL_DEV_MASTER_KEY is required for LocalDevSigner")
        seed = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_LOCAL_DEV_INFO).derive(
            settings.local_dev_master_key.get_secret_value().encode("utf-8")
        )
        self._key = Ed25519PrivateKey.from_private_bytes(seed)
        raw = self._key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self._key_id = "local-dev:" + hashlib.sha256(raw).hexdigest()[:16]

    @property
    def key_id(self) -> str:
        return self._key_id

    def public_key(self) -> Ed25519PublicKey:
        return self._key.public_key()

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message)


def verify_signature(
    algorithm: str, public_key: PublicKeyTypes, message: bytes, signature: bytes
) -> bool:
    """True when ``signature`` is valid for ``message`` under ``public_key``."""
    try:
        if algorithm == ALG_ED25519 and isinstance(public_key, Ed25519PublicKey):
            public_key.verify(signature, message)
            return True
        if algorithm == ALG_KMS_ECDSA and isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
            return True
    except InvalidSignature:
        return False
    return False
