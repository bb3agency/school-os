"""Pure ASGI middleware: request ID + access log, API security headers, body size limit.

- ``RequestContextMiddleware`` (NFR-OBS-001, docs/09 §2): generates ``req_<uuid7 hex>`` for every
  request (a client's ``X-Request-Id`` is ignored: audit events store this id, so a caller must
  not choose it; audit 2026-10-06 hardening), stores it in ``request.state.request_id`` and the
  log context, returns it on every response and writes ONE ``http.request`` line per request
  with the route *template*, method, status and duration. Bodies, query strings and headers are
  never logged (SEC-008). Unhandled exceptions become a problem+json 500 carrying the request
  ID and are then re-raised so the server and tracing still see them.
- ``SecurityHeadersMiddleware`` (SEC-010, docs/07 §11): API response headers; ``no-store`` on
  ``/api/v1/*`` unless a route sets its own ``Cache-Control``.
- ``BodySizeLimitMiddleware`` (docs/07 §11 API4): 1 MiB by default, a separate limit for
  ``multipart/form-data``, also 1 MiB: no route takes multipart (files go straight to presigned
  S3 URLs), so the header must not let an unauthenticated client make the API buffer more
  (SEC-010). Oversized requests get a 413 problem+json.
- ``BidiControlMiddleware`` (audit 2026-10-06 hardening, CVE-2021-42574): a JSON body whose
  text (values or keys) holds a Unicode text-direction control (``Bidi_Control``: ALM, LRM, RLM,
  LRE..RLO, LRI..PDI) gets 422 ``invalid_characters``, so a name or note can never read
  differently on screen than it is stored. The signed machine routes (edge agents, fleet
  heartbeats) are exempt: they clean their own text.
- ``RateLimitMiddleware`` (P2-07, docs/09 §2.7): resolves the client IP from the trusted proxy
  chain, applies the per-IP layer (``machine_ip`` on the machine paths) and the IP's
  authentication backoff before the body is read, answers 429 problem+json with ``Retry-After``,
  and writes the ``RateLimit-Policy`` / ``RateLimit`` headers of every policy the request met
  (the route guards add theirs: ``app.core.ratelimit.enforce``). Health checks and CORS
  preflight are exempt.

Pure ASGI (not ``BaseHTTPMiddleware``) so streaming, background tasks and context variables
behave normally.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from typing import Any

from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.routing import Match, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import ratelimit
from app.core.config import Settings
from app.core.errors import problem
from app.core.ids import new_id
from app.core.logging import bind_context, get_logger, request_scope, reset_context

__all__ = [
    "API_CSP",
    "BIDI_CONTROLS",
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_MAX_MULTIPART_BYTES",
    "REQUEST_ID_HEADER",
    "BidiControlMiddleware",
    "BodySizeLimitMiddleware",
    "RateLimitMiddleware",
    "RequestContextMiddleware",
    "SecurityHeadersMiddleware",
    "install_middleware",
    "new_request_id",
]

REQUEST_ID_HEADER = "X-Request-Id"
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_MAX_MULTIPART_BYTES = DEFAULT_MAX_BODY_BYTES
API_PREFIX = "/api/v1"
API_CSP = "default-src 'none'; frame-ancestors 'none'"
SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    ("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
)

_LOGGER_NAME = "app.http"


def new_request_id() -> str:
    return f"req_{new_id().hex}"


def _route_template(scope: Scope) -> str | None:
    """The matched route's path template, never the raw path (which may carry identifiers)."""
    path = getattr(scope.get("route"), "path", None)
    if isinstance(path, str):
        return path
    # Plain Starlette routes (e.g. openapi.json) do not record themselves in the scope.
    for route in getattr(getattr(scope.get("app"), "router", None), "routes", ()):
        if isinstance(route, Route) and route.matches(scope)[0] is Match.FULL:
            return route.path
    return None


def _route_label(scope: Scope) -> str:
    method = str(scope.get("method", "")).upper()
    return f"{method} {_route_template(scope) or 'unmatched'}"


