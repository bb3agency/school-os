"""Embeddings package (K2): fake provider, TenantEmbedder, caches, provider selection.

docs/06 §4.6 and §12, ADR-0006. Offline only: no network, no database (the session is an
opaque token passed through to the cache). All texts are synthetic.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import ValidationError

from app.core.config import KnowledgeProviderMode
from app.knowledge.config.embeddings import Candidate, EmbeddingsConfig, load_embeddings_config
from app.knowledge.domain import InputType
from app.knowledge.embeddings import (
    CachingTenantEmbedder,
    EmbeddingCache,
    EmbeddingDimensionError,
    EmbeddingsNotConfiguredError,
    FakeEmbeddingsProvider,
    InMemoryEmbeddingCache,
    batches,
    content_sha256,
    select_embeddings_provider,
)
from app.knowledge.interfaces import EmbeddingsProvider, TenantEmbedder

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

SESSION = cast("Session", object())
TENANT_A = uuid.UUID("00000000-0000-7000-8000-00000000000a")
TENANT_B = uuid.UUID("00000000-0000-7000-8000-00000000000b")


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def config(**overrides: Any) -> EmbeddingsConfig:
    data = load_embeddings_config().model_dump()
    for dotted, value in overrides.items():
        node = data
        *path, last = dotted.split("__")
        for part in path:
            node = node[part]
        node[last] = value
    return EmbeddingsConfig.model_validate(data)


class Recording:
    """Wraps a provider; records each call's batch and can fail the first N calls."""

    def __init__(
        self,
        inner: EmbeddingsProvider | None = None,
        *,
        failures: Sequence[BaseException] = (),
        dimensions: int | None = None,
    ) -> None:
        self.inner = inner or FakeEmbeddingsProvider(model="sos-fake-test-v1", dimensions=1024)
        self.calls: list[tuple[list[str], str]] = []
        self.failures = list(failures)
        self._dimensions = dimensions

    @property
    def name(self) -> str:
        return "recording"

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def dimensions(self) -> int:
        return self._dimensions or self.inner.dimensions

    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        self.calls.append((list(texts), input_type))
        if self.failures:
            raise self.failures.pop(0)
        return self.inner.embed(texts, input_type)

    @property
    def texts_sent(self) -> list[str]:
        return [t for batch, _ in self.calls for t in batch]


class Transient(Exception):
    retryable = True

    def __init__(self, retry_after_s: float | None = None) -> None:
        super().__init__("transient")
        self.retry_after_s = retry_after_s


def embedder(
    provider: EmbeddingsProvider,
    cfg: EmbeddingsConfig | None = None,
    cache: EmbeddingCache | None = None,
    *,
    sleeps: list[float] | None = None,
    clock: list[float] | None = None,
) -> CachingTenantEmbedder:
    now = clock if clock is not None else [0.0]
    return CachingTenantEmbedder(
        provider,
        cfg or config(),
        cache if cache is not None else InMemoryEmbeddingCache(),
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
        clock=lambda: now[0],
        jitter=lambda: 1.0,
    )


# --- fake provider ---------------------------------------------------------------------------


def test_FR_KB_001_fake_satisfies_the_provider_protocol() -> None:
    fake = FakeEmbeddingsProvider(model="sos-fake-test-v1", dimensions=1024)
    assert isinstance(fake, EmbeddingsProvider)
    assert fake.name == "fake"
    assert fake.dimensions == 1024


def test_FR_KB_001_fake_is_deterministic_and_unit_length() -> None:
    texts = ["Fee payment due by 15/07 for class 9.", "ఫీజు చెల్లింపు గడువు 15/07"]
    a = FakeEmbeddingsProvider(model="m-one", dimensions=1024).embed(texts, "document")
    b = FakeEmbeddingsProvider(model="m-one", dimensions=1024).embed(texts, "query")
    assert a == b
    for vector in a:
        assert len(vector) == 1024
        assert math.isclose(math.sqrt(sum(x * x for x in vector)), 1.0, rel_tol=1e-9)


