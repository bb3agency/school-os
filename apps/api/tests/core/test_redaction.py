"""Aadhaar/PII redaction (CLAUDE.md invariant 4; PRV-013, PRV-015, SEC-008, SEC-013).

All numbers here are synthetic: they are generated in-process with a valid Verhoeff check digit
and never correspond to a real person (no real data in tests, invariant 11).
"""

from __future__ import annotations

import random
import re
import unicodedata
import uuid

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.core.redaction import (
    AadhaarMatch,
    contains_full_aadhaar,
    find_aadhaar,
    mask_aadhaar,
    redact,
    verhoeff_check_digit,
    verhoeff_valid,
)

TELUGU_DIGITS = str.maketrans("0123456789", "౦౧౨౩౪౫౬౭౮౯")
DEVANAGARI_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def generate_valid_aadhaar_like(rng: random.Random) -> str:
    """A synthetic 12-digit number with a valid Verhoeff check digit (test-only helper).

    It never leaves the test process and is not a real Aadhaar number.
    """
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    return body + verhoeff_check_digit(body)


def make_invalid(number: str) -> str:
    """Flip the check digit so the Verhoeff checksum fails."""
    last = int(number[-1])
    return number[:-1] + str((last + 1) % 10)


RNG = random.Random(20260926)
VALID = generate_valid_aadhaar_like(RNG)
VALID_2 = generate_valid_aadhaar_like(RNG)
INVALID = make_invalid(VALID)


def spaced(n: str) -> str:
    return f"{n[:4]} {n[4:8]} {n[8:]}"


def hyphenated(n: str) -> str:
    return f"{n[:4]}-{n[4:8]}-{n[8:]}"


def masked(n: str) -> str:
    return f"XXXX XXXX {n[-4:]}"


# --- Verhoeff -------------------------------------------------------------------------------


def test_PRV_015_verhoeff_known_vectors() -> None:
    # Classic published Verhoeff examples: 236 -> check digit 3; 12345 -> 1.
    assert verhoeff_check_digit("236") == "3"
    assert verhoeff_valid("2363")
    assert not verhoeff_valid("2364")
    assert verhoeff_check_digit("12345") == "1"
    assert verhoeff_valid("123451")


def test_PRV_015_verhoeff_rejects_non_digits_and_empty() -> None:
    assert not verhoeff_valid("")
    assert not verhoeff_valid("12a4")
    assert not verhoeff_valid("1234 5678")


def test_PRV_015_verhoeff_accepts_telugu_digits() -> None:
    assert verhoeff_valid(VALID.translate(TELUGU_DIGITS))
    assert not verhoeff_valid(INVALID.translate(TELUGU_DIGITS))


def test_PRV_015_helper_generates_valid_numbers() -> None:
    rng = random.Random(1)
    for _ in range(50):
        n = generate_valid_aadhaar_like(rng)
        assert len(n) == 12
        assert verhoeff_valid(n)
        assert not verhoeff_valid(make_invalid(n))


# --- Aadhaar masking: table-driven ---------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (VALID, masked(VALID)),
        (spaced(VALID), masked(VALID)),
        (hyphenated(VALID), masked(VALID)),
        (f"{VALID[:6]} {VALID[6:]}", masked(VALID)),  # free grouping 6-6
        (f"{VALID[:2]}-{VALID[2:9]} {VALID[9:]}", masked(VALID)),  # mixed separators
        (f"{VALID[:4]}  {VALID[4:8]}  {VALID[8:]}", masked(VALID)),  # double spaces (OCR)
        (f"{VALID[:4]} - {VALID[4:8]} - {VALID[8:]}", masked(VALID)),
        (f"{VALID[:4]}\u00a0{VALID[4:8]}\u00a0{VALID[8:]}", masked(VALID)),  # NBSP
        (f"Student UID: {spaced(VALID)}.", f"Student UID: {masked(VALID)}."),
        (f"ఆధార్ సంఖ్య {spaced(VALID)} ఉంది", f"ఆధార్ సంఖ్య {masked(VALID)} ఉంది"),
        (f"విద్యార్థి{VALID}పేరు", f"విద్యార్థి{masked(VALID)}పేరు"),
        (f"({VALID})", f"({masked(VALID)})"),
        (f"a={VALID};b={VALID_2}", f"a={masked(VALID)};b={masked(VALID_2)}"),
        (f"Class 5 {spaced(VALID)}", f"Class 5 {masked(VALID)}"),  # neighbouring small number
    ],
)
def test_PRV_015_valid_aadhaar_is_masked(text: str, expected: str) -> None:
    assert redact(text) == expected
    assert mask_aadhaar(text) == expected


