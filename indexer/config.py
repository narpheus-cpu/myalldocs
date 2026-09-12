from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


@dataclass(frozen=True)
class Settings:
    raw: dict[str, Any]
    model_policy: dict[str, Any]
    profiles: dict[str, Any]
    root: Path = ROOT

    @classmethod
    def load(cls, root: Path = ROOT) -> "Settings":
        return cls(
            raw=_load(root / "config" / "indexer.json"),
            model_policy=_load(root / "config" / "model-policy.json"),
            profiles=_load(root / "config" / "analysis-profiles.json"),
            root=root,
        )

    def secret(self, name: str, required: bool = True) -> str | None:
        value = os.getenv(name)
        if required and not value:
            raise RuntimeError(f"Missing required secret: {name}")
        return value

    @property
    def metadata(self) -> dict[str, Any]:
        return self.raw["metadataResolution"]

    @property
    def quota(self) -> dict[str, Any]:
        return self.raw["quota"]

    def assert_zero_cost(self) -> None:
        policy = self.raw.get("zeroCostPolicy", {})
        forbidden_flags = ("cloudBillingAllowed", "paidGeminiTierAllowed", "externalSearchAllowed", "paidInfrastructureAllowed")
        if policy.get("enabled") is not True or any(policy.get(name) is not False for name in forbidden_flags):
            raise RuntimeError("ZERO_COST_POLICY_VIOLATION: paid or externally billed features cannot be enabled")
        if self.model_policy.get("zeroCostMode") is not True or self.model_policy.get("requireFreeTierPublishedModel") is not True:
            raise RuntimeError("ZERO_COST_POLICY_VIOLATION: model policy must require published Free Tier models")
        drive = self.raw.get("driveQuota", {})
        if drive.get("pauseOn403Or429") is not True:
            raise RuntimeError("ZERO_COST_POLICY_VIOLATION: Drive quota responses must pause the run")
        for name in ("maxQuotaUnitsPerRun", "maxDownloadBytesPerRun"):
            if int(drive.get(name, 0) or 0) <= 0:
                raise RuntimeError(f"ZERO_COST_POLICY_VIOLATION: {name} must be a positive safety limit")
        gemini = self.raw.get("quota", {})
        for name in ("maxRequestsPerRun", "maxTokensPerRun", "maxBooksPerRun", "maxChunksPerRun", "maxRuntimeMinutes"):
            if int(gemini.get(name, 0) or 0) <= 0:
                raise RuntimeError(f"ZERO_COST_POLICY_VIOLATION: {name} must be a positive safety limit")