def test_FR_KB_001_fake_pins_a_known_vector() -> None:
    """Stable across processes and Python versions (blake2b, no hash salting)."""
    vector = FakeEmbeddingsProvider(model="m-one", dimensions=1024).embed(["circular"], "query")[0]
    nonzero = [i for i, x in enumerate(vector) if x != 0.0]
    assert len(nonzero) >= 5
    again = FakeEmbeddingsProvider(model="m-one", dimensions=1024).embed(["Circular"], "query")[0]
    assert again == vector  # case-folded


@pytest.mark.parametrize(
    ("anchor", "near", "far"),
    [
        (
            "Fee payment due date for class 9 students",
            "fee payment due date for class 9",
            "Annual sports day schedule announced",
        ),
        (
            "పదవ తరగతి పరీక్షల షెడ్యూల్ విడుదల",
            "పదవ తరగతి పరీక్షల షెడ్యూల్",
            "పాఠశాల బస్సు మార్గం మార్పు",
        ),
        (
            "Class 10 exam schedule circular",
            "class 10 exam schedule",
            "School bus route changed from Monday",
        ),
    ],
)
def test_FR_KB_001_fake_places_similar_texts_near(anchor: str, near: str, far: str) -> None:
    fake = FakeEmbeddingsProvider(model="m-one", dimensions=1024)
    a, n, f = fake.embed([anchor, near, far], "document")
    assert cosine(a, n) > 0.75
    assert cosine(a, f) < 0.3
    assert cosine(a, n) > cosine(a, f) + 0.4


def test_fake_handles_text_without_words() -> None:
    (vector,) = FakeEmbeddingsProvider(model="m-one", dimensions=1024).embed(["-- // --"], "query")
    assert math.isclose(sum(x * x for x in vector), 1.0)


# --- batching ----------------------------------------------------------------------------------


def test_batches_split_by_count_and_characters_in_order() -> None:
    texts = ["a" * 10, "b" * 10, "c" * 10, "d" * 50, "e" * 5]
    got = list(batches(texts, max_texts=2, max_chars=40))
    assert got == [["a" * 10, "b" * 10], ["c" * 10], ["d" * 50], ["e" * 5]]
    assert [t for b in got for t in b] == texts


def test_FR_KB_001_embedder_batches_misses_by_config() -> None:
    provider = Recording()
    cfg = config(batching__max_texts=3, batching__max_chars=100_000)
    texts = [f"Synthetic circular number {i}" for i in range(8)]
    vectors = embedder(provider, cfg).embed(SESSION, TENANT_A, texts, "document")
    assert [len(batch) for batch, _ in provider.calls] == [3, 3, 2]
    assert vectors == provider.inner.embed(texts, "document")


def test_FR_KB_001_embedder_batches_by_characters() -> None:
    provider = Recording()
    cfg = config(batching__max_texts=100, batching__max_chars=1000)
    texts = ["x" * 400 + str(i) for i in range(5)]
    embedder(provider, cfg).embed(SESSION, TENANT_A, texts, "document")
    assert [len(batch) for batch, _ in provider.calls] == [2, 2, 1]


def test_embedder_embeds_duplicates_once_and_keeps_order() -> None:
    provider = Recording()
    texts = ["Holiday on Friday", "Fee reminder", "Holiday on Friday"]
    vectors = embedder(provider).embed(SESSION, TENANT_A, texts, "document")
    assert provider.texts_sent == ["Holiday on Friday", "Fee reminder"]
    assert vectors[0] == vectors[2] != vectors[1]


def test_embedder_satisfies_the_tenant_embedder_protocol() -> None:
    e = embedder(Recording())
    assert isinstance(e, TenantEmbedder)
    assert e.model == "sos-fake-test-v1"
    assert e.embed(SESSION, TENANT_A, [], "query") == []


@pytest.mark.parametrize("bad", ["", "   "])
def test_embedder_refuses_blank_texts(bad: str) -> None:
    with pytest.raises(ValueError, match="non-blank"):
        embedder(Recording()).embed(SESSION, TENANT_A, ["ok", bad], "document")


# --- per-tenant document cache ---------------------------------------------------------------


