import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from indexer.canonical import CanonicalValidationError, legacy_to_canonical, normalize_canonical, text_sha256
from indexer.models import DriveBook
from indexer.content_edit_worker import apply_content_edit
from indexer.prior_knowledge import prior_knowledge_prompt, valid_prior_result
from indexer.private_queue import CanonicalUploadFormatError, parse_canonical_upload, parse_jsonl
from indexer.queue_context import service_account_email
from indexer.queue_worker import QueueWorker, _validate_public_content, match_entries, reconcile_completed_entries


def test_jsonl_is_parsed_in_memory_and_requires_txt_or_epub():
    rows = parse_jsonl('{"filename":"가/책.txt","excerpt_start":"비공개"}\n{"filename":"책.epub"}\n'.encode())
    assert len(rows) == 2
    with pytest.raises(ValueError):
        parse_jsonl(b'{"filename":"book.pdf"}\n')


def test_completed_upload_accepts_object_array_and_jsonl_even_with_json_filename():
    first = {"identity": {"title": "첫 책", "author": "작가"}, "content": {"oneLineSummary": "한 줄", "overallSummary": "전체"}}
    second = {"identity": {"title": "둘째 책", "author": "작가"}, "content": {"oneLineSummary": "한 줄", "overallSummary": "전체"}}

    assert parse_canonical_upload(json.dumps(first, ensure_ascii=False).encode()) == [first]
    assert parse_canonical_upload(json.dumps([first, second], ensure_ascii=False).encode()) == [first, second]
    jsonl = "\n".join(json.dumps(item, ensure_ascii=False) for item in (first, second)).encode()
    assert parse_canonical_upload(jsonl) == [first, second]


def test_completed_upload_reports_the_bad_jsonl_line_without_crashing_the_runner():
    payload = b'{"identity":{"title":"ok"}}\n{"identity":}\n'
    with pytest.raises(CanonicalUploadFormatError, match="2번째 줄"):
        parse_canonical_upload(payload)


def test_private_queue_reads_shared_files_but_delegates_state_writes():
    root = Path(__file__).resolve().parents[1]
    source = (root / "indexer" / "private_queue.py").read_text(encoding="utf-8")
    assert '"https://www.googleapis.com/auth/drive.readonly"' in source
    assert '"route": "queue-state"' in source
    assert '"stateGzipBase64"' in source
    assert "gzip.compress" in source
    assert "timeout=90" in source
    assert 'str(value.get("error") or "")[:300]' in source
    assert 'self.service.files().update' not in source
    assert 'self.service.files().create' not in source
    assert '"https://www.googleapis.com/auth/drive.file"' not in source
    assert '"https://www.googleapis.com/auth/drive"]' not in source


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


def test_canonical_preserves_uploaded_custom_analysis_fields_and_titles():
    raw = {
        "identity": {"title": "제목", "author": "작가", "workProfile": {"primary": "fiction"}},
        "content": {
            "oneLineSummary": "한 줄",
            "플롯구조분석": {"title": "플롯구조분석", "content": ["발단", "전개"]},
            "adaptiveAnalysis": [{"key": "plotStructure", "title": "플롯구조분석", "content": {"단계": ["발단"]}}],
        },
        "source": {"driveFileId": "d" * 12, "filename": "책.txt"},
        "system": {"driveFileId": "d" * 12, "driveMatchStatus": "MATCHED"},
    }
    book = normalize_canonical(raw)
    assert book["content"]["플롯구조분석"]["title"] == "플롯구조분석"
    assert book["content"]["adaptiveAnalysis"][0]["title"] == "플롯구조분석"


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
    assert "dispatchFailureText(finish.dispatch)" in script


