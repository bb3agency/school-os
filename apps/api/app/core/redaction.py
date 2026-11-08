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
- ``redact_log(text)``: ``redact`` plus credentials and provider identifiers (structured logs,
  local tracebacks, span names; R2-3I, SEC-008, SEC-037): bearer tokens and JWTs, ``name=value``
  secrets in query strings (OAuth ``code``, ``state``, ``hub.verify_token``, ...), Meta access
  tokens, Razorpay ids (``pay_``, ``order_``, ``plink_``, key ids, ...), hex signatures (40+
  hex characters), long mixed-case keys (parent link tokens, Razorpay route values), upper-case
  codes with digits (certificate codes, bank UTRs) and 12-22 digit numbers (UPI UTRs, WABA ids).
  Not for content pipelines: only logs and traces, where such values are never needed.
- ``contains_full_aadhaar(text)``: input rejection (FR-STU-012).
- ``find_aadhaar(text)``: where the numbers ``mask_aadhaar`` masks are (image redaction,
  PRV-016: callers map the character ranges to OCR bounding boxes).

Detection rules (defined and tested in ``tests/core/test_redaction.py``)
-----------------------------------------------------------------------
Text is NFC-normalised. A *run* is a sequence of digit *groups* joined by a separator (any
number of whitespace or invisible format (Cf) characters of any kind, or a hyphen/dash/minus
with any number of them on each side: every gap the input check collapses, audit 2026-11-08
DPA-01; dots, slashes, underscores and commas are not run separators, so
structured numbers such as invoice numbers ``SOS/2026-27/000123`` and dates stay intact; rule 4
covers them for numbers grouped 4-4-4). Input checks also collapse every
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
4. Grouped Aadhaar (separate check; audit 2026-10-04 data-layer note 11, lead decision D52):
   exactly three 4-digit groups, each pair joined by 1-3 characters from whitespace (incl. NBSP,
   narrow NBSP and every other Zs), hyphens and Unicode dashes, ``.`` ``/`` ``_`` and invisible
   format characters (Cf) in any mix (``2345.6789/0124``, ``2345 - 6789 - 0124``,
   ``2345/ 6789 .0124``), or by a single ``,``; Verhoeff-valid; no group inside a longer
   contiguous digit block, and not continued by another digit group joined only by ``.`` ``/``
   ``,`` ``_``. Refused by ``contains_full_aadhaar`` and ``contains_grouped_aadhaar``, masked
   like rule 1, located by ``find_aadhaar``. Over-masking is accepted, under-masking is not.

Masking repeats until nothing changes, so ``redact(redact(x)) == redact(x)``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