def test_LLM08_document_cache_hits_by_sha256_within_a_tenant() -> None:
    provider = Recording()
    cache = InMemoryEmbeddingCache()
    e = embedder(provider, cache=cache)
    first = e.embed(SESSION, TENANT_A, ["Minutes of the PTA meeting"], "document")
    second = e.embed(SESSION, TENANT_A, ["Minutes of the PTA meeting", "New text"], "document")
    assert provider.texts_sent == ["Minutes of the PTA meeting", "New text"]
    assert second[0] == first[0]
    stored = cache.get_many(
        SESSION, TENANT_A, provider.model, [content_sha256("Minutes of the PTA meeting")]
    )
    assert list(stored) == [content_sha256("Minutes of the PTA meeting")]


def test_LLM08_document_cache_is_never_shared_across_tenants() -> None:
    provider = Recording()
    cache = InMemoryEmbeddingCache()
    e = embedder(provider, cache=cache)
    e.embed(SESSION, TENANT_A, ["Same synthetic circular"], "document")
    e.embed(SESSION, TENANT_B, ["Same synthetic circular"], "document")
    assert provider.texts_sent == ["Same synthetic circular", "Same synthetic circular"]
    assert len(cache) == 2


def test_document_cache_is_keyed_by_model() -> None:
    cache = InMemoryEmbeddingCache()
    one = Recording(FakeEmbeddingsProvider(model="model-one", dimensions=1024))
    two = Recording(FakeEmbeddingsProvider(model="model-two", dimensions=1024))
    embedder(one, cache=cache).embed(SESSION, TENANT_A, ["Timetable"], "document")
    embedder(two, cache=cache).embed(SESSION, TENANT_A, ["Timetable"], "document")
    assert two.texts_sent == ["Timetable"]


def test_queries_do_not_use_or_fill_the_document_cache() -> None:
    provider = Recording()
    cache = InMemoryEmbeddingCache()
    e = embedder(provider, cache=cache)
    e.embed(SESSION, TENANT_A, ["When is the exam?"], "document")
    e.embed(SESSION, TENANT_A, ["When is the exam?"], "query")
    assert len(provider.calls) == 2
    assert len(cache) == 1


class WrongCache:
    def get_many(self, *args: object) -> dict[bytes, list[float]]:
        return {content_sha256("Poisoned"): [0.5] * 512}

    def put_many(self, *args: object) -> None:
        raise AssertionError("not reached")


def test_cached_vectors_are_dimension_checked() -> None:
    e = embedder(Recording(), cache=cast("EmbeddingCache", WrongCache()))
    with pytest.raises(EmbeddingDimensionError):
        e.embed(SESSION, TENANT_A, ["Poisoned"], "document")


# --- query cache (600 s) ---------------------------------------------------------------------


def test_query_cache_hits_within_ttl_and_expires_after() -> None:
    provider = Recording()
    clock = [1000.0]
    e = embedder(provider, clock=clock)
    assert load_embeddings_config().query_cache_ttl_s == 600
    first = e.embed(SESSION, TENANT_A, ["Who is the class teacher of 9A?"], "query")
    clock[0] += 599
    again = e.embed(SESSION, TENANT_A, ["Who is the class teacher of 9A?"], "query")
    assert len(provider.calls) == 1
    assert again == first
    clock[0] += 2
    e.embed(SESSION, TENANT_A, ["Who is the class teacher of 9A?"], "query")
    assert len(provider.calls) == 2


def test_LLM08_query_cache_is_per_tenant() -> None:
    provider = Recording()
    e = embedder(provider)
    e.embed(SESSION, TENANT_A, ["Fee due date?"], "query")
    e.embed(SESSION, TENANT_B, ["Fee due date?"], "query")
    assert len(provider.calls) == 2


def test_query_cache_is_bounded() -> None:
    provider = Recording()
    e = embedder(provider, config(query_cache_max_entries=2))
    for text in ("q one", "q two", "q three", "q one"):
        e.embed(SESSION, TENANT_A, [text], "query")
    assert provider.texts_sent == ["q one", "q two", "q three", "q one"]


