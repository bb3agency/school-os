"""Plans, subscriptions, billing accounts, GST invoices and payments (FR-PLT-010..019).

Rules (docs/16 §5.6-5.9, §9, §10):
- Plans are versioned; a published plan never changes (DB trigger); a price change is a new
  version.
- Plan changes take effect at the next period (no proration in M0); a cancellation takes effect
  at period end (a trial is cancelled at once).
- Past due is set by a daily sweep; suspension is NEVER automatic: an operator with step-up,
  after the 15-day grace, outside protected exam windows unless a platform_owner overrides.
- Invoices: drafts are generated per subscription and period (idempotent); issuing assigns the
  next gapless number of the Indian financial year under a row lock; issued invoices are frozen.
- GST 18% by default: CGST + SGST when the place of supply equals the supplier's state (AP = 37),
  IGST otherwise; half-up rounding to paise.
- Commercial catalogue (ADR-0038): a plan's one-time "Implementation and data verification" fee
  is charged once, on the subscription's first invoice (the next new invoice if that one is
  voided). An AI answer bundle is a monthly add-on billed in advance with the plan; answers above
  its quota in a calendar month are billed on the next invoice (``usage_month`` stops a second
  charge). Answers are counts from ``platform.usage_daily``, never tenant data.
"""

from __future__ import annotations

import calendar
import datetime as dt
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import RowMapping, and_, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import platform_session
from app.core.errors import Conflict, NotFound, PreconditionFailed, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.platform import models as m
from app.platform import repository as repo
from app.platform import tenant_audit
from app.platform.common import (
    SYSTEM,
    Actor,
    add_months,
    audit_platform,
    billing_cfg,
    clamp_limit,
    db_errors,
    must,
    now,
    parse_cursor,
    today_ist,
)
from app.platform.payments import get_provider
from app.platform.schemas import (
    AiBundleOut,
    BillingAccountIn,
    BillingAccountOut,
    InvoiceLineIn,
    InvoiceOut,
    JobOut,
    PaymentIn,
    PaymentOut,
    PlanIn,
    PlanOut,
    PlanPatch,
    SubscriptionOut,
)
from app.tenancy import service as tenancy

log = get_logger(__name__)
PAISE = Decimal("0.01")


# --- money and GST (pure) ---------------------------------------------------------------------


def round_paise(value: Decimal) -> Decimal:
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class TaxTotals:
    tax_type: str
    taxable_value_inr: Decimal
    cgst_inr: Decimal
    sgst_inr: Decimal
    igst_inr: Decimal
    total_inr: Decimal


def compute_tax(
    lines: Iterable[tuple[Decimal, Decimal]], *, supplier_state: str, place_of_supply: str
) -> TaxTotals:
    """``lines`` = (amount_inr, gst_rate). Tax per rate group on the group's taxable value.

    Intra-state (place of supply == supplier state): CGST = SGST = rate/2 each; otherwise IGST.
    """
    by_rate: dict[Decimal, Decimal] = {}
    for amount, rate in lines:
        by_rate[Decimal(rate)] = by_rate.get(Decimal(rate), Decimal("0")) + round_paise(amount)
    taxable = sum(by_rate.values(), Decimal("0"))
    if taxable < 0:
        raise ValidationFailed(
            [{"field": "lines", "code": "negative_total", "message_key": "errors.negative_total"}]
        )
    intra = place_of_supply == supplier_state
    cgst = sgst = igst = Decimal("0.00")
    for rate, base in by_rate.items():
        if intra:
            half = round_paise(base * rate / Decimal(200))
            cgst += half
            sgst += half
        else:
            igst += round_paise(base * rate / Decimal(100))
    return TaxTotals(
        tax_type="cgst_sgst" if intra else "igst",
        taxable_value_inr=round_paise(taxable),
        cgst_inr=round_paise(cgst),
        sgst_inr=round_paise(sgst),
        igst_inr=round_paise(igst),
        total_inr=round_paise(taxable + cgst + sgst + igst),
    )


def financial_year(day: dt.date) -> tuple[str, str]:
    """Indian financial year (1 April - 31 March) as ('2026-27', '26-27')."""
    start = day.year if day.month >= 4 else day.year - 1
    end = (start + 1) % 100
    return f"{start}-{end:02d}", f"{start % 100:02d}-{end:02d}"


def format_invoice_number(prefix: str, fy_short: str, seq: int) -> str:
    number = str(billing_cfg()["invoice_number_format"]).format(
        prefix=prefix, fy_short=fy_short, seq=seq
    )
    if len(number) > 16:  # CGST Rule 46
        raise Conflict("Invoice number would exceed 16 characters.", code="invoice_number_length")
    return number


def _period_months(plan: Mapping[Any, Any]) -> int:
    return 12 if plan["billing_period"] == "annual" else 1


# --- one-time fee, AI bundles and overage (pure) ----------------------------------------------

ONE_TIME_FEE_TEXT = "Implementation and data verification (one-time)"


def next_month(day: dt.date) -> dt.date:
    """The first day of the calendar month after ``day``."""
    return add_months(day.replace(day=1), 1)


def ai_overage(answers: int, included: int, rate: Decimal) -> tuple[int, Decimal]:
    """(extra answers, amount) for one calendar month: answers above the bundle's quota."""
    extra = max(int(answers) - int(included), 0)
    return extra, round_paise(Decimal(extra) * Decimal(rate))


def _rupees(amount: Decimal) -> str:
    return f"₹{Decimal(amount):,.2f}"


def overage_description(bundle_name: str, month: dt.date, extra: int, rate: Decimal) -> str:
    return (
        f"AI answers above the {bundle_name} bundle, {calendar.month_name[month.month]} "
        f"{month.year}: {extra:,} extra answers \N{MULTIPLICATION SIGN} {_rupees(rate)}"
    )


def bundle_description(bundle: Mapping[Any, Any], start: dt.date, last_day: dt.date) -> str:
    return (
        f"AI answers: {bundle['name']} bundle, {int(bundle['included_answers']):,} answers a "
        f"month ({start.isoformat()} to {last_day.isoformat()})"
    )


# --- plans ------------------------------------------------------------------------------------


def _plan_values(data: PlanIn | PlanPatch) -> dict[str, Any]:
    values = data.model_dump(exclude_unset=isinstance(data, PlanPatch), mode="python")
    if "limits" in values and values["limits"] is not None:
        values["limits"] = {
            k: (str(v) if isinstance(v, Decimal) else v)
            for k, v in values["limits"].items()
            if v is not None
        }
    if "gst_rate" in values:
        values["gst_rate"] = Decimal(values["gst_rate"])
    if values.get("one_time_fee_inr", 0) is None:  # omitted or null: the default 0, or keep
        del values["one_time_fee_inr"]
    return values


