"""Bank statement import, matching proposals, UPI confirmations and exceptions (R2-2E; ADR-0042
decision 9, P7; owner decision D19; FR-FEE-016, FR-FEE-017, FR-FEE-025, FR-FEE-026). Internal:
other modules use ``app.fees.service``.

- **Parse, then delete (FR-FEE-016).** The uploaded ``import_file`` (CSV or XLSX, ≤ 2 MB, the
  caller's own upload) is read and deleted in its own transaction (retention reason
  ``bank_statement_read``), so the file never lingers whatever the parse finds. The parse is
  :mod:`app.fees.statements_parse` (pure; formula cells, a printed Aadhaar number and files over
  5,000 transaction rows refused). Only credits are stored; the narration is masked and then
  encrypted with the school's DEK and erased after 90 days. The same file is read once; the same
  credit (reference, amount, date) of an overlapping statement is stored once.
- **Proposals, never postings.** Matching rules (``upi.yaml``) only propose: the exact UTR and
  amount of an issued, unconfirmed UPI receipt (pre-selected), or, for a credit without a UTR,
  the single unconfirmed UPI receipt of the same amount near the value date (not pre-selected).
  A person confirms (bulk allowed); each confirmation is a row in ``fin.upi_confirmations``
  (append-only) and the receipt itself is never edited. A credit whose UTR is on a receipt with
  a different amount stays unmatched and is listed as an exception with that receipt next to it:
  nothing is "corrected" (the receipt is cancelled and issued again through the normal flow).
- **Idempotent.** Confirming a confirmed line again counts as ``already_confirmed``; lines are
  locked in id order; the database refuses a second confirmation of a receipt or a line.
- **Audit** (same transaction; ids and counts only, never UTRs, amounts with names or narration):
  ``fee.statement.read`` (document id, kind, size), ``fee.statement.imported`` (counts),
  ``fee.upi.confirmed`` (counts), ``fee.upi.line_ignored`` (reason code).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Final

from sqlalchemy.orm import Session

from app.authz.context import UserContext
from app.core.db import tenant_session
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.ids import new_id
from app.core.logging import get_logger
from app.core.spreadsheet import CSV_MIME, XLSX_MIME, FileKind, SpreadsheetError, raw_rows
from app.documents import service as documents
from app.fees import _common as c
from app.fees import upi
from app.fees import upi_repository as repo
from app.fees.models import Receipt
from app.fees.statements_parse import ParsedLine, StatementRefused, parse_statement
from app.fees.upi_config import UpiConfig
from app.fees.upi_models import BankStatementImport, BankStatementLine
from app.fees.upi_schemas import (
    ConfirmIn,
    ConfirmOut,
    ConfirmSkipped,
    IgnoreIn,
    MatchIn,
    PresetOut,
    ReceiptRef,
    RematchOut,
    StatementDetailOut,
    StatementImportIn,
    StatementImportOut,
    StatementLineOut,
    UpiExceptionsOut,
)
from app.identity import service as identity
from app.students import crypto
from app.students import rotation as key_rotation

log = get_logger(__name__)

STATEMENT_REASON: Final = "bank_statement_read"
LINES_TABLE: Final = "fin.bank_statement_lines"
NARRATION_COLUMN: Final = "narration_ciphertext"
_MIME_KINDS: Final[dict[str, FileKind]] = {XLSX_MIME: "xlsx", CSV_MIME: "csv"}
_REFUSED_DETAIL: Final = {
    "aadhaar_in_file": "Don't put Aadhaar numbers in the statement. Remove them and upload it "
    "again.",
    "formula_cell": "The statement has formulas. Download it again from the bank and upload that "
    "file without changing it.",
    "too_many_lines": "The statement has more than 5,000 transactions. Download a shorter period "
    "and upload each part.",
    "no_header": "SchoolOS could not find the statement's column headings (date, description and "
    "credit). Choose your bank's format, or download the statement again as Excel or CSV.",
    "rows_unreadable": "Some rows of the statement cannot be read (dates or amounts). Download it "
    "again from the bank and upload that file without changing it.",
    "unknown_preset": "Choose one of the listed bank formats.",
}


def _cfg() -> UpiConfig:
    return upi.config()


def _refused(code: str, issues: Sequence[tuple[int, str, str]] = ()) -> ValidationFailed:
    errors = [c.error("document_id", code)] + [
        {
            "field": f"row {row}" + (f" {col}" if col else ""),
            "code": icode,
            "message_key": f"errors.{icode}",
        }
        for row, col, icode in issues
    ]
    detail = _REFUSED_DETAIL.get(
        code, "This statement cannot be read. Download it again from the bank and upload it."
    )
    return ValidationFailed(errors, detail=detail)


# --- presets ------------------------------------------------------------------------------------


def presets(session: Session, ctx: UserContext) -> list[PresetOut]:
    """The bank formats SchoolOS reads (``fee.reconcile``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE)
    return [
        PresetOut(key=p.key, version=p.version, name=p.name, verified=p.verified)
        for p in _cfg().presets
    ]


