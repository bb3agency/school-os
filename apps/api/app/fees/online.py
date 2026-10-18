"""Online payments through the school's own Razorpay account (R2-2D; ADR-0042 decisions 2, 3,
6-10; US-2106 AC2, US-2107, US-2108, US-2111; FR-PAY-001..016). Internal to ``app.fees``: other
modules use ``app.fees.service``.

- **The school is the merchant (decision 2).** Links, payments, refunds and settlements live in
  the school's own account; SchoolOS never receives, holds or routes money, adds no charge and
  takes no commission (D8, D28). There is no payout, transfer or Route call anywhere
  (FR-PAY-016, pinned in ``providers/razorpay.ALLOWED_ENDPOINTS``).
- **Connection (FR-PAY-002..004, FR-PAY-012..014).** OAuth (Technology Partner) or, for pilots
  while ``SOS_RAZORPAY_MANUAL_KEYS_ALLOWED`` is on, the school's own key id and secret. Secrets
  (key secret, tokens, the webhook secret and, for 7 days after a rotation, the previous one,
  the route value) are wrapped by the school's key wrapper bound to the row
  (``payment_connection/<id>``, SEC-035), never returned (the webhook secret is shown once),
  never logged, exported or audited. Connecting creates the ``razorpay_route`` public handle
  that routes webhooks to the school (ADR-0047); disconnecting revokes it, cancels open links
  and erases every secret. ``payments.configure`` with step-up (owner).
- **Payment links (FR-PAY-005, FR-PAY-006, FR-PAY-015).** One intent per account and choice of
  instalments; the amount is what is due on them now (never typed); an open link for the same
  instalments and amount is reused; overlapping open links are closed; any counter receipt on
  those instalments closes them too (``links.close_overlapping``). Razorpay gets the amount,
  the intent id as ``reference_id``, a description with the school's short name and the intent
  code, ``notes = {intent}``, an expiry, no customer and no notifications (decision 8).
- **Webhooks (FR-PAY-007).** ``require_webhook_signature("razorpay")`` with
  :class:`app.fees.webhook.RazorpayWebhookVerifier`; the minimised event is stored encrypted
  in ``fin.provider_events`` (unique per event id) and the worker is woken.
- **Processing (FR-PAY-008, FR-PAY-009).** Every event is acted on only after **re-fetching**
  the payment (and its order) from the provider: currency INR, status captured, the order's
  ``receipt`` is one of this school's intents, amount equal to the intent's. Then one
  transaction issues the receipt (mode ``razorpay``, same gap-free series) with its ledger
  entries; a repeat is a no-op (unique provider payment id). Anything else is a reconciliation
  exception, never a guessed receipt. Order of arrival does not matter.
- **Refunds (FR-PAY-011).** Maker-checker in ``approvals``; after approval the worker asks the
  provider (idempotent ``receipt`` = the refund id); the voucher and ledger entries are written
  when the provider confirms (webhook or re-fetch); a failure notifies the requester.
- **Reconciliation (FR-PAY-010).** Daily at 06:00 IST for the previous IST day: payments,
  refunds and settlements compared with receipts and vouchers; exceptions are listed for a
  person to explain (audited); nothing is changed automatically.
- **Logs and audit (invariant 5, FR-FEE-025).** Ids, codes, counts and amounts only; never a key,
  token, signature, payer contact, email, VPA or card detail.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import secrets
import uuid
from collections import Counter
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.authz.context import Scopes, UserContext
from app.authz.http import decode_cursor, encode_cursor
from app.authz.kv import KVUnavailable, kv_store
from app.core.crypto import CryptoError, unwrap_bound, wrap_bound
from app.core.db import context_free_session, tenant_session
from app.core.errors import (
    Conflict,
    DomainError,
    Forbidden,
    NotFound,
    ServiceUnavailable,
    ValidationFailed,
)
from app.core.ids import new_id
from app.core.logging import get_logger
from app.fees import _common as c
from app.fees import ledger, links, receipts, refund_hooks
from app.fees import online_repository as orepo
from app.fees import repository as repo
from app.fees.approvals import write_voucher
from app.fees.money import IST, spread, total
from app.fees.online_models import (
    PaymentConnection,
    PaymentIntent,
    ProviderEvent,
    ReconciliationItem,
)
from app.fees.online_schemas import (
    ConnectionTestOut,
    ExplainIn,
    ManualConnectOut,
    ManualKeysIn,
    OAuthCallbackIn,
    OAuthStartOut,
    PaymentConnectionOut,
    PaymentIntentCreate,
    PaymentIntentOut,
    PaymentLinkInfo,
    ReconciliationItemOut,
    ReconciliationPage,
    ReconciliationRunOut,
    ReconciliationSummaryOut,
    WebhookSetupOut,
)
from app.fees.payments_config import PaymentsConfig, load_payments_config
from app.fees.payments_runtime import settings as payments_settings
from app.fees.providers import (
    Credentials,
    LinkRequest,
    Payment,
    PaymentProvider,
    ProviderError,
    Tokens,
    get_provider,
)
from app.fees.providers.razorpay import parse_webhook
from app.identity import service as identity
from app.notifications import service as notifications
from app.ops import service as ops
from app.public import service as public
from app.public.service import ResolvedHandle
from app.students import crypto as dek
from app.students import rotation as key_rotation
from app.students import service as students
from app.tenancy import service as tenancy

CONFIGURE: Final = "payments.configure"
PROVIDER: Final = "razorpay"
EVENTS_TABLE: Final = "fin.provider_events"
INBOX_EVENT: Final = "fees.payments.inbox"
INBOX_TASK: Final = "fees.process_payment_events"
REVOKED_TEMPLATE: Final = "payments.connection_revoked"
REFUND_FAILED_TEMPLATE: Final = "payments.refund_failed"
EXCEPTIONS_TEMPLATE: Final = "payments.reconciliation_exceptions"
EXPIRING_TEMPLATE: Final = "payments.authorization_expiring"
SYSTEM_ID: Final = uuid.UUID(int=0)
WEBHOOK_PATH: Final = "/api/v1/webhooks/razorpay/"
_STATE_PREFIX: Final = "sos:payments:oauth:"
_HEX32: Final = frozenset("0123456789abcdef")

ops.register_outbox_route(INBOX_EVENT, INBOX_TASK)

log = get_logger(__name__)


def cfg() -> PaymentsConfig:
    return load_payments_config()


# --- errors ---------------------------------------------------------------------------------------


class PaymentsNotConnected(Conflict):
    code = "payments_not_connected"

    def __init__(self) -> None:
        super().__init__(
            "Online payments are not connected for this school. Collect the fee at the counter."
        )


class AlreadyConnected(Conflict):
    code = "payments_already_connected"

    def __init__(self) -> None:
        super().__init__("Online payments are already connected. Disconnect them first.")


class ManualKeysNotAllowed(Forbidden):
    code = "manual_keys_not_allowed"

    def __init__(self) -> None:
        super().__init__(
            "Connecting with keys is not offered on this deployment. Use Connect Razorpay."
        )


class OAuthNotAvailable(Conflict):
    code = "oauth_not_available"

    def __init__(self) -> None:
        super().__init__(
            "Connecting through Razorpay sign-in is not available yet. Ask SchoolOS support "
            "about connecting with your own keys."
        )


class OAuthStateInvalid(Forbidden):
    code = "oauth_state_invalid"

    def __init__(self) -> None:
        super().__init__("This connection attempt expired or was not started by you. Start again.")


class ProviderFailed(DomainError):
    status, code, title = 502, "payments_provider_error", "Razorpay did not accept the request"


class NothingDue(Conflict):
    code = "nothing_due"

    def __init__(self) -> None:
        super().__init__("Nothing is due on the chosen instalments, so no payment link is needed.")


class IntentClosed(Conflict):
    code = "intent_closed"

    def __init__(self) -> None:
        super().__init__("This payment link is already paid, expired or cancelled.")


class TooManyLinks(Conflict):
    code = "too_many_links"

    def __init__(self) -> None:
        super().__init__("This student has too many open payment links. Cancel one first.")


def _provider_failed(exc: ProviderError, action: str) -> DomainError:
    log.warning(
        "payments.provider_failed",
        resource_type="payment_connection",
        action=action,
        error_code=exc.code,
        outcome=str(exc.status) if exc.status is not None else None,
    )
    if exc.code == "auth_failed":
        return ProviderFailed(
            "Razorpay refused the school's credentials. Check the connection in Settings > "
            "Online payments.",
            code="payments_auth_failed",
        )
    if action == "create_link":
        return ProviderFailed(
            "Razorpay did not create the payment link. Try again in a few minutes.",
            code="payment_link_failed",
        )
    return ProviderFailed("Razorpay did not accept the request. Try again in a few minutes.")


# --- secrets and credentials (SEC-035) ------------------------------------------------------------


def _resource(connection_id: uuid.UUID) -> str:
    return f"payment_connection/{connection_id}"


def _wrap(tenant_id: uuid.UUID, connection_id: uuid.UUID, secret: str) -> bytes:
    return wrap_bound(
        dek.get_keyring().wrapper,
        secret.encode(),
        tenant_id=tenant_id,
        resource=_resource(connection_id),
    )


def _unwrap(conn: PaymentConnection, blob: bytes | None) -> str:
    if blob is None:
        raise PaymentsNotConnected()
    try:
        raw = unwrap_bound(
            dek.get_keyring().wrapper,
            bytes(blob),
            tenant_id=conn.tenant_id,
            resource=_resource(conn.id),
        )
    except CryptoError:
        log.error("payments.secret_unreadable", resource_id=conn.id, error_code="crypto")
        raise PaymentsNotConnected() from None
    return raw.decode()


def credentials(conn: PaymentConnection) -> Credentials:
    if conn.mode == "manual_keys":
        return Credentials(key_id=conn.key_id, key_secret=_unwrap(conn, conn.key_secret_ciphertext))
    return Credentials(access_token=_unwrap(conn, conn.access_token_ciphertext))


def _provider() -> PaymentProvider:
    return get_provider()


def _new_webhook_secret() -> str:
    return secrets.token_urlsafe(cfg().webhook.secret_bytes)


def webhook_url(route: str) -> str:
    base = (payments_settings().webhook_base_url or "").rstrip("/")
    return f"{base}{WEBHOOK_PATH}{route}"


# --- permissions ----------------------------------------------------------------------------------


def _require_reader(session: Session, ctx: UserContext) -> None:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, CONFIGURE, c.READ)


def _require_configure(session: Session, ctx: UserContext) -> None:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, CONFIGURE)
    c.require_recent_mfa(ctx)


def _audit(
    session: Session,
    action: str,
    resource_id: uuid.UUID | None,
    summary: dict[str, Any],
    *,
    resource_type: str = "payment_connection",
    system: bool = False,
) -> None:
    c.record(
        session,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        summary=summary,
        system=system,
    )


# --- connection -----------------------------------------------------------------------------------


def _oauth_available() -> bool:
    s = payments_settings()
    return bool(
        s.razorpay_partner_client_id
        and s.razorpay_partner_client_secret is not None
        and s.razorpay_oauth_redirect_uri
    )


def _key_hint(key_id: str | None) -> str | None:
    if not key_id:
        return None
    prefix = "rzp_live_" if key_id.startswith("rzp_live_") else "rzp_test_"
    return f"{prefix}…{key_id[-4:]}"


def _connection_out(session: Session, conn: PaymentConnection | None) -> PaymentConnectionOut:
    s = payments_settings()
    base: dict[str, Any] = {
        "manual_keys_allowed": s.razorpay_manual_keys_allowed,
        "oauth_available": _oauth_available(),
    }
    if conn is None:
        return PaymentConnectionOut(connected=False, **base)
    now = orepo.now(session)
    url = None
    if conn.route_ciphertext is not None:
        try:
            url = webhook_url(_unwrap(conn, conn.route_ciphertext))
        except PaymentsNotConnected:
            url = None
    names = identity.member_display_names(session, c.unique_ids([conn.connected_by]))
    return PaymentConnectionOut(
        connected=conn.status == "active",
        id=conn.id,
        mode=conn.mode,
        environment=conn.environment,
        status=conn.status,
        provider_account_id=conn.provider_account_id,
        key_id_hint=_key_hint(conn.key_id),
        webhook_url=url,
        webhook_events=list(cfg().razorpay.webhook_events),
        connected_at=conn.connected_at,
        connected_by_name=names.get(conn.connected_by) if conn.connected_by else None,
        access_expires_at=conn.access_expires_at,
        refresh_expires_at=conn.refresh_expires_at,
        secret_rotated_at=conn.secret_rotated_at,
        previous_valid_until=(
            conn.previous_until if conn.previous_until and conn.previous_until > now else None
        ),
        last_checked_at=conn.last_checked_at,
        last_error_code=conn.last_error_code,
        revoked_at=conn.revoked_at,
        open_links=len([i for i in _open_intents_of(session, conn.id) if i.expires_at > now]),
        pending_events=orepo.count_events(session, "received"),
        failed_events=orepo.count_events(session, "failed"),
        version=conn.version,
        **base,
    )


def _open_intents_of(session: Session, connection_id: uuid.UUID) -> list[PaymentIntent]:
    return list(
        session.scalars(
            select(PaymentIntent).where(
                PaymentIntent.connection_id == connection_id, PaymentIntent.status == "created"
            )
        )
    )


def get_connection(session: Session, ctx: UserContext) -> PaymentConnectionOut:
    """The school's connection without its secrets (``payments.configure`` or
    ``finance.read``; FR-PAY-002/003)."""
    _require_reader(session, ctx)
    return _connection_out(session, orepo.live_connection(session))


def _clear_revoked(session: Session, ctx: UserContext) -> None:
    """A revoked connection is replaced by a new one: erase it first (no secrets kept)."""
    existing = orepo.live_connection(session, lock=True)
    if existing is None:
        return
    if existing.status == "active":
        raise AlreadyConnected()
    _erase(session, existing, by=ctx.membership_id)


def _erase(session: Session, conn: PaymentConnection, *, by: uuid.UUID | None) -> None:
    if conn.route_handle_id is not None:
        public.revoke(
            session, conn.route_handle_id, reason="payments_disconnected", by_user=by is not None
        )
    orepo.update_connection(
        session,
        conn.id,
        {
            "status": "disconnected",
            "key_secret_ciphertext": None,
            "access_token_ciphertext": None,
            "refresh_token_ciphertext": None,
            "webhook_secret_ciphertext": None,
            "previous_webhook_secret_ciphertext": None,
            "previous_until": None,
            "route_ciphertext": None,
            "disconnected_by": by,
            "disconnected_at": orepo.now(session),
        },
    )


def _route_handle(
    session: Session, ctx: UserContext, connection_id: uuid.UUID
) -> tuple[uuid.UUID, str]:
    issued = public.issue_handle(
        session,
        kind="razorpay_route",
        target_type="payment_connection",
        target_id=connection_id,
        created_by=ctx.user_id,
    )
    if issued.key is None:  # pragma: no cover - the service generates route keys
        raise RuntimeError("route key missing")
    return issued.handle_id, issued.key


def connect_manual(session: Session, ctx: UserContext, data: ManualKeysIn) -> ManualConnectOut:
    """Connect with the school's own key id and secret (pilots until partner approval, D13;
    FR-PAY-003; ``payments.configure``, step-up). The keys are checked with a read call, wrapped
    and never returned; the webhook URL and a new random secret are returned **once** for the
    school to add in its Razorpay dashboard. Errors: 403 ``manual_keys_not_allowed``; 409
    ``payments_already_connected``; 422 ``keys_rejected``; 502 ``payments_provider_error``.
    Audit ``payments.connected`` (mode, environment)."""
    _require_configure(session, ctx)
    if not payments_settings().razorpay_manual_keys_allowed:
        raise ManualKeysNotAllowed()
    _clear_revoked(session, ctx)
    secret = data.key_secret.get_secret_value()
    creds = Credentials(key_id=data.key_id, key_secret=secret)
    try:
        _provider().check_credentials(creds)
    except ProviderError as exc:
        if exc.code == "auth_failed":
            raise ValidationFailed(
                [c.error("key_secret", "keys_rejected")],
                detail="Razorpay did not accept this key id and secret. Copy them again from "
                "the dashboard (Account & Settings > API keys).",
            ) from None
        raise _provider_failed(exc, "connect") from None
    tenant_id = repo.current_tenant_id(session)
    connection_id = new_id()
    handle_id, route = _route_handle(session, ctx, connection_id)
    webhook_secret = _new_webhook_secret()
    now = orepo.now(session)
    with c.db_errors(), _db_errors():
        conn = orepo.insert_connection(
            session,
            id=connection_id,
            tenant_id=tenant_id,
            provider=PROVIDER,
            mode="manual_keys",
            environment="live" if data.key_id.startswith("rzp_live_") else "test",
            status="active",
            key_id=data.key_id,
            key_secret_ciphertext=_wrap(tenant_id, connection_id, secret),
            webhook_secret_ciphertext=_wrap(tenant_id, connection_id, webhook_secret),
            route_ciphertext=_wrap(tenant_id, connection_id, route),
            route_handle_id=handle_id,
            connected_by=ctx.membership_id,
            connected_at=now,
            last_checked_at=now,
        )
    _audit(
        session,
        "payments.connected",
        conn.id,
        {"mode": conn.mode, "environment": conn.environment},
    )
    log.info("payments.connected", resource_type="payment_connection", resource_id=conn.id)
    return ManualConnectOut(
        connection=_connection_out(session, conn),
        webhook=WebhookSetupOut(
            url=webhook_url(route),
            secret=webhook_secret,
            events=list(cfg().razorpay.webhook_events),
        ),
    )


def _state_key(state: str) -> str:
    return _STATE_PREFIX + hashlib.sha256(state.encode()).hexdigest()


def oauth_start(session: Session, ctx: UserContext) -> OAuthStartOut:
    """Start the Razorpay OAuth sign-in (FR-PAY-002; ``payments.configure``, step-up): a
    single-use ``state`` bound to this member and school for 10 minutes. 409
    ``oauth_not_available`` until SchoolOS is an approved Technology Partner (D13)."""
    _require_configure(session, ctx)
    if not _oauth_available():
        raise OAuthNotAvailable()
    existing = orepo.live_connection(session)
    if existing is not None and existing.status == "active":
        raise AlreadyConnected()
    state = secrets.token_urlsafe(32)
    binding = json.dumps({"t": str(ctx.tenant_id), "m": str(ctx.membership_id)}).encode()
    try:
        kv_store().set(_state_key(state), binding, ttl_s=cfg().oauth.state_ttl_s)
    except KVUnavailable:
        raise ServiceUnavailable() from None
    try:
        url = _provider().authorize_url(state)
    except ProviderError as exc:
        raise _provider_failed(exc, "oauth_start") from None
    _audit(session, "payments.oauth_started", None, {"provider": PROVIDER})
    return OAuthStartOut(authorize_url=url)


def _take_state(ctx: UserContext, state: str) -> None:
    key = _state_key(state)
    try:
        store = kv_store()
        value = store.get(key)
        store.delete(key)
    except KVUnavailable:
        raise ServiceUnavailable() from None
    if value is None:
        raise OAuthStateInvalid()
    try:
        bound = json.loads(value)
    except ValueError:
        raise OAuthStateInvalid() from None
    if bound.get("t") != str(ctx.tenant_id) or bound.get("m") != str(ctx.membership_id):
        raise OAuthStateInvalid()


def oauth_callback(
    session: Session, ctx: UserContext, data: OAuthCallbackIn
) -> PaymentConnectionOut:
    """Finish the OAuth sign-in (FR-PAY-002, FR-PAY-004): the single-use state must belong to
    this member and school (403 ``oauth_state_invalid``); the code is exchanged server-side;
    the tokens are wrapped bound to the new row; the account webhook is created with a fresh
    per-school secret and the route URL. The code is never logged. Audit ``payments.connected``."""
    _require_configure(session, ctx)
    if not _oauth_available():
        raise OAuthNotAvailable()
    _take_state(ctx, data.state)
    _clear_revoked(session, ctx)
    provider = _provider()
    try:
        tokens = provider.exchange_code(data.code)
    except ProviderError as exc:
        raise _provider_failed(exc, "oauth_exchange") from None
    tenant_id = repo.current_tenant_id(session)
    connection_id = new_id()
    handle_id, route = _route_handle(session, ctx, connection_id)
    webhook_secret = _new_webhook_secret()
    creds = Credentials(access_token=tokens.access_token)
    try:
        webhook_id = provider.create_webhook(
            creds,
            tokens.account_id,
            webhook_url(route),
            webhook_secret,
            cfg().razorpay.webhook_events,
        )
    except ProviderError as exc:
        raise _provider_failed(exc, "create_webhook") from None
    now = orepo.now(session)
    with c.db_errors(), _db_errors():
        conn = orepo.insert_connection(
            session,
            id=connection_id,
            tenant_id=tenant_id,
            provider=PROVIDER,
            mode="oauth",
            environment="live" if tokens.live else "test",
            status="active",
            provider_account_id=tokens.account_id,
            webhook_id=webhook_id,
            route_handle_id=handle_id,
            connected_by=ctx.membership_id,
            connected_at=now,
            last_checked_at=now,
            **_token_values(tenant_id, connection_id, tokens, now),
            webhook_secret_ciphertext=_wrap(tenant_id, connection_id, webhook_secret),
            route_ciphertext=_wrap(tenant_id, connection_id, route),
        )
    _audit(
        session,
        "payments.connected",
        conn.id,
        {"mode": conn.mode, "environment": conn.environment},
    )
    return _connection_out(session, conn)


def _token_values(
    tenant_id: uuid.UUID, connection_id: uuid.UUID, tokens: Tokens, now: dt.datetime
) -> dict[str, Any]:
    return {
        "access_token_ciphertext": _wrap(tenant_id, connection_id, tokens.access_token),
        "refresh_token_ciphertext": _wrap(tenant_id, connection_id, tokens.refresh_token),
        "access_expires_at": now + dt.timedelta(seconds=tokens.expires_in),
        "refresh_expires_at": now + dt.timedelta(days=cfg().razorpay.refresh_token_days),
    }


def _live(session: Session, *, lock: bool = False) -> PaymentConnection:
    conn = orepo.live_connection(session, lock=lock)
    if conn is None:
        raise PaymentsNotConnected()
    return conn


def _active(session: Session, *, lock: bool = False) -> PaymentConnection:
    conn = _live(session, lock=lock)
    if conn.status != "active":
        raise PaymentsNotConnected()
    return conn


def mark_revoked(session: Session, conn: PaymentConnection, reason: str) -> None:
    """The school withdrew SchoolOS's access or its keys stopped working (FR-PAY-012): no new
    links until it reconnects; owners are told. Audit ``payments.revoked`` (system)."""
    if conn.status != "active":
        return
    now = orepo.now(session)
    orepo.update_connection(
        session,
        conn.id,
        {"status": "revoked", "revoked_at": now, "last_error_code": reason, "last_checked_at": now},
    )
    _audit(session, "payments.revoked", conn.id, {"reason": reason}, system=True)
    notifications.notify(
        session,
        tenant_id=conn.tenant_id,
        recipients=notifications.PermissionSelector(CONFIGURE),
        template_key=REVOKED_TEMPLATE,
        params={"connection_id": str(conn.id), "reason": reason},
        resource_id=conn.id,
        dedupe_key=f"{REVOKED_TEMPLATE}:{conn.id}",
    )
    log.warning("payments.revoked", resource_type="payment_connection", resource_id=conn.id)


def test_connection(session: Session, ctx: UserContext) -> ConnectionTestOut:
    """Check the connection with a read call (``payments.configure``, step-up). A refusal (401)
    marks it revoked (FR-PAY-012). Audit ``payments.tested``."""
    _require_configure(session, ctx)
    conn = _live(session, lock=True)
    now = orepo.now(session)
    error: str | None = None
    if conn.status == "active":
        try:
            _provider().check_credentials(credentials(conn))
        except ProviderError as exc:
            error = exc.code
    else:
        error = conn.last_error_code or "revoked"
    if error == "auth_failed":
        mark_revoked(session, conn, "auth_failed")
    else:
        orepo.update_connection(
            session, conn.id, {"last_checked_at": now, "last_error_code": error}
        )
    _audit(session, "payments.tested", conn.id, {"ok": error is None, "error_code": error})
    return ConnectionTestOut(ok=error is None, error_code=error, checked_at=now)


def rotate_webhook_secret(session: Session, ctx: UserContext) -> WebhookSetupOut:
    """A new webhook secret (FR-PAY-014; ``payments.configure``, step-up): returned once; the
    previous one keeps verifying retried deliveries for 7 days, then it is erased. With OAuth
    the provider's webhook is updated at once; with manual keys the school pastes the new secret
    in its dashboard. Audit ``payments.webhook_secret_rotated``."""
    _require_configure(session, ctx)
    conn = _active(session, lock=True)
    secret = _new_webhook_secret()
    route = _unwrap(conn, conn.route_ciphertext)
    if conn.mode == "oauth" and conn.provider_account_id and conn.webhook_id:
        try:
            _provider().update_webhook(
                credentials(conn),
                conn.provider_account_id,
                conn.webhook_id,
                url=webhook_url(route),
                secret=secret,
                events=cfg().razorpay.webhook_events,
            )
        except ProviderError as exc:
            raise _provider_failed(exc, "update_webhook") from None
    now = orepo.now(session)
    until = now + dt.timedelta(days=cfg().webhook.previous_secret_days)
    conn = orepo.update_connection(
        session,
        conn.id,
        {
            "webhook_secret_ciphertext": _wrap(conn.tenant_id, conn.id, secret),
            "previous_webhook_secret_ciphertext": conn.webhook_secret_ciphertext,
            "previous_until": until,
            "secret_rotated_at": now,
        },
    )
    _audit(session, "payments.webhook_secret_rotated", conn.id, {"mode": conn.mode})
    return WebhookSetupOut(
        url=webhook_url(route),
        secret=secret,
        events=list(cfg().razorpay.webhook_events),
        previous_valid_until=until,
    )


def disconnect(session: Session, ctx: UserContext) -> PaymentConnectionOut:
    """Disconnect (FR-PAY-013; ``payments.configure``, step-up): open links are cancelled at the
    provider (failures are logged; the links are closed here anyway), the OAuth webhook deleted
    and the token revoked, the route handle revoked, every secret erased; receipts, intents and
    history stay. Audit ``payments.disconnected`` (counts)."""
    _require_configure(session, ctx)
    conn = _live(session, lock=True)
    cancelled = failed = 0
    provider = _provider()
    creds: Credentials | None = None
    if conn.status == "active":
        try:
            creds = credentials(conn)
        except PaymentsNotConnected:
            creds = None
    now = orepo.now(session)
    for intent in orepo.open_intents_of_connection(session, conn.id, 10_000):
        if creds is not None:
            try:
                provider.cancel_link(creds, intent.provider_link_id)
            except ProviderError:
                failed += 1
        orepo.update_intent(
            session,
            intent.id,
            {
                "status": "cancelled",
                "cancelled_at": now,
                "cancelled_by": ctx.membership_id,
                "closed_at": now,
            },
        )
        cancelled += 1
    if creds is not None and conn.mode == "oauth":
        try:
            if conn.provider_account_id and conn.webhook_id:
                provider.delete_webhook(creds, conn.provider_account_id, conn.webhook_id)
            if creds.access_token:
                provider.revoke(creds.access_token)
        except ProviderError as exc:
            log.warning(
                "payments.disconnect_provider_failed",
                resource_type="payment_connection",
                resource_id=conn.id,
                error_code=exc.code,
            )
    _erase(session, conn, by=ctx.membership_id)
    _audit(
        session,
        "payments.disconnected",
        conn.id,
        {"mode": conn.mode, "links_cancelled": cancelled, "provider_failures": failed},
    )
    return _connection_out(session, None)


@contextmanager
def _db_errors() -> Iterator[None]:
    try:
        yield
    except DBAPIError as exc:
        mapped = orepo.translate_db_error(exc)
        if mapped is None:
            raise
        raise mapped from exc


# --- webhook intake (API process; FR-PAY-007) -----------------------------------------------------


def webhook_secrets(handle: ResolvedHandle) -> list[bytes] | None:
    """The secrets that may have signed a delivery to ``handle`` (the current one and, within
    7 days of a rotation, the previous one), read in the school's own ``tenant_session``.
    ``None`` when the handle no longer belongs to a live connection."""
    with tenant_session(handle.tenant_id) as session:
        conn = orepo.live_connection(session)
        if (
            conn is None
            or conn.route_handle_id != handle.handle_id
            or public.load_handle(session, handle.handle_id, ("razorpay_route",)) is None
        ):
            return None
        out = [_unwrap(conn, conn.webhook_secret_ciphertext).encode()]
        if (
            conn.previous_webhook_secret_ciphertext is not None
            and conn.previous_until is not None
            and conn.previous_until > orepo.now(session)
        ):
            out.append(_unwrap(conn, conn.previous_webhook_secret_ciphertext).encode())
        return out


def store_event(handle: ResolvedHandle, event_id: str, body: bytes) -> bool:
    """Store one verified delivery in the school's inbox (encrypted, minimised, unique per
    event id) and wake the worker. Returns ``True`` when it was new. A body that is not a
    Razorpay event is dropped (it was signed with the school's secret, so it is logged)."""
    try:
        parsed = parse_webhook(json.loads(body))
    except (ValueError, UnicodeDecodeError, ProviderError):
        log.warning("payments.webhook.malformed", resource_type="webhook", outcome="dropped")
        return False
    with tenant_session(handle.tenant_id) as session:
        conn = orepo.live_connection(session)
        if conn is None or conn.route_handle_id != handle.handle_id:
            log.warning("payments.webhook.no_connection", outcome="dropped")
            return False
        row_id = new_id()
        blob, _ = dek.encrypt_value(
            session,
            json.dumps(parsed, sort_keys=True, separators=(",", ":")),
            table=EVENTS_TABLE,
            column="payload_ciphertext",
            row_id=row_id,
        )
        created = parsed.get("created_at")
        inserted = orepo.insert_event(
            session,
            id=row_id,
            tenant_id=conn.tenant_id,
            provider=PROVIDER,
            connection_id=conn.id,
            event_id=event_id,
            event_type=str(parsed["event"]),
            event_at=(
                dt.datetime.fromtimestamp(created, tz=dt.UTC) if isinstance(created, int) else None
            ),
            payload_ciphertext=blob,
        )
        if inserted is None:
            return False
        ops.enqueue_event(session, INBOX_EVENT, {"count": 1})
        return True


# --- payment intents (FR-PAY-005, FR-PAY-006, FR-PAY-015) -----------------------------------------


def _effective_status(intent: PaymentIntent, now: dt.datetime) -> str:
    if intent.status == "created" and intent.expires_at <= now:
        return "expired"
    return intent.status


def _intent_out(
    session: Session, intent: PaymentIntent, *, reused: bool = False
) -> PaymentIntentOut:
    now = orepo.now(session)
    receipt = orepo.receipt_for_intent(session, intent.id)
    names = identity.member_display_names(session, c.unique_ids([intent.created_by]))
    return PaymentIntentOut(
        id=intent.id,
        account_id=intent.account_id,
        intent_code=intent.intent_code,
        amount=intent.amount,
        instalments=list(intent.instalments),
        status=_effective_status(intent, now),
        short_url=intent.short_url,
        expires_at=intent.expires_at,
        created_via=intent.created_via,
        created_by=intent.created_by,
        created_by_name=names.get(intent.created_by) if intent.created_by else None,
        created_at=intent.created_at,
        paid_at=intent.paid_at,
        receipt_id=receipt.id if receipt else None,
        receipt_number=receipt.receipt_number if receipt else None,
        cancelled_at=intent.cancelled_at,
        reused=reused,
        version=intent.version,
    )


def _due_on(dues: ledger.Dues, instalments: Collection[int]) -> Decimal:
    wanted = set(instalments)
    return total(
        o.due for b, o in dues.lines if b.instalment_no in wanted and o.collectable and o.due > 0
    )


def _description(session: Session, code: str) -> str:
    name = tenancy.get_tenant(session).name.strip()
    limit = cfg().links.school_name_max_length
    short = name if len(name) <= limit else name[: limit - 1].rstrip() + "…"
    return cfg().links.description.format(school=short, code=code)


def _callback_url() -> str | None:
    base = payments_settings().public_base_url
    return f"{base.rstrip('/')}/p/paid" if base else None


def create_intent(
    session: Session,
    *,
    account_id: uuid.UUID,
    instalments: Collection[int],
    created_via: str,
    created_by: uuid.UUID | None,
    public_handle_id: uuid.UUID | None = None,
    expires_in_days: int | None = None,
) -> tuple[PaymentIntent, bool]:
    """Create (or reuse) a payment link for the account's chosen instalments. The caller checked
    who may do this. Returns the intent and whether it was reused."""
    wanted = sorted(set(instalments))
    if not wanted:
        raise ValidationFailed([c.error("instalments", "missing")])
    orepo.lock_account_intents(session, account_id)
    account = ledger.load_account(session, account_id, lock=True)  # 404 before anything else
    conn = _active(session)
    if account.status != "open":
        raise Conflict("This fee account is closed.", code="account_closed")
    today = c.today(session)
    dues = ledger.dues_for(session, account, today)
    known = {b.instalment_no for b, _ in dues.lines}
    for i, no in enumerate(wanted):
        if no not in known:
            raise ValidationFailed([c.error(f"instalments.{i}", "invalid")])
    amount = _due_on(dues, wanted)
    if amount < Decimal("1.00"):
        raise NothingDue()
    now = orepo.now(session)
    links_cfg = cfg().links
    margin = dt.timedelta(minutes=links_cfg.min_expiry_minutes)
    open_now = orepo.open_intents(session, account.id, lock=True)
    for intent in open_now:
        if (
            sorted(intent.instalments) == wanted
            and intent.amount == amount
            and intent.expires_at > now + margin
            and intent.connection_id == conn.id
        ):
            return intent, True
    live = [i for i in open_now if i.expires_at > now]
    if len(live) >= links_cfg.max_open_per_account:
        raise TooManyLinks()
    days = expires_in_days or links_cfg.default_expiry_days
    days = max(1, min(days, links_cfg.max_expiry_days))
    expires_at = now + dt.timedelta(days=days)
    intent_id = new_id()
    code = intent_id.hex[-8:].upper()
    request = LinkRequest(
        amount=amount,
        reference_id=intent_id.hex,
        description=_description(session, code),
        expire_by=expires_at,
        notes={"intent": intent_id.hex},
        callback_url=_callback_url(),
    )
    try:
        link = _provider().create_link(credentials(conn), request)
    except ProviderError as exc:
        if exc.code == "auth_failed":
            mark_revoked(session, conn, "auth_failed")
        raise _provider_failed(exc, "create_link") from None
    if link.amount != amount or link.reference_id != intent_id.hex:
        raise ProviderFailed("Razorpay returned a different link. Try again.")
    # Overlapping open links ask for the same dues: close them (FR-PAY-006).
    links.close_overlapping(session, account.id, wanted, cancelled_by=created_by)
    with c.db_errors(), _db_errors():
        intent = orepo.insert_intent(
            session,
            id=intent_id,
            tenant_id=account.tenant_id,
            account_id=account.id,
            connection_id=conn.id,
            intent_code=code,
            amount=amount,
            instalments=wanted,
            status="created",
            provider_link_id=link.id,
            short_url=link.short_url,
            expires_at=expires_at,
            created_via=created_via,
            created_by=created_by,
            public_handle_id=public_handle_id,
            created_at=now,
        )
    _audit(
        session,
        "fee.payment_link.created",
        intent.id,
        {
            "account_id": account.id,
            "amount": c.money_text(amount),
            "instalments": wanted,
            "via": created_via,
        },
        resource_type="fee_payment_intent",
        system=created_by is None,
    )
    return intent, False


def create_intent_for_staff(
    session: Session, ctx: UserContext, account_id: uuid.UUID, data: PaymentIntentCreate
) -> PaymentIntentOut:
    """Create or reuse a payment link (``fee.collect``; FR-PAY-005/006). Errors: 409
    ``payments_not_connected``, ``nothing_due``, ``account_closed``, ``too_many_links``; 422
    unknown instalments; 502 ``payment_link_failed``. Audit ``fee.payment_link.created``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT)
    intent, reused = create_intent(
        session,
        account_id=account_id,
        instalments=data.instalments,
        created_via="staff",
        created_by=ctx.membership_id,
        expires_in_days=data.expires_in_days,
    )
    return _intent_out(session, intent, reused=reused)


def list_intents(
    session: Session, ctx: UserContext, account_id: uuid.UUID
) -> list[PaymentIntentOut]:
    """An account's payment links, newest first (``fee.collect`` or ``finance.read``)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    ledger.load_account(session, account_id)
    return [
        _intent_out(session, i)
        for i in orepo.intents_of_account(session, account_id, c.settings().limits.list_max)
    ]


def get_intent(session: Session, ctx: UserContext, intent_id: uuid.UUID) -> PaymentIntentOut:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT, c.READ)
    intent = orepo.get_intent(session, intent_id)
    if intent is None:
        raise NotFound("Payment link not found")
    return _intent_out(session, intent)


