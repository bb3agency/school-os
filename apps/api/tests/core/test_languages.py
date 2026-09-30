"""English first: Telugu is hidden unless switched on (ADR-0036, product owner 2026-09-30)."""

from __future__ import annotations

import pytest

from app.core import languages
from app.core.config import Environment, Settings


def test_telugu_is_off_by_default() -> None:
    assert Settings(env=Environment.LOCAL).telugu_enabled is False


def test_enabled_languages_is_english_only_by_default() -> None:
    s = Settings(env=Environment.LOCAL)
    assert languages.enabled_languages(s) == ("en",)
    assert languages.telugu_enabled(s) is False


def test_switch_brings_telugu_back() -> None:
    s = Settings(env=Environment.LOCAL, telugu_enabled=True)
    assert languages.enabled_languages(s) == ("en", "te")
    assert languages.telugu_enabled(s) is True


@pytest.mark.parametrize("requested", ["te", "mixed", "hi", "", None])
def test_output_language_falls_back_to_english_when_telugu_is_hidden(
    requested: str | None,
) -> None:
    assert languages.output_language(requested, Settings(env=Environment.LOCAL)) == "en"


def test_output_language_keeps_telugu_when_switched_on() -> None:
    s = Settings(env=Environment.LOCAL, telugu_enabled=True)
    assert languages.output_language("te", s) == "te"
    assert languages.output_language("mixed", s) == "te"  # code-mixed questions (FR-KB-006)
    assert languages.output_language("en", s) == "en"
    assert languages.output_language("hi", s) == "en"
