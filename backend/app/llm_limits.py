"""Global LLM concurrency + circuit breaker; per-thread job deadlines."""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

logger = logging.getLogger("llm_limits")

_THREAD = threading.local()


class LLMBudgetExceeded(RuntimeError):
    """Raised when concurrency, deadline, or circuit breaker blocks a call."""


@dataclass
class LLMLimitSnapshot:
    in_flight: int
    max_concurrent: int
    open_until: float
    consecutive_failures: int
    deadline_at: float | None


class LLMLimiter:
    def __init__(
        self,
        *,
        max_concurrent: int = 4,
        circuit_fail_threshold: int = 5,
        circuit_cooldown_sec: float = 30.0,
    ) -> None:
        self.max_concurrent = max(1, int(max_concurrent))
        self.circuit_fail_threshold = max(1, int(circuit_fail_threshold))
        self.circuit_cooldown_sec = float(circuit_cooldown_sec)
        self._sem = threading.Semaphore(self.max_concurrent)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._consecutive_failures = 0
        self._open_until = 0.0

    def configure(
        self,
        *,
        max_concurrent: int | None = None,
        circuit_fail_threshold: int | None = None,
        circuit_cooldown_sec: float | None = None,
    ) -> None:
        with self._lock:
            if circuit_fail_threshold is not None:
                self.circuit_fail_threshold = max(1, int(circuit_fail_threshold))
            if circuit_cooldown_sec is not None:
                self.circuit_cooldown_sec = float(circuit_cooldown_sec)
            if max_concurrent is not None:
                new_max = max(1, int(max_concurrent))
                # Never replace the semaphore while callers hold it — that breaks the cap.
                if new_max != self.max_concurrent:
                    if self._in_flight == 0:
                        self.max_concurrent = new_max
                        self._sem = threading.Semaphore(self.max_concurrent)
                    else:
                        logger.warning(
                            "skip llm semaphore resize while in_flight=%s (wanted=%s have=%s)",
                            self._in_flight,
                            new_max,
                            self.max_concurrent,
                        )

    def set_deadline(self, deadline_at: float | None) -> None:
        """Bind deadline to the current worker thread / task context only."""
        _THREAD.deadline_at = deadline_at

    def clear_deadline(self) -> None:
        self.set_deadline(None)

    @contextmanager
    def deadline_context(self, deadline_at: float | None) -> Iterator[None]:
        """Temporarily bind an absolute job deadline to this execution thread."""
        previous = getattr(_THREAD, "deadline_at", None)
        self.set_deadline(deadline_at)
        try:
            yield
        finally:
            self.set_deadline(previous)

    def remaining_deadline_sec(self) -> float | None:
        deadline_at = getattr(_THREAD, "deadline_at", None)
        if deadline_at is None:
            return None
        return max(0.0, deadline_at - time.monotonic())

    def snapshot(self) -> LLMLimitSnapshot:
        with self._lock:
            return LLMLimitSnapshot(
                in_flight=self._in_flight,
                max_concurrent=self.max_concurrent,
                open_until=self._open_until,
                consecutive_failures=self._consecutive_failures,
                deadline_at=getattr(_THREAD, "deadline_at", None),
            )

    def _ensure_allowed(self) -> None:
        now = time.monotonic()
        deadline_at = getattr(_THREAD, "deadline_at", None)
        if deadline_at is not None and now >= deadline_at:
            raise LLMBudgetExceeded("job_deadline_exceeded")
        with self._lock:
            if now < self._open_until:
                raise LLMBudgetExceeded(f"circuit_open_until={self._open_until - now:.1f}s")

    @contextmanager
    def slot(self, *, acquire_timeout: float = 60.0) -> Iterator[None]:
        self._ensure_allowed()
        remaining = self.remaining_deadline_sec()
        if remaining is not None:
            acquire_timeout = min(acquire_timeout, max(0.1, remaining))
        got = self._sem.acquire(timeout=max(0.1, acquire_timeout))
        if not got:
            raise LLMBudgetExceeded("llm_concurrency_timeout")
        with self._lock:
            self._in_flight += 1
        try:
            self._ensure_allowed()
            yield
        finally:
            with self._lock:
                self._in_flight = max(0, self._in_flight - 1)
            self._sem.release()

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._open_until = 0.0

    def record_failure(self, *, is_rate_limit: bool = False) -> None:
        with self._lock:
            self._consecutive_failures += 1
            threshold = max(1, self.circuit_fail_threshold - (1 if is_rate_limit else 0))
            if self._consecutive_failures >= threshold:
                self._open_until = time.monotonic() + self.circuit_cooldown_sec
                logger.warning(
                    "llm circuit opened for %.1fs after %s failures",
                    self.circuit_cooldown_sec,
                    self._consecutive_failures,
                )


_GLOBAL_LIMITER = LLMLimiter()


def get_llm_limiter() -> LLMLimiter:
    return _GLOBAL_LIMITER


def configure_llm_limiter_from_settings(settings: Any) -> LLMLimiter:
    limiter = get_llm_limiter()
    limiter.configure(
        max_concurrent=int(getattr(settings, "llm_max_concurrent", 4) or 4),
        circuit_fail_threshold=int(getattr(settings, "llm_circuit_fail_threshold", 5) or 5),
        circuit_cooldown_sec=float(getattr(settings, "llm_circuit_cooldown_sec", 30) or 30),
    )
    return limiter
