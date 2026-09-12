from __future__ import annotations

import json
import urllib.request


def send_completion_callback(url: str, secret: str, payload: dict) -> None:
    body = json.dumps({**payload, "callbackSecret": secret}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"Apps Script callback failed with HTTP {response.status}")
