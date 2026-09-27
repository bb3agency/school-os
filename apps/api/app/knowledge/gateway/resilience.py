"""Retries with exponential backoff and jitter, and a circuit breaker (ADR-0005; NFR-AVL-004).

Values come from ``models.yaml`` ``client`` (invariant 13). The breaker is per process and per
provider (an outage is not tenant-specific): after ``circuit_failure_threshold`` consecutive
failed attempts it opens for ``circuit_open_s``; every call is refused at once (the caller
degrades to search-only, docs/06 §15). When the time is up one trial call goes through
(half-open): success closes the breaker, failure opens it again. ``rejected`` (4xx) responses do
not count: they are our bug, not the provider's health. Clock, sleep and randomness are
injectable so tests run instantly and deterministically.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from typing import Literal

from app.knowledge.config.llm import ClientConfig

BreakerState = Literal["closed", "open", "half_open"]


def backoff_delay(
    config: ClientConfig,
    attempt: int,
    *,
    retry_after_s: float | None = None,
    rand: Callable[[], float] = random.random,
) -> float:
    """Delay before retry ``attempt`` (0-based): ``min(max, base * 2^attempt)`` plus jitter in
    ``[0, base)``; a provider ``retry-after`` is honoured when longer, capped at the maximum."""
    delay = min(config.backoff_max_s, config.backoff_base_s * (2.0**attempt))
    delay += rand() * config.backoff_base_s
    if retry_after_s is not None and retry_after_s > delay:
        delay = retry_after_s
    return min(delay, config.backoff_max_s)


class CircuitBreaker:
    def __init__(
        self, config: ClientConfig, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._threshold = config.circuit_failure_threshold
        self._open_s = config.circuit_open_s
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_running = False

    @property
    def state(self) -> BreakerState:
        with self._lock:
            return self._state()

    def _state(self) -> BreakerState:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self._open_s:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        """Whether a call may go out now (half-open admits exactly one trial at a time)."""
        with self._lock:
            state = self._state()
            if state == "closed":
                return True
            if state == "half_open" and not self._trial_running:
                self._trial_running = True
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._trial_running = False

    def record_failure(self) -> bool:
        """Count a failed attempt; True when this failure opened (or re-opened) the breaker."""
        with self._lock:
            was_trial = self._trial_running
            self._trial_running = False
            self._failures += 1
            if was_trial or (self._opened_at is None and self._failures >= self._threshold):
                self._opened_at = self._clock()
                return True
            return False


__all__ = ["BreakerState", "CircuitBreaker", "backoff_delay"]
