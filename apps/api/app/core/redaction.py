"""Masking of Aadhaar numbers and other direct identifiers in free text.

CLAUDE.md invariant 4 / PRV-013..015 / SEC-008 / SEC-013: every text pipeline (OCR output,
extracted text, imports, logs, prompts, exports) masks 12-digit sequences that pass the Verhoeff
checksum. This module is the single implementation; nothing else should hand-roll it.

Public API
----------
- ``verhoeff_valid(digits)`` / ``verhoeff_check_digit(digits)``
- ``mask_aadhaar(text)``: masks Aadhaar-like numbers only (for OCR, ingestion, prompts, exports,
  where phone numbers and emails are legitimate content).
- ``redact(text)``: masks Aadhaar-like numbers, Indian mobile numbers and email addresses
  (logs, traces, error reports: last line of defence, docs/11 §2).
- ``contains_full_aadhaar(text)``: input rejection (FR-STU-012).
- ``find_aadhaar(text)``: where the numbers ``mask_aadhaar`` masks are (image redaction,
  PRV-016: callers map the character ranges to OCR bounding boxes).

Detection rules (defined and tested in ``tests/core/test_redaction.py``)
-----------------------------------------------------------------------
Text is NFC-normalised. A *run* is a sequence of digit *groups* joined by a short separator
(1-3 whitespace or zero-width characters of any kind, or a hyphen/dash/minus with up to 2 of
them on each side; dots, slashes and commas are not separators, so structured numbers such
as invoice numbers ``SOS/2026-27/000123`` and dates stay intact). Input checks also collapse every
whitespace run first, as stored text is (``contains_full_aadhaar``). Digits in any script
(ASCII, Telugu, Devanagari, ...) count via ``unicodedata.digit``. Candidates are windows of
consecutive whole groups inside a run, so:

- a contiguous block of 13+ digits is never treated as Aadhaar (it is some other identifier);
- ``Class 5 2345 6789 0124`` still finds the 12-digit window after ``5``.

1. Aadhaar: a 12-digit window passing Verhoeff, or failing it but with an Aadhaar keyword
   (``aadhaar``/``UID``/``ఆధార్``/``आधार``) within 20 characters (defence in depth). All matching
   windows are unioned and masked as ``XXXX XXXX 1234`` (last four digits kept).
2. Mobile (``redact`` only): a 10-digit window starting 6-9, optionally with ``0``/``91``/``+91``
   prefix, masked as ``XXXXXX1234``.
3. Email (``redact`` only): replaced by ``[redacted-email]``.

Masking repeats until nothing changes, so ``redact(redact(x)) == redact(x)``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

__all__ = [
    "EMAIL_MASK",
    "AadhaarMatch",
    "contains_full_aadhaar",
    "find_aadhaar",
    "mask_aadhaar",
    "redact",
    "verhoeff_check_digit",
    "verhoeff_valid",
]

EMAIL_MASK = "[redacted-email]"

# --- Verhoeff (dihedral group D5) ---------------------------------------------------------

_D: tuple[tuple[int, ...], ...] = (
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
_P: tuple[tuple[int, ...], ...] = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
    (1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
    (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
    (9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
    (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
    (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)
_INV: tuple[int, ...] = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def _to_ints(digits: str) -> list[int] | None:
    out: list[int] = []
    for ch in digits:
        if unicodedata.category(ch) != "Nd":
            return None
        out.append(unicodedata.digit(ch))
    return out


def verhoeff_valid(digits: str) -> bool:
    """True if ``digits`` (any script, no separators) carries a valid Verhoeff check digit."""
    values = _to_ints(digits)
    if not values:
        return False
    c = 0
    for i, value in enumerate(reversed(values)):
        c = _D[c][_P[i % 8][value]]
    return c == 0


def verhoeff_check_digit(digits: str) -> str:
    """Return the Verhoeff check digit to append to ``digits``."""
    values = _to_ints(digits)
    if values is None:
        raise ValueError("digits must contain decimal digits only")
    c = 0
    for i, value in enumerate(reversed(values)):
        c = _D[c][_P[(i + 1) % 8][value]]
    return str(_INV[c])


# --- Candidate runs -----------------------------------------------------------------------

# A gap is any Unicode whitespace (tab, line break, NBSP, thin/em/narrow spaces...) or an
# invisible zero-width character; up to 3 of them, or a dash with up to 2 on each side.
# Input checks run on raw text that is later stored with its whitespace collapsed (NFC, single
# spaces), so every gap that collapses into a space must already join the groups here, or a
# number typed as "1234<TAB>5678<TAB>9012" would pass the check and be stored as a full Aadhaar
# number (invariant 4).
_GAP = r"[\s\u200b-\u200d\u2060\ufeff]"
_DASH = r"[-\u2010-\u2015\u2212\ufe58\ufe63\uff0d]"
_SEP = rf"(?:{_GAP}{{1,3}}|{_GAP}{{0,2}}{_DASH}{_GAP}{{0,2}})"
_RUN_RE = re.compile(rf"\+?\d+(?:{_SEP}\d+)*")
_GROUP_RE = re.compile(r"\+?\d+")
# UUIDs are identifiers, never Aadhaar candidates: their decimal digits across hyphens pass
# Verhoeff ~0.24% of the time. Scanning happens on a view where UUID characters are replaced by
# a same-length non-digit, so indices match the original text.
_UUID_RE = re.compile(
    r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}"
    r"(?![0-9A-Fa-f])"
)


# The same UUIDs written as 32 hex characters (request ids ``req_<hex>``, trace ids): digit runs
# inside them read as mobile numbers now and then. Only runs of exactly 32 hex characters with at
# least one letter count, so a plain long number is still scanned.
_HEX32_RE = re.compile(r"(?<![0-9A-Za-z])(?=[0-9]*[A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Za-z])")


def _scan_view(text: str) -> str:
    def blank(m: re.Match[str]) -> str:
        return "u" * len(m.group(0))

    return _HEX32_RE.sub(blank, _UUID_RE.sub(blank, text))


_EMAIL_RE = re.compile(r"[\w.%+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_AADHAAR_CONTEXT_RE = re.compile(
    r"a{1,2}dha{1,2}r|(?<![a-z])uid(?:ai)?(?![a-z])|ఆధార్|आधार", re.IGNORECASE
)
_CONTEXT_DISTANCE = 20
_AADHAAR_LEN = 12
_MAX_GROUPS = _AADHAAR_LEN  # every group has at least one digit


@dataclass(frozen=True, slots=True)
class _Group:
    start: int  # includes a leading "+" if present
    end: int
    digits: str  # ASCII-normalised
    plus: bool


def _groups(text: str, run: re.Match[str]) -> list[_Group]:
    groups: list[_Group] = []
    for m in _GROUP_RE.finditer(text, run.start(), run.end()):
        raw = m.group()
        plus = raw.startswith("+")
        body = raw[1:] if plus else raw
        digits = "".join(str(unicodedata.digit(c)) for c in body)
        groups.append(_Group(m.start(), m.end(), digits, plus))
    return groups


def _windows(groups: list[_Group], size: int) -> list[tuple[int, int, str]]:
    """All (i, j, digits) with groups[i..j] holding exactly ``size`` digits."""
    found: list[tuple[int, int, str]] = []
    for i in range(len(groups)):
        total = ""
        for j in range(i, min(len(groups), i + _MAX_GROUPS)):
            total += groups[j].digits
            if len(total) == size:
                found.append((i, j, total))
            if len(total) >= size:
                break
    return found


def _is_explicit_phone(groups: list[_Group], i: int, digits: str) -> bool:
    """``+91 98765 43210`` is a phone number even if its 12 digits happen to pass Verhoeff."""
    return groups[i].plus and re.fullmatch(r"91[6-9]\d{9}", digits) is not None


def _context_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _AADHAAR_CONTEXT_RE.finditer(text)]


def _near_context(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    for k_start, k_end in spans:
        if k_end <= start and start - k_end <= _CONTEXT_DISTANCE:
            return True
        if k_start >= end and k_start - end <= _CONTEXT_DISTANCE:
            return True
    return False


def _merge(regions: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for i, j in sorted(regions):
        if merged and i <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], j))
        else:
            merged.append((i, j))
    return merged


def _aadhaar_windows(
    groups: list[_Group], spans: list[tuple[int, int]]
) -> list[tuple[int, int, str, bool]]:
    """Qualifying windows ``(i, j, digits, verhoeff_valid)``: Verhoeff-valid, or near a keyword."""
    found: list[tuple[int, int, str, bool]] = []
    for i, j, digits in _windows(groups, _AADHAAR_LEN):
        if _is_explicit_phone(groups, i, digits):
            continue
        valid = verhoeff_valid(digits)
        if valid or _near_context(groups[i].start, groups[j].end, spans):
            found.append((i, j, digits, valid))
    return found


def _aadhaar_regions(groups: list[_Group], spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    return _merge([(i, j) for i, j, _, _ in _aadhaar_windows(groups, spans)])


def _is_mobile(digits: str) -> bool:
    return (
        re.fullmatch(r"[6-9]\d{9}", digits) is not None
        or re.fullmatch(r"0[6-9]\d{9}", digits) is not None
        or re.fullmatch(r"91[6-9]\d{9}", digits) is not None
    )


def _mobile_regions(groups: list[_Group], taken: set[int]) -> list[tuple[int, int]]:
    regions: list[tuple[int, int]] = []
    i = 0
    while i < len(groups):
        match: tuple[int, int] | None = None
        total = ""
        for j in range(i, min(len(groups), i + _MAX_GROUPS)):
            if j in taken:
                break
            total += groups[j].digits
            if len(total) > _AADHAAR_LEN:
                break
            if len(total) >= 10 and _is_mobile(total):
                match = (i, j)
                break
        if match:
            regions.append(match)
            i = match[1] + 1
        else:
            i += 1
    return regions


def _mask_runs(text: str, *, mobiles: bool) -> tuple[str, bool]:
    """One masking pass. Returns (new_text, changed)."""
    spans = _context_spans(text)
    view = _scan_view(text)
    pieces: list[str] = []
    cursor = 0
    changed = False
    for run in _RUN_RE.finditer(view):
        groups = _groups(view, run)
        replacements: list[tuple[int, int, str]] = []
        aadhaar = _aadhaar_regions(groups, spans)
        taken: set[int] = set()
        for i, j in aadhaar:
            digits = "".join(g.digits for g in groups[i : j + 1])
            replacements.append((groups[i].start, groups[j].end, f"XXXX XXXX {digits[-4:]}"))
            taken.update(range(i, j + 1))
        if mobiles:
            for i, j in _mobile_regions(groups, taken):
                digits = "".join(g.digits for g in groups[i : j + 1])
                replacements.append((groups[i].start, groups[j].end, f"XXXXXX{digits[-4:]}"))
        for start, end, replacement in sorted(replacements):
            pieces.append(text[cursor:start])
            pieces.append(replacement)
            cursor = end
            changed = True
    pieces.append(text[cursor:])
    return "".join(pieces), changed


def _fixpoint(text: str, *, mobiles: bool) -> str:
    # Each pass strictly reduces the number of digits, so this terminates quickly.
    for _ in range(16):
        text, changed = _mask_runs(text, mobiles=mobiles)
        if not changed:
            break
    return text


def mask_aadhaar(text: str) -> str:
    """Mask Aadhaar-like numbers only (keeps phones/emails). Output is NFC-normalised."""
    return _fixpoint(unicodedata.normalize("NFC", text), mobiles=False)


def redact(text: str) -> str:
    """Mask Aadhaar-like numbers, Indian mobile numbers and emails. Idempotent, NFC output."""
    out = _fixpoint(unicodedata.normalize("NFC", text), mobiles=True)
    return _EMAIL_RE.sub(EMAIL_MASK, out)


def contains_full_aadhaar(text: str) -> bool:
    """True if ``text`` contains a 12-digit number passing the Verhoeff check (input rejection).

    Also checked with every whitespace run collapsed to one space: stored text is cleaned that
    way (NFC, single spaces), so however many spaces, tabs or line breaks separate the groups,
    the number that would be stored is refused (invariant 4; audit 2026-10-04, DL-01)."""
    nfc = unicodedata.normalize("NFC", text)
    return _has_valid_window(nfc) or _has_valid_window(_COLLAPSE_RE.sub(" ", nfc))


_COLLAPSE_RE = re.compile(r"[\s\u200b-\u200d\u2060\ufeff]+")


def _has_valid_window(text: str) -> bool:
    normalised = _scan_view(text)
    for run in _RUN_RE.finditer(normalised):
        groups = _groups(normalised, run)
        for _i, _j, digits in _windows(groups, _AADHAAR_LEN):
            if verhoeff_valid(digits):
                return True
    return False


@dataclass(frozen=True, slots=True)
class AadhaarMatch:
    """One 12-digit window that :func:`mask_aadhaar` masks: ``text[start:end]`` holds it
    (separators included), ``digits`` are its 12 digits in ASCII, ``verhoeff`` whether the
    checksum passes (False: masked only because an Aadhaar keyword is near)."""

    start: int
    end: int
    digits: str = field(repr=False)  # never in a log line or traceback (invariants 4, 5)
    verhoeff: bool


def find_aadhaar(text: str) -> list[AadhaarMatch]:
    """Every Aadhaar-like window in ``text``, in order, with its character range.

    Same rules as :func:`mask_aadhaar` (first pass), but windows are reported one by one rather
    than merged, and positions index ``text`` exactly as given: pass NFC-normalised text. Used
    to locate numbers on an image through OCR boxes (PRV-016); the caller must never log or
    store ``digits``.
    """
    spans = _context_spans(text)
    view = _scan_view(text)
    found: list[AadhaarMatch] = []
    for run in _RUN_RE.finditer(view):
        groups = _groups(view, run)
        for i, j, digits, valid in _aadhaar_windows(groups, spans):
            found.append(AadhaarMatch(groups[i].start, groups[j].end, digits, valid))
    return found
