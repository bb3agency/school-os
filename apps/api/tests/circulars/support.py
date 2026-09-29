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
    ids: tuple[uuid.UUID, uuid.UUID] = KB.text_document(
        admin,
        school,
        body,
        title=title or f"Synthetic circular {W.unique()}",
        acl=KB.ALL_ROLES_ACL if acl is None else acl,
        sensitivity=sensitivity,
    )
    return ids[0]


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


def draft_now(school: Any, notice_id: uuid.UUID) -> str:
    """Run the notice-drafting worker job in-process (``circulars.notice.draft_requested``)."""
    return service.run_notice_draft(school.tenant_id, notice_id)


def notice(school: Any, *, by: str = "owner", approved: bool = False) -> uuid.UUID:
    """A notice drafted by the AI from staff text (the worker job run in-process), optionally
    filled in by hand and approved."""
    actor = ctx(school, by)
    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = service.create_notice(
            db, actor, NoticeCreate(source="staff_text", text="Sports day is on 14/11/2026.")
        )
    draft_now(school, out.id)
    if approved:
        from app.circulars.schemas import NoticeUpdate

        with tenant_session(school.tenant_id, actor.user_id) as db:
            current = service.get_notice(db, actor, out.id)
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
                current.version,
            )
            service.approve_notice(db, actor, out.id, draft.version)
    return out.id


def failed_notice(admin: Engine, school: Any) -> tuple[uuid.UUID, int]:
    """A staff-text notice the AI could not draft (``draft_failed``, ``ai_unavailable``)."""
    actor = ctx(school, "owner")
    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = service.create_notice(
            db, actor, NoticeCreate(source="staff_text", text="Sports day is on 14/11/2026.")
        )
    with admin.begin() as c:
        c.execute(
            text(
                "UPDATE ops.parent_notices SET status = 'draft_failed', "
                "draft_error = 'ai_unavailable' WHERE id = :i"
            ),
            {"i": out.id},
        )
    return out.id, out.version


def render_all(school: Any, notice_id: uuid.UUID) -> str:
    return service.render_notice(school.tenant_id, notice_id)


# --- fresh objects for the security suites (authz matrix, BOLA): synthetic rows per call ---------


def fresh_circular(
    admin: Engine, school: Any, *, reading: str | None = None, suggestion: bool = False
) -> tuple[uuid.UUID, uuid.UUID | None, uuid.UUID | None]:
    """A circular every role may see, not indexed (so no reading is queued), optionally with a
    reading row (``ready`` or ``needs_review``) and one suggestion (ids only, synthetic text)."""
    document_id, version_id = KB.text_document(
        admin,
        school,
        "Submit the synthetic report by 15/10/2026.",
        title=f"Synthetic circular {W.unique()}",
        acl=KB.ALL_ROLES_ACL,
        ingest=False,
    )
    reading_id = suggestion_id = None
    if reading is not None:
        reading_id, suggestion_id = uuid.uuid4(), uuid.uuid4()
        with admin.begin() as c:
            c.execute(
                text(
                    "INSERT INTO kb.circular_readings (id, tenant_id, document_id, version_id, "
                    "version_no, status, error_code, completed_at) VALUES (:i, :t, :d, :v, 1, :s, "
                    ":e, now())"
                ),
                {
                    "i": reading_id,
                    "t": school.tenant_id,
                    "d": document_id,
                    "v": version_id,
                    "s": reading,
                    "e": "ai_unavailable" if reading == "needs_review" else None,
                },
            )
            if suggestion:
                c.execute(
                    text(
                        "INSERT INTO kb.circular_suggestions (id, tenant_id, reading_id, position, "
                        "title, due_on, citation) VALUES (:i, :t, :r, 1, 'Submit the report', "
                        "'2026-10-15', CAST(:c AS jsonb))"
                    ),
                    {
                        "i": suggestion_id,
                        "t": school.tenant_id,
                        "r": reading_id,
                        "c": '{"source": "sos://doc/' + str(document_id) + '/v1#p1", '
                        '"passage": 1, "page": 1, "quote": "Submit the synthetic report by '
                        '15/10/2026."}',
                    },
                )
    return document_id, reading_id, suggestion_id if suggestion else None


def complete_notice(school: Any, *, approved: bool = False) -> tuple[uuid.UUID, int]:
    """A draft with both languages filled (version 2), or approved (version 3)."""
    actor = ctx(school, "owner")
    from app.circulars.schemas import NoticeUpdate

    with tenant_session(school.tenant_id, actor.user_id) as db:
        out = service.create_notice(db, actor, NoticeCreate(source="blank"))
        draft = service.update_notice(
            db,
            actor,
            out.id,
            NoticeUpdate(
                title_en="Sports day",
                body_en="Sports day is on 14/11/2026.",
                title_te="క్రీడా దినోత్సవం",
                body_te="క్రీడా దినోత్సవం 14/11/2026న.",
            ),
            out.version,
        )
        version = draft.version
        if approved:
            version = service.approve_notice(db, actor, out.id, draft.version).version
    return out.id, version


def rendered_notice(admin: Engine, school: Any, *, state: str = "ready") -> tuple[uuid.UUID, int]:
    """An approved notice whose files are ``ready`` (keys set) or whose rendering ``failed``."""
    notice_id, version = complete_notice(school, approved=True)
    prefix = f"t/{school.tenant_id}/exports/{notice_id}/notice"
    with admin.begin() as c:
        if state == "ready":
            c.execute(
                text(
                    "UPDATE ops.parent_notices SET render_status = 'ready', pdf_key = :p, "
                    "png_key = :g, rendered_at = now() WHERE id = :i"
                ),
                {"p": prefix + ".pdf", "g": prefix + ".png", "i": notice_id},
            )
        else:
            c.execute(
                text(
                    "UPDATE ops.parent_notices SET render_status = 'failed', "
                    "render_error = 'render_failed' WHERE id = :i"
                ),
                {"i": notice_id},
            )
    return notice_id, version


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
    "draft_now",
    "failed_notice",
    "install",
    "notice",
    "read_circular",
    "read_now",
    "reading_row",
    "render_all",
    "suggestions",
    "task",
]
