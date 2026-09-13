import contextlib
import sys
from types import ModuleType, SimpleNamespace

from indexer.gemini_client import GeminiClient, InvalidJsonResponse, _json_from_text, _model_unavailable
from indexer.model_selector import SelectedModel
from indexer.rate_limiter import BudgetExceeded, RateLimitPaused, RateLimiter, ServiceUnavailablePaused


class FakeGenerateConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@contextlib.contextmanager
def fake_genai_module():
    fake_genai = ModuleType("google.genai")
    fake_genai.types = SimpleNamespace(GenerateContentConfig=FakeGenerateConfig)
    fake_google = ModuleType("google")
    fake_google.__path__ = []
    fake_google.genai = fake_genai
    previous_google = sys.modules.get("google")
    previous_genai = sys.modules.get("google.genai")
    sys.modules["google"] = fake_google
    sys.modules["google.genai"] = fake_genai
    try:
        yield
    finally:
        if previous_google is None:
            sys.modules.pop("google", None)
        else:
            sys.modules["google"] = previous_google
        if previous_genai is None:
            sys.modules.pop("google.genai", None)
        else:
            sys.modules["google.genai"] = previous_genai


def client_with_models(names, responder, max_invalid_json_retries=1, max_invalid_json_fallbacks=1, rate_retries=0, max_model_cycles=1, cooldown=0):
    client = object.__new__(GeminiClient)
    client.client = SimpleNamespace(models=SimpleNamespace(generate_content=responder))
    client.rate_limiter = RateLimiter({"maxRetries": rate_retries, "circuitBreakerFailures": 20, "safetyMargin": 1}, sleeper=lambda _: None)
    client._models = [SelectedModel(name, name) for name in names]
    client._model_index = 0
    client.selected = client._models[0]
    client._retries_per_model = rate_retries
    client._max_model_cycles = max_model_cycles
    client._cycle_cooldown = cooldown
    client._cycle_cooldown_cap = cooldown
    client._model_cycle = 1
    client.model_cycle_restarts = 0
    client._max_invalid_json_retries = max_invalid_json_retries
    client._max_invalid_json_fallbacks = max_invalid_json_fallbacks
    client.attempted_models = [client.selected.name]
    client.model_switch_count = 0
    client.last_model_error = ""
    client.invalid_json_responses = 0
    client._event_callback = None
    client.rate_limiter.set_event_callback(client._on_rate_event)
    return client


def test_503_switches_to_next_runtime_approved_model():
    calls = []

    def responder(model, contents, config):
        calls.append(model)
        if model == "gemini-a-flash":
            err = RuntimeError("503 UNAVAILABLE")
            err.status_code = 503
            raise err
        return SimpleNamespace(text='{"ok": true}', usage_metadata=None)

    client = client_with_models(["gemini-a-flash", "gemini-b-flash"], responder)
    events = []
    client.set_event_callback(events.append)
    with fake_genai_module():
        assert client.generate_json("local source only") == {"ok": True}
    assert calls == ["gemini-a-flash", "gemini-b-flash"]
    assert client.model_name == "gemini-b-flash"
    assert client.attempted_models == ["gemini-a-flash", "gemini-b-flash"]
    assert client.model_switch_count == 1
    assert any(event["event"] == "MODEL_FALLBACK" for event in events)


def test_503_after_every_model_remains_service_pause():
    def responder(model, contents, config):
        err = RuntimeError("503 UNAVAILABLE")
        err.status_code = 503
        raise err

    client = client_with_models(["gemini-a-flash", "gemini-b-flash"], responder)
    with fake_genai_module():
        try:
            client.generate_json("local source only")
        except ServiceUnavailablePaused as exc:
            assert "gemini-a-flash, gemini-b-flash" in str(exc)
        else:
            raise AssertionError("expected ServiceUnavailablePaused")


def test_429_retries_each_free_model_once_then_moves_to_next():
    calls = []

    def responder(model, contents, config):
        calls.append(model)
        if model != "gemini-c-flash":
            err = RuntimeError("429 RESOURCE_EXHAUSTED")
            err.status_code = 429
            raise err
        return SimpleNamespace(text='{"ok": true}', usage_metadata=None)

    client = client_with_models(
        ["gemini-a-flash", "gemini-b-flash", "gemini-c-flash"],
        responder,
        rate_retries=1,
    )
    with fake_genai_module():
        assert client.generate_json("local source only") == {"ok": True}
    assert calls == ["gemini-a-flash", "gemini-a-flash", "gemini-b-flash", "gemini-b-flash", "gemini-c-flash"]
    assert client.attempted_models == ["gemini-a-flash", "gemini-b-flash", "gemini-c-flash"]
    assert client.model_name == "gemini-c-flash"


def test_429_pauses_only_after_every_allowed_free_model_fails():
    def responder(model, contents, config):
        err = RuntimeError("429 RESOURCE_EXHAUSTED")
        err.status_code = 429
        raise err

    client = client_with_models(["gemini-a-flash", "gemini-b-flash", "gemini-c-flash"], responder)
    with fake_genai_module():
        try:
            client.generate_json("local source only")
        except RateLimitPaused as exc:
            assert "gemini-a-flash, gemini-b-flash, gemini-c-flash" in str(exc)
        else:
            raise AssertionError("expected RateLimitPaused")
    assert client.model_switch_count == 2


