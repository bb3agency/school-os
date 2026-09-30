"""Reranker providers and selection (docs/06 §6, §13.6; FR-KB-001; invariants 4, 8, 10, 13).

Offline: the deterministic fake, selection by ``retrieval.yaml`` + ``SOS_KB_PROVIDER_MODE``, and
the Voyage adapter over ``httpx.MockTransport`` only (no network, a synthetic key). Checks the
request shape, that errors carry no text, the timeout is the latency budget, and that live mode
cannot start without an evaluated model or an implementation.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from pydantic import SecretStr

from app.core.config import Environment, KnowledgeProviderMode, Settings
from app.knowledge.config.retrieval import Rerank, load_retrieval_config
from app.knowledge.gateway.rerank_voyage import (
    VoyageReranker,
    VoyageRerankError,
    build_voyage_reranker,
)
from app.knowledge.interfaces import Reranker
from app.knowledge.rerank import (
    FAKE_MODEL,
    FakeReranker,
    RerankerNotConfiguredError,
    select_reranker,
)

KEY = "synthetic" * 4  # low entropy on purpose: not a key shape (gitleaks)
QUESTION = "What is the payment amount for the science exhibition?"
PASSAGES = [
    "Sports day timings: the programme begins at 9:30 on the ground.",
    "Science exhibition payment: each student pays Rs. 150 at the office.",
    "Annual day payment: each student pays Rs. 200 towards costumes.",
]


def rerank_config(provider: str = "voyage", model: str | None = "rerank-synthetic-1") -> Rerank:
    base = load_retrieval_config().rerank
    return base.model_copy(
        update={
            "provider": provider,
            "voyage": base.voyage.model_copy(update={"model": model}),
        }
    )


def live(key: str | None = KEY) -> Settings:
    return Settings(
        env=Environment.LOCAL,
        kb_provider_mode=KnowledgeProviderMode.LIVE,
        embeddings_api_key=None if key is None else SecretStr(key),
    )


# --- fake ----------------------------------------------------------------------------------------


def test_FR_KB_001_fake_reranker_is_deterministic_and_prefers_the_matching_passage() -> None:
    fake = FakeReranker()
    assert isinstance(fake, Reranker)
    assert fake.model == FAKE_MODEL
    scores = fake.rerank(QUESTION, PASSAGES, timeout_s=1.0)
    assert scores == fake.rerank(QUESTION, PASSAGES, timeout_s=1.0)
    assert max(range(3), key=lambda i: scores[i]) == 1
    assert scores[1] > scores[2] > scores[0]  # shared "payment" words beat none
    assert fake.rerank("", PASSAGES, timeout_s=1.0) == [0.0, 0.0, 0.0]
    assert fake.rerank(QUESTION, [], timeout_s=1.0) == []


def test_FR_KB_006_fake_reranker_reads_telugu_and_near_spellings() -> None:
    fake = FakeReranker()
    telugu = ["విజ్ఞాన ప్రదర్శన రుసుము 150 రూపాయలు.", "క్రీడా దినోత్సవం సమయాలు."]
    scores = fake.rerank("విజ్ఞాన ప్రదర్శన రుసుము ఎంత?", telugu, timeout_s=1.0)
    assert scores[0] > scores[1]
    near = fake.rerank(
        "exhibiton fees", ["Exhibition fee notice.", "Bus route notice."], timeout_s=1
    )
    assert near[0] > near[1]


# --- selection -----------------------------------------------------------------------------------


def test_FR_KB_001_selection_off_fake_and_live() -> None:
    off = rerank_config(provider="off")
    assert select_reranker(KnowledgeProviderMode.FAKE, off, network={}) is None
    assert select_reranker(KnowledgeProviderMode.LIVE, off, network={}) is None
    fake = select_reranker(KnowledgeProviderMode.FAKE, rerank_config(), network={})
    assert isinstance(fake, FakeReranker)  # fake mode never sends passages anywhere

    built: list[Rerank] = []

    def factory(config: Rerank) -> Reranker:
        built.append(config)
        return FakeReranker()

    chosen = select_reranker(
        KnowledgeProviderMode.LIVE, rerank_config(), network={"voyage": factory}
    )
    assert chosen is not None
    assert built == [rerank_config()]
    with pytest.raises(RerankerNotConfiguredError, match="vertex"):
        select_reranker(KnowledgeProviderMode.LIVE, rerank_config("vertex"), network={})


def test_FR_KB_001_live_voyage_needs_an_evaluated_model_and_a_key() -> None:
    with pytest.raises(ValueError, match="evaluation"):
        build_voyage_reranker(rerank_config(model=None), settings=live())
    with pytest.raises(ValueError, match="SOS_EMBEDDINGS_API_KEY"):
        build_voyage_reranker(rerank_config(), settings=live(key=None))
    reranker = build_voyage_reranker(rerank_config(), settings=live())
    assert (reranker.name, reranker.model) == ("voyage", "rerank-synthetic-1")


# --- Voyage over MockTransport -------------------------------------------------------------------


def voyage(handler: Callable[[httpx.Request], httpx.Response]) -> VoyageReranker:
    return build_voyage_reranker(
        rerank_config(), settings=live(), transport=httpx.MockTransport(handler)
    )


def test_FR_KB_001_voyage_request_shape_and_scores_in_passage_order() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = json.loads(request.content)
        data = [
            {"index": i, "relevance_score": 1.0 - i / 10}
            for i in reversed(range(len(body["documents"])))
        ]
        return httpx.Response(200, json={"object": "list", "data": data, "usage": {}})

    scores = voyage(handler).rerank(QUESTION, PASSAGES, timeout_s=1.5)
    assert scores == [1.0, 0.9, 0.8]
    (request,) = seen
    assert str(request.url) == "https://api.voyageai.com/v1/rerank"
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert json.loads(request.content) == {
        "query": QUESTION,
        "documents": PASSAGES,
        "model": "rerank-synthetic-1",
        "truncation": True,
    }
    assert request.extensions["timeout"]["read"] == 1.5  # the latency budget


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="server says " + PASSAGES[1]),
        httpx.Response(200, json={"data": [{"index": 0, "relevance_score": 0.1}]}),
        httpx.Response(200, json={"data": [{"index": 7, "relevance_score": 0.1}] * 3}),
        httpx.Response(200, text="not json"),
    ],
)
def test_invariant_5_voyage_errors_carry_no_text(response: httpx.Response) -> None:
    with pytest.raises(VoyageRerankError) as err:
        voyage(lambda _: response).rerank(QUESTION, PASSAGES, timeout_s=1.0)
    message = str(err.value)
    assert QUESTION not in message
    assert all(p not in message for p in PASSAGES)
    assert KEY not in message


def test_NFR_AVL_004_voyage_timeout_is_an_error_not_a_hang() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("synthetic", request=request)

    with pytest.raises(VoyageRerankError, match="timeout"):
        voyage(handler).rerank(QUESTION, PASSAGES, timeout_s=0.1)
