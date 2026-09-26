"""Idempotency-Key, If-Match and cursor helpers (docs/09 §2)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
import redis
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.authz.context import Scopes, UserContext
from app.authz.http import (
    Idempotency,
    decode_cursor,
    encode_cursor,
    if_match_version,
    paginate,
)
from app.authz.kv import InMemoryKV, RedisKV, set_kv_store
from app.core.errors import BadRequest, Conflict, ServiceUnavailable, ValidationFailed


class Body(BaseModel):
    code: str


def _request(headers: dict[str, str], method: str = "POST") -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": method, "path": "/api/v1/classes", "headers": raw})


def _ctx() -> UserContext:
    return UserContext(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        membership_id=uuid.uuid4(),
        roles=frozenset(),
        permissions=frozenset(),
        scopes=Scopes(),
        mfa=True,
        auth_time=None,
    )


@pytest.fixture
def store() -> Iterator[InMemoryKV]:
    kv = InMemoryKV()
    set_kv_store(kv)
    yield kv
    set_kv_store(None)


def test_docs_09_in_progress_key_is_409(store: InMemoryKV) -> None:
    calls: list[int] = []

    def op() -> Body:
        calls.append(1)
        # A concurrent retry arrives while the first request is still running.
        again = Idempotency(_request({"Idempotency-Key": "key-00000001"}), ctx)
        with pytest.raises(Conflict) as exc:
            again.run(Session(), Body(code="IX"), lambda: Body(code="IX"))
        assert exc.value.code == "idempotency_in_progress"
        return Body(code="IX")

    ctx = _ctx()
    idem = Idempotency(_request({"Idempotency-Key": "key-00000001"}), ctx)
    session = Session()
    res = idem.run(session, Body(code="IX"), op)
    assert res.status_code == 201
    assert calls == [1]
    record = json.loads(store.get(idem.key or "") or b"{}")
    assert record["state"] == "pending", "stored as done only after the commit"


def test_docs_09_failed_operation_releases_key(store: InMemoryKV) -> None:
    ctx = _ctx()
    idem = Idempotency(_request({"Idempotency-Key": "key-00000002"}), ctx)

    def boom() -> Body:
        raise ValidationFailed([{"field": "code", "code": "invalid", "message_key": "x"}])

    with pytest.raises(ValidationFailed):
        idem.run(Session(), Body(code="X"), boom)
    assert store.get(idem.key or "") is None


class _Broken:
    def __getattr__(self, name: str) -> Any:
        def fail(*args: Any, **kwargs: Any) -> Any:
            raise redis.ConnectionError("synthetic outage")

        return fail


def test_docs_09_idempotency_store_outage_fails_closed() -> None:
    set_kv_store(RedisKV(_Broken()))
    try:
        idem = Idempotency(_request({"Idempotency-Key": "key-00000003"}), _ctx())
        with pytest.raises(ServiceUnavailable):
            idem.run(Session(), Body(code="X"), lambda: Body(code="X"))
        plain = Idempotency(_request({}), _ctx())
        assert plain.run(Session(), Body(code="X"), lambda: Body(code="X")).status_code == 201
    finally:
        set_kv_store(None)


def test_docs_09_key_is_scoped_to_tenant_user_and_route() -> None:
    ctx = _ctx()
    a = Idempotency(_request({"Idempotency-Key": "key-00000004"}), ctx).key
    b = Idempotency(_request({"Idempotency-Key": "key-00000004"}), _ctx()).key
    assert a != b
    assert a is not None
    assert str(ctx.tenant_id) in a
    assert "key-00000004" not in a, "raw keys are hashed"
    with pytest.raises(BadRequest):
        Idempotency(_request({"Idempotency-Key": "short"}), ctx)


@pytest.mark.parametrize(("header", "version"), [('W/"3"', 3), ('"12"', 12), (' W/"7" ', 7)])
def test_docs_09_if_match_parsing(header: str, version: int) -> None:
    assert if_match_version(_request({"If-Match": header}, "PATCH")) == version


@pytest.mark.parametrize("header", ["*", 'W/"a"', '"1", "2"', "3"])
def test_docs_09_if_match_rejects_other_forms(header: str) -> None:
    with pytest.raises(BadRequest):
        if_match_version(_request({"If-Match": header}, "PATCH"))


def test_docs_09_cursor_roundtrip_and_paging() -> None:
    assert decode_cursor(encode_cursor({"k": "b"})) == {"k": "b"}
    with pytest.raises(ValidationFailed):
        decode_cursor("%%%")
    with pytest.raises(ValidationFailed):
        paginate(["a"], key=str, cursor=encode_cursor({"k": 1}), limit=1)
    page = paginate(["c", "a", "b"], key=str, cursor=None, limit=2)
    assert page.data == ["a", "b"]
    last = paginate(["c", "a", "b"], key=str, cursor=page.next_cursor, limit=2)
    assert last.data == ["c"]
    assert last.next_cursor is None
