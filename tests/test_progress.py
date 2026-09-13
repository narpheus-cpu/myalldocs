import json
from pathlib import Path

from indexer.progress import ProgressReporter, _safe_status
from indexer.runtime_secret import KEY_PATTERN


ROOT = Path(__file__).resolve().parents[1]


def test_progress_relay_has_allowlist_and_never_sends_source_text():
    sent = []
    reporter = ProgressReporter("https://example.invalid/relay", "shared-secret", sender=lambda url, body: sent.append((url, body)), clock=lambda: 10)
    assert reporter.emit({"status": "RUNNING", "currentFileName": "book.epub", "sourceText": "private original"}, force=True)
    payload = json.loads(sent[0][1])
    assert payload["progress"]["currentFileName"] == "book.epub"
    assert "sourceText" not in payload["progress"]
    assert payload["callbackSecret"] == "shared-secret"


def test_progress_relay_throttles_non_forced_updates():
    sent = []
    reporter = ProgressReporter("https://example.invalid/relay", "secret", min_interval_seconds=3, sender=lambda url, body: sent.append(body), clock=lambda: 10)
    assert reporter.emit({"status": "RUNNING"})
    assert not reporter.emit({"status": "RUNNING"})
    assert len(sent) == 1


def test_runtime_key_validation_rejects_newlines():
    assert KEY_PATTERN.fullmatch("A" * 30)
    assert not KEY_PATTERN.fullmatch("A" * 30 + "\nINJECTED=value")


def test_indexing_page_has_key_editor_monitor_and_visible_picker_errors():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    for element_id in ("gemini-key", "save-api-key", "connect-monitor", "overall-progress", "activity-log", "setup-alert"):
        assert f'id="{element_id}"' in html
    for phrase in ("update-api-key", "ANALYZING_CHUNK", "폴더 선택 실패", "Google Picker 응답 시간이 초과"):
        assert phrase in script
    assert "pickerConfigurationIssues" in script
    assert "oauthConfigurationIssues" in script
    assert ".setAppId(config.googleCloudProjectNumber)" in script
    assert "localStorage" not in script
    assert "sessionStorage" not in script
    assert "public-config.js?v=" in html
    assert "js/app.js?v=" in html
    assert "js/app.js?v=20260913-index-dashboard" in html


def test_library_is_a_board_list_with_integrated_txt_download():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    assert "전체 도서 목록" in html
    assert 'id="book-row-template"' in html
    assert 'id="book-card-template"' not in html
    assert "새로고침하거나 닫아도 계속됩니다" in html
    for phrase in ("downloadBook", "saveIntegratedText", "summary.json", "analysis.json", "timeline.json", "relationships.json", "chunks.json", "\\ufeff"):
        assert phrase in script
    assert "검색어를 입력하면 결과가 여기에 표시됩니다" in script


def test_monochrome_reader_controls_and_drive_only_raw_chunk_popup():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    source_reader = (ROOT / "js" / "source-reader.js").read_text(encoding="utf-8")
    styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
    for element_id in ("font-smaller", "font-larger", "line-tighter", "line-looser", "toggle-evidence", "chunk-select", "chunk-dialog", "copy-chunk"):
        assert f'id="{element_id}"' in html
    for phrase in ("loadDriveChunks", "openSelectedChunk", "navigator.clipboard.writeText", "sourceChunks:new Map", "appendEvidence", "첫 번째"):
        assert phrase in script
    assert "www.googleapis.com/drive/v3/files/" in source_reader
    assert "Authorization: `Bearer ${accessToken}`" in source_reader
    assert "text/plain" not in source_reader  # no source text is embedded in the public bundle
    assert "Noto Serif" not in styles and "Georgia" not in styles
    assert "#bd4a2f" not in styles.casefold() and "#24483b" not in styles.casefold()
    assert ".detail-header {" in styles and "background: #fff" in styles
    assert ".panel {" in styles
    assert ".data-item" not in styles


def test_report_renderer_hides_description_label_and_uses_korean_sections():
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    prompts = (ROOT / "indexer" / "prompts.py").read_text(encoding="utf-8")
    assert 'if(k==="description"||k==="keyPoints"){appendValue' in script
    assert 'description:"설명"' not in script
    assert '`${chunkId}. ${ordinal(chunkId)} 구간' in script
    assert '`${index+1}. ${title||`${ordinal(sourceId)} 구간`}`' in script
    assert "summaryLong은 반드시 다음과 같은 한국어 마크다운 개조식 요약보고서" in prompts
    assert "번호 제목과 하이픈 목록" in prompts


