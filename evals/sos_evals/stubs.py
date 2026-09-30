"""Deterministic stub adapters that prove the harness and its gates work (no LLM, no DB).

- `stub-perfect`: an answer-key oracle. Retrieves the expected sources first from what the asker
  may see, cites exact sentences, refuses when it should and ignores embedded instructions. It
  must pass every gate; if it does not, the harness is wrong.
- `stub-leaky`: behaves like a retriever without the permission filter (the bug FR-KB-002
  forbids) and a chat history shared by everyone. It must trip the leakage hard gates.
- `stub-injectable`: behaves like a model that obeys instructions inside documents. It must
  trip the injection hard gate.
- `stub-telugu`: ignores the Telugu switch (``SOS_TELUGU_ENABLED``, ADR-0036) and keeps writing
  Telugu for Telugu questions, circulars and notices. It must trip the English-first hard gates.

English first (ADR-0036): every stub follows ``set_telugu``. With Telugu off, `stub-perfect`
writes English only: "not found" in English, a Telugu passage described in English (and cited,
not copied), English follow-ups, deadline titles and summaries, an English-only notice.

Contextual retrieval set (docs/06 §13.6): `stub-perfect` is perfect *within each variant's
information*: with contexts (``contextual``, ``contextual_rerank``) it ranks the expected page
first; without them (``plain``, ``rerank``) a question about a page whose own text never names
its subject is missed (nothing on the page can match it), any other is ranked first. So its
contextual gain is the share of such questions and its rerank gain 0. It never returns or reranks
a restricted document; `stub-leaky` returns and "reranks" them first (must trip the hard gate).

Latencies are derived from a hash of the question so reports are reproducible.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence

from sos_evals.acl import retrievable, visible
from sos_evals.adapters import AnswerSegment, AskResult, Citation, Retrieved
from sos_evals.circulars import CircularCase, CircularResult, SuggestedDeadline, dates_in
from sos_evals.contextual import (
    ContextualSet,
    CtxHit,
    CtxQuestion,
    CtxRetrieved,
    Variant,
    needs_context,
)
from sos_evals.conversations import ConversationCase, ConversationRun, TurnResult, script_of
from sos_evals.english_first import NoticeResult, has_telugu
from sos_evals.fees import FeeCase, FeeCitation, FeeResult, inr
from sos_evals.schema import Asker, CorpusItem, EvalItem, Locale

REFUSALS: Mapping[Locale, str] = {
    "en": "Not found in school records you can access.",
    "mixed": "Not found in school records you can access.",
    "te": "మీరు చూడగల పాఠశాల రికార్డులలో ఇది కనబడలేదు.",
}
_TE_PREFIX = "సమాధానం: "
EN_STAND_IN = "The cited passage from the school's records answers this."
"""What an English-first answer says instead of copying a Telugu passage (it cites it)."""


def _hash_ms(text: str, base: int, spread: int) -> float:
    return float(base + int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) % spread)


class _ConversationOracle:
    """The answer key for scripted conversations: cites what each step expects, writes in the
    expected script, suggests follow-ups in the question's script, saves only what may be saved,
    reuses only an allowed exact repeat, and sends the model nothing but the step's own question
    and the asker's saved memory while it is on."""

    telugu = True
    """``SOS_TELUGU_ENABLED`` (ADR-0036): the harness runs the main pass with it on."""

    def set_telugu(self, enabled: bool) -> None:
        self.telugu = enabled

    def _say(self, line: str) -> str:
        """``line`` as shown: while Telugu is hidden a Telugu line is described in English."""
        return EN_STAND_IN if not self.telugu and has_telugu(line) else line

    def run_conversation(self, case: ConversationCase) -> ConversationRun:
        docs = {d.key: d for d in case.docs}
        memory_on = {p.key: True for p in case.people}
        saved: dict[str, list[str]] = {p.key: [] for p in case.people}
        first_asked: dict[str, int] = {}
        turns: list[TurnResult] = []
        for index, step in enumerate(case.steps):
            if step.action == "memory_off":
                memory_on[step.who] = False
                continue
            if step.action in ("revoke", "revise", "delete_conversation"):
                continue
            if step.action == "remember":
                note = (step.question or "").split(" that ", 1)[-1]
                memory: tuple[tuple[str, str], ...] = ()
                if step.expect_memory == "saved" and memory_on[step.who]:
                    saved[step.who].append(note)
                    memory = (("saved", note),)
                turns.append(
                    TurnResult(
                        step=index,
                        text="Saved to your memory." if memory else REFUSALS["en"],
                        model_input=note,
                        memory=memory,
                        memories_after=tuple(saved[step.who]),
                    )
                )
                continue
            target = case.steps[step.target] if step.target is not None else None
            question = step.question or (target.question if target else "") or ""
            script = step.expect_script or script_of(question)
            if not self.telugu:
                script = "latin"  # ADR-0036: English whatever the question or preference
            lines = [docs[key].content.split(". ")[0] for key in step.expect_sources]
            text = " ".join(
                f"{_TE_PREFIX}{line}" if script == "te" else self._say(line) for line in lines
            )
            telugu_question = script_of(question) == "te" and self.telugu
            follow = "దీని గురించి మరిన్ని వివరాలు?" if telugu_question else "Anything else?"
            used = saved[step.who] if memory_on[step.who] else []
            cached = first_asked.get(question) if step.expect_cached else None
            first_asked.setdefault(question, index)
            turns.append(
                TurnResult(
                    step=index,
                    text=text or REFUSALS["en"],
                    citations=step.expect_sources,
                    provided=step.expect_sources,
                    model_input="\n".join([*used, question]),
                    followups=(follow,),
                    cached_from=cached,
                    memories_after=tuple(saved[step.who]),
                    title=None if self.telugu else "New conversation",
                    language=None if self.telugu else "en",
                )
            )
        return ConversationRun(turns=tuple(turns))


