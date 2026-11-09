"""Faithfulness and answer correctness with a calibrated judge (FA-07; FR-KB-051..FR-KB-057;
docs/06 §13.11).

- *Faithfulness*: factual sentences of written answers (the harness's own split,
  :mod:`sos_evals.sentences`) that the judge finds supported by the passages they validly cite /
  factual sentences. A factual sentence without a valid citation is unsupported without asking the
  judge. Soft gate ``faithfulness >= 0.95``.
- *Answer correctness*: answerable questions with a reference answer whose answer the judge finds
  correct (a refusal is incorrect) / such questions. The judge must also find every date, time,
  amount and number of the reference in the answer (exact check, :func:`sentences.figures`).
  Soft gate ``answer_correctness >= 0.85``.
- *Calibration*: before it scores anything, the judge labels ``judge_calibration.jsonl``
  (synthetic items with a label each: verbatim and paraphrased claims, changed dates and figures,
  claims from another passage, partial and wrong answers). ``judge_agreement`` = items where the
  judge agrees with the label / items (soft gate, threshold pinned to ``judge.toml``
  ``calibration.min_agreement``). A judge below the threshold is not evidence: faithfulness and
  correctness are then None and their gates fail.

Judges (the :class:`Judge` protocol; an adapter may offer its own as ``judge``):

- :class:`StubJudge` (``stub``, every offline run and CI): deterministic word and figure overlap
  with the thresholds of ``judge.toml`` ``[stub]``. It measures the harness and the plumbing, not
  meaning; it agrees with the calibration labels by construction of both.
- the live judge (``make eval-live``): the eval bridge's ``GatewayJudge`` calls the ``eval_judge``
  role of ``models.yaml`` through the real gateway (offline-only role, synthetic data only) with
  the prompts of ``judge.toml`` ``[live]``; its calls are priced with :mod:`sos_evals.cost`.
"""

from __future__ import annotations

import re
import tomllib
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from sos_evals import metrics, sentences
from sos_evals.adapters import AskResult, ModelCall
from sos_evals.schema import CorpusItem, EvalItem

JUDGE_FILE: Final = Path(__file__).resolve().parents[1] / "judge.toml"
_QUOTES_DASHES: Final = "".join(chr(c) for c in (0x201C, 0x201D, 0x2018, 0x2019, 0x2013, 0x2014))
_SPLIT: Final = re.compile(rf"[\s,;:.!?()\[\]{{}}\"'|/{_QUOTES_DASHES}]+")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StubSettings(_Model):
    claim_word_recall: float = Field(ge=0, le=1)
    reference_word_recall: float = Field(ge=0, le=1)
    stopwords: frozenset[str]


class LiveSettings(_Model):
    faithfulness_system: str = Field(min_length=1)
    correctness_system: str = Field(min_length=1)


class JudgeConfig(_Model):
    min_agreement: float = Field(ge=0, le=1)
    stub: StubSettings
    live: LiveSettings


def load_config(path: Path = JUDGE_FILE) -> JudgeConfig:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != 1:
        raise ValueError(f"{path.name}: unsupported version {data.get('version')!r}")
    stub = data["stub"]
    return JudgeConfig(
        min_agreement=data["calibration"]["min_agreement"],
        stub=StubSettings(
            claim_word_recall=stub["claim_word_recall"],
            reference_word_recall=stub["reference_word_recall"],
            stopwords=frozenset(w.casefold() for w in stub["stopwords"]),
        ),
        live=LiveSettings.model_validate(data["live"]),
    )


class Judge(Protocol):
    name: str
    calls: list[ModelCall]
    """Model calls the judge made (priced in the report; empty for the stub)."""

    def supported(self, claim: str, passages: Sequence[str]) -> bool:
        """Is every statement of ``claim`` supported by ``passages``?"""
        ...

    def correct(self, question: str, answer: str, reference: str) -> bool:
        """Does ``answer`` give what ``reference`` gives for ``question``?"""
        ...


def content_words(text: str, stopwords: frozenset[str]) -> set[str]:
    clean = unicodedata.normalize("NFC", sentences.MARKERS.sub(" ", text)).casefold()
    return {w for w in _SPLIT.split(clean) if len(w) > 1 and w not in stopwords}


class StubJudge:
    """Deterministic overlap judge (see the module docstring). Not a model."""

    name = "stub"

    def __init__(self, settings: StubSettings) -> None:
        self._s = settings
        self.calls: list[ModelCall] = []

    def _recall(self, needed: str, available: str) -> float:
        words = content_words(needed, self._s.stopwords)
        if not words:
            return 1.0
        return len(words & content_words(available, self._s.stopwords)) / len(words)

    def supported(self, claim: str, passages: Sequence[str]) -> bool:
        pool = "\n".join(passages)
        if not sentences.figures(sentences.words(claim)) <= sentences.figures(pool):
            return False
        return self._recall(claim, pool) >= self._s.claim_word_recall

    def correct(self, question: str, answer: str, reference: str) -> bool:
        if not sentences.figures(reference) <= sentences.figures(sentences.words(answer)):
            return False
        return self._recall(reference, answer) >= self._s.reference_word_recall


# --- calibration --------------------------------------------------------------------------------


class CalibrationItem(_Model):
    id: str = Field(pattern=r"^judge-[0-9]{3}$")
    kind: Literal["faithfulness", "correctness"]
    perturbation: str = Field(min_length=1)
    """How the item was made (``verbatim``, ``date_shift``, ``other_passage``, ...)."""
    question: str = ""
    text: str = Field(min_length=1)
    """The claim (faithfulness) or the answer (correctness)."""
    passages: tuple[str, ...] = ()
    reference: str = ""
    label: bool