def cancel_intent(session: Session, ctx: UserContext, intent_id: uuid.UUID) -> PaymentIntentOut:
    """Cancel an unpaid link (``fee.collect``; FR-PAY-015) at the provider first. A link the
    parent already paid stays open for its receipt (409 ``intent_paid``). Audit
    ``fee.payment_link.cancelled``."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.COLLECT)
    intent = orepo.get_intent(session, intent_id, lock=True)
    if intent is None:
        raise NotFound("Payment link not found")
    if intent.status != "created":
        raise IntentClosed()
    conn = orepo.get_connection(session, intent.connection_id)
    if conn is not None and conn.status == "active":
        creds = credentials(conn)
        provider = _provider()
        try:
            provider.cancel_link(creds, intent.provider_link_id)
        except ProviderError as exc:
            if exc.code in ("invalid_request", "not_found"):
                try:
                    link = provider.fetch_link(creds, intent.provider_link_id)
                except ProviderError as again:
                    raise _provider_failed(again, "cancel_link") from None
                if link.status in ("paid", "partially_paid"):
                    raise Conflict(
                        "The parent has already paid this link. The receipt appears shortly.",
                        code="intent_paid",
                    ) from None
            else:
                raise _provider_failed(exc, "cancel_link") from None
    now = orepo.now(session)
    intent = orepo.update_intent(
        session,
        intent.id,
        {
            "status": "cancelled",
            "cancelled_at": now,
            "cancelled_by": ctx.membership_id,
            "closed_at": now,
        },
    )
    _audit(
        session,
        "fee.payment_link.cancelled",
        intent.id,
        {"account_id": intent.account_id},
        resource_type="fee_payment_intent",
    )
    return _intent_out(session, intent)


def link_info(session: Session, intent_id: uuid.UUID) -> PaymentLinkInfo | None:
    intent = orepo.get_intent(session, intent_id)
    if intent is None:
        return None
    return PaymentLinkInfo(
        intent_id=intent.id,
        short_url=intent.short_url,
        expires_at=intent.expires_at,
        status=_effective_status(intent, orepo.now(session)),
        amount=intent.amount,
    )


def cancel_links_at_provider(tenant_id: uuid.UUID, intent_ids: Sequence[uuid.UUID]) -> int:
    """Worker: cancel at the provider the links closed here (``links.CANCEL_EVENT``). A link
    that was paid meanwhile is left alone: its payment becomes an exception, never a receipt."""
    done = 0
    with tenant_session(tenant_id) as session:
        provider = _provider()
        for intent_id in intent_ids:
            intent = orepo.get_intent(session, intent_id)
            if intent is None or intent.status != "cancelled":
                continue
            conn = orepo.get_connection(session, intent.connection_id)
            if conn is None or conn.status != "active":
                continue
            try:
                provider.cancel_link(credentials(conn), intent.provider_link_id)
                done += 1
            except ProviderError as exc:
                if exc.transient:
                    raise
                log.info(
                    "payments.link_cancel_skipped",
                    resource_type="fee_payment_intent",
                    resource_id=intent.id,
                    error_code=exc.code,
                )
    return done


# --- inbox processing (worker; FR-PAY-008, FR-PAY-009) --------------------------------------------


class _Retry(Exception):
    """Processing must be tried again later (provider unreachable, day closed)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _system_ctx(tenant_id: uuid.UUID) -> UserContext:
    """Whole-school student read for the receipt's frozen content (worker only)."""
    return UserContext(
        user_id=SYSTEM_ID,
        tenant_id=tenant_id,
        membership_id=SYSTEM_ID,
        roles=frozenset({"system"}),
        permissions=frozenset({"student.read_basic"}),
        scopes=Scopes(school=True),
        mfa=False,
        auth_time=None,
    )