class RequestContextMiddleware:
    """Request ID, log context and the single ``http.request`` access line."""

    def __init__(self, app: ASGIApp, *, quiet_paths: Iterable[str] = ()) -> None:
        self.app = app
        self.quiet_paths = frozenset(quiet_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        status = 500
        started = False
        error_type: str | None = None

        async def send_with_id(message: Message) -> None:
            nonlocal status, started
            if message["type"] == "http.response.start":
                started = True
                status = int(message["status"])
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        start = time.perf_counter()
        with request_scope() as late_fields:
            tokens = bind_context(request_id=request_id)
            try:
                await self.app(scope, receive, send_with_id)
            except Exception as exc:
                error_type = type(exc).__name__
                if not started:
                    response = problem(
                        Request(scope),
                        status=500,
                        code="internal_error",
                        title="Something went wrong",
                    )
                    await response(scope, receive, send_with_id)
                raise
            finally:
                duration_ms = round((time.perf_counter() - start) * 1000)
                self._access_log(scope, status, duration_ms, error_type, late_fields)
                reset_context(tokens)

    def _access_log(
        self,
        scope: Scope,
        status: int,
        duration_ms: int,
        error_type: str | None,
        late_fields: dict[str, object],
    ) -> None:
        if status < 500 and scope.get("path") in self.quiet_paths:
            return
        fields: dict[str, object] = {
            "route": _route_label(scope),
            "method": str(scope.get("method", "")).upper(),
            "status": status,
            "duration_ms": duration_ms,
        }
        for key in ("tenant_id", "user_id"):
            if key in late_fields:
                fields[key] = late_fields[key]
        if error_type:
            fields["error_type"] = error_type
        log = get_logger(_LOGGER_NAME)
        if status >= 500:
            log.error("http.request", **fields)
        else:
            log.info("http.request", **fields)


def _at_least_as_strict(policy: str | None) -> bool:
    """A route's own CSP (e.g. an HTML memo with one hashed style block) is kept only when it
    still denies everything by default and forbids framing, like :data:`API_CSP`."""
    if not policy:
        return False
    directives = {d.strip().lower() for d in policy.split(";")}
    return "default-src 'none'" in directives and "frame-ancestors 'none'" in directives


class SecurityHeadersMiddleware:
    """Security headers for a JSON API (docs/07 §11)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        api_prefix: str = API_PREFIX,
        csp_exempt_paths: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.api_prefix = api_prefix
        self.csp_exempt_paths = frozenset(csp_exempt_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = str(scope.get("path", ""))
        is_api = path == self.api_prefix or path.startswith(self.api_prefix + "/")
        csp = path not in self.csp_exempt_paths

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    headers[name] = value
                if csp and not _at_least_as_strict(headers.get("content-security-policy")):
                    headers["Content-Security-Policy"] = API_CSP
                if is_api and "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyTooLarge(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body is too large")


class BodySizeLimitMiddleware:
    """Reject request bodies above a limit with 413 problem+json (before parsing)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        max_multipart_bytes: int = DEFAULT_MAX_MULTIPART_BYTES,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_multipart_bytes = max_multipart_bytes

    def _limit_for(self, headers: Headers) -> int:
        content_type = headers.get("content-type", "").lower()
        if content_type.startswith("multipart/form-data"):
            return self.max_multipart_bytes
        return self.max_body_bytes

    async def _reject(self, scope: Scope, receive: Receive, send: Send, limit: int) -> None:
        response = problem(
            Request(scope),
            status=413,
            code="payload_too_large",
            title="Request body is too large",
            detail=f"Send at most {limit} bytes in one request.",
        )
        await response(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        limit = self._limit_for(headers)
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(scope, receive, send, limit)
            return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded and not response_started:
                return  # drop the app's own error response; we send the 413 below
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except _BodyTooLarge:
            if response_started:
                raise
        if exceeded and not response_started:
            await self._reject(scope, receive, send, limit)


# Unicode Bidi_Control: ALM, LRM, RLM, LRE, RLE, PDF, LRO, RLO, LRI, RLI, FSI, PDI.
BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
# Cheap pre-check on the raw bytes: the characters in UTF-8, or as JSON escapes.
_BIDI_HINT = re.compile(
    rb"\xd8\x9c|\xe2\x80[\x8e\x8f\xaa-\xae]|\xe2\x81[\xa6-\xa9]"
    rb"|\\u(?:061[cC]|200[eEfF]|202[a-eA-E]|206[6-9])"
)
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
MACHINE_PATH_PREFIXES = ("/api/v1/edge/", "/api/v1/fleet/")


def _has_bidi(value: Any) -> bool:
    if isinstance(value, str):
        return any(ch in BIDI_CONTROLS for ch in value)
    if isinstance(value, dict):
        return any(_has_bidi(k) or _has_bidi(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_bidi(v) for v in value)
    return False


class BidiControlMiddleware:
    """Refuse text-direction controls in JSON request bodies (see the module docstring)."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        api_prefix: str = API_PREFIX,
        exempt_prefixes: Iterable[str] = MACHINE_PATH_PREFIXES,
    ) -> None:
        self.app = app
        self.api_prefix = api_prefix
        self.exempt_prefixes = tuple(exempt_prefixes)

    def _applies(self, scope: Scope) -> bool:
        path = str(scope.get("path", ""))
        if str(scope.get("method", "")).upper() not in _BODY_METHODS:
            return False
        if not path.startswith(self.api_prefix + "/") or path.startswith(self.exempt_prefixes):
            return False
        return "json" in Headers(scope=scope).get("content-type", "").lower()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self._applies(scope):
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        if _BIDI_HINT.search(body):
            try:
                parsed: Any = json.loads(body)
            except ValueError:
                parsed = None  # not JSON after all: the route answers it
            if _has_bidi(parsed):
                response = problem(
                    Request(scope),
                    status=422,
                    code="invalid_characters",
                    title="Validation failed",
                    detail="Remove the invisible text-direction characters from the text and "
                    "try again.",
                    extra={
                        "errors": [
                            {
                                "field": "body",
                                "code": "invalid_characters",
                                "message_key": "errors.invalid_characters",
                            }
                        ]
                    },
                )
                await response(scope, receive, send)
                return
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class RateLimitMiddleware:
    """Layer 1 (per client IP) and the RateLimit response headers (docs/09 §2.7)."""

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        self.trusted = settings.trusted_proxy_networks

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limiter = ratelimit.get_rate_limiter()
        config = limiter.config
        ip, internal = ratelimit.client_ip(scope, self.trusted)
        scope.setdefault("state", {})[ratelimit.CLIENT_IP_STATE] = ip
        path = str(scope.get("path", ""))
        method = str(scope.get("method", "")).upper()
        send_with_headers = self._with_headers(scope, send)
        exempt = method == "OPTIONS" or path in config.exempt_paths
        if limiter.enabled and not exempt:
            hashed = limiter.ip_hash(ip)
            policy = config.policy("machine_ip" if config.is_machine_path(path) else "ip")
            buckets = [] if internal else [ratelimit.Bucket(policy, hashed)]
            decision = limiter.check(buckets, [limiter.block_key("ip", hashed)])
            ratelimit.remember(scope, decision)
            if not decision.allowed:
                get_logger(_LOGGER_NAME).warning(
                    "security.rate_limited",
                    policy=decision.denied.name if decision.denied else "auth_backoff",
                    route=f"{method} unmatched",
                    ip_hash=hashed,
                    retry_after_s=decision.retry_after_s,
                )
                await self._reject(scope, receive, send_with_headers, decision)
                return
        await self.app(scope, receive, send_with_headers)

    @staticmethod
    def _with_headers(scope: Scope, send: Send) -> Send:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                states = scope.get("state", {}).get(ratelimit.RATE_LIMIT_STATE) or []
                values = ratelimit.header_values(states)
                if values is not None:
                    headers = MutableHeaders(scope=message)
                    headers["RateLimit-Policy"] = values[0]
                    headers["RateLimit"] = values[1]
            await send(message)

        return send_with_headers

    @staticmethod
    async def _reject(
        scope: Scope, receive: Receive, send: Send, decision: ratelimit.Decision
    ) -> None:
        seconds = max(1, decision.retry_after_s)
        response = problem(
            Request(scope),
            status=429,
            code="rate_limited",
            title="Too many requests",
            detail=f"Too many requests. Wait {seconds} seconds and try again.",
            extra={"retry_after": seconds},
            headers={"Retry-After": str(seconds)},
        )
        await response(scope, receive, send)


def install_middleware(
    app: FastAPI,
    settings: Settings,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    max_multipart_bytes: int = DEFAULT_MAX_MULTIPART_BYTES,
    quiet_paths: Iterable[str] = ("/healthz", "/readyz"),
) -> None:
    """Add the middleware in the right order (outermost last): headers > context > rate limit >
    body limit > text-direction check."""
    docs_paths: set[str] = set()
    if not settings.is_production_like:
        for path in (app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url):
            if path:
                docs_paths.add(path)
    app.add_middleware(BidiControlMiddleware)
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=max_body_bytes,
        max_multipart_bytes=max_multipart_bytes,
    )
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware, quiet_paths=quiet_paths)
    app.add_middleware(SecurityHeadersMiddleware, csp_exempt_paths=docs_paths)
