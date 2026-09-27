"""Row validation for spreadsheet imports (FR-IMP-003, SEC-013, SEC-015, SEC-017). Pure.

The service builds a :class:`ValidationContext` (attribute catalog, current-year classes and
sections, the importer's reach, existing students by admission number) and calls
:func:`validate_sheet`; nothing here touches the database, so 2,000 rows validate in well under
a second (NFR-PERF-004 budget: 60 s).

Per row: every cell is scanned for a full Aadhaar number (row error
``aadhaar_full_number_rejected``; the cell is never kept), formula cells are refused when mapped
(``formula_not_evaluated``), the admission number is required and matched against existing
students (``update``) or the row creates one (``create``, only from sources that may create),
values are converted by attribute type, identity values from the admission register must not
contradict the recorded register value (``identity_change_required``: use a change request),
class/section must exist in the current academic year and lie within the importer's scope, and
duplicate admission numbers within the file are errors on every copy.

Errors and warnings are ``{"field", "code", "message_key"}`` (+ ``"ref"`` = another row number).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from app.core.redaction import contains_full_aadhaar
from app.imports.config import ImportConfig
from app.imports.mapping import IGNORE, SPECIAL_TARGETS
from app.imports.sheet import Cell, Sheet, SheetRow
from app.imports.values import (
    ClassResolver,
    cell_text,
    clean_text,
    day_first_proven,
    enum_lookup,
    has_control_chars,
    parse_date,
)

AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
AADHAAR_MESSAGE_KEY: Final = "errors.aadhaar_last4_only"
FORMULA_CODE: Final = "formula_not_evaluated"
ANCHOR_SOURCE: Final = "admission_register"  # BR-01: the legal anchor of identity values
MIN_DATE: Final = dt.date(1900, 1, 1)
_EXPLICIT_PHONE_RE: Final = re.compile(r"\+91[ \u00a0-]?[6-9][0-9]{4}[ \u00a0-]?[0-9]{5}")
_MASKED_LAST4_RE: Final = re.compile(r"^(?:[Xx*•]{4}[\s-]?){2}(\d{4})$")
_TWELVE_DIGITS_RE: Final = re.compile(r"^\d{4}[\s-]?\d{4}[\s-]?\d{4}$")

RowStatus = Literal["valid", "error"]
Action = Literal["create", "update"]
Issue = dict[str, str]


def issue(
    field_name: str, code: str, message_key: str | None = None, ref: int | None = None
) -> Issue:
    out = {"field": field_name, "code": code, "message_key": message_key or f"errors.{code}"}
    if ref is not None:
        out["ref"] = str(ref)
    return out


def is_full_aadhaar(text: str) -> bool:
    """Same rule as the student service: 12 digits passing Verhoeff, except an explicit
    ``+91`` mobile number."""
    if _EXPLICIT_PHONE_RE.fullmatch(text.strip()):
        return False
    return contains_full_aadhaar(text)


@dataclass(frozen=True, slots=True)
class AttributeSpec:
    key: str
    data_type: str
    classification: str
    is_identity: bool
    allowed_sources: tuple[str, ...] | None
    allowed_values: tuple[str, ...] | None
    # From the student catalog (students.attribute_rules); None = the import's own limits only.
    max_length: int | None = None
    pattern: str | None = None
    not_future: bool = True

    @property
    def sensitive(self) -> bool:
        return self.classification == "C3"

    def allows(self, source: str) -> bool:
        return self.allowed_sources is None or source in self.allowed_sources


@dataclass(frozen=True, slots=True)
class SectionInfo:
    id: str
    class_id: str
    name: str
    label: str  # e.g. "IX-A"


@dataclass(frozen=True, slots=True)
class ExistingStudent:
    id: str
    section_id: str | None
    anchor_values: Mapping[str, str]  # identity attribute -> current admission-register value


@dataclass(frozen=True, slots=True)
class ValidationContext:
    source: str
    specs: Mapping[str, AttributeSpec]
    classes: ClassResolver
    sections: Sequence[SectionInfo]
    has_current_year: bool
    allowed_sections: frozenset[str] | None  # None = whole school
    can_create: bool
    existing: Mapping[str, ExistingStudent]  # key: admission_key(admission number)
    config: ImportConfig
    today: dt.date

    @property
    def creates(self) -> bool:
        spec = self.specs.get("admission_no")
        return (
            self.source in self.config.creating_sources
            and spec is not None
            and spec.allows(self.source)
        )


def admission_key(value: str) -> str:
    return clean_text(value).casefold()


def allowed_targets(specs: Mapping[str, AttributeSpec], source: str) -> set[str]:
    """Attributes the source may record, the structure columns, and always the admission
    number (the match key, even when this source cannot record it)."""
    targets = {k for k, s in specs.items() if s.allows(source)} | set(SPECIAL_TARGETS)
    if "admission_no" in specs:
        targets.add("admission_no")
    return targets


def mapping_problems(
    mapping: Mapping[str, str], columns: int, specs: Mapping[str, AttributeSpec], source: str
) -> list[Issue]:
    """Field errors for a column mapping (index -> target); empty when it is usable."""
    problems: list[Issue] = []
    allowed = allowed_targets(specs, source)
    seen: dict[str, str] = {}
    for key, target in mapping.items():
        where = f"columns.{key}"
        if not key.isdigit() or not 0 <= int(key) < columns:
            problems.append(issue(where, "unknown_column"))
            continue
        if target == IGNORE:
            continue
        if target not in allowed:
            code = "source_not_allowed" if target in specs else "unknown_target"
            problems.append(issue(where, code))
            continue
        if target in seen:
            problems.append(issue(where, "duplicate_target", ref=None))
            continue
        seen[target] = key
    if "class_section" in seen and ({"class", "section"} & seen.keys()):
        problems.append(issue(f"columns.{seen['class_section']}", "class_section_conflict"))
    return problems


@dataclass(slots=True)
class RowResult:
    row_no: int
    status: RowStatus = "valid"
    action: Action | None = None
    student_id: str | None = None
    admission_no: str | None = None
    values: dict[str, str] = field(default_factory=dict)  # every clean value, C3 included
    section_id: str | None = None
    class_label: str | None = None
    roll_no: str | None = None
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    def parsed(self, specs: Mapping[str, AttributeSpec]) -> dict[str, Any]:
        """What is stored in ``sis.import_rows.parsed``: no C3 value ever (docs/05 §5.2)."""
        visible = {k: v for k, v in self.values.items() if k in specs and not specs[k].sensitive}
        sensitive = sorted(k for k in self.values if k in specs and specs[k].sensitive)
        return {
            "admission_no": self.admission_no,
            "values": visible,
            "sensitive": sensitive,
            "section_id": self.section_id,
            "class_label": self.class_label,
            "roll_no": self.roll_no,
        }


@dataclass(frozen=True, slots=True)
class ValidationResult:
    rows: list[RowResult]
    stats: dict[str, int]

    @property
    def error_rows(self) -> int:
        return sum(1 for r in self.rows if r.status == "error")


def _field_name(index: int, targets: Mapping[int, str]) -> str:
    target = targets.get(index)
    return target if target and target != IGNORE else f"column_{index + 1}"


class _RowValidator:
    def __init__(self, sheet: Sheet, mapping: Mapping[str, str], ctx: ValidationContext) -> None:
        self.ctx = ctx
        self.cfg = ctx.config
        self.targets: dict[int, str] = {int(k): v for k, v in mapping.items() if v != IGNORE}
        self.by_target: dict[str, int] = {v: k for k, v in self.targets.items()}
        self.sections_by_class: dict[str, dict[str, SectionInfo]] = {}
        for section in ctx.sections:
            self.sections_by_class.setdefault(section.class_id, {})[section.name.casefold()] = (
                section
            )
        self.day_first: dict[str, bool] = {
            key: day_first_proven(row.cell(index).value for row in sheet.rows)
            for key, index in self.by_target.items()
            if key in ctx.specs and ctx.specs[key].data_type == "date"
        }
        self.enums: dict[str, dict[str, str]] = {
            key: enum_lookup(spec.allowed_values or (), self.cfg.enum_synonyms.get(key))
            for key, spec in ctx.specs.items()
            if spec.data_type == "enum"
        }
        self.patterns: dict[str, re.Pattern[str]] = {
            key: re.compile(spec.pattern) for key, spec in ctx.specs.items() if spec.pattern
        }

    # -- cells --------------------------------------------------------------------------------

    def _scan(self, row: SheetRow, result: RowResult) -> set[int]:
        """Aadhaar and formula checks on every cell; returns the columns that must be skipped."""
        poisoned: set[int] = set()
        for index, cell in enumerate(row.cells):
            text = cell_text(cell.value)
            name = _field_name(index, self.targets)
            if text is not None and is_full_aadhaar(text):
                result.errors.append(issue(name, AADHAAR_CODE, AADHAAR_MESSAGE_KEY))
                poisoned.add(index)
                continue
            if cell.formula:
                poisoned.add(index)
                if index in self.targets:
                    result.errors.append(issue(name, FORMULA_CODE))
                else:
                    result.warnings.append(issue(name, FORMULA_CODE))
        return poisoned

    def _cell(self, row: SheetRow, target: str, poisoned: set[int]) -> Cell | None:
        index = self.by_target.get(target)
        if index is None or index in poisoned:
            return None
        cell = row.cell(index)
        return None if cell.empty else cell

    # -- values -------------------------------------------------------------------------------

    def _text(
        self, key: str, cell: Cell, result: RowResult, max_length: int | None = None
    ) -> str | None:
        """Cleaned cell text; ``max_length`` is the attribute's own limit (student catalog),
        else the import's limit for ``key`` (``text_max_length`` in config.yaml)."""
        text = cell_text(cell.value)
        if text is None:
            return None
        if has_control_chars(text):
            result.errors.append(issue(key, "invalid"))
            return None
        if len(text) > (max_length if max_length is not None else self.cfg.max_length(key)):
            result.errors.append(issue(key, "too_long"))
            return None
        return text

    def _value(  # noqa: PLR0911, PLR0912  - one early return per input shape keeps the table-driven rules readable
        self, spec: AttributeSpec, cell: Cell, result: RowResult
    ) -> str | None:
        key = spec.key
        if spec.data_type == "date":
            parsed = parse_date(cell.value, day_first_proven=self.day_first.get(key, False))
            if parsed.value is None:
                result.errors.append(issue(key, parsed.error or "invalid_date"))
                return None
            if parsed.value < MIN_DATE:
                result.errors.append(issue(key, "date_too_early"))
                return None
            if spec.not_future and parsed.value > self.ctx.today:
                result.errors.append(issue(key, "date_in_future"))
                return None
            if parsed.ambiguous:
                result.warnings.append(issue(key, "ambiguous_date"))
            return parsed.value.isoformat()
        text = self._text(key, cell, result, spec.max_length)
        if text is None:
            return None
        if spec.data_type == "enum":
            value = self.enums.get(key, {}).get(text.casefold())
            if value is None:
                result.errors.append(issue(key, "not_allowed"))
            return value
        if spec.data_type == "digits4":
            masked = _MASKED_LAST4_RE.match(text)
            if masked is not None:
                return masked.group(1)
            if _TWELVE_DIGITS_RE.match(text):
                result.errors.append(issue(key, AADHAAR_CODE, AADHAAR_MESSAGE_KEY))
                return None
            if re.fullmatch(r"[0-9]{4}", text) is None:
                result.errors.append(issue(key, "digits4_required", AADHAAR_MESSAGE_KEY))
                return None
        pattern = self.patterns.get(key)
        if pattern is not None and pattern.fullmatch(text) is None:
            result.errors.append(issue(key, "invalid_format"))
            return None
        return text

    # -- structure ----------------------------------------------------------------------------

    def _section(  # noqa: PLR0911  - one early return per input shape keeps the table-driven rules readable
        self, row: SheetRow, poisoned: set[int], result: RowResult
    ) -> None:
        combined = self._cell(row, "class_section", poisoned)
        class_cell = self._cell(row, "class", poisoned)
        section_cell = self._cell(row, "section", poisoned)
        if combined is None and class_cell is None and section_cell is None:
            return
        if not self.ctx.has_current_year:
            result.errors.append(issue("class", "no_current_academic_year"))
            return
        class_id: str | None
        section_name: str | None
        field_name = "class_section" if combined is not None else "class"
        if combined is not None:
            pair = self.ctx.classes.resolve_class_section(cell_text(combined.value) or "")
            if pair is None:
                result.errors.append(issue("class_section", "class_not_found"))
                return
            class_id, section_name = pair
        else:
            if class_cell is None:
                result.errors.append(issue("class", "missing"))
                return
            class_id = self.ctx.classes.resolve_class(cell_text(class_cell.value) or "")
            if class_id is None:
                result.errors.append(issue("class", "class_not_found"))
                return
            if section_cell is None:
                result.errors.append(issue("section", "missing"))
                return
            section_name = cell_text(section_cell.value)
        section = self.sections_by_class.get(class_id, {}).get((section_name or "").casefold())
        if section is None:
            result.errors.append(
                issue("section" if combined is None else field_name, "section_not_found")
            )
            return
        result.section_id = section.id
        result.class_label = section.label

    # -- row ----------------------------------------------------------------------------------

    def row(self, row: SheetRow) -> RowResult:
        ctx = self.ctx
        result = RowResult(row_no=row.row_no)
        poisoned = self._scan(row, result)
        for key, spec in ctx.specs.items():
            if key not in self.by_target:
                continue
            cell = self._cell(row, key, poisoned)
            if cell is None:
                continue
            value = self._value(spec, cell, result)
            if value is not None:
                result.values[key] = value
        roll = self._cell(row, "roll_no", poisoned)
        if roll is not None:
            result.roll_no = self._text("roll_no", roll, result)
        self._section(row, poisoned, result)

        admission = result.values.get("admission_no")
        adm_index = self.by_target.get("admission_no")
        adm_poisoned = adm_index is not None and adm_index in poisoned
        if (
            admission is None
            and not adm_poisoned
            and not any(e["field"] == "admission_no" for e in result.errors)
        ):
            result.errors.append(issue("admission_no", "missing"))
        result.admission_no = admission
        existing = ctx.existing.get(admission_key(admission)) if admission else None
        if existing is not None:
            self._update_row(existing, result)
        elif admission is not None:
            self._create_row(result)
        if not ctx.creates and "admission_no" in result.values:
            # Match key only: the admission number cannot be recorded from this source.
            result.values.pop("admission_no")
        for key in self.cfg.required_always:
            if key != "admission_no" and key not in result.values and not _has_error(result, key):
                result.errors.append(issue(key, "missing"))
        return result

    def _create_row(self, result: RowResult) -> None:
        ctx = self.ctx
        result.action = "create"
        if not ctx.creates:
            result.errors.append(issue("admission_no", "no_matching_student"))
            return
        if not ctx.can_create:
            result.errors.append(issue("admission_no", "student_create_not_permitted"))
        required = (
            *self.cfg.required_create,
            *self.cfg.required_create_by_source.get(ctx.source, ()),
        )
        for key in required:
            if key not in result.values and not _has_error(result, key):
                result.errors.append(issue(key, "missing"))
        if result.section_id is None:
            if ctx.allowed_sections is not None and not _has_error(
                result, "class", "section", "class_section"
            ):
                result.errors.append(issue("section", "section_required"))
            elif not _has_error(result, "class", "section", "class_section"):
                result.warnings.append(issue("section", "no_section"))
        elif ctx.allowed_sections is not None and result.section_id not in ctx.allowed_sections:
            result.errors.append(issue("section", "section_out_of_scope"))

    def _update_row(self, existing: ExistingStudent, result: RowResult) -> None:
        result.action = "update"
        result.student_id = existing.id
        if self.ctx.source == ANCHOR_SOURCE:
            for key, value in result.values.items():
                spec = self.ctx.specs[key]
                recorded = existing.anchor_values.get(key)
                if spec.is_identity and recorded is not None and recorded != value:
                    result.errors.append(issue(key, "identity_change_required"))
        if result.section_id is not None and result.section_id != existing.section_id:
            result.warnings.append(issue("section", "enrolment_unchanged"))
        if self.ctx.allowed_sections is not None and (
            existing.section_id is None or existing.section_id not in self.ctx.allowed_sections
        ):
            result.errors.append(issue("admission_no", "student_out_of_scope"))


