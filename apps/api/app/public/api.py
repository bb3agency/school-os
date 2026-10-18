"""Public parent-link routes (docs/09 §4 "Public"; ADR-0046, ADR-0047; FR-PUB-001, FR-PUB-008,
FR-PUB-010..012).

Called only by the web BFF's ``(public)`` route group with its service token and no user token
(:func:`app.public.guards.require_public_handle`); each route is listed in
``public_routes.yaml``. The guard resolves the key to a school; the handler then works only in
that school's ``tenant_session`` (RLS) and re-reads the handle, so a revocation that happened
after the resolve still wins. Every key that does not open a page gets the same 404.

- ``POST /public/links/exchange``: the BFF's ``/p/{token}`` handler sends the token here and,
  on 200, sets the ``__Host-sos_parent`` cookie until ``expires_at`` and answers 303 to
  ``/p/view``. Not a view (link previews end here).
- ``GET /public/links/current``: the cookie's page: kind, expiry, language, school name and the
  content block of its kind (R2-3H, ``app.public.pages``). Counts a view; the first one is
  audited. A link whose content cannot be shown is the same 404 (and not counted).
- ``GET /public/links/current/document``: the notice or receipt PDF (presigned, at most 5 min).
- ``POST /public/links/current/pay``: "Pay online" on a dues link: creates or reuses the payment
  link on the school's own Razorpay account; only new links spend ``public_pay`` (5 a day).
- ``GET /public/links/current/payment``: the status of that link (the return page polls it).
- ``POST /public/links/current/optin``: the parent's message language on an opt-in link.

The BFF adds what the browser needs (the CSRF token bound to the cookie, the Origin check, the
303 to Razorpay); none of these routes takes a user token.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.db import tenant_session
from app.core.languages import parent_language
from app.public import pages, service
from app.public.config import PARENT_KINDS, ParentKind
from app.public.guards import PublicCaller, PublicNotFound, require_public_handle
from app.public.schemas import (
    LinkEnvelopeOut,
    LinkExchangeIn,
    LinkExchangeOut,
    ParentDocumentLinkOut,
    ParentLanguage,
    ParentOptinIn,
    ParentOptinOut,
    ParentPayIn,
    ParentPaymentStatusOut,
    ParentPayOut,
)
from app.students import service as students
from app.tenancy import service as tenancy

router = APIRouter(prefix="/api/v1", tags=["public"])

ByBodyToken = Annotated[
    PublicCaller, Depends(require_public_handle(*PARENT_KINDS, key="body", param="token"))
]
ByCookieToken = Annotated[PublicCaller, Depends(require_public_handle(*PARENT_KINDS))]
# R2-3H: routes of one or two kinds (another kind's key is the uniform 404 at the guard).
DocumentToken = Annotated[
    PublicCaller, Depends(require_public_handle("parent_notice", "parent_receipt"))
]
DuesToken = Annotated[PublicCaller, Depends(require_public_handle("parent_dues"))]
OptinToken = Annotated[PublicCaller, Depends(require_public_handle("parent_optin"))]
PAY_POLICY = "public_pay"

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "Unknown, expired or revoked link (always the same body)."},
    429: {"description": "Too many requests or failed lookups from this address."},
}


def _parent_handle(
    session: Session, caller: PublicCaller
) -> tuple[service.HandleView, dt.datetime]:
    """The live parent link and its expiry (parent links always expire), else the 404."""
    handle = service.load_handle(session, caller.handle_id, PARENT_KINDS)
    if handle is None or handle.expires_at is None:
        raise PublicNotFound()
    return handle, handle.expires_at


def _kind(handle: service.HandleView) -> ParentKind:
    kind: ParentKind = handle.kind  # type: ignore[assignment]
    return kind


def _exchange(caller: PublicCaller) -> LinkExchangeOut:
    with tenant_session(caller.tenant_id) as session:
        handle, expires_at = _parent_handle(session, caller)
        return LinkExchangeOut(kind=_kind(handle), expires_at=expires_at)


@router.post("/public/links/exchange", response_model=LinkExchangeOut, responses=_NOT_FOUND)
def exchange_link(caller: ByBodyToken, body: LinkExchangeIn) -> LinkExchangeOut:
    """Check a parent link token for the BFF's exchange-and-redirect (FR-PUB-010). No user
    token; the BFF's service token only. Answers what the cookie needs; not counted as a view."""
    del body  # the guard read the token from the body
    return _exchange(caller)


