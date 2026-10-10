"""APAAR consent configuration and the printed form, without a database (ADR-0039; FR-APC-004,
PRV-021, invariant 4)."""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from app.apaar import templates
from app.apaar.config import FORM_KEYS, load_config, parse_config
from app.core.redaction import verhoeff_check_digit


def _raw() -> dict[str, Any]:
    from importlib import resources

    raw: dict[str, Any] = yaml.safe_load(
        resources.files("app.apaar").joinpath("config.yaml").read_text("utf-8")
    )
    return raw


def test_FR_APC_004_config_has_both_languages_and_a_refusal_basis() -> None:
    cfg = load_config()
    assert set(cfg.form) == {"en", "te"}
    for texts in cfg.form.values():
        assert set(texts) == FORM_KEYS
    # Owner decision D3: the legal basis comes from a secondary source and is unverified.
    assert cfg.legal_basis.secondary_source is True
    assert cfg.legal_basis.verified is False
    assert "20 July 2026" in cfg.legal_basis.refusal


def test_FR_APC_004_config_rejects_missing_or_untranslated_text() -> None:
    raw = _raw()
    broken = copy.deepcopy(raw)
    del broken["form"]["te"]["refuse"]
    with pytest.raises(ValidationError):
        parse_config(broken)
    english = copy.deepcopy(raw)
    english["form"]["te"]["refuse"] = english["form"]["en"]["refuse"]
    with pytest.raises(ValidationError):
        parse_config(english)


def _student(**over: Any) -> templates.FormStudent:
    base: dict[str, Any] = {
        "name": "Synthetica <b>Bold</b> Rani",
        "admission_no": "AP/0001",
        "class_section": "IX-A",
        "dob": dt.date(2012, 3, 14),
        "pen": "21345678901",
    }
    base.update(over)
    return templates.FormStudent(**base)


def test_FR_APC_004_form_offers_give_and_refuse_with_details_escaped() -> None:
    page = templates.render_forms(
        load_config(), language="en", school="Synthetic Model School", students=[_student()]
    )
    assert "I GIVE consent" in page
    assert "I DO NOT GIVE consent" in page
    assert "You may refuse" in page
    assert "Synthetica &lt;b&gt;Bold&lt;/b&gt; Rani" in page
    assert "14/03/2012" in page
    assert "21345678901" in page
    assert "<script" not in page
    assert page.count("<article>") == 1


def test_FR_APC_004_telugu_form_and_one_page_per_student() -> None:
    page = templates.render_forms(
        load_config(), language="te", school="Synthetic", students=[_student(), _student()]
    )
    assert 'lang="te"' in page
    assert "నేను సమ్మతి ఇవ్వడం లేదు" in page
    assert page.count("<article>") == 2
    assert "Nirmala UI" in page


def test_invariant_4_form_masks_an_aadhaar_number_in_any_value() -> None:
    body = "45678901234"
    aadhaar = body + verhoeff_check_digit(body)
    page = templates.render_forms(
        load_config(),
        language="en",
        school=f"School {aadhaar}",
        students=[_student(name=f"Synthetica {aadhaar}", admission_no=aadhaar)],
    )
    assert aadhaar not in page


def test_FR_APC_004_csp_allows_only_the_two_styles() -> None:
    assert templates.STYLE_CSP.startswith("default-src 'none'; style-src 'sha256-")
    assert templates.STYLE_CSP.count("'sha256-") == 2
    assert "script-src" not in templates.STYLE_CSP
