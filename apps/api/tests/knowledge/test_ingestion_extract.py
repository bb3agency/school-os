"""Extraction and cleaning (docs/06 §4.2-4.3; FR-DOC-001 types, invariant 4 / PRV-013).

DOCX and plain text become pages of structural blocks; hostile files (zip bombs, XML entities,
not a zip) fail with a code; cleaning normalises text and masks Aadhaar-like numbers before
anything else sees it. Synthetic content only.
"""

from __future__ import annotations

import importlib.util
import sys
import unicodedata
from pathlib import Path
from types import ModuleType

import pytest

from app.core.redaction import contains_full_aadhaar
from app.knowledge.domain import ExtractedBlock, ExtractedPage
from app.knowledge.ingestion.clean import Redactor, clean_document, clean_text
from app.knowledge.ingestion.extract import (
    DOCX_MIME,
    TEXT_MIME,
    ExtractionFailed,
    extract_pages,
)


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


S = _load("sos_test_ingestion_support", Path(__file__).with_name("ingestion_support.py"))
LIMITS = S.CONFIG.extraction


def blocks(pages: list[ExtractedPage]) -> list[tuple[int, str, str, int | None]]:
    return [(pg.page_no, b.kind, b.text, b.level) for pg in pages for b in pg.blocks]


# --- DOCX -----------------------------------------------------------------------------------------


def test_FR_DOC_001_docx_structure_headings_lists_tables_and_pages() -> None:
    pages = extract_pages(S.circular_docx(), DOCX_MIME, LIMITS)
    got = blocks(pages)
    assert got[0] == (1, "heading", "Half-yearly examinations 2026", 1)  # Title style
    assert (1, "paragraph", "Sub: Conduct of half-yearly examinations", None) in got
    assert (1, "heading", "1. Timings", 1) in got  # localised style id, English name
    assert (2, "heading", "2. Seating", 2) in got  # after the explicit page break
    table = next(b for pg in pages for b in pg.blocks if b.kind == "table")
    assert table.table_header == ("Class", "Room")
    assert table.text == "6 | R1\n7 | R2"
    assert got[-1] == (2, "list_item", "విద్యార్థులు సమయానికి పాఠశాలకు రావాలి.", None)


def test_docx_outline_level_and_rendered_page_breaks_count_once() -> None:
    body = (
        '<w:p><w:pPr><w:outlineLvl w:val="2"/></w:pPr><w:r><w:t>Deep heading</w:t></w:r></w:p>'
        "<w:p><w:r><w:t>first</w:t></w:r></w:p>"
        '<w:p><w:r><w:br w:type="page"/><w:lastRenderedPageBreak/><w:t>second</w:t></w:r></w:p>'
        "<w:p><w:pPr><w:pageBreakBefore/></w:pPr><w:r><w:t>third</w:t></w:r></w:p>"
    )
    got = blocks(extract_pages(S.docx(body), DOCX_MIME, LIMITS))
    assert got == [
        (1, "heading", "Deep heading", 3),
        (1, "paragraph", "first", None),
        (2, "paragraph", "second", None),
        (3, "paragraph", "third", None),
    ]


def test_docx_skips_deleted_revisions_and_field_codes_and_reads_content_controls() -> None:
    body = (
        "<w:p><w:r><w:t>kept</w:t></w:r><w:del><w:r><w:delText>removed</w:delText></w:r></w:del>"
        '<w:r><w:instrText> HYPERLINK "x" </w:instrText></w:r>'
        "<w:r><w:tab/><w:t>text</w:t></w:r></w:p>"
        "<w:sdt><w:sdtContent><w:p><w:r><w:t>inside control</w:t></w:r></w:p>"
        "</w:sdtContent></w:sdt>"
    )
    got = blocks(extract_pages(S.docx(body), DOCX_MIME, LIMITS))
    assert [text for _, _, text, _ in got] == ["kept text", "inside control"]


@pytest.mark.parametrize(
    ("data", "code"),
    [
        (b"not a zip file", "corrupt"),
        (b"PK\x03\x04garbage", "corrupt"),
    ],
)
def test_broken_docx_fails_with_a_code(data: bytes, code: str) -> None:
    with pytest.raises(ExtractionFailed) as err:
        extract_pages(data, DOCX_MIME, LIMITS)
    assert err.value.code == code


def test_SEC_docx_without_document_part_is_refused() -> None:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/other.xml", "<x/>")
    with pytest.raises(ExtractionFailed, match="corrupt"):
        extract_pages(buf.getvalue(), DOCX_MIME, LIMITS)


