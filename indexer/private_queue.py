from __future__ import annotations

import base64
import gzip
import io
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


class CanonicalUploadFormatError(ValueError):
    """A completed-index upload cannot be decoded into book objects."""


def parse_canonical_upload(payload: bytes) -> list[dict[str, Any]]:
    """Accept a canonical object, an object array, or newline-delimited objects.

    Some generators export several completed books as JSONL while still naming
    the file ``.json``.  Treat that common representation as a multi-book upload
    instead of letting ``json.loads`` abort the entire GitHub run.
    """

    text = payload.decode("utf-8-sig").strip()
    if not text:
        raise CanonicalUploadFormatError("완성 인덱싱 JSON 파일이 비어 있습니다.")
    try:
        parsed: Any = json.loads(text)
    except json.JSONDecodeError as whole_error:
        entries: list[dict[str, Any]] = []
        lines = [(number, line.strip()) for number, line in enumerate(text.splitlines(), 1) if line.strip()]
        if len(lines) < 2:
            raise CanonicalUploadFormatError(
                f"완성 인덱싱 JSON 문법 오류: {whole_error.lineno}행 {whole_error.colno}열"
            ) from whole_error
        for number, line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError as line_error:
                raise CanonicalUploadFormatError(
                    f"완성 인덱싱 JSONL {number}번째 줄 문법 오류: {line_error.colno}열"
                ) from line_error
            if not isinstance(value, dict):
                raise CanonicalUploadFormatError(f"완성 인덱싱 JSONL {number}번째 줄은 객체여야 합니다.")
            entries.append(value)
        return entries

    entries = parsed if isinstance(parsed, list) else [parsed]
    if not entries:
        raise CanonicalUploadFormatError("완성 인덱싱 JSON에 도서 객체가 없습니다.")
    if not all(isinstance(item, dict) for item in entries):
        raise CanonicalUploadFormatError("완성 인덱싱 JSON은 객체, 객체 배열 또는 줄별 객체 형식이어야 합니다.")
    return entries


@dataclass
class QueueBundle:
    manifest_id: str
    manifest: dict[str, Any]
    entries: list[dict[str, Any]]
    state: dict[str, Any]


class PrivateQueueDrive:
    """Read only files explicitly shared with the queue worker.

    Apps Script creates the queue files, so drive.file alone cannot always
    discover them. drive.readonly makes explicitly shared queue files visible.
    State writes go back through the authenticated Apps Script owner instead of
    granting the worker broad Drive write access.
    """

    def __init__(self, service_account_json: str, callback_url: str = "", callback_secret: str = "") -> None:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        info = json.loads(service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
        )
        self.service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        self.callback_url = callback_url
        self.callback_secret = callback_secret

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

    def manifest(self, manifest_id: str) -> dict[str, Any]:
        return _object(self.download(manifest_id), "대기열 안내 파일")

    def load_bundle(self, manifest_id: str) -> QueueBundle:
        manifest = self.manifest(manifest_id)
        if manifest.get("schemaVersion") != 1 or manifest.get("kind") not in {"catalog-jsonl", "canonical-json", "content-edit", "source-link"}:
            raise ValueError("지원하지 않는 비공개 대기열 형식입니다.")
        parts = manifest.get("parts")
        if not isinstance(parts, list) or not parts:
            raise ValueError("대기열에 업로드 조각이 없습니다.")
        payload = b"".join(self.download(str(item["fileId"])) for item in sorted(parts, key=lambda item: int(item.get("index", 0))))
        if manifest["kind"] == "catalog-jsonl":
            entries = parse_jsonl(payload)
        elif manifest["kind"] in {"content-edit", "source-link"}:
            label = "인덱싱 내용 편집 파일" if manifest["kind"] == "content-edit" else "원문 연결 수정 파일"
            entries = [_object(payload, label)]
        else:
            entries = parse_canonical_upload(payload)
        state_file_id = str(manifest.get("stateFileId") or "")
        state = _object(self.download(state_file_id), "대기열 상태") if state_file_id else {}
        return QueueBundle(manifest_id, manifest, entries, state)

    def save_state(self, bundle: QueueBundle, state: dict[str, Any]) -> None:
        if not self.callback_url or not self.callback_secret:
            raise RuntimeError("비공개 대기열 상태 저장 연결이 없습니다.")
        compressed = gzip.compress(json.dumps(state, ensure_ascii=False).encode("utf-8"), compresslevel=6)
        body = json.dumps({
            "route": "queue-state",
            "callbackSecret": self.callback_secret,
            "manifestId": bundle.manifest_id,
            "stateGzipBase64": base64.b64encode(compressed).decode("ascii"),
        }, ensure_ascii=False).encode("utf-8")
        value: Any = None
        last_error: Exception | None = None
        max_attempts = 5
        for attempt in range(max_attempts):
            request = urllib.request.Request(
                self.callback_url,
                data=body,
                headers={"Content-Type": "text/plain;charset=utf-8"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    value = json.loads(response.read().decode("utf-8"))
                if isinstance(value, dict) and value.get("ok") is True:
                    bundle.state = state
                    return
                detail = str(value.get("error") or "")[:300] if isinstance(value, dict) else "응답 형식 오류"
                raise RuntimeError(f"Apps Script가 비공개 대기열 상태를 저장하지 못했습니다: {detail}")
            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read(500).decode("utf-8", errors="replace")
                except Exception:
                    detail = ""
                last_error = RuntimeError(f"HTTP {exc.code}{': ' + detail if detail else ''}")
            except (TimeoutError, OSError) as exc:
                last_error = exc
            if attempt + 1 < max_attempts:
                time.sleep(min(16, 2 ** (attempt + 1)))
        raise RuntimeError(f"Apps Script 상태 저장 연결이 {max_attempts}회 실패했습니다: {last_error}")


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