# --- import ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Read:
    data: bytes
    kind: FileKind
    deleted_at: dt.datetime
    today: dt.date


def _consume(ctx: UserContext, document_id: uuid.UUID) -> _Read:
    """Read the caller's own ``import_file`` and delete it, committed in its own transaction:
    whatever the parse finds, the statement file does not linger (FR-FEE-016). 404 when not
    visible or uploaded by someone else; 409 ``document_not_ready`` while the virus check runs;
    415/413 for other types or sizes."""
    limits = _cfg().statements
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        c.require_on(session, ctx)
        c.require_school_wide(ctx, c.RECONCILE)
        if not documents.is_own_upload(session, ctx, document_id):
            raise NotFound("Document not found")
        obj = documents.document_object(session, document_id)
        if obj.purpose != "import_file":
            raise ValidationFailed([c.error("document_id", "not_an_import_file")])
        kind = _MIME_KINDS.get(obj.mime_type)
        if kind is None:
            raise documents.UnsupportedFileType("Upload the statement as an XLSX or CSV file.")
        if obj.size_bytes > limits.max_file_bytes:
            raise documents.FileTooLarge("A bank statement can be at most 2 MB.")
        data = documents.read_document_object(session, obj)
        documents.delete_for_retention(session, document_id, reason=STATEMENT_REASON)
        c.record(
            session,
            action="fee.statement.read",
            resource_type="document",
            resource_id=document_id,
            summary={"file_kind": kind, "size_bytes": len(data)},
        )
        return _Read(data=data, kind=kind, deleted_at=c.now(session), today=c.today(session))


def _encrypt(session: Session, line_id: uuid.UUID, narration: str | None) -> bytes | None:
    if not narration:
        return None
    blob, _ = crypto.encrypt_value(
        session, narration, table=LINES_TABLE, column=NARRATION_COLUMN, row_id=line_id
    )
    return blob


