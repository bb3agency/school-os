"""Ask finish-up (R2.4) end to end on the real database: ``GET /knowledge/usage``,
``GET /knowledge/resolve`` and the reason on stored search-only answers. Synthetic data only.

Requirements: FR-KB-040 (US-2501), FR-KB-041 (US-2502), FR-KB-042 (US-2503); ADR-0038
decision 4 (billable answers); invariants 1, 2, 3, 5, 8.
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
from urllib.parse import quote

import pytest
from sqlalchemy import Engine, text
from structlog.testing import capture_logs

from app.core.config import get_settings
from app.core.logging import ALLOWED_FIELDS
from app.knowledge import composition
from app.knowledge.gateway.budget import IST
from app.knowledge.gateway.fake import FakeTransport
from app.knowledge.usage import month_bounds
from app.platform import usage as platform_usage
from app.tenancy import service as tenancy

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

ALL_ROLES = K.ALL_ROLES_ACL
USAGE = "/api/v1/knowledge/usage"
PLAIN = "Sports day will be held on 28/11/2026 on the school ground. All classes take part."
SECTION = "Remedial science for section 9C runs on Fridays at 14:00 in the laboratory."
SENSITIVE = "Counsellor visits for the synthetic welfare group are on Mondays at 11:00."
OTHER = "Annual day for the other school is on 05/12/2026 in the main hall."
QUESTION = "When is sports day for Synthetic Vidyalaya?"


@pytest.fixture(scope="module")
def docs(world: Any, admin_engine: Engine) -> Iterator[dict[str, uuid.UUID]]:
    K.install_runtime()
    a, b = world.a, world.b
    out = {
        "plain": K.text_document(admin_engine, a, PLAIN, title="Sports day", acl=ALL_ROLES)[0],
        "section": K.text_document(
            admin_engine,
            a,
            SECTION,
            title="Remedial science 9C",
            acl=[("section", str(a.ids["section_9c"]))],
        )[0],
        "sensitive": K.text_document(
            admin_engine,
            a,
            SENSITIVE,
            title="Welfare",
            acl=ALL_ROLES,
            sensitivity="C3",
            ingest=False,  # C3 is never indexed; its page can still be cited and opened
        )[0],
        "other": K.text_document(admin_engine, b, OTHER, title="Annual day", acl=ALL_ROLES)[0],
    }
    K.enable_ai(admin_engine, a.tenant_id)
    K.enable_ai(admin_engine, b.tenant_id)
    yield out
    composition.set_runtime(None)


@pytest.fixture
def rt(docs: dict[str, uuid.UUID]) -> Iterator[Any]:
    """A fresh runtime per test (its own in-memory spend ledger)."""
    runtime, _transport = K.install_runtime()
    yield runtime
    composition.set_runtime(None)


def _forget(runtime: Any) -> None:
    if runtime.policy is not None:
        runtime.policy.forget()


def _usage(api: Any, who: Any) -> dict[str, Any]:
    res = api.call(who, "GET", USAGE)
    assert res.status_code == 200, res.text
    body: dict[str, Any] = res.json()
    return body


def _queries(
    admin: Engine,
    school: Any,
    person: Any,
    statuses: list[str],
    *,
    at: dt.datetime | None = None,
) -> None:
    """Logged questions (ids and dummy ciphertext only) with these statuses."""
    with admin.begin() as c:
        for status in statuses:
            c.execute(
                text(
                    "INSERT INTO kb.queries (id, tenant_id, session_id, user_id, "
                    "question_ciphertext, question_hmac, key_version, mode, status, created_at) "
                    "VALUES (:i, :t, :s, :u, :q, :h, 1, :m, :st, COALESCE(:at, now()))"
                ),
                {
                    "i": uuid.uuid4(),
                    "t": school.tenant_id,
                    "s": uuid.uuid4(),
                    "u": person.user_id,
                    "q": b"synthetic-ciphertext",
                    "h": uuid.uuid4().bytes * 2,
                    "m": "search_only" if status == "search_only" else "full",
                    "st": status,
                    "at": at,
                },
            )


def _settings(admin: Engine, tenant_id: uuid.UUID, patch: str | None) -> None:
    with admin.begin() as c:
        if patch is None:
            c.execute(
                text(
                    "UPDATE core.tenants SET settings = settings - 'ai_monthly_budget_inr' "
                    "WHERE id = :t"
                ),
                {"t": tenant_id},
            )
        else:
            c.execute(
                text(
                    "UPDATE core.tenants SET settings = settings || CAST(:p AS jsonb) WHERE id = :t"
                ),
                {"p": patch, "t": tenant_id},
            )


# --- GET /knowledge/usage (FR-KB-040) -----------------------------------------------------------


def test_FR_KB_040_usage_counts_billable_answers_for_billing_readers_only(
    world: Any, api: Any, admin_engine: Engine, rt: Any
) -> None:
    a, b = world.a, world.b
    owner, owner_b = a.people["owner"], b.people["owner"]
    before = _usage(api, owner)
    before_b = _usage(api, owner_b)
    assert before["answers_used"] is not None
    month, start, _end = month_bounds(dt.datetime.now(dt.UTC))
    assert before["month"] == month
    _queries(
        admin_engine,
        a,
        a.people["teacher"],
        ["answered", "answered", "answered", "not_found", "search_only", "refused", "cancelled"],
    )
    _queries(admin_engine, a, owner, ["answered", "answered"], at=start - dt.timedelta(hours=1))
    _queries(admin_engine, b, owner_b, ["answered", "answered"])
    after = _usage(api, owner)
    # Only `answered` counts (billing.yaml billable_statuses), only this IST month, only school A.
    assert after["answers_used"] == before["answers_used"] + 3
    assert _usage(api, owner_b)["answers_used"] == before_b["answers_used"] + 2
    for role in ("principal", "accountant"):  # the other tenant.billing.read holders
        assert _usage(api, a.people[role])["answers_used"] == after["answers_used"]
    for role in ("office_admin", "office_staff", "exam_coordinator", "class_teacher", "teacher"):
        seen = _usage(api, a.people[role])
        assert seen["answers_used"] is None
        assert seen["included_answers"] is None
        assert seen["state"] == after["state"]
    # Without kb.ask: 403 (the auditor reads documents but does not ask).
    assert api.call(a.people["auditor_readonly"], "GET", USAGE).status_code == 403


def test_FR_KB_040_usage_matches_the_billing_collectors_daily_counts(
    world: Any, api: Any, admin_engine: Engine, rt: Any
) -> None:
    """The number on the Ask page equals the sum of the control plane's daily counts for the
    month so far (platform.usage.ai_counts_on, ADR-0038 decision 4)."""
    a = world.a
    _queries(admin_engine, a, a.people["owner"], ["answered", "not_found"])
    now = dt.datetime.now(dt.UTC)
    _month, start, _end = month_bounds(now)
    today = now.astimezone(IST).date()
    day, collected = start.date(), 0
    while day <= today:
        collected += platform_usage.ai_counts_on(a.tenant_id, day)[1]
        day += dt.timedelta(days=1)
    assert _usage(api, a.people["owner"])["answers_used"] == collected


def test_FR_KB_040_included_answers_and_near_limit_from_the_bundle(
    world: Any, api: Any, admin_engine: Engine, rt: Any
) -> None:
    a = world.a
    owner, teacher = a.people["owner"], a.people["teacher"]
    _queries(admin_engine, a, teacher, ["answered"])
    try:
        assert _usage(api, owner)["included_answers"] is None  # no bundle
        used = _usage(api, owner)["answers_used"]
        tenancy.set_ai_answer_allowance(a.tenant_id, used * 100)
        _forget(rt)
        got = _usage(api, owner)
        assert (got["state"], got["included_answers"]) == ("ok", used * 100)
        # At 80 % of the included answers: near_limit, for everyone, numbers only for billing.
        tenancy.set_ai_answer_allowance(a.tenant_id, used)
        _forget(rt)
        got = _usage(api, owner)
        assert (got["state"], got["included_answers"], got["search_only_reason"]) == (
            "near_limit",
            used,
            None,
        )
        seen = _usage(api, teacher)
        assert (seen["state"], seen["answers_used"], seen["included_answers"]) == (
            "near_limit",
            None,
            None,
        )
    finally:
        tenancy.set_ai_answer_allowance(a.tenant_id, None)
        _forget(rt)


def test_FR_KB_040_search_only_when_ai_is_off_or_the_budget_is_used_up(
    world: Any, api: Any, admin_engine: Engine, rt: Any
) -> None:
    a = world.a
    teacher = a.people["teacher"]
    try:
        K.enable_ai(admin_engine, a.tenant_id, enabled=False)
        _forget(rt)
        got = _usage(api, teacher)
        assert (got["state"], got["search_only_reason"]) == ("search_only", "ai_disabled")
        K.enable_ai(admin_engine, a.tenant_id)
        _settings(admin_engine, a.tenant_id, '{"ai_monthly_budget_inr": 0}')
        _forget(rt)
        got = _usage(api, teacher)
        assert (got["state"], got["search_only_reason"]) == ("search_only", "ai_budget_exhausted")
    finally:
        K.enable_ai(admin_engine, a.tenant_id)
        _settings(admin_engine, a.tenant_id, None)
        _forget(rt)
    assert _usage(api, teacher)["state"] in {"ok", "near_limit"}


def test_FR_KB_040_kill_switch_reads_ai_disabled(
    world: Any, api: Any, docs: dict[str, uuid.UUID]
) -> None:
    settings = get_settings().model_copy(update={"kb_enabled": False})
    composition.set_runtime(
        composition.build_runtime(settings, transport=FakeTransport(record=True))
    )
    try:
        got = _usage(api, world.a.people["principal"])
        assert (got["state"], got["search_only_reason"]) == ("search_only", "ai_disabled")
    finally:
        composition.set_runtime(None)


# --- GET /knowledge/resolve (FR-KB-042) ---------------------------------------------------------


def _resolve(api: Any, who: Any, source: str) -> Any:
    return api.call(who, "GET", f"/api/v1/knowledge/resolve?source={quote(source, safe='')}")


def _page(document_id: uuid.UUID, version: int = 1) -> str:
    return f"sos://doc/{document_id}/v{version}#p1"


def test_FR_KB_042_resolve_opens_a_document_the_caller_can_read(
    world: Any, api: Any, docs: dict[str, uuid.UUID], rt: Any
) -> None:
    a = world.a
    doc = docs["plain"]
    for role in ("owner", "teacher", "auditor_readonly"):  # kb.ask or document.read
        res = _resolve(api, a.people[role], _page(doc))
        assert res.status_code == 200, (role, res.text)
        assert res.json() == {"kind": "doc", "id": str(doc), "href": f"/documents/{doc}"}


def test_FR_KB_042_resolve_is_404_outside_acl_scope_masking_and_school(
    world: Any, api: Any, docs: dict[str, uuid.UUID], rt: Any
) -> None:
    a = world.a
    owner, teacher = a.people["owner"], a.people["teacher"]
    # Section ACL (9C): the class teacher of 9A and the teacher of class X cannot open it.
    for role in ("class_teacher", "teacher"):
        assert _resolve(api, a.people[role], _page(docs["section"])).status_code == 404
    # A C3 document needs student.read_sensitive (the retrieval rule).
    assert _resolve(api, owner, _page(docs["sensitive"])).status_code == 200
    assert _resolve(api, teacher, _page(docs["sensitive"])).status_code == 404
    # Another school's document, a version that does not exist, a malformed source: one 404.
    for source in (
        _page(docs["other"]),
        _page(docs["plain"], version=9),
        f"sos://doc/{uuid.uuid4()}/v1#p1",
        "sos://doc/not-an-id/v1#p1",
        f"sos://student/{uuid.uuid4()}/field/dob?src=admission_register",
        f"sos://finding/{uuid.uuid4()}",
    ):
        res = _resolve(api, owner, source)
        assert res.status_code == 404, source
        assert str(docs["other"]) not in res.text
    # Not a sos:// source at all: 422.
    assert _resolve(api, owner, "https://evil.example/x").status_code == 422


def test_FR_KB_042_resolve_conversation_sources_only_for_their_owner(
    world: Any, api: Any, admin_engine: Engine, rt: Any
) -> None:
    a = world.a
    owner = a.people["owner"]
    cid = K.conversation_row(a, owner)
    with admin_engine.connect() as c:
        qid: uuid.UUID = c.execute(
            text("SELECT id FROM kb.queries WHERE conversation_id = :c"), {"c": cid}
        ).scalar_one()
    source = f"sos://conversation/{cid}#q{qid}"
    res = _resolve(api, owner, source)
    assert res.status_code == 200, res.text
    assert res.json() == {"kind": "conversation", "id": str(cid), "href": f"/ask/c/{cid}#m-{qid}"}
    for role in ("principal", "auditor_readonly"):  # someone else; without kb.ask
        assert _resolve(api, a.people[role], source).status_code == 404
    assert _resolve(api, world.b.people["owner"], source).status_code == 404


# --- stored search-only reasons (FR-KB-041) -----------------------------------------------------


def test_FR_KB_041_stored_search_only_answer_says_why(
    world: Any, api: Any, admin_engine: Engine, docs: dict[str, uuid.UUID], rt: Any
) -> None:
    a = world.a
    teacher = a.people["teacher"]
    try:
        K.enable_ai(admin_engine, a.tenant_id, enabled=False)
        _forget(rt)
        res, events = K.ask(api, teacher, QUESTION)
        assert res.status_code == 200
        error = next(d for e, d in events if e == "error")
        assert error["type"] == "ai_disabled"
        cid = next(d for e, d in events if e == "meta")["conversation_id"]
    finally:
        K.enable_ai(admin_engine, a.tenant_id)
        _forget(rt)
    detail = api.call(teacher, "GET", f"/api/v1/knowledge/conversations/{cid}").json()
    (message,) = detail["messages"]
    assert (message["mode"], message["search_only_reason"]) == ("search_only", "ai_disabled")


# --- no personal data in logs (invariant 5) -----------------------------------------------------


def test_FR_KB_040_FR_KB_042_usage_and_resolve_log_no_text(
    world: Any, api: Any, docs: dict[str, uuid.UUID], rt: Any
) -> None:
    a = world.a
    with capture_logs() as logs:
        _usage(api, a.people["owner"])
        _usage(api, a.people["teacher"])
        _resolve(api, a.people["owner"], _page(docs["plain"]))
        _resolve(api, a.people["teacher"], _page(docs["sensitive"]))
    for event in logs:
        assert set(event) <= {*ALLOWED_FIELDS, "event", "log_level"}, event
        flat = repr(event)
        for secret in (PLAIN, SENSITIVE, "Sports day", "Welfare", a.people["owner"].display_name):
            assert secret not in flat
