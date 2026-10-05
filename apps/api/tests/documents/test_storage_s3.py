"""Real S3 semantics against SeaweedFS (FR-DOC-003, FR-DOC-004, SEC-016; ADR-0014).

Starts the pinned ``chrislusf/seaweedfs`` image from docker-compose.yml with testcontainers
(like the PostgreSQL fixture, nothing is skipped when Docker is missing). Proves that the
presigned POST policy is enforced by the object store itself (size, exact Content-Type, exact
key), that presigned GETs download as attachments and expire within 5 minutes, that objects are
never public, and the full browser -> S3 -> API -> scan -> download round trip.
"""

from __future__ import annotations

import base64
import json
import re
import sys
import time
import urllib.parse
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import httpx
import pytest
from botocore.config import Config
from botocore.stub import Stubber

from app.documents import service, storage
from app.documents.storage import S3ObjectStore

pytestmark = pytest.mark.s3
S = sys.modules["sos_test_documents_support"]

REPO_ROOT = Path(__file__).resolve().parents[4]
COMPOSE = REPO_ROOT / "docker-compose.yml"
S3_CONFIG = REPO_ROOT / "infra" / "docker" / "seaweedfs" / "s3.json"
BUCKET = "sos-test-files"
PDF_CT = "application/pdf"


def _seaweed_image() -> str:
    match = re.search(
        r"image:\s*(chrislusf/seaweedfs:[^\s]+@sha256:[0-9a-f]{64})", COMPOSE.read_text()
    )
    assert match, "docker-compose.yml pins chrislusf/seaweedfs by digest"
    return match.group(1)


def _credentials() -> tuple[str, str]:
    ident = json.loads(S3_CONFIG.read_text())["identities"][0]["credentials"][0]
    return ident["accessKey"], ident["secretKey"]


def _client(endpoint: str) -> Any:
    key, secret = _credentials()
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name="ap-south-1",
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@pytest.fixture(scope="module")
def s3_endpoint() -> Iterator[str]:
    from testcontainers.core.container import DockerContainer

    container = (
        DockerContainer(_seaweed_image())
        .with_command(
            "server -dir=/data -s3 -s3.port=8333 -s3.config=/etc/seaweedfs/s3.json "
            "-master.volumeSizeLimitMB=64"
        )
        .with_volume_mapping(str(S3_CONFIG), "/etc/seaweedfs/s3.json", "ro")
        .with_exposed_ports(8333)
    )
    with container:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8333)}"
        client = _client(endpoint)
        deadline = time.monotonic() + 90
        while True:
            try:
                client.create_bucket(Bucket=BUCKET)
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(1)
        yield endpoint


@pytest.fixture
def s3_store(s3_endpoint: str) -> Iterator[S3ObjectStore]:
    real = S3ObjectStore(_client(s3_endpoint), BUCKET)
    storage.set_object_store(real)
    yield real
    S.memory_store()  # restore the in-memory store for other tests


def _post(post: storage.PresignedPost, data: bytes, content_type: str, **override: str) -> int:
    fields = {**post.fields, **override}
    res = httpx.post(post.url, data=fields, files={"file": ("f", data, content_type)}, timeout=30)
    return res.status_code


def test_SEC_016_presigned_post_policy_is_enforced_by_the_store(s3_store: S3ObjectStore) -> None:
    tenant, doc = uuid.uuid4(), uuid.uuid4()
    key = storage.document_key(tenant, doc, 1, "pdf")
    data = S.pdf()
    post = s3_store.presigned_post(key=key, content_type=PDF_CT, max_bytes=len(data), expires_s=600)
    assert post.fields["key"] == key
    assert _post(post, data + b"x" * 64, PDF_CT) == 400, "content-length-range"
    assert _post(post, data, "text/html", **{"Content-Type": "text/html"}) == 403
    other = storage.document_key(uuid.uuid4(), doc, 1, "pdf")
    assert _post(post, data, PDF_CT, key=other) == 403, "exact key: no other tenant prefix"
    assert _post(post, data, PDF_CT, **{"x-amz-signature": "0" * 64}) == 403
    assert s3_store.head(key) is None
    assert _post(post, data, PDF_CT) == 204
    head = s3_store.head(key)
    assert head is not None
    assert (head.size, head.content_type) == (len(data), PDF_CT)
    assert s3_store.read_range(key, 0, 5) == b"%PDF-"
    assert b"".join(s3_store.iter_chunks(key, 7)) == data


