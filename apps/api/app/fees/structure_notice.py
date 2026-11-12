"""Fee structure notice and year-on-year change summary (FM-10; FR-FEE-113..125; internal:
other modules use ``app.fees.service``).

- **Notice (FR-FEE-113..117).** A printable A4 page per class for an academic year: the class's
  general plan (no category; the active one, else the newest draft marked DRAFT), its heads and
  annual amounts, the instalments with due dates and amounts, and the plan's late-fee rule in
  words. The wording (``structure_notice.yaml``) says "the school's fee structure", never that an
  authority approved it.
- **Change summary (FR-FEE-118..122).** Each class's plan for the year against the previous
  academic year's active general plan: heads added, removed and changed, amounts and percent,
  with an increase above the school's review threshold flagged (config, ``verified: false``:
  not a legal rule).
- **Data.** Plans, heads and amounts only: no account, student or payment data, so nothing here
  is personal. ``finance.read`` or ``fee.manage``, school-wide (FR-FEE-024).
- **Out of scope.** No public page (a published notice would need an ADR-0047 handle decision)
  and no approval-minute attachment; reminders with last year's dues are FM-01's owner items.
"""

from __future__ import annotations

import base64
import hashlib
import html
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.errors import ValidationFailed
from app.core.redaction import mask_aadhaar
from app.fees import _common as c
from app.fees import repository as repo
from app.fees.models import FeePlan
from app.fees.money import ZERO, group_indian, total
from app.fees.structure_notice_schemas import (
    FeeStructureChangesOut,
    FeeStructureClassChange,
    FeeStructureHeadChange,
    FeeStructureNoticeClass,
    FeeStructureNoticeHead,
    FeeStructureNoticeInstalment,
    FeeStructureNoticeOut,
)
from app.fees.templates import format_date
from app.tenancy import service as tenancy

CONFIG_PATH: Final = Path(__file__).with_name("structure_notice.yaml")
PERCENT_STEP: Final = Decimal("0.1")
HUNDRED: Final = Decimal(100)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WarnPercent(_Frozen):
    value: Decimal = Field(ge=0, le=1000)
    verified: bool
    source: str = Field(min_length=1)


class LateFeeWording(_Frozen):
    none: str
    flat_per_instalment: str
    per_day: str
    per_day_no_cap: str


class NoticeWording(_Frozen):
    title: str = Field(min_length=1)
    draft_mark: str = Field(min_length=1)
    disclaimer: str = Field(min_length=1)
    late_fee: LateFeeWording


class StructureNoticeConfig(_Frozen):
    version: int = Field(ge=1)
    warn_percent: WarnPercent
    notice: NoticeWording


@lru_cache(maxsize=1)
def load_config() -> StructureNoticeConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    cfg = StructureNoticeConfig.model_validate(raw)
    lowered = (cfg.notice.title + cfg.notice.disclaimer).lower()
    if "approved" in lowered or "commission" in lowered:
        raise ValueError("the notice never claims an authority's approval (FM-10)")
    return cfg


# --- plans of a year ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ClassPlan:
    plan: FeePlan
    heads: list[FeeStructureNoticeHead]
    instalments: list[FeeStructureNoticeInstalment]


def _pick(plans: Sequence[FeePlan], statuses: tuple[str, ...]) -> dict[uuid.UUID, FeePlan]:
    """Per class: the first status in ``statuses`` that has a general plan, newest version."""
    out: dict[uuid.UUID, FeePlan] = {}
    for status in statuses:
        for plan in sorted(
            (p for p in plans if p.status == status and p.category_code is None),
            key=lambda p: -p.plan_version,
        ):
            out.setdefault(plan.class_id, plan)
    return out


