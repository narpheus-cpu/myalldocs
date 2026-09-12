from __future__ import annotations

import io
import zipfile

from indexer.parsers.epub import parse_epub
from indexer.parsers.txt import parse_txt


def make_epub() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("META-INF/container.xml", """<?xml version='1.0'?><container xmlns='urn:oasis:names:tc:opendocument:xmlns:container'><rootfiles><rootfile full-path='OEBPS/content.opf'/></rootfiles></container>""")
        zf.writestr("OEBPS/content.opf", """<package xmlns='http://www.idpf.org/2007/opf'><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'><dc:title>테스트 책</dc:title><dc:creator>테스트 저자</dc:creator></metadata><manifest><item id='c2' href='two.xhtml'/><item id='c1' href='one.xhtml'/></manifest><spine><itemref idref='c1'/><itemref idref='c2'/></spine></package>""")
        zf.writestr("OEBPS/one.xhtml", "<html><body><h1>첫 장</h1><p>첫 내용</p><nav>목차 제거</nav></body></html>")
        zf.writestr("OEBPS/two.xhtml", "<html><body><h1>둘째 장</h1><p>둘째 내용</p><script>제거</script></body></html>")
    return out.getvalue()


def test_txt_utf8_and_newlines():
    assert parse_txt("가\r\n나".encode()).text == "가\n나"


def test_txt_cp949():
    assert "한글" in parse_txt("한글 본문".encode("cp949")).text


def test_txt_empty():
    assert parse_txt(b"").text == ""


def test_epub_metadata_and_spine_order():
    book = parse_epub(make_epub())
    assert (book.embedded_title, book.embedded_author) == ("테스트 책", "테스트 저자")
    assert book.text.index("첫 내용") < book.text.index("둘째 내용")
    assert "목차 제거" not in book.text and "제거" not in book.text
