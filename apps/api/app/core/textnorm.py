"""Name normalisation and Telugu→Latin transliteration (docs/02 §6, FR-STU-010, FR-DQ-003).

Shared by ``students`` (search keys written with each value) and ``dq`` (match classes), so it
lives in ``core``. Pure functions, no I/O.

Transliteration is table-driven (ISO 15919-style, simplified to ASCII for matching keys):
consonants carry an inherent ``a`` unless followed by a vowel sign or virama; anusvara becomes
``m``/``n``; long vowels are folded to short ones in the *comparison key* because school records
spell them inconsistently (``SEETHA``/``SITA``). The key is for matching only, never for display.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Final

# --- Telugu tables (Unicode block U+0C00–U+0C7F) ------------------------------------------------

_VOWELS: Final[dict[str, str]] = {
    "అ": "a", "ఆ": "aa", "ఇ": "i", "ఈ": "ee", "ఉ": "u", "ఊ": "oo", "ఋ": "ru", "ౠ": "ruu",
    "ఎ": "e", "ఏ": "ee", "ఐ": "ai", "ఒ": "o", "ఓ": "oo", "ఔ": "au", "ఌ": "lu", "ౡ": "luu",
}  # fmt: skip
_VOWEL_SIGNS: Final[dict[str, str]] = {
    "ా": "aa", "ి": "i", "ీ": "ee", "ు": "u", "ూ": "oo", "ృ": "ru", "ౄ": "ruu",
    "ె": "e", "ే": "ee", "ై": "ai", "ొ": "o", "ో": "oo", "ౌ": "au", "ౢ": "lu", "ౣ": "luu",
}  # fmt: skip
_CONSONANTS: Final[dict[str, str]] = {
    "క": "k", "ఖ": "kh", "గ": "g", "ఘ": "gh", "ఙ": "n",
    "చ": "ch", "ఛ": "chh", "జ": "j", "ఝ": "jh", "ఞ": "n",
    "ట": "t", "ఠ": "th", "డ": "d", "ఢ": "dh", "ణ": "n",
    "త": "t", "థ": "th", "ద": "d", "ధ": "dh", "న": "n",
    "ప": "p", "ఫ": "ph", "బ": "b", "భ": "bh", "మ": "m",
    "య": "y", "ర": "r", "ఱ": "r", "ల": "l", "ళ": "l", "వ": "v",
    "శ": "sh", "ష": "sh", "స": "s", "హ": "h",
    "ౘ": "ts", "ౙ": "dz", "ౚ": "r",
}  # fmt: skip
_VIRAMA: Final = "్"
_ANUSVARA: Final = "ం"
_VISARGA: Final = "ః"
_CHANDRABINDU: Final = "ఁ"
_TELUGU_DIGITS: Final = {chr(0x0C66 + i): str(i) for i in range(10)}
_LABIALS: Final = frozenset("pbm")

_TELUGU_RE: Final = re.compile(r"[ఀ-౿]")
_SPLIT_RE: Final = re.compile(r"[\s.\-_,/]+")


def has_telugu(text: str) -> bool:
    return bool(_TELUGU_RE.search(text))


def transliterate_telugu(text: str) -> str:
    """Transliterate Telugu script to lowercase ASCII Latin; other characters pass through."""
    text = unicodedata.normalize("NFC", text)
    out: list[str] = []
    chars = list(text)
    i = 0
    while i < len(chars):
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if ch in _CONSONANTS:
            base = _CONSONANTS[ch]
            if nxt == _VIRAMA:
                out.append(base)
                i += 2
                continue
            if nxt in _VOWEL_SIGNS:
                out.append(base + _VOWEL_SIGNS[nxt])
                i += 2
                continue
            out.append(base + "a")
        elif ch in _VOWELS:
            out.append(_VOWELS[ch])
        elif ch == _ANUSVARA:
            following = _CONSONANTS.get(nxt, "")
            out.append("m" if following[:1] in _LABIALS else "n")
        elif ch in (_VISARGA, _CHANDRABINDU):
            out.append("h" if ch == _VISARGA else "n")
        elif ch in _TELUGU_DIGITS:
            out.append(_TELUGU_DIGITS[ch])
        elif ch in _VOWEL_SIGNS or ch == _VIRAMA or has_telugu(ch):
            pass  # stray combining mark, or a rare sign with no letter value in names
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# --- normalisation ----------------------------------------------------------------------------


@dataclass(frozen=True)
class NameTokens:
    """Tokens of a normalised name; ``initials`` are single letters (``K.`` → ``K``)."""

    tokens: tuple[str, ...]

    @property
    def initials(self) -> tuple[str, ...]:
        return tuple(t for t in self.tokens if len(t) == 1)

    @property
    def words(self) -> tuple[str, ...]:
        return tuple(t for t in self.tokens if len(t) > 1)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def normalize_name(text: str) -> str:
    """Display-safe normalised form: NFC, trimmed, single spaces, Latin upper-cased.

    Telugu script is kept (not transliterated) so the original can be shown.
    """
    text = nfc(text).strip()
    text = re.sub(r"\s+", " ", text)
    return text.upper()


def comparison_key(text: str) -> str:
    """Latin, upper-case, space-separated key used for matching and trigram search.

    Telugu script is transliterated; punctuation (dots, hyphens) becomes a separator.
    """
    text = nfc(text)
    if has_telugu(text):
        text = transliterate_telugu(text)
    tokens = [t for t in _SPLIT_RE.split(text.upper()) if t]
    return " ".join(tokens)


def tokenize(text: str) -> NameTokens:
    key = comparison_key(text)
    return NameTokens(tuple(key.split(" ")) if key else ())


_PHONETIC_RULES: Final[tuple[tuple[str, str], ...]] = (
    ("TH", "T"),
    ("DH", "D"),
    ("KH", "K"),
    ("GH", "G"),
    ("BH", "B"),
    ("PH", "P"),
    ("SH", "S"),
    ("W", "V"),
    ("EE", "I"),
    ("OO", "U"),
    ("AA", "A"),
    ("Z", "J"),
    ("Q", "K"),
    ("Y", "I"),
)


def phonetic_key(text: str) -> str:
    """Light phonetic key (docs/02 §6 step 4): folds aspirates, long vowels and doubles.

    Applied per token; tokens keep their order so ORDER checks still work on top of it.
    """
    out: list[str] = []
    for token in comparison_key(text).split(" "):
        if not token:
            continue
        t = token
        for src, dst in _PHONETIC_RULES:
            t = t.replace(src, dst)
        t = re.sub(r"(.)\1+", r"\1", t)  # collapse doubled letters
        if len(t) > 1 and t.endswith("A"):
            t = t[:-1]  # trailing inherent vowel: SAI vs SAIA, RAMA vs RAM
        out.append(t)
    return " ".join(out)
