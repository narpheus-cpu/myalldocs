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
    deny_names = tuple(value.casefold() for value in policy.get("denyNameFragments", []))
    deny_descriptions = tuple(value.casefold() for value in policy.get("denyDescriptionFragments", ["deprecated", "retired", "shut down"]))
    allowed = [re.compile(p, re.I) for p in policy.get("allowNamePatterns", [r"^gemini-"])]
    free_tier = [re.compile(p, re.I) for p in policy.get("freeTierModelPatterns", [])]
    zero_cost = policy.get("zeroCostMode") is True
    if zero_cost and (policy.get("requireFreeTierPublishedModel") is not True or not free_tier):
        raise NoSupportedModel("NO_SUPPORTED_MODEL: zero-cost mode has no published Free Tier model policy")
    preferred = [re.compile(p, re.I) for p in policy.get("preferencePatterns", [])]
    ordered = {str(name).casefold(): index for index, name in enumerate(policy.get("preferredModelOrder", []))}
    for item in models:
        raw_name = str(getattr(item, "name", ""))
        name = raw_name.removeprefix("models/")
        lowered = name.casefold()
        actions = list(getattr(item, "supported_actions", None) or getattr(item, "supported_generation_methods", None) or [])
        description = str(getattr(item, "description", "")).casefold()
        if "generateContent" not in actions:
            continue
        if any(fragment in lowered for fragment in deny_names):
            continue
        if any(fragment in description for fragment in deny_descriptions):
            continue
        if not any(pattern.search(name) for pattern in allowed):
            continue
        if zero_cost and not any(pattern.fullmatch(name) for pattern in free_tier):
            continue
        generic_rank = next((i for i, pattern in enumerate(preferred) if pattern.search(name)), len(preferred))
        exact_name = re.sub(r"-[0-9]{3}$", "", lowered)
        rank = ordered.get(exact_name, len(ordered) + generic_rank)
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