def create_plan(actor: Actor, data: PlanIn) -> PlanOut:
    values = _plan_values(data)
    values.setdefault("sac_code", None)
    if values["sac_code"] is None:
        values["sac_code"] = str(billing_cfg()["default_sac_code"])
    with platform_session() as s, db_errors():
        s.execute(select(func.pg_advisory_xact_lock(func.hashtext("platform.plan:" + data.code))))
        version = repo.next_plan_version(s, data.code)
        row = repo.insert_row(
            s,
            m.plans,
            {
                **values,
                "id": new_id(),
                "version": version,
                "status": "draft",
                "created_by": actor.operator_id,
            },
        )
        audit_platform(
            s, actor, "plan.created", "plan", row["id"], {"code": data.code, "version": version}
        )
        return PlanOut.model_validate(dict(row))


def list_plans(status: str | None = None) -> list[PlanOut]:
    with platform_session() as s:
        stmt = select(m.plans).order_by(m.plans.c.code, m.plans.c.version.desc())
        if status:
            stmt = stmt.where(m.plans.c.status == status)
        return [PlanOut.model_validate(dict(r)) for r in s.execute(stmt).mappings()]


def get_plan(plan_id: uuid.UUID) -> PlanOut:
    with platform_session() as s:
        row = repo.get(s, m.plans, plan_id)
    if row is None:
        raise NotFound("Plan not found")
    return PlanOut.model_validate(dict(row))


def update_plan(actor: Actor, plan_id: uuid.UUID, data: PlanPatch) -> PlanOut:
    with platform_session() as s, db_errors():
        row = repo.get(s, m.plans, plan_id, for_update=True)
        if row is None:
            raise NotFound("Plan not found")
        if row["status"] != "draft":
            raise Conflict(
                "Published plans cannot change; create a new version.", code="plan_published"
            )
        values = _plan_values(data)
        merged = {**dict(row), **values}
        if (merged["pricing_model"] == "per_student") != (
            merged["per_student_price_inr"] is not None
        ):
            raise ValidationFailed(
                [
                    {
                        "field": "per_student_price_inr",
                        "code": "invalid",
                        "message_key": "errors.invalid",
                    }
                ]
            )
        row = repo.update_row(s, m.plans, plan_id, values, bump_version=False)
        audit_platform(s, actor, "plan.updated", "plan", plan_id, {"fields": sorted(values)})
        return PlanOut.model_validate(dict(row))


def set_plan_status(actor: Actor, plan_id: uuid.UUID, status: str) -> PlanOut:
    allowed = {"published": "draft", "retired": "published"}
    with platform_session() as s, db_errors():
        row = repo.get(s, m.plans, plan_id, for_update=True)
        if row is None:
            raise NotFound("Plan not found")
        if row["status"] != allowed[status]:
            raise Conflict(f"Only a {allowed[status]} plan can be {status}.", code="invalid_state")
        values: dict[str, Any] = {"status": status}
        if status == "published":
            values["published_at"] = now()
        row = repo.update_row(s, m.plans, plan_id, values, bump_version=False)
        audit_platform(
            s,
            actor,
            f"plan.{status}",
            "plan",
            plan_id,
            {"code": row["code"], "version": row["version"]},
        )
        return PlanOut.model_validate(dict(row))


# --- billing accounts -------------------------------------------------------------------------


def insert_billing_account(s: Session, tenant_id: uuid.UUID, data: BillingAccountIn) -> RowMapping:
    return repo.insert_row(
        s, m.billing_accounts, {**data.model_dump(), "id": new_id(), "tenant_id": tenant_id}
    )


def get_billing_account(tenant_id: uuid.UUID) -> BillingAccountOut:
    with platform_session() as s:
        row = repo.get_by(s, m.billing_accounts, m.billing_accounts.c.tenant_id == tenant_id)
    if row is None:
        raise NotFound("Billing account not found")
    return BillingAccountOut.model_validate(dict(row))


def put_billing_account(
    actor: Actor, tenant_id: uuid.UUID, data: BillingAccountIn, *, expected_version: int | None
) -> BillingAccountOut:
    """Changes apply to future invoices only (issued invoices keep their snapshot)."""
    with platform_session() as s, db_errors():
        row = repo.get_by(
            s, m.billing_accounts, m.billing_accounts.c.tenant_id == tenant_id, for_update=True
        )
        if row is None:
            raise NotFound("Billing account not found")
        if expected_version is not None and row["version"] != expected_version:
            raise PreconditionFailed()
        values = data.model_dump()
        changed = sorted(k for k, v in values.items() if row[k] != v)
        row = repo.update_row(s, m.billing_accounts, row["id"], values)
        audit_platform(
            s,
            actor,
            "billing_account.updated",
            "billing_account",
            row["id"],
            {"fields": changed},
            tenant_id=tenant_id,
        )
        return BillingAccountOut.model_validate(dict(row))


# --- subscriptions ----------------------------------------------------------------------------


def published_plan(s: Session, plan_id: uuid.UUID) -> RowMapping:
    plan = repo.get(s, m.plans, plan_id)
    if plan is None or plan["status"] != "published":
        raise ValidationFailed(
            [{"field": "plan_id", "code": "plan_not_available", "message_key": "errors.plan"}]
        )
    return plan


def insert_subscription(
    s: Session,
    *,
    tenant_id: uuid.UUID,
    account_id: uuid.UUID,
    plan: RowMapping,
    start_as: str,
    price_override: Decimal | None,
    override_reason: str | None,
    today: dt.date,
) -> RowMapping:
    trial = start_as == "trial"
    trial_days = int(plan["trial_days"])
    end = (
        today + dt.timedelta(days=max(trial_days, 1))
        if trial
        else add_months(today, _period_months(plan))
    )
    return repo.insert_row(
        s,
        m.subscriptions,
        {
            "id": new_id(),
            "tenant_id": tenant_id,
            "billing_account_id": account_id,
            "plan_id": plan["id"],
            "status": "trial" if trial else "active",
            "trial_ends_at": (dt.datetime.combine(end, dt.time(), dt.UTC) if trial else None),
            "current_period_start": today,
            "current_period_end": end,
            "price_override_inr": price_override,
            "override_reason": override_reason,
        },
    )


