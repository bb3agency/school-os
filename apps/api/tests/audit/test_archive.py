"""FR-AUD-004: signed daily archive of audit events to S3 (SEC-007).

S3 is a fake in-memory client or a real boto3 client under ``botocore.stub.Stubber`` (which
validates request parameters against the S3 API model). No network access.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import boto3
import pytest
import rfc8785
from botocore.stub import ANY, Stubber
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed
from pydantic import SecretStr
from sqlalchemy import text

from app.audit.archive import (
    ArchiveChainError,
    ArchiveConflictError,
    export_day,
    object_key,
)
from app.audit.signing import (
    ALG_ED25519,
    ALG_KMS_ECDSA,
    KmsSigner,
    LocalDevSigner,
    SignerRefused,
    verify_signature,
)
from app.core.config import Environment, KeyWrapperKind, Settings

BUCKET = "sos-test-audit-archive"


def _dev_settings(key: str = "test-only-master-key") -> Settings:
    return Settings(env=Environment.CI, local_dev_master_key=SecretStr(key))


def _prod_settings() -> Settings:
    return Settings(
        env=Environment.PROD,
        key_wrapper=KeyWrapperKind.KMS,
        database_url=SecretStr("postgresql+psycopg://sos_app:x@db.internal/schoolos"),
        platform_database_url=SecretStr("postgresql+psycopg://sos_platform:x@db.internal/schoolos"),
        service_token_key=SecretStr("k" * 48),
        local_dev_master_key=SecretStr("should-never-be-used"),
    )


def _day_of(event: Any) -> date:
    occurred: datetime = event.occurred_at
    return occurred.astimezone(UTC).date()


# ---- signing ----


def test_FR_AUD_004_local_dev_signer_refuses_production() -> None:
    with pytest.raises(SignerRefused, match="not allowed"):
        LocalDevSigner(_prod_settings())


def test_FR_AUD_004_local_dev_signer_requires_key() -> None:
    with pytest.raises(SignerRefused, match="MASTER_KEY"):
        LocalDevSigner(Settings(env=Environment.CI, local_dev_master_key=None))


def test_FR_AUD_004_local_dev_signer_is_deterministic_and_verifiable() -> None:
    a, b = LocalDevSigner(_dev_settings()), LocalDevSigner(_dev_settings())
    other = LocalDevSigner(_dev_settings("another-test-key"))
    assert a.key_id == b.key_id != other.key_id
    sig = a.sign(b"manifest")
    assert sig == b.sign(b"manifest")
    assert verify_signature(ALG_ED25519, a.public_key(), b"manifest", sig)
    assert not verify_signature(ALG_ED25519, a.public_key(), b"manifest!", sig)
    assert not verify_signature(ALG_ED25519, other.public_key(), b"manifest", sig)


def test_FR_AUD_004_kms_signer_signs_digest_with_ecdsa() -> None:
    private = ec.generate_private_key(ec.SECP256R1())
    message = b'{"manifest":1}'
    digest = hashlib.sha256(message).digest()
    fake_sig = private.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    arn = "arn:aws:kms:ap-south-1:000000000000:key/00000000-0000-0000-0000-000000000000"
    client = boto3.client(
        "kms", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="y"
    )
    with Stubber(client) as stub:
        stub.add_response(
            "sign",
            {"KeyId": arn, "Signature": fake_sig, "SigningAlgorithm": ALG_KMS_ECDSA},
            {
                "KeyId": arn,
                "Message": digest,
                "MessageType": "DIGEST",
                "SigningAlgorithm": ALG_KMS_ECDSA,
            },
        )
        signer = KmsSigner(arn, client=client)
        signature = signer.sign(message)
        stub.assert_no_pending_responses()
    assert signer.key_id == arn
    assert verify_signature(ALG_KMS_ECDSA, private.public_key(), message, signature)
    assert not verify_signature(ALG_KMS_ECDSA, private.public_key(), b"other", signature)


def test_FR_AUD_004_kms_signer_needs_arn() -> None:
    with pytest.raises(SignerRefused):
        KmsSigner("", client=object())  # type: ignore[arg-type]


# ---- export ----


@pytest.mark.db
def test_FR_AUD_004_export_writes_verifiable_signed_archive(
    tenant: uuid.UUID, record_events: Any, fake_s3: Any
) -> None:
    events = record_events(tenant, 4)
    day = _day_of(events[0])
    signer = LocalDevSigner(_dev_settings())
    result = export_day(tenant, day, s3=fake_s3, signer=signer, bucket=BUCKET)

    assert result.status == "written"
    assert result.key == object_key(tenant, day)
    assert result.key.startswith(f"t/{tenant}/{day:%Y/%m/%d}/audit-{day.isoformat()}")
    body = fake_s3.body(BUCKET, result.key)
    assert hashlib.sha256(body).hexdigest() == result.sha256
    lines = gzip.decompress(body).decode("utf-8").splitlines()
    assert len(lines) == len(events) == result.event_count

    prev = bytes(32)
    for line, event in zip(lines, events, strict=True):
        obj = json.loads(line)
        assert rfc8785.dumps(obj) == line.encode("utf-8"), "each line is canonical JSON"
        stored_prev, stored_hash = (
            bytes.fromhex(obj.pop("prev_hash")),
            bytes.fromhex(obj.pop("hash")),
        )
        assert stored_prev == prev
        assert hashlib.sha256(prev + rfc8785.dumps(obj)).digest() == stored_hash == event.hash
        prev = stored_hash

    sig_doc = json.loads(fake_s3.body(BUCKET, result.sig_key))
    manifest = sig_doc["manifest"]
    assert manifest["sha256"] == result.sha256
    assert (manifest["first_seq"], manifest["last_seq"]) == (1, 4)
    assert manifest["first_prev_hash"] == "00" * 32
    assert manifest["last_hash"] == events[-1].hash.hex()
    assert manifest["key_id"] == signer.key_id
    signature = base64.b64decode(sig_doc["signature"])
    assert verify_signature(ALG_ED25519, signer.public_key(), rfc8785.dumps(manifest), signature)


@pytest.mark.db
def test_FR_AUD_004_export_is_deterministic_and_idempotent(
    tenant: uuid.UUID, record_events: Any, fake_s3: Any
) -> None:
    events = record_events(tenant, 2)
    day = _day_of(events[0])
    signer = LocalDevSigner(_dev_settings())
    first = export_day(tenant, day, s3=fake_s3, signer=signer, bucket=BUCKET)
    puts = len(fake_s3.puts)
    again = export_day(tenant, day, s3=fake_s3, signer=signer, bucket=BUCKET)
    assert again.status == "skipped"
    assert again.sha256 == first.sha256
    assert len(fake_s3.puts) == puts, "no rewrite of an identical archive"

    other = type(fake_s3)()
    export_day(tenant, day, s3=other, signer=signer, bucket=BUCKET)
    assert other.body(BUCKET, first.key) == fake_s3.body(BUCKET, first.key)
    assert other.body(BUCKET, first.sig_key) == fake_s3.body(BUCKET, first.sig_key)


@pytest.mark.db
def test_FR_AUD_004_export_conflict_is_an_error(
    tenant: uuid.UUID, record_events: Any, fake_s3: Any
) -> None:
    events = record_events(tenant, 1)
    day = _day_of(events[0])
    fake_s3.put_object(
        Bucket=BUCKET, Key=object_key(tenant, day), Body=b"x", Metadata={"sha256": "0"}
    )
    with pytest.raises(ArchiveConflictError):
        export_day(tenant, day, s3=fake_s3, signer=LocalDevSigner(_dev_settings()), bucket=BUCKET)


@pytest.mark.db
def test_FR_AUD_004_empty_day_writes_nothing(
    tenant: uuid.UUID, fake_s3: Any, app_engine: Any
) -> None:
    result = export_day(
        tenant, date(2026, 9, 1), s3=fake_s3, signer=LocalDevSigner(_dev_settings()), bucket=BUCKET
    )
    assert result.status == "empty"
    assert fake_s3.puts == []


@pytest.mark.db
def test_FR_AUD_004_export_refuses_tampered_events(
    tenant: uuid.UUID, record_events: Any, fake_s3: Any, tamper: Any
) -> None:
    events = record_events(tenant, 2)
    with tamper() as c:
        c.execute(
            text("UPDATE audit.events SET summary = '{}' WHERE tenant_id = :t AND seq = 2"),
            {"t": tenant},
        )
    with pytest.raises(ArchiveChainError):
        export_day(
            tenant,
            _day_of(events[0]),
            s3=fake_s3,
            signer=LocalDevSigner(_dev_settings()),
            bucket=BUCKET,
        )
    assert fake_s3.puts == []


@pytest.mark.db
def test_FR_AUD_004_object_lock_request_shape(tenant: uuid.UUID, record_events: Any) -> None:
    """Real boto3 S3 client + Stubber: parameters must be valid for the S3 API model."""
    events = record_events(tenant, 1)
    day = _day_of(events[0])
    key = object_key(tenant, day)
    now = datetime(2026, 9, 27, 20, 30, tzinfo=UTC)
    retain = now + timedelta(days=1096)
    client = boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="x", aws_secret_access_key="y"
    )
    put_params = {
        "Bucket": BUCKET,
        "Body": ANY,
        "ContentType": ANY,
        "Metadata": ANY,
        "ObjectLockMode": "COMPLIANCE",
        "ObjectLockRetainUntilDate": retain,
        "ChecksumAlgorithm": "SHA256",
    }
    with Stubber(client) as stub:
        stub.add_client_error(
            "head_object",
            "404",
            http_status_code=404,
            expected_params={"Bucket": BUCKET, "Key": key},
        )
        stub.add_response("put_object", {"ETag": '"a"'}, {**put_params, "Key": key})
        stub.add_client_error(
            "head_object",
            "404",
            http_status_code=404,
            expected_params={"Bucket": BUCKET, "Key": key + ".sig"},
        )
        stub.add_response("put_object", {"ETag": '"b"'}, {**put_params, "Key": key + ".sig"})
        result = export_day(
            tenant,
            day,
            s3=client,
            signer=LocalDevSigner(_dev_settings()),
            bucket=BUCKET,
            object_lock_retention_days=1096,
            now=now,
        )
        stub.assert_no_pending_responses()
    assert result.status == "written"
