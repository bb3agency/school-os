"""Certificate of deletion: content and bilingual A4 page (FR-PLT-005; docs/16 §5.5; ADR-0029).

Pure: no database, no storage. :func:`build_content` turns an offboarding run and the school's
deployment row into the certificate content (a JSON object: IDs, dates, codes and counts only;
no student or staff personal data), :func:`content_sha256` hashes its canonical JSON, and
:func:`render_html` prints it as one escaped A4 page with English and Telugu labels for
:mod:`app.core.pdf`. Labels and texts come from ``billing.yaml`` → ``offboarding.certificate``;
the Telugu wording is pending review (docs/16 §19).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import uuid
from collections.abc import Mapping
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from app.core.pdf import FONT_FAMILY, FONT_URL
from app.platform.common import config

CATEGORY_ORDER: Final = (
    "students",
    "changes",
    "dq",
    "extraction",
    "imports",
    "documents",
    "knowledge",
    "exports",
    "notifications",
    "breakglass",
    "ops",
    "identity",
    "tenancy",
)


class Bilingual(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    en: str = Field(min_length=1, max_length=300)
    te: str = Field(min_length=1, max_length=300)


class CertificateText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    intro: Bilingual
    deleted: Bilingual
    retained: Bilingual
    pending: Bilingual
    staff_sign_in_pending: Bilingual
    footer: Bilingual


class CertificateConfig(BaseModel):
    """``billing.yaml`` → ``offboarding.certificate``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    template_version: str = Field(pattern=r"^v[0-9]{1,3}$")
    object_prefix: str = Field(pattern=r"^platform/[a-z0-9][a-z0-9_-]*/$")
    download_url_ttl_s: int = Field(ge=30, le=300)
    telugu_review: str = Field(pattern=r"^(pending|done)$")
    title: Bilingual
    categories: dict[str, Bilingual]
    text: CertificateText


class OffboardingConfig(BaseModel):
    """``billing.yaml`` → ``offboarding``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    deletion_deadline_days: int = Field(ge=1, le=30)
    due_soon_days: int = Field(ge=1, le=29)
    lease_seconds: int = Field(ge=60, le=3600)
    audit_chain_retention_days: int = Field(ge=365, le=3650)
    audit_archive_retention_days: int = Field(ge=365, le=3650)
    shared_backup_max_age_days: int = Field(ge=1, le=3650)
    certificate: CertificateConfig


def offboarding_config() -> OffboardingConfig:
    return OffboardingConfig.model_validate(config()["offboarding"])


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat(timespec="seconds")
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value)


def _id(value: Any) -> str | None:
    return str(value) if value is not None else None


def build_content(
    run: Mapping[str, Any],
    deployment: Mapping[str, Any],
    *,
    certificate_id: uuid.UUID,
    issued_at: dt.datetime,
    cfg: OffboardingConfig,
) -> dict[str, Any]:
    """The certificate content: counts per category, dates, operator IDs. No personal data."""
    shared = run["tier"] == "shared"
    deleted_at: dt.datetime = run["data_deleted_at"] or run["teardown_confirmed_at"] or issued_at
    rows: Mapping[str, int] = run["inventory"] or {}
    if shared:
        deleted = [{"category": n, "count": int(rows.get(n, 0))} for n in CATEGORY_ORDER]
        deleted.append({"category": "files", "count": int(run["objects_before"] or 0)})
        deleted.append({"category": "keys", "count": int(run["keys_destroyed"] or 0)})
    else:  # the host (database, files, backups, key) is destroyed as a whole
        deleted = [{"category": "dedicated_host", "count": 1}]
    retained: list[dict[str, Any]] = [
        {"category": "billing_records", "until": "legal_retention_8_years"},
    ]
    if shared:
        retained[:0] = [
            {
                "category": "school_audit_log",
                "delete_after": _iso(
                    (issued_at + dt.timedelta(days=cfg.audit_chain_retention_days)).date()
                ),
            },
            {
                "category": "audit_archives",
                "expire_by": _iso(
                    (deleted_at + dt.timedelta(days=cfg.audit_archive_retention_days)).date()
                ),
            },
            {
                "category": "backups",
                "expire_by": _iso(
                    (deleted_at + dt.timedelta(days=cfg.shared_backup_max_age_days)).date()
                ),
            },
        ]
    backups: dict[str, Any] = (
        {"method": "expire", "expire_by": retained[2]["expire_by"]}
        if shared
        else {
            "method": "crypto_shredded",
            "kms_deletion_reference": run["kms_deletion_reference"],
            "host_teardown_reference": run["host_teardown_reference"],
        }
    )
    return {
        "certificate_id": str(certificate_id),
        "template_version": cfg.certificate.template_version,
        "tenant_id": str(run["tenant_id"]),
        "tenant_code": deployment["tenant_code"],
        "school_name": deployment["school_name"],
        "tier": run["tier"],
        "requested_by": _id(deployment["offboard_requested_by"]),
        "approved_by": _id(deployment["offboard_approved_by"]),
        "export_confirmed_by": _id(run["export_confirmed_by"]),
        "teardown_confirmed_by": _id(run["teardown_confirmed_by"]),
        "requested_at": _iso(deployment["offboard_requested_at"]),
        "approved_at": _iso(run["approved_at"]),
        "export_basis": run["export_basis"],
        "export_confirmed_at": _iso(run["export_confirmed_at"]),
        "deadline_at": _iso(run["deadline_at"]),
        "deletion_started_at": _iso(run["deletion_started_at"]),
        "data_deleted_at": _iso(deleted_at),
        "keys_destroyed_at": _iso(run["keys_destroyed_at"] or run["teardown_confirmed_at"]),
        "issued_at": _iso(issued_at),
        "deleted": deleted,
        "profiles_cleared": int(run["profiles_cleared"] or 0),
        "retained": retained,
        "backups": backups,
        "pending": [{"category": "staff_sign_in_accounts", "reason": "identity_rework"}],
    }


def content_sha256(content: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON (sorted keys, no spaces, UTF-8)."""
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _label(cfg: CertificateConfig, category: str) -> str:
    label = cfg.categories.get(category)
    if label is None:
        return _e(category)
    return f'{_e(label.en)}<br><span class="te">{_e(label.te)}</span>'


