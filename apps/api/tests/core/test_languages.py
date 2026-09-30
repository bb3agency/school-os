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


def test_telugu_text_is_hidden_by_default_and_kept_when_switched_on() -> None:
    off = Settings(env=Environment.LOCAL)
    on = Settings(env=Environment.LOCAL, telugu_enabled=True)
    assert languages.telugu_text("బదిలీ సర్టిఫికేట్", off) is None
    assert languages.telugu_text("బదిలీ సర్టిఫికేట్", on) == "బదిలీ సర్టిఫికేట్"
    assert languages.telugu_text(None, on) is None


def test_contains_telugu_spots_any_character_of_the_block() -> None:
    assert languages.contains_telugu("Name: రాము")
    assert languages.contains_telugu("ఀ")
    assert languages.contains_telugu("౿")
    assert not languages.contains_telugu("Transfer certificate, 2026-27")
    assert not languages.contains_telugu("௿ಀ")  # Tamil / Kannada neighbours
    assert not languages.contains_telugu("")


def test_telugu_on_fixture_switches_telugu_on_for_one_test(telugu_on: None) -> None:
    assert languages.telugu_enabled() is True
    assert languages.enabled_languages() == ("en", "te")
    assert languages.output_language("te") == "te"


def test_telugu_is_off_without_the_fixture() -> None:
    assert languages.telugu_enabled() is False
    assert languages.output_language("te") == "en"


def test_output_language_keeps_telugu_when_switched_on() -> None:
    s = Settings(env=Environment.LOCAL, telugu_enabled=True)
    assert languages.output_language("te", s) == "te"
    assert languages.output_language("mixed", s) == "te"  # code-mixed questions (FR-KB-006)
    assert languages.output_language("en", s) == "en"
    assert languages.output_language("hi", s) == "en"