def test_SEC_016_conditional_copy_refuses_a_changed_source(s3_store: S3ObjectStore) -> None:
    tenant, intent = uuid.uuid4(), uuid.uuid4()
    staging = storage.upload_key(tenant, intent, "pdf")
    final = storage.document_key(tenant, uuid.uuid4(), 1, "pdf")
    data = S.pdf()
    s3_store.put(staging, data, "application/octet-stream")
    opened = s3_store.open(staging)
    assert b"".join(opened.chunks) == data
    s3_store.put(staging, S.pdf("swapped"), PDF_CT)  # replaced after it was read
    with pytest.raises(storage.ObjectChanged):
        s3_store.copy(staging, final, if_match=opened.etag, content_type=PDF_CT)
    assert s3_store.head(final) is None
    current = s3_store.open(staging)
    current.close()
    s3_store.copy(staging, final, if_match=current.etag, content_type=PDF_CT)
    head = s3_store.head(final)
    assert head is not None
    assert head.content_type == PDF_CT


def test_SEC_016_upload_url_lifetime_is_capped(s3_store: S3ObjectStore) -> None:
    with pytest.raises(ValueError, match="600"):
        s3_store.presigned_post(
            key="t/x/docs/y/v1/original.pdf", content_type=PDF_CT, max_bytes=10, expires_s=601
        )
    with pytest.raises(ValueError, match="300"):
        s3_store.presigned_get(
            key="t/x/docs/y/v1/original.pdf", content_type=PDF_CT, filename="a.pdf", expires_s=301
        )


def test_FR_DOC_004_presigned_get_is_attachment_and_objects_are_never_public(
    s3_store: S3ObjectStore, s3_endpoint: str
) -> None:
    tenant = uuid.uuid4()
    key = storage.document_key(tenant, uuid.uuid4(), 1, "pdf")
    data = S.pdf()
    s3_store.put(key, data, PDF_CT)
    url, _expires_at = s3_store.presigned_get(
        key=key, content_type=PDF_CT, filename='evil"; inline; x=".pdf', expires_s=300
    )
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert int(query["X-Amz-Expires"][0]) <= 300
    res = httpx.get(url, timeout=30)
    assert res.status_code == 200
    assert res.content == data
    disposition = res.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert disposition == 'attachment; filename="evilinlinex.pdf"'
    assert res.headers["content-type"] == PDF_CT
    anonymous = httpx.get(f"{s3_endpoint}/{BUCKET}/{key}", timeout=30)
    assert anonymous.status_code == 403
    assert s3_store.delete_prefix(storage.tenant_prefix(tenant) + "docs/") == 1
    assert s3_store.head(key) is None
    with pytest.raises(ValueError, match="tenant prefix"):
        s3_store.delete_prefix("t/")


def test_FR_DOC_003_sse_kms_is_required_by_policy_and_sent_on_writes() -> None:
    client = boto3.client(
        "s3",
        region_name="ap-south-1",
        aws_access_key_id="synthetic-access",
        aws_secret_access_key="synthetic-secret",
        config=Config(signature_version="s3v4"),
    )
    kms = "arn:aws:kms:ap-south-1:000000000000:key/synthetic"
    store = S3ObjectStore(client, BUCKET, kms_key_id=kms)
    post = store.presigned_post(
        key="t/a/docs/b/v1/original.pdf", content_type=PDF_CT, max_bytes=100, expires_s=600
    )
    policy = json.loads(base64.b64decode(post.fields["policy"]))
    conditions = policy["conditions"]
    assert {"x-amz-server-side-encryption": "aws:kms"} in conditions
    assert {"x-amz-server-side-encryption-aws-kms-key-id": kms} in conditions
    assert {"Content-Type": PDF_CT} in conditions
    assert ["content-length-range", 1, 100] in conditions
    assert {"key": "t/a/docs/b/v1/original.pdf"} in conditions
    assert post.fields["x-amz-server-side-encryption"] == "aws:kms"
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": "t/a/docs/b/v1/derived/pages/1.png",
                "Body": b"png",
                "ContentType": "image/png",
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": kms,
            },
        )
        store.put("t/a/docs/b/v1/derived/pages/1.png", b"png", "image/png")
        stub.assert_no_pending_responses()


