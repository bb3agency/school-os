"""Certificates and registers public API (M3; US-1101..US-1108; FR-CERT-001..014,
FR-REG-001..005; BR-01, BR-04, BR-11, BR-12). *(Proposed from the roadmap scope; PO to
confirm.)*

- **From the checked record (FR-CERT-002).** Printed values are the canonical values of the
  student record (``students.canonical_values``: the admission register anchors identity
  fields, BR-01). Issuing (and approving) is refused with 409 ``certificate_blocked`` while an
  open blocker finding of a base rule concerns a printed field or the student as a whole
  (``dq.open_blockers``), or a required printed value is empty. Nothing here corrects a record
  (invariant 6): the fix is a change request or a waiver (US-502), then the certificate.
- **Maker-checker (FR-CERT-004, 07 §6.3).** Types with ``requires_approval`` (TC) wait for a
  holder of ``certificate.approve`` (step-up, ``If-Match``) who is not the requester (service
  check + DB ``CHECK``). Other types are issued at once by ``certificate.issue``.
- **Issue (FR-CERT-003/005/006).** One transaction: the next serial number of (school, type,
  current academic year) from the locked counter row, the printed values frozen with their
  SHA-256, the register entry, and for a TC the withdrawal (``students
  .withdraw_for_transfer_certificate``: active enrolments end, status ``left``). The PDF is
  rendered on queue ``pdf`` (outbox ``certificate.render_requested``) and stored as a document.
- **Duplicates (FR-CERT-007)** copy the original's frozen content with a copy number and a
  reason; **cancellation (FR-CERT-008)** keeps the number and archives the document.
- **Registers (FR-REG-001..004)**: A4 print pages of the TC register, the certificate issue
  register and the admission and withdrawal register (``register.read``, step-up, school-wide).
- **Scope (SEC-015).** Certificates are reached through their student: scoped holders see only
  their students; anything else is 404.
- Audit summaries, outbox payloads, notification params and logs hold IDs, codes and serial
  numbers only; never names or printed values.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.certificates import repository as repo
from app.certificates import templates
from app.certificates.config import (
    CertificatesConfig,
    InputSpec,
    TypeSpec,
    format_serial,
    load_config,
)
from app.certificates.models import Certificate
from app.certificates.schemas import (
    ApproveIn,
    Blocker,
    CertificateContent,
    CertificateOut,
    CertificatePreview,
    CertificateRequest,
    CertificateTypeOut,
    ChoiceOut,
    ContentLine,
    DownloadUrlOut,
    DuplicateRequest,
    InputOut,
    PreviewWarning,
    PrintedField,
    ReasonIn,
)
from app.core import purge as purging
from app.core.db import tenant_session
from app.core.errors import (
    Conflict,
    Forbidden,
    NotFound,
    PreconditionFailed,
    StepUpRequired,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.languages import telugu_enabled, telugu_text
from app.core.logging import get_context, get_logger
from app.core.pdf import PdfRenderer, get_renderer
from app.core.records import RecordTable
from app.core.redaction import contains_full_aadhaar
from app.documents import service as documents
from app.dq import service as dq
from app.identity import service as identity
from app.identity.principal import STEP_UP_MAX_AGE
from app.notifications import service as notifications
from app.ops import service as ops
from app.students import service as students
from app.students.schemas import EnrollmentOut
from app.tenancy import service as tenancy
from app.tenancy.schemas import AcademicYearOut, ClassOut, SectionOut

READ: Final = "certificate.read"
ISSUE: Final = "certificate.issue"
APPROVE: Final = "certificate.approve"
REGISTER: Final = "register.read"
STUDENT_READ: Final = "student.read_basic"
RENDER_EVENT: Final = "certificate.render_requested"
RENDER_TASK: Final = "certificates.render"
DOCUMENT_PURPOSE: Final = "certificate"
AADHAAR_CODE: Final = "aadhaar_full_number_rejected"
AADHAAR_DETAIL: Final = "Don't enter Aadhaar numbers. Enter only the last 4 digits."
IST: Final = ZoneInfo("Asia/Kolkata")
LIVE_ENROLMENT: Final = "active"
ISSUABLE_STATUSES: Final = frozenset({"active", "provisional"})
_FILENAME_RE: Final = re.compile(r"[^A-Za-z0-9._-]+")
# The admission and withdrawal register prints these canonical values (all C2).
REGISTER_KEYS: Final = (
    "full_name",
    "father_name",
    "mother_name",
    "dob",
    "admission_no",
    "admission_date",
)
DATE_KEYS: Final = frozenset({"dob", "admission_date"})
GENDER_LABELS: Final = {
    "male": "Male / పురుషుడు",
    "female": "Female / స్త్రీ",
    "transgender": "Transgender / ట్రాన్స్‌జెండర్",
}
# English first (ADR-0036): printed while Telugu is hidden.
GENDER_LABELS_EN: Final = {
    "male": "Male",
    "female": "Female",
    "transgender": "Transgender",
}

ops.register_outbox_route(RENDER_EVENT, RENDER_TASK)

log = get_logger(__name__)


# --- errors (docs/09 certificates) ---------------------------------------------------------------


class CertificateBlocked(Conflict):
    code = "certificate_blocked"

    def __init__(self) -> None:
        super().__init__(
            "This certificate cannot be issued yet. Open the preview to see what needs fixing "
            "(a finding to resolve or a field to correct through a change request)."
        )


class CertificateNotPending(Conflict):
    code = "certificate_not_pending"

    def __init__(self) -> None:
        super().__init__("This certificate was already decided or issued. Reload to see it.")


class CertificateNotIssued(Conflict):
    code = "certificate_not_issued"

    def __init__(self) -> None:
        super().__init__("Only an issued certificate can be printed again, copied or cancelled.")


class SelfApprovalForbidden(Forbidden):
    code = "self_approval_forbidden"

    def __init__(self) -> None:
        super().__init__("You prepared this certificate, so someone else must approve it.")


class NotRequester(Forbidden):
    code = "not_requester"

    def __init__(self) -> None:
        super().__init__("Only the person who prepared this certificate can withdraw it.")


class NoCurrentYear(Conflict):
    code = "no_current_academic_year"

    def __init__(self) -> None:
        super().__init__(
            "Set the current academic year first (Settings > School structure): serial numbers "
            "run per academic year."
        )


# --- plumbing ------------------------------------------------------------------------------------


def settings() -> CertificatesConfig:
    return load_config()


def _now(session: Session) -> dt.datetime:
    """Transaction time (tests move the clock by patching this function)."""
    return repo.now(session)


def _today(session: Session) -> dt.date:
    return _now(session).astimezone(IST).date()


@contextmanager
def _db_errors() -> Iterator[None]:
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def _audit(
    session: Session,
    *,
    action: str,
    resource_id: uuid.UUID | None,
    summary: Mapping[str, Any],
    system: bool = False,
    resource_type: str = "certificate",
) -> None:
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if system else "user",
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _error(field: str, code: str, message_key: str | None = None) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": message_key or f"errors.{code}"}


def _clean_reason(field: str, value: str | None, *, required: bool) -> str | None:
    """Reasons and notes: trimmed, 10..1000 characters, never a full Aadhaar number."""
    if value is None or not value.strip():
        if required:
            raise ValidationFailed([_error(field, "missing")])
        return None
    text = value.strip()
    if contains_full_aadhaar(text):
        raise ValidationFailed(
            [_error(field, AADHAAR_CODE, "errors.aadhaar_last4_only")], detail=AADHAAR_DETAIL
        )
    cfg = settings()
    if len(text) < cfg.reason_min_length:
        raise ValidationFailed([_error(field, "too_short")])
    if len(text) > cfg.reason_max_length:
        raise ValidationFailed([_error(field, "too_long")])
    return text


def _require_recent_mfa(ctx: UserContext) -> None:
    """Defence in depth for step-up routes: MFA sign-in within 5 minutes, else 428."""
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


def _reach(
    session: Session, ctx: UserContext, permissions: Iterable[str]
) -> frozenset[uuid.UUID] | None:
    """Students the caller reaches through any of ``permissions`` (and ``student.read_basic``);
    ``None`` means the whole school."""
    held = [p for p in dict.fromkeys(permissions) if ctx.has(p)]
    if not held or not ctx.has(STUDENT_READ):
        return frozenset()
    read_school = ctx.scope_for(STUDENT_READ).school_wide
    reached: set[uuid.UUID] = set()
    for permission in held:
        grant = ctx.scope_for(permission)
        if grant.school_wide:
            if read_school:
                return None
            reached.update(students.list_students_in_scope(session, ctx))
            continue
        if grant.section_ids:
            reached.update(
                students.list_students_in_scope(session, ctx, section_ids=grant.section_ids)
            )
        if grant.class_ids:
            reached.update(students.list_students_in_scope(session, ctx, class_ids=grant.class_ids))
    return frozenset(reached)


def _in_reach(reach: frozenset[uuid.UUID] | None, student_id: uuid.UUID) -> bool:
    return reach is None or student_id in reach


def _load(
    session: Session,
    ctx: UserContext,
    certificate_id: uuid.UUID,
    *,
    permissions: Iterable[str],
    lock: bool = False,
) -> Certificate:
    """The certificate if the caller reaches its student through ``permissions``; else 404."""
    row = repo.get_certificate(session, certificate_id, lock=lock)
    if row is None or not _in_reach(_reach(session, ctx, permissions), row.student_id):
        raise NotFound("Certificate not found")
    return row


def _check_version(row: Certificate, expected_version: int) -> None:
    if row.version != expected_version:
        raise PreconditionFailed("This certificate was changed by someone else. Reload it.")


def _notify(
    session: Session, row: Certificate, template_key: str, recipients: notifications.Recipients
) -> None:
    notifications.notify(
        session,
        tenant_id=row.tenant_id,
        recipients=recipients,
        template_key=template_key,
        params={
            "certificate_id": str(row.id),
            "student_id": str(row.student_id),
            "certificate_type": row.certificate_type,
        },
        resource_id=row.id,
        dedupe_key=f"{template_key}:{row.id}",
    )


# --- structure and record ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Structure:
    years: dict[uuid.UUID, AcademicYearOut]
    sections: dict[uuid.UUID, SectionOut]
    classes: dict[uuid.UUID, ClassOut]
    current: AcademicYearOut | None

    @classmethod
    def load(cls, session: Session) -> _Structure:
        years = {y.id: y for y in tenancy.list_academic_years(session)}
        return cls(
            years=years,
            sections={s.id: s for s in tenancy.list_sections(session)},
            classes={c.id: c for c in tenancy.list_classes(session)},
            current=next((y for y in years.values() if y.is_current), None),
        )

    def class_labels(self, section_id: uuid.UUID) -> tuple[str, str]:
        section = self.sections.get(section_id)
        klass = self.classes.get(section.class_id) if section is not None else None
        if section is None or klass is None:
            return ("—", "—")
        return (f"{klass.display_en} {section.name}", f"{klass.display_te} {section.name}")

    def year_label(self, year_id: uuid.UUID | None) -> str:
        year = self.years.get(year_id) if year_id is not None else None
        return year.label if year is not None else "—"


def _format_value(key: str, value: str | None) -> str | None:
    if value is None or value == "":
        return None
    if key in DATE_KEYS:
        try:
            return templates.format_date(dt.date.fromisoformat(value))
        except ValueError:
            return value
    if key == "gender":
        labels = GENDER_LABELS if telugu_enabled() else GENDER_LABELS_EN
        return labels.get(value, value)
    return value


@dataclass(frozen=True, slots=True)
class _Record:
    """What a certificate needs from the student record, read once."""

    spec: TypeSpec
    certificate_type: str
    student_id: uuid.UUID
    student_status: str
    fields: list[PrintedField]
    raw: dict[str, str | None]
    enrolments: list[EnrollmentOut]
    structure: _Structure
    blockers: list[Blocker]
    warnings: list[PreviewWarning]

    @property
    def current_enrolment(self) -> EnrollmentOut | None:
        year = self.structure.current
        return next(
            (
                e
                for e in reversed(self.enrolments)
                if e.status == LIVE_ENROLMENT and year is not None and e.academic_year_id == year.id
            ),
            None,
        )

    @property
    def active_enrolments(self) -> list[EnrollmentOut]:
        return [e for e in self.enrolments if e.status == LIVE_ENROLMENT]

    @property
    def last_enrolment(self) -> EnrollmentOut | None:
        active = self.active_enrolments
        if active:
            return active[-1]
        return self.enrolments[-1] if self.enrolments else None


def _record(
    session: Session, ctx: UserContext, student_id: uuid.UUID, certificate_type: str
) -> _Record:
    """Read the printed values, enrolments and blockers of one student the caller reaches."""
    cfg = settings()
    spec = cfg.spec(certificate_type)
    profile = students.get_profile(session, ctx, student_id)  # 404 outside READ scope
    catalog = {a.key: a for a in students.attribute_catalog(session)}
    # FR-CERT-009: never a restricted (C3) attribute, whatever the configuration says.
    keys = [k for k in spec.printed if k in catalog and catalog[k].classification != "C3"]
    canonical = students.canonical_values(session, [student_id], keys).get(student_id, {})
    fields: list[PrintedField] = []
    raw: dict[str, str | None] = {}
    blockers: list[Blocker] = []
    warnings: list[PreviewWarning] = []
    for key in keys:
        value = canonical.get(key)
        raw[key] = value.value if value is not None else None
        fields.append(
            PrintedField(
                key=key,
                label_en=catalog[key].label_en,
                label_te=telugu_text(catalog[key].label_te) or "",  # ADR-0036
                value=_format_value(key, raw[key]),
                source=value.source if value is not None else None,
                verified=bool(value and value.verified),
                provisional=bool(value and value.provisional),
            )
        )
        if raw[key] is None and key in spec.required:
            blockers.append(Blocker(code="missing_value", attribute_key=key))
        elif value is not None and value.provisional:
            warnings.append(PreviewWarning(code="provisional_value", attribute_key=key))
    for missing in (k for k in spec.required if k not in catalog):
        blockers.append(Blocker(code="missing_value", attribute_key=missing))
    blockers.extend(
        Blocker(
            code="dq_blocker",
            attribute_key=b.attribute_key,
            finding_id=b.finding_id,
            rule_id=b.rule_id,
        )
        for b in dq.open_blockers(session, student_id, spec.printed)
    )
    structure = _Structure.load(session)
    enrolments = students.enrolment_histories(session, [student_id]).get(student_id, [])
    record = _Record(
        spec=spec,
        certificate_type=certificate_type,
        student_id=student_id,
        student_status=profile.status,
        fields=fields,
        raw=raw,
        enrolments=enrolments,
        structure=structure,
        blockers=blockers,
        warnings=warnings,
    )
    if structure.current is None:
        blockers.append(Blocker(code="no_current_year"))
    if spec.needs_enrolment:
        if profile.status not in ISSUABLE_STATUSES:
            blockers.append(Blocker(code="student_not_active"))
        elif record.current_enrolment is None:
            blockers.append(Blocker(code="no_enrolment"))
    elif certificate_type in ("study", "conduct") and not enrolments:
        blockers.append(Blocker(code="no_enrolment"))
    return record


def _preview_out(record: _Record) -> CertificatePreview:
    current = record.current_enrolment or record.last_enrolment
    label = record.structure.class_labels(current.section_id)[0] if current else None
    year = record.structure.current
    return CertificatePreview(
        student_id=record.student_id,
        certificate_type=record.certificate_type,
        requires_approval=record.spec.requires_approval,
        fields=record.fields,
        class_label=label,
        academic_year_label=year.label if year is not None else None,
        blockers=record.blockers,
        warnings=record.warnings,
        can_issue=not record.blockers,
    )


def types_catalog() -> list[CertificateTypeOut]:
    """Certificate types with their inputs and labels (``config.yaml``)."""
    cfg = settings()
    out: list[CertificateTypeOut] = []
    for key, spec in cfg.types.items():
        inputs = [
            InputOut(
                key=name,
                kind=inp.kind,
                required=inp.required,
                max_length=inp.max_length,
                choices=[
                    ChoiceOut(
                        value=c,
                        label_en=cfg.choice_labels[c].en,
                        label_te=telugu_text(cfg.choice_labels[c].te) or "",
                    )
                    for c in inp.choices
                ],
            )
            for name, inp in spec.inputs.items()
        ]
        out.append(
            CertificateTypeOut(
                key=key,
                label_en=spec.label_en,
                label_te=telugu_text(spec.label_te) or "",  # empty while hidden (ADR-0036)
                requires_approval=spec.requires_approval,
                ends_enrolment=spec.ends_enrolment,
                printed=list(spec.printed),
                inputs=inputs,
            )
        )
    return out


def preview(
    session: Session, ctx: UserContext, student_id: uuid.UUID, certificate_type: str
) -> CertificatePreview:
    """What the certificate would print now, with blockers and warnings (US-1101 AC1-AC4;
    permission ``certificate.issue``; 404 outside scope). Nothing is written or audited."""
    if not _in_reach(_reach(session, ctx, [ISSUE]), student_id):
        raise NotFound("Student not found")
    return _preview_out(_record(session, ctx, student_id, certificate_type))


# --- inputs --------------------------------------------------------------------------------------


def _clean_input(inp: InputSpec, value: str, today: dt.date) -> tuple[str | None, str | None]:
    """One input: (clean value, None) or (None, error code)."""
    if contains_full_aadhaar(value):
        return None, AADHAAR_CODE
    if inp.kind == "choice":
        return (value, None) if value in inp.choices else (None, "invalid_choice")
    if inp.kind == "date":
        try:
            day = dt.date.fromisoformat(value)
        except ValueError:
            return None, "invalid_date"
        return (None, "date_in_future") if day > today else (day.isoformat(), None)
    if inp.max_length is not None and len(value) > inp.max_length:
        return None, "too_long"
    return value, None


def _clean_inputs(
    session: Session, spec: TypeSpec, raw: Mapping[str, str], record: _Record
) -> dict[str, str]:
    """The type's inputs, checked against ``config.yaml`` (field-level 422 codes)."""
    errors = [_error(f"inputs.{key}", "unknown_input") for key in raw if key not in spec.inputs]
    clean: dict[str, str] = {}
    today = _today(session)
    for key, inp in spec.inputs.items():
        value = (raw.get(key) or "").strip()
        if not value:
            if inp.required:
                errors.append(_error(f"inputs.{key}", "missing"))
            continue
        good, code = _clean_input(inp, value, today)
        if code is not None:
            key_msg = "errors.aadhaar_last4_only" if code == AADHAAR_CODE else None
            errors.append(_error(f"inputs.{key}", code, key_msg))
        elif good is not None:
            clean[key] = good
    leaving = clean.get("leaving_date")
    if leaving is not None:
        day = dt.date.fromisoformat(leaving)
        admitted = record.raw.get("admission_date")
        starts = [e.started_on for e in record.active_enrolments if e.started_on is not None]
        if (admitted is not None and day < dt.date.fromisoformat(admitted)) or any(
            day < s for s in starts
        ):
            errors.append(_error("inputs.leaving_date", "leaving_date_before_enrolment"))
    if errors:
        raise ValidationFailed(errors)
    return clean


