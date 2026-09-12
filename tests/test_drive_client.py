from indexer.drive_client import EPUB_MIME, TEXT_MIME, _is_supported_book


def test_text_mime_is_accepted_when_drive_copy_name_loses_txt_suffix():
    assert _is_supported_book({"name": "소설.txt의 사본", "mimeType": TEXT_MIME})


def test_epub_mime_is_accepted_without_epub_suffix():
    assert _is_supported_book({"name": "전자책의 사본", "mimeType": EPUB_MIME})


def test_unsupported_mime_and_suffix_are_rejected():
    assert not _is_supported_book({"name": "문서.pdf", "mimeType": "application/pdf"})