def _sub_or_404(s: Session, sub_id: uuid.UUID) -> RowMapping:
    row = repo.get(s, m.subscriptions, sub_id, for_update=True)
    if row is None:
        raise NotFound("Subscription not found")
    return row


def list_subscriptions(
    status: str | None = None, limit: int = 50, cursor: str | None = None
) -> tuple[list[SubscriptionOut], str | None]:
    limit = clamp_limit(limit)
    conds = [m.subscriptions.c.status == status] if status else []
    with platform_session() as s:
        rows = repo.list_rows(s, m.subscriptions, *conds, limit=limit, cursor=parse_cursor(cursor))
    items = [SubscriptionOut.model_validate(dict(r)) for r in rows[:limit]]
    return items, (str(rows[limit - 1]["id"]) if len(rows) > limit else None)


def get_subscription(sub_id: uuid.UUID) -> SubscriptionOut:
    with platform_session() as s:
        row = repo.get(s, m.subscriptions, sub_id)
    if row is None:
        raise NotFound("Subscription not found")
    return SubscriptionOut.model_validate(dict(row))


def activate_subscription(
    actor: Actor, sub_id: uuid.UUID, *, today: dt.date | None = None
) -> SubscriptionOut:
    today = today or today_ist()
    with platform_session() as s, db_errors():
        sub = _sub_or_404(s, sub_id)
        if sub["status"] != "trial":
            raise Conflict("Only a trial can be activated.", code="invalid_state")
        plan = repo.get(s, m.plans, sub["pending_plan_id"] or sub["plan_id"])
        plan = must(plan)
        values: dict[str, Any] = {
            "status": "active",
            "plan_id": plan["id"],
            "pending_plan_id": None,
            "current_period_start": today,
            "current_period_end": add_months(today, _period_months(plan)),
        }
        if sub["ai_bundle_id"] is not None:
            # Trial answers are free: the bundle counts from the month after activation.
            values["ai_bundle_from"] = max(sub["ai_bundle_from"], next_month(today))
        sub = repo.update_row(s, m.subscriptions, sub_id, values)
        audit_platform(
            s,
            actor,
            "subscription.activated",
            "subscription",
            sub_id,
            {},
            tenant_id=sub["tenant_id"],
        )
        # Billing in advance: the first period's draft invoice.
        create_draft(s, SYSTEM, sub, today)
        return SubscriptionOut.model_validate(dict(sub))


def extend_trial(actor: Actor, sub_id: uuid.UUID, trial_ends_at: dt.datetime) -> SubscriptionOut:
    with platform_session() as s, db_errors():
        sub = _sub_or_404(s, sub_id)
        if sub["status"] != "trial":
            raise Conflict("Only a trial can be extended.", code="invalid_state")
        if trial_ends_at <= sub["trial_ends_at"]:
            raise ValidationFailed(
                [{"field": "trial_ends_at", "code": "not_later", "message_key": "errors.not_later"}]
            )
        end_day = trial_ends_at.astimezone(dt.UTC).date()
        sub = repo.update_row(
            s,
            m.subscriptions,
            sub_id,
            {
                "trial_ends_at": trial_ends_at,
                "current_period_end": max(
                    end_day, sub["current_period_start"] + dt.timedelta(days=1)
                ),
            },
        )
        audit_platform(
            s,
            actor,
            "subscription.trial_extended",
            "subscription",
            sub_id,
            {"trial_ends_on": end_day.isoformat()},
            tenant_id=sub["tenant_id"],
        )
        return SubscriptionOut.model_validate(dict(sub))


def change_plan(actor: Actor, sub_id: uuid.UUID, plan_id: uuid.UUID) -> SubscriptionOut:
    with platform_session() as s, db_errors():
        sub = _sub_or_404(s, sub_id)
        if sub["status"] == "cancelled":
            raise Conflict("The subscription is cancelled.", code="invalid_state")
        new_plan = published_plan(s, plan_id)
        current = repo.get(s, m.plans, sub["plan_id"])
        current = must(current)
        if new_plan["tier"] != current["tier"]:
            raise Conflict("Moving between tiers needs a migration project.", code="tier_change")
        if sub["ai_bundle_id"] is not None:
            _monthly_only([new_plan])
        if sub["status"] == "trial":
            values: dict[str, Any] = {"plan_id": plan_id, "pending_plan_id": None}
        else:
            values = {"pending_plan_id": None if plan_id == sub["plan_id"] else plan_id}
        sub = repo.update_row(s, m.subscriptions, sub_id, values)
        audit_platform(
            s,
            actor,
            "subscription.plan_changed",
            "subscription",
            sub_id,
            {"plan_id": str(plan_id), "effective": "now" if "plan_id" in values else "next_period"},
            tenant_id=sub["tenant_id"],
        )
        return SubscriptionOut.model_validate(dict(sub))


def set_price_override(
    actor: Actor, sub_id: uuid.UUID, amount: Decimal | None, reason: str | None
) -> SubscriptionOut:
    with platform_session() as s, db_errors():
        sub = _sub_or_404(s, sub_id)
        sub = repo.update_row(
            s, m.subscriptions, sub_id, {"price_override_inr": amount, "override_reason": reason}
        )
        audit_platform(
            s,
            actor,
            "subscription.price_override_set",
            "subscription",
            sub_id,
            {"price_override_inr": str(amount) if amount is not None else None},
            tenant_id=sub["tenant_id"],
        )
        return SubscriptionOut.model_validate(dict(sub))


def list_ai_bundles(status: str | None = None) -> list[AiBundleOut]:
    with platform_session() as s:
        stmt = select(m.ai_bundles).order_by(
            m.ai_bundles.c.included_answers, m.ai_bundles.c.version.desc()
        )
        if status:
            stmt = stmt.where(m.ai_bundles.c.status == status)
        return [AiBundleOut.model_validate(dict(r)) for r in s.execute(stmt).mappings()]


def _monthly_only(plans: Iterable[Mapping[Any, Any] | None]) -> None:
    if any(p is not None and p["billing_period"] != "monthly" for p in plans):
        raise Conflict(
            "AI answer bundles are monthly; they need a monthly plan.",
            code="ai_bundle_needs_monthly_plan",
        )


