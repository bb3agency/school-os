"""Register-photo extraction evaluation (FA-07; FR-KB-058..FR-KB-062; docs/06 §13.12).

Synthetic register pages (``extraction.jsonl``: printed and handwritten, English, Telugu and
code-mixed) go through an :class:`ExtractionAdapter`, which returns what the system KEEPS of the
page: the candidate rows stored for review (the kept, masked cells) and the page image as stored
(the original, a redacted copy or nothing), read back as text. The harness scores:

- ``extraction_field_accuracy``: kept register fields read correctly / fields on the pages (each
  page row against its best-matching stored row; values compared NFC, case-folded, spaces
  collapsed). Soft gate.
- ``extraction_row_recall``: page rows with a stored row that has at least half of their fields
  right / page rows. Soft gate.
- ``extraction_aadhaar_redaction_recall``: planted Aadhaar-like numbers (12 digits, Verhoeff-valid)
  that appear unmasked NOWHERE in what the system kept / planted numbers. **Hard gate = 1.0.**
- ``extraction_unmasked_aadhaar_count``: every Verhoeff-valid 12-digit number, planted or not,
  in anything the system kept (stored values and the stored image's text). **Hard gate = 0**
  (invariant 4: any unmasked number in extraction output fails).

Invariant 11 and the dataset rule "no 12-digit numbers in the synthetic files": the planted
numbers are written in the dataset as placeholders (``{{AADHAAR:<seed>:<style>}}``,
``{{DECOY:<seed>:<style>}}``) and exist only in memory, made by :func:`planted_number` (a
checksum-valid number) and :func:`decoy_number` (the same digits with a wrong check digit, which
must NOT be treated as Aadhaar by the gate). Reports never print a value a page carried: only ids,
counts and the kind of leak.

The harness never imports the application: it has its own Verhoeff check and its own detector,
which is at least as eager as the product's (any whitespace, dash, ``.``, ``/``, ``_``, ``|``,
``:``, ``*`` or ``,`` between digit groups, digits of any script), so it can only find more.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

KEPT_FIELDS: Final = (
    "admission_no",
    "admission_date",
    "full_name",
    "gender",
    "dob",
    "father_name",
    "mother_name",
    "mother_tongue",
    "nationality",
)
"""The register columns the system may keep (``app/extraction/config.yaml`` ``fields``; the eval
bridge test pins them equal). Other columns are read, scanned and dropped."""

Style = Literal["spaced", "hyphen", "plain", "telugu", "pipe", "split"]
STYLES: Final[tuple[Style, ...]] = ("spaced", "hyphen", "plain", "telugu", "pipe", "split")
PLACEHOLDER: Final = re.compile(r"\{\{(AADHAAR|DECOY):(\d{1,4}):([a-z]+)\}\}")
_TELUGU_DIGITS: Final = "౦౧౨౩౪౫౬౭౮౯"
_DASHES: Final = "".join(chr(c) for c in (*range(0x2010, 0x2016), 0x2212))
"""Unicode hyphens and dashes (U+2010..U+2015) and the minus sign (U+2212)."""
_SEPARATORS: Final = rf"[\s\-{_DASHES}./_|:*,]{{1,3}}"
_RUN: Final = re.compile(rf"\d+(?:{_SEPARATORS}\d+)*")
_GROUP: Final = re.compile(r"\d+")
_JOINED_444: Final = re.compile(
    rf"(?<!\d)(\d{{4}}){_SEPARATORS}(\d{{4}}){_SEPARATORS}(\d{{4}})(?!\d)"
)
_JOINED_12: Final = re.compile(r"(?<!\d)\d{12}(?!\d)")
_SPACES: Final = re.compile(r"\s+")

# --- Verhoeff (the harness's own; never the application's) ---------------------------------------

_D: Final = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
    (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
    (4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
    (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
    (7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
    (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_P: Final = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_INV: Final = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_valid(digits: str) -> bool:
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = _D[check][_P[i % 8][int(ch)]]
    return check == 0


def _check_digit(digits: str) -> str:
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = _D[check][_P[(i + 1) % 8][int(ch)]]
    return str(_INV[check])


def _body(seed: int) -> str:
    raw = hashlib.sha256(f"sos-eval-register-number:{seed}".encode()).hexdigest()
    digits = "".join(str(int(c, 16) % 10) for c in raw)[:11]
    return str(2 + int(digits[0]) % 8) + digits[1:]


def planted_number(seed: int) -> str:
    """A synthetic 12-digit number that passes the Verhoeff check (in memory only)."""
    body = _body(seed)
    return body + _check_digit(body)


def decoy_number(seed: int) -> str:
    """The same 11 digits with a wrong check digit: 12 digits that are NOT Aadhaar-like."""
    body = _body(seed)
    return body + str((int(_check_digit(body)) + 1) % 10)


def written(digits: str, style: str) -> str:
    """``digits`` as a clerk or an OCR engine writes them (``split`` = three word spans, joined
    by the page's reading order; here one space)."""
    a, b, c = digits[:4], digits[4:8], digits[8:]
    if style in ("spaced", "split"):
        return f"{a} {b} {c}"
    if style == "hyphen":
        return f"{a}-{b}-{c}"
    if style == "plain":
        return digits
    if style == "telugu":
        return " ".join("".join(_TELUGU_DIGITS[int(d)] for d in part) for part in (a, b, c))
    if style == "pipe":
        return f"{a} | {b} | {c}"
    raise ValueError(f"unknown number style {style!r}")


def materialise(text: str) -> str:
    """Replace the placeholders with in-memory numbers (``split`` is expanded by the page)."""

    def one(match: re.Match[str]) -> str:
        kind, seed, style = match.group(1), int(match.group(2)), match.group(3)
        number = planted_number(seed) if kind == "AADHAAR" else decoy_number(seed)
        return written(number, style)

    return PLACEHOLDER.sub(one, text)


def planted_in(text: str) -> list[str]:
    """The checksum-valid numbers a dataset text plants (digits)."""
    return [
        planted_number(int(m.group(2))) for m in PLACEHOLDER.finditer(text) if m[1] == "AADHAAR"
    ]


def ascii_digits(text: str) -> str:
    """Digits of any script as ASCII (``unicodedata.digit``); everything else unchanged."""
    out = []
    for ch in unicodedata.normalize("NFC", text):
        value = unicodedata.digit(ch, None)
        out.append(str(value) if value is not None and not ch.isascii() else ch)
    return "".join(out)


def numbers_in(text: str) -> set[str]:
    """Verhoeff-valid 12-digit numbers written in ONE string: windows of whole digit groups
    joined by up to three separator characters (see the module docstring)."""
    found: set[str] = set()
    for run in _RUN.finditer(ascii_digits(text)):
        groups = _GROUP.findall(run.group())
        for start in range(len(groups)):
            total = ""
            for group in groups[start:]:
                total += group
                if len(total) >= 12:
                    break
            if len(total) == 12 and verhoeff_valid(total):
                found.add(total)
    return found


def numbers_joined(texts: Sequence[str]) -> set[str]:
    """Numbers split across pieces of text read in order (word-level OCR: ``2345`` ``6789``
    ``0124``): a 4-4-4 grouping or 12 contiguous digits across the joined text."""
    joined = ascii_digits(" ".join(texts))
    found = {"".join(m.groups()) for m in _JOINED_444.finditer(joined)}
    found |= {m.group() for m in _JOINED_12.finditer(joined)}
    return {n for n in found if verhoeff_valid(n)}


def normalise(value: str) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFC", value)).strip().casefold()


# --- dataset ----------------------------------------------------------------------------------


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Cell(_Model):
    column: str = Field(min_length=1)
    value: str
    """What the register says (placeholders for planted numbers)."""
    read_as: str | None = None
    """What an OCR engine reads instead (a handwriting misread); None = read correctly."""
    scored: bool = True
    """False for a kept cell that also carries a planted number (its masked value is not
    compared; the leak gates still check it)."""


class MarginNote(_Model):
    """Text written outside the table (a remark, a number in the margin)."""

    text: str = Field(min_length=1)
    placed: bool = True
    """False: the OCR engine returns the text without a position on the image (the page cannot
    be redacted, so the system must discard the image)."""


class ExtractionCase(_Model):
    id: str = Field(pattern=r"^extraction-[0-9]{3}$")
    script: Literal["printed", "handwritten"]
    locale: Literal["en", "te", "mixed"]
    rows: tuple[tuple[Cell, ...], ...] = Field(min_length=1)
    margin: tuple[MarginNote, ...] = ()
    note: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        for row in self.rows:
            columns = [c.column for c in row]
            if len(set(columns)) != len(columns):
                raise ValueError(f"{self.id}: a row repeats a column")
            for cell in row:
                if cell.scored and cell.column in KEPT_FIELDS and planted_in(cell.value):
                    raise ValueError(f"{self.id}: a scored cell cannot carry a planted number")
        return self

    @property
    def planted(self) -> tuple[str, ...]:
        texts = [c.value for row in self.rows for c in row] + [n.text for n in self.margin]
        return tuple(sorted({n for t in texts for n in planted_in(t)}))

    def page(self) -> RegisterPage:
        """The page as an OCR engine reads it, numbers made in memory."""
        rows = tuple(
            tuple(
                PageCell(c.column, materialise(c.read_as if c.read_as is not None else c.value))
                for c in row
            )
            for row in self.rows
        )
        margin: list[PageNote] = []
        for note in self.margin:
            split = PLACEHOLDER.fullmatch(note.text)
            if split is not None and split.group(3) == "split":
                margin += [PageNote(part, note.placed) for part in materialise(note.text).split()]
            else:
                margin.append(PageNote(materialise(note.text), note.placed))
        return RegisterPage(self.id, self.script, self.locale, rows, tuple(margin))

    def truth(self) -> list[dict[str, str]]:
        """Each page row's scored kept fields as written in the register."""
        return [
            {c.column: c.value for c in row if c.column in KEPT_FIELDS and c.scored}
            for row in self.rows
        ]


@dataclass(frozen=True, slots=True)
class PageCell:
    column: str
    text: str


@dataclass(frozen=True, slots=True)
class PageNote:
    text: str
    placed: bool


@dataclass(frozen=True, slots=True)
class RegisterPage:
    """What the adapter renders and reads. Holds real (synthetic, checksum-valid) numbers: never
    log, print or serialise it."""

    id: str
    script: str
    locale: str
    rows: tuple[tuple[PageCell, ...], ...]
    margin: tuple[PageNote, ...]

    def reading_order(self) -> list[str]:
        """Cells row by row in column order, then the margin: the order adapters lay the page out
        in (the joined-text checks depend on it)."""
        return [c.text for row in self.rows for c in row] + [n.text for n in self.margin]


def validate_cases(cases: Sequence[ExtractionCase]) -> None:
    """Keep the gates meaningful: unique ids, every planted number findable by the detector, and
    nothing else on a page (decoys included, before and after masking) that the detector reads
    as a number."""
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"duplicate extraction case {case.id}")
        seen.add(case.id)
        page = case.page()
        texts = page.reading_order()
        found = numbers_joined(texts) | {n for t in texts for n in numbers_in(t)}
        if found != set(case.planted):
            raise ValueError(f"{case.id}: the detector does not find exactly the planted numbers")
        cleaned = ["" if _is_piece(t, case.planted) else _drop(t, case.planted) for t in texts]
        masked = ["" if _is_piece(t, case.planted) else _mask(t, case.planted) for t in texts]
        for variant in (cleaned, masked):
            if numbers_joined(variant) or any(numbers_in(t) for t in variant):
                raise ValueError(f"{case.id}: text around a planted number reads as a number")


