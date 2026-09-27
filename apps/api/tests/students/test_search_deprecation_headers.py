"""Deprecated personal-data parameters on ``GET /students`` announce their end (SEC-008,
FR-STU-010; docs/09 §1 Deprecation, RFC 9745 ``Deprecation``, RFC 8594 ``Sunset``).

``query`` and ``admission_no`` still work until the sunset date, but every response that used
them says so: ``Deprecation`` (when they were deprecated), ``Sunset`` (when they stop working)
and ``Link: rel="successor-version"`` (``POST /students/search``). A plain class/section listing
carries none of these headers. Synthetic data only.
"""

from __future__ import annotations

import json
from email.utils import parsedate_to_datetime
from typing import Any

import pytest

from app.openapi_export import document

GET = "/api/v1/students"
SUNSET = "Thu, 31 Dec 2026 23:59:59 GMT"
SUCCESSOR = '</api/v1/students/search>; rel="successor-version"'


@pytest.mark.db
@pytest.mark.parametrize("params", [{"query": "synthetica"}, {"admission_no": "SYN-0001"}])
def test_SEC_008_deprecated_get_params_announce_sunset(
    world: Any, api: Any, params: dict[str, str]
) -> None:
    res = api.call(world.person("office_admin"), "GET", GET, params=params)
    assert res.status_code == 200, res.text
    assert res.headers["Deprecation"].startswith("@")
    assert res.headers["Sunset"] == SUNSET
    sunset = parsedate_to_datetime(res.headers["Sunset"])
    deprecated_at = int(res.headers["Deprecation"].removeprefix("@"))
    assert deprecated_at < sunset.timestamp()  # announced before it ends
    assert SUCCESSOR in res.headers["Link"]


@pytest.mark.db
def test_SEC_008_plain_listing_has_no_deprecation_headers(world: Any, api: Any) -> None:
    res = api.call(world.person("office_admin"), "GET", GET, params={"status": "active"})
    assert res.status_code == 200, res.text
    for header in ("Deprecation", "Sunset", "Link"):
        assert header not in res.headers


def test_SEC_008_openapi_documents_the_sunset() -> None:
    doc: dict[str, Any] = json.loads(document())
    op = doc["paths"][GET]["get"]
    assert "Sunset" in op["description"]
    assert "31 Dec 2026" in op["description"]
    params = {p["name"]: p for p in op["parameters"]}
    for name in ("query", "admission_no"):
        assert params[name]["deprecated"] is True
        assert "31 Dec 2026" in params[name]["description"]
