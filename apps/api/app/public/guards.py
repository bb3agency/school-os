"""Route guards for requests without a signed-in member (ADR-0047 decision 4; FR-PUB-003,
FR-PUB-011; SEC-034, SEC-036; CLAUDE.md invariant 2).

Three dependencies, each carrying ``sos_permission`` (a pseudo-permission no role can hold:
it is not in the catalog) and ``sos_public_guard``, so the route-enumeration test accepts exactly
them on exactly the routes listed in ``public_routes.yaml``:

- :func:`require_public_handle` (parent pages and certificate verification, called by the BFF):
  1. the BFF service token is required and a user token is refused (FR-PUB-001; 401);
  2. room in the per-IP failed-lookup budget ``public_lookup_fail`` (429 before any lookup, so a
     used-up budget cannot be used as an oracle); the per-IP layer ``public_ip`` already ran in
     the middleware;
  3. the key (header ``X-SOS-Public-Token`` set by the BFF from its cookie, a JSON body field,
     or a path parameter) is shape-checked and resolved through ``core.resolve_public_handle``;
     unknown, malformed, expired, revoked and suspended-school keys all charge the failure
     budget (cost 2) and get the same 404 body (``not_found``, "Not found");
  4. the per-handle budget ``public_token`` is charged (429).
  It returns :class:`PublicCaller` (school id, handle id, kind): the route then opens that
  school's ``tenant_session`` and re-reads the handle (``public.service.load_handle``).
- :func:`require_webhook_signature` (provider webhooks, straight to the API): the provider's
  body limit (413), its registered verifier (404 while none is registered; 401 without detail
  for a bad or missing signature; 404 for an unknown route), the per-route-handle budget
  ``webhook_route`` (429), and replay detection by event id (a repeat is marked
  ``duplicate``: the handler answers 200 without processing it again).
- :func:`require_meta_verify_token` (Meta's subscription challenge, ``GET``): ``hub.mode`` =
  ``subscribe`` and ``hub.verify_token`` equal (constant time) to ``SOS_META_WEBHOOK_VERIFY_TOKEN``
  (403 otherwise, and always while the setting is unset); returns ``hub.challenge``.

Log lines carry kinds, reason codes and ``ip_hash`` only: never a key, token, signature, header
or body (invariant 5; docs/07 §11.2 "no token in any log line").
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from dataclasses import dataclass
from typing import Annotated, Final, Literal

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from app.authz.kv import KVUnavailable, kv_store
from app.core import ratelimit
from app.core.config import get_settings
from app.core.errors import (
    DomainError,
    Forbidden,
    NotFound,
    ServiceUnavailable,
    Unauthenticated,
)
from app.core.logging import get_logger
from app.identity.service_token import (
    SERVICE_TOKEN_HEADER,
    ServiceTokenVerifier,
    get_service_token_verifier,
)
from app.public import service
from app.public.config import (
    HANDLE_KINDS,
    WEBHOOK_PROVIDERS,
    HandleKind,
    WebhookProvider,
    rules,
)
from app.public.webhooks import (
    WebhookRejected,
    WebhookRequest,
    WebhookUnknownRoute,
    webhook_verifier,
)

log = get_logger(__name__)

PUBLIC_TOKEN_HEADER: Final = "X-SOS-Public-Token"  # noqa: S105 (a header name)
PSEUDO_HANDLE: Final = "public.handle"
PSEUDO_WEBHOOK: Final = "public.webhook"
PSEUDO_META_VERIFY: Final = "public.meta_verify"
"""Pseudo-permissions: not in the catalog, no role can hold them (like ``tally.agent``)."""
LOOKUP_FAILURE_COST: Final = 2
_REPLAY_PREFIX: Final = "sos:webhook:seen:"
_SUBSCRIBE: Final = "subscribe"
_CHALLENGE: Final = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
KeySource = Literal["header", "body", "path"]


class PublicNotFound(NotFound):
    """The one answer for every key that does not open a page (ADR-0047 decision 5)."""

    def __init__(self) -> None:
        super().__init__()


class PayloadTooLarge(DomainError):
    status, code, title = 413, "payload_too_large", "Request body is too large"


@dataclass(frozen=True, slots=True)
class PublicCaller:
    """A live handle found by the resolver: the school to open and the handle to re-read."""

    tenant_id: uuid.UUID
    handle_id: uuid.UUID
    kind: HandleKind


@dataclass(frozen=True, slots=True)
class WebhookCaller:
    """A delivery whose signature checked out. ``duplicate``: its event id was seen within the
    replay window (answer 200, do nothing). ``body`` is the raw, verified body."""

    provider: WebhookProvider
    event_id: str | None
    handles: tuple[service.ResolvedHandle, ...]
    body: bytes
    duplicate: bool


def _ip_hash(request: Request) -> str:
    return ratelimit.ip_hash(ratelimit.client_ip_of(request))


def _service_only(request: Request, verifier: ServiceTokenVerifier) -> None:
    """The BFF's service token and no user token (FR-PUB-001)."""
    if request.headers.get("authorization") is not None:
        ratelimit.record_auth_failure(request, reason="user_token_on_public_route")
        raise Unauthenticated("Public pages take no user sign-in")
    token = request.headers.get(SERVICE_TOKEN_HEADER)
    try:
        if not token:
            raise Unauthenticated("Requests must come through the SchoolOS web app")
        verifier.verify(token)
    except Unauthenticated:
        ratelimit.record_auth_failure(request, reason="token_rejected")
        raise


