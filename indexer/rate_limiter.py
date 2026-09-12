from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable, TypeVar


T = TypeVar("T")


class SafePause(RuntimeError):
    """A recoverable stop that must preserve checkpoints for the next run."""


class RateLimitPaused(SafePause):
    pass


class BudgetExceeded(RateLimitPaused):
    pass


class ServiceUnavailablePaused(SafePause):
    pass


@dataclass
class Usage:
    attempts: int = 0
    requests: int = 0
    failed_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class RateLimiter:
    def __init__(
        self,
        config: dict,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.sleep = sleeper
        self.clock = clock
        self.event_callback = event_callback
        self.usage = Usage()
        self.request_times: deque[float] = deque()
        self.token_events: deque[tuple[float, int]] = deque()
        self.last_success_at: float | None = None
        self.consecutive_retryable_failures = 0

    def before_request(self, estimated_input_tokens: int = 0) -> None:
        max_requests = int(self.config.get("maxRequestsPerRun", 0) or 0)
        max_tokens = int(self.config.get("maxTokensPerRun", 0) or 0)
        if max_requests and self.usage.attempts >= max_requests:
            raise BudgetExceeded("per-run request budget reached")
        if max_tokens and self.usage.input_tokens + self.usage.output_tokens + estimated_input_tokens > max_tokens:
            raise BudgetExceeded("per-run token budget reached")
        self._trim()
        minimum_interval = float(self.config.get("minimumSuccessfulRequestIntervalSeconds", 0) or 0)
        if minimum_interval and self.last_success_at is not None:
            interval_wait = minimum_interval - (self.clock() - self.last_success_at)
            if interval_wait > 0:
                self._emit_event({"event": "PACING", "retryDelaySeconds": round(interval_wait, 2)})
                self.sleep(interval_wait)
                self._trim()
        margin = float(self.config.get("safetyMargin", 0.75))
        rpm = int(float(self.config.get("requestsPerMinute", 0) or 0) * margin)
        tpm = int(float(self.config.get("tokensPerMinute", 0) or 0) * margin)
        waits: list[float] = []
        if rpm and len(self.request_times) >= max(1, rpm):
            waits.append(60 - (self.clock() - self.request_times[0]))
        current_tokens = sum(tokens for _, tokens in self.token_events)
        if tpm and current_tokens + estimated_input_tokens > tpm and self.token_events:
            waits.append(60 - (self.clock() - self.token_events[0][0]))
        if waits and max(waits) > 0:
            self.sleep(max(waits) + 0.05)
            self._trim()

    def record(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        now = self.clock()
        total = max(0, input_tokens) + max(0, output_tokens)
        self.usage.requests += 1
        self.usage.input_tokens += max(0, input_tokens)
        self.usage.output_tokens += max(0, output_tokens)
        self.token_events.append((now, total))
        self.last_success_at = now
        self.consecutive_retryable_failures = 0

    def set_event_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self.event_callback = callback

    def reset_retry_state(self) -> None:
        self.consecutive_retryable_failures = 0

    def call(self, operation: Callable[[], T], estimated_input_tokens: int = 0, max_retries: int | None = None) -> T:
        retries = int(self.config.get("maxRetries", 4)) if max_retries is None else max(0, int(max_retries))
        base = float(self.config.get("backoffBaseSeconds", 2))
        cap = float(self.config.get("backoffMaxSeconds", 60))
        breaker = int(self.config.get("circuitBreakerFailures", 5))
        for attempt in range(retries + 1):
            self.before_request(estimated_input_tokens)
            self.usage.attempts += 1
            self.request_times.append(self.clock())
            try:
                return operation()
            except Exception as exc:
                self.usage.failed_attempts += 1
                status = _status_code(exc)
                if status not in {408, 429, 500, 502, 503, 504} or attempt >= retries:
                    pause = _pause_error(status, "retries exhausted")
                    if pause is not None:
                        self._emit_event({"event": "PAUSED", "statusCode": status, "attempt": attempt + 1, "maxAttempts": retries + 1})
                        raise pause from exc
                    raise
                self.consecutive_retryable_failures += 1
                if self.consecutive_retryable_failures >= breaker:
                    pause = _pause_error(status, "circuit breaker opened")
                    if pause is not None:
                        self._emit_event({"event": "PAUSED", "statusCode": status, "attempt": attempt + 1, "maxAttempts": retries + 1})
                        raise pause from exc
                    raise
                retry_after = _retry_after(exc)
                delay = retry_after if retry_after is not None else min(cap, base * (2**attempt)) + random.uniform(0, min(1.0, base))
                self._emit_event({
                    "event": "RETRY",
                    "statusCode": status,
                    "attempt": attempt + 1,
                    "maxAttempts": retries + 1,
                    "retryDelaySeconds": round(delay, 2),
                })
                self.sleep(delay)
        raise AssertionError("unreachable")

    def _emit_event(self, event: dict[str, Any]) -> None:
        if self.event_callback is None:
            return
        try:
            self.event_callback(event)
        except Exception:
            # Monitoring must never alter retry or quota behavior.
            pass

    def _trim(self) -> None:
        cutoff = self.clock() - 60
        while self.request_times and self.request_times[0] <= cutoff:
            self.request_times.popleft()
        while self.token_events and self.token_events[0][0] <= cutoff:
            self.token_events.popleft()


def _status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if callable(value):
            value = value()
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    text = str(exc)
    for code in (429, 503, 504, 500, 408, 403, 401):
        if str(code) in text:
            return code
    return None


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", {}) if response is not None else getattr(exc, "headers", {})
    value = headers.get("Retry-After") if headers else None
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError):
            return None


def _pause_error(status: int | None, reason: str) -> SafePause | None:
    if status == 429:
        return RateLimitPaused(f"Gemini free-tier rate limit: {reason} (429)")
    if status in {408, 500, 502, 503, 504}:
        return ServiceUnavailablePaused(f"Gemini service temporarily unavailable: {reason} ({status})")
    return None
