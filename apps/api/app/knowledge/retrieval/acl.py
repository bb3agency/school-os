"""The ONE retrieval filter (docs/06 §6 ``<ALLOWED>``; FR-KB-002, SEC-018, invariant 8).

:func:`acl_predicate` is placed in the WHERE clause of every candidate branch (vector, full text,
keyword) and of the final detail query, so nothing the caller may not read is ever ranked,
returned or handed to a model. Tenant isolation is RLS on ``kb.document_chunks`` (invariant 1);
these keys only narrow within the school.

The rule mirrors the documents service's visibility rule (docs/05 §6.1, ``AclKeys``):

- ``sees_all`` (``document.manage_acl``): every document of the school;
- otherwise a chunk is visible when an ACL copy overlaps the caller's roles, sections or classes,
  or names the caller's membership (the caller's section/class keys are already widened by the
  academic structure: a class scope covers its sections, a section scope matches its class);
- school-wide readers also see every section- or class-restricted document and documents with an
  EMPTY ACL; for anyone else an empty ACL matches nothing (fail closed, never public).

Always, for everyone: only ``is_latest`` chunks and the optional filters. Restricted (``C3``)
documents only for callers whose keys carry ``read_sensitive`` (``student.read_sensitive``,
docs/05 §6.1); for everyone else the predicate excludes them in SQL, whatever their ACL says
(fail closed). The documents service also lets an uploader open their own C3 file; retrieval does
not (chunks carry no uploader), which is narrower, never wider.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Final

from sqlalchemy import (
    ARRAY,
    ColumnElement,
    Text,
    Uuid,
    and_,
    any_,
    bindparam,
    false,
    func,
    or_,
    true,
)

from app.knowledge.domain import AclKeys, SearchFilters
from app.knowledge.models import DocumentChunk

RESTRICTED_SENSITIVITY: Final = "C3"
"""Retrieved only with ``AclKeys.read_sensitive`` (see the module docstring)."""

_C = DocumentChunk


def _uuids(values: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    return sorted(values, key=str)


def _acl_match(acl: AclKeys) -> ColumnElement[bool]:
    matches: list[ColumnElement[bool]] = []
    if acl.roles:
        matches.append(
            _C.acl_roles.overlap(bindparam("acl_roles", sorted(acl.roles), type_=ARRAY(Text)))
        )
    if acl.section_ids:
        matches.append(
            _C.acl_sections.overlap(
                bindparam("acl_sections", _uuids(acl.section_ids), type_=ARRAY(Uuid()))
            )
        )
    if acl.class_ids:
        matches.append(
            _C.acl_classes.overlap(
                bindparam("acl_classes", _uuids(acl.class_ids), type_=ARRAY(Uuid()))
            )
        )
    matches.append(
        _C.acl_memberships.overlap(
            bindparam("acl_membership", [acl.membership_id], type_=ARRAY(Uuid()))
        )
    )
    if acl.school_wide:
        matches.append(func.cardinality(_C.acl_sections) > 0)
        matches.append(func.cardinality(_C.acl_classes) > 0)
        matches.append(
            and_(
                func.cardinality(_C.acl_roles) == 0,
                func.cardinality(_C.acl_sections) == 0,
                func.cardinality(_C.acl_classes) == 0,
                func.cardinality(_C.acl_memberships) == 0,
            )
        )
    return or_(*matches)


def acl_predicate(acl: AclKeys, filters: SearchFilters | None = None) -> ColumnElement[bool]:
    """``<ALLOWED>`` over ``kb.document_chunks`` for this caller (bound parameters only)."""
    clauses: list[ColumnElement[bool]] = [_C.is_latest == true()]
    if not acl.read_sensitive:
        clauses.append(
            _C.sensitivity != bindparam("restricted_sensitivity", RESTRICTED_SENSITIVITY)
        )
    if not acl.sees_all:
        clauses.append(_acl_match(acl))
    if filters is not None:
        if filters.doc_types is not None:
            if not filters.doc_types:
                clauses.append(false())
            else:
                clauses.append(
                    _C.doc_type
                    == any_(bindparam("doc_types", sorted(filters.doc_types), type_=ARRAY(Text)))
                )
        if filters.from_date is not None:
            clauses.append(_C.issued_on >= bindparam("from_date", filters.from_date))
    return and_(*clauses)


__all__ = ["RESTRICTED_SENSITIVITY", "acl_predicate"]
