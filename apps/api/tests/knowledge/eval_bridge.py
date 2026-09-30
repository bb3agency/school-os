"""Eval bridge: the RAG harness (``evals/sos_evals``) against the REAL knowledge service.

    make eval EVAL_ADAPTER=app-fake [EVAL_SUITE=full]
    uv run python apps/api/tests/knowledge/eval_bridge.py run --adapter app-fake --suite fast

Test tooling only (docs/06 §13): it lives under ``tests/`` so no production code path imports
``sos_evals``, and the harness itself still imports no application code (it talks to this
module only through its ``RetrievalAdapter`` / ``AskAdapter`` protocols).

What runs for real: a fresh PostgreSQL 16 + pgvector (testcontainers, or the admin URL in
``SOS_TEST_ADMIN_DATABASE_URL``) bootstrapped and migrated like production; two synthetic
schools provisioned through ``tenancy`` with the academic structure of ``sos_evals.acl``; every
corpus document stored like the documents module stores it (DOCX, sensitivity, ACL rows incl.
membership entries, versions, ``is_latest``) and indexed by the real ingestion pipeline
(extraction, Aadhaar masking, chunking, fake embeddings, SQL chunk store; C3 is excluded there);
every corpus student created through ``students.service`` (admission-register identity values,
some with a conflicting UDISE+ date of birth); then ``knowledge.service`` for every question:
ACL keys, hybrid retrieval filtered in SQL under RLS, the record tools under the caller's
scopes, the tool loop through the real gateway (redaction, budget, rate limit, metering
ledger), citation validation, output sanitising, the encrypted query log and the audit event.

Askers are real principals: each is a membership with its system role's permissions exactly as
the resolver builds them (school-wide readers, ACL managers, scoped teachers, C3 holders) and
its section/class scopes. Before any question runs, :meth:`AppFakeAdapter.parity_mismatches`
checks that the harness's oracle (``sos_evals.acl``) and the application agree on what every
asker may see and retrieve; a disagreement stops the run (exit 3), because leakage is judged by
the oracle. ``tests/knowledge/test_eval_bridge.py`` runs the same check over a grid of every
role and scope.

What is fake (offline, deterministic; SOS_KB_PROVIDER_MODE=fake):

- embeddings: ``FakeEmbeddingsProvider`` (hashed words and trigrams, no meaning, no translation);
- the model: :class:`EvalFakeTransport`, a stand-in. A question naming an admission number and a
  record field (date of birth, father's/mother's name, date of admission; English, Telugu or
  Latin-script Telugu words) calls ``find_students`` with the number, then
  ``get_student_facts`` for that field of the matching student, and answers the fact's first
  sentence with a citation (or "not found" when the lookup finds nobody). Any other question
  calls ``search_documents`` with the question, keeps only results that share the question's
  identifiers (tokens with digits) and at least half of its content words, and answers one cited
  sentence per kept result. Telugu-script questions get a Telugu prefix. Requests for Aadhaar
  numbers are refused (system prompt rule 5, invariant 4). Refusal, recall and language numbers
  therefore measure this stand-in plus the application's controls, never Claude's judgement;
  faithfulness and correctness need the calibrated judge and the live gateway (docs/06 §13.2).
- ``retrieve`` (Recall@10/MRR): the same deterministic record lookup through the real record
  tools (when the question names a student and a field), then ``search_documents``.

Circular reading (M4, ``sos_evals.circulars``): each synthetic circular is stored in the first
school as a C1 ``circular`` document, indexed by the real pipeline (whose hook queues the
reading), read by ``circulars.service.run_reading`` through the real gateway with the product's
offline stand-in (``knowledge.gateway.fake_circulars``: sentences that write a date and an action
word), and scored from the stored suggestions. Recall and precision therefore measure that
stand-in plus the application's grounding checks, never Claude.

Fee dues (M6, ``sos_evals.fees``; ADR-0032): each synthetic case is its own school (the
academic structure above) with the case's students created through ``students.service``, its
Tally ledgers, links and the ``tally.connector.enabled`` flag as the case says, and one asker
with the case's role (class and subject teachers scoped to the asked student's section). The
question goes through ``knowledge.service`` like any other; the stand-in, when the application
offers ``get_fee_dues``, finds the student by the admission number in the question (never by a
name) and calls the tool (without a number: the school totals), then answers with the tool's text
and cites it whole. Without the tool (no ``finance.read``, connector off) it searches documents
(there are none) and answers "not found". The figures therefore measure the application's tool,
links, permission and flag checks, never Claude.

Mapping: record sources carry the application's student ids; the bridge rewrites them to the
corpus ids (``sos://student/<corpus id>/...``). Document ids are the corpus ids. Corpus doc
types outside the documents module's list (``note``, ``checklist``, ``timetable``) are stored as
``other``. The gateway's per-school provider rate limit is raised for the run (a whole question
set in a minute is not traffic).
"""

from __future__ import annotations

import atexit
import datetime as dt
import hashlib
import importlib.util
import math
import os
import re
import sys
import time
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from sqlalchemy import Engine, create_engine, select, text

TESTS: Final = Path(__file__).resolve().parents[1]
DOC_TYPES: Final = frozenset(
    {"circular", "policy", "minutes", "register_scan", "certificate", "letter", "form", "report"}
)
CLASS_CODES: Final = {"9": "IX", "10": "X"}
_SPLIT: Final = re.compile(r"[\s,;:?!()\"'\u201c\u201d\u2018\u2019]+")
STOPWORDS: Final = frozenset(
    {
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "what",
        "when",
        "which",
        "who",
        "whom",
        "where",
        "how",
        "does",
        "did",
        "do",
        "for",
        "of",
        "to",
        "in",
        "on",
        "at",
        "a",
        "an",
        "and",
        "or",
        "by",
        "with",
        "from",
        "this",
        "that",
        "our",
        "their",
        "school",
        "school's",
        "will",
        "it",
        "its",
        "about",
        "any",
        "there",
        "has",
        "have",
        "please",
        "tell",
        "me",
        "give",
        "list",
        "you",
        "your",
        "all",
        "time",
        "date",
        "according",
        "enti",
        "lo",
        "eppudu",
        "ఎప్పుడు",
        "ఏమిటి",
        "ఎంత",
        "ఎవరు",
    }
)
AADHAAR_WORDS: Final = ("aadhaar", "aadhar", "ఆధార్")
TELUGU_PREFIX: Final = "సమాధానం: "
FIND: Final = "find_students"
FACTS: Final = "get_student_facts"
SEARCH: Final = "search_documents"
ADMISSION_NO: Final = re.compile(r"\b[A-Z]{2}-\d{4}-\d{4}\b")
FIELD_WORDS: Final = (
    ("admission_date", ("date of admission", "admission date", "చేరిన తేదీ", "cherina")),
    ("father_name", ("father", "తండ్రి", "tandri")),
    ("mother_name", ("mother", "తల్లి పేరు", "talli")),
    ("dob", ("date of birth", "dob", "birth", "పుట్టిన", "puttina")),
)
"""Record fields the stand-in recognises (first match wins)."""
CHATS: Final = "search_my_conversations"
CHAT_WORDS: Final = ("did i ask", "i asked", "నేను అడిగిన")
"""Questions about the caller's own earlier chats (ADR-0034)."""
FEES: Final = "get_fee_dues"
FEE_WORDS: Final = ("fee", "dues", "owe", "ఫీజు", "బకాయి", "బాకీ", "kattali")
"""Fee questions the stand-in recognises (only when the application offers get_fee_dues)."""
_STUDENT_ID: Final = re.compile(r"Student ID: ([0-9a-f-]{36})\.")
_STUDENT_URI: Final = re.compile(r"^sos://student/([0-9a-f-]{36})(.*)$")
Step = tuple[str, dict[str, Any]] | list[dict[str, Any]]
"""The stand-in's next move: a tool call (name, input), or the cited answer blocks (empty:
nothing relevant, answer "not found")."""


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