__all__ = [
    "AADHAAR_GROUP_SEPARATOR",
    "EMAIL_MASK",
    "NUMBER_RUN_CHARACTER",
    "AadhaarMatch",
    "contains_full_aadhaar",
    "contains_grouped_aadhaar",
    "find_aadhaar",
    "mask_aadhaar",
    "redact",
    "redact_log",
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
# invisible zero-width character; any number of them, or a dash with any number on each side.
# Input checks run on raw text that is later stored with its whitespace collapsed (NFC, single
# spaces), so every gap that collapses into a space must already join the groups here, or a
# number typed as "1234<TAB>5678<TAB>9012" would pass the check and be stored as a full Aadhaar
# number (invariant 4).
# Invisible format characters (category Cf: soft hyphen, zero-width and bidi marks, invisible
# operators) show nothing on screen or in print, so they count as gaps too: "2345<LRM>6789..."
# reads as a full number. This is every Cf character of Unicode 15.0 (D52; a test checks it
# against the running interpreter's ``unicodedata``).
_INVISIBLE = (
    r"\u00ad\u0600-\u0605\u061c\u06dd\u070f\u0890-\u0891\u08e2\u180e\u200b-\u200f\u202a-\u202e"
    r"\u2060-\u2064\u2066-\u206f\ufeff\ufff9-\ufffb\U000110bd\U000110cd\U00013430-\U0001343f"
    r"\U0001bca0-\U0001bca3\U0001d173-\U0001d17a\U000e0001\U000e0020-\U000e007f"
)
_GAP = rf"[\s{_INVISIBLE}]"
_DASH = r"[-\u2010-\u2015\u2212\ufe58\ufe63\uff0d]"
# Any number of gap characters joins (audit 2026-11-08 DPA-01): the input check collapses every
# whitespace run before it looks, so a masker that stopped at 3 let "2345    6789    0124"
# through wherever text is masked rather than refused (Ask questions, AI gateway requests, model
# output, logs). Gaps and digits are disjoint classes, so the scan stays linear.
_SEP = rf"(?:{_GAP}+|{_GAP}*{_DASH}{_GAP}*)"
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


# The lookbehind starts a match only at the start of a local-part run: a long run of word
# characters and dots with no ``@`` is then tried once, not from every position (linear).
_EMAIL_RE = re.compile(r"(?<![\w.%+\-])[\w.%+\-]+@[\w\-]+(?:\.[\w\-]+)+")
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


# Grouped form with any separators (audit 2026-10-04 data-layer note 11; lead decision D52):
# exactly three 4-digit groups, each pair joined by 1-3 characters from space and every other
# whitespace (NBSP, narrow NBSP, all Zs), hyphen and Unicode dashes, dot, slash, underscore and
# invisible format characters (Cf), in any mix, or by a single comma; masked when the 12 digits
# pass Verhoeff (no keyword rule here: these characters are too common). Over-masking is
# accepted, under-masking is not. Boundaries: a group is never part of a longer contiguous digit
# block, and the three groups are not continued by another digit group joined only by dots,
# slashes, commas or underscores (``1234.5678.9012.3456``), so ``SOS/2026-27/000123``, dates and
# amounts never match. Windows may overlap (zero-width lookahead); every quantifier is bounded,
# so the scan is linear in the text length.
_WIDE_SEP = rf"(?:[\s{_INVISIBLE}\-\u2010-\u2015\u2212\ufe58\ufe63\uff0d._/]{{1,3}}|,)"
_TIGHT = r"[./,_]"
AADHAAR_GROUP_SEPARATOR = _WIDE_SEP
"""Regex fragment for what may join two 4-digit groups (rule 4); other maskers reuse it."""
NUMBER_RUN_CHARACTER = rf"[+\d\s{_INVISIBLE}\-\u2010-\u2015\u2212\ufe58\ufe63\uff0d._/,]"
"""Regex class of every character that may continue a number the maskers look at (digits of
any script, every gap, dash and rule-4 separator). A masker that sees text in pieces (the AI
stream, audit 2026-11-08 DPA-02) holds back a trailing run of these until the run has ended."""
_PUNCT_RE = re.compile(
    rf"(?<!\d)(?<!\d{_TIGHT})(?<!\d{_TIGHT}{{2}})(?<!\d{_TIGHT}{{3}})"
    rf"(?=(\d{{4}}){_WIDE_SEP}(\d{{4}}){_WIDE_SEP}(\d{{4}})(?!{_TIGHT}{{0,3}}\d))"
)


def _punctuated(view: str) -> list[tuple[int, int, str]]:
    """``(start, end, digits)`` of every Verhoeff-valid grouped 12-digit number (may overlap)."""
    found: list[tuple[int, int, str]] = []
    for m in _PUNCT_RE.finditer(view):
        digits = "".join(str(unicodedata.digit(c)) for c in m.group(1) + m.group(2) + m.group(3))
        if verhoeff_valid(digits):
            found.append((m.start(1), m.end(3), digits))
    return found


def _mask_punctuated(text: str) -> tuple[str, bool]:
    found = _punctuated(_scan_view(text))
    if not found:
        return text, False
    # Overlapping windows (``a.b.c.d`` with two valid windows) merge into one masked region that
    # keeps the last four digits of its last window.
    regions: list[tuple[int, int, str]] = []
    for start, end, digits in sorted(found):
        if regions and start < regions[-1][1]:
            regions[-1] = (regions[-1][0], end, digits)
        else:
            regions.append((start, end, digits))
    pieces: list[str] = []
    cursor = 0
    for start, end, digits in regions:
        pieces.append(text[cursor:start])
        pieces.append(f"XXXX XXXX {digits[-4:]}")
        cursor = end
    pieces.append(text[cursor:])
    return "".join(pieces), True


def _mask_runs(text: str, *, mobiles: bool) -> tuple[str, bool]:
    """One masking pass. Returns (new_text, changed)."""
    text, changed_punct = _mask_punctuated(text)
    text, changed = _mask_runs_whitespace(text, mobiles=mobiles)
    return text, changed or changed_punct


def _mask_runs_whitespace(text: str, *, mobiles: bool) -> tuple[str, bool]:
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


# --- Credentials and provider identifiers (logs and traces only; R2-3I) ---------------------

TOKEN_MASK = "[redacted-token]"  # noqa: S105 (a mask, not a secret)
ID_MASK = "[redacted-id]"
NUMBER_MASK = "[redacted-number]"
_SECRET_MASK = "[redacted]"  # noqa: S105 (a mask, not a secret)

_BEARER_RE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]*")
# ``name=value`` pairs whose value is a credential (query strings, form bodies, headers echoed
# in an exception message). The name is kept so the line still says what was there.
_PAIR_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])("
    r"code|state|token|access_token|refresh_token|id_token|client_secret|secret|password|"
    r"signature|sig|sha256|key|api_key|key_secret|hub\.verify_token|hub\.challenge|"
    r"razorpay_signature|razorpay_payment_id|razorpay_payment_link_id|utr|rrn"
    r")([=:]\s?)([^\s&\"',;]+)"
)
_META_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])EAA[A-Za-z0-9]{20,}")
# Razorpay entity ids and key ids: a known prefix and 14 base62 characters (at least one digit
# or capital, so snake_case names such as ``order_received`` stay readable).
_RAZORPAY_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:rzp_live|rzp_test|pay|order|plink|rfnd|cust|inv|setl|sub|qr|txn|evt|"
    r"acc|disp|pout|fa|va|aov|card|token)_(?=[A-Za-z0-9]*[A-Z0-9])[A-Za-z0-9]{10,20}"
    r"(?![A-Za-z0-9])"
)
_HEX_SECRET_RE = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{40,}(?![0-9A-Za-z])")
# Random keys: 20+ URL-safe characters with capitals, small letters and a digit, ``_`` or ``-``
# (parent link tokens, Meta and OAuth codes). snake_case names, CamelCase class names (local
# tracebacks stay readable) and lower-case hex request ids never match.
_MIXED_KEY_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[a-z])"
    r"(?=[A-Za-z0-9_-]*[0-9_-])[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)
