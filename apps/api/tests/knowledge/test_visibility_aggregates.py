"""Aggregate sources (``count`` / ``fee``) carry the caller's scope (audit 2026-10-04 W3-09).

A ``count_students`` total is computed over the caller's scope at the time. Earlier answers are
shown again and re-sent as context after a re-check (invariant 8), so a school-wide total must
not stay visible after the person's scope narrows to one section: the source key carries a
fingerprint of the tool permission's scope, and the re-check compares it. An old key without a
fingerprint is visible only to a caller whose scope is school-wide (fail closed otherwise).
"""

from __future__ import annotations

import uuid
from typing import Any, cast

import pytest

from app.authz.context import Scopes, UserContext
from app.knowledge import sources
from app.knowledge.visibility import COUNT_TOOL, FEE_TOOL, SourceVisibility

KEY = uuid.UUID("0192a0de-0000-7000-8000-00000000c0c1")
SECTION_A = uuid.UUID("0192a0de-0000-7000-8000-0000000005a1")
SECTION_B = uuid.UUID("0192a0de-0000-7000-8000-0000000005b1")


def ctx(*, school: bool, sections: frozenset[uuid.UUID] = frozenset()) -> UserContext:
    permissions = frozenset({"kb.ask", "student.read_basic", "finance.read"})
    return UserContext(
        user_id=uuid.UUID(int=1),
        tenant_id=uuid.UUID(int=2),
        membership_id=uuid.UUID(int=3),
        roles=frozenset({"class_teacher"}),
        permissions=permissions,
        scopes=Scopes(school=school, section_ids=sections),
        mfa=True,
        auth_time=None,
        scoped_permissions=frozenset() if school else permissions,
    )


def visibility(c: UserContext, tools: tuple[str, ...] = (COUNT_TOOL, FEE_TOOL)) -> SourceVisibility:
    # count/fee checks read no rows: the session is never used.
    return SourceVisibility(cast(Any, None), c, tools_available=lambda: tools)


def count_source(c: UserContext) -> str:
    return sources.student_count(
        KEY, scope=sources.scope_fingerprint(c.scope_for("student.read_basic"))
    )


def test_W3_09_count_source_round_trips_with_its_scope_fingerprint() -> None:
    fp = sources.scope_fingerprint(ctx(school=True).scope_for("student.read_basic"))
    uri = sources.student_count(KEY, scope=fp)
    assert uri == f"sos://count/{KEY}#s{fp}"
    assert sources.parse(uri) == sources.SourceRef(kind="count", object_id=KEY, scope=fp)
    fee = sources.fee_dues(KEY, scope=fp)
    assert sources.parse(fee) == sources.SourceRef(kind="fee", object_id=KEY, scope=fp)


def test_W3_09_fingerprint_is_ids_only_and_order_independent() -> None:
    one = sources.scope_fingerprint(
        ctx(school=False, sections=frozenset({SECTION_A, SECTION_B})).scope_for(
            "student.read_basic"
        )
    )
    two = sources.scope_fingerprint(
        ctx(school=False, sections=frozenset({SECTION_B, SECTION_A})).scope_for(
            "student.read_basic"
        )
    )
    assert one == two
    assert len(one) == 16
    assert all(ch in "0123456789abcdef" for ch in one)
    assert str(SECTION_A) not in one


@pytest.mark.parametrize(
    "uri",
    [
        f"sos://count/{KEY}#sXYZ",
        f"sos://count/{KEY}#s0123",
        f"sos://count/{KEY}#p1",
        f"sos://fee/{KEY}#s0123456789abcdef0",
    ],
)
def test_W3_09_malformed_fingerprints_do_not_parse(uri: str) -> None:
    with pytest.raises(ValueError, match="source URI"):
        sources.parse(uri)


def test_W3_09_school_wide_total_is_not_visible_after_the_scope_narrows() -> None:
    before = ctx(school=True)
    source = count_source(before)
    assert visibility(before).visible(source)
    after = ctx(school=False, sections=frozenset({SECTION_A}))
    assert not visibility(after).visible(source)


def test_W3_09_a_section_count_is_not_visible_after_the_sections_change() -> None:
    before = ctx(school=False, sections=frozenset({SECTION_A}))
    source = count_source(before)
    assert visibility(before).visible(source)
    assert not visibility(ctx(school=False, sections=frozenset({SECTION_B}))).visible(source)
    assert not visibility(ctx(school=True)).visible(source)


def test_W3_09_count_still_needs_the_tool() -> None:
    c = ctx(school=True)
    assert not visibility(c, tools=()).visible(count_source(c))


def test_W3_09_old_keys_without_a_fingerprint_are_visible_only_school_wide() -> None:
    old_count = f"sos://count/{KEY}"
    old_fee = f"sos://fee/{KEY}"
    assert visibility(ctx(school=True)).visible(old_count)
    assert visibility(ctx(school=True)).visible(old_fee)
    narrow = ctx(school=False, sections=frozenset({SECTION_A}))
    assert not visibility(narrow).visible(old_count)
    assert not visibility(narrow).visible(old_fee)


def test_W3_09_fee_source_compares_the_finance_scope() -> None:
    c = ctx(school=True)
    fee = sources.fee_dues(KEY, scope=sources.scope_fingerprint(c.scope_for("finance.read")))
    assert visibility(c).visible(fee)
    assert not visibility(c, tools=(COUNT_TOOL,)).visible(fee)
    other = sources.fee_dues(KEY, scope="0" * 16)
    assert not visibility(c).visible(other)
