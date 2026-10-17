"""Certificate verification codes (ADR-0045 decision 4; FR-CERT-019, FR-CERT-022).

Pure module. A code is 16 characters of Crockford's base32 alphabet (``0-9`` and ``A-Z``
without ``I L O U``): 32 symbols, so 16 characters carry exactly 80 bits, all from the OS CSPRNG
(:func:`secrets.randbits`). Nothing about the serial, the student or the school goes in: it is
random and nothing else. It prints in four groups (``7K4M-Q2XP-9HD3-RT6W``).

Look-alikes are handled the Crockford way when a code is typed (:func:`normalise`): case, spaces
and dashes are ignored, ``O`` reads as ``0`` and ``I``/``L`` as ``1``, so a verifier who misreads
the paper still lands on the right code. ``U`` never appears in a code and makes it invalid.
Only the normalised form is hashed into the public handle (``app.public``, ADR-0047).
"""

from __future__ import annotations

import re
import secrets
from typing import Final

ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LENGTH: Final = 16
GROUP: Final = 4
BITS: Final = 80
CODE_RE: Final = re.compile(r"^[0-9A-HJKMNP-TV-Z]{16}$")
# Spaces, ASCII dashes and the Unicode dashes U+2010..U+2015 a phone keyboard may insert.
_UNICODE_DASHES: Final = "".join(chr(c) for c in range(0x2010, 0x2016))
_IGNORED: Final = re.compile(r"[\s\-" + _UNICODE_DASHES + "]+")
_LOOK_ALIKES: Final = str.maketrans({"O": "0", "I": "1", "L": "1"})

if len(set(ALPHABET)) != 32 or LENGTH * 5 != BITS:  # pragma: no cover - import-time invariant
    raise RuntimeError("the verification code alphabet must have 32 symbols for 80 bits")


def new_code() -> str:
    """A fresh 16-character code (80 random bits)."""
    value = secrets.randbits(BITS)
    out = []
    for _ in range(LENGTH):
        value, digit = divmod(value, 32)
        out.append(ALPHABET[digit])
    return "".join(reversed(out))


def normalise(typed: str | None) -> str | None:
    """The canonical code for what someone typed or scanned, or ``None`` when it cannot be one.
    Case, spaces and dashes do not matter; ``O`` = ``0`` and ``I``/``L`` = ``1``."""
    if not isinstance(typed, str) or len(typed) > 64:
        return None
    text = _IGNORED.sub("", typed).upper().translate(_LOOK_ALIKES)
    return text if CODE_RE.fullmatch(text) else None


def grouped(code: str) -> str:
    """``7K4MQ2XP9HD3RT6W`` -> ``7K4M-Q2XP-9HD3-RT6W`` (as printed)."""
    return "-".join(code[i : i + GROUP] for i in range(0, len(code), GROUP))


__all__ = ["ALPHABET", "BITS", "CODE_RE", "LENGTH", "grouped", "new_code", "normalise"]