# --- the stand-in model -------------------------------------------------------------------------


def tokens(text_: str) -> set[str]:
    out = set()
    for raw in _SPLIT.split(text_.casefold()):
        token = raw.strip(".-/")
        if len(token) >= 2 and token not in STOPWORDS:
            out.add(token)
    return out


def _matches(word: str, words: set[str]) -> bool:
    if word in words:
        return True
    if word.isascii() and len(word) >= 5:
        return any(w.isascii() and len(w) >= 5 and w[:4] == word[:4] for w in words)
    return False


def relevant(question: str, evidence: str) -> bool:
    """The stand-in's judgement: identifiers must all appear, and half the content words."""
    wanted = tokens(question)
    if not wanted:
        return False
    have = tokens(evidence)
    identifiers = {w for w in wanted if any(ch.isdigit() for ch in w)}
    if not all(w in have for w in identifiers):
        return False
    hits = sum(1 for w in wanted if _matches(w, have))
    return hits >= max(1, math.ceil(len(wanted) / 2))


def record_request(question: str) -> tuple[str, str] | None:
    """(admission number, field) when the question asks for one field of one named student."""
    number = ADMISSION_NO.search(question)
    if number is None:
        return None
    folded = question.casefold()
    for name, words in FIELD_WORDS:
        if any(w in folded for w in words):
            return number.group(0), name
    return None


def student_id_for(admission_no: str, blocks: Iterable[Mapping[str, Any]]) -> str | None:
    """The ID in the ``find_students`` block whose admission number matches exactly."""
    for block in blocks:
        text_ = _block_text(block)
        found = _STUDENT_ID.search(text_)
        if found and f"Admission no.: {admission_no}." in text_:
            return found.group(1)
    return None


def _first_sentence(text_: str) -> str:
    return re.split(r"(?<=[.!?।])\s", text_.strip(), maxsplit=1)[0][:200]


def _block_text(block: Mapping[str, Any]) -> str:
    return "".join(str(b.get("text", "")) for b in block.get("content") or ())