# --- content (frozen at issue) -------------------------------------------------------------------


def _letterhead(session: Session) -> dict[str, str]:
    tenant = tenancy.get_tenant(session)
    head = tenant.settings.certificate_letterhead
    return {
        "school_name_en": tenant.name,
        "school_name_te": head.school_name_te,
        "school_address_en": head.address_en,
        "school_address_te": head.address_te,
        "school_affiliation": head.affiliation,
        "school_place": head.place,
    }


def _both(en: str, te: str) -> str:
    """``English / Telugu``; English only while Telugu is hidden (ADR-0036)."""
    return en if not te or te == en or not telugu_enabled() else f"{en} / {te}"


def _class_and_year(record: _Record, enrolment: EnrollmentOut | None) -> tuple[str, str]:
    if enrolment is None:
        return ("—", "—")
    en, te = record.structure.class_labels(enrolment.section_id)
    year = record.structure.year_label(enrolment.academic_year_id)
    return (f"{en} ({year})", f"{te} ({year})")


def _details(
    record: _Record, inputs: Mapping[str, str], cfg: CertificatesConfig
) -> list[ContentLine]:
    labels = cfg.detail_labels
    lines: list[ContentLine] = []

    def add(key: str, value: str | None) -> None:
        label = labels.get(key)
        lines.append(
            ContentLine(
                key=key,
                label_en=label.en if label else key,
                label_te=label.te if label else key,
                value=value,
            )
        )

    first = record.enrolments[0] if record.enrolments else None
    last = record.last_enrolment
    first_en, first_te = _class_and_year(record, first)
    last_en, last_te = _class_and_year(record, last)
    if record.certificate_type == "transfer":
        add("admission_class", _both(first_en, first_te) if first else None)
        add("class_at_leaving", _both(last_en, last_te) if last else None)
        dob = record.raw.get("dob")
        add("dob_in_words", templates.date_in_words(dt.date.fromisoformat(dob)) if dob else None)
        add("leaving_date", templates.format_date(dt.date.fromisoformat(inputs["leaving_date"])))
        for key in ("leaving_reason", "promotion", "conduct"):
            choice = cfg.choice_labels[inputs[key]]
            add(key, _both(choice.en, choice.te))
        add("remarks", inputs.get("remarks") or "—")
        return lines
    studying = last is not None and last.status == LIVE_ENROLMENT
    add("study_from", first_en)
    add("study_to", f"{last_en}, still studying" if studying else last_en)
    lines.append(ContentLine(key="study_from_te", label_en="", label_te="", value=first_te))
    lines.append(
        ContentLine(
            key="study_to_te",
            label_en="",
            label_te="",
            value=f"{last_te}, ప్రస్తుతం చదువుతున్నారు" if studying else last_te,
        )
    )
    if record.certificate_type == "bonafide":
        purpose = cfg.choice_labels[inputs["purpose"]]
        note = inputs.get("purpose_note")
        add("purpose", f"{purpose.en} ({note})" if note else purpose.en)
        lines.append(
            ContentLine(
                key="purpose_te",
                label_en="",
                label_te="",
                value=f"{purpose.te} ({note})" if note else purpose.te,
            )
        )
    elif record.certificate_type == "conduct":
        conduct = cfg.choice_labels[inputs["conduct"]]
        add("conduct", conduct.en.lower())
        lines.append(ContentLine(key="conduct_te", label_en="", label_te="", value=conduct.te))
    elif inputs.get("purpose_note"):
        add("purpose_note", inputs["purpose_note"])
    return lines


