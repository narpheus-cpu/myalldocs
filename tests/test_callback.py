from __future__ import annotations

import json

import indexer.callback as callback


class FakeResponse:
    def __init__(self, body: dict, status: int = 200):
        self.status = status
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.body


def test_completion_callback_requires_positive_email_confirmation():
    original = callback.urllib.request.urlopen
    callback.urllib.request.urlopen = lambda request, timeout: FakeResponse({"ok": False, "error": "Mail permission required"})
    try:
        try:
            callback.send_completion_callback("https://example.invalid", "secret", {"status": "COMPLETE"})
        except RuntimeError as exc:
            assert "Mail permission required" in str(exc)
        else:
            raise AssertionError("expected callback rejection")
    finally:
        callback.urllib.request.urlopen = original


def test_completion_callback_accepts_confirmed_delivery():
    original = callback.urllib.request.urlopen
    callback.urllib.request.urlopen = lambda request, timeout: FakeResponse({"ok": True, "emailSent": True})
    try:
        result = callback.send_completion_callback("https://example.invalid", "secret", {"status": "COMPLETE"})
        assert result["emailSent"] is True
    finally:
        callback.urllib.request.urlopen = original
