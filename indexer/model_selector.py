from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


class NoSupportedModel(RuntimeError):
    pass


@dataclass(frozen=True)
class SelectedModel:
    name: str
    display_name: str


def rank_models(models: Iterable[Any], policy: dict) -> list[SelectedModel]:
    candidates: list[tuple[int, int, int, str, str]] = []
    deny = tuple(value.casefold() for value in policy.get("denyNameFragments", []))
    allowed = [re.compile(p, re.I) for p in policy.get("allowNamePatterns", [r"^gemini-"])]
    preferred = [re.compile(p, re.I) for p in policy.get("preferencePatterns", [])]
    for item in models:
        raw_name = str(getattr(item, "name", ""))
        name = raw_name.removeprefix("models/")
        lowered = name.casefold()
        actions = list(getattr(item, "supported_actions", None) or getattr(item, "supported_generation_methods", None) or [])
        description = str(getattr(item, "description", "")).casefold()
        if "generateContent" not in actions:
            continue
        if any(fragment in lowered or fragment in description for fragment in deny):
            continue
        if not any(pattern.search(name) for pattern in allowed):
            continue
        rank = next((i for i, pattern in enumerate(preferred) if pattern.search(name)), len(preferred))
        version = re.search(r"gemini-(\d+)(?:\.(\d+))?", name, re.I)
        major = int(version.group(1)) if version else 0
        minor = int(version.group(2) or 0) if version else 0
        candidates.append((rank, -major, -minor, name, str(getattr(item, "display_name", name))))
    if not candidates:
        raise NoSupportedModel("NO_SUPPORTED_MODEL: no stable generateContent model matched model-policy.json")
    candidates.sort()
    return [SelectedModel(name, display_name) for _, _, _, name, display_name in candidates]


def select_model(models: Iterable[Any], policy: dict) -> SelectedModel:
    return rank_models(models, policy)[0]