def test_FR_EXP_003_export_files_carry_the_7_day_lifecycle_tag_on_upload() -> None:
    """docs/05 §13: exports are kept 7 days. S3 lifecycle filters match a literal prefix only,
    and the key starts with the tenant (t/<tenant_id>/exports/...), so the rule ``exports-7d``
    (infra/terraform) selects by the tag ``sos-lifecycle=export-7d``, set in the same PUT."""
    client = boto3.client(
        "s3",
        region_name="ap-south-1",
        aws_access_key_id="synthetic-access",
        aws_secret_access_key="synthetic-secret",
        config=Config(signature_version="s3v4"),
    )
    kms = "arn:aws:kms:ap-south-1:000000000000:key/synthetic"
    store = S3ObjectStore(client, BUCKET, kms_key_id=kms)
    key = "t/a/exports/b/precheck.pdf"
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": BUCKET,
                "Key": key,
                "Body": b"%PDF-",
                "ContentType": PDF_CT,
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": kms,
                "Tagging": "sos-lifecycle=export-7d",
            },
        )
        store.put(key, b"%PDF-", PDF_CT, lifecycle=storage.LIFECYCLE_EXPORT)
        stub.assert_no_pending_responses()
    with pytest.raises(ValueError, match="lifecycle"):
        store.put(key, b"%PDF-", PDF_CT, lifecycle="forever")


def test_FR_EXP_003_lifecycle_tag_is_stored_with_the_object(
    s3_store: S3ObjectStore, s3_endpoint: str
) -> None:
    client = _client(s3_endpoint)
    key = storage.export_key(uuid.uuid4(), uuid.uuid4(), "precheck.pdf")
    s3_store.put(key, S.pdf(), PDF_CT, lifecycle=storage.LIFECYCLE_EXPORT)
    tags = client.get_object_tagging(Bucket=BUCKET, Key=key)["TagSet"]
    assert tags == [{"Key": "sos-lifecycle", "Value": "export-7d"}]
    plain = storage.document_key(uuid.uuid4(), uuid.uuid4(), 1, "pdf")
    s3_store.put(plain, S.pdf(), PDF_CT)
    assert client.get_object_tagging(Bucket=BUCKET, Key=plain)["TagSet"] == []


def test_PRV_016_discard_tags_then_deletes_and_is_idempotent(s3_store: S3ObjectStore) -> None:
    key = storage.document_key(uuid.uuid4(), uuid.uuid4(), 1, "png")
    s3_store.put(key, S.png(), "image/png")
    s3_store.discard(key)
    assert s3_store.head(key) is None
    s3_store.discard(key)  # already gone: nothing to do, no error
    with pytest.raises(ValueError, match="tenant prefix"):
        s3_store.discard("elsewhere/original.png")


VERSIONED_BUCKET = "sos-test-files-versioned"


@pytest.fixture
def versioned_store(s3_endpoint: str) -> tuple[S3ObjectStore, Any]:
    """A versioned bucket, like the real files bucket (90-day noncurrent recovery window)."""
    client = _client(s3_endpoint)
    try:
        client.create_bucket(Bucket=VERSIONED_BUCKET)
    except client.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
    client.put_bucket_versioning(
        Bucket=VERSIONED_BUCKET, VersioningConfiguration={"Status": "Enabled"}
    )
    return S3ObjectStore(client, VERSIONED_BUCKET), client


def _version_tags(client: Any, key: str) -> list[tuple[str, list[Any]]]:
    out: list[tuple[str, list[Any]]] = []
    for v in client.list_object_versions(Bucket=VERSIONED_BUCKET, Prefix=key).get("Versions", []):
        if v["Key"] == key:
            tags = client.get_object_tagging(
                Bucket=VERSIONED_BUCKET, Key=key, VersionId=v["VersionId"]
            )["TagSet"]
            out.append((v["VersionId"], tags))
    return out


DISCARDED_TAGS = [{"Key": "sos-lifecycle", "Value": "discarded"}]


def test_PRV_016_discard_tags_every_stored_version_of_the_key(
    versioned_store: tuple[S3ObjectStore, Any],
) -> None:
    """Audit 2026-10-04, W3-07: the bucket is versioned, so a key written twice (an Aadhaar
    image re-posted with the same presigned POST) keeps its first bytes as a noncurrent
    version. ``discard`` tagged only the current version: the older one stayed for the 90-day
    window instead of the 1-day ``discarded-1d`` rule. Every version must carry the tag."""
    store, client = versioned_store
    key = storage.document_key(uuid.uuid4(), uuid.uuid4(), 1, "png")
    store.put(key, S.png(), "image/png")
    store.put(key, S.png() + b"second", "image/png")
    assert len(_version_tags(client, key)) == 2
    store.discard(key)
    assert store.head(key) is None
    versions = _version_tags(client, key)
    assert len(versions) == 2
    assert all(tags == DISCARDED_TAGS for _, tags in versions), versions
    store.discard(key)  # only noncurrent versions and a delete marker are left: still fine