def test_returned_vectors_are_copies() -> None:
    e = embedder(Recording())
    v = e.embed(SESSION, TENANT_A, ["Mutable?"], "query")[0]
    v[0] = 99.0
    assert e.embed(SESSION, TENANT_A, ["Mutable?"], "query")[0][0] != 99.0


# --- dimension guard ---------------------------------------------------------------------------


def test_FR_KB_001_provider_dimensions_must_equal_storage() -> None:
    small = FakeEmbeddingsProvider(model="sos-fake-test-v1", dimensions=512)
    with pytest.raises(EmbeddingDimensionError, match="re-embedding migration"):
        embedder(small)


def test_FR_KB_001_returned_vectors_must_have_storage_dimensions() -> None:
    liar = Recording(FakeEmbeddingsProvider(model="sos-fake-test-v1", dimensions=512))
    liar._dimensions = 1024  # claims 1024, returns 512
    cache = InMemoryEmbeddingCache()
    with pytest.raises(EmbeddingDimensionError):
        embedder(liar, cache=cache).embed(SESSION, TENANT_A, ["text"], "document")
    assert len(cache) == 0


class ShortCount(Recording):
    def embed(self, texts: Sequence[str], input_type: InputType) -> list[list[float]]:
        return super().embed(texts, input_type)[:-1]


def test_provider_must_return_one_vector_per_text() -> None:
    with pytest.raises(EmbeddingDimensionError, match="vectors for"):
        embedder(ShortCount()).embed(SESSION, TENANT_A, ["one", "two"], "document")


# --- retries -----------------------------------------------------------------------------------


def test_transient_failures_are_retried_with_capped_backoff() -> None:
    provider = Recording(failures=[Transient(), TimeoutError(), ConnectionError()])
    sleeps: list[float] = []
    cfg = config(retry__max_attempts=4, retry__base_delay_s=0.5, retry__max_delay_s=1.5)
    vectors = embedder(provider, cfg, sleeps=sleeps).embed(SESSION, TENANT_A, ["t"], "document")
    assert len(vectors) == 1
    assert len(provider.calls) == 4
    assert sleeps == [0.5, 1.0, 1.5]  # jitter pinned at 1.0; capped at max_delay_s


def test_retry_after_is_honoured_up_to_the_cap() -> None:
    provider = Recording(failures=[Transient(retry_after_s=3.0), Transient(retry_after_s=60)])
    sleeps: list[float] = []
    cfg = config(retry__base_delay_s=0.5, retry__max_delay_s=8.0)
    embedder(provider, cfg, sleeps=sleeps).embed(SESSION, TENANT_A, ["t"], "document")
    assert sleeps == [3.0, 8.0]


def test_retries_give_up_after_max_attempts() -> None:
    provider = Recording(failures=[Transient()] * 5)
    sleeps: list[float] = []
    cache = InMemoryEmbeddingCache()
    with pytest.raises(Transient):
        embedder(provider, config(retry__max_attempts=3), cache, sleeps=sleeps).embed(
            SESSION, TENANT_A, ["t"], "document"
        )
    assert len(provider.calls) == 3
    assert len(sleeps) == 2
    assert len(cache) == 0


def test_permanent_failures_are_not_retried() -> None:
    provider = Recording(failures=[ValueError("bad request")])
    sleeps: list[float] = []
    with pytest.raises(ValueError, match="bad request"):
        embedder(provider, sleeps=sleeps).embed(SESSION, TENANT_A, ["t"], "document")
    assert len(provider.calls) == 1
    assert sleeps == []


# --- no text in logs (invariant 5) -----------------------------------------------------------


def test_SEC_008_no_text_in_logs(capsys: pytest.CaptureFixture[str]) -> None:
    secret = "Ravi Kumar Synthetic, DOB 01/02/2012, ph 9876543210"
    provider = Recording(failures=[Transient()])
    capsys.readouterr()
    embedder(provider).embed(SESSION, TENANT_A, [secret], "document")
    with pytest.raises(ValueError, match="Ravi"):  # the caller gets it; the log must not
        embedder(Recording(failures=[ValueError(secret)])).embed(
            SESSION, TENANT_A, [secret + " again"], "query"
        )
    out = capsys.readouterr()
    logged = out.out + out.err
    assert "kb.embeddings.retry" in logged  # the lines exist...
    assert "kb.embeddings.embedded" in logged
    assert "kb.embeddings.failed" in logged
    for fragment in ("Ravi", "Kumar", "01/02/2012", "9876543210", content_sha256(secret).hex()):
        assert fragment not in logged  # ...and carry no text or digest


