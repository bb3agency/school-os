"""Board and portal readiness: exact cross-source diff and fix owners (FR-DQ-030..FR-DQ-034,
US-503, US-504, ADR-0040).

Pure module (import-linter contract ``dq-pure-library``): no database, no routes.

UDISE+ validates a child's name, gender and date of birth against Aadhaar **exactly**, down to
spelling and spacing, and the AP SSC board (BSEAP) takes its nominal roll from UDISE+. So unlike
the name match classes of :mod:`app.dq.matching` (which forgive spacing, initials and spelling
variants to find *the same person*), readiness compares values character by character:

- :func:`diff` says **what** differs between the right value and another record, as kind codes
  (``spacing``, ``initials``, ``transposed``, ``day_month_swapped`` ...). Values are NFC
  normalised first; capital letters and spaces still count (``case`` and ``spacing`` kinds).
- :func:`segments` and :func:`describe` show **where**, character by character, for screens and
  the parent verification slip only (they contain the values; never store or log them).
- :func:`assess_field` decides **who fixes it** (:data:`OWNERS`) from the readiness config
  (``config/readiness.yaml``): the admission register holds the right value (BR-01) unless a
  corroborating record (birth certificate, incoming TC) contradicts it; a source that differs
  from the right value is fixed by its owner (Aadhaar: the parent at an Aadhaar centre; UDISE+:
  the school through the MEO/MIS coordinator; the register: a change request with evidence;
  undecided: a person). Nothing is ever corrected automatically (invariant 6).
- :func:`student_status` turns the differences into ``ready`` / ``needs_parent`` /
  ``needs_school`` / ``blocked``.
"""

from __future__ import annotations

import datetime as dt
import difflib
import functools
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.textnorm import has_telugu
from app.dq.explanations import ROUTE_CODES
from app.dq.matching import MatchClass
from app.dq.profiles import READINESS_SOURCES, Label, ReadinessField, ReadinessSpec
from app.dq.rules import Severity

Owner = Literal["parent_aadhaar", "school_udise", "school_register", "unknown"]
Status = Literal["ready", "needs_parent", "needs_school", "blocked"]
Reason = Literal["mismatch", "undecided", "missing"]
ValueKind = Literal["name", "date", "enum"]

OWNERS: Final[tuple[Owner, ...]] = ("parent_aadhaar", "school_udise", "school_register", "unknown")
STATUSES: Final[tuple[Status, ...]] = ("ready", "needs_parent", "needs_school", "blocked")
NAME_KINDS: Final = (
    "case",
    "spacing",
    "punctuation",
    "word_missing",
    "order",
    "initials",
    "transposed",
    "variant",
    "spelling",
    "script",
    "different",
)
DATE_KINDS: Final = ("day_month_swapped", "day", "month", "year", "unreadable")
KINDS: Final = (*NAME_KINDS, *DATE_KINDS, "missing")
CHANGE_CODES: Final = ("space_added", "space_missing", "swapped", "replaced", "added", "removed")
_PUNCT_RE: Final = re.compile("[.'\u2019_,/-]")
_WS_RE: Final = re.compile(r"\s+")


# --- configuration ----------------------------------------------------------------------------


