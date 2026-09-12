from pathlib import Path


def test_repository_contains_no_private_key_or_hardcoded_gemini_secret():
    root=Path(__file__).resolve().parents[1]
    allowed={".py",".js",".json",".md",".yml",".html",".css",".gs",".txt"}
    text="\n".join(
        path.read_text(encoding="utf-8",errors="ignore")
        for path in root.rglob("*")
        if path.is_file() and path.suffix in allowed and not any(part.startswith(".") for part in path.relative_to(root).parts)
    )
    private_key_marker = "-----BEGIN " + "PRIVATE KEY-----"
    google_key_prefix = "AIza" + "Sy"
    assert private_key_marker not in text
    assert google_key_prefix not in text
