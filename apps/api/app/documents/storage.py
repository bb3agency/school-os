"""Object storage for documents (FR-DOC-003, FR-DOC-004, SEC-016; docs/04 §8.2, docs/07 §10).

Layout (private bucket, versioning on, tenant prefix first so lifecycle and deletion can work
per school)::

    t/<tenant_id>/docs/<document_id>/v<n>/original.<ext>
    t/<tenant_id>/docs/<document_id>/v<n>/derived/...        (M2: text layer, page renders)
    t/<tenant_id>/imports/<batch_id>/raw.<ext>
    t/<tenant_id>/exports/<export_id>/<file>                  (exports; deleted after 7 days)
    t/<tenant_id>/tenant-export/<export_id>.zip               (full export; deleted after 24 h)

- Lifecycle rules (infra/terraform, files bucket) can only filter on a literal prefix, and every
  key starts with the tenant, so expiring categories are selected by the object tag
  ``sos-lifecycle``, set in the same PUT (``put(..., lifecycle=...)``): export files carry
  ``export-7d`` (rule ``exports-7d``: current and noncurrent versions expire after 7 days / 1 day);
  the school's full data export (FR-ADM-001) carries ``tenant-export-2d`` (rule
  ``tenant-export-2d``: 2 days / noncurrent 1 day), a backstop to the 24-hour purge job.
- Large objects (the full export archive) are streamed with :meth:`ObjectStore.open_writer`: an
  S3 multipart upload with the same SSE-KMS and tagging arguments, so the archive is never
  written to the worker's disk nor held in memory at once.

- Browsers upload with a presigned POST that pins the exact key, the exact Content-Type and a
  content-length-range, and expires in at most 10 minutes. With ``SOS_S3_KMS_KEY_ID`` set the
  policy also requires SSE-KMS with that key (the bucket default encrypts as well).
- Downloads are presigned GETs valid at most 5 minutes that force
  ``Content-Disposition: attachment`` and the verified content type. Nothing is ever public.
- The bucket is versioned (90-day recovery window for overwrites and deletes). Objects that must
  not be kept at all (PRV-016: an image that showed a full Aadhaar number) are removed with
  :meth:`ObjectStore.discard`, which tags them ``sos-lifecycle=discarded`` before deleting; the
  lifecycle rule ``discarded-1d`` (infra/terraform) expires such versions after one day. Every
  automatic deletion uses it too (retention purges, expired exports and uploads: docs/08 §7,
  :meth:`ObjectStore.purge_prefix` for a prefix); only a person's delete keeps the 90-day window.

``ObjectStore`` is a Protocol so tests can swap in an in-memory store; the real implementation
is :class:`S3ObjectStore` (boto3; SeaweedFS locally and in CI via an endpoint override).
"""

from __future__ import annotations

import datetime as dt
import re
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
_EXPORT_FILENAME_RE: Final = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}")
LIFECYCLE_TAG: Final = "sos-lifecycle"
DISCARDED: Final = "discarded"
# ``sos-lifecycle`` values a PUT may set; each has a rule of the same tag in infra/terraform
# (modules/s3 and modules/dedicated_host). ``discarded`` is set only by :meth:`discard`.
LIFECYCLE_EXPORT: Final = "export-7d"
LIFECYCLE_TENANT_EXPORT: Final = "tenant-export-2d"
LIFECYCLE_TAG_VALUES: Final = frozenset(
    {LIFECYCLE_EXPORT, LIFECYCLE_TENANT_EXPORT, "import-raw-90d"}
)
# Multipart upload part size for streamed objects (S3 minimum 5 MiB except the last part).
MULTIPART_PART_BYTES: Final = 8 * 1024 * 1024
MAX_MULTIPART_PARTS: Final = 10_000


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
    last_modified: dt.datetime | None = None


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


def export_prefix(tenant_id: uuid.UUID, export_id: uuid.UUID) -> str:
    """``t/<tenant_id>/exports/<export_id>/`` (docs/04 §8.2; lifecycle: delete after 7 days)."""
    return f"{tenant_prefix(tenant_id)}exports/{export_id}/"


def export_key(tenant_id: uuid.UUID, export_id: uuid.UUID, filename: str) -> str:
    if not _EXPORT_FILENAME_RE.fullmatch(filename):
        raise ValueError("export file names are generated: [a-z0-9][a-z0-9._-]{0,79}")
    return f"{export_prefix(tenant_id, export_id)}{filename}"


def tenant_export_key(tenant_id: uuid.UUID, export_id: uuid.UUID) -> str:
    """``t/<tenant_id>/tenant-export/<export_id>.zip`` (docs/04 §8.2; FR-ADM-001: the school's
    full data export, lifecycle tag ``tenant-export-2d``)."""
    return f"{tenant_prefix(tenant_id)}tenant-export/{export_id}.zip"


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