def test_uncertainty_flag_is_hidden_and_prompt_formats_are_manageable():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    formats = (ROOT / "js" / "prompt-formats.js").read_text(encoding="utf-8")
    for element_id in ("prompt-format-select", "manage-formats", "format-list", "edit-format", "add-format", "delete-format", "format-name", "format-instruction"):
        assert f'id="{element_id}"' in html
    for phrase in ("composeCopyText", "ondragstart", "ondrop", "draggedFormatId", "savePromptFormats"):
        assert phrase in script
    assert '["chunkId","charCount","position","uncertain"]' in script
    assert '["chunkId","charCount","position","title","uncertain"]' in script
    assert 'return prompt + separator + source' in formats
    assert 'const separator = /[:：]\\s*$/.test(prompt) ? " " : ": ";' in formats
    assert "indexedDB.open" in formats
    assert "localStorage" not in formats and "sessionStorage" not in formats


def test_indexing_dashboard_and_verified_key_status_are_clear():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
    for element_id in ("api-key-state", "api-key-mask", "api-key-updated", "api-key-use"):
        assert f'id="{element_id}"' in html
    for phrase in ("renderApiKeyStatus", "서버 저장을 확인했습니다", "다음 인덱싱부터 새 키 사용", "현재 작업", "모델 및 재시도", "처리 결과", "무료 사용량"):
        assert phrase in script
    for selector in (".dashboard-card", ".status-dashboard", ".status-group", ".key-verification"):
        assert selector in styles


def test_reader_uses_compact_actions_and_hides_key_points_heading():
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
    assert 'k==="description"||k==="keyPoints"' in script
    assert 'keyPoints:""' in script
    assert ".detail-content h3 + h2" in styles
    assert ".detail-actions a, .detail-actions button" in styles
    assert "font-size: 11px" in styles


def test_metadata_editor_persists_manual_override_through_authorized_relay():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    relay = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    for element_id in ("metadata-dialog", "metadata-title", "metadata-author", "metadata-genre", "metadata-profile", "save-metadata"):
        assert f'id="{element_id}"' in html
    for phrase in ("작품 정보 편집", 'route:"update-metadata"', "applyMetadataOverride", "data/metadata-overrides.json"):
        assert phrase in script
    for phrase in ("updateMetadata_", "assertAuthorizedUser_", "assertFileWithinRoot_", "data/metadata-overrides.json", "document.byDriveFileId[driveFileId] = override"):
        assert phrase in relay


def test_relay_and_workflow_connect_saved_key_and_live_progress():
    relay = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "index-books.yml").read_text(encoding="utf-8")
    for phrase in ("update-api-key", "runtime-key", "handleProgress_", "oauth2/v3/userinfo", "LIVE_STATUS_JSON"):
        assert phrase in relay
    assert "MailApp.sendEmail" in relay
    assert "GmailApp" not in relay
    assert "mimeType === 'text/plain'" in relay
    assert "python -m indexer.runtime_secret" in workflow


def test_duplicate_dispatch_is_blocked_in_browser_relay_and_workflow_checkout_is_fresh():
    relay = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "index-books.yml").read_text(encoding="utf-8")
    assert "LockService.getScriptLock" in relay
    assert "alreadyRunning: true" in relay
    assert "state.dispatching" in script
    assert "ref: ${{ github.ref_name }}" in workflow


def test_index_completion_explicitly_triggers_pages_and_email_can_be_retried():
    pages = (ROOT / ".github" / "workflows" / "deploy-pages.yml").read_text(encoding="utf-8")
    retry = (ROOT / ".github" / "workflows" / "retry-completion-email.yml").read_text(encoding="utf-8")
    assert 'workflows: ["Index books"]' in pages
    assert "types: [completed]" in pages
    assert "python -m indexer.notify" in retry


def test_safe_status_excludes_unknown_fields():
    result = _safe_status({"status": "RUNNING", "apiRequestAttempts": 3, "apiSuccessfulRequests": 1, "attemptedModels": ["gemini-free"], "apiKey": "never", "rawText": "never"})
    assert result["status"] == "RUNNING"
    assert result["apiRequestAttempts"] == 3
    assert result["apiSuccessfulRequests"] == 1
    assert result["attemptedModels"] == ["gemini-free"]
    assert "apiKey" not in result and "rawText" not in result


def test_monitor_distinguishes_service_pause_and_real_completion_progress():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    assert 'id="activity-log" class="activity-log"' in html
    assert "<ol id=\"activity-log\"" not in html
    for phrase in ("PAUSED_SERVICE_UNAVAILABLE", "GEMINI_RETRY", "MODEL_FALLBACK", "MODEL_COOLDOWN", "MODEL_CYCLE_RESTART", "GEMINI_PACING", "apiRequestAttempts", "Gemini 호출", "state.lastStatus", "saved.errors", "data/job-status.json?status="):
        assert phrase in script
    assert "resolved/total*100" in script
    assert "index/total*100" not in script
