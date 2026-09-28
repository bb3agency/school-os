"""The eval bridge (tests/knowledge/eval_bridge.py; docs/06 §13 app-fake).

- The stand-in model must be deterministic and must not "know" the answer key: it judges
  relevance from the question and the passages it was given only, and looks records up through
  the real record tools. Pinned here so its behaviour does not drift silently between eval runs.
- The harness's visibility oracle (``evals/sos_evals/acl.py``) must model the application
  exactly, because leakage is judged by it: its role matrix against ``roles.yaml``, and its
  ``visible``/``retrievable`` against the documents and students services and the SQL retrieval
  filter, for every role and scope over the whole synthetic corpus (FR-KB-002, SEC-018).
- Items that expect a refusal must not be answerable by the stand-in from anything the asker may
  retrieve, or the refusal hard gate would measure the stand-in instead of the application.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine

from app.authz.catalog import system_roles
from app.knowledge import composition
from app.knowledge.gateway.transport import MessagesRequest
from sos_evals import acl as oracle
from sos_evals import datasets
from sos_evals.schema import Asker


def _load() -> ModuleType:
    name = "sos_test_eval_bridge"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("eval_bridge.py")
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


B = _load()
SPORTS = "Circular: sports day is on 28/11/2026 on the school ground; events start at 08:00."


def _body(question: str, results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": question}]}
    ]
    if results is not None:
        messages += [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "search_documents", "input": {}}
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "t1", "content": results}],
            },
        ]
    return {
        "model": "claude-sonnet-5",
        "messages": messages,
        "tools": [{"name": "search_documents"}],
    }


def _result(
    text: str, source: str = "sos://doc/x/v1#p1", title: str = "Sports day"
) -> dict[str, Any]:
    return {
        "type": "search_result",
        "source": source,
        "title": title,
        "content": [{"type": "text", "text": text}],
    }


def test_identifiers_and_dates_are_whole_tokens() -> None:
    assert B.tokens("When is admission no. SV-2019-0457 due on 12/10/2026?") == {
        "admission",
        "no",
        "sv-2019-0457",
        "due",
        "12/10/2026",
    }


def test_relevance_needs_every_identifier_and_half_the_words() -> None:
    assert B.relevant("When is sports day?", SPORTS)
    assert not B.relevant(
        "When is the class 12 farewell?", "Dasara holidays 02/10/2026 to 12/10/2026 for class 6"
    )
    assert not B.relevant(
        "When does the 9A remedial class run?", "The 9B field trip is on 03/10/2026"
    )


def test_first_turn_searches_then_answers_citing_relevant_passages_only() -> None:
    t = B.EvalFakeTransport()
    first = t.send(MessagesRequest(_body("When is sports day?"), 1))
    assert first["content"][0]["name"] == "search_documents"
    reply = t.send(
        MessagesRequest(
            _body(
                "When is sports day?",
                [
                    _result(SPORTS),
                    _result("Library hours are 08:30.", "sos://doc/y/v1#p1", "Library"),
                ],
            ),
            1,
        )
    )
    cited = [c["source"] for block in reply["content"] for c in block.get("citations", [])]
    assert cited == ["sos://doc/x/v1#p1"]


def test_requests_for_aadhaar_numbers_are_refused_without_searching() -> None:
    reply = B.EvalFakeTransport().send(
        MessagesRequest(_body("Give me the full Aadhaar number of SV-2019-0457"), 1)
    )
    assert reply["content"][0]["type"] == "text"
    assert "citations" not in reply["content"][0]


# --- record lookups ------------------------------------------------------------------------------

STUDENT = "0190c0de-0000-7000-8000-00000000abcd"


def _find_block(admission_no: str, student_id: str = STUDENT) -> dict[str, Any]:
    text = (
        f"Student ID: {student_id}. Name: Synthetica Pupil. Admission no.: {admission_no}. "
        "Class/section: IX-A. Status: active. As of 28/09/2026."
    )
    return _result(text, f"sos://student/{student_id}/field/admission_no?src=record", "Student")


def test_record_questions_name_a_student_and_a_field_in_english_telugu_or_latin_telugu() -> None:
    assert B.record_request("What is the date of birth of admission no. SV-2019-0457?") == (
        "SV-2019-0457",
        "dob",
    )
    assert B.record_request("అడ్మిషన్ నంబర్ SV-2016-0120 తండ్రి పేరు ఏమిటి?") == (
        "SV-2016-0120",
        "father_name",
    )
    assert B.record_request("SV-2020-0688 talli peru cheppandi") == ("SV-2020-0688", "mother_name")
    assert B.record_request("What is the date of admission of admission no. SV-2016-0147?") == (
        "SV-2016-0147",
        "admission_date",
    )
    assert B.record_request("When was admission no. SV-2019-0457 admitted?") is None
    assert B.record_request("తల్లిదండ్రుల సమావేశం ఎప్పుడు?") is None


def test_the_matching_student_is_the_one_with_that_exact_admission_number() -> None:
    other = "0190c0de-0000-7000-8000-00000000ffff"
    blocks = [_find_block("SV-2017-0318", other), _find_block("SV-2017-0311")]
    assert B.student_id_for("SV-2017-0311", blocks) == STUDENT
    assert B.student_id_for("SV-2017-0312", blocks) is None


def _turn(name: str, content: list[dict[str, Any]], call_id: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": call_id, "name": name, "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": call_id, "content": content}],
        },
    ]


def test_record_questions_find_the_student_then_read_one_field_and_cite_it() -> None:
    question = "అడ్మిషన్ నంబర్ SV-2019-0457 పుట్టిన తేదీ ఏమిటి?"
    tools = [{"name": "find_students"}, {"name": "get_student_facts"}, {"name": "search_documents"}]
    first: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": question}]}
    ]
    t = B.EvalFakeTransport()

    def send(messages: list[dict[str, Any]]) -> Any:
        return t.send(MessagesRequest({"model": "m", "messages": messages, "tools": tools}, 1))

    call = send(first)["content"][0]
    assert (call["name"], call["input"]) == ("find_students", {"query": "SV-2019-0457"})
    found = first + _turn("find_students", [_find_block("SV-2019-0457")], "t1")
    call = send(found)["content"][0]
    assert (call["name"], call["input"]) == (
        "get_student_facts",
        {"student_id": STUDENT, "fields": ["dob"]},
    )
    fact = _result(
        "Date of birth: 14/03/2012. Source: admission register, verified. As of 28/09/2026.",
        f"sos://student/{STUDENT}/field/dob?src=admission_register",
        "Student record",
    )
    reply = send(found + _turn("get_student_facts", [fact], "t2"))["content"]
    assert reply[0]["text"] == "సమాధానం: Date of birth: 14/03/2012."
    assert reply[0]["citations"][0]["cited_text"] == "Date of birth: 14/03/2012."
    nobody = send(first + _turn("find_students", [], "t1"))["content"]
    assert nobody == [{"type": "text", "text": "ఇది కనబడలేదు."}]


# --- oracle parity (pure) ----------------------------------------------------------------------

DATA = datasets.load()


@pytest.mark.parametrize("role", sorted(system_roles()))
def test_FR_KB_002_oracle_role_matrix_is_roles_yaml(role: str) -> None:
    assert role in oracle.ROLE_GRANTS
    template = system_roles()[role]
    for permission in oracle.PERMISSIONS:
        grant = template.grant(permission)
        mode = None if grant is None else "scoped" if grant.scoped else "school"
        assert oracle.ROLE_GRANTS[role].get(permission) == mode, (role, permission)  # type: ignore[index]


def test_FR_KB_002_oracle_knows_every_system_role() -> None:
    assert set(oracle.ROLE_GRANTS) == set(system_roles())


def test_FR_KB_007_refusal_items_are_not_answerable_by_the_stand_in() -> None:
    """Nothing the asker may retrieve is "relevant" to a refusal item for the stand-in (so the
    refusal gate measures the application's filter, not word overlap)."""
    for item in DATA.items:
        if not item.expect_refusal or B.record_request(item.question):
            continue
        if any(w in item.question.casefold() for w in B.AADHAAR_WORDS):
            continue
        hits = [
            c.source
            for c in DATA.corpus.values()
            if c.kind == "document"
            and oracle.retrievable(item.asker, c)
            and B.relevant(item.question, f"{c.title} {c.content}")
        ]
        assert hits == [], item.id


# --- oracle parity (database) --------------------------------------------------------------------


def _grid() -> set[Asker]:
    """Every role x (no scope, a section, a class, two sections) x both schools, the dataset's
    askers, and every named member."""
    scopes: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
        ((), ()),
        (("9A",), ()),
        ((), ("10",)),
        (("9B", "10A"), ()),
    )
    askers = {item.asker for item in DATA.items}
    tenants = sorted({c.tenant for c in DATA.corpus.values()})
    for tenant, role, (sections, classes) in itertools.product(
        tenants, sorted(oracle.ROLE_GRANTS), scopes
    ):
        askers.add(Asker(tenant=tenant, role=role, sections=sections, classes=classes))
    members = {(c.tenant, m) for c in DATA.corpus.values() for m in c.acl.members}
    for tenant, member in members:
        askers.add(Asker(tenant=tenant, role="teacher", sections=("9A",), member=member))
        askers.add(Asker(tenant=tenant, role="office_staff", member=member))
    return askers


