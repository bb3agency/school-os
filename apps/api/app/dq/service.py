"""Data-quality public API: runs, findings workflow, catalog and summary (US-501, US-502;
FR-DQ-001..006, FR-DQ-020; NFR-PERF-005).

Other modules call only these functions.

- **Runs.** :func:`request_run` (``POST /dq/runs``) checks small scopes inside the request and
  queues bigger ones for the worker (outbox ``dq.run.requested`` -> ``dq.execute_run`` on queue
  ``dq``; the requester gets the ``dq.run.completed`` notification). :func:`run_checks` is the
  synchronous core. Runs started by events (:func:`run_for_event`) re-check the students a write
  touched: ``student.values.changed``, ``import.committed``/``import.reverted``,
  ``extraction.confirmed`` and ``change_request.approved`` (outbox -> ``dq.run_incremental``).
  Every run is audited (``dq.run.completed``) with counts only.
- **Findings** are idempotent per fingerprint and reopen when a resolved conflict returns
  (:mod:`app.dq.engine`). Stored and returned values are masked; the current C2 value is added
  for display, C3 values never (they are revealed on the student record, with its own audit).
- **Scope (SEC-015).** A holder of a scoped grant (class teacher) sees only findings of students
  in their sections this year; anything else, including other schools' ids, is 404.
- **Workflow (FR-DQ-020).** Resolve needs a note or a change request; waive needs
  ``dq.findings.waive`` (route) and a reason, and step-up MFA for blockers (428). Both audited.
- **Change requests** are linked through outbox events (payload
  ``{change_request_id, student_id, attribute_key}``): ``submitted`` links the student's
  unresolved findings of that attribute, ``approved`` re-checks the student and resolves the
  findings the correction removed with resolution ``change_request``, ``rejected`` unlinks.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import Page, decode_cursor, encode_cursor
from app.changes import service as changes
from app.core import purge as purging
from app.core.config import get_settings
from app.core.db import tenant_session
from app.core.errors import (
    Conflict,
    NotFound,
    PreconditionFailed,
    StepUpRequired,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_context, get_logger
from app.core.redaction import contains_full_aadhaar
from app.dq import engine
from app.dq import repository as repo
from app.dq.explanations import Language, load_explanations
from app.dq.masking import FULL_MASK
from app.dq.models import ACTIVE_STATUSES
from app.dq.profiles import load_engine_config, load_profiles
from app.dq.rules import Severity, load_rules
from app.dq.schemas import (
    Bilingual,
    FindingFilters,
    FindingOut,
    FindingValue,
    ProfileOut,
    ResolveIn,
    RuleCount,
    RuleOut,
    RunCreate,
    RunOut,
    SeverityPolicyOut,
    StudentRef,
    SummaryOut,
    WaiveIn,
)
from app.identity.principal import STEP_UP_MAX_AGE
from app.notifications import service as notifications
from app.ops import service as ops
from app.students import service as students
from app.tenancy import service as tenancy

log = get_logger(__name__)

READ: Final = "dq.findings.read"
RESOLVE: Final = "dq.findings.resolve"
WAIVE: Final = "dq.findings.waive"
STUDENT_READ: Final = "student.read_basic"

RUN_REQUESTED_EVENT: Final = "dq.run.requested"
EXECUTE_TASK: Final = "dq.execute_run"
INCREMENTAL_TASK: Final = "dq.run_incremental"
LINK_TASK: Final = "dq.link_change_request"
UNLINK_TASK: Final = "dq.unlink_change_request"
INCREMENTAL_EVENTS: Final = (
    "student.values.changed",
    "import.committed",
    "import.reverted",
    "extraction.confirmed",
    "change_request.approved",
)
for _event in INCREMENTAL_EVENTS:
    ops.register_outbox_route(_event, INCREMENTAL_TASK)
ops.register_outbox_route("change_request.submitted", LINK_TASK)
# A request that closes without being applied releases its findings.
for _closed in ("change_request.rejected", "change_request.cancelled", "change_request.expired"):
    ops.register_outbox_route(_closed, UNLINK_TASK)
ops.register_outbox_route(RUN_REQUESTED_EVENT, EXECUTE_TASK)

MAX_OFFSET: Final = 100_000
AADHAAR_ERROR: Final = {
    "code": "aadhaar_full_number_rejected",
    "message_key": "errors.aadhaar_last4_only",
}


# --- plumbing -------------------------------------------------------------------------------------


def _audit(
    session: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    summary: Mapping[str, Any],
    system: bool = False,
    actor_id: uuid.UUID | None = None,
) -> None:
    request_id = get_context().get("request_id")
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if system else "user",
        actor_id=actor_id,
        request_id=request_id if isinstance(request_id, str) else None,
    )


def _reach(session: Session, ctx: UserContext) -> frozenset[uuid.UUID] | None:
    """Students whose findings the caller may see (``None`` = the whole school)."""
    if (
        ctx.has(READ)
        and ctx.has(STUDENT_READ)
        and ctx.scope_for(READ).school_wide
        and ctx.scope_for(STUDENT_READ).school_wide
    ):
        return None
    if not ctx.has(READ):
        return frozenset()
    return frozenset(students.list_students_in_scope(session, ctx))


def _visible(reach: frozenset[uuid.UUID] | None, student_id: uuid.UUID | None) -> bool:
    return student_id is not None and (reach is None or student_id in reach)


def _reject_aadhaar(field: str, value: str | None) -> None:
    if value and contains_full_aadhaar(value):
        raise ValidationFailed(
            [{"field": field, **AADHAAR_ERROR}],
            detail="Aadhaar numbers are never stored. Remove the number and try again.",
        )


# --- rendering ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Labels:
    attributes: Mapping[str, tuple[str, str]]
    sensitive: frozenset[str]

    @classmethod
    def load(cls, session: Session) -> _Labels:
        catalog = students.attribute_catalog(session)
        return cls(
            attributes={a.key: (a.label_en, a.label_te) for a in catalog},
            sensitive=frozenset(a.key for a in catalog if a.classification == "C3"),
        )


def _param_text(
    name: str, value: Any, language: Language, labels: _Labels, related_visible: bool
) -> str:
    text = str(value)
    if name == "profile":
        profile = load_profiles().get(text)
        return profile.label(language.value) if profile is not None else text
    if name == "field":
        pair = labels.attributes.get(text)
        return (pair[1] if language is Language.TE else pair[0]) if pair else text
    if name == "issue":
        issue = load_engine_config().format_issues.get(text)
        return issue.text(language.value) if issue is not None else text
    if name == "student" and not related_visible:
        return FULL_MASK
    return text


def _explanation(
    code: str, params: Mapping[str, Any], labels: _Labels, related_visible: bool
) -> Bilingual:
    catalog = load_explanations()
    texts = {
        lang: catalog.render(
            code,
            lang,
            **{k: _param_text(k, v, lang, labels, related_visible) for k, v in params.items()},
        )
        for lang in Language
    }
    return Bilingual(code=code, en=texts[Language.EN], te=texts[Language.TE])


def _bilingual(code: str) -> Bilingual:
    text = load_explanations().get(code)
    return Bilingual(code=code, en=text.en, te=text.te)


@dataclass(frozen=True, slots=True)
class _Display:
    """Current C2 values, student names and admission numbers for one page of findings."""

    values: Mapping[uuid.UUID, str | None]
    names: Mapping[uuid.UUID, tuple[str | None, str | None]]


def _display(session: Session, rows: Sequence[RowMapping], labels: _Labels) -> _Display:
    student_ids = {r["student_id"] for r in rows}
    keys = {
        v["attribute_key"]
        for r in rows
        for v in (r["details"] or {}).get("values", [])
        if v.get("attribute_key") not in labels.sensitive
    }
    current: dict[uuid.UUID, str | None] = {}
    if keys:
        by_student = students.source_values(session, list(student_ids), keys)
        for per_key in by_student.values():
            for per_source in per_key.values():
                for sv in per_source.values():
                    current[sv.value_id] = sv.value
    canon = students.canonical_values(session, list(student_ids), ["full_name", "admission_no"])
    names = {
        sid: (
            per["full_name"].value if "full_name" in per else None,
            per["admission_no"].value if "admission_no" in per else None,
        )
        for sid, per in canon.items()
    }
    return _Display(values=current, names=names)


def _finding_out(
    row: RowMapping, labels: _Labels, display: _Display, reach: frozenset[uuid.UUID] | None
) -> FindingOut:
    details = dict(row["details"] or {})
    raw_values = details.pop("values", [])
    match = details.get("match")
    related = row["related_student_id"]
    related_visible = related is None or _visible(reach, related)
    values = []
    for v in raw_values:
        value_id = uuid.UUID(str(v["value_id"]))
        sensitive = v["attribute_key"] in labels.sensitive
        values.append(
            FindingValue(
                attribute_key=v["attribute_key"],
                source=v["source"],
                value_id=value_id,
                masked=v.get("masked"),
                value=None if sensitive else display.values.get(value_id),
                sensitive=sensitive,
            )
        )
    name, admission_no = display.names.get(row["student_id"], (None, None))
    return FindingOut(
        id=row["id"],
        student=StudentRef(id=row["student_id"], display_name=name, admission_no=admission_no),
        related_student_id=related,
        rule_id=row["rule_id"],
        rule_version=row["rule_version"],
        profile_key=row["profile_key"],
        attribute_key=row["attribute_key"],
        sources=list(row["sources"] or []),
        match_class=row["match_class"],
        severity=row["severity"],
        blocker=row["severity"] == Severity.BLOCKER.value,
        status=row["status"],
        explanation=_explanation(
            row["explanation_code"], row["explanation_params"] or {}, labels, related_visible
        ),
        match_explanation=(
            _bilingual(str(match["explanation_code"]))
            if isinstance(match, dict) and match.get("explanation_code")
            else None
        ),
        routes=[_bilingual(code) for code in row["route_codes"]],
        values=values,
        details=details,
        resolution=row["resolution"],
        resolution_note=row["resolution_note"],
        change_request_id=row["change_request_id"],
        resolved_by=row["resolved_by"],
        resolved_at=row["resolved_at"],
        waived_by=row["waived_by"],
        waived_at=row["waived_at"],
        waived_reason=row["waived_reason"],
        reopened_count=row["reopened_count"],
        first_seen_at=row["first_seen_at"],
        last_seen_at=row["last_seen_at"],
        first_seen_run_id=row["first_seen_run_id"],
        last_seen_run_id=row["last_seen_run_id"],
        version=row["version"],
    )


def _render(
    session: Session, rows: Sequence[RowMapping], reach: frozenset[uuid.UUID] | None
) -> list[FindingOut]:
    if not rows:
        return []
    labels = _Labels.load(session)
    display = _display(session, rows, labels)
    return [_finding_out(r, labels, display, reach) for r in rows]


def _run_out(row: RowMapping) -> RunOut:
    return RunOut(
        id=row["id"],
        trigger=row["trigger"],
        event_type=row["event_type"],
        status=row["status"],
        profile_key=row["profile_key"],
        scope=dict(row["scope"] or {}),
        stats=dict(row["stats"]) if row["stats"] is not None else None,
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        error_code=row["error_code"],
    )


# --- runs -----------------------------------------------------------------------------------------


def _check_profile(profile_key: str | None) -> None:
    if profile_key is not None and profile_key not in load_profiles():
        raise ValidationFailed(
            [
                {
                    "field": "profile_key",
                    "code": "unknown_profile",
                    "message_key": "errors.dq.unknown_profile",
                }
            ]
        )


def _check_structure(session: Session, scope: engine.Scope) -> None:
    """Unknown (or other schools') sections and classes are a 422, like any bad reference."""
    for i, section_id in enumerate(scope.section_ids or ()):
        try:
            tenancy.get_section(session, section_id)
        except NotFound:
            raise ValidationFailed(
                [
                    {
                        "field": f"scope.section_ids.{i}",
                        "code": "not_found",
                        "message_key": "errors.not_found",
                    }
                ]
            ) from None
    for i, class_id in enumerate(scope.class_ids or ()):
        try:
            tenancy.get_class(session, class_id)
        except NotFound:
            raise ValidationFailed(
                [
                    {
                        "field": f"scope.class_ids.{i}",
                        "code": "not_found",
                        "message_key": "errors.not_found",
                    }
                ]
            ) from None


def _scope_of(data: RunCreate) -> engine.Scope:
    s = data.scope
    return engine.Scope(
        section_ids=tuple(s.section_ids) if s.section_ids else None,
        class_ids=tuple(s.class_ids) if s.class_ids else None,
        student_ids=tuple(s.student_ids) if s.student_ids else None,
        batch_id=s.batch_id,
    )


def _complete(
    session: Session,
    run: RowMapping,
    stats: engine.RunStats,
    profiles: Sequence[str],
    *,
    system: bool,
) -> RowMapping:
    at = repo.now(session)
    row = repo.update_run(
        session,
        run["id"],
        {"status": "completed", "finished_at": at, "stats": stats.as_json(profiles)},
    )
    _audit(
        session,
        action="dq.run.completed",
        resource_type="dq_run",
        resource_id=run["id"],
        summary={
            "trigger": run["trigger"],
            "event_type": run["event_type"],
            "profile_key": run["profile_key"],
            "students": stats.students,
            "new": stats.new,
            "reopened": stats.reopened,
            "cleared": stats.cleared,
            "blockers": stats.blockers,
            "warnings": stats.warnings,
        },
        system=system,
        actor_id=None if system else run["started_by"],
    )
    log.info(
        "dq.run.completed",
        resource_type="dq_run",
        resource_id=run["id"],
        count=stats.students,
        duration_ms=stats.duration_ms,
    )
    return row


def run_checks(
    session: Session,
    ctx: UserContext,
    *,
    section_ids: Collection[uuid.UUID] | None = None,
    class_ids: Collection[uuid.UUID] | None = None,
    student_ids: Collection[uuid.UUID] | None = None,
    batch_id: uuid.UUID | None = None,
    profile_key: str | None = None,
) -> RunOut:
    """Check the scope now, in the caller's transaction (manual run); returns the finished run.

    The scope is limited to the students ``ctx`` may read. Audit: ``dq.run.completed``.
    """
    _check_profile(profile_key)
    scope = engine.Scope(
        section_ids=tuple(section_ids) if section_ids else None,
        class_ids=tuple(class_ids) if class_ids else None,
        student_ids=tuple(student_ids) if student_ids else None,
        batch_id=batch_id,
    )
    ids = engine.resolve_students(session, ctx, scope)
    return _run_now(session, ctx, scope, ids, profile_key)


def _run_now(
    session: Session,
    ctx: UserContext,
    scope: engine.Scope,
    ids: Sequence[uuid.UUID],
    profile_key: str | None,
) -> RunOut:
    at = repo.now(session)
    run = repo.insert_run(
        session,
        id=new_id(),
        tenant_id=ctx.tenant_id,
        trigger="manual",
        scope=scope.as_json(),
        profile_key=profile_key,
        status="running",
        started_by=ctx.user_id,
        requested_by_membership=ctx.membership_id,
        started_at=at,
    )
    profiles = [profile_key] if profile_key else []
    stats = engine.execute(session, run_id=run["id"], student_ids=ids, profile_keys=profiles)
    return _run_out(_complete(session, run, stats, profiles, system=False))


def request_run(session: Session, ctx: UserContext, data: RunCreate) -> RunOut:
    """``POST /dq/runs``: run now when the scope has at most ``sync_max_students`` students,
    else queue it for the worker (status ``queued``; the requester is notified when done)."""
    _check_profile(data.profile_key)
    scope = _scope_of(data)
    _check_structure(session, scope)
    ids = engine.resolve_students(session, ctx, scope)
    if len(ids) <= load_engine_config().sync_max_students:
        return _run_now(session, ctx, scope, ids, data.profile_key)
    # The worker runs with a whole-school context: freeze a scoped caller's reach now.
    school_wide = _reach(session, ctx) is None
    effective = scope if school_wide else engine.Scope(student_ids=tuple(ids))
    run = repo.insert_run(
        session,
        id=new_id(),
        tenant_id=ctx.tenant_id,
        trigger="manual",
        scope=effective.as_json(),
        profile_key=data.profile_key,
        status="queued",
        started_by=ctx.user_id,
        requested_by_membership=ctx.membership_id,
    )
    ops.enqueue_event(session, RUN_REQUESTED_EVENT, {"run_id": run["id"]})
    log.info("dq.run.queued", resource_type="dq_run", resource_id=run["id"], count=len(ids))
    return _run_out(run)


def get_run(session: Session, ctx: UserContext, run_id: uuid.UUID) -> RunOut:
    """A run; scoped callers see only their own runs (else 404)."""
    row = repo.get_run(session, run_id)
    if row is None:
        raise NotFound("Check run not found")
    if _reach(session, ctx) is not None and row["requested_by_membership"] != ctx.membership_id:
        raise NotFound("Check run not found")
    return _run_out(row)


def _worker_session(tenant_id: uuid.UUID) -> Any:
    return tenant_session(
        tenant_id, statement_timeout_ms=get_settings().worker_statement_timeout_ms
    )


def execute_queued_run(tenant_id: uuid.UUID, run_id: uuid.UUID) -> str:
    """Worker: run a queued manual run and notify the requester (idempotent: a finished run is
    left alone). Returns the final status."""
    try:
        with _worker_session(tenant_id) as session:
            run = repo.get_run(session, run_id, lock=True)
            if run is None:
                return "missing"
            if run["status"] not in ("queued", "running"):
                return str(run["status"])
            run = repo.update_run(
                session, run_id, {"status": "running", "started_at": repo.now(session)}
            )
            ctx = engine.system_context(tenant_id)
            ids = engine.resolve_students(session, ctx, engine.Scope.from_json(run["scope"]))
            profiles = [run["profile_key"]] if run["profile_key"] else []
            stats = engine.execute(session, run_id=run_id, student_ids=ids, profile_keys=profiles)
            done = _complete(session, run, stats, profiles, system=False)
            if run["requested_by_membership"] is not None:
                notifications.notify(
                    session,
                    tenant_id=tenant_id,
                    recipients=[run["requested_by_membership"]],
                    template_key="dq.run.completed",
                    params={
                        "run_id": run_id,
                        "blockers": stats.blockers,
                        "warnings": stats.warnings,
                    },
                    resource_id=run_id,
                    dedupe_key=f"dq.run:{run_id}",
                )
            return str(done["status"])
    except Exception as exc:
        with _worker_session(tenant_id) as session:
            if repo.get_run(session, run_id) is not None:
                repo.update_run(
                    session,
                    run_id,
                    {
                        "status": "failed",
                        "finished_at": repo.now(session),
                        "error_code": type(exc).__name__[:100],
                    },
                )
        log.error(
            "dq.run.failed",
            resource_type="dq_run",
            resource_id=run_id,
            error_type=type(exc).__name__,
        )
        raise


def _uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


@dataclass(frozen=True, slots=True)
class _EventScope:
    event_type: str
    scope: engine.Scope
    change_request: engine.ChangeRequestLink | None = None


def _event_scope(payload: Mapping[str, Any]) -> _EventScope:
    """Which students an outbox event touched (payload shapes of CONTRACT §9)."""
    if "change_request_id" in payload:
        student = _uuid(payload["student_id"])
        attribute = payload.get("attribute_key")
        link = engine.ChangeRequestLink(
            change_request_id=_uuid(payload["change_request_id"]),
            student_id=student,
            attribute_key=str(attribute) if attribute else None,
        )
        return _EventScope("change_request.approved", engine.Scope(student_ids=(student,)), link)
    if "student_ids" in payload:
        ids = tuple(_uuid(s) for s in payload["student_ids"])
        return _EventScope("student.values.changed", engine.Scope(student_ids=ids))
    if "item_id" in payload and payload.get("student_id"):
        student = _uuid(payload["student_id"])
        return _EventScope("extraction.confirmed", engine.Scope(student_ids=(student,)))
    if "batch_id" in payload:
        return _EventScope("import.batch", engine.Scope(batch_id=_uuid(payload["batch_id"])))
    raise ValueError("unrecognised data-quality event payload")


def run_for_event(tenant_id: uuid.UUID, payload: Mapping[str, Any]) -> RunOut | None:
    """Worker: incremental run for the students an event touched (system actor, whole-school
    reach). Profiles with unresolved findings for those students are re-checked too, so a
    corrected value clears its profile finding. ``None`` when no student is affected."""
    target = _event_scope(payload)
    with _worker_session(tenant_id) as session:
        ctx = engine.system_context(tenant_id)
        ids = engine.resolve_students(session, ctx, target.scope)
        if not ids:
            return None
        link = target.change_request
        if link is not None:
            repo.link_change_request(
                session,
                student_id=link.student_id,
                attribute_key=link.attribute_key,
                change_request_id=link.change_request_id,
            )
        profiles = repo.profiles_in_use(session, ids)
        run = repo.insert_run(
            session,
            id=new_id(),
            tenant_id=tenant_id,
            trigger="event",
            event_type=target.event_type,
            scope=engine.Scope(student_ids=tuple(ids)).as_json(),
            status="running",
            started_at=repo.now(session),
        )
        stats = engine.execute(
            session, run_id=run["id"], student_ids=ids, profile_keys=profiles, change_request=link
        )
        return _run_out(_complete(session, run, stats, profiles, system=True))


def link_change_request(tenant_id: uuid.UUID, payload: Mapping[str, Any]) -> int:
    """Worker (``change_request.submitted``): link the student's unresolved findings of the
    attribute to the request. Audit: ``dq.findings.linked`` (ids and counts)."""
    cr, student = _uuid(payload["change_request_id"]), _uuid(payload["student_id"])
    attribute = payload.get("attribute_key")
    with tenant_session(tenant_id) as session:
        ids = repo.link_change_request(
            session,
            student_id=student,
            attribute_key=str(attribute) if attribute else None,
            change_request_id=cr,
        )
        if ids:
            _audit(
                session,
                action="dq.findings.linked",
                resource_type="change_request",
                resource_id=cr,
                summary={"student_id": student, "count": len(ids), "finding_ids": ids[:50]},
                system=True,
            )
        return len(ids)


def unlink_change_request(tenant_id: uuid.UUID, payload: Mapping[str, Any]) -> int:
    """Worker (``change_request.rejected``/``cancelled``/``expired``): unresolved findings
    forget the request."""
    cr = _uuid(payload["change_request_id"])
    with tenant_session(tenant_id) as session:
        ids = repo.unlink_change_request(session, cr)
        if ids:
            _audit(
                session,
                action="dq.findings.unlinked",
                resource_type="change_request",
                resource_id=cr,
                summary={"count": len(ids), "finding_ids": ids[:50]},
                system=True,
            )
        return len(ids)


# --- findings: read -------------------------------------------------------------------------------


def _offset(cursor: str | None) -> int:
    decoded = decode_cursor(cursor)
    if decoded is None:
        return 0
    offset = decoded.get("o")
    if not isinstance(offset, int) or isinstance(offset, bool) or not 0 <= offset <= MAX_OFFSET:
        raise ValidationFailed(
            [{"field": "cursor", "code": "invalid", "message_key": "errors.invalid_cursor"}]
        )
    return offset


def _student_filter(
    session: Session,
    ctx: UserContext,
    reach: frozenset[uuid.UUID] | None,
    *,
    section_ids: Collection[uuid.UUID] | None,
    student_id: uuid.UUID | None,
) -> frozenset[uuid.UUID] | None:
    ids = reach
    if section_ids:
        in_sections = frozenset(
            students.list_students_in_scope(session, ctx, section_ids=list(section_ids))
        )
        ids = in_sections if ids is None else ids & in_sections
    if student_id is not None:
        ids = frozenset({student_id}) if ids is None else ids & {student_id}
    return ids


def list_findings(
    session: Session,
    ctx: UserContext,
    filters: FindingFilters,
    *,
    limit: int = 50,
    cursor: str | None = None,
) -> Page[FindingOut]:
    """Findings, most severe first (US-501 AC1). ``status`` defaults to the unresolved ones
    (``open``, ``reopened``); ``profile_key`` keeps the base rules plus that profile's."""
    offset = _offset(cursor)
    _check_profile(filters.profile_key)
    reach = _reach(session, ctx)
    ids = _student_filter(
        session,
        ctx,
        reach,
        section_ids=[filters.section_id] if filters.section_id else None,
        student_id=filters.student_id,
    )
    if ids is not None and not ids:
        return Page[FindingOut](data=[], next_cursor=None)
    rows = repo.list_findings(
        session,
        repo.FindingFilter(
            student_ids=ids,
            statuses=filters.status or ACTIVE_STATUSES,
            severities=filters.severity,
            rule_ids=filters.rule_id,
            profile_key=filters.profile_key,
            attribute_key=filters.attribute_key,
        ),
        offset=offset,
        limit=limit + 1,
    )
    more = len(rows) > limit
    return Page[FindingOut](
        data=_render(session, rows[:limit], reach),
        next_cursor=encode_cursor({"o": offset + limit}) if more else None,
    )


def _visible_finding(
    session: Session, ctx: UserContext, finding_id: uuid.UUID, *, lock: bool = False
) -> tuple[RowMapping, frozenset[uuid.UUID] | None]:
    row = repo.get_finding(session, finding_id, lock=lock)
    reach = _reach(session, ctx)
    if row is None or not _visible(reach, row["student_id"]):
        raise NotFound("Finding not found")
    return row, reach


def get_finding(session: Session, ctx: UserContext, finding_id: uuid.UUID) -> FindingOut:
    row, reach = _visible_finding(session, ctx, finding_id)
    return _render(session, [row], reach)[0]


def findings_for_student(
    session: Session, ctx: UserContext, student_id: uuid.UUID
) -> list[FindingOut]:
    """Every finding of one student, most severe first (student record, change requests);
    404 outside the caller's scope."""
    reach = _reach(session, ctx)
    if not _visible(reach, student_id):
        raise NotFound("Student not found")
    rows = repo.list_findings(
        session, repo.FindingFilter(student_ids=[student_id]), offset=0, limit=1000
    )
    return _render(session, rows, reach)


def findings_for_students(
    session: Session,
    ctx: UserContext,
    student_ids: Collection[uuid.UUID],
    *,
    profile_key: str | None = None,
    limit: int = 20_000,
) -> list[FindingOut]:
    """Unresolved findings (``open``, ``reopened``) of ``student_ids``, most severe first, for
    pre-check exports (US-501 AC4). ``profile_key`` keeps the base rules plus that profile's.
    Students outside the caller's reach are silently left out (the caller asked for a list, not
    for one object); at most ``limit`` findings are returned."""
    _check_profile(profile_key)
    reach = _reach(session, ctx)
    wanted = frozenset(student_ids)
    ids = wanted if reach is None else wanted & reach
    if not ids:
        return []
    rows = repo.list_findings(
        session,
        repo.FindingFilter(student_ids=ids, statuses=ACTIVE_STATUSES, profile_key=profile_key),
        offset=0,
        limit=max(1, min(limit, 100_000)),
    )
    return _render(session, rows, reach)


# --- findings: workflow ---------------------------------------------------------------------------


def _open_for_change(row: RowMapping, expected_version: int | None) -> None:
    if row["status"] not in ACTIVE_STATUSES:
        raise Conflict(
            "This finding is already resolved or waived. Reload the list.",
            code="finding_not_open",
        )
    if expected_version is not None and row["version"] != expected_version:
        raise PreconditionFailed("The finding was changed by someone else. Reload and try again.")


def _check_change_request(
    session: Session, change_request_id: uuid.UUID, students_of_finding: Collection[object]
) -> None:
    """A finding is resolved only by a request of this school about one of its students."""
    student = changes.request_student(session, change_request_id)
    if student is None:
        code, detail = "not_found", "No change request with this id. Check the id and try again."
    elif student not in students_of_finding:
        code, detail = (
            "other_student",
            "This change request is about another student. Choose a request for this student.",
        )
    else:
        return
    raise ValidationFailed(
        [{"field": "change_request_id", "code": code, "message_key": f"errors.{code}"}],
        detail=detail,
    )


def resolve_finding(
    session: Session,
    ctx: UserContext,
    finding_id: uuid.UUID,
    data: ResolveIn,
    *,
    expected_version: int | None = None,
) -> FindingOut:
    """Resolve with a note and/or a change request (US-502 AC1, FR-DQ-020). Permission
    ``dq.findings.resolve``. A re-run reopens it if the conflict is still there (AC2).
    Audit: ``dq.finding.resolved``."""
    _reject_aadhaar("note", data.note)
    row, reach = _visible_finding(session, ctx, finding_id, lock=True)
    _open_for_change(row, expected_version)
    if data.change_request_id is not None:
        _check_change_request(
            session, data.change_request_id, (row["student_id"], row["related_student_id"])
        )
    note = data.note.strip() if data.note else None
    updated = repo.update_finding(
        session,
        finding_id,
        {
            "status": "resolved",
            "resolution": "change_request" if data.change_request_id else "note",
            "resolution_note": note,
            "change_request_id": data.change_request_id or row["change_request_id"],
            "resolved_by": ctx.user_id,
            "resolved_at": repo.now(session),
        },
        expected_version=row["version"],
    )
    if updated is None:  # pragma: no cover - the row is locked
        raise PreconditionFailed("The finding was changed by someone else. Reload and try again.")
    _audit(
        session,
        action="dq.finding.resolved",
        resource_type="dq_finding",
        resource_id=finding_id,
        summary={
            "student_id": row["student_id"],
            "rule_id": row["rule_id"],
            "severity": row["severity"],
            "resolution": updated["resolution"],
            "change_request_id": data.change_request_id,
            "has_note": note is not None,
        },
    )
    return _render(session, [updated], reach)[0]


def resolve_with_change_request(
    session: Session, ctx: UserContext, finding_id: uuid.UUID, change_request_id: uuid.UUID
) -> FindingOut:
    """Resolve a finding by linking the change request that corrects it (``app.changes``)."""
    return resolve_finding(session, ctx, finding_id, ResolveIn(change_request_id=change_request_id))


def _require_step_up(ctx: UserContext) -> None:
    """SEC-005: MFA-backed sign-in within 5 minutes (428 ``step_up_required``)."""
    if not ctx.mfa or ctx.auth_time is None:
        raise StepUpRequired()
    age = dt.datetime.now(dt.UTC) - ctx.auth_time
    if age > STEP_UP_MAX_AGE or age < -dt.timedelta(seconds=30):
        raise StepUpRequired()


def waive_finding(
    session: Session,
    ctx: UserContext,
    finding_id: uuid.UUID,
    data: WaiveIn,
    *,
    expected_version: int | None = None,
) -> FindingOut:
    """Accept a finding with a reason (US-502 AC1, FR-DQ-020). Permission
    ``dq.findings.waive``; blockers also need step-up MFA (428). The waiver covers this exact
    conflict: a re-run with different values reopens it. Audit: ``dq.finding.waived``."""
    _reject_aadhaar("reason", data.reason)
    row, reach = _visible_finding(session, ctx, finding_id, lock=True)
    _open_for_change(row, expected_version)
    if row["severity"] == Severity.BLOCKER.value:
        _require_step_up(ctx)
    updated = repo.update_finding(
        session,
        finding_id,
        {
            "status": "waived",
            "waived_by": ctx.user_id,
            "waived_at": repo.now(session),
            "waived_reason": data.reason.strip(),
        },
        expected_version=row["version"],
    )
    if updated is None:  # pragma: no cover - the row is locked
        raise PreconditionFailed("The finding was changed by someone else. Reload and try again.")
    _audit(
        session,
        action="dq.finding.waived",
        resource_type="dq_finding",
        resource_id=finding_id,
        summary={
            "student_id": row["student_id"],
            "rule_id": row["rule_id"],
            "severity": row["severity"],
            "step_up": row["severity"] == Severity.BLOCKER.value,
        },
    )
    return _render(session, [updated], reach)[0]


# --- catalog and summary --------------------------------------------------------------------------


def rules_catalog() -> list[RuleOut]:
    """DQ-001..DQ-012 with EN/TE explanation templates and correction routes (FR-DQ-001)."""
    catalog = load_explanations()
    out = []
    for rule in load_rules().values():
        text = catalog.rules[rule.explanation_key]
        policy = rule.severity
        out.append(
            RuleOut(
                id=rule.id,
                version=rule.version,
                check=rule.check.value,
                scope=rule.scope.value,
                attribute_keys=list(rule.attribute_keys),
                sources=list(rule.sources),
                requires_profile=rule.requires_profile,
                severity=SeverityPolicyOut(
                    mode=policy.mode,
                    level=policy.level.value if policy.level else None,
                    floor=policy.floor.value if policy.floor else None,
                    cap=policy.cap.value if policy.cap else None,
                ),
                explanation=Bilingual(code=rule.explanation_key, en=text.en, te=text.te),
                routes=[_bilingual(code) for code in rule.routes],
            )
        )
    return out


def profiles_catalog() -> list[ProfileOut]:
    return [
        ProfileOut(
            key=p.key,
            version=p.version,
            label_en=p.label_en,
            label_te=p.label_te,
            required_fields=list(p.required_fields),
            needs_apaar=p.needs_apaar,
        )
        for p in load_profiles().values()
    ]


def summary(
    session: Session,
    ctx: UserContext,
    *,
    profile_key: str | None = None,
    section_ids: Iterable[uuid.UUID] | None = None,
) -> SummaryOut:
    """Unresolved findings counted by severity and rule; blockers apart from warnings
    (US-501 AC2). Limited to the caller's scope and, if given, to sections."""
    _check_profile(profile_key)
    reach = _reach(session, ctx)
    sections = list(section_ids or [])
    ids = _student_filter(session, ctx, reach, section_ids=sections or None, student_id=None)
    last = repo.last_manual_run(session, profile_key)
    if ids is not None and not ids:
        rows: list[RowMapping] = []
        with_blockers = 0
    else:
        flt = repo.FindingFilter(student_ids=ids, statuses=ACTIVE_STATUSES, profile_key=profile_key)
        rows = repo.count_findings(session, flt)
        with_blockers = repo.students_with_severity(session, flt, Severity.BLOCKER.value)
    by_severity = {s.value: 0 for s in Severity}
    for r in rows:
        by_severity[r["severity"]] += int(r["n"])
    blockers = by_severity[Severity.BLOCKER.value]
    return SummaryOut(
        profile_key=profile_key,
        blockers=blockers,
        warnings=sum(by_severity.values()) - blockers,
        students_with_blockers=with_blockers,
        by_severity=by_severity,
        by_rule=[
            RuleCount(rule_id=r["rule_id"], severity=r["severity"], count=int(r["n"])) for r in rows
        ],
        last_run=(
            _run_out(last)
            if last is not None
            and (reach is None or last["requested_by_membership"] == ctx.membership_id)
            else None
        ),
    )


__all__ = [
    "EXECUTE_TASK",
    "INCREMENTAL_EVENTS",
    "INCREMENTAL_TASK",
    "LINK_TASK",
    "READ",
    "RESOLVE",
    "RUN_REQUESTED_EVENT",
    "UNLINK_TASK",
    "WAIVE",
    "execute_queued_run",
    "findings_for_student",
    "findings_for_students",
    "get_finding",
    "get_run",
    "link_change_request",
    "list_findings",
    "profiles_catalog",
    "purge_tenant_data",
    "request_run",
    "resolve_finding",
    "resolve_with_change_request",
    "rules_catalog",
    "run_checks",
    "run_for_event",
    "summary",
    "tenant_data_counts",
    "unlink_change_request",
    "waive_finding",
]


# --- offboarding purge (FR-PLT-005, ADR-0029) ------------------------------------------------
# Data-quality findings and runs.
# Registered with app.tenancy at import; the offboarding job counts them as sos_app and deletes
# them as sos_purger (children before parents) inside the school's tenant_session.
_PURGE = purging.PurgeTables(
    deleted=("sis.dq_findings", "sis.dq_runs"),
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    """Rows of the current school in this module's tables (offboarding inventory)."""
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    """Delete the current school's rows of this module (offboarding only: the database allows it
    only as ``sos_purger`` for a school in ``offboarding``)."""
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="dq", count=tenant_data_counts, purge=purge_tenant_data)
)