def _build_content(
    session: Session,
    record: _Record,
    inputs: Mapping[str, str],
    *,
    serial: str,
    year: AcademicYearOut,
    issued_on: dt.date,
) -> CertificateContent:
    cfg = settings()
    spec = record.spec
    current = record.current_enrolment or record.last_enrolment
    class_en, class_te = (
        record.structure.class_labels(current.section_id) if current else (None, None)
    )
    fields = [
        ContentLine(key=f.key, label_en=f.label_en, label_te=f.label_te, value=f.value)
        for f in record.fields
    ]
    blanks = [
        ContentLine(
            key=key,
            label_en=cfg.blank_labels[key].en,
            label_te=cfg.blank_labels[key].te,
            value=None,
        )
        for key in spec.official_format_todo
    ]
    content = CertificateContent(
        certificate_type=record.certificate_type,
        title_en=spec.label_en,
        title_te=spec.label_te,
        serial=serial,
        academic_year_label=year.label,
        issued_on=issued_on,
        student_name=record.raw.get("full_name") or "—",
        admission_no=record.raw.get("admission_no") or "—",
        class_label_en=class_en,
        class_label_te=class_te,
        fields=fields,
        details=_details(record, inputs, cfg),
        blanks=blanks,
        **_letterhead(session),
    )
    # English first (ADR-0036): a certificate issued while Telugu is hidden is English only,
    # and that is what is frozen and hashed.
    return templates.shown(content)


