"""Tiny in-process circuit breaker for optional Redis-backed features.

This is a *latency optimisation only* - it holds no distributed state. While
open, calls are skipped immediately instead of each request paying a connect/read
timeout against an unavailable Redis.
"""

from __future__ import annotations

import time


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, cooldown_seconds: float = 2.0) -> None:
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= self._cooldown:
            # Half-open: let one probe through; a failure re-opens immediately.
            self._opened_at = None
            self._failures = self._threshold - 1
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = time.monotonic()