def set_ai_bundle(
    actor: Actor,
    sub_id: uuid.UUID,
    bundle_id: uuid.UUID | None,
    *,
    today: dt.date | None = None,
) -> SubscriptionOut:
    """Choose, change or remove the AI answer bundle (docs/16 §5.7).

    A new bundle counts from the first full calendar month after today (a trial's from the month
    after activation). A change keeps that month and applies to the next invoice and to the quota
    of any month not yet billed; nothing already invoiced is prorated.
    """
    today = today or today_ist()
    with platform_session() as s, db_errors():
        sub = _sub_or_404(s, sub_id)
        if sub["status"] == "cancelled":
            raise Conflict("The subscription is cancelled.", code="invalid_state")
        if bundle_id is None:
            values: dict[str, Any] = {"ai_bundle_id": None, "ai_bundle_from": None}
            action, summary = "subscription.ai_bundle_removed", {}
        else:
            bundle = repo.get(s, m.ai_bundles, bundle_id)
            if bundle is None or bundle["status"] != "published":
                raise ValidationFailed(
                    [
                        {
                            "field": "ai_bundle_id",
                            "code": "ai_bundle_not_available",
                            "message_key": "errors.ai_bundle",
                        }
                    ]
                )
            plan_ids = (sub["plan_id"], sub["pending_plan_id"])
            _monthly_only(repo.get(s, m.plans, p) for p in plan_ids if p is not None)
            values = {
                "ai_bundle_id": bundle_id,
                "ai_bundle_from": sub["ai_bundle_from"] or next_month(today),
            }
            action = "subscription.ai_bundle_set"
            summary = {"ai_bundle_code": bundle["code"], "ai_bundle_version": bundle["version"]}
        sub = repo.update_row(s, m.subscriptions, sub_id, values)
        audit_platform(
            s, actor, action, "subscription", sub_id, summary, tenant_id=sub["tenant_id"]
        )
        out = SubscriptionOut.model_validate(dict(sub))
    _sync_ai_allowance_now(out.tenant_id)
    return out


# --- the school's AI budget follows its bundle (owner decision 2026-10-03; ADR-0020 B3) -------


def ai_allowance_for(s: Session, tenant_id: uuid.UUID) -> int | None:
    """Included answers a month of the school's live subscription's bundle (platform data
    only), or ``None`` without a bundle or a live subscription."""
    stmt = (
        select(m.ai_bundles.c.included_answers)
        .select_from(
            m.subscriptions.join(m.ai_bundles, m.ai_bundles.c.id == m.subscriptions.c.ai_bundle_id)
        )
        .where(m.subscriptions.c.tenant_id == tenant_id, m.subscriptions.c.status != "cancelled")
        .order_by(m.subscriptions.c.created_at.desc())
        .limit(1)
    )
    value = s.execute(stmt).scalar_one_or_none()
    return None if value is None else int(value)


def sync_ai_allowance(tenant_id: uuid.UUID) -> bool:
    """Hand the bundle's included answers to a shared-tier school (``True`` if it changed).

    The school derives its monthly AI budget from this number (``knowledge.policy``). The only
    tenant-side step is the lifecycle-style ``tenancy.set_ai_answer_allowance`` (ADR-0020
    amendment B3): one count in, a flag out, in the school's own session. Idempotent; called
    after a bundle change and by the daily collector, which repairs a missed or failed write.
    Dedicated-tier schools live on their own host and are not reached from here (docs/16 §19
    Q18).
    """
    with platform_session() as s:
        dep = repo.get_by(s, m.deployments, m.deployments.c.tenant_id == tenant_id)
        if dep is None or dep["mode"] != "shared":
            return False
        answers = ai_allowance_for(s, tenant_id)
    return tenancy.set_ai_answer_allowance(tenant_id, answers)


def _sync_ai_allowance_now(tenant_id: uuid.UUID) -> None:
    """Best effort after the platform change has committed: a failure is logged (ids only) and
    the daily collector catches up; the operator's action stands."""
    try:
        sync_ai_allowance(tenant_id)
    except Exception as exc:
        log.warning(
            "platform.ai_allowance.sync_failed",
            tenant_id=str(tenant_id),
            error_type=type(exc).__name__,
        )


def cancel_subscription(actor: Actor, sub_id: uuid.UUID, reason: str) -> SubscriptionOut:
    with platform_session() as s, db_errors():
        sub = _sub_or_404(s, sub_id)
        if sub["status"] == "cancelled":
            raise Conflict("Already cancelled.", code="invalid_state")
        if sub["status"] == "trial":
            values: dict[str, Any] = {
                "status": "cancelled",
                "cancelled_at": now(),
                "cancel_reason": reason,
            }
        else:
            values = {"cancel_at_period_end": True, "cancel_reason": reason}
        sub = repo.update_row(s, m.subscriptions, sub_id, values)
        audit_platform(
            s,
            actor,
            "subscription.cancelled",
            "subscription",
            sub_id,
            {"at_period_end": sub["status"] != "cancelled"},
            tenant_id=sub["tenant_id"],
        )
        return SubscriptionOut.model_validate(dict(sub))


def in_protected_window(boards: Sequence[str], day: dt.date) -> str | None:
    for window in billing_cfg().get("protected_windows") or []:
        start = dt.date.fromisoformat(str(window["start"]))
        end = dt.date.fromisoformat(str(window["end"]))
        applies = not window.get("boards") or set(window["boards"]) & set(boards)
        if start <= day <= end and applies:
            return str(window["name"])
    return None


def suspend_subscription(
    actor: Actor,
    sub_id: uuid.UUID,
    reason: str,
    *,
    actor_is_owner: bool,
    exam_window_override: bool,
    today: dt.date | None = None,
) -> SubscriptionOut:
    """Billing suspension: only from past_due after grace; never automatic (docs/16 §9)."""
    today = today or today_ist()
    with platform_session() as ps:
        sub0 = repo.get(ps, m.subscriptions, sub_id)
        dep = (
            repo.get_by(ps, m.deployments, m.deployments.c.tenant_id == sub0["tenant_id"])
            if sub0
            else None
        )
    if sub0 is None or dep is None:
        raise NotFound("Subscription not found")
    shared_active = dep["mode"] == "shared" and dep["tenant_status"] == "active"
    with platform_session() as s, db_errors():
        sub = _sub_or_404(s, sub_id)
        if sub["status"] != "past_due":
            raise Conflict("Only a past-due subscription can be suspended.", code="invalid_state")
        if sub["grace_ends_on"] is None or today < sub["grace_ends_on"]:
            raise Conflict("The 15-day grace period has not ended.", code="grace_not_over")
        window = in_protected_window(dep["boards"], today)
        override_by = None
        if window is not None:
            if not (exam_window_override and actor_is_owner):
                raise Conflict(
                    "Today is inside a protected exam window; a platform owner must approve.",
                    code="exam_window",
                )
            override_by = actor.operator_id
        sub = repo.update_row(
            s,
            m.subscriptions,
            sub_id,
            {
                "status": "suspended",
                "suspended_at": now(),
                "suspended_by": actor.operator_id,
                "suspension_reason": reason,
                "exam_window_override_by": override_by,
            },
        )
        if shared_active:
            tenancy.suspend_tenant(s, sub["tenant_id"])
            repo.update_row(
                s,
                m.deployments,
                dep["id"],
                {"tenant_status": "suspended", "tenant_status_reason": "billing"},
            )
            tenant_audit.enqueue(
                s, sub["tenant_id"], actor, "tenant.suspended", {"cause": "billing"}
            )
        audit_platform(
            s,
            actor,
            "subscription.suspended",
            "subscription",
            sub_id,
            {"exam_window_override": override_by is not None},
            tenant_id=sub["tenant_id"],
        )
        out = SubscriptionOut.model_validate(dict(sub))
    if shared_active:
        tenant_audit.deliver_now(out.tenant_id)
    return out


