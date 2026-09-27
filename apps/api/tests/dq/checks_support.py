"""Builders for in-memory DQ facts (synthetic data only). Loaded by path from tests/dq."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from app.dq.checks import CanonicalFact, CheckContext, EnrolmentFact, SourceFact, StudentFacts
from app.dq.matching import load_match_policy, load_variant_dictionary
from app.dq.profiles import Profile, load_engine_config, load_profiles

IDENTITY_KEYS = frozenset(
    {"full_name", "dob", "gender", "father_name", "mother_name", "admission_no", "admission_date"}
)
REGISTER = "admission_register"


def facts(
    *,
    student_id: uuid.UUID | None = None,
    values: Mapping[tuple[str, str], str | None] | None = None,
    canonical: Mapping[str, str | CanonicalFact | None] | None = None,
    enrolments: Iterable[EnrolmentFact] = (),
    admission_no: str | None = None,
) -> StudentFacts:
    """``values`` maps (physical attribute key, source) -> plaintext.

    ``canonical`` defaults to the register values (verified, not provisional).
    """
    per: dict[str, dict[str, SourceFact]] = {}
    for (key, source), value in (values or {}).items():
        per.setdefault(key, {})[source] = SourceFact(uuid.uuid4(), value)
    canon: dict[str, CanonicalFact] = {}
    if canonical is None:
        for key, sources in per.items():
            if REGISTER in sources:
                canon[key] = CanonicalFact(sources[REGISTER].value, REGISTER, False)
    else:
        for key, raw in canonical.items():
            canon[key] = (
                raw
                if isinstance(raw, CanonicalFact)
                else CanonicalFact(raw, REGISTER if raw is not None else None, False)
            )
    return StudentFacts(
        student_id=student_id or uuid.uuid4(),
        admission_no=admission_no,
        values=per,
        canonical=canon,
        enrolments=tuple(enrolments),
    )


def enrolment(
    class_code: str = "IX",
    *,
    starts_on: dt.date = dt.date(2026, 6, 1),
    current: bool = True,
    year_id: uuid.UUID | None = None,
) -> EnrolmentFact:
    return EnrolmentFact(
        enrollment_id=uuid.uuid4(),
        section_id=uuid.uuid4(),
        academic_year_id=year_id or uuid.uuid4(),
        class_code=class_code,
        year_starts_on=starts_on,
        current=current,
    )


def context(
    students: Iterable[StudentFacts],
    *,
    profiles: Iterable[str] = (),
    population: Iterable[StudentFacts] = (),
    **overrides: Any,
) -> CheckContext:
    all_profiles = load_profiles()
    chosen: tuple[Profile, ...] = tuple(all_profiles[p] for p in profiles)
    in_scope = {f.student_id: f for f in students}
    return CheckContext(
        students=in_scope,
        config=overrides.pop("config", load_engine_config()),
        policy=load_match_policy(),
        variants=load_variant_dictionary(),
        population={f.student_id: f for f in population} | in_scope,
        profiles=chosen,
        identity_keys=IDENTITY_KEYS,
        **overrides,
    )
