from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from indexer.checkpoint import atomic_write_json


class RepositoryStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.data = root / "data"

    def overrides(self) -> dict[str, dict]:
        path = self.data / "metadata-overrides.json"
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value.get("byDriveFileId", value)

    def manifest(self, book_id: str) -> dict | None:
        path = self.data / "books" / book_id / "manifest.json"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def save_book(self, book_id: str, files: dict[str, Any]) -> None:
        directory = self.data / "books" / book_id
        for name, value in files.items():
            atomic_write_json(directory / name, value)

    def update_catalog(self, entry: dict) -> None:
        path = self.data / "catalog.json"
        current = {"schemaVersion": 1, "books": []}
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                current = json.load(handle)
        books = [item for item in current.get("books", []) if item.get("bookId") != entry.get("bookId")]
        books.append(entry)
        books.sort(key=lambda item: ((item.get("author") or "").casefold(), (item.get("title") or "").casefold()))
        current["books"] = books
        atomic_write_json(path, current)
