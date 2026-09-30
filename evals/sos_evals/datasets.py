"""Load and validate the JSONL datasets (docs/06 §13.1)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sos_evals.acl import CLASSES, SECTIONS, can_ask, retrievable, visible
from sos_evals.circulars import CircularCase, validate_cases
from sos_evals.contextual import ContextualSet, CtxDocument, CtxQuestion
from sos_evals.conversations import ConversationCase
from sos_evals.conversations import validate_cases as validate_conversation_cases
from sos_evals.fees import FeeCase
from sos_evals.fees import validate_cases as validate_fee_cases
from sos_evals.schema import CATEGORIES, CorpusItem, EvalItem

EVALS_DIR = Path(__file__).resolve().parents[1]
DATASETS_DIR = EVALS_DIR / "datasets"
CORPUS_FILE = "corpus.jsonl"
CIRCULARS_FILE = "circulars.jsonl"
FEES_FILE = "fees.jsonl"
CONTEXTUAL_FILE = "contextual.jsonl"
CONVERSATIONS_FILE = "conversations.jsonl"

Suite = Literal["fast", "full"]


class DatasetError(ValueError):
    pass


@dataclass(frozen=True)
class Dataset:
    corpus: dict[str, CorpusItem]
    items: tuple[EvalItem, ...]
    sha256: str
    """Digest of every dataset file, so reports say which data they measured."""
    circulars: tuple[CircularCase, ...] = ()
    """Synthetic circulars for the M4 reading eval (every suite runs all of them)."""
    fees: tuple[FeeCase, ...] = ()
    """Synthetic fee cases for the M6 Tally eval (every suite runs all of them)."""
    contextual: ContextualSet | None = None
    """Contextual retrieval and reranking set (docs/06 §13.6; the fast suite runs its subset)."""
    conversations: tuple[ConversationCase, ...] = ()
    """Scripted Ask conversations (ADR-0034; every suite runs all of them)."""

    def select(self, suite: Suite) -> tuple[EvalItem, ...]:
        if suite == "full":
            return self.items
        return tuple(item for item in self.items if item.fast)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"{path.name}:{number}: invalid JSON ({exc.msg})") from exc
        if not isinstance(row, dict):
            raise DatasetError(f"{path.name}:{number}: each line must be a JSON object")
        rows.append(row)
    return rows


def dataset_files(directory: Path) -> list[Path]:
    return [
        directory / CORPUS_FILE,
        *(directory / f"{c}.jsonl" for c in CATEGORIES),
        directory / CIRCULARS_FILE,
        directory / FEES_FILE,
        directory / CONTEXTUAL_FILE,
        directory / CONVERSATIONS_FILE,
    ]


def load(directory: Path = DATASETS_DIR) -> Dataset:
    files = dataset_files(directory)
    missing = [path.name for path in files if not path.is_file()]
    if missing:
        raise DatasetError(f"missing dataset files in {directory}: {', '.join(missing)}")
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))

    corpus: dict[str, CorpusItem] = {}
    for row in _read_jsonl(files[0]):
        item = CorpusItem.model_validate(row)
        if item.source in corpus:
            raise DatasetError(f"duplicate corpus source {item.source}")
        corpus[item.source] = item

    items: list[EvalItem] = []
    for category, path in zip(CATEGORIES, files[1 : 1 + len(CATEGORIES)], strict=True):
        for row in _read_jsonl(path):
            question = EvalItem.model_validate(row)
            if question.category != category:
                raise DatasetError(f"{question.id} is in {path.name} but has {question.category}")
            items.append(question)
    validate(corpus, items)
    circulars = tuple(
        CircularCase.model_validate(row) for row in _read_jsonl(directory / CIRCULARS_FILE)
    )
    fees = tuple(FeeCase.model_validate(row) for row in _read_jsonl(directory / FEES_FILE))
    conversations = tuple(
        ConversationCase.model_validate(row) for row in _read_jsonl(directory / CONVERSATIONS_FILE)
    )
    try:
        validate_cases(circulars)
        validate_fee_cases(fees)
        validate_conversation_cases(conversations)
        contextual = load_contextual(directory / CONTEXTUAL_FILE)
    except ValueError as exc:
        raise DatasetError(str(exc)) from exc
    return Dataset(
        corpus=corpus,
        items=tuple(items),
        sha256=digest.hexdigest(),
        circulars=circulars,
        fees=fees,
        contextual=contextual,
        conversations=conversations,
    )


def load_contextual(path: Path) -> ContextualSet:
    """``contextual.jsonl``: document rows (``kind: document``) then question rows."""
    documents: list[CtxDocument] = []
    questions: list[CtxQuestion] = []
    for row in _read_jsonl(path):
        if row.get("kind") == "document":
            documents.append(CtxDocument.model_validate(row))
        elif row.get("kind") == "question":
            questions.append(CtxQuestion.model_validate(row))
        else:
            raise DatasetError(f"{path.name}: each row needs kind document or question")
    return ContextualSet(documents=tuple(documents), questions=tuple(questions))


def _check_structure(where: str, sections: Sequence[str], classes: Sequence[str]) -> None:
    unknown = sorted((set(sections) - SECTIONS) | (set(classes) - CLASSES))
    if unknown:
        raise DatasetError(f"{where}: sections/classes outside the academic structure: {unknown}")


def _validate_corpus(corpus: dict[str, CorpusItem]) -> None:
    markers: set[str] = set()
    for known in corpus.values():
        if known.marker in markers:
            raise DatasetError(f"marker {known.marker} is used twice")
        markers.add(known.marker)
        _check_structure(known.source, known.acl.sections, known.acl.classes)
        if known.student_section is not None:
            _check_structure(known.source, (known.student_section,), ())


def validate(corpus: dict[str, CorpusItem], items: Sequence[EvalItem]) -> None:
    """Cross-checks that keep the gates meaningful (a wrong key would hide a leak)."""
    _validate_corpus(corpus)
    seen: set[str] = set()
    for item in items:
        if item.id in seen:
            raise DatasetError(f"duplicate item id {item.id}")
        seen.add(item.id)
        _check_structure(item.id, item.asker.sections, item.asker.classes)
        if not can_ask(item.asker):
            raise DatasetError(f"{item.id}: the asker's role does not hold kb.ask")
        for source in item.expected_sources:
            entry = corpus.get(source)
            if entry is None:
                raise DatasetError(f"{item.id}: expected source {source} is not in the corpus")
            if not retrievable(item.asker, entry):
                raise DatasetError(f"{item.id}: expected source {source} is not retrievable")
        for source in item.probe_sources:
            entry = corpus.get(source)
            if entry is None:
                raise DatasetError(f"{item.id}: probe source {source} is not in the corpus")
            if visible(item.asker, entry):
                raise DatasetError(f"{item.id}: probe source {source} is visible to the asker")
        if item.injection and not any(
            corpus[source].injection_canaries for source in item.expected_sources
        ):
            raise DatasetError(f"{item.id}: an injection item must cite an injected document")
