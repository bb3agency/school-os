"""Helpers for the public surface tests (synthetic data only). Loaded by path from tests/public."""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.core.db import tenant_session
from app.identity.service_token import SERVICE_TOKEN_HEADER, issue_service_token
from app.public import service
from app.public.config import PARENT_TARGET_TYPES, HandleKind
from app.public.guards import PUBLIC_TOKEN_HEADER

TESTS = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")
_TARGETS: dict[str, str] = dict(PARENT_TARGET_TYPES.items())
EXCHANGE = "/api/v1/public/links/exchange"
CURRENT = "/api/v1/public/links/current"


def student_with_guardian(school: Any) -> tuple[uuid.UUID, uuid.UUID]:
    """A fresh synthetic student in 9A with one guardian (synthetic name, no phone)."""
    student = SW.create(
        school,
        name=f"Synthetica Public {uuid.uuid4().hex[:6]}",
        section_key="section_9a",
        admission_no=f"PB/{uuid.uuid4().hex[:8]}",
    )
    guardian: uuid.UUID = SW.add_guardian(school, student, full_name="Synthetic Guardian")
    return student, guardian


def issue(
    school: Any,
    kind: HandleKind = "parent_notice",
    *,
    guardian_id: uuid.UUID | None = None,
    target_id: uuid.UUID | None = None,
    key: str | None = None,
    target_type: str | None = None,
    by_owner: bool = True,
) -> service.IssuedHandle:
    """A handle of ``school``. A ``parent_notice`` without ``target_id`` points at a fresh
    approved synthetic notice (R2-3H: the page shows it); other kinds default to the target
    type their page expects (``PARENT_TARGET_TYPES``) and a random target id."""
    owner = school.people["owner"]
    with tenant_session(school.tenant_id, owner.user_id if by_owner else None) as db:
        if kind == "parent_notice" and target_id is None:
            target_id = approved_notice(db, owner.user_id)
        return service.issue_handle(
            db,
            kind=kind,
            target_type=target_type or _TARGETS.get(kind, "notice"),
            target_id=target_id or uuid.uuid4(),
            guardian_id=guardian_id,
            key=key,
            created_by=owner.user_id if by_owner else None,
        )


def approved_notice(
    db: Any,
    user_id: uuid.UUID,
    *,
    title: str = "Sports day",
    body: str = "Sports day is on 14/11/2026 at 9:00. Children come in sports uniform.",
    status: str = "approved",
    pdf: bool = False,
) -> uuid.UUID:
    """An approved synthetic notice written in the current school's ``tenant_session`` (test
    setup; the circulars tests cover drafting and approval). ``pdf``: its files are ready."""
    notice_id = uuid.uuid4()
    db.execute(
        text(
            "INSERT INTO ops.parent_notices (id, tenant_id, source, status, title_en, body_en, "
            "title_te, body_te, created_by, approved_by, approved_at, render_status, pdf_key, "
            "png_key, rendered_at) VALUES (:i, core.current_tenant(), 'blank', :s, :t, :b, :t, :b, "
            ":u, :ab, :at, :rs, CASE WHEN :pk THEN 't/' || core.current_tenant() || '/exports/' || "
            "CAST(:i AS text) || '/notice.pdf' END, CASE WHEN :pk THEN 't/' || "
            "core.current_tenant() || '/exports/' || CAST(:i AS text) || '/notice.png' END, :ra)"
        ),
        {
            "i": notice_id,
            "s": status,
            "t": title,
            "b": body,
            "u": user_id,
            "ab": user_id if status == "approved" else None,
            "at": dt.datetime.now(dt.UTC) if status == "approved" else None,
            "rs": "ready" if pdf else None,
            "pk": pdf,
            "ra": dt.datetime.now(dt.UTC) if pdf else None,
        },
    )
    return notice_id


def key_hash(key: str) -> bytes:
    return hashlib.sha256(key.encode("ascii")).digest()


def insert_raw(
    admin: Engine,
    tenant_id: uuid.UUID,
    *,
    kind: str,
    key: str,
    created_at: dt.datetime,
    expires_at: dt.datetime | None,
    target_type: str = "notice",
) -> uuid.UUID:
    """A handle written directly (test setup only), e.g. one that expired long ago."""
    handle_id = uuid.uuid4()
    with admin.begin() as c:
        c.execute(
            text(
                "INSERT INTO core.public_handles (id, tenant_id, kind, key_hash, target_type, "
                "target_id, created_at, expires_at) VALUES (:i, :t, :k, :h, :tt, :ti, :c, :e)"
            ),
            {
                "i": handle_id,
                "t": tenant_id,
                "k": kind,
                "h": key_hash(key),
                "tt": target_type,
                "ti": uuid.uuid4(),
                "c": created_at,
                "e": expires_at,
            },
        )
    return handle_id


def handle_row(admin: Engine, handle_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        row = c.execute(
            text("SELECT * FROM core.public_handles WHERE id = :i"), {"i": handle_id}
        ).one()
    return dict(row._mapping)


def set_tenant_status(admin: Engine, tenant_id: uuid.UUID, status: str) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET status = :s WHERE id = :t"), {"s": status, "t": tenant_id}
        )


def public_call(
    client: TestClient,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict[str, Any] | None = None,
    service_token: bool = True,
    bearer: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Call a public route the way the BFF does: a fresh service token, no user token."""
    h: dict[str, str] = dict(headers or {})
    if service_token:
        h[SERVICE_TOKEN_HEADER] = issue_service_token()
    if bearer is not None:
        h["Authorization"] = f"Bearer {bearer}"
    if token is not None:
        h[PUBLIC_TOKEN_HEADER] = token
    response: httpx.Response = client.request(method, path, headers=h, json=body)
    return response


def exchange(client: TestClient, token: str, **kw: Any) -> httpx.Response:
    return public_call(client, "POST", EXCHANGE, body={"token": token}, **kw)


def current(client: TestClient, token: str, **kw: Any) -> httpx.Response:
    return public_call(client, "GET", CURRENT, token=token, **kw)


def comparable(response: httpx.Response) -> dict[str, Any]:
    """A problem body without the per-request id."""
    body: dict[str, Any] = dict(response.json())
    body.pop("request_id", None)
    return body
