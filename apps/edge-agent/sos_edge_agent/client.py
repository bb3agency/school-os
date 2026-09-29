"""HTTP to SchoolOS (outbound HTTPS, signed) and to Tally (this PC only), over the standard
library (ADR-0032 §1, §3).

- :class:`UrllibTransport`: TLS verification on with the system trust store, **no redirects**
  (a signed request is never replayed to another address), timeouts, bounded response size.
- :class:`SchoolOSClient`: the agent routes of ``/api/v1/edge/tally``. Every request after
  enrolment is signed (:mod:`.signing`); answers map to typed errors the sync loop understands
  (401 credential refused, 404 connector switched off, 409 ``agent_outdated``, 429 rate limited,
  5xx or network: try again later).
- :class:`TallyClient`: POSTs export envelopes to Tally's XML server on 127.0.0.1 and checks each
  one is an export first.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol
from urllib.parse import urlsplit

from sos_edge_agent import __version__
from sos_edge_agent.config import AgentConfig, check_tally_url
from sos_edge_agent.signing import new_nonce, signed_headers
from sos_edge_agent.tally_xml import assert_export_only

API_PREFIX: Final = "/api/v1/edge/tally"
MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
TIMEOUT_S: Final = 30.0


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


class Transport(Protocol):
    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> HttpResponse: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class UrllibTransport:
    def __init__(self, *, timeout_s: float = TIMEOUT_S) -> None:
        context = ssl.create_default_context()
        self._opener = urllib.request.build_opener(
            _NoRedirect(), urllib.request.HTTPSHandler(context=context)
        )
        self._timeout = timeout_s

    def request(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None
    ) -> HttpResponse:
        if urlsplit(url).scheme not in ("http", "https"):
            raise TransientError("unsupported URL scheme")
        req = urllib.request.Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
        try:
            with self._opener.open(req, timeout=self._timeout) as res:
                data = res.read(MAX_RESPONSE_BYTES + 1)
                return HttpResponse(res.status, dict(res.headers.items()), data)
        except urllib.error.HTTPError as err:
            data = err.read(MAX_RESPONSE_BYTES + 1) if err.fp is not None else b""
            return HttpResponse(err.code, dict(err.headers.items()), data)
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            raise TransientError("network error") from err


# --- errors the sync loop acts on ---------------------------------------------------------------


class AgentError(RuntimeError):
    """Base class; ``code`` is safe to log (no personal data, no secrets)."""

    code = "agent_error"


class TransientError(AgentError):
    code = "transient"


class CredentialRefused(AgentError):
    """401: the device was revoked, the key is wrong, or the clock is off by over 5 minutes."""

    code = "credential_refused"


class ConnectorOff(AgentError):
    code = "connector_off"


class Outdated(AgentError):
    code = "agent_outdated"


class RateLimited(AgentError):
    code = "rate_limited"

    def __init__(self, retry_after_s: float | None = None) -> None:
        super().__init__(self.code)
        self.retry_after_s = retry_after_s


class Refused(AgentError):
    """422/409 other than the above: the server refused this snapshot (code from the problem)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _problem_code(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return "unknown"
    if not isinstance(data, dict):
        return "unknown"
    errors = data.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("code", data.get("code", "unknown")))[:64]
    return str(data.get("code", "unknown"))[:64]


def _raise_for(res: HttpResponse) -> None:
    if 200 <= res.status < 300:
        return
    if res.status == 401:
        raise CredentialRefused("credential_refused")
    if res.status == 404:
        raise ConnectorOff("connector_off")
    if res.status == 429:
        after = res.headers.get("Retry-After") or res.headers.get("retry-after")
        raise RateLimited(float(after) if after and after.isdigit() else None)
    code = _problem_code(res.body)
    if res.status == 409 and code == "agent_outdated":
        raise Outdated("agent_outdated")
    if res.status in (409, 413, 422):
        raise Refused(code)
    raise TransientError(f"http_{res.status}")


