"""Ask conversations end to end (ADR-0034; docs/06 §5, docs/09 Knowledge; FR-KB-008, FR-KB-009,
FR-KB-012): history routes, regenerate and edit, status and follow-up events, recent turns and
the rolling summary as context, query rewrite, the documents-only answer cache and the
caller's own chat search. Real database, real pipeline, offline fake model; every person is a
fresh synthetic member so tests never see each other's history. Synthetic data only.

Invariants 1, 2, 3 (404 for anyone else's conversation), 5 (no titles, questions, answers or
suggestions in logs), 7 (audit), 8 (sources re-checked before history is shown or sent).
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text
from structlog.testing import capture_logs

from app.core.db import tenant_session
from app.knowledge import composition, conversations, service
from app.knowledge import repository as repo
from app.knowledge.config.conversations import load_conversations_config
from app.knowledge.config.llm import load_llm_config
from app.knowledge.config.tools import load_tools_config
from app.knowledge.tools.conversations import SearchMyConversationsTool
from app.knowledge.tools.registry import offered
from app.knowledge.visibility import SourceVisibility

pytestmark = pytest.mark.db


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
world = W.world
api = W.api

ZEBRA = "The Zebra festival is on 14/11/2026 in the school hall. Parents may attend from 10:00."
QUOKKA = "The Quokka camp starts on 02/12/2026 at the sports ground. Bring a water bottle."
PRIVATE = "The Yak committee meets on 05/12/2026 in room Y7 to plan the annual day."
CONFIG = load_llm_config().conversation


@pytest.fixture(scope="module")
def docs(world: Any, admin_engine: Engine) -> Iterator[dict[str, uuid.UUID]]:
    K.install_runtime()
    a = world.a
    out = {
        "zebra": K.text_document(
            admin_engine, a, ZEBRA, title="Zebra festival", acl=K.ALL_ROLES_ACL
        )[0],
        "quokka": K.text_document(
            admin_engine, a, QUOKKA, title="Quokka camp", acl=K.ALL_ROLES_ACL
        )[0],
    }
    K.enable_ai(admin_engine, a.tenant_id)
    K.enable_ai(admin_engine, world.b.tenant_id)
    yield out
    composition.set_runtime(None)


@pytest.fixture
def fake(docs: dict[str, uuid.UUID]) -> Iterator[Any]:
    _rt, transport = K.install_runtime()
    yield transport
    composition.set_runtime(None)


def _person(admin: Engine, world: Any, role: str = "office_staff", **kw: Any) -> Any:
    return W.add_member(admin, world.a.tenant_id, [role], **kw)


def _ask(
    api: Any, who: Any, question: str | None = None, **ids: Any
) -> list[tuple[str, dict[str, Any]]]:
    body: dict[str, Any] = {k: str(v) for k, v in ids.items()}
    if question is not None:
        body["question"] = question
    res = api.call(who, "POST", "/api/v1/knowledge/ask", json=body)
    assert res.status_code == 200, res.text
    events: list[tuple[str, dict[str, Any]]] = K.parse_sse(res.text)
    return events


def _first(events: list[tuple[str, dict[str, Any]]], name: str) -> dict[str, Any]:
    return next(d for e, d in events if e == name)


def _answer_requests(sent: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [b for b in sent if not isinstance((b.get("output_config") or {}).get("format"), dict)]


def _structured(sent: list[Mapping[str, Any]], tag: str) -> list[Mapping[str, Any]]:
    return [
        b
        for b in sent
        if ((b.get("output_config") or {}).get("format") or {}).get("schema", {}).get("description")
        == tag
    ]


def _texts(body: Mapping[str, Any]) -> list[str]:
    return [
        str(b.get("text", "")) for b in body["messages"][0]["content"] if b.get("type") == "text"
    ]


def _block(body: Mapping[str, Any], header: str) -> str | None:
    return next((t for t in _texts(body) if t.startswith(header)), None)


def _detail(api: Any, who: Any, conversation_id: str) -> Any:
    return api.call(who, "GET", f"/api/v1/knowledge/conversations/{conversation_id}")


# --- history routes ----------------------------------------------------------------------------


def test_FR_KB_012_a_question_without_a_conversation_starts_one_named_after_it(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    events = _ask(api, who, "When is the Zebra festival?")
    meta = events[0][1]
    assert meta["title"] == "When is the Zebra festival?"
    assert meta["cached"] is False
    assert meta["cached_from"] is None
    assert meta["summarized"] is False
    assert _first(events, "final")["summarized"] is False
    cid = meta["conversation_id"]
    listed = api.call(who, "GET", "/api/v1/knowledge/conversations").json()
    assert [c["id"] for c in listed["data"]] == [cid]
    assert listed["data"][0]["message_count"] == 1
    assert listed["data"][0]["title"] == "When is the Zebra festival?"
    res = _detail(api, who, cid)
    assert res.status_code == 200, res.text
    assert res.headers["ETag"] == 'W/"1"'
    detail = res.json()
    [message] = detail["messages"]
    assert message["query_id"] == meta["query_id"]
    assert message["question"] == "When is the Zebra festival?"
    assert message["status"] == "answered"
    assert message["mode"] == "full"
    assert message["language"] == "en"
    assert message["answer"] == _first(events, "final")["text"]
    assert "[1]" in message["answer"]
    assert message["superseded"] is False
    assert message["feedback"] is None
    cited = message["citations"][0]
    assert cited["index"] == 1
    assert cited["withheld"] is False
    assert cited["title"]
    assert cited["snippet"]
    assert message["followups"] == _first(events, "followups")["questions"]
    assert message["followups"]
    # Stored encrypted: neither the title nor the follow-ups are plaintext in the database.
    with admin_engine.connect() as c:
        title: Any = c.execute(
            text("SELECT title_ciphertext FROM kb.conversations WHERE id = :i"), {"i": cid}
        ).scalar_one()
        row = c.execute(
            text("SELECT followups_ciphertext, citations_ciphertext FROM kb.queries WHERE id = :i"),
            {"i": meta["query_id"]},
        ).one()
    assert b"Zebra" not in bytes(title)
    assert b"details" not in bytes(row.followups_ciphertext)
    assert b"Zebra" not in bytes(row.citations_ciphertext)


def test_FR_KB_012_a_derived_title_is_cut_at_a_word_and_never_holds_a_12_digit_number(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    question = (
        "Is 1234 5678 9012 the reference printed on the Zebra festival permission letter "
        "for the parents of class nine?"
    )
    title = _ask(api, who, question)[0][1]["title"]
    assert len(title) <= load_conversations_config().titles.derived_max_chars
    assert title.endswith("…")
    assert "5678" not in title
    assert "XXXX XXXX XXXX" in title


def test_FR_KB_012_list_is_pinned_first_then_newest_activity_and_pages(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    ids = [
        _ask(api, who, f"Zebra festival question {n}?")[0][1]["conversation_id"] for n in range(3)
    ]
    pin = api.call(
        who,
        "PATCH",
        f"/api/v1/knowledge/conversations/{ids[0]}",
        json={"pinned": True},
        headers={"If-Match": 'W/"1"'},
    )
    assert pin.status_code == 200, pin.text
    assert pin.json()["pinned"] is True
    page = api.call(who, "GET", "/api/v1/knowledge/conversations", params={"limit": 2}).json()
    assert [c["id"] for c in page["data"]] == [ids[0], ids[2]]
    rest = api.call(
        who,
        "GET",
        "/api/v1/knowledge/conversations",
        params={"limit": 2, "cursor": page["next_cursor"]},
    ).json()
    assert [c["id"] for c in rest["data"]] == [ids[1]]
    assert rest["next_cursor"] is None


def test_FR_KB_012_rename_needs_if_match_and_refuses_aadhaar_like_numbers(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    cid = _ask(api, who, "When is the Zebra festival?")[0][1]["conversation_id"]
    path = f"/api/v1/knowledge/conversations/{cid}"
    ok = api.call(
        who, "PATCH", path, json={"title": "  Zebra   plans  "}, headers={"If-Match": 'W/"1"'}
    )
    assert ok.status_code == 200, ok.text
    assert (ok.json()["title"], ok.json()["version"]) == ("Zebra plans", 2)
    assert ok.headers["ETag"] == 'W/"2"'
    stale = api.call(who, "PATCH", path, json={"title": "Old"}, headers={"If-Match": 'W/"1"'})
    assert stale.status_code == 412
    bad = api.call(
        who, "PATCH", path, json={"title": "Card 2345 6789 0123"}, headers={"If-Match": 'W/"2"'}
    )
    assert bad.status_code == 422
    assert bad.json()["errors"][0]["code"] == "title_personal_number"
    too_long = api.call(
        who, "PATCH", path, json={"title": "x" * 121}, headers={"If-Match": 'W/"2"'}
    )
    assert too_long.status_code == 422
    missing = api.call(who, "PATCH", path, json={"title": "Z"})
    assert (missing.status_code, missing.json()["code"]) == (400, "if_match_required")
    [event] = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "kb.conversation.updated")
        if str(e["resource_id"]) == cid
    ]
    assert event["summary"] == {"changed": ["title"], "pinned": False, "title_chars": 11}


def test_invariant_3_another_persons_conversation_is_404_and_never_joined(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    owner, other = _person(admin_engine, world), _person(admin_engine, world)
    meta = _ask(api, owner, "When is the Zebra festival?")[0][1]
    cid = meta["conversation_id"]
    path = f"/api/v1/knowledge/conversations/{cid}"
    assert _detail(api, other, cid).status_code == 404
    assert (
        api.call(other, "PATCH", path, json={"pinned": True}, headers={"If-Match": 'W/"1"'})
    ).status_code == 404
    assert api.call(other, "DELETE", path).status_code == 404
    res = api.call(
        other,
        "POST",
        "/api/v1/knowledge/ask",
        json={"question": "And the time?", "conversation_id": cid},
    )
    assert res.status_code == 404
    random = api.call(
        owner,
        "POST",
        "/api/v1/knowledge/ask",
        json={"question": "And the time?", "conversation_id": str(uuid.uuid4())},
    )
    assert random.status_code == 404
    # The older session_id never joins someone else's conversation: a new one starts.
    fake.sent.clear()
    joined = _ask(api, other, "And the time?", session_id=cid)
    assert joined[0][1]["conversation_id"] != cid
    assert "When is the Zebra festival?" not in str(fake.sent)
    assert (
        api.call(other, "GET", "/api/v1/knowledge/conversations").json()["data"][0]["id"]
        == (joined[0][1]["conversation_id"])
    )


def test_FR_KB_012_delete_hides_the_conversation_everywhere_and_keeps_the_query_log(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    meta = _ask(api, who, "When is the Zebra festival?")[0][1]
    cid, qid = meta["conversation_id"], meta["query_id"]
    assert api.call(who, "DELETE", f"/api/v1/knowledge/conversations/{cid}").status_code == 204
    assert api.call(who, "DELETE", f"/api/v1/knowledge/conversations/{cid}").status_code == 404
    assert _detail(api, who, cid).status_code == 404
    assert api.call(who, "GET", "/api/v1/knowledge/conversations").json()["data"] == []
    again = api.call(
        who, "POST", "/api/v1/knowledge/ask", json={"question": "Why?", "conversation_id": cid}
    )
    assert again.status_code == 404
    feedback = api.call(
        who, "POST", f"/api/v1/knowledge/queries/{qid}/feedback", json={"feedback": "helpful"}
    )
    assert feedback.status_code == 404
    with admin_engine.connect() as c:
        row = c.execute(
            text("SELECT title_ciphertext, deleted_at FROM kb.conversations WHERE id = :i"),
            {"i": cid},
        ).one()
        kept = c.execute(text("SELECT count(*) FROM kb.queries WHERE id = :i"), {"i": qid})
        assert kept.scalar_one() == 1  # FR-KB-009: purged with the query log, after 180 days
    assert row.title_ciphertext is None
    assert row.deleted_at is not None
    [event] = [
        e
        for e in W.audit_events(admin_engine, world.a.tenant_id, "kb.conversation.deleted")
        if str(e["resource_id"]) == cid
    ]
    assert event["summary"] == {"messages": 1}


# --- regenerate and edit -----------------------------------------------------------------------


def test_FR_KB_012_regenerate_supersedes_the_answer_and_asks_the_same_question_again(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    first = _ask(api, who, "When is the Zebra festival?")[0][1]
    cid = first["conversation_id"]
    again = _ask(api, who, regenerate_of=first["query_id"])
    meta = again[0][1]
    assert meta["conversation_id"] == cid
    assert meta["cached"] is False  # regenerate never uses the cache
    assert _first(again, "final")["status"] == "answered"
    messages = _detail(api, who, cid).json()["messages"]
    assert [(m["query_id"], m["superseded"]) for m in messages] == [
        (first["query_id"], True),
        (meta["query_id"], False),
    ]
    assert messages[1]["question"] == "When is the Zebra festival?"
    assert _detail(api, who, cid).json()["message_count"] == 1
    twice = api.call(
        who, "POST", "/api/v1/knowledge/ask", json={"regenerate_of": first["query_id"]}
    )
    assert (twice.status_code, twice.json()["code"]) == (409, "message_superseded")
    other = api.call(
        _person(admin_engine, world),
        "POST",
        "/api/v1/knowledge/ask",
        json={"regenerate_of": meta["query_id"]},
    )
    assert other.status_code == 404
    both = api.call(
        who,
        "POST",
        "/api/v1/knowledge/ask",
        json={"question": "x", "regenerate_of": meta["query_id"], "edit_of": meta["query_id"]},
    )
    assert both.status_code == 422
    with admin_engine.connect() as c:
        row = c.execute(
            text("SELECT revises, revision FROM kb.queries WHERE id = :i"), {"i": meta["query_id"]}
        ).one()
    assert (str(row.revises), row.revision) == (first["query_id"], "regenerate")


def test_FR_KB_012_edit_supersedes_that_and_later_messages_and_context_drops_them(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    q1 = _ask(api, who, "When is the Zebra festival?")[0][1]
    cid = q1["conversation_id"]
    q2 = _ask(api, who, "Can parents attend the zebra event?", conversation_id=cid)[0][1]
    q3 = _ask(api, who, "What time does the zebra event start?", conversation_id=cid)[0][1]
    fake.sent.clear()
    edited = _ask(api, who, "Where is the Zebra festival held?", edit_of=q2["query_id"])
    earlier = _block(_answer_requests(fake.sent)[0], CONFIG.earlier_questions_header)
    assert earlier is not None
    assert "- When is the Zebra festival?" in earlier
    assert "parents attend" not in earlier
    assert "What time" not in earlier
    messages = _detail(api, who, cid).json()["messages"]
    state = {m["query_id"]: m["superseded"] for m in messages}
    assert state == {
        q1["query_id"]: False,
        q2["query_id"]: True,
        q3["query_id"]: True,
        edited[0][1]["query_id"]: False,
    }


def test_FR_KB_012_only_the_latest_messages_can_be_changed(
    world: Any, api: Any, admin_engine: Engine, fake: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = load_conversations_config()
    small = cfg.model_copy(
        update={"revisions": cfg.revisions.model_copy(update={"max_revisable_messages": 2})}
    )
    monkeypatch.setattr(conversations, "config", lambda: small)
    who = _person(admin_engine, world)
    first = _ask(api, who, "When is the Zebra festival?")[0][1]
    cid = first["conversation_id"]
    for n in range(2):
        _ask(api, who, f"Zebra follow-up number {n}?", conversation_id=cid)
    res = api.call(who, "POST", "/api/v1/knowledge/ask", json={"regenerate_of": first["query_id"]})
    assert (res.status_code, res.json()["code"]) == (409, "message_not_revisable")


# --- events -------------------------------------------------------------------------------------


def test_FR_KB_008_follow_ups_come_in_the_questions_language_after_final(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    events = _ask(api, who, "Zebra festival ఎప్పుడు?")
    names = [e for e, _ in events]
    assert names.index("followups") > names.index("final")
    assert names[-1] == "done"
    questions = _first(events, "followups")["questions"]
    assert questions
    assert all(any("ఀ" <= ch <= "౿" for ch in q) for q in questions)


def test_FR_KB_008_search_only_answers_get_no_follow_ups(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    K.enable_ai(admin_engine, world.a.tenant_id, enabled=False)
    try:
        off = _ask(api, who, "When is the Zebra festival?")
    finally:
        K.enable_ai(admin_engine, world.a.tenant_id)
    assert _first(off, "final")["mode"] == "search_only"
    assert _first(off, "followups") == {"questions": []}
    assert off[0][1]["cached"] is False  # a school with AI off gets no stored AI answer either


# --- context: recent turns, rewrite, summary -----------------------------------------------------


def test_FR_KB_012_a_follow_up_is_rewritten_and_carries_recent_turns_with_answers(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    first = _ask(api, who, "When is the Zebra festival?")[0][1]
    fake.sent.clear()
    events = _ask(api, who, "Can parents attend it?", conversation_id=first["conversation_id"])
    [rewrite] = _structured(fake.sent, conversations.SCHEMA_TAGS["rewrite"])
    assert "When is the Zebra festival?" in str(rewrite)
    body = _answer_requests(fake.sent)[0]
    earlier = _block(body, CONFIG.earlier_questions_header)
    assert earlier is not None
    assert "- When is the Zebra festival?" in earlier
    assert f"  {CONFIG.earlier_answer_label} " in earlier
    # The model gets the standalone rewrite as the question and the user's own words with it.
    texts = _texts(body)
    assert "Zebra" in texts[-1]
    assert (
        _block(body, CONFIG.rewritten_header)
        == f"{CONFIG.rewritten_header}\nCan parents attend it?"
    )
    # What is shown, stored and audited is the original question.
    messages = _detail(api, who, first["conversation_id"]).json()["messages"]
    assert messages[1]["question"] == "Can parents attend it?"
    assert _first(events, "final")["status"] == "answered"


def test_invariant_8_history_never_shows_or_sends_what_the_user_can_no_longer_see(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    doc, _ = K.text_document(
        admin_engine,
        world.a,
        PRIVATE,
        title="Yak committee",
        acl=[("membership", str(who.membership_id))],
    )
    first = _ask(api, who, "When does the Yak committee meet?")
    cid = first[0][1]["conversation_id"]
    assert any(str(doc) in d["source"] for e, d in first if e == "citation")
    with admin_engine.begin() as c:  # access withdrawn
        c.execute(text("DELETE FROM kb.document_acl WHERE document_id = :d"), {"d": doc})
        c.execute(
            text(
                "INSERT INTO kb.document_acl (tenant_id, document_id, principal_type, "
                "principal_ref) VALUES (:t, :d, 'role', 'owner')"
            ),
            {"t": world.a.tenant_id, "d": doc},
        )
    K.pipeline().refresh_acl(world.a.tenant_id, doc)  # the worker's ACL refresh of the index
    message = _detail(api, who, cid).json()["messages"][0]
    assert message["answer"] is None
    assert message["answer_withheld"] is True
    assert message["followups"] == []
    withheld = [c for c in message["citations"] if str(doc) in c["source"]]
    assert withheld
    assert all(c["withheld"] and c["title"] is None and c["snippet"] is None for c in withheld)
    assert "Y7" not in str(message)
    fake.sent.clear()
    _ask(api, who, "Who attends it?", conversation_id=cid)
    earlier = _block(_answer_requests(fake.sent)[0], CONFIG.earlier_questions_header)
    assert earlier is not None
    assert "- When does the Yak committee meet?" in earlier  # the user's own question stays
    assert CONFIG.earlier_answer_label not in earlier
    assert "05/12/2026" not in str(fake.sent)
    assert "Y7" not in str(fake.sent)


def test_FR_KB_012_rolling_summary_is_queued_after_an_answer_and_used_next_time(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    keep = CONFIG.max_earlier_questions
    cid = _ask(api, who, "When is the Zebra festival?")[0][1]["conversation_id"]
    for n in range(keep):
        _ask(api, who, f"Zebra festival detail number {n}?", conversation_id=cid)
    with admin_engine.connect() as c:
        payloads = [
            r.payload
            for r in c.execute(
                text(
                    "SELECT payload FROM ops.outbox WHERE tenant_id = :t AND event_type = :e "
                    "ORDER BY created_at"
                ),
                {"t": world.a.tenant_id, "e": conversations.SUMMARY_EVENT},
            )
            if r.payload["conversation_id"] == cid
        ]
    assert len(payloads) == 1  # queued once the oldest turn left the recent window
    payload = payloads[0]
    assert set(payload) == {"conversation_id", "user_id", "through", "answers"}
    assert "Zebra" not in str(payload)  # ids only
    assert service.summarise_conversation(world.a.tenant_id, payload) == "summarised"
    assert service.summarise_conversation(world.a.tenant_id, payload) == "nothing_to_do"
    with admin_engine.connect() as c:
        row = c.execute(
            text(
                "SELECT summary_ciphertext, summary_sources, summary_through FROM "
                "kb.conversations WHERE id = :i"
            ),
            {"i": cid},
        ).one()
    assert b"Zebra" not in bytes(row.summary_ciphertext)
    assert any(s.startswith("sos://doc/") for s in row.summary_sources)
    fake.sent.clear()
    parking = _ask(api, who, "And the parking?", conversation_id=cid)
    summary = _block(_answer_requests(fake.sent)[0], CONFIG.summary_header)
    assert summary is not None
    assert "When is the Zebra festival?" in summary
    # "Earlier messages summarised": on meta, final and the stored message (web contract).
    assert parking[0][1]["summarized"] is True
    assert _first(parking, "final")["summarized"] is True
    stored = _detail(api, who, cid).json()["messages"]
    assert [m["summarized"] for m in stored] == [False] * (keep + 1) + [True]
    # A summary resting on a source the user can no longer see is forgotten, never sent.
    with admin_engine.begin() as c:
        c.execute(
            text("UPDATE kb.conversations SET summary_sources = CAST(:s AS jsonb) WHERE id = :i"),
            {"s": f'["sos://doc/{uuid.uuid4()}/v1#p1"]', "i": cid},
        )
    fake.sent.clear()
    _ask(api, who, "And the gate?", conversation_id=cid)
    assert _block(_answer_requests(fake.sent)[0], CONFIG.summary_header) is None
    with admin_engine.connect() as c:
        cleared: Any = c.execute(
            text("SELECT summary_ciphertext FROM kb.conversations WHERE id = :i"), {"i": cid}
        ).scalar_one()
    assert cleared is None


# --- answer cache (docs/06 cost and performance design) -----------------------------------------


def _calls(admin: Engine, query_id: str) -> list[str]:
    with admin.connect() as c:
        return list(
            c.execute(
                text("SELECT role FROM kb.llm_calls WHERE query_id = :q ORDER BY occurred_at"),
                {"q": query_id},
            ).scalars()
        )


def test_answer_cache_reuses_an_exact_repeat_for_the_same_access_only(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    question = f"When does the Quokka camp start {uuid.uuid4().hex[:6]}?"
    first = _ask(api, _person(admin_engine, world), question)
    assert first[0][1]["cached"] is False
    second = _ask(api, _person(admin_engine, world), question.upper())
    meta = second[0][1]
    assert (meta["cached"], meta["cached_from"]) == (True, first[0][1]["query_id"])
    assert _first(second, "final")["text"] == _first(first, "final")["text"]
    assert [d["source"] for e, d in second if e == "citation"] == [
        d["source"] for e, d in first if e == "citation"
    ]
    assert _first(second, "followups") == _first(first, "followups")
    assert _calls(admin_engine, meta["query_id"]) == []  # zero tokens, no model call
    with admin_engine.connect() as c:
        row = c.execute(
            text("SELECT cached_from, input_tokens, status FROM kb.queries WHERE id = :i"),
            {"i": meta["query_id"]},
        ).one()
    assert (str(row.cached_from), row.input_tokens, row.status) == (
        first[0][1]["query_id"],
        0,
        "answered",
    )
    # Another reach (a class teacher's scoped document access) never shares it.
    teacher = _person(
        admin_engine, world, "class_teacher", scopes=[("section", world.a.ids["section_9a"])]
    )
    assert _ask(api, teacher, question)[0][1]["cached"] is False


def test_answer_cache_is_invalidated_when_a_cited_document_changes(
    world: Any, api: Any, admin_engine: Engine, fake: Any, docs: dict[str, uuid.UUID]
) -> None:
    question = f"When does the Quokka camp start {uuid.uuid4().hex[:6]}?"
    first = _ask(api, _person(admin_engine, world), question)
    assert first[0][1]["cached"] is False
    K.pipeline().refresh_acl(world.a.tenant_id, docs["quokka"])  # an ACL change
    again = _ask(api, _person(admin_engine, world), question)
    assert again[0][1]["cached"] is False
    with admin_engine.connect() as c:
        invalidated: Any = c.execute(
            text("SELECT cache_invalidated_at FROM kb.queries WHERE id = :i"),
            {"i": first[0][1]["query_id"]},
        ).scalar_one()
    assert invalidated is not None


# --- search my past chats ------------------------------------------------------------------------


def test_search_my_conversations_finds_only_the_callers_own_live_chats(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    mine, theirs = _person(admin_engine, world), _person(admin_engine, world)
    kept = _ask(api, mine, "When does the Quokka camp start?")[0][1]
    gone = _ask(api, mine, "Quokka camp water bottle rule?")[0][1]
    _ask(api, theirs, "Quokka camp for the other person?")
    api.call(mine, "DELETE", f"/api/v1/knowledge/conversations/{gone['conversation_id']}")
    tool = SearchMyConversationsTool(load_tools_config().tools["search_my_conversations"])
    ctx = K.SW.ctx_for(world.a.tenant_id, mine, "office_staff")
    with tenant_session(world.a.tenant_id, mine.user_id) as s:
        outcome = tool.run(s, ctx, "call-1", {"query": "what did I ask about the quokka camp"})
    assert not outcome.is_error
    sources = [b.source for b in outcome.blocks]
    assert sources == [f"sos://conversation/{kept['conversation_id']}#q{kept['query_id']}"]
    assert "other person" not in str(outcome.blocks)
    assert outcome.blocks[0].text.startswith("You asked: When does the Quokka camp start?")


def test_search_my_conversations_is_offered_and_cited_through_the_answer_loop(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    _ask(api, who, "When does the Quokka camp start?")
    ctx = K.SW.ctx_for(world.a.tenant_id, who, "office_staff")
    tools = composition.runtime().tools
    with tenant_session(world.a.tenant_id, who.user_id) as s:
        names = [t.spec.name for t in offered(tools, ctx, s)]
        visibility = SourceVisibility(s, ctx)
        conv = repo.recent_conversations(s, who.user_id, 1)[0]
        source = f"sos://conversation/{conv.id}#q{uuid.uuid4()}"
        assert visibility.visible(source)
        other = K.SW.ctx_for(world.a.tenant_id, _person(admin_engine, world), "office_staff")
        assert not SourceVisibility(s, other).visible(source)
    assert "search_my_conversations" in names


# --- logs ---------------------------------------------------------------------------------------


def test_invariant_5_no_title_question_answer_or_suggestion_in_logs(
    world: Any, api: Any, admin_engine: Engine, fake: Any
) -> None:
    who = _person(admin_engine, world)
    with capture_logs() as logs:
        first = _ask(api, who, "When is the Zebra festival for Ravikanth Synthetica?")
        cid = first[0][1]["conversation_id"]
        _ask(api, who, "Can Ravikanth attend it?", conversation_id=cid)
        api.call(
            who,
            "PATCH",
            f"/api/v1/knowledge/conversations/{cid}",
            json={"title": "Ravikanth festival notes"},
            headers={"If-Match": 'W/"1"'},
        )
        _detail(api, who, cid)
    dumped = str(logs)
    for secret in ("Ravikanth", "Zebra festival is on", "Are there more details", "14/11/2026"):
        assert secret not in dumped
