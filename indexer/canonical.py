from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from indexer.checkpoint import atomic_write_json


CANONICAL_SCHEMA_VERSION = 2
PROVENANCE_SOURCES = {"source_text_analysis", "model_prior_knowledge", "user_authored"}
WORK_PROFILES = {
    "fiction", "drama", "poetry", "academic", "philosophy", "history_biography",
    "science_technical", "essay_general_nonfiction", "practical_manual",
    "mixed_anthology", "unknown",
}


class CanonicalValidationError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text_for_hash(value: str) -> str:
    return re.sub(r"[ \t]+", " ", str(value).replace("\r\n", "\n").replace("\r", "\n")).strip()


def text_sha256(value: str) -> str:
    return hashlib.sha256(normalize_text_for_hash(value).encode("utf-8")).hexdigest()


def stable_book_id(drive_file_id: str) -> str:
    return hashlib.sha256(str(drive_file_id).encode("utf-8")).hexdigest()[:20]


def _string(value: Any, limit: int = 200_000) -> str:
    return str(value or "").strip()[:limit]


def _tags(value: Any) -> list[str]:
    source = value if isinstance(value, list) else re.split(r"[,\n]", _string(value, 4000))
    result: list[str] = []
    seen: set[str] = set()
    for item in source:
        tag = re.sub(r"\s+", " ", _string(item, 60).lstrip("#")).strip()
        key = tag.casefold()
        if tag and key not in seen:
            seen.add(key)
            result.append(tag)
    return result[:30]


def _list_of_dicts(value: Any, limit: int = 1000) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [deepcopy(item) for item in value[:limit] if isinstance(item, dict)]