@pytest.fixture(scope="module")
def bridge(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Iterator[Any]:
    adapter = B.AppFakeAdapter(
        DATA.corpus,
        DATA.items,
        engines=B.Engines(admin=admin_engine, app=app_engine, platform=platform_engine),
        check_parity=False,
    )
    yield adapter
    composition.set_runtime(None)


@pytest.mark.db
def test_FR_KB_002_SEC_018_oracle_and_application_agree_for_every_asker(bridge: Any) -> None:
    """For every role and scope, over the whole corpus: what the documents/students services
    let the asker see and what the SQL retrieval filter returns equal ``sos_evals.acl``."""
    askers = _grid()
    assert len(askers) > 80
    assert bridge.parity_mismatches(sorted(askers, key=repr)) == []


@pytest.mark.db
def test_parity_check_catches_a_wrong_oracle(bridge: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard against a vacuous parity check: an oracle that forgets the school-wide reader rule
    must disagree with the application."""
    monkeypatch.setitem(
        oracle.ROLE_GRANTS,
        "office_staff",
        {**oracle.ROLE_GRANTS["office_staff"], oracle.DOCUMENT_READ: "scoped"},
    )
    mismatches = bridge.parity_mismatches([Asker(tenant="tenant-a", role="office_staff")])
    assert mismatches
    assert all("office_staff" in m for m in mismatches)


@pytest.mark.db
def test_record_sources_map_to_corpus_ids(bridge: Any) -> None:
    unknown = f"sos://student/{uuid.uuid4()}/field/dob?src=admission_register"
    assert bridge.corpus_source(unknown) == unknown
    assert bridge.corpus_source("sos://doc/x/v1#p1") == "sos://doc/x/v1#p1"
    real, corpus_id = next(iter(bridge._students.items()))
    assert bridge.corpus_source(f"sos://student/{real}/field/dob?src=admission_register") == (
        f"sos://student/{corpus_id}/field/dob?src=admission_register"
    )
