"""``app/academics/config.yaml`` (versioned configuration; invariant 13). Pure."""

from __future__ import annotations

from functools import lru_cache
from importlib import resources
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

AttendanceStatus = Literal["present", "absent", "late", "leave"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AttendanceRules(_Model):
    codes: dict[str, AttendanceStatus] = Field(min_length=4)
    max_entries: int = Field(ge=100, le=10000)
    max_sheet_dates: int = Field(ge=1, le=62)


class MarksRules(_Model):
    max_subjects: int = Field(ge=1, le=40)
    max_subject_chars: int = Field(ge=10, le=60)
    max_marks_ceiling: int = Field(ge=10, le=1000)
    absent_codes: tuple[str, ...] = Field(min_length=1)
    max_entries: int = Field(ge=100, le=10000)


class SheetLimits(_Model):
    """Satisfies :class:`app.core.spreadsheet.ReadLimits`."""

    max_rows: int = Field(ge=10, le=2000)
    max_columns: int = Field(ge=5, le=100)
    max_cell_chars: int = Field(ge=20, le=1000)
    xlsx_max_uncompressed_bytes: int = Field(ge=1_000_000, le=104_857_600)
    xlsx_max_compression_ratio: int = Field(ge=10, le=1000)
    xlsx_max_members: int = Field(ge=10, le=5000)
    max_scanned_rows: int = Field(ge=10, le=60000)


class SheetRules(_Model):
    admission_headers: tuple[str, ...] = Field(min_length=1)
    roll_headers: tuple[str, ...] = Field(min_length=1)
    name_headers: tuple[str, ...] = Field(min_length=1)
    max_label_rows: tuple[str, ...] = Field(min_length=1)
    limits: SheetLimits
    max_file_bytes: int = Field(ge=10_000, le=10_485_760)
    max_issues: int = Field(ge=10, le=1000)


class AcademicsConfig(_Model):
    version: int = Field(ge=1)
    attendance: AttendanceRules
    marks: MarksRules
    sheets: SheetRules


@lru_cache(maxsize=1)
def load_config() -> AcademicsConfig:
    raw = yaml.safe_load(
        resources.files("app.academics").joinpath("config.yaml").read_text("utf-8")
    )
    return AcademicsConfig.model_validate(raw)


__all__ = [
    "AcademicsConfig",
    "AttendanceRules",
    "AttendanceStatus",
    "MarksRules",
    "SheetLimits",
    "SheetRules",
    "load_config",
]
