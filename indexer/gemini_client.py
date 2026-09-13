from __future__ import annotations

import json
import re
from typing import Any, Callable

from indexer.model_selector import NoSupportedModel, SelectedModel, rank_models
from indexer.rate_limiter import BudgetExceeded, RateLimitPaused, RateLimiter, ServiceUnavailablePaused


class InvalidJsonResponse(ValueError):
    pass


class UnexpectedJsonShape(ValueError):
    pass


class GeminiClient:
    def __init__(self, api_key: str, model_policy: dict, rate_limiter: RateLimiter) -> None:
        from google import genai

        self._genai = genai
        self.client = genai.Client(api_key=api_key)
        self.rate_limiter = rate_limiter
        self._models = rank_models(self.client.models.list(), model_policy)
        self._model_index = 0
        self.selected: SelectedModel = self._models[0]
        self._retries_per_model = max(0, int(model_policy.get("retriesPerModel", 1)))
        self._max_model_cycles = max(1, int(model_policy.get("maxModelCyclesPerRequest", 3)))
        self._cycle_cooldown = max(0.0, float(model_policy.get("modelCycleCooldownSeconds", 300)))
        self._cycle_cooldown_cap = max(self._cycle_cooldown, float(model_policy.get("modelCycleCooldownMaxSeconds", 900)))
        self._model_cycle = 1
        self.model_cycle_restarts = 0
        self._max_invalid_json_retries = max(0, int(model_policy.get("maxInvalidJsonRetries", 1)))
        self._max_invalid_json_fallbacks = max(0, int(model_policy.get("maxInvalidJsonFallbacks", 1)))
        self.attempted_models = [self.selected.name]
        self.model_switch_count = 0
        self.last_model_error = ""
        self.invalid_json_responses = 0
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

    @property
    def model_cycle(self) -> int:
        return self._model_cycle

    @property
    def max_model_cycles(self) -> int:
        return self._max_model_cycles

    def _restart_model_cycle(self, reason: str) -> bool:
        if self._model_cycle >= self._max_model_cycles:
            return False
        previous = self.model_name
        delay = min(self._cycle_cooldown_cap, self._cycle_cooldown * (2 ** (self._model_cycle - 1)))
        next_cycle = self._model_cycle + 1
        self.last_model_error = reason[:300]
        self._emit_event({
            "event": "MODEL_COOLDOWN",
            "model": previous,
            "modelCycle": self._model_cycle,
            "maxModelCycles": self._max_model_cycles,
            "retryDelaySeconds": round(delay, 2),
            "message": f"모든 무료 모델을 확인했습니다. {int(delay)}초 쿨다운 후 {next_cycle}번째 순환을 시작합니다.",
        })
        self.rate_limiter.sleep(delay)
        self._model_index = 0
        self.selected = self._models[0]
        self._model_cycle = next_cycle
        self.model_cycle_restarts += 1
        self.model_switch_count += 1
        self.attempted_models.append(self.model_name)
        self.rate_limiter.reset_retry_state()
        self._emit_event({
            "event": "MODEL_CYCLE_RESTART",
            "model": self.model_name,
            "previousModel": previous,
            "modelCycle": self._model_cycle,
            "maxModelCycles": self._max_model_cycles,
            "message": f"쿨다운이 끝나 {self.model_name}부터 무료 모델 탐색을 다시 시작합니다.",
        })
        return True

    def _switch_or_restart(self, reason: str) -> bool:
        return self._switch_model(reason) or self._restart_model_cycle(reason)

    def generate_json(self, prompt: str, schema: dict | None = None, expected_type: type | None = None) -> Any:
        from google.genai import types

        config_kwargs: dict[str, Any] = {
            "response_mime_type": "application/json",
            "temperature": 0.1,
        }
        if schema:
            config_kwargs["response_json_schema"] = schema
        config = types.GenerateContentConfig(**config_kwargs)

        request_prompt = prompt
        invalid_json_retries = 0
        invalid_json_fallbacks = 0
        while True:
            def operation():
                return self.client.models.generate_content(model=self.model_name, contents=request_prompt, config=config)

            try:
                response = self.rate_limiter.call(
                    operation,
                    estimated_input_tokens=max(1, len(request_prompt) // 4),
                    max_retries=self._retries_per_model,
                )
            except BudgetExceeded:
                raise
            except RateLimitPaused as exc:
                self.last_model_error = str(exc)[:300]
                if not self._switch_or_restart(str(exc)):
                    tried = ", ".join(self.attempted_models)
                    raise RateLimitPaused(f"모든 허용 무료 모델을 {self._max_model_cycles}회 순환한 뒤 체크포인트 일시정지: {tried}") from exc
                continue
            except ServiceUnavailablePaused as exc:
                self.last_model_error = str(exc)[:300]
                if not self._switch_or_restart(str(exc)):
                    tried = ", ".join(self.attempted_models)
                    raise ServiceUnavailablePaused(f"모든 무료 모델을 {self._max_model_cycles}회 순환했지만 서비스가 응답하지 않아 체크포인트 일시정지: {tried}") from exc
                continue
            except Exception as exc:
                if not _model_unavailable(exc):
                    raise
                if not self._switch_model(str(exc)):
                    raise NoSupportedModel("NO_SUPPORTED_MODEL: every allowed runtime model was unavailable") from exc
                continue
            usage = getattr(response, "usage_metadata", None)
            self.rate_limiter.record(
                int(getattr(usage, "prompt_token_count", 0) or 0),
                int(getattr(usage, "candidates_token_count", 0) or 0),
            )
            self._model_cycle = 1
            try:
                parsed = _json_from_text(response.text or "")
                if expected_type is not None and not isinstance(parsed, expected_type):
                    raise UnexpectedJsonShape(f"expected {expected_type.__name__}")
                return parsed
            except (json.JSONDecodeError, UnexpectedJsonShape) as exc:
                self.invalid_json_responses += 1
                error = (
                    f"invalid JSON response from {self.model_name}: line {exc.lineno}, column {exc.colno}"
                    if isinstance(exc, json.JSONDecodeError)
                    else f"invalid JSON structure from {self.model_name}: {exc}"
                )
                self.last_model_error = error
                if invalid_json_retries < self._max_invalid_json_retries:
                    invalid_json_retries += 1
                    request_prompt = _strict_json_retry_prompt(prompt)
                    self._emit_event({
                        "event": "INVALID_JSON_RETRY",
                        "model": self.model_name,
                        "attempt": invalid_json_retries,
                        "maxAttempts": self._max_invalid_json_retries,
                        "message": f"{self.model_name}의 JSON 문법 오류로 안전하게 다시 요청합니다.",
                    })
                    continue
                if invalid_json_fallbacks < self._max_invalid_json_fallbacks and self._switch_model(error):
                    invalid_json_fallbacks += 1
                    invalid_json_retries = 0
                    request_prompt = _strict_json_retry_prompt(prompt)
                    continue
                raise InvalidJsonResponse(error) from exc


def _json_from_text(text: str) -> Any:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.I | re.S)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as original:
        starts = [position for position in (cleaned.find("{"), cleaned.find("[")) if position >= 0]
        if not starts:
            raise
        start = min(starts)
        closing = "}" if cleaned[start] == "{" else "]"
        end = cleaned.rfind(closing)
        if end <= start:
            raise
        try:
            return json.loads(cleaned[start:end + 1])
        except json.JSONDecodeError:
            raise original


def _strict_json_retry_prompt(prompt: str) -> str:
    return (
        prompt
        + "\n\n이전 응답에는 JSON 문법 오류 또는 구조 오류가 있었습니다. 원문 근거와 요구된 구조는 유지하되, "
          "설명·마크다운 코드블록·주석 없이 JSON 파서가 읽을 수 있는 단 하나의 유효한 JSON 값만 출력하세요. "
          "문자열 내부 큰따옴표와 줄바꿈을 올바르게 이스케이프하고 모든 쉼표·괄호를 확인하세요."
    )


def _model_unavailable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if callable(status):
        status = status()
    text = str(exc).casefold()
    return str(status) in {"400", "404"} and "model" in text and any(
        word in text for word in ("not found", "not_found", "unsupported", "unavailable", "no longer available")
    )