def reactivate_subscription(
    actor: Actor, sub_id: uuid.UUID, *, today: dt.date | None = None
) -> SubscriptionOut:
    today = today or today_ist()
    with platform_session() as ps:
        sub0 = repo.get(ps, m.subscriptions, sub_id)
        dep = (
            repo.get_by(ps, m.deployments, m.deployments.c.tenant_id == sub0["tenant_id"])
            if sub0
            else None
        )
    if sub0 is None or dep is None:
        raise NotFound("Subscription not found")
    resume_tenant = (
        dep["mode"] == "shared"
        and dep["tenant_status"] == "suspended"
        and dep["tenant_status_reason"] == "billing"
    )
    with platform_session() as s, db_errors():
        sub = _sub_or_404(s, sub_id)
        if sub["status"] != "suspended":
            raise Conflict(
                "Only a suspended subscription can be reactivated.", code="invalid_state"
            )
        if repo.overdue_invoices(s, sub_id, today):
            raise Conflict("Overdue invoices must be paid first.", code="overdue_invoices")
        sub = repo.update_row(
            s,
            m.subscriptions,
            sub_id,
            {
                "status": "active",
                "suspended_at": None,
                "suspended_by": None,
                "suspension_reason": None,
                "exam_window_override_by": None,
                "past_due_since": None,
                "grace_ends_on": None,
            },
        )
        if resume_tenant:
            tenancy.reactivate_tenant(s, sub["tenant_id"])
            repo.update_row(
                s,
                m.deployments,
                dep["id"],
                {"tenant_status": "active", "tenant_status_reason": None},
            )
            tenant_audit.enqueue(
                s, sub["tenant_id"], actor, "tenant.reactivated", {"cause": "billing"}
            )
        audit_platform(
            s,
            actor,
            "subscription.reactivated",
            "subscription",
            sub_id,
            {},
            tenant_id=sub["tenant_id"],
        )
        out = SubscriptionOut.model_validate(dict(sub))
    if resume_tenant:
        tenant_audit.deliver_now(out.tenant_id)
    return out


def mark_past_due(*, today: dt.date | None = None) -> int:
    """Daily sweep: active subscriptions with an issued invoice unpaid after its due date become
    past_due (grace 15 days). It never suspends anything."""
    today = today or today_ist()
    grace = int(billing_cfg()["grace_days"])
    count = 0
    with platform_session() as s:
        for sub in repo.subscriptions_with_overdue(s, today):
            repo.update_row(
                s,
                m.subscriptions,
                sub["id"],
                {
                    "status": "past_due",
                    "past_due_since": today,
                    "grace_ends_on": today + dt.timedelta(days=grace),
                },
            )
            audit_platform(
                s,
                SYSTEM,
                "subscription.past_due",
                "subscription",
                sub["id"],
                {"grace_ends_on": (today + dt.timedelta(days=grace)).isoformat()},
                tenant_id=sub["tenant_id"],
            )
            count += 1
    log.info("billing.past_due.sweep", count=count, outcome="ok")
    return count


def roll_periods(*, today: dt.date | None = None) -> int:
    """Advance ended periods: apply pending plan changes and period-end cancellations."""
    today = today or today_ist()
    count = 0
    with platform_session() as s:
        for sub in repo.subscriptions_to_roll(s, today):
            if sub["cancel_at_period_end"]:
                repo.update_row(
                    s, m.subscriptions, sub["id"], {"status": "cancelled", "cancelled_at": now()}
                )
                audit_platform(
                    s,
                    SYSTEM,
                    "subscription.cancelled",
                    "subscription",
                    sub["id"],
                    {"at_period_end": True},
                    tenant_id=sub["tenant_id"],
                )
            else:
                plan = repo.get(s, m.plans, sub["pending_plan_id"] or sub["plan_id"])
                plan = must(plan)
                start = sub["current_period_end"]
                end = add_months(start, _period_months(plan))
                while end <= today:  # catch up if the job did not run for a while
                    start, end = end, add_months(end, _period_months(plan))
                repo.update_row(
                    s,
                    m.subscriptions,
                    sub["id"],
                    {
                        "plan_id": plan["id"],
                        "pending_plan_id": None,
                        "current_period_start": start,
                        "current_period_end": end,
                    },
                )
            count += 1
    return count


# --- invoices ---------------------------------------------------------------------------------


def _recipient(account: Mapping[Any, Any]) -> dict[str, Any]:
    return {
        k: account[k]
        for k in ("address_line1", "address_line2", "city", "district", "postal_code", "state_code")
    }


def _line_rows(lines: Sequence[InvoiceLineIn], plan: Mapping[Any, Any]) -> list[dict[str, Any]]:
    rows = []
    for i, line in enumerate(lines, start=1):
        amount = round_paise(line.quantity * line.unit_price_inr)
        if line.kind == "discount" and amount > 0:
            amount = -amount
        rows.append(
            {
                "id": new_id(),
                "line_no": i,
                "kind": line.kind,
                "description": line.description,
                "sac_code": plan["sac_code"],
                "quantity": line.quantity,
                "unit_price_inr": line.unit_price_inr,
                "amount_inr": amount,
                "gst_rate": plan["gst_rate"],
                "usage_month": line.usage_month,
            }
        )
    return rows


