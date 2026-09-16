from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from indexer.checkpoint import atomic_write_json
from indexer.private_queue import PrivateQueueDrive
from indexer.queue_context import notify_queue_result


ROOT = Path(__file__).resolve().parents[1]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_public_content(value: Any, depth: int = 0) -> None:
    if depth > 30:
        raise ValueError("편집 내용의 구조가 너무 깊습니다.")
    if isinstance(value, dict):
        for key, item in value.items():
            if re.fullmatch(r"(?:source|raw|original|full)[_-]?text", str(key), re.IGNORECASE):
                raise ValueError("원문 전문은 공개 저장소에 저장할 수 없습니다.")
            if re.search(r"(?:api[_-]?key|secret|access[_-]?token|refresh[_-]?token|password)", str(key), re.IGNORECASE):
                raise ValueError("API 키나 인증 정보는 공개 저장소에 저장할 수 없습니다.")
            _validate_public_content(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_public_content(item, depth + 1)


def apply_content_edit(root: Path, bundle: Any) -> dict[str, Any]:
    if bundle.manifest.get("kind") != "content-edit" or len(bundle.entries) != 1:
        raise ValueError("인덱싱 내용 편집 전용 대기열이 아닙니다.")
    entry = bundle.entries[0]
    drive_file_id = str(entry.get("driveFileId") or "").strip()
    content = entry.get("content")
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,200}", drive_file_id):
        raise ValueError("원본 파일 ID가 올바르지 않습니다.")
    if not isinstance(content, dict):
        raise ValueError("저장할 인덱싱 내용이 올바르지 않습니다.")
    _validate_public_content(content)

    path = root / "data" / "content-overrides.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("기존 인덱싱 내용 수정값을 읽지 못했습니다.") from exc
    document.setdefault("schemaVersion", 1)
    document.setdefault("description", "driveFileId별 인덱싱 내용 수동 수정값. 자동 재인덱싱과 별도로 보존됩니다.")
    if not isinstance(document.get("byDriveFileId"), dict):
        raise ValueError("기존 인덱싱 내용 수정값의 구조가 올바르지 않습니다.")
    updated_at = _now()
    document["byDriveFileId"][drive_file_id] = {"content": content, "updatedAt": updated_at}
    atomic_write_json(path, document)
    return {
        "status": "COMPLETE",
        "phase": "COMPLETE",
        "message": "수정한 인덱싱 내용을 GitHub에 반영했습니다.",
        "totalFiles": 1,
        "complete": 1,
        "skipped": 0,
        "failed": 0,
        "metadataReview": 0,
        "allTargetsComplete": True,
        "finishedAt": updated_at,
        "driveFileId": drive_file_id,
    }


def main() -> int:
    manifest_id = str(os.getenv("PRIVATE_QUEUE_MANIFEST_ID") or "").strip()
    service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    callback_url = os.getenv("APPS_SCRIPT_CALLBACK_URL", "")
    callback_secret = os.getenv("APPS_SCRIPT_CALLBACK_SECRET", "")
    if not manifest_id:
        raise RuntimeError("편집 대기열 안내 파일이 없습니다.")
    private_drive = PrivateQueueDrive(service_account_json, callback_url, callback_secret)
    bundle = private_drive.load_bundle(manifest_id)
    summary = apply_content_edit(ROOT, bundle)
    state = {
        "schemaVersion": 1,
        "manifestId": manifest_id,
        "kind": "content-edit",
        "entries": [{"filename": bundle.manifest.get("originalFilename", "content-edit.json"), "status": "COMPLETE", "driveFileId": summary["driveFileId"]}],
        "createdAt": bundle.manifest.get("createdAt") or summary["finishedAt"],
        "updatedAt": summary["finishedAt"],
        "lastRunStatus": "COMPLETE",
    }
    private_drive.save_state(bundle, state)
    notify_queue_result(callback_url, callback_secret, manifest_id, "COMPLETE", summary)
    print("Indexed content edit applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