def import_statement(ctx: UserContext, data: StatementImportIn) -> StatementImportOut:
    """Read a bank statement (``fee.reconcile``; FR-FEE-016): the file is deleted once read;
    credits are stored and matched (proposals only). 422 with row numbers when the file cannot
    be read; 409 ``statement_already_imported`` for the same file twice."""
    read = _consume(ctx, data.document_id)
    cfg = _cfg()
    try:
        parsed = parse_statement(
            raw_rows(read.data, read.kind, cfg.statements),
            cfg,
            today=read.today,
            preset_key=data.preset,
        )
    except SpreadsheetError as exc:
        raise _refused(exc.code) from None
    except StatementRefused as exc:
        raise _refused(exc.code, [(i.row, i.column, i.code) for i in exc.issues]) from None
    sha = hashlib.sha256(read.data).digest()
    with tenant_session(ctx.tenant_id, ctx.user_id) as session:
        c.require_on(session, ctx)
        repo.lock_imports(session)
        if repo.import_with_sha(session, sha) is not None:
            raise Conflict(
                "This statement file was already imported. Upload a newer statement.",
                code="statement_already_imported",
            )
        fresh, duplicates = _new_credits(session, parsed.lines)
        plan = _proposals(session, [(i, line) for i, line in enumerate(fresh)], cfg)
        period = parsed.period
        import_id = new_id()
        with upi.db_errors():
            row = repo.insert_import(
                session,
                {
                    "id": import_id,
                    "preset_key": parsed.preset.key,
                    "preset_version": parsed.preset.version,
                    "file_kind": read.kind,
                    "file_sha256": sha,
                    "period_from": period[0] if period else None,
                    "period_to": period[1] if period else None,
                    "line_count": parsed.transactions,
                    "credit_count": len(parsed.lines),
                    "duplicate_count": duplicates,
                    "proposed_count": len(plan),
                    "imported_by": ctx.membership_id,
                    "file_deleted_at": read.deleted_at,
                },
            )
            for index, line in enumerate(fresh):
                line_id = new_id()
                receipt_id, kind = plan.get(index, (None, None))
                inserted = repo.insert_line(
                    session,
                    {
                        "id": line_id,
                        "import_id": import_id,
                        "line_no": line.line_no,
                        "value_date": line.value_date,
                        "amount": line.amount,
                        "ref_kind": line.ref_kind,
                        "utr_or_ref": line.reference,
                        "narration_ciphertext": _encrypt(session, line_id, line.narration),
                        "match_status": "proposed" if receipt_id else "unmatched",
                        "match_kind": kind,
                        "receipt_id": receipt_id,
                    },
                )
                if not inserted:  # pragma: no cover - the import lock makes this unreachable
                    raise Conflict("Try again.", code="statement_conflict")
        c.record(
            session,
            action="fee.statement.imported",
            resource_type="bank_statement",
            resource_id=row.id,
            summary={
                "preset": row.preset_key,
                "file_kind": row.file_kind,
                "lines": row.line_count,
                "credits": row.credit_count,
                "duplicates": row.duplicate_count,
                "proposed": row.proposed_count,
            },
        )
        log.info("fees.statement_imported", resource_id=row.id, count=row.credit_count)
        return _import_out(session, row)


def _new_credits(session: Session, lines: Sequence[ParsedLine]) -> tuple[list[ParsedLine], int]:
    """The credits not stored yet (overlapping statements), and how many were already there.
    Within one file the same reference, amount and date counts once too."""
    seen: set[tuple[str, object, dt.date]] = set()
    fresh: list[ParsedLine] = []
    duplicates = 0
    existing = repo.existing_credits(
        session, [(x.reference, x.amount, x.value_date) for x in lines if x.reference]
    )
    for line in lines:
        if line.reference is not None:
            key = (line.reference, line.amount, line.value_date)
            if key in seen or key in existing:
                duplicates += 1
                continue
            seen.add(key)
        fresh.append(line)
    return fresh, duplicates


# --- matching (proposals only) --------------------------------------------------------------------


def _proposals(
    session: Session, lines: Sequence[tuple[int, ParsedLine | BankStatementLine]], cfg: UpiConfig
) -> dict[int, tuple[uuid.UUID, str]]:
    """``{index: (receipt id, rule)}`` for the lines a rule matches (see the module docstring).
    A receipt is proposed for one line at most; an amount-and-date candidate wanted by two
    credits is proposed for neither."""
    rules = cfg.matching
    out: dict[int, tuple[uuid.UUID, str]] = {}
    taken: set[uuid.UUID] = set()
    utr_lines = [(i, x) for i, x in lines if _ref_kind(x) == "utr"]
    if rules.utr_exact and utr_lines:
        receipts = repo.receipts_with_utr(session, {_ref(x) or "" for _, x in utr_lines})
        issued = {r.utr: r for r in receipts if r.status == "issued"}
        confirmed = repo.confirmed_receipt_ids(session, [r.id for r in issued.values()])
        busy = repo.receipts_in_use(session, [r.id for r in issued.values()])
        for index, line in utr_lines:
            receipt = issued.get(_ref(line))
            if (
                receipt is None
                or receipt.amount != line.amount
                or receipt.id in confirmed
                or receipt.id in busy
                or receipt.id in taken
            ):
                continue
            out[index] = (receipt.id, "utr_exact")
            taken.add(receipt.id)
    near = rules.amount_date
    if near.enabled:
        wanted: dict[uuid.UUID, list[int]] = {}
        for index, line in lines:
            if _ref_kind(line) == "utr":
                continue
            day = line.value_date
            candidates = [
                r
                for r in repo.open_upi_receipts_by_amount(
                    session,
                    line.amount,
                    day - dt.timedelta(days=near.days_before),
                    day + dt.timedelta(days=near.days_after),
                )
                if r.id not in taken
            ]
            if len(candidates) == 1:
                wanted.setdefault(candidates[0].id, []).append(index)
        for receipt_id, indexes in wanted.items():
            if len(indexes) == 1:
                out[indexes[0]] = (receipt_id, "amount_date")
    return out


