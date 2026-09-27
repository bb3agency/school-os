"""Text extraction (docs/06 §4.2): DOCX, plain text and the PDF text layer. Output is RAW (not
yet cleaned or redacted); :mod:`app.knowledge.ingestion.clean` must run before anything is
stored, logged, chunked or embedded (invariant 4).

- **DOCX** is read with the standard library ``zipfile`` and ``defusedxml`` (already a
  dependency; no XXE, entity expansion or DTDs), not python-docx: paragraphs in body order
  (including content controls), headings from paragraph styles (``styles.xml`` names
  ``heading N``/``Title`` or an outline level), numbered/bulleted paragraphs as list items,
  tables as rows of ``cell | cell`` with the first row as header, and page breaks (explicit
  breaks and Word's last rendered page breaks) for page references. Deleted revisions, field
  codes, headers and footers are not read. Each XML part is size-capped before parsing
  (zip-bomb guard, ``extraction.max_xml_bytes``).
- **Plain text** (UTF-8): paragraphs separated by blank lines; form feeds start a new page.
- **PDF text layer** (ADR-0027): :mod:`app.knowledge.ingestion.pdf` with pypdfium2, imported
  only when a PDF is extracted (the ingest worker), with its own limits (``extraction.pdf``);
  encrypted PDFs are refused and scanned or mojibake pages fail with ``needs_ocr``.

Failures raise :class:`ExtractionFailed` with a code only; never the file's text.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Final
from xml.etree.ElementTree import Element, ParseError

from defusedxml import DefusedXmlException  # type: ignore[import-untyped]
from defusedxml.ElementTree import fromstring  # type: ignore[import-untyped]

from app.knowledge.chunking import TABLE_CELL_SEPARATOR
from app.knowledge.config.chunking import ExtractionConfig
from app.knowledge.domain import BlockKind, ExtractedBlock, ExtractedPage

DOCX_MIME: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIME: Final = "text/plain"
PDF_MIME: Final = "application/pdf"

_W: Final = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_NAME: Final = re.compile(r"^heading\s*([1-9])$")
_MAX_OUTLINE_LEVEL: Final = 9
_PARAGRAPH_SPLIT: Final = re.compile(r"\n(?:[ \t\v]*\n)+")


class ExtractionFailed(Exception):
    """Extraction refused or failed; ``code`` is safe to log and store (never file text)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def extract_pages(data: bytes, mime_type: str, limits: ExtractionConfig) -> list[ExtractedPage]:
    """Raw pages of a file of a supported type (``extraction.supported_mime_types``)."""
    mime = mime_type.split(";", 1)[0].strip().lower()
    if mime not in limits.supported_mime_types:
        raise ExtractionFailed("unsupported_type")
    if mime == DOCX_MIME:
        pages = _docx(data, limits)
    elif mime == TEXT_MIME:
        pages = _plain(data)
    elif mime == PDF_MIME:
        # Lazy: only the ingest path loads the native PDFium library (ADR-0027).
        from app.knowledge.ingestion.pdf import extract_pdf  # noqa: PLC0415

        pages = extract_pdf(data, limits)
    else:  # pragma: no cover - configured but not implemented
        raise ExtractionFailed("unsupported_type")
    chars = sum(len(b.text) + sum(len(c) for c in b.table_header) for p in pages for b in p.blocks)
    if chars > limits.max_text_chars:
        raise ExtractionFailed("text_too_large")
    return pages


# --- page assembly ------------------------------------------------------------------------------


@dataclass(slots=True)
class _Pages:
    pages: list[ExtractedPage] = field(default_factory=list)
    blocks: list[ExtractedBlock] = field(default_factory=list)
    page_no: int = 1

    def add(self, block: ExtractedBlock) -> None:
        self.blocks.append(block)

    def page_break(self) -> None:
        """A new page starts, unless the current one is still empty (Word writes both an
        explicit break and a rendered break for the same page boundary)."""
        if not self.blocks:
            return
        self.pages.append(ExtractedPage(page_no=self.page_no, blocks=tuple(self.blocks)))
        self.blocks = []
        self.page_no += 1

    def done(self) -> list[ExtractedPage]:
        if self.blocks:
            self.pages.append(ExtractedPage(page_no=self.page_no, blocks=tuple(self.blocks)))
        return self.pages


# --- plain text ---------------------------------------------------------------------------------


def _plain(data: bytes) -> list[ExtractedPage]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ExtractionFailed("not_utf8") from exc
    out = _Pages()
    for i, page in enumerate(text.replace("\r\n", "\n").split("\f")):
        if i:
            out.page_break()
        for paragraph in _PARAGRAPH_SPLIT.split(page):
            if paragraph.strip():
                out.add(ExtractedBlock(kind="paragraph", text=paragraph))
    return out.done()


# --- DOCX ---------------------------------------------------------------------------------------


def _read_part(archive: zipfile.ZipFile, name: str, limit: int) -> bytes | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        raise ExtractionFailed("part_too_large")
    with archive.open(info) as fh:
        data = fh.read(limit + 1)
    if len(data) > limit:  # the header lied about the size
        raise ExtractionFailed("part_too_large")
    return data


def _parse(data: bytes) -> Element:
    try:
        root: Element = fromstring(data)
    except (ParseError, DefusedXmlException) as exc:
        raise ExtractionFailed("corrupt") from exc
    return root


