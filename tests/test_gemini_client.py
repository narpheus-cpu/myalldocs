import contextlib
import sys
from types import ModuleType, SimpleNamespace

from indexer.gemini_client import GeminiClient
from indexer.model_selector import SelectedModel
from indexer.rate_limiter import RateLimiter, ServiceUnavailablePaused


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


def client_with_models(names, responder, max_service_fallbacks=2):
    client = object.__new__(GeminiClient)
    client.client = SimpleNamespace(models=SimpleNamespace(generate_content=responder))
    client.rate_limiter = RateLimiter({"maxRetries": 0, "circuitBreakerFailures": 5, "safetyMargin": 1}, sleeper=lambda _: None)
    client._models = [SelectedModel(name, name) for name in names]
    client._model_index = 0
    client.selected = client._models[0]
    client._max_service_fallbacks = max_service_fallbacks
    client._service_fallbacks = 0
    client.attempted_models = [client.selected.name]
    client.model_switch_count = 0
    client.last_model_error = ""
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


def test_503_after_allowed_fallbacks_remains_service_pause():
    def responder(model, contents, config):
        err = RuntimeError("503 UNAVAILABLE")
        err.status_code = 503
        raise err

    client = client_with_models(["gemini-a-flash", "gemini-b-flash"], responder, max_service_fallbacks=1)
    with fake_genai_module():
        try:
            client.generate_json("local source only")
        except ServiceUnavailablePaused as exc:
            assert "gemini-a-flash, gemini-b-flash" in str(exc)
        else:
            raise AssertionError("expected ServiceUnavailablePaused")
