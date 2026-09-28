"""Retrieval filters by tenant and ACL IN SQL before ranking (FR-KB-002, SEC-018, invariant 8).

One synthetic school A with documents restricted in every way the documents service allows, and
a second school B holding the SAME texts and vectors. Every caller must get exactly the chunks
the visibility rule allows (docs/05 §6.1): nothing from B, no other section, no other role, no
superseded version, no C3 file, and an empty ACL only for school-wide readers.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from sqlalchemy import Engine

from app.core.db import tenant_session
from app.knowledge import sources
from app.knowledge.domain import AclKeys, SearchFilters
from app.knowledge.interfaces import Retriever
from app.knowledge.retrieval import HybridRetriever, acl_predicate


def _load() -> ModuleType:
    name = "sos_test_kb_retrieval_support"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("retrieval_support.py")
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


R = _load()
pytestmark = pytest.mark.db

TOPIC = "exam-timings"
QUERY = "zebracircular exam timings"


@dataclass
class School:
    tenant_id: uuid.UUID
    docs: dict[str, object]
    section_9a: uuid.UUID
    section_9b: uuid.UUID
    class_9: uuid.UUID
    member: uuid.UUID

    def ids(self, *names: str) -> set[uuid.UUID]:
        out: set[uuid.UUID] = set()
        for n in names:
            out.update(self.docs[n].latest_chunks)  # type: ignore[attr-defined]
        return out

    @property
    def superseded(self) -> set[uuid.UUID]:
        doc = self.docs["superseded"]
        return set(doc.chunks[doc.versions[0]])  # type: ignore[attr-defined]


def _school(admin: Engine, s9a: uuid.UUID, s9b: uuid.UUID, c9: uuid.UUID, m: uuid.UUID) -> School:
    t = R.make_tenant(admin)
    docs: dict[str, object] = {}

    def doc(name: str, *, sensitivity: str = "C1", **acl: object) -> None:
        d = R.make_document(admin, t, title=f"Synthetic {name}", sensitivity=sensitivity)
        R.add_version(admin, d)
        R.add_chunks(
            admin,
            d,
            [f"zebracircular exam timings {name} marker-{name}"],
            topic=TOPIC,
            header=f"[Circular] Synthetic DEO · zebracircular {name}",
            **acl,
        )
        docs[name] = d

    doc("school_wide")  # empty ACL
    doc("section_9a", acl_sections=[s9a])
    doc("section_9b", acl_sections=[s9b])
    doc("class_9", acl_classes=[c9])
    doc("accountant_only", acl_roles=["accountant"])
    doc("one_member", acl_memberships=[m])
    doc("restricted_c3", sensitivity="C3", acl_roles=["principal", "accountant"])
    old = R.make_document(admin, t, title="Synthetic superseded")
    R.add_version(admin, old)
    R.add_chunks(
        admin, old, ["zebracircular exam timings oldmarker v1"], topic=TOPIC, acl_roles=["teacher"]
    )
    R.add_version(admin, old)
    R.add_chunks(
        admin, old, ["zebracircular exam timings newmarker v2"], topic=TOPIC, acl_roles=["teacher"]
    )
    docs["superseded"] = old
    return School(t, docs, s9a, s9b, c9, m)


@pytest.fixture(scope="module")
def schools(admin_engine: Engine, app_engine: Engine) -> Iterator[tuple[School, School]]:
    s9a, s9b, c9, m = (uuid.uuid4() for _ in range(4))
    a = _school(admin_engine, s9a, s9b, c9, m)
    b = _school(admin_engine, s9a, s9b, c9, m)  # identical text, vectors and ACL keys
    yield a, b
    R.delete_tenant_data(admin_engine, [a.tenant_id, b.tenant_id])


def _search(tenant_id: uuid.UUID, acl: AclKeys, **kw: object) -> list[uuid.UUID]:
    retriever = HybridRetriever()
    with tenant_session(tenant_id) as s:
        return [r.chunk_id for r in retriever.search(s, acl, R.query(QUERY, TOPIC, **kw))]


def test_hybrid_retriever_implements_the_contract() -> None:
    assert isinstance(HybridRetriever(), Retriever)


def test_SEC_018_class_teacher_sees_only_their_sections(schools: tuple[School, School]) -> None:
    a, _ = schools
    # A section scope is widened by the structure: 9A and its class 9 (docs/05 §6.1).
    acl = R.keys(roles={"class_teacher"}, sections={a.section_9a}, classes={a.class_9})
    got = set(_search(a.tenant_id, acl))
    assert got == a.ids("section_9a", "class_9")
    assert not got & a.ids("section_9b", "school_wide", "accountant_only")


def test_SEC_018_role_restricted_documents(schools: tuple[School, School]) -> None:
    a, _ = schools
    assert set(_search(a.tenant_id, R.keys(roles={"accountant"}))) == a.ids("accountant_only")
    assert set(_search(a.tenant_id, R.keys(roles={"exam_coordinator"}))) == set()


def test_SEC_018_membership_entries_match_only_that_membership(
    schools: tuple[School, School],
) -> None:
    a, _ = schools
    assert set(_search(a.tenant_id, R.keys(membership=a.member))) == a.ids("one_member")


def test_SEC_018_empty_acl_is_visible_only_to_school_wide_readers(
    schools: tuple[School, School],
) -> None:
    a, _ = schools
    school_wide = set(_search(a.tenant_id, R.keys(roles={"principal"}, school_wide=True)))
    assert school_wide == a.ids("school_wide", "section_9a", "section_9b", "class_9")
    scoped = set(_search(a.tenant_id, R.keys(roles={"principal"})))
    assert not scoped & a.ids("school_wide")


def test_SEC_018_manage_acl_holders_see_everything_but_c3_and_old_versions(
    schools: tuple[School, School],
) -> None:
    a, _ = schools
    got = set(_search(a.tenant_id, R.keys(sees_all=True)))
    expected = a.ids(
        "school_wide",
        "section_9a",
        "section_9b",
        "class_9",
        "accountant_only",
        "one_member",
        "superseded",
    )
    assert got == expected


def test_SEC_018_c3_documents_are_never_retrieved(schools: tuple[School, School]) -> None:
    a, _ = schools
    for acl in (R.keys(sees_all=True), R.keys(roles={"accountant"}, school_wide=True)):
        assert not set(_search(a.tenant_id, acl)) & a.ids("restricted_c3")


def test_SEC_018_c3_documents_only_for_read_sensitive_holders_their_acl_reaches(
    schools: tuple[School, School],
) -> None:
    """``AclKeys.read_sensitive`` (``student.read_sensitive``) adds C3 documents, and only those
    the caller's ACL keys reach; without the flag the SQL filter returns zero C3 chunks."""
    a, b = schools
    holder = R.keys(roles={"accountant"}, read_sensitive=True)
    assert set(_search(a.tenant_id, holder)) == a.ids("accountant_only", "restricted_c3")
    # The flag never widens the ACL: another role, or a scoped reader, still gets no C3 file.
    for acl in (
        R.keys(roles={"teacher"}, read_sensitive=True),
        R.keys(sections={a.section_9a}, classes={a.class_9}, read_sensitive=True),
    ):
        assert not set(_search(a.tenant_id, acl)) & a.ids("restricted_c3")
    everything = set(_search(a.tenant_id, R.keys(sees_all=True, read_sensitive=True)))
    assert a.ids("restricted_c3") <= everything
    # Non-holders: zero C3 chunks for every combination of keys (fail closed).
    for acl in (
        R.keys(roles={"principal", "accountant"}),
        R.keys(roles={"principal"}, school_wide=True),
        R.keys(sees_all=True),
        R.keys(sees_all=True, school_wide=True, roles={"principal", "accountant"}),
    ):
        assert not set(_search(a.tenant_id, acl)) & a.ids("restricted_c3")
    # And never another school's C3 file, even with every key.
    assert not set(_search(b.tenant_id, R.keys(sees_all=True, read_sensitive=True))) & a.ids(
        "restricted_c3"
    )