def _content_json(content: CertificateContent) -> tuple[dict[str, Any], bytes]:
    data: dict[str, Any] = content.model_dump(mode="json")
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return data, hashlib.sha256(canonical.encode("utf-8")).digest()


def _content_of(row: Certificate) -> CertificateContent | None:
    return CertificateContent.model_validate(row.content) if row.content is not None else None


# --- outputs -------------------------------------------------------------------------------------


def _names(
    session: Session, rows: Sequence[Certificate]
) -> dict[uuid.UUID, tuple[str | None, str | None]]:
    """(name, admission number) per student: the frozen content for issued certificates, the
    current canonical values for requests (C2)."""
    out: dict[uuid.UUID, tuple[str | None, str | None]] = {}
    wanted = {r.student_id for r in rows if r.content is None}
    if wanted:
        values = students.canonical_values(session, wanted, ["full_name", "admission_no"])
        for sid, per in values.items():
            name = per.get("full_name")
            adm = per.get("admission_no")
            out[sid] = (name.value if name else None, adm.value if adm else None)
    return out


def _out(
    ctx: UserContext,
    row: Certificate,
    names: Mapping[uuid.UUID, tuple[str | None, str | None]],
) -> CertificateOut:
    spec = settings().spec(row.certificate_type)
    content = _content_of(row)
    pending = row.status == "pending"
    mine = row.requested_by == ctx.membership_id
    name: str | None
    adm: str | None
    if content is not None:
        name, adm = content.student_name, content.admission_no
    else:
        name, adm = names.get(row.student_id, (None, None))
    return CertificateOut(
        id=row.id,
        student_id=row.student_id,
        certificate_type=row.certificate_type,
        status=row.status,
        requires_approval=spec.requires_approval,
        inputs={str(k): str(v) for k, v in (row.inputs or {}).items()},
        original_certificate_id=row.original_certificate_id,
        duplicate_no=row.duplicate_no,
        duplicate_reason=row.duplicate_reason,
        academic_year_id=row.academic_year_id,
        serial=content.serial if content is not None else None,
        student_name=name,
        admission_no=adm,
        # The frozen record is kept whole; its Telugu is not shown while hidden (ADR-0036).
        content=templates.shown(content) if content is not None else None,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_note=row.decision_note,
        issued_by=row.issued_by,
        issued_at=row.issued_at,
        cancelled_by=row.cancelled_by,
        cancelled_at=row.cancelled_at,
        cancel_reason=row.cancel_reason,
        document_id=row.document_id,
        pdf_status=row.pdf_status,
        version=row.version,
        can_approve=pending and spec.requires_approval and ctx.has(APPROVE) and not mine,
        can_withdraw=pending and mine and ctx.has(ISSUE),
        can_cancel=row.status == "issued" and ctx.has(APPROVE),
        can_duplicate=row.status == "issued" and ctx.has(ISSUE),
    )


def _one(session: Session, ctx: UserContext, row: Certificate) -> CertificateOut:
    return _out(ctx, row, _names(session, [row]))


# --- request, issue ------------------------------------------------------------------------------


def _issue(
    session: Session,
    ctx: UserContext,
    row: Certificate,
    *,
    record: _Record | None,
    original: Certificate | None,
    note: str | None = None,
) -> Certificate:
    """Issue ``row`` in the caller's transaction (FR-CERT-003/005/006/007): serial number (or
    copy number), frozen content, register entry, TC withdrawal, audit, PDF queued."""
    cfg = settings()
    spec = cfg.spec(row.certificate_type)
    structure = record.structure if record is not None else _Structure.load(session)
    year = structure.current
    if year is None:
        raise NoCurrentYear()
    now = _now(session)
    values: dict[str, Any] = {
        "status": "issued",
        "academic_year_id": year.id,
        "template_version": cfg.template_version,
        "issued_by": ctx.membership_id,
        "issued_at": now,
        "pdf_status": "queued",
    }
    if spec.requires_approval:
        values.update(decided_by=ctx.membership_id, decided_at=now, decision_note=note)
    if original is not None:
        # A duplicate is a copy of what the original printed (FR-CERT-007).
        if original.content is None or original.content_sha256 is None:
            raise CertificateNotIssued()
        values.update(
            content=original.content,
            content_sha256=original.content_sha256,
            duplicate_no=repo.next_duplicate_no(session, original.id),
        )
        serial = str(original.content.get("serial", ""))
    else:
        if record is None:  # pragma: no cover - originals always carry their record
            raise RuntimeError("an original certificate needs the student record")
        number = repo.allocate_serial(session, row.certificate_type, year.id)
        serial = format_serial(cfg.serial, spec.prefix, year.label, number)
        content = _build_content(
            session,
            record,
            {str(k): str(v) for k, v in (row.inputs or {}).items()},
            serial=serial,
            year=year,
            issued_on=now.astimezone(IST).date(),
        )
        data, digest = _content_json(content)
        values.update(serial_no=number, serial=serial, content=data, content_sha256=digest)
    with _db_errors():
        issued = repo.update_certificate(session, row.id, values)
    withdrawn = 0
    if spec.ends_enrolment and original is None:
        leaving = dt.date.fromisoformat(str((row.inputs or {})["leaving_date"]))
        result = students.withdraw_for_transfer_certificate(
            session, row.student_id, left_on=leaving, certificate_id=row.id
        )
        withdrawn = len(result.enrollment_ids)
    _audit(
        session,
        action="certificate.issued",
        resource_id=issued.id,
        summary={
            "student_id": issued.student_id,
            "certificate_type": issued.certificate_type,
            "serial": serial,
            "academic_year_id": year.id,
            "duplicate_of": issued.original_certificate_id,
            "duplicate_no": issued.duplicate_no,
            "approved": spec.requires_approval,
            "requested_by": issued.requested_by,
            "enrollments_ended": withdrawn,
        },
    )
    ops.enqueue_event(
        session, RENDER_EVENT, {"certificate_id": str(issued.id), "user_id": str(ctx.user_id)}
    )
    log.info("certificate.issued", resource_type="certificate", resource_id=issued.id)
    return issued


def request_certificate(
    session: Session, ctx: UserContext, student_id: uuid.UUID, data: CertificateRequest
) -> CertificateOut:
    """Prepare (TC: waits for approval) or issue (other types) a certificate (US-1101,
    US-1102; permission ``certificate.issue``).

    Errors: 404 student outside scope; 422 inputs; 409 ``certificate_blocked``,
    ``transfer_certificate_exists``, ``no_current_academic_year``. Audit
    ``certificate.requested`` (+ ``certificate.issued``); notification to approvers."""
    if not _in_reach(_reach(session, ctx, [ISSUE]), student_id):
        raise NotFound("Student not found")
    record = _record(session, ctx, student_id, data.certificate_type)
    spec = record.spec
    inputs = _clean_inputs(session, spec, data.inputs, record)
    if record.blockers:
        if any(b.code == "no_current_year" for b in record.blockers):
            raise NoCurrentYear()
        raise CertificateBlocked()
    with _db_errors():
        row = repo.insert_certificate(
            session,
            id=new_id(),
            tenant_id=repo.current_tenant_id(session),
            student_id=student_id,
            certificate_type=data.certificate_type,
            status="pending",
            inputs=inputs,
            requested_by=ctx.membership_id,
            requested_at=_now(session),
        )
    _audit(
        session,
        action="certificate.requested",
        resource_id=row.id,
        summary={
            "student_id": student_id,
            "certificate_type": data.certificate_type,
            "needs_approval": spec.requires_approval,
            "input_keys": sorted(inputs),
        },
    )
    if spec.requires_approval:
        _notify(
            session,
            row,
            "certificate.approval_requested",
            notifications.PermissionSelector(APPROVE),
        )
        return _one(session, ctx, row)
    return _one(session, ctx, _issue(session, ctx, row, record=record, original=None))