class RequirePublicHandle:
    """Dependency of the parent page and verification routes (see the module docstring)."""

    sos_permission = PSEUDO_HANDLE
    sos_public_guard = "public_handle"
    sos_step_up = False
    sos_scope = None

    def __init__(self, kinds: tuple[HandleKind, ...], *, key: KeySource, param: str) -> None:
        if not kinds or any(k not in HANDLE_KINDS for k in kinds):
            raise ValueError(f"unknown public handle kinds {kinds!r}")
        if key not in ("header", "body", "path"):
            raise ValueError(f"unknown key source {key!r}")
        self.sos_handle_kinds: tuple[HandleKind, ...] = kinds
        self.key: KeySource = key
        self.param = param

    def __repr__(self) -> str:
        return f"require_public_handle({', '.join(map(repr, self.sos_handle_kinds))})"

    async def _key(self, request: Request) -> str | None:
        if self.key == "header":
            return request.headers.get(PUBLIC_TOKEN_HEADER)
        if self.key == "path":
            value = request.path_params.get(self.param)
            return value if isinstance(value, str) else None
        try:
            body = json.loads(await request.body() or b"null")
        except (ValueError, UnicodeDecodeError):
            return None
        value = body.get(self.param) if isinstance(body, dict) else None
        return value if isinstance(value, str) else None

    async def __call__(
        self,
        request: Request,
        verifier: Annotated[ServiceTokenVerifier, Depends(get_service_token_verifier)],
    ) -> PublicCaller:
        _service_only(request, verifier)
        partition = _ip_hash(request)
        ratelimit.ensure_room(request, "public_lookup_fail", partition, cost=LOOKUP_FAILURE_COST)
        key = await self._key(request)
        found = (
            await run_in_threadpool(service.resolve_any, self.sos_handle_kinds, key)
            if key is not None
            else None
        )
        if found is None:
            ratelimit.charge(request, "public_lookup_fail", partition, cost=LOOKUP_FAILURE_COST)
            log.warning(
                "public.lookup_failed",
                resource_type="public_handle",
                action=self.sos_handle_kinds[0] if len(self.sos_handle_kinds) == 1 else "parent",
                ip_hash=partition,
                outcome="not_found",
            )
            raise PublicNotFound()
        ratelimit.charge(request, "public_token", str(found.handle_id))
        return PublicCaller(tenant_id=found.tenant_id, handle_id=found.handle_id, kind=found.kind)


def require_public_handle(
    *kinds: HandleKind, key: KeySource = "header", param: str = "token"
) -> RequirePublicHandle:
    """Guard for a public page route: the key comes from ``key`` (``header``: the BFF puts the
    parent cookie's token in ``X-SOS-Public-Token``; ``body``: the JSON field ``param``;
    ``path``: the path parameter ``param``)."""
    return RequirePublicHandle(tuple(kinds), key=key, param=param)


