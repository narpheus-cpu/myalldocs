from __future__ import annotations

import json
import urllib.request


def send_completion_callback(url: str, secret: str, payload: dict) -> dict:
    body = json.dumps({**payload, "callbackSecret": secret}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"Apps Script callback failed with HTTP {response.status}")
        raw = response.read().decode("utf-8", errors="replace")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Apps Script callback returned an invalid response") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        detail = str(result.get("error", "unknown Apps Script error"))[:300] if isinstance(result, dict) else "invalid response"
        raise RuntimeError(f"Apps Script callback rejected completion email: {detail}")
    if result.get("emailSent") is not True:
        raise RuntimeError("Apps Script callback did not confirm completion email delivery")
    return result