def request_duplicate(
    session: Session, ctx: UserContext, certificate_id: uuid.UUID, data: DuplicateRequest
) -> CertificateOut:
    """A duplicate of an issued certificate (US-1104; permission ``certificate.issue``): a copy
    of the original's printed values marked DUPLICATE, with a reason; TC duplicates wait for
    approval. Errors: 404; 409 ``certificate_not_issued`` / ``duplicate_pending``; 422 reason.
    Audit ``certificate.duplicate_requested`` (+ ``certificate.issued``)."""
    reason = _clean_reason("reason", data.reason, required=True)
    source = _load(session, ctx, certificate_id, permissions=[ISSUE])
    original_id = source.original_certificate_id or source.id
    original = repo.get_certificate(session, original_id, lock=True)
    if original is None or original.status != "issued" or source.status != "issued":
        raise CertificateNotIssued()
    spec = settings().spec(original.certificate_type)
    with _db_errors():
        row = repo.insert_certificate(
            session,
            id=new_id(),
            tenant_id=repo.current_tenant_id(session),
            student_id=original.student_id,
            certificate_type=original.certificate_type,
            status="pending",
            inputs={},
            original_certificate_id=original.id,
            duplicate_reason=reason,
            requested_by=ctx.membership_id,
            requested_at=_now(session),
        )
    _audit(
        session,
        action="certificate.duplicate_requested",
        resource_id=row.id,
        summary={
            "student_id": row.student_id,
            "certificate_type": row.certificate_type,
            "original_certificate_id": original.id,
            "needs_approval": spec.requires_approval,
        },
    )
    if spec.requires_approval:
        _notify(
            session,
            row,
            "certificate.approval_requested",
            notifications.PermissionSelector(APPROVE),
        )
        return _one(session, ctx, row)
    return _one(session, ctx, _issue(session, ctx, row, record=None, original=original))


# --- decisions -----------------------------------------------------------------------------------


def _pending(row: Certificate) -> None:
    if row.status != "pending":
        raise CertificateNotPending()


def approve(
    session: Session,
    ctx: UserContext,
    certificate_id: uuid.UUID,
    data: ApproveIn,
    *,
    expected_version: int,
) -> CertificateOut:
    """Approve and issue a certificate that needs approval (US-1102 AC2-AC3; permission
    ``certificate.approve``, MFA within 5 minutes, not the requester, ``If-Match``). Blockers
    are checked again. Errors: 404; 403 ``self_approval_forbidden``; 428; 412; 409
    ``certificate_not_pending`` / ``certificate_blocked``."""
    row = _load(session, ctx, certificate_id, permissions=[APPROVE], lock=True)
    if row.requested_by == ctx.membership_id:
        raise SelfApprovalForbidden()
    _require_recent_mfa(ctx)
    _check_version(row, expected_version)
    _pending(row)
    note = _clean_reason("note", data.note, required=False)
    original: Certificate | None = None
    record: _Record | None = None
    if row.original_certificate_id is not None:
        original = repo.get_certificate(session, row.original_certificate_id, lock=True)
        if original is None or original.status != "issued":
            raise CertificateNotIssued()
    else:
        record = _record(session, ctx, row.student_id, row.certificate_type)
        if record.blockers:
            if any(b.code == "no_current_year" for b in record.blockers):
                raise NoCurrentYear()
            raise CertificateBlocked()
        # The student may have left or been re-enrolled since the request: check again.
        _clean_inputs(session, record.spec, dict(row.inputs or {}), record)
    issued = _issue(session, ctx, row, record=record, original=original, note=note)
    _notify(session, issued, "certificate.approved", [issued.requested_by])
    return _one(session, ctx, issued)


def reject(
    session: Session,
    ctx: UserContext,
    certificate_id: uuid.UUID,
    data: ReasonIn,
    *,
    expected_version: int,
) -> CertificateOut:
    """Reject a request with a reason the requester sees (permission ``certificate.approve``,
    step-up, not the requester, ``If-Match``). Audit ``certificate.rejected``; notification."""
    row = _load(session, ctx, certificate_id, permissions=[APPROVE], lock=True)
    if row.requested_by == ctx.membership_id:
        raise SelfApprovalForbidden()
    _require_recent_mfa(ctx)
    _check_version(row, expected_version)
    _pending(row)
    reason = _clean_reason("reason", data.reason, required=True)
    with _db_errors():
        row = repo.update_certificate(
            session,
            row.id,
            {
                "status": "rejected",
                "decided_by": ctx.membership_id,
                "decided_at": _now(session),
                "decision_note": reason,
            },
        )
    _audit(
        session,
        action="certificate.rejected",
        resource_id=row.id,
        summary={"student_id": row.student_id, "certificate_type": row.certificate_type},
    )
    _notify(session, row, "certificate.rejected", [row.requested_by])
    log.info("certificate.rejected", resource_type="certificate", resource_id=row.id)
    return _one(session, ctx, row)


def withdraw(
    session: Session, ctx: UserContext, certificate_id: uuid.UUID, *, expected_version: int
) -> CertificateOut:
    """The requester withdraws a pending request (permission ``certificate.issue``,
    ``If-Match``). Audit ``certificate.withdrawn``."""
    row = _load(session, ctx, certificate_id, permissions=[ISSUE], lock=True)
    if row.requested_by != ctx.membership_id:
        raise NotRequester()
    _check_version(row, expected_version)
    _pending(row)
    with _db_errors():
        row = repo.update_certificate(session, row.id, {"status": "withdrawn"})
    _audit(
        session,
        action="certificate.withdrawn",
        resource_id=row.id,
        summary={"student_id": row.student_id, "certificate_type": row.certificate_type},
    )
    return _one(session, ctx, row)


def cancel(
    session: Session,
    ctx: UserContext,
    certificate_id: uuid.UUID,
    data: ReasonIn,
    *,
    expected_version: int,
) -> CertificateOut:
    """Cancel an issued certificate (US-1105; permission ``certificate.approve``, step-up,
    ``If-Match``, reason). It keeps its number and stays in the register as cancelled; its
    document is archived; a TC does not re-enrol the student. Audit ``certificate.cancelled``."""
    row = _load(session, ctx, certificate_id, permissions=[APPROVE], lock=True)
    _require_recent_mfa(ctx)
    _check_version(row, expected_version)
    if row.status != "issued":
        raise CertificateNotIssued()
    reason = _clean_reason("reason", data.reason, required=True)
    with _db_errors():
        row = repo.update_certificate(
            session,
            row.id,
            {
                "status": "cancelled",
                "cancelled_by": ctx.membership_id,
                "cancelled_at": _now(session),
                "cancel_reason": reason,
            },
        )
    archived = (
        documents.archive_generated_document(session, row.document_id)
        if row.document_id is not None
        else False
    )
    _audit(
        session,
        action="certificate.cancelled",
        resource_id=row.id,
        summary={
            "student_id": row.student_id,
            "certificate_type": row.certificate_type,
            "serial": row.serial,
            "duplicate_no": row.duplicate_no,
            "document_archived": archived,
        },
    )
    log.info("certificate.cancelled", resource_type="certificate", resource_id=row.id)
    return _one(session, ctx, row)


# --- reads ---------------------------------------------------------------------------------------

_READERS: Final = (READ, ISSUE, APPROVE)