def _ref_kind(line: ParsedLine | BankStatementLine) -> str:
    return line.ref_kind


def _ref(line: ParsedLine | BankStatementLine) -> str | None:
    return line.reference if isinstance(line, ParsedLine) else line.utr_or_ref


def rematch(session: Session, ctx: UserContext) -> RematchOut:
    """Look again for receipts for the recent unmatched credits (receipts issued after the
    statement was imported); proposals only (``fee.reconcile``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE)
    cfg = _cfg()
    repo.lock_imports(session)
    since = c.today(session) - dt.timedelta(days=cfg.matching.rematch_window_days)
    lines = repo.unmatched_lines(session, since=since, limit=cfg.matching.max_review_lines)
    plan = _proposals(session, list(enumerate(lines)), cfg)
    with upi.db_errors():
        for index, (receipt_id, kind) in plan.items():
            repo.set_line(
                session,
                lines[index].id,
                {"match_status": "proposed", "match_kind": kind, "receipt_id": receipt_id},
            )
    log.info("fees.statement_rematched", count=len(plan))
    return RematchOut(looked_at=len(lines), proposed=len(plan))


# --- reading --------------------------------------------------------------------------------------


def _import_out(session: Session, row: BankStatementImport) -> StatementImportOut:
    names = identity.member_display_names(session, [row.imported_by])
    return StatementImportOut(
        id=row.id,
        preset_key=row.preset_key,
        preset_version=row.preset_version,
        file_kind=row.file_kind,
        period_from=row.period_from,
        period_to=row.period_to,
        line_count=row.line_count,
        credit_count=row.credit_count,
        duplicate_count=row.duplicate_count,
        proposed_count=row.proposed_count,
        imported_by=row.imported_by,
        imported_by_name=names.get(row.imported_by),
        imported_at=row.imported_at,
        file_deleted_at=row.file_deleted_at,
    )


def _receipt_ref(r: Receipt) -> ReceiptRef:
    student = r.content.get("student") or {}
    return ReceiptRef(
        id=r.id,
        receipt_number=r.receipt_number,
        receipt_date=r.receipt_date,
        amount=r.amount,
        status=r.status,
        utr=r.utr,
        student_name=str(student.get("name", "")),
        class_section=str(student.get("class_section", "")),
    )


def _narration(session: Session, line: BankStatementLine) -> str | None:
    if line.narration_ciphertext is None:
        return None
    try:
        return crypto.decrypt_value(
            session,
            bytes(line.narration_ciphertext),
            table=LINES_TABLE,
            column=NARRATION_COLUMN,
            row_id=line.id,
        )
    except crypto.CryptoError:  # pragma: no cover - tampered or moved ciphertext
        log.error("fees.narration_unreadable", resource_id=line.id, error_code="crypto")
        return None


def _exception(  # noqa: PLR0911 - one return per exception kind
    line: BankStatementLine,
    receipt: Receipt | None,
    by_utr: dict[str, list[Receipt]],
    confirmed: set[uuid.UUID],
) -> tuple[str | None, Receipt | None]:
    """Why a line needs a person (computed when read, so always current), and the receipt it
    conflicts with."""
    if line.match_status == "confirmed":
        if receipt is not None and receipt.status == "cancelled":
            return "confirmed_receipt_cancelled", None
        return None, None
    if line.match_status != "unmatched":
        return None, None
    if line.ref_kind != "utr" or line.utr_or_ref is None:
        return "no_receipt", None
    holders = by_utr.get(line.utr_or_ref, [])
    issued = next((r for r in holders if r.status == "issued"), None)
    if issued is None:
        return ("receipt_cancelled" if holders else "no_receipt"), (holders[0] if holders else None)
    if issued.amount != line.amount:
        return "amount_mismatch", issued
    if issued.id in confirmed:
        return "receipt_already_confirmed", issued
    return None, issued  # a match found after the import: "Find matches again" proposes it


def lines_out(session: Session, lines: Sequence[BankStatementLine]) -> list[StatementLineOut]:
    receipts = repo.receipts_by_id(session, {x.receipt_id for x in lines if x.receipt_id})
    utrs = {
        x.utr_or_ref
        for x in lines
        if x.ref_kind == "utr" and x.utr_or_ref and x.match_status == "unmatched"
    }
    by_utr: dict[str, list[Receipt]] = {}
    for r in repo.receipts_with_utr(session, utrs):
        by_utr.setdefault(r.utr or "", []).append(r)
    confirmed = repo.confirmed_receipt_ids(
        session, [r.id for group in by_utr.values() for r in group]
    )
    names = identity.member_display_names(session, c.unique_ids(x.decided_by for x in lines))
    out = []
    for x in lines:
        receipt = receipts.get(x.receipt_id) if x.receipt_id else None
        code, conflict = _exception(x, receipt, by_utr, confirmed)
        out.append(
            StatementLineOut(
                id=x.id,
                import_id=x.import_id,
                line_no=x.line_no,
                value_date=x.value_date,
                amount=x.amount,
                ref_kind=x.ref_kind,
                utr_or_ref=x.utr_or_ref,
                narration=_narration(session, x),
                narration_erased=x.narration_erased_at is not None,
                match_status=x.match_status,
                match_kind=x.match_kind,
                receipt=_receipt_ref(receipt) if receipt else None,
                exception=code,
                conflict_receipt=_receipt_ref(conflict) if conflict else None,
                ignore_reason=x.ignore_reason,
                decided_by_name=names.get(x.decided_by) if x.decided_by else None,
                decided_at=x.decided_at,
                version=x.version,
            )
        )
    return out


def list_statements(session: Session, ctx: UserContext, *, limit: int) -> list[StatementImportOut]:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE)
    return [_import_out(session, r) for r in repo.list_imports(session, limit)]


def _load_import(session: Session, import_id: uuid.UUID) -> BankStatementImport:
    row = repo.get_import(session, import_id)
    if row is None:
        raise NotFound("Bank statement not found")
    return row


def get_statement(
    session: Session, ctx: UserContext, import_id: uuid.UUID, *, status: str | None = None
) -> StatementDetailOut:
    """A statement with its credits, proposals and exceptions (``fee.reconcile``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE)
    row = _load_import(session, import_id)
    lines = repo.lines_of_import(session, import_id)
    if status is not None:
        lines = [x for x in lines if x.match_status == status]
    return StatementDetailOut(statement=_import_out(session, row), lines=lines_out(session, lines))