def open_exception(
    session: Session,
    *,
    kind: str,
    reason: str,
    dedupe: str,
    provider_payment_id: str | None = None,
    provider_refund_id: str | None = None,
    intent_id: uuid.UUID | None = None,
    receipt_id: uuid.UUID | None = None,
    refund_id: uuid.UUID | None = None,
    amount: Decimal | None = None,
    expected_amount: Decimal | None = None,
    run_id: uuid.UUID | None = None,
) -> bool:
    """Record a reconciliation exception once (FR-PAY-009/010). Audit
    ``fee.reconciliation.exception_opened`` (kind, reason; system)."""
    item_id = new_id()
    inserted = orepo.insert_item(
        session,
        id=item_id,
        tenant_id=repo.current_tenant_id(session),
        run_id=run_id,
        kind=kind,
        reason_code=reason,
        dedupe_key=f"{kind}:{dedupe}"[:120],
        provider_payment_id=provider_payment_id,
        provider_refund_id=provider_refund_id,
        intent_id=intent_id,
        receipt_id=receipt_id,
        refund_id=refund_id,
        amount=amount,
        expected_amount=expected_amount,
    )
    if inserted is None:
        return False
    _audit(
        session,
        "fee.reconciliation.exception_opened",
        inserted,
        {"kind": kind, "reason": reason, "intent_id": intent_id, "receipt_id": receipt_id},
        resource_type="fee_reconciliation_item",
        system=True,
    )
    log.warning(
        "payments.exception_opened",
        resource_type="fee_reconciliation_item",
        resource_id=inserted,
        error_code=reason,
        action=kind,
    )
    return True


