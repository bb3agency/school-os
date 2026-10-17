"""WhatsApp messaging routes (docs/09 §4 "Messaging", "Webhooks"; ADR-0043, ADR-0047;
US-2201..US-2203, US-2208; FR-MSG-002..004, FR-MSG-008..010, FR-MSG-017, FR-MSG-020, FR-MSG-024).

Staff routes declare ``require``/``require_any`` with ``messaging.configure`` (school-wide,
step-up for changes), ``messaging.optin.record`` or ``message.read``; the service applies the
student scope per guardian and section (class teachers: their sections; 404 outside). On a
dedicated host or with ``SOS_MESSAGING_ENABLED`` off every route answers
``404 module_unavailable`` (FR-MSG-024).

The Meta webhook (``/api/v1/webhooks/meta``) takes no member: ``GET`` answers Meta's
subscription challenge (``require_meta_verify_token``), ``POST`` is verified by
``require_webhook_signature("meta")`` (``X-Hub-Signature-256``, ≤ 3 MB) and each entry is routed
to its school by the WABA id handle; events are stored, then 200. Both are pinned in
``app/public/public_routes.yaml``.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require, require_any
from app.authz.http import IdempotencyDep, IfMatch, OptionalIfMatch, etag
from app.messaging import service
from app.messaging.schemas import (
    AccountOut,
    EmbeddedSignupIn,
    GuardianOptinsOut,
    JoinSlipsIn,
    ManualConnectIn,
    MessagePage,
    MessageSearchIn,
    OptinIn,
    SectionOptinSummaryOut,
    SettingsIn,
    SettingsOut,
    TemplatesOut,
)
from app.messaging.slips import STYLE_CSP
from app.messaging.webhook import parse_entries
from app.public import service as public
from app.public.guards import (
    WebhookCaller,
    require_meta_verify_token,
    require_webhook_signature,
)

router = APIRouter(prefix="/api/v1", tags=["messaging"])
webhook_router = APIRouter(prefix="/api/v1", tags=["webhooks"])

AccountReader = Annotated[UserContext, Depends(require_any(service.CONFIGURE, service.READ))]
Configurer = Annotated[
    UserContext, Depends(require(service.CONFIGURE, scope="school", step_up=True))
]
SettingsReader = Annotated[UserContext, Depends(require_any(service.READ, service.CONFIGURE))]
OptinReader = Annotated[UserContext, Depends(require_any(service.READ, service.RECORD))]
Recorder = Annotated[UserContext, Depends(require(service.RECORD))]
Reader = Annotated[UserContext, Depends(require(service.READ))]

_UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    404: {"description": "``module_unavailable`` on a dedicated host or with messaging off."}
}


def _account_headers(item: AccountOut) -> dict[str, str]:
    return {"Location": "/api/v1/messaging/account", "ETag": etag(item.version)}


@router.get("/messaging/account", response_model=AccountOut, responses=_UNAVAILABLE)
def get_account(ctx: AccountReader, db: TenantDB, response: Response) -> AccountOut:
    """The school's WhatsApp connection: number, display name, status and pause reason, quality
    rating, messaging limit (and the default daily cap, Meta's limit minus 10%), billing currency
    state (INR required from 1 Jan 2027) and each template's status (FR-MSG-004; permission
    ``messaging.configure`` or ``message.read``). ``connected`` false before connecting."""
    out = service.get_account(db, ctx)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post(
    "/messaging/account/embedded-signup",
    response_model=AccountOut,
    status_code=201,
    responses=_UNAVAILABLE,
)
def connect_embedded_signup(
    ctx: Configurer, db: TenantDB, body: EmbeddedSignupIn, idem: IdempotencyDep
) -> Response:
    """Finish Meta's Embedded Signup: exchange the code for the school's business token,
    register the number, subscribe SchoolOS's app to the WABA and submit the message templates
    (FR-MSG-002; ``messaging.configure``, MFA within 5 minutes; ``Idempotency-Key``). The school
    pays Meta directly with its own payment method (owner decision D7). Errors: 409
    ``messaging_already_connected``; 502 ``messaging_auth_failed`` or
    ``messaging_provider_error``."""
    return idem.run(
        db,
        body,
        lambda: service.connect_embedded_signup(db, ctx, body),
        headers=_account_headers,
    )


@router.post(
    "/messaging/account/manual",
    response_model=AccountOut,
    status_code=201,
    responses=_UNAVAILABLE,
)
def connect_manual(
    ctx: Configurer, db: TenantDB, body: ManualConnectIn, idem: IdempotencyDep
) -> Response:
    """Connect with a system-user token from the school's own Business Manager, for pilots until
    SchoolOS is a Meta Tech Provider (FR-MSG-003, owner decision D13; ``messaging.configure``,
    MFA within 5 minutes). The token is checked with a read call, stored wrapped and never shown
    again. Errors: 403 ``manual_token_not_allowed``; 409 ``messaging_already_connected``; 502."""
    return idem.run(
        db, body, lambda: service.connect_manual(db, ctx, body), headers=_account_headers
    )


@router.post("/messaging/account/resume", response_model=AccountOut, responses=_UNAVAILABLE)
def resume(ctx: Configurer, db: TenantDB) -> AccountOut:
    """Resume sending after a pause (FR-MSG-021; ``messaging.configure``, MFA within 5 minutes).
    Errors: 409 ``messaging_not_paused``, ``messaging_still_restricted``,
    ``messaging_not_connected``."""
    return service.resume(db, ctx)


@router.delete("/messaging/account", status_code=204, responses=_UNAVAILABLE)
def disconnect(ctx: Configurer, db: TenantDB, version: OptionalIfMatch) -> Response:
    """Disconnect WhatsApp: sending stops at once, the token is erased and open parent links
    sent through messages stop working (US-2201 AC4; ``messaging.configure``, MFA within 5
    minutes; optional ``If-Match``). Audited."""
    service.disconnect(db, ctx, expected_version=version)
    return Response(status_code=204)


@router.get("/messaging/templates", response_model=TemplatesOut, responses=_UNAVAILABLE)
def list_templates(ctx: AccountReader, db: TenantDB) -> TemplatesOut:
    """Each catalogue template per language with Meta's status and category, whether Telugu
    text is reviewed and whether it can be used now (FR-MSG-005..007)."""
    return TemplatesOut(items=service.list_templates(db, ctx))


@router.post(
    "/messaging/templates/sync", response_model=AccountOut, status_code=202, responses=_UNAVAILABLE
)
def sync_templates(ctx: Configurer, db: TenantDB) -> AccountOut:
    """Submit templates not created yet and read every status back (in the background;
    ``messaging.configure``, MFA within 5 minutes)."""
    return service.request_template_sync(db, ctx)


@router.get("/messaging/settings", response_model=SettingsOut, responses=_UNAVAILABLE)
def get_settings(ctx: SettingsReader, db: TenantDB, response: Response) -> SettingsOut:
    """Messaging switches per purpose, absence-alert time, quiet hours, daily cap and the office
    phone for automatic replies, with the estimated Meta rate used for cost previews
    (``message.read`` or ``messaging.configure``)."""
    out = service.get_messaging_settings(db, ctx)
    response.headers["ETag"] = etag(out.version)
    return out


@router.put("/messaging/settings", response_model=SettingsOut, responses=_UNAVAILABLE)
def put_settings(
    ctx: Configurer, db: TenantDB, body: SettingsIn, version: IfMatch, response: Response
) -> SettingsOut:
    """Change the messaging settings (``messaging.configure``, MFA within 5 minutes;
    ``If-Match``, ``W/"0"`` before the first save). Audited (field names)."""
    out = service.update_messaging_settings(db, ctx, body, expected_version=version)
    response.headers["ETag"] = etag(out.version)
    return out


@router.get(
    "/guardians/{guardian_id}/optins", response_model=GuardianOptinsOut, responses=_UNAVAILABLE
)
def get_optins(
    ctx: OptinReader, db: TenantDB, guardian_id: uuid.UUID, response: Response
) -> GuardianOptinsOut:
    """A guardian's WhatsApp opt-in state per category (notices, attendance, fees), whether it
    is for the guardian's current number, the full history with methods and recorders, and the
    guardian's message language (``message.read`` or ``messaging.optin.record``; class
    teachers: guardians of their sections' students). ``ETag``: the number of decisions."""
    out = service.get_guardian_optins(db, ctx, guardian_id)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post(
    "/guardians/{guardian_id}/optins",
    response_model=GuardianOptinsOut,
    status_code=201,
    responses=_UNAVAILABLE,
)
def record_optin(
    *,
    ctx: Recorder,
    db: TenantDB,
    guardian_id: uuid.UUID,
    body: OptinIn,
    version: OptionalIfMatch,
    idem: IdempotencyDep,
) -> Response:
    """Record a guardian's opt-in or opt-out for one category with its method and date: a signed
    form (evidence document required), in person (the caller is the recorder) or a stop request
    (FR-MSG-008; ``messaging.optin.record``; ``Idempotency-Key``, ``If-Match``). Errors: 409
    ``opted_out_by_parent``; 412; 422 ``no_mobile_number``, ``evidence_required``,
    ``method_does_not_match_status``, ``date_in_future``."""
    return idem.run(
        db,
        body,
        lambda: service.record_optin(db, ctx, guardian_id, body, expected_version=version),
        headers=lambda out: {
            "Location": f"/api/v1/guardians/{guardian_id}/optins",
            "ETag": etag(out.version),
        },
        refetch=lambda gid: service.get_guardian_optins(db, ctx, gid),
    )


