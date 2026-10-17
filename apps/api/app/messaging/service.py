"""WhatsApp messaging core: public API (R2-1C; ADR-0043, ADR-0044, ADR-0046, ADR-0047;
US-2201..US-2203, US-2208..US-2210; FR-MSG-001..010, FR-MSG-016..024).

Other modules call only these functions. The sending wave (R2-3G, ``app/messaging/sends/``)
builds notice broadcasts, absence alerts, fee reminders and receipts on :func:`queue_message`
and :func:`deliver`.

- **Availability (FR-MSG-024).** Messaging exists only on the shared tier with
  ``SOS_MESSAGING_ENABLED`` (owner decision D24); otherwise every route answers
  ``404 module_unavailable``.
- **Connection (FR-MSG-002..004).** Embedded Signup v4 (code exchanged for the school's business
  token, number registered with a PIN SchoolOS generates, app subscribed to the WABA) or, for
  pilots, a manual system-user token checked by a read call. Token and PIN are wrapped with the
  school's key wrapper bound to the account row (``resource: "messaging_account/<id>"``); they are
  never returned, logged, exported or audited. Connecting creates the ``meta_waba`` public handle
  that routes webhooks to the school (ADR-0047) and queues template provisioning; disconnecting
  erases the secrets, revokes the handle and every open parent link sent through messages.
- **Templates (FR-MSG-005..007).** The catalogue's utility templates are created in the school's
  WABA per language; their status and category are synced hourly and from webhooks. A template
  Meta moves out of utility is paused at once and the owner and principal are told.
- **Opt-ins (FR-MSG-008..010).** Append-only decisions per guardian and category, with method,
  wording version, evidence and recorder; bound to the guardian's number at the time (a new
  number needs a new opt-in). Join slips carry a single-use code; an inbound ``JOIN <code>`` from
  the guardian's own number opts in and is confirmed. ``STOP`` (any language, or the quick reply)
  opts the number out of everything, revokes open parent links and is confirmed once; staff
  cannot opt a parent back in after a STOP except with a signed form or the parent's own join.
- **Webhook inbox (FR-MSG-016, FR-MSG-020, FR-MSG-021).** Verified events are stored encrypted
  (dedupe key per event) and applied by the worker: statuses only move forward, inbound text is
  classified and never stored, quality drops and restrictions pause sending until a person
  resumes.
- **Audit and logs (FR-MSG-022, invariant 5).** Connections, template changes, opt-ins, pauses
  and queued messages are audited in the same transaction with ids, codes and counts only; no
  phone number, name, token or message text reaches a log, an audit summary or a metric.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import uuid
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.authz.context import UserContext
from app.authz.http import decode_cursor, encode_cursor
from app.core import purge as purging
from app.core.config import DeploymentMode, Settings
from app.core.crypto import CryptoError, unwrap_bound, wrap_bound
from app.core.db import context_free_session, tenant_session
from app.core.errors import (
    Conflict,
    DomainError,
    Forbidden,
    NotFound,
    PreconditionFailed,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.languages import Language, parent_language
from app.core.logging import get_context, get_logger
from app.core.records import RecordTable
from app.documents import service as documents
from app.identity import service as identity
from app.messaging import catalogue as cat
from app.messaging import eligibility as rules
from app.messaging import repository as repo
from app.messaging import slips
from app.messaging.config import OPTIN_CATEGORIES, OptinCategory, load_config
from app.messaging.models import Account, Message, MessageTemplate, MessagingSettings, Optin
from app.messaging.providers import (
    MessagingProvider,
    ProviderError,
    TemplateMessage,
    get_provider,
)
from app.messaging.runtime import settings as messaging_settings
from app.messaging.schemas import (
    CategoryCounts,
    CategoryStateOut,
    EmbeddedSignupIn,
    GuardianOptinsOut,
    JoinSlipsIn,
    ManualConnectIn,
    MessagingAccountOut,
    MessagingMessageOut,
    MessagingMessagePage,
    MessagingSearchIn,
    MessagingSettingsIn,
    MessagingSettingsOut,
    MessagingTemplateOut,
    OptinEntryOut,
    OptinIn,
    SectionOptinSummaryOut,
)
from app.messaging.webhook import ParsedEvent
from app.notifications import service as notifications
from app.ops import service as ops
from app.public import service as public
from app.students import crypto
from app.students import rotation as key_rotation
from app.students import service as students
from app.tenancy import service as tenancy

CONFIGURE: Final = "messaging.configure"
RECORD: Final = "messaging.optin.record"
READ: Final = "message.read"
IST: Final = ZoneInfo("Asia/Kolkata")
PROVIDER: Final = "meta_cloud"
PROVISION_EVENT: Final = "messaging.provision_templates"
PROVISION_TASK: Final = "messaging.provision_templates"
INBOX_EVENT: Final = "messaging.inbox"
INBOX_TASK: Final = "messaging.process_inbox"
EVENTS_TABLE: Final = "msg.provider_events"
MESSAGES_TABLE: Final = "msg.messages"
_STAFF_METHODS: Final = frozenset({"signed_form", "in_person", "staff_recorded_stop"})
_PARENT_ACTIONS: Final = frozenset({"signed_form", "whatsapp_join"})
_META_STATUS: Final = {
    "PENDING": "pending",
    "IN_APPEAL": "pending",
    "APPROVED": "approved",
    "REJECTED": "rejected",
    "PAUSED": "paused",
    "DISABLED": "disabled",
    "PENDING_DELETION": "disabled",
    "DELETED": "disabled",
}
_STATUS_RANK: Final = {"queued": 0, "sent": 1, "delivered": 2, "read": 3}
_PERMANENT_REASONS: Final = {
    131026: "not_on_whatsapp",
    131047: "window_closed",
    131050: "user_stopped_marketing",
    131051: "unsupported_message",
    131052: "media_error",
    132000: "template_parameters",
    132001: "template_missing",
    132005: "template_text_too_long",
    132007: "template_policy",
    132012: "template_parameters",
    132015: "template_paused",
    132016: "template_disabled",
    133010: "number_not_registered",
}

ops.register_outbox_route(PROVISION_EVENT, PROVISION_TASK)
ops.register_outbox_route(INBOX_EVENT, INBOX_TASK)

log = get_logger(__name__)


# --- errors -----------------------------------------------------------------------------------


class ModuleUnavailable(NotFound):
    code = "module_unavailable"


class AlreadyConnected(Conflict):
    code = "messaging_already_connected"


class NotConnected(Conflict):
    code = "messaging_not_connected"


class StillRestricted(Conflict):
    code = "messaging_still_restricted"


class OptedOutByParent(Conflict):
    code = "opted_out_by_parent"


class ManualTokenNotAllowed(Forbidden):
    code = "manual_token_not_allowed"


class ProviderFailed(DomainError):
    status, code, title = 502, "messaging_provider_error", "WhatsApp did not accept the request"


# --- availability and helpers -----------------------------------------------------------------


def available(settings: Settings | None = None) -> bool:
    """FR-MSG-024: shared tier with ``SOS_MESSAGING_ENABLED`` only (owner decision D24)."""
    s = settings or messaging_settings()
    return s.messaging_enabled and s.deployment_mode is DeploymentMode.SHARED


def ensure_available() -> None:
    if not available():
        raise ModuleUnavailable("WhatsApp messages are not available on this deployment.")


@contextmanager
def _db_errors() -> Iterator[None]:
    try:
        yield
    except DBAPIError as exc:
        mapped = repo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


def _error(field: str, code: str) -> dict[str, str]:
    return {"field": field, "code": code, "message_key": f"errors.{code}"}


def _request_id() -> str | None:
    value = get_context().get("request_id")
    return value if isinstance(value, str) else None


def _audit(
    session: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | None,
    summary: Mapping[str, Any],
    system: bool = False,
) -> None:
    audit.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        actor_type="system" if system else "user",
        request_id=_request_id(),
    )


def _today(session: Session) -> dt.date:
    return repo.now(session).astimezone(IST).date()


def _resource(account_id: uuid.UUID) -> str:
    return f"messaging_account/{account_id}"


def _wrap(session: Session, account_id: uuid.UUID, secret: str) -> bytes:
    wrapper = crypto.get_keyring().wrapper
    return wrap_bound(
        wrapper,
        secret.encode(),
        tenant_id=repo.current_tenant_id(session),
        resource=_resource(account_id),
    )


def _token(session: Session, account: Account) -> str:
    if account.token_ciphertext is None:
        raise NotConnected("Connect WhatsApp first.")
    wrapper = crypto.get_keyring().wrapper
    try:
        raw = unwrap_bound(
            wrapper,
            bytes(account.token_ciphertext),
            tenant_id=account.tenant_id,
            resource=_resource(account.id),
        )
    except CryptoError:
        log.error("messaging.token_unreadable", resource_id=account.id, error_code="crypto")
        raise NotConnected("The WhatsApp connection must be made again.") from None
    return raw.decode()


def _provider() -> MessagingProvider:
    return get_provider()


def _provider_failed(exc: ProviderError, action: str) -> ProviderFailed:
    log.warning(
        "messaging.provider_failed",
        resource_type="messaging_account",
        action=action,
        error_code=exc.code,
        outcome=str(exc.meta_code) if exc.meta_code is not None else None,
    )
    if exc.code == "auth_failed":
        return ProviderFailed(
            "Meta did not accept the connection. Check the token or start the sign-up again.",
            code="messaging_auth_failed",
        )
    return ProviderFailed("WhatsApp did not accept the request. Try again in a few minutes.")


# --- account ----------------------------------------------------------------------------------


def _currency_state(account: Account) -> str:
    if account.currency is None:
        return "unknown"
    return "confirmed_inr" if account.currency == "INR" else "not_inr"


def _template_out(row: MessageTemplate) -> MessagingTemplateOut:
    try:
        reviewed = cat.template(row.template_key).text(row.language).reviewed  # type: ignore[arg-type]
    except KeyError:
        reviewed = False
    state = rules.TemplateState(row.status, row.provider_category, row.paused_by_sos)
    return MessagingTemplateOut(
        template_key=row.template_key,
        template_version=row.template_version,
        language=row.language,
        provider_name=row.provider_name,
        status=row.status,
        provider_category=row.provider_category,
        paused_by_sos=row.paused_by_sos,
        rejected_reason=row.rejected_reason,
        reviewed=reviewed,
        usable=rules.template_usable(state),
        last_synced_at=row.last_synced_at,
    )


def _account_out(session: Session, account: Account | None) -> MessagingAccountOut:
    s = messaging_settings()
    cfg = load_config()
    signup = (
        {
            "app_id": s.meta_app_id,
            "config_id": s.meta_embedded_signup_config_id,
            "version": cfg.graph.embedded_signup_version,
            "graph_version": s.meta_graph_api_version or cfg.graph.api_version,
        }
        if s.meta_app_id and s.meta_embedded_signup_config_id
        else None
    )
    school_language = tenancy.parent_language(session)
    base: dict[str, Any] = {
        "manual_token_allowed": s.messaging_manual_token_allowed,
        "embedded_signup": signup,
        "inr_required_from": cfg.sending.inr_required_from,
        "unreviewed_texts": cat.unreviewed(school_language) if school_language == "te" else [],
    }
    if account is None:
        return MessagingAccountOut(connected=False, **base)
    return MessagingAccountOut(
        connected=True,
        id=account.id,
        mode=account.mode,
        status=account.status,
        pause_reason=account.pause_reason,
        waba_id=account.waba_id,
        phone_number_id=account.phone_number_id,
        display_phone=account.display_phone,
        display_name=account.display_name,
        quality_rating=account.quality_rating,
        messaging_limit=account.messaging_limit,
        messaging_limit_count=cfg.limit_for(account.messaging_limit),
        default_daily_cap=cfg.default_daily_cap(account.messaging_limit),
        currency=account.currency,
        currency_state=_currency_state(account),
        connected_at=account.connected_at,
        paused_at=account.paused_at,
        last_synced_at=account.last_synced_at,
        templates=[_template_out(t) for t in repo.templates_of(session, account.id)],
        version=account.version,
        **base,
    )


def get_account(session: Session, ctx: UserContext) -> MessagingAccountOut:
    """The school's WhatsApp connection, its number, quality, limit, billing currency state and
    template statuses (FR-MSG-004; ``messaging.configure`` or ``message.read``)."""
    del ctx
    ensure_available()
    return _account_out(session, repo.live_account(session))


def list_templates(session: Session, ctx: UserContext) -> list[MessagingTemplateOut]:
    del ctx
    ensure_available()
    account = repo.live_account(session)
    return (
        []
        if account is None
        else [_template_out(t) for t in repo.templates_of(session, account.id)]
    )


def _new_pin() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _apply_info(
    session: Session, account: Account, provider: MessagingProvider, token: str
) -> dict[str, Any]:
    """Read the number and account from Meta (also the manual token's check, FR-MSG-003)."""
    number = provider.phone_number(token, account.phone_number_id)
    info = provider.account(token, account.waba_id)
    values: dict[str, Any] = {
        "display_phone": number.display_phone[:32] if number.display_phone else None,
        "display_name": (number.display_name or info.name or "")[:200] or None,
        "quality_rating": (number.quality_rating or "").upper()[:20] or None,
        "messaging_limit": number.messaging_limit,
        "number_status": (number.status or "")[:40] or None,
        "currency": info.currency if info.currency and len(info.currency) == 3 else None,
        "last_synced_at": repo.now(session),
    }
    if values["currency"] == "INR" and account.currency_confirmed_at is None:
        values["currency_confirmed_at"] = repo.now(session)
    return values


def _connect(
    session: Session,
    ctx: UserContext,
    *,
    mode: str,
    waba_id: str,
    phone_number_id: str,
    business_id: str | None,
    obtain_token: Callable[[MessagingProvider], str],
    register: bool,
) -> MessagingAccountOut:
    ensure_available()
    if repo.live_account(session, lock=True) is not None:
        raise AlreadyConnected("WhatsApp is already connected. Disconnect it first.")
    provider = _provider()
    try:
        token = obtain_token(provider)
    except ProviderError as exc:
        raise _provider_failed(exc, "connect") from None
    account_id = new_id()
    pin = _new_pin() if register else None
    with _db_errors():
        account = repo.insert_account(
            session,
            id=account_id,
            provider=PROVIDER,
            mode=mode,
            business_id=business_id,
            waba_id=waba_id,
            phone_number_id=phone_number_id,
            status="connecting",
            token_ciphertext=_wrap(session, account_id, token),
            pin_ciphertext=_wrap(session, account_id, pin) if pin else None,
            connected_by=ctx.user_id,
            connected_at=repo.now(session),
        )
    try:
        if pin is not None:
            provider.register_number(token, phone_number_id, pin)
        provider.subscribe_app(token, waba_id)
        values = _apply_info(session, account, provider, token)
    except ProviderError as exc:
        raise _provider_failed(exc, "connect") from None
    try:
        issued = public.issue_handle(
            session,
            kind="meta_waba",
            target_type="messaging_account",
            target_id=account_id,
            key=waba_id,
            created_by=ctx.user_id,
        )
    except public.HandleKeyInUse:
        raise AlreadyConnected(
            "This WhatsApp Business account is already connected to a school."
        ) from None
    repo.update_account(
        session, account_id, {**values, "status": "active", "waba_handle_id": issued.handle_id}
    )
    _ensure_settings(session)
    _ensure_template_rows(session, account_id)
    ops.enqueue_event(session, PROVISION_EVENT, {"account_id": account_id})
    _audit(
        session,
        action="messaging.connected",
        resource_type="messaging_account",
        resource_id=account_id,
        summary={
            "mode": mode,
            "currency_known": values["currency"] is not None,
            "quality": values["quality_rating"],
            "limit": values["messaging_limit"],
        },
    )
    session.flush()
    session.refresh(account)
    return _account_out(session, account)


def connect_embedded_signup(
    session: Session, ctx: UserContext, data: EmbeddedSignupIn
) -> MessagingAccountOut:
    """Connect with Meta's Embedded Signup v4 (FR-MSG-002; ``messaging.configure``, step-up).
    Errors: 409 ``messaging_already_connected``; 502 ``messaging_auth_failed`` /
    ``messaging_provider_error``. Audit ``messaging.connected``."""
    return _connect(
        session,
        ctx,
        mode="embedded_signup",
        waba_id=data.waba_id,
        phone_number_id=data.phone_number_id,
        business_id=data.business_id,
        obtain_token=lambda p: p.exchange_code(data.code),
        register=True,
    )


def connect_manual(
    session: Session, ctx: UserContext, data: ManualConnectIn
) -> MessagingAccountOut:
    """Connect with a system-user token for pilots (FR-MSG-003; D13), checked by a read call
    before saving. 403 ``manual_token_not_allowed`` unless
    ``SOS_MESSAGING_MANUAL_TOKEN_ALLOWED``."""
    ensure_available()
    if not messaging_settings().messaging_manual_token_allowed:
        raise ManualTokenNotAllowed(
            "Connecting with a pasted token is switched off. Use Connect WhatsApp instead."
        )
    token = data.token.get_secret_value()
    return _connect(
        session,
        ctx,
        mode="manual_token",
        waba_id=data.waba_id,
        phone_number_id=data.phone_number_id,
        business_id=None,
        obtain_token=lambda p: token,
        register=False,
    )


def _live_or_409(session: Session, *, lock: bool = True) -> Account:
    account = repo.live_account(session, lock=lock)
    if account is None:
        raise NotConnected("WhatsApp is not connected.")
    return account


def disconnect(session: Session, ctx: UserContext, *, expected_version: int | None) -> None:
    """Stop all sending at once (US-2201 AC4): erase the token and PIN, revoke the WABA handle
    and every open parent link sent through messages, audit ``messaging.disconnected``."""
    ensure_available()
    account = _live_or_409(session)
    if expected_version is not None and expected_version != account.version:
        raise PreconditionFailed("The connection changed just now. Reload and try again.")
    repo.update_account(
        session,
        account.id,
        {
            "status": "disconnected",
            "disconnected_by": ctx.user_id,
            "disconnected_at": repo.now(session),
            "token_ciphertext": None,
            "pin_ciphertext": None,
        },
    )
    revoked = 0
    if account.waba_handle_id is not None:
        revoked += int(
            public.revoke(session, account.waba_handle_id, reason="whatsapp_disconnected")
        )
    links = 0
    for handle_id in dict.fromkeys(repo.open_handle_ids(session)):
        links += int(public.revoke(session, handle_id, reason="whatsapp_disconnected"))
    _audit(
        session,
        action="messaging.disconnected",
        resource_type="messaging_account",
        resource_id=account.id,
        summary={"mode": account.mode, "parent_links_revoked": links, "routes_revoked": revoked},
    )


def resume(session: Session, ctx: UserContext) -> MessagingAccountOut:
    """Resume sending after a pause (FR-MSG-021; ``messaging.configure``, step-up). The account
    is read from Meta first; 409 ``messaging_still_restricted`` while the quality is still
    flagged or red."""
    del ctx
    ensure_available()
    account = _live_or_409(session)
    if account.status != "paused":
        raise Conflict("Sending is not paused.", code="messaging_not_paused")
    try:
        values = _apply_info(session, account, _provider(), _token(session, account))
    except ProviderError as exc:
        raise _provider_failed(exc, "resume") from None
    if (values.get("quality_rating") or "") in load_config().sending.pausing_quality:
        repo.update_account(session, account.id, values)
        raise StillRestricted(
            "Meta still rates the number's quality low. Wait until it improves before resuming."
        )
    repo.update_account(
        session, account.id, {**values, "status": "active", "pause_reason": None, "paused_at": None}
    )
    session.refresh(account)
    _audit(
        session,
        action="messaging.resumed",
        resource_type="messaging_account",
        resource_id=account.id,
        summary={"previous_reason": account.pause_reason},
    )
    return _account_out(session, account)


def _pause(session: Session, account: Account, reason: str) -> bool:
    """Pause sending for the school (FR-MSG-021), tell owners and principals; idempotent."""
    if account.status != "active":
        return False
    repo.update_account(
        session,
        account.id,
        {"status": "paused", "pause_reason": reason, "paused_at": repo.now(session)},
    )
    _audit(
        session,
        action="messaging.paused",
        resource_type="messaging_account",
        resource_id=account.id,
        summary={"reason": reason},
        system=True,
    )
    notifications.notify(
        session,
        tenant_id=account.tenant_id,
        recipients=notifications.PermissionSelector(CONFIGURE),
        template_key="messaging.paused",
        params={"account_id": str(account.id), "reason": reason},
        resource_id=account.id,
        dedupe_key=f"messaging.paused:{account.id}:{reason}:{_today(session).isoformat()}",
    )
    return True


def sync_account(tenant_id: uuid.UUID) -> str:
    """Hourly: read the number's quality, limit and the billing currency (FR-MSG-004); pause on
    a red or flagged rating (FR-MSG-021). Returns an outcome code."""
    with tenant_session(tenant_id) as session:
        account = repo.live_account(session, lock=True)
        if account is None:
            return "not_connected"
        try:
            values = _apply_info(session, account, _provider(), _token(session, account))
        except (ProviderError, NotConnected) as exc:
            code = exc.code if isinstance(exc, ProviderError) else "token"
            log.warning("messaging.sync_failed", tenant_id=tenant_id, error_code=code)
            return "failed"
        repo.update_account(session, account.id, values)
        quality = values.get("quality_rating") or ""
        if quality in load_config().sending.pausing_quality:
            _pause(session, account, f"quality_{quality.lower()}")
        return "synced"


# --- templates --------------------------------------------------------------------------------


def _ensure_template_rows(session: Session, account_id: uuid.UUID) -> None:
    rows = [
        {
            "id": new_id(),
            "template_key": key,
            "template_version": item.version,
            "language": language,
            "provider_name": cat.provider_name(key, item.version, language),
            "status": "not_created",
        }
        for key, item in cat.catalogue().templates.items()
        for language in ("en", "te")
    ]
    repo.ensure_template_rows(session, account_id, rows)


def provision_templates(tenant_id: uuid.UUID, account_id: uuid.UUID) -> dict[str, int]:
    """Create the catalogue's templates (both languages) in the school's WABA (FR-MSG-006).
    Already created ones are left alone; a failure is kept on the row (``error_code``) and the
    next sync or provisioning retries. Audit ``messaging.template.created`` (counts)."""
    counts: Counter[str] = Counter()
    base_url = messaging_settings().public_base_url
    with tenant_session(tenant_id) as session:
        account = repo.get_account(session, account_id, lock=True)
        if account is None or account.status == "disconnected":
            return {"skipped": 1}
        provider, token = _provider(), _token(session, account)
        _ensure_template_rows(session, account_id)
        for row in repo.templates_of(session, account_id):
            if row.status != "not_created":
                continue
            try:
                components = cat.build_components(row.template_key, row.language, base_url)  # type: ignore[arg-type]
                info = provider.create_template(
                    token,
                    account.waba_id,
                    name=row.provider_name,
                    language=cat.meta_language(row.language),  # type: ignore[arg-type]
                    components=components,
                )
            except ValueError:
                repo.update_template(session, row.id, {"error_code": "public_base_url_unset"})
                counts["failed"] += 1
                continue
            except ProviderError as exc:
                repo.update_template(session, row.id, {"error_code": f"provider_{exc.code}"[:40]})
                counts["failed"] += 1
                continue
            repo.update_template(
                session,
                row.id,
                {
                    "status": _META_STATUS.get(info.status.upper(), "pending"),
                    "provider_template_id": info.template_id,
                    "provider_category": (info.category or "UTILITY").upper()[:40],
                    "error_code": None,
                    "last_synced_at": repo.now(session),
                },
            )
            counts["created"] += 1
        if counts:
            _audit(
                session,
                action="messaging.template.created",
                resource_type="messaging_account",
                resource_id=account_id,
                summary={"created": counts["created"], "failed": counts["failed"]},
                system=True,
            )
    return dict(counts)


def _apply_template_state(
    session: Session,
    account: Account,
    row: MessageTemplate,
    *,
    status: str | None,
    category: str | None,
    rejected_reason: str | None = None,
) -> None:
    """Record Meta's status/category for one template; pause it when it leaves utility."""
    values: dict[str, Any] = {"last_synced_at": repo.now(session)}
    changed: dict[str, Any] = {}
    if status is not None and status != row.status:
        values["status"] = status
        changed["status"] = status
    if rejected_reason is not None:
        values["rejected_reason"] = rejected_reason[:200]
    if category is not None and category != row.provider_category:
        values["provider_category"] = category[:40]
        changed["category"] = category[:40]
    newly_paused = category is not None and category.upper() != "UTILITY" and not row.paused_by_sos
    if newly_paused:
        values["paused_by_sos"] = True
    repo.update_template(session, row.id, values)
    if changed:
        _audit(
            session,
            action="messaging.template.status_changed",
            resource_type="messaging_template",
            resource_id=row.id,
            summary={"template_key": row.template_key, "language": row.language, **changed},
            system=True,
        )
    if newly_paused:
        # FR-MSG-007: never used again until a new version is approved as utility.
        _audit(
            session,
            action="messaging.template.paused",
            resource_type="messaging_template",
            resource_id=row.id,
            summary={"template_key": row.template_key, "language": row.language},
            system=True,
        )
        notifications.notify(
            session,
            tenant_id=account.tenant_id,
            recipients=notifications.PermissionSelector(CONFIGURE),
            template_key="messaging.template_paused",
            params={"account_id": str(account.id), "template_key": row.template_key},
            resource_id=account.id,
            dedupe_key=f"messaging.template_paused:{row.id}",
        )


def sync_templates(tenant_id: uuid.UUID) -> dict[str, int]:
    """Hourly and on request: read every template's status and category from Meta
    (FR-MSG-006, FR-MSG-007)."""
    counts: Counter[str] = Counter()
    with tenant_session(tenant_id) as session:
        account = repo.live_account(session, lock=True)
        if account is None:
            return {}
        try:
            found = _provider().list_templates(_token(session, account), account.waba_id)
        except ProviderError as exc:
            log.warning("messaging.template_sync_failed", tenant_id=tenant_id, error_code=exc.code)
            return {"failed": 1}
        by_name = {(t.name, t.language): t for t in found}
        for row in repo.templates_of(session, account.id):
            info = by_name.get((row.provider_name, cat.meta_language(row.language)))  # type: ignore[arg-type]
            if info is None:
                continue
            _apply_template_state(
                session,
                account,
                row,
                status=_META_STATUS.get(info.status.upper(), row.status),
                category=(info.category or "").upper() or None,
                rejected_reason=info.rejected_reason,
            )
            counts["synced"] += 1
    return dict(counts)


def request_template_sync(session: Session, ctx: UserContext) -> MessagingAccountOut:
    """Sync now (``messaging.configure``, step-up): templates not created yet are created, the
    rest are read back. Runs in the worker; the page reloads the statuses."""
    del ctx
    ensure_available()
    account = _live_or_409(session, lock=False)
    ops.enqueue_event(session, PROVISION_EVENT, {"account_id": account.id, "sync": True})
    return _account_out(session, account)


# --- settings ---------------------------------------------------------------------------------


def _ensure_settings(session: Session) -> MessagingSettings:
    row = repo.get_settings(session)
    if row is None:
        with _db_errors():
            try:
                savepoint = session.begin_nested()
                row = repo.insert_settings(
                    session,
                    id=new_id(),
                    notices_enabled=False,
                    absence_alerts_enabled=False,
                    fee_reminders_enabled=False,
                    fee_receipts_enabled=False,
                    absence_alert_time=dt.time(11, 0),
                    quiet_start=dt.time(21, 0),
                    quiet_end=dt.time(7, 0),
                )
                savepoint.commit()
            except IntegrityError:
                savepoint.rollback()
                row = repo.get_settings(session)
    if row is None:  # pragma: no cover - inserted or found above
        raise RuntimeError("messaging settings row missing")
    return row


def _settings_out(session: Session, row: MessagingSettings) -> MessagingSettingsOut:
    cfg = load_config()
    account = repo.live_account(session)
    return MessagingSettingsOut(
        notices_enabled=row.notices_enabled,
        absence_alerts_enabled=row.absence_alerts_enabled,
        fee_reminders_enabled=row.fee_reminders_enabled,
        fee_receipts_enabled=row.fee_receipts_enabled,
        absence_alert_time=row.absence_alert_time,
        quiet_start=row.quiet_start,
        quiet_end=row.quiet_end,
        daily_cap=row.daily_cap,
        default_daily_cap=cfg.default_daily_cap(account.messaging_limit if account else None),
        office_phone=row.office_phone,
        utility_rate_inr=str(cfg.cost.utility_rate_inr),
        gst_percent=str(cfg.cost.gst_percent),
        version=row.version,
    )


def get_messaging_settings(session: Session, ctx: UserContext) -> MessagingSettingsOut:
    del ctx
    ensure_available()
    row = repo.get_settings(session)
    if row is None:
        return MessagingSettingsOut(
            notices_enabled=False,
            absence_alerts_enabled=False,
            fee_reminders_enabled=False,
            fee_receipts_enabled=False,
            absence_alert_time=dt.time(11, 0),
            quiet_start=dt.time(21, 0),
            quiet_end=dt.time(7, 0),
            daily_cap=None,
            default_daily_cap=load_config().default_daily_cap(
                a.messaging_limit if (a := repo.live_account(session)) else None
            ),
            office_phone=None,
            utility_rate_inr=str(load_config().cost.utility_rate_inr),
            gst_percent=str(load_config().cost.gst_percent),
            version=0,
        )
    return _settings_out(session, row)


def _office_phone(value: str | None) -> str | None:
    if value is None:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 12:
        digits = digits[1:]
    if len(digits) not in (10, 11):
        raise ValidationFailed([_error("office_phone", "invalid_phone")])
    return digits


def update_messaging_settings(
    session: Session, ctx: UserContext, data: MessagingSettingsIn, *, expected_version: int
) -> MessagingSettingsOut:
    """Change switches and times (``messaging.configure``, step-up; ``If-Match``, ``W/"0"``
    before the first save). Audit ``messaging.settings.changed`` (field names only)."""
    ensure_available()
    office = _office_phone(data.office_phone)
    existing = repo.get_settings(session, lock=True)
    current = existing.version if existing is not None else 0
    if expected_version != current:
        raise PreconditionFailed("The settings were changed by someone else. Reload and try again.")
    row = existing or _ensure_settings(session)
    values = {
        "notices_enabled": data.notices_enabled,
        "absence_alerts_enabled": data.absence_alerts_enabled,
        "fee_reminders_enabled": data.fee_reminders_enabled,
        "fee_receipts_enabled": data.fee_receipts_enabled,
        "absence_alert_time": data.absence_alert_time,
        "quiet_start": data.quiet_start,
        "quiet_end": data.quiet_end,
        "daily_cap": data.daily_cap,
        "office_phone": office,
        "updated_by": ctx.user_id,
    }
    changed = sorted(k for k, v in values.items() if k != "updated_by" and getattr(row, k) != v)
    row = repo.update_settings(session, row.id, values)
    _audit(
        session,
        action="messaging.settings.changed",
        resource_type="messaging_settings",
        resource_id=row.id,
        summary={"fields": changed},
    )
    return _settings_out(session, row)


# --- opt-ins ----------------------------------------------------------------------------------


def _reach_guardian(
    session: Session, ctx: UserContext, guardian_id: uuid.UUID, permissions: Sequence[str]
) -> list[uuid.UUID]:
    """404 unless one of ``permissions`` the caller holds reaches a student the guardian is
    linked to (class teachers: their sections). Returns those students."""
    reached: list[uuid.UUID] = []
    for student_id in students.guardian_student_ids(session, guardian_id):
        for permission in permissions:
            if not ctx.has(permission):
                continue
            try:
                students.ensure_in_scope(session, ctx, student_id, permission)
            except NotFound:
                continue
            reached.append(student_id)
            break
    if not reached:
        raise NotFound("Guardian not found")
    return reached


def _for_current_number(session: Session, guardian_id: uuid.UUID, row: Optin | None) -> bool:
    if row is None or row.status != "opted_in" or row.phone_bidx is None:
        return False
    try:
        now_index = students.guardian_phone_index(
            session, guardian_id, key_version=row.phone_key_version
        )
    except crypto.KeyMaterialMissing:
        return False
    return now_index is not None and now_index.digest == bytes(row.phone_bidx)


def _category_states(
    session: Session, guardian_id: uuid.UUID, current: Mapping[tuple[uuid.UUID, str], Optin]
) -> list[CategoryStateOut]:
    out: list[CategoryStateOut] = []
    for category in OPTIN_CATEGORIES:
        row = current.get((guardian_id, category))
        out.append(
            CategoryStateOut(
                category=category,
                status=row.status if row is not None else "not_asked",
                method=row.method if row is not None else None,
                decided_on=row.decided_on if row is not None else None,
                stopped_by_parent=row is not None and row.method == "whatsapp_stop",
                for_current_number=_for_current_number(session, guardian_id, row),
            )
        )
    return out


def _optins_out(session: Session, guardian_id: uuid.UUID) -> GuardianOptinsOut:
    history = repo.optin_history(session, guardian_id)
    names = identity.member_display_names(
        session, {r.recorded_by_membership for r in history if r.recorded_by_membership}
    )
    phone = students.guardian_phone_index(session, guardian_id)
    own = students.guardian_language(session, guardian_id)
    current = repo.current_optins(session, [guardian_id])
    return GuardianOptinsOut(
        id=guardian_id,
        guardian_id=guardian_id,
        has_mobile=phone is not None,
        phone_last4=phone.last4 if phone is not None else None,
        preferred_language=own,
        effective_language=parent_language(tenancy.parent_language(session), own),
        categories=_category_states(session, guardian_id, current),
        history=[
            OptinEntryOut(
                id=r.id,
                category=r.category,
                seq=r.seq,
                status=r.status,
                method=r.method,
                wording_version=r.wording_version,
                decided_on=r.decided_on,
                evidence_document_id=r.evidence_document_id,
                recorded_by_name=(
                    names.get(r.recorded_by_membership) if r.recorded_by_membership else None
                ),
                recorded_at=r.recorded_at,
            )
            for r in history
        ],
        version=len(history),
    )


def get_guardian_optins(
    session: Session, ctx: UserContext, guardian_id: uuid.UUID
) -> GuardianOptinsOut:
    """Current state per category and the full history (``message.read`` or
    ``messaging.optin.record``; the guardian's student in scope, else 404)."""
    ensure_available()
    _reach_guardian(session, ctx, guardian_id, (READ, RECORD))
    return _optins_out(session, guardian_id)


def _insert_decision(
    session: Session,
    *,
    guardian_id: uuid.UUID,
    category: str,
    previous: Optin | None,
    status: str,
    method: str,
    decided_on: dt.date,
    evidence_document_id: uuid.UUID | None,
    phone: students.GuardianPhoneIndex | None,
    ctx: UserContext | None,
) -> Optin:
    with _db_errors():
        row = repo.insert_optin(
            session,
            id=new_id(),
            guardian_id=guardian_id,
            category=category,
            seq=(previous.seq if previous is not None else 0) + 1,
            status=status,
            method=method,
            wording_version=load_config().optin.wording_version,
            decided_on=decided_on,
            evidence_document_id=evidence_document_id,
            phone_bidx=phone.digest if (phone is not None and status == "opted_in") else None,
            phone_key_version=phone.key_version
            if (phone is not None and status == "opted_in")
            else None,
            recorded_by=ctx.user_id if ctx is not None else None,
            recorded_by_membership=ctx.membership_id if ctx is not None else None,
        )
    _audit(
        session,
        action="messaging.optin.recorded",
        resource_type="guardian",
        resource_id=guardian_id,
        summary={
            "optin_id": row.id,
            "category": category,
            "status": status,
            "method": method,
            "previous_status": previous.status if previous is not None else "not_asked",
            "has_evidence": evidence_document_id is not None,
            "evidence_document_id": evidence_document_id,
        },
        system=ctx is None,
    )
    return row


def record_optin(
    session: Session,
    ctx: UserContext,
    guardian_id: uuid.UUID,
    data: OptinIn,
    *,
    expected_version: int | None = None,
) -> GuardianOptinsOut:
    """Record a guardian's decision for one category (FR-MSG-008; ``messaging.optin.record``,
    student in scope). Errors: 404; 412 stale ``If-Match`` (the number of decisions read); 422
    ``no_mobile_number``, ``evidence_required``, ``date_in_future``, ``date_too_early``,
    ``evidence_document_id`` ``not_found``; 409 ``opted_out_by_parent`` (after the parent's STOP
    only a signed form or the parent's own join opts back in). An opt-out revokes the
    guardian's open parent links. Audit ``messaging.optin.recorded``."""
    ensure_available()
    _reach_guardian(session, ctx, guardian_id, (RECORD,))
    history = repo.optin_history(session, guardian_id)
    if expected_version is not None and expected_version != len(history):
        raise PreconditionFailed(
            "Someone else recorded a decision for this guardian just now. Reload and try again."
        )
    previous = repo.current_optins(session, [guardian_id]).get((guardian_id, data.category))
    if (data.status == "opted_in") == (data.method == "staff_recorded_stop"):
        raise ValidationFailed([_error("method", "method_does_not_match_status")])
    problems: list[dict[str, str]] = []
    today = _today(session)
    if data.decided_on > today:
        problems.append(_error("decided_on", "date_in_future"))
    elif data.decided_on < today - dt.timedelta(days=load_config().optin.max_decision_age_days):
        problems.append(_error("decided_on", "date_too_early"))
    if data.method == "signed_form" and data.evidence_document_id is None:
        problems.append(_error("evidence_document_id", "evidence_required"))
    if data.evidence_document_id is not None and not (
        documents.is_visible(session, ctx, data.evidence_document_id)
        and documents.evidence_exists(session, data.evidence_document_id)
    ):
        problems.append(_error("evidence_document_id", "not_found"))
    phone = students.guardian_phone_index(session, guardian_id)
    if data.status == "opted_in" and phone is None:
        problems.append(_error("guardian_id", "no_mobile_number"))
    if problems:
        raise ValidationFailed(problems)
    if (
        data.status == "opted_in"
        and previous is not None
        and previous.method == "whatsapp_stop"
        and data.method not in _PARENT_ACTIONS
    ):
        raise OptedOutByParent(
            "The parent stopped messages from WhatsApp. Record a new signed form, or ask the "
            "parent to send the join message again."
        )
    _insert_decision(
        session,
        guardian_id=guardian_id,
        category=data.category,
        previous=previous,
        status=data.status,
        method=data.method,
        decided_on=data.decided_on,
        evidence_document_id=data.evidence_document_id,
        phone=phone,
        ctx=ctx,
    )
    if data.status == "opted_out":
        public.revoke_for_guardian(session, guardian_id, reason="opted_out")
    return _optins_out(session, guardian_id)


def _reach_section(
    session: Session, ctx: UserContext, section_id: uuid.UUID, permission: str
) -> list[uuid.UUID]:
    try:
        section = tenancy.get_section(session, section_id)
    except NotFound:
        raise NotFound("Section not found") from None
    reach = students.list_students_in_scope(
        session, ctx, section_ids=[section_id], permissions=(permission,)
    )
    if reach:
        return reach
    grant = ctx.scope_for(permission)
    if grant.school_wide or section_id in grant.section_ids or section.class_id in grant.class_ids:
        return []
    raise NotFound("Section not found")


def section_summary(
    session: Session, ctx: UserContext, section_id: uuid.UUID
) -> SectionOptinSummaryOut:
    """Opted in / opted out / not asked per category for the guardians of a section's students
    (US-2202 AC3; ``message.read``, scope). Counts only."""
    ensure_available()
    student_ids = _reach_section(session, ctx, section_id, READ)
    guardians = students.guardians_of_students(session, student_ids)
    guardian_ids = sorted({g.guardian_id for g in guardians})
    current = repo.current_optins(session, guardian_ids)
    counts: dict[OptinCategory, Counter[str]] = {c: Counter() for c in OPTIN_CATEGORIES}
    for guardian_id in guardian_ids:
        for category in OPTIN_CATEGORIES:
            row = current.get((guardian_id, category))
            counts[category][row.status if row is not None else "not_asked"] += 1
    return SectionOptinSummaryOut(
        section_id=section_id,
        students=len(student_ids),
        guardians=len(guardian_ids),
        guardians_without_mobile=len({g.guardian_id for g in guardians if not g.has_phone}),
        categories={
            c: CategoryCounts(
                opted_in=counts[c]["opted_in"],
                opted_out=counts[c]["opted_out"],
                not_asked=counts[c]["not_asked"],
            )
            for c in OPTIN_CATEGORIES
        },
    )


# --- join slips (FR-MSG-009) ------------------------------------------------------------------


def _code_hash(code: str) -> bytes:
    return hashlib.sha256(code.encode("ascii")).digest()


def _new_code() -> str:
    cfg = load_config().optin
    return "".join(secrets.choice(cfg.join_code_alphabet) for _ in range(cfg.join_code_length))


def join_slips(session: Session, ctx: UserContext, data: JoinSlipsIn) -> str:
    """Printable A4 join slips (two per page) for the guardians with a mobile number of the
    given students or section, in each guardian's parent language (FR-MSG-009, US-2202 AC2;
    ``messaging.optin.record``, scope). Each slip carries a new single-use code valid 30 days;
    only its hash is stored. 409 ``messaging_not_connected``, ``no_slips``, ``too_many_slips``.
    Audit ``messaging.join_slips.printed`` (count)."""
    ensure_available()
    # Scope first: a section or student out of reach is 404 whether or not WhatsApp is connected.
    if data.section_id is not None:
        student_ids = _reach_section(session, ctx, data.section_id, RECORD)
    else:
        student_ids = []
        for student_id in dict.fromkeys(data.student_ids or []):
            students.ensure_in_scope(session, ctx, student_id, RECORD)
            student_ids.append(student_id)
    account = repo.live_account(session)
    if account is None or not account.display_phone:
        raise NotConnected("Connect WhatsApp before printing join slips.")
    names = students.summaries(session, ctx, student_ids)
    guardians = [g for g in students.guardians_of_students(session, student_ids) if g.has_phone]
    cfg = load_config().optin
    if not guardians:
        raise Conflict("No guardian of these students has a mobile number.", code="no_slips")
    if len(guardians) > cfg.max_slips_per_print:
        raise Conflict(
            f"Print at most {cfg.max_slips_per_print} slips at a time.", code="too_many_slips"
        )
    school = tenancy.school_display_name(session)
    school_language = tenancy.parent_language(session)
    expires = repo.now(session) + dt.timedelta(days=cfg.join_code_days)
    number = "".join(ch for ch in account.display_phone if ch.isdigit())
    items: list[slips.Slip] = []
    for guardian in guardians:
        code = _new_code()
        repo.insert_join_code(
            session,
            id=new_id(),
            code_hash=_code_hash(code),
            guardian_id=guardian.guardian_id,
            categories=list(data.categories),
            expires_at=expires,
            created_by=ctx.user_id,
        )
        summary = names.get(guardian.student_id)
        items.append(
            slips.Slip(
                language=parent_language(school_language, guardian.preferred_language),
                student_name=(summary.display_name or "") if summary is not None else "",
                class_section=(summary.class_section or "") if summary is not None else "",
                code=code,
                categories=tuple(data.categories),
                valid_until=expires.astimezone(IST).date(),
            )
        )
    page = slips.render(school_name=school, whatsapp_number=number, slips=items)
    _audit(
        session,
        action="messaging.join_slips.printed",
        resource_type="section" if data.section_id is not None else "student",
        resource_id=data.section_id,
        summary={"count": len(items), "categories": sorted(data.categories)},
    )
    return page


# --- messages: queue, deliver, history --------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QueuedMessage:
    message_id: uuid.UUID
    created: bool


def queue_message(
    session: Session,
    *,
    purpose: str,
    guardian_id: uuid.UUID | None,
    student_id: uuid.UUID | None,
    source_type: str,
    source_id: uuid.UUID,
    dedupe_key: str,
    variables: Mapping[str, str] | None = None,
    text_key: cat.TextKey | None = None,
    created_by: uuid.UUID | None = None,
) -> QueuedMessage | None:
    """Queue one message to a guardian (idempotent per purpose, source, guardian and dedupe
    key; ``None`` when it exists). Template purposes take the catalogue variables (encrypted on
    the row, never logged); ``optin_stopped`` is a free-form text. Language: the guardian's,
    else the school's parent language; Telugu falls back to English when the Telugu template is
    not usable (``language_fallback``, FR-LANG-004). Audit ``messaging.message.queued``."""
    own = students.guardian_language(session, guardian_id) if guardian_id else None
    language: Language = parent_language(tenancy.parent_language(session), own)
    fallback = False
    template_key: str | None = None
    template_version: int | None = None
    if text_key is None:
        item = cat.template(purpose)
        template_key, template_version = purpose, item.version
        account = repo.live_account(session)
        if language == "te" and account is not None:
            te = repo.template_for(session, account.id, purpose, item.version, "te")
            state = (
                rules.TemplateState(te.status, te.provider_category, te.paused_by_sos)
                if te
                else None
            )
            if not rules.template_usable(state):
                language, fallback = "en", True
        values = cat.check_variables(purpose, variables or {})
    else:
        values = []
    phone = students.guardian_phone_index(session, guardian_id) if guardian_id else None
    message_id = new_id()
    vars_blob = (
        crypto.encrypt_value(
            session,
            json.dumps(values, ensure_ascii=False),
            table=MESSAGES_TABLE,
            column="vars_ciphertext",
            row_id=message_id,
        )[0]
        if values
        else None
    )
    row = repo.insert_message(
        session,
        id=message_id,
        purpose=purpose,
        kind="template" if text_key is None else "text",
        template_key=template_key,
        template_version=template_version,
        language=language,
        language_fallback=fallback,
        guardian_id=guardian_id,
        student_id=student_id,
        source_type=source_type,
        source_id=source_id,
        dedupe_key=dedupe_key,
        phone_bidx=phone.digest if phone else None,
        phone_key_version=phone.key_version if phone else None,
        phone_last4=phone.last4 if phone else None,
        vars_ciphertext=vars_blob,
        created_by=created_by,
    )
    if row is None:
        return None
    _audit(
        session,
        action="messaging.message.queued",
        resource_type="message",
        resource_id=row.id,
        summary={
            "purpose": purpose,
            "template_key": template_key,
            "language": language,
            "language_fallback": fallback,
            "guardian_id": guardian_id,
        },
        system=created_by is None,
    )
    return QueuedMessage(row.id, True)


@dataclass(frozen=True, slots=True)
class _Prepared:
    message_id: uuid.UUID
    token: str
    phone_number_id: str
    to: str
    template: TemplateMessage | None
    text: str | None


def _optin_state(
    session: Session, guardian_id: uuid.UUID | None, category: str | None
) -> rules.OptinState | None:
    if guardian_id is None or category is None:
        return None
    row = repo.current_optins(session, [guardian_id]).get((guardian_id, category))
    if row is None:
        return None
    return rules.OptinState(row.status, _for_current_number(session, guardian_id, row))


def _prepare(
    session: Session, message: Message, to_override: str | None, text_body: str | None
) -> _Prepared | str:
    """Everything needed to call the provider, or the skip reason."""
    account = repo.live_account(session)
    account_state = rules.AccountState(account.status, account.currency) if account else None
    template_row = None
    category: str | None = None
    if message.kind == "template" and account is not None and message.template_key is not None:
        template_row = repo.template_for(
            session,
            account.id,
            message.template_key,
            message.template_version or 1,
            message.language,
        )
        category = cat.template(message.template_key).optin_category
    template_state = (
        rules.TemplateState(
            template_row.status, template_row.provider_category, template_row.paused_by_sos
        )
        if template_row is not None
        else None
    )
    phone = (
        to_override
        if to_override is not None
        else (
            students.guardian_phone_for_sending(session, message.guardian_id)
            if message.guardian_id is not None
            else None
        )
    )
    reason = rules.skip_reason(
        account=account_state,
        template=template_state,
        needs_template=message.kind == "template",
        optin=_optin_state(session, message.guardian_id, category),
        needs_optin=category is not None,
        phone=phone,
        today=_today(session),
        inr_required_from=load_config().sending.inr_required_from,
    )
    if reason is not None:
        return reason
    to = rules.whatsapp_number(phone)
    if account is None or to is None:  # pragma: no cover - skip_reason refused both
        return "messaging_not_connected" if account is None else "no_mobile_number"
    template: TemplateMessage | None = None
    if message.kind == "template" and template_row is not None and message.template_key:
        values: list[str] = []
        if message.vars_ciphertext is not None:
            values = json.loads(
                crypto.decrypt_value(
                    session,
                    bytes(message.vars_ciphertext),
                    table=MESSAGES_TABLE,
                    column="vars_ciphertext",
                    row_id=message.id,
                )
            )
        buttons = cat.template(message.template_key).buttons
        template = TemplateMessage(
            name=template_row.provider_name,
            language=cat.meta_language(message.language),  # type: ignore[arg-type]
            body_values=values,
            url_button_index=buttons.index("url") if "url" in buttons else None,
            stop_button_index=buttons.index("stop") if "stop" in buttons else None,
            stop_payload=load_config().keywords.stop_button_payload,
        )
    return _Prepared(
        message_id=message.id,
        token=_token(session, account),
        phone_number_id=account.phone_number_id,
        to=to,
        template=template,
        text=text_body,
    )


def deliver(
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
    *,
    to: str | None = None,
    text_body: str | None = None,
) -> str:
    """Send one queued message now (worker). Checks the account, template, opt-in and number
    (:mod:`app.messaging.eligibility`; FR-MSG-011 as far as this wave goes), decrypts the
    number only here (FR-MSG-019), calls the provider outside the transaction and records the
    outcome: ``sent`` with Meta's message id, ``skipped`` with the reason, ``failed`` with the
    error for permanent errors, or still ``queued`` with one more attempt for transient ones.
    ``to`` (10-digit mobile) / ``text_body``: a free-form reply to the number that wrote (inside
    its window).
    Returns the resulting status."""
    with tenant_session(tenant_id) as session:
        message = repo.get_message(session, message_id, lock=True)
        if message is None or message.status != "queued":
            return message.status if message is not None else "missing"
        prepared = _prepare(session, message, to, text_body)
        if isinstance(prepared, str):
            repo.update_message(session, message_id, {"status": "skipped", "skip_reason": prepared})
            return "skipped"
        attempts = message.attempts + 1
    provider = _provider()
    try:
        if prepared.template is not None:
            result = provider.send_template(
                prepared.token, prepared.phone_number_id, prepared.to, prepared.template
            )
        else:
            result = provider.send_text(
                prepared.token, prepared.phone_number_id, prepared.to, prepared.text or ""
            )
    except ProviderError as exc:
        code = f"meta:{exc.meta_code}" if exc.meta_code is not None else exc.code
        cfg = load_config().sending
        transient = exc.transient and exc.meta_code not in cfg.permanent_error_codes
        with tenant_session(tenant_id) as session:
            if transient and attempts < cfg.max_attempts:
                repo.update_message(session, message_id, {"attempts": attempts, "error_code": code})
                status = "queued"
            else:
                repo.update_message(
                    session,
                    message_id,
                    {
                        "status": "failed",
                        "attempts": attempts,
                        "error_code": code,
                        "failed_at": repo.now(session),
                    },
                )
                status = "failed"
        log.warning(
            "messaging.send_failed",
            tenant_id=tenant_id,
            resource_id=message_id,
            error_code=code[:40],
            outcome=status,
        )
        return status
    with tenant_session(tenant_id) as session:
        repo.update_message(
            session,
            message_id,
            {
                "status": "sent",
                "attempts": attempts,
                "provider_message_id": result.provider_message_id,
                "error_code": None,
                "sent_at": repo.now(session),
            },
        )
    return "sent"


def _error_reason(code: str | None) -> str | None:
    if code is None:
        return None
    if code.startswith("meta:"):
        try:
            return _PERMANENT_REASONS.get(int(code[5:]), "provider_error")
        except ValueError:
            return "provider_error"
    return code


def _message_out(row: Message) -> MessagingMessageOut:
    return MessagingMessageOut(
        id=row.id,
        purpose=row.purpose,
        kind=row.kind,
        template_key=row.template_key,
        template_version=row.template_version,
        language=row.language,
        language_fallback=row.language_fallback,
        guardian_id=row.guardian_id,
        student_id=row.student_id,
        source_type=row.source_type,
        source_id=row.source_id,
        phone_last4=row.phone_last4,
        status=row.status,
        skip_reason=row.skip_reason,
        error_code=row.error_code,
        error_reason=_error_reason(row.error_code),
        attempts=row.attempts,
        queued_at=row.queued_at,
        sent_at=row.sent_at,
        delivered_at=row.delivered_at,
        read_at=row.read_at,
        failed_at=row.failed_at,
    )


def search_messages(
    session: Session, ctx: UserContext, data: MessagingSearchIn
) -> MessagingMessagePage:
    """Message history (US-2208; ``message.read``). School-wide readers may list everything;
    scoped readers (class teachers) must name a student or guardian in their sections (422
    ``filter_required``). A student or guardian outside reach is 404. Phones appear only as the
    last four digits (FR-MSG-017)."""
    ensure_available()
    school_wide = ctx.scope_for(READ).school_wide
    guardian_ids: list[uuid.UUID] | None = None
    student_ids: list[uuid.UUID] | None = None
    if data.guardian_id is not None:
        _reach_guardian(session, ctx, data.guardian_id, (READ,))
        guardian_ids = [data.guardian_id]
    if data.student_id is not None:
        students.ensure_in_scope(session, ctx, data.student_id, READ)
        student_ids = [data.student_id]
        if guardian_ids is None:
            guardian_ids = [
                g.guardian_id for g in students.guardians_of_students(session, [data.student_id])
            ]
    if not school_wide and guardian_ids is None and student_ids is None:
        raise ValidationFailed([_error("student_id", "filter_required")])
    cursor = decode_cursor(data.cursor)
    after = None
    if cursor is not None:
        try:
            after = (dt.datetime.fromisoformat(str(cursor["at"])), uuid.UUID(str(cursor["id"])))
        except (KeyError, ValueError):
            raise ValidationFailed([_error("cursor", "invalid")]) from None

    def _start(day: dt.date | None) -> dt.datetime | None:
        return dt.datetime.combine(day, dt.time(0), tzinfo=IST) if day else None

    rows = repo.search_messages(
        session,
        guardian_ids=guardian_ids,
        student_ids=student_ids,
        purpose=data.purpose,
        status=data.status,
        since=_start(data.since),
        until=_start(data.until + dt.timedelta(days=1)) if data.until else None,
        after=after,
        limit=data.limit + 1,
    )
    more = len(rows) > data.limit
    rows = rows[: data.limit]
    next_cursor = (
        encode_cursor({"at": rows[-1].queued_at.isoformat(), "id": str(rows[-1].id)})
        if more and rows
        else None
    )
    return MessagingMessagePage(items=[_message_out(r) for r in rows], next_cursor=next_cursor)


# --- webhook intake (API process) -------------------------------------------------------------


def store_events(
    tenant_id: uuid.UUID, handle_id: uuid.UUID, waba_id: str, events: Sequence[ParsedEvent]
) -> int:
    """Store one school's verified events in its inbox (encrypted, deduplicated by key) and wake
    the worker (FR-MSG-020). The handle and the account are re-read under RLS; events for a
    disconnected account or another WABA are dropped. Returns how many were new."""
    if not events:
        return 0
    with tenant_session(tenant_id) as session:
        account = repo.live_account(session)
        if (
            account is None
            or account.waba_id != waba_id
            or account.waba_handle_id != handle_id
            or public.load_handle(session, handle_id, ("meta_waba",)) is None
        ):
            log.warning("messaging.webhook.no_account", tenant_id=tenant_id, outcome="dropped")
            return 0
        stored = 0
        for event in events:
            event_id = new_id()
            blob, _ = crypto.encrypt_value(
                session,
                json.dumps(event.payload, ensure_ascii=False, sort_keys=True),
                table=EVENTS_TABLE,
                column="payload_ciphertext",
                row_id=event_id,
            )
            inserted = repo.insert_provider_event(
                session,
                id=event_id,
                provider=PROVIDER,
                account_id=account.id,
                event_key=event.event_key,
                event_type=event.event_type,
                event_at=event.event_at,
                payload_ciphertext=blob,
            )
            stored += int(inserted is not None)
        if stored:
            ops.enqueue_event(session, INBOX_EVENT, {"count": stored})
        return stored


# --- inbox processing (worker) ----------------------------------------------------------------


@dataclass
class _Outbox:
    """Sends to make after the processing transaction commits."""

    deliveries: list[tuple[uuid.UUID, str | None, str | None]]
    replies: list[tuple[str, str]]


def _apply_status(session: Session, payload: Mapping[str, Any], counts: Counter[str]) -> str:
    message = repo.message_by_provider_id(session, str(payload.get("id") or ""))
    if message is None:
        return "ignored"
    new = str(payload.get("status") or "").lower()
    old = message.status
    if old in ("read", "failed", "skipped"):
        counts["late"] += 1
        return "ignored"
    values: dict[str, Any] = {}
    now = repo.now(session)
    if new == "failed":
        if old not in ("queued", "sent"):
            counts["late"] += 1
            return "ignored"
        code = payload.get("error_code")
        values = {
            "status": "failed",
            "failed_at": now,
            "error_code": f"meta:{code}" if code else "meta_failed",
        }
    elif new in _STATUS_RANK:
        if _STATUS_RANK[new] <= _STATUS_RANK.get(old, 0):
            counts["late"] += 1
            return "ignored"  # out of order: never move backwards (FR-MSG-016)
        values = {"status": new, f"{new}_at": now}
        if new in ("delivered", "read") and message.sent_at is None:
            values["sent_at"] = now
        if new == "read" and message.delivered_at is None:
            values["delivered_at"] = now
    else:
        return "ignored"
    if isinstance(payload.get("billable"), bool):
        values["billable"] = payload["billable"]
    category = payload.get("pricing_category")
    if isinstance(category, str) and category.isascii():
        values["pricing_category"] = category.lower()[:40]
    repo.update_message(session, message.id, values)
    counts[new] += 1
    return "processed"


def _stop(session: Session, guardian_ids: Sequence[uuid.UUID], today: dt.date) -> int:
    recorded = 0
    current = repo.current_optins(session, guardian_ids)
    for guardian_id in guardian_ids:
        for category in OPTIN_CATEGORIES:
            previous = current.get((guardian_id, category))
            if previous is not None and previous.method == "whatsapp_stop":
                continue
            _insert_decision(
                session,
                guardian_id=guardian_id,
                category=category,
                previous=previous,
                status="opted_out",
                method="whatsapp_stop",
                decided_on=today,
                evidence_document_id=None,
                phone=None,
                ctx=None,
            )
            recorded += 1
        # FR-MSG-010 / FR-PUB-007: links sent to this guardian stop working at once.
        public.revoke_for_guardian(session, guardian_id, reason="opted_out", by_user=False)
    return recorded


def _join(
    session: Session, code: str | None, guardian_ids: Sequence[uuid.UUID], today: dt.date
) -> tuple[str, uuid.UUID | None]:
    if code is None:
        return "join_without_code", None
    row = repo.join_code_by_hash(session, _code_hash(code))
    now = repo.now(session)
    if row is None or row.used_at is not None or row.expires_at <= now:
        return "join_code_invalid", None
    if row.guardian_id not in guardian_ids:
        # A code sent from another number: refused silently, counted (FR-MSG-009).
        log.warning("messaging.join_refused", resource_id=row.id, outcome="other_number")
        return "join_other_number", None
    phone = students.guardian_phone_index(session, row.guardian_id)
    current = repo.current_optins(session, [row.guardian_id])
    for category in row.categories:
        _insert_decision(
            session,
            guardian_id=row.guardian_id,
            category=category,
            previous=current.get((row.guardian_id, category)),
            status="opted_in",
            method="whatsapp_join",
            decided_on=today,
            evidence_document_id=None,
            phone=phone,
            ctx=None,
        )
    repo.mark_join_code_used(session, row.id, now)
    return "joined", row.guardian_id


def _apply_inbound(  # noqa: PLR0911 - one return per inbound outcome
    session: Session, event_id: uuid.UUID, payload: Mapping[str, Any], outbox: _Outbox
) -> str:
    mobile = rules.mobile_from_wa_id(payload.get("wa_id"))
    if mobile is None:
        return "ignored"  # not an Indian mobile number: never one of the school's guardians
    wa_id = "91" + mobile
    index = students.phone_index(session, mobile)
    guardian_ids = students.guardian_ids_by_phone(session, mobile)
    kind = str(payload.get("kind") or "other")
    today = _today(session)
    inbound = repo.insert_inbound(
        session,
        id=new_id(),
        phone_bidx=index.digest,
        phone_key_version=index.key_version,
        kind=kind if kind in ("join", "stop", "other") else "other",
        received_at=repo.now(session),
    )
    school = tenancy.school_display_name(session)
    if kind == "stop":
        if not guardian_ids:
            repo.mark_inbound(session, inbound.id, replied=False, outcome="unknown_number")
            return "processed"
        _stop(session, guardian_ids, today)
        queued = queue_message(
            session,
            purpose="optin_stopped",
            guardian_id=guardian_ids[0],
            student_id=None,
            source_type="inbound",
            source_id=inbound.id,
            dedupe_key="stop",
            text_key="optin_stopped",
        )
        if queued is not None:
            message = repo.get_message(session, queued.message_id)
            language: Language = message.language if message else "en"  # type: ignore[assignment]
            body = cat.render_text("optin_stopped", language, school_name=school)
            outbox.deliveries.append((queued.message_id, mobile, body))
        repo.mark_inbound(session, inbound.id, replied=True, outcome="stopped")
        return "processed"
    if kind == "join":
        outcome, guardian_id = _join(session, payload.get("code"), guardian_ids, today)
        if guardian_id is not None:
            queued = queue_message(
                session,
                purpose="optin_confirm",
                guardian_id=guardian_id,
                student_id=None,
                source_type="inbound",
                source_id=inbound.id,
                dedupe_key="join",
                variables={"school_name": school},
            )
            if queued is not None:
                outbox.deliveries.append((queued.message_id, mobile, None))
            repo.mark_inbound(session, inbound.id, replied=True, outcome=outcome)
            return "processed"
        if outcome == "join_other_number":
            repo.mark_inbound(session, inbound.id, replied=False, outcome=outcome)
            return "processed"
        # A join without a valid code is answered like any other message.
    # Any other message: at most one automatic reply a day per number (FR-MSG-018).
    start = dt.datetime.combine(today, dt.time(0), tzinfo=IST)
    if repo.replies_since(session, index.digest, start) >= load_config().auto_reply_per_day:
        repo.mark_inbound(session, inbound.id, replied=False, outcome="reply_limit")
        return "processed"
    settings_row = repo.get_settings(session)
    office = settings_row.office_phone if settings_row is not None else None
    own = students.guardian_language(session, guardian_ids[0]) if guardian_ids else None
    language = parent_language(tenancy.parent_language(session), own)
    body = (
        cat.render_text("auto_reply", language, school_name=school, office_phone=office)
        if office
        else cat.render_text("auto_reply_no_phone", language, school_name=school)
    )
    outbox.replies.append((wa_id, body))
    repo.mark_inbound(session, inbound.id, replied=True, outcome="auto_reply")
    del event_id
    return "processed"


def _apply_template_event(
    session: Session, account: Account, event_type: str, payload: Mapping[str, Any]
) -> str:
    name = str(payload.get("message_template_name") or "")
    language = str(payload.get("message_template_language") or "")
    row = next(
        (
            t
            for t in repo.templates_of(session, account.id)
            if t.provider_name == name and cat.meta_language(t.language) == language  # type: ignore[arg-type]
        ),
        None,
    )
    if row is None:
        return "ignored"
    if event_type == "template_status":
        status = _META_STATUS.get(str(payload.get("event") or "").upper())
        _apply_template_state(
            session,
            account,
            row,
            status=status,
            category=None,
            rejected_reason=str(payload["reason"]) if payload.get("reason") else None,
        )
    else:
        category = str(payload.get("new_category") or "").upper() or None
        _apply_template_state(session, account, row, status=None, category=category)
    return "processed"


_RESTRICTIONS: Final = {
    "ACCOUNT_RESTRICTION": "account_restricted",
    "ACCOUNT_VIOLATION": "account_restricted",
    "DISABLED_UPDATE": "number_disabled",
    "ACCOUNT_DELETED": "account_deleted",
}


def _apply_account_event(
    session: Session, account: Account, event_type: str, payload: Mapping[str, Any]
) -> str:
    event = str(payload.get("event") or "").upper()
    if event_type == "phone_quality":
        limit = payload.get("current_limit")
        if isinstance(limit, str) and limit in load_config().messaging_limits:
            repo.update_account(session, account.id, {"messaging_limit": limit})
        if event == "FLAGGED":
            repo.update_account(session, account.id, {"quality_rating": "FLAGGED"})
            _pause(session, account, "quality_flagged")
        elif event == "UNFLAGGED":
            repo.update_account(session, account.id, {"quality_rating": "GREEN"})
        return "processed"
    reason = _RESTRICTIONS.get(event)
    if reason is not None:
        _pause(session, account, reason)
        return "processed"
    return "ignored"


def process_inbox(tenant_id: uuid.UUID) -> dict[str, int]:  # noqa: PLR0912 - one branch per event type
    """Apply the school's stored webhook events in arrival order (worker; FR-MSG-010,
    FR-MSG-016, FR-MSG-021): statuses, inbound keywords, template and account updates. Each
    event is applied in a savepoint (a broken one is retried, then marked ``failed``). Sends
    the confirmations and replies after the transaction commits. Audit
    ``messaging.statuses.applied`` with counts per run (not one event per status)."""
    cfg = load_config().inbox
    totals: Counter[str] = Counter()
    for _ in range(50):
        outbox = _Outbox(deliveries=[], replies=[])
        statuses: Counter[str] = Counter()
        with tenant_session(tenant_id) as session:
            events = repo.pending_events(session, cfg.batch_size)
            if not events:
                break
            account = repo.live_account(session)
            for event in events:
                savepoint = session.begin_nested()
                try:
                    payload = json.loads(
                        crypto.decrypt_value(
                            session,
                            bytes(event.payload_ciphertext or b""),
                            table=EVENTS_TABLE,
                            column="payload_ciphertext",
                            row_id=event.id,
                        )
                    )
                    if account is None or account.id != event.account_id:
                        outcome = "ignored"
                    elif event.event_type == "message_status":
                        outcome = _apply_status(session, payload, statuses)
                    elif event.event_type == "inbound_message":
                        outcome = _apply_inbound(session, event.id, payload, outbox)
                    elif event.event_type in ("template_status", "template_category"):
                        outcome = _apply_template_event(session, account, event.event_type, payload)
                    elif event.event_type in ("phone_quality", "account_update"):
                        outcome = _apply_account_event(session, account, event.event_type, payload)
                    else:
                        outcome = "ignored"
                    savepoint.commit()
                except (DBAPIError, CryptoError, ValueError, KeyError) as exc:
                    savepoint.rollback()
                    attempts = event.attempts + 1
                    final = attempts >= cfg.max_attempts
                    repo.finish_event(
                        session,
                        event.id,
                        status="failed" if final else "received",
                        attempts=attempts,
                        error_code="apply_failed",
                    )
                    log.warning(
                        "messaging.inbox.failed",
                        tenant_id=tenant_id,
                        resource_id=event.id,
                        error_type=type(exc).__name__,
                    )
                    totals["failed"] += 1
                    continue
                repo.finish_event(
                    session, event.id, status=outcome, attempts=event.attempts + 1, error_code=None
                )
                totals[outcome] += 1
            if statuses:
                _audit(
                    session,
                    action="messaging.statuses.applied",
                    resource_type="messaging_account",
                    resource_id=account.id if account else None,
                    summary=dict(statuses),
                    system=True,
                )
            reply_token: tuple[str, str] | None = None
            if outbox.replies and account is not None and account.status != "disconnected":
                reply_token = (_token(session, account), account.phone_number_id)
        for message_id, to, body in outbox.deliveries:
            deliver(tenant_id, message_id, to=to, text_body=body)
        if reply_token is not None:
            provider = _provider()
            for to, body in outbox.replies:
                try:
                    provider.send_text(reply_token[0], reply_token[1], to, body)
                except ProviderError as exc:
                    log.warning("messaging.reply_failed", tenant_id=tenant_id, error_code=exc.code)
        if len(events) < cfg.batch_size:
            break
    return dict(totals)


# --- retention (FR-MSG-023) -------------------------------------------------------------------


def purge_expired(tenant_id: uuid.UUID) -> dict[str, int]:
    """Daily: provider events 30 days after processing, inbound kinds after 90 days, message
    variables after 180 days, messages after 13 months, expired join codes (docs/05 §13)."""
    cfg = load_config().retention
    out: Counter[str] = Counter()
    jobs: list[tuple[str, Callable[[Session, dt.datetime, int], int], int]] = [
        ("provider_events", repo.purge_provider_events, cfg.provider_events_days),
        ("inbound_events", repo.purge_inbound, cfg.inbound_events_days),
        ("message_vars", repo.erase_message_vars, cfg.message_vars_days),
        ("messages", repo.purge_messages, cfg.messages_days),
        ("join_codes", repo.purge_join_codes, cfg.join_codes_after_expiry_days),
    ]
    for name, job, days in jobs:
        while True:
            with tenant_session(tenant_id) as session:
                before = repo.now(session) - dt.timedelta(days=days)
                done = job(session, before, cfg.purge_batch_size)
            out[name] += done
            if done < cfg.purge_batch_size:
                break
    return dict(out)


def live_tenant_ids() -> list[uuid.UUID]:
    with context_free_session() as session:
        return tenancy.list_tenant_ids(session, ("active",))


# --- full data export and offboarding (FR-ADM-001, FR-PLT-005, ADR-0029) ----------------------


def reencrypt_batch(
    session: Session, keyring: crypto.TenantKeyring, target_version: int, limit: int
) -> int:
    """DEK rotation (``app.students.rotation.Reencryptor``; SEC-012): message variables and
    inbox payloads under another key version are re-encrypted to ``target_version`` with the
    same associated data, and opt-in phone indexes are recomputed under it (an index that no
    longer matches the guardian's number becomes one that matches nothing, as before: the
    number changed). Rows are locked (``SKIP LOCKED``); returns how many were rewritten."""
    done = 0
    for message in repo.stale_message_vars(session, target_version, limit):
        blob = crypto.reencrypt_value(
            session,
            bytes(message.vars_ciphertext or b""),
            table=MESSAGES_TABLE,
            column="vars_ciphertext",
            row_id=message.id,
            key_version=target_version,
            keyring=keyring,
        )
        repo.set_message_vars(session, message.id, blob)
        done += 1
    for event in repo.stale_event_payloads(session, target_version, limit - done):
        blob = crypto.reencrypt_value(
            session,
            bytes(event.payload_ciphertext or b""),
            table=EVENTS_TABLE,
            column="payload_ciphertext",
            row_id=event.id,
            key_version=target_version,
            keyring=keyring,
        )
        repo.set_event_payload(session, event.id, blob)
        done += 1
    for optin in repo.stale_optin_indexes(session, target_version, limit - done):
        still_same = _for_current_number(session, optin.guardian_id, optin)
        fresh = (
            students.guardian_phone_index(session, optin.guardian_id, key_version=target_version)
            if still_same
            else None
        )
        digest = (
            fresh.digest
            if fresh is not None
            else hashlib.sha256(b"sos-optin-other-number|" + optin.id.bytes).digest()
        )
        repo.set_optin_index(session, optin.id, digest, target_version)
        done += 1
    return done


key_rotation.register_reencryptor("messaging", reencrypt_batch)


def export_records(session: Session) -> list[RecordTable]:
    """The school's messaging records for its full data export: accounts (no token or PIN),
    templates, settings, opt-ins and message history (no blind indexes, variables or handles).
    Join codes, inbound kinds and the provider inbox are not exported (hashes, raw payloads)."""
    return repo.export_tables(session)


_PURGE = purging.PurgeTables(
    deleted=(
        "msg.provider_events",
        "msg.inbound_events",
        "msg.messages",
        "msg.join_codes",
        "msg.optins",
        "msg.settings",
        "msg.templates",
        "msg.accounts",
    )
)


def tenant_data_counts(session: Session) -> dict[str, int]:
    return _PURGE.count(session)


def purge_tenant_data(session: Session) -> dict[str, int]:
    return _PURGE.delete(session)


tenancy.register_data_owner(
    tenancy.TenantDataOwner(name="messaging", count=tenant_data_counts, purge=purge_tenant_data)
)


__all__ = [
    "CONFIGURE",
    "READ",
    "RECORD",
    "QueuedMessage",
    "available",
    "connect_embedded_signup",
    "connect_manual",
    "deliver",
    "disconnect",
    "ensure_available",
    "export_records",
    "get_account",
    "get_guardian_optins",
    "get_messaging_settings",
    "join_slips",
    "list_templates",
    "live_tenant_ids",
    "process_inbox",
    "provision_templates",
    "purge_expired",
    "purge_tenant_data",
    "queue_message",
    "record_optin",
    "request_template_sync",
    "resume",
    "search_messages",
    "section_summary",
    "store_events",
    "sync_account",
    "sync_templates",
    "tenant_data_counts",
    "update_messaging_settings",
]
