from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from indexer.checkpoint import CheckpointStore, atomic_write_json
from indexer.chunker import chunk_text
from indexer.config import Settings
from indexer.drive_client import DriveClient
from indexer.gemini_client import GeminiClient
from indexer.metadata import collect_local_evidence, needs_web_verification, resolve_metadata
from indexer.model_selector import NoSupportedModel
from indexer.models import DriveBook, Evidence
from indexer.parsers import parse_epub, parse_txt
from indexer.profiles import choose_profile
from indexer.prompts import analyze_chunk, classify_document, resolve_local_metadata, synthesize
from indexer.rate_limiter import BudgetExceeded, RateLimitPaused, RateLimiter
from indexer.storage import RepositoryStorage


LOG = logging.getLogger("book-indexer")


class IndexPipeline:
    def __init__(self, settings: Settings, drive: DriveClient, gemini: GeminiClient, started_at: float | None = None) -> None:
        self.settings = settings
        self.drive = drive
        self.gemini = gemini
        self.storage = RepositoryStorage(settings.root)
        self.checkpoints = CheckpointStore(settings.root / "data" / "checkpoints")
        self.started_at = started_at or time.time()
        self.chunks_this_run = 0

    def run(self, folder_id: str, recursive: bool = True, force: bool = False, profile_override: str | None = None) -> dict:
        configured_root = os.getenv("DRIVE_ROOT_FOLDER_ID")
        if configured_root:
            self.drive.assert_descendant(folder_id, configured_root)
        folder = self.drive.folder_metadata(folder_id)
        books = list(self.drive.iter_books(folder_id, recursive))
        max_books = int(self.settings.quota.get("maxBooksPerRun", 0) or 0)
        status = self._job("RUNNING", folder, len(books))
        if not books:
            status.update({"status": "ERROR", "allTargetsComplete": False, "message": "선택 폴더에 대상 TXT/EPUB가 없습니다.", "finishedAt": _now()})
            self._save_job(status)
            return status
        counts = {"complete": 0, "skipped": 0, "failed": 0, "metadataReview": 0, "processedChunks": 0}
        errors: list[dict] = []
        paused = False
        for index, book in enumerate(books):
            if max_books and counts["complete"] >= max_books:
                paused = True
                status["message"] = "사용자 설정 maxBooksPerRun에 도달하여 안전하게 일시정지했습니다."
                break
            try:
                outcome = self._process_book(book, force, profile_override)
                counts[outcome] += 1
                if outcome == "complete":
                    manifest = self.storage.manifest(_book_id(book.id)) or {}
                    if manifest.get("metadata", {}).get("metadataStatus") == "NEEDS_METADATA_REVIEW":
                        counts["metadataReview"] += 1
            except (RateLimitPaused, BudgetExceeded) as exc:
                LOG.warning("Paused safely: %s", exc)
                paused = True
                status["message"] = str(exc)
                break
            except NoSupportedModel as exc:
                status.update(counts)
                status.update({"status": "NO_SUPPORTED_MODEL", "message": str(exc), "allTargetsComplete": False, "finishedAt": _now()})
                self._save_job(status)
                return status
            except Exception as exc:  # one corrupt book must not destroy the run
                LOG.exception("Failed to index %s", book.name)
                counts["failed"] += 1
                errors.append({"driveFileId": book.id, "filename": book.name, "error": str(exc)[:500]})
            counts["processedChunks"] = self.chunks_this_run
            status.update(counts)
            status["currentFileIndex"] = index + 1
            self._save_job(status)

        counts["processedChunks"] = self.chunks_this_run
        all_complete = not paused and counts["failed"] == 0 and counts["complete"] + counts["skipped"] == len(books)
        final_status = "COMPLETE" if all_complete else ("PAUSED_RATE_LIMIT" if paused else "ERROR")
        status.update(counts)
        status.update({
            "status": final_status,
            "allTargetsComplete": all_complete,
            "errors": errors,
            "model": self.gemini.model_name,
            "apiRequests": self.gemini.rate_limiter.usage.requests,
            "inputTokens": self.gemini.rate_limiter.usage.input_tokens,
            "outputTokens": self.gemini.rate_limiter.usage.output_tokens,
            "finishedAt": _now(),
        })
        self._save_job(status)
        return status

    def _process_book(self, book: DriveBook, force: bool, profile_override: str | None) -> str:
        book_id = _book_id(book.id)
        versions = self.settings.raw["versions"]
        existing = self.storage.manifest(book_id)
        source_hint = book.md5Checksum or f"{book.modifiedTime}:{book.size}"
        if not force and existing and existing.get("source", {}).get("changeKey") == source_hint and existing.get("versions") == versions and existing.get("indexStatus") == "COMPLETE":
            LOG.info("SKIP %s (unchanged)", book.name)
            return "skipped"

        LOG.info("DOWNLOAD %s", book.name)
        raw = self.drive.download(book.id)
        checksum = hashlib.sha256(raw).hexdigest()
        change_key = book.md5Checksum or checksum
        if not force and existing and existing.get("source", {}).get("changeKey") == change_key and existing.get("versions") == versions and existing.get("indexStatus") == "COMPLETE":
            return "skipped"
        parsed = parse_epub(raw) if book.name.casefold().endswith(".epub") else parse_txt(raw)
        if not parsed.text.strip():
            raise ValueError("parsed book is empty")

        overrides = self.storage.overrides().get(book.id, {})
        evidence = collect_local_evidence(parsed, book.name, book.folderPath)
        threshold = float(self.settings.metadata.get("confirmationThreshold", 0.75))
        metadata = resolve_metadata(evidence, threshold, overrides)
        if not metadata.manualOverrideApplied and (metadata.metadataStatus == "NEEDS_METADATA_REVIEW" or metadata.conflictDetected):
            ai_choice, _ = self.gemini.generate_json(resolve_local_metadata([item.to_dict() for item in evidence]))
            if isinstance(ai_choice, dict):
                title = _validated_candidate(ai_choice.get("title"), evidence, "title")
                author = _validated_candidate(ai_choice.get("author"), evidence, "author")
                if title or author:
                    evidence.append(Evidence(
                        "ai_local_resolution", title, author, 0.16,
                        str(ai_choice.get("rationale", ""))[:500], external=False,
                    ))
                    metadata = resolve_metadata(evidence, threshold, overrides)
        web_enabled = bool(self.settings.metadata.get("webVerificationEnabled", False))
        web_threshold = float(self.settings.metadata.get("webVerificationThreshold", 0.75))
        if needs_web_verification(metadata, web_enabled, web_threshold):
            candidates = self.gemini.verify_metadata_on_web(metadata.title, metadata.author, parsed.text)
            for candidate in candidates[:3]:
                sources = candidate.get("groundingSources") or []
                evidence.append(Evidence(
                    "web_verification",
                    candidate.get("title"),
                    candidate.get("author"),
                    min(0.10, max(0.0, float(candidate.get("confidence", 0))) * 0.10),
                    candidate.get("rationale"),
                    sources[0].get("url") if sources else None,
                    True,
                ))
            metadata = resolve_metadata(evidence, threshold, overrides, web_performed=True)

        chunks = chunk_text(
            parsed.text,
            int(self.settings.raw["chunking"]["targetCharacters"]),
            int(self.settings.raw["chunking"]["overlapCharacters"]),
        )
        if not chunks:
            raise ValueError("no chunks produced")
        checkpoint = self.checkpoints.load(book_id)
        checkpoint_valid = checkpoint.get("sourceChecksum") == checksum and checkpoint.get("versions") == versions
        analyses: list[dict] = checkpoint.get("chunkAnalyses", []) if checkpoint_valid else []
        completed_ids = {int(item["chunkId"]) for item in analyses if "chunkId" in item}

        excerpts = [{"chunkId": item["chunkId"], "text": item["text"][:2500]} for item in (chunks[:1] + chunks[len(chunks)//2:len(chunks)//2+1] + chunks[-1:])]
        classification = checkpoint.get("classification") if checkpoint_valid else None
        if not classification:
            classification, _ = self.gemini.generate_json(classify_document(excerpts))
        manual_profile = profile_override or overrides.get("documentType")
        profile_name, profile = choose_profile(classification, self.settings.profiles, manual_profile)

        interval = int(self.settings.raw.get("checkpointIntervalChunks", 5))
        max_chunks = int(self.settings.quota.get("maxChunksPerRun", 0) or 0)
        for chunk in chunks:
            if chunk["chunkId"] in completed_ids:
                continue
            if max_chunks and self.chunks_this_run >= max_chunks:
                self._save_checkpoint(book_id, checksum, versions, classification, analyses, len(chunks))
                raise BudgetExceeded("maxChunksPerRun reached")
            self._check_runtime()
            try:
                analysis, _ = self.gemini.generate_json(analyze_chunk(chunk, profile))
            except (RateLimitPaused, NoSupportedModel):
                self._save_checkpoint(book_id, checksum, versions, classification, analyses, len(chunks))
                raise
            if not isinstance(analysis, dict):
                raise ValueError("Gemini chunk analysis was not an object")
            analysis["chunkId"] = chunk["chunkId"]
            analyses.append(analysis)
            self.chunks_this_run += 1
            if len(analyses) % interval == 0:
                self._save_checkpoint(book_id, checksum, versions, classification, analyses, len(chunks))

        analyses.sort(key=lambda item: int(item.get("chunkId", 0)))
        self._save_checkpoint(book_id, checksum, versions, classification, analyses, len(chunks))
        try:
            level = analyses
            while len(level) > 20:
                next_level: list[dict] = []
                for offset in range(0, len(level), 20):
                    item, _ = self.gemini.generate_json(synthesize(level[offset:offset + 20], profile, False))
                    next_level.append(item)
                level = next_level
            final_analysis, _ = self.gemini.generate_json(synthesize(level, profile, True))
        except (RateLimitPaused, NoSupportedModel):
            self._save_checkpoint(book_id, checksum, versions, classification, analyses, len(chunks))
            raise
        if not isinstance(final_analysis, dict):
            raise ValueError("Gemini synthesis was not an object")

        manifest = {
            "bookId": book_id,
            "driveFileId": book.id,
            "title": metadata.title,
            "author": metadata.author,
            "filename": book.name,
            "format": parsed.format,
            "documentType": profile_name,
            "genre": classification.get("genre", "unknown"),
            "classification": classification,
            "analysisProfile": profile_name,
            "tabs": profile["tabs"],
            "metadata": metadata.to_dict(),
            "source": {
                "modifiedTime": book.modifiedTime,
                "md5Checksum": book.md5Checksum,
                "sha256": checksum,
                "changeKey": change_key,
                "size": book.size,
                "mimeType": book.mimeType,
                "folderPath": book.folderPath,
                "webViewLink": book.webViewLink,
            },
            "model": {"name": self.gemini.model_name, "sdk": "google-genai", "selection": "runtime models.list policy"},
            "versions": versions,
            "indexStatus": "COMPLETE",
            "indexedAt": _now(),
        }
        public_chunks = [{"chunkId": item["chunkId"], "charCount": item["charCount"], "position": item["position"], "summary": next((a.get("summary") for a in analyses if a.get("chunkId") == item["chunkId"]), "")} for item in chunks]
        self.storage.save_book(book_id, {
            "manifest.json": manifest,
            "summary.json": {"summaryShort": final_analysis.get("summaryShort", ""), "summaryLong": final_analysis.get("summaryLong", ""), "uncertainties": final_analysis.get("uncertainties", [])},
            "chunks.json": {"chunks": public_chunks},
            "analysis.json": {"profile": profile_name, "sections": final_analysis.get("sections", []), "analysis": final_analysis.get("analysis", {}), "uncertainties": final_analysis.get("uncertainties", [])},
            "timeline.json": {"timeline": final_analysis.get("timeline", [])},
            "relationships.json": {"relationships": final_analysis.get("relationships", [])},
        })
        self.storage.update_catalog({
            "bookId": book_id, "driveFileId": book.id, "title": metadata.title, "author": metadata.author,
            "documentType": profile_name, "genre": manifest["genre"], "tags": _catalog_tags(final_analysis),
            "format": parsed.format, "indexStatus": "COMPLETE", "metadataStatus": metadata.metadataStatus,
            "updatedAt": manifest["indexedAt"], "webViewLink": book.webViewLink,
        })
        self.checkpoints.clear(book_id)
        return "complete"

    def _save_checkpoint(self, book_id: str, checksum: str, versions: dict, classification: dict, analyses: list[dict], total: int) -> None:
        self.checkpoints.save(book_id, {
            "status": "PARTIAL", "sourceChecksum": checksum, "versions": versions,
            "classification": classification, "completedChunks": len(analyses), "totalChunks": total,
            "nextChunk": len(analyses) + 1, "chunkAnalyses": analyses, "updatedAt": _now(),
        })

    def _check_runtime(self) -> None:
        maximum = float(self.settings.quota.get("maxRuntimeMinutes", 0) or 0)
        if maximum and time.time() - self.started_at >= maximum * 60:
            raise BudgetExceeded("maxRuntimeMinutes reached")

    def _job(self, state: str, folder: dict, total: int) -> dict:
        return {"status": state, "folderId": folder["id"], "folderName": folder.get("name"), "totalFiles": total, "startedAt": _now()}

    def _save_job(self, status: dict) -> None:
        atomic_write_json(self.settings.root / "data" / "job-status.json", status)


def _book_id(file_id: str) -> str:
    return hashlib.sha256(file_id.encode("utf-8")).hexdigest()[:20]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _catalog_tags(final_analysis: dict) -> list[str]:
    tags: list[str] = []
    for value in (final_analysis.get("analysis") or {}).values():
        if isinstance(value, list):
            for item in value[:5]:
                if isinstance(item, str):
                    tags.append(item[:60])
                elif isinstance(item, dict):
                    label = item.get("name") or item.get("title") or item.get("concept")
                    if label:
                        tags.append(str(label)[:60])
    return list(dict.fromkeys(tags))[:20]


def _validated_candidate(value: Any, evidence: list[Evidence], field: str) -> str | None:
    if not value:
        return None
    normalize = lambda text: "".join(ch for ch in str(text).casefold() if ch.isalnum())
    wanted = normalize(value)
    for item in evidence:
        candidate = getattr(item, field)
        if candidate and normalize(candidate) == wanted:
            return candidate
    return None
