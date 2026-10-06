"""API contract guards, checked on every route of the live app (audit 2026-10-06, API4/API3/API6).

The route-by-route audit (docs/security/audit-2026-10-06-api-routes.md) found its issues by hand;
these checks keep the classes of issue from coming back. Each walks ``app.routes`` (not the docs),
so a new route is covered the day it is added. An exception needs an entry in the allowlist next
to the check, with the reason.

1. Every request body model (and every model nested in one) forbids unknown fields (API3, mass
   assignment).
2. Every string in a request body is bounded: a ``max_length``, or an anchored pattern whose
   quantifiers are all bounded. Every list, set and dict in a body has a ``max_length`` (API4).
3. Every list route caps ``limit`` at 200 (docs/09 §2), in the query or in the search body.
4. Every path parameter is a UUID, or a bounded, pattern-checked key (API1, input validation).
5. No response model carries a field named like a secret, a credential or a storage location
   (API3), except the one-time secrets listed with their reason.
6. Every POST that creates a resource or starts a job accepts ``Idempotency-Key`` (docs/09 §2,
   API6).
7. Every PUT/PATCH on a versioned resource (its response has ``version``) honours ``If-Match``
   (docs/09 §2, API6). Where the route keeps it optional for the web's sake it is still honoured
   when sent; the route's own test proves that.
"""

from __future__ import annotations

import re
import types
import typing
import uuid
from collections.abc import Iterator
from enum import Enum
from typing import Any, Literal, Union, get_args, get_origin

import pytest
from fastapi import FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from tests.security.test_route_enumeration import api_routes

from app.main import create_app

MAX_LIMIT = 200


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return create_app()


# --- helpers ---------------------------------------------------------------------------------