def _intent_for_reference(session: Session, reference: str | None) -> PaymentIntent | None:
    """The intent an order's ``receipt`` names (RLS: only this school's). Not locked: the
    account is locked first, then the intent, in the counter's order (no deadlock)."""
    if not reference or len(reference) != 32 or not set(reference) <= _HEX32:
        return None
    return orepo.get_intent(session, uuid.UUID(hex=reference))


def _is_ours(reference: str | None) -> bool:
    return bool(reference) and len(reference or "") == 32 and set(reference or "") <= _HEX32


def _fetch(call: Any, *args: Any) -> Any:
    try:
        return call(*args)
    except ProviderError as exc:
        if exc.code == "not_found":
            return None
        raise _Retry(exc.code) from None


def _record_payment(
    session: Session,
    conn: PaymentConnection,
    payment: Payment,
    *,
    order_id: str | None,
    intent: PaymentIntent | None,
    paid_at: dt.datetime | None,
) -> None:
    orepo.upsert_payment(
        session,
        {
            "id": new_id(),
            "tenant_id": conn.tenant_id,
            "provider": PROVIDER,
            "connection_id": conn.id,
            "provider_payment_id": payment.id,
            "provider_order_id": order_id,
            "intent_id": intent.id if intent else None,
            "amount": payment.amount,
            "currency": payment.currency[:3].upper() if payment.currency else "XXX",
            "method": payment.method,
            "status": payment.status,
            "amount_refunded": payment.amount_refunded,
            "provider_fee": payment.fee,
            "provider_tax": payment.tax,
            "paid_at": paid_at,
            "last_fetched_at": orepo.now(session),
        },
    )


