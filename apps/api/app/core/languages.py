"""Which languages SchoolOS shows (ADR-0036).

English first: the product owner decided on 2026-09-30 to hide Telugu everywhere for now. The
Telugu code, catalogs and templates stay in place behind one switch, ``SOS_TELUGU_ENABLED``
(default false), so bringing Telugu back is a configuration change, not a rewrite. Every module
asks this module, never the setting directly.
"""

from __future__ import annotations

from typing import Literal

from app.core.config import Settings, get_settings

Language = Literal["en", "te"]

ENGLISH: Language = "en"
TELUGU: Language = "te"


def telugu_enabled(settings: Settings | None = None) -> bool:
    """True only when Telugu has been switched back on."""
    return (settings or get_settings()).telugu_enabled


def enabled_languages(settings: Settings | None = None) -> tuple[Language, ...]:
    """The languages shown to people, English first."""
    return (ENGLISH, TELUGU) if telugu_enabled(settings) else (ENGLISH,)


def output_language(requested: str | None, settings: Settings | None = None) -> Language:
    """The language to answer or render in: Telugu (also for code-mixed ``mixed``) only when
    it is switched on, otherwise English."""
    if requested in (TELUGU, "mixed") and telugu_enabled(settings):
        return TELUGU
    return ENGLISH


def telugu_text(value: str | None, settings: Settings | None = None) -> str | None:
    """A Telugu-only presentation value (a ``*_te`` label, a Telugu heading): ``value`` while
    Telugu is switched on, ``None`` while it is hidden. Stored data is not passed through here,
    only output that exists just to show Telugu."""
    return value if telugu_enabled(settings) else None


def contains_telugu(text: str) -> bool:
    """True if ``text`` has any character of the Telugu Unicode block (U+0C00-U+0C7F)."""
    return any(_TELUGU_FIRST <= ch <= _TELUGU_LAST for ch in text)


_TELUGU_FIRST = "ఀ"
_TELUGU_LAST = "౿"
