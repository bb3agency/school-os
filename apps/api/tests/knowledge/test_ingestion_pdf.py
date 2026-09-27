"""PDF text-layer extraction (ADR-0027; FR-KB-001, FR-DOC-001, FR-DOC-006, PRV-013, invariants 4
and 5).

Per-page text with the PDF's own page numbers (for ``#p{page}`` citations); Telugu survives as
NFC; encrypted PDFs are refused; scanned pages and legacy-font mojibake are flagged
``needs_ocr`` instead of being indexed empty or as garbage; the size, page, object, character and
time limits stop hostile files; an Aadhaar-like number in a PDF never reaches the chunks; the
native library is loaded only by the ingest path. Synthetic, hand-built PDFs only.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unicodedata
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.core.redaction import contains_full_aadhaar
from app.knowledge.config.chunking import ExtractionConfig
from app.knowledge.domain import ExtractedPage
from app.knowledge.ingestion import pdf as pdf_module
from app.knowledge.ingestion.clean import clean_document
from app.knowledge.ingestion.extract import PDF_MIME, ExtractionFailed, extract_pages


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
P = _load("sos_test_pdf_support", Path(__file__).with_name("pdf_support.py"))
LIMITS: ExtractionConfig = S.CONFIG.extraction
DOC = uuid.UUID("0190e000-0000-7000-8000-0000000000f1")

TE_LINE_1 = "విద్యార్థులు సమయానికి పాఠశాలకు రావాలి."
TE_LINE_2 = "అర్ధ సంవత్సర పరీక్షలు సెప్టెంబరు 22 నుండి."


def limits(**changes: Any) -> ExtractionConfig:
    return LIMITS.model_copy(update={"pdf": LIMITS.pdf.model_copy(update=changes)})


def texts(pages: list[ExtractedPage]) -> list[tuple[int, str]]:
    return [(pg.page_no, b.text) for pg in pages for b in pg.blocks]


def failure(data: bytes, config: ExtractionConfig = LIMITS) -> str:
    with pytest.raises(ExtractionFailed) as exc:
        extract_pages(data, PDF_MIME, config)
    return exc.value.code


def cleaned(pages: list[ExtractedPage]) -> Any:
    return clean_document(
        pages,
        tenant_id=S.TENANT_A,
        document_id=DOC,
        version_id=DOC,
        version_no=1,
        dominant_share=S.CONFIG.language_dominant_share,
    )


# --- text and page numbers ------------------------------------------------------------------


def test_FR_KB_001_english_pdf_text_layer_with_page_numbers() -> None:
    data = P.pdf(
        [
            P.text_page(
                "Sub: Conduct of half-yearly exam-",
                "inations for classes 6 to 10.",
                "Ref: Proceedings of the DEO (Guntur) dated 12.08.2026",
            ),
            P.text_page("1. Examinations begin at 9.30 AM.", "2. Carry the hall ticket."),
        ]
    )
    got = texts(extract_pages(data, PDF_MIME, LIMITS))
    assert [page for page, _ in got] == [1, 1, 2, 2]
    # A word hyphenated over a line break is joined; a line ending a sentence ends the block.
    assert got[0] == (1, "Sub: Conduct of half-yearly examinations for classes 6 to 10.")
    assert got[1] == (1, "Ref: Proceedings of the DEO (Guntur) dated 12.08.2026")
    assert got[2] == (2, "1. Examinations begin at 9.30 AM.")
    assert got[3] == (2, "2. Carry the hall ticket.")


def test_FR_KB_001_telugu_pdf_text_survives_as_nfc_with_page_numbers() -> None:
    data = P.pdf([P.telugu_page(TE_LINE_1), P.Page(), P.telugu_page(TE_LINE_2)])
    pages = extract_pages(data, PDF_MIME, LIMITS)
    # The blank page 2 is skipped, but page 3 keeps its own number (citations use it).
    assert texts(pages) == [(1, TE_LINE_1), (3, TE_LINE_2)]
    document = cleaned(pages).document
    got = [(pg.page_no, b.text, b.language) for pg in document.pages for b in pg.blocks]
    assert got == [(1, TE_LINE_1, "te"), (3, TE_LINE_2, "te")]
    assert all(unicodedata.is_normalized("NFC", text) for _, text, _ in got)


def test_mixed_english_and_telugu_pages() -> None:
    data = P.pdf([P.text_page("Circular No. 14/2026: holidays."), P.telugu_page(TE_LINE_1)])
    assert texts(extract_pages(data, PDF_MIME, LIMITS)) == [
        (1, "Circular No. 14/2026: holidays."),
        (2, TE_LINE_1),
    ]


# --- refused and flagged files --------------------------------------------------------------


@pytest.mark.parametrize("mode", ["user_password", "empty_user_password"])
def test_SEC_encrypted_pdfs_are_refused(mode: str) -> None:
    assert failure(P.pdf([P.text_page("Confidential synthetic circular.")], encryption=mode)) == (
        "encrypted"
    )


def test_scanned_pdf_without_text_layer_needs_ocr_instead_of_indexing_nothing() -> None:
    assert failure(P.pdf([P.Page(image=True), P.Page(image=True)])) == "needs_ocr"


def test_a_scanned_page_among_text_pages_needs_ocr() -> None:
    data = P.pdf(
        [P.text_page("Sub: Timetable for the half-yearly examinations."), P.Page(image=True)]
    )
    assert failure(data) == "needs_ocr"


def test_a_page_with_a_logo_and_enough_text_is_read() -> None:
    page = P.Page(lines=("Government of Andhra Pradesh, School Education Department.",), image=True)
    assert texts(extract_pages(P.pdf([page]), PDF_MIME, LIMITS)) == [
        (1, "Government of Andhra Pradesh, School Education Department.")
    ]


def test_legacy_font_mojibake_is_not_indexed() -> None:
    page = P.Page(lines=(TE_LINE_1,), unicode_font=True, private_use=True)
    assert failure(P.pdf([page])) == "needs_ocr"


def test_a_pdf_with_only_blank_pages_needs_ocr() -> None:
    assert failure(P.pdf([P.Page()])) == "needs_ocr"


@pytest.mark.parametrize(
    "data", [b"%PDF-1.7\n%%EOF", b"not a pdf at all", P.pdf([P.text_page("x")])[:200]]
)
def test_corrupt_pdfs_fail_with_a_code(data: bytes) -> None:
    assert failure(data) == "corrupt"


# --- limits (PDF bombs) -----------------------------------------------------------------------


def test_SEC_file_over_the_byte_limit_is_not_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    def must_not_open(*_: object, **__: object) -> None:
        raise AssertionError("the PDF was opened")

    monkeypatch.setattr("pypdfium2.PdfDocument", must_not_open)
    data = P.pdf([P.text_page("Sub: A synthetic circular.")])
    assert failure(data, limits(max_bytes=len(data) - 1)) == "file_too_large"


def test_SEC_too_many_pages_is_refused() -> None:
    data = P.pdf([P.text_page(f"Page {n} of a synthetic bundle.") for n in range(1, 4)])
    assert failure(data, limits(max_pages=2)) == "too_many_pages"
    assert len(extract_pages(data, PDF_MIME, limits(max_pages=3))) == 3


def test_SEC_too_many_objects_on_a_page_is_refused() -> None:
    data = P.pdf([P.Page(lines=("Sub: A synthetic circular.",), extra_objects=20)])
    assert failure(data, limits(max_objects_per_page=10)) == "page_too_complex"


def test_SEC_too_many_characters_on_a_page_is_refused() -> None:
    data = P.pdf([P.text_page("A synthetic line of circular text. " * 4)])
    assert failure(data, limits(max_chars_per_page=100)) == "page_text_too_large"


def test_SEC_total_text_limit_applies_to_pdfs() -> None:
    lines = ["A synthetic line of circular text that repeats."] * 30
    data = P.pdf([P.text_page(*lines) for _ in range(2)])
    config = LIMITS.model_copy(update={"max_text_chars": 1000})
    assert failure(data, config) == "text_too_large"


def test_SEC_time_budget_is_checked_between_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks: Iterator[float] = iter([0.0, 1.0, 50.0, 200.0, 400.0])
    monkeypatch.setattr(pdf_module, "_clock", lambda: next(ticks))
    data = P.pdf([P.text_page(f"Page {n}.") for n in range(1, 5)])
    assert failure(data, limits(time_budget_seconds=100)) == "time_budget_exceeded"


def test_failure_codes_never_carry_file_text() -> None:
    data = P.pdf([P.Page(lines=(S.SYNTHETIC_NAME,), image=True)])
    with pytest.raises(ExtractionFailed) as exc:
        extract_pages(data, PDF_MIME, limits(min_letters_per_page=100))
    assert str(exc.value) == exc.value.code == "needs_ocr"
    assert exc.value.__cause__ is None
    assert S.SYNTHETIC_NAME not in repr(exc.value)


# --- redaction --------------------------------------------------------------------------------


def test_PRV_013_aadhaar_in_a_pdf_never_reaches_the_cleaned_text() -> None:
    first, rest = S.AADHAAR_SPACED[:9], S.AADHAAR_SPACED[9:]
    data = P.pdf(
        [
            P.text_page(
                f"Synthetic student Aadhaar {S.AADHAAR_SPACED} recorded.",
                f"Wrapped number {first}",  # the number continues on the next line
                f"{rest} for verification.",
                f"Unspaced {S.SYNTHETIC_AADHAAR} in a table cell.",
            )
        ]
    )
    raw = extract_pages(data, PDF_MIME, LIMITS)
    result = cleaned(raw)
    body = "\n".join(b.text for pg in result.document.pages for b in pg.blocks)
    assert not contains_full_aadhaar(body)
    assert S.SYNTHETIC_AADHAAR not in body.replace(" ", "")
    assert body.count(S.AADHAAR_MASK) == 3
    assert result.redactions == 3


def test_PRV_013_pdf_pipeline_end_to_end_indexes_masked_chunks() -> None:
    w = S.world()
    version = S.version(1, mime=PDF_MIME)
    data = P.pdf(
        [
            P.text_page(
                "Sub: Half-yearly examinations 2026.",
                f"Aadhaar of a synthetic student: {S.AADHAAR_SPACED}.",
            ),
            P.telugu_page(TE_LINE_1),
        ]
    )
    w.source.put(S.TENANT_A, S.facts(DOC, [version]), {1: data})
    assert w.pipeline.ingest(S.TENANT_A, DOC, version.id) == "indexed"
    stored = [r for r in w.store.rows if r.document_id == DOC]
    assert stored
    chunks = [r.item.chunk for r in stored]
    assert min(c.page_from or 0 for c in chunks) == 1
    assert max(c.page_to or 0 for c in chunks) == 2
    joined = "\n".join(c.content for c in chunks)
    assert TE_LINE_1 in joined
    assert S.AADHAAR_MASK in joined
    for text in [*w.embedder.texts, joined]:
        assert not contains_full_aadhaar(text)
        assert S.SYNTHETIC_AADHAAR not in text.replace(" ", "")


def test_scanned_pdf_through_the_pipeline_is_flagged_not_indexed() -> None:
    w = S.world()
    version = S.version(1, mime=PDF_MIME)
    w.source.put(S.TENANT_A, S.facts(DOC, [version]), {1: P.pdf([P.Page(image=True)])})
    assert w.pipeline.ingest(S.TENANT_A, DOC, version.id) == "needs_ocr"
    assert not [r for r in w.store.rows if r.document_id == DOC]
    assert w.embedder.texts == []


# --- only the ingest path loads the native library ---------------------------------------------


def test_the_api_process_does_not_load_pdfium() -> None:
    code = (
        "import sys\n"
        "import app.main, app.knowledge.ingestion.extract, app.knowledge.ingestion.pipeline\n"
        "assert 'pypdfium2' not in sys.modules, 'pypdfium2 imported outside the ingest path'\n"
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr[-2000:]