def test_PRV_016_purge_prefix_tags_noncurrent_versions_under_the_prefix(
    versioned_store: tuple[S3ObjectStore, Any],
) -> None:
    """W3-07: a purge also reaches keys whose current version was deleted earlier (only
    noncurrent versions are left, e.g. after a person's delete), and older versions of the
    live keys."""
    store, client = versioned_store
    prefix = f"t/{uuid.uuid4()}/docs/{uuid.uuid4()}/"
    live, gone = f"{prefix}v1/original.png", f"{prefix}v2/original.png"
    store.put(live, S.png(), "image/png")
    store.put(live, S.png() + b"again", "image/png")
    store.put(gone, S.png(), "image/png")
    store.delete(gone)  # a plain delete: noncurrent, 90-day window
    assert store.purge_prefix(prefix) == 1
    for key in (live, gone):
        versions = _version_tags(client, key)
        assert versions, key
        assert all(tags == DISCARDED_TAGS for _, tags in versions), (key, versions)
    assert store.purge_prefix(prefix) == 0


def _stubbed_store() -> tuple[S3ObjectStore, Any]:
    client = boto3.client(
        "s3",
        region_name="ap-south-1",
        aws_access_key_id="synthetic-access",
        aws_secret_access_key="synthetic-secret",
        config=Config(signature_version="s3v4"),
    )
    return S3ObjectStore(client, BUCKET), client


def test_PRV_016_discard_marks_the_object_for_the_short_lifecycle_rule() -> None:
    """The files bucket is versioned: a plain delete keeps the bytes as a noncurrent version for
    the 90-day recovery window. ``discard`` tags the object first so the lifecycle rule
    ``discarded-1d`` (infra/terraform) expires that noncurrent version after one day."""
    store, client = _stubbed_store()
    key = "t/a/docs/b/v1/original.png"
    tagging = {"TagSet": [{"Key": "sos-lifecycle", "Value": "discarded"}]}

    def listed(*versions: tuple[str, str, bool]) -> dict[str, Any]:
        return {
            "Versions": [
                {"Key": k, "VersionId": v, "IsLatest": latest} for k, v, latest in versions
            ],
            "IsTruncated": False,
        }

    with Stubber(client) as stub:
        stub.add_response(
            "list_object_versions",
            # An older version, the current one, and another key sharing the prefix.
            listed((key, "v-new", True), (key, "v-old", False), (key + ".bak", "v-x", True)),
            {"Bucket": BUCKET, "Prefix": key},
        )
        for version in ("v-new", "v-old"):
            stub.add_response(
                "put_object_tagging",
                {},
                {"Bucket": BUCKET, "Key": key, "VersionId": version, "Tagging": tagging},
            )
        stub.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": key})
        store.discard(key)
        stub.assert_no_pending_responses()
    with Stubber(client) as stub:  # only a delete marker / noncurrent version left: no delete
        stub.add_response(
            "list_object_versions", listed((key, "v-old", False)), {"Bucket": BUCKET, "Prefix": key}
        )
        stub.add_response(
            "put_object_tagging",
            {},
            {"Bucket": BUCKET, "Key": key, "VersionId": "v-old", "Tagging": tagging},
        )
        store.discard(key)
        stub.assert_no_pending_responses()
    for missing in ("NoSuchKey", "NoSuchVersion", "MethodNotAllowed"):  # removed meanwhile
        with Stubber(client) as stub:
            stub.add_response("list_object_versions", listed((key, "v-old", False)))
            stub.add_client_error("put_object_tagging", service_error_code=missing)
            store.discard(key)
            stub.assert_no_pending_responses()
    with Stubber(client) as stub:
        stub.add_response("list_object_versions", listed((key, "v-new", True)))
        stub.add_client_error("put_object_tagging", service_error_code="AccessDenied")
        with pytest.raises(storage.ObjectStoreError):
            store.discard(key)


