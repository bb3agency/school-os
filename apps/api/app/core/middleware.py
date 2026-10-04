"""Pure ASGI middleware: request ID + access log, API security headers, body size limit.

- ``RequestContextMiddleware`` (NFR-OBS-001, docs/09 §2): accepts a well-formed ``X-Request-Id``
  or generates ``req_<uuid7 hex>``, stores it in ``request.state.request_id`` and the log context,
  echoes it on every response and writes ONE ``http.request`` line per request with the route
  *template*, method, status and duration. Bodies, query strings and headers are never logged
  (SEC-008). Unhandled exceptions become a problem+json 500 carrying the request ID and are then
  re-raised so the server and tracing still see them.
- ``SecurityHeadersMiddleware`` (SEC-010, docs/07 §11): API response headers; ``no-store`` on
  ``/api/v1/*`` unless a route sets its own ``Cache-Control``.
- ``BodySizeLimitMiddleware`` (docs/07 §11 API4): 1 MiB by default, a separate limit for
  ``multipart/form-data``, also 1 MiB: no route takes multipart (files go straight to presigned
  S3 URLs), so the header must not let an unauthenticated client make the API buffer more
  (SEC-010). Oversized requests get a 413 problem+json.

Pure ASGI (not ``BaseHTTPMiddleware``) so streaming, background tasks and context variables
behave normally.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable

from fastapi import FastAPI
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.routing import Match, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import Settings
from app.core.errors import problem
from app.core.ids import new_id
from app.core.logging import bind_context, get_logger, request_scope, reset_context

__all__ = [
    "API_CSP",
    "DEFAULT_MAX_BODY_BYTES",
    "DEFAULT_MAX_MULTIPART_BYTES",
    "REQUEST_ID_HEADER",
    "BodySizeLimitMiddleware",
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

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,64}")
_LOGGER_NAME = "app.http"


def new_request_id() -> str:
    return f"req_{new_id().hex}"


def _incoming_request_id(scope: Scope) -> str | None:
    value = Headers(scope=scope).get(REQUEST_ID_HEADER)
    if value is not None and _REQUEST_ID_RE.fullmatch(value):
        return value
    return None


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

        request_id = _incoming_request_id(scope) or new_request_id()
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


def install_middleware(
    app: FastAPI,
    settings: Settings,
    *,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    max_multipart_bytes: int = DEFAULT_MAX_MULTIPART_BYTES,
    quiet_paths: Iterable[str] = ("/healthz", "/readyz"),
) -> None:
    """Add the middleware in the right order (outermost last): headers > context > body limit."""
    docs_paths: set[str] = set()
    if not settings.is_production_like:
        for path in (app.docs_url, app.redoc_url, app.swagger_ui_oauth2_redirect_url):
            if path:
                docs_paths.add(path)
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_body_bytes=max_body_bytes,
        max_multipart_bytes=max_multipart_bytes,
    )
    app.add_middleware(RequestContextMiddleware, quiet_paths=quiet_paths)
    app.add_middleware(SecurityHeadersMiddleware, csp_exempt_paths=docs_paths)