def _after(cursor: str | None) -> tuple[dt.datetime, uuid.UUID] | None:
    raw = decode_cursor(cursor)
    if raw is None:
        return None
    try:
        at = dt.datetime.fromisoformat(str(raw["t"]))
        last = uuid.UUID(str(raw["i"]))
    except (KeyError, ValueError):
        raise ValidationFailed([_error("cursor", "invalid", "errors.invalid_cursor")]) from None
    if at.tzinfo is None:
        raise ValidationFailed([_error("cursor", "invalid", "errors.invalid_cursor")])
    return at, last


def list_certificates(
    session: Session,
    ctx: UserContext,
    *,
    student_id: uuid.UUID | None = None,
    certificate_type: str | None = None,
    status: str | None = None,
    academic_year_id: uuid.UUID | None = None,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[CertificateOut]:
    """Certificates of students the caller reaches, newest first (read, issue or approve)."""
    reach = _reach(session, ctx, _READERS)
    rows = repo.list_certificates(
        session,
        student_ids=reach,
        student_id=student_id,
        certificate_type=certificate_type,
        status=status,
        academic_year_id=academic_year_id,
        after=_after(cursor),
        limit=limit + 1,
    )
    page, more = rows[:limit], len(rows) > limit
    names = _names(session, page)
    next_cursor = (
        encode_cursor({"t": page[-1].requested_at.isoformat(), "i": str(page[-1].id)})
        if more and page
        else None
    )
    return Page[CertificateOut](data=[_out(ctx, r, names) for r in page], next_cursor=next_cursor)


def get_certificate(
    session: Session, ctx: UserContext, certificate_id: uuid.UUID
) -> CertificateOut:
    """One certificate (read, issue or approve permission; 404 outside scope or school)."""
    return _one(session, ctx, _load(session, ctx, certificate_id, permissions=_READERS))


def _reference(row: Certificate) -> str:
    digest = row.content_sha256.hex() if row.content_sha256 is not None else row.id.hex
    return digest[:10].upper()


def _duplicate_mark(row: Certificate) -> templates.DuplicateMark | None:
    if row.original_certificate_id is None:
        return None
    issued = row.issued_at or dt.datetime.now(dt.UTC)
    return templates.DuplicateMark(
        copy_no=row.duplicate_no or 0, issued_on=issued.astimezone(IST).date()
    )


def _void(session: Session, row: Certificate) -> bool:
    """Cancelled, or a duplicate whose original was cancelled (its copies are void with it;
    audit 2026-10-05 A-07)."""
    if row.status == "cancelled":
        return True
    if row.original_certificate_id is None:
        return False
    original = repo.get_certificate(session, row.original_certificate_id)
    return original is not None and original.status == "cancelled"


def _page(session: Session, ctx: UserContext, row: Certificate, *, for_pdf: bool) -> str:
    """The certificate page: frozen content once issued; a DRAFT built from the record now
    while the request waits for approval."""
    if row.status in ("rejected", "withdrawn"):
        raise Conflict("This request was closed and has nothing to print.", code="not_printable")
    mark: templates.Mark = None
    if _void(session, row):
        mark = "cancelled"
    elif row.status == "pending":
        mark = "draft"
    content = _content_of(row)
    if content is None and row.original_certificate_id is not None:
        original = repo.get_certificate(session, row.original_certificate_id)
        content = _content_of(original) if original is not None else None
    if content is None:
        record = _record(session, ctx, row.student_id, row.certificate_type)
        year = record.structure.current
        if year is None:
            raise NoCurrentYear()
        content = _build_content(
            session,
            record,
            {str(k): str(v) for k, v in (row.inputs or {}).items()},
            serial="—",
            year=year,
            issued_on=_today(session),
        )
    return templates.render_certificate(
        content,
        reference=_reference(row),
        mark=mark,
        duplicate=_duplicate_mark(row),
        for_pdf=for_pdf,
    )


def print_page(session: Session, ctx: UserContext, certificate_id: uuid.UUID) -> str:
    """The print view (HTML) of a certificate (FR-CERT-011; read, issue or approve permission).
    Audit ``certificate.print_viewed``."""
    row = _load(session, ctx, certificate_id, permissions=_READERS)
    page = _page(session, ctx, row, for_pdf=False)
    _audit(
        session,
        action="certificate.print_viewed",
        resource_id=row.id,
        summary={"student_id": row.student_id, "status": row.status},
    )
    return page


def download_url(session: Session, ctx: UserContext, certificate_id: uuid.UUID) -> DownloadUrlOut:
    """A presigned PDF download (≤ 5 minutes, attachment) once the PDF is stored and scanned
    (FR-CERT-011; permission ``certificate.read``). 409 ``pdf_not_ready`` before; audit
    ``certificate.downloaded``. A cancelled certificate (or a duplicate of a cancelled original)
    answers 409 ``certificate_cancelled``: its stored PDF was made before the cancellation and
    carries no mark; the print view shows it marked CANCELLED (audit 2026-10-05 A-07)."""
    row = _load(session, ctx, certificate_id, permissions=[READ])
    if _void(session, row):
        raise Conflict(
            "This certificate was cancelled. Open it to print it marked CANCELLED.",
            code="certificate_cancelled",
        )
    if row.document_id is None:
        raise Conflict("The PDF is still being made. Try again in a moment.", code="pdf_not_ready")
    name = _FILENAME_RE.sub("-", f"{row.certificate_type}-{row.serial or row.id.hex[:8]}")
    if row.duplicate_no:
        name += f"-duplicate-{row.duplicate_no}"
    filename = f"{name}.pdf"
    url, expires_at = documents.generated_download_url(
        session, row.document_id, filename=filename, ttl_s=settings().download_url_ttl_s
    )
    _audit(
        session,
        action="certificate.downloaded",
        resource_id=row.id,
        summary={"student_id": row.student_id, "document_id": row.document_id},
    )
    return DownloadUrlOut(url=url, expires_at=expires_at, filename=filename)


def retry_render(session: Session, ctx: UserContext, certificate_id: uuid.UUID) -> CertificateOut:
    """Queue the PDF again after it failed (permission ``certificate.issue``). Idempotent for a
    PDF that is queued or ready."""
    row = _load(session, ctx, certificate_id, permissions=[ISSUE], lock=True)
    if row.status not in ("issued", "cancelled"):
        raise CertificateNotIssued()
    if row.pdf_status == "failed":
        with _db_errors():
            row = repo.update_certificate(
                session, row.id, {"pdf_status": "queued", "pdf_error": None}
            )
        ops.enqueue_event(
            session, RENDER_EVENT, {"certificate_id": str(row.id), "user_id": str(ctx.user_id)}
        )
        _audit(
            session,
            action="certificate.render_requested",
            resource_id=row.id,
            summary={"student_id": row.student_id},
        )
    return _one(session, ctx, row)


# --- PDF (worker, queue "pdf") -------------------------------------------------------------------


def render_pdf(
    tenant_id: uuid.UUID,
    certificate_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    renderer: PdfRenderer | None = None,
) -> str:
    """Worker: print an issued certificate to PDF and store it as a document (FR-CERT-010).
    Idempotent (a stored PDF is left alone). Returns the PDF status. Errors propagate for a
    retry; after the last one the task calls :func:`mark_pdf_failed`."""
    with tenant_session(tenant_id, user_id) as session:
        row = repo.get_certificate(session, certificate_id)
        if row is None:
            return "missing"
        if row.document_id is not None or row.content is None:
            return row.pdf_status
        content = _content_of(row)
        if content is None:  # pragma: no cover - checked above
            return row.pdf_status
        page = templates.render_certificate(
            content,
            reference=_reference(row),
            mark="cancelled" if row.status == "cancelled" else None,
            duplicate=_duplicate_mark(row),
            for_pdf=True,
        )
        title = f"{content.title_en} {content.serial}"
        if row.duplicate_no:
            title += f" (duplicate {row.duplicate_no})"
        issued_on = content.issued_on
        year_id = row.academic_year_id
    pdf = (renderer or get_renderer()).render(page)
    with tenant_session(tenant_id, user_id) as session:
        row = repo.get_certificate(session, certificate_id, lock=True)
        if row is None:
            return "missing"
        if row.document_id is not None:
            return row.pdf_status
        document_id = documents.store_generated_document(
            session,
            purpose=DOCUMENT_PURPOSE,
            title=title[:200],
            content=pdf,
            created_by=user_id,
            acl_roles=settings().document_acl_roles,
            issued_on=issued_on,
            academic_year_id=year_id,
            # Bilingual while Telugu is shown, English otherwise (ADR-0036).
            language="mixed" if telugu_enabled() else "en",
        )
        with _db_errors():
            row = repo.update_certificate(
                session,
                row.id,
                {"document_id": document_id, "pdf_status": "ready", "pdf_error": None},
            )
        if row.status == "cancelled":
            documents.archive_generated_document(session, document_id)
        _audit(
            session,
            action="certificate.pdf_stored",
            resource_id=row.id,
            summary={"document_id": document_id, "size_bytes": len(pdf)},
            system=True,
        )
    log.info("certificate.pdf_stored", resource_type="certificate", resource_id=certificate_id)
    return "ready"


def mark_pdf_failed(tenant_id: uuid.UUID, certificate_id: uuid.UUID, code: str) -> None:
    """After the last retry: the certificate shows the PDF failed (retry from the screen)."""
    with tenant_session(tenant_id) as session:
        row = repo.get_certificate(session, certificate_id, lock=True)
        if row is None or row.document_id is not None:
            return
        repo.update_certificate(session, row.id, {"pdf_status": "failed", "pdf_error": code})
        _audit(
            session,
            action="certificate.pdf_failed",
            resource_id=row.id,
            summary={"error_code": code},
            system=True,
        )


# --- registers (FR-REG-001..004) -----------------------------------------------------------------


def _register_year(session: Session, academic_year_id: uuid.UUID | None) -> AcademicYearOut:
    if academic_year_id is not None:
        return tenancy.get_academic_year(session, academic_year_id)
    year = tenancy.get_current_academic_year(session)
    if year is None:
        raise NoCurrentYear()
    return year


def _too_many(count: int) -> None:
    if count > settings().register_max_rows:
        raise ValidationFailed(
            [_error("academic_year_id", "too_many_rows")],
            detail="This register has too many rows to print at once.",
        )


def _choice_en(key: str | None) -> str:
    if not key:
        return ""
    label = settings().choice_labels.get(key)
    return label.en if label is not None else key


def _status_text(row: Certificate, original: Certificate | None) -> str:
    parts: list[str] = []
    if row.original_certificate_id is not None:
        parts.append(f"Duplicate {row.duplicate_no}: {row.duplicate_reason or ''}".strip())
    if row.status == "cancelled":
        when = row.cancelled_at.astimezone(IST).date() if row.cancelled_at else None
        parts.append(f"CANCELLED {templates.format_date(when)}: {row.cancel_reason or ''}")
    elif original is not None and original.status == "cancelled":
        parts.append("Original cancelled")
    return " · ".join(parts) or "Issued"


def _register_types(kind: str, certificate_type: str | None) -> tuple[str, ...]:
    if kind == "transfer":
        return ("transfer",)
    others = tuple(t for t in settings().types if t != "transfer")
    if certificate_type is not None and certificate_type not in others:
        raise ValidationFailed([_error("certificate_type", "invalid_choice")])
    return (certificate_type,) if certificate_type else others


def check_register(
    session: Session,
    ctx: UserContext,
    *,
    kind: str,
    academic_year_id: uuid.UUID | None,
    certificate_type: str | None = None,
) -> None:
    """Whether a register can be printed now, without printing it (the registers screen asks
    before it links to the print view): the year (404 / 409 ``no_current_year``), the type
    (422) and the size (422 ``too_many_rows``) are checked like the view checks them; the
    caller's guard has already checked ``register.read`` and step-up. Not a view: nothing is
    rendered and nothing audited (the print view itself is audited, once)."""
    year = _register_year(session, academic_year_id)
    if kind == "admission":
        _too_many(len(_admission_students(session, ctx, year.id)[0]))
        return
    limit = settings().register_max_rows
    rows = repo.register_entries(
        session, _register_types(kind, certificate_type), year.id, limit=limit + 1
    )
    _too_many(len(rows))


def register_page(
    session: Session,
    ctx: UserContext,
    *,
    kind: str,
    academic_year_id: uuid.UUID | None,
    certificate_type: str | None = None,
) -> str:
    """The TC register (``kind=transfer``, FR-REG-001) or the certificate issue register
    (``kind=certificates``, FR-REG-002) of an academic year (default: the current one), as an A4
    landscape print page (``register.read``, step-up, school-wide). Audit ``register.viewed``."""
    year = _register_year(session, academic_year_id)
    types = _register_types(kind, certificate_type)
    limit = settings().register_max_rows
    rows = repo.register_entries(session, types, year.id, limit=limit + 1)
    _too_many(len(rows))
    originals = repo.get_many(
        session, {r.original_certificate_id for r in rows if r.original_certificate_id}
    )
    people = identity.member_display_names(
        session, {m for r in rows for m in (r.issued_by, r.decided_by, r.requested_by) if m}
    )
    head = _letterhead(session)
    lines: list[list[str]] = []
    cancelled: list[bool] = []
    for row in rows:
        content = _content_of(row)
        if content is None:  # pragma: no cover - issued rows always have content
            continue
        original = (
            originals.get(row.original_certificate_id) if row.original_certificate_id else None
        )
        issued_on = row.issued_at.astimezone(IST).date() if row.issued_at else None
        serial = content.serial + (f" (D{row.duplicate_no})" if row.duplicate_no else "")
        father = next((f.value for f in content.fields if f.key == "father_name"), "") or ""
        issued_by = people.get(row.issued_by, "—") if row.issued_by else "—"
        if kind == "transfer":
            details = {d.key: d.value or "" for d in content.details}
            inputs = (original.inputs if original is not None else row.inputs) or {}
            approved = people.get(row.decided_by, "—") if row.decided_by else "—"
            lines.append(
                [
                    serial,
                    templates.format_date(issued_on),
                    content.admission_no,
                    content.student_name,
                    father,
                    details.get("class_at_leaving", ""),
                    details.get("leaving_date", ""),
                    _choice_en(str(inputs.get("leaving_reason", ""))),
                    _choice_en(str(inputs.get("conduct", ""))),
                    people.get(row.requested_by, "—"),
                    approved,
                    _status_text(row, original),
                ]
            )
        else:
            details = {d.key: d.value or "" for d in content.details}
            lines.append(
                [
                    serial,
                    content.title_en,
                    templates.format_date(issued_on),
                    content.admission_no,
                    content.student_name,
                    father,
                    content.class_label_en or "",
                    details.get("purpose") or details.get("conduct") or "",
                    issued_by,
                    _status_text(row, original),
                ]
            )
        cancelled.append(row.status == "cancelled")
    if kind == "transfer":
        header = [
            ("Serial no.", "క్రమ సంఖ్య"),
            ("Date of issue", "జారీ తేదీ"),
            ("Admission no.", "ప్రవేశ సంఖ్య"),
            ("Name of the pupil", "విద్యార్థి పేరు"),
            ("Father's name", "తండ్రి పేరు"),
            ("Class at leaving", "విడిచిన తరగతి"),
            ("Date of leaving", "విడిచిన తేదీ"),
            ("Reason", "కారణం"),
            ("Conduct", "ప్రవర్తన"),
            ("Prepared by", "తయారు చేసినవారు"),
            ("Approved by", "ఆమోదించినవారు"),
            ("Remarks", "వ్యాఖ్యలు"),
        ]
        title = ("Transfer certificate register (counterfoil)", "బదిలీ ధృవీకరణ పత్రాల రిజిస్టర్")
    else:
        header = [
            ("Serial no.", "క్రమ సంఖ్య"),
            ("Certificate", "ధృవీకరణ పత్రం"),
            ("Date of issue", "జారీ తేదీ"),
            ("Admission no.", "ప్రవేశ సంఖ్య"),
            ("Name of the pupil", "విద్యార్థి పేరు"),
            ("Father's name", "తండ్రి పేరు"),
            ("Class", "తరగతి"),
            ("Purpose or conduct", "ప్రయోజనం లేదా ప్రవర్తన"),
            ("Issued by", "జారీ చేసినవారు"),
            ("Remarks", "వ్యాఖ్యలు"),
        ]
        title = ("Certificate issue register", "ధృవీకరణ పత్రాల జారీ రిజిస్టర్")
    page = templates.RegisterPage(
        title_en=title[0],
        title_te=title[1],
        school_name=head["school_name_en"],
        school_name_te=head["school_name_te"],
        academic_year_label=year.label,
        printed_at=_now(session),
        header=header,
        rows=lines,
        cancelled=cancelled,
        empty_en="No certificates were issued in this academic year.",
        empty_te="ఈ విద్యా సంవత్సరంలో ధృవీకరణ పత్రాలు జారీ కాలేదు.",
    )
    _audit(
        session,
        action="register.viewed",
        resource_id=year.id,
        resource_type="academic_year",
        summary={
            "register": kind,
            "certificate_type": certificate_type,
            "academic_year_id": year.id,
            "rows": len(lines),
        },
    )
    return templates.render_register(page)


AdmissionKey = tuple[int, tuple[tuple[int, int, str], ...]]


def _admission_sort_key(value: str | None) -> AdmissionKey:
    """Admission numbers in natural order (``A/9`` before ``A/10``, ``2024/15`` before
    ``2025/3``): every run of digits compares as a number, the text between runs as text;
    an empty number sorts last."""
    text = value or ""
    parts = tuple(
        (0, int(part), "") if part.isdecimal() else (1, 0, part)
        for part in re.split(r"(\d+)", text)
        if part
    )
    return (0 if text else 1, parts)


def _admission_students(
    session: Session, ctx: UserContext, year_id: uuid.UUID
) -> tuple[list[uuid.UUID], dict[uuid.UUID, list[EnrollmentOut]]]:
    """The students with an enrolment in the year, and every student's enrolment history."""
    student_ids = students.list_students_in_scope(session, ctx)
    histories = students.enrolment_histories(session, student_ids)
    in_year = [
        sid for sid, hist in histories.items() if any(e.academic_year_id == year_id for e in hist)
    ]
    return in_year, histories


def admission_register_page(
    session: Session, ctx: UserContext, *, academic_year_id: uuid.UUID | None
) -> str:
    """The admission and withdrawal register (FR-REG-003) for the students with an enrolment in
    an academic year (default: the current one), in admission-number order (``register.read``,
    step-up, school-wide). Audit ``register.viewed``."""
    year = _register_year(session, academic_year_id)
    structure = _Structure.load(session)
    in_year, histories = _admission_students(session, ctx, year.id)
    _too_many(len(in_year))
    values = students.canonical_values(session, in_year, REGISTER_KEYS)
    tcs = repo.transfer_certificates_of(session, in_year)
    head = _letterhead(session)

    def value(sid: uuid.UUID, key: str) -> str:
        v = values.get(sid, {}).get(key)
        return _format_value(key, v.value if v is not None else None) or ""

    def class_of(enrolment: EnrollmentOut | None) -> str:
        if enrolment is None:
            return ""
        en, _ = structure.class_labels(enrolment.section_id)
        return f"{en} ({structure.year_label(enrolment.academic_year_id)})"

    def order(sid: uuid.UUID) -> tuple[AdmissionKey, str]:
        adm = values.get(sid, {}).get("admission_no")
        return (_admission_sort_key(adm.value if adm is not None else None), str(sid))

    ordered = sorted(in_year, key=order)
    lines: list[list[str]] = []
    for sid in ordered:
        hist = histories.get(sid, [])
        first = hist[0] if hist else None
        tc = tcs.get(sid)
        ended = [e for e in hist if e.status != LIVE_ENROLMENT and e.ended_on is not None]
        last_left = (
            ended[-1] if ended and not any(e.status == LIVE_ENROLMENT for e in hist) else None
        )
        tc_inputs = (tc.inputs or {}) if tc is not None else {}
        leaving = (
            templates.format_date(dt.date.fromisoformat(str(tc_inputs["leaving_date"])))
            if "leaving_date" in tc_inputs
            else templates.format_date(last_left.ended_on if last_left else None)
        )
        tc_content = _content_of(tc) if tc is not None else None
        lines.append(
            [
                value(sid, "admission_no"),
                value(sid, "admission_date"),
                value(sid, "full_name"),
                value(sid, "father_name"),
                value(sid, "mother_name"),
                value(sid, "dob"),
                class_of(first),
                leaving,
                class_of(last_left) if last_left else "",
                tc_content.serial if tc_content is not None else "",
                _choice_en(str(tc_inputs.get("leaving_reason", ""))) if tc else "",
            ]
        )
    page = templates.RegisterPage(
        title_en="Admission and withdrawal register",
        title_te="ప్రవేశ, నిష్క్రమణ రిజిస్టర్",
        school_name=head["school_name_en"],
        school_name_te=head["school_name_te"],
        academic_year_label=year.label,
        printed_at=_now(session),
        header=[
            ("Admission no.", "ప్రవేశ సంఖ్య"),
            ("Date of admission", "ప్రవేశ తేదీ"),
            ("Name of the pupil", "విద్యార్థి పేరు"),
            ("Father's name", "తండ్రి పేరు"),
            ("Mother's name", "తల్లి పేరు"),
            ("Date of birth", "పుట్టిన తేదీ"),
            ("Class admitted to", "ప్రవేశించిన తరగతి"),
            ("Date of leaving", "విడిచిన తేదీ"),
            ("Class at leaving", "విడిచిన తరగతి"),
            ("TC serial no.", "టీసీ క్రమ సంఖ్య"),
            ("Reason for leaving", "విడిచిపెట్టడానికి కారణం"),
        ],
        rows=lines,
        cancelled=[False] * len(lines),
        empty_en="No students were enrolled in this academic year.",
        empty_te="ఈ విద్యా సంవత్సరంలో విద్యార్థులు నమోదు కాలేదు.",
    )
    _audit(
        session,
        action="register.viewed",
        resource_id=year.id,
        resource_type="academic_year",
        summary={
            "register": "admission_withdrawal",
            "academic_year_id": year.id,
            "rows": len(lines),
        },
    )
    return templates.render_register(page)


# --- full data export and offboarding ------------------------------------------------------------


def export_records(session: Session) -> list[RecordTable]:
    """Worker only: the certificate register and serial counters of the current school for its
    full data export (``app.admin``; the caller checked ``tenant.export_all`` and audits the
    export). Printed values are C2 only (FR-CERT-009)."""
    return repo.export_tables(session)


# Certificates and serial counters (FR-PLT-005, ADR-0029). Registered with app.tenancy at
# import; purged before students, documents and the academic structure they reference.
_PURGE = purging.PurgeTables(deleted=("sis.certificates", "sis.certificate_counters"))


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only: the database allows it
    only as ``sos_purger`` for a school in ``offboarding``)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="certificates", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "APPROVE",
    "ISSUE",
    "READ",
    "REGISTER",
    "RENDER_EVENT",
    "RENDER_TASK",
    "CertificateBlocked",
    "CertificateNotIssued",
    "CertificateNotPending",
    "NoCurrentYear",
    "NotRequester",
    "SelfApprovalForbidden",
    "admission_register_page",
    "approve",
    "cancel",
    "check_register",
    "download_url",
    "export_records",
    "get_certificate",
    "list_certificates",
    "mark_pdf_failed",
    "preview",
    "print_page",
    "purge_tenant_data",
    "register_page",
    "reject",
    "render_pdf",
    "request_certificate",
    "request_duplicate",
    "retry_render",
    "settings",
    "tenant_data_counts",
    "types_catalog",
    "withdraw",
]
