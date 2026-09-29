"""Admin configuration (FR-ADM-001, FR-ADM-002; invariant 13: thresholds in versioned files).

The retention defaults must equal what each module's own job uses when a school keeps the
default, and the bounds must never let a school keep working data longer than the bucket's
lifecycle backstop or shorten the audit log below 13 months (PRV-018).
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from app.admin.config import (
    CONFIG_PATH,
    RETENTION_PATH,
    AdminConfig,
    RetentionConfig,
    load_config,
    load_retention,
)
from app.exports import service as exports
from app.exports.config import load_config as exports_config
from app.imports import service as imports
from app.imports.config import import_config
from app.notifications import service as notifications
from app.notifications import templates


def test_FR_ADM_001_config_loads_and_link_is_valid_24_hours() -> None:
    cfg = load_config().tenant_export
    assert cfg.link_valid_hours == 24  # FR-ADM-001
    assert cfg.download_url_ttl_s <= 300  # docs/07 §10: presigned GETs at most 5 minutes
    assert cfg.task_soft_time_limit_s < cfg.task_time_limit_s
    assert cfg.readme_en.strip()
    assert cfg.readme_te.strip()


def test_FR_ADM_001_never_exported_matches_the_exports_module() -> None:
    """PRV-013/014: the Aadhaar-as-printed fields are never exported, in any export."""
    assert set(load_config().tenant_export.never_exported) == set(exports_config().never_exported)
    assert {
        "aadhaar_name_as_printed",
        "aadhaar_dob_as_printed",
        "aadhaar_gender_as_printed",
    } <= set(load_config().tenant_export.never_exported)


def test_FR_ADM_002_retention_defaults_match_each_job_and_configurable_ones_are_enforced() -> None:
    categories = load_retention().categories
    assert categories["import_raw_files"].default_days == import_config().raw_file_retention_days
    assert categories["exports"].default_days == exports_config().retention_days
    assert categories["notifications_read"].default_days == templates.read_retention_days()
    assert (
        categories["tenant_exports"].default_days * 24
        == load_config().tenant_export.link_valid_hours
    )
    # The category keys the jobs ask app.core.retention for.
    assert imports.RAW_FILE_RETENTION_CATEGORY in categories
    assert exports.RETENTION_CATEGORY in categories
    assert notifications.READ_RETENTION_CATEGORY in categories
    for key, spec in categories.items():
        if spec.configurable:
            assert spec.enforced_by, key


def test_FR_ADM_002_bounds_follow_the_legal_and_backstop_rules() -> None:
    categories = load_retention().categories
    # Working data may only be shortened: the bucket lifecycle rules delete at the default.
    for key in ("import_raw_files", "exports", "notifications_read"):
        assert categories[key].max_days == categories[key].default_days, key
    # PRV-018 / DPDP: audit and security logs at least 13 months, never configurable.
    audit_events = categories["audit_events"]
    assert not audit_events.configurable
    assert audit_events.min_days >= 395
    assert not categories["tenant_exports"].configurable


@pytest.mark.parametrize(
    "spec",
    [
        {
            "default_days": 10,
            "min_days": 20,
            "max_days": 30,
            "configurable": True,
            "enforced_by": "a.b",
        },
        {
            "default_days": 10,
            "min_days": 1,
            "max_days": 30,
            "configurable": False,
            "enforced_by": None,
        },
        {
            "default_days": 10,
            "min_days": 1,
            "max_days": 30,
            "configurable": True,
            "enforced_by": None,
        },
        {
            "default_days": 10,
            "min_days": 1,
            "max_days": 4000,
            "configurable": True,
            "enforced_by": "a.b",
        },
    ],
)
def test_FR_ADM_002_bad_retention_config_is_refused(spec: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RetentionConfig.model_validate({"version": 1, "categories": {"thing": spec}})


def test_FR_ADM_002_retention_file_is_the_one_shipped() -> None:
    raw = yaml.safe_load(RETENTION_PATH.read_text(encoding="utf-8"))
    assert RetentionConfig.model_validate(raw) == load_retention()
    raw_cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert AdminConfig.model_validate(raw_cfg) == load_config()


def test_FR_ADM_002_clamp_keeps_values_within_bounds() -> None:
    spec = load_retention().categories["import_raw_files"]
    assert spec.clamp(1) == spec.min_days
    assert spec.clamp(10_000) == spec.max_days
    assert spec.clamp(30) == 30
