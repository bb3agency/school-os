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
    assert s3_store.head(up["fields"]["key"]) is None
