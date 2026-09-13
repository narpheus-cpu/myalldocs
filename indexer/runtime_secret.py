from __future__ import annotations

import json
import os
import re
import urllib.request


KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{20,300}$")


def fetch_runtime_key(url: str, secret: str) -> str | None:
    if not url or not secret:
        return None
    body = json.dumps({"route": "runtime-key", "callbackSecret": secret}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "text/plain;charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    value = str(data.get("geminiApiKey", "")).strip() if data.get("ok") else ""
    return value if KEY_PATTERN.fullmatch(value) else None


def main() -> int:
    try:
        key = fetch_runtime_key(
            os.getenv("APPS_SCRIPT_CALLBACK_URL", ""),
            os.getenv("APPS_SCRIPT_CALLBACK_SECRET", ""),
        )
    except Exception as exc:
        print(f"User-updated Gemini key unavailable; existing GitHub Secret remains active: {type(exc).__name__}")
        return 0
    if not key:
        print("No user-updated Gemini key; existing GitHub Secret remains active.")
        return 0
    destination = os.getenv("GITHUB_ENV", "")
    if not destination:
        raise RuntimeError("GITHUB_ENV is unavailable")
    print(f"::add-mask::{key}")
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write(f"GEMINI_API_KEY={key}\n")
    print("User-updated Gemini key loaded for this run.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
