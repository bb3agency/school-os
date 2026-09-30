"""Gateway transports, provider-mode guards and the SDK boundary (ADR-0005; CLAUDE.md §11; K4).

- ``fake`` is offline and deterministic, gives grounded answers with ``search_result`` citations
  built from the sources it was given (docs/06 §7, §9), and is refused in staging/prod.
- ``live`` uses the Anthropic SDK with the organization key from settings only (invariant 10);
  exercised here through an in-process ``httpx.MockTransport``: no network, no real key.
- ``anthropic`` is imported only by ``knowledge/gateway/anthropic_transport.py`` (an in-suite
  mirror of semgrep ``sos-llm-sdk-outside-gateway`` over apps/, scripts/ and evals/).
"""

from __future__ import annotations

import ast
import json
import logging
import re
import uuid
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from pydantic import SecretStr

from app.authz.kv import InMemoryKV
from app.core.config import Environment, KnowledgeProviderMode, Settings
from app.knowledge.config.llm import load_llm_config
from app.knowledge.domain import (
    AssistantMessage,
    ConversationItem,
    Metering,
    ModelTurn,
    ToolCall,
    ToolOutcome,
    ToolResultsMessage,
    ToolSpec,
    Usage,
    UserMessage,
)
from app.knowledge.domain import SearchResultBlock as Block
from app.knowledge.gateway.anthropic_transport import AnthropicTransport
from app.knowledge.gateway.budget import InMemorySpendLedger, StaticAiPolicy
from app.knowledge.gateway.errors import AiDisabled
from app.knowledge.gateway.factory import ProviderModeError, build_gateway, build_transport
from app.knowledge.gateway.fake import NOT_FOUND_EN, NOT_FOUND_TE, FakeTransport
from app.knowledge.gateway.fake_gemini import GeminiWireFake
from app.knowledge.gateway.gateway import Gateway
from app.knowledge.gateway.metering import RecordingSink
from app.knowledge.gateway.transport import MessagesRequest, Transport, TransportError