def normalize_canonical(raw: dict[str, Any], *, require_drive_match: bool = True) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise CanonicalValidationError("도서 데이터의 최상위 값은 객체여야 합니다.")
    identity = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
    system = raw.get("system") if isinstance(raw.get("system"), dict) else {}
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}

    title = _string(identity.get("title") or raw.get("title"), 300)
    author = _string(identity.get("author") or raw.get("author"), 300)
    if not title:
        raise CanonicalValidationError("작품명이 없습니다.")
    if not author:
        raise CanonicalValidationError("작가명이 없습니다.")

    profile = _string((identity.get("workProfile") or {}).get("primary") if isinstance(identity.get("workProfile"), dict) else identity.get("work_profile"), 80)
    profile = profile or _string(raw.get("documentType"), 80) or "unknown"
    if profile not in WORK_PROFILES:
        profile = "unknown"

    drive_id = _string(system.get("driveFileId") or system.get("drive_file_id") or source.get("driveFileId") or source.get("drive_file_id") or raw.get("driveFileId"), 200)
    match_status = _string(system.get("driveMatchStatus") or system.get("drive_match_status"), 40) or ("MATCHED" if drive_id else "UNMATCHED")
    if require_drive_match and (not drive_id or match_status != "MATCHED"):
        raise CanonicalValidationError("Google Drive 원본 연결이 완료되지 않았습니다.")

    provenance = _string((system.get("generation") or {}).get("source") if isinstance(system.get("generation"), dict) else raw.get("provenance"), 60) or "user_authored"
    if provenance not in PROVENANCE_SOURCES:
        provenance = "user_authored"
    created = _string(system.get("createdAt") or system.get("created_at") or raw.get("indexedAt"), 80) or now_iso()
    updated = _string(system.get("updatedAt") or system.get("updated_at"), 80) or created
    book_id = _string(system.get("libraryEntryId") or system.get("library_entry_id") or raw.get("bookId"), 100) or (stable_book_id(drive_id) if drive_id else "")

    summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    key_entities = content.get("keyEntities") or content.get("key_entities") or raw.get("key_entities") or {}
    if not isinstance(key_entities, dict):
        key_entities = {}
    setting = content.get("setting") or raw.get("setting") or {}
    if not isinstance(setting, dict):
        setting = {}
    generation = system.get("generation") if isinstance(system.get("generation"), dict) else {}
    legacy_model = (raw.get("model") or {}).get("name", "") if isinstance(raw.get("model"), dict) else ""
    result = {
        "schemaVersion": CANONICAL_SCHEMA_VERSION,
        "identity": {
            "title": title,
            "author": author,
            "genre": _string(identity.get("genre") or raw.get("genre"), 100),
            "tags": _tags(identity.get("tags") if "tags" in identity else raw.get("tags")),
            "workProfile": {
                "primary": profile,
                "secondary": _tags((identity.get("workProfile") or {}).get("secondary", []) if isinstance(identity.get("workProfile"), dict) else []),
            },
            "aliases": deepcopy(identity.get("aliases")) if isinstance(identity.get("aliases"), dict) else {"titles": [], "authors": []},
        },
        "content": {
            "authorIntroduction": _string(content.get("authorIntroduction") or content.get("author_introduction") or raw.get("authorIntroduction")),
            "oneLineSummary": _string(content.get("oneLineSummary") or content.get("one_line_summary") or summary.get("summaryShort") or raw.get("oneLineSummary"), 2000),
            "overallSummary": _string(content.get("overallSummary") or content.get("overall_summary") or summary.get("summaryLong") or raw.get("overallSummary")),
            "sectionSummaries": _list_of_dicts(content.get("sectionSummaries") or content.get("section_summaries") or raw.get("sectionSummaries")),
            "keyEntities": {
                "type": _string(key_entities.get("type"), 60) or "mixed",
                "items": _list_of_dicts(key_entities.get("items")),
            },
            "setting": {
                "internal": deepcopy(setting.get("internal")) if isinstance(setting.get("internal"), dict) else {},
                "external": deepcopy(setting.get("external")) if isinstance(setting.get("external"), dict) else {},
            },
            "adaptiveAnalysis": _list_of_dicts(content.get("adaptiveAnalysis") or content.get("adaptive_analysis") or raw.get("adaptiveAnalysis"), 30),
        },
        "source": {
            "provider": "google-drive",
            "driveFileId": drive_id,
            "filename": _string(source.get("filename") or raw.get("filename"), 500),
            "format": _string(source.get("format") or raw.get("format"), 20).casefold(),
            "mimeType": _string(source.get("mimeType") or source.get("mime_type"), 120),
            "modifiedTime": _string(source.get("modifiedTime") or source.get("modified_time"), 80),
            "size": _string(source.get("size"), 40),
            "folderPath": deepcopy(source.get("folderPath") or source.get("folder_path") or []),
            "relativePath": _string(source.get("relativePath") or source.get("relative_path"), 2000),
            "webViewLink": _string(source.get("webViewLink") or source.get("web_view_link"), 2000),
            "sourceSha256": _string(source.get("sourceSha256") or source.get("source_sha256") or source.get("sha256"), 64),
            "textSha256": _string(source.get("textSha256") or source.get("text_sha256"), 64),
            "changeKey": _string(source.get("changeKey") or source.get("change_key"), 200),
            "chunking": deepcopy(source.get("chunking") or raw.get("chunking") or {"targetCharacters": 5000, "overlapCharacters": 0}),
        },
        "system": {
            "libraryEntryId": book_id,
            "workId": _string(system.get("workId") or system.get("work_id"), 100) or book_id,
            "driveFileId": drive_id,
            "driveMatchStatus": match_status,
            "identityConfidence": float(system.get("identityConfidence") or system.get("identity_confidence") or 0),
            "identityStatus": _string(system.get("identityStatus") or system.get("identity_status"), 60) or "CONFIRMED",
            "indexStatus": _string(system.get("indexStatus") or system.get("index_status") or raw.get("indexStatus"), 60) or "COMPLETE",
            "schemaVersion": CANONICAL_SCHEMA_VERSION,
            "promptVersion": int(system.get("promptVersion") or system.get("prompt_version") or 1),
            "createdAt": created,
            "updatedAt": updated,
            "generation": {
                "source": provenance,
                "model": _string(generation.get("model") or legacy_model, 200),
            },
            "edited": bool(system.get("edited") or False),
        },
    }
    known_content_keys = {
        "authorIntroduction", "author_introduction", "oneLineSummary", "one_line_summary",
        "overallSummary", "overall_summary", "sectionSummaries", "section_summaries",
        "keyEntities", "key_entities", "setting", "adaptiveAnalysis", "adaptive_analysis",
    }
    forbidden_content_keys = {"sourceText", "source_text", "rawText", "raw_text", "originalText", "original_text", "fullText", "full_text"}
    for key, value in content.items():
        if key not in known_content_keys and key not in forbidden_content_keys:
            result["content"][key] = deepcopy(value)
    if not result["source"]["format"]:
        result["source"]["format"] = "epub" if result["source"]["filename"].casefold().endswith(".epub") else "txt"
    return result