def _receipt_date(session: Session, captured: dt.datetime) -> dt.date:
    """FR-FEE-022: the capture's IST date, unless that day is closed (then today); today must be
    open, else the event waits for tomorrow (``day_closed``)."""
    today = c.today(session)
    day = min(captured.astimezone(IST).date(), today)
    if day != today:
        repo.lock_day(session, day, exclusive=False)
        if repo.get_day_close(session, day) is None:
            return day
    try:
        c.ensure_day_open(session, today)
    except c.DayClosed:
        raise _Retry("day_closed") from None
    return today


def _online_parts(
    dues: ledger.Dues, instalments: Collection[int], amount: Decimal
) -> list[tuple[int, uuid.UUID | None, Decimal]] | None:
    wanted = set(instalments)
    lines = [
        (b, o) for b, o in dues.lines if b.instalment_no in wanted and o.collectable and o.due > 0
    ]
    room = total(o.due for _, o in lines)
    if room < amount:
        return None
    parts = spread(amount, [o.due for _, o in lines])
    return [
        (b.instalment_no, b.head_id, p) for (b, _), p in zip(lines, parts, strict=True) if p > 0
    ]


def post_payment(  # noqa: PLR0911, PLR0912 - one outcome per check (FR-PAY-008/009)
    session: Session,
    conn: PaymentConnection,
    payment_id: str,
    *,
    captured_at: dt.datetime | None = None,
    run_id: uuid.UUID | None = None,
) -> str:
    """Re-fetch one payment and its order from the provider and post its receipt, or open an
    exception (FR-PAY-008, FR-PAY-009). Never trusts the webhook's own figures. Returns the
    outcome code."""
    provider = _provider()
    creds = credentials(conn)
    payment: Payment | None = _fetch(provider.fetch_payment, creds, payment_id)
    if payment is None:
        return "payment_unknown"
    order = _fetch(provider.fetch_order, creds, payment.order_id) if payment.order_id else None
    reference = order.receipt if order is not None else None
    intent = _intent_for_reference(session, reference)
    paid_at = captured_at or payment.created_at
    account = None
    if intent is not None:
        # Lock order as at the counter: the account, then its links, then anything that
        # references a link (FR-FEE-010, FR-PAY-006): no deadlock with a counter receipt.
        account = ledger.load_account(session, intent.account_id, lock=True)
        intent = orepo.get_intent(session, intent.id, lock=True) or intent
    _record_payment(
        session, conn, payment, order_id=payment.order_id, intent=intent, paid_at=paid_at
    )
    existing = orepo.receipt_for_payment(session, payment.id)
    if existing is not None:
        return "duplicate"
    captured = payment.status in ("captured", "refunded") and payment.captured
    if intent is None:
        if captured and _is_ours(reference):
            open_exception(
                session,
                kind="unknown_intent",
                reason="intent_not_found",
                dedupe=payment.id,
                provider_payment_id=payment.id,
                amount=payment.amount,
                run_id=run_id,
            )
            return "exception"
        return "not_schoolos"
    if not captured:
        return f"not_captured_{payment.status}"[:60]
    common: dict[str, Any] = {
        "provider_payment_id": payment.id,
        "intent_id": intent.id,
        "amount": payment.amount,
        "expected_amount": intent.amount,
        "run_id": run_id,
    }
    if payment.currency != "INR":
        open_exception(
            session, kind="amount_mismatch", reason="currency", dedupe=payment.id, **common
        )
        return "exception"
    if payment.amount != intent.amount:
        open_exception(
            session, kind="amount_mismatch", reason="amount", dedupe=payment.id, **common
        )
        return "exception"
    if account is None:  # pragma: no cover - set above whenever there is an intent
        raise _Retry("account_missing")
    if intent.status == "paid" and intent.provider_payment_id != payment.id:
        open_exception(
            session,
            kind="paid_not_receipted",
            reason="intent_already_paid",
            dedupe=payment.id,
            **common,
        )
        return "exception"
    if payment.status == "refunded":
        open_exception(
            session, kind="refunded", reason="refunded_before_receipt", dedupe=payment.id, **common
        )
        return "exception"
    if account.status != "open":
        open_exception(
            session, kind="paid_not_receipted", reason="account_closed", dedupe=payment.id, **common
        )
        return "exception"
    if intent.status in ("cancelled", "expired", "failed"):
        # The link was closed here (a counter receipt, a cancel) but the parent paid anyway: the
        # dues decide whether a receipt is still possible below.
        pass
    receipt_date = _receipt_date(session, paid_at)
    dues = ledger.dues_for(session, account, c.today(session))
    parts = _online_parts(dues, intent.instalments, payment.amount)
    if parts is None:
        open_exception(
            session,
            kind="paid_not_receipted",
            reason="dues_already_paid",
            dedupe=payment.id,
            **common,
        )
        return "exception"
    student = students.summaries(session, _system_ctx(account.tenant_id), [account.student_id]).get(
        account.student_id
    )
    if student is None:
        open_exception(
            session,
            kind="paid_not_receipted",
            reason="student_not_found",
            dedupe=payment.id,
            **common,
        )
        return "exception"
    row = receipts.write_receipt(
        session,
        account=account,
        amount=payment.amount,
        mode="razorpay",
        details={"provider": PROVIDER, "payment_id": payment.id, "method": payment.method},
        utr=None,
        parts=parts,
        dues=dues,
        receipt_date=receipt_date,
        student=student,
        collected_by=None,
        collector_name="Online payment (Razorpay)",
        actor_user_id=SYSTEM_ID,
        provider_payment_id=payment.id,
        payment_intent_id=intent.id,
        system=True,
    )
    now = orepo.now(session)
    orepo.update_intent(
        session,
        intent.id,
        {
            "status": "paid",
            "provider_payment_id": payment.id,
            "paid_at": paid_at,
            "closed_at": intent.closed_at or now,
        },
    )
    orepo.link_payment_receipt(session, payment.id, row.id)
    for item in orepo.open_items_for_payment(session, payment.id):
        orepo.update_item(session, item.id, {"status": "resolved", "resolved_at": now})
    links.close_overlapping(session, account.id, {p[0] for p in parts}, keep=intent.id)
    return "receipt_issued"