REPO = Path(__file__).resolve().parents[4]
GATEWAY = REPO / "apps" / "api" / "app" / "knowledge" / "gateway"
TENANT = uuid.UUID("0192f000-0000-7000-8000-000000000001")
DOC = uuid.UUID("0192f000-0000-7000-8000-0000000000d1")
# Clearly not a key: the mock transport checks it arrives as x-api-key and nothing else.
TEST_KEY = "sk-ant-test-synthetic-not-a-real-key"
SEARCH = ToolSpec(
    "search_documents",
    "Search documents",
    {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    "document.read",
)


ANTHROPIC = load_llm_config().use_fallback()
"""Every role on its evaluated Anthropic fallback (ADR-0033): the Messages-API transports here."""


def gateway_with(transport: Transport, **settings: Any) -> Gateway:
    return build_gateway(
        Settings(env=Environment.LOCAL, kb_enabled=True, **settings),
        policy=StaticAiPolicy(),
        sink=RecordingSink(),
        transport=transport,
        ledger=InMemorySpendLedger(),
        counters=InMemoryKV(),
        config=ANTHROPIC,
    )


# --- fake provider --------------------------------------------------------------------------------


def blocks() -> tuple[Block, ...]:
    return (
        Block(
            f"sos://doc/{DOC}/v2#p1", "Circular · Exam timings", "Exams start at 09:30. Bring ID."
        ),
        Block(f"sos://doc/{DOC}/v2#p2", "Circular · Venue", "The venue is the main hall."),
    )


def run_fake_ask(question: str, results: tuple[Block, ...]) -> tuple[Any, Any]:
    gw = gateway_with(FakeTransport())
    metering = Metering(TENANT, "ask")
    first = gw.run_turn(metering, "answer", "system", [UserMessage(question)], [SEARCH])
    (call,) = first.tool_calls
    conversation: list[ConversationItem] = [
        UserMessage(question),
        AssistantMessage(first),
        ToolResultsMessage((ToolOutcome(call.call_id, results),)),
    ]
    return first, gw.run_turn(metering, "answer", "system", conversation, [SEARCH])


def test_FR_KB_005_fake_searches_then_answers_with_citations_from_the_given_sources() -> None:
    first, answer = run_fake_ask("When do exams start?", blocks())
    assert first.stop_reason == "tool_use"
    assert first.tool_calls[0].name == "search_documents"
    assert first.tool_calls[0].arguments == {"query": "When do exams start?"}
    assert answer.stop_reason == "end_turn"
    assert len(answer.segments) == 2
    for segment, block in zip(answer.segments, blocks(), strict=True):
        (citation,) = segment.citations
        assert citation.source == block.source
        assert citation.cited_text in block.text  # docs/06 §9 rule 2 holds
    assert answer.usage.input_tokens > 0
    assert answer.usage.output_tokens > 0


def test_FR_KB_005_fake_is_deterministic() -> None:
    assert run_fake_ask("When do exams start?", blocks()) == run_fake_ask(
        "When do exams start?", blocks()
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [("When do exams start?", NOT_FOUND_EN), ("పరీక్షలు ఎప్పుడు?", NOT_FOUND_TE)],
)
def test_FR_KB_006_fake_says_not_found_in_the_question_script(question: str, expected: str) -> None:
    _, answer = run_fake_ask(question, ())
    assert [s.text for s in answer.segments] == [expected]
    assert answer.segments[0].citations == ()


def test_FR_KB_003_fake_structured_output_satisfies_the_schema() -> None:
    schema = {
        "type": "object",
        "properties": {
            "doc_type": {"type": "string", "enum": ["circular", "letter"]},
            "deadlines": {"type": "array", "items": {"type": "string"}},
            "issued_on": {"type": ["string", "null"]},
        },
        "required": ["doc_type", "deadlines", "issued_on"],
        "additionalProperties": False,
    }
    out = gateway_with(FakeTransport()).generate_json(
        Metering(TENANT, "metadata"), "metadata", "s", "t", schema
    )
    assert out == {"doc_type": "circular", "deadlines": [], "issued_on": None}


# --- provider mode guards (SEC-020, invariant 10) ----------------------------------------------


def test_SEC_020_local_default_is_the_offline_fake() -> None:
    transport = build_transport(Settings(env=Environment.LOCAL), load_llm_config())
    assert isinstance(transport, GeminiWireFake)  # the Gemini wire, offline (ADR-0033)
    assert isinstance(transport.inner, FakeTransport)
    fallback = build_transport(Settings(env=Environment.LOCAL), ANTHROPIC)
    assert isinstance(fallback, FakeTransport)


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_SEC_020_fake_is_refused_in_staging_and_prod(env: Environment) -> None:
    # model_construct bypasses the Settings guard, as a mis-built object would.
    settings = Settings.model_construct(env=env, kb_provider_mode=KnowledgeProviderMode.FAKE)
    with pytest.raises(ProviderModeError, match="fake"):
        build_transport(settings, load_llm_config())


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr("   ")])
def test_invariant_10_live_needs_the_organization_key_from_settings(key: SecretStr | None) -> None:
    settings = Settings(
        env=Environment.LOCAL, kb_provider_mode=KnowledgeProviderMode.LIVE, anthropic_api_key=key
    )
    with pytest.raises(ProviderModeError, match="SOS_ANTHROPIC_API_KEY"):
        build_transport(settings, ANTHROPIC)  # a role on the Anthropic fallback needs the key


def test_invariant_10_live_with_a_key_uses_the_anthropic_transport() -> None:
    settings = Settings(
        env=Environment.LOCAL,
        kb_provider_mode=KnowledgeProviderMode.LIVE,
        anthropic_api_key=SecretStr(TEST_KEY),
    )
    assert isinstance(build_transport(settings, ANTHROPIC), AnthropicTransport)


def test_SEC_020_kb_enabled_setting_is_the_kill_switch() -> None:
    gw = build_gateway(
        Settings(env=Environment.LOCAL),  # SOS_KB_ENABLED defaults to off
        policy=StaticAiPolicy(),
        sink=RecordingSink(),
        ledger=InMemorySpendLedger(),
        counters=InMemoryKV(),
    )
    with pytest.raises(AiDisabled):
        gw.run_turn(Metering(TENANT, "ask"), "answer", "s", [UserMessage("q")], [])


# --- live transport through the SDK, offline ----------------------------------------------------


class Recorder:
    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.responses.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def ok_message() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-5",
            "content": [
                {
                    "type": "text",
                    "text": "Exams start at 09:30.",
                    "citations": [
                        {
                            "type": "search_result_location",
                            "source": f"sos://doc/{DOC}/v2#p1",
                            "title": "Circular",
                            "cited_text": "Exams start at 09:30.",
                            "search_result_index": 0,
                            "start_block_index": 0,
                            "end_block_index": 1,
                        }
                    ],
                }
            ],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 12},
        },
    )


