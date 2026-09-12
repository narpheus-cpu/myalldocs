from indexer.metadata import collect_local_evidence, resolve_metadata
from indexer.models import Evidence, ParsedBook


def test_epub_metadata_outweighs_wrong_filename():
    parsed = ParsedBook("테스트 책\n저자: 김작가\n본문", "epub", "테스트 책", "김작가")
    result = resolve_metadata(collect_local_evidence(parsed, "틀린제목_다른사람.epub", ["book", "소설"]), 0.75)
    assert result.title == "테스트 책"
    assert result.author == "김작가"
    assert result.titleSource == "epub_opf"


def test_strong_conflict_needs_review():
    evidence = [Evidence("epub_opf", "책A", "저자A", .42), Evidence("title_page", "책B", "저자B", .38)]
    result = resolve_metadata(evidence, .75)
    assert result.conflictDetected and result.metadataStatus == "NEEDS_METADATA_REVIEW"


def test_manual_override_is_absolute_and_preserved():
    result = resolve_metadata([Evidence("epub_opf", "오래된 제목", "저자", .42)], .75, {"title": "수정 제목", "author": "수정 저자"})
    assert result.title == "수정 제목" and result.confidence == 1
    assert result.manualOverrideApplied and result.metadataStatus == "confirmed"


def test_low_confidence_stays_in_manual_review():
    result = resolve_metadata([Evidence("filename", "파일 제목", None, .08)], .75)
    assert result.metadataStatus == "NEEDS_METADATA_REVIEW"
    assert result.confidence < .75
