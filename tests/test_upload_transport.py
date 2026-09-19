from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_browser_uses_server_upload_capabilities_instead_of_fixed_128kb_parts():
    source = (ROOT / "js" / "app.js").read_text(encoding="utf-8")
    upload = source[source.index("async function uploadPrivateFile"):source.index("function bytesToBase64")]
    assert "Number(capabilities.partBytes)" in upload
    assert "Math.min(2097152" in upload
    assert "capabilities.partDelayMs" in upload
    assert "uploadToken" in upload
    assert "!uploadToken,true" in upload


def test_apps_script_v2_authenticates_once_and_accepts_two_megabyte_parts():
    source = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    authorization = source.index("assertAuthorizedUser_(body);")
    assert source.index("body.route === 'upload-part' && body.uploadToken") < authorization
    assert source.index("body.route === 'upload-finish' && body.uploadToken") < authorization
    assert "uploadProtocolVersion: 2" in source
    assert "partBytes: 2097152" in source
    assert "bytes.length > 2097152" in source
    assert "record.uploadToken" in source


def test_apps_script_internal_queued_edits_forward_the_upload_session_token():
    source = (ROOT / "apps-script" / "Code.gs").read_text(encoding="utf-8")
    assert source.count("uploadToken: started.uploadToken") >= 4