def _json(res: HttpResponse) -> dict[str, Any]:
    if len(res.body) > MAX_RESPONSE_BYTES:
        raise TransientError("response_too_large")
    try:
        data = json.loads(res.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as err:
        raise TransientError("bad_response") from err
    if not isinstance(data, dict):
        raise TransientError("bad_response")
    return data


# --- SchoolOS -----------------------------------------------------------------------------------


class SchoolOSClient:
    def __init__(
        self,
        config: AgentConfig,
        secret: Callable[[], bytes],
        transport: Transport,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._secret = secret
        self._transport = transport
        self._clock = clock

    @property
    def config(self) -> AgentConfig:
        return self._config

    def use(self, config: AgentConfig) -> None:
        """After a key rotation: sign with the new key id from now on."""
        self._config = config

    def _url(self, route: str) -> str:
        return f"{self._config.server_url}{API_PREFIX}{route}"

    def enrol(self, code: str, platform: str) -> dict[str, Any]:
        body = json.dumps(
            {"code": code, "agent_version": __version__, "platform": platform}
        ).encode()
        now = self._clock() if self._clock is not None else None
        headers = {
            "Content-Type": "application/json",
            "X-SOS-Tenant": str(self._config.tenant_id),
            "X-SOS-Timestamp": str(int(now if now is not None else time.time())),
            "X-SOS-Nonce": new_nonce(),
            "X-SOS-Agent-Version": __version__,
        }
        res = self._transport.request("POST", self._url("/enrol"), headers, body)
        _raise_for(res)
        return _json(res)

    def signed(
        self, method: str, route: str, payload: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        cfg = self._config
        if cfg.device_id is None or cfg.key_id is None:
            raise CredentialRefused("not_enrolled")
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = signed_headers(
            secret=self._secret(),
            tenant_id=cfg.tenant_id,
            device_id=cfg.device_id,
            key_id=cfg.key_id,
            method=method,
            path=API_PREFIX + route,
            body=body,
            agent_version=__version__,
            now=self._clock() if self._clock is not None else None,
        )
        if payload is not None:
            headers["Content-Type"] = "application/json"
        res = self._transport.request(method, self._url(route), headers, body or None)
        _raise_for(res)
        return _json(res)

    def get_config(self) -> dict[str, Any]:
        return self.signed("GET", "/config")

    def put_catalog(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.signed("PUT", "/catalog", payload)

    def post_sync(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.signed("POST", "/syncs", payload)

    def rotate_key(self) -> dict[str, Any]:
        return self.signed("POST", "/key-rotation")


# --- Tally ----------------------------------------------------------------------------------------


class TallyClient:
    """Tally's XML server on this PC (``tally_url`` is checked to be local)."""

    def __init__(self, tally_url: str, transport: Transport) -> None:
        self._url = check_tally_url(tally_url)
        self._transport = transport

    def export(self, envelope: bytes) -> bytes:
        assert_export_only(envelope)
        headers = {"Content-Type": "text/xml; charset=utf-8"}
        try:
            res = self._transport.request("POST", self._url, headers, envelope)
        except TransientError as err:
            raise TallyUnavailable("tally_unreachable") from err
        if res.status != 200:
            raise TallyUnavailable(f"tally_http_{res.status}")
        if len(res.body) > MAX_RESPONSE_BYTES:
            raise TallyUnavailable("tally_response_too_large")
        return res.body


class TallyUnavailable(AgentError):
    """Tally is not running, its XML server is off, or the company is not open."""

    code = "tally_unavailable"


__all__ = [
    "API_PREFIX",
    "AgentError",
    "ConnectorOff",
    "CredentialRefused",
    "HttpResponse",
    "Outdated",
    "RateLimited",
    "Refused",
    "SchoolOSClient",
    "TallyClient",
    "TallyUnavailable",
    "TransientError",
    "Transport",
    "UrllibTransport",
]