def _apply_link_event(session: Session, conn: PaymentConnection, link_id: str | None) -> str:
    if not link_id:
        return "ignored"
    intent = session.scalars(
        select(PaymentIntent).where(PaymentIntent.provider_link_id == link_id).with_for_update()
    ).one_or_none()
    if intent is None:
        return "not_schoolos"
    link = _fetch(_provider().fetch_link, credentials(conn), link_id)
    if link is None or intent.status != "created":
        return "no_change"
    if link.status in ("expired", "cancelled"):
        now = orepo.now(session)
        values: dict[str, Any] = {"status": link.status, "closed_at": now}
        if link.status == "cancelled":
            values["cancelled_at"] = now
        orepo.update_intent(session, intent.id, values)
        return f"link_{link.status}"
    return "no_change"


def _refund_row_for(session: Session, refund_id: str, receipt_ref: str | None) -> Any:
    row = orepo.refund_by_provider_id(session, refund_id)
    if row is None and receipt_ref and _is_ours(receipt_ref):
        row = repo.get_refund(session, uuid.UUID(hex=receipt_ref), lock=True)
    return row


def apply_refund(  # noqa: PLR0911 - one outcome per refund state
    session: Session,
    conn: PaymentConnection,
    payment_id: str,
    refund_id: str,
    receipt_ref: str | None = None,
) -> str:
    """Re-fetch a refund and post its voucher when processed, or mark it failed (FR-PAY-011)."""
    refund = _fetch(_provider().fetch_refund, credentials(conn), payment_id, refund_id)
    if refund is None:
        return "refund_unknown"
    row = _refund_row_for(session, refund.id, refund.receipt or receipt_ref)
    if row is None:
        receipt = orepo.receipt_for_payment(session, payment_id)
        if receipt is None:
            return "not_schoolos"
        if refund.status == "processed":
            open_exception(
                session,
                kind="refunded",
                reason="refund_outside_schoolos",
                dedupe=refund.id,
                provider_payment_id=payment_id,
                provider_refund_id=refund.id,
                receipt_id=receipt.id,
                amount=refund.amount,
            )
            return "exception"
        return "no_change"
    if row.status in ("processed", "rejected", "withdrawn", "pending"):
        return "no_change"
    if refund.amount != row.amount:
        open_exception(
            session,
            kind="refunded",
            reason="refund_amount_differs",
            dedupe=refund.id,
            provider_payment_id=payment_id,
            provider_refund_id=refund.id,
            receipt_id=row.receipt_id,
            refund_id=row.id,
            amount=refund.amount,
            expected_amount=row.amount,
        )
        return "exception"
    if refund.status == "processed":
        receipt = receipts.load_receipt(session, row.receipt_id, lock=True)
        today = c.today(session)
        try:
            c.ensure_day_open(session, today)
        except c.DayClosed:
            raise _Retry("day_closed") from None
        values_row = row
        if row.provider_refund_id is None:
            values_row = repo.update_refund(session, row.id, {"provider_refund_id": refund.id})
        out = write_voucher(session, values_row, receipt, today, decision=None, posted_by=None)
        _audit(
            session,
            "fee.refund.processed",
            out.id,
            {
                "voucher_number": out.voucher_number,
                "receipt_number": receipt.receipt_number,
                "amount": c.money_text(out.amount),
                "mode": out.mode,
            },
            resource_type="fee_refund",
            system=True,
        )
        return "voucher_written"
    if refund.status == "failed":
        _refund_failed(session, row, refund.id, payment_id)
        return "refund_failed"
    if row.status == "approved":
        repo.update_refund(
            session, row.id, {"status": "processing", "provider_refund_id": refund.id}
        )
    return "no_change"


def _refund_failed(
    session: Session, row: Any, provider_refund_id: str | None, payment_id: str
) -> None:
    values: dict[str, Any] = {"status": "failed"}
    if provider_refund_id and row.provider_refund_id is None:
        values["provider_refund_id"] = provider_refund_id
    repo.update_refund(session, row.id, values)
    open_exception(
        session,
        kind="refund_failed",
        reason="provider_failed",
        dedupe=str(row.id),
        provider_payment_id=payment_id,
        provider_refund_id=provider_refund_id,
        receipt_id=row.receipt_id,
        refund_id=row.id,
        amount=row.amount,
    )
    notifications.notify(
        session,
        tenant_id=row.tenant_id,
        recipients=[row.requested_by],
        template_key=REFUND_FAILED_TEMPLATE,
        params={"refund_id": str(row.id), "account_id": str(row.account_id)},
        resource_id=row.id,
        dedupe_key=f"{REFUND_FAILED_TEMPLATE}:{row.id}",
    )
    _audit(
        session,
        "fee.refund.failed",
        row.id,
        {"mode": row.mode},
        resource_type="fee_refund",
        system=True,
    )


def _confirm_revocation(session: Session, conn: PaymentConnection) -> str:
    if conn.status != "active":
        return "no_change"
    try:
        _provider().check_credentials(credentials(conn))
    except ProviderError as exc:
        if exc.code == "auth_failed":
            mark_revoked(session, conn, "authorization_revoked")
            return "revoked"
        raise _Retry(exc.code) from None
    return "not_confirmed"


def _apply_event(  # noqa: PLR0911 - one branch per event type
    session: Session, conn: PaymentConnection, event: ProviderEvent, payload: dict[str, Any]
) -> str:
    kind = event.event_type
    payment = payload.get("payment") or {}
    link = payload.get("link") or {}
    refund = payload.get("refund") or {}
    if kind == "account.app.authorization_revoked":
        return _confirm_revocation(session, conn)
    if conn.status != "active":
        raise _Retry("connection_revoked")
    if kind in ("payment_link.paid", "payment.captured", "payment.failed", "payment.authorized"):
        payment_id = payment.get("id")
        if not isinstance(payment_id, str):
            return "ignored"
        captured_at = event.event_at if kind in ("payment_link.paid", "payment.captured") else None
        return post_payment(session, conn, payment_id, captured_at=captured_at)
    if kind in ("payment_link.expired", "payment_link.cancelled"):
        return _apply_link_event(session, conn, link.get("id"))
    if kind in ("refund.processed", "refund.failed", "refund.created"):
        refund_id, payment_id = refund.get("id"), refund.get("payment_id")
        if not isinstance(refund_id, str) or not isinstance(payment_id, str):
            return "ignored"
        return apply_refund(session, conn, payment_id, refund_id, refund.get("receipt"))
    if kind == "settlement.processed":
        return "noted"
    return "ignored"


def process_events(tenant_id: uuid.UUID) -> dict[str, int]:
    """Worker: apply the school's stored events, one transaction each, oldest first (FR-PAY-008,
    FR-PAY-009). A provider that cannot be reached or a closed day retries the event later with
    backoff; after the last attempt it is ``failed`` (shown on the reconciliation screen). One
    event's failure never blocks the others."""
    inbox = cfg().inbox
    totals: Counter[str] = Counter()
    for _ in range(inbox.batch_size):
        with tenant_session(tenant_id) as session:
            now = orepo.now(session)
            events = orepo.next_events(session, now, 1)
            if not events:
                break
            event = events[0]
            attempts = event.attempts + 1
            savepoint = session.begin_nested()
            try:
                payload = json.loads(
                    dek.decrypt_value(
                        session,
                        bytes(event.payload_ciphertext),
                        table=EVENTS_TABLE,
                        column="payload_ciphertext",
                        row_id=event.id,
                    )
                )
                conn = orepo.get_connection(session, event.connection_id)
                if conn is None or conn.status == "disconnected":
                    outcome = "disconnected"
                else:
                    outcome = _apply_event(session, conn, event, payload)
                savepoint.commit()
            except _Retry as retry:
                savepoint.rollback()
                final = attempts >= inbox.max_attempts
                orepo.finish_event(
                    session,
                    event.id,
                    {
                        "status": "failed" if final else "received",
                        "attempts": attempts,
                        "error_code": retry.code[:60],
                        "next_attempt_at": now + dt.timedelta(seconds=inbox.backoff(attempts)),
                        "processed_at": now if final else None,
                    },
                )
                totals["retry" if not final else "failed"] += 1
                log.warning(
                    "payments.inbox.retry",
                    tenant_id=tenant_id,
                    resource_id=event.id,
                    error_code=retry.code,
                    outcome="failed" if final else "retry",
                )
                continue
            except (DBAPIError, CryptoError, ValueError, KeyError, DomainError) as exc:
                savepoint.rollback()
                final = attempts >= inbox.max_attempts
                orepo.finish_event(
                    session,
                    event.id,
                    {
                        "status": "failed" if final else "received",
                        "attempts": attempts,
                        "error_code": "apply_failed",
                        "next_attempt_at": now + dt.timedelta(seconds=inbox.backoff(attempts)),
                        "processed_at": now if final else None,
                    },
                )
                totals["failed" if final else "retry"] += 1
                log.warning(
                    "payments.inbox.failed",
                    tenant_id=tenant_id,
                    resource_id=event.id,
                    error_type=type(exc).__name__,
                )
                continue
            status = (
                "ignored" if outcome in ("ignored", "not_schoolos", "disconnected") else "processed"
            )
            orepo.finish_event(
                session,
                event.id,
                {
                    "status": status,
                    "outcome": outcome[:60],
                    "attempts": attempts,
                    "error_code": None,
                    "processed_at": now,
                },
            )
            totals[outcome] += 1
    return dict(totals)


# --- online refunds (worker; FR-PAY-011) ----------------------------------------------------------


def send_refund(tenant_id: uuid.UUID, refund_id: uuid.UUID) -> str:
    """Worker: ask the provider to refund an approved ``original_method`` refund (idempotent
    ``receipt`` = the refund id, so a retry never refunds twice). A transient failure raises for
    the task to retry; a permanent one marks the refund failed and tells the requester."""
    with tenant_session(tenant_id) as session:
        row = repo.get_refund(session, refund_id, lock=True)
        if row is None or row.mode != "original_method" or row.status != "approved":
            return "skipped"
        receipt = receipts.load_receipt(session, row.receipt_id)
        conn = orepo.live_connection(session)
        if conn is None or conn.status != "active" or receipt.provider_payment_id is None:
            _refund_failed(session, row, None, receipt.provider_payment_id or "")
            return "failed"
        try:
            refund = _provider().create_refund(
                credentials(conn),
                receipt.provider_payment_id,
                row.amount,
                receipt=row.id.hex,
                notes={"refund": str(row.id)},
            )
        except ProviderError as exc:
            if exc.transient:
                raise
            if exc.code == "auth_failed":
                mark_revoked(session, conn, "auth_failed")
            _refund_failed(session, row, None, receipt.provider_payment_id)
            return "failed"
        row = repo.update_refund(
            session, row.id, {"status": "processing", "provider_refund_id": refund.id}
        )
        _audit(
            session,
            "fee.refund.sent",
            row.id,
            {"amount": c.money_text(row.amount), "mode": row.mode},
            resource_type="fee_refund",
            system=True,
        )
        if refund.status in ("processed", "failed"):
            try:
                return apply_refund(session, conn, receipt.provider_payment_id, refund.id)
            except _Retry:
                return "processing"
        return "processing"


