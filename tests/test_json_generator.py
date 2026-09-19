from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_json_generator_is_available_as_a_spa_tab():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    app = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    assert 'href="#json-generator" data-nav="json-generator"' in html
    assert 'id="json-generator-view"' in html
    assert 'key==="json-generator"?"json-generator"' in app
    assert 'initJsonGenerator()' in app


def test_json_generator_keeps_source_files_in_the_browser():
    source = (ROOT / "js" / "json-generator.js").read_text(encoding="utf-8")
    assert "file.arrayBuffer()" in source
    assert "application/x-ndjson" in source
    assert "URL.createObjectURL" in source
    assert "fetch(" not in source
    assert "XMLHttpRequest" not in source


def test_json_generator_includes_expected_identification_fields_and_epub_support():
    source = (ROOT / "js" / "json-generator.js").read_text(encoding="utf-8")
    for field in ("filename", "relative_path", "excerpt_start", "excerpt_middle", "excerpt_late"):
        assert field in source
    assert "window.JSZip.loadAsync" in source
    assert 'new TextDecoder("euc-kr")' in source
    assert "webkitRelativePath" in source
