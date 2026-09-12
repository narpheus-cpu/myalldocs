from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_beginner_has_double_click_entrypoint_and_plain_guide():
    assert (ROOT / "시작하기.cmd").is_file()
    assert (ROOT / "초보자-안내서.md").is_file()
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "시작하기.cmd" in readme
    assert "초보자-안내서.md" in readme


def test_setup_wizard_uploads_secrets_without_writing_credential_files():
    source = (ROOT / "setup" / "beginner-setup.ps1").read_text(encoding="utf-8-sig")
    assert "Read-Host 'GEMINI API Key" in source
    assert "-AsSecureString" in source
    assert "gh secret set GEMINI_API_KEY" in source
    assert "gh secret set GOOGLE_SERVICE_ACCOUNT_JSON" in source
    assert "Set-Content" not in source
    assert "Out-File" not in source


def test_setup_wizard_rejects_private_repository_and_starts_first_run():
    source = (ROOT / "setup" / "beginner-setup.ps1").read_text(encoding="utf-8-sig")
    assert "if ($repoInfo.isPrivate)" in source
    assert "workflow', 'run', 'index-books.yml'" in source
    assert "folder_id=" in source


def test_setup_wizard_preserves_existing_remote_and_never_force_pushes():
    source = (ROOT / "setup" / "beginner-setup.ps1").read_text(encoding="utf-8-sig")
    assert "New-ConnectedWorkingCopy" in source
    assert "git clone --branch main" in source
    assert "(Join-Path $SourceRoot 'data')" in source
    assert "--force" not in source
