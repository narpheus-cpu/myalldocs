from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from indexer.checkpoint import atomic_write_json
from indexer.canonical import normalize_canonical, write_search_index


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

    def canonical(self, book_id: str) -> dict | None:
        path = self.data / "books" / book_id / "book.json"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else None

    def completed_manifest_by_sha256(self, checksum: str) -> dict | None:
        books = self.data / "books"
        if not books.exists():
            return None
        for path in books.glob("*/manifest.json"):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    manifest = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("indexStatus") == "COMPLETE" and manifest.get("source", {}).get("sha256") == checksum:
                return manifest
        return None

    def completed_count_for_quota_day(self, now: datetime | None = None) -> int:
        """Count books completed in the current Gemini RPD day (Pacific time)."""
        path = self.data / "catalog.json"
        if not path.exists():
            return 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                books = json.load(handle).get("books", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            return 0
        pacific = ZoneInfo("America/Los_Angeles")
        target_day = (now or datetime.now(timezone.utc)).astimezone(pacific).date()
        count = 0
        for book in books:
            if book.get("indexStatus") != "COMPLETE":
                continue
            value = str(book.get("updatedAt") or book.get("indexedAt") or "").strip()
            if not value:
                continue
            try:
                completed_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if completed_at.tzinfo is None:
                    completed_at = completed_at.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if completed_at.astimezone(pacific).date() == target_day:
                count += 1
        return count

    def save_book(self, book_id: str, files: dict[str, Any]) -> None:
        directory = self.data / "books" / book_id
        for name, value in files.items():
            atomic_write_json(directory / name, value)

    def save_canonical(self, value: dict[str, Any]) -> dict[str, Any]:
        book = normalize_canonical(value)
        book_id = book["system"]["libraryEntryId"]
        if not book_id:
            raise ValueError("canonical book ID is empty")
        atomic_write_json(self.data / "books" / book_id / "book.json", book)
        identity = book["identity"]
        source = book["source"]
        system = book["system"]
        self.update_catalog({
            "bookId": book_id,
            "driveFileId": source["driveFileId"],
            "title": identity["title"],
            "author": identity["author"],
            "documentType": identity["workProfile"]["primary"],
            "genre": identity["genre"],
            "tags": identity["tags"],
            "format": source["format"],
            "indexStatus": system["indexStatus"],
            "metadataStatus": system["identityStatus"],
            "generationSource": system["generation"]["source"],
            "edited": system["edited"],
            "createdAt": system["createdAt"],
            "updatedAt": system["updatedAt"],
            "webViewLink": source["webViewLink"],
            "oneLineSummary": book["content"]["oneLineSummary"],
        })
        write_search_index(self.root)
        return book

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