def test_FR_DOC_007_purge_prefix_discards_every_page_of_a_large_prefix() -> None:
    """Automatic retention deletions (docs/08 §7) discard every object under a prefix, across
    listing pages (1000 keys each), all collected before the first delete so the listing does
    not shift. Each object is tagged before its delete (1-day rule, not the 90-day window)."""
    store, client = _stubbed_store()
    prefix = f"t/{uuid.uuid4()}/docs/{uuid.uuid4()}/"
    first = [f"{prefix}v{i}/original.pdf" for i in range(1, 1001)]
    second = [f"{prefix}v1001/original.pdf", f"{prefix}v1001/derived/text.txt"]
    with Stubber(client) as stub:
        stub.add_response(
            "list_object_versions",
            {
                "Versions": [{"Key": k, "VersionId": "v1", "IsLatest": True} for k in first],
                "IsTruncated": True,
                "NextKeyMarker": first[-1],
                "NextVersionIdMarker": "v1",
            },
            {"Bucket": BUCKET, "Prefix": prefix},
        )
        stub.add_response(
            "list_object_versions",
            {
                "Versions": [{"Key": k, "VersionId": "v1", "IsLatest": True} for k in second],
                "IsTruncated": False,
            },
            {
                "Bucket": BUCKET,
                "Prefix": prefix,
                "KeyMarker": first[-1],
                "VersionIdMarker": "v1",
            },
        )
        for key in first + second:
            stub.add_response(
                "put_object_tagging",
                {},
                {
                    "Bucket": BUCKET,
                    "Key": key,
                    "VersionId": "v1",
                    "Tagging": {"TagSet": [{"Key": "sos-lifecycle", "Value": "discarded"}]},
                },
            )
            stub.add_response("delete_object", {}, {"Bucket": BUCKET, "Key": key})
        assert store.purge_prefix(prefix) == 1002
        stub.assert_no_pending_responses()
    with pytest.raises(ValueError, match="tenant"):
        store.purge_prefix("t/")


def test_FR_DOC_007_purge_prefix_is_idempotent_against_s3(s3_store: S3ObjectStore) -> None:
    """A retried purge (the task failed half-way) finishes what is left and fails nothing."""
    prefix = f"t/{uuid.uuid4()}/imports/{uuid.uuid4()}/"
    keys = [f"{prefix}raw-{i}.csv" for i in range(5)]
    for key in keys:
        s3_store.put(key, b"a,b\r\n", "text/csv")
    s3_store.discard(keys[0])  # a first attempt that stopped after one object
    assert s3_store.purge_prefix(prefix) == 4
    assert all(s3_store.head(k) is None for k in keys)
    assert s3_store.purge_prefix(prefix) == 0


@pytest.mark.db
def test_FR_DOC_001_browser_to_s3_to_api_round_trip(
    s3_store: S3ObjectStore, world: Any, api: Any
) -> None:
    who = world.person("office_admin")
    data = S.pdf()
    res = api.call(
        who,
        "POST",
        "/api/v1/documents/uploads",
        json={
            "filename": "circular.pdf",
            "content_type": PDF_CT,
            "size_bytes": len(data),
            "purpose": "circular",
        },
    )
    assert res.status_code == 201, res.text
    up = res.json()
    status = httpx.post(
        up["url"], data=up["fields"], files={"file": ("circular.pdf", data, PDF_CT)}, timeout=30
    ).status_code
    assert status == 204
    reg = api.call(
        who, "POST", "/api/v1/documents", json={"upload_id": up["upload_id"], "title": "Round trip"}
    )
    assert reg.status_code == 202, reg.text
    doc = reg.json()
    verdict = service.scan_version(
        world.a.tenant_id, uuid.UUID(doc["id"]), uuid.UUID(doc["current_version"]["id"])
    )
    assert verdict == "ready", "local settings build the dev scanner"
    link = api.call(who, "GET", f"/api/v1/documents/{doc['id']}/download-url")
    assert link.status_code == 200, link.text
    got = httpx.get(link.json()["url"], timeout=30)
    assert got.status_code == 200
    assert got.content == data
    assert got.headers["content-disposition"].startswith("attachment;")

    # A mismatching upload is deleted from the real store.
    png = S.png()
    res = api.call(
        who,
        "POST",
        "/api/v1/documents/uploads",
        json={
            "filename": "fake.pdf",
            "content_type": PDF_CT,
            "size_bytes": len(png),
            "purpose": "circular",
        },
    )
    up = res.json()
    httpx.post(up["url"], data=up["fields"], files={"file": ("fake.pdf", png, PDF_CT)}, timeout=30)
    assert s3_store.head(up["fields"]["key"]) is not None
    bad = api.call(
        who, "POST", "/api/v1/documents", json={"upload_id": up["upload_id"], "title": "Fake"}
    )
    assert bad.status_code == 415
    # W3-06: the api only queues the discard; the worker removes the object.
    assert s3_store.head(up["fields"]["key"]) is not None
    payload = {"upload_id": up["upload_id"]}
    assert service.discard_unused_object(world.a.tenant_id, payload, store=s3_store) is True
    assert s3_store.head(up["fields"]["key"]) is None


