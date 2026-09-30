"""The gateway on the Gemini wire (ADR-0033; docs/06 §5, §7, §9, §12; FR-KB-005..009, SEC-020).

Contract tests against synthetic Vertex AI responses in ``gemini_fixtures/`` (hand-written in the
documented REST shape of ``generateContent`` / ``streamGenerateContent``; no network, no real
data): the request the codec builds (system instruction with the passage-marker rules, numbered
passages in ``functionResponse`` parts, ``functionDeclarations``, ``toolConfig`` NONE after the
last round, output cap and thinking level from ``models.yaml``), Aadhaar masking before send with
thought signatures and image bytes left intact, function calls and their signature replay,
``[n]`` markers mapped back to this request's passages (unknown or number-unsupported markers
dropped), finish reasons (safety and blocked prompts are refusals, ``MAX_TOKENS`` truncation,
malformed calls invalid output), usage with cached tokens into metering, structured output,
images for the extraction role only, and streaming (markers never shown, Aadhaar never split).
"""

from __future__ import annotations

import base64
import copy
import json
import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from app.authz.kv import InMemoryKV
from app.core.redaction import verhoeff_check_digit
from app.knowledge.config.llm import load_llm_config
from app.knowledge.config.tools import load_tools_config
from app.knowledge.domain import (
    AssistantMessage,
    ConversationItem,
    Metering,
    ModelTurn,
    SearchResultBlock,
    TextDelta,
    ToolCall,
    ToolOutcome,
    ToolResultsMessage,
    ToolSpec,
    UserMessage,
)
from app.knowledge.gateway.budget import (
    BudgetGuard,
    InMemorySpendLedger,
    StaticAiPolicy,
    TenantAiSettings,
)
from app.knowledge.gateway.codec import ImageInput
from app.knowledge.gateway.errors import GatewayMisuse, InvalidModelOutput
from app.knowledge.gateway.gateway import Gateway
from app.knowledge.gateway.gemini_wire import GeminiStreamAssembler
from app.knowledge.gateway.metering import RecordingSink
from app.knowledge.gateway.transport import MessagesRequest, TransportError

