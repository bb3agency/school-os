"""The Meta WhatsApp Cloud API provider (ADR-0043 decisions 1, 5; FR-MSG-001..006).

Plain REST over httpx (no SDK): base URL and Graph API version from ``app/messaging/config.yaml``
(``SOS_META_GRAPH_API_VERSION`` may override the version for a tested upgrade). Every call
carries the school's business token as a Bearer header; the Embedded Signup code exchange uses
the platform's app id and secret (``SOS_META_APP_ID``, ``SOS_META_APP_SECRET``).

Errors become :class:`ProviderError` with Meta's numeric code; nothing is logged here (the
caller logs codes only). Tokens, phone numbers and message bodies never reach a log line, an
exception message or a metric.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import Settings, get_settings
from app.messaging.config import load_config
from app.messaging.providers.base import (
    AccountInfo,
    PhoneNumberInfo,
    ProviderError,
    SendResult,
    TemplateInfo,
    TemplateMessage,
)

_TRANSIENT_HTTP = frozenset({429, 500, 502, 503, 504})


class MetaCloudProvider:
    """``MessagingProvider`` for the Meta Cloud API."""

    name = "meta_cloud"

    def __init__(
        self, settings: Settings | None = None, *, client: httpx.Client | None = None
    ) -> None:
        self._settings = settings or get_settings()
        cfg = load_config().graph
        version = self._settings.meta_graph_api_version or cfg.api_version
        self._base = f"{str(cfg.base_url).rstrip('/')}/{version}"
        self._page = cfg.templates_page_size
        self._client = client or httpx.Client(timeout=cfg.timeout_s, follow_redirects=False)

    # --- plumbing -------------------------------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, str] | None = None,
        json: Any = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = self._client.request(
                method, f"{self._base}/{path}", params=params, json=json, headers=headers
            )
        except httpx.TimeoutException:
            raise ProviderError("unavailable", transient=True) from None
        except httpx.HTTPError:
            raise ProviderError("unavailable", transient=True) from None
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400 or not isinstance(body, dict):
            raise _error(response.status_code, body)
        return body

    # --- onboarding -----------------------------------------------------------------------------

    def exchange_code(self, code: str) -> str:
        app_id = self._settings.meta_app_id
        secret = self._settings.meta_app_secret
        if not app_id or secret is None:
            raise ProviderError("not_configured")
        body = self._call(
            "GET",
            "oauth/access_token",
            params={"client_id": app_id, "client_secret": secret.get_secret_value(), "code": code},
        )
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            raise ProviderError("auth_failed")
        return token

    def register_number(self, token: str, phone_number_id: str, pin: str) -> None:
        self._call(
            "POST",
            f"{phone_number_id}/register",
            token=token,
            json={"messaging_product": "whatsapp", "pin": pin},
        )

    def subscribe_app(self, token: str, waba_id: str) -> None:
        self._call("POST", f"{waba_id}/subscribed_apps", token=token)

    def phone_number(self, token: str, phone_number_id: str) -> PhoneNumberInfo:
        body = self._call(
            "GET",
            phone_number_id,
            token=token,
            params={
                "fields": "display_phone_number,verified_name,quality_rating,"
                "messaging_limit_tier,status"
            },
        )
        return PhoneNumberInfo(
            phone_number_id=str(body.get("id") or phone_number_id),
            display_phone=_str(body.get("display_phone_number")),
            display_name=_str(body.get("verified_name")),
            quality_rating=_str(body.get("quality_rating")),
            messaging_limit=_str(body.get("messaging_limit_tier")),
            status=_str(body.get("status")),
        )

    def account(self, token: str, waba_id: str) -> AccountInfo:
        body = self._call(
            "GET", waba_id, token=token, params={"fields": "name,currency,account_review_status"}
        )
        return AccountInfo(
            waba_id=str(body.get("id") or waba_id),
            name=_str(body.get("name")),
            currency=_str(body.get("currency")),
            review_status=_str(body.get("account_review_status")),
        )

    # --- templates ------------------------------------------------------------------------------

    def create_template(
        self, token: str, waba_id: str, *, name: str, language: str, components: list[Any]
    ) -> TemplateInfo:
        body = self._call(
            "POST",
            f"{waba_id}/message_templates",
            token=token,
            json={
                "name": name,
                "language": language,
                "category": "UTILITY",
                "components": components,
            },
        )
        return TemplateInfo(
            name=name,
            language=language,
            status=str(body.get("status") or "PENDING"),
            category=_str(body.get("category")),
            template_id=_str(body.get("id")),
        )

    def list_templates(self, token: str, waba_id: str) -> list[TemplateInfo]:
        out: list[TemplateInfo] = []
        params: dict[str, str] = {
            "fields": "name,language,status,category,id,rejected_reason",
            "limit": str(self._page),
        }
        for _ in range(50):  # pages; a school has a few dozen templates at most
            body = self._call("GET", f"{waba_id}/message_templates", token=token, params=params)
            for item in body.get("data") or []:
                if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                    continue
                out.append(
                    TemplateInfo(
                        name=item["name"],
                        language=str(item.get("language") or ""),
                        status=str(item.get("status") or ""),
                        category=_str(item.get("category")),
                        template_id=_str(item.get("id")),
                        rejected_reason=_str(item.get("rejected_reason")),
                    )
                )
            after = ((body.get("paging") or {}).get("cursors") or {}).get("after")
            if not isinstance(after, str) or not (body.get("paging") or {}).get("next"):
                break
            params = {**params, "after": after}
        return out

    # --- sending --------------------------------------------------------------------------------

    def send_template(
        self, token: str, phone_number_id: str, to: str, message: TemplateMessage
    ) -> SendResult:
        components: list[dict[str, Any]] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": v} for v in message.body_values],
            }
        ]
        if message.url_suffix is not None and message.url_button_index is not None:
            components.append(
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": str(message.url_button_index),
                    "parameters": [{"type": "text", "text": message.url_suffix}],
                }
            )
        if message.stop_button_index is not None and message.stop_payload:
            # FR-MSG-010: the "Stop messages" quick reply comes back with this payload.
            components.append(
                {
                    "type": "button",
                    "sub_type": "quick_reply",
                    "index": str(message.stop_button_index),
                    "parameters": [{"type": "payload", "payload": message.stop_payload}],
                }
            )
        body = self._call(
            "POST",
            f"{phone_number_id}/messages",
            token=token,
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "template",
                "template": {
                    "name": message.name,
                    "language": {"code": message.language},
                    "components": components,
                },
            },
        )
        return SendResult(provider_message_id=_message_id(body))

    def send_text(self, token: str, phone_number_id: str, to: str, body: str) -> SendResult:
        result = self._call(
            "POST",
            f"{phone_number_id}/messages",
            token=token,
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                "type": "text",
                "text": {"preview_url": False, "body": body},
            },
        )
        return SendResult(provider_message_id=_message_id(result))


def _str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _message_id(body: dict[str, Any]) -> str:
    messages = body.get("messages")
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        value = messages[0].get("id")
        if isinstance(value, str) and value:
            return value
    raise ProviderError("invalid_response")


def _error(status: int, body: Any) -> ProviderError:
    error = body.get("error") if isinstance(body, dict) else None
    meta_code: int | None = None
    if isinstance(error, dict) and isinstance(error.get("code"), int):
        meta_code = int(error["code"])
    transient = status in _TRANSIENT_HTTP or (
        meta_code is not None and meta_code in load_config().sending.transient_error_codes
    )
    if status in (401, 403) or meta_code == 190:
        return ProviderError("auth_failed", meta_code=meta_code)
    if status == 404:
        return ProviderError("not_found", meta_code=meta_code)
    if status == 429:
        return ProviderError("rate_limited", meta_code=meta_code, transient=True)
    if status >= 500:
        return ProviderError("unavailable", meta_code=meta_code, transient=True)
    return ProviderError("meta_error", meta_code=meta_code, transient=transient)


__all__ = ["MetaCloudProvider"]
