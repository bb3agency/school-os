"""Object storage for documents (FR-DOC-003, FR-DOC-004, SEC-016; docs/04 §8.2, docs/07 §10).

Layout (private bucket, versioning on, tenant prefix first so lifecycle and deletion can work
per school)::

    t/<tenant_id>/docs/<document_id>/v<n>/original.<ext>
    t/<tenant_id>/docs/<document_id>/v<n>/derived/...        (M2: text layer, page renders)
    t/<tenant_id>/imports/<batch_id>/raw.<ext>

- Browsers upload with a presigned POST that pins the exact key, the exact Content-Type and a
  content-length-range, and expires in at most 10 minutes. With ``SOS_S3_KMS_KEY_ID`` set the
  policy also requires SSE-KMS with that key (the bucket default encrypts as well).
- Downloads are presigned GETs valid at most 5 minutes that force
  ``Content-Disposition: attachment`` and the verified content type. Nothing is ever public.

``ObjectStore`` is a Protocol so tests can swap in an in-memory store; the real implementation
is :class:`S3ObjectStore` (boto3; SeaweedFS locally and in CI via an endpoint override).
"""

from __future__ import annotations

import datetime as dt
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import ObjectIdentifierTypeDef

MAX_UPLOAD_URL_TTL_S: Final = 600
MAX_DOWNLOAD_URL_TTL_S: Final = 300
CHUNK_BYTES: Final = 1024 * 1024
SSE_KMS: Final = "aws:kms"


class ObjectStoreError(RuntimeError):
    """The object store failed or refused (never carries object contents)."""


class ObjectChanged(ObjectStoreError):
    """The source object no longer has the ETag that was verified (conditional copy failed)."""


@dataclass(frozen=True, slots=True)
class OpenedObject:
    """One GET of an object: its ETag and a stream of its bytes (close() releases it)."""

    etag: str
    chunks: Iterator[bytes]

    def close(self) -> None:
        close = getattr(self.chunks, "close", None)
        if callable(close):
            close()


@dataclass(frozen=True, slots=True)
class PresignedPost:
    url: str
    fields: dict[str, str]
    expires_at: dt.datetime


@dataclass(frozen=True, slots=True)
class ObjectHead:
    size: int
    content_type: str | None
    sse: str | None = None
    kms_key_id: str | None = None


def tenant_prefix(tenant_id: uuid.UUID) -> str:
    return f"t/{tenant_id}/"


def document_key(tenant_id: uuid.UUID, document_id: uuid.UUID, version_no: int, ext: str) -> str:
    return f"{tenant_prefix(tenant_id)}docs/{document_id}/v{version_no}/original.{ext}"