def test_SEC_018_the_c3_clause_is_in_the_predicate_unless_read_sensitive() -> None:
    from sqlalchemy.dialects import postgresql

    def sql(acl: AclKeys) -> str:
        compiled = acl_predicate(acl, SearchFilters()).compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
        return str(compiled)

    assert "sensitivity !=" in sql(R.keys(sees_all=True))
    assert "sensitivity !=" in sql(R.keys(roles={"principal"}, school_wide=True))
    assert "sensitivity" not in sql(R.keys(sees_all=True, read_sensitive=True))


def test_FR_KB_002_superseded_versions_are_excluded(schools: tuple[School, School]) -> None:
    a, _ = schools
    retriever = HybridRetriever()
    with tenant_session(a.tenant_id) as s:
        results = retriever.search(
            s, R.keys(roles={"teacher"}), R.query("oldmarker v1", TOPIC, k=50)
        )
    assert {r.chunk_id for r in results} == a.ids("superseded")
    assert not {r.chunk_id for r in results} & a.superseded
    assert [r.version_no for r in results] == [2]
    assert all("oldmarker" not in r.content for r in results)


def test_SEC_018_cross_tenant_leakage_is_zero(schools: tuple[School, School]) -> None:
    a, b = schools
    all_a = {c for d in a.docs.values() for cs in d.chunks.values() for c in cs}  # type: ignore[attr-defined]
    all_b = {c for d in b.docs.values() for cs in d.chunks.values() for c in cs}  # type: ignore[attr-defined]
    for acl in (
        R.keys(sees_all=True),
        R.keys(roles={"principal", "accountant", "teacher"}, school_wide=True),
        R.keys(sections={a.section_9a}, classes={a.class_9}, membership=a.member),
    ):
        from_b = set(_search(b.tenant_id, acl))
        assert from_b, "school B must find its own copies"
        assert from_b <= all_b
        assert not from_b & all_a


