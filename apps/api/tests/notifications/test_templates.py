"""Bilingual notification templates (FR-NOT-001, CLAUDE.md §6.13 config in versioned files)."""

from __future__ import annotations

from typing import Any

import pytest

from app.notifications import templates as t

REQUIRED = {
    "change_request.submitted",
    "change_request.approved",
    "change_request.rejected",
    "dq.run.completed",
    "import.validated",
    "import.committed",
    "document.quarantined",
    "breakglass.requested",
    "breakglass.approved",
    "breakglass.expired",
    "announcement.new",
}
NUMERIC = {"blockers", "warnings", "valid_rows", "error_rows", "rows", "minutes", "low_confidence_rows"}


def _sample(template: t.Template, number: int = 2) -> dict[str, Any]:
    return {
        p: number if p in NUMERIC else ("support_request" if p == "reason_code" else "id-1")
        for p in template.params
    }


def test_FR_NOT_001_required_templates_exist_in_english_and_telugu() -> None:
    catalog = t.catalog()
    assert set(catalog) >= REQUIRED
    for template in catalog.values():
        assert set(template.messages) == {"en", "te"}


@pytest.mark.parametrize("key", sorted(t.catalog()))
@pytest.mark.parametrize("number", [0, 1, 7])
def test_FR_NOT_001_every_template_renders_in_both_languages(key: str, number: int) -> None:
    template = t.get(key)
    for lang in ("en", "te"):
        msg = t.render(key, _sample(template, number), lang)
        assert msg.title
        assert msg.body
        assert "{" not in msg.title + msg.body
        assert "#" not in msg.body
        assert "  " not in msg.body
        assert " ." not in msg.body
    te = t.render(key, _sample(template, number), "te")
    assert any("ఀ" <= ch <= "౿" for ch in te.title + te.body), "Telugu script"


def test_FR_NOT_001_plural_and_select_forms() -> None:
    zero = t.render("dq.run.completed", {"run_id": "r", "blockers": 0, "warnings": 1}, "en")
    assert zero.body == ("The data check found no problems that block submission and 1 warning.")
    many = t.render("import.committed", {"import_id": "i", "rows": 12}, "en")
    assert many.body == "12 rows were added to the student records."
    one = t.render("import.committed", {"import_id": "i", "rows": 1}, "te")
    assert one.body.startswith("1 వరుస ")
    legal = t.render(
        "breakglass.requested",
        {"grant_id": "g", "minutes": 60, "reason_code": "legal_obligation"},
        "en",
    )
    assert "because the law requires it" in legal.body
    unknown = t.render(
        "breakglass.requested", {"grant_id": "g", "minutes": 60, "reason_code": "zzz"}, "en"
    )
    assert "60 minutes. Review" in unknown.body


def test_FR_NOT_001_params_must_match_declared_placeholders() -> None:
    template = t.get("import.committed")
    t.check_params(template, {"import_id": "i", "rows": 3})
    with pytest.raises(t.TemplateError, match="missing"):
        t.check_params(template, {"import_id": "i"})
    with pytest.raises(t.TemplateError, match="undeclared"):
        t.check_params(template, {"import_id": "i", "rows": 3, "student_id": "s"})
    with pytest.raises(t.TemplateError):
        t.get("nope.nothing")
    with pytest.raises(t.TemplateError):
        t.render("import.committed", {"import_id": "i", "rows": "many"}, "en")


def test_FR_NOT_001_template_parser_rejects_malformed_messages() -> None:
    for bad in ("{", "}", "{count, plural, one {x}}", "{Bad}", "{n, date, short}"):
        with pytest.raises(t.TemplateError):
            t.placeholders(bad)
    assert t.placeholders("{a} {b, select, x {{c}} other {}}") == {"a", "b", "c"}


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "en"),
        ("", "en"),
        ("te", "te"),
        ("te-IN,te;q=0.9,en;q=0.8", "te"),
        ("en-IN,en;q=0.9,te;q=0.8", "en"),
        ("hi-IN, te;q=0.5", "te"),
        ("fr", "en"),
        ("en;q=0.2, te;q=0.7", "te"),
        ("te;q=abc", "en"),
    ],
)
def test_FR_NOT_001_accept_language_negotiation(header: str | None, expected: str) -> None:
    assert t.negotiate_language(header) == expected


def test_FR_ADM_002_read_retention_is_ninety_days() -> None:
    assert t.read_retention_days() == 90
