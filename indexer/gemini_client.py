from __future__ import annotations

import json
import re
from typing import Any

from indexer.model_selector import NoSupportedModel, SelectedModel, rank_models
from indexer.rate_limiter import RateLimiter


class GeminiClient:
    def __init__(self, api_key: str, model_policy: dict, rate_limiter: RateLimiter) -> None:
        from google import genai

        self._genai = genai
        self.client = genai.Client(api_key=api_key)
        self.rate_limiter = rate_limiter
        self._models = rank_models(self.client.models.list(), model_policy)
        self._model_index = 0
        self.selected: SelectedModel = self._models[0]

    @property
    def model_name(self) -> str:
        return self.selected.name

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
            except Exception as exc:
                if not _model_unavailable(exc):
                    raise
                self._model_index += 1
                if self._model_index >= len(self._models):
                    raise NoSupportedModel("NO_SUPPORTED_MODEL: every allowed runtime model was unavailable") from exc
                self.selected = self._models[self._model_index]
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