GAPS = [
    "\t",  # copied from a spreadsheet
    "\n",  # wrapped in a text area
    "\r\n",
    "   ",  # three spaces (PDF copy, OCR)
    "\u2002",  # en space
    "\u2003",  # em space
    "\u2009",  # thin space
    "\u202f",  # narrow no-break space
    "\u3000",  # ideographic space
    "\u200b",  # zero-width space
    "\ufeff",  # zero-width no-break space
    ".",
    "/",
    "\u2014",  # em dash
    "\u2212",  # minus sign
    "\uff0d",  # full-width hyphen-minus
    "  -  ",
    " \u2013 ",  # spaced en dash
    ",",
    "_",
]


@pytest.mark.parametrize("gap", ["    ", "\t\t\t\t", "\n\n\n\n\n", " .   ", "\u3000 \t \n "])
def test_PRV_015_input_check_refuses_what_whitespace_collapsing_would_store(gap: str) -> None:
    """DL-01: however wide the gap, the single-spaced text that would be stored is refused."""
    raw = gap.join([VALID[:4], VALID[4:8], VALID[8:]])
    assert contains_full_aadhaar(raw)
    assert contains_full_aadhaar(re.sub(r"\s+", " ", raw))


@pytest.mark.parametrize("gap", GAPS)
def test_PRV_015_any_gap_that_collapses_or_reads_as_a_separator_is_detected(gap: str) -> None:
    """SEC-013 / invariant 4: input checks run on the raw text, and the stored value has its
    whitespace collapsed to single spaces; a tab, line break, wide space or zero-width character
    between the groups must not let a full Aadhaar number through (audit 2026-10-04, DL-01)."""
    text = f"Ref {gap.join([VALID[:4], VALID[4:8], VALID[8:]])} end"
    assert contains_full_aadhaar(text)
    assert VALID[:4] not in mask_aadhaar(text)
    assert masked(VALID) in mask_aadhaar(text)
    assert masked(VALID) in redact(text)
    assert [m.verhoeff for m in find_aadhaar(text)] == [True]


def test_PRV_015_telugu_and_devanagari_digits_are_masked() -> None:
    for table in (TELUGU_DIGITS, DEVANAGARI_DIGITS):
        out = redact(f"సంఖ్య {spaced(VALID).translate(table)}")
        assert out == f"సంఖ్య {masked(VALID)}"


def test_PRV_015_invalid_checksum_without_context_is_untouched() -> None:
    assert redact(INVALID) == INVALID
    assert redact(f"Receipt {spaced(INVALID)} paid") == f"Receipt {spaced(INVALID)} paid"


@pytest.mark.parametrize(
    "template",
    [
        "Aadhaar: {n}",
        "aadhar no {n}",
        "UID {n}",
        "UIDAI ref {n}",
        "ఆధార్ {n}",
        "आधार {n}",
        "{n} (Aadhaar)",
    ],
)
def test_PRV_015_invalid_checksum_in_aadhaar_context_is_masked(template: str) -> None:
    text = template.format(n=spaced(INVALID))
    out = redact(text)
    assert INVALID not in re.sub(r"\D", "", out)
    assert masked(INVALID) in out


def test_PRV_015_context_further_than_20_chars_does_not_apply() -> None:
    text = "Aadhaar" + " filler text here, more " + spaced(INVALID)
    assert redact(text) == text


def test_PRV_015_uid_inside_word_is_not_context() -> None:
    text = f"guide {spaced(INVALID)}"
    assert redact(text) == text


