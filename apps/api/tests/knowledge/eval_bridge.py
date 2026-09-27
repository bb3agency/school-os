"""Eval bridge: the RAG harness (``evals/sos_evals``) against the REAL knowledge service.

    make eval EVAL_ADAPTER=app-fake [EVAL_SUITE=full]
    uv run python apps/api/tests/knowledge/eval_bridge.py run --adapter app-fake --suite fast

Test tooling only (docs/06 §13): it lives under ``tests/`` so no production code path imports
``sos_evals``, and the harness itself still imports no application code (it talks to this
module only through its ``RetrievalAdapter`` / ``AskAdapter`` protocols).

What runs for real: a fresh PostgreSQL 16 + pgvector (testcontainers, or the admin URL in
``SOS_TEST_ADMIN_DATABASE_URL``) bootstrapped and migrated like production; two synthetic
schools provisioned through ``tenancy``; every corpus document stored like the documents module
stores it (DOCX, ACL rows, versions, ``is_latest``) and indexed by the real ingestion pipeline
(extraction, Aadhaar masking, chunking, fake embeddings, SQL chunk store); then
``knowledge.service`` for every question: ACL keys, hybrid retrieval filtered in SQL under RLS,
the tool loop through the real gateway (redaction, budget, rate limit, metering ledger),
citation validation, output sanitising, the encrypted query log and the audit event.

What is fake (offline, deterministic; SOS_KB_PROVIDER_MODE=fake):

- embeddings: ``FakeEmbeddingsProvider`` (hashed words and trigrams, no meaning, no translation);
- the model: :class:`EvalFakeTransport`, a stand-in that calls ``search_documents`` with the
  question, keeps only results that share the question's identifiers (tokens with digits) and at
  least half of its content words, answers one cited sentence per kept result (the first
  sentence, as the gateway's own fake does), refuses requests for Aadhaar numbers (system prompt
  rule 5, invariant 4), and says "not found" otherwise. Refusal and recall numbers therefore
  measure this stand-in plus the application's controls, never Claude's judgement; faithfulness
  and correctness need the calibrated judge and the live gateway (docs/06 §13.2).

How the corpus maps onto the product (see docs/06 §13 "app-fake"):

- The harness's visibility oracle knows only roles, sections and classes. Each asker therefore
  becomes a scoped principal of its role: its role's permissions, ``document.read`` limited to
  its sections/classes and no ``document.manage_acl`` (school-wide readers and ACL managers are a
  product rule the oracle cannot express; docs/06 §6 as built).
- Record items (``sos://student/...``) are not seeded: the stand-in model never calls the record
  tools (they are covered by tests/knowledge/test_ask_api.py). Record questions are answered from
  documents or refused, so their recall is 0 and they show as false refusals (both soft).
- Corpus doc types outside the documents module's list (``note``, ``checklist``) are stored as
  ``other``; sources keep the corpus document ids, versions and page 1.
- The gateway's per-school provider rate limit is raised for the run (a whole question set in a
  minute is not traffic).
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
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from sqlalchemy import Engine, create_engine, text

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


def _first_sentence(text_: str) -> str:
    return re.split(r"(?<=[.!?।])\s", text_.strip(), maxsplit=1)[0][:200]


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
        for block in self._blocks(messages[0]):
            if block.get("type") == "text":
                return str(block.get("text", ""))
        return ""

    def _results(self, messages: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
        found: list[Mapping[str, Any]] = []
        for message in messages:
            for block in self._blocks(message):
                if block.get("type") == "tool_result":
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

    def send(self, request: Any) -> Mapping[str, Any]:
        body = request.body
        self.sent.append(body)
        messages = list(body.get("messages") or ())
        question = self._question(messages)
        telugu = bool(re.search(r"[ఀ-౿]", question))
        not_found = "ఇది కనబడలేదు." if telugu else "I could not find this in the records."
        if any(w in question.casefold() for w in AADHAAR_WORDS):
            return self._reply([{"type": "text", "text": not_found}], "end_turn", body)
        tools = {t.get("name") for t in body.get("tools") or ()}
        answered = any(b.get("type") == "tool_result" for m in messages for b in self._blocks(m))
        forbidden = (body.get("tool_choice") or {}).get("type") == "none"
        if not answered and "search_documents" in tools and not forbidden:
            call = {
                "type": "tool_use",
                "id": "toolu_eval_01",
                "name": "search_documents",
                "input": {"query": question},
            }
            return self._reply([call], "tool_use", body)
        kept = []
        for result in self._results(messages):
            text_ = "".join(str(b.get("text", "")) for b in result.get("content") or ())
            if relevant(question, f"{result.get('title', '')} {text_}"):
                kept.append((result, text_))
        if not kept:
            return self._reply([{"type": "text", "text": not_found}], "end_turn", body)
        content = []
        for index, (result, text_) in enumerate(kept[:3]):
            citation = {
                "type": "search_result_location",
                "source": result.get("source"),
                "title": result.get("title"),
                "cited_text": text_,
                "search_result_index": index,
                "start_block_index": 0,
                "end_block_index": 1,
            }
            content.append(
                {
                    "type": "text",
                    "text": f"{result.get('title')}: {_first_sentence(text_)}",
                    "citations": [citation],
                }
            )
        return self._reply(content, "end_turn", body)


# --- the application side -----------------------------------------------------------------------


class _Stack:
    """Database, engines and process state for one run (torn down at exit)."""

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

        self.admin: Engine = create_engine(db.admin_url)
        self.app: Engine = create_engine(db.app_url, pool_size=5)
        self.platform: Engine = create_engine(db.platform_url, pool_size=2)
        core_db.set_engine("app", self.app)
        core_db.set_engine("platform", self.platform)
        set_kv_store(InMemoryKV())
        atexit.register(self.close)

    def close(self) -> None:
        for engine in (self.app, self.platform, self.admin):
            engine.dispose()


class AppFakeAdapter:
    """``RetrievalAdapter`` + ``AskAdapter`` over ``knowledge.service`` (see the docstring)."""

    name = "app-fake"

    def __init__(self, corpus: Mapping[str, Any], items: Iterable[Any]) -> None:
        self._stack = _Stack()
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
        self._schools: dict[str, Any] = {}
        self._members: dict[tuple[str, str], Any] = {}
        items = list(items)
        tenants = sorted({c.tenant for c in corpus.values()} | {i.asker.tenant for i in items})
        for label in tenants:
            self._schools[label] = self._school(label)
        self._seed(corpus)

    # --- set-up ---------------------------------------------------------------------------------

    def _school(self, label: str) -> Any:
        from app.core.db import tenant_session
        from app.tenancy import service as tenancy
        from app.tenancy.schemas import AcademicYearCreate, ClassCreate, SectionCreate

        world = self.K.W
        school = world.School(world.provision_school())
        owner = world.add_member(self._stack.admin, school.tenant_id, ["owner"])
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
            for number, code in CLASS_CODES.items():
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
                for letter in "AB":
                    section = tenancy.create_section(
                        db, SectionCreate(academic_year_id=year.id, class_id=klass.id, name=letter)
                    )
                    school.ids[f"section:{number}{letter}"] = section.id
        self.K.enable_ai(self._stack.admin, school.tenant_id)
        return school

    def _acl(self, school: Any, acl: Any) -> list[tuple[str, str]]:
        entries = [("role", r) for r in acl.roles]
        entries += [("section", str(school.ids[f"section:{s}"])) for s in acl.sections]
        entries += [("class", str(school.ids[f"class:{c}"])) for c in acl.classes]
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
        admin = self._stack.admin
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
                        ":ti, :io, 'C1', :u)"
                    ),
                    {
                        "d": doc_id,
                        "t": school.tenant_id,
                        "dt": item.doc_type if item.doc_type in DOC_TYPES else "other",
                        "ti": item.title,
                        "io": item.issued_on,
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
            for ptype, ref in self._acl(school, item.acl):
                c.execute(
                    text(
                        "INSERT INTO kb.document_acl (tenant_id, document_id, principal_type, "
                        "principal_ref) VALUES (:t, :d, :pt, :r)"
                    ),
                    {"t": school.tenant_id, "d": doc_id, "pt": ptype, "r": ref},
                )
        support.D.memory_store().put(key, data, DOCX_MIME)
        outcome = support.pipeline().ingest(school.tenant_id, doc_id, version_id)
        if outcome != "indexed":
            raise RuntimeError(f"corpus document {doc_id} v{version_no} was not indexed: {outcome}")

    # --- askers ---------------------------------------------------------------------------------

    def _context(self, asker: Any) -> tuple[Any, Any]:
        from app.authz.catalog import implicit_permissions, system_roles
        from app.authz.context import Scopes, UserContext

        school = self._schools[asker.tenant]
        key = (asker.tenant, asker.role)
        if key not in self._members:
            self._members[key] = self.K.W.add_member(
                self._stack.admin, school.tenant_id, [asker.role]
            )
        member = self._members[key]
        template = system_roles()[asker.role]
        permissions = (set(template.permission_keys) | set(implicit_permissions())) - {
            "document.manage_acl"
        }
        scoped = {p for p in template.permission_keys if (g := template.grant(p)) and g.scoped} | {
            "document.read"
        }
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

    # --- the protocols --------------------------------------------------------------------------

    def retrieve(self, question: str, asker: Any, k: int) -> Any:
        from app.core.db import tenant_session
        from sos_evals.adapters import Retrieved

        school, ctx = self._context(asker)
        started = time.perf_counter()
        with tenant_session(school.tenant_id, ctx.user_id) as s:
            found = self._service.search_documents(s, ctx, question, k=k)
        return Retrieved(
            sources=tuple(dict.fromkeys(c.source for c in found)),
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    def ask(self, question: str, asker: Any) -> Any:
        from app.core.db import tenant_session
        from app.knowledge.domain import AskRequest
        from sos_evals.adapters import AnswerSegment, AskResult, Citation

        school, ctx = self._context(asker)
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
                    Citation(source=c.source, cited_text=c.cited_text) for c in seg.citations
                ),
            )
            for seg in answer.segments
        )
        if answer.mode == "search_only":
            # Ranked passages without prose: each cited passage is a segment of its own.
            segments = tuple(
                AnswerSegment(
                    text=c.snippet, citations=(Citation(source=c.source, cited_text=c.snippet),)
                )
                for c in answer.cited
            )
        return AskResult(
            segments=segments,
            refused=answer.refused,
            provided_sources=answer.provided,
            latency_ms=(time.perf_counter() - started) * 1000,
        )


def build(corpus: Mapping[str, Any], items: Iterable[Any]) -> AppFakeAdapter:
    return AppFakeAdapter(corpus, items)


def main(argv: Sequence[str] | None = None) -> int:
    from sos_evals import cli, stubs

    cast(dict[str, Any], stubs.STUBS)[AppFakeAdapter.name] = build
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