def _class_plans(
    session: Session, year_id: uuid.UUID, statuses: tuple[str, ...]
) -> dict[uuid.UUID, _ClassPlan]:
    chosen = _pick(repo.list_plans(session, academic_year_id=year_id), statuses)
    ids = [p.id for p in chosen.values()]
    heads = repo.heads_by_id(session)
    lines: dict[uuid.UUID, list[FeeStructureNoticeHead]] = {}
    for line in repo.plan_lines(session, ids):
        head = heads.get(line.head_id)
        lines.setdefault(line.plan_id, []).append(
            FeeStructureNoticeHead(
                head_id=line.head_id,
                code=head.code if head else "",
                name=head.name if head else "",
                annual_amount=line.annual_amount,
            )
        )
    instalments: dict[uuid.UUID, list[FeeStructureNoticeInstalment]] = {}
    for inst in repo.plan_instalments(session, ids):
        instalments.setdefault(inst.plan_id, []).append(
            FeeStructureNoticeInstalment(
                instalment_no=inst.instalment_no,
                label=inst.label,
                due_on=inst.due_on,
                amount=total(Decimal(str(v)) for v in (inst.amounts or {}).values()),
            )
        )
    return {
        class_id: _ClassPlan(plan, lines.get(plan.id, []), instalments.get(plan.id, []))
        for class_id, plan in chosen.items()
    }


def _late_fee_text(rule: dict[str, object]) -> str:
    words = load_config().notice.late_fee
    kind = str(rule.get("kind") or "none")

    def money(key: str) -> str:
        value = rule.get(key)
        return group_indian(Decimal(str(value))) if value is not None else ""

    grace = str(rule.get("grace_days") or 0)
    if kind == "flat_per_instalment":
        return words.flat_per_instalment.format(amount=money("amount"), grace_days=grace)
    if kind == "per_day":
        if rule.get("cap") is None:
            return words.per_day_no_cap.format(amount=money("amount"), grace_days=grace)
        return words.per_day.format(amount=money("amount"), grace_days=grace, cap=money("cap"))
    return words.none


def _ordered_classes(session: Session) -> list[tuple[uuid.UUID, str]]:
    return [
        (k.id, k.display_en)
        for k in sorted(tenancy.list_classes(session), key=lambda k: (k.sort_order, k.code))
    ]


# --- notice (FR-FEE-113..117) --------------------------------------------------------------------


def structure_notice(
    session: Session,
    ctx: UserContext,
    academic_year_id: uuid.UUID,
    *,
    class_id: uuid.UUID | None = None,
) -> FeeStructureNoticeOut:
    """The fee structure of the year by class (``finance.read`` or ``fee.manage``). 404 for an
    unknown year; 422 ``class_id`` ``not_found`` for an unknown class."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ, c.MANAGE)
    year = tenancy.get_academic_year(session, academic_year_id)
    classes = _ordered_classes(session)
    if class_id is not None:
        classes = [k for k in classes if k[0] == class_id]
        if not classes:
            raise ValidationFailed([c.error("class_id", "not_found")])
    plans = _class_plans(session, year.id, ("active", "draft"))
    words = load_config().notice
    out: list[FeeStructureNoticeClass] = []
    missing: list[str] = []
    for cid, label in classes:
        found = plans.get(cid)
        if found is None:
            missing.append(label)
            continue
        out.append(
            FeeStructureNoticeClass(
                class_id=cid,
                class_label=label,
                plan_id=found.plan.id,
                plan_status="active" if found.plan.status == "active" else "draft",
                heads=found.heads,
                annual_total=total(h.annual_amount for h in found.heads),
                instalments=found.instalments,
                late_fee_text=_late_fee_text(dict(found.plan.late_fee_rule or {})),
            )
        )
    return FeeStructureNoticeOut(
        academic_year_id=year.id,
        academic_year_label=year.label,
        title=words.title.format(year=year.label),
        disclaimer=words.disclaimer,
        classes=out,
        classes_without_plan=missing,
    )


def notice_print(
    session: Session,
    ctx: UserContext,
    academic_year_id: uuid.UUID,
    *,
    class_id: uuid.UUID | None = None,
) -> str:
    """The notice as print-ready A4 pages, one per class (no scripts). Audit
    ``fee.structure_notice.printed`` (year, class count)."""
    notice = structure_notice(session, ctx, academic_year_id, class_id=class_id)
    tenant = tenancy.get_tenant(session)
    head = tenant.settings.certificate_letterhead
    page = render_notice(
        notice,
        school_name=tenant.name,
        address=head.address_en,
        draft_mark=load_config().notice.draft_mark,
    )
    c.record(
        session,
        action="fee.structure_notice.printed",
        resource_type="academic_year",
        resource_id=notice.academic_year_id,
        summary={"classes": len(notice.classes), "class_id": class_id},
    )
    return page


# --- change summary (FR-FEE-118..122) ------------------------------------------------------------


def _percent(previous: Decimal, current: Decimal) -> Decimal | None:
    if previous <= 0:
        return None
    return ((current - previous) * HUNDRED / previous).quantize(PERCENT_STEP, ROUND_HALF_UP)


def _above(percent: Decimal | None, threshold: Decimal) -> bool:
    return percent is not None and percent > threshold


def _previous_year(session: Session, year_id: uuid.UUID) -> tuple[uuid.UUID, str] | None:
    years = tenancy.list_academic_years(session)
    this = next(y for y in years if y.id == year_id)
    earlier = [y for y in years if y.starts_on < this.starts_on]
    if not earlier:
        return None
    last = max(earlier, key=lambda y: y.starts_on)
    return last.id, last.label


def structure_changes(
    session: Session,
    ctx: UserContext,
    academic_year_id: uuid.UUID,
    *,
    warn_percent: Decimal | None = None,
) -> FeeStructureChangesOut:
    """This year's general plans (active, else newest draft) against the previous academic
    year's active general plans, per class and head (``finance.read`` or ``fee.manage``). An
    increase above ``warn_percent`` (default from config) is flagged. 404 for an unknown year.
    Nothing is written."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.READ, c.MANAGE)
    year = tenancy.get_academic_year(session, academic_year_id)
    cfg = load_config().warn_percent
    threshold = cfg.value if warn_percent is None else warn_percent
    previous = _previous_year(session, year.id)
    current = _class_plans(session, year.id, ("active", "draft"))
    before = _class_plans(session, previous[0], ("active",)) if previous else {}
    rows: list[FeeStructureClassChange] = []
    for cid, label in _ordered_classes(session):
        now, then = current.get(cid), before.get(cid)
        if now is None and then is None:
            continue
        rows.append(_class_change(cid, label, now, then, threshold))
    return FeeStructureChangesOut(
        academic_year_id=year.id,
        academic_year_label=year.label,
        previous_year_id=previous[0] if previous else None,
        previous_year_label=previous[1] if previous else None,
        warn_percent=threshold,
        warn_percent_verified=cfg.verified,
        classes=rows,
        classes_above_threshold=sum(
            1 for r in rows if r.above_threshold or any(h.above_threshold for h in r.heads)
        ),
    )