@pytest.mark.parametrize("extra", ["1", "12", "0000"])
def test_PRV_015_contiguous_runs_longer_than_12_are_not_aadhaar(extra: str) -> None:
    for text in (VALID + extra, extra + VALID):
        # 13+ contiguous digits are some other identifier; never partially masked.
        assert mask_aadhaar(text) == text


def test_PRV_015_contains_full_aadhaar() -> None:
    assert contains_full_aadhaar(f"no {spaced(VALID)}")
    assert contains_full_aadhaar(VALID.translate(TELUGU_DIGITS))
    assert not contains_full_aadhaar(f"no {spaced(INVALID)}")
    assert not contains_full_aadhaar("last4 only: 1234")
    assert not contains_full_aadhaar(VALID + "7")


def test_PRV_015_mask_aadhaar_leaves_phones_and_emails() -> None:
    text = "call 9876543210 or mail ravi@example.org"
    assert mask_aadhaar(text) == text


def test_PRV_015_redact_normalises_to_nfc() -> None:
    decomposed = unicodedata.normalize("NFD", "café")
    assert redact(decomposed) == "café"


# --- Mobiles and emails --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("9876543210", "XXXXXX3210"),
        ("ph 98765 43210.", "ph XXXXXX3210."),
        ("+91 98765 43210", "XXXXXX3210"),
        ("+91-98765-43210", "XXXXXX3210"),
        ("+919876543210", "XXXXXX3210"),
        ("09876543210", "XXXXXX3210"),
        ("0 98765-43210", "XXXXXX3210"),
        ("తల్లి ఫోన్ 7012345678", "తల్లి ఫోన్ XXXXXX5678"),
        ("5123456789", "5123456789"),  # does not start with 6-9: not a mobile
        ("Roll 12 of 40", "Roll 12 of 40"),
        ("2026-09-26", "2026-09-26"),
    ],
)
def test_SEC_008_mobile_numbers_are_masked(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ravi.kumar@example.org", "[redacted-email]"),
        ("Mail: Sai_V+school@mail.example.co.in!", "Mail: [redacted-email]!"),
        ("<a.b@c.io>", "<[redacted-email]>"),
        ("no email here @ all", "no email here @ all"),
    ],
)
def test_SEC_008_emails_are_masked(text: str, expected: str) -> None:
    assert redact(text) == expected


def test_SEC_008_aadhaar_takes_priority_over_mobile() -> None:
    # A valid Aadhaar whose tail could look like a mobile is still masked as Aadhaar.
    text = f"98 {spaced(VALID)}"
    out = redact(text)
    assert VALID not in re.sub(r"\D", "", out)
    assert masked(VALID) in out


# --- Properties ----------------------------------------------------------------------------

NON_DIGIT_TEXT = st.text(
    alphabet=st.one_of(
        st.characters(min_codepoint=0x20, max_codepoint=0x7E, exclude_categories=["Nd"]),
        st.characters(min_codepoint=0x0C00, max_codepoint=0x0C7F, exclude_categories=["Nd"]),
    ),
    max_size=40,
)


@st.composite
def valid_numbers(draw: st.DrawFn) -> str:
    body = str(draw(st.integers(2, 9))) + "".join(
        str(d) for d in draw(st.lists(st.integers(0, 9), min_size=10, max_size=10))
    )
    return body + verhoeff_check_digit(body)


@st.composite
def formatted(draw: st.DrawFn, number: str) -> str:
    style = draw(st.sampled_from(["plain", "444", "hyphen", "free", "telugu"]))
    if style == "444":
        return spaced(number)
    if style == "hyphen":
        return hyphenated(number)
    if style == "telugu":
        return spaced(number).translate(TELUGU_DIGITS)
    if style == "free":
        cuts = sorted(draw(st.sets(st.integers(1, 11), max_size=4)))
        parts, prev = [], 0
        for cut in [*cuts, 12]:
            parts.append(number[prev:cut])
            prev = cut
        return " ".join(parts)
    return number


