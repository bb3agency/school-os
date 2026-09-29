"""Synthetic set-up for the circulars, tasks and notices tests (M4; synthetic data only).

Loaded by path (``--import-mode=importlib``). Builds on ``tests/api/world.py`` (schools A and B,
one member per role) and ``tests/knowledge/ask_support.py`` (the knowledge runtime with the
offline fake provider, documents indexed through the REAL ingestion pipeline):

- :func:`install`: keyring, in-memory object store, knowledge runtime (fake provider recording
  every request body) and fake PDF/PNG renderers.
- :func:`circular`: a circular stored like the documents module stores it and indexed; the
  indexing hook queues its reading; :func:`read_now` runs the worker job in-process.
- :func:`task` / :func:`notice`: rows made through the real service.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import importlib.util
import sys
import uuid
from pathlib import Path
from types import ModuleType
from typing import Any

from sqlalchemy import Engine, text

from app.authz.context import UserContext
from app.circulars import service
from app.circulars.schemas import NoticeCreate, TaskCreate
from app.core import pdf
from app.core.db import tenant_session

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


KB = _load("sos_test_ask_support", TESTS / "knowledge" / "ask_support.py")
W = KB.W
D = KB.D
SW = KB.SW

EN_CIRCULAR = """Office of the District Educational Officer, Guntur.
Rc.No.456/C/2026 Date: 01/10/2026.
Sub: UDISE+ data collection for 2026-27.
Ref: Circular dated 12/08/2026.
All Headmasters are requested to submit the UDISE+ data sheets on or before 15/10/2026.
A review meeting will be conducted on 22/10/2026 at the district office."""
TE_CIRCULAR = """జిల్లా విద్యాశాఖ అధికారి కార్యాలయం, గుంటూరు.
తేదీ: 02/10/2026.
పదవ తరగతి విద్యార్థుల నామినల్ రోల్స్ 20 అక్టోబర్ 2026 లోగా సమర్పించాలి."""
NO_DATES = """Office of the District Educational Officer, Guntur.
Sub: Cleanliness drive.
All schools should keep their premises clean."""


class FakePdf:
    def __init__(self) -> None:
        self.pages: list[str] = []

    def render(self, html: str) -> bytes:
        self.pages.append(html)
        return b"%PDF-1.7 synthetic notice"

    def render_png(self, html: str, *, width_px: int) -> bytes:
        self.pages.append(html)
        return b"\x89PNG\r\n\x1a\nsynthetic"


def install() -> tuple[Any, Any, FakePdf]:
    SW.configure_keyring()
    store = D.memory_store()
    _runtime, transport = KB.install_runtime()
    fake = FakePdf()
    pdf.set_renderer(fake)
    pdf.set_image_renderer(fake)
    return store, transport, fake


def ctx(school: Any, role: str, person: Any | None = None, **scopes: Any) -> UserContext:
    who = person or school.people[role]
    base: UserContext = SW.ctx_for(school.tenant_id, who, role, **scopes)
    return dataclasses.replace(base, auth_time=dt.datetime.now(dt.UTC))


def circular(
    admin: Engine,
    school: Any,
    body: str = EN_CIRCULAR,
    *,
    title: str | None = None,
    acl: list[tuple[str, str]] | None = None,
    sensitivity: str = "C1",
) -> uuid.UUID:
    document_id, _version = KB.text_document(
        admin,
        school,
        body,
        title=title or f"Synthetic circular {W.unique()}",
        acl=KB.ALL_ROLES_ACL if acl is None else acl,
        sensitivity=sensitivity,
    )
    return document_id


def reading_row(admin: Engine, document_id: uuid.UUID) -> dict[str, Any]:
    with admin.connect() as c:
        row = c.execute(
            text("SELECT * FROM kb.circular_readings WHERE document_id = :d ORDER BY version_no"),
            {"d": document_id},
        ).first()
    assert row is not None
    return dict(row._mapping)


def read_now(admin: Engine, school: Any, document_id: uuid.UUID) -> str:
    return service.run_reading(school.tenant_id, reading_row(admin, document_id)["id"])


def read_circular(admin: Engine, school: Any, body: str = EN_CIRCULAR, **kw: Any) -> uuid.UUID:
    document_id = circular(admin, school, body, **kw)
    assert read_now(admin, school, document_id) == "ready"
    return document_id


def suggestions(admin: Engine, document_id: uuid.UUID) -> list[dict[str, Any]]:
    with admin.connect() as c:
        rows = c.execute(
            text(
                "SELECT s.* FROM kb.circular_suggestions s JOIN kb.circular_readings r "
                "ON r.id = s.reading_id WHERE r.document_id = :d ORDER BY s.position"
            ),
            {"d": document_id},
        )
        return [dict(r._mapping) for r in rows]


def task(
    school: Any,
    *,
    owner: Any | None = None,
    by: str = "owner",
    due_on: dt.date | None = None,
    title: str = "Send the fee report",
) -> uuid.UUID:
    actor = ctx(school, by)
    who = owner or school.people[by]
    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = service.create_task(
            db,
            actor,
            TaskCreate(
                title=title,
                owner_membership_id=who.membership_id,
                due_on=due_on or service.today_ist() + dt.timedelta(days=10),
            ),
        )
    return out.id


def notice(school: Any, *, by: str = "owner", approved: bool = False) -> uuid.UUID:
    actor = ctx(school, by)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = service.create_notice(
            db, actor, NoticeCreate(source="staff_text", text="Sports day is on 14/11/2026.")
        )
        if approved:
            from app.circulars.schemas import NoticeUpdate

            draft = service.update_notice(
                db,
                actor,
                out.id,
                NoticeUpdate(
                    title_en="Sports day",
                    body_en="Sports day is on 14/11/2026 at 9:00.",
                    title_te="క్రీడా దినోత్సవం",
                    body_te="క్రీడా దినోత్సవం 14/11/2026 ఉదయం 9:00కు.",
                ),
                out.version,
            )
            service.approve_notice(db, actor, out.id, draft.version)
    return out.id


def render_all(school: Any, notice_id: uuid.UUID) -> str:
    return service.render_notice(school.tenant_id, notice_id)


def db_value(admin: Engine, sql: str, **params: Any) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar_one()


__all__ = [
    "EN_CIRCULAR",
    "NO_DATES",
    "TE_CIRCULAR",
    "FakePdf",
    "circular",
    "ctx",
    "db_value",
    "install",
    "notice",
    "read_circular",
    "read_now",
    "reading_row",
    "render_all",
    "suggestions",
    "task",
]