def _is_piece(text: str, planted: Iterable[str]) -> bool:
    """A word of a number that word-level OCR split (``2345``): masked as part of the joined
    text, never on its own."""
    return any(text.strip() in (n[:4], n[4:8], n[8:]) for n in planted)


def _drop(text: str, planted: Iterable[str]) -> str:
    return "" if any(_holds(text, n) for n in planted) else text


def _holds(text: str, number: str) -> bool:
    digits = "".join(ch for ch in ascii_digits(text) if ch.isdigit())
    return number in digits or number in numbers_in(text)


def _mask(text: str, planted: Iterable[str]) -> str:
    out = text
    for number in planted:
        for style in STYLES:
            out = out.replace(written(number, style), f"XXXX XXXX {number[-4:]}")
        out = ascii_digits(out).replace(number, f"XXXX XXXX {number[-4:]}")
    return out


def page_numbers(page: RegisterPage) -> set[str]:
    """Every Aadhaar-like number the harness's detector finds on a page."""
    texts = page.reading_order()
    return numbers_joined(texts) | {n for t in texts for n in numbers_in(t)}


def without_numbers(texts: Sequence[str], numbers: Iterable[str]) -> list[str]:
    """``texts`` with every piece that shows one of ``numbers`` left out (perfect redaction)."""
    found = list(numbers)
    return [t for t in texts if not _is_piece(t, found) and not any(_holds(t, n) for n in found)]


