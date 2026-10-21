"""Disclosure values: check typed inputs, format numbers and build the frozen content
(ADR-0051 decisions 2 and 3; FR-DISC-004..006; NFR-PRV-006, PRV-037, SEC-050).

Pure module (no database, no web): the service passes the format, the draft and the derived
values and gets back normalised inputs, per-item errors or the content dictionary that a snapshot
freezes and the public page shows (an explicit allowlist: section and item keys, labels, and one
of ``value``, ``table``, ``documents`` or ``link`` per item).

- Text is NFC-normalised with single spaces; markup (``<`` or ``>``) and control characters are
  refused (inputs are plain text, rendered escaped).
- Aadhaar-like numbers (a 12-digit run, or anything :mod:`app.core.redaction` would mask) are
  refused in every item; phone-like numbers (10 or more digits in a run, an Indian mobile number)
  only outside the two contact items.
- Board results are numbers per year and class (no names); a count below
  :data:`SMALL_COUNT` is published as "fewer than 10" and its pass percentage left out
  (FR-DISC-006, owner decision D42).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final
from urllib.parse import urlsplit

from app.core.redaction import contains_full_aadhaar, find_aadhaar
from app.disclosure.formats import DisclosureFormat, FormatItem

SMALL_COUNT: Final = 10
FEWER_THAN: Final = "fewer than 10"
NOT_RECORDED: Final = "Not recorded in SchoolOS"
RESULT_CLASSES: Final = ("X", "XII")
RESULT_COLUMNS: Final = ("Year", "Class", "Appeared", "Passed", "Pass percentage")
MAX_DOCUMENTS_PER_ITEM: Final = 10
MAX_DOCUMENTS: Final = 200
LINK_MAX: Final = 300
_SPACES: Final = re.compile(r"\s+")
_TWELVE_DIGITS: Final = re.compile(r"(?<!\d)\d{4}[ .-]?\d{4}[ .-]?\d{4}(?!\d)")
_PHONE_LIKE: Final = re.compile(
    r"(?<!\d)(?:\+?91[ -]?)?0?[6-9]\d{4}[ -]?\d{5}(?!\d)|(?<!\d)\d{10,}(?!\d)"
)
_EMAIL: Final = re.compile(r"^[^@\s<>]{1,64}@[A-Za-z0-9.-]{1,180}\.[A-Za-z]{2,24}$")
_PHONE: Final = re.compile(r"^[0-9+()\-,/ ]{6,60}$")
_YEAR: Final = re.compile(r"^(\d{4})-(\d{2})$")


class Rejected(ValueError):
    """A value the item does not accept; ``code`` is the 422 error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Table:
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]

    def as_dict(self) -> dict[str, Any]:
        return {"columns": list(self.columns), "rows": [list(r) for r in self.rows]}


@dataclass(frozen=True, slots=True)
class Derived:
    """A value computed from SchoolOS records: text, a table, or nothing recorded."""

    value: str | None = None
    table: Table | None = None


@dataclass(frozen=True, slots=True)
class PublishedDocument:
    """A document version a snapshot publishes; ``n`` numbers it in the public URL."""

    n: int
    item_key: str
    document_id: Any
    version_id: Any
    title: str


@dataclass(slots=True)
class Problems:
    errors: list[dict[str, str]] = field(default_factory=list)

    def add(self, item: str, code: str) -> None:
        self.errors.append({"field": item, "code": code, "message_key": f"errors.{code}"})


# --- text checks ---------------------------------------------------------------------------------


def clean_text(value: str) -> str:
    """NFC, single spaces, trimmed."""
    return _SPACES.sub(" ", unicodedata.normalize("NFC", value)).strip()


def has_aadhaar_like(text: str) -> bool:
    return (
        bool(_TWELVE_DIGITS.search(text)) or contains_full_aadhaar(text) or bool(find_aadhaar(text))
    )


def has_phone_like(text: str) -> bool:
    return bool(_PHONE_LIKE.search(text))


def _check_text(text: str, *, contact: bool) -> None:
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in text):
        raise Rejected("invalid_characters")
    if "<" in text or ">" in text:
        raise Rejected("markup_not_allowed")
    if has_aadhaar_like(text):
        raise Rejected("aadhaar_not_allowed")
    if not contact and has_phone_like(text):
        raise Rejected("phone_not_allowed")