def _class_change(
    class_id: uuid.UUID,
    label: str,
    now: _ClassPlan | None,
    then: _ClassPlan | None,
    threshold: Decimal,
) -> FeeStructureClassChange:
    cur = {h.head_id: h for h in (now.heads if now else [])}
    old = {h.head_id: h for h in (then.heads if then else [])}
    heads: list[FeeStructureHeadChange] = []
    for head_id in [*cur, *(h for h in old if h not in cur)]:
        a, b = old.get(head_id), cur.get(head_id)
        ref = b if b is not None else a
        if ref is None:
            continue
        before = a.annual_amount if a else None
        after = b.annual_amount if b else None
        if a is None:
            kind = "added"
        elif b is None:
            kind = "removed"
        elif before == after:
            kind = "unchanged"
        else:
            kind = "changed"
        pct = _percent(before, after) if before is not None and after is not None else None
        heads.append(
            FeeStructureHeadChange(
                head_id=head_id,
                code=ref.code,
                name=ref.name,
                change=kind,
                previous_amount=before,
                current_amount=after,
                difference=(after or ZERO) - (before or ZERO),
                percent=pct,
                above_threshold=_above(pct, threshold),
            )
        )
    previous_total = total(h.annual_amount for h in old.values())
    current_total = total(h.annual_amount for h in cur.values())
    pct = _percent(previous_total, current_total) if then and now else None
    return FeeStructureClassChange(
        class_id=class_id,
        class_label=label,
        plan_status=(
            None if now is None else ("active" if now.plan.status == "active" else "draft")
        ),
        has_previous=then is not None,
        previous_total=previous_total,
        current_total=current_total,
        difference=current_total - previous_total,
        percent=pct,
        above_threshold=_above(pct, threshold),
        heads=heads,
    )


# --- the printed notice (HTML/CSS, template v1) --------------------------------------------------

