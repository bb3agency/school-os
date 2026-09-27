"""PDF text layer (ADR-0027, docs/06 §4.2) with pypdfium2 (Google PDFium).

Imported lazily by :func:`app.knowledge.ingestion.extract.extract_pages`, so only the ingest
worker ever loads the native library (semgrep ``sos-pdfium-outside-ingestion`` keeps it that way).
The bytes are attacker-controlled (malware-scanned, but still hostile input), so:

- a file over ``pdf.max_bytes`` is not opened; an encrypted PDF (a user password, or only an
  owner password restricting copying) is refused; more than ``pdf.max_pages`` pages, more than
  ``pdf.max_objects_per_page`` objects or ``pdf.max_chars_per_page`` characters on a page, or
  running past ``pdf.time_budget_seconds`` (checked between pages) refuses the version;
- a page that looks scanned (graphics and fewer than ``pdf.min_letters_per_page`` letters) or
  whose text layer is mojibake (more than ``pdf.max_bad_char_share`` unmapped or private-use
  characters, as legacy non-Unicode Telugu fonts produce) is never indexed from its text layer:
  the whole version fails with ``needs_ocr`` so it can go to OCR later, instead of being indexed
  partly, empty, or as garbage. Blank pages are skipped; a PDF with no text at all needs OCR.

Each page's text becomes paragraph blocks (a line ending a sentence or a ``Sub:``-style heading
ends a block), with the PDF's own 1-based page numbers for citations (FR-DOC-006). PDFium drops
the line break after a word it saw hyphenated at a line end and marks the spot with U+FFFE; the
mark is removed so the word is whole again. Output is RAW: ``clean`` must still run (NFC and
Aadhaar masking, invariant 4). Failures carry a code only, never the file's text (invariant 5).
PDFium is not thread-safe, so extraction is serialised per process.
"""

from __future__ import annotations

import threading
import time
import unicodedata
from collections.abc import Callable
from typing import Final

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from app.knowledge.config.chunking import ExtractionConfig, PdfLimits
from app.knowledge.domain import ExtractedBlock, ExtractedPage
from app.knowledge.ingestion.extract import ExtractionFailed

_clock: Callable[[], float] = time.monotonic
_LOCK: Final = threading.Lock()
_LINE_HYPHEN: Final = "￾"
_BLOCK_END: Final = (".", "?", "!", ":", "।", "॥")
_TEXT_OBJECT: Final = pdfium_c.FPDF_PAGEOBJ_TEXT


def extract_pdf(data: bytes, limits: ExtractionConfig) -> list[ExtractedPage]:
    """Raw pages of a PDF's text layer, or :class:`ExtractionFailed` with a code."""
    rules = limits.pdf
    if len(data) > rules.max_bytes:
        raise ExtractionFailed("file_too_large")
    with _LOCK:
        document = _open(data)
        try:
            return _pages(document, rules, limits.max_text_chars)
        finally:
            document.close()


def _open(data: bytes) -> pdfium.PdfDocument:
    try:
        document = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        code = "encrypted" if exc.err_code == pdfium_c.FPDF_ERR_PASSWORD else "corrupt"
        raise ExtractionFailed(code) from None
    if pdfium_c.FPDF_GetSecurityHandlerRevision(document) != -1:
        document.close()
        raise ExtractionFailed("encrypted")
    return document


def _pages(document: pdfium.PdfDocument, rules: PdfLimits, max_chars: int) -> list[ExtractedPage]:
    count = len(document)
    if count > rules.max_pages:
        raise ExtractionFailed("too_many_pages")
    deadline = _clock() + rules.time_budget_seconds
    pages: list[ExtractedPage] = []
    total = 0
    for index in range(count):
        if _clock() > deadline:
            raise ExtractionFailed("time_budget_exceeded")
        text = _page_text(document, index, rules)
        if text is None:
            continue
        total += len(text)
        if total > max_chars:
            raise ExtractionFailed("text_too_large")
        blocks = tuple(ExtractedBlock(kind="paragraph", text=b) for b in _blocks(text))
        if blocks:
            pages.append(ExtractedPage(page_no=index + 1, blocks=blocks))
    if not pages:
        raise ExtractionFailed("needs_ocr")
    return pages


def _page_text(document: pdfium.PdfDocument, index: int, rules: PdfLimits) -> str | None:
    """The page's text, None for a blank page; ``needs_ocr`` for a scan or mojibake."""
    try:
        page = document[index]
    except pdfium.PdfiumError:
        raise ExtractionFailed("corrupt") from None
    try:
        objects = pdfium_c.FPDFPage_CountObjects(page)
        if objects > rules.max_objects_per_page:
            raise ExtractionFailed("page_too_complex")
        graphics = any(
            pdfium_c.FPDFPageObj_GetType(pdfium_c.FPDFPage_GetObject(page, i)) != _TEXT_OBJECT
            for i in range(objects)
        )
        textpage = page.get_textpage()
        try:
            chars = textpage.count_chars()
            if chars > rules.max_chars_per_page:
                raise ExtractionFailed("page_text_too_large")
            text = textpage.get_text_range() if chars > 0 else ""
        finally:
            textpage.close()
    finally:
        page.close()
    text = text.replace(_LINE_HYPHEN, "")
    letters, bad, visible = _census(text)
    if visible and bad / visible > rules.max_bad_char_share:
        raise ExtractionFailed("needs_ocr")
    if graphics and letters < rules.min_letters_per_page:
        raise ExtractionFailed("needs_ocr")
    return text if visible else None


def _census(text: str) -> tuple[int, int, int]:
    """(letters incl. combining marks, unmapped characters, non-space characters)."""
    letters = bad = visible = 0
    for ch in text:
        if ch.isspace():
            continue
        visible += 1
        category = unicodedata.category(ch)
        if category[0] in "LM":
            letters += 1
        elif ch == "�" or category in ("Co", "Cn", "Cs", "Cc"):
            bad += 1
    return letters, bad, visible


def _blocks(text: str) -> list[str]:
    """Lines joined into paragraphs; a line ending a sentence (or a ``Sub:`` label) ends one."""
    blocks: list[str] = []
    current: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
        if line.endswith(_BLOCK_END):
            blocks.append("\n".join(current))
            current = []
    if current:
        blocks.append("\n".join(current))
    return blocks


__all__ = ["extract_pdf"]
