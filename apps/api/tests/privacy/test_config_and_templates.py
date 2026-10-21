"""Consent and breach configuration, printed forms and the breach templates' app copy
(R3-1B; ADR-0049, ADR-0050; FR-DPDP-020, FR-DPDP-023, FR-DPDP-036). No database."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.privacy import breach_config, consents_config, consents_templates
from app.privacy.consents_templates import FormPurpose, FormStudent

REPO = Path(__file__).resolve().parents[4]
RUNBOOK = REPO / "docs" / "legal" / "breach-runbook.md"


def _plain(text: str) -> str:
    """Markdown and YAML folding removed: quote marks, bold, line breaks and spaces."""
    text = re.sub(r"(?m)^>\s?", "", text)
    text = text.replace("**", "")
    return " ".join(text.split())


def test_FR_DPDP_020_consent_config_loads_with_three_suggestions() -> None:
    cfg = consents_config.load_config()
    assert set(cfg.suggestions) == {
        "photos_school_media",
        "name_results_public",
        "share_outside_organiser",
    }
    for s in cfg.suggestions.values():
        assert all(word in s.description for word in ("What", "Why", "Who", "how long"))


def test_FR_DPDP_020_config_refuses_bad_keys_and_missing_texts() -> None:
    raw = {
        "version": 1,
        "form_layout_version": "x",
        "note_max_length": 500,
        "earliest_decision": "2026-04-01",
        "max_forms_per_print": 10,
        "max_active_purposes": 5,
        "suggestions": {"Bad Key": {"label_en": "ab", "description": "0123456789"}},
        "form": {"en": {}, "te": {}},
    }
    with pytest.raises(ValueError, match="suggestion keys"):
        consents_config.parse_config(raw)


def test_FR_DPDP_023_form_prints_both_choices_side_by_side_and_masks_aadhaar() -> None:
    cfg = consents_config.load_config()
    page = consents_templates.render_forms(
        cfg,
        language="en",
        school="Synthetic Model School",
        students=[
            FormStudent(name="Synthetica 234567890124", admission_no="A1", class_section="IX A")
        ],
        purposes=[FormPurpose(label="Photos", description="Photos at events.", form_version=2)],
    )
    assert page.count("I agree") == 1
    assert page.count("I do not agree") == 1
    assert "do not depend on your answers" in page
    assert "234567890124" not in page
    assert "<script" not in page
    assert "Form version: 2" in page


def test_FR_DPDP_036_breach_templates_match_the_runbook() -> None:
    cfg = breach_config.load_config()
    runbook = RUNBOOK.read_text("utf-8")
    assert f"| template_version | {cfg.template_version} |" in runbook
    plain = _plain(runbook)
    for key in ("family_notice", "board_outline"):
        template = cfg.templates[key]
        body = _plain(template.body)
        assert body in plain, key
    assert _plain(cfg.templates["family_notice"].title).replace('"', "") in plain.replace("**", "")


def test_FR_DPDP_034_clocks_are_the_documented_ones() -> None:
    clocks = breach_config.load_config().clocks
    assert (clocks.board_notice_hours, clocks.board_report_hours) == (24, 72)
    assert (clocks.reminders.board_24h, clocks.reminders.report_60h) == (24, 60)


def test_FR_DPDP_036_unset_placeholders_print_not_set() -> None:
    assert breach_config.fill("Call {{privacy_contact_phone}}", {}) == "Call [not set]"
    assert breach_config.fill("Hi {{name}}", {"name": "Office"}) == "Hi Office"
