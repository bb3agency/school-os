"""Privacy registries and templates stay complete and in step with the docs (R3-1A; ADR-0048
decisions 1, 4 and 6; FR-DPDP-001, FR-DPDP-008, FR-DPDP-017; NFR-PRV-007)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from sqlalchemy import Engine, text

from app.privacy import config as cfg
from app.privacy import service

ROOT = Path(__file__).resolve().parents[4]
API = ROOT / "apps" / "api"
DOCS = ROOT / "docs"
TENANT_SCHEMAS = ("core", "sis", "kb", "audit", "ops", "fin", "msg")


def _purge_order() -> list[str]:
    data = yaml.safe_load((API / "app" / "tenancy" / "offboarding.yaml").read_text("utf-8"))
    order: list[str] = data["purge_order"]
    return order


def test_NFR_PRV_007_every_purge_module_is_a_contributor_or_declared() -> None:
    registry = cfg.subjects_registry()
    listed = set(registry.contributors) | set(registry.no_personal_data)
    missing = [m for m in _purge_order() if m not in listed]
    assert missing == [], f"add these modules to app/privacy/subjects.yaml: {missing}"
    stale = sorted(listed - set(_purge_order()))
    assert stale == [], f"subjects.yaml lists modules that own no tenant tables: {stale}"


def test_FR_DPDP_008_every_contributor_resolves_and_owns_its_classes() -> None:
    classes = cfg.record_classes().classes
    resolved = service.contributors()
    assert {c.module for c in resolved} == set(cfg.subjects_registry().contributors)
    for contributor in resolved:
        for key in contributor.classes:
            assert classes[key].owner == contributor.module
        erasable = [k for k in contributor.classes if classes[k].erasure == "allowed"]
        if erasable:
            assert contributor.eraser is not None, contributor.module


def _section_13() -> dict[str, str]:
    """docs/05 §13 first cell -> Default cell."""
    lines = (DOCS / "05-data-model.md").read_text("utf-8").split("\n")
    start = next(i for i, ln in enumerate(lines) if ln.startswith("## 13."))
    end = next(i for i, ln in enumerate(lines) if i > start and ln.startswith("## 14."))
    rows: dict[str, str] = {}
    for ln in lines[start:end]:
        if not ln.startswith("| ") or ln.startswith(("| Data |", "|---")):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        rows[cells[0]] = cells[1]
    return rows


def test_FR_DPDP_017_retention_matches_docs_05_section_13() -> None:
    rows = _section_13()
    for key, cls in cfg.record_classes().classes.items():
        matches = [d for d in rows if d == cls.docs_row] or [d for d in rows if cls.docs_row in d]
        assert len(matches) == 1, f"{key}: docs_row {cls.docs_row!r} matches {matches}"
        assert rows[matches[0]] == cls.retention, key


@pytest.mark.db
def test_FR_DPDP_017_every_tenant_table_is_in_exactly_one_class(admin_engine: Engine) -> None:
    with admin_engine.connect() as c:
        tables = {
            f"{r.schema}.{r.name}"
            for r in c.execute(
                text(
                    """
                    SELECT n.nspname AS schema, c.relname AS name
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = ANY(:s) AND c.relkind IN ('r', 'p')
                      AND NOT c.relispartition
                      AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid = c.oid
                                  AND a.attname = 'tenant_id' AND NOT a.attisdropped)
                    """
                ),
                {"s": list(TENANT_SCHEMAS)},
            )
        }
    classified = [t for c in cfg.record_classes().classes.values() for t in c.tables]
    assert len(classified) == len(set(classified))
    assert sorted(tables - set(classified)) == []
    assert sorted(set(classified) - tables) == []


def _strip(markdown: str) -> str:
    out = re.sub(r"[*_`>|#-]", " ", markdown)
    return re.sub(r"\s+", " ", out).strip()


@pytest.mark.parametrize("key", cfg.TEMPLATE_KEYS)
def test_FR_DPDP_001_notice_templates_match_docs_legal(key: cfg.TemplateKey) -> None:
    template = cfg.notice_template(key)
    source = (ROOT / template.source).read_text("utf-8")
    assert f"| template_version | {template.template_version} (" in source
    body = source.split("\n---\n", 1)[1]
    assert _strip(body) == _strip(template.body_markdown)
    assert "Not legal advice" in source


def test_FR_DPDP_001_letter_templates_match_docs_legal() -> None:
    letters = cfg.letter_templates()
    source = (ROOT / letters.source).read_text("utf-8")
    assert f"| template_version | {letters.template_version} (" in source
    flat = _strip(source)
    for letter in letters.letters.values():
        assert _strip(letter.body) in flat
