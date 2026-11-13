"""Board candidate catalogue: medium, language and subject choices per board scheme (FO-10;
FR-STU-020..024, FR-STU-030; ``board_catalogue.yaml``).

Pure (no database): the catalogue, its validation and :func:`candidate_issues`, the one place
that decides whether a candidate's recorded choices are complete and valid. The board candidate
grid (``app.students.board_candidate``) shows the same issues that DQ-032 raises as findings
(``app.dq.checks``, through ``app.students.service``), so the two never disagree.

Values are catalogue codes, never personal data beyond the choice itself (C2). Photographs and
signatures are not held: ``photo_signature_ready`` is a yes/no tick (an ADR would be needed to
store them).
"""

from __future__ import annotations

import functools
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, Final, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MEDIUM_KEY: Final = "exam_medium"
LANGUAGE_SLOTS: Final = ("first_language", "second_language", "third_language")
SUBJECTS_KEY: Final = "board_subjects"
PHOTO_KEY: Final = "photo_signature_ready"
CANDIDATE_KEYS: Final = (MEDIUM_KEY, *LANGUAGE_SLOTS, SUBJECTS_KEY, PHOTO_KEY)
PHOTO_READY: Final = "yes"
MEDIUM_VALUES: Final = ("english", "telugu", "urdu", "hindi", "tamil", "kannada", "odia")

_CODE_RE: Final = re.compile(r"^[A-Z0-9][A-Z0-9_]{1,15}$")
_SCHEME_RE: Final = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
_CLASS_RE: Final = re.compile(r"^[A-Z0-9]{1,8}$")
_BOARDS: Final = frozenset({"BSEAP", "CBSE", "CISCE"})

IssueReason = Literal["missing", "not_in_list", "repeated", "group_missing", "too_many", "photo"]


class LanguageSlot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    slot: str
    required: bool = True
    options: tuple[str, ...] = Field(min_length=1)

    @field_validator("slot")
    @classmethod
    def _slot(cls, value: str) -> str:
        if value not in LANGUAGE_SLOTS:
            raise ValueError(f"slot must be one of {list(LANGUAGE_SLOTS)}")
        return value


class SubjectGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    label_en: str = Field(min_length=1)
    required: bool = True
    min: int = Field(ge=0)
    max: int = Field(ge=1)
    options: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _bounds(self) -> SubjectGroup:
        if self.min > self.max or (self.required and self.min < 1):
            raise ValueError(f"{self.key}: min/max out of order")
        return self


class MediumSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    required: bool = True
    options: tuple[str, ...] = Field(min_length=1)

    @field_validator("options")
    @classmethod
    def _options(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(MEDIUM_VALUES))
        if unknown:
            raise ValueError(f"unknown media {unknown}")
        return value