def poll_refunds(tenant_id: uuid.UUID) -> int:
    """Worker: re-fetch refunds still in progress (a lost ``refund.processed`` webhook)."""
    done = 0
    with tenant_session(tenant_id) as session:
        conn = orepo.live_connection(session)
        if conn is None or conn.status != "active":
            return 0
        for row in orepo.refunds_in_flight(session, 100):
            if row.status == "approved" and row.provider_refund_id is None:
                # Its send task gave up (provider unreachable): ask again; the provider's
                # idempotency reference (the refund id) keeps it to one refund.
                refund_hooks.queue_online_refund(session, row.id)
                continue
            if row.provider_refund_id is None or row.status != "processing":
                continue
            receipt = receipts.load_receipt(session, row.receipt_id)
            if receipt.provider_payment_id is None:
                continue
            savepoint = session.begin_nested()
            try:
                apply_refund(session, conn, receipt.provider_payment_id, row.provider_refund_id)
                savepoint.commit()
                done += 1
            except _Retry:
                savepoint.rollback()
    return done


# --- reconciliation (worker; FR-PAY-010) ----------------------------------------------------------


def _day_bounds(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time.min, tzinfo=IST)
    return start, start + dt.timedelta(days=1) - dt.timedelta(seconds=1)


def _working_days_before(at: dt.datetime, days: int) -> dt.datetime:
    """``days`` working days (Monday to Friday) before ``at``."""
    cursor = at
    left = days
    while left > 0:
        cursor -= dt.timedelta(days=1)
        if cursor.astimezone(IST).weekday() < 5:
            left -= 1
    return cursor


def reconcile(tenant_id: uuid.UUID, day: dt.date | None = None) -> dict[str, int]:
    """Worker, daily 06:00 IST: compare the provider's payments, refunds and settlements of the
    previous IST day with receipts and vouchers (FR-PAY-010). Exceptions are recorded once each;
    nothing is changed in receipts or the ledger. Audit ``fee.reconciliation.run`` (counts)."""
    rcfg = cfg().reconciliation
    with tenant_session(tenant_id) as session:
        conn = orepo.live_connection(session)
        if conn is None or conn.status != "active":
            return {"skipped": 1}
        run_day = day or (c.today(session) - dt.timedelta(days=1))
        run = orepo.get_run(session, run_day)
        if run is not None and run.status == "done":
            return {"already_done": 1}
        if run is None:
            run = orepo.insert_run(
                session, id=new_id(), tenant_id=tenant_id, provider=PROVIDER, run_date=run_day
            )
        run_id, connection_id = run.id, conn.id
    counts: Counter[str] = Counter()
    try:
        state = _Run(tenant_id, connection_id, run_id, run_day, counts)
        _reconcile_payments(state, rcfg.page_size, rcfg.max_pages)
        _reconcile_receipts(state)
        _reconcile_settlements(state, rcfg)
    except (ProviderError, _Retry) as exc:
        code = exc.code
        with tenant_session(tenant_id) as session:
            orepo.update_run(
                session,
                run_id,
                {
                    "status": "failed",
                    "error_code": str(code)[:60],
                    "finished_at": orepo.now(session),
                    "payments_seen": counts["payments"],
                    "exceptions_opened": counts["exceptions"],
                },
            )
            if code == "auth_failed":
                found = orepo.live_connection(session)
                if found is not None:
                    mark_revoked(session, found, "auth_failed")
        return {**dict(counts), "failed": 1}
    with tenant_session(tenant_id) as session:
        orepo.update_run(
            session,
            run_id,
            {
                "status": "done",
                "error_code": None,
                "finished_at": orepo.now(session),
                "payments_seen": counts["payments"],
                "exceptions_opened": counts["exceptions"],
            },
        )
        _audit(
            session,
            "fee.reconciliation.run",
            run_id,
            {
                "run_date": run_day.isoformat(),
                "payments": counts["payments"],
                "exceptions": counts["exceptions"],
            },
            resource_type="fee_reconciliation_run",
            system=True,
        )
        open_count = orepo.count_open_items(session)
        if counts["exceptions"]:
            notifications.notify(
                session,
                tenant_id=tenant_id,
                recipients=notifications.PermissionSelector(c.RECONCILE),
                template_key=EXCEPTIONS_TEMPLATE,
                params={"count": open_count, "run_id": str(run_id)},
                resource_id=run_id,
                dedupe_key=f"{EXCEPTIONS_TEMPLATE}:{run_day.isoformat()}",
            )
    return dict(counts)


@contextmanager
def _with_connection(
    tenant_id: uuid.UUID, connection_id: uuid.UUID
) -> Iterator[tuple[Session, PaymentConnection]]:
    """One transaction of the school with its connection still active (reconciliation step)."""
    with tenant_session(tenant_id) as session:
        conn = orepo.get_connection(session, connection_id)
        if conn is None or conn.status != "active":
            raise _Retry("connection_revoked")
        yield session, conn


@dataclass(frozen=True, slots=True)
class _Run:
    """One school's reconciliation run (its ids, the IST day and the counters)."""

    tenant_id: uuid.UUID
    connection_id: uuid.UUID
    run_id: uuid.UUID
    day: dt.date
    counts: Counter[str]


def _reconcile_payments(run: _Run, page_size: int, max_pages: int) -> None:
    tenant_id, connection_id, run_id, day, counts = (
        run.tenant_id,
        run.connection_id,
        run.run_id,
        run.day,
        run.counts,
    )
    start, end = _day_bounds(day)
    provider = _provider()
    for page in range(max_pages):
        with _with_connection(tenant_id, connection_id) as (session, conn):
            creds = credentials(conn)
            rows = provider.list_payments(creds, start, end, skip=page * page_size, count=page_size)
            for payment in rows:
                counts["payments"] += 1
                order = (
                    _fetch(provider.fetch_order, creds, payment.order_id)
                    if payment.order_id
                    else None
                )
                reference = order.receipt if order is not None else None
                if not _is_ours(reference):
                    continue
                intent = _intent_for_reference(session, reference)
                _record_payment(
                    session,
                    conn,
                    payment,
                    order_id=payment.order_id,
                    intent=intent,
                    paid_at=payment.created_at,
                )
                receipt = orepo.receipt_for_payment(session, payment.id)
                captured = payment.captured and payment.status in ("captured", "refunded")
                if receipt is None:
                    if captured:
                        opened = open_exception(
                            session,
                            kind="paid_not_receipted" if intent else "unknown_intent",
                            reason="no_receipt" if intent else "intent_not_found",
                            dedupe=payment.id,
                            provider_payment_id=payment.id,
                            intent_id=intent.id if intent else None,
                            amount=payment.amount,
                            expected_amount=intent.amount if intent else None,
                            run_id=run_id,
                        )
                        counts["exceptions"] += int(opened)
                    continue
                if receipt.amount != payment.amount:
                    opened = open_exception(
                        session,
                        kind="amount_mismatch",
                        reason="receipt_amount",
                        dedupe=payment.id,
                        provider_payment_id=payment.id,
                        receipt_id=receipt.id,
                        amount=payment.amount,
                        expected_amount=receipt.amount,
                        run_id=run_id,
                    )
                    counts["exceptions"] += int(opened)
                vouchers = total(
                    r.amount
                    for r in repo.refunds_of(session, [receipt.id])
                    if r.status == "processed"
                )
                if payment.amount_refunded > vouchers:
                    opened = open_exception(
                        session,
                        kind="refunded",
                        reason="refund_without_voucher",
                        dedupe=f"{payment.id}:{payment.amount_refunded}",
                        provider_payment_id=payment.id,
                        receipt_id=receipt.id,
                        amount=payment.amount_refunded,
                        expected_amount=vouchers,
                        run_id=run_id,
                    )
                    counts["exceptions"] += int(opened)
        if len(rows) < page_size:
            break


def _reconcile_receipts(run: _Run) -> None:
    tenant_id, connection_id, run_id, day, counts = (
        run.tenant_id,
        run.connection_id,
        run.run_id,
        run.day,
        run.counts,
    )
    provider = _provider()
    with _with_connection(tenant_id, connection_id) as (session, conn):
        creds = credentials(conn)
        for receipt in orepo.online_receipts_between(session, day, day):
            if receipt.status != "issued" or receipt.provider_payment_id is None:
                continue
            payment = _fetch(provider.fetch_payment, creds, receipt.provider_payment_id)
            ok = (
                payment is not None
                and payment.captured
                and payment.status
                in (
                    "captured",
                    "refunded",
                )
            )
            if not ok:
                opened = open_exception(
                    session,
                    kind="receipted_not_captured",
                    reason="not_captured" if payment else "payment_not_found",
                    dedupe=receipt.provider_payment_id,
                    provider_payment_id=receipt.provider_payment_id,
                    receipt_id=receipt.id,
                    intent_id=receipt.payment_intent_id,
                    amount=payment.amount if payment else None,
                    expected_amount=receipt.amount,
                    run_id=run_id,
                )
                counts["exceptions"] += int(opened)


