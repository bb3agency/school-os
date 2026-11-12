"""Fix-owner letter routes (FA-08; FR-DQ-049..FR-DQ-060; docs/09 Data quality).

``dq.readiness.manage`` lists the letters and the students they cover, prints the parent letters
and records letters given or sent; the board letter leaves the school, so it needs
``export.portal`` with a recent MFA sign-in (like the UDISE+ letter to the MEO). A student,
section or finding outside the caller's reach, or of another school, is 404. No route changes a
student record (invariant 6); no AI writes any letter.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response
from fastapi.responses import HTMLResponse

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.authz.http import IdempotencyDep
from app.dq import fix_letters
from app.dq.fix_letter_page import STYLE_CSP
from app.dq.fix_letter_schemas import (
    FixLetterKey,
    FixLetterSentIn,
    FixLetterSentOut,
    FixLettersOut,
)
from app.dq.schemas import PROFILE_PATTERN

router = APIRouter(prefix="/api/v1/dq/readiness", tags=["data-quality"])

Manager = Annotated[UserContext, Depends(require(fix_letters.MANAGE))]
BoardPrinter = Annotated[UserContext, Depends(require(fix_letters.PRINT_BOARD, step_up=True))]
ProfileKey = Annotated[
    str,
    Path(
        pattern=PROFILE_PATTERN,
        max_length=64,
        description="Readiness profile, e.g. bseap-ssc-2027",
    ),
]
Check = Annotated[
    bool, Query(description="true: only check that the letters can be printed now; 204")
]

_PAGE_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": STYLE_CSP,
    "X-Content-Type-Options": "nosniff",
}
_PRINT_RESPONSES: dict[int | str, dict[str, object]] = {
    200: {"content": {"text/html": {}}, "description": "Print-ready A4 letters"},
    204: {"description": "With check=true: the letters can be printed now (nothing audited)"},
}


def _page(page: str | None, name: str) -> Response:
    if page is None:
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    return HTMLResponse(
        page, headers={**_PAGE_HEADERS, "Content-Disposition": f'inline; filename="{name}"'}
    )


@router.get("/{profile_key}/fix-letters", response_model=FixLettersOut)
def list_fix_letters(
    ctx: Manager,
    db: TenantDB,
    profile_key: ProfileKey,
    section_id: uuid.UUID | None = None,
) -> FixLettersOut:
    """The fix-owner letters offered for the profile (the parent letter asking for an Aadhaar
    update, the board registration correction letter) and, for each, the students it covers now
    with the fields it lists (labels only, never a value) and the date it was last recorded as
    given or sent (permission ``dq.readiness.manage``; a section outside your reach: 404). The
    UDISE+ letter to the MEO is the UDISE+ correction request."""
    return fix_letters.overview(db, ctx, profile_key, section_id=section_id)


@router.get(
    "/{profile_key}/parent-letters", response_class=HTMLResponse, responses=_PRINT_RESPONSES
)
def parent_letters(
    *,
    ctx: Manager,
    db: TenantDB,
    profile_key: ProfileKey,
    student_id: uuid.UUID | None = None,
    section_id: uuid.UUID | None = None,
    finding_id: uuid.UUID | None = None,
    check: Check = False,
) -> Response:
    """Letters to parents asking for an Aadhaar update at an Aadhaar centre (A4, one per
    student), for one student, one readiness finding or one section (exactly one; permission
    ``dq.readiness.manage``). Each lists the field, the value in the school's record and what
    differs: never the Aadhaar value or number. Audited; with ``check=true`` 204 only, not
    audited. 409 ``nothing_to_print`` when no open Aadhaar difference is left."""
    page = fix_letters.parent_letters(
        db,
        ctx,
        profile_key,
        student_id=student_id,
        section_id=section_id,
        finding_id=finding_id,
        check=check,
    )
    return _page(page, f"parent-letters-{profile_key}.html")


@router.get("/{profile_key}/board-letter", response_class=HTMLResponse, responses=_PRINT_RESPONSES)
def board_letter(
    *,
    ctx: BoardPrinter,
    db: TenantDB,
    profile_key: ProfileKey,
    section_id: uuid.UUID | None = None,
    recipient: Annotated[
        str | None, Query(pattern=r"^[a-z_]{2,30}$", description="Recipient role key")
    ] = None,
    check: Check = False,
) -> Response:
    """The A4 letter to the board office listing the open board registration corrections of the
    profile or one section (permission ``export.portal`` with a recent MFA sign-in, 428
    ``step_up_required``): PEN, student, field, the value in the registration, the correct
    value, its record and the evidence held. Audited; ``check=true`` 204 only. 409
    ``nothing_to_print``."""
    page = fix_letters.board_letter(
        db, ctx, profile_key, section_id=section_id, recipient=recipient, check=check
    )
    return _page(page, f"board-letter-{profile_key}.html")


@router.post(
    "/{profile_key}/fix-letters/{letter_key}/sent",
    response_model=FixLetterSentOut,
    status_code=201,
)
def record_fix_letters_sent(
    *,
    ctx: Manager,
    db: TenantDB,
    profile_key: ProfileKey,
    letter_key: Annotated[FixLetterKey, Path(max_length=40, description="Letter key")],
    body: FixLetterSentIn,
    idem: IdempotencyDep,
) -> Response:
    """Record that the letters were given to the parents (follow-up step ``parent_contacted``
    by letter) or that the board letter was sent (``request_sent`` by letter), for up to 300
    students (permission ``dq.readiness.manage``). Each student must be in your reach (else 404)
    and still have an open difference for this letter (else 422 ``no_open_fix``). Audited as a
    follow-up step. Accepts ``Idempotency-Key``."""

    def run() -> FixLetterSentOut:
        recorded, letter = fix_letters.record_sent(
            db,
            ctx,
            profile_key,
            letter_key,
            student_ids=body.student_ids,
            occurred_on=body.occurred_on,
        )
        return FixLetterSentOut(
            recorded=recorded,
            fix_owner=letter.owner,
            event=letter.followup.event,
            channel=letter.followup.channel,
        )

    return idem.run(db, body, run, status_code=201)