FIXTURES = Path(__file__).with_name("gemini_fixtures")
CONFIG = load_llm_config()
TENANT = uuid.UUID("0192f000-0000-7000-8000-000000000001")
QUERY = uuid.UUID("0192f000-0000-7000-8000-0000000000aa")
DOC = uuid.UUID("0192f000-0000-7000-8000-0000000000d1")
SOURCE_1 = f"sos://doc/{DOC}/v1#p2"
SOURCE_2 = f"sos://doc/{DOC}/v1#p3"
AADHAAR = "23456789012" + verhoeff_check_digit("23456789012")  # synthetic, passes Verhoeff
MASKED = f"XXXX XXXX {AADHAAR[-4:]}"
METERING = Metering(tenant_id=TENANT, feature="ask", query_id=QUERY)
SEARCH = ToolSpec(
    "search_documents",
    "Search the school's documents the user can read.",
    {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    "document.read",
)
BLOCK_1 = SearchResultBlock(
    SOURCE_1,
    "Circular · Exam timings",
    "Quarterly examinations for Class IX. Exams start at 09:30 on 12/10/2026. Bring ID.",
)
BLOCK_2 = SearchResultBlock(SOURCE_2, "Circular · Venue", "The venue is the main hall.")


def fixture(name: str) -> dict[str, Any]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def sse_chunks(name: str) -> list[dict[str, Any]]:
    lines = (FIXTURES / name).read_text(encoding="utf-8").splitlines()
    return [json.loads(line[5:]) for line in lines if line.startswith("data:")]


class ScriptedGemini:
    """Gemini-wire double: returns (or raises) scripted steps; records every request."""

    name = "gemini"
    wire = "gemini"

    def __init__(self, *steps: Mapping[str, Any] | TransportError, chunks: Any = None) -> None:
        self.steps = list(steps)
        self.requests: list[MessagesRequest] = []
        self.chunks = chunks

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        self.requests.append(request)
        step = self.steps.pop(0) if self.steps else fixture("answer_markers.json")
        if isinstance(step, TransportError):
            raise step
        return copy.deepcopy(step)

    @property
    def bodies(self) -> list[Mapping[str, Any]]:
        return [r.body for r in self.requests]


class StreamingGemini(ScriptedGemini):
    def stream(self, request: MessagesRequest) -> Iterator[Mapping[str, Any]]:
        self.requests.append(request)
        yield from copy.deepcopy(self.chunks)


@dataclass
class Rig:
    gateway: Gateway
    transport: ScriptedGemini
    sink: RecordingSink
    ledger: InMemorySpendLedger = field(default_factory=InMemorySpendLedger)


def rig(*steps: Mapping[str, Any] | TransportError, transport: ScriptedGemini | None = None) -> Rig:
    ledger = InMemorySpendLedger()
    guard = BudgetGuard(
        CONFIG,
        StaticAiPolicy(TenantAiSettings(True, Decimal(5000))),
        ledger,
        InMemoryKV(),
    )
    transport = transport or ScriptedGemini(*steps)
    sink = RecordingSink()
    gateway = Gateway(
        config=CONFIG,
        tools_config=load_tools_config(),
        transports={"gemini": transport},
        guard=guard,
        sink=sink,
        enabled=lambda: True,
        sleep=lambda _s: None,
    )
    return Rig(gateway, transport, sink, ledger)


def grounded(*blocks: SearchResultBlock, signature: str | None = "sig-1") -> list[ConversationItem]:
    call = ToolCall("call-7f3a", "search_documents", {"query": "exam"}, signature=signature)
    first = ModelTurn("gemini-3.5-flash", "tool_use", (), (call,), _usage())
    return [
        UserMessage("When do Class 9 exams start?"),
        AssistantMessage(first),
        ToolResultsMessage((ToolOutcome("call-7f3a", blocks or (BLOCK_1, BLOCK_2)),)),
    ]


def _usage() -> Any:
    from app.knowledge.domain import Usage

    return Usage(1, 1)


def ask(r: Rig, conversation: Sequence[ConversationItem] | None = None) -> ModelTurn:
    return r.gateway.run_turn(
        METERING, "answer", "You are the records assistant.", conversation or grounded(), [SEARCH]
    )


# --- request shape (docs/06 §7, §12; models.yaml) ------------------------------------------------


def test_ADR_0033_answer_request_shape() -> None:
    r = rig()
    ask(r)
    (request,) = r.transport.requests
    body = request.body
    assert request.model == "gemini-3.5-flash"
    assert request.location is None  # product traffic: the India region from settings
    assert request.static_prefix == ("systemInstruction", "tools")
    assert request.timeout_s == 60
    system = body["systemInstruction"]["parts"][0]["text"]
    assert system.startswith("You are the records assistant.")
    assert CONFIG.citations.marker_instructions in system
    assert body["generationConfig"] == {
        "maxOutputTokens": 1500,
        "candidateCount": 1,
        "thinkingConfig": {"thinkingLevel": "LOW"},
    }
    (declarations,) = body["tools"]
    assert declarations["functionDeclarations"] == [
        {
            "name": "search_documents",
            "description": SEARCH.description,
            "parametersJsonSchema": dict(SEARCH.input_schema),
        }
    ]
    assert "toolConfig" not in body  # AUTO: never forced (SEC-020)
    user, model, results = body["contents"]
    assert user == {"role": "user", "parts": [{"text": "When do Class 9 exams start?"}]}
    assert model == {
        "role": "model",
        "parts": [
            {
                "functionCall": {
                    "name": "search_documents",
                    "args": {"query": "exam"},
                    "id": "call-7f3a",
                },
                "thoughtSignature": "sig-1",
            }
        ],
    }
    assert results == {
        "role": "user",
        "parts": [
            {
                "functionResponse": {
                    "name": "search_documents",
                    "id": "call-7f3a",
                    "response": {
                        "passages": [
                            {"n": 1, "title": BLOCK_1.title, "text": BLOCK_1.text},
                            {"n": 2, "title": BLOCK_2.title, "text": BLOCK_2.text},
                        ]
                    },
                }
            }
        ],
    }
    assert "sos://" not in json.dumps(body)  # the model gets numbers, never our ids


def test_FR_KB_005_passages_are_numbered_across_tool_rounds() -> None:
    conversation = grounded(BLOCK_1)
    second = ToolCall("call-2", "search_documents", {"query": "venue"}, signature="sig-2")
    conversation += [
        AssistantMessage(ModelTurn("m", "tool_use", (), (second,), _usage())),
        ToolResultsMessage((ToolOutcome("call-2", (BLOCK_2,)),)),
    ]
    r = rig()
    ask(r, conversation)
    body = r.transport.bodies[0]
    passages = [
        p["n"]
        for c in body["contents"]
        for part in c["parts"]
        if "functionResponse" in part
        for p in part["functionResponse"]["response"]["passages"]
    ]
    assert passages == [1, 2]
    signatures = [
        part.get("thoughtSignature")
        for c in body["contents"]
        for part in c["parts"]
        if "functionCall" in part
    ]
    assert signatures == ["sig-1", "sig-2"]


def test_SEC_020_after_three_tool_rounds_function_calling_is_off() -> None:
    conversation: list[ConversationItem] = [UserMessage("q")]
    for n in range(3):
        call = ToolCall(f"c{n}", "search_documents", {"query": "q"}, signature=f"s{n}")
        conversation += [
            AssistantMessage(ModelTurn("m", "tool_use", (), (call,), _usage())),
            ToolResultsMessage((ToolOutcome(f"c{n}", ()),)),
        ]
    r = rig()
    ask(r, conversation)
    body = r.transport.bodies[0]
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "NONE"}}