def _models(annotation: Any, seen: set[type[BaseModel]]) -> Iterator[type[BaseModel]]:
    """Every BaseModel reachable from ``annotation`` (unions, containers, nested fields)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return
        seen.add(annotation)
        yield annotation
        for field in annotation.model_fields.values():
            yield from _models(field.annotation, seen)
        return
    for arg in get_args(annotation):
        yield from _models(arg, seen)


def body_models(route: APIRoute) -> list[type[BaseModel]]:
    if route.body_field is None:
        return []
    return list(_models(route.body_field.field_info.annotation, set()))


def response_models(route: APIRoute) -> list[type[BaseModel]]:
    return list(_models(route.response_model, set()))


def _all_dependants(dep: Dependant) -> Iterator[Dependant]:
    yield dep
    for sub in dep.dependencies:
        yield from _all_dependants(sub)


def _dependency_names(route: APIRoute) -> set[str]:
    return {
        getattr(d.call, "__name__", type(d.call).__name__)
        for d in _all_dependants(route.dependant)
        if d.call is not None
    }


def _header_names(route: APIRoute) -> set[str]:
    return {
        (p.alias or p.name).lower()
        for d in _all_dependants(route.dependant)
        for p in d.header_params
    }


# Constraint discovery: StringConstraints, annotated_types.MaxLen, FieldInfo and pydantic's
# general metadata all expose ``max_length`` / ``pattern`` attributes.
def _max_length(metadata: list[Any]) -> int | None:
    for m in metadata:
        if isinstance(m, FieldInfo):
            found = _max_length(list(m.metadata))
            if found is not None:
                return found
        value = getattr(m, "max_length", None)
        if isinstance(value, int):
            return value
    return None


def _pattern(metadata: list[Any]) -> str | None:
    for m in metadata:
        if isinstance(m, FieldInfo):
            found = _pattern(list(m.metadata))
            if found is not None:
                return found
        value = getattr(m, "pattern", None)
        if isinstance(value, str):
            return value
    return None


_CLASS = re.compile(r"\[(?:\\.|[^\]\\])*\]")
_ESCAPE = re.compile(r"\\.")


def bounded_pattern(pattern: str) -> bool:
    """An anchored regex whose quantifiers are all bounded matches only short strings."""
    if not (pattern.startswith("^") and pattern.endswith("$")):
        return False
    core = _CLASS.sub("x", _ESCAPE.sub("x", pattern))
    return not re.search(r"[*+]|\{\d*,\}", core)


def _unbounded(annotation: Any, metadata: list[Any], where: str) -> Iterator[str]:
    """Yield a description of each unbounded string or container inside ``annotation``."""
    origin = get_origin(annotation)
    if origin is typing.Annotated:
        inner, *extra = get_args(annotation)
        yield from _unbounded(inner, [*metadata, *extra], where)
        return
    if origin in (Union, types.UnionType):
        for arg in get_args(annotation):
            if arg is not type(None):
                yield from _unbounded(arg, metadata, where)
        return
    if annotation is str:
        if _max_length(metadata) is None and not (
            (p := _pattern(metadata)) is not None and bounded_pattern(p)
        ):
            yield f"{where}: string without max_length"
        return
    if origin in (list, set, frozenset, tuple, dict):
        if _max_length(metadata) is None and origin is not tuple:
            yield f"{where}: {origin.__name__} without max_length"
        for i, arg in enumerate(get_args(annotation)):
            if arg is not Ellipsis:
                yield from _unbounded(
                    arg, [], f"{where}[{'key' if origin is dict and i == 0 else 'item'}]"
                )
        return
    # BaseModel (walked separately), Literal, Enum, numbers, dates, UUIDs, bools: bounded.
    if origin is Literal or (isinstance(annotation, type) and issubclass(annotation, Enum)):
        return


# --- 1. extra="forbid" -------------------------------------------------------------------------


def test_API3_every_request_body_model_forbids_unknown_fields(app: FastAPI) -> None:
    problems = sorted(
        {
            f"{m.__module__}.{m.__qualname__}"
            for _, _, route in api_routes(app)
            for m in body_models(route)
            if m.model_config.get("extra") != "forbid"
        }
    )
    assert problems == []


# --- 2. bounded strings and containers ------------------------------------------------------------


def test_API4_every_string_and_array_in_a_request_body_is_bounded(app: FastAPI) -> None:
    problems: set[str] = set()
    for _, _, route in api_routes(app):
        for model in body_models(route):
            for name, field in model.model_fields.items():
                where = f"{model.__module__}.{model.__qualname__}.{name}"
                problems.update(_unbounded(field.annotation, list(field.metadata), where))
    assert sorted(problems) == []


def test_bounded_pattern_helper() -> None:
    assert bounded_pattern(r"^[a-z][a-z0-9_]{1,63}$")
    assert bounded_pattern(r"^\+?[0-9]{10,13}$")
    assert not bounded_pattern(r"^[^\x00-\x1f]*$")
    assert not bounded_pattern(r"^[a-z]+$")
    assert not bounded_pattern(r"^[a-z]{2,}$")
    assert not bounded_pattern(r"[a-z]{2}")


# --- 3. list routes cap limit ------------------------------------------------------------------


def _is_page(route: APIRoute) -> bool:
    model = route.response_model
    return (
        isinstance(model, type)
        and issubclass(model, BaseModel)
        and ("next_cursor" in model.model_fields)
    )


def _limit_cap(route: APIRoute) -> int | None:
    """The upper bound of ``limit`` in the query or the body (None when there is none)."""
    for d in _all_dependants(route.dependant):
        for p in d.query_params:
            if p.name == "limit":
                caps = [getattr(m, "le", None) for m in p.field_info.metadata]
                return next((c for c in caps if c is not None), None)
    for model in body_models(route):
        field = model.model_fields.get("limit")
        if field is not None:
            caps = [getattr(m, "le", None) for m in field.metadata]
            return next((c for c in caps if c is not None), None)
    return None


# Page responses without a ``limit``, with the reason the list is bounded anyway.
UNPAGED_LISTS = {
    ("POST", "/api/v1/classes/defaults"): "the fixed default class catalog",
    ("GET", "/api/v1/tenant/billing/invoices"): "the last 24 invoices",
    ("GET", "/api/v1/knowledge/memories"): "a person's own items, capped (409 memory_full)",
    ("GET", "/api/v1/platform/plans"): "the plan catalogue (operators only)",
    ("GET", "/api/v1/platform/ai-bundles"): "the AI bundle catalogue (operators only)",
    ("GET", "/api/v1/platform/flags"): "the feature flag catalogue (operators only)",
    # Reported (audit 2026-10-06 R-14): one row per school, not paged; the operator UI reads the
    # whole list. Paging it changes the operator UI.
    ("GET", "/api/v1/platform/deployments"): "one row per school (operators only; R-14)",
    # Reported (R-14): the service returns the newest 200 and says there is no next page.
    ("GET", "/api/v1/platform/announcements"): "newest 200 (operators only; R-14)",
    ("GET", "/api/v1/platform/break-glass-requests"): "newest 200 (operators only; R-14)",
}


def test_API4_every_list_route_caps_limit(app: FastAPI) -> None:
    problems: list[str] = []
    for method, path, route in api_routes(app):
        has_limit = any(
            p.name == "limit" for d in _all_dependants(route.dependant) for p in d.query_params
        ) or any("limit" in m.model_fields for m in body_models(route))
        if not (_is_page(route) or has_limit):
            continue
        cap = _limit_cap(route)
        if cap is None and not has_limit and (method, path) in UNPAGED_LISTS:
            continue
        if cap is None or cap > MAX_LIMIT:
            problems.append(f"{method} {path}: limit cap {cap}")
    assert problems == []
    routes = {(m, p) for m, p, _ in api_routes(app)}
    assert set(UNPAGED_LISTS) <= routes, "remove entries for routes that no longer exist"


# --- 4. path parameters ------------------------------------------------------------------------

# Path parameters that are not UUIDs, with the reason.
NON_UUID_PATH_PARAMS = {
    # Feature-flag keys are a bounded, pattern-checked catalog key (FlagKey).
    ("PUT", "/api/v1/platform/flags/{key}", "key"),
    ("PUT", "/api/v1/platform/flags/{key}/tenants/{tenant_id}", "key"),
    ("DELETE", "/api/v1/platform/flags/{key}/tenants/{tenant_id}", "key"),
    # A spreadsheet row number inside an import the caller already reaches (bounded int).
    ("PATCH", "/api/v1/imports/{import_id}/sheet/rows/{row_no}", "row_no"),
}


def test_API1_every_path_parameter_is_a_uuid_or_a_bounded_key(app: FastAPI) -> None:
    problems: list[str] = []
    for method, path, route in api_routes(app):
        for p in route.dependant.path_params:
            ann = p.field_info.annotation
            if (method, path, p.name) in NON_UUID_PATH_PARAMS:
                meta = list(p.field_info.metadata)
                for arg in get_args(ann):
                    meta.append(arg)
                bounded = _max_length(meta) is not None or any(
                    getattr(m, "le", None) is not None for m in meta
                )
                if not bounded:
                    problems.append(f"{method} {path}: {p.name} is not bounded")
                continue
            if ann is not uuid.UUID:
                problems.append(f"{method} {path}: {p.name} is {ann}")
    assert problems == []


# --- 5. no secrets or storage keys in responses ------------------------------------------------

_SECRET_NAME = re.compile(
    r"(^|_)(key|token|secret|password|passwd|hmac|credential|credentials)$"
    r"|idp_subject|^sub$|storage|bucket|s3_|object_key|presign|private"
)

# Response fields whose names look like secrets, with the reason they may be returned.
RESPONSE_FIELD_ALLOWLIST = {
    # Catalog and code keys, not credentials.
    "app.admin.schemas.RetentionCategoryOut.key": "retention category code",
    "app.students.schemas.AttributeOut.key": "attribute catalog key",
    "app.certificates.schemas.CertificateTypeOut.key": "certificate type code",
    "app.certificates.schemas.InputOut.key": "certificate input code",
    "app.certificates.schemas.PrintedField.key": "printed field code",
    "app.certificates.schemas.ContentLine.key": "printed field code",
    "app.certificates.schemas.Blocker.attribute_key": "attribute catalog key",
    "app.certificates.schemas.PreviewWarning.attribute_key": "attribute catalog key",
    "app.dq.schemas.FindingOut.attribute_key": "attribute catalog key",
    "app.dq.schemas.FindingValue.attribute_key": "attribute catalog key",
    "app.dq.schemas.FindingOut.profile_key": "DQ profile code",
    "app.dq.schemas.ProfileOut.key": "DQ profile code",
    "app.dq.schemas.SummaryOut.profile_key": "DQ profile code",
    "app.dq.schemas.RunOut.profile_key": "DQ profile code",
    "app.exports.schemas.ExportProfileOut.key": "export profile code",
    "app.exports.schemas.ExportOut.profile_key": "export profile code",
    "app.imports.schemas.Issue.message_key": "i18n message key",
    "app.insights.schemas.RuleSettingOut.key": "rule code",
    "app.notifications.schemas.NotificationOut.template_key": "i18n template key",
    "app.identity.schemas.PermissionOut.key": "permission key",
    "app.identity.schemas.RoleOut.key": "role key",
    "app.platform.schemas.FlagOut.key": "feature flag key",
    "app.changes.schemas.ChangeRequestOut.attribute_key": "attribute catalog key",
    "app.students.schemas.ValueOut.attribute_key": "attribute catalog key",
    "app.students.schemas.RevealOut.attribute_key": "attribute catalog key",
    "app.students.schemas.ValueRecorded.attribute_key": "attribute catalog key",
    "app.platform.schemas.UsageDailyOut.storage_bytes": "a usage count, not a location",
    "app.platform.schemas.JobOut.idempotency_key": "server-made job key (e.g. the invoice month)",
    # One-time secrets, shown once to the party that must hold them (by design).
    "app.tally.schemas.EnrolOut.secret": "device secret, returned once to the enrolling agent "
    "(ADR-0032)",
    "app.tally.schemas.KeyRotationOut.secret": "new device secret, returned once to the device",
    "app.platform.schemas.HeartbeatKeyOut.heartbeat_key": "heartbeat key, shown once to the "
    "operator who rotates it (docs/16 §12)",
    "app.platform.schemas.ProvisionOut.heartbeat_key": "heartbeat key of a new dedicated host, "
    "shown once at provisioning (docs/16 §12)",
}


def test_API3_no_response_model_exposes_a_secret_or_storage_field(app: FastAPI) -> None:
    seen: set[str] = set()
    problems: set[str] = set()
    for _, _, route in api_routes(app):
        for model in response_models(route):
            for name in model.model_fields:
                qual = f"{model.__module__}.{model.__qualname__}.{name}"
                seen.add(qual)
                if _SECRET_NAME.search(name) and qual not in RESPONSE_FIELD_ALLOWLIST:
                    problems.add(qual)
    assert sorted(problems) == []
    stale = set(RESPONSE_FIELD_ALLOWLIST) - seen
    assert sorted(stale) == [], "remove allowlist entries for fields that no longer exist"


# --- 6. Idempotency-Key on creating POSTs ---------------------------------------------------------

# POST routes whose last path segment is one of these act on an existing resource (a state
# transition guarded by the state machine, so a repeat answers 409) or compute without storing.
ACTION_SEGMENTS = {
    "search", "archive", "unarchive", "make-current", "approve", "reject", "deny", "cancel",
    "withdraw", "revoke", "resolve", "waive", "verify", "end", "read", "read-all", "review",
    "dismiss", "close", "assign", "erase", "confirm", "retire", "publish", "issue", "void",
    "reverse", "activate", "suspend", "reactivate", "deactivate", "decommission", "status",
    "feedback", "defaults", "accept", "decline", "accept-invitations", "active-tenant",
    "login-event", "sensitive-reveal", "export", "revert", "validate", "commit",
    "emergency-confirm", "extend-trial", "change-plan", "render", "draft", "invitation-email",
    "support-session", "ask", "key-rotation", "heartbeat",
}  # fmt: skip

# Creating POSTs that do not take Idempotency-Key, with the reason.
IDEMPOTENCY_EXEMPT = {
    ("POST", "/api/v1/fleet/heartbeat"): "machine route; nonce and timestamp make it replay-safe",
    ("POST", "/api/v1/edge/tally/enrol"): "machine route; the one-time code is consumed",
    ("POST", "/api/v1/edge/tally/syncs"): "machine route; sync ids are unique per device",
    ("POST", "/api/v1/sections/{section_id}/attendance"): "an upsert keyed by student and day",
    ("POST", "/api/v1/sections/{section_id}/exams/{exam_id}/marks"): "an upsert keyed by student",
    ("POST", "/api/v1/sections/{section_id}/attendance/sheet"): "a preview; stores nothing",
    ("POST", "/api/v1/sections/{section_id}/exams/{exam_id}/marks/sheet"): "a preview",
    ("POST", "/api/v1/academic-years/{year_id}/promotions:preview"): "a preview",
    ("POST", "/api/v1/academic-years/{year_id}/promotions:undo"): "a state transition",
    ("POST", "/api/v1/platform/invoice-runs"): "idempotent per month (one run per month)",
    ("POST", "/api/v1/platform/audit/verify"): "re-runs a read-only verification",
    ("POST", "/api/v1/platform/tenants/{tenant_id}/offboarding"): "a state transition (409 on "
    "repeat)",
    ("POST", "/api/v1/platform/tenants/{tenant_id}/owner-invite:resend"): "re-sends the same "
    "invitation; no resource is created",
    ("POST", "/api/v1/platform/tenants/{tenant_id}/provisioning:resume"): "resumes the same "
    "provisioning",
    ("POST", "/api/v1/platform/tenants/{tenant_id}/offboarding:approve"): "a state transition",
    ("POST", "/api/v1/platform/tenants/{tenant_id}/offboarding:confirm-export"): "a state "
    "transition",
    ("POST", "/api/v1/platform/tenants/{tenant_id}/offboarding:confirm-teardown"): "a state "
    "transition",
    # Reported (audit 2026-10-06 R-15): the answer carries the new key once, so a replay store
    # would have to keep it; a repeated rotation drops the key the host still uses.
    ("POST", "/api/v1/platform/deployments/{deployment_id}/heartbeat-key:rotate"): "returns a "
    "one-time secret; see R-15",
    ("POST", "/api/v1/tally/enrolment-codes"): "returns a one-time code that the replay store "
    "must not keep; an unused code expires in 30 minutes",
    ("POST", "/api/v1/tally/parties/{party_id}/links"): "idempotent by design (the same link)",
    ("POST", "/api/v1/insights/flags/{flag_id}/actions"): "the answer carries decrypted note "
    "text (C3) that the replay store would keep for 24 h (round-2 H-01); a repeat is visible",
    ("POST", "/api/v1/users/{user_id}/invitation-email"): "re-sends the same invitation behind "
    "a per-member cool-down; nothing is created",
    ("POST", "/api/v1/circulars/{document_id}/read"): "a state transition (409 "
    "reading_in_progress on repeat)",
}


def _accepts_idempotency_key(route: APIRoute) -> bool:
    return "get_idempotency" in _dependency_names(route) or (
        "idempotency-key" in _header_names(route)
    )


def creating_posts(app: FastAPI) -> list[tuple[str, str, APIRoute]]:
    out = []
    for method, path, route in api_routes(app):
        if method != "POST":
            continue
        last = path.rstrip("/").rsplit("/", 1)[-1]
        verb = last.rsplit(":", 1)[-1] if ":" in last else last
        if verb in ACTION_SEGMENTS and route.status_code not in (201, 202):
            continue
        out.append((method, path, route))
    return out


def test_API6_every_creating_post_accepts_an_idempotency_key(app: FastAPI) -> None:
    # A POST that requires If-Match is a guarded state change: a repeat answers 412 or 409.
    problems = [
        f"{m} {p}"
        for m, p, route in creating_posts(app)
        if not _accepts_idempotency_key(route)
        and "if_match_version" not in _dependency_names(route)
        and (m, p) not in IDEMPOTENCY_EXEMPT
    ]
    assert problems == []
    routes = {(m, p) for m, p, _ in api_routes(app)}
    assert set(IDEMPOTENCY_EXEMPT) <= routes, "remove exemptions for routes that no longer exist"


# --- 7. If-Match on versioned updates ----------------------------------------------------------

# PUT/PATCH routes on a versioned resource that do not take If-Match, with the reason.
IF_MATCH_EXEMPT: dict[tuple[str, str], str] = {}


def _honours_if_match(route: APIRoute) -> bool:
    names = _dependency_names(route)
    return bool(names & {"if_match_version", "optional_if_match_version"}) or (
        "if-match" in _header_names(route)
    )


def _versioned(route: APIRoute) -> bool:
    model = route.response_model
    return (
        isinstance(model, type)
        and issubclass(model, BaseModel)
        and bool({"version", "row_version"} & set(model.model_fields))
    )


def test_API6_every_update_of_a_versioned_resource_honours_if_match(app: FastAPI) -> None:
    problems = [
        f"{m} {p}"
        for m, p, route in api_routes(app)
        if m in ("PUT", "PATCH")
        and _versioned(route)
        and not _honours_if_match(route)
        and (m, p) not in IF_MATCH_EXEMPT
    ]
    assert problems == []