def test_FR_KB_002_filters_apply_in_sql(schools: tuple[School, School]) -> None:
    a, _ = schools
    everyone = R.keys(sees_all=True)
    none = _search(a.tenant_id, everyone, filters=SearchFilters(doc_types=frozenset({"policy"})))
    assert none == []
    future = SearchFilters(from_date=dt.date(2999, 1, 1))
    assert _search(a.tenant_id, everyone, filters=future) == []
    assert _search(a.tenant_id, everyone, filters=SearchFilters(doc_types=frozenset())) == []


def test_results_carry_citable_sources(schools: tuple[School, School]) -> None:
    a, _ = schools
    retriever = HybridRetriever()
    with tenant_session(a.tenant_id) as s:
        results = retriever.search(s, R.keys(roles={"accountant"}), R.query(QUERY, TOPIC))
    assert len(results) == 1
    r = results[0]
    doc = a.docs["accountant_only"]
    assert r.document_id == doc.document_id  # type: ignore[attr-defined]
    assert r.title == "Synthetic accountant_only"
    assert r.source == sources.document_page(r.document_id, version_no=1, page=1)
    assert "marker-accountant_only" in r.content


def test_no_tenant_context_returns_nothing(
    schools: tuple[School, School], app_engine: Engine
) -> None:
    from app.core.db import context_free_session

    with context_free_session() as s:
        assert HybridRetriever().search(s, R.keys(sees_all=True), R.query(QUERY, TOPIC)) == []


def test_every_branch_carries_the_acl_predicate() -> None:
    """The predicate is rendered inside each branch's own WHERE clause (docs/06 §6)."""
    from sqlalchemy.dialects import postgresql

    from app.knowledge.config.retrieval import load_retrieval_config
    from app.knowledge.retrieval.hybrid import QueryText, branch_statements

    acl = R.keys(roles={"teacher"}, sections={uuid.uuid4()})
    qt = QueryText("x", tuple(R.topic_vector("x")), "'x'")
    predicate = str(
        acl_predicate(acl, SearchFilters()).compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]
    )
    for name, stmt in branch_statements(load_retrieval_config(), acl, SearchFilters(), qt).items():
        sql = str(stmt.compile(dialect=postgresql.dialect()))  # type: ignore[no-untyped-call]
        inner_where = sql.split("WHERE", 1)[1]
        assert predicate in inner_where, name
        assert "acl_roles &&" in inner_where, name
        assert "is_latest" in inner_where, name


def test_SEC_018_scoped_askers_agree_with_the_eval_oracle(schools: tuple[School, School]) -> None:
    """``evals/sos_evals/acl.py`` judges leakage in ``make eval``; on role/section/class
    entries it and the SQL filter agree (keys as the asker's role grants them: the accountant
    is a school-wide reader). The full parity check, built from real principals over the whole
    eval corpus, is ``tests/knowledge/test_eval_bridge.py``."""
    from sos_evals import acl as oracle
    from sos_evals.schema import Acl, Asker, CorpusItem

    a, _ = schools
    specs: dict[str, Acl] = {
        "school_wide": Acl(),
        "section_9a": Acl(sections=(str(a.section_9a),)),
        "section_9b": Acl(sections=(str(a.section_9b),)),
        "class_9": Acl(classes=(str(a.class_9),)),
        "accountant_only": Acl(roles=("accountant",)),
    }
    items = {
        name: CorpusItem(
            source=sources.document_page(a.docs[name].document_id, version_no=1, page=1),  # type: ignore[attr-defined]
            tenant=str(a.tenant_id),
            kind="document",
            doc_type="circular",
            title=name,
            locale="en",
            acl=acl,
            content=f"synthetic MK-{i:06X}",
            marker=f"MK-{i:06X}",
        )
        for i, (name, acl) in enumerate(specs.items())
    }
    askers = [
        Asker(
            tenant=str(a.tenant_id),
            role="class_teacher",
            sections=(str(a.section_9a),),
            classes=(str(a.class_9),),
        ),
        Asker(tenant=str(a.tenant_id), role="accountant"),
        Asker(tenant=str(a.tenant_id), role="teacher"),
        Asker(tenant=str(a.tenant_id), role="class_teacher", sections=(str(a.section_9b),)),
    ]
    for asker in askers:
        expected = {n for n, item in items.items() if oracle.retrievable(asker, item)}
        acl = R.keys(
            roles={asker.role},
            sections={uuid.UUID(s) for s in asker.sections},
            classes={uuid.UUID(c) for c in asker.classes},
            school_wide=oracle.grant(asker, oracle.DOCUMENT_READ) == "school",
        )
        got_ids = set(_search(a.tenant_id, acl))
        got = {n for n in specs if a.ids(n) <= got_ids}
        assert got == expected, asker