def mask(text: str, planted: Iterable[str]) -> str:
    """The harness's stand-in for the product's masking (stubs only): ``XXXX XXXX 1234``."""
    return _mask(text, planted)


# --- adapter ----------------------------------------------------------------------------------


class ExtractionResult(_Model):
    rows: tuple[dict[str, str], ...]
    """The candidate rows stored for review: kept column -> stored (masked) value."""
    image: Literal["original", "redacted", "withheld"]
    """The page image as stored: unchanged, a redacted copy, or none (discarded)."""
    image_text: tuple[str, ...] = ()
    """Everything a reader gets back from the stored image, in reading order (empty when
    withheld)."""


class ExtractionAdapter(Protocol):
    name: str

    def extract_register_page(self, page: RegisterPage) -> ExtractionResult: ...


# --- scoring ----------------------------------------------------------------------------------


class ExtractionOutcome(_Model):
    """One page's score. Never a value from the page: counts and leak kinds only."""

    id: str
    script: str
    locale: str
    fields: int
    correct_fields: int
    rows: int
    recalled_rows: int
    planted: int
    leaked_planted: int
    unmasked_numbers: int
    leak_places: tuple[str, ...]
    """Where an unmasked number was found: ``stored_value`` and/or ``image``."""
    image: str


class ExtractionMetrics(_Model):
    extraction_items: int = 0
    extraction_planted: int = 0
    extraction_field_accuracy: float | None = None
    extraction_row_recall: float | None = None
    extraction_aadhaar_redaction_recall: float | None = None
    extraction_unmasked_aadhaar_count: int | None = None
    extraction_images_withheld: int = 0
    extraction_needless_redactions: int = 0
    """Pages without a planted number whose image was redacted or discarded (over-masking is
    accepted, but it costs the clerk the page image)."""