class ObjectWriter(Protocol):
    """A streamed upload (:meth:`ObjectStore.open_writer`). ``write`` appends bytes; ``close``
    completes the object (nothing is visible before); ``abort`` discards everything written.
    Not seekable, so ``zipfile`` writes a streamed archive (data descriptors)."""

    def write(self, data: bytes, /) -> int: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...

    def abort(self) -> None: ...

    @property
    def size(self) -> int: ...


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

    def put(
        self, key: str, data: bytes, content_type: str, *, lifecycle: str | None = None
    ) -> None:
        """Write an object; ``lifecycle`` (one of :data:`LIFECYCLE_TAG_VALUES`) tags it
        ``sos-lifecycle=<value>`` in the same request so the bucket's lifecycle rule expires it."""
        ...

    def open_writer(
        self, key: str, content_type: str, *, lifecycle: str | None = None
    ) -> ObjectWriter:
        """Stream a large object (multipart upload, SSE-KMS, ``lifecycle`` tag as in
        :meth:`put`); see :class:`ObjectWriter`."""
        ...

    def delete(self, key: str) -> None: ...

    def discard(self, key: str) -> None:
        """Delete an object that must not be kept (see module docstring); idempotent."""
        ...

    def delete_prefix(self, prefix: str) -> int: ...

    def count_prefix(self, prefix: str) -> int:
        """Current objects under a tenant prefix (``t/<tenant_id>/`` or below)."""
        ...

    def purge_prefix(self, prefix: str) -> int:
        """Offboarding (ADR-0029): :meth:`discard` every current object under a tenant prefix,
        so the versioned bucket expires the bytes after one day. Idempotent and resumable."""
        ...


_TENANT_ROOT = re.compile(r"^t/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/")


def check_tenant_prefix(prefix: str) -> str:
    """A prefix inside one school's objects (``t/<tenant_id>/...``), never the bucket root."""
    if not _TENANT_ROOT.match(prefix) or ".." in prefix.split("/"):
        raise ValueError("refusing a prefix outside one tenant")
    return prefix


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
            last_modified=res.get("LastModified"),
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

    def put(
        self, key: str, data: bytes, content_type: str, *, lifecycle: str | None = None
    ) -> None:
        extra: dict[str, str] = dict(self._sse_args())
        if lifecycle is not None:
            if lifecycle not in LIFECYCLE_TAG_VALUES:
                raise ValueError("unknown lifecycle tag value")
            # URL-encoded query string; both parts are fixed ASCII tokens.
            extra["Tagging"] = f"{LIFECYCLE_TAG}={lifecycle}"
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                **extra,  # type: ignore[arg-type]
            )
        except ClientError as exc:
            raise ObjectStoreError("put_failed") from exc

    def open_writer(
        self, key: str, content_type: str, *, lifecycle: str | None = None
    ) -> ObjectWriter:
        extra: dict[str, str] = dict(self._sse_args())
        if lifecycle is not None:
            if lifecycle not in LIFECYCLE_TAG_VALUES:
                raise ValueError("unknown lifecycle tag value")
            extra["Tagging"] = f"{LIFECYCLE_TAG}={lifecycle}"
        try:
            res = self._client.create_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                ContentType=content_type,
                **extra,  # type: ignore[arg-type]
            )
        except ClientError as exc:
            raise ObjectStoreError("put_failed") from exc
        return _S3MultipartWriter(self._client, self._bucket, key, str(res["UploadId"]))

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            raise ObjectStoreError("delete_failed") from exc

    def discard(self, key: str) -> None:
        """Tag EVERY stored version of ``key`` ``sos-lifecycle=discarded``, then delete the
        current one. The versioned bucket keeps the bytes as noncurrent versions, which the
        lifecycle rule ``discarded-1d`` expires after one day instead of the 90-day recovery
        window; an older version (the key written twice, e.g. a re-posted upload) is tagged too
        (audit W3-07). A key that is gone already (no object, or only a delete marker) is fine:
        retries are safe."""
        if not key.startswith("t/") or ".." in key.split("/"):
            raise ValueError("refusing to discard outside a tenant prefix")
        versions = [v for v in self._versions(key) if v[0] == key]
        self._discard_versions(key, versions)

    def _versions(self, prefix: str) -> Iterator[tuple[str, str, bool]]:
        """``(key, version_id, is_latest)`` of every stored version under ``prefix`` (delete
        markers hold no bytes and are left out)."""
        try:
            paginator = self._client.get_paginator("list_object_versions")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for v in page.get("Versions", []):
                    if "Key" in v and "VersionId" in v:
                        yield v["Key"], v["VersionId"], bool(v.get("IsLatest"))
        except ClientError as exc:
            raise ObjectStoreError("list_failed") from exc

    def _discard_versions(self, key: str, versions: list[tuple[str, str, bool]]) -> bool:
        """Tag each version of ``key``, then delete the current object if there is one.
        Returns whether a current object was deleted."""
        for _, version_id, _ in versions:
            try:
                self._client.put_object_tagging(
                    Bucket=self._bucket,
                    Key=key,
                    VersionId=version_id,
                    Tagging={"TagSet": [{"Key": LIFECYCLE_TAG, "Value": DISCARDED}]},
                )
            except ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                if code in (
                    "404",
                    "NoSuchKey",
                    "NoSuchVersion",
                    "NotFound",
                    "405",
                    "MethodNotAllowed",
                ):
                    continue  # removed meanwhile (or a delete marker): nothing left to tag
                raise ObjectStoreError("tag_failed") from exc
        if any(latest for _, _, latest in versions):
            self.delete(key)
            return True
        return False

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

    def _keys(self, prefix: str) -> Iterator[str]:
        try:
            paginator = self._client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    if "Key" in obj:
                        yield obj["Key"]
        except ClientError as exc:
            raise ObjectStoreError("list_failed") from exc

    def count_prefix(self, prefix: str) -> int:
        return sum(1 for _ in self._keys(check_tenant_prefix(prefix)))

    def purge_prefix(self, prefix: str) -> int:
        """Discard every key under the prefix, including keys with only noncurrent versions
        left (deleted earlier by a person, 90-day window) and older versions of live keys
        (audit W3-07). Returns the number of current objects deleted."""
        # Collect first: deleting while paginating would shift the listing.
        by_key: dict[str, list[tuple[str, str, bool]]] = {}
        for version in self._versions(check_tenant_prefix(prefix)):
            by_key.setdefault(version[0], []).append(version)
        return sum(self._discard_versions(key, versions) for key, versions in by_key.items())


