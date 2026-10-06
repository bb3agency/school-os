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
    status, code, title = 429, "rate_limited", "Too many requests"


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
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError) -> JSONResponse:
        extra = {"errors": exc.errors} if isinstance(exc, ValidationFailed) else None
        return problem(
            request,
            status=exc.status,
            code=exc.code,
            title=exc.title,
            detail=exc.detail,
            extra=extra,
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
        if _is_nul_refusal(exc):
            return problem(
                request,
                status=422,
                code="invalid_characters",
                title="Validation failed",
                detail="Remove the invisible NUL character from the text and try again.",
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
        return problem(request, status=500, code="internal_error", title="Something went wrong")


_NUL_MESSAGE = "cannot contain NUL"


def _is_nul_refusal(exc: BaseException) -> bool:
    """The driver's refusal of a NUL (U+0000) in text (psycopg ``DataError``), raised directly or
    wrapped by SQLAlchemy. Free text in many request models may carry one; the database is the
    last place it is refused, so it is answered as a 422, not a 500 (audit 2026-10-06 R-12)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if type(current).__name__ == "DataError" and _NUL_MESSAGE in str(current):
            return True
        orig = getattr(current, "orig", None)
        current = orig if isinstance(orig, BaseException) else current.__cause__
    return False
