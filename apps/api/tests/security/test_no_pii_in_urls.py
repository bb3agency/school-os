"""Personal data never travels in a URL (SEC-008, CLAUDE.md invariant 5; docs/09 §1, docs/13).

URLs, query string included, are written to access logs we cannot filter: the shared-tier ALB
logs every request line to S3, and proxies and browsers keep them too. Names, phone numbers,
emails, dates of birth, addresses, admission numbers and free-text searches therefore go in a
JSON body (for searches: ``POST .../search``), never in query parameters. IDs, codes, enums,
record dates, cursors and page sizes are fine.

The OpenAPI document is the source of truth: a new query parameter that looks like personal data
or free text fails here until it moves to a body (or, if it provably carries no personal data, is
added to ``NOT_PERSONAL`` with the reason).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from app.openapi_export import document

REPO = Path(__file__).resolve().parents[4]
CADDYFILE = REPO / "deploy" / "dedicated" / "Caddyfile"

# Parameter-name tokens that mean personal data (or a search that may hold it).
PERSONAL_TOKENS = frozenset(
    {
        "q",
        "query",
        "search",
        "text",
        "name",
        "names",
        "phone",
        "mobile",
        "email",
        "dob",
        "birth",
        "address",
        "aadhaar",
        "admission",
        "guardian",
        "parent",
        "father",
        "mother",
        "roll",
    }
)

# Legacy parameters kept working for now, deprecated in OpenAPI with a Deprecation header, and
# redacted in the dedicated-tier Caddy log. Do not add to this set: use a POST body instead.
DEPRECATED_LEGACY = frozenset(
    {
        ("GET", "/api/v1/students", "query"),
        ("GET", "/api/v1/students", "admission_no"),
    }
)

# Free-text-looking parameters that carry no personal data, with the reason.
NOT_PERSONAL = {
    # Control plane: matches school name or tenant code (C1 business data, no student data).
    ("GET", "/api/v1/platform/tenants", "q"): "school name or code (C1)",
}

PLATFORM = "/api/v1/platform/"

# Opaque or bounded strings that are not free text.
OPAQUE = frozenset({"cursor"})


@pytest.fixture(scope="module")
def openapi() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(document())
    return doc


def _resolve(doc: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref:
        target: Any = doc
        for part in ref.removeprefix("#/").split("/"):
            target = target[part]
        return _resolve(doc, target)
    return schema


def _variants(doc: dict[str, Any], schema: dict[str, Any]) -> list[dict[str, Any]]:
    schema = _resolve(doc, schema)
    options = schema.get("anyOf") or schema.get("oneOf")
    if options:
        return [v for o in options for v in _variants(doc, o)]
    if schema.get("type") == "array":
        return _variants(doc, schema.get("items", {}))
    return [schema]


def _is_free_text(doc: dict[str, Any], schema: dict[str, Any]) -> bool:
    """A string that is not a UUID/date/enum/const/pattern-constrained code."""
    for variant in _variants(doc, schema):
        if variant.get("type") != "string":
            continue
        if variant.get("format") or "enum" in variant or "const" in variant:
            continue
        if variant.get("pattern"):
            continue
        return True
    return False


def _query_params(doc: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for path, ops in doc["paths"].items():
        for method, op in ops.items():
            for param in op.get("parameters", []):
                if param.get("in") == "query":
                    out.append((method.upper(), path, param))
    return out


def _looks_personal(name: str) -> bool:
    return bool(set(name.lower().split("_")) & PERSONAL_TOKENS)


def test_SEC_008_no_personal_data_in_query_parameters(openapi: dict[str, Any]) -> None:
    offenders = []
    for method, path, param in _query_params(openapi):
        key = (method, path, param["name"])
        if key in DEPRECATED_LEGACY or key in NOT_PERSONAL or param["name"] in OPAQUE:
            continue
        personal = _looks_personal(param["name"])
        # Control-plane filters are unconstrained codes (status, tier, plan, action) checked in
        # code; the platform schema holds no student data, so only the name check applies there.
        free_text = not path.startswith(PLATFORM) and _is_free_text(
            openapi, param.get("schema", {})
        )
        if personal or free_text:
            offenders.append(f"{method} {path} ?{param['name']}")
    assert not offenders, (
        "Personal data or free text in a query string is written to access logs; "
        "move it to a JSON body (e.g. POST .../search): " + ", ".join(offenders)
    )


def test_SEC_008_legacy_personal_parameters_are_deprecated(openapi: dict[str, Any]) -> None:
    params = {(m, p, q["name"]): q for m, p, q in _query_params(openapi)}
    for key in DEPRECATED_LEGACY:
        assert key in params, f"{key} is gone: drop it from DEPRECATED_LEGACY"
        assert params[key].get("deprecated") is True, key
        assert "POST /api/v1/students/search" in params[key].get("description", ""), key


def test_SEC_008_student_search_takes_personal_data_only_in_the_body(
    openapi: dict[str, Any],
) -> None:
    op = openapi["paths"]["/api/v1/students/search"]["post"]
    assert not [p for p in op.get("parameters", []) if p.get("in") in ("query", "path")]
    body = _resolve(openapi, op["requestBody"]["content"]["application/json"]["schema"])
    assert op["requestBody"]["required"] is True
    for field in ("query", "admission_no", "section_id", "class_id", "status", "limit", "cursor"):
        assert field in body["properties"], field
    assert body.get("additionalProperties") is False
    ok = _resolve(openapi, op["responses"]["200"]["content"]["application/json"]["schema"])
    get = openapi["paths"]["/api/v1/students"]["get"]
    listed = _resolve(openapi, get["responses"]["200"]["content"]["application/json"]["schema"])
    assert ok == listed, "same Page[StudentSummary] as GET /students"


def _caddy_redacted_query_params() -> set[str]:
    text = CADDYFILE.read_text(encoding="utf-8")
    block = re.search(r"request>uri query \{(?P<body>[^}]*)\}", text)
    assert block, "Caddy log filter for request>uri query is missing"
    return set(re.findall(r"^\s*replace\s+(\S+)\s+REDACTED\s*$", block["body"], re.MULTILINE))


def test_SEC_008_dedicated_proxy_log_redacts_personal_query_parameters() -> None:
    """Defense in depth for old clients still sending the deprecated parameters."""
    redacted = _caddy_redacted_query_params()
    assert {"code", "state"} <= redacted, "OIDC callback credentials stay redacted"
    assert {name for _, _, name in DEPRECATED_LEGACY} <= redacted
