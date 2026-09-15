from __future__ import annotations

import json
from pathlib import Path

from indexer.canonical import legacy_to_canonical, write_search_index
from indexer.config import ROOT
from indexer.storage import RepositoryStorage


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def migrate(root: Path = ROOT) -> int:
    count = 0
    storage = RepositoryStorage(root)
    overrides = storage.overrides()
    for directory in sorted((root / "data" / "books").iterdir()):
        if not directory.is_dir() or (directory / "book.json").exists():
            continue
        manifest = _read(directory / "manifest.json")
        if not manifest or manifest.get("indexStatus") != "COMPLETE":
            continue
        canonical = legacy_to_canonical(
            manifest,
            _read(directory / "summary.json"),
            _read(directory / "analysis.json"),
            _read(directory / "timeline.json"),
            _read(directory / "relationships.json"),
            _read(directory / "chunks.json"),
        )
        override = overrides.get(str(manifest.get("driveFileId") or ""), {})
        if isinstance(override, dict) and override:
            identity = canonical["identity"]
            for key in ("title", "author", "genre"):
                if override.get(key):
                    identity[key] = override[key]
            if isinstance(override.get("tags"), list):
                identity["tags"] = override["tags"]
            if override.get("documentType"):
                identity["workProfile"]["primary"] = override["documentType"]
            canonical["system"]["edited"] = True
        storage.save_canonical(canonical)
        count += 1
    write_search_index(root)
    return count


if __name__ == "__main__":
    print(f"migrated={migrate()}")