# --- provider selection --------------------------------------------------------------------------


def selected(provider: str = "voyage", model: str | None = "synthetic-embed-1") -> EmbeddingsConfig:
    data = load_embeddings_config().model_dump()
    key = next(k for k, c in data["candidates"].items() if c["provider"] == provider)
    data["candidates"][key]["model"] = model
    data["selected"] = key
    return EmbeddingsConfig.model_validate(data)


def test_SEC_020_fake_mode_selects_the_offline_fake_whatever_is_selected() -> None:
    def boom(candidate: Candidate) -> EmbeddingsProvider:
        raise AssertionError("network factory must not be called in fake mode")

    for cfg in (load_embeddings_config(), selected()):
        provider = select_embeddings_provider(
            KnowledgeProviderMode.FAKE, cfg, network={"voyage": boom}
        )
        assert isinstance(provider, FakeEmbeddingsProvider)
        assert provider.model == cfg.fake.model
        assert provider.dimensions == cfg.storage.dimensions == 1024


def test_FR_KB_001_live_mode_refuses_until_adr_0006_selects_a_model() -> None:
    cfg = load_embeddings_config()
    assert cfg.selected is None
    with pytest.raises(EmbeddingsNotConfiguredError, match="ADR-0006"):
        select_embeddings_provider(KnowledgeProviderMode.LIVE, cfg, network={})


def test_live_mode_uses_the_factory_for_the_selected_provider() -> None:
    seen: list[Candidate] = []

    def factory(candidate: Candidate) -> EmbeddingsProvider:
        seen.append(candidate)
        assert candidate.model is not None
        return FakeEmbeddingsProvider(model=candidate.model, dimensions=candidate.dimensions)

    provider = select_embeddings_provider(
        KnowledgeProviderMode.LIVE, selected(), network={"voyage": factory}
    )
    assert provider.model == "synthetic-embed-1"
    assert [c.provider for c in seen] == ["voyage"]


def test_live_mode_refuses_a_provider_without_an_implementation() -> None:
    with pytest.raises(EmbeddingsNotConfiguredError, match="self_hosted"):
        select_embeddings_provider(
            KnowledgeProviderMode.LIVE, selected("self_hosted"), network={"voyage": _never}
        )


def test_live_mode_refuses_a_factory_that_disagrees_with_the_candidate() -> None:
    def wrong(candidate: Candidate) -> EmbeddingsProvider:
        return FakeEmbeddingsProvider(model="another-model", dimensions=1024)

    with pytest.raises(EmbeddingsNotConfiguredError, match="does not match"):
        select_embeddings_provider(
            KnowledgeProviderMode.LIVE, selected(), network={"voyage": wrong}
        )


def _never(candidate: Candidate) -> EmbeddingsProvider:
    raise AssertionError("not reached")


# --- embeddings.yaml additions (K2) ------------------------------------------------------------


def test_shipped_request_shaping_and_retry_policy() -> None:
    cfg = load_embeddings_config()
    assert cfg.batching.max_texts <= 1000  # Voyage per-request limit
    assert cfg.retry.max_attempts >= 2
    assert cfg.voyage.endpoint.startswith("https://")
    assert cfg.fake.model not in {c.model for c in cfg.candidates.values()}


@pytest.mark.parametrize(
    ("dotted", "value", "match"),
    [
        ("retry__max_delay_s", 0.1, "max_delay_s"),
        ("voyage__endpoint", "http://api.voyageai.com/v1/embeddings", "endpoint"),
        ("batching__max_texts", 5000, "max_texts"),
        ("fake__model", "Not A Model ID", "model"),
    ],
)
def test_invalid_embeddings_settings_are_refused(dotted: str, value: object, match: str) -> None:
    with pytest.raises(ValidationError, match=match):
        config(**{dotted: value})
