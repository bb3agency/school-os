"""Bilingual explanation texts and correction routes (FR-DQ-001, FR-DQ-006, docs/02 §5-6)."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.textnorm import has_telugu
from app.dq.explanations import (
    ROUTE_CODES,
    ExplanationCatalog,
    Language,
    TemplateParamsError,
    UnknownExplanation,
    load_explanations,
    parse_catalog,
    placeholders_of,
)
from app.dq.matching import MatchClass

PRD = Path(__file__).resolve().parents[4] / "docs" / "02-PRD.md"


@pytest.fixture(scope="module")
def catalog() -> ExplanationCatalog:
    return load_explanations()


def _raw(catalog: ExplanationCatalog) -> dict[str, Any]:
    return copy.deepcopy(catalog.model_dump(mode="json"))


def _prd_section(title: str) -> str:
    text = PRD.read_text("utf-8")
    start = text.index(title)
    return text[start : text.index("\n## ", start + 1)]


@pytest.mark.parametrize("match_class", list(MatchClass))
def test_FR_DQ_006_every_match_class_has_bilingual_text(
    catalog: ExplanationCatalog, match_class: MatchClass
) -> None:
    entry = catalog.match_classes[match_class.explanation_code]
    assert entry.en.strip()
    assert has_telugu(entry.te)
    assert catalog.render(match_class.explanation_code, Language.TE) == entry.te


def test_FR_DQ_006_four_correction_routes_match_the_prd(catalog: ExplanationCatalog) -> None:
    assert set(catalog.routes) == set(ROUTE_CODES)
    section = _prd_section("## 5. Data-quality rules catalog")
    routes_line = next(line for line in section.splitlines() if "Suggested routes" in line)
    documented = set(re.findall(r'"([^"]+)"', routes_line))
    assert {catalog.routes[code].en.rstrip(".") for code in ROUTE_CODES} == documented
    for code in ROUTE_CODES:
        assert has_telugu(catalog.routes[code].te)


def test_FR_DQ_001_rule_texts_match_the_prd_catalog(catalog: ExplanationCatalog) -> None:
    section = _prd_section("## 5. Data-quality rules catalog")
    rows = re.findall(r'^\| (DQ-\d{3}) \|.*\| "([^"]+)" \|$', section, flags=re.MULTILINE)
    assert len(rows) == 15  # DQ-030 (ADR-0041)
    for rule_id, english in rows:
        assert catalog.rules[rule_id].en == english
        assert has_telugu(catalog.rules[rule_id].te)


def test_render_fills_placeholders_in_both_languages(catalog: ExplanationCatalog) -> None:
    texts = catalog.bilingual("DQ-007", n=17, c="IX")
    assert texts[Language.EN] == "Age 17 is unusual for class IX. Check DOB."
    assert "17" in texts[Language.TE]
    assert "IX" in texts[Language.TE]
    assert catalog.render("DQ-005", "en", profile="CISCE", field="Mother's name").startswith(
        "Required for CISCE"
    )


def test_render_rejects_missing_or_extra_parameters(catalog: ExplanationCatalog) -> None:
    with pytest.raises(TemplateParamsError):
        catalog.render("DQ-007", Language.EN, n=17)
    with pytest.raises(TemplateParamsError):
        catalog.render("NM-TYPO", Language.EN, name="anything")
    with pytest.raises(UnknownExplanation):
        catalog.render("NM-NOPE", Language.EN)
    with pytest.raises(ValueError, match="xx"):
        catalog.render("NM-TYPO", "xx")


def test_placeholders_must_be_plain_names() -> None:
    assert placeholders_of("Age {n} for {c}") == {"n", "c"}
    for bad in ("{n.__class__}", "{n!r}", "{n:>10}", "{0}", "{N}"):
        with pytest.raises(ValueError, match="placeholder"):
            placeholders_of(bad)


def test_telugu_text_must_be_telugu(catalog: ExplanationCatalog) -> None:
    raw = _raw(catalog)
    raw["match_classes"]["NM-TYPO"]["te"] = "Almost the same."
    with pytest.raises(ValidationError, match="Telugu"):
        parse_catalog(raw)


def test_languages_must_share_placeholders(catalog: ExplanationCatalog) -> None:
    raw = _raw(catalog)
    raw["rules"]["DQ-007"]["te"] = "వయస్సు {n} అసాధారణంగా ఉంది."
    with pytest.raises(ValidationError, match="same placeholders"):
        parse_catalog(raw)


def test_all_routes_are_required(catalog: ExplanationCatalog) -> None:
    raw = _raw(catalog)
    del raw["routes"]["ROUTE-UIDAI"]
    with pytest.raises(ValidationError, match="ROUTE-UIDAI"):
        parse_catalog(raw)


@pytest.mark.parametrize(
    ("section", "code"),
    [("match_classes", "TYPO"), ("routes", "ROUTE_X"), ("rules", "DQ-1")],
)
def test_codes_must_be_well_formed(catalog: ExplanationCatalog, section: str, code: str) -> None:
    raw = _raw(catalog)
    raw[section][code] = {"en": "Text.", "te": "పాఠం."}
    with pytest.raises(ValidationError, match="malformed"):
        parse_catalog(raw)


def test_lookup(catalog: ExplanationCatalog) -> None:
    assert catalog.has("NM-EXACT")
    assert catalog.has("ROUTE-BOARD")
    assert catalog.has("DQ-012")
    assert not catalog.has("DQ-999")
    assert catalog.get("ROUTE-UDISE").text(Language.EN).startswith("Update UDISE+")
