"""Visibility oracle: who may see which corpus item (docs/06 §6 `<ALLOWED>`, FR-KB-002).

The harness judges leakage with this oracle, never with the system under test. It models the
product's rules as built, from the role matrix (`app/authz/roles.yaml`, copied in
:data:`ROLE_GRANTS` because the harness imports no application code) and the documents and
students services:

Documents (`documents.service` visibility, docs/05 §6.1; retrieval `knowledge.retrieval.acl`):

- ACL managers (`document.manage_acl` school-wide) see every document of their school;
- anyone else needs `document.read`; a document is visible when an ACL entry names the asker's
  role or membership, or a section/class the asker's grant reaches (a class scope covers its
  sections; a section scope makes its class match class-level entries);
- school-wide readers (`document.read` not limited by scopes) also see every section- or
  class-restricted document and documents with an EMPTY ACL; for anyone else an empty ACL
  matches nothing (fail closed);
- C3 (restricted) documents also need `student.read_sensitive` (the download rule; the uploader
  exception never applies to eval askers, who upload nothing).

Records (`students.service`: search and profile reads): `student.read_basic` school-wide, or
scoped to a reach that includes the student's current section. Only C2 fields are in the corpus
(C3 values are masked by the students service and never reach the model).

`retrievable` adds what retrieval applies on top: only `is_latest` document versions, and C3
documents never (they are neither indexed nor retrieved until retrieval enforces
`student.read_sensitive`: `chunking.yaml` `indexed_sensitivities`, `retrieval/acl.py`).
`apps/api/tests/knowledge/test_eval_bridge.py` pins this module against the application: the
role matrix against `roles.yaml`, and `retrievable`/`visible` against the production filter and
services for every asker over the whole corpus.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal

from sos_evals.schema import Asker, CorpusItem, Role

Grant = Literal["school", "scoped"]
"""How a role holds a permission: school-wide (✓, ✓ᴿ) or limited to its scopes (S)."""

DOCUMENT_READ: Final = "document.read"
DOCUMENT_MANAGE_ACL: Final = "document.manage_acl"
STUDENT_READ_BASIC: Final = "student.read_basic"
STUDENT_READ_SENSITIVE: Final = "student.read_sensitive"
KB_ASK: Final = "kb.ask"
PERMISSIONS: Final = (
    DOCUMENT_READ,
    DOCUMENT_MANAGE_ACL,
    STUDENT_READ_BASIC,
    STUDENT_READ_SENSITIVE,
    KB_ASK,
)
"""The permissions visibility depends on (the only columns of :data:`ROLE_GRANTS`)."""

_S: Final[Grant] = "school"
_P: Final[Grant] = "scoped"
_MANAGER: Final[Mapping[str, Grant]] = {
    DOCUMENT_READ: _S,
    DOCUMENT_MANAGE_ACL: _S,
    STUDENT_READ_BASIC: _S,
    STUDENT_READ_SENSITIVE: _S,
    KB_ASK: _S,
}
_READER: Final[Mapping[str, Grant]] = {DOCUMENT_READ: _S, STUDENT_READ_BASIC: _S, KB_ASK: _S}

ROLE_GRANTS: Final[Mapping[Role, Mapping[str, Grant]]] = {
    "owner": _MANAGER,
    "principal": _MANAGER,
    "office_admin": _MANAGER,
    "office_staff": _READER,
    "accountant": _READER,
    "exam_coordinator": _READER,
    "class_teacher": {
        DOCUMENT_READ: _P,
        STUDENT_READ_BASIC: _P,
        STUDENT_READ_SENSITIVE: _P,
        KB_ASK: _P,
    },
    "teacher": {DOCUMENT_READ: _P, STUDENT_READ_BASIC: _P, KB_ASK: _P},
    "auditor_readonly": {DOCUMENT_READ: _S, STUDENT_READ_BASIC: _S},
}
"""`app/authz/roles.yaml` restricted to :data:`PERMISSIONS` (pinned by the bridge tests)."""

ACADEMIC_STRUCTURE: Final[Mapping[str, tuple[str, ...]]] = {
    "9": ("9A", "9B"),
    "10": ("10A", "10B"),
}
"""Class -> sections of the current academic year, the same in every synthetic school (the
eval bridge provisions exactly this structure)."""

SECTIONS: Final = frozenset(s for sections in ACADEMIC_STRUCTURE.values() for s in sections)
CLASSES: Final = frozenset(ACADEMIC_STRUCTURE)

C3_RETRIEVED: Final = False
"""Retrieval as built never returns C3 documents (see the module docstring). Change this only
together with the application, when retrieval starts enforcing `student.read_sensitive`; the
parity test fails until both agree."""


def class_of(section: str) -> str | None:
    """The class of a section of :data:`ACADEMIC_STRUCTURE` (None for any other label)."""
    for klass, sections in ACADEMIC_STRUCTURE.items():
        if section in sections:
            return klass
    return None


def grant(asker: Asker, permission: str) -> Grant | None:
    return ROLE_GRANTS[asker.role].get(permission)


def has(asker: Asker, permission: str) -> bool:
    return grant(asker, permission) is not None


def reach(asker: Asker) -> tuple[frozenset[str], frozenset[str]]:
    """Sections and classes the asker's scopes reach, widened by the academic structure."""
    sections = set(asker.sections)
    classes = set(asker.classes)
    for klass in asker.classes:
        sections |= set(ACADEMIC_STRUCTURE.get(klass, ()))
    classes |= {k for s in asker.sections if (k := class_of(s)) is not None}
    return frozenset(sections), frozenset(classes)


def _document_visible(asker: Asker, item: CorpusItem) -> bool:
    if item.sensitivity == "C3" and not has(asker, STUDENT_READ_SENSITIVE):
        return False
    if grant(asker, DOCUMENT_MANAGE_ACL) == "school":
        return True
    read = grant(asker, DOCUMENT_READ)
    if read is None:
        return False
    acl = item.acl
    if asker.role in acl.roles or (asker.member is not None and asker.member in acl.members):
        return True
    if read == "school":
        empty = not (acl.roles or acl.members or acl.sections or acl.classes)
        return bool(acl.sections or acl.classes) or empty
    sections, classes = reach(asker)
    return bool(sections & set(acl.sections)) or bool(classes & set(acl.classes))


def _record_visible(asker: Asker, item: CorpusItem) -> bool:
    read = grant(asker, STUDENT_READ_BASIC)
    if read == "school":
        return True
    if read is None or item.student_section is None:
        return False
    return item.student_section in reach(asker)[0]


def visible(asker: Asker, item: CorpusItem) -> bool:
    """May the asker see this item at all (any version)?"""
    if item.tenant != asker.tenant:
        return False
    if item.kind == "record":
        return _record_visible(asker, item)
    return _document_visible(asker, item)


def retrievable(asker: Asker, item: CorpusItem) -> bool:
    """May retrieval hand this item to the asker's answer (latest versions, no C3)?"""
    if item.sensitivity == "C3" and not C3_RETRIEVED:
        return False
    return item.is_latest and visible(asker, item)


def can_ask(asker: Asker) -> bool:
    """Holds `kb.ask` (the ask pipeline refuses anyone else before retrieval)."""
    return has(asker, KB_ASK)