def test_invariant_9_a_failed_tool_is_reported_as_an_error_not_as_passages() -> None:
    conversation = grounded()
    conversation[-1] = ToolResultsMessage((ToolOutcome("call-7f3a", (), is_error=True),))
    r = rig()
    ask(r, conversation)
    part = r.transport.bodies[0]["contents"][2]["parts"][0]["functionResponse"]
    assert set(part["response"]) == {"error"}


def test_invariant_4_everything_is_masked_but_signatures_survive() -> None:
    block = SearchResultBlock(SOURCE_1, f"Register {AADHAAR}", f"Aadhaar {AADHAAR} on file.")
    conversation = grounded(block, signature=f"sig{AADHAAR}")
    conversation[0] = UserMessage(f"Whose Aadhaar is {AADHAAR}?", earlier_questions=(AADHAAR,))
    r = rig()
    r.gateway.run_turn(METERING, "answer", f"System {AADHAAR}", conversation, [SEARCH])
    raw = json.dumps(r.transport.bodies[0], ensure_ascii=False)
    assert raw.count(AADHAAR) == 1  # only inside the opaque signature, which is not text
    assert r.transport.bodies[0]["contents"][1]["parts"][0]["thoughtSignature"] == f"sig{AADHAAR}"
    assert MASKED in raw


def test_FR_KB_012_earlier_questions_go_first_under_the_configured_header() -> None:
    r = rig()
    ask(r, [UserMessage("And the venue?", earlier_questions=("When do exams start?",))])
    parts = r.transport.bodies[0]["contents"][0]["parts"]
    assert parts[0]["text"].startswith(CONFIG.conversation.earlier_questions_header)
    assert parts[-1] == {"text": "And the venue?"}


# --- responses: tool calls, citations, stops, usage ---------------------------------------------


def test_invariant_9_function_calls_carry_their_signature_and_only_offered_tools() -> None:
    turn = ask(rig(fixture("tool_call.json")), [UserMessage("When do exams start?")])
    assert turn.stop_reason == "tool_use"
    (call,) = turn.tool_calls
    assert (call.call_id, call.name, dict(call.arguments)) == (
        "call-7f3a",
        "search_documents",
        {"query": "exam timings class 9"},
    )
    assert (
        call.signature
        == fixture("tool_call.json")["candidates"][0]["content"]["parts"][0]["thoughtSignature"]
    )
    rogue = fixture("tool_call.json")
    rogue["candidates"][0]["content"]["parts"][0]["functionCall"]["name"] = "update_student"
    r = rig(rogue)
    with pytest.raises(InvalidModelOutput):
        ask(r, [UserMessage("q")])
    assert r.sink.events[0].outcome == "invalid_output"


def test_invariant_9_a_call_without_an_id_gets_a_local_id_never_sent_back() -> None:
    step = fixture("tool_call.json")
    del step["candidates"][0]["content"]["parts"][0]["functionCall"]["id"]
    r = rig(step)
    turn = ask(r, [UserMessage("q")])
    (call,) = turn.tool_calls
    assert call.call_id.startswith("sos-fc-")
    conversation: list[ConversationItem] = [
        UserMessage("q"),
        AssistantMessage(turn),
        ToolResultsMessage((ToolOutcome(call.call_id, (BLOCK_1,)),)),
    ]
    ask(r, conversation)
    body = r.transport.bodies[1]
    assert "id" not in body["contents"][1]["parts"][0]["functionCall"]
    assert "id" not in body["contents"][2]["parts"][0]["functionResponse"]