def legacy_to_canonical(manifest: dict[str, Any], summary: dict[str, Any], analysis: dict[str, Any], timeline: dict[str, Any], relationships: dict[str, Any], chunks: dict[str, Any]) -> dict[str, Any]:
    analysis_values = analysis.get("analysis", {}) if isinstance(analysis.get("analysis"), dict) else {}
    entity_key = next((key for key in ("characters", "concepts", "people", "principles", "organizations", "systems") if analysis_values.get(key)), "")
    entity_type = {"characters": "characters", "concepts": "concepts", "people": "persons", "principles": "principles", "organizations": "organizations", "systems": "systems"}.get(entity_key, "mixed")
    adaptive = []
    for key, value in analysis_values.items():
        if key == entity_key:
            continue
        adaptive.append({"key": key, "title": key, "content": deepcopy(value)})
    if timeline.get("timeline"):
        adaptive.append({"key": "timeline", "title": "연대기·사건 흐름", "content": deepcopy(timeline["timeline"])})
    if relationships.get("relationships"):
        adaptive.append({"key": "relationships", "title": "관계 변화", "content": deepcopy(relationships["relationships"])})
    source = deepcopy(manifest.get("source", {}))
    source.update({
        "driveFileId": manifest.get("driveFileId", ""),
        "filename": manifest.get("filename", ""),
        "format": manifest.get("format", ""),
        "sourceSha256": source.get("sha256", ""),
        "chunking": manifest.get("chunking", {"targetCharacters": 5000, "overlapCharacters": 250}),
    })
    metadata = manifest.get("metadata", {}) if isinstance(manifest.get("metadata"), dict) else {}
    raw = {
        "bookId": manifest.get("bookId"),
        "identity": {
            "title": manifest.get("title"), "author": manifest.get("author"), "genre": manifest.get("genre"),
            "tags": manifest.get("tags", []),
            "workProfile": {"primary": manifest.get("documentType", "unknown"), "secondary": []},
        },
        "content": {
            "authorIntroduction": "",
            "oneLineSummary": summary.get("summaryShort", ""),
            "overallSummary": summary.get("summaryLong", ""),
            "sectionSummaries": [
                {"sectionNumber": item.get("chunkId", index + 1), "title": f"제{item.get('chunkId', index + 1)}구간", "summary": item.get("summary", "")}
                for index, item in enumerate(chunks.get("chunks", [])) if isinstance(item, dict)
            ],
            "keyEntities": {"type": entity_type, "items": deepcopy(analysis_values.get(entity_key, [])) if entity_key else []},
            "setting": {"internal": {"analysis": deepcopy(analysis_values.get("settings", []))}, "external": {}},
            "adaptiveAnalysis": adaptive,
        },
        "source": source,
        "system": {
            "libraryEntryId": manifest.get("bookId"), "workId": manifest.get("bookId"),
            "driveFileId": manifest.get("driveFileId"), "driveMatchStatus": "MATCHED",
            "identityConfidence": metadata.get("confidence", 0), "identityStatus": metadata.get("metadataStatus", "confirmed"),
            "indexStatus": manifest.get("indexStatus", "COMPLETE"), "createdAt": manifest.get("indexedAt"),
            "updatedAt": manifest.get("indexedAt"), "edited": bool(metadata.get("manualOverrideApplied")),
            "generation": {"source": "source_text_analysis", "model": (manifest.get("model") or {}).get("name", "")},
        },
    }
    return normalize_canonical(raw)


def flatten_search_text(value: Any) -> str:
    parts: list[str] = []
    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)
        elif isinstance(item, (str, int, float)):
            parts.append(str(item))
    visit(value)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def write_search_index(root: Path) -> None:
    items = []
    for path in sorted((root / "data" / "books").glob("*/book.json")):
        try:
            book = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if book.get("system", {}).get("indexStatus") != "COMPLETE" or book.get("system", {}).get("driveMatchStatus") != "MATCHED":
            continue
        items.append({"bookId": book.get("system", {}).get("libraryEntryId") or path.parent.name, "text": flatten_search_text({"identity": book.get("identity"), "content": book.get("content")})})
    atomic_write_json(root / "data" / "search-index.json", {"schemaVersion": 1, "books": items})
