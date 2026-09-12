from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from indexer.models import Evidence, MetadataResolution, ParsedBook


SOURCE_WEIGHTS = {
    "manual_override": 1.0,
    "epub_opf": 0.42,
    "title_page": 0.38,
    "folder": 0.12,
    "filename": 0.08,
    "ai_local_resolution": 0.16,
}


def _norm(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^0-9a-z가-힣]", "", value.casefold())


def _filename_guess(filename: str) -> tuple[str | None, str | None]:
    stem = Path(filename).stem
    stem = re.sub(r"\[[^\]]*\]|\([^)]*(완결|스캔|텍본)[^)]*\)", " ", stem, flags=re.I)
    parts = [p.strip(" -_") for p in re.split(r"\s*[-_]\s*", stem) if p.strip(" -_")]
    if len(parts) >= 2:
        return parts[0], parts[-1]
    return (stem.strip() or None), None


def _title_page_guess(text: str) -> tuple[str | None, str | None]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text[:8000].splitlines()]
    lines = [line for line in lines if 1 < len(line) <= 120][:30]
    author = None
    title = None
    for line in lines:
        match = re.match(r"(?:지은이|저자|글)\s*[:：]?\s*(.+)$", line, re.I)
        if match:
            author = match.group(1).strip()
            break
    for line in lines:
        if re.match(r"(?:지은이|저자|글|목차|차례|제\s*\d+\s*[장부])", line, re.I):
            continue
        title = line
        break
    return title, author


def collect_local_evidence(parsed: ParsedBook, filename: str, folder_path: list[str]) -> list[Evidence]:
    evidence: list[Evidence] = []
    if parsed.embedded_title or parsed.embedded_author:
        evidence.append(Evidence("epub_opf", parsed.embedded_title, parsed.embedded_author, SOURCE_WEIGHTS["epub_opf"]))
    page_title, page_author = _title_page_guess(parsed.text)
    if page_title or page_author:
        evidence.append(Evidence("title_page", page_title, page_author, SOURCE_WEIGHTS["title_page"], "본문 앞부분의 표제부 후보"))
    if folder_path:
        evidence.append(Evidence("folder", folder_path[-1], folder_path[-2] if len(folder_path) > 1 else None, SOURCE_WEIGHTS["folder"], "/".join(folder_path)))
    file_title, file_author = _filename_guess(filename)
    evidence.append(Evidence("filename", file_title, file_author, SOURCE_WEIGHTS["filename"], filename))
    return evidence


def resolve_metadata(
    evidence: list[Evidence],
    threshold: float = 0.75,
    override: dict[str, Any] | None = None,
) -> MetadataResolution:
    if override and (override.get("title") or override.get("author")):
        title = override.get("title") or _best(evidence, "title")[0] or "제목 미상"
        author = override.get("author") or _best(evidence, "author")[0] or "저자 미상"
        manual = Evidence("manual_override", title, author, 1.0, "재인덱싱에도 보존되는 사용자 수정")
        return MetadataResolution(title, author, 1.0, [manual, *evidence], "manual_override", "manual_override", False, "confirmed", True)

    title, title_source, title_score = _best(evidence, "title")
    author, author_source, author_score = _best(evidence, "author")
    conflict = _has_conflict(evidence, "title") or _has_conflict(evidence, "author")
    completeness = 1.0 if title and author else 0.65
    confidence = round(min(1.0, ((title_score + author_score) / 2) * completeness), 3)
    if conflict or confidence < threshold:
        status = "NEEDS_METADATA_REVIEW"
    elif confidence >= 0.8:
        status = "confirmed"
    else:
        status = "inferred"
    return MetadataResolution(
        title or "제목 미상",
        author or "저자 미상",
        confidence,
        evidence,
        title_source or "unknown",
        author_source or "unknown",
        conflict,
        status,
        False,
    )


def _best(evidence: list[Evidence], field: str) -> tuple[str | None, str | None, float]:
    scores: dict[str, float] = {}
    originals: dict[str, tuple[str, str]] = {}
    original_weights: dict[str, float] = {}
    sources: dict[str, set[str]] = {}
    for item in evidence:
        value = getattr(item, field)
        key = _norm(value)
        if not key or not value:
            continue
        scores[key] = scores.get(key, 0.0) + item.weight
        sources.setdefault(key, set()).add(item.source)
        if key not in originals or item.weight > original_weights[key]:
            originals[key] = (value, item.source)
            original_weights[key] = item.weight
    if not scores:
        return None, None, 0.0
    key = max(scores, key=scores.get)
    value, source = originals[key]
    reliability = scores[key]
    if "epub_opf" in sources[key]:
        reliability = max(reliability, 0.90)
    elif "title_page" in sources[key]:
        reliability = max(reliability, 0.82)
    elif "folder" in sources[key]:
        reliability = max(reliability, 0.45)
    elif "filename" in sources[key]:
        reliability = max(reliability, 0.30)
    return value, source, min(1.0, reliability)


def _has_conflict(evidence: list[Evidence], field: str) -> bool:
    strong = {
        _norm(getattr(item, field))
        for item in evidence
        if item.source in {"epub_opf", "title_page"} and getattr(item, field)
    }
    return len(strong) > 1