def test_FR_KB_005_markers_become_validated_citations() -> None:
    r = rig(fixture("answer_markers.json"))
    with capture_logs() as logs:
        turn = ask(r)
    assert turn.stop_reason == "end_turn"
    assert turn.model == "gemini-3.5-flash"
    texts = [s.text for s in turn.segments]
    assert texts == [
        "Exams for Class 9 start at 09:30 on 12/10/2026.",
        " The venue is the main hall.",
        " Bring the hall ticket.",
    ]
    first, second, third = turn.segments
    (c1,) = first.citations
    assert c1.source == SOURCE_1
    # The fewest sentences that write the statement's numbers (09:30 covers the 9), copied.
    assert c1.cited_text == "Exams start at 09:30 on 12/10/2026."
    assert c1.cited_text in BLOCK_1.text  # docs/06 §9 rule 2 holds by construction
    assert [c.source for c in second.citations] == [SOURCE_2]
    assert third.citations == ()  # [7] names no passage of this request: dropped
    assert "Checked the circular" not in "".join(texts)  # thought parts are never shown
    dropped = [e for e in logs if e["event"] == "kb.llm.citation_markers_dropped"]
    assert [e["count"] for e in dropped] == [1]
    assert all("hall ticket" not in str(e) for e in logs)  # invariant 5


def test_FR_KB_007_a_marker_whose_passage_lacks_the_statements_numbers_is_dropped() -> None:
    invented = fixture("answer_markers.json")
    invented["candidates"][0]["content"]["parts"] = [
        {"text": "Exams start at 09:30 on 14/10/2026 [1]. Class 9 sits in the main hall [1][2]."}
    ]
    turn = ask(rig(invented))
    first, second = turn.segments
    assert first.citations == ()  # 14 is in neither the title nor the text of passage 1
    assert {c.source for c in second.citations} == {SOURCE_1, SOURCE_2}  # "IX" counts as 9


def test_FR_KB_007_safety_and_blocked_prompts_are_refusals() -> None:
    for name in ("safety_block.json", "prompt_blocked.json"):
        r = rig(fixture(name))
        turn = ask(r)
        assert turn.stop_reason == "refusal"
        assert turn.segments == ()  # partial text of a blocked answer is never shown
        assert r.sink.events[0].outcome == "refused"


def test_SEC_020_truncation_and_malformed_calls() -> None:
    r = rig(fixture("max_tokens.json"))
    assert ask(r).stop_reason == "max_tokens"
    assert r.sink.events[0].outcome == "max_tokens"
    r = rig(fixture("malformed_call.json"))
    with pytest.raises(InvalidModelOutput):
        ask(r)
    assert r.sink.events[0].outcome == "invalid_output"
    assert r.sink.events[0].input_tokens == 700  # billed tokens are still metered


def test_NFR_CST_001_usage_with_cached_and_thought_tokens_is_metered_at_list_price() -> None:
    r = rig(fixture("answer_markers.json"))
    ask(r)
    (event,) = r.sink.events
    assert (event.provider, event.model) == ("gemini", "gemini-3.5-flash")
    assert (event.input_tokens, event.cache_read_tokens, event.output_tokens) == (1000, 4200, 100)
    # 1000 x 1.65 + 4200 x 1.65 x 0.10 + 100 x 9.90, per million tokens.
    assert event.cost_usd == Decimal("0.003333")


# --- structured output and images ---------------------------------------------------------------


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "doc_type": {"type": "string", "enum": ["circular", "letter"]},
        "deadlines": {"type": "array", "items": {"type": "string"}},
        "issued_on": {"type": ["string", "null"]},
    },
    "required": ["doc_type", "deadlines", "issued_on"],
    "additionalProperties": False,
}


def test_FR_KB_003_structured_output_uses_the_json_schema_and_is_validated() -> None:
    r = rig(fixture("structured.json"))
    out = r.gateway.generate_json(
        Metering(TENANT, "metadata"), "metadata", "Extract metadata.", f"Text {AADHAAR}", SCHEMA
    )
    assert out == {"doc_type": "circular", "deadlines": ["2026-10-15"], "issued_on": None}
    (request,) = r.transport.requests
    assert request.model == "gemini-3.5-flash-lite"
    assert request.static_prefix == ("systemInstruction",)
    generation = request.body["generationConfig"]
    assert generation["responseMimeType"] == "application/json"
    assert generation["responseJsonSchema"] == SCHEMA
    assert generation["maxOutputTokens"] == 500
    assert AADHAAR not in json.dumps(request.body)
    assert "tools" not in request.body  # never a forced tool


def test_FR_KB_003_declined_or_off_schema_structured_output_is_invalid() -> None:
    for step in (fixture("safety_block.json"), fixture("prompt_blocked.json")):
        r = rig(step)
        with pytest.raises(InvalidModelOutput, match="declined"):
            r.gateway.generate_json(Metering(TENANT, "metadata"), "metadata", "s", "t", SCHEMA)
        assert r.sink.events[0].outcome == "invalid_output"
    bad = fixture("structured.json")
    bad["candidates"][0]["content"]["parts"] = [{"text": '{"doc_type": "memo"}'}]
    with pytest.raises(InvalidModelOutput):
        rig(bad).gateway.generate_json(Metering(TENANT, "metadata"), "metadata", "s", "t", SCHEMA)