def test_all_models_cool_down_then_restart_from_first_model():
    calls = []
    waits = []

    def responder(model, contents, config):
        calls.append(model)
        if len(calls) <= 2:
            err = RuntimeError("429 RESOURCE_EXHAUSTED")
            err.status_code = 429
            raise err
        return SimpleNamespace(text='{"ok": true}', usage_metadata=None)

    client = client_with_models(
        ["gemini-a-flash", "gemini-b-flash"], responder,
        max_model_cycles=2, cooldown=300,
    )
    client.rate_limiter.sleep = waits.append
    events = []
    client.set_event_callback(events.append)
    with fake_genai_module():
        assert client.generate_json("local source only") == {"ok": True}
    assert calls == ["gemini-a-flash", "gemini-b-flash", "gemini-a-flash"]
    assert waits == [300]
    assert client.model_cycle_restarts == 1
    assert any(event["event"] == "MODEL_COOLDOWN" for event in events)
    assert any(event["event"] == "MODEL_CYCLE_RESTART" for event in events)


def test_local_request_budget_never_switches_models():
    def responder(model, contents, config):
        return SimpleNamespace(text='{"ok": true}', usage_metadata=None)

    client = client_with_models(["gemini-a-flash", "gemini-b-flash"], responder)
    client.rate_limiter.config["maxRequestsPerRun"] = 1
    client.rate_limiter.usage.attempts = 1
    with fake_genai_module():
        try:
            client.generate_json("local source only")
        except BudgetExceeded:
            pass
        else:
            raise AssertionError("expected BudgetExceeded")
    assert client.model_name == "gemini-a-flash"
    assert client.model_switch_count == 0


def test_json_parser_accepts_fenced_or_explanatory_wrapper_only():
    assert _json_from_text('```json\n{"ok": true}\n```') == {"ok": True}
    assert _json_from_text('결과입니다:\n{"ok": true}\n끝') == {"ok": True}


def test_invalid_json_is_retried_with_strict_prompt_then_succeeds():
    prompts = []

    def responder(model, contents, config):
        prompts.append(contents)
        text = '{"ok": true "broken": 1}' if len(prompts) == 1 else '{"ok": true}'
        return SimpleNamespace(text=text, usage_metadata=None)

    client = client_with_models(["gemini-a-flash"], responder)
    events = []
    client.set_event_callback(events.append)
    with fake_genai_module():
        assert client.generate_json("local source only") == {"ok": True}
    assert len(prompts) == 2
    assert "JSON 문법 오류" in prompts[1]
    assert client.invalid_json_responses == 1
    assert client.rate_limiter.usage.requests == 2
    assert any(event["event"] == "INVALID_JSON_RETRY" for event in events)


def test_repeated_invalid_json_falls_back_to_next_free_model():
    calls = []

    def responder(model, contents, config):
        calls.append(model)
        text = '{"ok": true "broken": 1}' if model == "gemini-a-flash" else '{"ok": true}'
        return SimpleNamespace(text=text, usage_metadata=None)

    client = client_with_models(
        ["gemini-a-flash", "gemini-b-flash"], responder,
        max_invalid_json_retries=0,
        max_invalid_json_fallbacks=1,
    )
    with fake_genai_module():
        assert client.generate_json("local source only") == {"ok": True}
    assert calls == ["gemini-a-flash", "gemini-b-flash"]
    assert client.model_name == "gemini-b-flash"
    assert client.model_switch_count == 1


def test_wrong_json_shape_is_retried_before_pipeline_uses_it():
    calls = []

    def responder(model, contents, config):
        calls.append(model)
        text = '[{"wrong": true}]' if len(calls) == 1 else '{"ok": true}'
        return SimpleNamespace(text=text, usage_metadata=None)

    client = client_with_models(["gemini-a-flash"], responder)
    with fake_genai_module():
        assert client.generate_json("local source only", expected_type=dict) == {"ok": True}
    assert calls == ["gemini-a-flash", "gemini-a-flash"]
    assert client.invalid_json_responses == 1


def test_invalid_json_error_never_includes_raw_model_output():
    raw = '{"private_original": "DO_NOT_LOG" "broken": true}'

    def responder(model, contents, config):
        return SimpleNamespace(text=raw, usage_metadata=None)

    client = client_with_models(
        ["gemini-a-flash"], responder,
        max_invalid_json_retries=0,
        max_invalid_json_fallbacks=0,
    )
    with fake_genai_module():
        try:
            client.generate_json("local source only")
        except InvalidJsonResponse as exc:
            assert "DO_NOT_LOG" not in str(exc)
            assert "line" in str(exc) and "column" in str(exc)
        else:
            raise AssertionError("expected InvalidJsonResponse")


def test_new_user_model_404_is_treated_as_fallback_not_book_error():
    error = RuntimeError(
        "404 NOT_FOUND: This model models/gemini-2.5-flash is no longer available to new users."
    )
    error.status_code = 404
    assert _model_unavailable(error)


def test_new_user_model_404_switches_to_flash_lite():
    calls = []

    def responder(model, contents, config):
        calls.append(model)
        if model == "gemini-2.5-flash":
            error = RuntimeError("404 NOT_FOUND: model gemini-2.5-flash is no longer available to new users")
            error.status_code = 404
            raise error
        return SimpleNamespace(text='{"ok": true}', usage_metadata=None)

    client = client_with_models(["gemini-2.5-flash", "gemini-3.5-flash-lite"], responder)
    with fake_genai_module():
        assert client.generate_json("local source only") == {"ok": True}
    assert calls == ["gemini-2.5-flash", "gemini-3.5-flash-lite"]
