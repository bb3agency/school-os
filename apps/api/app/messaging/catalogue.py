"""The WhatsApp message catalogue (``templates.yaml``; ADR-0043 decision 3, ADR-0044 decisions
5-6; FR-MSG-005, FR-MSG-006, FR-LANG-004, FR-LANG-005; PRV-026).

Pure module (no database or web). Loads and validates the packaged catalogue once:

- every template is category ``utility`` (anything else fails at load: marketing templates are
  never created) and has an English and a Telugu body with the same positional variables
  ``{{1}}..{{n}}``, in order, neither first nor last in the body (Meta's rule);
- variables are short typed strings with a maximum length (PRV-026: only what the purpose needs);
- buttons: only the dynamic URL to the parent link page (ADR-0046); no opt-out button and no
  "reply STOP" text (owner decision D32: utility messages only; an inbound STOP is still honoured,
  FR-MSG-010);
- ``reviewed`` per language (FR-LANG-005).

:func:`provider_name` gives the deterministic name a template has in every school's WABA
(``sos_<key>_v<version>_<lang>``) and :func:`build_components` the Meta component list used to
create it. :func:`render_text` fills a free-form reply (texts are sent only inside the service
window the parent opened).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from functools import lru_cache
from importlib import resources
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.languages import contains_telugu
from app.messaging.config import LANGUAGES, Language, OptinCategory

Purpose = Literal[
    "optin_confirm",
    "school_notice",
    "absence_alert",
    "fee_reminder",
    "fee_receipt",
    "optin_stopped",
]
TemplatePurpose = Literal[
    "optin_confirm", "school_notice", "absence_alert", "fee_reminder", "fee_receipt"
]
Button = Literal["url"]
TextKey = Literal["optin_stopped", "auto_reply", "auto_reply_no_phone"]

_VAR: Final = re.compile(r"\{\{(\d+)\}\}")
_NAMED: Final = re.compile(r"\{([a-z_]+)\}")
_KEY: Final = re.compile(r"^[a-z][a-z_]{2,30}$")
_NAME: Final = re.compile(r"^[a-z][a-z_]{1,30}$")
SLIP_KEYS: Final = frozenset(
    {
        "title",
        "for_parents_of",
        "categories_intro",
        "category_notices",
        "category_attendance",
        "category_fees",
        "step_scan",
        "step_send",
        "voluntary",
        "valid_until",
        "footer",
    }
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Variable(_Model):
    name: str = Field(pattern=_NAME.pattern)
    max_length: int = Field(ge=1, le=200)


class TemplateText(_Model):
    reviewed: bool
    body: str = Field(min_length=10, max_length=1024)
    url_label: str | None = Field(default=None, min_length=1, max_length=25)


class Template(_Model):
    version: int = Field(ge=1, le=99)
    purpose: TemplatePurpose
    category: Literal["utility"]
    optin_category: OptinCategory | None
    variables: tuple[Variable, ...] = Field(min_length=1, max_length=10)
    buttons: tuple[Button, ...] = ()
    en: TemplateText
    te: TemplateText

    def text(self, language: Language) -> TemplateText:
        return self.en if language == "en" else self.te

    @model_validator(mode="after")
    def _consistent(self) -> Template:
        names = [v.name for v in self.variables]
        if len(set(names)) != len(names):
            raise ValueError("variable names repeat")
        if len(set(self.buttons)) != len(self.buttons):
            raise ValueError("buttons repeat")
        wanted = set(range(1, len(self.variables) + 1))
        for language in LANGUAGES:
            text = self.text(language)
            used = [int(n) for n in _VAR.findall(text.body)]
            if set(used) != wanted or len(used) != len(wanted):
                raise ValueError(f"{language} body must use {{{{1}}}}..{{{{n}}}} once each")
            stripped = text.body.strip()
            if _VAR.match(stripped) or re.search(r"\{\{\d+\}\}[.!?]?$", stripped):
                raise ValueError(f"{language} body may not start or end with a variable")
            if ("url" in self.buttons) != (text.url_label is not None):
                raise ValueError(f"{language} url_label exactly when a url button is listed")
            if re.search(r"\bSTOP\b|ఆపు", text.body):
                raise ValueError(f"{language} body may not ask parents to reply STOP (D32)")
        if contains_telugu(self.en.body) or not contains_telugu(self.te.body):
            raise ValueError("the en body is English and the te body Telugu")
        return self


class Text(_Model):
    reviewed: bool
    body: str = Field(min_length=10, max_length=1024)


class TextEntry(_Model):
    en: Text
    te: Text

    def text(self, language: Language) -> Text:
        return self.en if language == "en" else self.te


class SlipText(_Model):
    model_config = ConfigDict(extra="allow", frozen=True)
    reviewed: bool

    def get(self, key: str) -> str:
        value = (self.model_extra or {}).get(key)
        if not isinstance(value, str):
            raise KeyError(key)
        return value


class Catalogue(_Model):
    version: int = Field(ge=1)
    templates: dict[str, Template]
    texts: dict[TextKey, TextEntry]
    slip: dict[Language, SlipText]

    @model_validator(mode="after")
    def _complete(self) -> Catalogue:
        purposes = [t.purpose for t in self.templates.values()]
        if sorted(purposes) != sorted(set(purposes)):
            raise ValueError("one template per purpose")
        for key, template in self.templates.items():
            if not _KEY.fullmatch(key) or key != template.purpose:
                raise ValueError(f"template key {key!r} must equal its purpose")
        for entry in self.texts.values():
            for language in LANGUAGES:
                names = set(_NAMED.findall(entry.text(language).body))
                if not names <= {"school_name", "office_phone"}:
                    raise ValueError("texts may only use {school_name} and {office_phone}")
        for language in LANGUAGES:
            extra = set((self.slip[language].model_extra or {}).keys())
            if extra != SLIP_KEYS:
                raise ValueError(f"slip.{language} must have exactly {sorted(SLIP_KEYS)}")
        return self


@lru_cache(maxsize=1)
def catalogue() -> Catalogue:
    text = resources.files("app.messaging").joinpath("templates.yaml").read_text("utf-8")
    return Catalogue.model_validate(yaml.safe_load(text))


def template(key: str) -> Template:
    found = catalogue().templates.get(key)
    if found is None:
        raise KeyError(f"unknown template {key!r}")
    return found


def provider_name(key: str, version: int, language: Language) -> str:
    """The template's name in every school's WABA (FR-MSG-006)."""
    return f"sos_{key}_v{version}_{language}"


def meta_language(language: Language) -> str:
    """Meta's language code for a catalogue language."""
    return {"en": "en", "te": "te"}[language]