def _has_error(result: RowResult, *fields: str) -> bool:
    return any(e["field"] in fields for e in result.errors)


def validate_sheet(
    sheet: Sheet, mapping: Mapping[str, str], ctx: ValidationContext
) -> ValidationResult:
    validator = _RowValidator(sheet, mapping, ctx)
    rows = [validator.row(r) for r in sheet.rows]
    groups: dict[str, list[RowResult]] = {}
    for result in rows:
        if result.admission_no:
            groups.setdefault(admission_key(result.admission_no), []).append(result)
    for members in groups.values():
        if len(members) < 2:
            continue
        for result in members:
            other = next(m.row_no for m in members if m.row_no != result.row_no)
            result.errors.append(issue("admission_no", "duplicate_in_file", ref=other))
    stats = {
        "rows": len(rows),
        "valid": 0,
        "errors": 0,
        "warnings": 0,
        "create": 0,
        "update": 0,
        "formula_cells": sheet.formula_cells,
        "aadhaar_cells": 0,
        "ambiguous_dates": 0,
    }
    for result in rows:
        result.status = "error" if result.errors else "valid"
        stats["valid" if result.status == "valid" else "errors"] += 1
        stats["warnings"] += 1 if result.warnings else 0
        if result.status == "valid" and result.action is not None:
            stats[result.action] += 1
        stats["aadhaar_cells"] += sum(1 for e in result.errors if e["code"] == AADHAAR_CODE)
        stats["ambiguous_dates"] += sum(1 for w in result.warnings if w["code"] == "ambiguous_date")
    return ValidationResult(rows, stats)


__all__ = [
    "AADHAAR_CODE",
    "ANCHOR_SOURCE",
    "FORMULA_CODE",
    "AttributeSpec",
    "ExistingStudent",
    "RowResult",
    "SectionInfo",
    "ValidationContext",
    "ValidationResult",
    "admission_key",
    "allowed_targets",
    "is_full_aadhaar",
    "issue",
    "mapping_problems",
    "validate_sheet",
]
