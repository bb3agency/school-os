"""Certificate of deletion template (FR-PLT-005; docs/16 §5.5; ADR-0029). Pure: no database.

Content carries IDs, dates, codes and counts only (no student or staff personal data); the hash
is over canonical JSON; the page escapes every value, uses only the bundled font, and shows
English and Telugu labels. The real Chromium render is checked by text extraction and skipped
only when Chromium cannot be launched.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

import pytest

from app.core.pdf import FONT_URL, ChromiumRenderer
from app.platform.deletion_certificate import (
    CATEGORY_ORDER,
    build_content,
    content_sha256,
    offboarding_config,
    pdf_filename,
    render_html,
)

T0 = dt.datetime(2026, 9, 1, 6, 0, tzinfo=dt.UTC)
PERSONAL_KEYS = ("name", "email", "phone", "dob", "address", "aadhaar", "guardian", "display")


def _run(tier: str = "shared", **overrides: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "tenant_id": uuid.UUID("0199aa00-0000-7000-8000-000000000001"),
        "tier": tier,
        "approved_at": T0,
        "deadline_at": T0 + dt.timedelta(days=30),
        "export_basis": "school_confirmed",
        "export_confirmed_by": uuid.uuid4(),
        "export_confirmed_at": T0 + dt.timedelta(days=2),
        "teardown_confirmed_by": None,
        "teardown_confirmed_at": None,
        "kms_deletion_reference": None,
        "host_teardown_reference": None,
        "inventory": {"students": 2412, "documents": 88, "identity": 41},
        "objects_before": 350,
        "profiles_cleared": 17,
        "keys_destroyed": 2,
        "deletion_started_at": T0 + dt.timedelta(days=2, hours=1),
        "data_deleted_at": T0 + dt.timedelta(days=2, hours=2),
        "keys_destroyed_at": T0 + dt.timedelta(days=2, hours=2, minutes=1),
    }
    run.update(overrides)
    return run


def _deployment(name: str = "Synthetic Public School") -> dict[str, Any]:
    return {
        "tenant_code": "s-synthetic",
        "school_name": name,
        "offboard_requested_by": uuid.uuid4(),
        "offboard_approved_by": uuid.uuid4(),
        "offboard_requested_at": T0 - dt.timedelta(days=1),
    }


def _content(**kw: Any) -> dict[str, Any]:
    cfg = offboarding_config()
    return build_content(
        _run(**kw),
        _deployment(),
        certificate_id=uuid.uuid4(),
        issued_at=T0 + dt.timedelta(days=2, hours=3),
        cfg=cfg,
    )


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [k for key, v in value.items() for k in (key, *_keys(v))]
    if isinstance(value, list):
        return [k for v in value for k in _keys(v)]
    return []


def test_FR_PLT_005_shared_content_counts_retained_and_pending() -> None:
    content = _content()
    deleted = {d["category"]: d["count"] for d in content["deleted"]}
    assert list(deleted)[: len(CATEGORY_ORDER)] == list(CATEGORY_ORDER)
    assert (deleted["students"], deleted["files"], deleted["keys"]) == (2412, 350, 2)
    assert deleted["dq"] == 0
    retained = {r["category"]: r for r in content["retained"]}
    # Audit chain kept a year after the certificate, then deleted (product owner 2026-09-29).
    assert retained["school_audit_log"]["delete_after"] == "2027-09-04"
    # Last backup that may hold the data expires 365 days after the deletion (docs/10 §9).
    assert retained["backups"]["expire_by"] == "2027-09-03"
    assert content["backups"] == {"method": "expire", "expire_by": "2027-09-03"}
    assert content["pending"] == [
        {"category": "staff_sign_in_accounts", "reason": "identity_rework"}
    ]
    assert not [k for k in _keys(content) if any(p in k for p in PERSONAL_KEYS if p != "name")]
    assert {k for k in _keys(content) if "name" in k} == {"school_name"}


def test_FR_PLT_005_dedicated_content_is_crypto_shredded() -> None:
    at = T0 + dt.timedelta(days=5)
    content = _content(
        tier="dedicated",
        inventory=None,
        objects_before=None,
        keys_destroyed=None,
        deletion_started_at=at,
        data_deleted_at=at,
        keys_destroyed_at=at,
        teardown_confirmed_by=uuid.uuid4(),
        teardown_confirmed_at=at,
        kms_deletion_reference="tf-run-101",
        host_teardown_reference="tf-run-102",
    )
    assert content["deleted"] == [{"category": "dedicated_host", "count": 1}]
    assert content["backups"]["method"] == "crypto_shredded"
    assert [r["category"] for r in content["retained"]] == ["billing_records"]


def test_FR_PLT_005_hash_is_over_canonical_json() -> None:
    content = _content()
    reordered = dict(reversed(list(content.items())))
    assert content_sha256(content) == content_sha256(reordered)
    changed = {**content, "deleted": [*content["deleted"][:-1], {"category": "keys", "count": 3}]}
    assert content_sha256(changed) != content_sha256(content)
    assert re.fullmatch(r"[0-9a-f]{64}", content_sha256(content))


def test_FR_PLT_005_page_escapes_values_and_is_bilingual() -> None:
    cfg = offboarding_config().certificate
    content = build_content(
        _run(),
        _deployment('Synthetic <script>alert("x")</script> School'),
        certificate_id=uuid.uuid4(),
        issued_at=T0,
        cfg=offboarding_config(),
    )
    page = render_html(content, "ab" * 32, cfg)
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert cfg.title.en in page
    assert cfg.title.te in page  # Telugu labels (wording pending review)
    assert cfg.categories["staff_sign_in_accounts"].te in page
    urls = set(re.findall(r"https?://[^\"')\s]+", page))
    assert urls == {FONT_URL}
    assert pdf_filename("s-synthetic") == "certificate-of-deletion-s-synthetic.pdf"
    assert offboarding_config().certificate.telugu_review == "pending"


def _chromium_available() -> bool:
    from playwright.sync_api import Error, sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(chromium_sandbox=False)
            browser.close()
    except Error:
        return False
    return True


@pytest.mark.chromium
def test_FR_PLT_005_real_render_text_has_school_counts_and_hash() -> None:
    if not _chromium_available():
        pytest.skip("headless Chromium is not installed on this machine")
    import pypdfium2 as pdfium

    content = _content()
    digest = content_sha256(content)
    out = ChromiumRenderer(sandbox=False, timeout_ms=60_000).render(
        render_html(content, digest, offboarding_config().certificate)
    )
    assert out.startswith(b"%PDF-")
    pdf = pdfium.PdfDocument(out)
    try:
        text = " ".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
    finally:
        pdf.close()
    text = re.sub(r"\s+", " ", text)
    for expected in ("Certificate of deletion", "Synthetic Public School", "2412", digest[:16]):
        assert expected in text, expected
