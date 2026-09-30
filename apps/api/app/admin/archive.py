"""The full export archive's files (FR-ADM-001, SEC-017, invariant 4). Pure: no database, web or
storage; ``app.admin.service`` feeds it record tables and writes the bytes into the zip.

Every value is cleaned the same way for CSV and JSON: NFC text, control characters removed (tab,
line feed and carriage return kept), and any 12-digit Verhoeff-valid sequence masked
(:func:`app.core.redaction.mask_aadhaar`; full Aadhaar numbers are never stored, this is defence
in depth). CSV cells additionally pass :func:`app.core.spreadsheet.safe_cell`, which neutralises
formula injection with a leading apostrophe; CSV is UTF-8 with a BOM so Excel shows Telugu. JSON
keeps types (numbers, booleans, null, nested objects) and is UTF-8 without escapes.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import unicodedata
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, cast

from app.core.languages import telugu_enabled
from app.core.records import RecordTable
from app.core.redaction import mask_aadhaar
from app.core.spreadsheet import write_csv

FORMAT: Final = "schoolos-full-export"
_CONTROL_RE: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_EXT_RE: Final = re.compile(r"\.([a-z0-9]{1,8})$")


def clean_text(value: str) -> str:
    return mask_aadhaar(_CONTROL_RE.sub("", unicodedata.normalize("NFC", value)))


def _scalar(value: object) -> object:
    """JSON scalar for one value (text cleaned); ``NotImplemented`` for containers."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Mapping | list | tuple | set | frozenset):
        return NotImplemented
    # str, UUID, Decimal and anything else: its text.
    return clean_text(str(value))


def json_value(value: object) -> Any:
    """A JSON-safe copy of ``value`` with every string cleaned."""
    scalar = _scalar(value)
    if scalar is not NotImplemented:
        return scalar
    if isinstance(value, Mapping):
        return {clean_text(str(k)): json_value(v) for k, v in value.items()}
    seq = cast("Iterable[object]", value)
    items = sorted(seq, key=str) if isinstance(value, set | frozenset) else seq
    return [json_value(v) for v in items]


def _csv_value(value: object) -> object:
    plain = json_value(value)
    if isinstance(plain, dict | list):
        return json.dumps(plain, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(plain, bool):
        return "true" if plain else "false"
    return plain


def table_csv(table: RecordTable) -> bytes:
    """``records/<name>.csv``: header row, then one row per record (SEC-017 safe cells)."""
    return write_csv(table.columns, [[_csv_value(v) for v in row] for row in table.rows])


def table_json(table: RecordTable) -> bytes:
    """``records/<name>.json``: ``{"table", "columns", "notes", "rows": [{column: value}]}``."""
    doc = {
        "table": table.name,
        "columns": list(table.columns),
        "notes": list(table.notes),
        "rows": [
            {c: json_value(v) for c, v in zip(table.columns, row, strict=True)}
            for row in table.rows
        ],
    }
    return json.dumps(doc, ensure_ascii=False, indent=1).encode("utf-8")


def document_path(document_id: uuid.UUID, version_no: int, object_key: str) -> str:
    """``documents/<document_id>/v<n>.<ext>`` (the extension of the stored object; generated
    names only, never titles)."""
    match = _EXT_RE.search(object_key)
    ext = match.group(1) if match else "bin"
    return f"documents/{document_id}/v{version_no}.{ext}"


@dataclass(frozen=True, slots=True)
class TableEntry:
    name: str
    rows: int
    notes: tuple[str, ...]


def manifest(
    *,
    export_id: uuid.UUID,
    layout_version: int,
    school: Mapping[str, Any],
    generated_at: dt.datetime,
    include_sensitive: bool,
    tables: Sequence[TableEntry],
    documents: int,
    document_bytes: int,
    audit_events: int,
    never_exported: Sequence[str],
) -> bytes:
    """``manifest.json``: what the archive holds (no personal data: school id, code and name,
    counts and codes)."""
    doc = {
        "format": FORMAT,
        "layout_version": layout_version,
        "export_id": str(export_id),
        "school": json_value(dict(school)),
        "generated_at": json_value(generated_at),
        "include_sensitive": include_sensitive,
        "restricted_values": "included" if include_sensitive else "masked",
        "never_exported": list(never_exported),
        "tables": [
            {
                "name": t.name,
                "rows": t.rows,
                "files": [f"records/{t.name}.csv", f"records/{t.name}.json"],
                "notes": list(t.notes),
            }
            for t in tables
        ],
        "documents": {"files": documents, "bytes": document_bytes, "folder": "documents/"},
        "audit": {"events": audit_events, "file": "audit/audit-log.csv"},
    }
    return json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")


def readme(en: str, te: str) -> bytes:
    """README.txt of a data export: English, then Telugu only while Telugu is shown
    (ADR-0036)."""
    if not telugu_enabled():
        return (en.rstrip() + "\n").encode("utf-8")
    return (en.rstrip() + "\n\n" + "-" * 72 + "\n\n" + te.rstrip() + "\n").encode("utf-8")


def rows_of(
    items: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> Iterator[tuple[Any, ...]]:
    for item in items:
        yield tuple(item.get(c) for c in columns)


__all__ = [
    "FORMAT",
    "TableEntry",
    "clean_text",
    "document_path",
    "json_value",
    "manifest",
    "readme",
    "rows_of",
    "table_csv",
    "table_json",
]
