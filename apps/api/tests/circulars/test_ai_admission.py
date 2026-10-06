"""Notice drafts and circular readings asked for by a person go through the same per-user AI
admission as "Ask the school" (audit 2026-10-06 R-20; SEC-020; OWASP LLM10, API4).

Before, ``POST /notices`` (AI draft), ``POST /notices/{id}/draft`` and ``POST /circulars/{id}/read``
queued model calls with no per-user limit, so one member could spend the school's AI budget.
Now each takes one unit of the person's question budget (models.yaml
``rate_limit.questions_per_minute_per_user``, the bucket Ask uses): over it, 429
``ai_rate_limited`` with ``Retry-After`` and the RateLimit headers, and nothing is queued. A blank
notice calls no model and is never limited.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine, text

from app.core import ratelimit
from app.knowledge import service as knowledge
from app.knowledge.config.llm import load_llm_config

from .conftest import C

pytestmark = pytest.mark.db


def _limit_to_one() -> None:
    llm = load_llm_config()
    limited = llm.model_copy(
        update={
            "rate_limit": llm.rate_limit.model_copy(update={"questions_per_minute_per_user": 1})
        }
    )
    C.KB.install_runtime(llm_config=limited)


def _queued(admin: Engine, tenant_id: uuid.UUID, event_type: str) -> int:
    with admin.connect() as c:
        return int(
            c.execute(
                text("SELECT count(*) FROM ops.outbox WHERE tenant_id = :t AND event_type = :e"),
                {"t": tenant_id, "e": event_type},
            ).scalar_one()
        )


def _fresh_budgets() -> None:
    limiter = ratelimit.get_rate_limiter()
    if isinstance(limiter.store, ratelimit.InMemoryRateLimitStore):
        limiter.store.reset()


def _assert_limited(res: Any) -> None:
    assert res.status_code == 429, res.text
    assert res.json()["code"] == "ai_rate_limited"
    assert int(res.headers["Retry-After"]) > 0
    assert '"kb_ask";q=1;w=60' in res.headers["RateLimit-Policy"]
    assert '"kb_ask";r=0' in res.headers["RateLimit"]


def test_R_20_notice_drafts_share_the_per_user_ai_budget(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    _limit_to_one()
    school = ai_on.a
    office = school.people["office_staff"]
    body = {"source": "staff_text", "text": "School reopens on 02/06/2027 after the holidays."}
    first = api.call(office, "POST", "/api/v1/notices", json=body)
    assert first.status_code == 202, first.text
    assert '"kb_ask";q=1;w=60' in first.headers["RateLimit-Policy"]
    before = _queued(admin_engine, school.tenant_id, "circulars.notice.draft_requested")
    second = api.call(office, "POST", "/api/v1/notices", json=body)
    _assert_limited(second)
    assert _queued(admin_engine, school.tenant_id, "circulars.notice.draft_requested") == before
    # A blank notice calls no model: never limited.
    blank = api.call(office, "POST", "/api/v1/notices", json={"source": "blank"})
    assert blank.status_code == 202, blank.text
    # The budget is the person's: a colleague still drafts.
    other = api.call(school.people["principal"], "POST", "/api/v1/notices", json=body)
    assert other.status_code == 202, other.text


def test_R_20_ask_and_notice_drafts_use_one_budget(ai_on: Any, api: Any) -> None:
    """The same admission as Ask: a draft spends the unit a question would have used."""
    _limit_to_one()
    office = ai_on.a.people["office_staff"]
    body = {"source": "staff_text", "text": "Sports day is on 14/11/2026."}
    assert api.call(office, "POST", "/api/v1/notices", json=body).status_code == 202
    asked = api.call(office, "POST", "/api/v1/knowledge/ask", json={"question": "When?"})
    assert (asked.status_code, asked.json()["code"]) == (429, "ai_rate_limited")


def test_R_20_retrying_a_failed_draft_is_admitted(
    ai_on: Any, api: Any, admin_engine: Engine
) -> None:
    school = ai_on.a
    notice_id, version = C.failed_notice(admin_engine, school)
    _limit_to_one()
    _fresh_budgets()  # the setup's own draft request is not part of this test
    owner = school.people["owner"]
    path = f"/api/v1/notices/{notice_id}/draft"
    del version  # the failed state was set directly; read the current one
    current = api.call(owner, "GET", f"/api/v1/notices/{notice_id}").json()["version"]
    headers = {"If-Match": f'W/"{current}"'}
    # Spend the owner's one unit, then the retry is refused before anything is queued.
    spend = api.call(owner, "POST", "/api/v1/notices", json={"source": "staff_text", "text": "x y"})
    assert spend.status_code == 202, spend.text
    refused = api.call(owner, "POST", path, json={}, headers=headers)
    _assert_limited(refused)
    still = api.call(owner, "GET", f"/api/v1/notices/{notice_id}").json()
    assert still["status"] == "draft_failed"


def _needs_review(admin: Engine, school: Any) -> uuid.UUID:
    """A circular whose first reading could not run (AI off): it may be read again."""
    C.KB.enable_ai(admin, school.tenant_id, enabled=False)
    try:
        document_id: uuid.UUID = C.circular(admin, school)
        assert C.read_now(admin, school, document_id) == "ai_disabled"
    finally:
        C.KB.enable_ai(admin, school.tenant_id)
    return document_id


def test_R_20_asking_for_a_circular_reading_is_admitted(
    ai_on: Any, api: Any, admin_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[uuid.UUID, uuid.UUID]] = []
    real = knowledge.admit_ai_request

    def spy(ctx: Any, **kw: Any) -> None:
        calls.append((ctx.tenant_id, ctx.user_id))
        real(ctx, **kw)

    monkeypatch.setattr(knowledge, "admit_ai_request", spy)
    school = ai_on.a
    office = school.people["office_staff"]
    first, second = _needs_review(admin_engine, school), _needs_review(admin_engine, school)
    _limit_to_one()
    res = api.call(office, "POST", f"/api/v1/circulars/{first}/read", json={})
    assert res.status_code == 202, res.text
    assert calls == [(school.tenant_id, office.user_id)]
    refused = api.call(office, "POST", f"/api/v1/circulars/{second}/read", json={})
    _assert_limited(refused)
    detail = api.call(office, "GET", f"/api/v1/circulars/{second}").json()
    assert detail["reading"]["can_retry"] is True, "nothing was queued"