def _language(session: Session, handle: service.HandleView) -> ParentLanguage:
    guardian_choice = (
        students.guardian_language(session, handle.guardian_id)
        if handle.guardian_id is not None
        else None
    )
    return parent_language(tenancy.parent_language(session), guardian_choice)


def _envelope(caller: PublicCaller) -> LinkEnvelopeOut:
    with tenant_session(caller.tenant_id) as session:
        handle, expires_at = _parent_handle(session, caller)
        language = _language(session, handle)
        content: dict[str, Any] = {}
        kind = _kind(handle)
        if kind == "parent_notice":
            content["notice"] = pages.notice(session, handle, language)
        elif kind == "parent_receipt":
            content["receipt"] = pages.receipt(session, handle)
        elif kind == "parent_dues":
            content["dues"] = pages.dues(session, handle)
        else:
            content["optin"] = pages.optin(session, handle)
        name = tenancy.school_display_name(session)
        service.record_view(session, handle)
        return LinkEnvelopeOut(
            kind=kind, expires_at=expires_at, language=language, school_name=name, **content
        )


@router.get("/public/links/current", response_model=LinkEnvelopeOut, responses=_NOT_FOUND)
def current_link(caller: ByCookieToken) -> LinkEnvelopeOut:
    """The parent page for the cookie's link (the BFF sends the cookie's token in
    ``X-SOS-Public-Token``): kind, expiry, language (guardian, else school, else English;
    FR-PUB-008), the school's name and the content of its kind (ADR-0046 decision 2;
    FR-PUB-009). Counts a view (FR-PUB-012)."""
    return _envelope(caller)


@router.get(
    "/public/links/current/document", response_model=ParentDocumentLinkOut, responses=_NOT_FOUND
)
def current_document(caller: DocumentToken) -> ParentDocumentLinkOut:
    """The PDF of a notice or receipt link, presigned for at most 5 minutes (FR-PUB-009); 404
    while there is none (a cancelled receipt, a notice whose files were removed). Audited by
    the owning module. Not a view."""
    with tenant_session(caller.tenant_id) as session:
        handle, _ = _parent_handle(session, caller)
        return pages.document(session, handle)


@router.post(
    "/public/links/current/pay",
    response_model=ParentPayOut,
    responses={
        **_NOT_FOUND,
        409: {"description": "Not connected, nothing due or too many open links."},
        502: {"description": "Razorpay did not make the link; try again."},
    },
)
def current_pay(request: Request, caller: DuesToken, body: ParentPayIn) -> ParentPayOut:
    """Pay online from a dues link (ADR-0046 decision 9; FR-PAY-005): creates, or reuses while
    it is valid, one payment link for the ticked instalments on the school's own Razorpay
    account (``created_via parent_page``), and answers where to send the parent. Idempotent per
    link and instalments: the same choice gets the same link back (``reused``) without spending
    the budget; new links are limited to 5 per dues link per day (``public_pay``, 429). The BFF
    checks the CSRF token and the Origin before calling."""
    partition = str(caller.handle_id)
    ratelimit.ensure_room(request, PAY_POLICY, partition)
    with tenant_session(caller.tenant_id) as session:
        handle, _ = _parent_handle(session, caller)
        out = pages.pay(session, handle, body.instalments)
    if not out.reused:
        ratelimit.charge(request, PAY_POLICY, partition)
    return out


@router.get(
    "/public/links/current/payment", response_model=ParentPaymentStatusOut, responses=_NOT_FOUND
)
def current_payment(
    caller: DuesToken,
    intent: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None,
) -> ParentPaymentStatusOut:
    """The status of the payment link Razorpay returned from (``intent`` = the link's reference,
    32 hex characters) or of the newest one this dues link made; 404 for anything else. The
    return page polls it; it never counts as a view."""
    with tenant_session(caller.tenant_id) as session:
        handle, _ = _parent_handle(session, caller)
        return pages.payment(session, handle, intent)


@router.post("/public/links/current/optin", response_model=ParentOptinOut, responses=_NOT_FOUND)
def current_optin(caller: OptinToken, body: ParentOptinIn) -> ParentOptinOut:
    """The parent's message language on an opt-in link ("Read messages in": English, Telugu or
    the school's choice; ADR-0044 decision 4, FR-LANG-003). Audited ``guardian.updated``. The
    page never records an opt-in or opt-out (ADR-0043 decision 7: STOP on WhatsApp)."""
    with tenant_session(caller.tenant_id) as session:
        handle, _ = _parent_handle(session, caller)
        return pages.set_language(session, handle, body.language)


__all__ = ["router"]