# Razorpay route values (``public.tokens.new_route_token``: 24 letters and digits) even in the
# rare draw without a digit.
_ROUTE_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9]*[A-Z])(?=[A-Za-z0-9]*[a-z])[A-Za-z0-9]{24}"
    r"(?![A-Za-z0-9_-])"
)
# Upper-case codes with digits: certificate verification codes, bank UTRs (``HDFCR520261021...``);
# never the ``XXXXXX1234`` a masked mobile number already is.
_UPPER_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?!XXXX)(?=(?:[A-Z]*[0-9]){4})(?=(?:[0-9]*[A-Z]){2})[A-Z0-9]{10,32}(?![A-Za-z0-9])"
)
# Certificate verification codes (``certificates.codes``: 16 Crockford base32 characters, plain or
# printed in dashed groups of four) whatever their mix of digits and letters.
_CERT_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9-])(?:[0-9A-HJKMNP-TV-Z]{4}-){3}[0-9A-HJKMNP-TV-Z]{4}(?![A-Za-z0-9-])"
    r"|(?<![A-Za-z0-9])[0-9A-HJKMNP-TV-Z]{16}(?![A-Za-z0-9])"
)
# UPI UTR/RRN (12 digits), NEFT/IMPS references, WABA and phone-number ids (15-16 digits).
_LONG_NUMBER_RE = re.compile(r"(?<![0-9A-Za-z])[0-9]{12,22}(?![0-9A-Za-z])")


def _mask_credentials(segment: str) -> str:
    segment = _BEARER_RE.sub(lambda m: f"{m.group(1)} {TOKEN_MASK}", segment)
    segment = _JWT_RE.sub(TOKEN_MASK, segment)
    segment = _PAIR_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{_SECRET_MASK}", segment)
    segment = _META_TOKEN_RE.sub(TOKEN_MASK, segment)
    segment = _RAZORPAY_ID_RE.sub(ID_MASK, segment)
    segment = _HEX_SECRET_RE.sub(TOKEN_MASK, segment)
    segment = _MIXED_KEY_RE.sub(TOKEN_MASK, segment)
    segment = _ROUTE_VALUE_RE.sub(TOKEN_MASK, segment)
    segment = _UPPER_CODE_RE.sub(ID_MASK, segment)
    segment = _CERT_CODE_RE.sub(ID_MASK, segment)
    return _LONG_NUMBER_RE.sub(NUMBER_MASK, segment)


def redact_log(text: str) -> str:
    """:func:`redact` plus credentials and provider identifiers (see the module docstring).

    For log lines, tracebacks and span names only. UUIDs are kept verbatim (they are the ids
    logs are meant to carry). Idempotent: ``redact_log(redact_log(x)) == redact_log(x)``."""
    out = redact(text)
    pieces: list[str] = []
    cursor = 0
    for m in _UUID_RE.finditer(out):
        pieces.append(_mask_credentials(out[cursor : m.start()]))
        pieces.append(m.group(0))
        cursor = m.end()
    pieces.append(_mask_credentials(out[cursor:]))
    return "".join(pieces)


def contains_full_aadhaar(text: str) -> bool:
    """True if ``text`` contains a 12-digit number passing the Verhoeff check (input rejection).

    Also checked with every whitespace run collapsed to one space: stored text is cleaned that
    way (NFC, single spaces), so however many spaces, tabs or line breaks separate the groups,
    the number that would be stored is refused (invariant 4; audit 2026-10-04, DL-01)."""
    nfc = unicodedata.normalize("NFC", text)
    return _has_valid_window(nfc) or _has_valid_window(_COLLAPSE_RE.sub(" ", nfc))


_COLLAPSE_RE = re.compile(rf"[\s{_INVISIBLE}]+")


def contains_grouped_aadhaar(text: str) -> bool:
    """True if ``text`` holds a Verhoeff-valid number grouped 4-4-4 (rule 4 separators).

    For inputs where unbroken 12-digit numbers are legitimate (bank statement UPI references,
    ADR-0042 P9) but an Aadhaar number written as printed on the card is not."""
    return bool(_punctuated(_scan_view(unicodedata.normalize("NFC", text))))


def _has_valid_window(text: str) -> bool:
    normalised = _scan_view(text)
    if _punctuated(normalised):
        return True
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
    seen = {(m.start, m.end) for m in found}
    found.extend(
        AadhaarMatch(s, e, digits, True)
        for s, e, digits in _punctuated(view)
        if (s, e) not in seen  # a whitespace-grouped number matches both rules
    )
    return sorted(found, key=lambda m: (m.start, m.end))
