"""Streaming through the LLM gateway (docs/06 §5.1; FR-KB-008, FR-KB-009, FR-KB-011).

``Gateway.stream_turn`` runs the same controls as ``run_turn`` and yields Aadhaar-masked text
deltas, then the complete turn. Offline: scripted streaming transports replay Messages API
stream events built from a response (the fake provider's event shape). Covers: deltas add up to
the turn and the turn equals ``run_turn``'s (one metering event); an Aadhaar number split over
deltas never appears unmasked (invariant 4); a failure before the first event is retried, a
failure mid-stream is not (search-only fallback upstream, NFR-AVL-004); closing the stream early
closes the provider call and meters it ``cancelled``; budget and switches refuse before any
call; a transport without streaming still works; tool input is assembled from JSON pieces; the
earlier questions of a session go before the question (FR-KB-012).
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.core.redaction import verhoeff_check_digit
from app.knowledge.config.llm import load_llm_config
from app.knowledge.domain import Metering, ModelTurn, TextDelta, UserMessage
from app.knowledge.gateway import wire
from app.knowledge.gateway.errors import BudgetExhausted, ProviderUnavailable
from app.knowledge.gateway.fake import FakeTransport, _stream_events, text_pieces
from app.knowledge.gateway.streaming import AadhaarStreamMasker
from app.knowledge.gateway.transport import MessagesRequest, TransportError
from app.knowledge.interfaces import StreamingLlmGateway


def _load() -> ModuleType:
    name = "sos_test_gateway_module"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            name, Path(__file__).with_name("test_gateway.py")
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


G = _load()
TENANT = G.TENANT
METERING = Metering(tenant_id=TENANT, feature="ask", query_id=G.QUERY)
ANTHROPIC = load_llm_config().use_fallback()
"""Messages API wire: every role on its Anthropic fallback (ADR-0033)."""


class StreamingScripted(G.ScriptedTransport):  # type: ignore[misc,name-defined]
    """``ScriptedTransport`` that also streams; ``fail_after`` events raises mid-stream."""

    def __init__(self, *steps: Any, fail_after: int | None = None) -> None:
        super().__init__(*steps)
        self.fail_after = fail_after
        self.closed = 0
        self.streams = 0

    def stream(self, request: MessagesRequest) -> Iterator[Mapping[str, Any]]:
        self.streams += 1
        response = self.send(request)  # raises a scripted TransportError before any event
        return self._events(response)

    def _events(self, response: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
        try:
            for n, event in enumerate(_stream_events(response)):
                if self.fail_after is not None and n == self.fail_after:
                    raise TransportError("overloaded", status=529)
                yield event
        finally:
            self.closed += 1


def streaming_rig(*steps: Any, fail_after: int | None = None, **kw: Any) -> Any:
    return G.rig(transport=StreamingScripted(*steps, fail_after=fail_after), **kw)


def stream(r: Any, conversation: Any = None) -> list[Any]:
    return list(
        r.gateway.stream_turn(
            METERING, "answer", "system", conversation or [UserMessage(G.QUESTION)], [G.SEARCH_TOOL]
        )
    )


def deltas(items: list[Any]) -> str:
    return "".join(i.text for i in items if isinstance(i, TextDelta))


def test_FR_KB_008_gateway_implements_the_streaming_contract() -> None:
    assert isinstance(G.rig().gateway, StreamingLlmGateway)


def test_FR_KB_008_deltas_add_up_to_the_turn_which_equals_run_turn() -> None:
    long_text = "Exams start at 09:30 on 12/10/2026 in the main hall for every class."
    items = stream(streaming_rig(G.text_response(long_text)))
    assert len([i for i in items if isinstance(i, TextDelta)]) > 3
    assert isinstance(items[-1], ModelTurn)
    assert all(isinstance(i, TextDelta) for i in items[:-1])
    assert deltas(items) == long_text
    unstreamed = G.ask(G.rig(G.text_response(long_text)))
    assert items[-1] == unstreamed


def test_FR_KB_009_a_streamed_call_is_metered_once_with_its_tokens() -> None:
    r = streaming_rig(G.text_response(usage={"input_tokens": 1000, "output_tokens": 200}))
    stream(r)
    assert [(e.outcome, e.input_tokens, e.output_tokens) for e in r.sink.events] == [
        ("ok", 1000, 200)
    ]


def test_invariant_4_an_aadhaar_number_split_over_deltas_is_never_shown() -> None:
    items = stream(streaming_rig(G.text_response(f"On file: {G.AADHAAR_SPACED} for the pupil.")))
    shown = deltas(items)
    assert G.AADHAAR not in shown.replace(" ", "")
    assert G.MASKED in shown
    for item in items[:-1]:
        assert G.AADHAAR[:4] not in item.text or "XXXX" in item.text


def test_invariant_4_keyword_context_before_a_held_number_still_masks_it() -> None:
    # 12 digits that fail Verhoeff are masked only next to the word Aadhaar.
    digits = "234567890120" if verhoeff_check_digit("23456789012") != "0" else "234567890121"
    masker = AadhaarStreamMasker()
    shown = "".join(masker.feed(p) for p in ("Aadhaar ", "no. ", digits[:6], digits[6:], " ok"))
    shown += masker.flush()
    assert digits not in shown
    assert shown.endswith(f"XXXX XXXX {digits[-4:]} ok")


def test_NFR_AVL_004_a_failure_before_the_first_event_is_retried() -> None:
    r = streaming_rig(TransportError("rate_limited", status=429), G.text_response())
    items = stream(r)
    assert isinstance(items[-1], ModelTurn)
    assert r.transport.streams == 2
    assert len(r.clock.sleeps) == 1


def test_NFR_AVL_004_a_failure_mid_stream_raises_unavailable_and_meters_it() -> None:
    r = streaming_rig(G.text_response("One two three four five six seven."), fail_after=3)
    got: list[Any] = []
    it = r.gateway.stream_turn(
        METERING, "answer", "system", [UserMessage(G.QUESTION)], [G.SEARCH_TOOL]
    )
    with pytest.raises(ProviderUnavailable):
        got.extend(it)
    assert got
    assert all(isinstance(i, TextDelta) for i in got)
    assert r.transport.streams == 1  # never retried once events flowed
    assert r.transport.closed == 1
    assert [e.outcome for e in r.sink.events] == ["unavailable"]
    assert r.sink.events[0].input_tokens == 1000  # message_start usage is still billed


def test_FR_KB_009_closing_the_stream_early_cancels_and_meters_it() -> None:
    r = streaming_rig(G.text_response("One two three four five six seven eight nine ten."))
    it = r.gateway.stream_turn(
        METERING, "answer", "system", [UserMessage(G.QUESTION)], [G.SEARCH_TOOL]
    )
    assert isinstance(next(it), TextDelta)
    it.close()
    assert r.transport.closed == 1
    assert [e.outcome for e in r.sink.events] == ["cancelled"]


def test_FR_KB_011_closing_the_stream_on_its_last_delta_still_records_the_spend() -> None:
    # The text ends in digits, so the masker holds them until the stream ends: the last delta
    # comes after the provider call has finished. A client that leaves right then must not
    # make a fully billed call disappear from the month's spend.
    text = "The synthetic picnic buses leave from gate 12"
    r = streaming_rig(G.text_response(text, usage={"input_tokens": 1000, "output_tokens": 200}))
    it = r.gateway.stream_turn(
        METERING, "answer", "system", [UserMessage(G.QUESTION)], [G.SEARCH_TOOL]
    )
    shown = ""
    while shown != text:
        item = next(it)
        assert isinstance(item, TextDelta)
        shown += item.text
    it.close()  # the client went away before the complete turn was sent
    assert [(e.outcome, e.input_tokens, e.output_tokens) for e in r.sink.events] == [
        ("ok", 1000, 200)
    ]
    assert r.ledger.spent_usd(TENANT, "2026-09") == r.sink.events[0].cost_usd > 0


def test_FR_KB_011_budget_refuses_before_any_provider_call() -> None:
    r = streaming_rig(budget_inr=0)
    with pytest.raises(BudgetExhausted):
        stream(r)
    assert r.transport.streams == 0


def test_a_transport_without_streaming_gives_one_delta_then_the_turn() -> None:
    items = stream(G.rig(G.text_response("Exams start at 09:30.")))
    assert [type(i) for i in items] == [TextDelta, ModelTurn]
    assert items[0].text == "Exams start at 09:30."


def test_invariant_9_streamed_tool_input_is_assembled_and_checked() -> None:
    call = {
        "model": "claude-sonnet-5",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "search_documents",
                "input": {"query": "x y"},
            }
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    items = stream(streaming_rig(call))
    turn = items[-1]
    assert deltas(items) == ""
    assert [(c.name, dict(c.arguments)) for c in turn.tool_calls] == [
        ("search_documents", {"query": "x y"})
    ]


def test_the_fake_provider_streams_the_same_answer_it_sends() -> None:
    fake = FakeTransport()
    body = wire.turn_request(
        ANTHROPIC,
        ANTHROPIC.roles["answer"],
        "system",
        [UserMessage("When is sports day?")],
        [],
    )
    request = MessagesRequest(body=body, timeout_s=60)
    assembler = wire.StreamAssembler()
    text = "".join(t for e in fake.stream(request) if (t := assembler.feed(e)))
    sent = fake.send(request)
    assert assembler.complete
    assert text == sent["content"][0]["text"]
    assert text_pieces("a b c d e") == ["a b c ", "d e"]


def test_FR_KB_012_earlier_questions_go_before_the_question() -> None:
    config = ANTHROPIC
    body = wire.turn_request(
        config,
        config.roles["answer"],
        "system",
        [UserMessage("And what time?", earlier_questions=("When is sports day?",))],
        [],
    )
    blocks = body["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["text", "text"]
    assert blocks[0]["text"].startswith(config.conversation.earlier_questions_header)
    assert "- When is sports day?" in blocks[0]["text"]
    assert blocks[-1]["text"] == "And what time?"


def test_invariant_4_earlier_questions_are_masked_too() -> None:
    config = ANTHROPIC
    body = wire.turn_request(
        config,
        config.roles["answer"],
        "system",
        [UserMessage("Next?", earlier_questions=(f"Is {G.AADHAAR} on file?",))],
        [],
    )
    assert G.AADHAAR not in str(body)