def _text(raw: object, item: FormatItem) -> str:
    if not isinstance(raw, str):
        raise Rejected("invalid")
    text = clean_text(raw)
    if not text:
        raise Rejected("required")
    if item.max_length is not None and len(text) > item.max_length:
        raise Rejected("too_long")
    _check_text(text, contact=item.contact)
    if item.type == "email" and not _EMAIL.fullmatch(text):
        raise Rejected("invalid_email")
    if item.type == "phone" and not _PHONE.fullmatch(text):
        raise Rejected("invalid_phone")
    return text


def _integer(raw: object, item: FormatItem) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise Rejected("invalid")
    if raw < 0 or (item.max_value is not None and raw > item.max_value):
        raise Rejected("out_of_range")
    return raw


def _number(raw: object, item: FormatItem) -> str:
    if isinstance(raw, bool) or not isinstance(raw, int | float | str):
        raise Rejected("invalid")
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        raise Rejected("invalid") from None
    if not value.is_finite() or value < 0:
        raise Rejected("out_of_range")
    if item.max_value is not None and value > item.max_value:
        raise Rejected("out_of_range")
    if value != value.quantize(Decimal("0.01")):
        raise Rejected("too_precise")
    return format(value.normalize(), "f")


def _results(raw: object, item: FormatItem) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise Rejected("invalid")
    if item.max_rows is not None and len(raw) > item.max_rows:
        raise Rejected("too_many_rows")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {
            "year",
            "class_level",
            "appeared",
            "passed",
        }:
            raise Rejected("invalid")
        year, level = entry["year"], entry["class_level"]
        appeared, passed = entry["appeared"], entry["passed"]
        match = _YEAR.fullmatch(year) if isinstance(year, str) else None
        if match is None or (int(match.group(1)) + 1) % 100 != int(match.group(2)):
            raise Rejected("invalid_year")
        if level not in RESULT_CLASSES:
            raise Rejected("invalid")
        for count in (appeared, passed):
            if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 100_000:
                raise Rejected("out_of_range")
        if passed > appeared:
            raise Rejected("passed_more_than_appeared")
        if (year, level) in seen:
            raise Rejected("duplicate_row")
        seen.add((year, level))
        rows.append({"year": year, "class_level": level, "appeared": appeared, "passed": passed})
    return sorted(rows, key=lambda r: (r["year"], RESULT_CLASSES.index(r["class_level"])))


def _link(raw: object) -> str:
    if not isinstance(raw, str):
        raise Rejected("invalid")
    text = raw.strip()
    if len(text) > LINK_MAX or any(c.isspace() for c in text) or "<" in text or ">" in text:
        raise Rejected("invalid_link")
    parts = urlsplit(text)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise Rejected("https_only")
    if has_aadhaar_like(text) or has_phone_like(text):
        raise Rejected("invalid_link")
    return text


def check_value(item: FormatItem, raw: object) -> object:
    """The normalised value of one input or link item, or :class:`Rejected`."""
    if item.fill == "link":
        return _link(raw)
    if item.type in ("text", "email", "phone"):
        return _text(raw, item)
    if item.type == "integer":
        return _integer(raw, item)
    if item.type == "number":
        return _number(raw, item)
    if item.type == "yes_no":
        if not isinstance(raw, bool):
            raise Rejected("invalid")
        return raw
    if item.type == "results":
        return _results(raw, item)
    raise Rejected("not_editable")


def check_inputs(
    fmt: DisclosureFormat, inputs: Mapping[str, object]
) -> tuple[dict[str, object], Problems]:
    """Normalised typed values (empty values dropped) and the per-item problems."""
    problems = Problems()
    out: dict[str, object] = {}
    for key, raw in inputs.items():
        item = fmt.item(key)
        if item is None or item.fill not in ("input", "link"):
            problems.add(f"inputs.{key}", "unknown_item")
            continue
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        if isinstance(raw, list) and not raw and item.type == "results":
            continue
        try:
            out[key] = check_value(item, raw)
        except Rejected as exc:
            problems.add(f"inputs.{key}", exc.code)
    return out, problems


def check_document_keys(
    fmt: DisclosureFormat, documents: Mapping[str, Sequence[object]]
) -> Problems:
    problems = Problems()
    total = 0
    for key, ids in documents.items():
        item = fmt.item(key)
        if item is None or not item.takes_documents:
            problems.add(f"documents.{key}", "unknown_item")
            continue
        if len(ids) > MAX_DOCUMENTS_PER_ITEM:
            problems.add(f"documents.{key}", "too_many_documents")
        if len(set(map(str, ids))) != len(ids):
            problems.add(f"documents.{key}", "duplicate_document")
        total += len(ids)
    if total > MAX_DOCUMENTS:
        problems.add("documents", "too_many_documents")
    return problems