# --- decisions ------------------------------------------------------------------------------------


def _check_pair(line: BankStatementLine, receipt: Receipt | None) -> str | None:
    """Why ``line`` cannot confirm ``receipt`` (the database checks the same), else ``None``."""
    if receipt is None or receipt.mode != "upi_qr":
        return "receipt_not_confirmable"
    if receipt.status != "issued":
        return "receipt_cancelled"
    if receipt.amount != line.amount:
        return "amount_mismatch"
    if line.ref_kind == "utr" and line.utr_or_ref != receipt.utr:
        return "utr_differs"
    return None


def _confirm_line(
    session: Session, ctx: UserContext, line: BankStatementLine, receipt_id: uuid.UUID, kind: str
) -> None:
    now = c.now(session)
    with upi.db_errors():
        repo.insert_confirmation(
            session,
            {
                "id": new_id(),
                "receipt_id": receipt_id,
                "statement_line_id": line.id,
                "match_kind": kind,
                "confirmed_by": ctx.membership_id,
                "confirmed_at": now,
            },
        )
        repo.set_line(
            session,
            line.id,
            {
                "match_status": "confirmed",
                "match_kind": kind,
                "receipt_id": receipt_id,
                "decided_by": ctx.membership_id,
                "decided_at": now,
            },
        )


def _unpropose(session: Session, line: BankStatementLine) -> None:
    repo.set_line(
        session, line.id, {"match_status": "unmatched", "match_kind": None, "receipt_id": None}
    )


