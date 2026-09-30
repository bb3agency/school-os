"""Contextual retrieval and reranking evaluation (docs/06 §13.5; FR-KB-001, FR-KB-002).

A third small question set (``contextual.jsonl``, generated from ``contextual_cases.py``):
synthetic multi-page circulars in English, Telugu script and code-mixed Latin-script Telugu whose
**titles are reference numbers** (as offices type them) and whose subject is written only on the
first page. Each question names the subject ("the science exhibition") and asks about a later
page ("how much must be paid") whose own text never names it: plain retrieval must tell apart
near-identical payment pages of several circulars, which is what a chunk context situating each
page in its document is for. Some documents are restricted to the principal and are the best
lexical match for questions asked by a teacher: they must never be retrieved nor sent to a
reranker (leakage).

Every question runs through four retrieval **variants** of the system under test:

- ``plain``: contexts off, reranking off (today's default);
- ``contextual``: contexts made at ingestion and searched;
- ``rerank``: contexts off, reranking of the fused candidates on;
- ``contextual_rerank``: both.

Metrics per variant: ``ctx_recall_at_5_<variant>`` (the expected document page in the top 5,
the top 5 being about what the answer model reads) and ``ctx_mrr_at_10_<variant>``; the gains
``ctx_recall_gain_contextual`` (contextual - plain recall@5) and ``ctx_mrr_gain_rerank``
(contextual_rerank - contextual MRR@10); ``ctx_leakage_count`` (questions where any variant
returned a restricted page or sent restricted text to the reranker); per-locale recall in the
outcomes. Pure: no I/O. The harness judges visibility from the dataset (``restricted``), never
from the system under test.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from sos_evals.schema import Locale

Variant = Literal["plain", "contextual", "rerank", "contextual_rerank"]
VARIANTS: Final[tuple[Variant, ...]] = ("plain", "contextual", "rerank", "contextual_rerank")
RECALL_K: Final = 5
MRR_K: Final = 10
_WORD: Final = re.compile(r"[\w\u0c00-\u0c7f]+")
STOPWORDS: Final = frozenset(
    {"the", "for", "and", "what", "when", "where", "does", "must", "how", "much", "which", "who"}
)


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CtxPage(_Model):
    heading: str = ""
    """Empty on the first page (the subject line opens it)."""
    text: str = Field(min_length=20)


class CtxDocument(_Model):
    kind: Literal["document"] = "document"
    id: str = Field(pattern=r"^ctx-(en|te|mx|rs)-[0-9]{2}$")
    locale: Locale
    title: str = Field(min_length=5, max_length=80)
    """What the office typed: a reference number, never the subject."""
    subject: str = Field(min_length=5, max_length=160)
    """Written only on page 1 (``Sub: ...``)."""
    restricted: bool = False
    """Visible only to the principal (the askers are teachers: must never be retrieved)."""
    pages: tuple[CtxPage, ...] = Field(min_length=2)

    def lines(self) -> list[tuple[str, str]]:
        """``(kind, text)`` in document order: ``subject``, ``heading``, ``text``, ``page``."""
        out: list[tuple[str, str]] = []
        for number, page in enumerate(self.pages, start=1):
            if number > 1:
                out.append(("page", ""))
            if page.heading:
                out.append(("heading", page.heading))
            elif number == 1:
                out.append(("subject", self.subject))
            out.append(("text", page.text))
        return out


class CtxQuestion(_Model):
    kind: Literal["question"] = "question"
    id: str = Field(pattern=r"^ctxq-[0-9]{3}$")
    locale: Locale
    question: str = Field(min_length=10, max_length=300)
    topic: str = Field(min_length=3, max_length=80)
    """The subject words the question uses (must be in the document's subject)."""
    document: str
    page: int = Field(ge=1)
    fast: bool = False


class CtxHit(_Model):
    document: str | None
    """The dataset document id, or None for a page the harness does not know."""
    page_from: int | None
    page_to: int | None


class CtxRetrieved(_Model):
    hits: tuple[CtxHit, ...]
    reranked_documents: tuple[str | None, ...] = ()
    """Dataset ids of every passage whose text was sent to a reranker (None = unknown)."""
    latency_ms: float | None = Field(default=None, ge=0)


class ContextualAdapter(Protocol):
    """Optionally also ``prepare_contextual(data: ContextualSet) -> None``: called once before
    the questions (the application adapter stores and indexes the documents there)."""

    name: str

    def retrieve_contextual(self, variant: Variant, question: CtxQuestion, k: int) -> CtxRetrieved:
        """Top ``k`` for the question under the variant's settings, as a teacher of the
        dataset school (every non-restricted document is visible to them)."""
        ...


def words(text: str) -> set[str]:
    folded = unicodedata.normalize("NFC", text).casefold()
    return {w for w in _WORD.findall(folded) if len(w) >= 3 and w not in STOPWORDS}


def needs_context(question: CtxQuestion, document: CtxDocument) -> bool:
    """True when the expected page's own text and heading share none of the topic's words."""
    page = document.pages[question.page - 1]
    return not (words(question.topic) & words(f"{page.heading} {page.text}"))


class CtxOutcome(_Model):
    id: str
    locale: Locale
    needs_context: bool
    ranks: dict[str, int | None]
    """Variant -> 1-based rank of the expected page within the top 10 (None = missed)."""
    leaks: tuple[str, ...]
    latency_ms: dict[str, float | None]


class ContextualMetrics(_Model):
    ctx_items: int = 0
    ctx_recall_at_5_plain: float | None = None
    ctx_recall_at_5_contextual: float | None = None
    ctx_recall_at_5_rerank: float | None = None
    ctx_recall_at_5_contextual_rerank: float | None = None
    ctx_mrr_at_10_plain: float | None = None
    ctx_mrr_at_10_contextual: float | None = None
    ctx_mrr_at_10_rerank: float | None = None
    ctx_mrr_at_10_contextual_rerank: float | None = None
    ctx_recall_gain_contextual: float | None = None
    ctx_mrr_gain_rerank: float | None = None
    ctx_leakage_count: int | None = None


def _hit(hit: CtxHit, question: CtxQuestion) -> bool:
    if hit.document != question.document or hit.page_from is None:
        return False
    return hit.page_from <= question.page <= (hit.page_to or hit.page_from)


def score(
    question: CtxQuestion,
    document: CtxDocument,
    results: dict[Variant, CtxRetrieved],
    restricted: frozenset[str],
) -> CtxOutcome:
    ranks: dict[str, int | None] = {}
    leaks: list[str] = []
    for variant, result in results.items():
        rank = next(
            (i for i, h in enumerate(result.hits[:MRR_K], start=1) if _hit(h, question)), None
        )
        ranks[variant] = rank
        for h in result.hits:
            if h.document is None or h.document in restricted:
                leaks.append(f"{variant}:retrieved:{h.document}")
        for d in result.reranked_documents:
            if d is None or d in restricted:
                leaks.append(f"{variant}:reranker:{d}")
    return CtxOutcome(
        id=question.id,
        locale=question.locale,
        needs_context=needs_context(question, document),
        ranks=ranks,
        leaks=tuple(dict.fromkeys(leaks)),
        latency_ms={v: r.latency_ms for v, r in results.items()},
    )


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def recall(outcomes: Sequence[CtxOutcome], variant: Variant, k: int = RECALL_K) -> float | None:
    got = [1.0 if (r := o.ranks.get(variant)) is not None and r <= k else 0.0 for o in outcomes]
    return _mean(got)


def mrr(outcomes: Sequence[CtxOutcome], variant: Variant) -> float | None:
    got = [1.0 / r if (r := o.ranks.get(variant)) is not None else 0.0 for o in outcomes]
    return _mean(got)


def aggregate(outcomes: Sequence[CtxOutcome]) -> ContextualMetrics:
    if not outcomes:
        return ContextualMetrics()
    values: dict[str, float | int | None] = {"ctx_items": len(outcomes)}
    for v in VARIANTS:
        values[f"ctx_recall_at_5_{v}"] = recall(outcomes, v)
        values[f"ctx_mrr_at_10_{v}"] = mrr(outcomes, v)
    plain, ctx = values["ctx_recall_at_5_plain"], values["ctx_recall_at_5_contextual"]
    both, ctx_mrr = values["ctx_mrr_at_10_contextual_rerank"], values["ctx_mrr_at_10_contextual"]
    if plain is not None and ctx is not None:
        values["ctx_recall_gain_contextual"] = float(ctx) - float(plain)
    if both is not None and ctx_mrr is not None:
        values["ctx_mrr_gain_rerank"] = float(both) - float(ctx_mrr)
    values["ctx_leakage_count"] = sum(1 for o in outcomes if o.leaks)
    return ContextualMetrics.model_validate(values)


def by_locale(outcomes: Sequence[CtxOutcome]) -> dict[str, dict[str, float | None]]:
    """Per locale and variant: recall@5 (the report's table)."""
    table: dict[str, dict[str, float | None]] = {}
    for locale in ("en", "te", "mixed"):
        mine = [o for o in outcomes if o.locale == locale]
        if mine:
            table[locale] = {v: recall(mine, v) for v in VARIANTS}
    return table


class ContextualSet(_Model):
    documents: tuple[CtxDocument, ...]
    questions: tuple[CtxQuestion, ...]

    @model_validator(mode="after")
    def _consistent(self) -> ContextualSet:
        validate(self.documents, self.questions)
        return self

    def select(self, fast: bool) -> tuple[CtxQuestion, ...]:
        return tuple(q for q in self.questions if q.fast) if fast else self.questions

    @property
    def restricted(self) -> frozenset[str]:
        return frozenset(d.id for d in self.documents if d.restricted)

    def document(self, doc_id: str) -> CtxDocument:
        return next(d for d in self.documents if d.id == doc_id)


def validate(documents: Sequence[CtxDocument], questions: Sequence[CtxQuestion]) -> None:
    """The answer key is consistent (a wrong key would hide a miss or a leak)."""
    docs = {d.id: d for d in documents}
    if len(docs) != len(documents):
        raise ValueError("duplicate contextual document id")
    titles = [d.title for d in documents]
    if len(set(titles)) != len(titles):
        raise ValueError("contextual document titles must be unique reference numbers")
    ids: set[str] = set()
    for q in questions:
        if q.id in ids:
            raise ValueError(f"duplicate contextual question {q.id}")
        ids.add(q.id)
        doc = docs.get(q.document)
        if doc is None:
            raise ValueError(f"{q.id}: unknown document {q.document}")
        if doc.restricted:
            raise ValueError(f"{q.id}: the expected document must be visible to the asker")
        if q.page > len(doc.pages):
            raise ValueError(f"{q.id}: page {q.page} is outside the document")
        if not words(q.topic) <= words(q.question) or not words(q.topic) <= words(doc.subject):
            raise ValueError(f"{q.id}: the topic must be in the question and the subject")
        if words(q.topic) & words(doc.title):
            raise ValueError(f"{q.id}: the title must not name the topic")


def run(
    data: ContextualSet, questions: Iterable[CtxQuestion], adapter: ContextualAdapter
) -> tuple[ContextualMetrics, tuple[CtxOutcome, ...]]:
    outcomes = []
    restricted = data.restricted
    prepare = getattr(adapter, "prepare_contextual", None)
    if callable(prepare):
        prepare(data)
    for q in questions:
        results = {v: adapter.retrieve_contextual(v, q, MRR_K) for v in VARIANTS}
        outcomes.append(score(q, data.document(q.document), results, restricted))
    return aggregate(outcomes), tuple(outcomes)


__all__ = [
    "MRR_K",
    "RECALL_K",
    "VARIANTS",
    "ContextualAdapter",
    "ContextualMetrics",
    "ContextualSet",
    "CtxDocument",
    "CtxHit",
    "CtxOutcome",
    "CtxPage",
    "CtxQuestion",
    "CtxRetrieved",
    "Variant",
    "aggregate",
    "by_locale",
    "needs_context",
    "run",
    "score",
    "validate",
    "words",
]