def _attr(element: Element | None, name: str) -> str | None:
    if element is None:
        return None
    return element.get(f"{_W}{name}")


def _heading_styles(styles: Element | None) -> dict[str, int]:
    """``styleId -> heading level`` from ``styles.xml`` (localised IDs keep English names)."""
    levels: dict[str, int] = {}
    if styles is None:
        return levels
    for style in styles.iter(f"{_W}style"):
        style_id = _attr(style, "styleId")
        if style_id is None or _attr(style, "type") not in (None, "paragraph"):
            continue
        name = (_attr(style.find(f"{_W}name"), "val") or "").strip().lower()
        if name == "title":
            levels[style_id] = 1
        elif m := _HEADING_NAME.match(name):
            levels[style_id] = int(m[1])
        else:
            level = _outline_level(style.find(f"{_W}pPr"))
            if level is not None:
                levels[style_id] = level
    return levels


def _outline_level(ppr: Element | None) -> int | None:
    if ppr is None:
        return None
    value = _attr(ppr.find(f"{_W}outlineLvl"), "val")
    if value is None or not value.isdigit() or int(value) >= _MAX_OUTLINE_LEVEL:
        return None
    return int(value) + 1


def _docx(data: bytes, limits: ExtractionConfig) -> list[ExtractedPage]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, ValueError) as exc:
        raise ExtractionFailed("corrupt") from exc
    with archive:
        try:
            document = _read_part(archive, "word/document.xml", limits.max_xml_bytes)
            styles = _read_part(archive, "word/styles.xml", limits.max_xml_bytes)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError) as exc:
            raise ExtractionFailed("corrupt") from exc
    if document is None:
        raise ExtractionFailed("corrupt")
    root = _parse(document)
    body = root.find(f"{_W}body")
    if body is None:
        raise ExtractionFailed("corrupt")
    walker = _DocxWalker(_heading_styles(_parse(styles) if styles is not None else None))
    for element in _body_elements(body):
        if element.tag == f"{_W}p":
            walker.paragraph(element)
        else:
            walker.table(element)
    return walker.out.done()


def _body_elements(container: Element) -> Iterator[Element]:
    """Paragraphs and tables in document order, looking inside content controls."""
    for child in container:
        if child.tag in (f"{_W}p", f"{_W}tbl"):
            yield child
        elif child.tag in (f"{_W}sdt", f"{_W}customXml"):
            content = child.find(f"{_W}sdtContent")
            yield from _body_elements(content if content is not None else child)


class _DocxWalker:
    def __init__(self, heading_styles: dict[str, int]) -> None:
        self.heading_styles = heading_styles
        self.out = _Pages()

    def _kind(self, ppr: Element | None) -> tuple[BlockKind, int | None]:
        if ppr is not None:
            style = _attr(ppr.find(f"{_W}pStyle"), "val")
            level = _outline_level(ppr)
            if level is None and style is not None:
                level = self.heading_styles.get(style)
            if level is not None:
                return "heading", level
            if ppr.find(f"{_W}numPr") is not None:
                return "list_item", None
        return "paragraph", None

    def paragraph(self, p: Element) -> None:
        ppr = p.find(f"{_W}pPr")
        kind, level = self._kind(ppr)
        if ppr is not None and ppr.find(f"{_W}pageBreakBefore") is not None:
            self.out.page_break()
        parts: list[str] = []

        def emit() -> None:
            text = "".join(parts)
            parts.clear()
            if text.strip():
                self.out.add(ExtractedBlock(kind=kind, text=text, level=level))

        for element in p.iter():
            tag = element.tag
            if tag == f"{_W}t":
                parts.append(element.text or "")
            elif tag in (f"{_W}tab", f"{_W}cr"):
                parts.append(" " if tag == f"{_W}tab" else "\n")
            elif tag == f"{_W}noBreakHyphen":
                parts.append("-")
            elif tag == f"{_W}br":
                if _attr(element, "type") == "page":
                    emit()
                    self.out.page_break()
                else:
                    parts.append("\n")
            elif tag == f"{_W}lastRenderedPageBreak":
                emit()
                self.out.page_break()
        emit()

    def table(self, tbl: Element) -> None:
        rows: list[list[str]] = []
        for tr in tbl.findall(f"{_W}tr"):
            cells = [_cell_text(tc) for tc in tr.findall(f"{_W}tc")]
            if any(c.strip() for c in cells):
                rows.append(cells)
        if not rows:
            return
        header: tuple[str, ...] = ()
        if len(rows) > 1:
            header, rows = tuple(rows[0]), rows[1:]
        text = "\n".join(TABLE_CELL_SEPARATOR.join(cells) for cells in rows)
        self.out.add(ExtractedBlock(kind="table", text=text, table_header=header))


def _cell_text(tc: Element) -> str:
    paragraphs = []
    for p in tc.iter(f"{_W}p"):
        text = "".join(
            (t.text or "") if t.tag == f"{_W}t" else " "
            for t in p.iter()
            if t.tag in (f"{_W}t", f"{_W}tab", f"{_W}br", f"{_W}cr")
        )
        if text.strip():
            paragraphs.append(" ".join(text.split()))
    return " ".join(paragraphs)


__all__ = ["DOCX_MIME", "PDF_MIME", "TEXT_MIME", "ExtractionFailed", "extract_pages"]
