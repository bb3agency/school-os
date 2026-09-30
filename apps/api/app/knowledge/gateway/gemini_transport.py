"""Live transport: Google Gemini on Vertex AI over REST with httpx (ADR-0033).

One attempt per call; retries, the circuit breaker, budgets and metering sit above in the
gateway. The request is ``POST {endpoint}/{api_version}/projects/{project}/locations/{location}
/publishers/google/models/{model}:generateContent`` (``:streamGenerateContent?alt=sse`` when
streaming) with a service-identity OAuth token (:mod:`.gemini_auth`; invariant 10).

- Endpoint: the regional endpoint of ``Settings.llm_gcp_location`` (an India region in
  staging/prod). Only an ``offline_only`` role (the eval judge, synthetic data) may name another
  location, e.g. ``global``; the config loader refuses it for product roles.
- Zero Data Retention (docs/08 §8): before the first call (and again every
  ``VERIFY_EVERY_S``) the transport reads the project's ``cacheConfig`` and sends nothing unless
  implicit caching is disabled (``verify_cache_config``, always on in staging/prod). A failed
  check is ``rejected`` (fail closed; the answer degrades to search-only).
- Explicit context caches for the static prefix only (:mod:`.gemini_cache`).
- Errors are classified by status only; Google's error text is never kept or logged (it can echo
  input): 429 ``rate_limited``, 503 ``overloaded``, other 5xx ``server``, other 4xx ``rejected``,
  timeouts ``timeout``, network errors ``connection``. A stream that sends an error object
  mid-way raises ``overloaded``/``server``.
- Nothing here logs a request, a response, a token or a key (invariant 5); httpx's loggers are
  capped at WARNING so they never print URLs with bodies at DEBUG.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Final

import httpx

from app.core.logging import get_logger
from app.knowledge.config.llm import GeminiConfig
from app.knowledge.gateway.gemini_auth import TokenSource
from app.knowledge.gateway.gemini_cache import ContextCaches, without_prefix
from app.knowledge.gateway.gemini_wire import dumps
from app.knowledge.gateway.transport import FailureKind, MessagesRequest, TransportError, Wire

log = get_logger(__name__)

VERIFY_EVERY_S: Final = 6 * 3600
_LOGGERS: Final = ("httpx", "httpcore")
_OVERLOADED: Final = frozenset({503})


def classify_status(status: int) -> FailureKind:
    if status == 429:
        return "rate_limited"
    if status in _OVERLOADED:
        return "overloaded"
    if status >= 500:
        return "server"
    if status == 408:
        return "timeout"
    return "rejected"


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("retry-after")
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _error(response: httpx.Response) -> TransportError:
    status = response.status_code
    return TransportError(
        classify_status(status), status=status, retry_after_s=_retry_after(response)
    )


class GeminiTransport:
    name = "gemini"
    wire: Wire = "gemini"

    def __init__(
        self,
        *,
        project: str,
        location: str,
        config: GeminiConfig,
        tokens: TokenSource,
        caches: ContextCaches | None = None,
        http: httpx.Client | None = None,
        verify_cache_config: bool = True,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not project.strip():
            raise ValueError("a Vertex AI project is required for live AI")
        for name in _LOGGERS:
            logging.getLogger(name).setLevel(logging.WARNING)
        self._project = project
        self._location = location
        self._config = config
        self._tokens = tokens
        self._caches = caches
        # No proxy or base URL from the environment decides where prompts go.
        self._http = http or httpx.Client(trust_env=False, follow_redirects=False)
        self._verify = verify_cache_config
        self._clock = clock
        self._verified_at: float | None = None
        self._lock = threading.Lock()

    # --- endpoints ----------------------------------------------------------------------------

    def _base(self, location: str) -> str:
        if location == "global":
            return self._config.global_endpoint
        return self._config.regional_endpoint.format(location=location)

    def _project_path(self, location: str) -> str:
        return f"projects/{self._project}/locations/{location}"

    def model_path(self, model: str, location: str | None = None) -> str:
        where = location or self._location
        return f"{self._project_path(where)}/publishers/google/models/{model}"

    def _api(self, location: str) -> str:
        return f"{self._base(location)}/{self._config.api_version}"

    def _url(self, request: MessagesRequest, method: str) -> str:
        if not request.model:
            raise TransportError("rejected")
        location = request.location or self._location
        return f"{self._api(location)}/{self.model_path(request.model, location)}:{method}"

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._tokens.token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    # --- Zero Data Retention check (docs/08 §8) -----------------------------------------------

    def verify_zero_data_retention(self, *, timeout_s: float = 10.0) -> None:
        """Refuse to send anything unless the project's implicit data caching is disabled."""
        if not self._verify:
            return
        now = self._clock()
        with self._lock:
            if self._verified_at is not None and now - self._verified_at < VERIFY_EVERY_S:
                return
            url = f"{self._api(self._location)}/projects/{self._project}/cacheConfig"
            try:
                response = self._http.get(url, headers=self._headers(), timeout=timeout_s)
            except httpx.TimeoutException:
                raise TransportError("timeout") from None
            except httpx.HTTPError:
                raise TransportError("connection") from None
            if response.status_code != 200:
                log.error("kb.llm.zdr_check_failed", error_code=response.status_code)
                if response.status_code >= 500 or response.status_code == 429:
                    raise _error(response)
                raise TransportError("rejected", status=response.status_code)
            try:
                disabled = response.json().get("disableCache") is True
            except (ValueError, AttributeError):
                disabled = False
            if not disabled:
                log.error("kb.llm.zdr_cache_enabled", action="refuse")
                raise TransportError("rejected")
            self._verified_at = now

    # --- explicit context caches --------------------------------------------------------------

    def _cached_body(
        self, request: MessagesRequest, timeout_s: float
    ) -> tuple[Mapping[str, Any], str | None]:
        """The body to send and the cache key it uses (None: sent whole)."""
        body = request.body
        caches = self._caches
        if caches is None or not request.model:
            return body, None
        location = request.location or self._location
        plan = caches.plan(request.model, location, body, request.static_prefix)
        if plan is None:
            return body, None
        key, prefix = plan
        name = caches.lookup(key)
        if name is None:
            name = self._create_cache(
                caches,
                key,
                model=request.model,
                location=location,
                prefix=prefix,
                timeout_s=timeout_s,
            )
        if name is None:
            return body, None
        return without_prefix(body, request.static_prefix, name), key

    def _create_cache(
        self,
        caches: ContextCaches,
        key: str,
        *,
        model: str,
        location: str,
        prefix: Mapping[str, Any],
        timeout_s: float,
    ) -> str | None:
        url = f"{self._api(location)}/{self._project_path(location)}/cachedContents"
        payload = caches.create_body(self.model_path(model, location), prefix)
        try:
            response = self._http.post(
                url, content=dumps(payload), headers=self._headers(), timeout=timeout_s
            )
        except httpx.HTTPError:
            caches.pause(key)
            return None
        if response.status_code != 200:
            log.info("kb.llm.cache_create_failed", error_code=response.status_code)
            caches.pause(key)
            return None
        try:
            return caches.store(key, response.json())
        except ValueError:
            caches.pause(key)
            return None

    def _stale_cache(self, key: str | None, response: httpx.Response) -> bool:
        """A 4xx on a cached request: the cache expired or was removed early. Pause it (the
        caller resends the request whole)."""
        status = response.status_code
        if key is None or self._caches is None or not 400 <= status < 500 or status == 429:
            return False
        self._caches.pause(key)
        return True

    # --- Transport ----------------------------------------------------------------------------

    def send(self, request: MessagesRequest) -> Mapping[str, Any]:
        self.verify_zero_data_retention()
        body, key = self._cached_body(request, request.timeout_s)
        response = self._post(self._url(request, "generateContent"), body, request.timeout_s)
        if self._stale_cache(key, response):
            response = self._post(
                self._url(request, "generateContent"), request.body, request.timeout_s
            )
        if response.status_code != 200:
            raise _error(response)
        try:
            data = response.json()
        except ValueError:
            raise TransportError("server", status=response.status_code) from None
        if not isinstance(data, Mapping):
            raise TransportError("server", status=response.status_code)
        return data

    def _post(self, url: str, body: Mapping[str, Any], timeout_s: float) -> httpx.Response:
        try:
            return self._http.post(
                url, content=dumps(body), headers=self._headers(), timeout=timeout_s
            )
        except httpx.TimeoutException:
            raise TransportError("timeout") from None
        except httpx.HTTPError:
            raise TransportError("connection") from None

    def stream(self, request: MessagesRequest) -> Iterator[Mapping[str, Any]]:
        """Server-sent events of one call as dicts. Closing the iterator closes the HTTP
        response, so Vertex stops generating."""
        self.verify_zero_data_retention()
        body, key = self._cached_body(request, request.timeout_s)
        url = self._url(request, "streamGenerateContent") + "?alt=sse"
        response = self._open(url, body, request.timeout_s)
        if self._stale_cache(key, response):
            response.close()
            response = self._open(url, request.body, request.timeout_s)
        if response.status_code != 200:
            response.read()
            response.close()
            raise _error(response)
        return _sse_events(response)

    def _open(self, url: str, body: Mapping[str, Any], timeout_s: float) -> httpx.Response:
        try:
            outgoing = self._http.build_request(
                "POST", url, content=dumps(body), headers=self._headers(), timeout=timeout_s
            )
            return self._http.send(outgoing, stream=True)
        except httpx.TimeoutException:
            raise TransportError("timeout") from None
        except httpx.HTTPError:
            raise TransportError("connection") from None

    def close(self) -> None:
        self._http.close()


