"""Cleaning and mandatory Aadhaar redaction (docs/06 §4.3; invariant 4, PRV-013).

Every block of raw extracted text goes through here before it is stored, indexed, logged,
chunked or embedded: Unicode NFC, control and format characters removed (ZWJ/ZWNJ kept: Telugu
needs them), words hyphenated across line breaks re-joined, whitespace collapsed (so a number
wrapped over two lines is seen as one run by the redactor), then ``core.redaction.mask_aadhaar``
(12-digit Verhoeff-valid sequences, or near an Aadhaar keyword, become ``XXXX XXXX 1234``). Table
rows keep one row per line. Blocks without a declared language get one by script.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from app.core.redaction import find_aadhaar, mask_aadhaar
from app.knowledge.chunking import detect_language
from app.knowledge.domain import ExtractedBlock, ExtractedDocument, ExtractedPage

_KEEP_FORMAT: Final = frozenset({"‌", "‍"})  # ZWNJ, ZWJ (Indic shaping)
_HYPHENATED: Final = re.compile(r"(\w)-\n(\w)")


@dataclass(frozen=True, slots=True)
class CleanResult:
    document: ExtractedDocument
    redactions: int
    """How many Aadhaar-like numbers were masked (a count, safe to log)."""


def _strip_controls(text: str) -> str:
    out = []
    for ch in text:
        category = unicodedata.category(ch)
        if ch in "\n\t":
            out.append(ch)
        elif category == "Cc" or (category == "Cf" and ch not in _KEEP_FORMAT):
            out.append(" " if category == "Cc" else "")
        else:
            out.append(ch)
    return "".join(out)


class Redactor:
    """``mask_aadhaar`` that counts what it masked (the count, never the digits)."""

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, text: str) -> str:
        masked = mask_aadhaar(text)
        if masked != text:
            self.count += max(1, len(find_aadhaar(text)))
        return masked


def _line(text: str) -> str:
    return " ".join(text.split())


def clean_text(text: str, redact: Redactor, *, keep_lines: bool = False) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = _strip_controls(text)
    if keep_lines:
        lines = (_line(line) for line in text.split("\n"))
        return "\n".join(redact(line) for line in lines if line)
    return redact(_line(_HYPHENATED.sub(r"\1\2", text)))


def clean_document(
    pages: Sequence[ExtractedPage],
    *,
    tenant_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    version_no: int,
    dominant_share: float,
) -> CleanResult:
    redact = Redactor()
    out_pages: list[ExtractedPage] = []
    for page in pages:
        blocks: list[ExtractedBlock] = []
        for block in page.blocks:
            if block.kind == "page_break":
                blocks.append(block)
                continue
            is_table = block.kind == "table"
            text = clean_text(block.text, redact, keep_lines=is_table)
            header = tuple(clean_text(c, redact) for c in block.table_header)
            if not text and not any(header):
                continue
            language = block.language or detect_language("\n".join([*header, text]), dominant_share)
            blocks.append(
                ExtractedBlock(
                    kind=block.kind,
                    text=text,
                    level=block.level,
                    language=language,
                    table_header=header,
                )
            )
        out_pages.append(
            ExtractedPage(
                page_no=page.page_no, blocks=tuple(blocks), ocr_confidence=page.ocr_confidence
            )
        )
    document = ExtractedDocument(
        tenant_id=tenant_id,
        document_id=document_id,
        version_id=version_id,
        version_no=version_no,
        pages=tuple(out_pages),
    )
    return CleanResult(document=document, redactions=redact.count)


__all__ = ["CleanResult", "Redactor", "clean_document", "clean_text"]
