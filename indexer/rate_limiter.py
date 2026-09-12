from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable, TypeVar


T = TypeVar("T")


class RateLimitPaused(RuntimeError):
    pass


class BudgetExceeded(RateLimitPaused):
    pass


@dataclass
class Usage:
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class RateLimiter:
    def __init__(self, config: dict, sleeper: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time) -> None:
        self.config = config
        self.sleep = sleeper
        self.clock = clock
        self.usage = Usage()
        self.request_times: deque[float] = deque()
        self.token_events: deque[tuple[float, int]] = deque()
        self.consecutive_retryable_failures = 0

    def before_request(self, estimated_input_tokens: int = 0) -> None:
        max_requests = int(self.config.get("maxRequestsPerRun", 0) or 0)
        max_tokens = int(self.config.get("maxTokensPerRun", 0) or 0)
        if max_requests and self.usage.requests >= max_requests:
            raise BudgetExceeded("per-run request budget reached")
        if max_tokens and self.usage.input_tokens + self.usage.output_tokens + estimated_input_tokens > max_tokens:
            raise BudgetExceeded("per-run token budget reached")
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
        self.request_times.append(now)
        self.token_events.append((now, total))
        self.consecutive_retryable_failures = 0

    def call(self, operation: Callable[[], T], estimated_input_tokens: int = 0) -> T:
        retries = int(self.config.get("maxRetries", 4))
        base = float(self.config.get("backoffBaseSeconds", 2))
        cap = float(self.config.get("backoffMaxSeconds", 60))
        breaker = int(self.config.get("circuitBreakerFailures", 5))
        for attempt in range(retries + 1):
            self.before_request(estimated_input_tokens)
            try:
                return operation()
            except Exception as exc:
                status = _status_code(exc)
                if status not in {408, 429, 500, 502, 503, 504} or attempt >= retries:
                    if status in {429, 503}:
                        raise RateLimitPaused(f"rate limit retries exhausted ({status})") from exc
                    raise
                self.consecutive_retryable_failures += 1
                if self.consecutive_retryable_failures >= breaker:
                    raise RateLimitPaused("rate-limit circuit breaker opened") from exc
                retry_after = _retry_after(exc)
                delay = retry_after if retry_after is not None else min(cap, base * (2**attempt)) + random.uniform(0, min(1.0, base))
                self.sleep(delay)
        raise AssertionError("unreachable")

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
