"""Test support for the CBSE disclosure (R3-1C; ADR-0051). Synthetic data only.

A CBSE school of its own (boards ``CBSE``) with one member per role and the shared structure,
documents inserted with the documents test support, the fake PDF renderer, and the public call
helpers of tests/public/support.py.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
from sqlalchemy import Engine, text

_HERE = Path(__file__).resolve()
TESTS = _HERE.parents[1]


def _load(name: str, path: Path) -> ModuleType:
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


W = _load("sos_test_api_world", TESTS / "api" / "world.py")
SW = _load("sos_test_student_world", TESTS / "students" / "student_world.py")
D = _load("sos_test_documents_support", TESTS / "documents" / "support.py")
P = _load("sos_test_public_support", TESTS / "public" / "support.py")

BASE = "/api/v1/disclosure"
ROLES = (
    "owner",
    "principal",
    "office_admin",
    "office_staff",
    "accountant",
    "exam_coordinator",
    "class_teacher",
    "teacher",
    "auditor_readonly",
)
REQUIRED = {
    "affiliation_no": "1130001",
    "school_code": "45001",
    "principal_qualification": "M.Sc., B.Ed.",
    "school_email": "office@synthetic-school.example",
    "contact_phone": "040 2345 6789",
}


class FakeRenderer:
    def __init__(self) -> None:
        self.pages: list[str] = []

    def render(self, html: str) -> bytes:
        self.pages.append(html)
        data: bytes = D.pdf()
        return data

    def render_png(self, html: str, *, width_px: int) -> bytes:  # pragma: no cover - unused
        raise NotImplementedError


@dataclass
class CbseSchool:
    tenant_id: uuid.UUID
    people: dict[str, Any] = field(default_factory=dict)
    ids: dict[str, uuid.UUID] = field(default_factory=dict)


def set_boards(admin: Engine, tenant_id: uuid.UUID, boards: list[str]) -> None:
    with admin.begin() as c:
        c.execute(
            text("UPDATE core.tenants SET boards = :b WHERE id = :t"), {"b": boards, "t": tenant_id}
        )


def cbse_school(admin: Engine) -> CbseSchool:
    s = W.School(W.provision_school())
    s.people["owner"] = W.add_member(admin, s.tenant_id, ["owner"])
    W.build_structure(s, s.people["owner"])
    for role in ROLES[1:]:
        scopes: list[tuple[str, uuid.UUID | None]] = []
        if role == "class_teacher":
            scopes = [("section", s.ids["section_9a"])]
        elif role == "teacher":
            scopes = [("class", s.ids["class_x"])]
        s.people[role] = W.add_member(admin, s.tenant_id, [role], scopes=scopes)
    set_boards(admin, s.tenant_id, ["CBSE"])
    out = CbseSchool(tenant_id=s.tenant_id, people=s.people, ids=s.ids)
    return out


def document(
    admin: Engine, school: Any, *, purpose: str = "policy", status: str = "ready", **kw: Any
) -> uuid.UUID:
    D.memory_store()
    doc_id: uuid.UUID = D.make_document(
        admin,
        school.tenant_id,
        school.people["owner"].user_id,
        purpose=purpose,
        doc_type="policy" if purpose == "policy" else purpose,
        status=status,
        **kw,
    )
    return doc_id


def etag(res: httpx.Response) -> str:
    return str(res.headers["ETag"])


def save_draft(
    api: Any,
    who: Any,
    *,
    inputs: dict[str, Any] | None = None,
    documents: dict[str, list[str]] | None = None,
) -> httpx.Response:
    current = api.call(who, "GET", f"{BASE}/draft")
    assert current.status_code == 200, current.text
    res: httpx.Response = api.call(
        who,
        "PUT",
        f"{BASE}/draft",
        json={
            "inputs": inputs if inputs is not None else dict(REQUIRED),
            "documents": documents or {},
        },
        headers={"If-Match": etag(current)},
    )
    return res


def publish(api: Any, who: Any, **kw: Any) -> httpx.Response:
    res: httpx.Response = api.call(
        who, "POST", f"{BASE}/publish", json={"confirm_no_student_data": True}, **kw
    )
    return res


def link_key(api: Any, who: Any) -> str:
    res = api.call(who, "GET", f"{BASE}/link")
    assert res.status_code == 200, res.text
    url = res.json()["url"]
    assert url
    return str(url).rsplit("/", 1)[1]


def scalar(admin: Engine, sql: str, **params: Any) -> Any:
    with admin.connect() as c:
        return c.execute(text(sql), params).scalar()


def rows(admin: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with admin.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), params)]


__all__ = [
    "BASE",
    "REQUIRED",
    "ROLES",
    "SW",
    "CbseSchool",
    "D",
    "FakeRenderer",
    "P",
    "W",
    "cbse_school",
    "document",
    "etag",
    "link_key",
    "publish",
    "rows",
    "save_draft",
    "scalar",
    "set_boards",
]
