from types import SimpleNamespace

import pytest

from indexer.rate_limiter import BudgetExceeded, RateLimitPaused, RateLimiter, ServiceUnavailablePaused


def test_per_run_budget_pauses_without_calling():
    limiter = RateLimiter({"maxRequestsPerRun": 1, "safetyMargin": 1}, sleeper=lambda _: None)
    limiter.usage.attempts = 1
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


def test_503_is_service_pause_not_free_quota_pause_and_counts_failed_attempts():
    events = []
    limiter = RateLimiter(
        {"maxRetries": 1, "circuitBreakerFailures": 5, "safetyMargin": 1},
        sleeper=lambda _: None,
        event_callback=events.append,
    )

    def fail():
        err = RuntimeError("503 UNAVAILABLE")
        err.status_code = 503
        raise err

    with pytest.raises(ServiceUnavailablePaused):
        limiter.call(fail)
    assert limiter.usage.attempts == 2
    assert limiter.usage.requests == 0
    assert limiter.usage.failed_attempts == 2
    assert [event["event"] for event in events] == ["RETRY", "PAUSED"]


def test_429_stays_rate_limit_pause_without_model_service_classification():
    limiter = RateLimiter({"maxRetries": 0, "circuitBreakerFailures": 5, "safetyMargin": 1}, sleeper=lambda _: None)

    def fail():
        err = RuntimeError("429 RESOURCE_EXHAUSTED")
        err.status_code = 429
        raise err

    caught = None
    try:
        limiter.call(fail)
    except RateLimitPaused as exc:
        caught = exc
    assert caught is not None
    assert not isinstance(caught, ServiceUnavailablePaused)
