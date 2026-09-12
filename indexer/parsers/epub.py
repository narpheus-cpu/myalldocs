from __future__ import annotations

import io
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET

from indexer.models import ParsedBook


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "nav"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "div", "h1", "h2", "h3", "li", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav"} and self.hidden:
            self.hidden -= 1
        if not self.hidden and tag in {"p", "div", "h1", "h2", "h3", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)

    def text(self) -> str:
        value = "".join(self.parts)
        value = re.sub(r"[ \t]+", " ", value)
        value = re.sub(r"\n\s*\n\s*\n+", "\n\n", value)
        return value.strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _read_xml(zf: zipfile.ZipFile, name: str) -> ET.Element:
    return ET.fromstring(zf.read(name))


def _opf_path(zf: zipfile.ZipFile) -> str:
    container = _read_xml(zf, "META-INF/container.xml")
    for elem in container.iter():
        if _local(elem.tag) == "rootfile" and elem.attrib.get("full-path"):
            return elem.attrib["full-path"]
    raise ValueError("EPUB container has no OPF rootfile")


def parse_epub(source: bytes | Path) -> ParsedBook:
    raw = source.read_bytes() if isinstance(source, Path) else source
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        opf_name = _opf_path(zf)
        opf = _read_xml(zf, opf_name)
        title = author = None
        manifest: dict[str, str] = {}
        spine: list[str] = []
        for elem in opf.iter():
            local = _local(elem.tag)
            if local == "title" and not title and elem.text:
                title = elem.text.strip()
            elif local == "creator" and not author and elem.text:
                author = elem.text.strip()
            elif local == "item" and elem.attrib.get("id") and elem.attrib.get("href"):
                manifest[elem.attrib["id"]] = elem.attrib["href"]
            elif local == "itemref" and elem.attrib.get("idref"):
                spine.append(elem.attrib["idref"])
        base = PurePosixPath(opf_name).parent
        sections: list[str] = []
        for item_id in spine:
            href = manifest.get(item_id)
            if not href:
                continue
            name = str(base / PurePosixPath(href.split("#", 1)[0]))
            parser = _TextExtractor()
            parser.feed(zf.read(name).decode("utf-8", errors="replace"))
            section = parser.text()
            if section:
                sections.append(section)
    return ParsedBook(
        text="\n\n".join(sections),
        format="epub",
        embedded_title=title,
        embedded_author=author,
        sections=sections,
    )
