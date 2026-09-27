"""Voyage embeddings provider over httpx (K2; ADR-0006, docs/06 §4.6, invariant 10).

Only ``httpx.MockTransport``: no live network, no real key (the key below is synthetic). Checks
the request shape, error classification, the retry contract with ``TenantEmbedder``, that
nothing (text, key, response body) leaks into errors or logs, and that the provider is only
built when config selects it.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Environment, KnowledgeProviderMode, Settings
from app.knowledge.config.embeddings import EmbeddingsConfig, load_embeddings_config
from app.knowledge.embeddings import (
    CachingTenantEmbedder,
    EmbeddingsNotConfiguredError,
    InMemoryEmbeddingCache,
    is_transient,
    select_embeddings_provider,
)
from app.knowledge.gateway.embeddings_voyage import (
    VoyageEmbeddingsError,
    VoyageEmbeddingsProvider,
    build_voyage_provider,
)
from app.knowledge.interfaces import EmbeddingsProvider

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SESSION = cast("Session", object())
TENANT = uuid.UUID("00000000-0000-7000-8000-0000000000aa")
KEY = "synthetic" * 4  # low entropy on purpose: not a key shape (gitleaks)
MODEL = "synthetic-embed-1"
Handler = Callable[[httpx.Request], httpx.Response]


def vector(i: int, dims: int = 1024) -> list[float]:
    v = [0.0] * dims
    v[i % dims] = 1.0
    return v


def ok(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    data = [
        {"object": "embedding", "embedding": vector(i), "index": i}
        for i in reversed(range(len(body["input"])))  # out of order on purpose
    ]
    return httpx.Response(
        200, json={"object": "list", "data": data, "model": body["model"], "usage": {}}
    )


class Server:
    """Records requests; answers with queued responses, then ``ok``."""

    def __init__(self, *responses: httpx.Response | type[httpx.TransportError]) -> None:
        self.requests: list[httpx.Request] = []
        self.queue = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, type):
                raise item("synthetic failure", request=request)
            return item
        return ok(request)


def provider(handler: Handler, dims: int = 1024) -> VoyageEmbeddingsProvider:
    return VoyageEmbeddingsProvider(
        model=MODEL,
        dimensions=dims,
        api_key=KEY,
        http=load_embeddings_config().voyage,
        transport=httpx.MockTransport(handler),
    )


def selected_config() -> EmbeddingsConfig:
    data = load_embeddings_config().model_dump()
    data["candidates"]["voyage_multilingual"]["model"] = MODEL
    data["selected"] = "voyage_multilingual"
    return EmbeddingsConfig.model_validate(data)


# --- request and response ------------------------------------------------------------------------


def test_FR_KB_001_request_shape_and_ordering() -> None:
    server = Server()
    vectors = provider(server).embed(["first text", "second text"], "query")
    assert vectors == [vector(0), vector(1)]
    (request,) = server.requests
    assert request.method == "POST"
    assert str(request.url) == load_embeddings_config().voyage.endpoint
    assert request.url.scheme == "https"
    assert request.headers["Authorization"] == f"Bearer {KEY}"
    body = json.loads(request.content)
    assert body == {
        "input": ["first text", "second text"],
        "model": MODEL,
        "input_type": "query",
        "output_dimension": 1024,
        "output_dtype": "float",
        "truncation": False,
    }


def test_empty_input_makes_no_request() -> None:
    server = Server()
    assert provider(server).embed([], "document") == []
    assert server.requests == []


def test_satisfies_the_provider_protocol() -> None:
    p = provider(Server())
    assert isinstance(p, EmbeddingsProvider)
    assert (p.name, p.model, p.dimensions) == ("voyage", MODEL, 1024)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"data": [{"embedding": [1.0]}]}),
        httpx.Response(200, json={"data": [{"index": 0, "embedding": vector(0, 512)}]}),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"data": [{"index": 0, "embedding": ["x"] * 1024}]}),
    ],
)
def test_malformed_or_wrong_dimension_responses_are_permanent_errors(
    response: httpx.Response,
) -> None:
    with pytest.raises(VoyageEmbeddingsError) as info:
        provider(Server(response)).embed(["t"], "document")
    assert info.value.retryable is False


# --- error classification --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "retryable"),
    [(408, True), (429, True), (500, True), (503, True), (400, False), (401, False), (409, False)],
)
def test_http_errors_are_classified(status: int, retryable: bool) -> None:
    server = Server(httpx.Response(status, json={"detail": "echo: first text"}))
    with pytest.raises(VoyageEmbeddingsError) as info:
        provider(server).embed(["first text"], "document")
    assert info.value.retryable is retryable
    assert info.value.status == status
    assert is_transient(info.value) is retryable
    message = str(info.value)
    assert "first text" not in message
    assert KEY not in message


def test_retry_after_header_is_parsed() -> None:
    server = Server(httpx.Response(429, headers={"Retry-After": "2"}))
    with pytest.raises(VoyageEmbeddingsError) as info:
        provider(server).embed(["t"], "document")
    assert info.value.retry_after_s == 2.0


@pytest.mark.parametrize("exc", [httpx.ReadTimeout, httpx.ConnectError])
def test_timeouts_and_transport_errors_are_transient(exc: type[httpx.TransportError]) -> None:
    with pytest.raises(VoyageEmbeddingsError) as info:
        provider(Server(exc)).embed(["t"], "document")
    assert info.value.retryable is True
    assert info.value.__cause__ is None


def test_timeouts_come_from_config() -> None:
    http = load_embeddings_config().voyage
    p = provider(Server())
    timeout = p._client.timeout
    assert (timeout.connect, timeout.read) == (http.connect_timeout_s, http.read_timeout_s)


# --- retries through TenantEmbedder -----------------------------------------------------------


def test_tenant_embedder_retries_transient_voyage_failures() -> None:
    server = Server(
        httpx.Response(503),
        httpx.ReadTimeout,
        httpx.Response(429, headers={"Retry-After": "1"}),
    )
    sleeps: list[float] = []
    embedder = CachingTenantEmbedder(
        provider(server),
        selected_config(),
        InMemoryEmbeddingCache(),
        sleep=sleeps.append,
        jitter=lambda: 1.0,
    )
    vectors = embedder.embed(SESSION, TENANT, ["Synthetic circular"], "document")
    assert vectors == [vector(0)]
    assert len(server.requests) == 4
    assert sleeps == [0.5, 1.0, 2.0]


def test_tenant_embedder_does_not_retry_permanent_voyage_failures() -> None:
    server = Server(httpx.Response(401))
    sleeps: list[float] = []
    embedder = CachingTenantEmbedder(
        provider(server), selected_config(), InMemoryEmbeddingCache(), sleep=sleeps.append
    )
    with pytest.raises(VoyageEmbeddingsError):
        embedder.embed(SESSION, TENANT, ["t"], "document")
    assert len(server.requests) == 1
    assert sleeps == []


def test_SEC_008_no_text_or_key_in_logs(capsys: pytest.CaptureFixture[str]) -> None:
    text = "Synthetic student Lakshmi Devi, ph 9123456780"
    server = Server(httpx.Response(500, json={"detail": text}))
    embedder = CachingTenantEmbedder(
        provider(server), selected_config(), InMemoryEmbeddingCache(), sleep=lambda _: None
    )
    capsys.readouterr()
    embedder.embed(SESSION, TENANT, [text], "document")
    out = capsys.readouterr()
    logged = out.out + out.err
    assert "kb.embeddings.retry" in logged
    for fragment in ("Lakshmi", "9123456780", KEY):
        assert fragment not in logged


# --- construction and selection -----------------------------------------------------------------


def settings(key: str | None = KEY) -> Settings:
    return Settings(
        env=Environment.LOCAL,
        kb_provider_mode=KnowledgeProviderMode.LIVE,
        embeddings_api_key=None if key is None else SecretStr(key),
    )


def voyage_factory(s: Settings, handler: Handler = ok) -> Callable[[Any], EmbeddingsProvider]:
    http = load_embeddings_config().voyage
    return lambda c: build_voyage_provider(
        c, settings=s, http=http, transport=httpx.MockTransport(handler)
    )


def test_SEC_020_voyage_is_selected_only_by_live_mode_and_config() -> None:
    cfg = selected_config()
    live = select_embeddings_provider(
        KnowledgeProviderMode.LIVE, cfg, network={"voyage": voyage_factory(settings())}
    )
    assert isinstance(live, VoyageEmbeddingsProvider)
    assert live.model == MODEL
    fake = select_embeddings_provider(
        KnowledgeProviderMode.FAKE, cfg, network={"voyage": voyage_factory(settings())}
    )
    assert not isinstance(fake, VoyageEmbeddingsProvider)


def test_FR_KB_001_shipped_config_refuses_live_voyage() -> None:
    with pytest.raises(EmbeddingsNotConfiguredError):
        select_embeddings_provider(
            KnowledgeProviderMode.LIVE,
            load_embeddings_config(),
            network={"voyage": voyage_factory(settings())},
        )


@pytest.mark.parametrize("key", [None, "", "   "])
def test_SEC_009_voyage_needs_an_api_key(key: str | None) -> None:
    with pytest.raises(ValueError, match="SOS_EMBEDDINGS_API_KEY"):
        select_embeddings_provider(
            KnowledgeProviderMode.LIVE,
            selected_config(),
            network={"voyage": voyage_factory(settings(key))},
        )


def test_build_refuses_a_candidate_without_a_model() -> None:
    candidate = load_embeddings_config().candidates["voyage_multilingual"]
    assert candidate.model is None
    with pytest.raises(ValueError, match="no model"):
        build_voyage_provider(candidate, settings=settings(), http=load_embeddings_config().voyage)


def test_build_refuses_a_non_voyage_candidate() -> None:
    candidate = load_embeddings_config().candidates["self_hosted_multilingual"]
    with pytest.raises(ValueError, match="not a voyage candidate"):
        build_voyage_provider(candidate, settings=settings(), http=load_embeddings_config().voyage)
