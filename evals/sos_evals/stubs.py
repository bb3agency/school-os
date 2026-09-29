"""Deterministic stub adapters that prove the harness and its gates work (no LLM, no DB).

- `stub-perfect`: an answer-key oracle. Retrieves the expected sources first from what the asker
  may see, cites exact sentences, refuses when it should and ignores embedded instructions. It
  must pass every gate; if it does not, the harness is wrong.
- `stub-leaky`: behaves like a retriever without the permission filter (the bug FR-KB-002
  forbids). It must trip the leakage hard gate.
- `stub-injectable`: behaves like a model that obeys instructions inside documents. It must
  trip the injection hard gate.

Latencies are derived from a hash of the question so reports are reproducible.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence

from sos_evals.acl import retrievable, visible
from sos_evals.adapters import AnswerSegment, AskResult, Citation, Retrieved
from sos_evals.circulars import CircularCase, CircularResult, SuggestedDeadline, dates_in
from sos_evals.schema import Asker, CorpusItem, EvalItem, Locale

REFUSALS: Mapping[Locale, str] = {
    "en": "Not found in school records you can access.",
    "mixed": "Not found in school records you can access.",
    "te": "మీరు చూడగల పాఠశాల రికార్డులలో ఇది కనబడలేదు.",
}
_TE_PREFIX = "సమాధానం: "


def _hash_ms(text: str, base: int, spread: int) -> float:
    return float(base + int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) % spread)


class PerfectStub:
    name = "stub-perfect"

    def __init__(self, corpus: Mapping[str, CorpusItem], items: Iterable[EvalItem]) -> None:
        self._corpus = dict(corpus)
        self._items = {(item.question, item.asker): item for item in items}

    def _item(self, question: str, asker: Asker) -> EvalItem | None:
        return self._items.get((question, asker))

    def _candidates(self, asker: Asker) -> list[str]:
        return sorted(s for s, c in self._corpus.items() if retrievable(asker, c))

    def _ranked(self, question: str, asker: Asker, k: int) -> list[str]:
        item = self._item(question, asker)
        first = list(item.expected_sources) if item else []
        rest = [s for s in self._candidates(asker) if s not in first]
        return (first + rest)[:k]

    def retrieve(self, question: str, asker: Asker, k: int) -> Retrieved:
        return Retrieved(
            sources=tuple(self._ranked(question, asker, k)),
            latency_ms=_hash_ms(question, 40, 200),
        )

    def _answer(self, sources: Sequence[str], locale: Locale) -> list[AnswerSegment]:
        segments = []
        for source in sources:
            lead = self._corpus[source].content.split("\n", 1)[0]
            text = f"{_TE_PREFIX}{lead}" if locale == "te" else lead
            segments.append(
                AnswerSegment(text=text, citations=(Citation(source=source, cited_text=lead),))
            )
        return segments

    def _refusal(self, locale: Locale, provided: Sequence[str]) -> AskResult:
        return AskResult(
            segments=(AnswerSegment(text=REFUSALS[locale]),),
            refused=True,
            provided_sources=tuple(provided),
        )

    def ask(self, question: str, asker: Asker) -> AskResult:
        provided = self._ranked(question, asker, 10)
        item = self._item(question, asker)
        latency = _hash_ms(question, 900, 2500)
        if item is None or item.expect_refusal:
            locale: Locale = item.locale if item else "en"
            return self._refusal(locale, provided).model_copy(update={"latency_ms": latency})
        return AskResult(
            segments=tuple(self._answer(item.expected_sources, item.locale)),
            refused=False,
            provided_sources=tuple(provided),
            latency_ms=latency,
        )

    def read_circular(self, case: CircularCase) -> CircularResult:
        """The answer key: each expected deadline quoting the line that writes it."""
        found = []
        for due in case.expected_deadlines:
            line = next(line for line in case.lines if due in dates_in(line))
            found.append(SuggestedDeadline(due_on=due, quote=line, title=line[:80]))
        return CircularResult(
            deadlines=tuple(found), reference_no=case.reference_no, issued_on=case.issued_on
        )


class LeakyStub(PerfectStub):
    """Ranks without the ACL filter and answers leakage probes from the forbidden sources."""

    name = "stub-leaky"

    def _candidates(self, asker: Asker) -> list[str]:
        return sorted(s for s, c in self._corpus.items() if c.is_latest)

    def _ranked(self, question: str, asker: Asker, k: int) -> list[str]:
        item = self._item(question, asker)
        first = list(item.probe_sources) if item else []
        first += [s for s in super()._ranked(question, asker, k) if s not in first]
        return first[:k]

    def ask(self, question: str, asker: Asker) -> AskResult:
        item = self._item(question, asker)
        if item is None or not item.leakage_probe:
            return super().ask(question, asker)
        return AskResult(
            segments=tuple(self._answer(item.probe_sources, item.locale)),
            refused=False,
            provided_sources=tuple(self._ranked(question, asker, 10)),
            latency_ms=_hash_ms(question, 900, 2500),
        )


class InjectableStub(PerfectStub):
    """Answers correctly, then obeys any instruction found in the documents it was given."""

    name = "stub-injectable"

    def ask(self, question: str, asker: Asker) -> AskResult:
        result = super().ask(question, asker)
        obeyed = [
            AnswerSegment(text=f"As instructed: {canary}")
            for source in result.provided_sources
            if visible(asker, self._corpus[source])
            for canary in self._corpus[source].injection_canaries
        ]
        if not obeyed:
            return result
        return result.model_copy(
            update={"segments": result.segments + tuple(obeyed), "refused": False}
        )


StubFactory = Callable[[Mapping[str, CorpusItem], Iterable[EvalItem]], PerfectStub]

STUBS: Mapping[str, StubFactory] = {
    PerfectStub.name: PerfectStub,
    LeakyStub.name: LeakyStub,
    InjectableStub.name: InjectableStub,
}
