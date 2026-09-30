"""Explicit Vertex AI context caches for a role's static prefix (ADR-0033; docs/06 §12 caching).

What may be cached: only the body keys the codec names in ``MessagesRequest.static_prefix``
(the system instruction and the tool definitions). Those are prompt text rendered from versioned
templates with the school's name, today's date and generic role wording, never a question, a
record, a document passage or an image, so a cache holds no personal data. The cache lives in the
configured Vertex location (India) until its TTL ends (``models.yaml gemini.explicit_cache``).

When a cache is used: the feature is enabled, the model has an ``explicit_cache_min_tokens``
capability and the prefix reaches it (estimated at 4 characters per token, which underestimates
Telugu and so errs towards not caching), and the request carries no ``toolConfig`` (Vertex does
not accept one next to ``cachedContent``). Otherwise the request goes out whole.

Graceful fallback: a failed create (any error) or a rejected use (the cache expired or was
deleted early) sends the request uncached and pauses caching for that prefix for
``failure_backoff_s``. Entries are per process (a second process makes its own cache; each
expires on its own TTL), at most ``max_entries``.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from app.knowledge.config.llm import ExplicitCache

CHARS_PER_TOKEN: Final = 4


@dataclass(frozen=True, slots=True)
class _Entry:
    name: str
    expires_at: float


class ContextCaches:
    def __init__(
        self,
        config: ExplicitCache,
        min_tokens: Callable[[str], int | None],
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._min_tokens = min_tokens
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._paused: dict[str, float] = {}
        self._lock = threading.Lock()

    def plan(
        self, model: str, location: str, body: Mapping[str, Any], prefix_keys: tuple[str, ...]
    ) -> tuple[str, dict[str, Any]] | None:
        """``(key, prefix)`` when this request should use a cache, else None."""
        if not self._config.enabled or not prefix_keys or "toolConfig" in body:
            return None
        minimum = self._min_tokens(model)
        prefix = {k: body[k] for k in prefix_keys if k in body}
        if minimum is None or not prefix:
            return None
        raw = json.dumps(prefix, ensure_ascii=False, sort_keys=True)
        if len(raw) // CHARS_PER_TOKEN < minimum:
            return None
        key = hashlib.sha256(f"{location}\n{model}\n{raw}".encode()).hexdigest()
        with self._lock:
            if self._paused.get(key, 0.0) > self._clock():
                return None
        return key, prefix

    def lookup(self, key: str) -> str | None:
        """A cache name that is valid for at least ``refresh_margin_s`` more seconds."""
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at - self._config.refresh_margin_s <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return entry.name

    def store(self, key: str, response: Mapping[str, Any]) -> str | None:
        name = response.get("name")
        if not isinstance(name, str) or "/cachedContents/" not in name:
            self.pause(key)
            return None
        expires = _parse_time(response.get("expireTime"))
        now = self._clock()
        expires_at = expires if expires is not None else now + self._config.ttl_s
        with self._lock:
            self._entries[key] = _Entry(name, expires_at)
            self._entries.move_to_end(key)
            while len(self._entries) > self._config.max_entries:
                self._entries.popitem(last=False)
        return name

    def pause(self, key: str) -> None:
        with self._lock:
            self._entries.pop(key, None)
            self._paused[key] = self._clock() + self._config.failure_backoff_s

    def create_body(self, model_path: str, prefix: Mapping[str, Any]) -> dict[str, Any]:
        return {"model": model_path, **prefix, "ttl": f"{self._config.ttl_s}s"}


def _parse_time(value: object) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def without_prefix(
    body: Mapping[str, Any], prefix_keys: tuple[str, ...], name: str
) -> dict[str, Any]:
    out = {k: v for k, v in body.items() if k not in prefix_keys}
    out["cachedContent"] = name
    return out


__all__ = ["CHARS_PER_TOKEN", "ContextCaches", "without_prefix"]
