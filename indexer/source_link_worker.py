from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from indexer.checkpoint import atomic_write_json
from indexer.config import ROOT
from indexer.private_queue import PrivateQueueDrive
from indexer.queue_context import notify_queue_result
from indexer.storage import RepositoryStorage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _override_document(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"기존 {path.name} 파일을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"기존 {path.name} 구조가 올바르지 않습니다.")
    value.setdefault("schemaVersion", 1)
    value.setdefault("description", description)
    value.setdefault("byDriveFileId", {})
    value.setdefault("byBookId", {})
    if not isinstance(value["byDriveFileId"], dict) or not isinstance(value["byBookId"], dict):
        raise ValueError(f"기존 {path.name} 연결표가 올바르지 않습니다.")
    return value


def _move_overrides(root: Path, book_id: str, old_drive_id: str, new_drive_id: str) -> None:
    descriptions = {
        "metadata-overrides.json": "도서별 수동 메타데이터 수정값. 원문 연결을 바꿔도 보존됩니다.",
        "content-overrides.json": "도서별 인덱싱 내용 수동 수정값. 원문 연결을 바꿔도 보존됩니다.",
    }
    for filename, description in descriptions.items():
        path = root / "data" / filename
        document = _override_document(path, description)
        value = document["byBookId"].get(book_id)
        if value is None and old_drive_id:
            value = document["byDriveFileId"].get(old_drive_id)
        if value is not None:
            document["byBookId"][book_id] = value
            if new_drive_id:
                document["byDriveFileId"][new_drive_id] = value
        if old_drive_id and old_drive_id != new_drive_id:
            document["byDriveFileId"].pop(old_drive_id, None)
        atomic_write_json(path, document)


def apply_source_link(root: Path, bundle: Any) -> dict[str, Any]:
    if bundle.manifest.get("kind") != "source-link" or len(bundle.entries) != 1:
        raise ValueError("원문 연결 수정 전용 대기열이 아닙니다.")
    entry = bundle.entries[0]
    book_id = str(entry.get("bookId") or "").strip()
    mode = str(entry.get("sourceMode") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", book_id):
        raise ValueError("도서 식별번호가 올바르지 않습니다.")
    if mode not in {"drive", "none"}:
        raise ValueError("원문 연결 방식이 올바르지 않습니다.")

    book_path = root / "data" / "books" / book_id / "book.json"
    try:
        book = json.loads(book_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("수정할 도서를 찾지 못했습니다.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("기존 도서 정보를 읽지 못했습니다.") from exc
    if not isinstance(book, dict):
        raise ValueError("기존 도서 정보의 구조가 올바르지 않습니다.")

    old_drive_id = str((book.get("source") or {}).get("driveFileId") or "")
    updated_at = _now()
    source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
    new_drive_id = str(source.get("driveFileId") or "").strip() if mode == "drive" else ""
    if mode == "drive" and not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", new_drive_id):
        raise ValueError("새 원본 파일 ID가 올바르지 않습니다.")

    if mode == "drive":
        filename = str(source.get("filename") or "").strip()
        mime_type = str(source.get("mimeType") or "").strip()
        if not filename:
            raise ValueError("새 원본 파일명이 없습니다.")
        if mime_type not in {"text/plain", "application/epub+zip"} and not filename.casefold().endswith((".txt", ".epub")):
            raise ValueError("TXT 또는 EPUB 원본만 연결할 수 있습니다.")
        book["source"] = {
            **(book.get("source") or {}),
            "provider": "google-drive",
            "driveFileId": new_drive_id,
            "filename": filename,
            "format": "epub" if filename.casefold().endswith(".epub") or mime_type == "application/epub+zip" else "txt",
            "mimeType": mime_type,
            "modifiedTime": str(source.get("modifiedTime") or ""),
            "size": str(source.get("size") or ""),
            "folderPath": source.get("folderPath") if isinstance(source.get("folderPath"), list) else [],
            "relativePath": str(source.get("relativePath") or ""),
            "webViewLink": str(source.get("webViewLink") or ""),
            "sourceSha256": "",
            "textSha256": "",
            "changeKey": "",
        }
        match_status = "MANUAL_MATCHED"
        connection_status = "manual"
    else:
        book["source"] = {
            **(book.get("source") or {}),
            "provider": "none", "driveFileId": "", "filename": "", "format": "", "mimeType": "",
            "modifiedTime": "", "size": "", "folderPath": [], "relativePath": "", "webViewLink": "",
            "sourceSha256": "", "textSha256": "", "changeKey": "",
        }
        match_status = "NO_SOURCE"
        connection_status = "none"

    system = book.setdefault("system", {})
    system["libraryEntryId"] = book_id
    system["driveFileId"] = new_drive_id
    system["driveMatchStatus"] = match_status
    system["updatedAt"] = updated_at
    system["edited"] = True
    system["sourceConnection"] = {
        "status": connection_status,
        "reviewRecommended": False,
        "matchBasis": "user_selection" if mode == "drive" else "user_no_source",
        "matchScore": 1.0 if mode == "drive" else 0.0,
        "updatedAt": updated_at,
    }

    _move_overrides(root, book_id, old_drive_id, new_drive_id)
    saved = RepositoryStorage(root).save_canonical(book)
    return {
        "status": "COMPLETE", "phase": "COMPLETE",
        "message": "선택한 Google Drive 원문으로 연결을 수정했습니다." if mode == "drive" else "이 도서를 원문 없음으로 저장했습니다.",
        "totalFiles": 1, "complete": 1, "skipped": 0, "failed": 0, "metadataReview": 0,
        "allTargetsComplete": True, "finishedAt": updated_at,
        "bookId": book_id, "driveFileId": new_drive_id,
        "sourceConnectionStatus": saved["system"]["sourceConnection"]["status"],
    }


def main() -> int:
    manifest_id = str(os.getenv("PRIVATE_QUEUE_MANIFEST_ID") or "").strip()
    if not manifest_id:
        raise RuntimeError("원문 연결 수정 대기열 안내 파일이 없습니다.")
    private_drive = PrivateQueueDrive(
        os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", ""),
        os.getenv("APPS_SCRIPT_CALLBACK_URL", ""),
        os.getenv("APPS_SCRIPT_CALLBACK_SECRET", ""),
    )
    bundle = private_drive.load_bundle(manifest_id)
    summary = apply_source_link(ROOT, bundle)
    state = {
        "schemaVersion": 1, "manifestId": manifest_id, "kind": "source-link",
        "entries": [{
            "filename": bundle.manifest.get("originalFilename", "source-link.json"),
            "status": "COMPLETE", "driveFileId": summary["driveFileId"], "bookId": summary["bookId"],
        }],
        "createdAt": bundle.manifest.get("createdAt") or summary["finishedAt"],
        "updatedAt": summary["finishedAt"], "lastRunStatus": "COMPLETE",
    }
    private_drive.save_state(bundle, state)
    notify_queue_result(
        os.getenv("APPS_SCRIPT_CALLBACK_URL", ""),
        os.getenv("APPS_SCRIPT_CALLBACK_SECRET", ""), manifest_id, "COMPLETE", summary,
    )
    atomic_write_json(ROOT / "data" / "job-status.json", summary)
    print("Source link updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
