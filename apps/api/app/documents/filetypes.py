"""File kinds accepted for upload, decided by content, never by extension (FR-DOC-001, T5).

- Binary kinds (PDF, JPEG, PNG, DOCX, XLSX) are recognised by magic bytes with ``filetype`` and
  must equal the kind the uploader declared (the presigned POST also pinned that Content-Type).
- CSV (spreadsheet imports only) has no magic number: the whole object must be UTF-8 text
  without NUL bytes and must not look like any binary kind.
- Cheap structural checks catch common polyglots: images whose first bytes carry HTML/script
  markers, PNGs that do not end with ``IEND``, PDFs without ``%%EOF`` near the end.
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from typing import Final

import filetype  # type: ignore[import-untyped]  # filetype 1.2 ships no type hints

HEAD_BYTES: Final = 8192
TAIL_BYTES: Final = 1024


@dataclass(frozen=True, slots=True)
class FileKind:
    key: str
    mime: str
    ext: str


PDF = FileKind("pdf", "application/pdf", "pdf")
JPG = FileKind("jpg", "image/jpeg", "jpg")
PNG = FileKind("png", "image/png", "png")
DOCX = FileKind(
    "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
)
XLSX = FileKind("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx")
CSV = FileKind("csv", "text/csv", "csv")

KINDS: Final[dict[str, FileKind]] = {k.key: k for k in (PDF, JPG, PNG, DOCX, XLSX, CSV)}
_BY_MIME: Final[dict[str, FileKind]] = {k.mime: k for k in KINDS.values()}
_BY_EXT: Final[dict[str, FileKind]] = {k.ext: k for k in KINDS.values()} | {"jpeg": JPG}

_PNG_TRAILER: Final = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"
_MARKUP: Final = re.compile(rb"<\s*(script|html|!doctype|svg|iframe|body|\?php)", re.IGNORECASE)


def kind_for_content_type(content_type: str) -> FileKind | None:
    return _BY_MIME.get(content_type.split(";", 1)[0].strip().lower())


def kind_for_filename(filename: str) -> FileKind | None:
    _, dot, ext = filename.rpartition(".")
    return _BY_EXT.get(ext.lower()) if dot else None


def sniff(head: bytes) -> FileKind | None:
    """The binary kind the bytes say they are (``None`` when unknown or not allowlisted)."""
    guess = filetype.guess(head[:HEAD_BYTES])
    return _BY_MIME.get(guess.mime) if guess is not None else None


def check_head(expected: FileKind, head: bytes) -> str | None:
    """Error code when the first bytes do not prove ``expected``; ``None`` when they do."""
    if not head:
        return "empty_file"
    detected = sniff(head)
    if expected is CSV:
        if detected is not None or filetype.guess(head[:HEAD_BYTES]) is not None:
            return "type_mismatch"
        return None  # the full text check runs over the whole object (CsvChecker)
    if detected is not expected:
        return "type_mismatch"
    if expected in (JPG, PNG) and _MARKUP.search(head[:HEAD_BYTES]):
        return "polyglot_suspected"
    return None


def check_tail(expected: FileKind, tail: bytes) -> str | None:
    """Error code when the end of the file is not a well-formed end of ``expected``."""
    if expected is PNG and not tail.endswith(_PNG_TRAILER):
        return "polyglot_suspected"
    if expected is PDF and b"%%EOF" not in tail[-TAIL_BYTES:]:
        return "polyglot_suspected"
    return None


class CsvChecker:
    """Streaming check that an object is UTF-8 text without NUL bytes (imports only)."""

    def __init__(self) -> None:
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self.error: str | None = None

    def feed(self, chunk: bytes) -> None:
        if self.error is not None:
            return
        if b"\x00" in chunk:
            self.error = "not_text"
            return
        try:
            self._decoder.decode(chunk)
        except UnicodeDecodeError:
            self.error = "not_utf8"

    def finish(self) -> str | None:
        if self.error is None:
            try:
                self._decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                self.error = "not_utf8"
        return self.error
