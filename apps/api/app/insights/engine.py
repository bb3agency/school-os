"""ABC indicators and early-warning rules (FR-EW-001..003; FR-MRK-005). Pure: no database, no
web, no AI.

The engine is deterministic and explainable (08 §4 PRV-005): it sees one student's marked school
days (date, status), exam percentages of the current academic year and the dates of "concern"
notes, never names or note text. :func:`findings` returns what the rules of ``rules.yaml``
(with the school's thresholds) would flag, each with a ``basis`` (what it is about: the run's
first day, the exam, the month) and ``evidence`` (numbers, codes and ISO dates only) that the
flag keeps and shows. Whether a flag is actually raised (not already open, not raised before
for that basis) is decided by the service and the database's unique keys (FR-EW-003).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.insights.config import Indicator, RuleKey, RuleSetting

PRESENT = frozenset({"present", "late"})


class MarkLike(Protocol):
    @property
    def on_date(self) -> dt.date: ...

    @property
    def status(self) -> str: ...


class ResultLike(Protocol):
    @property
    def exam_id(self) -> uuid.UUID: ...

    @property
    def held_on(self) -> dt.date: ...

    @property
    def percent(self) -> float | None: ...


@dataclass(frozen=True, slots=True)
class Mark:
    on_date: dt.date
    status: str


@dataclass(frozen=True, slots=True)
class Result:
    exam_id: uuid.UUID
    held_on: dt.date
    percent: float | None


@dataclass(frozen=True, slots=True)
class Facts:
    """One student's facts: marks and results oldest first; concern-note dates in any order."""

    marks: Sequence[MarkLike] = ()
    results: Sequence[ResultLike] = ()
    concern_dates: Sequence[dt.date] = ()


@dataclass(frozen=True, slots=True)
class AttendanceIndicator:
    days: int
    present: int
    late: int
    absent: int
    leave: int
    rate: float | None
    streak: int
    streak_from: dt.date | None
    streak_to: dt.date | None
    concern: bool


@dataclass(frozen=True, slots=True)
class BehaviourIndicator:
    concerns: int
    window_days: int
    concern: bool


@dataclass(frozen=True, slots=True)
class CourseIndicator:
    exam_id: uuid.UUID | None
    percent: float | None
    previous_exam_id: uuid.UUID | None
    previous_percent: float | None
    change: float | None
    concern: bool


@dataclass(frozen=True, slots=True)
class Indicators:
    attendance: AttendanceIndicator
    behaviour: BehaviourIndicator
    course: CourseIndicator


@dataclass(frozen=True, slots=True)
class Finding:
    rule: RuleKey
    indicator: Indicator
    basis: str
    evidence: dict[str, Any] = field(default_factory=dict)


# --- attendance -----------------------------------------------------------------------------------


def _streak(marks: Sequence[MarkLike]) -> tuple[int, dt.date | None, dt.date | None]:
    """The run of absences at the end of the student's marked days."""
    count, first, last = 0, None, None
    for mark in reversed(marks):
        if mark.status != "absent":
            break
        count += 1
        first = mark.on_date
        last = last or mark.on_date
    return count, first, last


def _window(marks: Sequence[MarkLike], size: int) -> list[MarkLike]:
    return list(marks[-size:]) if size > 0 else []


def _rate(marks: Sequence[MarkLike]) -> tuple[float | None, int]:
    """Present-or-late share of the marked days, leave days left out; (rate, days counted)."""
    counted = [m for m in marks if m.status != "leave"]
    if not counted:
        return None, 0
    present = sum(1 for m in counted if m.status in PRESENT)
    return round(present * 100 / len(counted), 1), len(counted)


def _attendance(
    marks: Sequence[MarkLike], settings: Mapping[RuleKey, RuleSetting]
) -> AttendanceIndicator:
    streak_rule = settings["attendance_streak"]
    rate_rule = settings["attendance_rate"]
    window = _window(marks, rate_rule.spec.window or 30)
    rate, counted = _rate(window)
    streak, first, last = _streak(marks)
    by = {
        s: sum(1 for m in window if m.status == s) for s in ("present", "late", "absent", "leave")
    }
    rate_concern = (
        rate_rule.enabled
        and rate is not None
        and counted >= (rate_rule.spec.min_days or 1)
        and rate < rate_rule.threshold
    )
    return AttendanceIndicator(
        days=len(window),
        present=by["present"],
        late=by["late"],
        absent=by["absent"],
        leave=by["leave"],
        rate=rate,
        streak=streak,
        streak_from=first,
        streak_to=last,
        concern=bool(rate_concern) or (streak_rule.enabled and streak >= streak_rule.threshold),
    )


# --- course performance ---------------------------------------------------------------------------


