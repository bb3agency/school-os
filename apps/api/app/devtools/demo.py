"""The demo school as pure data (FA-12; FR-DEMO-001..015; docs/guides/demo-school.md). NEVER real
data.

SYNTHETIC ONLY (CLAUDE.md invariant 11). The demo school is one school of the synthetic dataset
(:func:`app.devtools.plan.build_plan`, so every person, e-mail and IdP subject follows the
generator's markers and ``make synthetic-check`` finds it) with a fixed, clearly fictional name
and the boards that show the AP wedge (State Board SSC, UDISE+, APAAR) and the CBSE disclosure.
On top of the generator's students, documents and Class X fees, :mod:`app.devtools.demo_seeder`
applies the scenario below so that every screen built in feature waves F1/F2 has something to
show.

Generations (FR-DEMO-004): the school code is ``demo-g<n>-a``; a reset (``--reset``) deletes the
live generation through the offboarding purge and seeds generation ``n + 1``. Tenant IDs stay
UUIDv5 values of ``(dataset_version, code)``, so each generation is deterministic too.

Everything here is a pure function of ``(seed, generation)``; dates that must be "near today"
(certificates expiring, follow-ups overdue) are offsets applied to the day of the run.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

from app.devtools.plan import DatasetPlan, TenantPlan, build_plan

DEMO_SEED: Final = 20270201
DEMO_STUDENTS: Final = 400
DEMO_CODE_PREFIX: Final = "demo"
DEMO_CODE_RE: Final = re.compile(r"^demo-g([1-9][0-9]{0,3})-a$")
MAX_GENERATION: Final = 9999
# "Synthetic" and "Demo" in the name: nobody mistakes it for a real school (FR-DEMO-002), and
# make synthetic-check recognises it like every generated school.
DEMO_SCHOOL_NAME: Final = "Sunrise Synthetic Demo School"
DEMO_BOARDS: Final = ("BSEAP", "CBSE")


class DemoPlanError(ValueError):
    """The requested demo generation is invalid."""


def code_for(generation: int) -> str:
    if not 1 <= generation <= MAX_GENERATION:
        raise DemoPlanError(f"generation must be between 1 and {MAX_GENERATION}")
    return f"{DEMO_CODE_PREFIX}-g{generation}-a"


def generation_of(code: str) -> int | None:
    """The generation of a demo school code, or None for any other code."""
    match = DEMO_CODE_RE.match(code)
    return int(match.group(1)) if match else None


def build_demo_plan(*, seed: int = DEMO_SEED, generation: int = 1) -> DatasetPlan:
    """One school: the generator's plan for ``demo-g<generation>-a`` with the demo name and boards
    (FR-DEMO-001, FR-DEMO-003). Deterministic for ``(seed, generation)``."""
    code_for(generation)
    base = build_plan(seed=seed, tenants=1, code_prefix=f"{DEMO_CODE_PREFIX}-g{generation}")
    school: TenantPlan = dataclasses.replace(
        base.tenants[0], name=DEMO_SCHOOL_NAME, boards=DEMO_BOARDS
    )
    return dataclasses.replace(base, tenants=(school,))


# --- the scenario (applied by app.devtools.demo_seeder) -------------------------------------------

# FR-DEMO-006 earlier-year dues: Class IX fees of 2025-26, never paid by some of today's Class X.
ARREARS_PLAN_NAME: Final = "Demo Class IX fees 2025-26"
ARREARS_CLASS: Final = "IX"
ARREARS_SECTION: Final = "A"
ARREARS_HEAD: Final = "TUI"
ARREARS_ANNUAL: Final = Decimal("10000.00")
ARREARS_INSTALMENTS: Final = (
    (1, "Term 1", dt.date(2025, 6, 15)),
    (2, "Term 2", dt.date(2026, 1, 15)),
)
ARREARS_STARTED_ON: Final = dt.date(2025, 6, 2)
ARREARS_ENDED_ON: Final = dt.date(2026, 4, 30)
# Class X fee accounts by admission number: the first four carry the generator's receipts.
ARREARS_ACCOUNTS: Final = slice(4, 10)


@dataclass(frozen=True, slots=True)
class AddonSpec:
    head_code: str
    head_name: str
    head_kind: Literal["transport", "books", "other"]
    label: str
    per_instalment: Decimal
    accounts: slice
    """Which Class X fee accounts (by admission number) are put on it."""


# FR-DEMO-007 add-on fees: two transport bands on one head and one optional activity, charged on
# both instalments of the generator's Class X plan (app.devtools.finance_seeder.INSTALMENTS).
ADDON_INSTALMENTS: Final = (1, 2)
ADDONS: Final = (
    AddonSpec(
        "TRN", "Transport fee", "transport", "Band 1 (0-5 km)", Decimal("3000.00"), slice(0, 12)
    ),
    AddonSpec(
        "TRN", "Transport fee", "transport", "Band 2 (5-10 km)", Decimal("4500.00"), slice(12, 18)
    ),
    AddonSpec("ACT", "Activity fee", "other", "Robotics club", Decimal("1500.00"), slice(2, 6)),
)


@dataclass(frozen=True, slots=True)
class CertificateSpec:
    kind: str
    reference_no: str
    issued_by: str
    valid_from_days: int
    valid_until_days: int | None
    """Offsets from the day of the run (negative: in the past); None: does not expire."""


# FR-DEMO-009 compliance register: one valid, one expiring within 30 days, one expired, one
# without expiry; every reference number says DEMO.
CERTIFICATES: Final = (
    CertificateSpec("building_safety", "DEMO/BS/0001", "Demo Municipal Engineer", -200, 530),
    CertificateSpec("fire_noc", "DEMO/FIRE/0001", "Demo Fire Station Officer", -340, 21),
    CertificateSpec("water_safety", "DEMO/WATER/0001", "Demo Public Health Lab", -380, -15),
    CertificateSpec(
        "trust_registration", "DEMO/TRUST/0001", "Demo Registrar of Societies", -3650, None
    ),
)


@dataclass(frozen=True, slots=True)
class CalendarEntrySpec:
    kind: Literal["holiday", "vacation", "working_day"]
    name: str
    starts_on: dt.date
    ends_on: dt.date


# FR-DEMO-010 school calendar 2026-27: Sundays and second Saturdays off (the D85 default), two
# vacations, one holiday and one extra working day. Demo dates, not an official calendar.
CALENDAR_WEEKLY_OFF: Final = (6,)
CALENDAR_SATURDAYS_OFF: Final = (2,)
CALENDAR_ENTRIES: Final = (
    CalendarEntrySpec(
        "vacation", "Dasara holidays (demo)", dt.date(2026, 10, 2), dt.date(2026, 10, 14)
    ),
    CalendarEntrySpec(
        "vacation", "Sankranti holidays (demo)", dt.date(2027, 1, 10), dt.date(2027, 1, 18)
    ),
    CalendarEntrySpec("holiday", "Republic Day", dt.date(2027, 1, 26), dt.date(2027, 1, 26)),
    CalendarEntrySpec(
        "working_day",
        "Annual day (extra working day, demo)",
        dt.date(2026, 12, 13),
        dt.date(2026, 12, 13),
    ),
)

# FR-DEMO-008 UDISE+ follow-ups on fixes outside the school (FO-01): slips given to parents, and
# requests sent to the MEO long enough ago to be overdue (14 days, D85).
# The SSC 2027 readiness check (classes IX and X) names the parent (Aadhaar) and the MEO
# (UDISE+) as fix owners; its follow-up page is where the demo shows them.
READINESS_PROFILE: Final = "bseap-ssc-2027"
FOLLOWUP_SLIPS: Final = 4
FOLLOWUP_REQUESTS: Final = 2
FOLLOWUP_REQUEST_DAYS_AGO: Final = 20

# FR-DEMO-012 CBSE disclosure draft: fictional values only, nothing published (publishing is a
# step-up action the presenter shows live). Contact items stay empty: no phone or e-mail.
DISCLOSURE_INPUTS: Final[dict[str, object]] = {
    "affiliation_no": "DEMO-000000",
    "school_code": "DEMO-00000",
    "principal_qualification": "M.Sc., B.Ed. (demo)",
    "vice_principal_count": 1,
    "campus_area": 6000,
    "library_area": 120,
}