class BoardScheme(BaseModel):
    """One board's candidate choices (``verified: false`` until a school confirmed them)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    version: int = Field(ge=1)
    label_en: str = Field(min_length=1)
    board: str
    classes: tuple[str, ...] = Field(min_length=1)
    verified: bool = False
    source: tuple[str, ...] = Field(min_length=1)
    source_note: str = Field(min_length=1)
    medium: MediumSpec
    languages: tuple[LanguageSlot, ...] = ()
    subjects: tuple[SubjectGroup, ...] = ()
    max_subjects: int = Field(ge=0)
    photo_required: bool = True
    codes: Mapping[str, str]

    @field_validator("board")
    @classmethod
    def _board(cls, value: str) -> str:
        if value not in _BOARDS:
            raise ValueError(f"board must be one of {sorted(_BOARDS)}")
        return value

    @field_validator("classes")
    @classmethod
    def _classes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value) or not all(_CLASS_RE.match(c) for c in value):
            raise ValueError("classes must be unique class codes")
        return value

    @field_validator("source")
    @classmethod
    def _source(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not all(v.startswith("https://") for v in value):
            raise ValueError("source entries must be https URLs")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> BoardScheme:
        if not _SCHEME_RE.match(self.key):
            raise ValueError("scheme keys are lowercase letters, digits and hyphens")
        slots = [s.slot for s in self.languages]
        if len(set(slots)) != len(slots):
            raise ValueError(f"{self.key}: a language slot is listed twice")
        groups = [g.key for g in self.subjects]
        if len(set(groups)) != len(groups):
            raise ValueError(f"{self.key}: a subject group is listed twice")
        used = {c for s in self.languages for c in s.options} | {
            c for g in self.subjects for c in g.options
        }
        bad = sorted(c for c in used if not _CODE_RE.match(c))
        if bad:
            raise ValueError(f"{self.key}: malformed codes {bad}")
        unlabelled = sorted(used - set(self.codes))
        if unlabelled:
            raise ValueError(f"{self.key}: codes without a label {unlabelled}")
        if self.subjects and self.max_subjects < sum(g.min for g in self.subjects):
            raise ValueError(f"{self.key}: max_subjects is below the required subjects")
        return self

    def applies_to(self, class_code: str | None) -> bool:
        return class_code is not None and class_code in self.classes

    def slot(self, key: str) -> LanguageSlot | None:
        return next((s for s in self.languages if s.slot == key), None)

    def group_label(self, key: str) -> str:
        """The English label of subject group ``key`` (the key itself when unknown)."""
        return next((g.label_en for g in self.subjects if g.key == key), key)

    def issues(self, values: Mapping[str, str | None], /) -> tuple[CandidateIssue, ...]:
        """:func:`candidate_issues` for this scheme (DQ-032 calls it through the engine)."""
        return candidate_issues(self, values)

    @property
    def keys(self) -> tuple[str, ...]:
        """The candidate attribute keys this scheme asks for (grid columns, sheet columns)."""
        out: list[str] = [MEDIUM_KEY]
        out.extend(s.slot for s in self.languages)
        if self.subjects:
            out.append(SUBJECTS_KEY)
        out.append(PHOTO_KEY)
        return tuple(out)


class BoardCatalogue(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=1)
    schemes: Mapping[str, BoardScheme]

    def for_board(self, board: str) -> tuple[BoardScheme, ...]:
        return tuple(s for s in self.schemes.values() if s.board == board)


def parse_catalogue(raw: Any) -> BoardCatalogue:
    if not isinstance(raw, dict) or not isinstance(raw.get("schemes"), dict):
        raise ValueError("board catalogue needs a `schemes` mapping")
    schemes = {
        key: BoardScheme.model_validate({"key": key, **spec})
        for key, spec in raw["schemes"].items()
    }
    return BoardCatalogue(version=raw.get("version", 0), schemes=schemes)


@functools.cache
def load_board_catalogue() -> BoardCatalogue:
    """The packaged catalogue (``app/students/board_catalogue.yaml``), validated once."""
    text = resources.files("app.students").joinpath("board_catalogue.yaml").read_text("utf-8")
    return parse_catalogue(yaml.safe_load(text))


# --- candidate issues ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateIssue:
    """One reason a candidate is not ready: ``field`` is the attribute key; ``code`` the
    offending catalogue code (``not_in_list``, ``repeated``); ``group`` the subject group key
    (``group_missing``, ``too_many``)."""

    field: str
    reason: IssueReason
    code: str | None = None
    group: str | None = None


def split_codes(value: str | None) -> list[str]:
    """``"041, 086,087"`` -> ``["041", "086", "087"]`` (upper case, empty parts dropped)."""
    if value is None:
        return []
    return [p.strip().upper() for p in value.split(",") if p.strip()]


def _blank(value: str | None) -> bool:
    return value is None or not value.strip()


def _language_issues(scheme: BoardScheme, values: Mapping[str, str | None]) -> list[CandidateIssue]:
    out: list[CandidateIssue] = []
    seen: set[str] = set()
    for slot in scheme.languages:
        raw = values.get(slot.slot)
        if _blank(raw):
            if slot.required:
                out.append(CandidateIssue(slot.slot, "missing"))
            continue
        code = str(raw).strip().upper()
        if code not in slot.options:
            out.append(CandidateIssue(slot.slot, "not_in_list", code=code))
        elif code in seen:
            out.append(CandidateIssue(slot.slot, "repeated", code=code))
        seen.add(code)
    return out


def _subject_issues(
    scheme: BoardScheme, raw: str | None, languages: set[str]
) -> list[CandidateIssue]:
    if not scheme.subjects:
        return []
    codes = split_codes(raw)
    if not codes:
        return [CandidateIssue(SUBJECTS_KEY, "missing")]
    out: list[CandidateIssue] = []
    known = {c for g in scheme.subjects for c in g.options}
    seen: set[str] = set()
    for code in codes:
        if code not in known:
            out.append(CandidateIssue(SUBJECTS_KEY, "not_in_list", code=code))
        elif code in seen or code in languages:
            out.append(CandidateIssue(SUBJECTS_KEY, "repeated", code=code))
        seen.add(code)
    for group in scheme.subjects:
        chosen = [c for c in dict.fromkeys(codes) if c in group.options and c not in languages]
        if len(chosen) < group.min:
            out.append(CandidateIssue(SUBJECTS_KEY, "group_missing", group=group.key))
        elif len(chosen) > group.max:
            out.append(CandidateIssue(SUBJECTS_KEY, "too_many", group=group.key))
    if len(set(codes)) > scheme.max_subjects:
        out.append(CandidateIssue(SUBJECTS_KEY, "too_many"))
    return out


def candidate_issues(
    scheme: BoardScheme, values: Mapping[str, str | None]
) -> tuple[CandidateIssue, ...]:
    """Every reason the recorded choices (attribute key -> canonical value) are not ready for
    ``scheme``, in column order. Empty: ready. Nothing is corrected (invariant 6)."""
    out: list[CandidateIssue] = []
    medium = values.get(MEDIUM_KEY)
    if _blank(medium):
        if scheme.medium.required:
            out.append(CandidateIssue(MEDIUM_KEY, "missing"))
    elif str(medium).strip().lower() not in scheme.medium.options:
        out.append(CandidateIssue(MEDIUM_KEY, "not_in_list", code=str(medium).strip().lower()))
    out.extend(_language_issues(scheme, values))
    languages = {
        str(values[s.slot]).strip().upper()
        for s in scheme.languages
        if not _blank(values.get(s.slot))
    }
    out.extend(_subject_issues(scheme, values.get(SUBJECTS_KEY), languages))
    if scheme.photo_required:
        photo = values.get(PHOTO_KEY)
        if _blank(photo) or str(photo).strip().lower() != PHOTO_READY:
            out.append(CandidateIssue(PHOTO_KEY, "photo"))
    return tuple(out)


def normalise_codes(codes: Sequence[str]) -> str:
    """The stored form of a subject list: upper case, de-duplicated, comma separated."""
    return ",".join(dict.fromkeys(c.strip().upper() for c in codes if c.strip()))
