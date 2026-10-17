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
- ``GET /public/links/current``: the frame of the cookie's page (kind, expiry, language, school
  name). Counts a view; the first one is audited. Wave R2-3H adds the page content per kind.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.db import tenant_session
from app.core.languages import parent_language
from app.public import service
from app.public.config import PARENT_KINDS, ParentKind
from app.public.guards import PublicCaller, PublicNotFound, require_public_handle
from app.public.schemas import LinkEnvelopeOut, LinkExchangeIn, LinkExchangeOut
from app.students import service as students
from app.tenancy import service as tenancy

router = APIRouter(prefix="/api/v1", tags=["public"])

ByBodyToken = Annotated[
    PublicCaller, Depends(require_public_handle(*PARENT_KINDS, key="body", param="token"))
]
ByCookieToken = Annotated[PublicCaller, Depends(require_public_handle(*PARENT_KINDS))]

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


def _envelope(caller: PublicCaller) -> LinkEnvelopeOut:
    with tenant_session(caller.tenant_id) as session:
        handle, expires_at = _parent_handle(session, caller)
        guardian_choice = (
            students.guardian_language(session, handle.guardian_id)
            if handle.guardian_id is not None
            else None
        )
        language = parent_language(tenancy.parent_language(session), guardian_choice)
        name = tenancy.school_display_name(session)
        service.record_view(session, handle)
        return LinkEnvelopeOut(
            kind=_kind(handle), expires_at=expires_at, language=language, school_name=name
        )


@router.get("/public/links/current", response_model=LinkEnvelopeOut, responses=_NOT_FOUND)
def current_link(caller: ByCookieToken) -> LinkEnvelopeOut:
    """The parent page frame for the cookie's link (the BFF sends the cookie's token in
    ``X-SOS-Public-Token``): kind, expiry, language (guardian, else school, else English;
    FR-PUB-008) and the school's name. Counts a view (FR-PUB-012)."""
    return _envelope(caller)


__all__ = ["router"]
