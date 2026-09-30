"""The full export archive's files (FR-ADM-001, SEC-017, invariant 4). Pure: no database."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import uuid
from decimal import Decimal

import pytest

from app.admin import archive
from app.core.records import RecordTable
from app.core.redaction import verhoeff_check_digit

# A synthetic 12-digit number that passes the Verhoeff check (never a real one).
_BASE = "23456789012"
AADHAAR_LIKE = _BASE + verhoeff_check_digit(_BASE)


def _table() -> RecordTable:
    return RecordTable(
        name="students",
        columns=("id", "name", "note", "when", "flags", "extra", "amount", "ok"),
        rows=[
            (
                uuid.UUID(int=1),
                "Synthetica Kumari",
                f'=HYPERLINK("x") card {AADHAAR_LIKE}',
                dt.datetime(2026, 9, 29, 5, 30, tzinfo=dt.UTC),
                ["b", "a"],
                {"k": "v"},
                Decimal("1.500"),
                True,
            ),
            (
                uuid.UUID(int=2),
                "కృత్రిమ విద్యార్థి",
                "@cmd\x07",
                dt.date(2012, 3, 14),
                [],
                {},
                None,
                False,
            ),
        ],
    )


def test_SEC_017_csv_neutralises_formulas_and_masks_aadhaar_like_numbers() -> None:
    raw = archive.table_csv(_table()).decode("utf-8")
    assert raw.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(raw[1:])))
    assert rows[0]["note"].startswith("'=")
    assert AADHAAR_LIKE not in raw
    assert rows[1]["note"] == "'@cmd"  # control character removed, then neutralised
    assert rows[0]["when"] == "2026-09-29T05:30:00Z"
    assert rows[0]["flags"] == '["b","a"]'
    assert rows[0]["extra"] == '{"k":"v"}'
    assert rows[0]["ok"] == "true"
    assert rows[1]["name"] == "కృత్రిమ విద్యార్థి"
    assert rows[1]["amount"] == ""


def test_FR_ADM_001_json_keeps_types_and_masks_aadhaar_like_numbers() -> None:
    doc = json.loads(archive.table_json(_table()))
    assert doc["table"] == "students"
    assert doc["columns"][0] == "id"
    first, second = doc["rows"]
    assert first["id"] == str(uuid.UUID(int=1))
    assert AADHAAR_LIKE not in first["note"]
    assert first["note"].startswith("=HYPERLINK")  # JSON is data, not a spreadsheet
    assert first["flags"] == ["b", "a"]
    assert first["extra"] == {"k": "v"}
    assert first["amount"] == "1.500"
    assert first["ok"] is True
    assert second["when"] == "2012-03-14"
    assert second["amount"] is None
    assert second["note"] == "@cmd"


def test_FR_ADM_001_document_paths_are_generated_never_titles() -> None:
    doc = uuid.UUID(int=5)
    key = f"t/{uuid.UUID(int=9)}/docs/{doc}/v2/original.pdf"
    assert archive.document_path(doc, 2, key) == f"documents/{doc}/v2.pdf"
    assert archive.document_path(doc, 1, "t/x/docs/y/v1/original") == f"documents/{doc}/v1.bin"


def test_FR_ADM_001_manifest_lists_tables_counts_and_masking() -> None:
    raw = archive.manifest(
        export_id=uuid.UUID(int=3),
        layout_version=1,
        school={"id": uuid.UUID(int=4), "code": "s-1", "name": "Synthetic Model School"},
        generated_at=dt.datetime(2026, 9, 29, tzinfo=dt.UTC),
        include_sensitive=False,
        tables=[archive.TableEntry("students", 2, ("c3_masked",))],
        documents=3,
        document_bytes=1234,
        audit_events=10,
        never_exported=("aadhaar_name_as_printed",),
    )
    doc = json.loads(raw)
    assert doc["format"] == "schoolos-full-export"
    assert doc["restricted_values"] == "masked"
    assert doc["tables"] == [
        {
            "name": "students",
            "rows": 2,
            "files": ["records/students.csv", "records/students.json"],
            "notes": ["c3_masked"],
        }
    ]
    assert doc["documents"]["files"] == 3
    assert doc["audit"] == {"events": 10, "file": "audit/audit-log.csv"}
    assert doc["never_exported"] == ["aadhaar_name_as_printed"]


@pytest.mark.usefixtures("telugu_on")  # Telugu output: switched on (ADR-0036)
def test_FR_ADM_001_readme_is_bilingual() -> None:
    text = archive.readme("English part", "తెలుగు భాగం").decode("utf-8")
    assert "English part" in text
    assert "తెలుగు భాగం" in text


def test_ADR_0036_readme_is_english_only_while_telugu_is_hidden() -> None:
    text = archive.readme("English part", "తెలుగు భాగం").decode("utf-8")
    assert text == "English part\n"


def test_FR_ADM_001_record_tables_check_their_shape() -> None:
    with pytest.raises(ValueError, match="name"):
        RecordTable(name="Bad Name", columns=("a",), rows=[])
    with pytest.raises(ValueError, match="values"):
        RecordTable(name="ok", columns=("a", "b"), rows=[(1,)])
