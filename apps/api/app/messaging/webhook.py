"""Meta webhook verification and parsing (ADR-0043 decision 6, ADR-0047; FR-MSG-020; SEC-034).

- :class:`MetaWebhookVerifier` is the ``meta`` verifier of the R2-0 registry
  (``app.public.webhooks``): ``X-Hub-Signature-256: sha256=<hex HMAC-SHA256 of the raw body with
  the Meta app secret>``, compared in constant time; then every ``entry[].id`` (a WABA id) is
  resolved to a school through the ``meta_waba`` public handle. Entries of unknown WABAs are
  dropped and counted (Meta sends one callback for every school, so an unknown one is not an
  error: answering 404 would make Meta retry and disable the subscription). Its event id is the
  SHA-256 of the raw body, so an exact replay is recognised by the guard; each event inside is
  deduplicated again by its own key in the school's inbox.
- :func:`parse_entries` turns a verified body into per-WABA events with a dedupe key, a type and
  a **minimised** payload: inbound message text is classified (``stop``, ``join`` + code,
  ``other``) and dropped here, never stored (FR-MSG-018); contact profile names are dropped.

Nothing here logs a phone number, a message, a token or the body.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Final

from app.core.logging import get_logger
from app.messaging.config import classify, load_config
from app.messaging.runtime import settings as messaging_settings
from app.public.config import WebhookProvider
from app.public.service import ResolvedHandle
from app.public.webhooks import (
    VerifiedWebhook,
    WebhookRejected,
    WebhookRequest,
    register_webhook_verifier,
    webhook_verifier,
)

log = get_logger(__name__)

SIGNATURE_HEADER: Final = "x-hub-signature-256"
_PREFIX: Final = "sha256="
_MAX_ENTRIES: Final = 200


def signature(secret: bytes, body: bytes) -> str:
    """``X-Hub-Signature-256`` value for ``body`` (tests and the fake use it too)."""
    return _PREFIX + hmac.new(secret, body, hashlib.sha256).hexdigest()


def _json(body: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def waba_ids(payload: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in payload.get("entry") or []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            out.append(entry["id"])
    return list(dict.fromkeys(out))


class MetaWebhookVerifier:
    """The real Meta verifier (registered by :func:`install`). The app secret is read from the
    settings on every call (rotation needs no restart); unset, every delivery is refused."""

    provider: WebhookProvider = "meta"

    def verify(self, request: WebhookRequest) -> VerifiedWebhook:
        secret = messaging_settings().meta_app_secret
        if secret is None:
            raise WebhookRejected("app_secret_unset")
        given = request.headers.get(SIGNATURE_HEADER) or ""
        if not given.startswith(_PREFIX):
            raise WebhookRejected("signature_missing")
        expected = signature(secret.get_secret_value().encode(), request.body)
        if not hmac.compare_digest(given.strip().lower().encode(), expected.encode()):
            raise WebhookRejected("signature_mismatch")
        payload = _json(request.body)
        if payload is None or payload.get("object") != "whatsapp_business_account":
            raise WebhookRejected("not_whatsapp")
        handles: list[ResolvedHandle] = []
        unknown = 0
        for waba in waba_ids(payload)[:_MAX_ENTRIES]:
            found = request.resolve("meta_waba", waba)
            if found is None:
                unknown += 1
            else:
                handles.append(found)
        if unknown:
            log.warning(
                "messaging.webhook.unknown_waba",
                resource_type="webhook",
                action="meta",
                count=unknown,
                outcome="dropped",
            )
        event_id = "body:" + hashlib.sha256(request.body).hexdigest()
        return VerifiedWebhook(event_id=event_id, handles=tuple(handles))


def install() -> None:
    """Register :class:`MetaWebhookVerifier` unless a ``meta`` verifier is already registered
    (the API factory runs it; tests may install their own first)."""
    if webhook_verifier("meta") is None:
        register_webhook_verifier(MetaWebhookVerifier())


# --- parsing ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedEvent:
    event_key: str
    event_type: str
    event_at: dt.datetime | None
    payload: dict[str, Any] = field(default_factory=dict)


def _ts(value: object) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(int(str(value)), tz=dt.UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _digest(field_name: str, value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return f"{field_name}:{hashlib.sha256(raw.encode()).hexdigest()[:40]}"


def _short(value: object, limit: int = 120) -> str | None:
    return value[:limit] if isinstance(value, str) and value else None


def _status_event(status: dict[str, Any]) -> ParsedEvent | None:
    message_id, state = _short(status.get("id"), 200), _short(status.get("status"), 20)
    if message_id is None or state is None:
        return None
    timestamp = str(status.get("timestamp") or "")
    raw_pricing = status.get("pricing")
    pricing: dict[str, Any] = raw_pricing if isinstance(raw_pricing, dict) else {}
    raw_errors = status.get("errors")
    errors: list[Any] = raw_errors if isinstance(raw_errors, list) else []
    code = next(
        (e.get("code") for e in errors if isinstance(e, dict) and isinstance(e.get("code"), int)),
        None,
    )
    return ParsedEvent(
        event_key=f"status:{message_id}:{state}:{timestamp}"[:300],
        event_type="message_status",
        event_at=_ts(timestamp),
        payload={
            "id": message_id,
            "status": state,
            "billable": pricing.get("billable"),
            "pricing_category": _short(pricing.get("category"), 40),
            "error_code": code,
        },
    )


def _inbound_event(message: dict[str, Any]) -> ParsedEvent | None:
    message_id = _short(message.get("id"), 200)
    sender = _short(message.get("from"), 20)
    if message_id is None or sender is None:
        return None
    kind_of = message.get("type")
    text: str | None = None
    payload: str | None = None
    if kind_of == "text" and isinstance(message.get("text"), dict):
        body = message["text"].get("body")
        text = body if isinstance(body, str) else None
    elif kind_of == "button" and isinstance(message.get("button"), dict):
        raw = message["button"].get("payload")
        payload = raw if isinstance(raw, str) else None
        label = message["button"].get("text")
        text = label if isinstance(label, str) else None
    elif kind_of == "interactive" and isinstance(message.get("interactive"), dict):
        reply = message["interactive"].get("button_reply")
        if isinstance(reply, dict):
            raw = reply.get("id")
            payload = raw if isinstance(raw, str) else None
            label = reply.get("title")
            text = label if isinstance(label, str) else None
    keyword, code = classify(text, payload)
    if keyword == "other" and kind_of == "button" and text is not None:
        # A version 1 template's quick reply arrives as its label ("Stop messages" / Telugu);
        # templates no longer carry it (D32), but old messages may still be tapped.
        keyword, code = _stop_label(text)
    # The text itself ends here (FR-MSG-018): only the classification is kept.
    return ParsedEvent(
        event_key=f"in:{message_id}",
        event_type="inbound_message",
        event_at=_ts(message.get("timestamp")),
        payload={"wa_id": sender, "kind": keyword, "code": code},
    )


def _stop_label(text: str) -> tuple[Any, str | None]:
    labels = set(load_config().keywords.stop_button_labels)
    return ("stop", None) if text.strip() in labels else ("other", None)


def _change_events(change: dict[str, Any]) -> Iterator[ParsedEvent]:  # noqa: PLR0912 - per field
    field_name = change.get("field")
    value = change.get("value")
    if not isinstance(value, dict):
        return
    if field_name == "messages":
        for status in value.get("statuses") or []:
            if isinstance(status, dict) and (parsed := _status_event(status)) is not None:
                yield parsed
        for message in value.get("messages") or []:
            if isinstance(message, dict) and (parsed := _inbound_event(message)) is not None:
                yield parsed
        return
    kept: dict[str, Any] = {}
    keys: tuple[str, ...]
    if field_name == "message_template_status_update":
        kind = "template_status"
        keys = (
            "event",
            "message_template_id",
            "message_template_name",
            "message_template_language",
            "reason",
        )
    elif field_name == "template_category_update":
        kind = "template_category"
        keys = (
            "message_template_id",
            "message_template_name",
            "message_template_language",
            "previous_category",
            "new_category",
        )
    elif field_name == "phone_number_quality_update":
        kind = "phone_quality"
        keys = ("event", "current_limit", "old_limit")
    elif field_name == "account_update":
        kind = "account_update"
        keys = ("event",)
    else:
        kind = "other"
        keys = ()
    for key in keys:
        item = value.get(key)
        if isinstance(item, str | int):
            kept[key] = str(item)[:120]
    if kind == "other":
        kept["field"] = str(field_name)[:60]
    yield ParsedEvent(
        event_key=_digest(str(field_name), value),
        event_type=kind,
        event_at=_ts(value.get("timestamp")) if "timestamp" in value else None,
        payload=kept,
    )


def parse_entries(body: bytes) -> dict[str, list[ParsedEvent]]:
    """Events per WABA id of a verified Meta delivery (unknown shapes are skipped)."""
    payload = _json(body) or {}
    out: dict[str, list[ParsedEvent]] = {}
    for entry in (payload.get("entry") or [])[:_MAX_ENTRIES]:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        events = out.setdefault(entry["id"], [])
        for change in entry.get("changes") or []:
            if isinstance(change, dict):
                events.extend(_change_events(change))
    return out


__all__ = [
    "SIGNATURE_HEADER",
    "MetaWebhookVerifier",
    "ParsedEvent",
    "install",
    "parse_entries",
    "signature",
    "waba_ids",
]
