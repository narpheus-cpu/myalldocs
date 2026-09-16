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
    assert KEY_PATTERN.fullmatch("AQ." + "A" * 30)
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
    assert 'localStorage.setItem("GEMINI' not in script
    assert "sessionStorage" not in script
    assert "public-config.js?v=" in html
    assert "js/app.js?v=" in html
    assert "js/app.js?v=20260916-persistence-delete1" in html


def test_drive_library_lists_raw_files_and_dispatches_only_checked_items():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    drive_library = (ROOT / "js" / "drive-library.js").read_text(encoding="utf-8")
    relay = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "index-books.yml").read_text(encoding="utf-8")
    for element_id in ("drive-view", "drive-pick-folder", "drive-select-all", "drive-index-selected", "drive-file-list", "drive-file-row-template"):
        assert f'id="{element_id}"' in html
    for phrase in ("dispatchSelectedDriveFiles", 'route:"dispatch-selected"', "indexedBookForDriveFile", '"[인덱싱 완료]"', "showDriveFile", "showRawDriveChunk"):
        assert phrase in script
    assert 'item.mimeType === TEXT_MIME || item.mimeType === EPUB_MIME' in drive_library
    assert "application/vnd.google-apps.document" not in drive_library
    assert "folderNameTag" in drive_library and "replace(/\\d+/g" in drive_library
    assert "INDEX_SELECTION_" in relay and "normalizeFileIds_" in relay
    assert "selection_id:" in workflow and "--file-ids-json" in workflow


def test_library_is_a_board_list_with_indexed_time():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    assert "전체 도서 목록" in html
    assert 'id="book-row-template"' in html
    assert 'id="book-card-template"' not in html
    assert "새로고침하거나 닫아도 계속됩니다" in html
    assert 'class="indexed-time"' in html
    assert "인덱싱 일시" in html
    assert "download-book" not in html and "downloadBook" not in script
    for phrase in ("book.updatedAt||book.indexedAt", "saveIntegratedText", "summary.json", "analysis.json", "timeline.json", "relationships.json", "chunks.json", "\\ufeff"):
        assert phrase in script
    assert "조건에 맞는 도서" in script


def test_monochrome_reader_controls_and_drive_only_raw_chunk_popup():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    source_reader = (ROOT / "js" / "source-reader.js").read_text(encoding="utf-8")
    styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
    for element_id in ("font-smaller", "font-larger", "line-tighter", "line-looser", "toggle-evidence", "chunk-select", "chunk-dialog", "copy-chunk"):
        assert f'id="{element_id}"' in html
    for phrase in ("loadDriveChunks", "openSelectedChunk", "navigator.clipboard.writeText", "sourceChunks:new Map", "appendEvidence", "sectionLabel"):
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
    assert "기타 정보" not in script
    assert '`${sectionLabel(chunkId)}' in script
    assert 'function sectionLabel(value){return`제${Number(value).toLocaleString("ko-KR")}구간`}' in script
    assert "열여섯 번째 구간" not in script
    assert "summaryLong은 반드시 다음과 같은 한국어 마크다운 개조식 요약보고서" in prompts
    assert "여러 구간의 내용을 연관된 사건·논점·변화 단위로 재분류" in prompts
    assert 'detailedSummary:data.summary?.summaryLong' in script
    assert "번호 제목과 하이픈 목록" in prompts


def test_uncertainty_flag_is_hidden_and_prompt_formats_are_manageable():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    formats = (ROOT / "js" / "prompt-formats.js").read_text(encoding="utf-8")
    for element_id in ("prompt-format-select", "copy-chunk", "manage-formats", "quick-edit-format", "quick-add-format", "quick-delete-format", "format-list", "edit-format", "add-format", "delete-format", "format-name", "format-instruction"):
        assert f'id="{element_id}"' in html
    for phrase in ("composeCopyText", "ondragstart", "ondrop", "draggedFormatId", "savePromptFormats"):
        assert phrase in script
    assert '["chunkId","charCount","position","uncertain"]' in script
    assert '["chunkId","charCount","position","title","uncertain"]' in script
    assert 'return prompt + separator + source' in formats
    assert 'const separator = /[:：]\\s*$/.test(prompt) ? " " : ": ";' in formats
    assert 'updateCopyTrigger()' in script
    assert 'button.textContent=name' in script
    assert html.index('id="prompt-format-select"') < html.index('id="copy-chunk"') < html.index('id="manage-formats"')
    assert "indexedDB.open" in formats
    assert "localStorage" not in formats and "sessionStorage" not in formats
    open_chunk = script.index("async function openSelectedChunk")
    copy_chunk = script.index("async function copyChunk")
    source = script[open_chunk:copy_chunk]
    assert source.index("await getAccessToken()") < source.index("dialog.showModal()")


