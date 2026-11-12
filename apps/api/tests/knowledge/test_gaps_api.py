"""Knowledge gaps end to end on the real database (FA-06; FR-KB-081..FR-KB-095).

Questions are logged as Ask logs them (encrypted under the school's key), collected by
:func:`app.knowledge.gaps.collect` (the ``knowledge.collect_gaps`` job), listed and closed
through the routes. Documents are ingested through the real pipeline so a verified answer
written from a gap quotes real page text. Synthetic data only.

Invariants 1 (other school 404 and never listed), 2 (403 for roles without
``kb.verified_answer.manage``), 5 (no question text in logs), 7 (audit in the same
transaction), 8 (a verified answer from a gap is checked like any other).
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, text
from structlog.testing import capture_logs

from app.core.db import tenant_session
from app.knowledge import composition, gaps, repository, tasks
from app.students import crypto

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

ALL_ROLES = [("role", r) for r in ("owner", "principal", "office_admin", "office_staff")]
FEES = "Term 2 fees are due on 15/12/2026 at the school office."
SECRET_NAME = "Zorvanthi Synthetica"


@pytest.fixture(scope="module")
def doc(world: Any, admin_engine: Engine) -> Iterator[uuid.UUID]:
    K.install_runtime()
    doc_id = K.text_document(admin_engine, world.a, FEES, title="Fee dates", acl=ALL_ROLES)[0]
    yield doc_id
    composition.set_runtime(None)


def _log_question(
    tenant: uuid.UUID,
    question: str,
    *,
    status: str = "not_found",
    mode: str = "full",
    error: str | None = None,
    feedback: str | None = None,
    route: str | None = "documents",
    at: dt.datetime | None = None,
) -> uuid.UUID:
    query_id = uuid.uuid4()
    moment = at or dt.datetime.now(dt.UTC)
    with tenant_session(tenant) as s:
        blob, version = crypto.encrypt_value(
            s, question, table="kb.queries", column="question_ciphertext", row_id=query_id
        )
        repository.insert_query(
            s,
            {
                "id": query_id,
                "session_id": uuid.uuid4(),
                "user_id": uuid.uuid4(),
                "question_ciphertext": blob,
                "question_hmac": bytes(32),
                "key_version": version,
                "language": "en",
                "mode": mode,
                "route": route,
                "status": status,
                "error": error,
                "feedback": feedback,
                "feedback_at": moment if feedback else None,
                "created_at": moment,
            },
        )
    return query_id


def _collect(tenant: uuid.UUID) -> gaps.CollectResult:
    with tenant_session(tenant) as s:
        return gaps.collect(s)


def _gap_rows(admin: Engine, tenant: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [
            dict(r._mapping)
            for r in c.execute(
                text(
                    "SELECT id, status, not_found_count, unchecked_count, not_helpful_count, "
                    "question_ciphertext FROM kb.knowledge_gaps WHERE tenant_id = :t"
                ),
                {"t": tenant},
            )
        ]


def _open(api: Any, who: Any) -> list[dict[str, Any]]:
    res = api.call(who, "GET", "/api/v1/knowledge/gaps?limit=200")
    assert res.status_code == 200, res.text
    items: list[dict[str, Any]] = res.json()["data"]
    return items


def _find(items: list[dict[str, Any]], word: str) -> dict[str, Any]:
    return next(i for i in items if i["question"] and word in i["question"])


# --- collection (FR-KB-081..084) ---------------------------------------------------------------


def test_FR_KB_081_signals_are_grouped_counted_once_and_kept_redacted(
    world: Any, admin_engine: Engine
) -> None:
    K.SW.configure_keyring()
    school = W.provision_school()
    _log_question(school, "When is the PTM for class 9A? Call 9876543210")
    _log_question(school, "when is the PTM for class 9A?  call 9876543210")
    _log_question(school, "When is the PTM for class 9A? Call 9876543210", feedback="not_helpful",
                  status="answered")  # fmt: skip
    _log_question(school, "Bus route 4 timings", status="search_only", mode="search_only")
    # Not gaps: an outage, a good answer, a memory instruction, a refusal.
    _log_question(school, "Library hours", status="search_only", mode="search_only",
                  error="ai_budget_exhausted")  # fmt: skip
    _log_question(school, "Sports day date", status="answered")
    _log_question(school, "Remember that I teach 9A", route="memory")
    _log_question(school, "Show me all phone numbers", status="refused")

    first = _collect(school)
    assert (first.questions, first.opened) == (4, 2)
    again = _collect(school)
    assert again.questions == 0  # idempotent: each logged question is counted once
    rows = _gap_rows(admin_engine, school)
    assert len(rows) == 2
    counts = sorted(
        (r["not_found_count"], r["unchecked_count"], r["not_helpful_count"]) for r in rows
    )
    assert counts == [(0, 1, 0), (2, 0, 1)]
    for r in rows:  # ciphertext only, under the school's key
        assert b"PTM" not in bytes(r["question_ciphertext"])


def test_FR_KB_084_a_question_asked_again_after_closing_opens_a_new_gap(
    world: Any, api: Any, doc: uuid.UUID
) -> None:
    a = world.a
    question = "Where is the transfer certificate request form?"
    _log_question(a.tenant_id, question)
    _collect(a.tenant_id)
    principal = world.person("principal")
    first = _find(_open(api, principal), "transfer certificate request")
    res = api.call(
        principal,
        "POST",
        f"/api/v1/knowledge/gaps/{first['id']}/close",
        json={"resolution": "dismissed"},
        headers={"If-Match": f'"{first["version"]}"'},
    )
    assert res.status_code == 200, res.text
    _log_question(a.tenant_id, question)
    _collect(a.tenant_id)
    second = _find(_open(api, principal), "transfer certificate request")
    assert second["id"] != first["id"]
    assert second["occurrences"] == 1


def test_FR_KB_092_old_signals_outside_the_look_back_are_not_collected(
    world: Any, admin_engine: Engine
) -> None:
    K.SW.configure_keyring()
    school = W.provision_school()
    _log_question(school, "Old question", at=dt.datetime.now(dt.UTC) - dt.timedelta(days=10))
    assert _collect(school).questions == 0


def test_FR_KB_091_gaps_past_retention_are_purged_per_school(
    world: Any, admin_engine: Engine
) -> None:
    K.SW.configure_keyring()
    school, other = W.provision_school(), W.provision_school()
    for tenant in (school, other):
        _log_question(tenant, "Uniform supplier name")
        _collect(tenant)
    later = dt.datetime.now(dt.UTC) + dt.timedelta(days=91)
    with tenant_session(school) as s:
        assert gaps.purge(s, now=later) == 1
    assert _gap_rows(admin_engine, school) == []
    assert len(_gap_rows(admin_engine, other)) == 1
    with tenant_session(other) as s:
        assert gaps.purge(s) == 0  # within the retention


def test_FR_KB_093_the_jobs_run_school_by_school(world: Any, admin_engine: Engine) -> None:
    K.SW.configure_keyring()
    school = W.provision_school()
    _log_question(school, "Hostel visiting hours")
    result = tasks.collect_gaps_all()
    assert result["failed"] == 0
    assert result["questions"] >= 1
    assert tasks.purge_gaps_all()["failed"] == 0


def test_invariant_5_no_question_text_in_logs(world: Any, admin_engine: Engine) -> None:
    K.SW.configure_keyring()
    school = W.provision_school()
    _log_question(school, f"Did {SECRET_NAME} pay the hostel fee?")
    with capture_logs() as logs:
        _collect(school)
        tasks.collect_gaps_all()
    assert SECRET_NAME not in str(logs)
    assert "hostel fee" not in str(logs)


# --- routes (FR-KB-085..090) -------------------------------------------------------------------


def test_FR_KB_085_keepers_see_redacted_gaps_others_are_refused(
    world: Any, api: Any, admin_engine: Engine, doc: uuid.UUID
) -> None:
    a = world.a
    _log_question(a.tenant_id, "Who signs admission no 40417 bonafide? 9876501234")
    _collect(a.tenant_id)
    principal = world.person("principal")
    item = _find(_open(api, principal), "bonafide")
    assert "40417" not in item["question"]
    assert "9876501234" not in item["question"]
    assert item["status"] == "open"
    assert item["occurrences"] == item["not_found_count"] == 1
    assert "user_id" not in item
    assert "query_id" not in item
    assert _find(_open(api, world.person("office_admin")), "bonafide")["id"] == item["id"]
    for role in ("owner", "office_staff", "class_teacher", "teacher", "auditor_readonly"):
        res = api.call(world.person(role), "GET", "/api/v1/knowledge/gaps")
        assert res.status_code == 403, role
    audits = W.audit_events(admin_engine, a.tenant_id, "kb.gaps.viewed")
    assert audits
    assert set(audits[-1]["summary"]) == {"status", "count", "offset"}


def test_invariant_1_another_school_neither_sees_nor_closes_the_gap(
    world: Any, api: Any, admin_engine: Engine, doc: uuid.UUID
) -> None:
    a, b = world.a, world.b
    _log_question(a.tenant_id, "Is there a canteen menu circular for November?")
    _collect(a.tenant_id)
    item = _find(_open(api, world.person("principal")), "canteen")
    b_principal = W.add_member(admin_engine, b.tenant_id, ["principal"])
    seen = _open(api, b_principal)
    assert item["id"] not in {i["id"] for i in seen}
    res = api.call(
        b_principal,
        "POST",
        f"/api/v1/knowledge/gaps/{item['id']}/close",
        json={"resolution": "dismissed"},
        headers={"If-Match": '"1"'},
    )
    assert res.status_code == 404


def test_FR_KB_087_a_verified_answer_written_from_a_gap_closes_it(
    world: Any, api: Any, admin_engine: Engine, doc: uuid.UUID
) -> None:
    a = world.a
    _log_question(a.tenant_id, "When are term 2 fees due?")
    _collect(a.tenant_id)
    principal = world.person("principal")
    item = _find(_open(api, principal), "term 2 fees")
    path = f"/api/v1/knowledge/gaps/{item['id']}/verified-answer"
    body = {
        "question": item["question"],
        "language": "en",
        "answer_text": "Term 2 fees are due on 15/12/2026.",
        "citations": [{"source": f"sos://doc/{doc}/v1#p1", "cited_text": "due on 15/12/2026"}],
    }
    bad = api.call(
        principal,
        "POST",
        path,
        json={**body, "citations": [{**body["citations"][0], "cited_text": "due on 01/01/2031"}]},
        headers={"If-Match": f'"{item["version"]}"'},
    )
    assert bad.status_code == 422  # grounding rules of verified answers apply unchanged
    assert bad.json()["errors"][0]["code"] == "citation_text_not_found"
    denied = api.call(
        world.person("office_staff"), "POST", path, json=body, headers={"If-Match": '"1"'}
    )
    assert denied.status_code == 403
    stale = api.call(principal, "POST", path, json=body, headers={"If-Match": '"99"'})
    assert stale.status_code == 412
    ok = api.call(principal, "POST", path, json=body, headers={"If-Match": f'"{item["version"]}"'})
    assert ok.status_code == 200, ok.text
    closed = ok.json()
    assert closed["status"] == "closed"
    assert closed["resolution"] == "verified_answer"
    assert closed["closed_by"] == str(principal.membership_id)
    assert closed["closed_by_name"]
    answer_id = closed["verified_answer_id"]
    answers = api.call(principal, "GET", "/api/v1/knowledge/verified-answers").json()["data"]
    answer = next(v for v in answers if v["id"] == answer_id)
    assert answer["verified_by"] == str(principal.membership_id)  # author and date kept
    assert answer["verified_at"]
    again = api.call(
        principal, "POST", path, json=body, headers={"If-Match": f'"{closed["version"]}"'}
    )
    assert again.status_code == 409
    assert again.json()["code"] == "knowledge_gap_closed"
    events = W.audit_events(admin_engine, a.tenant_id, "kb.gap.closed")
    assert item["id"] in {str(e["resource_id"]) for e in events}
    created = W.audit_events(admin_engine, a.tenant_id, "kb.verified_answer.created")
    assert answer_id in {str(e["resource_id"]) for e in created}


def test_FR_KB_088_a_gap_closes_with_a_readable_document_or_is_dismissed(
    world: Any, api: Any, admin_engine: Engine, doc: uuid.UUID
) -> None:
    a = world.a
    _log_question(a.tenant_id, "Where is the fee dates notice?")
    _log_question(a.tenant_id, "What is the lab safety policy?")
    _collect(a.tenant_id)
    principal = world.person("principal")
    items = _open(api, principal)
    fee, lab = _find(items, "fee dates"), _find(items, "lab safety")
    path = f"/api/v1/knowledge/gaps/{fee['id']}/close"
    missing = api.call(
        principal,
        "POST",
        path,
        json={"resolution": "document", "document_id": str(uuid.uuid4())},
        headers={"If-Match": f'"{fee["version"]}"'},
    )
    assert missing.status_code == 422
    assert missing.json()["errors"][0]["code"] == "gap_document_not_found"
    ok = api.call(
        principal,
        "POST",
        path,
        json={"resolution": "document", "document_id": str(doc)},
        headers={"If-Match": f'"{fee["version"]}"'},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["document_id"] == str(doc)
    assert ok.headers["ETag"] == f'"{ok.json()["version"]}"' or ok.headers["ETag"].endswith(
        f'"{ok.json()["version"]}"'
    )
    dismissed = api.call(
        world.person("office_admin"),
        "POST",
        f"/api/v1/knowledge/gaps/{lab['id']}/close",
        json={"resolution": "dismissed"},
        headers={"If-Match": f'"{lab["version"]}"'},
    )
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["status"] == "dismissed"
    assert dismissed.json()["resolution"] is None
    done = api.call(principal, "GET", "/api/v1/knowledge/gaps?status=dismissed")
    assert lab["id"] in {i["id"] for i in done.json()["data"]}
    assert W.audit_events(admin_engine, a.tenant_id, "kb.gap.dismissed")


def test_FR_KB_089_linking_an_answer_needs_an_active_one_the_caller_can_use(
    world: Any, api: Any, doc: uuid.UUID
) -> None:
    a = world.a
    _log_question(a.tenant_id, "Can I pay fees by cheque?")
    _collect(a.tenant_id)
    principal = world.person("principal")
    item = _find(_open(api, principal), "cheque")
    res = api.call(
        principal,
        "POST",
        f"/api/v1/knowledge/gaps/{item['id']}/close",
        json={"resolution": "verified_answer", "verified_answer_id": str(uuid.uuid4())},
        headers={"If-Match": f'"{item["version"]}"'},
    )
    assert res.status_code == 422
    assert res.json()["errors"][0]["code"] == "gap_answer_not_usable"


def test_FR_KB_090_summary_is_counts_only(world: Any, api: Any, doc: uuid.UUID) -> None:
    principal = world.person("principal")
    res = api.call(principal, "GET", "/api/v1/knowledge/gaps/summary")
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body) == {
        "open_gaps",
        "not_found",
        "unchecked",
        "not_helpful",
        "months",
        "retention_days",
    }
    assert len(body["months"]) == gaps.SUMMARY_MONTHS
    assert body["retention_days"] == 90
    assert all(isinstance(v, int) for k, v in body.items() if k != "months")
    denied = api.call(world.person("teacher"), "GET", "/api/v1/knowledge/gaps/summary")
    assert denied.status_code == 403
