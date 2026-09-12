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
    assert "js/app.js?v=20260913-library-list" in html


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
    for phrase in ("PAUSED_SERVICE_UNAVAILABLE", "GEMINI_RETRY", "MODEL_FALLBACK", "apiRequestAttempts", "Gemini 호출 시도", "state.lastStatus", "saved.errors", "data/job-status.json?status="):
        assert phrase in script
    assert "resolved/total*100" in script
    assert "index/total*100" not in script