def test_SEC_xml_entities_are_refused() -> None:
    evil = (
        '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
        f'<w:document xmlns:w="{S.W_NS}"><w:body><w:p><w:r><w:t>&b;</w:t></w:r></w:p></w:body>'
        "</w:document>"
    )
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", evil)
    with pytest.raises(ExtractionFailed, match="corrupt"):
        extract_pages(buf.getvalue(), DOCX_MIME, LIMITS)


def test_SEC_oversized_xml_part_is_refused_before_parsing() -> None:
    limits = LIMITS.model_copy(update={"max_xml_bytes": 2048})
    data = S.docx(S.p("x" * 5000))  # compresses to a few hundred bytes
    with pytest.raises(ExtractionFailed, match="part_too_large"):
        extract_pages(data, DOCX_MIME, limits)


def test_too_much_text_is_refused() -> None:
    limits = LIMITS.model_copy(update={"max_text_chars": 1000})
    with pytest.raises(ExtractionFailed, match="text_too_large"):
        extract_pages(("word " * 400).encode(), TEXT_MIME, limits)


@pytest.mark.parametrize(
    "mime",
    ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "image/png", "text/csv"],
)
def test_unsupported_types_are_refused(mime: str) -> None:
    with pytest.raises(ExtractionFailed, match="unsupported_type"):
        extract_pages(b"%PDF-1.7", mime, LIMITS)


# --- plain text -----------------------------------------------------------------------------------


def test_plain_text_paragraphs_and_form_feed_pages() -> None:
    data = "﻿First para\nwraps here.\n\n  \nSecond para\fPage two".encode()
    got = blocks(extract_pages(data, "text/plain; charset=utf-8", LIMITS))
    assert got == [
        (1, "paragraph", "First para\nwraps here.", None),
        (1, "paragraph", "Second para", None),
        (2, "paragraph", "Page two", None),
    ]


def test_plain_text_must_be_utf8() -> None:
    with pytest.raises(ExtractionFailed, match="not_utf8"):
        extract_pages(b"\xff\xfe\x00bad", TEXT_MIME, LIMITS)


# --- cleaning and redaction -----------------------------------------------------------------------


def test_cleaning_normalises_text() -> None:
    redact = Redactor()
    decomposed = unicodedata.normalize("NFD", "café")
    assert clean_text(f"{decomposed}\x07 exam-\ninations \u00ad ok\u200b", redact) == (
        "café examinations ok"
    )
    assert clean_text("శ్రీ\u200cరామ", redact) == "శ్రీ\u200cరామ"  # ZWNJ kept
    assert clean_text(" a |  b \n\n c | d ", redact, keep_lines=True) == "a | b\nc | d"


def test_PRV_013_aadhaar_is_masked_even_when_wrapped_over_lines() -> None:
    redact = Redactor()
    wrapped = f"ref {S.SYNTHETIC_AADHAAR[:4]} {S.SYNTHETIC_AADHAAR[4:8]}\n{S.SYNTHETIC_AADHAAR[8:]}"
    assert clean_text(wrapped, redact) == f"ref {S.AADHAAR_MASK}"
    assert redact.count == 1


def test_PRV_013_clean_document_masks_text_table_cells_and_headers() -> None:
    page = ExtractedPage(
        page_no=1,
        blocks=(
            ExtractedBlock(kind="paragraph", text=f"number {S.AADHAAR_SPACED}"),
            ExtractedBlock(
                kind="table",
                text=f"1 | {S.SYNTHETIC_AADHAAR}",
                table_header=(f"No {S.SYNTHETIC_AADHAAR}", "Value"),
            ),
            ExtractedBlock(kind="paragraph", text="   "),
        ),
    )
    result = clean_document(
        [page],
        tenant_id=S.TENANT_A,
        document_id=S.TENANT_A,
        version_id=S.TENANT_A,
        version_no=1,
        dominant_share=0.8,
    )
    texts = [b.text for b in result.document.pages[0].blocks] + list(
        result.document.pages[0].blocks[1].table_header
    )
    assert len(result.document.pages[0].blocks) == 2  # the empty paragraph is dropped
    assert result.redactions == 3
    assert not any(contains_full_aadhaar(t) for t in texts)
    assert all(S.AADHAAR_MASK in t for t in texts if "XXXX" in t)
    assert result.document.pages[0].blocks[0].language == "en"