def test_completed_identical_source_is_skipped_independent_of_metadata_and_versions():
    pipeline = (ROOT / "indexer" / "pipeline.py").read_text(encoding="utf-8")
    storage = (ROOT / "indexer" / "storage.py").read_text(encoding="utf-8")
    assert "completed_manifest_by_sha256(checksum)" in pipeline
    assert "작품명·작가명과 관계없이 이미 완료된 동일 원문" in pipeline
    assert 'existing.get("versions") == versions' not in pipeline
    assert "manifest.get(\"source\", {}).get(\"sha256\") == checksum" in storage


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


def test_detail_tabs_follow_canonical_json_and_all_indexed_content_is_editable():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    relay = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    canonical = (ROOT / "indexer" / "canonical.py").read_text(encoding="utf-8")
    for phrase in ("canonicalTabDefinitions", "adaptiveTabLabel", "adaptive:${index}", "content:${key}", "tabDefinitions"):
        assert phrase in script
    for phrase in ('contentEdit.id="edit-indexed-content"', 'contentSave.id="save-indexed-content"', 'route:"update-book-content"', "renderContentEditor", "content-overrides.json"):
        assert phrase in script
    assert "body.route === 'update-book-content'" in relay
    assert "function updateBookContent_" in relay
    assert "validatePublicBookContent_" in relay
    assert "result[\"content\"][key] = deepcopy(value)" in canonical
    assert (ROOT / "data" / "content-overrides.json").exists()


def test_reader_has_whole_source_persistent_geometry_and_collapsible_controls():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "assets" / "styles.css").read_text(encoding="utf-8")
    assert 'whole.textContent="원문 전체"' in script
    assert 'all.textContent=chunks.length?"원문 전체"' in script
    assert 'id="reader-toolbar-details"' in html and "<summary>구간·보기 설정</summary>" in html
    assert 'id="chunk-select" aria-label="현재 원문 구간" hidden' in html
    assert '<label for="chunk-select">원문 구간</label>' not in html
    assert "Google Drive 원문</p>" not in html
    for phrase in ("saveReaderWindowGeometry", "applyReaderWindowSettings", "width:state.reader.width", "toolbarOpen:state.reader.toolbarOpen"):
        assert phrase in script
    assert ".chunk-dialog[open]" in styles
    assert ".compact-actions" in styles


def test_full_source_pagination_yields_and_caches_instead_of_blocking_the_page():
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    for phrase in ("async function paginateReader", "paginationToken", "paginationCache", "cacheReaderPages", "pages.length%8===0", "requestAnimationFrame"):
        assert phrase in script
    assert "high=text.length" not in script


def test_library_filters_and_detailed_summary_layout_persist_and_render_as_sections():
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    for phrase in ("bookmap_library_filters_v1", "persistLibraryFilters", "restoreLibraryFilters", "libraryTimestamp", "pagehide"):
        assert phrase in script
    for phrase in ("bookmap_detail_reader_settings_v1", "restoreDetailReaderSettings", "saveDetailReaderSettings"):
        assert phrase in script
    assert script.index("restoreDetailReaderSettings();restoreLibraryFilters();updateReaderControls()") < script.index('fetchJSON("data/catalog.json")')
    assert 'window.addEventListener("pageshow"' in script
    assert '["summaryShort","summaryLong","detailedSummary","summary","authorIntroduction"]' in script


def test_reader_restores_last_chunk_and_page_after_refresh():
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    for phrase in ("loadReaderBookmark(manifest)", "bookmarkExists", "preferredOffset", "saveReaderBookmark()"):
        assert phrase in script
    assert "openSelectedChunk(true)" in script


