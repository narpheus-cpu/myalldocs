from types import SimpleNamespace

import pytest

from indexer.rate_limiter import BudgetExceeded, RateLimitPaused, RateLimiter


def test_per_run_budget_pauses_without_calling():
    limiter = RateLimiter({"maxRequestsPerRun": 1, "safetyMargin": 1}, sleeper=lambda _: None)
    limiter.record()
    with pytest.raises(BudgetExceeded):
        limiter.before_request()


def test_retry_after_is_respected_then_succeeds():
    waits=[]; attempts={"n":0}
    limiter=RateLimiter({"maxRetries":2,"circuitBreakerFailures":5,"safetyMargin":1},sleeper=waits.append)
    def operation():
        attempts["n"]+=1
        if attempts["n"]==1:
            err=RuntimeError("429");err.status_code=429;err.response=SimpleNamespace(headers={"Retry-After":"3"});raise err
        return "ok"
    assert limiter.call(operation)=="ok" and waits==[3.0]


def test_repeated_429_opens_circuit_breaker():
    limiter=RateLimiter({"maxRetries":5,"circuitBreakerFailures":2,"safetyMargin":1},sleeper=lambda _:None)
    def fail():
        err=RuntimeError("429");err.status_code=429;raise err
    with pytest.raises(RateLimitPaused): limiter.call(fail)
