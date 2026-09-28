"""Object storage for invoice PDFs: the control-plane bucket/prefix (ADR-0017 Amendment
2026-09-28; docs/16 §5.8).

Invoices are control-plane business records, not school data: they are stored under
``billing.yaml`` → ``invoice_pdf.object_prefix`` (``platform/invoices/``) of
``SOS_PLATFORM_INVOICE_BUCKET``, or of the files bucket when that is unset, and **never** under
a school prefix ``t/<tenant_id>/`` (so offboarding a school, which deletes its prefix, keeps its
invoices, which stay as business records; docs/16 §5.5, 08 §14).

Private bucket, SSE-KMS with ``SOS_S3_KMS_KEY_ID`` when set, presigned GETs of at most five
minutes that force ``Content-Disposition: attachment``. The platform module cannot use
``app.documents`` (a tenant module; ADR-0020), so this is a deliberately small client of its
own: put, head, delete and presign, nothing that lists or reaches another prefix.
``InvoiceStore`` is a Protocol so tests use :class:`MemoryInvoiceStore`.
"""

from __future__ import annotations

import datetime as dt
import threading
import uuid
from typing import TYPE_CHECKING, Final, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import Settings, get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

PDF_MIME: Final = "application/pdf"
MAX_URL_TTL_S: Final = 300
SSE_KMS: Final = "aws:kms"


class InvoiceStoreError(RuntimeError):
    """The object store failed or refused (never carries document contents)."""


def object_key(
    prefix: str, financial_year: str, invoice_id: uuid.UUID, render_id: uuid.UUID
) -> str:
    """``platform/invoices/<fy>/<invoice_id>/<render_id>.pdf``: one object per render attempt,
    so two concurrent renders never overwrite each other (the database keeps exactly one)."""
    if not prefix.endswith("/") or prefix.startswith(("t/", "/")) or ".." in prefix:
        raise ValueError("invoice prefix must be a control-plane prefix ending in '/'")
    return f"{prefix}{financial_year}/{invoice_id}/{render_id}.pdf"


def attachment(filename: str) -> str:
    safe = "".join(c for c in filename if c.isascii() and (c.isalnum() or c in "._-"))
    return f'attachment; filename="{safe or "invoice.pdf"}"'


class InvoiceStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...

    def presigned_get(
        self, key: str, *, filename: str, expires_s: int
    ) -> tuple[str, dt.datetime]: ...


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def _check_key(key: str) -> None:
    if key.startswith(("t/", "/")) or ".." in key.split("/"):
        raise ValueError("refusing an invoice key outside the control-plane prefix")


class S3InvoiceStore:
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

    def put(self, key: str, data: bytes) -> None:
        _check_key(key)
        extra: dict[str, str] = {}
        if self._kms_key_id:
            extra = {"ServerSideEncryption": SSE_KMS, "SSEKMSKeyId": self._kms_key_id}
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=PDF_MIME,
                **extra,  # type: ignore[arg-type]
            )
        except ClientError as exc:
            raise InvoiceStoreError("put_failed") from exc

    def exists(self, key: str) -> bool:
        _check_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise InvoiceStoreError("head_failed") from exc
        return True

    def delete(self, key: str) -> None:
        _check_key(key)
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            raise InvoiceStoreError("delete_failed") from exc

    def presigned_get(self, key: str, *, filename: str, expires_s: int) -> tuple[str, dt.datetime]:
        _check_key(key)
        if not 1 <= expires_s <= MAX_URL_TTL_S:
            raise ValueError("download URL lifetime must be 1..300 seconds")
        url = self._presign.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": attachment(filename),
                "ResponseContentType": PDF_MIME,
                "ResponseCacheControl": "private, no-store",
            },
            ExpiresIn=expires_s,
        )
        return str(url), _utcnow() + dt.timedelta(seconds=expires_s)


class MemoryInvoiceStore:
    """In-memory store for tests (same key rules as the S3 store)."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put(self, key: str, data: bytes) -> None:
        _check_key(key)
        self.objects[key] = data

    def exists(self, key: str) -> bool:
        return key in self.objects

    def delete(self, key: str) -> None:
        _check_key(key)
        self.objects.pop(key, None)
        self.deleted.append(key)

    def presigned_get(self, key: str, *, filename: str, expires_s: int) -> tuple[str, dt.datetime]:
        _check_key(key)
        if not 1 <= expires_s <= MAX_URL_TTL_S:
            raise ValueError("download URL lifetime must be 1..300 seconds")
        url = f"https://invoices.example.test/{key}?disposition={attachment(filename)}"
        return url, _utcnow() + dt.timedelta(seconds=expires_s)


def _client(settings: Settings, endpoint: str | None) -> S3Client:
    # Same addressing rules as app.documents.storage (virtual-hosted in AWS, path style locally).
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


def build_s3_store(settings: Settings) -> S3InvoiceStore:
    client = _client(settings, settings.s3_endpoint_url)
    presign_endpoint = settings.s3_presign_endpoint_url or settings.s3_endpoint_url
    presign = (
        client
        if presign_endpoint == settings.s3_endpoint_url
        else _client(settings, presign_endpoint)
    )
    return S3InvoiceStore(
        client,
        settings.platform_invoice_bucket or settings.s3_bucket_files,
        presign_client=presign,
        kms_key_id=settings.s3_kms_key_id,
    )


_lock = threading.Lock()
_store: InvoiceStore | None = None


def get_invoice_store() -> InvoiceStore:
    global _store  # noqa: PLW0603
    with _lock:
        if _store is None:
            _store = build_s3_store(get_settings())
        return _store


def set_invoice_store(store: InvoiceStore | None) -> None:
    """Override the store (tests); ``None`` rebuilds from settings."""
    global _store  # noqa: PLW0603
    with _lock:
        _store = store


__all__ = [
    "PDF_MIME",
    "InvoiceStore",
    "InvoiceStoreError",
    "MemoryInvoiceStore",
    "S3InvoiceStore",
    "get_invoice_store",
    "object_key",
    "set_invoice_store",
]
