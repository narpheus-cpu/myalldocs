from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)


class CheckpointStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def path(self, book_id: str) -> Path:
        return self.directory / f"{book_id}.json"

    def load(self, book_id: str) -> dict:
        path = self.path(book_id)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def save(self, book_id: str, value: dict) -> None:
        atomic_write_json(self.path(book_id), value)

    def clear(self, book_id: str) -> None:
        path = self.path(book_id)
        if path.exists():
            path.unlink()