def validate_calibration(items: Sequence[CalibrationItem]) -> None:
    ids = [i.id for i in items]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate judge calibration ids")
    for item in items:
        if item.kind == "faithfulness" and not item.passages:
            raise ValueError(f"{item.id}: a faithfulness item needs passages")
        if item.kind == "correctness" and not item.reference:
            raise ValueError(f"{item.id}: a correctness item needs a reference")


class CalibrationOutcome(_Model):
    id: str
    kind: str
    perturbation: str
    label: bool
    verdict: bool


def calibrate(
    judge: Judge, items: Sequence[CalibrationItem]
) -> tuple[float | None, float | None, tuple[CalibrationOutcome, ...]]:
    """Agreement (share of items where the judge agrees with the label) and Cohen's kappa."""
    outcomes = tuple(
        CalibrationOutcome(
            id=i.id,
            kind=i.kind,
            perturbation=i.perturbation,
            label=i.label,
            verdict=judge.supported(i.text, i.passages)
            if i.kind == "faithfulness"
            else judge.correct(i.question, i.text, i.reference),
        )
        for i in items
    )
    if not outcomes:
        return None, None, ()
    n = len(outcomes)
    agree = sum(1 for o in outcomes if o.verdict == o.label) / n
    yes_label = sum(1 for o in outcomes if o.label) / n
    yes_judge = sum(1 for o in outcomes if o.verdict) / n
    expected = yes_label * yes_judge + (1 - yes_label) * (1 - yes_judge)
    kappa = (agree - expected) / (1 - expected) if expected < 1 else None
    return agree, kappa, outcomes


# --- scoring the run ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JudgedItem:
    claims: int
    supported: int
    correct: bool | None
    """None when the item has no reference answer or expects a refusal."""


def _cited_passages(
    item: EvalItem, answer: AskResult, corpus: Mapping[str, CorpusItem]
) -> list[tuple[str, list[str]]]:
    """Each segment's text and the passages it VALIDLY cites (title and text)."""
    out = []
    for segment in answer.segments:
        passages = []
        for citation in segment.citations:
            if metrics.citation_error(
                citation, asker=item.asker, provided=answer.provided_sources, corpus=corpus
            ):
                continue
            source = corpus[citation.source]
            # The source's metadata is part of what the answer may state (the application labels
            # each passage with its type, title and issue date, as sentences.score allows).
            issued = f" · issued {source.issued_on:%d/%m/%Y}" if source.issued_on else ""
            label = source.doc_type.replace("_", " ")
            passages.append(f"{label} · {source.title}{issued}\n{source.content}")
        out.append((segment.text, passages))
    return out


def judge_item(
    judge: Judge, item: EvalItem, answer: AskResult, corpus: Mapping[str, CorpusItem]
) -> JudgedItem:
    claims = supported = 0
    if not answer.refused and answer.mode == "full":
        joined = ""
        spans: list[tuple[int, int, list[str]]] = []
        for text, passages in _cited_passages(item, answer, corpus):
            clean = sentences.MARKERS.sub("", text)
            if joined and clean and not joined[-1].isspace() and not clean[0].isspace():
                joined += " "
            spans.append((len(joined), len(joined) + len(clean), passages))
            joined += clean
        for sentence in sentences.split(joined):
            if not sentences.is_factual(sentence.text):
                continue
            claims += 1
            pool = [
                p
                for start, end, passages in spans
                if max(start, sentence.start) < min(end, sentence.end)
                for p in passages
            ]
            supported += bool(pool) and judge.supported(sentence.text, pool)
    correct: bool | None = None
    if not item.expect_refusal and item.reference_answer:
        correct = (
            not answer.refused
            and answer.mode == "full"
            and judge.correct(item.question, answer.text, item.reference_answer)
        )
    return JudgedItem(claims=claims, supported=supported, correct=correct)


class JudgeMetrics(_Model):
    judge_calibration_items: int = 0
    judge_agreement: float | None = None
    judge_kappa: float | None = None
    faithfulness: float | None = None
    faithfulness_claims: int = 0
    answer_correctness: float | None = None
    answer_correctness_items: int = 0


def measure(
    judge: Judge,
    calibration: Sequence[CalibrationItem],
    judged: Sequence[tuple[EvalItem, AskResult]],
    corpus: Mapping[str, CorpusItem],
    *,
    min_agreement: float,
) -> tuple[JudgeMetrics, tuple[CalibrationOutcome, ...]]:
    agreement, kappa, outcomes = calibrate(judge, calibration)
    base = JudgeMetrics(
        judge_calibration_items=len(outcomes),
        judge_agreement=agreement,
        judge_kappa=kappa,
    )
    if agreement is None or agreement < min_agreement:
        return base, outcomes  # an uncalibrated judge is no evidence: the gates fail
    results = [judge_item(judge, item, answer, corpus) for item, answer in judged]
    claims = sum(r.claims for r in results)
    graded = [r.correct for r in results if r.correct is not None]
    return base.model_copy(
        update={
            "faithfulness": sum(r.supported for r in results) / claims if claims else None,
            "faithfulness_claims": claims,
            "answer_correctness": sum(graded) / len(graded) if graded else None,
            "answer_correctness_items": len(graded),
        }
    ), outcomes


__all__ = [
    "JUDGE_FILE",
    "CalibrationItem",
    "CalibrationOutcome",
    "Judge",
    "JudgeConfig",
    "JudgeMetrics",
    "StubJudge",
    "calibrate",
    "content_words",
    "judge_item",
    "load_config",
    "measure",
    "validate_calibration",
]