# --- streamed uploads (the school's full data export, FR-ADM-001) ------------------------------


def test_FR_ADM_001_streamed_upload_carries_sse_kms_and_the_tenant_export_tag() -> None:
    """The archive is streamed as a multipart upload (never written to the worker's disk): the
    create request carries SSE-KMS and ``sos-lifecycle=tenant-export-2d`` (bucket rule
    ``tenant-export-2d``); parts are sent when full and the upload completes on close."""
    client = boto3.client(
        "s3",
        region_name="ap-south-1",
        aws_access_key_id="synthetic-access",
        aws_secret_access_key="synthetic-secret",
        config=Config(signature_version="s3v4"),
    )
    kms = "arn:aws:kms:ap-south-1:000000000000:key/synthetic"
    store = S3ObjectStore(client, BUCKET, kms_key_id=kms)
    key = storage.tenant_export_key(uuid.UUID(int=1), uuid.UUID(int=2))
    assert key == f"t/{uuid.UUID(int=1)}/tenant-export/{uuid.UUID(int=2)}.zip"
    with Stubber(client) as stub:
        stub.add_response(
            "create_multipart_upload",
            {"UploadId": "u-1"},
            {
                "Bucket": BUCKET,
                "Key": key,
                "ContentType": "application/zip",
                "ServerSideEncryption": "aws:kms",
                "SSEKMSKeyId": kms,
                "Tagging": "sos-lifecycle=tenant-export-2d",
            },
        )
        stub.add_response(
            "upload_part",
            {"ETag": '"e1"'},
            {"Bucket": BUCKET, "Key": key, "UploadId": "u-1", "PartNumber": 1, "Body": b"PK-small"},
        )
        stub.add_response(
            "complete_multipart_upload",
            {},
            {
                "Bucket": BUCKET,
                "Key": key,
                "UploadId": "u-1",
                "MultipartUpload": {"Parts": [{"ETag": '"e1"', "PartNumber": 1}]},
            },
        )
        writer = store.open_writer(
            key, "application/zip", lifecycle=storage.LIFECYCLE_TENANT_EXPORT
        )
        writer.write(b"PK-")
        writer.write(b"small")
        assert writer.size == 8
        writer.close()
        stub.assert_no_pending_responses()
    with pytest.raises(ValueError, match="lifecycle"):
        store.open_writer(key, "application/zip", lifecycle="forever")


def test_FR_ADM_001_streamed_upload_round_trip_and_abort(
    s3_store: S3ObjectStore, s3_endpoint: str
) -> None:
    client = _client(s3_endpoint)
    key = storage.tenant_export_key(uuid.uuid4(), uuid.uuid4())
    data = (b"synthetic archive bytes " * 400_000)[: storage.MULTIPART_PART_BYTES + 12_345]
    writer = s3_store.open_writer(key, "application/zip", lifecycle=storage.LIFECYCLE_TENANT_EXPORT)
    for i in range(0, len(data), 1_000_000):
        writer.write(data[i : i + 1_000_000])
    assert s3_store.head(key) is None, "nothing is visible before the upload completes"
    writer.close()
    assert b"".join(s3_store.iter_chunks(key)) == data
    tags = client.get_object_tagging(Bucket=BUCKET, Key=key)["TagSet"]
    assert tags == [{"Key": "sos-lifecycle", "Value": "tenant-export-2d"}]
    s3_store.delete(key)

    aborted = storage.tenant_export_key(uuid.uuid4(), uuid.uuid4())
    writer = s3_store.open_writer(aborted, "application/zip")
    writer.write(data)
    writer.abort()
    assert s3_store.head(aborted) is None
    with pytest.raises(ValueError, match="closed"):
        writer.write(b"more")