def document_prefix(tenant_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return f"{tenant_prefix(tenant_id)}docs/{document_id}/"


def derived_key(
    tenant_id: uuid.UUID, document_id: uuid.UUID, version_no: int, relative: str
) -> str:
    if not relative or relative.startswith("/") or ".." in relative.split("/"):
        raise ValueError("derived path must be relative without '..'")
    return f"{tenant_prefix(tenant_id)}docs/{document_id}/v{version_no}/derived/{relative}"


def import_key(tenant_id: uuid.UUID, batch_id: uuid.UUID, ext: str) -> str:
    return f"{tenant_prefix(tenant_id)}imports/{batch_id}/raw.{ext}"


def upload_key(tenant_id: uuid.UUID, intent_id: uuid.UUID, ext: str) -> str:
    """Staging key a presigned POST writes to. Verified bytes are then copied (If-Match on the
    verified ETag) to the final key, which no presigned POST ever targets, so an uploader
    cannot replace a file after it was checked."""
    return f"{tenant_prefix(tenant_id)}uploads/{intent_id}/original.{ext}"


def key_in_tenant(key: str, tenant_id: uuid.UUID) -> bool:
    return key.startswith(tenant_prefix(tenant_id)) and ".." not in key.split("/")


def attachment_disposition(filename: str) -> str:
    """``attachment`` with an ASCII-only generated filename (no titles or names in URLs)."""
    safe = "".join(c for c in filename if c.isascii() and (c.isalnum() or c in "._-"))
    return f'attachment; filename="{safe or "download"}"'


class ObjectStore(Protocol):
    def presigned_post(
        self, *, key: str, content_type: str, max_bytes: int, expires_s: int
    ) -> PresignedPost: ...

    def presigned_get(
        self, *, key: str, content_type: str, filename: str, expires_s: int
    ) -> tuple[str, dt.datetime]: ...

    def head(self, key: str) -> ObjectHead | None: ...

    def read_range(self, key: str, start: int, length: int) -> bytes: ...

    def iter_chunks(self, key: str, chunk_bytes: int = CHUNK_BYTES) -> Iterator[bytes]: ...

    def open(self, key: str, chunk_bytes: int = CHUNK_BYTES) -> OpenedObject: ...

    def copy(self, src: str, dst: str, *, if_match: str, content_type: str) -> None: ...

    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def delete_prefix(self, prefix: str) -> int: ...


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class S3ObjectStore:
    """boto3 implementation. ``presign_client`` signs browser-facing URLs (may use another
    endpoint than ``client``, e.g. localhost vs the compose service name)."""

    def __init__(
        self,
        client: S3Client,
        bucket: str,
        *,
        presign_client: S3Client | None = None,
        kms_key_id: str | None = None,
    ) -> None:
        self._client = client
        self._presign = presign_client or client
        self._bucket = bucket
        self._kms_key_id = kms_key_id

    @property
    def kms_key_id(self) -> str | None:
        return self._kms_key_id

    def _sse_args(self) -> dict[str, str]:
        if not self._kms_key_id:
            return {}
        return {"ServerSideEncryption": SSE_KMS, "SSEKMSKeyId": self._kms_key_id}

    def presigned_post(
        self, *, key: str, content_type: str, max_bytes: int, expires_s: int
    ) -> PresignedPost:
        if not 1 <= expires_s <= MAX_UPLOAD_URL_TTL_S:
            raise ValueError("upload URL lifetime must be 1..600 seconds")
        fields: dict[str, str] = {"Content-Type": content_type}
        conditions: list[Any] = [
            {"Content-Type": content_type},
            ["content-length-range", 1, max_bytes],
        ]
        if self._kms_key_id:
            fields["x-amz-server-side-encryption"] = SSE_KMS
            fields["x-amz-server-side-encryption-aws-kms-key-id"] = self._kms_key_id
            conditions += [
                {"x-amz-server-side-encryption": SSE_KMS},
                {"x-amz-server-side-encryption-aws-kms-key-id": self._kms_key_id},
            ]
        # The key is an exact match (boto3 adds {"key": key}); no starts-with wildcard.
        post = self._presign.generate_presigned_post(
            Bucket=self._bucket,
            Key=key,
            Fields=fields,
            Conditions=conditions,
            ExpiresIn=expires_s,
        )
        return PresignedPost(
            url=str(post["url"]),
            fields={str(k): str(v) for k, v in post["fields"].items()},
            expires_at=_utcnow() + dt.timedelta(seconds=expires_s),
        )

    def presigned_get(
        self, *, key: str, content_type: str, filename: str, expires_s: int
    ) -> tuple[str, dt.datetime]:
        if not 1 <= expires_s <= MAX_DOWNLOAD_URL_TTL_S:
            raise ValueError("download URL lifetime must be 1..300 seconds")
        url = self._presign.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": attachment_disposition(filename),
                "ResponseContentType": content_type,
                "ResponseCacheControl": "private, no-store",
            },
            ExpiresIn=expires_s,
        )
        return str(url), _utcnow() + dt.timedelta(seconds=expires_s)

    def head(self, key: str) -> ObjectHead | None:
        try:
            res = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise ObjectStoreError("head_failed") from exc
        return ObjectHead(
            size=int(res["ContentLength"]),
            content_type=res.get("ContentType"),
            sse=res.get("ServerSideEncryption"),
            kms_key_id=res.get("SSEKMSKeyId"),
        )

    def read_range(self, key: str, start: int, length: int) -> bytes:
        if length <= 0:
            return b""
        try:
            res = self._client.get_object(
                Bucket=self._bucket, Key=key, Range=f"bytes={start}-{start + length - 1}"
            )
            return bytes(res["Body"].read())
        except ClientError as exc:
            raise ObjectStoreError("read_failed") from exc

    def iter_chunks(self, key: str, chunk_bytes: int = CHUNK_BYTES) -> Iterator[bytes]:
        try:
            body = self._client.get_object(Bucket=self._bucket, Key=key)["Body"]
        except ClientError as exc:
            raise ObjectStoreError("read_failed") from exc
        try:
            yield from body.iter_chunks(chunk_bytes)
        finally:
            body.close()

    def open(self, key: str, chunk_bytes: int = CHUNK_BYTES) -> OpenedObject:
        try:
            res = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            raise ObjectStoreError("read_failed") from exc
        body = res["Body"]

        def chunks() -> Iterator[bytes]:
            try:
                yield from body.iter_chunks(chunk_bytes)
            finally:
                body.close()

        return OpenedObject(etag=str(res["ETag"]), chunks=chunks())

    def copy(self, src: str, dst: str, *, if_match: str, content_type: str) -> None:
        try:
            self._client.copy_object(
                Bucket=self._bucket,
                Key=dst,
                CopySource={"Bucket": self._bucket, "Key": src},
                CopySourceIfMatch=if_match,
                MetadataDirective="REPLACE",
                ContentType=content_type,
                **self._sse_args(),  # type: ignore[arg-type]
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("PreconditionFailed", "412"):
                raise ObjectChanged("source_changed") from exc
            raise ObjectStoreError("copy_failed") from exc

    def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                **self._sse_args(),  # type: ignore[arg-type]
            )
        except ClientError as exc:
            raise ObjectStoreError("put_failed") from exc

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            raise ObjectStoreError("delete_failed") from exc

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object under ``prefix`` (must be inside a tenant prefix)."""
        if not prefix.startswith("t/") or prefix.count("/") < 3:
            raise ValueError("refusing to delete outside a tenant prefix")
        deleted = 0
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                keys: list[ObjectIdentifierTypeDef] = [
                    {"Key": o["Key"]} for o in page.get("Contents", []) if "Key" in o
                ]
                if keys:
                    self._client.delete_objects(
                        Bucket=self._bucket, Delete={"Objects": keys, "Quiet": True}
                    )
                    deleted += len(keys)
        except ClientError as exc:
            raise ObjectStoreError("delete_failed") from exc
        return deleted


def _client(settings: Settings, endpoint: str | None) -> S3Client:
    config = Config(
        signature_version="s3v4",
        s3={"addressing_style": "path" if endpoint else "auto"},
        retries={"max_attempts": 3, "mode": "standard"},
        connect_timeout=5,
        read_timeout=30,
    )
    client: S3Client = boto3.client(
        "s3", endpoint_url=endpoint, region_name=settings.aws_region, config=config
    )
    return client


def build_s3_store(settings: Settings) -> S3ObjectStore:
    client = _client(settings, settings.s3_endpoint_url)
    presign_endpoint = settings.s3_presign_endpoint_url or settings.s3_endpoint_url
    presign = (
        client
        if presign_endpoint == settings.s3_endpoint_url
        else _client(settings, presign_endpoint)
    )
    return S3ObjectStore(
        client,
        settings.s3_bucket_files,
        presign_client=presign,
        kms_key_id=settings.s3_kms_key_id,
    )


_lock = threading.Lock()
_store: ObjectStore | None = None


def get_object_store() -> ObjectStore:
    global _store  # noqa: PLW0603
    with _lock:
        if _store is None:
            _store = build_s3_store(get_settings())
        return _store


def set_object_store(store: ObjectStore | None) -> None:
    """Override the store (tests, worker bootstrap); ``None`` rebuilds from settings."""
    global _store  # noqa: PLW0603
    with _lock:
        _store = store