def confirm(
    session: Session, ctx: UserContext, import_id: uuid.UUID, data: ConfirmIn
) -> ConfirmOut:
    """Confirm the proposed matches of a statement in one step (``fee.reconcile``; FR-FEE-016).
    Each line is re-checked under lock: a proposal that no longer holds (receipt cancelled,
    confirmed elsewhere) is dropped back to unmatched and reported in ``skipped``; the receipt is
    never changed. Confirming again is harmless (``already_confirmed``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE)
    _load_import(session, import_id)
    wanted = list(dict.fromkeys(data.line_ids))
    lines = repo.lock_lines(session, wanted)
    found = {x.id for x in lines}
    skipped = [ConfirmSkipped(line_id=i, code="not_found") for i in wanted if i not in found]
    confirmed = already = 0
    for line in lines:
        if line.import_id != import_id:
            skipped.append(ConfirmSkipped(line_id=line.id, code="not_in_statement"))
            continue
        if line.match_status == "confirmed":
            already += 1
            continue
        if line.match_status != "proposed" or line.receipt_id is None or line.match_kind is None:
            skipped.append(ConfirmSkipped(line_id=line.id, code="not_proposed"))
            continue
        receipt = repo.receipt_for_update(session, line.receipt_id)
        problem = _check_pair(line, receipt)
        if problem is None and repo.confirmation_of_receipt(session, line.receipt_id) is not None:
            problem = "receipt_already_confirmed"
        if problem is not None:
            _unpropose(session, line)
            skipped.append(ConfirmSkipped(line_id=line.id, code=problem))
            continue
        _confirm_line(session, ctx, line, line.receipt_id, line.match_kind)
        confirmed += 1
    c.record(
        session,
        action="fee.upi.confirmed",
        resource_type="bank_statement",
        resource_id=import_id,
        summary={"confirmed": confirmed, "already": already, "skipped": len(skipped)},
    )
    log.info("fees.upi_confirmed", resource_id=import_id, count=confirmed)
    return ConfirmOut(confirmed=confirmed, already_confirmed=already, skipped=skipped)


def _load_line(session: Session, line_id: uuid.UUID) -> BankStatementLine:
    line = repo.get_line(session, line_id, lock=True)
    if line is None:
        raise NotFound("Bank statement line not found")
    return line


_PAIR_MESSAGES: Final = {
    "receipt_not_confirmable": "Only an issued UPI receipt can be confirmed from the bank.",
    "receipt_cancelled": "This receipt is cancelled. Choose the receipt issued in its place.",
    "amount_mismatch": "The bank credit and the receipt have different amounts, so they are not "
    "matched. Check the receipt; a wrong receipt is cancelled and issued again.",
    "utr_differs": "The bank credit carries a different UPI reference than the receipt, so they "
    "are not matched. Check the receipt; a wrong receipt is cancelled and issued again.",
    "receipt_already_confirmed": "This receipt is already confirmed from another bank credit.",
}


def match(
    session: Session, ctx: UserContext, line_id: uuid.UUID, data: MatchIn
) -> StatementLineOut:
    """A person matches one credit with one UPI receipt and confirms it (``fee.reconcile``): for
    a credit no rule could match, or a confirmed credit whose receipt was cancelled and issued
    again. The pair must agree (same amount; the same UTR when the bank shows one): 409 otherwise,
    never corrected. Repeating the same match is harmless."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE)
    line = _load_line(session, line_id)
    if line.match_status == "confirmed" and line.receipt_id == data.receipt_id:
        return lines_out(session, [line])[0]
    if line.match_status == "ignored":
        raise Conflict("This bank line was marked as not a fee payment.", code="line_decided")
    if line.match_status == "confirmed":
        current = repo.receipts_by_id(session, [line.receipt_id]) if line.receipt_id else {}
        old = current.get(line.receipt_id) if line.receipt_id else None
        if old is None or old.status != "cancelled":
            raise Conflict("This bank credit already confirms a receipt.", code="line_decided")
    receipt = repo.receipt_for_update(session, data.receipt_id)
    if receipt is None:
        raise NotFound("Receipt not found")
    problem = _check_pair(line, receipt)
    if problem is None and repo.confirmation_of_receipt(session, receipt.id) is not None:
        problem = "receipt_already_confirmed"
    if problem is not None:
        raise Conflict(_PAIR_MESSAGES[problem], code=problem)
    holder = repo.proposal_for_receipt(session, receipt.id)
    if holder is not None and holder.id != line.id:
        if holder.match_status == "confirmed":  # pragma: no cover - confirmation_of_receipt
            raise Conflict(_PAIR_MESSAGES["receipt_already_confirmed"], code="receipt_taken")
        _unpropose(session, repo.get_line(session, holder.id, lock=True) or holder)
    kind = line.match_kind if line.receipt_id == receipt.id and line.match_kind else "manual"
    _confirm_line(session, ctx, line, receipt.id, kind)
    c.record(
        session,
        action="fee.upi.confirmed",
        resource_type="bank_statement",
        resource_id=line.import_id,
        summary={"confirmed": 1, "already": 0, "skipped": 0, "manual": kind == "manual"},
    )
    return lines_out(session, [_load_line(session, line_id)])[0]


