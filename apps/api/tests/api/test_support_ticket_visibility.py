"""School support tickets are readable only by the person who raised them and by holders of
``support.manage`` (R-17, FR-PLT-027, OWASP API3, DPDP purpose limit).

Ticket subjects and messages may name students, so a ticket is not shared with every staff role
that may open one. Other staff get 404 (no existence leak) on the detail and the reply, and the
list leaves the ticket out. The operator side is unchanged (tests/platform/test_school_side.py).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import Engine

from .conftest import W

pytestmark = pytest.mark.db

TICKETS = "/api/v1/support/tickets"
BODY = {"category": "other", "subject": "Printer question", "body": "Synthetic question"}


def _ids(api: Any, who: Any, *, tenant: uuid.UUID | None = None) -> set[str]:
    res = api.call(who, "GET", TICKETS, tenant=tenant)
    assert res.status_code == 200, res.text
    return {t["id"] for t in res.json()["data"]}


def test_R_17_ticket_is_visible_to_its_raiser_and_support_managers_only(
    world: Any, api: Any, admin_engine: Engine
) -> None:
    raiser = world.a.people["teacher"]
    res = api.call(raiser, "POST", TICKETS, json=BODY)
    assert res.status_code == 201, res.text
    ticket_id = res.json()["id"]
    detail = f"{TICKETS}/{ticket_id}"

    # The person who raised it reads, lists and replies.
    assert api.call(raiser, "GET", detail).status_code == 200
    assert ticket_id in _ids(api, raiser)
    reply = api.call(raiser, "POST", f"{detail}/messages", json={"body": "More detail"})
    assert reply.status_code == 200, reply.text

    # support.manage holders (owner, principal, office_admin) see every ticket of the school.
    for role in ("owner", "principal", "office_admin"):
        manager = world.a.people[role]
        assert api.call(manager, "GET", detail).status_code == 200, role
        assert ticket_id in _ids(api, manager), role
    answer = api.call(
        world.a.people["principal"], "POST", f"{detail}/messages", json={"body": "Seen"}
    )
    assert answer.status_code == 200, answer.text

    # Other staff who may open tickets get 404 and do not see it in the list.
    for role in ("office_staff", "accountant", "exam_coordinator", "class_teacher"):
        other = world.a.people[role]
        assert api.call(other, "GET", detail).status_code == 404, role
        assert ticket_id not in _ids(api, other), role
        sneaky = api.call(other, "POST", f"{detail}/messages", json={"body": "Peek"})
        assert sneaky.status_code == 404, role

    # A new staff member with no tickets of their own sees an empty list.
    newcomer = W.add_member(admin_engine, world.a.tenant_id, ["office_staff"])
    assert _ids(api, newcomer) == set()

    # Another school never sees it, even its owner (a support.manage holder there).
    b_owner = world.b.people["owner"]
    assert api.call(b_owner, "GET", detail).status_code == 404
    assert ticket_id not in _ids(api, b_owner)