NOTICE_STYLE: Final = """
@page { size: A4; margin: 16mm; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; }
body { color: #111; background: #fff; font-family: "Noto Sans", Arial, sans-serif;
  font-size: 11pt; line-height: 1.5; }
section { position: relative; page-break-after: always; border: 1.5px solid #222;
  padding: 8mm 10mm; }
section:last-of-type { page-break-after: auto; }
header { text-align: center; border-bottom: 1px solid #222; padding-bottom: 3mm; }
.school { font-size: 16pt; font-weight: 700; margin: 0; }
.address { margin: 0; font-size: 10pt; }
h1 { text-align: center; font-size: 15pt; margin: 4mm 0 1mm; }
h2 { text-align: center; font-size: 13pt; margin: 0 0 3mm; }
h3 { font-size: 11pt; margin: 4mm 0 1mm; }
.draft { border: 2px solid #900; color: #900; padding: 1mm 2mm; font-weight: 700;
  text-align: center; }
table { width: 100%; border-collapse: collapse; margin: 1mm 0; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid #555; padding: 1mm 2mm; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }
th { background: #f0f0f0; font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tfoot td { font-weight: 700; }
p { margin: 2mm 0; }
.disclaimer { font-size: 9.5pt; color: #333; border: 1px solid #999; padding: 2mm 3mm; }
footer { margin-top: 3mm; font-size: 7.5pt; color: #444; }
@media screen { body { background: #e9e9e9; } section { max-width: 190mm; margin: 6mm auto;
  background: #fff; } }
"""


def _style_hash(style: str) -> str:
    digest = hashlib.sha256(style.encode("utf-8")).digest()
    return "'sha256-" + base64.b64encode(digest).decode("ascii") + "'"


NOTICE_CSP: Final = (
    f"default-src 'none'; style-src {_style_hash(NOTICE_STYLE)}; img-src 'none'; "
    "font-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)


def _e(value: object) -> str:
    return html.escape(mask_aadhaar("" if value is None else str(value)), quote=True)


def render_notice(
    notice: FeeStructureNoticeOut, *, school_name: str, address: str, draft_mark: str
) -> str:
    """One A4 section per class; a draft plan's page carries the DRAFT mark (FR-FEE-115)."""
    sections: list[str] = []
    for k in notice.classes:
        heads = "".join(
            f'<tr><td>{_e(h.name)}</td><td class="num">{_e(group_indian(h.annual_amount))}</td>'
            "</tr>"
            for h in k.heads
        )
        instalments = "".join(
            f"<tr><td>{_e(i.label)}</td><td>{_e(format_date(i.due_on))}</td>"
            f'<td class="num">{_e(group_indian(i.amount))}</td></tr>'
            for i in k.instalments
        )
        sections.append(
            "<section><header>"
            f'<p class="school">{_e(school_name)}</p>'
            + (f'<p class="address">{_e(address)}</p>' if address else "")
            + "</header>"
            + (f'<p class="draft">{_e(draft_mark)}</p>' if k.plan_status == "draft" else "")
            + f"<h1>{_e(notice.title)}</h1><h2>{_e(k.class_label)}</h2>"
            "<h3>Fee heads (for the year)</h3>"
            '<table><thead><tr><th>Fee head</th><th class="num">Amount (₹)</th></tr></thead>'
            f"<tbody>{heads}</tbody>"
            f'<tfoot><tr><td>Total for the year</td><td class="num">'
            f"{_e(group_indian(k.annual_total))}</td></tr></tfoot></table>"
            "<h3>Instalments</h3>"
            + (
                "<table><thead><tr><th>Instalment</th><th>Due date</th>"
                f'<th class="num">Amount (₹)</th></tr></thead><tbody>{instalments}</tbody></table>'
                if k.instalments
                else "<p>Paid in one amount.</p>"
            )
            + f"<h3>Late fee</h3><p>{_e(k.late_fee_text)}</p>"
            f'<p class="disclaimer">{_e(notice.disclaimer)}</p>'
            "<footer>Generated by SchoolOS</footer></section>"
        )
    if not sections:
        sections.append(
            f'<section><header><p class="school">{_e(school_name)}</p></header>'
            f"<h1>{_e(notice.title)}</h1><p>No fee plan has been set up for this year.</p>"
            "</section>"
        )
    return (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex, nofollow">'
        f"<title>{_e(notice.title)}</title><style>{NOTICE_STYLE}</style></head>"
        f"<body>{''.join(sections)}</body></html>"
    )


__all__ = [
    "CONFIG_PATH",
    "NOTICE_CSP",
    "StructureNoticeConfig",
    "load_config",
    "notice_print",
    "render_notice",
    "structure_changes",
    "structure_notice",
]