def digits_only(text: str) -> str:
    return "".join(str(unicodedata.digit(c)) for c in text if unicodedata.category(c) == "Nd")


@settings(max_examples=300)
@given(prefix=NON_DIGIT_TEXT, suffix=NON_DIGIT_TEXT, data=st.data(), number=valid_numbers())
def test_PRV_015_property_every_valid_number_is_masked(
    prefix: str, suffix: str, data: st.DataObject, number: str
) -> None:
    text = prefix + data.draw(formatted(number)) + suffix
    out = redact(text)
    assert number not in digits_only(out)
    assert contains_full_aadhaar(text)
    assert not contains_full_aadhaar(out)


@settings(max_examples=300)
@given(
    prefix=NON_DIGIT_TEXT,
    suffix=NON_DIGIT_TEXT,
    data=st.data(),
    numbers=st.lists(valid_numbers(), min_size=1, max_size=3),
    phone=st.from_regex(r"[6-9][0-9]{9}", fullmatch=True),
)
def test_PRV_015_property_masking_is_idempotent(
    prefix: str, suffix: str, data: st.DataObject, numbers: list[str], phone: str
) -> None:
    body = " ".join(data.draw(formatted(n)) for n in numbers)
    text = f"{prefix}{body} {phone} x@y.in{suffix}"
    once = redact(text)
    assert redact(once) == once
    assert mask_aadhaar(mask_aadhaar(text)) == mask_aadhaar(text)


@settings(max_examples=300)
@given(prefix=NON_DIGIT_TEXT, suffix=NON_DIGIT_TEXT, number=valid_numbers(), grouped=st.booleans())
def test_PRV_015_property_invalid_numbers_without_context_unchanged(
    prefix: str, suffix: str, number: str, grouped: bool
) -> None:
    invalid = make_invalid(number)
    context = re.compile(r"aa?dhaa?r|adhaa?r|uid|ఆధార్|आधार", re.IGNORECASE)
    assume(not context.search(prefix + suffix))
    assume("@" not in prefix + suffix)
    # 91 + [6-9]... is a mobile with country code; that is masked on purpose.
    assume(not re.fullmatch(r"91[6-9]\d{9}", invalid))
    text = prefix + (spaced(invalid) if grouped else invalid) + suffix
    assert redact(text) == unicodedata.normalize("NFC", text)


# --- UUIDs are identifiers, never Aadhaar candidates (false-positive fix) ----------------------

KNOWN_COLLISION = "a37058d2-1459-4055-8683-a2308be1de0d"  # digits across hyphens pass Verhoeff


def test_PRV_015_known_uuid_collision_is_not_aadhaar() -> None:
    assert not contains_full_aadhaar(KNOWN_COLLISION)
    assert mask_aadhaar(KNOWN_COLLISION) == KNOWN_COLLISION
    assert redact(f"student {KNOWN_COLLISION} updated") == f"student {KNOWN_COLLISION} updated"


@given(st.uuids())
def test_PRV_015_uuids_are_never_flagged_or_masked(value: uuid.UUID) -> None:
    for text in (str(value), str(value).upper(), f"id={value};", f"{value} {value}"):
        assert not contains_full_aadhaar(text)
        assert mask_aadhaar(text) == text


def test_PRV_015_real_aadhaar_next_to_a_uuid_is_still_found() -> None:
    body = "23456789012"
    number = body + verhoeff_check_digit(body)
    text = f"{KNOWN_COLLISION} {number[:4]} {number[4:8]} {number[8:]}"
    assert contains_full_aadhaar(text)
    masked = mask_aadhaar(text)
    assert masked.startswith(KNOWN_COLLISION)
    assert number not in masked.replace(" ", "")


# Request and trace ids are UUIDs written as 32 hex characters without hyphens; their digit
# runs look like mobile numbers (6-9 + 9 digits) or Aadhaar numbers now and then (SEC-008).
KNOWN_HEX_COLLISION = "req_01a0e0b9d6597733935fa0f8f875a46f"  # "6597733935" reads as a mobile


