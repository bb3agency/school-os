"""Content-based file type checks (FR-DOC-001, SEC-016, threat T5). No database needed."""

from __future__ import annotations

import sys

import pytest

from app.documents import filetypes as ft

S = sys.modules["sos_test_documents_support"]


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (S.pdf(), ft.PDF),
        (S.png(), ft.PNG),
        (S.jpg(), ft.JPG),
        (S.xlsx(), ft.XLSX),
        (S.docx(), ft.DOCX),
    ],
    ids=["pdf", "png", "jpg", "xlsx", "docx"],
)
def test_FR_DOC_001_allowlisted_kinds_are_recognised_by_magic_bytes(
    data: bytes, kind: ft.FileKind
) -> None:
    assert ft.sniff(data) is kind
    assert ft.check_head(kind, data) is None
    assert ft.check_tail(kind, data[-ft.TAIL_BYTES :]) is None


def test_FR_DOC_001_png_renamed_to_pdf_is_a_mismatch() -> None:
    assert ft.check_head(ft.PDF, S.png()) == "type_mismatch"


def test_SEC_016_html_declared_as_jpeg_is_rejected() -> None:
    html = b"<!doctype html><html><script>alert(1)</script></html>"
    assert ft.sniff(html) is None
    assert ft.check_head(ft.JPG, html) == "type_mismatch"


def test_SEC_016_jpeg_carrying_markup_is_a_suspected_polyglot() -> None:
    polyglot = S.jpg(extra=b"\xff\xfe\x00\x20<script>fetch('/x')</script>")
    assert ft.sniff(polyglot) is ft.JPG
    assert ft.check_head(ft.JPG, polyglot) == "polyglot_suspected"


def test_SEC_016_png_with_appended_archive_is_a_suspected_polyglot() -> None:
    polyglot = S.png() + S.xlsx()
    assert ft.check_head(ft.PNG, polyglot) is None
    assert ft.check_tail(ft.PNG, polyglot[-ft.TAIL_BYTES :]) == "polyglot_suspected"


def test_SEC_016_pdf_without_eof_marker_is_rejected() -> None:
    assert ft.check_tail(ft.PDF, b"%PDF-1.7\n" + b"x" * 2000) == "polyglot_suspected"


def test_FR_DOC_001_executables_and_unknown_bytes_are_not_allowlisted() -> None:
    assert ft.sniff(b"MZ\x90\x00" + b"\x00" * 100) is None
    assert ft.check_head(ft.PDF, b"MZ\x90\x00" + b"\x00" * 100) == "type_mismatch"
    assert ft.check_head(ft.PDF, b"") == "empty_file"


def test_FR_IMP_csv_is_accepted_only_as_utf8_text_without_nul() -> None:
    assert ft.check_head(ft.CSV, S.csv_text()) is None
    assert ft.check_head(ft.CSV, S.pdf()) == "type_mismatch", "binary kinds are not CSV"
    ok = ft.CsvChecker()
    data = S.csv_text()
    for i in range(0, len(data), 7):  # multi-byte Telugu split across chunks
        ok.feed(data[i : i + 7])
    assert ok.finish() is None
    nul = ft.CsvChecker()
    nul.feed(b"a,b\n\x00\x01")
    assert nul.finish() == "not_text"
    latin = ft.CsvChecker()
    latin.feed(b"name\ncaf\xe9\n")
    assert latin.finish() == "not_utf8"


def test_FR_DOC_001_declared_types_and_extensions() -> None:
    assert ft.kind_for_content_type("application/pdf; charset=binary") is ft.PDF
    assert ft.kind_for_content_type("text/html") is None
    assert ft.kind_for_filename("scan.JPEG") is ft.JPG
    assert ft.kind_for_filename("register.png.exe") is None
    assert ft.kind_for_filename("noextension") is None