def _row_score(truth: Mapping[str, str], stored: Mapping[str, str]) -> int:
    return sum(1 for k, v in truth.items() if normalise(stored.get(k, "")) == normalise(v))


def score(case: ExtractionCase, result: ExtractionResult) -> ExtractionOutcome:
    truth = case.truth()
    correct = recalled = 0
    unused = list(result.rows)
    for row in truth:
        # One stored row per page row (best unused match), so a row is never credited twice.
        scores = [_row_score(row, stored) for stored in unused]
        best = max(scores, default=0)
        if scores:
            unused.pop(scores.index(best))
        correct += best
        recalled += bool(row) and best * 2 >= len(row)
    stored_values = [v for row in result.rows for v in row.values()]
    image_text = list(result.image_text) if result.image != "withheld" else []
    in_values = {n for v in stored_values for n in numbers_in(v)}
    in_image = numbers_joined(image_text) | {n for t in image_text for n in numbers_in(t)}
    leaked = [
        n
        for n in case.planted
        if n in in_values
        or n in in_image
        or any(_holds(t, n) for t in [*stored_values, *image_text])
    ]
    places = []
    if in_values or any(_holds(v, n) for v in stored_values for n in case.planted):
        places.append("stored_value")
    if in_image or any(_holds(t, n) for t in image_text for n in case.planted):
        places.append("image")
    return ExtractionOutcome(
        id=case.id,
        script=case.script,
        locale=case.locale,
        fields=sum(len(r) for r in truth),
        correct_fields=correct,
        rows=sum(1 for r in truth if r),
        recalled_rows=recalled,
        planted=len(case.planted),
        leaked_planted=len(leaked),
        unmasked_numbers=len(in_values | in_image | set(leaked)),
        leak_places=tuple(places),
        image=result.image,
    )


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def aggregate(outcomes: Sequence[ExtractionOutcome]) -> ExtractionMetrics:
    if not outcomes:
        return ExtractionMetrics()
    planted = sum(o.planted for o in outcomes)
    return ExtractionMetrics(
        extraction_items=len(outcomes),
        extraction_planted=planted,
        extraction_field_accuracy=_ratio(
            sum(o.correct_fields for o in outcomes), sum(o.fields for o in outcomes)
        ),
        extraction_row_recall=_ratio(
            sum(o.recalled_rows for o in outcomes), sum(o.rows for o in outcomes)
        ),
        extraction_aadhaar_redaction_recall=_ratio(
            planted - sum(o.leaked_planted for o in outcomes), planted
        ),
        extraction_unmasked_aadhaar_count=sum(o.unmasked_numbers for o in outcomes),
        extraction_images_withheld=sum(1 for o in outcomes if o.image == "withheld"),
        extraction_needless_redactions=sum(
            1 for o in outcomes if not o.planted and o.image != "original"
        ),
    )


def run(
    cases: Sequence[ExtractionCase], adapter: ExtractionAdapter
) -> tuple[ExtractionMetrics, tuple[ExtractionOutcome, ...]]:
    outcomes = tuple(score(case, adapter.extract_register_page(case.page())) for case in cases)
    return aggregate(outcomes), outcomes


__all__ = [
    "KEPT_FIELDS",
    "Cell",
    "ExtractionAdapter",
    "ExtractionCase",
    "ExtractionMetrics",
    "ExtractionOutcome",
    "ExtractionResult",
    "MarginNote",
    "PageCell",
    "PageNote",
    "RegisterPage",
    "aggregate",
    "decoy_number",
    "mask",
    "materialise",
    "numbers_in",
    "numbers_joined",
    "page_numbers",
    "planted_number",
    "run",
    "score",
    "validate_cases",
    "verhoeff_valid",
    "without_numbers",
    "written",
]