async def _limited_body(request: Request, limit: int) -> bytes:
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise PayloadTooLarge(f"Send at most {limit} bytes in one request.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise PayloadTooLarge(f"Send at most {limit} bytes in one request.")
        chunks.append(chunk)
    return b"".join(chunks)


def _seen_before(provider: WebhookProvider, event_id: str) -> bool:
    """Claim the event id for the replay window; ``True`` when it was already claimed."""
    digest = hashlib.sha256(f"{provider}|{event_id}".encode()).hexdigest()
    ttl = rules().replay_window_hours * 3600
    try:
        fresh = kv_store().set(f"{_REPLAY_PREFIX}{digest}", b"1", ttl_s=ttl, nx=True)
    except KVUnavailable:
        log.warning(
            "security.webhook.rejected",
            resource_type="webhook",
            action=provider,
            error_code="kv_unavailable",
        )
        raise ServiceUnavailable() from None
    return not fresh


class RequireWebhookSignature:
    """Dependency of a provider webhook route (see the module docstring)."""

    sos_permission = PSEUDO_WEBHOOK
    sos_public_guard = "webhook_signature"
    sos_step_up = False
    sos_scope = None

    def __init__(self, provider: WebhookProvider) -> None:
        if provider not in WEBHOOK_PROVIDERS:
            raise ValueError(f"unknown webhook provider {provider!r}")
        self.sos_provider: WebhookProvider = provider

    def __repr__(self) -> str:
        return f"require_webhook_signature({self.sos_provider!r})"

    async def __call__(self, request: Request) -> WebhookCaller:
        provider = self.sos_provider
        verifier = webhook_verifier(provider)
        if verifier is None:
            raise NotFound()
        limit = rules().webhook_providers[provider].max_body_bytes
        body = await _limited_body(request, limit)
        path_params = {k: str(v) for k, v in request.path_params.items()}
        call = WebhookRequest(
            provider=provider,
            headers=request.headers,
            body=body,
            path_params=path_params,
            resolve=service.resolve,
        )
        try:
            verified = await run_in_threadpool(verifier.verify, call)
        except WebhookUnknownRoute:
            log.warning(
                "security.webhook.rejected",
                resource_type="webhook",
                action=provider,
                error_code="unknown_route",
                ip_hash=_ip_hash(request),
            )
            raise NotFound() from None
        except WebhookRejected as rejected:
            log.warning(
                "security.webhook.rejected",
                resource_type="webhook",
                action=provider,
                error_code=rejected.reason,
                ip_hash=_ip_hash(request),
            )
            raise Unauthenticated() from None
        for handle in verified.handles:
            ratelimit.charge(request, "webhook_route", str(handle.handle_id))
        duplicate = verified.event_id is not None and await run_in_threadpool(
            _seen_before, provider, verified.event_id
        )
        return WebhookCaller(
            provider=provider,
            event_id=verified.event_id,
            handles=verified.handles,
            body=body,
            duplicate=duplicate,
        )


def require_webhook_signature(provider: WebhookProvider) -> RequireWebhookSignature:
    return RequireWebhookSignature(provider)


class RequireMetaVerifyToken:
    """Dependency of ``GET /api/v1/webhooks/meta`` (Meta's subscription challenge)."""

    sos_permission = PSEUDO_META_VERIFY
    sos_public_guard = "meta_verify_token"
    sos_step_up = False
    sos_scope = None

    def __repr__(self) -> str:
        return "require_meta_verify_token()"

    def __call__(self, request: Request) -> str:
        expected = get_settings().meta_webhook_verify_token
        query = request.query_params
        given = query.get("hub.verify_token") or ""
        challenge = query.get("hub.challenge") or ""
        ok = (
            expected is not None
            and query.get("hub.mode") == _SUBSCRIBE
            and hmac.compare_digest(given.encode(), expected.get_secret_value().encode())
            and _CHALLENGE.fullmatch(challenge) is not None
        )
        if not ok:
            log.warning(
                "security.webhook.rejected",
                resource_type="webhook",
                action="meta",
                error_code="verify_token_mismatch" if expected else "verify_token_unset",
                ip_hash=_ip_hash(request),
            )
            raise Forbidden()
        return challenge


def require_meta_verify_token() -> RequireMetaVerifyToken:
    return RequireMetaVerifyToken()


__all__ = [
    "LOOKUP_FAILURE_COST",
    "PSEUDO_HANDLE",
    "PSEUDO_META_VERIFY",
    "PSEUDO_WEBHOOK",
    "PUBLIC_TOKEN_HEADER",
    "PayloadTooLarge",
    "PublicCaller",
    "PublicNotFound",
    "RequireMetaVerifyToken",
    "RequirePublicHandle",
    "RequireWebhookSignature",
    "WebhookCaller",
    "require_meta_verify_token",
    "require_public_handle",
    "require_webhook_signature",
]
