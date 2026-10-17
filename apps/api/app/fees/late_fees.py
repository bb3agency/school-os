"""Late fees (US-2104; FR-FEE-009; ADR-0042 P5, D18; internal).

The nightly job (00:30 IST) posts, per open account whose plan has a rule, for each instalment
whose fee lines (not the late fee itself, so never compounding) are still due after
``due_on + grace_days``:

- ``flat_per_instalment``: one ``late_fee`` entry keyed ``flat:<instalment>``;
- ``per_day``: one entry per overdue day keyed ``day:<instalment>:<YYYYMMDD>``, until the late
  fees of the instalment reach ``cap`` (the last one is trimmed to the cap).

Keys are unique per account (index ``ledger_entries_one_late_fee``), so running the job again,
or two workers at once, posts nothing twice. A new plan version does not post the same late fee
again (the key has no plan version). Waivers are adjustments (FR-FEE-007).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.fees import _common as c
from app.fees import ledger
from app.fees import repository as repo
from app.fees.money import ZERO


def _posts_for(
    dues: ledger.Dues,
    rule: dict[str, Any],
    today: dt.date,
    keys: set[str],
    max_days: int,
) -> list[tuple[int, str, Decimal, dt.date]]:
    kind = rule.get("kind")
    if kind not in ("flat_per_instalment", "per_day"):
        return []
    amount = Decimal(str(rule["amount"]))
    grace = int(rule.get("grace_days", 0))
    principal: dict[int, Decimal] = {}
    late: dict[int, Decimal] = {}
    due_on: dict[int, dt.date] = {}
    for bucket, line in dues.lines:
        if line.due_on is not None:
            due_on[bucket.instalment_no] = line.due_on
        if bucket.head_id is None:
            late[bucket.instalment_no] = late.get(bucket.instalment_no, ZERO) + bucket.late_fee
        elif line.collectable:
            principal[bucket.instalment_no] = principal.get(bucket.instalment_no, ZERO) + bucket.due
    out: list[tuple[int, str, Decimal, dt.date]] = []
    for inst_no, owed in sorted(principal.items()):
        start = due_on.get(inst_no)
        if owed <= 0 or start is None:
            continue
        first = start + dt.timedelta(days=grace + 1)
        if first > today:
            continue
        if kind == "flat_per_instalment":
            key = f"flat:{inst_no}"
            if key not in keys:
                out.append((inst_no, key, amount, today))
            continue
        cap = Decimal(str(rule["cap"]))
        posted = late.get(inst_no, ZERO)
        day = max(first, today - dt.timedelta(days=max_days - 1))
        while day <= today and posted < cap:
            key = f"day:{inst_no}:{day:%Y%m%d}"
            if key not in keys:
                part = min(amount, cap - posted)
                out.append((inst_no, key, part, day))
                posted += part
            day += dt.timedelta(days=1)
    return out


def post_for_school(session: Session, *, today: dt.date | None = None) -> dict[str, int]:
    """Post the late fees of every open account of the current school (system actor). Returns
    counts; audits ``fee.late_fees.posted`` (counts only) when anything was posted."""
    day = today or c.today(session)
    plans = {p.id: p for p in repo.list_plans(session) if p.late_fee_rule.get("kind") != "none"}
    accounts = [
        a for pid in plans for a in repo.accounts_on_plan(session, pid) if a.status == "open"
    ]
    dues = ledger.dues_of(session, accounts, day)
    max_days = c.settings().late_fees.max_catch_up_days
    rows: list[dict[str, Any]] = []
    touched: set[uuid.UUID] = set()
    for account in accounts:
        keys = repo.late_fee_keys(session, account.id)
        for inst_no, key, amount, entry_date in _posts_for(
            dues[account.id], plans[account.plan_id].late_fee_rule, day, keys, max_days
        ):
            touched.add(account.id)
            rows.append(
                {
                    "id": new_id(),
                    "tenant_id": account.tenant_id,
                    "account_id": account.id,
                    "entry_kind": "late_fee",
                    "amount": amount,
                    "instalment_no": inst_no,
                    "head_id": None,
                    "entry_date": entry_date,
                    "late_fee_key": key,
                    "posted_by": None,
                }
            )
    posted = repo.insert_entries(session, rows)
    if posted:
        c.record(
            session,
            action="fee.late_fees.posted",
            resource_type="fee_ledger",
            resource_id=None,
            summary={"entries": posted, "accounts": len(touched), "date": day.isoformat()},
            system=True,
        )
    return {"accounts": len(accounts), "posted": posted}


__all__ = ["post_for_school"]
