"""Origin of browser-facing presigned URLs in staging/prod (SEC-016, SEC-010, FR-DOC-001..004).

Browsers upload with a presigned POST straight to S3 and preview images from presigned GET URLs
(docs/07 §10). Both only work when the URL origin is exactly the one the files bucket's CORS rule
and the web app's CSP (``FILES_ORIGIN``: connect-src + img-src) expect. The API pins boto3 to
virtual-hosted, regional addressing so that origin is ``https://<bucket>.s3.<region>.amazonaws.com``;
these tests fail if a boto3/botocore upgrade (or a config change) silently moves it, and check that
the deploy files derive the same origin. No network: synthetic credentials, synthetic bucket names.
"""

from __future__ import annotations

import base64
import json
import re
import urllib.parse
from pathlib import Path

import pytest
import yaml

from app.core.config import Settings
from app.documents import storage

REPO = Path(__file__).resolve().parents[4]
COMPOSE = REPO / "deploy" / "dedicated" / "compose.yaml"
ENV_TEMPLATE = REPO / "deploy" / "dedicated" / ".env.template"
TERRAFORM = REPO / "infra" / "terraform" / "modules"

REGION = "ap-south-1"
BUCKET = "sos-prod-files-111122223333"
KMS = "arn:aws:kms:ap-south-1:111122223333:key/00000000-0000-0000-0000-000000000001"
TENANT = "0192a0de-0000-7000-8000-00000000a001"
KEY = f"t/{TENANT}/uploads/0192a0de-0000-7000-8000-0000000000f1/original.pdf"
PDF = "application/pdf"


@pytest.fixture(autouse=True)
def offline_aws_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Synthetic static credentials; no profile, config file or endpoint override can leak in."""
    for name in (
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
        "AWS_SESSION_TOKEN",
        "AWS_ENDPOINT_URL",
        "AWS_ENDPOINT_URL_S3",
        "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS",
        "AWS_USE_FIPS_ENDPOINT",
        "AWS_USE_DUALSTACK_ENDPOINT",
        "AWS_S3_US_EAST_1_REGIONAL_ENDPOINT",
        "AWS_S3_DISABLE_MULTIREGION_ACCESS_POINTS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIASYNTHETICEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "synthetic-secret-access-key-not-real")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-credentials"))


def aws_settings(bucket: str = BUCKET, region: str = REGION) -> Settings:
    """Staging/prod storage settings: real AWS, no endpoint overrides (docs/10 §11)."""
    return Settings().model_copy(
        update={
            "s3_endpoint_url": None,
            "s3_presign_endpoint_url": None,
            "s3_bucket_files": bucket,
            "aws_region": region,
            "s3_kms_key_id": KMS,
        }
    )


def origin(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def presigned_origins(settings: Settings) -> tuple[str, str]:
    store = storage.build_s3_store(settings)
    post = store.presigned_post(key=KEY, content_type=PDF, max_bytes=1024, expires_s=600)
    get_url, _ = store.presigned_get(key=KEY, content_type=PDF, filename="f.pdf", expires_s=300)
    return origin(post.url), origin(get_url)


def test_SEC_016_presigned_post_and_get_use_the_regional_virtual_hosted_origin() -> None:
    expected = f"https://{BUCKET}.s3.{REGION}.amazonaws.com"
    post_origin, get_origin = presigned_origins(aws_settings())
    # Not the legacy global host <bucket>.s3.amazonaws.com (it redirects outside us-east-1, which
    # breaks a cross-origin POST) and not path style (a different origin than CORS/CSP expect).
    assert post_origin == expected
    assert get_origin == expected


def test_SEC_016_the_pinned_origin_keeps_the_upload_policy_intact() -> None:
    store = storage.build_s3_store(aws_settings())
    post = store.presigned_post(key=KEY, content_type=PDF, max_bytes=1024, expires_s=600)
    assert urllib.parse.urlsplit(post.url).path == "/"
    policy = json.loads(base64.b64decode(post.fields["policy"]))
    conditions = policy["conditions"]
    assert {"key": KEY} in conditions
    assert {"Content-Type": PDF} in conditions
    assert ["content-length-range", 1, 1024] in conditions
    assert {"x-amz-server-side-encryption": "aws:kms"} in conditions
    assert {"x-amz-server-side-encryption-aws-kms-key-id": KMS} in conditions
    assert post.fields["x-amz-algorithm"] == "AWS4-HMAC-SHA256"
    with pytest.raises(ValueError, match=r"1\.\.600"):
        store.presigned_post(key=KEY, content_type=PDF, max_bytes=1024, expires_s=601)

    get_url, _ = store.presigned_get(key=KEY, content_type=PDF, filename="f.pdf", expires_s=300)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(get_url).query)
    assert urllib.parse.urlsplit(get_url).path == f"/{KEY}"
    assert query["X-Amz-Expires"] == ["300"]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert query["response-content-disposition"] == ['attachment; filename="f.pdf"']
    with pytest.raises(ValueError, match=r"1\.\.300"):
        store.presigned_get(key=KEY, content_type=PDF, filename="f.pdf", expires_s=301)


def test_SEC_016_local_endpoints_stay_path_style() -> None:
    settings = aws_settings().model_copy(
        update={
            "s3_endpoint_url": "http://s3:8333",
            "s3_presign_endpoint_url": "http://localhost:8333",
            "s3_bucket_files": "sos-local-files",
        }
    )
    store = storage.build_s3_store(settings)
    post = store.presigned_post(key=KEY, content_type=PDF, max_bytes=1024, expires_s=600)
    assert post.url == "http://localhost:8333/sos-local-files"


# --- deploy files derive the same origin ----------------------------------------------------------


def _host_env_template() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_TEMPLATE.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line)
        if m:
            env[m.group(1)] = m.group(2).strip().strip('"')
    return env


def test_SEC_010_dedicated_web_files_origin_matches_the_presigned_origin() -> None:
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    expr = str(doc["services"]["web"]["environment"]["FILES_ORIGIN"])
    host_env = _host_env_template()
    rendered = re.sub(r"\$\{([A-Z0-9_]+):\?\}", lambda m: host_env[m.group(1)], expr)
    assert "$" not in rendered, "FILES_ORIGIN uses only required host.env variables"
    bucket, region = host_env["SOS_S3_BUCKET_FILES"], host_env["AWS_REGION"]
    post_origin, get_origin = presigned_origins(aws_settings(bucket=bucket, region=region))
    assert rendered == post_origin == get_origin
    for service, spec in doc["services"].items():
        if service != "web":
            assert "FILES_ORIGIN" not in (spec.get("environment") or {}), service


def test_SEC_010_terraform_files_origin_uses_the_same_formula() -> None:
    outputs = (TERRAFORM / "s3_bucket" / "outputs.tf").read_text(encoding="utf-8")
    assert '"https://${var.name}.s3.${data.aws_region.current.region}.amazonaws.com"' in outputs
    shared = (TERRAFORM / "shared_platform" / "main.tf").read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s+FILES_ORIGIN\s+=\s+module\.s3\.files_browser_origin$", shared)
    assert re.search(r'files_upload_origins\s+=\s+\["https://\$\{var\.app_domain\}"\]', shared)
