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

    def generate_json(self, prompt: str, schema: dict | None = None, use_search: bool = False) -> tuple[Any, list[dict]]:
        from google.genai import types

        config_kwargs: dict[str, Any] = {
            "response_mime_type": "application/json",
            "temperature": 0.1,
        }
        if schema:
            config_kwargs["response_json_schema"] = schema
        if use_search:
            config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
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
        return data, _grounding_sources(response)

    def verify_metadata_on_web(self, title: str, author: str, excerpt: str) -> list[dict]:
        prompt = f"""Use Google Search only to verify this uncertain book metadata. Search conservatively for editions and namesakes.
Return JSON with a `candidates` array. Each candidate: title, author, confidence (0..1), rationale.
Do not summarize or supplement the book. Do not claim identity without corroboration.
Local title candidate: {title}
Local author candidate: {author}
Short source excerpt: {excerpt[:1200]}
"""
        data, sources = self.generate_json(prompt, use_search=True)
        candidates = data.get("candidates", []) if isinstance(data, dict) else []
        for candidate in candidates:
            candidate["groundingSources"] = sources
        return candidates


def _json_from_text(text: str) -> Any:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    return json.loads(cleaned)


def _grounding_sources(response: Any) -> list[dict]:
    found: list[dict] = []
    for candidate in getattr(response, "candidates", None) or []:
        meta = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(meta, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            if uri:
                found.append({"title": getattr(web, "title", None), "url": uri})
    return found


def _model_unavailable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if callable(status):
        status = status()
    text = str(exc).casefold()
    return str(status) in {"400", "404"} and "model" in text and any(word in text for word in ("not found", "unsupported", "unavailable"))