def missing_required(fmt: DisclosureFormat, inputs: Mapping[str, object]) -> list[str]:
    return [i.key for i in fmt.items() if i.required and inputs.get(i.key) in (None, "", [])]


# --- formatting ----------------------------------------------------------------------------------


def group_indian(value: Decimal) -> str:
    """``1234567.5`` -> ``12,34,567.50`` (Indian digit grouping, two decimals)."""
    q = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole, frac = f"{abs(q):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    text = ",".join([*groups, tail]) if groups else tail
    sign = "-" if q < 0 else ""
    return f"{sign}{text}.{frac}"


def rupees(value: Decimal) -> str:
    return f"₹{group_indian(value)}"


def count_text(count: int) -> str:
    """A count of children as published: below 10 it is "fewer than 10" (D42)."""
    return FEWER_THAN if count < SMALL_COUNT else str(count)


def results_table(rows: Sequence[Mapping[str, Any]]) -> Table:
    """Board results as published (FR-DISC-006): counts below 10 suppressed, and the pass
    percentage left out whenever either count is suppressed."""
    out: list[tuple[str, ...]] = []
    for r in rows:
        appeared, passed = int(r["appeared"]), int(r["passed"])
        small = appeared < SMALL_COUNT or passed < SMALL_COUNT
        pct = "" if small or appeared == 0 else f"{Decimal(passed * 100) / appeared:.1f}%"
        out.append(
            (
                str(r["year"]),
                f"Class {r['class_level']}",
                count_text(appeared),
                count_text(passed),
                pct,
            )
        )
    return Table(columns=RESULT_COLUMNS, rows=tuple(out))


def input_display(item: FormatItem, value: object) -> tuple[str | None, Table | None]:
    if item.type == "yes_no":
        return ("Yes" if value else "No"), None
    if item.type == "results" and isinstance(value, list):
        return None, results_table(value)
    return str(value), None


def format_date(value: dt.date) -> str:
    return value.strftime("%d %b %Y")


# --- content -------------------------------------------------------------------------------------


def build_content(
    fmt: DisclosureFormat,
    *,
    school_name: str,
    inputs: Mapping[str, object],
    derived: Mapping[str, Derived],
    school: Mapping[str, str | None],
    documents: Sequence[PublishedDocument],
) -> dict[str, Any]:
    """The frozen content of a snapshot (FR-DISC-005): every section and item in the format's
    order with what the page shows. ``derived`` maps derived sources to their values now;
    ``school`` maps school sources; ``documents`` are the versions being published."""
    by_item: dict[str, list[dict[str, Any]]] = {}
    for doc in documents:
        by_item.setdefault(doc.item_key, []).append({"n": doc.n, "title": doc.title})
    sections: list[dict[str, Any]] = []
    for section in fmt.sections:
        items: list[dict[str, Any]] = []
        for item in section.items:
            entry: dict[str, Any] = {
                "key": item.key,
                "label": item.label,
                "from_schoolos": item.fill in ("school", "derived"),
                "value": None,
                "table": None,
                "documents": by_item.get(item.key, []),
                "link": None,
            }
            if item.fill == "school":
                entry["value"] = school.get(item.source or "")
            elif item.fill == "derived":
                found = derived.get(item.source or "", Derived())
                entry["value"] = found.value
                entry["table"] = found.table.as_dict() if found.table else None
            elif item.fill == "input" and item.key in inputs:
                value, table = input_display(item, inputs[item.key])
                entry["value"] = value
                entry["table"] = table.as_dict() if table else None
            elif item.fill == "link":
                link = inputs.get(item.key)
                entry["link"] = link if isinstance(link, str) else None
            items.append(entry)
        sections.append({"key": section.key, "title": section.title, "items": items})
    return {
        "format_key": fmt.key,
        "format_version": fmt.version,
        "title": fmt.title,
        "school_name": school_name,
        "sections": sections,
    }


__all__ = [
    "FEWER_THAN",
    "MAX_DOCUMENTS",
    "MAX_DOCUMENTS_PER_ITEM",
    "NOT_RECORDED",
    "SMALL_COUNT",
    "Derived",
    "Problems",
    "PublishedDocument",
    "Rejected",
    "Table",
    "build_content",
    "check_document_keys",
    "check_inputs",
    "check_value",
    "clean_text",
    "count_text",
    "format_date",
    "group_indian",
    "has_aadhaar_like",
    "has_phone_like",
    "input_display",
    "missing_required",
    "results_table",
    "rupees",
]
