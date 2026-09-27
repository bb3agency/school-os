"""The eval bridge's stand-in model (tests/knowledge/eval_bridge.py; docs/06 §13 app-fake).

The stand-in must be deterministic and must not "know" the answer key: it judges relevance from
the question and the passages it was given only. Pinned here so its behaviour does not drift
silently between eval runs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from app.knowledge.gateway.transport import MessagesRequest


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
