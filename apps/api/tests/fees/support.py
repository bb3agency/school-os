"""Fee test objects created through the real services (synthetic data only).

Loaded by path (pytest runs with ``--import-mode=importlib``) from ``tests/fees`` and the security
suites (authz matrix, BOLA). Builds on ``tests/api/world.py`` (schools and staff),
``tests/students/student_world.py`` (students) and ``tests/documents/support.py`` (in-memory
object store).

A school's fee setup: heads ``TUI`` (tuition) and ``EXM`` (exam), and ``BUS`` (transport,
taxable: never collected), one active plan for Class IX of the current year with two
instalments: the first due 30 days ago (overdue), the second due in 30 days (both inside the
synthetic year 2026-27).
"""

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import importlib.util
import itertools
import sys
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, text

from app.authz.context import UserContext
from app.core.db import tenant_session
from app.fees import service as fees
from app.fees.schemas import (
    HeadCreate,
    InstalmentIn,
    LateFeeRule,
    PlanApplyIn,
    PlanCreate,
    PlanLineIn,
    ReceiptCreate,
)

_HERE = Path(__file__).resolve()
_names = itertools.count(1)
IST = ZoneInfo("Asia/Kolkata")
FLAG = "fees.enabled"
REASON = "Synthetic reason for this fee request"


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


W = _load("sos_test_api_world", _HERE.parents[1] / "api" / "world.py")
SW = _load("sos_test_student_world", _HERE.parents[1] / "students" / "student_world.py")
D = _load("sos_test_documents_support", _HERE.parents[1] / "documents" / "support.py")


class FakeRenderer:
    def __init__(self) -> None:
        self.pages: list[str] = []

    def render(self, html: str) -> bytes:
        self.pages.append(html)
        pdf: bytes = D.pdf()
        return pdf


def install() -> Any:
    SW.configure_keyring()
    return D.memory_store()


def today() -> dt.date:
    return dt.datetime.now(IST).date()


# --- switching fees on (FR-FEE-001) --------------------------------------------------------------


def set_flag(admin: Engine, tenant_id: uuid.UUID, *, enabled: bool) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO platform.feature_flags (id, key, tenant_id, enabled) "
                "VALUES (:i, :k, :t, :e) ON CONFLICT (key, tenant_id) "
                "DO UPDATE SET enabled = EXCLUDED.enabled"
            ),
            {"i": uuid.uuid4(), "k": FLAG, "t": tenant_id, "e": enabled},
        )


def set_setting(admin: Engine, tenant_id: uuid.UUID, *, enabled: bool) -> None:
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE core.tenants SET settings = jsonb_set(coalesce(settings, '{}'::jsonb), "
                "'{fees_enabled}', to_jsonb(CAST(:e AS boolean))) WHERE id = :t"
            ),
            {"t": tenant_id, "e": enabled},
        )


def enable(admin: Engine, tenant_id: uuid.UUID, *, on: bool = True) -> None:
    set_flag(admin, tenant_id, enabled=on)
    set_setting(admin, tenant_id, enabled=on)


@contextlib.contextmanager
def fees_on(admin: Engine, tenant_id: uuid.UUID) -> Iterator[None]:
    """Fees on for one block (security suites share schools with other tests)."""
    enable(admin, tenant_id)
    try:
        yield
    finally:
        enable(admin, tenant_id, on=False)


# --- contexts and calls ---------------------------------------------------------------------------


def ctx(school: Any, person: Any, role: str, **scopes: Any) -> UserContext:
    """The role's permissions with a fresh MFA sign-in (step-up satisfied)."""
    base: UserContext = SW.ctx_for(school.tenant_id, person, role, **scopes)
    return dataclasses.replace(base, auth_time=dt.datetime.now(dt.UTC))


def call(school: Any, role: str, fn: Any, *args: Any, **kw: Any) -> Any:
    person = kw.pop("person", None) or school.people[role]
    c = kw.pop("as_ctx", None) or ctx(school, person, role)
    with tenant_session(school.tenant_id, person.user_id) as s:
        return fn(s, c, *args, **kw)


# --- setup ----------------------------------------------------------------------------------------


@dataclasses.dataclass
class Setup:
    tui: uuid.UUID
    exm: uuid.UUID
    bus: uuid.UUID
    plan_id: uuid.UUID
    first_due: dt.date
    second_due: dt.date


