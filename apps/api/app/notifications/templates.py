"""Bilingual notification templates (FR-NOT-001): loading, validation and rendering.

``templates.yaml`` lives in the package. Messages use a small ICU MessageFormat subset:
``{name}``, ``{name, plural, =0 {...} one {...} other {...}}`` (``#`` is the number) and
``{name, select, code {...} other {...}}``. English and Telugu both use the CLDR plural
categories ``one`` and ``other``. Everything is validated when first loaded: both languages
exist, every placeholder is declared in ``params`` and both languages use the same placeholders.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any, Final, Literal

import yaml

Language = Literal["en", "te"]
LANGUAGES: Final[tuple[Language, ...]] = ("en", "te")
DEFAULT_LANGUAGE: Final[Language] = "en"
_KEY_RE: Final = re.compile(r"^[a-z_]+(\.[a-z_]+)+$")
_NAME_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SPACE_BEFORE_PUNCT: Final = re.compile(r"\s+([.,])")
_MULTI_SPACE: Final = re.compile(r" {2,}")


class TemplateError(ValueError):
    """A template is malformed, unknown, or rendered with missing/undeclared params."""


@dataclass(frozen=True, slots=True)
class Message:
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class Template:
    key: str
    params: frozenset[str]
    resource_type: str | None
    messages: Mapping[str, Message]


# --- tiny ICU subset -------------------------------------------------------------------------


def _closing(text: str, start: int) -> int:
    """Index of the ``}`` matching the ``{`` at ``start``."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise TemplateError("unbalanced braces")


def _branches(spec: str) -> dict[str, str]:
    """Parse ``sel {msg} sel {msg} ...`` into a mapping."""
    out: dict[str, str] = {}
    i = 0
    while i < len(spec):
        if spec[i].isspace():
            i += 1
            continue
        j = spec.find("{", i)
        if j < 0:
            raise TemplateError("branch without a message")
        selector = spec[i:j].strip()
        end = _closing(spec, j)
        if not selector or selector in out:
            raise TemplateError(f"bad or duplicate selector {selector!r}")
        out[selector] = spec[j + 1 : end]
        i = end + 1
    if "other" not in out:
        raise TemplateError("plural/select needs an 'other' branch")
    return out


def _split_arg(inner: str) -> tuple[str, str | None, str]:
    parts = inner.split(",", 2)
    name = parts[0].strip()
    if not _NAME_RE.match(name):
        raise TemplateError(f"bad placeholder {name!r}")
    if len(parts) == 1:
        return name, None, ""
    if len(parts) != 3:
        raise TemplateError(f"placeholder {name!r} needs a type and branches")
    kind = parts[1].strip()
    if kind not in ("plural", "select"):
        raise TemplateError(f"unsupported placeholder type {kind!r}")
    return name, kind, parts[2]


def placeholders(message: str) -> set[str]:
    """Every placeholder name used in ``message`` (including nested branches)."""
    names: set[str] = set()
    i = 0
    while i < len(message):
        if message[i] == "{":
            end = _closing(message, i)
            name, kind, rest = _split_arg(message[i + 1 : end])
            names.add(name)
            if kind is not None:
                for branch in _branches(rest).values():
                    names |= placeholders(branch)
            i = end + 1
        elif message[i] == "}":
            raise TemplateError("unbalanced braces")
        else:
            i += 1
    return names


def _plural_branch(branches: Mapping[str, str], value: Any) -> tuple[str, int]:
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise TemplateError("plural value must be a whole number") from exc
    exact = branches.get(f"={number}")
    if exact is not None:
        return exact, number
    category = "one" if number == 1 else "other"
    return branches.get(category, branches["other"]), number


def format_message(message: str, params: Mapping[str, Any]) -> str:
    out: list[str] = []
    i = 0
    while i < len(message):
        char = message[i]
        if char == "{":
            end = _closing(message, i)
            name, kind, rest = _split_arg(message[i + 1 : end])
            if name not in params:
                raise TemplateError(f"missing param {name!r}")
            value = params[name]
            if kind is None:
                out.append("" if value is None else str(value))
            elif kind == "plural":
                branch, number = _plural_branch(_branches(rest), value)
                out.append(format_message(branch.replace("#", str(number)), params))
            else:
                branches = _branches(rest)
                out.append(format_message(branches.get(str(value), branches["other"]), params))
            i = end + 1
        else:
            out.append(char)
            i += 1
    return "".join(out)


def _tidy(text: str) -> str:
    return _MULTI_SPACE.sub(" ", _SPACE_BEFORE_PUNCT.sub(r"\1", text)).strip()


# --- loading ---------------------------------------------------------------------------------


def _load_raw() -> dict[str, Any]:
    raw = yaml.safe_load(
        resources.files("app.notifications").joinpath("templates.yaml").read_text("utf-8")
    )
    if not isinstance(raw, dict):
        raise TemplateError("templates.yaml: expected a mapping")
    return raw


def _parse_template(key: str, spec: Any) -> Template:
    if not _KEY_RE.match(key) or len(key) > 100:
        raise TemplateError(f"bad template key {key!r}")
    if not isinstance(spec, dict):
        raise TemplateError(f"{key}: expected a mapping")
    params = spec.get("params", [])
    if not isinstance(params, list) or not all(
        isinstance(p, str) and _NAME_RE.match(p) for p in params
    ):
        raise TemplateError(f"{key}: params must be a list of names")
    resource_type = spec.get("resource_type")
    if resource_type is not None and not (
        isinstance(resource_type, str) and _NAME_RE.match(resource_type)
    ):
        raise TemplateError(f"{key}: bad resource_type")
    messages: dict[str, Message] = {}
    used_by_language: dict[str, set[str]] = {}
    for lang in LANGUAGES:
        msg = spec.get(lang)
        if not isinstance(msg, dict) or set(msg) != {"title", "body"}:
            raise TemplateError(f"{key}.{lang}: needs exactly title and body")
        title, body = msg["title"], msg["body"]
        if not isinstance(title, str) or not isinstance(body, str) or not title or not body:
            raise TemplateError(f"{key}.{lang}: title and body must be text")
        used_by_language[lang] = placeholders(title) | placeholders(body)
        messages[lang] = Message(title=title.strip(), body=body.strip())
    declared = set(params)
    for name, used in used_by_language.items():
        if not used <= declared:
            raise TemplateError(f"{key}.{name}: undeclared placeholders {sorted(used - declared)}")
    if used_by_language["en"] != used_by_language["te"]:
        raise TemplateError(f"{key}: en and te use different placeholders")
    return Template(
        key=key, params=frozenset(params), resource_type=resource_type, messages=messages
    )


@lru_cache(maxsize=1)
def catalog() -> Mapping[str, Template]:
    raw = _load_raw()
    items = raw.get("templates")
    if not isinstance(items, dict) or not items:
        raise TemplateError("templates.yaml: 'templates' must be a non-empty mapping")
    return {key: _parse_template(key, spec) for key, spec in items.items()}


@lru_cache(maxsize=1)
def read_retention_days() -> int:
    days = (_load_raw().get("retention") or {}).get("read_days")
    if not isinstance(days, int) or days < 1:
        raise TemplateError("templates.yaml: retention.read_days must be a positive int")
    return days


def get(key: str) -> Template:
    template = catalog().get(key)
    if template is None:
        raise TemplateError(f"unknown notification template {key!r}")
    return template


def check_params(template: Template, params: Mapping[str, Any]) -> None:
    """Params must be exactly the declared ones (no extra keys, none missing)."""
    keys = set(params)
    if keys != template.params:
        missing, extra = sorted(template.params - keys), sorted(keys - template.params)
        raise TemplateError(f"{template.key}: missing {missing}, undeclared {extra}")


def render(key: str, params: Mapping[str, Any], language: str) -> Message:
    template = get(key)
    lang = language if language in LANGUAGES else DEFAULT_LANGUAGE
    msg = template.messages[lang]
    return Message(
        title=_tidy(format_message(msg.title, params)),
        body=_tidy(format_message(msg.body, params)),
    )


def negotiate_language(accept_language: str | None) -> Language:
    """Pick ``te`` or ``en`` from an Accept-Language header (highest q wins; default en)."""
    best: tuple[float, Language] = (0.0, DEFAULT_LANGUAGE)
    for part in (accept_language or "").split(","):
        tag, _, q_part = part.strip().partition(";")
        primary = tag.strip().lower().split("-")[0]
        if primary not in LANGUAGES:
            continue
        quality = 1.0
        q_part = q_part.strip()
        if q_part.startswith("q="):
            try:
                quality = float(q_part[2:])
            except ValueError:
                continue
        if quality > best[0]:
            best = (quality, "te" if primary == "te" else "en")
    return best[1]