def test_ADR_0033_images_go_inline_after_masking_and_only_for_the_extraction_role() -> None:
    image = ImageInput("image/png", b"\x89PNG synthetic " + AADHAAR.encode())
    r = rig(fixture("structured.json"))
    r.gateway.generate_json(
        Metering(TENANT, "extraction"), "extraction", "Read rows.", "Page 1", SCHEMA, images=[image]
    )
    parts = r.transport.bodies[0]["contents"][0]["parts"]
    assert parts[0] == {"inlineData": {"mimeType": "image/png", "data": image.base64}}
    assert base64.b64decode(parts[0]["inlineData"]["data"]) == image.data  # bytes untouched
    assert parts[1] == {"text": "Page 1"}
    with pytest.raises(GatewayMisuse, match="does not take images"):
        rig().gateway.generate_json(
            Metering(TENANT, "metadata"), "metadata", "s", "t", SCHEMA, images=[image]
        )


# --- streaming (docs/06 §5.1) -------------------------------------------------------------------


def stream(r: Rig) -> list[Any]:
    return list(r.gateway.stream_turn(METERING, "answer", "system", grounded(), [SEARCH]))


def test_FR_KB_008_streamed_deltas_hide_markers_and_the_turn_equals_run_turn() -> None:
    chunks = sse_chunks("stream_answer.sse")
    r = rig(transport=StreamingGemini(chunks=chunks))
    items = stream(r)
    turn = items[-1]
    assert isinstance(turn, ModelTurn)
    shown = "".join(i.text for i in items if isinstance(i, TextDelta))
    assert "[" not in shown
    assert "]" not in shown
    assert shown == "Exams start at 09:30 on 12/10/2026. The venue is the main hall."
    whole = fixture("answer_markers.json")
    whole["candidates"][0]["content"]["parts"] = [
        {"text": "Exams start at 09:30 on 12/10/2026 [1]. The venue is the main hall [2]."}
    ]
    whole["usageMetadata"] = chunks[-1]["usageMetadata"]
    assert turn == ask(rig(whole))
    (event,) = r.sink.events
    assert (event.outcome, event.input_tokens, event.output_tokens) == ("ok", 900, 50)


def test_invariant_4_an_aadhaar_number_split_across_chunks_is_never_shown() -> None:
    spaced = f"{AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}"
    pieces = [f"Number {spaced[:3]}", spaced[3:9], f"{spaced[9:]} is on file [1]."]
    chunks: list[dict[str, Any]] = [
        {"candidates": [{"content": {"role": "model", "parts": [{"text": p}]}}]} for p in pieces
    ]
    chunks[-1]["candidates"][0]["finishReason"] = "STOP"
    items = stream(rig(transport=StreamingGemini(chunks=chunks)))
    for item in items[:-1]:
        assert AADHAAR[4:8] not in item.text  # no delta carries a middle group unmasked
    shown = "".join(i.text for i in items if isinstance(i, TextDelta))
    assert AADHAAR not in shown.replace(" ", "")
    assert MASKED in shown


def test_NFR_AVL_004_an_error_mid_stream_is_unavailable_and_metered() -> None:
    class Failing(StreamingGemini):
        def stream(self, request: MessagesRequest) -> Iterator[Mapping[str, Any]]:
            self.requests.append(request)
            yield sse_chunks("stream_error.sse")[0]
            raise TransportError("overloaded", status=503)

    from app.knowledge.gateway.errors import ProviderUnavailable

    r = rig(transport=Failing())
    with pytest.raises(ProviderUnavailable):
        stream(r)
    assert [e.outcome for e in r.sink.events] == ["unavailable"]


def test_the_stream_assembler_rebuilds_function_calls_with_signatures() -> None:
    call = fixture("tool_call.json")
    part = call["candidates"][0]["content"]["parts"][0]
    assembler = GeminiStreamAssembler()
    assembler.feed({"candidates": [{"content": {"role": "model", "parts": [part]}}]})
    assembler.feed(
        {"candidates": [{"finishReason": "STOP"}], "usageMetadata": call["usageMetadata"]}
    )
    assert assembler.complete
    rebuilt = assembler.response()
    assert rebuilt["candidates"][0]["content"]["parts"] == [part]
    assert rebuilt["usageMetadata"] == call["usageMetadata"]
