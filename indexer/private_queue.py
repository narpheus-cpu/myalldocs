from __future__ import annotations

import io
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class QueueBundle:
    manifest_id: str
    manifest: dict[str, Any]
    entries: list[dict[str, Any]]
    state: dict[str, Any]


class PrivateQueueDrive:
    """Read and update only files explicitly shared with the queue app.

    Source books use DriveClient with drive.readonly. This separate client uses
    drive.file and is never handed a source-book ID for mutation.
    """

    def __init__(self, service_account_json: str) -> None:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def download(self, file_id: str) -> bytes:
        from googleapiclient.http import MediaIoBaseDownload

        request = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
        handle = io.BytesIO()
        downloader = MediaIoBaseDownload(handle, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return handle.getvalue()

    def metadata(self, file_id: str, fields: str = "id,name,parents") -> dict[str, Any]:
        return self.service.files().get(fileId=file_id, fields=fields, supportsAllDrives=True).execute()

    def create_json(self, parent_id: str, name: str, value: Any) -> str:
        from googleapiclient.http import MediaIoBaseUpload

        body = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        media = MediaIoBaseUpload(io.BytesIO(body), mimetype="application/json", resumable=False)
        result = self.service.files().create(
            body={"name": name, "parents": [parent_id], "mimeType": "application/json"},
            media_body=media, fields="id", supportsAllDrives=True,
        ).execute()
        return str(result["id"])

    def update_json(self, file_id: str, value: Any) -> None:
        from googleapiclient.http import MediaIoBaseUpload

        body = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
        media = MediaIoBaseUpload(io.BytesIO(body), mimetype="application/json", resumable=False)
        self.service.files().update(fileId=file_id, media_body=media, supportsAllDrives=True).execute()

    def manifest(self, manifest_id: str) -> dict[str, Any]:
        return _object(self.download(manifest_id), "대기열 안내 파일")

    def load_bundle(self, manifest_id: str) -> QueueBundle:
        manifest = self.manifest(manifest_id)
        if manifest.get("schemaVersion") != 1 or manifest.get("kind") not in {"catalog-jsonl", "canonical-json"}:
            raise ValueError("지원하지 않는 비공개 대기열 형식입니다.")
        parts = manifest.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError("대기열에 업로드 조각이 없습니다.")
        payload = b"".join(self.download(str(item["fileId"])) for item in sorted(parts, key=lambda item: int(item.get("index", 0))))
        if manifest["kind"] == "catalog-jsonl":
            entries = parse_jsonl(payload)
        else:
            parsed = json.loads(payload.decode("utf-8-sig"))
            entries = parsed if isinstance(parsed, list) else [parsed]
            if not all(isinstance(item, dict) for item in entries):
                raise ValueError("완성 인덱싱 JSON은 객체 또는 객체 배열이어야 합니다.")
        state_file_id = str(manifest.get("stateFileId") or "")
        state = _object(self.download(state_file_id), "대기열 상태") if state_file_id else {}
        return QueueBundle(manifest_id, manifest, entries, state)

    def save_state(self, bundle: QueueBundle, state: dict[str, Any]) -> None:
        state_file_id = str(bundle.manifest.get("stateFileId") or "")
        if not state_file_id:
            parent = str(bundle.manifest.get("sessionFolderId") or self.metadata(bundle.manifest_id).get("parents", [""])[0])
            if not parent:
                raise ValueError("비공개 대기열 폴더를 찾지 못했습니다.")
            state_file_id = self.create_json(parent, "queue-state.json", state)
            bundle.manifest["stateFileId"] = state_file_id
            self.update_json(bundle.manifest_id, bundle.manifest)
        else:
            self.update_json(state_file_id, state)
        bundle.state = state


def parse_jsonl(payload: bytes) -> list[dict[str, Any]]:
    text = payload.decode("utf-8-sig")
    entries: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSONL {number}번째 줄 문법 오류: {exc.msg}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"JSONL {number}번째 줄은 JSON 객체여야 합니다.")
        filename = str(value.get("filename") or "").strip()
        if not filename:
            raise ValueError(f"JSONL {number}번째 줄에 filename이 없습니다.")
        if not filename.casefold().endswith((".txt", ".epub")) and str(value.get("format") or "").casefold() not in {"txt", "epub"}:
            raise ValueError(f"JSONL {number}번째 줄은 TXT 또는 EPUB가 아닙니다.")
        entries.append(value)
    if not entries:
        raise ValueError("JSONL에 도서 항목이 없습니다.")
    if len(entries) > 50_000:
        raise ValueError("한 번에 등록할 수 있는 항목은 최대 50,000개입니다.")
    return entries


def _object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label}을 읽지 못했습니다.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label}은 JSON 객체여야 합니다.")
    return value
