"""Data-principal subject protocol (R3-1A; ADR-0048 decisions 6 and 8; FR-DPDP-008..014,
NFR-PRV-007).

The access report and lawful erasure are assembled from every module that holds personal data.
Each such module exposes two public functions in its own ``service.py`` and lists them in
``app/privacy/subjects.yaml`` (fail closed: a module that owns tenant tables and is neither a
contributor nor declared ``no_personal_data`` fails ``tests/privacy/test_registries.py``):

- ``subject_report(session, subject) -> list[ReportSection]``: what the module holds about the
  subject, one or more sections per record class (keys from ``app/privacy/record_classes.yaml``).
  Runs in the caller's ``tenant_session`` (RLS: this school only). The privacy service has
  already checked who may see the subject; contributors never widen it to other people: never
  another child's data, never another person's free text, never an Aadhaar number (only "last 4
  digits and as-printed details held"), never secrets, hashes, tokens or ciphertext. Restricted
  (C3) values of the subject's own record may be included in clear.
- ``erase_for_subject(session, subject, class_keys, *, dry_run) -> list[ErasureOutcome]``
  (only modules whose record classes allow erasure): with ``dry_run=True`` count what would be
  erased and change nothing; otherwise erase it in the caller's transaction (derived AI data
  follows its source, docs/08 §7) and return the counts. Anything the module cannot erase by
  itself is returned as a plain-English manual step. Never erases official records: the
  privacy service only passes class keys whose rule allows erasure.

Pure value types (no database, no feature-module imports): ``core`` is the bottom layer.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Final, Literal, Protocol

from sqlalchemy.orm import Session

SubjectKind = Literal["student", "guardian", "member"]
SUBJECT_KINDS: Final[tuple[SubjectKind, ...]] = ("student", "guardian", "member")
MAX_SECTION_ROWS: Final = 500  # a contributor truncates longer sections and says so in ``note``


@dataclass(frozen=True, slots=True)
class Subject:
    """The person a request is about: a student (``sis.students.id``), a guardian
    (``sis.guardians.id``) or a staff member (``core.memberships.id``) of the current school."""

    kind: SubjectKind
    id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ReportSection:
    """One block of the access report: a small table of already-formatted English strings.

    ``class_key`` names the record class (``record_classes.yaml``) the rows belong to; the
    report prints the class's source, retention and erasure rule next to the rows. ``note`` is
    an optional plain-English remark (e.g. "Showing the latest 500 of 1,234 entries")."""

    class_key: str
    title: str
    columns: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...] = ()
    note: str | None = None

    def __post_init__(self) -> None:
        width = len(self.columns)
        if width == 0:
            raise ValueError("a report section needs at least one column")
        if any(len(r) != width for r in self.rows):
            raise ValueError(f"every row of section {self.title!r} needs {width} values")


@dataclass(frozen=True, slots=True)
class ErasureOutcome:
    """What a module erased (or, in a dry run, would erase) for one record class."""

    class_key: str
    count: int
    manual_steps: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        if self.count < 0:
            raise ValueError("count must not be negative")


class SubjectReporter(Protocol):
    def __call__(self, session: Session, subject: Subject) -> list[ReportSection]: ...


class SubjectEraser(Protocol):
    def __call__(
        self,
        session: Session,
        subject: Subject,
        class_keys: Collection[str],
        *,
        dry_run: bool,
    ) -> list[ErasureOutcome]: ...


__all__ = [
    "MAX_SECTION_ROWS",
    "SUBJECT_KINDS",
    "ErasureOutcome",
    "ReportSection",
    "Subject",
    "SubjectEraser",
    "SubjectKind",
    "SubjectReporter",
]
