"""The M2 wave 5 record tools on the real database (docs/06 §7; ADR-0008; FR-KB-004, SEC-018,
SEC-020; invariants 3, 8, 9, 14).

``get_value_history``, ``count_students``, ``list_findings`` and ``list_documents`` run through
the real ask route with a scripted model that calls ONE tool and cites what it returns. Each
test checks what reached the model (the recorded request bodies): only the caller's scope, no
restricted (C3) value, no actor names without ``audit.read``, no C3 document without
``student.read_sensitive``, and numbers only from the count tool. Synthetic data only.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine

from app.knowledge import composition
from app.knowledge.config.tools import load_tools_config
from app.knowledge.gateway.transport import MessagesRequest
from app.knowledge.tools.counts import CountStudentsTool
from app.knowledge.tools.registry import build_tools

pytestmark = pytest.mark.db


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


TESTS = Path(__file__).resolve().parents[1]
K = _load("sos_test_ask_support", TESTS / "knowledge" / "ask_support.py")
DQ = _load("sos_test_dq_support", TESTS / "dq" / "dq_support.py")
W = K.W
world = W.world
api = W.api


class OneToolTransport:
    """Plays a model that calls ``tool`` once, then cites every result it got back."""

    name = "fake"

    def __init__(self, tool: str, arguments: Mapping[str, Any]) -> None:
        self.tool = tool
        self.arguments = dict(arguments)
        self.sent: list[Any] = []
        self.results: list[Any] = []

    def _reply(self, content: list[dict[str, Any]], stop: str) -> dict[str, Any]:
        return {
            "model": "claude-sonnet-5",
            "content": content,
            "stop_reason": stop,
            "usage": {"input_tokens": 50, "output_tokens": 10},
        }

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        body = request.body
        self.sent.append(body)
        messages = body["messages"]
        if not any(m["role"] == "assistant" for m in messages):
            call = {"type": "tool_use", "id": "toolu_1", "name": self.tool, "input": self.arguments}
            return self._reply([call], "tool_use")
        last = messages[-1]["content"]
        self.results = [
            r for b in last if b.get("type") == "tool_result" for r in b.get("content", [])
        ]
        if not self.results:
            return self._reply([{"type": "text", "text": "Not found."}], "end_turn")
        blocks = []
        for i, r in enumerate(self.results):
            text = r["content"][0]["text"]
            citation = {
                "type": "search_result_location",
                "source": r["source"],
                "title": r["title"],
                "cited_text": text,
                "search_result_index": i,
                "start_block_index": 0,
                "end_block_index": 1,
            }
            blocks.append({"type": "text", "text": text, "citations": [citation]})
        return self._reply(blocks, "end_turn")

    @property
    def error(self) -> bool:
        if len(self.sent) < 2:
            return False
        last = self.sent[-1]["messages"][-1]["content"]
        return any(b.get("is_error") for b in last if b.get("type") == "tool_result")


@pytest.fixture(scope="module")
def ready(world: Any, admin_engine: Engine) -> Iterator[dict[str, uuid.UUID]]:
    K.install_runtime()
    ids = K.SW.ensure_students(world)
    K.enable_ai(admin_engine, world.a.tenant_id)
    composition.set_runtime(None)
    yield ids
    composition.set_runtime(None)


def ask_with(api: Any, who: Any, tool: str, arguments: Mapping[str, Any]) -> tuple[Any, Any]:
    transport = OneToolTransport(tool, arguments)
    K.install_runtime(transport=transport)
    try:
        res, events = K.ask(api, who, "A synthetic records question?")
    finally:
        composition.set_runtime(None)
    assert res.status_code == 200, res.text
    return transport, events


def _sent(transport: OneToolTransport) -> str:
    return str(transport.sent)


# --- offered tools ------------------------------------------------------------------------------


def test_ADR_0008_every_whitelisted_tool_is_built_and_offered_by_permission(
    world: Any, api: Any, ready: dict[str, uuid.UUID]
) -> None:
    built = build_tools(load_tools_config(), composition.build_runtime().search)
    assert sorted(built) == sorted(load_tools_config().tools)
    transport, _ = ask_with(api, world.person("principal"), "count_students", {})
    # get_fee_dues is also behind the school's Tally connector flag (off here; ADR-0032):
    # tests/knowledge/test_fee_tool_db.py offers it with the flag on.
    flagged = {"get_fee_dues"}
    assert sorted(t["name"] for t in transport.sent[0]["tools"]) == sorted(set(built) - flagged)
    transport, _ = ask_with(api, world.person("accountant"), "count_students", {})
    offered = {t["name"] for t in transport.sent[0]["tools"]}
    assert "list_findings" not in offered  # accountant has no dq.findings.read


# --- get_value_history --------------------------------------------------------------------------


def test_FR_KB_004_value_history_reads_one_field_in_scope(
    world: Any, api: Any, ready: dict[str, uuid.UUID]
) -> None:
    sid = ready["s9a"]
    transport, events = ask_with(
        api,
        world.person("class_teacher"),
        "get_value_history",
        {"student_id": str(sid), "field": "dob"},
    )
    sources = [d["source"] for e, d in events if e == "citation"]
    assert sources == [f"sos://student/{sid}/field/dob?src=admission_register"]
    assert "14/03/2012" in transport.results[0]["content"][0]["text"]
    # No audit.read: who recorded it is not named.
    assert "a staff member" in transport.results[0]["content"][0]["text"]
    owner_name = world.a.people["owner"].display_name
    assert owner_name not in _sent(transport)


def test_FR_KB_004_value_history_names_actors_only_with_audit_read(
    world: Any, api: Any, ready: dict[str, uuid.UUID]
) -> None:
    transport, _ = ask_with(
        api,
        world.person("principal"),
        "get_value_history",
        {"student_id": str(ready["s9a"]), "field": "dob"},
    )
    text = transport.results[0]["content"][0]["text"]
    assert world.a.people["owner"].display_name in text


def test_invariant_3_value_history_outside_scope_or_school_is_an_error(
    world: Any, api: Any, ready: dict[str, uuid.UUID]
) -> None:
    for sid in (ready["s9c"], ready["b_sb"]):
        transport, events = ask_with(
            api,
            world.person("class_teacher"),
            "get_value_history",
            {"student_id": str(sid), "field": "dob"},
        )
        assert transport.error
        assert transport.results == []
        assert [e for e, _ in events if e == "citation"] == []


def test_SEC_020_value_history_never_accepts_a_c3_attribute(
    world: Any, api: Any, ready: dict[str, uuid.UUID]
) -> None:
    transport, _ = ask_with(
        api,
        world.person("principal"),
        "get_value_history",
        {"student_id": str(ready["s9a"]), "field": "health_notes"},
    )
    assert transport.error
    assert "asthma" not in _sent(transport)


# --- count_students -----------------------------------------------------------------------------


def _count_text(transport: OneToolTransport) -> str:
    (result,) = transport.results
    assert result["source"].startswith("sos://count/")
    text: str = result["content"][0]["text"]
    return text


def test_FR_KB_004_counts_are_scoped_and_numbers_only(
    world: Any, api: Any, ready: dict[str, uuid.UUID]
) -> None:
    everyone, _ = ask_with(
        api, world.person("principal"), "count_students", {"group_by": "section"}
    )
    mine, events = ask_with(
        api, world.person("class_teacher"), "count_students", {"group_by": "section"}
    )
    school_text, own_text = _count_text(everyone), _count_text(mine)
    assert school_text.count(":") > own_text.count(":")  # more sections for the principal
    assert "Synthetica" not in school_text + own_text  # never names
    assert str(ready["s9a"]) not in school_text + own_text  # never ids
    school_total = int(school_text.split("Total: ")[1].split(".")[0])
    own_total = int(own_text.split("Total: ")[1].split(".")[0])
    assert 0 < own_total < school_total
    assert next(d["source"] for e, d in events if e == "citation").startswith("sos://count/")


def test_invariant_14_gender_breakdown_hides_small_groups() -> None:
    tool = CountStudentsTool(load_tools_config().tools["count_students"])
    assert tool._suppressed({"female": 12, "male": 3}) == {"female", "male"}
    assert tool._suppressed({"female": 2, "male": 3, "transgender": 9}) == {"female", "male"}
    assert tool._suppressed({"female": 12, "male": 30}) == set()


# --- list_findings ------------------------------------------------------------------------------


def test_FR_KB_004_findings_follow_the_callers_scope(
    world: Any, api: Any, ready: dict[str, uuid.UUID]
) -> None:
    in_9a = DQ.high_finding(world.a, "section_9a")
    in_9c = DQ.high_finding(world.a, "section_9c")
    principal, _ = ask_with(api, world.person("principal"), "list_findings", {"severity": ["high"]})
    teacher, _ = ask_with(
        api, world.person("class_teacher"), "list_findings", {"severity": ["high"]}
    )
    school = {r["source"] for r in principal.results}
    own = {r["source"] for r in teacher.results}
    assert {f"sos://finding/{in_9a}", f"sos://finding/{in_9c}"} <= school
    assert f"sos://finding/{in_9a}" in own
    assert f"sos://finding/{in_9c}" not in own
    assert str(in_9c) not in _sent(teacher)


def test_invariant_2_findings_need_dq_findings_read(
    world: Any, api: Any, ready: dict[str, uuid.UUID]
) -> None:
    transport, _ = ask_with(api, world.person("accountant"), "list_findings", {})
    # Not offered, so the model's call to it is refused before anything runs.
    assert "list_findings" not in {t["name"] for t in transport.sent[0]["tools"]}


# --- list_documents -----------------------------------------------------------------------------


def test_SEC_018_document_list_hides_c3_without_read_sensitive_and_never_sends_content(
    world: Any, api: Any, admin_engine: Engine, ready: dict[str, uuid.UUID]
) -> None:
    restricted, _ = K.text_document(
        admin_engine,
        world.a,
        "Synthetic restricted counselling note body.",
        title="Synthetic restricted register",
        acl=K.ALL_ROLES_ACL,
        sensitivity="C3",
        ingest=False,
    )
    shared = K.shared_document(admin_engine, world.a)
    staff, _ = ask_with(api, world.person("office_staff"), "list_documents", {})
    principal, _ = ask_with(api, world.person("principal"), "list_documents", {})
    staff_sources = {r["source"] for r in staff.results}
    principal_sources = {r["source"] for r in principal.results}
    assert f"sos://doc/{shared}/v1#p1" in staff_sources
    assert f"sos://doc/{restricted}/v1#p1" not in staff_sources
    assert "Synthetic restricted register" not in _sent(staff)
    assert f"sos://doc/{restricted}/v1#p1" in principal_sources
    for transport in (staff, principal):
        assert K.SHARED_TEXT not in _sent(transport)  # metadata only, never content
        assert "counselling note body" not in _sent(transport)
