from __future__ import annotations

import re


def chunk_text(text: str, target_chars: int = 5000, overlap_chars: int = 250) -> list[dict]:
    if target_chars < 200:
        raise ValueError("target_chars must be at least 200")
    clean = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean:
        return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = _split_long(paragraph, target_chars)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}".strip() if current else piece
            if current and len(candidate) > target_chars:
                chunks.append(current)
                overlap = current[-overlap_chars:].lstrip() if overlap_chars else ""
                current = f"{overlap}\n\n{piece}".strip()
            else:
                current = candidate
    if current:
        chunks.append(current)
    total = len(chunks)
    return [
        {"chunkId": i + 1, "text": value, "charCount": len(value), "position": round(i / max(1, total - 1), 4)}
        for i, value in enumerate(chunks)
    ]


def _split_long(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    result: list[str] = []
    buf = ""
    for sentence in sentences:
        if len(sentence) > limit:
            if buf:
                result.append(buf)
                buf = ""
            result.extend(sentence[i : i + limit] for i in range(0, len(sentence), limit))
        elif not buf:
            buf = sentence
        elif len(buf) + 1 + len(sentence) <= limit:
            buf += " " + sentence
        else:
            result.append(buf)
            buf = sentence
    if buf:
        result.append(buf)
    return result