def _default_lines(
    s: Session,
    sub: Mapping[Any, Any],
    plan: Mapping[Any, Any],
    period_start: dt.date,
    period_end: dt.date,
) -> list[InvoiceLineIn]:
    price = (
        sub["price_override_inr"]
        if sub["price_override_inr"] is not None
        else plan["base_price_inr"]
    )
    last_day = period_end - dt.timedelta(days=1)
    lines = [
        InvoiceLineIn(
            kind="subscription",
            description=(
                f"SchoolOS {plan['name']} ({period_start.isoformat()} to {last_day.isoformat()})"
            ),
            quantity=Decimal("1"),
            unit_price_inr=Decimal(price),
        )
    ]
    fee = Decimal(plan["one_time_fee_inr"] or 0)
    if fee > 0 and not repo.subscription_has_line(s, sub["id"], kind="one_time_fee"):
        lines.append(
            InvoiceLineIn(kind="one_time_fee", description=ONE_TIME_FEE_TEXT, unit_price_inr=fee)
        )
    if plan["pricing_model"] == "per_student":
        usage = repo.usage_on_or_before(s, sub["tenant_id"], period_start - dt.timedelta(days=1))
        students = int(usage["students_active"]) if usage else 0
        included = int(plan["included_students"] or 0)
        extra = max(students, included) - included
        if extra > 0:
            lines.append(
                InvoiceLineIn(
                    kind="per_student",
                    description="Students above the included count",
                    quantity=Decimal(extra),
                    unit_price_inr=Decimal(plan["per_student_price_inr"]),
                )
            )
    if sub["ai_bundle_id"] is not None:
        lines.extend(_ai_lines(s, sub, period_start, last_day))
    return lines


def _ai_lines(
    s: Session, sub: Mapping[Any, Any], period_start: dt.date, last_day: dt.date
) -> list[InvoiceLineIn]:
    """The bundle (in advance, for this period) and last calendar month's overage (in arrears).

    Overage for month M goes on an invoice whose period starts in M + 1, only from the month the
    bundle counts from, and only when no live invoice of the subscription already bills M.
    """
    bundle = must(repo.get(s, m.ai_bundles, sub["ai_bundle_id"]))
    lines = [
        InvoiceLineIn(
            kind="addon",
            description=bundle_description(bundle, period_start, last_day),
            unit_price_inr=Decimal(bundle["price_inr"]),
        )
    ]
    month = add_months(period_start.replace(day=1), -1)
    if month < sub["ai_bundle_from"] or repo.subscription_has_line(
        s, sub["id"], kind="usage_overage", usage_month=month
    ):
        return lines
    answers = repo.ai_answers_between(s, sub["tenant_id"], month, next_month(month))
    rate = Decimal(bundle["overage_rate_inr"])
    extra, _ = ai_overage(answers, int(bundle["included_answers"]), rate)
    if extra > 0:
        lines.append(
            InvoiceLineIn(
                kind="usage_overage",
                description=overage_description(bundle["name"], month, extra, rate),
                quantity=Decimal(extra),
                unit_price_inr=rate,
                usage_month=month,
            )
        )
    return lines


def ai_answers_in_month(tenant_id: uuid.UUID, month: dt.date) -> int:
    """Billable AI answers of one school in one calendar month (counts from usage_daily)."""
    start = month.replace(day=1)
    with platform_session() as s:
        return repo.ai_answers_between(s, tenant_id, start, next_month(start))


def _totals(s: Session, invoice: Mapping[Any, Any]) -> dict[str, Any]:
    lines = repo.invoice_lines(s, invoice["id"])
    tax = compute_tax(
        ((Decimal(r["amount_inr"]), Decimal(r["gst_rate"])) for r in lines),
        supplier_state=invoice["supplier_state_code"],
        place_of_supply=invoice["place_of_supply_state_code"],
    )
    return {
        "tax_type": tax.tax_type,
        "taxable_value_inr": tax.taxable_value_inr,
        "cgst_inr": tax.cgst_inr,
        "sgst_inr": tax.sgst_inr,
        "igst_inr": tax.igst_inr,
        "total_inr": tax.total_inr,
    }


def create_draft(
    s: Session,
    actor: Actor,
    sub: Mapping[Any, Any],
    period_start: dt.date,
    *,
    lines: Sequence[InvoiceLineIn] | None = None,
) -> RowMapping | None:
    """Create a draft for (subscription, period) unless a live invoice exists. Returns the draft."""
    existing = repo.get_by(
        s,
        m.invoices,
        m.invoices.c.subscription_id == sub["id"],
        m.invoices.c.period_start == period_start,
        m.invoices.c.status != "void",
    )
    if existing is not None:
        return None
    account = repo.get(s, m.billing_accounts, sub["billing_account_id"])
    use_pending = sub["pending_plan_id"] is not None and period_start >= sub["current_period_end"]
    plan = repo.get(s, m.plans, sub["pending_plan_id"] if use_pending else sub["plan_id"])
    account = must(account)
    plan = must(plan)
    period_end = add_months(period_start, _period_months(plan))
    settings = get_settings()
    supplier_state = settings.billing_supplier_state_code
    place = account["state_code"]
    invoice = repo.insert_row(
        s,
        m.invoices,
        {
            "id": new_id(),
            "tenant_id": sub["tenant_id"],
            "subscription_id": sub["id"],
            "billing_account_id": account["id"],
            "status": "draft",
            "period_start": period_start,
            "period_end": period_end,
            "supplier_legal_name": settings.billing_supplier_legal_name,
            "supplier_gstin": settings.billing_supplier_gstin,
            "supplier_state_code": supplier_state,
            "recipient_legal_name": account["legal_name"],
            "recipient_gstin": account["gstin"],
            "recipient_address": _recipient(account),
            "place_of_supply_state_code": place,
            "tax_type": "cgst_sgst" if place == supplier_state else "igst",
        },
    )
    rows = _line_rows(lines or _default_lines(s, sub, plan, period_start, period_end), plan)
    repo.replace_invoice_lines(s, invoice["id"], rows)
    invoice = repo.update_row(s, m.invoices, invoice["id"], _totals(s, invoice), bump_version=False)
    audit_platform(
        s,
        actor,
        "invoice.generated" if actor.operator_id is None else "invoice.created",
        "invoice",
        invoice["id"],
        {"subscription_id": str(sub["id"]), "period_start": period_start.isoformat()},
        tenant_id=sub["tenant_id"],
    )
    return invoice