class PerfectStub(_ConversationOracle):
    name = "stub-perfect"

    def __init__(self, corpus: Mapping[str, CorpusItem], items: Iterable[EvalItem]) -> None:
        self._corpus = dict(corpus)
        self._items = {(item.question, item.asker): item for item in items}

    _ctx: ContextualSet | None = None

    def prepare_contextual(self, data: ContextualSet) -> None:
        self._ctx = data

    def _ctx_ranked(self, variant: Variant, question: CtxQuestion) -> list[CtxHit]:
        data = self._ctx
        if data is None:
            return []
        document = data.document(question.document)
        hits = []
        if variant.startswith("contextual") or not needs_context(question, document):
            hits.append(
                CtxHit(document=document.id, page_from=question.page, page_to=question.page)
            )
        for other in data.documents:
            if other.restricted or other.id == document.id:
                continue
            hits.append(CtxHit(document=other.id, page_from=question.page, page_to=question.page))
        return hits

    def retrieve_contextual(self, variant: Variant, question: CtxQuestion, k: int) -> CtxRetrieved:
        hits = self._ctx_ranked(variant, question)[:k]
        sent = tuple(h.document for h in hits) if variant.endswith("rerank") else ()
        return CtxRetrieved(
            hits=tuple(hits),
            reranked_documents=sent,
            latency_ms=_hash_ms(f"{variant}:{question.id}", 30, 100),
        )

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
            text = f"{_TE_PREFIX}{lead}" if locale == "te" and self.telugu else self._say(lead)
            segments.append(
                AnswerSegment(text=text, citations=(Citation(source=source, cited_text=lead),))
            )
        return segments

    def _language(self, locale: Locale) -> str:
        return locale if self.telugu else "en"

    def _refusal(self, locale: Locale, provided: Sequence[str]) -> AskResult:
        return AskResult(
            segments=(AnswerSegment(text=REFUSALS[locale if self.telugu else "en"]),),
            refused=True,
            provided_sources=tuple(provided),
            language=self._language(locale),
        )

    def ask(self, question: str, asker: Asker) -> AskResult:
        provided = self._ranked(question, asker, 10)
        item = self._item(question, asker)
        latency = _hash_ms(question, 900, 2500)
        if item is None or item.expect_refusal:
            locale: Locale = item.locale if item else "en"
            return self._refusal(locale, provided).model_copy(update={"latency_ms": latency})
        english = "Anything else in the school's records about this?"
        return AskResult(
            segments=tuple(self._answer(item.expected_sources, item.locale)),
            refused=False,
            provided_sources=tuple(provided),
            latency_ms=latency,
            language=self._language(item.locale),
            followups=() if self.telugu else (english,),
            title=None if self.telugu else "New conversation",
        )

    def read_circular(self, case: CircularCase) -> CircularResult:
        """The answer key: each expected deadline quoting the line that writes it (English
        titles and an English summary while Telugu is hidden)."""
        found = []
        for due in case.expected_deadlines:
            line = next(line for line in case.lines if due in dates_in(line))
            title = line[:80]
            if not self.telugu and has_telugu(title):
                title = f"Action needed by {due:%d/%m/%Y}"
            found.append(SuggestedDeadline(due_on=due, quote=line, title=title))
        summary = None if self.telugu else f"The circular sets {len(found)} deadline(s)."
        return CircularResult(
            deadlines=tuple(found),
            reference_no=case.reference_no,
            issued_on=case.issued_on,
            summary_en=summary,
        )

    def draft_notice(self, case: CircularCase) -> NoticeResult:
        """An English notice listing the confirmed dates (bilingual while Telugu is on)."""
        dates = ", ".join(f"{d:%d/%m/%Y}" for d in case.expected_deadlines) or "see the notice"
        body = f"Dear parents, please note these dates: {dates}."
        if not self.telugu:
            return NoticeResult(title_en="Notice for parents", body_en=body)
        return NoticeResult(
            title_en="Notice for parents",
            body_en=body,
            title_te="తల్లిదండ్రులకు సూచన",
            body_te=f"దయచేసి ఈ తేదీలను గమనించండి: {dates}.",
        )

    @staticmethod
    def _fee_answer(case: FeeCase, lines: list[str]) -> FeeResult:
        source = f"sos://fee/{uuid.uuid5(uuid.NAMESPACE_URL, case.id)}"
        said = [f"{_TE_PREFIX}{line}" if case.locale == "te" else line for line in lines]
        return FeeResult(
            text=" ".join(said),
            citations=tuple(FeeCitation(source=source, cited_text=line) for line in lines),
            provided_sources=(source,),
        )

    def ask_fees(self, case: FeeCase) -> FeeResult:
        """The answer key: the exact figure from the linked ledgers, or no figure at all."""
        if case.expect_refusal or case.expected_total is None:
            return FeeResult(text=REFUSALS[case.locale])
        return self._fee_answer(
            case, [f"Fee due from Tally: {inr(case.expected_total)} (linked ledgers only)."]
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

    def _ctx_ranked(self, variant: Variant, question: CtxQuestion) -> list[CtxHit]:
        """No permission filter: the restricted memos come first."""
        data = self._ctx
        forbidden = [
            CtxHit(document=d.id, page_from=question.page, page_to=question.page)
            for d in (data.documents if data else ())
            if d.restricted
        ]
        return forbidden + super()._ctx_ranked(variant, question)

    def ask_fees(self, case: FeeCase) -> FeeResult:
        """Maps ledgers by name and ignores who may see fees: states a figure every time."""
        target = next((s for s in case.students if s.key == case.student), None)
        first = target.name.split()[0].casefold() if target else ""
        guessed = [
            ledger for ledger in case.ledgers if first and first in ledger.name.casefold()
        ] or list(case.ledgers)
        lines = [f"Fee due: {inr(ledger.balance)}." for ledger in guessed]
        return self._fee_answer(case, lines)

    def run_conversation(self, case: ConversationCase) -> ConversationRun:
        """Keeps one shared history for everyone: every earlier question, whoever asked it and
        whether it was edited away, goes to the model with every document's text."""
        run = super().run_conversation(case)
        everything = [d.content for d in case.docs]
        turns: list[TurnResult] = []
        for turn in run.turns:
            earlier = [s.question for s in case.steps[: turn.step] if s.question]
            leaked = "\n".join([*everything, *earlier, turn.model_input])
            turns.append(turn.model_copy(update={"model_input": leaked}))
        return ConversationRun(turns=tuple(turns))


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


class TeluguStub(PerfectStub):
    """Ignores the Telugu switch: Telugu stays on whatever the harness sets (ADR-0036)."""

    name = "stub-telugu"

    def set_telugu(self, enabled: bool) -> None:
        self.telugu = True


StubFactory = Callable[[Mapping[str, CorpusItem], Iterable[EvalItem]], PerfectStub]

STUBS: Mapping[str, StubFactory] = {
    PerfectStub.name: PerfectStub,
    LeakyStub.name: LeakyStub,
    InjectableStub.name: InjectableStub,
    TeluguStub.name: TeluguStub,
}
