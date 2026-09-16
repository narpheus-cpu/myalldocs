from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from indexer.canonical import normalize_canonical, stable_book_id, text_sha256
from indexer.checkpoint import atomic_write_json
from indexer.config import Settings
from indexer.drive_client import DriveClient, DriveBook
from indexer.drive_quota import DriveQuotaPaused
from indexer.gemini_client import GeminiClient
from indexer.model_selector import NoSupportedModel
from indexer.parsers import parse_epub, parse_txt
from indexer.prior_knowledge import prior_knowledge_prompt, valid_prior_result
from indexer.private_queue import CanonicalUploadFormatError, PrivateQueueDrive, QueueBundle
from indexer.progress import ProgressReporter
from indexer.queue_context import notify_queue_result
from indexer.rate_limiter import BudgetExceeded, RateLimitPaused, RateLimiter, ServiceUnavailablePaused
from indexer.storage import RepositoryStorage


LOG = logging.getLogger("book-indexer.queue")


class NoGeminiClient:
    """Status-compatible client for completed JSON imports.

    It deliberately never constructs the GenAI SDK client, so a completed JSON
    upload makes no model-list or generation request.
    """

    def __init__(self, quota: dict[str, Any]) -> None:
        self.rate_limiter = RateLimiter(quota)
        self.model_name = ""
        self.attempted_models: list[str] = []
        self.model_switch_count = 0
        self.model_cycle = 0
        self.max_model_cycles = 0
        self.model_cycle_restarts = 0
        self.last_model_error = ""
        self.invalid_json_responses = 0

    def set_event_callback(self, callback: Any) -> None:
        return None

    def generate_json(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("완성 인덱싱 JSON 경로에서는 Gemini를 호출할 수 없습니다.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(value: Any) -> str:
    return re.sub(r"[\s\\/]+", "/", str(value or "").strip()).strip("/").casefold()


def _relative_folder(path: list[str], root_name: str) -> str:
    parts = [str(item).strip() for item in path if str(item).strip()]
    if parts and _norm(parts[0]) == _norm(root_name):
        parts = parts[1:]
    return "/".join(parts)


def match_entries(entries: list[dict[str, Any]], books: list[DriveBook], root_name: str) -> list[dict[str, Any]]:
    by_filename: dict[str, list[DriveBook]] = {}
    by_path: dict[str, list[DriveBook]] = {}
    for book in books:
        by_filename.setdefault(_norm(book.name), []).append(book)
        key = _norm("/".join(filter(None, [_relative_folder(book.folderPath, root_name), book.name])))
        by_path.setdefault(key, []).append(book)
    matched: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        entry_source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
        filename = str(entry.get("filename") or entry_source.get("filename") or "").strip()
        relative = str(entry.get("relative_path") or entry.get("relativePath") or entry_source.get("relativePath") or "").strip()
        relative = relative[:-len(filename)].rstrip("/\\") if filename and _norm(relative).endswith(_norm(filename)) else relative
        exact = by_path.get(_norm("/".join(filter(None, [relative, filename]))), []) if relative else []
        candidates = exact or by_filename.get(_norm(filename), [])
        status = "MATCHED" if len(candidates) == 1 else ("NOT_FOUND" if not candidates else "AMBIGUOUS")
        selected = candidates[0] if len(candidates) == 1 else None
        matched.append({
            "queueIndex": index,
            "filename": filename,
            "relativePath": relative,
            "status": status,
            "driveFileId": selected.id if selected else "",
            "candidateCount": len(candidates),
            "folderPath": selected.folderPath if selected else [],
        })
    return matched


def _catalog_by_drive(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "data" / "catalog.json"
    if not path.exists():
        return {}
    try:
        values = json.loads(path.read_text(encoding="utf-8")).get("books", [])
    except (OSError, json.JSONDecodeError, AttributeError):
        return {}
    return {str(item.get("driveFileId")): item for item in values if item.get("driveFileId")}


def reconcile_completed_entries(entries: list[dict[str, Any]], catalog: dict[str, dict[str, Any]], root: Path) -> int:
    """Requeue items whose private checkpoint says COMPLETE but whose public result is absent.

    A runner can disappear after saving the private checkpoint but before its Git
    commit reaches the repository.  Treat the repository book plus catalog row as
    the durable completion boundary so the next run repairs that split state.
    """
    repaired = 0
    for item in entries:
        if item.get("status") != "COMPLETE":
            continue
        book_id = str(item.get("bookId") or "")
        drive_file_id = str(item.get("driveFileId") or "")
        catalog_entry = catalog.get(drive_file_id, {})
        book_exists = bool(re.fullmatch(r"[0-9a-f]{20}", book_id)) and (root / "data" / "books" / book_id / "book.json").is_file()
        catalog_matches = str(catalog_entry.get("bookId") or "") == book_id
        if book_exists and catalog_matches:
            continue
        item["status"] = "MATCHED"
        item["reason"] = "이전 실행 결과가 GitHub에 반영되지 않아 자동으로 다시 처리합니다."
        item.pop("bookId", None)
        repaired += 1
    return repaired


class QueueWorker:
    def __init__(self, settings: Settings, source_drive: DriveClient, private_drive: PrivateQueueDrive, gemini: GeminiClient, progress: ProgressReporter) -> None:
        self.settings = settings
        self.source_drive = source_drive
        self.private_drive = private_drive
        self.gemini = gemini
        self.progress = progress
        self.storage = RepositoryStorage(settings.root)
        self.status: dict[str, Any] = {}
        self.gemini_event: dict[str, Any] = {}
        self.gemini.set_event_callback(self._on_gemini_event)

    def _on_gemini_event(self, event: dict[str, Any]) -> None:
        self.gemini_event = event
        phase = {
            "RETRY": "GEMINI_RETRY", "PACING": "GEMINI_PACING", "PAUSED": "PAUSED_RATE_LIMIT",
            "MODEL_FALLBACK": "MODEL_FALLBACK", "MODEL_COOLDOWN": "MODEL_COOLDOWN",
            "MODEL_CYCLE_RESTART": "MODEL_CYCLE_RESTART", "INVALID_JSON_RETRY": "INVALID_JSON_RETRY",
        }.get(str(event.get("event")), str(event.get("event") or "INDEXING"))
        self._emit(phase, str(event.get("message") or "Gemini 호출 상태가 변경되었습니다."), True)

    def _emit(self, phase: str, message: str, force: bool = False) -> None:
        usage = self.gemini.rate_limiter.usage
        self.status.update({
            "phase": phase, "message": message, "model": self.gemini.model_name,
            "apiRequests": usage.attempts, "apiSuccessfulRequests": usage.requests,
            "apiRequestAttempts": usage.attempts, "apiFailedAttempts": usage.failed_attempts,
            "inputTokens": usage.input_tokens, "outputTokens": usage.output_tokens,
            "attemptedModels": self.gemini.attempted_models, "modelSwitchCount": self.gemini.model_switch_count,
            "modelCycle": self.gemini.model_cycle, "maxModelCycles": self.gemini.max_model_cycles,
            "modelCycleRestarts": self.gemini.model_cycle_restarts, "lastModelError": self.gemini.last_model_error,
            "invalidJsonResponses": self.gemini.invalid_json_responses,
            "driveQuotaUnits": self.source_drive.quota.usage.quota_units,
            "driveDownloadedBytes": self.source_drive.quota.usage.downloaded_bytes,
            "updatedAt": _now(),
        })
        self.status.update({key: value for key, value in self.gemini_event.items() if key not in {"event", "message"}})
        atomic_write_json(self.settings.root / "data" / "job-status.json", self.status)
        self.progress.emit(self.status, force=force)

    def run(self, manifest_id: str) -> dict[str, Any]:
        bundle = self.private_drive.load_bundle(manifest_id)
        root_id = str(bundle.manifest.get("sourceRootFolderId") or os.getenv("DRIVE_ROOT_FOLDER_ID") or "")
        if not root_id:
            raise ValueError("Drive 원본 루트 설정이 없습니다.")
        self.source_drive.assert_descendant(root_id, os.getenv("DRIVE_ROOT_FOLDER_ID") or root_id)
        root_meta = self.source_drive.folder_metadata(root_id)
        state = bundle.state if isinstance(bundle.state, dict) else {}
        if not isinstance(state.get("entries"), list) or len(state["entries"]) != len(bundle.entries):
            self.status = {"status": "RUNNING", "phase": "MATCHING", "message": "Drive 원본 목록을 한 번만 확인하고 있습니다.", "totalFiles": len(bundle.entries), "startedAt": _now()}
            self._emit("MATCHING", self.status["message"], True)
            drive_books = list(self.source_drive.iter_books(root_id, True))
            state = {"schemaVersion": 1, "manifestId": manifest_id, "kind": bundle.manifest["kind"], "entries": match_entries(bundle.entries, drive_books, str(root_meta.get("name") or "")), "createdAt": _now(), "updatedAt": _now()}
            self.private_drive.save_state(bundle, state)
        else:
            drive_books = list(self.source_drive.iter_books(root_id, True))

        queue_entries = state["entries"]
        catalog = _catalog_by_drive(self.settings.root)
        repaired = reconcile_completed_entries(queue_entries, catalog, self.settings.root)
        if repaired:
            state.update({"entries": queue_entries, "updatedAt": _now(), "recoveredMissingPublicResults": repaired})
            self.private_drive.save_state(bundle, state)
        complete_before = sum(1 for item in queue_entries if item.get("status") in {"COMPLETE", "SKIPPED"})
        counts = {
            "complete": sum(1 for item in queue_entries if item.get("status") == "COMPLETE"),
            "skipped": sum(1 for item in queue_entries if item.get("status") == "SKIPPED"),
            "failed": sum(1 for item in queue_entries if item.get("status") == "ERROR"),
            "metadataReview": sum(1 for item in queue_entries if item.get("status") == "NEEDS_METADATA_REVIEW"),
        }
        self.status = {"status": "RUNNING", "phase": "QUEUE_READY", "message": "비공개 대기열을 이어서 처리합니다.", "totalFiles": len(queue_entries), "startedAt": state.get("createdAt") or _now(), **counts}
        self._emit("QUEUE_READY", self.status["message"], True)
        max_books = int(self.settings.raw.get("queue", {}).get("maxBooksPerRun", 20) or 20)
        processed = 0
        pause = ""
        books_by_id = {book.id: book for book in drive_books}

        for index, item in enumerate(queue_entries):
            if item.get("status") in {"COMPLETE", "SKIPPED", "NOT_FOUND", "AMBIGUOUS", "NEEDS_METADATA_REVIEW"}:
                continue
            if item.get("status") not in {"MATCHED", "IDENTIFYING", "INDEXING"}:
                continue
            if processed >= max_books:
                pause = "PAUSED_SAFETY_BUDGET"
                break
            file_id = str(item.get("driveFileId") or "")
            source = books_by_id.get(file_id)
            if not source:
                item["status"] = "NOT_FOUND"
                continue
            self.status.update({"currentFileName": source.name, "currentFileIndex": index + 1})
            self._emit("STARTING_BOOK", "대기열의 다음 도서를 확인합니다.", True)
            if file_id in catalog:
                item["status"] = "SKIPPED"
                item["reason"] = "동일한 Drive 원본이 이미 등록되어 있습니다."
                counts["skipped"] += 1
                continue
            try:
                raw = self.source_drive.download(file_id, int(source.size) if source.size else None)
                source_hash = hashlib.sha256(raw).hexdigest()
                parsed = parse_epub(raw) if source.mimeType == "application/epub+zip" or source.name.casefold().endswith(".epub") else parse_txt(raw)
                normalized_text_hash = text_sha256(parsed.text)
                if self._duplicate_hash(source_hash, normalized_text_hash):
                    item.update({"status": "SKIPPED", "sourceSha256": source_hash, "textSha256": normalized_text_hash, "reason": "동일한 원문 내용이 이미 등록되어 있습니다."})
                    counts["skipped"] += 1
                    continue
                if bundle.manifest["kind"] == "canonical-json":
                    item["status"] = "INDEXING"
                    self.private_drive.save_state(bundle, {**state, "entries": queue_entries, "updatedAt": _now()})
                    canonical = self._completed_upload(bundle.entries[index], source, source_hash, normalized_text_hash)
                else:
                    item["status"] = "IDENTIFYING"
                    self.private_drive.save_state(bundle, {**state, "entries": queue_entries, "updatedAt": _now()})
                    self._emit("IDENTIFYING", "발췌문으로 작품을 식별하고 사전지식 인덱스를 생성합니다.", True)
                    result = self.gemini.generate_json(prior_knowledge_prompt(bundle.entries[index]), expected_type=dict)
                    valid, reason = valid_prior_result(result, float(self.settings.raw.get("queue", {}).get("identityThreshold", 0.82)))
                    if not valid:
                        item.update({"status": "NEEDS_METADATA_REVIEW", "reason": reason, "identityDecision": _public_identity(result)})
                        counts["metadataReview"] += 1
                        processed += 1
                        self.private_drive.save_state(bundle, {**state, "entries": queue_entries, "updatedAt": _now()})
                        continue
                    item["status"] = "INDEXING"
                    canonical = self._generated(result, source, source_hash, normalized_text_hash)
                self.storage.save_canonical(canonical)
                item.update({"status": "COMPLETE", "bookId": canonical["system"]["libraryEntryId"], "sourceSha256": source_hash, "textSha256": normalized_text_hash})
                counts["complete"] += 1
                catalog[file_id] = {"bookId": canonical["system"]["libraryEntryId"]}
                processed += 1
            except ServiceUnavailablePaused as exc:
                pause = "PAUSED_SERVICE_UNAVAILABLE"; item["reason"] = str(exc); break
            except (RateLimitPaused, BudgetExceeded, DriveQuotaPaused) as exc:
                pause = "PAUSED_RATE_LIMIT"; item["reason"] = str(exc); break
            except NoSupportedModel as exc:
                pause = "NO_SUPPORTED_MODEL"; item["reason"] = str(exc); break
            except Exception as exc:
                LOG.exception("Queue item failed: %s", source.name)
                item.update({"status": "ERROR", "reason": str(exc)[:500]})
                counts["failed"] += 1
                processed += 1
            finally:
                state.update({"entries": queue_entries, "updatedAt": _now()})
                self.private_drive.save_state(bundle, state)
                self.status.update(counts)
                self._emit("BOOK_FINISHED", str(item.get("reason") or "도서 처리가 끝났습니다."), True)

        unresolved = [item for item in queue_entries if item.get("status") not in {"COMPLETE", "SKIPPED"}]
        terminal_review = unresolved and all(item.get("status") in {"NOT_FOUND", "AMBIGUOUS", "NEEDS_METADATA_REVIEW", "ERROR"} for item in unresolved)
        final = pause or ("COMPLETE" if not unresolved else ("NEEDS_USER_REVIEW" if terminal_review else "PAUSED_SAFETY_BUDGET"))
        self.status.update(counts)
        self.status.update({"status": final, "phase": final, "allTargetsComplete": not unresolved, "finishedAt": _now(), "message": _final_message(final, len(unresolved), complete_before, counts)})
        state.update({"entries": queue_entries, "updatedAt": _now(), "lastRunStatus": final})
        self.private_drive.save_state(bundle, state)
        self._emit(final, self.status["message"], True)
        return self.status

    def _duplicate_hash(self, source_hash: str, normalized_text_hash: str) -> bool:
        for path in (self.settings.root / "data" / "books").glob("*/book.json"):
            try:
                source = json.loads(path.read_text(encoding="utf-8")).get("source", {})
            except (OSError, json.JSONDecodeError):
                continue
            if source.get("sourceSha256") == source_hash or source.get("textSha256") == normalized_text_hash:
                return True
        return False

    def _generated(self, result: dict[str, Any], source: DriveBook, source_hash: str, normalized_text_hash: str) -> dict[str, Any]:
        decision = result["identityDecision"]
        book_id = stable_book_id(source.id)
        return normalize_canonical({
            "identity": result["identity"], "content": result["content"],
            "source": _source_record(source, source_hash, normalized_text_hash),
            "system": {
                "libraryEntryId": book_id, "workId": book_id, "driveFileId": source.id, "driveMatchStatus": "MATCHED",
                "identityConfidence": decision.get("confidence", 0), "identityStatus": decision.get("identityStatus", "INFERRED"),
                "indexStatus": "COMPLETE", "promptVersion": 2, "createdAt": _now(), "updatedAt": _now(),
                "generation": {"source": "model_prior_knowledge", "model": self.gemini.model_name}, "edited": False,
            },
        })

    def _completed_upload(self, raw: dict[str, Any], source: DriveBook, source_hash: str, normalized_text_hash: str) -> dict[str, Any]:
        _validate_completed_payload(raw)
        value = json.loads(json.dumps(raw, ensure_ascii=False))
        source_value = value.get("source") if isinstance(value.get("source"), dict) else {}
        source_value.update(_source_record(source, source_hash, normalized_text_hash))
        value["source"] = source_value
        system_value = value.get("system") if isinstance(value.get("system"), dict) else {}
        system_value.update({
            "libraryEntryId": stable_book_id(source.id), "workId": system_value.get("workId") or stable_book_id(source.id),
            "driveFileId": source.id, "driveMatchStatus": "MATCHED", "indexStatus": "COMPLETE",
            "createdAt": system_value.get("createdAt") or _now(), "updatedAt": _now(),
            "generation": {"source": "user_authored", "model": ""}, "edited": True,
        })
        value["system"] = system_value
        return normalize_canonical(value)


def _source_record(source: DriveBook, source_hash: str, normalized_text_hash: str) -> dict[str, Any]:
    return {
        "provider": "google-drive", "driveFileId": source.id, "filename": source.name,
        "format": "epub" if source.mimeType == "application/epub+zip" or source.name.casefold().endswith(".epub") else "txt",
        "mimeType": source.mimeType, "modifiedTime": source.modifiedTime or "", "size": source.size or "",
        "folderPath": source.folderPath, "relativePath": "/".join(source.folderPath), "webViewLink": source.webViewLink or "",
        "sourceSha256": source_hash, "textSha256": normalized_text_hash,
        "changeKey": source.md5Checksum or source_hash, "chunking": {"targetCharacters": 5000, "overlapCharacters": 0},
    }


def _validate_completed_payload(raw: dict[str, Any]) -> None:
    if not isinstance(raw, dict):
        raise ValueError("완성 인덱싱 JSON은 객체여야 합니다.")
    identity = raw.get("identity")
    content = raw.get("content")
    if not isinstance(identity, dict) or not str(identity.get("title") or "").strip() or not str(identity.get("author") or "").strip():
        raise ValueError("완성 인덱싱 JSON의 identity.title과 identity.author가 필요합니다.")
    if not isinstance(content, dict):
        raise ValueError("완성 인덱싱 JSON의 content 객체가 필요합니다.")
    for name in ("oneLineSummary", "overallSummary"):
        if not str(content.get(name) or "").strip():
            raise ValueError(f"완성 인덱싱 JSON의 content.{name}이 필요합니다.")
    for forbidden in ("excerpt_start", "excerpt_middle", "excerpt_late", "sourceText", "source_text"):
        if forbidden in raw or forbidden in content:
            raise ValueError("완성 인덱싱 JSON에 원문 또는 식별 발췌문을 포함할 수 없습니다.")


def _public_identity(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict) or not isinstance(result.get("identityDecision"), dict):
        return {}
    value = result["identityDecision"]
    return {key: value.get(key) for key in ("title", "author", "confidence", "identityStatus", "knowledgeSufficient", "reason")}


def _final_message(status: str, unresolved: int, complete_before: int, counts: dict[str, int]) -> str:
    if status == "COMPLETE":
        return "비공개 대기열의 모든 도서가 처리되었습니다."
    if status == "NEEDS_USER_REVIEW":
        return f"자동으로 확정할 수 없는 {unresolved}개 항목이 있어 사용자 확인을 기다립니다."
    if status == "PAUSED_SAFETY_BUDGET":
        return "이번 실행의 무료 안전 예산만큼 처리했습니다. 남은 대기열은 예약 실행에서 자동으로 이어집니다."
    if status == "PAUSED_RATE_LIMIT":
        return "Gemini 무료 한도 때문에 안전하게 멈췄습니다. 대기열은 다음 예약 실행에서 자동으로 이어집니다."
    if status == "PAUSED_SERVICE_UNAVAILABLE":
        return "Gemini 서비스가 일시적으로 응답하지 않아 멈췄습니다. 다음 예약 실행에서 자동으로 이어집니다."
    return status


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    manifest_id = str(os.getenv("PRIVATE_QUEUE_MANIFEST_ID") or "")
    if not manifest_id:
        print("Private queue is empty; exiting without API calls.")
        return 0
    settings = Settings.load()
    settings.assert_zero_cost()
    secret_json = settings.secret("GOOGLE_SERVICE_ACCOUNT_JSON") or ""
    progress = ProgressReporter.from_env()
    callback_url = os.getenv("APPS_SCRIPT_CALLBACK_URL", "")
    callback_secret = os.getenv("APPS_SCRIPT_CALLBACK_SECRET", "")
    try:
        drive = DriveClient(secret_json, settings.raw.get("driveQuota", {}))
        private_drive = PrivateQueueDrive(secret_json, callback_url, callback_secret)
        bundle_kind = private_drive.manifest(manifest_id).get("kind")
        gemini = (
            GeminiClient(settings.secret("GEMINI_API_KEY") or "", settings.model_policy, RateLimiter(settings.quota))
            if bundle_kind == "catalog-jsonl"
            else NoGeminiClient(settings.quota)
        )
        status = QueueWorker(settings, drive, private_drive, gemini, progress).run(manifest_id)
    except CanonicalUploadFormatError as exc:
        status = {
            "status": "NEEDS_USER_REVIEW", "phase": "UPLOAD_FORMAT_ERROR",
            "message": str(exc), "allTargetsComplete": False, "finishedAt": _now(),
        }
        atomic_write_json(settings.root / "data" / "job-status.json", status)
        progress.emit(status, force=True)
    except NoSupportedModel as exc:
        status = {"status": "NO_SUPPORTED_MODEL", "phase": "NO_SUPPORTED_MODEL", "message": str(exc), "allTargetsComplete": False, "finishedAt": _now()}
        atomic_write_json(settings.root / "data" / "job-status.json", status)
        progress.emit(status, force=True)
    try:
        notify_queue_result(callback_url, callback_secret, manifest_id, status["status"], {
            **{key: status.get(key, 0) for key in ("complete", "skipped", "failed", "metadataReview", "totalFiles")},
            "allTargetsComplete": status.get("allTargetsComplete") is True,
        })
    except Exception as exc:
        LOG.warning("Queue result callback failed: %s", exc)
    return 0 if status["status"] in {"COMPLETE", "PAUSED_RATE_LIMIT", "PAUSED_SERVICE_UNAVAILABLE", "PAUSED_SAFETY_BUDGET", "NEEDS_USER_REVIEW"} else 1


if __name__ == "__main__":
    sys.exit(main())