def _bi(text: Bilingual) -> str:
    return f'{_e(text.en)}<br><span class="te">{_e(text.te)}</span>'


_CSS = f"""
@font-face {{ font-family: "{FONT_FAMILY}"; src: url("{FONT_URL}"); }}
@page {{ size: A4; margin: 16mm 14mm; }}
body {{ font-family: "{FONT_FAMILY}", sans-serif; font-size: 10pt; color: #111; }}
h1 {{ font-size: 16pt; margin: 0 0 4mm; }}
.te {{ font-family: "{FONT_FAMILY}", sans-serif; color: #333; }}
table {{ border-collapse: collapse; width: 100%; margin: 3mm 0; }}
th, td {{ border: 0.3mm solid #999; padding: 1.5mm 2mm; text-align: left; vertical-align: top; }}
th {{ background: #f0f0f0; width: 45%; }}
td.num {{ text-align: right; }}
.hash {{ font-family: monospace; font-size: 8pt; word-break: break-all; }}
footer {{ margin-top: 6mm; font-size: 8.5pt; color: #333; }}
"""


def render_html(content: Mapping[str, Any], sha256: str, cfg: CertificateConfig) -> str:
    """One A4 page (two when long); every value is escaped; English and Telugu labels."""
    facts = [
        ("School", content["school_name"]),
        ("School code", content["tenant_code"]),
        ("School ID", content["tenant_id"]),
        ("Tier", content["tier"]),
        ("Offboarding requested", content["requested_at"]),
        ("Approved (two operators)", content["approved_at"]),
        ("Export confirmed", f"{content['export_confirmed_at']} ({content['export_basis']})"),
        ("Data deleted", content["data_deleted_at"]),
        ("Keys destroyed", content["keys_destroyed_at"]),
        ("Certificate issued", content["issued_at"]),
        ("Certificate ID", content["certificate_id"]),
        ("Requested by (operator ID)", content["requested_by"]),
        ("Approved by (operator ID)", content["approved_by"]),
    ]
    fact_rows = "".join(f"<tr><th>{_e(k)}</th><td>{_e(v)}</td></tr>" for k, v in facts)
    deleted_rows = "".join(
        f'<tr><td>{_label(cfg, d["category"])}</td><td class="num">{int(d["count"])}</td></tr>'
        for d in content["deleted"]
    )
    retained_rows = "".join(
        f"<tr><td>{_label(cfg, r['category'])}</td>"
        f"<td>{_e(r.get('delete_after') or r.get('expire_by') or r.get('until'))}</td></tr>"
        for r in content["retained"]
    )
    backups = content["backups"]
    if backups["method"] == "crypto_shredded":
        retained_rows += (
            f"<tr><td>{_label(cfg, 'backups')}</td><td>crypto-shredded: "
            f"{_e(backups['kms_deletion_reference'])}; host "
            f"{_e(backups['host_teardown_reference'])}</td></tr>"
        )
    pending_rows = "".join(
        f"<tr><td>{_label(cfg, p['category'])}</td>"
        f"<td>{_bi(cfg.text.staff_sign_in_pending)}</td></tr>"
        for p in content["pending"]
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{_e(cfg.title.en)}</title><style>{_CSS}</style></head><body>"
        f"<h1>{_bi(cfg.title)}</h1>"
        f"<p>{_bi(cfg.text.intro)}</p>"
        f"<table>{fact_rows}</table>"
        f"<h2>{_bi(cfg.text.deleted)}</h2><table>{deleted_rows}</table>"
        f"<h2>{_bi(cfg.text.retained)}</h2><table>{retained_rows}</table>"
        f"<h2>{_bi(cfg.text.pending)}</h2><table>{pending_rows}</table>"
        f'<p>SHA-256: <span class="hash">{_e(sha256)}</span></p>'
        f"<footer>{_bi(cfg.text.footer)} · {_e(content['template_version'])}</footer>"
        "</body></html>"
    )


def pdf_filename(tenant_code: str) -> str:
    safe = "".join(c for c in tenant_code if c.isascii() and (c.isalnum() or c == "-"))
    return f"certificate-of-deletion-{safe or 'school'}.pdf"


__all__ = [
    "CertificateConfig",
    "OffboardingConfig",
    "build_content",
    "content_sha256",
    "offboarding_config",
    "pdf_filename",
    "render_html",
]
