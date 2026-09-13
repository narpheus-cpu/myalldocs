import json

from indexer.main import parser
from indexer.runtime_secret import fetch_runtime_context


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps({
            "ok": True,
            "geminiApiKey": "AQ." + "A" * 30,
            "selectedFileIds": ["valid_drive_file_123", "bad id", 17],
        }).encode("utf-8")


def test_runtime_context_accepts_only_safe_selected_file_ids(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: _Response())
    result = fetch_runtime_context("https://example.invalid", "secret", "selection")
    assert result["geminiApiKey"].startswith("AQ.")
    assert result["selectedFileIds"] == ["valid_drive_file_123"]


def test_cli_accepts_selected_file_id_json_without_changing_folder_mode():
    args = parser().parse_args(["--folder-id", "folder", "--file-ids-json", '["one","two"]'])
    assert args.folder_id == "folder"
    assert args.file_ids_json == '["one","two"]'