class Owners(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    by_source: dict[str, Owner]
    undecided: Owner
    missing: dict[str, Owner]


class ReadinessConfig(BaseModel):
    """``config/readiness.yaml`` (validated once)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    referee: str
    corroborating_sources: tuple[str, ...]
    owners: Owners
    status_of_owner: dict[Owner, Status]
    status_order: tuple[Status, ...]
    severity: dict[Literal["mismatch", "undecided", "missing", "advisory"], Severity]
    explanations: dict[str, str]
    routes: dict[Owner, tuple[str, ...]]
    sources: dict[str, Label]
    kinds: dict[str, Label]
    owner_labels: dict[Owner, Label]
    changes: dict[str, Label]

    @model_validator(mode="after")
    def _owners(self) -> ReadinessConfig:
        if self.referee not in READINESS_SOURCES:
            raise ValueError("the referee must be a readiness source")
        if set(self.owners.by_source) != READINESS_SOURCES:
            raise ValueError(f"owners.by_source must list exactly {sorted(READINESS_SOURCES)}")
        if set(self.owners.missing) != READINESS_SOURCES:
            raise ValueError(f"owners.missing must list exactly {sorted(READINESS_SOURCES)}")
        if set(self.status_of_owner) != set(OWNERS) or set(self.owner_labels) != set(OWNERS):
            raise ValueError("every fix owner needs a status and a label")
        if set(self.routes) != set(OWNERS):
            raise ValueError("every fix owner needs correction routes")
        for owner, routes in self.routes.items():
            if not routes or not set(routes) <= set(ROUTE_CODES):
                raise ValueError(f"{owner}: unknown or no correction routes")
        return self

    @model_validator(mode="after")
    def _complete(self) -> ReadinessConfig:
        if sorted(self.status_order) != sorted(STATUSES) or self.status_order[-1] != "ready":
            raise ValueError("status_order lists every status, worst first, ending with ready")
        if set(self.severity) != {"mismatch", "undecided", "missing", "advisory"}:
            raise ValueError("severity needs mismatch, undecided, missing and advisory")
        if set(self.explanations) != {*OWNERS, "missing", "advisory"}:
            raise ValueError("explanations need every owner, missing and advisory")
        if set(self.kinds) != set(KINDS):
            raise ValueError(f"kinds must list exactly {list(KINDS)}")
        if set(self.changes) != set(CHANGE_CODES):
            raise ValueError(f"changes must list exactly {list(CHANGE_CODES)}")
        needed = READINESS_SOURCES | set(self.corroborating_sources)
        if not needed <= set(self.sources):
            raise ValueError("every compared and corroborating source needs a label")
        return self

    def rank(self, status: Status) -> int:
        """Higher is worse: ready 0 ... blocked 3."""
        return len(self.status_order) - 1 - self.status_order.index(status)

    def check_profile(self, spec: ReadinessSpec) -> None:
        """A profile's advisory kinds must be known kinds."""
        unknown = sorted(set(spec.advisory_kinds) - set(KINDS))
        if unknown:
            raise ValueError(f"unknown advisory kinds {unknown}")


def parse_readiness_config(raw: Any) -> ReadinessConfig:
    return ReadinessConfig.model_validate(raw)


@functools.cache
def load_readiness_config() -> ReadinessConfig:
    """``app/dq/config/readiness.yaml``, validated once."""
    text = resources.files("app.dq").joinpath("config/readiness.yaml").read_text("utf-8")
    return parse_readiness_config(yaml.safe_load(text))


# --- diff -------------------------------------------------------------------------------------


def nfc(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _parse_date(value: str) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _letters(value: str) -> str:
    return _WS_RE.sub("", value)


def _transposed(a: str, b: str) -> bool:
    """``b`` is ``a`` with two adjacent characters swapped (spaces ignored, case kept)."""
    x, y = _letters(a).casefold(), _letters(b).casefold()
    if len(x) != len(y) or x == y:
        return False
    diffs = [i for i, (p, q) in enumerate(zip(x, y, strict=True)) if p != q]
    return (
        len(diffs) == 2
        and diffs[1] == diffs[0] + 1
        and x[diffs[0]] == y[diffs[1]]
        and x[diffs[1]] == y[diffs[0]]
    )


Classifier = Callable[[str, str], MatchClass]


_KIND_OF_CLASS: Final[dict[MatchClass, str]] = {
    MatchClass.EXACT: "punctuation",
    MatchClass.ORDER: "order",
    MatchClass.SPACING: "spacing",
    MatchClass.INITIALS: "initials",
    MatchClass.VARIANT: "variant",
    MatchClass.TYPO: "spelling",
}


def _surface_kinds(a: str, b: str) -> tuple[str, ...] | None:
    """Differences of writing only (script, capitals, spaces, marks, two swapped letters), or
    ``None`` when the letters themselves differ."""
    if has_telugu(a) != has_telugu(b):
        return ("script",)
    if a.casefold() == b.casefold():
        return ("case",)
    if _letters(a).casefold() == _letters(b).casefold():
        return ("spacing", "case") if _letters(a) != _letters(b) else ("spacing",)
    return _word_kinds(a, b)


def _word_kinds(a: str, b: str) -> tuple[str, ...] | None:
    """Marks only, a whole word missing on one side, or two swapped letters."""
    stripped_a = _WS_RE.sub(" ", _PUNCT_RE.sub(" ", a)).strip().casefold()
    stripped_b = _WS_RE.sub(" ", _PUNCT_RE.sub(" ", b)).strip().casefold()
    if stripped_a == stripped_b or _letters(stripped_a) == _letters(stripped_b):
        return ("punctuation",)
    words_a, words_b = set(stripped_a.split()), set(stripped_b.split())
    if words_a < words_b or words_b < words_a:
        return ("word_missing",)
    if _transposed(a, b):
        return ("transposed",)
    return None


def name_kinds(a: str, b: str, classify: Classifier) -> tuple[str, ...]:
    """Kinds of difference between two names (NFC first; capital letters and spaces count)."""
    a, b = nfc(a), nfc(b)
    if a == b:
        return ()
    surface = _surface_kinds(a, b)
    if surface is not None:
        return surface
    return (_KIND_OF_CLASS.get(classify(a, b), "different"),)


def date_kinds(a: str, b: str) -> tuple[str, ...]:
    da, db = _parse_date(a), _parse_date(b)
    if da is None or db is None:
        return ("unreadable",)
    if da == db:
        return ()
    if da.year == db.year and da.day == db.month and da.month == db.day:
        return ("day_month_swapped",)
    return tuple(
        part
        for part, x, y in (
            ("day", da.day, db.day),
            ("month", da.month, db.month),
            ("year", da.year, db.year),
        )
        if x != y
    )


def diff(kind: ValueKind, a: str, b: str, classify: Classifier) -> tuple[str, ...]:
    """What differs between ``a`` (the right value) and ``b``; empty when they are equal.
    Names and other text are exact after NFC; dates compare as dates; enum values exactly."""
    if kind == "name":
        return name_kinds(a, b, classify)
    if kind == "date":
        return date_kinds(a, b)
    return () if nfc(a).strip() == nfc(b).strip() else ("different",)


def lenient_equal(kind: ValueKind, a: str, b: str) -> bool:
    """Same value for deciding which record is right: capital letters and repeated spaces do
    not count (corroboration only; readiness itself is exact)."""
    if kind == "date":
        da, db = _parse_date(a), _parse_date(b)
        return da is not None and da == db
    return _WS_RE.sub(" ", nfc(a)).strip().casefold() == _WS_RE.sub(" ", nfc(b)).strip().casefold()


def display_date(value: str) -> str:
    """``2012-03-14`` -> ``14/03/2012`` (Indian convention); other text unchanged."""
    parsed = _parse_date(value)
    return parsed.strftime("%d/%m/%Y") if parsed is not None else value


@dataclass(frozen=True, slots=True)
class Segment:
    """One piece of a character-level diff of the right value (``reference``) and another
    record (``other``). Contains the values: for display only."""

    op: Literal["equal", "insert", "delete", "replace"]
    reference: str
    other: str


def segments(kind: ValueKind, a: str, b: str) -> tuple[Segment, ...]:
    """Character-level diff of ``a`` and ``b`` (dates as DD/MM/YYYY)."""
    if kind == "date":
        a, b = display_date(a), display_date(b)
    a, b = nfc(a), nfc(b)
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return tuple(
        Segment(op=op, reference=a[i1:i2], other=b[j1:j2])
        for op, i1, i2, j1, j2 in matcher.get_opcodes()
    )


def _word_at(text: str, index: int) -> str:
    """The word of ``text`` at ``index`` (the one before it when ``index`` is a space)."""
    if not text:
        return ""
    index = max(0, min(index, len(text) - 1))
    if text[index].isspace() and index > 0:
        index -= 1
    while index > 0 and text[index].isspace():
        index -= 1
    start = index
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    end = index
    while end < len(text) and not text[end].isspace():
        end += 1
    return text[start:end]


@dataclass(frozen=True, slots=True)
class Change:
    """One character-level change as a code with its parts (values: display only)."""

    code: str
    word: str
    a: str
    b: str

    def text(self, cfg: ReadinessConfig, language: str) -> str:
        template = cfg.changes[self.code].text(language)
        return template.format(word=self.word, a=self.a, b=self.b)


# Kinds whose character changes read well one by one; the others (words reordered, initials,
# another script, a different name) are described by their kind alone.
DESCRIBED_KINDS: Final = frozenset(
    {"case", "spacing", "punctuation", "transposed", "spelling", "variant", "word_missing"}
)


def describe(kind: ValueKind, a: str, b: str, kinds: Iterable[str] = ()) -> tuple[Change, ...]:
    """The changes from ``a`` (right value) to ``b``, in plain words (names only; dates and
    the kinds outside :data:`DESCRIBED_KINDS` are described by their kinds)."""
    if kind != "name" or not set(kinds) <= DESCRIBED_KINDS:
        return ()
    a, b = nfc(a), nfc(b)
    if len(a) == len(b):
        diffs = [i for i, (p, q) in enumerate(zip(a, b, strict=True)) if p != q]
        if (
            len(diffs) == 2
            and diffs[1] == diffs[0] + 1
            and a[diffs[0]] == b[diffs[1]]
            and a[diffs[1]] == b[diffs[0]]
        ):
            i = diffs[0]
            return (Change("swapped", _word_at(a, i), a[i : i + 2], b[i : i + 2]),)
    out: list[Change] = []
    position = 0
    for seg in segments(kind, a, b):
        start = position
        position += len(seg.reference)
        if seg.op == "equal":
            continue
        word = _word_at(a, start if seg.op != "insert" else max(start - 1, 0))
        if not seg.reference.strip() and not seg.other.strip():
            code = "space_added" if len(seg.other) > len(seg.reference) else "space_missing"
            out.append(Change(code, word, seg.reference, seg.other))
        elif (
            seg.op == "replace"
            and len(seg.reference) == 2
            and seg.reference[::-1].casefold() == seg.other.casefold()
        ):
            out.append(Change("swapped", word, seg.reference, seg.other))
        elif seg.op == "replace":
            out.append(Change("replaced", word, seg.reference, seg.other))
        elif seg.op == "insert":
            out.append(Change("added", word, "", seg.other))
        else:
            out.append(Change("removed", word, seg.reference, ""))
    return tuple(out)


# --- fix owners -------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Item:
    """One reason a field is not ready (no values; codes only).

    ``source`` is the record to correct (``None`` when nobody knows which one is right);
    ``against`` the record holding the right value; ``sources`` every record involved.
    """

    attribute: str
    reason: Reason
    owner: Owner
    sources: tuple[str, ...]
    source: str | None = None
    against: str | None = None
    kinds: tuple[str, ...] = ()
    advisory: bool = False

    def counts(self) -> bool:
        """Whether the item changes readiness (advisory differences do not)."""
        return not self.advisory


@dataclass(frozen=True, slots=True)
class FieldAssessment:
    attribute: str
    kind: ValueKind
    # The right value's source when decided (the register, or the corroborating record that
    # contradicts it); None when undecided or nothing differs.
    reference: str | None
    items: tuple[Item, ...]


def _reference(
    spec: ReadinessField,
    kind: ValueKind,
    values: Mapping[str, str],
    cfg: ReadinessConfig,
) -> tuple[str | None, bool]:
    """(source of the right value or None, whether the register is contradicted)."""
    register = values.get(cfg.referee)
    if register is None:
        return None, False
    corroborating = {c: values[c] for c in cfg.corroborating_sources if values.get(c) is not None}
    agree = [c for c, v in corroborating.items() if lenient_equal(kind, v, register)]
    disputing = {c: v for c, v in corroborating.items() if c not in agree}
    if agree or not disputing:
        return cfg.referee, False
    compared = [v for s, v in values.items() if s in spec.sources and s != cfg.referee]
    held = {
        c: v
        for c, v in disputing.items()
        if any(lenient_equal(kind, v, other) for other in compared)
    }
    distinct: list[str] = []
    for value in held.values():
        if not any(lenient_equal(kind, value, seen) for seen in distinct):
            distinct.append(value)
    if len(distinct) == 1:
        source = next(c for c, v in held.items() if lenient_equal(kind, v, distinct[0]))
        return source, True
    return None, False


def assess_field(
    spec: ReadinessField,
    kind: ValueKind,
    values: Mapping[str, str | None],
    cfg: ReadinessConfig,
    classify: Classifier,
    *,
    advisory_kinds: Iterable[str] = (),
) -> FieldAssessment:
    """The readiness items of one field. ``values`` holds every source's current value (the
    compared ones, the register and the corroborating records)."""
    advisory = frozenset(advisory_kinds)
    present = {s: nfc(v) for s, v in values.items() if v is not None and v.strip()}
    items: list[Item] = [
        Item(
            attribute=spec.attribute,
            reason="missing",
            owner=cfg.owners.missing[s],
            sources=(s,),
            source=s,
            kinds=("missing",),
        )
        for s in spec.required
        if s not in present
    ]
    compared = [s for s in spec.sources if s in present]
    if len(compared) < 2 or all(present[s] == present[compared[0]] for s in compared):
        return FieldAssessment(spec.attribute, kind, None, tuple(items))
    reference, contradicted = _reference(spec, kind, present, cfg)
    if reference is None:
        first = present[compared[0]]
        other = next(present[s] for s in compared if present[s] != first)
        items.append(
            Item(
                attribute=spec.attribute,
                reason="undecided",
                owner=cfg.owners.undecided,
                sources=tuple(sorted(compared)),
                kinds=diff(kind, first, other, classify),
            )
        )
        return FieldAssessment(spec.attribute, kind, None, tuple(items))
    right = present[reference]
    for s in compared:
        if s == reference:
            continue
        if s == cfg.referee and not contradicted:
            continue
        kinds = diff(kind, right, present[s], classify)
        if not kinds:
            continue
        items.append(
            Item(
                attribute=spec.attribute,
                reason="mismatch",
                owner=cfg.owners.by_source[s],
                sources=tuple(sorted({s, reference})),
                source=s,
                against=reference,
                kinds=kinds,
                advisory=set(kinds) <= advisory,
            )
        )
    return FieldAssessment(spec.attribute, kind, reference, tuple(items))


def item_status(item: Item, cfg: ReadinessConfig) -> Status:
    return "ready" if not item.counts() else cfg.status_of_owner[item.owner]


def student_status(items: Iterable[Item], cfg: ReadinessConfig) -> Status:
    """The worst status among the items that count (``ready`` when none)."""
    worst: Status = "ready"
    for item in items:
        status = item_status(item, cfg)
        if cfg.rank(status) > cfg.rank(worst):
            worst = status
    return worst


def item_severity(item: Item, cfg: ReadinessConfig) -> Severity:
    if item.advisory:
        return cfg.severity["advisory"]
    return cfg.severity[item.reason]


def item_explanation(item: Item, cfg: ReadinessConfig) -> str:
    if item.reason == "missing":
        return cfg.explanations["missing"]
    if item.advisory:
        return cfg.explanations["advisory"]
    return cfg.explanations[item.owner]


def item_routes(item: Item, cfg: ReadinessConfig) -> tuple[str, ...]:
    return cfg.routes[item.owner]


def kinds_param(kinds: Sequence[str]) -> str:
    """Kind codes as one explanation parameter (``spacing+case``)."""
    return "+".join(kinds)


def kinds_text(param: str, cfg: ReadinessConfig, language: str) -> str:
    """``spacing+case`` -> "spaces differ, capital and small letters differ"."""
    words = [cfg.kinds[k].text(language) if k in cfg.kinds else k for k in param.split("+") if k]
    return ", ".join(words)


def source_text(source: str, cfg: ReadinessConfig, language: str) -> str:
    label = cfg.sources.get(source)
    return label.text(language) if label is not None else source


__all__ = [
    "KINDS",
    "OWNERS",
    "STATUSES",
    "Change",
    "FieldAssessment",
    "Item",
    "Owner",
    "ReadinessConfig",
    "Segment",
    "Status",
    "assess_field",
    "describe",
    "diff",
    "display_date",
    "item_explanation",
    "item_routes",
    "item_severity",
    "item_status",
    "kinds_param",
    "kinds_text",
    "lenient_equal",
    "load_readiness_config",
    "parse_readiness_config",
    "segments",
    "source_text",
    "student_status",
]
