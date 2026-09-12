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
