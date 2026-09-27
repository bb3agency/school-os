"""The committed RDS CA bundle (docs/10 §6): present, certificates only (SEC-009, SEC-011)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BUNDLE = ROOT / "infra" / "certs" / "rds-global-bundle.pem"
BLOCK = re.compile(r"-----BEGIN ([A-Z0-9 ]+)-----\r?\n[A-Za-z0-9+/=\r\n]+?-----END \1-----")


def test_SEC_011_rds_ca_bundle_is_committed_for_image_builds() -> None:
    # apps/api/Dockerfile copies it; a clean checkout must build.
    assert BUNDLE.is_file()
    dockerfile = ROOT / "apps" / "api" / "Dockerfile"
    assert "infra/certs/rds-global-bundle.pem" in dockerfile.read_text(encoding="utf-8")


def test_SEC_009_rds_ca_bundle_holds_certificates_only() -> None:
    text = BUNDLE.read_text(encoding="ascii")
    kinds = [m.group(1) for m in BLOCK.finditer(text)]
    assert kinds, "no PEM blocks"
    assert set(kinds) == {"CERTIFICATE"}
    # Nothing outside the certificate blocks (no stray keys or text).
    assert BLOCK.sub("", text).strip() == ""
