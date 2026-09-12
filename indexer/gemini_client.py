from __future__ import annotations

import json
import re
from typing import Any, Callable

from indexer.model_selector import NoSupportedModel, SelectedModel, rank_models
from indexer.rate_limiter import RateLimiter, ServiceUnavailablePaused


class GeminiClient:
    def __init__(self, api_key: str, model_policy: dict, rate_limiter: RateLimiter) -> None:
        from google import genai

        self._genai = genai
        self.client = genai.Client(api_key=api_key)
        self.rate_limiter = rate_limiter
        self._models = rank_models(self.client.models.list(), model_policy)
        self._model_index = 0
        self.selected: SelectedModel = self._models[0]
        self._max_service_fallbacks = max(0, int(model_policy.get("maxServiceFallbacks", 2)))
        self._service_fallbacks = 0
        self.attempted_models = [self.selected.name]
        self.model_switch_count = 0
        self.last_model_error = ""
        self._event_callback: Callable[[dict[str, Any]], None] | None = None
        self.rate_limiter.set_event_callback(self._on_rate_event)

    @property
    def model_name(self) -> str:
        return self.selected.name

    def set_event_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self._event_callback = callback

    def _emit_event(self, event: dict[str, Any]) -> None:
        if self._event_callback is not None:
            self._event_callback(event)

    def _on_rate_event(self, event: dict[str, Any]) -> None:
        self._emit_event({**event, "model": self.model_name})

    def _switch_model(self, reason: str) -> bool:
        if self._model_index + 1 >= len(self._models):
            return False
        previous = self.model_name
        self._model_index += 1
        self.selected = self._models[self._model_index]
        self.model_switch_count += 1
        self.attempted_models.append(self.model_name)
        self.last_model_error = reason[:300]
        self.rate_limiter.reset_retry_state()
        self._emit_event({
            "event": "MODEL_FALLBACK",
            "model": self.model_name,
            "previousModel": previous,
            "message": f"{previous} 실패 후 {self.model_name}(으)로 전환했습니다.",
        })
        return True

    def generate_json(self, prompt: str, schema: dict | None = None) -> Any:
        from google.genai import types

        config_kwargs: dict[str, Any] = {
            "response_mime_type": "application/json",
            "temperature": 0.1,
        }
        if schema:
            config_kwargs["response_json_schema"] = schema
        config = types.GenerateContentConfig(**config_kwargs)

        def operation():
            return self.client.models.generate_content(model=self.model_name, contents=prompt, config=config)

        while True:
            try:
                response = self.rate_limiter.call(operation, estimated_input_tokens=max(1, len(prompt) // 4))
                break
            except ServiceUnavailablePaused as exc:
                self.last_model_error = str(exc)[:300]
                if self._service_fallbacks >= self._max_service_fallbacks or not self._switch_model(str(exc)):
                    tried = ", ".join(self.attempted_models)
                    raise ServiceUnavailablePaused(f"Gemini service unavailable after free-model fallback: {tried}") from exc
                self._service_fallbacks += 1
            except Exception as exc:
                if not _model_unavailable(exc):
                    raise
                if not self._switch_model(str(exc)):
                    raise NoSupportedModel("NO_SUPPORTED_MODEL: every allowed runtime model was unavailable") from exc
        usage = getattr(response, "usage_metadata", None)
        self.rate_limiter.record(
            int(getattr(usage, "prompt_token_count", 0) or 0),
            int(getattr(usage, "candidates_token_count", 0) or 0),
        )
        data = _json_from_text(response.text or "")
        return data


def _json_from_text(text: str) -> Any:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    return json.loads(cleaned)


def _model_unavailable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if callable(status):
        status = status()
    text = str(exc).casefold()
    return str(status) in {"400", "404"} and "model" in text and any(word in text for word in ("not found", "unsupported", "unavailable"))