@router.post(
    "/messaging/join-slips",
    response_class=HTMLResponse,
    responses={
        200: {"content": {"text/html": {}}, "description": "Print-ready A4 slips (two a page)"},
        **_UNAVAILABLE,
    },
)
def join_slips(ctx: Recorder, db: TenantDB, body: JoinSlipsIn) -> HTMLResponse:
    """Printable join slips for the guardians (with a mobile number) of chosen students or a
    section, in each guardian's parent language, each with a new single-use code and a QR code
    for the school's WhatsApp number (FR-MSG-009; ``messaging.optin.record``, scope). Errors: 409
    ``messaging_not_connected``, ``no_slips``, ``too_many_slips``. Audited (count)."""
    page = service.join_slips(db, ctx, body)
    return HTMLResponse(
        page,
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": STYLE_CSP,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": 'inline; filename="whatsapp-join-slips.html"',
        },
    )


@router.get(
    "/sections/{section_id}/optins/summary",
    response_model=SectionOptinSummaryOut,
    responses=_UNAVAILABLE,
)
def section_summary(ctx: Reader, db: TenantDB, section_id: uuid.UUID) -> SectionOptinSummaryOut:
    """Opted in / opted out / not asked per category for a section's guardians, and how many
    have no mobile number (US-2202 AC3; ``message.read``, scope). Counts only."""
    return service.section_summary(db, ctx, section_id)