def live(recorder: Recorder) -> AnthropicTransport:
    return AnthropicTransport(
        api_key=TEST_KEY,
        base_url=load_llm_config().client.api_base_url,
        http_client=httpx.Client(transport=httpx.MockTransport(recorder)),
    )


@pytest.fixture
def hostile_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """SDK environment variables must change nothing (invariant 10)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env-should-not-be-used")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "env-token-should-not-be-used")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://attacker.invalid")


@pytest.mark.usefixtures("hostile_env")
def test_invariant_10_live_request_goes_to_the_configured_endpoint_with_the_settings_key() -> None:
    recorder = Recorder(ok_message())
    gw = gateway_with(live(recorder))
    block = Block(f"sos://doc/{DOC}/v2#p1", "Circular", "Exams start at 09:30.")
    turn0 = ModelTurn(
        "claude-sonnet-5",
        "tool_use",
        (),
        (ToolCall("toolu_1", "search_documents", {"query": "exam"}),),
        Usage(1, 1),
    )
    conversation: list[ConversationItem] = [
        UserMessage("When do exams start?"),
        AssistantMessage(turn0),
        ToolResultsMessage((ToolOutcome("toolu_1", (block,)),)),
    ]
    turn = gw.run_turn(Metering(TENANT, "ask"), "answer", "system", conversation, [SEARCH])

    (request,) = recorder.requests
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == TEST_KEY
    assert "authorization" not in {k.lower() for k in request.headers}
    body = json.loads(request.content)
    assert body["model"] == "claude-sonnet-5"
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"][2]["content"][0]["content"][0]["type"] == "search_result"
    assert "tool_choice" not in body
    assert turn.segments[0].citations[0].source == block.source
    assert turn.usage.input_tokens == 120


@pytest.mark.parametrize(
    ("step", "kind", "status"),
    [
        (
            httpx.Response(429, headers={"retry-after": "2"}, json={"type": "error"}),
            "rate_limited",
            429,
        ),
        (httpx.Response(529, json={"error": {"type": "overloaded_error"}}), "overloaded", 529),
        (httpx.Response(500, json={"error": {"type": "api_error"}}), "server", 500),
        (httpx.Response(400, json={"error": {"type": "invalid_request_error"}}), "rejected", 400),
        (httpx.Response(401, json={"error": {"type": "authentication_error"}}), "rejected", 401),
        (httpx.ReadTimeout("timed out"), "timeout", None),
        (httpx.ConnectError("refused"), "connection", None),
    ],
)
def test_NFR_AVL_004_sdk_failures_are_classified_and_not_retried_by_the_sdk(
    step: httpx.Response | Exception, kind: str, status: int | None
) -> None:
    recorder = Recorder(step)
    request = MessagesRequest(
        body={
            "model": "claude-sonnet-5",
            "max_tokens": 10,
            "messages": [{"role": "user", "content": "q"}],
        },
        timeout_s=5,
    )
    with pytest.raises(TransportError) as info:
        live(recorder).send(request)
    assert info.value.kind == kind
    assert info.value.status == status
    assert info.value.__cause__ is None  # the SDK error (and any echoed text) is dropped
    assert len(recorder.requests) == 1  # max_retries=0: the gateway owns retries
    if kind == "rate_limited":
        assert info.value.retry_after_s == 2


def test_invariant_5_sdk_loggers_are_capped() -> None:
    live(Recorder())
    for name in ("anthropic", "httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING


# --- CLAUDE.md §11: the SDK stays inside the gateway ---------------------------------------------

_SDK_NAMES = (
    r"anthropic|openai|voyageai|vertexai"
    r"|google\.genai|google\.generativeai|google\.cloud\.aiplatform|google\.auth|google\.oauth2"
)
_SDK = re.compile(rf"^({_SDK_NAMES})(\..+)?$")
_DYNAMIC = re.compile(rf"""(import_module|__import__)\(\s*["']({_SDK_NAMES})""")
_GOOGLE_FROM = frozenset({"genai", "generativeai", "auth", "oauth2"})


def _sdk_imports(path: Path) -> set[str]:
    """Provider SDKs a file imports (``google.*`` as its two-part name)."""
    source = path.read_text(encoding="utf-8")
    found: set[str] = {m.group(2) for m in _DYNAMIC.finditer(source)}
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if isinstance(node, ast.Import):
            found |= {m.group(1) for a in node.names if (m := _SDK.match(a.name))}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if match := _SDK.match(node.module):
                found.add(match.group(1))
            elif node.module in ("google", "google.cloud"):
                names = {a.name for a in node.names}
                found |= {f"google.{n}" for n in names & _GOOGLE_FROM}
                if node.module == "google.cloud" and "aiplatform" in names:
                    found.add("google.cloud.aiplatform")
    return found


def test_the_sdk_scan_sees_google_imports(tmp_path: Path) -> None:
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from google import genai\nimport google.auth.transport\nfrom google.oauth2 import x\n"
        "import vertexai\nimport google.protobuf\n",
        encoding="utf-8",
    )
    assert _sdk_imports(sample) == {"google.genai", "google.auth", "google.oauth2", "vertexai"}