def _course(
    results: Sequence[ResultLike], settings: Mapping[RuleKey, RuleSetting]
) -> CourseIndicator:
    scored = [r for r in results if r.percent is not None]
    latest = scored[-1] if scored else None
    previous = scored[-2] if len(scored) > 1 else None
    change = (
        round(latest.percent - previous.percent, 1)  # type: ignore[operator]
        if latest is not None and previous is not None
        else None
    )
    low, decline = settings["course_low"], settings["course_decline"]
    concern = bool(
        latest is not None
        and (
            (low.enabled and latest.percent < low.threshold)  # type: ignore[operator]
            or (decline.enabled and change is not None and -change >= decline.threshold)
        )
    )
    return CourseIndicator(
        exam_id=latest.exam_id if latest else None,
        percent=latest.percent if latest else None,
        previous_exam_id=previous.exam_id if previous else None,
        previous_percent=previous.percent if previous else None,
        change=change,
        concern=concern,
    )


# --- behaviour ------------------------------------------------------------------------------------


def _behaviour(
    dates: Sequence[dt.date], settings: Mapping[RuleKey, RuleSetting], today: dt.date
) -> BehaviourIndicator:
    rule = settings["behaviour_concerns"]
    window = rule.spec.window or 30
    since = today - dt.timedelta(days=window - 1)
    count = sum(1 for d in dates if since <= d <= today)
    return BehaviourIndicator(
        concerns=count, window_days=window, concern=rule.enabled and count >= rule.threshold
    )


def indicators(facts: Facts, settings: Mapping[RuleKey, RuleSetting], today: dt.date) -> Indicators:
    """The three ABC indicators of one student (FR-EW-001)."""
    return Indicators(
        attendance=_attendance(facts.marks, settings),
        behaviour=_behaviour(facts.concern_dates, settings, today),
        course=_course(facts.results, settings),
    )


def findings(
    facts: Facts, settings: Mapping[RuleKey, RuleSetting], today: dt.date
) -> list[Finding]:
    """What the enabled rules flag for one student (FR-EW-002), with basis and evidence."""
    ind = indicators(facts, settings, today)
    out: list[Finding] = []
    month = f"month:{today.strftime('%Y-%m')}"
    streak = settings["attendance_streak"]
    a = ind.attendance
    if streak.enabled and a.streak >= streak.threshold and a.streak_from and a.streak_to:
        out.append(
            Finding(
                "attendance_streak",
                "attendance",
                f"run:{a.streak_from.isoformat()}",
                {
                    "days": a.streak,
                    "from": a.streak_from.isoformat(),
                    "to": a.streak_to.isoformat(),
                    "threshold": streak.threshold,
                },
            )
        )
    rate = settings["attendance_rate"]
    counted = a.days - a.leave
    if (
        rate.enabled
        and a.rate is not None
        and counted >= (rate.spec.min_days or 1)
        and a.rate < rate.threshold
    ):
        out.append(
            Finding(
                "attendance_rate",
                "attendance",
                month,
                {
                    "rate": int(a.rate),
                    "days": counted,
                    "present": a.present + a.late,
                    "threshold": rate.threshold,
                },
            )
        )
    c = ind.course
    low = settings["course_low"]
    if (
        low.enabled
        and c.exam_id is not None
        and c.percent is not None
        and c.percent < low.threshold
    ):
        out.append(
            Finding(
                "course_low",
                "course",
                f"exam:{c.exam_id}",
                {"exam_id": str(c.exam_id), "percent": c.percent, "threshold": low.threshold},
            )
        )
    decline = settings["course_decline"]
    if (
        decline.enabled
        and c.exam_id is not None
        and c.previous_exam_id is not None
        and c.change is not None
        and -c.change >= decline.threshold
    ):
        out.append(
            Finding(
                "course_decline",
                "course",
                f"exam:{c.exam_id}",
                {
                    "exam_id": str(c.exam_id),
                    "previous_exam_id": str(c.previous_exam_id),
                    "percent": c.percent,
                    "previous_percent": c.previous_percent,
                    "drop": round(-c.change, 1),
                    "threshold": decline.threshold,
                },
            )
        )
    b = ind.behaviour
    behaviour = settings["behaviour_concerns"]
    if behaviour.enabled and b.concerns >= behaviour.threshold:
        out.append(
            Finding(
                "behaviour_concerns",
                "behaviour",
                month,
                {
                    "concerns": b.concerns,
                    "window_days": b.window_days,
                    "threshold": behaviour.threshold,
                },
            )
        )
    return out


__all__ = [
    "AttendanceIndicator",
    "BehaviourIndicator",
    "CourseIndicator",
    "Facts",
    "Finding",
    "Indicators",
    "Mark",
    "MarkLike",
    "Result",
    "ResultLike",
    "findings",
    "indicators",
]