def test_library_selected_books_can_be_safely_hidden_without_deleting_drive_source():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    relay = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    for element_id in ("library-select-all", "delete-selected-books", "library-selected-count", "library-delete-message"):
        assert f'id="{element_id}"' in html
    for phrase in ("selectedLibraryBookIds", "deleteSelectedBooks", 'route:"delete-books"', "data/deleted-books.json", "bookmap_locally_deleted_books_v1", "saveLocallyDeletedDriveFileIds()"):
        assert phrase in script
    for phrase in ("body.route === 'delete-books'", "function deleteBooks_", "assertFileWithinRoot_(driveFileId)", "data/deleted-books.json"):
        assert phrase in relay
    assert (ROOT / "data" / "deleted-books.json").exists()


def test_content_edits_are_immediate_locally_and_use_direct_commit_with_queue_fallback():
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    relay = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    for phrase in ("bookmap_pending_content_edits_v1", "savePendingContentEdits", "monitorBookContentSave", "이 브라우저에 저장됨"):
        assert phrase in script
    assert relay.index("updateBookContent_(body)") < relay.index("queueBookContentUpdate_(body)", relay.index("function handleBookContentUpdate_"))
    assert "directFallback" in relay


def test_metadata_editor_persists_manual_override_through_authorized_relay():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    relay = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    for element_id in ("metadata-dialog", "metadata-title", "metadata-author", "metadata-genre", "metadata-profile", "metadata-tags", "save-metadata"):
        assert f'id="{element_id}"' in html
    for phrase in ("작품 정보 편집", 'route:"update-metadata"', "applyMetadataOverride", "data/metadata-overrides.json"):
        assert phrase in script
    for phrase in ("updateMetadata_", "assertAuthorizedUser_", "assertFileWithinRoot_", "data/metadata-overrides.json", "document.byDriveFileId[driveFileId] = override"):
        assert phrase in relay
    assert "normalizeTags_(raw.tags)" in relay
    assert 'tags:uniqueTags($("#metadata-tags").value.split' in script


def test_relay_and_workflow_connect_saved_key_and_live_progress():
    relay = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "index-books.yml").read_text(encoding="utf-8")
    for phrase in ("update-api-key", "runtime-key", "handleProgress_", "oauth2/v3/userinfo", "LIVE_STATUS_JSON"):
        assert phrase in relay
    assert "sendEmail_(recipient" in relay
    assert "GmailApp" not in relay
    assert "mimeType === 'text/plain'" in relay
    assert "python -m indexer.runtime_secret" in workflow
    assert "python -m indexer.workflow_status start" in workflow
    assert "python -m indexer.workflow_status error" in workflow
    assert "verifyGitHubContentsWrite_" in relay
    assert "Contents: Read and write" in relay


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
    assert 'workflows: ["Index books", "Process private upload queue", "Apply indexed content edit"]' in pages
    assert "types: [completed]" in pages
    assert "python -m indexer.notify" in retry


def test_safe_status_excludes_unknown_fields():
    result = _safe_status({"status": "RUNNING", "folderId": "folder-1", "folderName": "책", "apiRequestAttempts": 3, "apiSuccessfulRequests": 1, "attemptedModels": ["gemini-free"], "apiKey": "never", "rawText": "never"})
    assert result["status"] == "RUNNING"
    assert result["apiRequestAttempts"] == 3
    assert result["apiSuccessfulRequests"] == 1
    assert result["attemptedModels"] == ["gemini-free"]
    assert result["folderId"] == "folder-1" and result["folderName"] == "책"
    assert "apiKey" not in result and "rawText" not in result


def test_current_folder_and_stale_queue_are_recovered_after_refresh():
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    relay = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    for phrase in ("syncFolderFromStatus", "setSelectedFolder", "reconcileQueuedStatus", "githubRepository", "folderName:state.selectedFolder.name"):
        assert phrase in script or phrase in (ROOT / "config" / "public-config.js").read_text(encoding="utf-8")
    for phrase in ("folderId","folderName","latestIndexRun_","previous[name]"):
        assert phrase in relay


def test_monitor_distinguishes_service_pause_and_real_completion_progress():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    assert 'id="activity-log" class="activity-log"' in html
    assert "<ol id=\"activity-log\"" not in html
    for phrase in ("PAUSED_SERVICE_UNAVAILABLE", "GEMINI_RETRY", "MODEL_FALLBACK", "MODEL_COOLDOWN", "MODEL_CYCLE_RESTART", "GEMINI_PACING", "apiRequestAttempts", "Gemini 호출", "state.lastStatus", "saved.errors", "data/job-status.json?status="):
        assert phrase in script
    assert "resolved/total*100" in script
    assert "index/total*100" not in script