def test_SEC_008_known_hex_id_collision_is_not_a_phone() -> None:
    assert redact(f"request {KNOWN_HEX_COLLISION} done") == f"request {KNOWN_HEX_COLLISION} done"
    assert not contains_full_aadhaar(KNOWN_HEX_COLLISION)


@given(st.uuids())
def test_SEC_008_hex_ids_are_never_masked(value: uuid.UUID) -> None:
    for text in (value.hex, f"req_{value.hex}", f"trace_id={value.hex}"):
        assert redact(text) == text
        assert not contains_full_aadhaar(text)


def test_SEC_008_numbers_next_to_hex_ids_are_still_masked() -> None:
    body = "23456789012"
    number = body + verhoeff_check_digit(body)
    text = f"{KNOWN_HEX_COLLISION} 9876543210 {number}"
    masked = redact(text)
    assert masked.startswith(KNOWN_HEX_COLLISION)
    assert "9876543210" not in masked
    assert number not in masked
    assert contains_full_aadhaar(text)


# --- positions for image redaction (PRV-016) ------------------------------------------------


def test_PRV_016_find_aadhaar_reports_positions_digits_and_checksum() -> None:
    spaced = f"{VALID[:4]} {VALID[4:8]} {VALID[8:]}"
    text = f"Name: Synthetica  UID {spaced} / roll 12"
    found = find_aadhaar(text)
    assert found == [
        AadhaarMatch(text.index(spaced), text.index(spaced) + len(spaced), VALID, verhoeff=True)
    ]
    assert text[found[0].start : found[0].end] == spaced


def test_PRV_016_find_aadhaar_matches_what_mask_aadhaar_masks() -> None:
    # Keyword-near invalid numbers are masked too, so they are reported (verhoeff=False).
    text = f"Aadhaar {INVALID} and {VALID_2} and plain {INVALID}"
    found = find_aadhaar(text)
    assert [(m.digits, m.verhoeff) for m in found] == [(INVALID, False), (VALID_2, True)]
    masked = mask_aadhaar(text)
    assert masked.count(INVALID) == 1, "only the plain invalid number is left"
    assert VALID_2 not in masked
    assert text[found[0].start : found[0].end] == INVALID


def test_PRV_016_find_aadhaar_reports_windows_not_merged_regions() -> None:
    # Each qualifying 12-digit window is reported on its own (mask_aadhaar merges overlapping
    # ones), so callers can compare the windows found in different texts of one page.
    second = VALID[4:] + "123" + verhoeff_check_digit(VALID[4:] + "123")
    text = f"{VALID[:4]} {VALID[4:8]} {VALID[8:]} {second[-4:]}"
    found = find_aadhaar(text)
    assert [m.digits for m in found] == [VALID, second]
    assert found[0].start < found[1].start < found[0].end


def test_PRV_016_aadhaar_match_repr_never_shows_the_digits() -> None:
    # A match that ends up in a log line, an assertion message or a traceback must not carry
    # the number (invariants 4 and 5).
    match = find_aadhaar(f"{VALID[:4]} {VALID[4:8]} {VALID[8:]}")[0]
    for shown in (repr(match), str(match), f"{match!r}"):
        assert VALID not in shown
        assert VALID[:4] not in shown


def test_PRV_016_find_aadhaar_ignores_phones_uuids_and_long_blocks() -> None:
    assert find_aadhaar("+91 98765 43210") == []
    assert find_aadhaar(str(KNOWN_COLLISION)) == []
    assert find_aadhaar(VALID + "7") == []
    assert find_aadhaar("") == []


@settings(max_examples=200)
@given(prefix=NON_DIGIT_TEXT, suffix=NON_DIGIT_TEXT, data=st.data(), number=valid_numbers())
def test_PRV_016_property_every_valid_number_is_located(
    prefix: str, suffix: str, data: st.DataObject, number: str
) -> None:
    text = unicodedata.normalize("NFC", prefix + data.draw(formatted(number)) + suffix)
    found = [m for m in find_aadhaar(text) if m.digits == number]
    assert found
    assert found[0].verhoeff
    assert digits_only(text[found[0].start : found[0].end]) == number