def _sse_events(response: httpx.Response) -> Iterator[Mapping[str, Any]]:
    """``data:`` lines of an SSE stream as JSON objects (one ``GenerateContentResponse``
    chunk each); an ``error`` object mid-stream is raised as a :class:`TransportError`."""
    try:
        data: list[str] = []
        for line in response.iter_lines():
            if line.startswith("data:"):
                data.append(line[5:].lstrip())
                continue
            if line.strip() or not data:
                continue  # comments, other fields, keep-alive blank lines
            yield _chunk("\n".join(data))
            data = []
        if data:
            yield _chunk("\n".join(data))
    except httpx.TimeoutException:
        raise TransportError("timeout") from None
    except httpx.HTTPError:
        raise TransportError("connection") from None
    finally:
        response.close()


def _chunk(raw: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except ValueError:
        raise TransportError("server") from None
    if isinstance(value, list):  # a JSON-array stream wrapper: take its one element
        value = value[0] if len(value) == 1 else {}
    if not isinstance(value, Mapping):
        raise TransportError("server")
    error = value.get("error")
    if isinstance(error, Mapping):
        code = error.get("code")
        overloaded = code in (429, 503) or error.get("status") in (
            "UNAVAILABLE",
            "RESOURCE_EXHAUSTED",
        )
        raise TransportError("overloaded" if overloaded else "server")
    return value


__all__ = ["VERIFY_EVERY_S", "GeminiTransport", "classify_status"]
