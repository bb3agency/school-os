"""Request and response bodies of the public routes (docs/09 §4 "Public"; ADR-0046).

Responses are explicit allowlists of fields (FR-PUB-009): nothing about a student, guardian,
phone, address or another school.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.public.config import ParentKind

ParentLanguage = Literal["en", "te"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LinkExchangeIn(_In):
    """The token from ``/p/{token}`` (the BFF's exchange-and-redirect handler sends it here)."""

    token: str = Field(min_length=1, max_length=128)


class LinkExchangeOut(_Out):
    """What the BFF needs to set the ``__Host-sos_parent`` cookie (max-age = until
    ``expires_at``). Exchanging does not count as a view."""

    kind: ParentKind
    expires_at: dt.datetime


class LinkEnvelopeOut(_Out):
    """The parent page's frame for the cookie's link (``/p/view``): which page it is, until when
    it works, its language (the guardian's choice, else the school's parent language, else
    English: ADR-0044) and the school's name for the page heading. The page content per kind is
    added by wave R2-3H (``app/public/pages.py``)."""

    kind: ParentKind
    expires_at: dt.datetime
    language: ParentLanguage
    school_name: str


__all__ = ["LinkEnvelopeOut", "LinkExchangeIn", "LinkExchangeOut", "ParentLanguage"]
