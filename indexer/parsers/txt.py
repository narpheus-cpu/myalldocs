from __future__ import annotations

from pathlib import Path

from indexer.models import ParsedBook


ENCODINGS = ("utf-8-sig", "utf-8", "cp949", "euc-kr")


def decode_txt(data: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def parse_txt(source: bytes | Path) -> ParsedBook:
    data = source.read_bytes() if isinstance(source, Path) else source
    text = decode_txt(data).replace("\r\n", "\n").replace("\r", "\n")
    return ParsedBook(text=text, format="txt", sections=[])
