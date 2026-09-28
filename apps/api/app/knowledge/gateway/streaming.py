"""Aadhaar masking for streamed model text (invariant 4; docs/06 §5.1).

``mask_aadhaar`` needs to see a whole number (digits with spaces or hyphens between groups) and
the words around it (a 12-digit number near "Aadhaar"/"ఆధార్" is masked even without a valid
Verhoeff digit). A stream arrives in arbitrary pieces, so :class:`AadhaarStreamMasker` holds back
the trailing run of digits and separators until a character that cannot continue a number
arrives, and masks each emitted piece together with the text just before it (the keyword
context), keeping only the new part. The complete turn is masked again as a whole by
:func:`app.knowledge.gateway.wire.parse_turn`; this only guarantees that no delta ever carries
an unmasked number.
"""

from __future__ import annotations

import re
from typing import Final

from app.core.redaction import mask_aadhaar

_TAIL: Final = re.compile(r"[+0-9 \u00a0\-\u2010-\u2013]*$")
"""Characters that may still continue a number at the end of the buffer."""
CONTEXT_CHARS: Final = 40
"""How much already-emitted text is used as keyword context (the redaction distance is 20).

A run of digits and separators is never split, however long: splitting could put the two halves
of one number into different deltas, which a client joins again. The run is bounded by the
role's output cap (``models.yaml``)."""


class AadhaarStreamMasker:
    def __init__(self) -> None:
        self._held = ""
        self._context = ""

    def _emit(self, piece: str) -> str:
        if not piece:
            return ""
        masked = mask_aadhaar(self._context + piece)
        if masked.startswith(self._context):
            out = masked[len(self._context) :]
        else:  # the context itself changed (cannot happen for masked context); mask alone
            out = mask_aadhaar(piece)
        self._context = (self._context + out)[-CONTEXT_CHARS:]
        return out

    def feed(self, text: str) -> str:
        """Masked text that is safe to show now (possibly empty)."""
        buffer = self._held + text
        match = _TAIL.search(buffer)
        cut = match.start() if match else len(buffer)
        self._held = buffer[cut:]
        return self._emit(buffer[:cut])

    def flush(self) -> str:
        """Whatever is still held, masked (the end of the stream)."""
        held, self._held = self._held, ""
        return self._emit(held)


__all__ = ["AadhaarStreamMasker"]