def _invoice_out(s: Session, row: Mapping[Any, Any]) -> InvoiceOut:
    balance = Decimal("0.00")
    if row["status"] == "issued":
        balance = max(
            Decimal(row["total_inr"]) - Decimal(row["amount_paid_inr"]) - Decimal(row["tds_inr"]),
            Decimal("0.00"),
        )
    lines = [dict(r) for r in repo.invoice_lines(s, row["id"])]
    return InvoiceOut.model_validate({**dict(row), "balance_due_inr": balance, "lines": lines})


def list_invoices(
    *,
    status: str | None = None,
    fy: str | None = None,
    tenant_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> tuple[list[InvoiceOut], str | None]:
    limit = clamp_limit(limit)
    conds = []
    if status:
        conds.append(m.invoices.c.status == status)
    if fy:
        conds.append(m.invoices.c.financial_year == fy)
    if tenant_id:
        conds.append(m.invoices.c.tenant_id == tenant_id)
    with platform_session() as s:
        rows = repo.list_rows(s, m.invoices, *conds, limit=limit, cursor=parse_cursor(cursor))
        items = [_invoice_out(s, r) for r in rows[:limit]]
    return items, (str(rows[limit - 1]["id"]) if len(rows) > limit else None)


def get_invoice(invoice_id: uuid.UUID) -> InvoiceOut:
    with platform_session() as s:
        row = repo.get(s, m.invoices, invoice_id)
        if row is None:
            raise NotFound("Invoice not found")
        return _invoice_out(s, row)


def create_manual_draft(actor: Actor, sub_id: uuid.UUID, period_start: dt.date) -> InvoiceOut:
    with platform_session() as s, db_errors():
        sub = _sub_or_404(s, sub_id)
        if sub["status"] in ("trial", "cancelled"):
            raise Conflict(
                "Trials and cancelled subscriptions are not invoiced.", code="invalid_state"
            )
        row = create_draft(s, actor, sub, period_start)
        if row is None:
            raise Conflict("An invoice already exists for this period.", code="duplicate")
        return _invoice_out(s, row)


def _draft_or_409(s: Session, invoice_id: uuid.UUID) -> RowMapping:
    row = repo.get(s, m.invoices, invoice_id, for_update=True)
    if row is None:
        raise NotFound("Invoice not found")
    if row["status"] != "draft":
        raise Conflict("Only draft invoices can change.", code="invoice_issued")
    return row


def update_draft(
    actor: Actor,
    invoice_id: uuid.UUID,
    *,
    lines: Sequence[InvoiceLineIn] | None,
    notes: str | None,
    notes_set: bool,
    expected_version: int | None,
) -> InvoiceOut:
    with platform_session() as s, db_errors():
        row = _draft_or_409(s, invoice_id)
        if expected_version is not None and row["version"] != expected_version:
            raise PreconditionFailed()
        fields = []
        if lines is not None:
            sub = repo.get(s, m.subscriptions, row["subscription_id"])
            plan = repo.get(s, m.plans, sub["plan_id"]) if sub else None
            plan = must(plan)
            repo.replace_invoice_lines(s, invoice_id, _line_rows(lines, plan))
            fields.append("lines")
        values: dict[str, Any] = _totals(s, row)
        if notes_set:
            values["notes"] = notes
            fields.append("notes")
        row = repo.update_row(s, m.invoices, invoice_id, values)
        audit_platform(
            s,
            actor,
            "invoice.updated",
            "invoice",
            invoice_id,
            {"fields": fields},
            tenant_id=row["tenant_id"],
        )
        return _invoice_out(s, row)


def discard_draft(actor: Actor, invoice_id: uuid.UUID) -> None:
    with platform_session() as s, db_errors():
        row = _draft_or_409(s, invoice_id)
        repo.delete_row(s, m.invoices, invoice_id)
        audit_platform(
            s,
            actor,
            "invoice.draft_discarded",
            "invoice",
            invoice_id,
            {},
            tenant_id=row["tenant_id"],
        )


def issue_invoice(
    actor: Actor, invoice_id: uuid.UUID, *, today: dt.date | None = None
) -> InvoiceOut:
    """Assign the next gapless number of the financial year and freeze the invoice (FR-PLT-016)."""
    today = today or today_ist()
    cfg = billing_cfg()
    with platform_session() as s, db_errors():
        row = _draft_or_409(s, invoice_id)
        account = repo.get(s, m.billing_accounts, row["billing_account_id"])
        account = must(account)
        refreshed = {
            "recipient_legal_name": account["legal_name"],
            "recipient_gstin": account["gstin"],
            "recipient_address": _recipient(account),
            "place_of_supply_state_code": account["state_code"],
        }
        totals = _totals(s, {**dict(row), **refreshed})
        if Decimal(totals["total_inr"]) <= 0:
            raise Conflict("An invoice must have a positive total.", code="empty_invoice")
        fy, fy_short = financial_year(today)
        prefix = str(cfg["invoice_prefix"])
        seq = repo.next_invoice_number(s, fy, prefix)
        number = format_invoice_number(prefix, fy_short, seq)
        row = repo.update_row(
            s,
            m.invoices,
            invoice_id,
            {
                **refreshed,
                **totals,
                "status": "issued",
                "financial_year": fy,
                "sequence_no": seq,
                "invoice_number": number,
                "issue_date": today,
                "due_date": today + dt.timedelta(days=int(cfg["payment_terms_days"])),
                "issued_by": actor.operator_id,
                "issued_at": now(),
            },
        )
        audit_platform(
            s,
            actor,
            "invoice.issued",
            "invoice",
            invoice_id,
            {"invoice_number": number, "total_inr": str(row["total_inr"])},
            tenant_id=row["tenant_id"],
        )
        return _invoice_out(s, row)


def void_invoice(actor: Actor, invoice_id: uuid.UUID, reason: str) -> InvoiceOut:
    with platform_session() as s, db_errors():
        row = repo.get(s, m.invoices, invoice_id, for_update=True)
        if row is None:
            raise NotFound("Invoice not found")
        if row["status"] != "issued" or row["amount_paid_inr"] > 0 or row["tds_inr"] > 0:
            raise Conflict("Only an issued, unpaid invoice can be voided.", code="invalid_state")
        row = repo.update_row(
            s,
            m.invoices,
            invoice_id,
            {
                "status": "void",
                "voided_by": actor.operator_id,
                "voided_at": now(),
                "void_reason": reason,
            },
        )
        audit_platform(
            s,
            actor,
            "invoice.voided",
            "invoice",
            invoice_id,
            {"invoice_number": row["invoice_number"]},
            tenant_id=row["tenant_id"],
        )
        return _invoice_out(s, row)


def _settle(s: Session, invoice: Mapping[Any, Any], today: dt.date) -> RowMapping:
    paid, tds = repo.payments_total(s, invoice["id"])
    covered = paid + tds >= Decimal(invoice["total_inr"])
    status = "paid" if covered else "issued"
    row = repo.update_row(
        s, m.invoices, invoice["id"], {"amount_paid_inr": paid, "tds_inr": tds, "status": status}
    )
    if status == "paid" and invoice["status"] != "paid":
        audit_platform(
            s, SYSTEM, "invoice.paid", "invoice", invoice["id"], {}, tenant_id=invoice["tenant_id"]
        )
    sub = repo.get(s, m.subscriptions, invoice["subscription_id"], for_update=True)
    if (
        sub is not None
        and sub["status"] == "past_due"
        and not repo.overdue_invoices(s, sub["id"], today)
    ):
        repo.update_row(
            s,
            m.subscriptions,
            sub["id"],
            {"status": "active", "past_due_since": None, "grace_ends_on": None},
        )
        audit_platform(
            s,
            SYSTEM,
            "subscription.reactivated",
            "subscription",
            sub["id"],
            {"cause": "paid"},
            tenant_id=sub["tenant_id"],
        )
    return row


def record_payment(
    actor: Actor, invoice_id: uuid.UUID, data: PaymentIn, *, today: dt.date | None = None
) -> PaymentOut:
    """Manual payment (FR-PLT-018). Partial payments allowed; paid when payments + TDS cover it."""
    today = today or today_ist()
    if actor.operator_id is None:
        raise Conflict("Payments are recorded by an operator.", code="operator_required")
    with platform_session() as s, db_errors():
        invoice = repo.get(s, m.invoices, invoice_id, for_update=True)
        if invoice is None:
            raise NotFound("Invoice not found")
        if invoice["status"] != "issued":
            raise Conflict(
                "Payments are recorded against issued, unpaid invoices.", code="invalid_state"
            )
        balance = (
            Decimal(invoice["total_inr"])
            - Decimal(invoice["amount_paid_inr"])
            - Decimal(invoice["tds_inr"])
        )
        if data.amount_inr + data.tds_inr > balance:
            raise ValidationFailed(
                [
                    {
                        "field": "amount_inr",
                        "code": "exceeds_balance",
                        "message_key": "errors.exceeds_balance",
                    }
                ]
            )
        payment = get_provider("manual").record(
            s, invoice=invoice, data=data, recorded_by=actor.operator_id
        )
        _settle(s, invoice, today)
        audit_platform(
            s,
            actor,
            "payment.recorded",
            "payment",
            payment["id"],
            {
                "invoice_id": str(invoice_id),
                "amount_inr": str(data.amount_inr),
                "method": data.method,
            },
            tenant_id=invoice["tenant_id"],
        )
        return PaymentOut.model_validate(dict(payment))


def get_payment(payment_id: uuid.UUID) -> PaymentOut:
    with platform_session() as s:
        row = repo.get(s, m.payments, payment_id)
    if row is None:
        raise NotFound("Payment not found")
    return PaymentOut.model_validate(dict(row))


def reverse_payment(
    actor: Actor, payment_id: uuid.UUID, reason: str, *, today: dt.date | None = None
) -> PaymentOut:
    today = today or today_ist()
    with platform_session() as s, db_errors():
        payment = repo.get(s, m.payments, payment_id, for_update=True)
        if payment is None:
            raise NotFound("Payment not found")
        if payment["status"] != "recorded":
            raise Conflict("The payment is already reversed.", code="invalid_state")
        invoice = repo.get(s, m.invoices, payment["invoice_id"], for_update=True)
        invoice = must(invoice)
        payment = repo.update_row(
            s,
            m.payments,
            payment_id,
            {
                "status": "reversed",
                "reversed_by": actor.operator_id,
                "reversed_at": now(),
                "reversal_reason": reason,
            },
            bump_version=False,
        )
        _settle(s, invoice, today)
        audit_platform(
            s,
            actor,
            "payment.reversed",
            "payment",
            payment_id,
            {"invoice_id": str(invoice["id"])},
            tenant_id=invoice["tenant_id"],
        )
        return PaymentOut.model_validate(dict(payment))


def generate_invoices(month: str, actor: Actor = SYSTEM) -> JobOut:
    """Monthly draft generation (docs/16 §10), idempotent per 'invoices.generate:<YYYY-MM>'.

    For each active/past-due subscription whose next period starts in ``month``: one draft
    for that coming period (billing in advance). A rerun resumes and never duplicates.
    """
    first = dt.date.fromisoformat(month + "-01")
    after = add_months(first, 1)
    key = f"invoices.generate:{month}"
    with platform_session() as s:
        job, created = repo.start_job(
            s,
            task_name="billing.generate_invoices",
            idempotency_key=key,
            created_by=actor.operator_id,
        )
        if not created and job["status"] == "succeeded":
            return JobOut.model_validate(dict(job))
        subs = repo.subscriptions_due_for_invoice(s, first, after)
    generated = 0
    failed = 0
    for sub in subs:
        try:
            with platform_session() as s, db_errors():
                locked = repo.get(s, m.subscriptions, sub["id"], for_update=True)
                if locked is not None and create_draft(
                    s, SYSTEM, locked, locked["current_period_end"]
                ):
                    generated += 1
        except Exception:  # one bad subscription must not stop the run
            failed += 1
            log.exception("billing.invoice.generate_failed", resource_id=str(sub["id"]))
    with platform_session() as s:
        job = repo.update_row(
            s,
            m.job_runs,
            job["id"],
            {
                "status": "succeeded" if failed == 0 else "failed",
                "progress": {"due": len(subs), "generated": generated, "failed": failed},
                "finished_at": now(),
                "attempts": job["attempts"] + (0 if created else 1),
            },
            bump_version=False,
        )
    log.info(
        "billing.invoices.generated", count=generated, outcome="ok" if not failed else "partial"
    )
    return JobOut.model_validate(dict(job))


def tenant_invoice_summaries(tenant_id: uuid.UUID, limit: int = 12) -> list[InvoiceOut]:
    with platform_session() as s:
        rows = list(
            s.execute(
                select(m.invoices)
                .where(and_(m.invoices.c.tenant_id == tenant_id, m.invoices.c.status != "draft"))
                .order_by(m.invoices.c.period_start.desc())
                .limit(limit)
            ).mappings()
        )
        return [_invoice_out(s, r) for r in rows]
