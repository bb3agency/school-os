"""Listing requests and grants: newest first, status filter, cursor paging (US-103 AC1)."""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Campus, MakeOperator, raise_request

pytestmark = pytest.mark.db
BASE = "/api/v1/breakglass/requests"


def test_US_103_list_is_newest_first_filterable_and_paged(
    campus: Campus, api: Any, make_operator: MakeOperator
) -> None:
    op = make_operator("support_agent")
    requests = [raise_request(campus.tenant_id, op, minutes=m) for m in (15, 30, 45)]
    owner = campus.person("owner")
    first = api.call(owner, "GET", BASE, params={"limit": 2})
    assert first.status_code == 200, first.text
    page = first.json()
    assert [i["duration_minutes"] for i in page["data"]] == [45, 30]
    rest = api.call(owner, "GET", BASE, params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert [i["duration_minutes"] for i in rest["data"]] == [15]
    assert rest["next_cursor"] is None
    newest = page["data"][0]["id"]
    assert api.call(owner, "POST", f"{BASE}/{newest}/deny").status_code == 200
    denied = api.call(owner, "GET", BASE, params={"status": "denied"}).json()["data"]
    assert [i["platform_request_id"] for i in denied] == [str(requests[2])]
    assert api.call(owner, "GET", BASE, params={"status": "bogus"}).status_code == 422
    assert api.call(owner, "GET", BASE, params={"cursor": "e30"}).status_code == 422
