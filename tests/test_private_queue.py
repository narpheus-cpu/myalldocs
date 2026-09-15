import json
from pathlib import Path

import pytest

from indexer.canonical import CanonicalValidationError, legacy_to_canonical, normalize_canonical, text_sha256
from indexer.models import DriveBook
from indexer.prior_knowledge import prior_knowledge_prompt, valid_prior_result
from indexer.private_queue import parse_jsonl
from indexer.queue_worker import match_entries


def test_jsonl_is_parsed_in_memory_and_requires_txt_or_epub():
    rows = parse_jsonl('{"filename":"가/책.txt","excerpt_start":"비공개"}\n{"filename":"책.epub"}\n'.encode())
    assert len(rows) == 2
    with pytest.raises(ValueError):
        parse_jsonl(b'{"filename":"book.pdf"}\n')


def test_drive_matching_prefers_relative_path_and_marks_ambiguous_names():
    books = [
        DriveBook("a" * 12, "같은책.txt", "text/plain", folderPath=["book", "소설"]),
        DriveBook("b" * 12, "같은책.txt", "text/plain", folderPath=["book", "철학"]),
    ]
    matched = match_entries([{"filename": "같은책.txt", "relative_path": "소설"}, {"filename": "같은책.txt"}], books, "book")
    assert matched[0]["status"] == "MATCHED"
    assert matched[0]["driveFileId"] == "a" * 12
    assert matched[1]["status"] == "AMBIGUOUS"


def test_prior_knowledge_prompt_keeps_sections_empty_and_forbids_web():
    prompt = prior_knowledge_prompt({"filename": "작품.txt", "excerpt_start": "식별용 일부"})
    assert "식별하는 데만 사용" in prompt
    assert "인터넷 검색" in prompt
    assert '"sectionSummaries": []' in prompt


def test_prior_result_requires_identity_confidence_and_knowledge():
    value = {"identityDecision": {"identityStatus": "INFERRED", "confidence": .9, "knowledgeSufficient": True}, "identity": {"title": "제목", "author": "작가"}, "content": {"sectionSummaries": []}}
    assert valid_prior_result(value, .82) == (True, "")
    value["identityDecision"]["knowledgeSufficient"] = False
    assert valid_prior_result(value, .82)[0] is False


def test_canonical_requires_drive_match_and_hash_is_stable():
    raw = {"identity": {"title": "제목", "author": "작가"}, "source": {}, "system": {}}
    with pytest.raises(CanonicalValidationError):
        normalize_canonical(raw)
    assert text_sha256("가  나\r\n다") == text_sha256("가 나\n다")


def test_legacy_migration_preserves_source_provenance():
    book = legacy_to_canonical(
        {"bookId": "x", "driveFileId": "d" * 12, "title": "제목", "author": "작가", "indexStatus": "COMPLETE", "metadata": {"manualOverrideApplied": True}},
        {"summaryShort": "한줄", "summaryLong": "전체"}, {}, {}, {}, {"chunks": []},
    )
    assert book["system"]["generation"]["source"] == "source_text_analysis"
    assert book["system"]["edited"] is True


def test_public_project_sources_do_not_persist_private_excerpt_fields():
    root = Path(__file__).resolve().parents[1]
    public = (root / "indexer" / "canonical.py").read_text(encoding="utf-8") + (root / "indexer" / "storage.py").read_text(encoding="utf-8")
    assert "excerpt_start" not in public
    assert "excerpt_middle" not in public
    assert "excerpt_late" not in public


def test_upload_ui_is_separate_and_drive_tab_is_removed_from_navigation():
    root = Path(__file__).resolve().parents[1]
    html = (root / "index.html").read_text(encoding="utf-8")
    script = (root / "js" / "app.js").read_text(encoding="utf-8")
    assert 'id="upload-catalog-jsonl"' in html
    assert 'id="upload-canonical-json"' in html
    assert 'data-nav="drive"' not in html
    assert 'data-nav="search"' not in html
    assert 'data-nav="library"' in html
    assert "upload-start" in script and "upload-part" in script and "upload-finish" in script


def test_private_upload_retries_transient_relay_failures_idempotently():
    root = Path(__file__).resolve().parents[1]
    script = (root / "js" / "app.js").read_text(encoding="utf-8")
    relay = (root / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    assert "const partBytes=131072" in script
    assert "maxAttempts=retryable?5:1" in script
    assert 'route:"upload-capabilities"' in script
    assert 'relayRequest({route:"upload-capabilities"},true,true)' in script
    assert "idempotentUploads: true" in relay
    assert "UPLOAD_REQUEST_" in relay
    assert "UPLOAD_FINISHED_" in relay
    assert "existingManifest" in relay
    assert "file.name === 'queue-manifest.json'" in relay


def test_queue_upload_survives_missing_or_rejected_github_token():
    root = Path(__file__).resolve().parents[1]
    relay = (root / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "queue-worker.yml").read_text(encoding="utf-8")
    assert "queueScheduledFallback_" in relay
    assert "github_http_" in relay
    assert "scheduledFallback: true" in relay
    assert 'cron: "*/20 * * * *"' in workflow
    assert workflow.index("Load the next private queue item") < workflow.index("Install current supported SDKs")
    script = (root / "js" / "app.js").read_text(encoding="utf-8")
    assert "finish.dispatch?.scheduledFallback" in script


def test_private_management_storage_enables_drive_api_and_rejects_personal_email():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "apps-script" / "appsscript.json").read_text(encoding="utf-8"))
    relay = (root / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    services = manifest["dependencies"]["enabledAdvancedServices"]
    assert {"userSymbol": "Drive", "version": "v3", "serviceId": "drive"} in services
    assert "function setupPrivateStorage()" in relay
    assert "iam\\.gserviceaccount\\.com" in relay
    assert "일반 Gmail 주소는 사용할 수 없습니다" in relay
    assert "function driveAdvancedError_" in relay
    assert "Drive.Files.create" in relay
    assert "Drive.Permissions.create" in relay


def test_private_upload_authentication_happens_before_file_picker():
    root = Path(__file__).resolve().parents[1]
    script = (root / "js" / "app.js").read_text(encoding="utf-8")
    assert 'onclick=()=>beginPrivateFileChoice("catalog-jsonl")' in script
    assert 'onclick=()=>$("#catalog-jsonl-file").click()' not in script
    assert 'e.type==="popup_failed_to_open"' in script
    assert "Google 계정이 연결됐습니다. 업로드 버튼을 한 번 더 눌러" in script


def test_completed_json_path_has_explicit_no_gemini_client():
    root = Path(__file__).resolve().parents[1]
    source = (root / "indexer" / "queue_worker.py").read_text(encoding="utf-8")
    assert "class NoGeminiClient" in source
    assert 'if bundle_kind == "catalog-jsonl"' in source
    assert 'else NoGeminiClient(settings.quota)' in source