_SETUPS: dict[uuid.UUID, Setup] = {}


def due_dates() -> tuple[dt.date, dt.date]:
    start, end = dt.date(2026, 6, 1), dt.date(2027, 3, 31)
    now = today()
    first = max(start, now - dt.timedelta(days=30))
    second = min(end, now + dt.timedelta(days=30))
    return first, second


def ensure_setup(school: Any, *, late_fee: LateFeeRule | None = None) -> Setup:
    """Heads and the active Class IX plan of ``school`` (once per school)."""
    install()
    found = _SETUPS.get(school.tenant_id)
    if found is not None:
        return found
    heads = {}
    for code, name, kind, exempt in (
        ("TUI", "Tuition fee", "tuition", True),
        ("EXM", "Exam fee", "exam", True),
        ("BUS", "Transport fee", "transport", False),
    ):
        heads[code] = call(
            school,
            "accountant",
            fees.create_head,
            HeadCreate(code=code, name=name, kind=kind, gst_exempt=exempt),
        ).id
    first, second = due_dates()
    plan = call(
        school,
        "accountant",
        fees.create_plan,
        PlanCreate(
            academic_year_id=school.ids["year"],
            class_id=school.ids["class_ix"],
            name="Class IX 2026-27",
            lines=[
                PlanLineIn(head_id=heads["TUI"], annual_amount=Decimal("6000.00")),
                PlanLineIn(head_id=heads["EXM"], annual_amount=Decimal("1000.00")),
                PlanLineIn(head_id=heads["BUS"], annual_amount=Decimal("2000.00")),
            ],
            instalments=[
                InstalmentIn(
                    instalment_no=1,
                    label="Term 1",
                    due_on=first,
                    amounts={
                        heads["TUI"]: Decimal("3000.00"),
                        heads["EXM"]: Decimal("500.00"),
                        heads["BUS"]: Decimal("1000.00"),
                    },
                ),
                InstalmentIn(
                    instalment_no=2,
                    label="Term 2",
                    due_on=second,
                    amounts={
                        heads["TUI"]: Decimal("3000.00"),
                        heads["EXM"]: Decimal("500.00"),
                        heads["BUS"]: Decimal("1000.00"),
                    },
                ),
            ],
            late_fee_rule=late_fee or LateFeeRule(),
        ),
    )
    plan = call(school, "accountant", fees.activate_plan, plan.id, expected_version=plan.version)
    out = Setup(heads["TUI"], heads["EXM"], heads["BUS"], plan.id, first, second)
    _SETUPS[school.tenant_id] = out
    return out


def student(school: Any, section_key: str = "section_9a") -> uuid.UUID:
    n = next(_names)
    value: uuid.UUID = SW.create(
        school,
        name=f"Synthetica Fee Student {n:04d}",
        section_key=section_key,
        admission_no=f"F/{uuid.uuid4().hex[:6].upper()}",
    )
    return value


def account(school: Any, student_id: uuid.UUID | None = None) -> uuid.UUID:
    """A fresh student of 9A with a fee account charged both instalments (applied with past
    instalments: the student was enrolled today)."""
    setup = ensure_setup(school)
    sid = student_id or student(school)
    call(
        school,
        "accountant",
        fees.apply_plan,
        setup.plan_id,
        PlanApplyIn(section_ids=[school.ids["section_9a"]], include_past_instalments=True),
    )
    with tenant_session(school.tenant_id) as s:
        value: object = s.execute(
            text("SELECT id FROM fin.student_fee_accounts WHERE student_id = :s"), {"s": sid}
        ).scalar_one()
    return uuid.UUID(str(value))


def issue(
    school: Any,
    account_id: uuid.UUID,
    amount: str | Decimal = "1000.00",
    *,
    role: str = "office_staff",
    mode: str = "cash",
    **extra: Any,
) -> Any:
    install()
    return call(
        school,
        role,
        fees.issue_receipt,
        ReceiptCreate(account_id=account_id, amount=Decimal(str(amount)), mode=mode, **extra),
    )


def utr() -> str:
    """A 12-digit synthetic UPI reference (random)."""
    return f"{uuid.uuid4().int % 10**12:012d}"


def scalar(admin: Engine, sql: str, **params: Any) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar()