class EvalFakeTransport:
    """Messages API stand-in (see the module docstring). Same response shape as the gateway's
    ``FakeTransport``; deterministic."""

    name = "fake"

    def __init__(self) -> None:
        self.sent: list[Mapping[str, Any]] = []

    @staticmethod
    def _blocks(message: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        content = message.get("content")
        return [b for b in content if isinstance(b, Mapping)] if isinstance(content, list) else []

    def _question(self, messages: Sequence[Mapping[str, Any]]) -> str:
        """The question: the LAST text block of the user turn (the conversation's summary,
        recent turns and the question as written come before it; ADR-0034)."""
        texts = [
            str(block.get("text", ""))
            for block in self._blocks(messages[0])
            if block.get("type") == "text"
        ]
        return texts[-1] if texts else ""

    @staticmethod
    def _prefers_telugu(body: Mapping[str, Any]) -> bool:
        """The user's memory block (a system block after the prompt) asks for Telugu."""
        from app.knowledge.config.llm import load_llm_config

        header = load_llm_config().conversation.memory_header
        return any(
            str(b.get("text", "")).startswith(header) and "telugu" in str(b.get("text")).casefold()
            for b in body.get("system") or ()
            if isinstance(b, Mapping)
        )

    def _chat_step(
        self, messages: Sequence[Mapping[str, Any]], question: str, tools: set[str], prefix: str
    ) -> Step:
        """ "What did I ask ...": the caller's own earlier chats, then their cited questions."""
        if CHATS not in set(self._calls(messages).values()) and CHATS in tools:
            return CHATS, {"query": question}
        found = [(r, _block_text(r)) for r in self._results(messages, CHATS)]
        return self._cite(found, prefix, first_only=True)

    def _calls(self, messages: Sequence[Mapping[str, Any]]) -> dict[str, str]:
        """tool_use id -> tool name, for the calls made so far."""
        return {
            str(b.get("id")): str(b.get("name"))
            for m in messages
            for b in self._blocks(m)
            if b.get("type") == "tool_use"
        }

    def _results(
        self, messages: Sequence[Mapping[str, Any]], tool: str | None = None
    ) -> list[Mapping[str, Any]]:
        calls = self._calls(messages)
        found: list[Mapping[str, Any]] = []
        for message in messages:
            for block in self._blocks(message):
                if block.get("type") != "tool_result":
                    continue
                if tool is not None and calls.get(str(block.get("tool_use_id"))) != tool:
                    continue
                content = block.get("content")
                if isinstance(content, list):
                    found += [r for r in content if r.get("type") == "search_result"]
        return found

    @staticmethod
    def _reply(content: list[dict[str, Any]], stop: str, body: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "model": body.get("model", "fake"),
            "content": content,
            "stop_reason": stop,
            "usage": {"input_tokens": len(str(body)) // 4, "output_tokens": 50},
        }

    def _use(
        self, name: str, arguments: Mapping[str, Any], body: Mapping[str, Any]
    ) -> dict[str, Any]:
        call = {"type": "tool_use", "id": f"toolu_eval_{name}", "name": name, "input": arguments}
        return self._reply([call], "tool_use", body)

    @staticmethod
    def _cite(
        results: Sequence[tuple[Mapping[str, Any], str]], prefix: str, *, first_only: bool
    ) -> list[dict[str, Any]]:
        content = []
        for index, (result, text_) in enumerate(results[:3]):
            sentence = _first_sentence(text_)
            citation = {
                "type": "search_result_location",
                "source": result.get("source"),
                "title": result.get("title"),
                "cited_text": sentence if first_only else text_,
                "search_result_index": index,
                "start_block_index": 0,
                "end_block_index": 1,
            }
            said = sentence if first_only else f"{result.get('title')}: {sentence}"
            content.append({"type": "text", "text": prefix + said, "citations": [citation]})
        return content

    def _record_step(
        self,
        messages: Sequence[Mapping[str, Any]],
        wanted: tuple[str, str],
        tools: set[str],
        prefix: str,
    ) -> Step:
        number, field = wanted
        called = set(self._calls(messages).values())
        if FIND not in called and FIND in tools:
            return FIND, {"query": number}
        student = student_id_for(number, self._results(messages, FIND))
        if student is not None and FACTS not in called and FACTS in tools:
            return FACTS, {"student_id": student, "fields": [field]}
        facts = [(r, _block_text(r)) for r in self._results(messages, FACTS)]
        return self._cite(facts, prefix, first_only=True)

    def _fee_step(
        self, messages: Sequence[Mapping[str, Any]], question: str, tools: set[str], prefix: str
    ) -> Step:
        """Find the student by the admission number in the question (never by a name), then
        ``get_fee_dues``; no number: the school totals. The answer is the tool's text, cited."""
        called = set(self._calls(messages).values())
        number = ADMISSION_NO.search(question)
        if number is not None:
            if FIND not in called and FIND in tools:
                return FIND, {"query": number.group(0)}
            student = student_id_for(number.group(0), self._results(messages, FIND))
            if student is None:
                return []
            if FEES not in called:
                return FEES, {"student_id": student}
        elif FEES not in called:
            return FEES, {}
        content = []
        for index, result in enumerate(self._results(messages, FEES)):
            text_ = _block_text(result)
            citation = {
                "type": "search_result_location",
                "source": result.get("source"),
                "title": result.get("title"),
                "cited_text": text_,
                "search_result_index": index,
                "start_block_index": 0,
                "end_block_index": 1,
            }
            content.append({"type": "text", "text": prefix + text_, "citations": [citation]})
        return content

    def _document_step(
        self, messages: Sequence[Mapping[str, Any]], question: str, tools: set[str], prefix: str
    ) -> Step:
        if not self._calls(messages) and SEARCH in tools:
            return SEARCH, {"query": question}
        kept = []
        for result in self._results(messages):
            text_ = _block_text(result)
            if relevant(question, f"{result.get('title', '')} {text_}"):
                kept.append((result, text_))
        return self._cite(kept, prefix, first_only=False)

    def send(self, request: Any) -> Mapping[str, Any]:
        body = request.body
        self.sent.append(body)
        if isinstance((body.get("output_config") or {}).get("format"), Mapping):
            # Structured output (M4 circular reading, notice drafts): the product's offline
            # stand-in (app.knowledge.gateway.fake_circulars), unchanged.
            from app.knowledge.gateway.fake import FakeTransport

            return FakeTransport().send(request)
        messages = list(body.get("messages") or ())
        question = self._question(messages)
        telugu = bool(re.search(r"[\u0c00-\u0c7f]", question))
        not_found = "ఇది కనబడలేదు." if telugu else "I could not find this in the records."
        prefix = TELUGU_PREFIX if telugu or self._prefers_telugu(body) else ""
        step: Step = []
        if not any(w in question.casefold() for w in AADHAAR_WORDS):
            tools: set[str] = set()
            if (body.get("tool_choice") or {}).get("type") != "none":
                tools = {str(t.get("name")) for t in body.get("tools") or ()}
            wanted = record_request(question)
            if CHATS in tools and any(w in question.casefold() for w in CHAT_WORDS):
                step = self._chat_step(messages, question, tools, prefix)
            elif FEES in tools and any(w in question.casefold() for w in FEE_WORDS):
                step = self._fee_step(messages, question, tools, prefix)
            elif wanted is not None:
                step = self._record_step(messages, wanted, tools, prefix)
            else:
                step = self._document_step(messages, question, tools, prefix)
        if isinstance(step, tuple):
            return self._use(step[0], step[1], body)
        return self._reply(step or [{"type": "text", "text": not_found}], "end_turn", body)


# --- the application side -----------------------------------------------------------------------


@dataclass(frozen=True)
class Engines:
    admin: Engine
    app: Engine
    platform: Engine


class _Stack:
    """Database, engines and process state for one command-line run (torn down at exit)."""

    def __init__(self) -> None:
        conftest = _load("sos_test_conftest_for_evals", TESTS / "conftest.py")
        external = os.environ.get("SOS_TEST_ADMIN_DATABASE_URL")
        if external:
            from sqlalchemy.engine import make_url

            url = make_url(external)
            db = conftest.TestDatabase(external, url.host or "localhost", url.port or 5432)
            conftest.run_bootstrap_with_local_psql(external)
        else:
            from testcontainers.postgres import PostgresContainer

            container = PostgresContainer(
                conftest.PG_IMAGE,
                username="postgres",
                password="test-admin-pw",
                dbname=conftest.DB_NAME,
                driver="psycopg",
            )
            container.start()
            atexit.register(container.stop)
            host = container.get_container_host_ip()
            port = int(container.get_exposed_port(5432))
            admin_url = (
                f"postgresql+psycopg://postgres:test-admin-pw@{host}:{port}/{conftest.DB_NAME}"
            )
            conftest.run_bootstrap_in_container(container.get_wrapped_container().id)
            db = conftest.TestDatabase(admin_url, host, port, container.get_wrapped_container().id)
        conftest.upgrade_head(db.migrator_url)

        from app.authz.kv import InMemoryKV, set_kv_store
        from app.core import db as core_db

        self.engines = Engines(
            admin=create_engine(db.admin_url),
            app=create_engine(db.app_url, pool_size=5),
            platform=create_engine(db.platform_url, pool_size=2),
        )
        core_db.set_engine("app", self.engines.app)
        core_db.set_engine("platform", self.engines.platform)
        set_kv_store(InMemoryKV())
        atexit.register(self.close)

    def close(self) -> None:
        for engine in (self.engines.app, self.engines.platform, self.engines.admin):
            engine.dispose()


class ParityError(ValueError):
    """The oracle and the application disagree about visibility (the run cannot be judged)."""


class AppFakeAdapter:
    """``RetrievalAdapter`` + ``AskAdapter`` over ``knowledge.service``, the M4
    ``CircularAdapter`` over ``circulars.service`` and the M6 ``FeeAdapter`` over
    ``knowledge.service`` with ``get_fee_dues`` (see the docstring)."""

    name = "app-fake"

    def __init__(
        self,
        corpus: Mapping[str, Any],
        items: Iterable[Any],
        *,
        engines: Engines | None = None,
        check_parity: bool = True,
    ) -> None:
        self._admin = engines.admin if engines is not None else _Stack().engines.admin
        self.K = _load("sos_test_ask_support", TESTS / "knowledge" / "ask_support.py")
        from app.knowledge import service
        from app.knowledge.config.llm import load_llm_config

        llm = load_llm_config()
        relaxed = llm.model_copy(
            update={
                "rate_limit": llm.rate_limit.model_copy(
                    update={"requests_per_minute_per_tenant": 100_000}
                )
            }
        )
        self.transport = EvalFakeTransport()
        self.K.install_runtime(transport=self.transport, llm_config=relaxed)
        self._service = service.get_service()
        self.corpus = dict(corpus)
        self._schools: dict[str, Any] = {}
        self._members: dict[tuple[str, str], Any] = {}
        self._versions: dict[uuid.UUID, str] = {}
        """Application version id -> corpus source."""
        self._students: dict[uuid.UUID, str] = {}
        """Application student id -> corpus student id."""
        items = list(items)
        tenants = sorted({c.tenant for c in corpus.values()} | {i.asker.tenant for i in items})
        for label in tenants:
            self._schools[label] = self._school(label)
        self._memberships(corpus, items)
        self._seed(corpus)
        self._seed_students(corpus)
        if check_parity:
            mismatches = self.parity_mismatches(i.asker for i in items)
            if mismatches:
                raise ParityError(
                    f"oracle/application visibility mismatch ({len(mismatches)}): "
                    + "; ".join(mismatches[:5])
                )

    # --- set-up ---------------------------------------------------------------------------------

    def _school(self, label: str) -> Any:
        from app.core.db import tenant_session
        from app.tenancy import service as tenancy
        from app.tenancy.schemas import AcademicYearCreate, ClassCreate, SectionCreate
        from sos_evals.acl import ACADEMIC_STRUCTURE

        world = self.K.W
        school = world.School(world.provision_school())
        owner = world.add_member(self._admin, school.tenant_id, ["owner"])
        school.people["owner"] = owner
        with tenant_session(school.tenant_id, owner.user_id) as db:
            year = tenancy.create_academic_year(
                db,
                AcademicYearCreate(
                    label="2026-27",
                    starts_on=dt.date(2026, 6, 1),
                    ends_on=dt.date(2027, 3, 31),
                    is_current=True,
                ),
            )
            for number, sections in ACADEMIC_STRUCTURE.items():
                code = CLASS_CODES[number]
                klass = tenancy.create_class(
                    db,
                    ClassCreate(
                        code=code,
                        display_en=f"Class {code}",
                        display_te=f"{number}వ తరగతి",
                        sort_order=100 + int(number),
                    ),
                )
                school.ids[f"class:{number}"] = klass.id
                for section_label in sections:
                    section = tenancy.create_section(
                        db,
                        SectionCreate(
                            academic_year_id=year.id,
                            class_id=klass.id,
                            name=section_label.removeprefix(number),
                        ),
                    )
                    school.ids[f"section:{section_label}"] = section.id
        self.K.enable_ai(self._admin, school.tenant_id)
        return school

    def _member(self, tenant: str, role: str, label: str | None) -> Any:
        """One membership per named member, else one per role (scopes live in the context)."""
        key = (tenant, label or f"role:{role}")
        if key not in self._members:
            school = self._schools[tenant]
            self._members[key] = self.K.W.add_member(self._admin, school.tenant_id, [role])
        return self._members[key]

    def _memberships(self, corpus: Mapping[str, Any], items: Sequence[Any]) -> None:
        for item in items:
            self._member(item.asker.tenant, item.asker.role, item.asker.member)
        for entry in corpus.values():
            for label in entry.acl.members:
                self._member(entry.tenant, "teacher", label)

    def _acl(self, school: Any, tenant: str, acl: Any) -> list[tuple[str, str]]:
        entries = [("role", r) for r in acl.roles]
        entries += [("section", str(school.ids[f"section:{s}"])) for s in acl.sections]
        entries += [("class", str(school.ids[f"class:{c}"])) for c in acl.classes]
        entries += [
            ("membership", str(self._members[(tenant, label)].membership_id))
            for label in acl.members
        ]
        return entries

    def _seed(self, corpus: Mapping[str, Any]) -> None:
        from app.knowledge import sources

        versions: dict[str, list[tuple[int, Any]]] = defaultdict(list)
        for item in corpus.values():
            if item.kind != "document":
                continue
            ref = sources.parse(item.source)
            versions[str(ref.object_id)].append((ref.version_no or 1, item))
        for doc_id, entries in sorted(versions.items()):
            entries.sort(key=lambda e: e[0])
            for version_no, item in entries:
                self._store_version(uuid.UUID(doc_id), version_no, item)

    def _store_version(self, doc_id: uuid.UUID, version_no: int, item: Any) -> None:
        from app.knowledge.ingestion.extract import DOCX_MIME

        support = self.K
        school = self._schools[item.tenant]
        admin = self._admin
        data = support.S.docx("".join(support.S.p(line) for line in item.content.splitlines()))
        version_id = uuid.uuid4()
        key = f"t/{school.tenant_id}/docs/{doc_id}/v{version_no}/original.docx"
        owner = school.people["owner"].user_id
        with admin.begin() as c:
            if version_no == 1:
                c.execute(
                    text(
                        "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, "
                        "issued_on, sensitivity, created_by) VALUES (:d, :t, 'circular', :dt, "
                        ":ti, :io, :se, :u)"
                    ),
                    {
                        "d": doc_id,
                        "t": school.tenant_id,
                        "dt": item.doc_type if item.doc_type in DOC_TYPES else "other",
                        "ti": item.title,
                        "io": item.issued_on,
                        "se": item.sensitivity,
                        "u": owner,
                    },
                )
            else:
                c.execute(text("DELETE FROM kb.document_acl WHERE document_id = :d"), {"d": doc_id})
                c.execute(
                    text("UPDATE kb.documents SET title = :ti, issued_on = :io WHERE id = :d"),
                    {"ti": item.title, "io": item.issued_on, "d": doc_id},
                )
            c.execute(
                text(
                    "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                    "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                    "(:v, :t, :d, :n, :k, :h, :m, :s, 'ready', :u)"
                ),
                {
                    "v": version_id,
                    "t": school.tenant_id,
                    "d": doc_id,
                    "n": version_no,
                    "k": key,
                    "h": hashlib.sha256(data).digest(),
                    "m": DOCX_MIME,
                    "s": len(data),
                    "u": owner,
                },
            )
            c.execute(
                text("UPDATE kb.documents SET current_version_id = :v WHERE id = :d"),
                {"v": version_id, "d": doc_id},
            )
            for ptype, ref in self._acl(school, item.tenant, item.acl):
                c.execute(
                    text(
                        "INSERT INTO kb.document_acl (tenant_id, document_id, principal_type, "
                        "principal_ref) VALUES (:t, :d, :pt, :r)"
                    ),
                    {"t": school.tenant_id, "d": doc_id, "pt": ptype, "r": ref},
                )
        self._versions[version_id] = item.source
        support.D.memory_store().put(key, data, DOCX_MIME)
        outcome = support.pipeline().ingest(school.tenant_id, doc_id, version_id)
        if outcome != "indexed" and item.sensitivity != "C3":
            raise RuntimeError(f"corpus document {doc_id} v{version_no} was not indexed: {outcome}")

    def _seed_students(self, corpus: Mapping[str, Any]) -> None:
        """Every corpus student through ``students.service`` (admission-register values)."""
        from app.core.db import tenant_session
        from app.students import service as students
        from app.students.schemas import StudentCreate, ValueIn
        from sos_evals.synthetic import STUDENTS

        wanted = {
            m.group(1)
            for c in corpus.values()
            if c.kind == "record" and (m := _STUDENT_URI.match(c.source))
        }
        register = "admission_register"
        for s in STUDENTS:
            if s.id not in wanted:
                continue
            school = self._schools[s.tenant]
            values = [
                ValueIn(attribute_key="full_name", source=register, value=s.name),
                ValueIn(attribute_key="dob", source=register, value=s.dob.isoformat()),
                ValueIn(attribute_key="father_name", source=register, value=s.father),
                ValueIn(attribute_key="mother_name", source=register, value=s.mother),
                ValueIn(attribute_key="admission_no", source=register, value=s.admission_no),
                ValueIn(
                    attribute_key="admission_date", source=register, value=s.admitted.isoformat()
                ),
            ]
            if s.udise_dob is not None:
                values.append(
                    ValueIn(attribute_key="dob", source="udise_plus", value=s.udise_dob.isoformat())
                )
            with tenant_session(school.tenant_id, school.people["owner"].user_id) as db:
                out = students.create_student(
                    db,
                    self.K.SW.admin_ctx(school),
                    StudentCreate(values=values, section_id=school.ids[f"section:{s.section}"]),
                )
            self._students[out.id] = s.id
        self._check_student_blocks(corpus)

    def _check_student_blocks(self, corpus: Mapping[str, Any]) -> None:
        """Each record item's first line is what ``get_student_facts`` really says (the corpus
        is the answer key, so a format drift must stop the run, not lower a metric)."""
        from app.core.db import tenant_session
        from app.knowledge import composition

        facts = composition.runtime().tools[FACTS]
        by_corpus = {v: k for k, v in self._students.items()}
        for item in corpus.values():
            m = _STUDENT_URI.match(item.source)
            if item.kind != "record" or m is None or "/field/admission_no?" in item.source:
                continue
            field = item.source.split("/field/", 1)[1].split("?", 1)[0]
            school = self._schools[item.tenant]
            with tenant_session(school.tenant_id, school.people["owner"].user_id) as s:
                outcome = facts.run(
                    s,
                    self.K.SW.admin_ctx(school),
                    "check",
                    {"student_id": str(by_corpus[m.group(1)]), "fields": [field]},
                )
            said = [(self.corpus_source(b.source), _first_sentence(b.text)) for b in outcome.blocks]
            expected = (item.source, item.content.splitlines()[0])
            if said != [expected]:
                raise RuntimeError(f"record tool drift for {item.source}: {said} != {[expected]}")

    # --- askers ---------------------------------------------------------------------------------

    def context(self, asker: Any) -> tuple[Any, Any]:
        """The asker as the resolver would build it: its role's permissions (school-wide or
        scoped as in roles.yaml) and its membership's section/class scopes."""
        from app.authz.catalog import implicit_permissions, system_roles
        from app.authz.context import Scopes, UserContext

        school = self._schools[asker.tenant]
        member = self._member(asker.tenant, asker.role, asker.member)
        template = system_roles()[asker.role]
        permissions = set(template.permission_keys) | set(implicit_permissions())
        scoped = {p for p in template.permission_keys if (g := template.grant(p)) and g.scoped}
        ctx = UserContext(
            user_id=member.user_id,
            tenant_id=school.tenant_id,
            membership_id=member.membership_id,
            roles=frozenset({asker.role}),
            permissions=frozenset(permissions),
            scopes=Scopes(
                school=False,
                section_ids=frozenset(school.ids[f"section:{s}"] for s in asker.sections),
                class_ids=frozenset(school.ids[f"class:{c}"] for c in asker.classes),
            ),
            mfa=True,
            auth_time=None,
            scoped_permissions=frozenset(scoped),
        )
        return school, ctx

    def corpus_source(self, source: str) -> str:
        """Application source URI -> corpus source URI (student ids differ; documents do not)."""
        m = _STUDENT_URI.match(source)
        if m is None:
            return source
        corpus_id = self._students.get(uuid.UUID(m.group(1)))
        return f"sos://student/{corpus_id}{m.group(2)}" if corpus_id else source

    # --- oracle parity ------------------------------------------------------------------------

    def _retrievable(self, session: Any, ctx: Any) -> set[str]:
        from app.knowledge.models import DocumentChunk
        from app.knowledge.retrieval.acl import acl_predicate
        from app.knowledge.tools.access import acl_keys

        keys = acl_keys(session, ctx)
        if keys is None:
            return set()
        rows = session.scalars(
            select(DocumentChunk.version_id).where(acl_predicate(keys)).distinct()
        )
        return {self._versions[v] for v in rows}

    def _visible_documents(self, session: Any, ctx: Any) -> set[str]:
        """Document ids the documents module lets the caller open (C3 needs the download rule)."""
        from app.core.errors import DomainError
        from app.documents import service as documents

        seen: dict[uuid.UUID, str] = {}
        before: uuid.UUID | None = None
        try:
            while True:
                page, before = documents.list_documents(session, ctx, limit=100, before_id=before)
                seen.update({d.id: d.sensitivity for d in page})
                if before is None:
                    break
        except DomainError:
            return set()
        visible = set()
        for doc_id, sensitivity in seen.items():
            if sensitivity == "C3":
                try:
                    documents.get_download_url(session, ctx, doc_id)
                except DomainError:
                    continue
            visible.add(str(doc_id))
        return visible

    def _visible_students(self, session: Any, ctx: Any) -> set[str]:
        from app.core.errors import DomainError
        from app.students import service as students

        if not ctx.has("student.read_basic"):
            return set()
        try:
            ids = students.list_students_in_scope(session, ctx)
        except DomainError:
            return set()
        return {self._students[i] for i in ids if i in self._students}

    def parity_mismatches(self, askers: Iterable[Any]) -> list[str]:
        """Every (asker, corpus item) where ``sos_evals.acl`` and the application disagree on
        visibility (documents and students services) or retrievability (the SQL filter)."""
        from app.core.db import tenant_session
        from app.knowledge import sources
        from sos_evals import acl as oracle

        out: list[str] = []
        for asker in dict.fromkeys(askers):
            school, ctx = self.context(asker)
            with tenant_session(school.tenant_id, ctx.user_id) as s:
                retrievable = self._retrievable(s, ctx)
                documents = self._visible_documents(s, ctx)
                students = self._visible_students(s, ctx)
            for item in self.corpus.values():
                if item.tenant != asker.tenant:
                    got = (False, False)  # RLS: another school's rows are not even queried
                elif item.kind == "document":
                    doc_id = str(sources.parse(item.source).object_id)
                    got = (doc_id in documents, item.source in retrievable)
                else:
                    m = _STUDENT_URI.match(item.source)
                    seen = m is not None and m.group(1) in students
                    got = (seen, seen)
                want = (oracle.visible(asker, item), oracle.retrievable(asker, item))
                if got != want:
                    out.append(
                        f"{asker.role}{list(asker.sections)}{list(asker.classes)}"
                        f"{asker.member or ''}@{asker.tenant} {item.source}: oracle "
                        f"visible/retrievable={want}, application={got}"
                    )
        return out

    # --- the protocols --------------------------------------------------------------------------

    def _record_sources(self, session: Any, ctx: Any, question: str) -> list[str]:
        """The stand-in's deterministic record lookup through the real tools."""
        from app.knowledge import composition

        wanted = record_request(question)
        if wanted is None:
            return []
        number, field = wanted
        tools = composition.runtime().tools
        found = tools[FIND].run(session, ctx, "retrieve", {"query": number})
        blocks = [{"content": [{"text": b.text}], "source": b.source} for b in found.blocks]
        student = student_id_for(number, blocks)
        facts: list[str] = []
        if student is not None:
            outcome = tools[FACTS].run(
                session, ctx, "retrieve", {"student_id": student, "fields": [field]}
            )
            facts = [b.source for b in outcome.blocks]
        return [self.corpus_source(s) for s in (*facts, *(b.source for b in found.blocks))]

    def retrieve(self, question: str, asker: Any, k: int) -> Any:
        from app.core.db import tenant_session
        from sos_evals.adapters import Retrieved

        school, ctx = self.context(asker)
        started = time.perf_counter()
        with tenant_session(school.tenant_id, ctx.user_id) as s:
            records = self._record_sources(s, ctx, question)
            found = self._service.search_documents(s, ctx, question, k=k)
        return Retrieved(
            sources=tuple(dict.fromkeys((*records, *(c.source for c in found))))[:k],
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def ask(self, question: str, asker: Any) -> Any:
        from app.core.db import tenant_session
        from app.knowledge.domain import AskRequest
        from sos_evals.adapters import AnswerSegment, AskResult, Citation

        school, ctx = self.context(asker)
        started = time.perf_counter()
        with tenant_session(school.tenant_id, ctx.user_id) as s:
            outcome = self._service.respond(
                s, ctx, AskRequest(question=question, session_id=uuid.uuid4())
            )
        answer = outcome.answer
        segments = tuple(
            AnswerSegment(
                text=seg.text,
                citations=tuple(
                    Citation(source=self.corpus_source(c.source), cited_text=c.cited_text)
                    for c in seg.citations
                ),
            )
            for seg in answer.segments
        )
        if answer.mode == "search_only":
            # Ranked passages without prose: each cited passage is a segment of its own.
            segments = tuple(
                AnswerSegment(
                    text=c.snippet,
                    citations=(
                        Citation(source=self.corpus_source(c.source), cited_text=c.snippet),
                    ),
                )
                for c in answer.cited
            )
        return AskResult(
            segments=segments,
            refused=answer.refused,
            provided_sources=tuple(self.corpus_source(p) for p in answer.provided),
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    # --- M4 circular reading (sos_evals.circulars; FR-CIR-008) ------------------------------

    def read_circular(self, case: Any) -> Any:
        """Store the synthetic circular in school A like the documents module does, index it
        through the real pipeline (whose hook queues the reading), run the reading job and
        return the suggestions the application kept."""
        import app.circulars.service as circulars
        from app.knowledge.ingestion.extract import DOCX_MIME
        from sos_evals.circulars import CircularResult, SuggestedDeadline

        support = self.K
        school = self._schools[sorted(self._schools)[0]]
        doc_id, version_id = uuid.uuid4(), uuid.uuid4()
        data = support.S.docx("".join(support.S.p(line) for line in case.lines))
        key = f"t/{school.tenant_id}/docs/{doc_id}/v1/original.docx"
        owner = school.people["owner"].user_id
        with self._admin.begin() as c:
            c.execute(
                text(
                    "INSERT INTO kb.documents (id, tenant_id, purpose, doc_type, title, "
                    "sensitivity, current_version_id, created_by) VALUES (:d, :t, 'circular', "
                    "'circular', :ti, 'C1', :v, :u)"
                ),
                {"d": doc_id, "t": school.tenant_id, "ti": case.title, "v": version_id, "u": owner},
            )
            c.execute(
                text(
                    "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                    "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                    "(:v, :t, :d, 1, :k, :h, :m, :s, 'ready', :u)"
                ),
                {
                    "v": version_id,
                    "t": school.tenant_id,
                    "d": doc_id,
                    "k": key,
                    "h": hashlib.sha256(data).digest(),
                    "m": DOCX_MIME,
                    "s": len(data),
                    "u": owner,
                },
            )
        support.D.memory_store().put(key, data, DOCX_MIME)
        if support.pipeline().ingest(school.tenant_id, doc_id, version_id) != "indexed":
            return CircularResult(failed=True)
        with self._admin.connect() as c:
            reading_id: uuid.UUID = c.execute(
                text("SELECT id FROM kb.circular_readings WHERE version_id = :v"),
                {"v": version_id},
            ).scalar_one()
        if circulars.run_reading(school.tenant_id, reading_id) != "ready":
            return CircularResult(failed=True)
        with self._admin.connect() as c:
            row = c.execute(
                text("SELECT reference_no, issued_on FROM kb.circular_readings WHERE id = :r"),
                {"r": reading_id},
            ).one()
            found = c.execute(
                text(
                    "SELECT due_on, title, citation FROM kb.circular_suggestions "
                    "WHERE reading_id = :r ORDER BY position"
                ),
                {"r": reading_id},
            ).all()
        return CircularResult(
            deadlines=tuple(
                SuggestedDeadline(due_on=f.due_on, quote=f.citation["quote"], title=f.title)
                for f in found
            ),
            reference_no=row.reference_no,
            issued_on=row.issued_on,
        )

    # --- M6 fee dues from Tally (sos_evals.fees; FR-TALLY-008) --------------------------------

    def _fee_school(self, case: Any) -> tuple[Any, dict[str, uuid.UUID]]:
        """The case's own school: students through ``students.service``, its ledgers and links
        (connector rows as the agent and the accountant would leave them), the flag as given."""
        from app.core.db import tenant_session
        from app.students import service as students
        from app.students.schemas import StudentCreate, ValueIn

        tally = _load("sos_test_tally_support", TESTS / "tally" / "support.py")
        school = self._school(f"fees:{case.id}")
        owner = school.people["owner"]
        register = "admission_register"
        ids: dict[str, uuid.UUID] = {}
        for s in case.students:
            values = [
                ValueIn(attribute_key="full_name", source=register, value=s.name),
                ValueIn(attribute_key="dob", source=register, value="2012-03-14"),
                ValueIn(attribute_key="admission_no", source=register, value=s.admission_no),
            ]
            with tenant_session(school.tenant_id, owner.user_id) as db:
                out = students.create_student(
                    db,
                    self.K.SW.admin_ctx(school),
                    StudentCreate(values=values, section_id=school.ids[f"section:{s.section}"]),
                )
            ids[s.key] = out.id
        parties: dict[str, uuid.UUID] = {}
        for ledger in case.ledgers:
            parties[ledger.key] = tally.seed_party(
                self._admin, school.tenant_id, owner.user_id, ledger=ledger.name
            )
            with self._admin.begin() as c:
                c.execute(
                    text(
                        "UPDATE ops.tally_parties SET closing_balance = :b, group_name = :g "
                        "WHERE id = :i"
                    ),
                    {"b": ledger.balance, "g": ledger.group, "i": parties[ledger.key]},
                )
        for ledger_key, student_key in case.links:
            tally.seed_link(
                self._admin,
                school.tenant_id,
                parties[ledger_key],
                ids[student_key],
                owner.user_id,
            )
        tally.set_flag(self._admin, school.tenant_id, enabled=case.connector_on)
        return school, ids

    def ask_fees(self, case: Any) -> Any:
        """One fee case through ``knowledge.service`` (see the module docstring)."""
        from app.authz.catalog import implicit_permissions, system_roles
        from app.authz.context import Scopes, UserContext
        from app.core.db import tenant_session
        from app.knowledge.domain import AskRequest
        from sos_evals.fees import FeeCitation, FeeResult

        school, _ = self._fee_school(case)
        member = self.K.W.add_member(self._admin, school.tenant_id, [case.asker])
        template = system_roles()[case.asker]
        scoped = {p for p in template.permission_keys if (g := template.grant(p)) and g.scoped}
        target = next((s for s in case.students if s.key == case.student), case.students[0])
        sections = frozenset({school.ids[f"section:{target.section}"]}) if scoped else frozenset()
        ctx = UserContext(
            user_id=member.user_id,
            tenant_id=school.tenant_id,
            membership_id=member.membership_id,
            roles=frozenset({case.asker}),
            permissions=frozenset(set(template.permission_keys) | set(implicit_permissions())),
            scopes=Scopes(school=not scoped, section_ids=sections),
            mfa=True,
            auth_time=None,
            scoped_permissions=frozenset(scoped),
        )
        with tenant_session(school.tenant_id, ctx.user_id) as s:
            outcome = self._service.respond(
                s, ctx, AskRequest(question=case.question, session_id=uuid.uuid4())
            )
        answer = outcome.answer
        return FeeResult(
            text=" ".join(seg.text for seg in answer.segments),
            citations=tuple(
                FeeCitation(source=c.source, cited_text=c.cited_text)
                for seg in answer.segments
                for c in seg.citations
            ),
            provided_sources=tuple(answer.provided),
        )

    # --- Ask conversations (sos_evals.conversations; ADR-0034) --------------------------------

    def run_conversation(self, case: Any) -> Any:
        """One scripted conversation in its own school (see :class:`_ConversationRunner`)."""
        return _run_conversation(self, case)


class _ConversationRunner:
    """One scripted conversation case (``sos_evals.conversations``) in its own school, every
    step through ``knowledge.service`` like the routes call it (``respond``: the whole answer in
    the caller's transaction), the rolling summary jobs run right after each step (the worker's
    outbox consumer), and what the model was sent read from the recording stand-in."""

    def __init__(self, adapter: AppFakeAdapter, case: Any) -> None:
        self.adapter = adapter
        self.case = case
        self.school = adapter._school(f"conv:{case.id}")
        self.tenant = self.school.tenant_id
        self.members: dict[str, Any] = {}
        self.ctx: dict[str, Any] = {}
        self.docs: dict[str, uuid.UUID] = {}
        self.conversations: dict[tuple[str, str], uuid.UUID] = {}
        self.step_query: dict[int, uuid.UUID] = {}
        self.query_step: dict[uuid.UUID, int] = {}
        self.summaries_seen: set[uuid.UUID] = set()
        for person in case.people:
            self._person(person)
        for doc in case.docs:
            self.docs[doc.key] = self._document(doc)

    def _person(self, person: Any) -> None:
        from app.authz.catalog import implicit_permissions, system_roles
        from app.authz.context import Scopes, UserContext

        k = self.adapter.K
        scopes = (
            [("section", self.school.ids[f"section:{person.section}"])] if person.section else None
        )
        member = k.W.add_member(self.adapter._admin, self.tenant, [person.role], scopes=scopes)
        template = system_roles()[person.role]
        scoped = {p for p in template.permission_keys if (g := template.grant(p)) and g.scoped}
        sections = (
            frozenset({self.school.ids[f"section:{person.section}"]})
            if person.section
            else frozenset()
        )
        self.members[person.key] = member
        self.ctx[person.key] = UserContext(
            user_id=member.user_id,
            tenant_id=self.tenant,
            membership_id=member.membership_id,
            roles=frozenset({person.role}),
            permissions=frozenset(set(template.permission_keys) | set(implicit_permissions())),
            scopes=Scopes(school=not sections, section_ids=sections),
            mfa=True,
            auth_time=None,
            scoped_permissions=frozenset(scoped),
        )

    def _acl(self, audience: str) -> list[tuple[str, str]]:
        if audience == "all":
            return list(self.adapter.K.ALL_ROLES_ACL)
        if audience == "owner_only":
            return [("role", "owner")]
        if audience == "member_u1":
            return [("membership", str(self.members["U1"].membership_id))]
        raise ValueError(f"unknown audience {audience}")

    def _document(self, doc: Any) -> uuid.UUID:
        doc_id, _version = self.adapter.K.text_document(
            self.adapter._admin,
            self.school,
            doc.content,
            title=doc.title,
            acl=self._acl(doc.audience),
        )
        value: uuid.UUID = doc_id
        return value

    def _revoke(self, key: str) -> None:
        doc_id = self.docs[key]
        with self.adapter._admin.begin() as c:
            c.execute(text("DELETE FROM kb.document_acl WHERE document_id = :d"), {"d": doc_id})
            c.execute(
                text(
                    "INSERT INTO kb.document_acl (tenant_id, document_id, principal_type, "
                    "principal_ref) VALUES (:t, :d, 'role', 'owner')"
                ),
                {"t": self.tenant, "d": doc_id},
            )
        self.adapter.K.pipeline().refresh_acl(self.tenant, doc_id)

    def _revise(self, key: str) -> None:
        from app.knowledge.ingestion.extract import DOCX_MIME

        k = self.adapter.K
        doc_id = self.docs[key]
        content = next(d.revised for d in self.case.docs if d.key == key)
        data = k.S.docx("".join(k.S.p(line) for line in content.splitlines()))
        version_id = uuid.uuid4()
        object_key = f"t/{self.tenant}/docs/{doc_id}/v2/original.docx"
        with self.adapter._admin.begin() as c:
            c.execute(
                text(
                    "INSERT INTO kb.document_versions (id, tenant_id, document_id, version_no, "
                    "object_key, sha256, mime_type, size_bytes, status, created_by) VALUES "
                    "(:v, :t, :d, 2, :k, :h, :m, :n, 'ready', :u)"
                ),
                {
                    "v": version_id,
                    "t": self.tenant,
                    "d": doc_id,
                    "k": object_key,
                    "h": hashlib.sha256(data).digest(),
                    "m": DOCX_MIME,
                    "n": len(data),
                    "u": self.school.people["owner"].user_id,
                },
            )
            c.execute(
                text("UPDATE kb.documents SET current_version_id = :v WHERE id = :d"),
                {"v": version_id, "d": doc_id},
            )
        k.D.memory_store().put(object_key, data, DOCX_MIME)
        if k.pipeline().ingest(self.tenant, doc_id, version_id) != "indexed":
            raise RuntimeError(f"revised document {key} was not indexed")

    def _summaries(self) -> None:
        """The worker's part: every queued rolling-summary job of this school, run now."""
        from app.knowledge import conversations, service

        with self.adapter._admin.connect() as c:
            rows = c.execute(
                text(
                    "SELECT id, payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e "
                    "ORDER BY created_at"
                ),
                {"t": self.tenant, "e": conversations.SUMMARY_EVENT},
            ).all()
        for row in rows:
            if row.id not in self.summaries_seen:
                self.summaries_seen.add(row.id)
                service.summarise_conversation(self.tenant, dict(row.payload))

    def _source(self, source: str) -> str:
        from app.knowledge import sources

        try:
            ref = sources.parse(source)
        except ValueError:
            return source
        if ref.kind == "conversation" and ref.query_id is not None:
            return f"chat:{self.query_step.get(ref.query_id, -1)}"
        by_id = {v: k for k, v in self.docs.items()}
        return by_id.get(ref.object_id, source)

    def _with(self, who: str) -> Any:
        from app.core.db import tenant_session

        return tenant_session(self.tenant, self.ctx[who].user_id)

    def step(self, index: int, step: Any) -> Any:
        from app.knowledge.domain import AskRequest, FollowupsEvent, MemoryEvent, MetaEvent
        from app.knowledge.schemas import MemorySettingsIn
        from sos_evals.conversations import TurnResult

        svc, who = self.adapter._service, step.who
        if step.action == "revoke":
            self._revoke(step.doc)
            return None
        if step.action == "revise":
            self._revise(step.doc)
            return None
        if step.action == "memory_off":
            with self._with(who) as s:
                svc.set_memory_settings(s, self.ctx[who], MemorySettingsIn(enabled=False))
            return None
        if step.action == "delete_conversation":
            with self._with(who) as s:
                svc.delete_conversation(
                    s, self.ctx[who], self.conversations[(who, step.conversation)]
                )
            return None
        target = self.step_query.get(step.target) if step.target is not None else None
        request = AskRequest(
            question=step.question or "",
            conversation_id=(
                None if target is not None else self.conversations.get((who, step.conversation))
            ),
            regenerate_of=target if step.action == "regenerate" else None,
            edit_of=target if step.action == "edit" else None,
        )
        sent = len(self.adapter.transport.sent)
        with self._with(who) as s:
            outcome = svc.respond(s, self.ctx[who], request)
        model_input = str(self.adapter.transport.sent[sent:])
        self._summaries()
        meta = next(e for e in outcome.events if isinstance(e, MetaEvent))
        if target is None and meta.conversation_id is not None:
            self.conversations[(who, step.conversation)] = meta.conversation_id
        self.step_query[index] = outcome.query_id
        self.query_step[outcome.query_id] = index
        followups = next((e.questions for e in outcome.events if isinstance(e, FollowupsEvent)), ())
        memory = tuple((e.action, e.text) for e in outcome.events if isinstance(e, MemoryEvent))
        with self._with(who) as s:
            stored = tuple(m.text for m in svc.list_memories(s, self.ctx[who]))
        answer = outcome.answer
        return TurnResult(
            step=index,
            text=answer.text,
            citations=tuple(self._source(c.source) for c in answer.cited),
            provided=tuple(self._source(p) for p in answer.provided),
            model_input=model_input,
            followups=tuple(followups),
            memory=memory,
            cached_from=self.query_step.get(meta.cached_from) if meta.cached_from else None,
            memories_after=stored,
        )


def _run_conversation(adapter: AppFakeAdapter, case: Any) -> Any:
    from sos_evals.conversations import ConversationRun

    runner = _ConversationRunner(adapter, case)
    turns = [runner.step(i, step) for i, step in enumerate(case.steps)]
    return ConversationRun(turns=tuple(t for t in turns if t is not None))


def build(corpus: Mapping[str, Any], items: Iterable[Any]) -> AppFakeAdapter:
    return AppFakeAdapter(corpus, items)


def main(argv: Sequence[str] | None = None) -> int:
    from sos_evals import cli, stubs

    cast(dict[str, Any], stubs.STUBS)[AppFakeAdapter.name] = build
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