def _example(var: Variable) -> str:
    return {
        "school_name": "Synthetic Model School",
        "notice_title": "School closed on Monday",
        "child_first_name": "Ravi",
        "date": "14/10/2026",
        "due_date": "20/10/2026",
        "amount": "2,500.00",
        "receipt_number": "RCT/2026-27/000123",
    }.get(var.name, "Example")


def build_components(key: str, language: Language, public_base_url: str | None) -> list[Any]:
    """Meta's ``components`` for creating the template (body with examples, buttons). A URL
    button needs ``public_base_url`` (``SOS_PUBLIC_BASE_URL``); ``ValueError`` without it."""
    item = template(key)
    text = item.text(language)
    components: list[Any] = [
        {
            "type": "BODY",
            "text": text.body,
            "example": {"body_text": [[_example(v) for v in item.variables]]},
        }
    ]
    buttons: list[dict[str, Any]] = []
    for button in item.buttons:
        if button == "url":
            if not public_base_url:
                raise ValueError("a URL button needs SOS_PUBLIC_BASE_URL")
            base = public_base_url.rstrip("/")
            buttons.append(
                {
                    "type": "URL",
                    "text": text.url_label,
                    "url": f"{base}/p/{{{{1}}}}",
                    "example": [f"{base}/p/EXAMPLE"],
                }
            )
        else:
            raise ValueError(f"unknown button {button!r}")  # pragma: no cover - Literal
    if buttons:
        components.append({"type": "BUTTONS", "buttons": buttons})
    return components


def check_variables(key: str, values: Mapping[str, str]) -> list[str]:
    """The template's variables in order, each checked against its maximum length."""
    item = template(key)
    out: list[str] = []
    for var in item.variables:
        value = values.get(var.name)
        if value is None or not value.strip():
            raise ValueError(f"variable {var.name} is missing")
        clean = " ".join(value.split())
        if len(clean) > var.max_length:
            clean = clean[: var.max_length - 1].rstrip() + "…"
        out.append(clean)
    return out


def render_text(key: TextKey, language: Language, **values: str) -> str:
    """A free-form reply in ``language`` (placeholders ``{school_name}``, ``{office_phone}``)."""
    body = catalogue().texts[key].text(language).body
    return _NAMED.sub(lambda m: values.get(m.group(1), ""), body)


def unreviewed(language: Language) -> list[str]:
    """Catalogue entries in ``language`` not yet signed off by a fluent reviewer
    (FR-LANG-005)."""
    cat = catalogue()
    out = [
        f"messaging.template.{k}" for k, t in cat.templates.items() if not t.text(language).reviewed
    ]
    out += [f"messaging.text.{k}" for k, e in cat.texts.items() if not e.text(language).reviewed]
    if not cat.slip[language].reviewed:
        out.append("messaging.slip")
    return sorted(out)


__all__ = [
    "SLIP_KEYS",
    "Button",
    "Catalogue",
    "Purpose",
    "Template",
    "TemplatePurpose",
    "TextKey",
    "build_components",
    "catalogue",
    "check_variables",
    "meta_language",
    "provider_name",
    "render_text",
    "template",
    "unreviewed",
]