def ignore(
    session: Session, ctx: UserContext, line_id: uuid.UUID, data: IgnoreIn
) -> StatementLineOut:
    """Mark a credit as not a fee payment (interest, a grant, a payment recorded elsewhere)
    (``fee.reconcile``). A confirmed credit cannot be ignored; ignoring twice is harmless."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE)
    line = _load_line(session, line_id)
    if line.match_status == "ignored":
        return lines_out(session, [line])[0]
    if line.match_status == "confirmed":
        raise Conflict("This bank credit confirms a receipt.", code="line_decided")
    with upi.db_errors():
        repo.set_line(
            session,
            line.id,
            {
                "match_status": "ignored",
                "match_kind": None,
                "receipt_id": None,
                "ignore_reason": data.reason,
                "decided_by": ctx.membership_id,
                "decided_at": c.now(session),
            },
        )
    c.record(
        session,
        action="fee.upi.line_ignored",
        resource_type="bank_statement_line",
        resource_id=line.id,
        summary={"reason": data.reason, "statement_id": str(line.import_id)},
    )
    return lines_out(session, [_load_line(session, line_id)])[0]


# --- exceptions (FR-FEE-017) ----------------------------------------------------------------------


def exceptions(session: Session, ctx: UserContext) -> UpiExceptionsOut:
    """UPI receipts not confirmed within the school's window, credits nobody matched yet and
    confirmed credits whose receipt was cancelled afterwards (``fee.reconcile``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE)
    today = c.today(session)
    days = upi.confirm_within_days(session)
    limit = _cfg().exceptions.list_max
    rows, count = repo.unconfirmed_receipts(
        session, dated_before=today - dt.timedelta(days=days), limit=limit
    )
    lines, line_count = repo.exception_lines(session, limit)
    return UpiExceptionsOut(
        as_of=today,
        confirm_within_days=days,
        unconfirmed_receipts=upi.unconfirmed_out(session, rows, today),
        unconfirmed_count=count,
        lines=lines_out(session, lines),
        lines_count=line_count,
        proposed_count=repo.count_proposed(session),
    )


# --- maintenance --------------------------------------------------------------------------------


def erase_old_narrations(session: Session, *, batch: int = 1000) -> int:
    """Erase narrations older than ``narration_keep_days`` (docs/05 §13; PRV-024). Returns how
    many were erased (one batch; the daily job repeats until none is left)."""
    keep = _cfg().statements.narration_keep_days
    before = c.now(session) - dt.timedelta(days=keep)
    ids = repo.stale_narrations(session, before, batch)
    return repo.erase_narrations(session, ids)


def reencrypt_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """DEK rotation (SEC-012): narrations under another key version are re-encrypted to
    ``target_version`` with the same associated data."""
    done = 0
    for line in repo.stale_narration_keys(session, target_version, limit):
        blob = crypto.reencrypt_value(
            session,
            bytes(line.narration_ciphertext or b""),
            table=LINES_TABLE,
            column=NARRATION_COLUMN,
            row_id=line.id,
            key_version=target_version,
            keyring=keyring,
        )
        repo.set_narration(session, line.id, blob)
        done += 1
    return done


key_rotation.register_reencryptor("fees_statements", reencrypt_batch)


def receipt_ids_in_use(session: Session, ids: Collection[uuid.UUID]) -> set[uuid.UUID]:
    return repo.receipts_in_use(session, ids)


__all__ = [
    "STATEMENT_REASON",
    "confirm",
    "erase_old_narrations",
    "exceptions",
    "get_statement",
    "ignore",
    "import_statement",
    "lines_out",
    "list_statements",
    "match",
    "presets",
    "rematch",
]