class _S3MultipartWriter:
    """:class:`ObjectWriter` over an S3 multipart upload: buffers up to
    :data:`MULTIPART_PART_BYTES`, uploads each full part, completes on :meth:`close` and aborts
    the upload (no partial object, no stored parts) on :meth:`abort` or any failure."""

    def __init__(self, client: S3Client, bucket: str, key: str, upload_id: str) -> None:
        self._client = client
        self._bucket = bucket
        self._key = key
        self._upload_id = upload_id
        self._buffer = bytearray()
        self._parts: list[dict[str, Any]] = []
        self._size = 0
        self._done = False

    @property
    def size(self) -> int:
        return self._size

    def write(self, data: bytes, /) -> int:
        if self._done:
            raise ValueError("writer is closed")
        self._buffer += data
        self._size += len(data)
        while len(self._buffer) >= MULTIPART_PART_BYTES:
            self._upload(bytes(self._buffer[:MULTIPART_PART_BYTES]))
            del self._buffer[:MULTIPART_PART_BYTES]
        return len(data)

    def flush(self) -> None:
        """Parts are uploaded when full; nothing to do."""

    def _upload(self, chunk: bytes) -> None:
        number = len(self._parts) + 1
        if number > MAX_MULTIPART_PARTS:
            self.abort()
            raise ObjectStoreError("too_large")
        try:
            res = self._client.upload_part(
                Bucket=self._bucket,
                Key=self._key,
                UploadId=self._upload_id,
                PartNumber=number,
                Body=chunk,
            )
        except ClientError as exc:
            self.abort()
            raise ObjectStoreError("put_failed") from exc
        self._parts.append({"ETag": res["ETag"], "PartNumber": number})

    def close(self) -> None:
        if self._done:
            return
        if self._buffer or not self._parts:
            self._upload(bytes(self._buffer))
            self._buffer.clear()
        try:
            self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=self._key,
                UploadId=self._upload_id,
                MultipartUpload={"Parts": self._parts},  # type: ignore[typeddict-item]
            )
        except ClientError as exc:
            self.abort()
            raise ObjectStoreError("put_failed") from exc
        self._done = True

    def abort(self) -> None:
        if self._done:
            return
        self._done = True
        self._buffer.clear()
        try:
            self._client.abort_multipart_upload(
                Bucket=self._bucket, Key=self._key, UploadId=self._upload_id
            )
        except ClientError:
            # The bucket's abort-incomplete-multipart lifecycle rule removes leftover parts.
            return


def _client(settings: Settings, endpoint: str | None) -> S3Client:
    """S3 client. With an endpoint override (SeaweedFS locally and in CI) path-style addressing.

    Without one (AWS, staging/prod) addressing is pinned to ``virtual``, so presigned POST and
    GET URLs always use the regional virtual-hosted origin
    ``https://<bucket>.s3.<region>.amazonaws.com``. That exact origin is what Terraform allows
    in the files bucket's CORS rule and what the web app's CSP gets as ``FILES_ORIGIN``
    (docs/07 §10, §11). ``auto`` would presign with the legacy global host
    ``<bucket>.s3.amazonaws.com`` (botocore 1.43), which redirects for buckets outside
    us-east-1 and breaks a cross-origin POST. Bucket names must therefore not contain dots.
    """
    config = Config(
        signature_version="s3v4",
        s3={"addressing_style": "path" if endpoint else "virtual"},
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