@router.post("/messages/search", response_model=MessagePage, responses=_UNAVAILABLE)
def search_messages(ctx: Reader, db: TenantDB, body: MessageSearchIn) -> MessagePage:
    """Message history and delivery log, newest first: purpose, template, language, status
    (queued, sent, delivered, read, failed with a plain reason, skipped with why) and the
    phone's last four digits only (US-2208, FR-MSG-017; ``message.read``). Class teachers name a
    student or guardian of their sections (422 ``filter_required``)."""
    return service.search_messages(db, ctx, body)


# --- Meta webhook (ADR-0043 decision 6, ADR-0047; FR-MSG-020) ---------------------------------

ChallengeDep = Annotated[str, Depends(require_meta_verify_token())]
MetaDelivery = Annotated[WebhookCaller, Depends(require_webhook_signature("meta"))]


@webhook_router.get(
    "/webhooks/meta",
    response_class=PlainTextResponse,
    responses={200: {"content": {"text/plain": {}}}, 403: {"description": "Token mismatch"}},
)
def meta_challenge(challenge: ChallengeDep) -> PlainTextResponse:
    """Meta's subscription check: ``hub.challenge`` back when ``hub.mode = subscribe`` and
    ``hub.verify_token`` matches ``SOS_META_WEBHOOK_VERIFY_TOKEN`` (403 otherwise)."""
    service.ensure_available()
    return PlainTextResponse(challenge)


@webhook_router.post(
    "/webhooks/meta",
    status_code=200,
    responses={
        401: {"description": "Bad or missing X-Hub-Signature-256 (no detail)."},
        413: {"description": "Larger than Meta's 3 MB."},
    },
)
def meta_webhook(delivery: MetaDelivery) -> Response:
    """Meta's events for every connected school. The signature (app secret over the raw body)
    was checked by the guard; each entry is routed by its WABA id to one school, its events are
    stored (encrypted, deduplicated) and the worker applies them. A replayed delivery and events
    of unknown or disconnected accounts are acknowledged without effect."""
    service.ensure_available()
    if delivery.duplicate:
        return Response(status_code=200)
    for waba_id, events in parse_entries(delivery.body).items():
        handle = public.resolve("meta_waba", waba_id)
        if handle is None:
            continue
        service.store_events(handle.tenant_id, handle.handle_id, waba_id, events)
    return Response(status_code=200)


__all__ = ["router", "webhook_router"]
