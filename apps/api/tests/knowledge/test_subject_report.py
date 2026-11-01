"""Ask history in the data principal's access report and erasure (FR-DPDP-009, FR-DPDP-013,
PRV-031, PRV-032; R3-1A). Synthetic data only."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.core.subjects import ErasureOutcome, ReportSection, Subject
from app.knowledge import conversations, memory, service
from app.knowledge import repository as repo
from app.knowledge.keys import QUESTION_PURPOSE, question_key
from app.students import crypto

pytestmark = pytest.mark.db

SUBJECTS_YAML = Path(__file__).resolve().parents[2] / "app" / "privacy" / "subjects.yaml"
NOW = dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.UTC)
CLASSES = {"ask_history", "ask_memory"}


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


K = _load("sos_test_ask_support", Path(__file__).with_name("ask_support.py"))
W = K.W


def _classes() -> set[str]:
    data = yaml.safe_load(SUBJECTS_YAML.read_text(encoding="utf-8"))
    return set(data["contributors"]["knowledge"]["classes"])


def _conversation(s: Any, user_id: uuid.UUID, title: str, **kw: Any) -> uuid.UUID:
    cid = uuid.uuid4()
    blob: bytes | None
    blob, version = conversations.seal(
        s, title, table="kb.conversations", column="title_ciphertext", row_id=cid
    )
    if kw.get("deleted_at") is not None:
        blob = None  # removed from the history: the title is erased (CHECK)
    repo.insert_conversation(
        s,
        {"id": cid, "user_id": user_id, "title_ciphertext": blob, "key_version": version} | kw,
    )
    return cid


def _question(s: Any, user_id: uuid.UUID, question: str, conversation_id: uuid.UUID) -> uuid.UUID:
    qid = uuid.uuid4()
    q, version = crypto.encrypt_value(
        s, question, table="kb.queries", column="question_ciphertext", row_id=qid
    )
    h, _ = crypto.blind_index(
        s, question_key(question), purpose=QUESTION_PURPOSE, key_version=version
    )
    repo.insert_query(
        s,
        {
            "id": qid,
            "session_id": conversation_id,
            "conversation_id": conversation_id,
            "user_id": user_id,
            "question_ciphertext": q,
            "question_hmac": h,
            "key_version": version,
            "mode": "full",
            "status": "answered",
            "created_at": NOW,
        },
    )
    return qid


def _history(tenant: uuid.UUID, user_id: uuid.UUID, tag: str) -> None:
    """Two conversations (one removed from the history), three questions, a memory item and the
    memory switch."""
    with tenant_session(tenant) as s:
        kept = _conversation(s, user_id, f"Fee dates {tag}")
        removed = _conversation(s, user_id, f"Removed chat {tag}", deleted_at=NOW)
        _question(s, user_id, f"When are fees due {tag}?", kept)
        _question(s, user_id, f"And the late fee {tag}?", kept)
        _question(s, user_id, f"Old question {tag}?", removed)
        memory.insert(
            s,
            user_id=user_id,
            text=f"Short answers {tag}",
            source="explicit",
            status="active",
            now=NOW,
        )
        repo.set_memory_enabled(s, user_id, False)


def _rows(admin: Engine, user_id: uuid.UUID) -> dict[str, int]:
    out: dict[str, int] = {}
    with admin.connect() as c:
        for table in ("queries", "conversations", "user_memories", "user_memory_settings"):
            out[table] = int(
                c.execute(
                    text(f"SELECT count(*) FROM kb.{table} WHERE user_id = :u"),
                    {"u": user_id},
                ).scalar_one()
            )
    return out


@pytest.fixture(scope="module")
def school(admin_engine: Engine, app_engine: Engine, platform_engine: Engine) -> Any:
    K.SW.configure_keyring()
    tenant = W.provision_school()
    return tenant


@pytest.fixture
def people(school: uuid.UUID, admin_engine: Engine) -> tuple[Any, Any]:
    subject = W.add_member(admin_engine, school, ["office_staff"])
    other = W.add_member(admin_engine, school, ["office_staff"])
    _history(school, subject.user_id, "mine")
    _history(school, other.user_id, "theirs")
    return subject, other


def _report(tenant: uuid.UUID, subject: Subject) -> list[ReportSection]:
    with tenant_session(tenant) as s:
        return service.subject_report(s, subject)


def _erase(tenant: uuid.UUID, subject: Subject, *, dry_run: bool) -> list[ErasureOutcome]:
    with tenant_session(tenant) as s:
        return service.erase_for_subject(s, subject, CLASSES, dry_run=dry_run)


def test_FR_DPDP_009_member_report_has_counts_and_own_titles(
    school: uuid.UUID, people: tuple[Any, Any]
) -> None:
    subject, _ = people
    sections = _report(school, Subject("member", subject.membership_id))
    assert [s.title for s in sections] == [
        'Questions to "Ask the school"',
        "Conversations",
        'What "Ask the school" remembers',
    ]
    assert [s.class_key for s in sections] == ["ask_history", "ask_history", "ask_memory"]
    assert {s.class_key for s in sections} <= _classes()
    summary = {r[0]: r[1] for r in sections[0].rows}
    assert summary["Questions asked (kept for 180 days)"] == "3"
    assert summary["Conversations"] == "1"
    assert summary["Conversations removed from the history (questions kept until deleted)"] == "1"
    memory = {r[0]: r[1] for r in sections[2].rows}
    assert memory["Memory items (kept until deleted)"] == "1"
    assert memory["Memory switched on"] == "No"
    assert [r[0] for r in sections[1].rows] == ["Fee dates mine"]


def test_FR_DPDP_009_report_has_no_question_texts_or_other_members(
    school: uuid.UUID, people: tuple[Any, Any]
) -> None:
    subject, _ = people
    sections = _report(school, Subject("member", subject.membership_id))
    flat = " ".join(" ".join(r) for s in sections for r in s.rows)
    assert "When are fees due" not in flat
    assert "Short answers" not in flat
    assert "Removed chat" not in flat
    assert "theirs" not in flat


def test_FR_DPDP_009_unsupported_kind_unknown_or_unused_member_is_empty(
    school: uuid.UUID, admin_engine: Engine
) -> None:
    unused = W.add_member(admin_engine, school, ["office_staff"])
    assert _report(school, Subject("member", unused.membership_id)) == []
    assert _report(school, Subject("member", uuid.uuid4())) == []
    assert _report(school, Subject("student", unused.membership_id)) == []
    assert _erase(school, Subject("guardian", unused.membership_id), dry_run=True) == []
    with tenant_session(school) as s:
        assert (
            service.erase_for_subject(
                s, Subject("member", unused.membership_id), {"documents"}, dry_run=True
            )
            == []
        )


def test_FR_DPDP_013_dry_run_counts_and_changes_nothing(
    school: uuid.UUID, people: tuple[Any, Any], admin_engine: Engine
) -> None:
    subject, _ = people
    before = _rows(admin_engine, subject.user_id)
    outcome = _erase(school, Subject("member", subject.membership_id), dry_run=True)
    assert outcome == [ErasureOutcome("ask_history", 3 + 2), ErasureOutcome("ask_memory", 1 + 1)]
    assert _rows(admin_engine, subject.user_id) == before


def test_FR_DPDP_013_erasure_deletes_only_the_members_history(
    school: uuid.UUID, people: tuple[Any, Any], admin_engine: Engine
) -> None:
    subject, other = people
    other_before = _rows(admin_engine, other.user_id)
    outcome = _erase(school, Subject("member", subject.membership_id), dry_run=False)
    assert outcome == [ErasureOutcome("ask_memory", 2), ErasureOutcome("ask_history", 5)]
    assert _rows(admin_engine, subject.user_id) == {
        "queries": 0,
        "conversations": 0,
        "user_memories": 0,
        "user_memory_settings": 0,
    }
    assert _rows(admin_engine, other.user_id) == other_before
    assert _report(school, Subject("member", subject.membership_id)) == []
    with admin_engine.connect() as c:
        summary: Any = c.execute(
            text(
                "SELECT summary FROM audit.events WHERE tenant_id = :t AND action = :a "
                "AND resource_id = :m"
            ),
            {"t": school, "a": "kb.ask_history.erased", "m": subject.membership_id},
        ).scalar_one()
    assert summary == {"queries": 3, "conversations": 2, "memories": 1, "memory_settings": 1}


def test_FR_DPDP_013_history_and_memory_are_erased_separately(
    school: uuid.UUID, admin_engine: Engine
) -> None:
    """``ask_memory`` is its own record class (kept until deleted, not 180 days; docs/05 §13):
    erasing the questions keeps the memory, and erasing the memory keeps the questions."""
    subject = W.add_member(admin_engine, school, ["office_staff"])
    _history(school, subject.user_id, "split")
    member = Subject("member", subject.membership_id)
    with tenant_session(school) as s:
        assert service.erase_for_subject(s, member, {"ask_history"}, dry_run=False) == [
            ErasureOutcome("ask_history", 5)
        ]
    rows = _rows(admin_engine, subject.user_id)
    assert (rows["queries"], rows["conversations"]) == (0, 0)
    assert (rows["user_memories"], rows["user_memory_settings"]) == (1, 1)
    assert [s.class_key for s in _report(school, member)] == ["ask_memory"]
    with tenant_session(school) as s:
        assert service.erase_for_subject(s, member, {"ask_memory"}, dry_run=False) == [
            ErasureOutcome("ask_memory", 2)
        ]
    assert _report(school, member) == []
