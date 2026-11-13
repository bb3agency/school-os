"""Board candidate routes: the scheme catalogue, the per-section entry grid and one student's
choices (FO-10; FR-STU-026..029; docs/09 Students).

Every route declares its permission with ``require()``; the service applies the caller's
class/section scope (404 outside scope or school). The save route scans the raw body for full
Aadhaar numbers first (SEC-013), as every student write does.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.authz.context import UserContext
from app.authz.dependencies import TenantDB, require
from app.students import board_candidate as candidates
from app.students import service as students
from app.students.api import AadhaarGuard
from app.students.board_candidate_schemas import (
    BoardCandidateGridOut,
    BoardCandidateSaveIn,
    BoardCandidateSaveOut,
    BoardSchemeOut,
    BoardStudentCandidateOut,
)

router = APIRouter(prefix="/api/v1", tags=["students"])

Reader = Annotated[UserContext, Depends(require(students.READ))]
Updater = Annotated[UserContext, Depends(require(students.UPDATE))]
SchemeKey = Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9-]{1,39}$")]


@router.get("/board-candidates/schemes", response_model=list[BoardSchemeOut])
def list_board_schemes(ctx: Reader) -> list[BoardSchemeOut]:
    """The board candidate schemes (medium, languages, subjects per board; ``verified: false``
    until a school confirmed them) (permission ``student.read_basic``)."""
    return candidates.list_schemes()


@router.get("/sections/{section_id}/board-candidates", response_model=BoardCandidateGridOut)
def section_board_candidates(
    ctx: Reader, db: TenantDB, section_id: uuid.UUID, scheme: SchemeKey = None
) -> BoardCandidateGridOut:
    """The section's students with their medium, languages, subjects and photo tick, and what
    is still missing for the board (permission ``student.read_basic``; 404 outside scope)."""
    return candidates.section_grid(db, ctx, section_id, scheme)


@router.put("/sections/{section_id}/board-candidates", response_model=BoardCandidateSaveOut)
def save_section_board_candidates(
    ctx: Updater,
    _aadhaar: AadhaarGuard,
    db: TenantDB,
    section_id: uuid.UUID,
    body: BoardCandidateSaveIn,
) -> BoardCandidateSaveOut:
    """Record the choices of many students of one section (permission
    ``student.update_nonidentity`` with its scope on each student). Fields left out are not
    changed; codes outside the scheme are refused (422 ``not_in_scheme``)."""
    return candidates.save_section(db, ctx, section_id, body)


@router.get("/students/{student_id}/board-candidate", response_model=BoardStudentCandidateOut)
def student_board_candidate(
    ctx: Reader, db: TenantDB, student_id: uuid.UUID
) -> BoardStudentCandidateOut:
    """One student's board candidate choices and what is missing (permission
    ``student.read_basic``; 404 outside scope)."""
    return candidates.student_candidate(db, ctx, student_id)
