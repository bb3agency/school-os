"""The live Gemini transport, its credentials and caches, offline (ADR-0033; invariant 10).

``GeminiTransport`` runs against an in-process ``httpx.MockTransport`` that replays the synthetic
Vertex AI responses in ``gemini_fixtures/``: no network, no real project, key or data.

- Endpoint: the regional endpoint of the configured location, API version and model path from
  ``models.yaml``; ``global`` only for an offline role that names it; the bearer token from the
  token source, no proxy or base URL taken from the environment.
- Zero Data Retention: nothing is sent until the project's ``cacheConfig`` says caching is
  disabled; a project with caching on, or a failed check, is refused (fail closed).
- Errors are classified by status only and never carry Google's message text; streams are parsed
  from server-sent events, an error object mid-stream raises.
- Explicit context caches hold only the static prefix, are created once and reused, are skipped
  below the model minimum or next to a ``toolConfig``, and fall back to an uncached request.
- Credentials: a service-account key signs its own token request (synthetic RSA key made in the
  test); workload identity federation needs an AWS-federated external account; a person's login
  is refused; the factory builds the Gemini transport only with complete settings.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app.core.config import Environment, KnowledgeProviderMode, LlmCredentialsSource, Settings
from app.knowledge.config.llm import ExplicitCache, load_llm_config
from app.knowledge.domain import Metering, UserMessage
from app.knowledge.gateway.factory import ProviderModeError, build_transports
from app.knowledge.gateway.fake import FakeTransport
from app.knowledge.gateway.fake_gemini import GeminiWireFake, signature_for
from app.knowledge.gateway.gemini_auth import GoogleTokenSource, StaticTokenSource
from app.knowledge.gateway.gemini_cache import ContextCaches
from app.knowledge.gateway.gemini_transport import GeminiTransport, classify_status
from app.knowledge.gateway.transport import MessagesRequest, TransportError

FIXTURES = Path(__file__).with_name("gemini_fixtures")
CONFIG = load_llm_config()
PROJECT = "sos-ai-test"
TOKEN = "ya29.synthetic-access-token"
BASE = "https://asia-south1-aiplatform.googleapis.com/v1"
MODEL_URL = f"{BASE}/projects/{PROJECT}/locations/asia-south1/publishers/google/models"
CACHE_CONFIG_URL = f"{BASE}/projects/{PROJECT}/cacheConfig"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class Vertex:
    """A scripted Vertex AI: routes by URL, records requests."""

    def __init__(self, **routes: Any) -> None:
        self.routes: dict[str, list[Any]] = {
            "cacheConfig": [httpx.Response(200, json=fixture("cache_config_disabled.json"))],
            **{k: list(v) for k, v in routes.items()},
        }
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        key = next(
            k
            for k in ("cacheConfig", "cachedContents", "streamGenerateContent", "generateContent")
            if path.endswith(k) or path.endswith(f":{k}")
        )
        steps = self.routes[key]
        step = steps.pop(0) if len(steps) > 1 else steps[0]
        if isinstance(step, Exception):
            raise step
        assert isinstance(step, httpx.Response)
        return step

    def calls(self, key: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.url.path.endswith(key)]


def transport(
    vertex: Vertex, *, caches: ContextCaches | None = None, verify: bool = True
) -> GeminiTransport:
    return GeminiTransport(
        project=PROJECT,
        location="asia-south1",
        config=CONFIG.gemini,
        tokens=StaticTokenSource(TOKEN),
        caches=caches,
        http=httpx.Client(transport=httpx.MockTransport(vertex)),
        verify_cache_config=verify,
    )


def request(body: dict[str, Any] | None = None, **kw: Any) -> MessagesRequest:
    return MessagesRequest(
        body=body or {"contents": [{"role": "user", "parts": [{"text": "q"}]}]},
        timeout_s=5,
        model=kw.pop("model", "gemini-3.5-flash"),
        **kw,
    )


# --- endpoint, auth, ZDR check ------------------------------------------------------------------


def test_ADR_0033_calls_the_regional_endpoint_with_a_bearer_token() -> None:
    vertex = Vertex(generateContent=[httpx.Response(200, json=fixture("answer_markers.json"))])
    data = transport(vertex).send(request())
    assert data["modelVersion"] == "gemini-3.5-flash"
    config_check, call = vertex.requests
    assert str(config_check.url) == CACHE_CONFIG_URL
    assert str(call.url) == f"{MODEL_URL}/gemini-3.5-flash:generateContent"
    assert call.headers["authorization"] == f"Bearer {TOKEN}"
    assert "x-goog-api-key" not in call.headers  # never an API key (invariant 10)
    assert json.loads(call.content) == request().body


def test_ADR_0033_only_an_offline_role_location_reaches_the_global_endpoint() -> None:
    vertex = Vertex(generateContent=[httpx.Response(200, json=fixture("structured.json"))])
    transport(vertex).send(request(model="gemini-3.1-pro-preview", location="global"))
    call = vertex.calls("generateContent")[0]
    assert str(call.url) == (
        "https://aiplatform.googleapis.com/v1/projects/sos-ai-test/locations/global/"
        "publishers/google/models/gemini-3.1-pro-preview:generateContent"
    )


def test_invariant_10_the_environment_cannot_redirect_or_proxy_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://attacker.invalid:3128")
    t = GeminiTransport(
        project=PROJECT,
        location="asia-south1",
        config=CONFIG.gemini,
        tokens=StaticTokenSource(TOKEN),
    )
    assert t._http.trust_env is False
    assert t._http.follow_redirects is False
    t.close()


@pytest.mark.parametrize(
    "answer",
    [
        httpx.Response(200, json={"name": "projects/x/cacheConfig", "disableCache": False}),
        httpx.Response(200, json={"name": "projects/x/cacheConfig"}),
        httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED"}}),
    ],
)
def test_ADR_0033_nothing_is_sent_unless_project_caching_is_disabled(
    answer: httpx.Response,
) -> None:
    vertex = Vertex(cacheConfig=[answer], generateContent=[httpx.Response(200, json={})])
    with pytest.raises(TransportError) as info:
        transport(vertex).send(request())
    assert info.value.kind == "rejected"
    assert vertex.calls("generateContent") == []


def test_ADR_0033_the_cache_config_is_checked_once_per_period() -> None:
    vertex = Vertex(generateContent=[httpx.Response(200, json=fixture("structured.json"))])
    t = transport(vertex)
    for _ in range(3):
        t.send(request())
    assert len(vertex.calls("cacheConfig")) == 1


# --- errors (NFR-AVL-004) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("step", "kind", "status"),
    [
        (
            httpx.Response(429, headers={"retry-after": "3"}, json=fixture("error_429.json")),
            "rate_limited",
            429,
        ),
        (httpx.Response(503, json={"error": {"code": 503}}), "overloaded", 503),
        (httpx.Response(500, json={"error": {"code": 500}}), "server", 500),
        (httpx.Response(504, json={"error": {"code": 504}}), "server", 504),
        (httpx.Response(400, json={"error": {"code": 400}}), "rejected", 400),
        (httpx.Response(404, json={"error": {"code": 404}}), "rejected", 404),
        (httpx.ReadTimeout("timed out"), "timeout", None),
        (httpx.ConnectError("refused"), "connection", None),
    ],
)
def test_NFR_AVL_004_failures_are_classified_without_provider_text(
    step: Any, kind: str, status: int | None
) -> None:
    vertex = Vertex(generateContent=[step])
    with pytest.raises(TransportError) as info:
        transport(vertex).send(request())
    assert (info.value.kind, info.value.status) == (kind, status)
    assert "echoed" not in str(info.value)
    assert info.value.__cause__ is None
    if kind == "rate_limited":
        assert info.value.retry_after_s == 3
    assert len(vertex.calls("generateContent")) == 1  # the gateway owns retries


def test_classify_status_table() -> None:
    assert [classify_status(s) for s in (429, 503, 502, 408, 401)] == [
        "rate_limited",
        "overloaded",
        "server",
        "timeout",
        "rejected",
    ]


def test_invariant_5_http_loggers_are_capped() -> None:
    transport(Vertex())
    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level >= logging.WARNING


# --- streaming ----------------------------------------------------------------------------------


def sse(name: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=(FIXTURES / name).read_bytes(),
    )


def test_FR_KB_008_streams_are_parsed_from_server_sent_events() -> None:
    vertex = Vertex(streamGenerateContent=[sse("stream_answer.sse")])
    chunks = list(transport(vertex).stream(request()))
    assert len(chunks) == 4
    assert chunks[-1]["candidates"][0]["finishReason"] == "STOP"
    (call,) = vertex.calls("streamGenerateContent")
    assert call.url.params["alt"] == "sse"


def test_NFR_AVL_004_an_error_object_mid_stream_raises() -> None:
    vertex = Vertex(streamGenerateContent=[sse("stream_error.sse")])
    events = transport(vertex).stream(request())
    assert next(events)["candidates"]
    with pytest.raises(TransportError) as info:
        next(events)
    assert info.value.kind == "overloaded"


def test_NFR_AVL_004_a_stream_that_fails_to_open_raises_before_any_event() -> None:
    vertex = Vertex(streamGenerateContent=[httpx.Response(429, json=fixture("error_429.json"))])
    with pytest.raises(TransportError) as info:
        transport(vertex).stream(request())
    assert info.value.kind == "rate_limited"


# --- explicit context caches (static prefix only) ------------------------------------------------


BIG_SYSTEM = {"parts": [{"text": "Static rules. " * 1600}]}  # ~22k chars: above 4096 tokens
TOOLS = [{"functionDeclarations": [{"name": "search_documents", "description": "Search"}]}]


def cached_body(**extra: Any) -> dict[str, Any]:
    return {
        "systemInstruction": BIG_SYSTEM,
        "tools": TOOLS,
        "contents": [{"role": "user", "parts": [{"text": "When do exams start?"}]}],
        "generationConfig": {"maxOutputTokens": 10},
        **extra,
    }


def caches(clock: Callable[[], float] = lambda: 1_790_000_000.0) -> ContextCaches:
    return ContextCaches(
        ExplicitCache(ttl_s=3600, refresh_margin_s=300, failure_backoff_s=300),
        lambda model: CONFIG.capabilities[model].explicit_cache_min_tokens,
        clock=clock,
    )


def test_ADR_0033_the_static_prefix_is_cached_once_and_reused() -> None:
    vertex = Vertex(
        cachedContents=[httpx.Response(200, json=fixture("cached_content.json"))],
        generateContent=[httpx.Response(200, json=fixture("answer_markers.json"))],
    )
    t = transport(vertex, caches=caches())
    prefix = ("systemInstruction", "tools")
    t.send(request(cached_body(), static_prefix=prefix))
    t.send(request(cached_body(), static_prefix=prefix))
    (create,) = vertex.calls("cachedContents")
    payload = json.loads(create.content)
    assert set(payload) == {"model", "systemInstruction", "tools", "ttl"}  # never contents
    assert payload["ttl"] == "3600s"
    assert payload["model"].endswith("/publishers/google/models/gemini-3.5-flash")
    for call in vertex.calls("generateContent"):
        sent = json.loads(call.content)
        assert sent["cachedContent"] == fixture("cached_content.json")["name"]
        assert "systemInstruction" not in sent
        assert "tools" not in sent
        assert sent["contents"] == cached_body()["contents"]


@pytest.mark.parametrize(
    "body",
    [
        {"systemInstruction": {"parts": [{"text": "Short rules."}]}, "contents": []},
        cached_body(toolConfig={"functionCallingConfig": {"mode": "NONE"}}),
    ],
)
def test_ADR_0033_small_prefixes_and_tool_config_go_uncached(body: dict[str, Any]) -> None:
    vertex = Vertex(generateContent=[httpx.Response(200, json=fixture("structured.json"))])
    transport(vertex, caches=caches()).send(
        request(body, static_prefix=("systemInstruction", "tools"))
    )
    assert vertex.calls("cachedContents") == []
    assert json.loads(vertex.calls("generateContent")[0].content) == body


def test_ADR_0033_a_failed_cache_falls_back_to_an_uncached_request() -> None:
    vertex = Vertex(
        cachedContents=[httpx.Response(400, json={"error": {"code": 400}})],
        generateContent=[httpx.Response(200, json=fixture("structured.json"))],
    )
    t = transport(vertex, caches=caches())
    prefix = ("systemInstruction", "tools")
    t.send(request(cached_body(), static_prefix=prefix))
    t.send(request(cached_body(), static_prefix=prefix))
    assert len(vertex.calls("cachedContents")) == 1  # paused after the failure
    for call in vertex.calls("generateContent"):
        assert "cachedContent" not in json.loads(call.content)


def test_ADR_0033_an_expired_cache_is_dropped_and_the_request_resent_whole() -> None:
    vertex = Vertex(
        cachedContents=[httpx.Response(200, json=fixture("cached_content.json"))],
        generateContent=[
            httpx.Response(404, json={"error": {"code": 404}}),
            httpx.Response(200, json=fixture("structured.json")),
        ],
    )
    transport(vertex, caches=caches()).send(
        request(cached_body(), static_prefix=("systemInstruction", "tools"))
    )
    first, second = vertex.calls("generateContent")
    assert "cachedContent" in json.loads(first.content)
    assert json.loads(second.content) == cached_body()


def test_ADR_0033_a_cache_close_to_expiry_is_replaced() -> None:
    now = [1_790_000_000.0]
    store = caches(clock=lambda: now[0])
    plan = store.plan("gemini-3.5-flash", "asia-south1", cached_body(), ("systemInstruction",))
    assert plan is not None
    key, _ = plan
    store.store(key, {"name": "projects/1/locations/asia-south1/cachedContents/9"})
    assert store.lookup(key) is not None
    now[0] += 3600 - 200  # inside the 300 s refresh margin
    assert store.lookup(key) is None


# --- credentials (invariant 10) -----------------------------------------------------------------


def _service_account_key() -> tuple[str, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    info = {
        "type": "service_account",
        "project_id": PROJECT,
        "private_key_id": "synthetic",
        "private_key": pem,
        "client_email": f"sos-vertex@{PROJECT}.iam.gserviceaccount.com",
        "client_id": "1",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    return json.dumps(info), key.public_key()


def test_invariant_10_a_service_account_signs_its_own_token_request() -> None:
    raw, public = _service_account_key()
    seen: list[httpx.Request] = []

    def oauth(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"access_token": TOKEN, "expires_in": 3600})

    source = GoogleTokenSource(
        LlmCredentialsSource.SERVICE_ACCOUNT_KEY,
        raw,
        http=httpx.Client(transport=httpx.MockTransport(oauth)),
        aws_region="ap-south-1",
    )
    assert source.token() == TOKEN
    assert source.token() == TOKEN  # cached until it nears expiry
    (req,) = seen
    assert str(req.url) == "https://oauth2.googleapis.com/token"
    form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
    claims = jwt.decode(
        form["assertion"],
        public,
        algorithms=["RS256"],
        audience="https://oauth2.googleapis.com/token",
    )
    assert claims["iss"] == f"sos-vertex@{PROJECT}.iam.gserviceaccount.com"
    assert claims["scope"] == "https://www.googleapis.com/auth/cloud-platform"


def test_invariant_10_a_failed_token_exchange_is_a_classified_failure() -> None:
    raw, _ = _service_account_key()
    source = GoogleTokenSource(
        LlmCredentialsSource.SERVICE_ACCOUNT_KEY,
        raw,
        http=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "x"}))
        ),
        aws_region="ap-south-1",
    )
    with pytest.raises(TransportError) as info:
        source.token()
    assert info.value.kind == "rejected"


def test_invariant_10_a_persons_login_never_becomes_a_token_source() -> None:
    person = json.dumps({"type": "authorized_user", "refresh_token": "x", "client_id": "y"})
    with pytest.raises(ValueError, match="authorized_user"):
        GoogleTokenSource(
            LlmCredentialsSource.SERVICE_ACCOUNT_KEY,
            person,
            http=httpx.Client(),
            aws_region="ap-south-1",
        )


# --- factory (SEC-020, invariant 10) ------------------------------------------------------------

WIF = json.dumps(
    {
        "type": "external_account",
        "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/"
        "sos/providers/aws",
        "subject_token_type": "urn:ietf:params:aws:token-type:aws4_request",
        "token_url": "https://sts.googleapis.com/v1/token",
        "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/"
        "-/serviceAccounts/sos-vertex@sos-ai-test.iam.gserviceaccount.com:generateAccessToken",
        "credential_source": {
            "environment_id": "aws1",
            "regional_cred_verification_url": "https://sts.{region}.amazonaws.com"
            "?Action=GetCallerIdentity&Version=2011-06-15",
        },
    }
)


def live(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "env": Environment.LOCAL,
        "kb_provider_mode": KnowledgeProviderMode.LIVE,
        "llm_gcp_project": PROJECT,
        "llm_gcp_credentials_source": LlmCredentialsSource.WORKLOAD_IDENTITY,
        "llm_gcp_credentials_json": SecretStr(WIF),
    }
    values.update(overrides)
    return Settings(**values)


def test_ADR_0033_live_builds_only_the_gemini_transport_for_an_all_gemini_config() -> None:
    transports = build_transports(live(), CONFIG)
    assert set(transports) == {"gemini"}
    assert isinstance(transports["gemini"], GeminiTransport)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"llm_gcp_project": None}, "SOS_LLM_GCP_PROJECT"),
        ({"llm_gcp_credentials_json": None}, "SOS_LLM_GCP_CREDENTIALS_JSON"),
    ],
)
def test_ADR_0033_live_gemini_needs_its_settings(overrides: dict[str, Any], match: str) -> None:
    with pytest.raises(ProviderModeError, match=match):
        build_transports(live(**overrides), CONFIG)


@pytest.mark.parametrize("env", [Environment.STAGING, Environment.PROD])
def test_ADR_0033_the_factory_refuses_prod_without_zdr_or_outside_india(env: Environment) -> None:
    settings = Settings.model_construct(
        env=env,
        kb_provider_mode=KnowledgeProviderMode.LIVE,
        llm_gcp_project=PROJECT,
        llm_gcp_location="asia-south1",
        llm_gcp_credentials_source=LlmCredentialsSource.WORKLOAD_IDENTITY,
        llm_gcp_credentials_json=SecretStr(WIF),
        llm_zdr_confirmed=False,
        llm_verify_cache_config=True,
    )
    with pytest.raises(ProviderModeError, match="ZDR"):
        build_transports(settings, CONFIG)
    moved = settings.model_copy(update={"llm_zdr_confirmed": True, "llm_gcp_location": "global"})
    with pytest.raises(ProviderModeError, match="India"):
        build_transports(moved, CONFIG)


def test_ADR_0033_a_config_with_a_fallback_role_builds_both_transports() -> None:
    mixed = CONFIG.use_fallback(["notice"])
    with pytest.raises(ProviderModeError, match="SOS_ANTHROPIC_API_KEY"):
        build_transports(live(), mixed)
    both = build_transports(live(anthropic_api_key=SecretStr("sk-ant-test-synthetic")), mixed)
    assert set(both) == {"gemini", "anthropic"}


# --- the offline Gemini-wire fake ---------------------------------------------------------------


def test_ADR_0033_the_fake_rejects_a_replayed_call_without_its_signature() -> None:
    fake = GeminiWireFake(FakeTransport())
    body: dict[str, Any] = {
        "contents": [
            {"role": "user", "parts": [{"text": "q"}]},
            {
                "role": "model",
                "parts": [{"functionCall": {"id": "toolu_fake_01", "name": "search_documents"}}],
            },
        ]
    }
    with pytest.raises(TransportError) as info:
        fake.send(request(body))
    assert info.value.kind == "rejected"
    body["contents"][1]["parts"][0]["thoughtSignature"] = signature_for("toolu_fake_01")
    assert fake.send(request(body))["candidates"]


def test_ADR_0033_fake_mode_answers_through_the_gemini_codec_with_valid_citations() -> None:
    from app.authz.kv import InMemoryKV
    from app.knowledge.domain import (
        AssistantMessage,
        SearchResultBlock,
        ToolOutcome,
        ToolResultsMessage,
        ToolSpec,
    )
    from app.knowledge.gateway.budget import InMemorySpendLedger, StaticAiPolicy
    from app.knowledge.gateway.factory import build_gateway
    from app.knowledge.gateway.metering import RecordingSink

    gw = build_gateway(
        Settings(env=Environment.LOCAL, kb_enabled=True),
        policy=StaticAiPolicy(),
        sink=RecordingSink(),
        ledger=InMemorySpendLedger(),
        counters=InMemoryKV(),
    )
    search = ToolSpec("search_documents", "Search", {"type": "object"}, "document.read")
    metering = Metering(uuid.uuid4(), "ask")
    first = gw.run_turn(metering, "answer", "s", [UserMessage("When do exams start?")], [search])
    (call,) = first.tool_calls
    assert call.signature == signature_for(call.call_id)
    block = SearchResultBlock("sos://doc/x/v1#p1", "Circular 7/2026", "Exams start at 09:30.")
    answer = gw.run_turn(
        metering,
        "answer",
        "s",
        [
            UserMessage("When do exams start?"),
            AssistantMessage(first),
            ToolResultsMessage((ToolOutcome(call.call_id, (block,)),)),
        ],
        [search],
    )
    (segment,) = answer.segments
    assert segment.text == "Circular 7/2026: Exams start at 09:30."
    assert [(c.source, c.cited_text) for c in segment.citations] == [
        (block.source, "Exams start at 09:30.")
    ]
