"""The visibility oracle models the product's rules (docs/05 §6.1, docs/06 §6; FR-KB-002).

Pure unit tests of `sos_evals.acl`. Parity with the application itself (roles.yaml, the SQL
retrieval filter, the documents and students services) is pinned by
`apps/api/tests/knowledge/test_eval_bridge.py`.
"""

from __future__ import annotations

import pytest

from sos_evals import acl
from sos_evals.schema import Acl, Asker, CorpusItem, Role

T = "t1"


def _doc(acl_: Acl, *, sensitivity: str = "C1", latest: bool = True, tenant: str = T) -> CorpusItem:
    return CorpusItem.model_validate(
        {
            "source": "sos://doc/00000000-0000-5000-8000-0000000000aa/v1#p1",
            "tenant": tenant,
            "kind": "document",
            "doc_type": "circular",
            "title": "t",
            "locale": "en",
            "is_latest": latest,
            "acl": acl_,
            "sensitivity": sensitivity,
            "content": "x MK-0000AA",
            "marker": "MK-0000AA",
        }
    )


def _record(section: str, tenant: str = T) -> CorpusItem:
    return CorpusItem.model_validate(
        {
            "source": "sos://student/00000000-0000-5000-8000-0000000000bb/field/dob?src=x",
            "tenant": tenant,
            "kind": "record",
            "doc_type": "student_field",
            "title": "t",
            "locale": "en",
            "student_section": section,
            "content": "Date of birth: 01/01/2012. MK-0000BB",
            "marker": "MK-0000BB",
        }
    )


def _asker(role: Role, sections: tuple[str, ...] = (), classes: tuple[str, ...] = ()) -> Asker:
    return Asker(tenant=T, role=role, sections=sections, classes=classes)


ROLE_ONLY = _doc(Acl(roles=("accountant",)))
SECTION_9B = _doc(Acl(sections=("9B",)))
CLASS_10 = _doc(Acl(classes=("10",)))
CLASS_9 = _doc(Acl(classes=("9",)))
EMPTY = _doc(Acl())
MEMBER = _doc(Acl(members=("lab-incharge",)))


@pytest.mark.parametrize("role", ["owner", "principal", "office_admin"])
def test_FR_KB_002_acl_managers_see_every_document_of_their_school(role: Role) -> None:
    manager = _asker(role)
    for item in (ROLE_ONLY, SECTION_9B, CLASS_10, EMPTY, MEMBER):
        assert acl.visible(manager, item)
        assert not acl.visible(manager, item.model_copy(update={"tenant": "t2"}))


@pytest.mark.parametrize("role", ["office_staff", "exam_coordinator", "auditor_readonly"])
def test_FR_KB_002_school_wide_readers_see_restricted_and_empty_acls_but_not_other_roles(
    role: Role,
) -> None:
    reader = _asker(role)
    assert acl.visible(reader, SECTION_9B)
    assert acl.visible(reader, CLASS_10)
    assert acl.visible(reader, EMPTY)
    assert not acl.visible(reader, ROLE_ONLY)
    assert not acl.visible(reader, MEMBER)


def test_FR_KB_002_role_entries_match_whatever_the_scope() -> None:
    assert acl.visible(_asker("accountant"), ROLE_ONLY)
    assert acl.visible(Asker(tenant=T, role="teacher"), _doc(Acl(roles=("teacher",))))


def test_FR_KB_002_scoped_readers_see_their_reach_only_and_never_an_empty_acl() -> None:
    ct_9a = _asker("class_teacher", sections=("9A",))
    assert not acl.visible(ct_9a, SECTION_9B)
    assert acl.visible(ct_9a, CLASS_9)  # a section scope makes its class match
    assert not acl.visible(ct_9a, CLASS_10)
    assert not acl.visible(ct_9a, EMPTY)
    teacher_10 = _asker("teacher", classes=("10",))
    assert acl.visible(teacher_10, CLASS_10)
    assert acl.visible(teacher_10, _doc(Acl(sections=("10B",))))  # a class scope covers sections
    assert not acl.visible(teacher_10, SECTION_9B)
    assert acl.reach(teacher_10) == (frozenset({"10A", "10B"}), frozenset({"10"}))
    unscoped = _asker("teacher")
    assert not any(acl.visible(unscoped, i) for i in (SECTION_9B, CLASS_10, EMPTY))


def test_FR_KB_002_membership_entries_match_the_named_membership_only() -> None:
    member = Asker(tenant=T, role="teacher", sections=("10A",), member="lab-incharge")
    other = Asker(tenant=T, role="teacher", sections=("10A",), member="sports-incharge")
    assert acl.visible(member, MEMBER)
    assert not acl.visible(other, MEMBER)
    assert not acl.visible(_asker("office_staff"), MEMBER)


def test_SEC_012_c3_documents_need_read_sensitive_and_are_never_retrieved() -> None:
    c3 = _doc(Acl(roles=("principal",), sections=("9A",)), sensitivity="C3")
    assert acl.visible(_asker("principal"), c3)
    assert acl.visible(_asker("class_teacher", sections=("9A",)), c3)  # scoped read_sensitive
    assert not acl.visible(_asker("teacher", sections=("9A",)), c3)  # ACL matches, no C3
    assert not acl.visible(_asker("office_staff"), c3)  # school-wide reader, no C3
    assert not acl.C3_RETRIEVED
    assert not acl.retrievable(_asker("principal"), c3)


def test_superseded_versions_are_visible_but_not_retrievable() -> None:
    old = _doc(Acl(roles=("office_staff",)), latest=False)
    clerk = _asker("office_staff")
    assert acl.visible(clerk, old)
    assert not acl.retrievable(clerk, old)


def test_invariant_3_records_follow_student_read_basic_scopes() -> None:
    s9b = _record("9B")
    readers: tuple[Role, ...] = ("owner", "principal", "office_admin", "office_staff", "accountant")
    for role in readers:
        assert acl.visible(_asker(role), s9b), role
    assert acl.visible(_asker("class_teacher", sections=("9B",)), s9b)
    assert acl.visible(_asker("teacher", classes=("9",)), s9b)
    assert not acl.visible(_asker("class_teacher", sections=("9A",)), s9b)
    assert not acl.visible(_asker("teacher"), s9b)
    assert not acl.visible(_asker("principal"), _record("9B", tenant="t2"))
    assert acl.retrievable(_asker("office_staff"), s9b)


def test_only_roles_with_kb_ask_can_ask() -> None:
    assert acl.can_ask(_asker("teacher"))
    assert not acl.can_ask(_asker("auditor_readonly"))