def _reconcile_settlements(run: _Run, rcfg: Any) -> None:
    tenant_id, connection_id, run_id, day, counts = (
        run.tenant_id,
        run.connection_id,
        run.run_id,
        run.day,
        run.counts,
    )
    provider = _provider()
    with _with_connection(tenant_id, connection_id) as (session, conn):
        creds = credentials(conn)
        for page in range(rcfg.max_pages):
            items = provider.settlement_items(creds, day, skip=page * 1000, count=1000)
            for item in items:
                if (
                    item.type == "payment"
                    and item.settled
                    and item.settled_at is not None
                    and orepo.set_settlement(
                        session, item.entity_id, item.settlement_id, item.settled_at
                    )
                ):
                    counts["settled"] += 1
            if len(items) < 1000:
                break
        cutoff = _working_days_before(
            dt.datetime.combine(day, dt.time.max, tzinfo=IST), rcfg.settlement_after_working_days
        )
        for payment in orepo.unsettled_payments(session, cutoff):
            opened = open_exception(
                session,
                kind="settlement_missing",
                reason="not_settled",
                dedupe=payment.provider_payment_id,
                provider_payment_id=payment.provider_payment_id,
                receipt_id=payment.receipt_id,
                intent_id=payment.intent_id,
                amount=payment.amount,
                run_id=run_id,
            )
            counts["exceptions"] += int(opened)


def expire_intents(tenant_id: uuid.UUID) -> int:
    """Worker: links past their expiry are ``expired`` (Razorpay expires them itself)."""
    with tenant_session(tenant_id) as session:
        now = orepo.now(session)
        rows = orepo.expired_open_intents(session, now, 1000)
        for intent in rows:
            orepo.update_intent(session, intent.id, {"status": "expired", "closed_at": now})
        return len(rows)


def purge_events(tenant_id: uuid.UUID) -> int:
    """Worker: processed inbox rows are deleted 90 days after processing (docs/05 §13)."""
    with tenant_session(tenant_id) as session:
        before = orepo.now(session) - dt.timedelta(days=cfg().inbox.retention_days)
        return orepo.purge_events(session, before)


def refresh_tokens(tenant_id: uuid.UUID) -> str:
    """Worker, daily: refresh OAuth tokens 14 days before the access token expires (the refresh
    token is single use: the new pair is stored in the same transaction), alert the owner 14
    days before the refresh token expires, and mark the connection revoked on a refusal
    (FR-PAY-012)."""
    ocfg = cfg().oauth
    with tenant_session(tenant_id) as session:
        conn = orepo.live_connection(session, lock=True)
        if conn is None or conn.mode != "oauth" or conn.status != "active":
            return "skipped"
        now = orepo.now(session)
        outcome = "fresh"
        if conn.access_expires_at is not None and conn.access_expires_at < now + dt.timedelta(
            days=ocfg.refresh_before_days
        ):
            try:
                tokens = _provider().refresh(_unwrap(conn, conn.refresh_token_ciphertext))
            except ProviderError as exc:
                if exc.code == "auth_failed":
                    mark_revoked(session, conn, "refresh_refused")
                    return "revoked"
                raise
            orepo.update_connection(
                session,
                conn.id,
                {**_token_values(conn.tenant_id, conn.id, tokens, now), "last_checked_at": now},
            )
            _audit(session, "payments.tokens_refreshed", conn.id, {"mode": conn.mode}, system=True)
            outcome = "refreshed"
            conn = orepo.get_connection(session, conn.id) or conn
        if conn.refresh_expires_at is not None and conn.refresh_expires_at < now + dt.timedelta(
            days=ocfg.alert_before_refresh_expiry_days
        ):
            days = max(0, (conn.refresh_expires_at - now).days)
            notifications.notify(
                session,
                tenant_id=conn.tenant_id,
                recipients=notifications.PermissionSelector(CONFIGURE),
                template_key=EXPIRING_TEMPLATE,
                params={"connection_id": str(conn.id), "days": days},
                resource_id=conn.id,
                dedupe_key=f"{EXPIRING_TEMPLATE}:{conn.id}:{conn.refresh_expires_at.date()}",
            )
        return outcome


def connected_tenant_ids() -> list[uuid.UUID]:
    with context_free_session() as session:
        return tenancy.list_tenant_ids(session, ("active",))


# --- reconciliation screen ------------------------------------------------------------------------


def _item_out(
    session: Session,
    item: ReconciliationItem,
    names: dict[uuid.UUID, str],
    runs: dict[uuid.UUID, dt.date],
) -> ReconciliationItemOut:
    receipt = repo.get_receipt(session, item.receipt_id) if item.receipt_id else None
    intent = orepo.get_intent(session, item.intent_id) if item.intent_id else None
    account_id = receipt.account_id if receipt else intent.account_id if intent else None
    return ReconciliationItemOut(
        id=item.id,
        kind=item.kind,
        reason_code=item.reason_code,
        status=item.status,
        provider_payment_id=item.provider_payment_id,
        provider_refund_id=item.provider_refund_id,
        intent_id=item.intent_id,
        intent_code=intent.intent_code if intent else None,
        receipt_id=item.receipt_id,
        receipt_number=receipt.receipt_number if receipt else None,
        refund_id=item.refund_id,
        account_id=account_id,
        amount=item.amount,
        expected_amount=item.expected_amount,
        note=item.note,
        explained_by=item.explained_by,
        explained_by_name=names.get(item.explained_by) if item.explained_by else None,
        explained_at=item.explained_at,
        resolved_at=item.resolved_at,
        created_at=item.created_at,
        run_date=runs.get(item.run_id) if item.run_id else None,
        version=item.version,
    )


def _require_recon_reader(session: Session, ctx: UserContext) -> None:
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE, c.READ)


def list_items(
    session: Session,
    ctx: UserContext,
    *,
    status: str | None,
    kind: str | None,
    limit: int,
    cursor: str | None,
) -> ReconciliationPage:
    """Online reconciliation exceptions, newest first (``finance.read`` or ``fee.manage``)."""
    _require_recon_reader(session, ctx)
    after: tuple[dt.datetime, uuid.UUID] | None = None
    data = decode_cursor(cursor)
    if data is not None:
        try:
            after = (dt.datetime.fromisoformat(str(data["t"])), uuid.UUID(str(data["i"])))
        except (KeyError, ValueError):
            raise ValidationFailed([c.error("cursor", "invalid")]) from None
    size = max(1, min(limit, cfg().reconciliation.list_max))
    rows = orepo.list_items(session, status=status, kind=kind, limit=size + 1, after=after)
    more = len(rows) > size
    rows = rows[:size]
    names = identity.member_display_names(session, c.unique_ids([r.explained_by for r in rows]))
    runs = {r.id: r.run_date for r in orepo.latest_runs(session, 400)}
    next_cursor = (
        encode_cursor({"t": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)})
        if more and rows
        else None
    )
    return ReconciliationPage(
        items=[_item_out(session, r, names, runs) for r in rows],
        next_cursor=next_cursor,
        open_count=orepo.count_open_items(session),
    )


def summary(session: Session, ctx: UserContext) -> ReconciliationSummaryOut:
    _require_recon_reader(session, ctx)
    conn = orepo.live_connection(session)
    return ReconciliationSummaryOut(
        connected=conn is not None and conn.status == "active",
        open_count=orepo.count_open_items(session),
        pending_events=orepo.count_events(session, "received"),
        failed_events=orepo.count_events(session, "failed"),
        runs=[
            ReconciliationRunOut(
                run_date=r.run_date,
                status=r.status,
                payments_seen=r.payments_seen,
                exceptions_opened=r.exceptions_opened,
                error_code=r.error_code,
                finished_at=r.finished_at,
            )
            for r in orepo.latest_runs(session, 14)
        ],
    )


def explain_item(
    session: Session,
    ctx: UserContext,
    item_id: uuid.UUID,
    data: ExplainIn,
    *,
    expected_version: int | None,
) -> ReconciliationItemOut:
    """Mark an exception explained with a note (``fee.reconcile``; FR-PAY-010). The note (10-500
    characters, never a full Aadhaar number) is stored, never logged or audited. Audit
    ``fee.reconciliation.explained`` (kind)."""
    c.require_on(session, ctx)
    c.require_school_wide(ctx, c.RECONCILE)
    item = orepo.get_item(session, item_id, lock=True)
    if item is None:
        raise NotFound("Reconciliation item not found")
    c.check_version(item, expected_version)
    if item.status != "open":
        raise Conflict(
            "This item was already explained or resolved. Reload to see it.", code="item_closed"
        )
    note = (data.note or "").strip()
    c.refuse_aadhaar("note", note)
    if len(note) < 10:
        raise ValidationFailed([c.error("note", "too_short")])
    with _db_errors():
        item = orepo.update_item(
            session,
            item.id,
            {
                "status": "explained",
                "note": note,
                "explained_by": ctx.membership_id,
                "explained_at": orepo.now(session),
            },
        )
    _audit(
        session,
        "fee.reconciliation.explained",
        item.id,
        {"kind": item.kind, "reason": item.reason_code},
        resource_type="fee_reconciliation_item",
    )
    names = identity.member_display_names(session, [ctx.membership_id])
    runs = {r.id: r.run_date for r in orepo.latest_runs(session, 400)}
    return _item_out(session, item, names, runs)


# --- DEK rotation (SEC-012) -----------------------------------------------------------------------


def reencrypt_batch(
    session: Session, keyring: dek.TenantKeyring, target_version: int, limit: int
) -> int:
    """Inbox payloads under another key version are re-encrypted to ``target_version`` with the
    same associated data (``app.students.rotation.Reencryptor``)."""
    done = 0
    for event in orepo.stale_event_payloads(session, target_version, limit):
        blob = dek.reencrypt_value(
            session,
            bytes(event.payload_ciphertext),
            table=EVENTS_TABLE,
            column="payload_ciphertext",
            row_id=event.id,
            key_version=target_version,
            keyring=keyring,
        )
        orepo.finish_event(session, event.id, {"payload_ciphertext": blob})
        done += 1
    return done


key_rotation.register_reencryptor("fees_online", reencrypt_batch)


__all__ = [
    "CONFIGURE",
    "INBOX_EVENT",
    "INBOX_TASK",
    "PaymentsNotConnected",
    "cancel_intent",
    "connect_manual",
    "create_intent",
    "create_intent_for_staff",
    "disconnect",
    "explain_item",
    "get_connection",
    "list_intents",
    "list_items",
    "oauth_callback",
    "oauth_start",
    "post_payment",
    "process_events",
    "reconcile",
    "rotate_webhook_secret",
    "send_refund",
    "store_event",
    "summary",
    "test_connection",
    "webhook_secrets",
]