def _python_files() -> Iterator[Path]:
    for top in ("apps", "scripts", "evals"):
        for path in (REPO / top).rglob("*.py"):
            if not {"node_modules", ".venv", "__pycache__"} & set(path.parts):
                yield path


def test_SEC_020_provider_sdks_are_imported_only_in_the_gateway() -> None:
    """Mirror of semgrep sos-llm-sdk-outside-gateway (apps/, scripts/, evals/)."""
    offenders = [
        f"{path.relative_to(REPO)}: {sorted(found)}"
        for path in _python_files()
        if GATEWAY not in path.parents and (found := _sdk_imports(path))
    ]
    assert not offenders, offenders


def test_SEC_020_within_the_gateway_only_the_live_transport_imports_anthropic() -> None:
    importers = sorted(p.name for p in GATEWAY.rglob("*.py") if "anthropic" in _sdk_imports(p))
    assert importers == ["anthropic_transport.py"]


def test_ADR_0033_within_the_gateway_only_gemini_auth_imports_google_auth() -> None:
    """Vertex AI is plain REST over httpx; google-auth only mints service-identity tokens."""
    importers = {
        p.name: found
        for p in GATEWAY.rglob("*.py")
        if (found := _sdk_imports(p) - {"anthropic", "voyageai"})
    }
    assert importers == {"gemini_auth.py": {"google.auth", "google.oauth2"}}


def test_ADR_0005_platform_and_knowledge_use_the_same_usd_inr_rate() -> None:
    billing = yaml.safe_load(
        (REPO / "apps" / "api" / "app" / "platform" / "billing.yaml").read_text(encoding="utf-8")
    )
    assert Decimal(billing["billing"]["usd_inr_rate"]) == load_llm_config().budget.usd_inr_rate


# --- streaming through the SDK (docs/06 §5.1; FR-KB-008) -----------------------------------------


def sse_response(events: list[dict[str, Any]]) -> httpx.Response:
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, content=body.encode("utf-8")
    )


def message_events() -> list[dict[str, Any]]:
    from app.knowledge.gateway.fake import _stream_events

    return list(_stream_events(ok_message().json()))


def test_FR_KB_008_live_stream_yields_deltas_then_the_cited_turn() -> None:
    from app.knowledge.domain import TextDelta

    recorder = Recorder(sse_response(message_events()))
    gw = gateway_with(live(recorder))
    items = list(
        gw.stream_turn(Metering(TENANT, "ask"), "answer", "s", [UserMessage("q")], [SEARCH])
    )
    body = json.loads(recorder.requests[0].content)
    assert body["stream"] is True
    text = "".join(i.text for i in items if isinstance(i, TextDelta))
    assert text == "Exams start at 09:30."
    turn = items[-1]
    assert isinstance(turn, ModelTurn)
    assert turn.segments[0].citations[0].source == f"sos://doc/{DOC}/v2#p1"
    assert turn.usage.output_tokens == 12


def test_NFR_AVL_004_live_error_event_mid_stream_is_unavailable() -> None:
    from app.knowledge.gateway.errors import ProviderUnavailable

    events = [
        *message_events()[:3],
        {"type": "error", "error": {"type": "overloaded_error", "message": "synthetic"}},
    ]
    gw = gateway_with(live(Recorder(sse_response(events))))
    stream = gw.stream_turn(Metering(TENANT, "ask"), "answer", "s", [UserMessage("q")], [SEARCH])
    with pytest.raises(ProviderUnavailable):
        list(stream)
