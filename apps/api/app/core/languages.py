"""Which languages SchoolOS shows (ADR-0036).

English first: the product owner decided on 2026-09-30 to hide Telugu everywhere for now. The
Telugu code, catalogs and templates stay in place behind one switch, ``SOS_TELUGU_ENABLED``
(default false), so bringing Telugu back is a configuration change, not a rewrite. Every module
asks this module, never the setting directly.

Parent-facing output is the one exception (ADR-0044, owner decision D9): its language is the
school's ``parent_language`` (or a guardian's own choice), read through :func:`parent_language`,
whatever the staff switch says.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Literal

import yaml

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


def shown_texts(
    texts: Mapping[str, str], separator: str = " · ", settings: Settings | None = None
) -> str:
    """Per-language texts (``{"en": ..., "te": ...}``, e.g. a bilingual watermark) joined for
    the languages shown, English first: only the English text while Telugu is hidden."""
    return separator.join(texts[lang] for lang in enabled_languages(settings) if texts.get(lang))


PARENT_LANGUAGES: tuple[Language, ...] = (ENGLISH, TELUGU)


def _as_language(value: object) -> Language | None:
    if value == ENGLISH:
        return ENGLISH
    if value == TELUGU:
        return TELUGU
    return None


def parent_language(
    school: Mapping[str, object] | str | None, guardian: str | None = None
) -> Language:
    """The language of everything a parent receives (ADR-0044; FR-LANG-001..003): WhatsApp
    templates, public parent pages, printed parent slips and forms.

    Resolution order: the guardian's own choice (``sis.guardians.preferred_language``), then the
    school's ``parent_language`` setting (``school`` is ``core.tenants.settings`` or the setting's
    value), then English. Independent of ``SOS_TELUGU_ENABLED`` by design: with the switch off,
    staff screens, staff PDFs, notifications and AI answers stay English (ADR-0036) while a school
    may still write to parents in Telugu. Never use it for a staff surface."""
    chosen = _as_language(guardian)
    if chosen is not None:
        return chosen
    setting = school.get("parent_language") if isinstance(school, Mapping) else school
    return _as_language(setting) or ENGLISH


@lru_cache(maxsize=1)
def _parent_text_sources() -> dict[str, bool]:
    raw = yaml.safe_load(
        resources.files("app.core").joinpath("parent_texts.yaml").read_text("utf-8")
    )
    sources = raw.get("sources") if isinstance(raw, dict) else None
    if not isinstance(sources, dict) or not sources:
        raise ValueError("parent_texts.yaml needs a non-empty 'sources' mapping")
    out: dict[str, bool] = {}
    for key, spec in sources.items():
        reviewed = spec.get("reviewed") if isinstance(spec, dict) else None
        if not isinstance(key, str) or not isinstance(reviewed, bool):
            raise ValueError(f"parent_texts.yaml: {key!r} needs reviewed: true|false")
        out[key] = reviewed
    return out


def unreviewed_parent_texts(language: Language) -> list[str]:
    """Keys of the parent-facing Telugu texts not yet signed off by a fluent reviewer that a
    school writing to parents in ``language`` would use (FR-LANG-005); empty for English."""
    if language != TELUGU:
        return []
    return sorted(key for key, reviewed in _parent_text_sources().items() if not reviewed)


def contains_telugu(text: str) -> bool:
    """True if ``text`` has any character of the Telugu Unicode block (U+0C00-U+0C7F)."""
    return any(_TELUGU_FIRST <= ch <= _TELUGU_LAST for ch in text)


_TELUGU_FIRST = chr(0x0C00)
_TELUGU_LAST = chr(0x0C7F)
