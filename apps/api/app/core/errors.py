"""Domain exceptions mapped centrally to RFC 9457 problem+json (09 §3).

Responses never include stack traces, SQL, or hints about other tenants' data.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_JSON = "application/problem+json"
ERROR_TYPE_BASE = "https://docs.schoolos.example/errors/"


class DomainError(Exception):
    status: int = 500
    code: str = "internal_error"
    title: str = "Something went wrong"

    def __init__(self, detail: str | None = None, *, code: str | None = None) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        if code is not None:
            self.code = code


class BadRequest(DomainError):
    status, code, title = 400, "bad_request", "Bad request"


class Unauthenticated(DomainError):
    status, code, title = 401, "unauthenticated", "Sign in required"


class Forbidden(DomainError):
    status, code, title = 403, "forbidden", "You do not have permission to do this"


class NotFound(DomainError):
    """Also used for resources outside the caller's tenant or scope (never reveal existence)."""

    status, code, title = 404, "not_found", "Not found"


class Conflict(DomainError):
    status, code, title = 409, "conflict", "The resource changed or is in the wrong state"


class PreconditionFailed(DomainError):
    status, code, title = 412, "precondition_failed", "The resource was changed by someone else"


class ValidationFailed(DomainError):
    status, code, title = 422, "validation_error", "Validation failed"

    def __init__(self, errors: list[dict[str, str]], detail: str | None = None) -> None:
        super().__init__(detail or f"{len(errors)} field(s) need attention")
        self.errors = errors


class StepUpRequired(DomainError):
    status, code, title = 428, "step_up_required", "Please confirm it's you with MFA"


class RateLimited(DomainError):
    """429 (RFC 6585). ``retry_after_s`` becomes the ``Retry-After`` header (RFC 9110 §10.2.3)
    and the ``retry_after`` member of the problem body, so the UI can say how long to wait."""

    status, code, title = 429, "rate_limited", "Too many requests"
    retry_after_s: int | None = None

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        retry_after_s: int | None = None,
    ) -> None:
        super().__init__(detail, code=code)
        if retry_after_s is not None:
            self.retry_after_s = max(1, int(retry_after_s))


class ServiceUnavailable(DomainError):
    status, code, title = 503, "service_unavailable", "Service temporarily unavailable"


def problem(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    extra: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": ERROR_TYPE_BASE + code,
        "title": title,
        "status": status,
        "code": code,
        "instance": request.url.path,
        "request_id": getattr(request.state, "request_id", None),
    }
    if detail:
        body["detail"] = detail
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        extra: dict[str, Any] | None = (
            {"errors": exc.errors} if isinstance(exc, ValidationFailed) else None
        )
        headers: dict[str, str] | None = None
        if isinstance(exc, RateLimited) and exc.retry_after_s is not None:
            headers = {"Retry-After": str(exc.retry_after_s)}
            extra = {"retry_after": exc.retry_after_s}
        return problem(
            request,
            status=exc.status,
            code=exc.code,
            title=exc.title,
            detail=exc.detail,
            extra=extra,
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {
                "field": ".".join(str(p) for p in err.get("loc", ())[1:]) or "body",
                "code": str(err.get("type", "invalid")),
                "message_key": f"errors.{err.get('type', 'invalid')}",
            }
            for err in exc.errors()
        ]
        # Never echo submitted values back (they may contain personal data).
        return problem(
            request,
            status=422,
            code="validation_error",
            title="Validation failed",
            detail=f"{len(errors)} field(s) need attention",
            extra={"errors": errors},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return problem(request, status=exc.status_code, code=code, title=str(exc.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        return problem(request, status=500, code="internal_error", title="Something went wrong")
