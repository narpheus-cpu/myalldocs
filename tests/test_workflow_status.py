from indexer.workflow_status import build_status


def test_preflight_status_replaces_stale_queue_and_keeps_folder():
    status = build_status("start", {
        "GITHUB_RUN_ID": "123",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_SERVER_URL": "https://github.com",
        "INPUT_FOLDER_ID": "folder-id",
    })
    assert status["status"] == "RUNNING"
    assert status["phase"] == "PREPARING"
    assert status["folderId"] == "folder-id"
    assert status["runUrl"].endswith("/owner/repo/actions/runs/123")


def test_preflight_failure_is_terminal_not_queued():
    status = build_status("error", {"INPUT_FOLDER_ID": "folder-id"})
    assert status["status"] == "ERROR"
    assert status["allTargetsComplete"] is False
    assert "인덱싱은 시작되지 않았습니다" in status["message"]