def test_github_dispatch_token_is_verified_before_replacement_and_failures_are_visible():
    root = Path(__file__).resolve().parents[1]
    relay = (root / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    script = (root / "js" / "app.js").read_text(encoding="utf-8")
    html = (root / "index.html").read_text(encoding="utf-8")
    assert "function updateGitHubToken_(body)" in relay
    assert relay.index("var result = requestQueueWorkflow_") < relay.index("GITHUB_TOKEN: token")
    assert "githubDispatchFailureMessage_" in relay
    assert "GITHUB_DISPATCH_LAST_REASON" in relay
    assert 'id="save-github-token"' in html
    assert 'id="retry-queue-dispatch"' in html
    assert "dispatchFailureText" in script
    assert "기존 값은 바꾸지 않았습니다" in script
    assert "정기 자동 실행을 기다립니다(보통 20분 이내).`" not in script


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
    assert "driveEnsureEditor_(folder.id" in relay
    assert "ensureQueueManifestAccess_(ids[0], serviceAccountEmail)" in relay
    assert "driveEnsureEditor_(String(manifestId), serviceAccountEmail)" in relay
    assert "Drive.Permissions.list" in relay
    assert "body.serviceAccountEmail" in relay
    assert "normalizeServiceAccountEmail_" in relay
    assert "incomingEmail !== storedEmail" in relay
    assert "body.route === 'queue-state'" in relay
    assert "function handleQueueState_(body)" in relay
    assert "Utilities.ungzip" in relay
    assert "'application/gzip'" in relay
    assert "Drive.Files.update" in relay
    assert ".setContent(" not in relay


def test_queue_worker_uses_the_service_account_identity_from_its_secret():
    assert service_account_email('{"client_email":"Worker@Project.iam.gserviceaccount.com"}') == "worker@project.iam.gserviceaccount.com"
    assert service_account_email('{"client_email":"ordinary@example.com"}') == ""
    assert service_account_email("not-json") == ""


def test_queue_lookup_retries_transient_apps_script_timeouts():
    root = Path(__file__).resolve().parents[1]
    source = (root / "indexer" / "queue_context.py").read_text(encoding="utf-8")
    assert "for attempt in range(2)" in source
    assert "timeout=60" in source


def test_missing_public_result_is_requeued_after_runner_commit_failure(tmp_path):
    entries = [{"status": "COMPLETE", "bookId": "a" * 20, "driveFileId": "drive-file-1"}]
    assert reconcile_completed_entries(entries, {}, tmp_path) == 1
    assert entries[0]["status"] == "MATCHED"
    assert "bookId" not in entries[0]

    book = tmp_path / "data" / "books" / ("a" * 20) / "book.json"
    book.parent.mkdir(parents=True)
    book.write_text("{}", encoding="utf-8")
    entries[0].update({"status": "COMPLETE", "bookId": "a" * 20})
    catalog = {"drive-file-1": {"bookId": "a" * 20}}
    assert reconcile_completed_entries(entries, catalog, tmp_path) == 0


def test_manual_upload_detects_legacy_automatic_source_hash(tmp_path):
    manifest = tmp_path / "data" / "books" / "automatic-id" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"source": {"sha256": "same-source"}}), encoding="utf-8")
    worker = object.__new__(QueueWorker)
    worker.settings = SimpleNamespace(root=tmp_path)
    assert worker._duplicate_hash("same-source", "different-text") is True


def test_queue_result_commit_and_callback_are_recoverable_and_idempotent():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "queue-worker.yml").read_text(encoding="utf-8")
    callback = (root / "indexer" / "queue_context.py").read_text(encoding="utf-8")
    relay = (root / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    assert "data/job-status.json" in workflow
    assert "for attempt in range(3)" in callback
    assert '"resultId": result_id' in callback
    assert "QUEUE_RESULT_" in relay
    assert "LockService.getScriptLock()" in relay


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


def test_content_edit_uses_private_drive_and_actions_without_exposing_secrets():
    root = Path(__file__).resolve().parents[1]
    relay = (root / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    workflow = (root / ".github" / "workflows" / "apply-content-edit.yml").read_text(encoding="utf-8")
    deploy = (root / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")
    assert "queueBookContentUpdate_(body)" in relay
    assert "apply-content-edit.yml/dispatches" in relay
    assert "skipDispatch: true" in relay
    assert "permissions:\n  contents: write" in workflow
    assert "data/content-overrides.json" in workflow
    assert "python -m indexer.content_edit_worker" in workflow
    assert "python -m pytest" not in workflow
    assert "pip install -r requirements.txt" not in workflow
    assert '"Apply indexed content edit"' in deploy
    assert "stableContentJSON(override.content)===expected" in (root / "js" / "app.js").read_text(encoding="utf-8")

    _validate_public_content({"overallSummary": "공개 가능한 요약"})
    with pytest.raises(ValueError, match="원문 전문"):
        _validate_public_content({"rawText": "공개 금지"})
    with pytest.raises(ValueError, match="인증 정보"):
        _validate_public_content({"geminiApiKey": "공개 금지"})


def test_lightweight_content_edit_worker_updates_only_public_override(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "content-overrides.json").write_text('{"schemaVersion":1,"byDriveFileId":{}}', encoding="utf-8")
    bundle = SimpleNamespace(
        manifest={"kind": "content-edit"},
        entries=[{"driveFileId": "drive-file-123", "content": {"overallSummary": "고친 내용"}}],
    )
    result = apply_content_edit(tmp_path, bundle)
    saved = json.loads((data / "content-overrides.json").read_text(encoding="utf-8"))
    assert result["status"] == "COMPLETE"
    assert saved["byDriveFileId"]["drive-file-123"]["content"]["overallSummary"] == "고친 내용"
