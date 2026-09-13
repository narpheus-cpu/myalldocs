from __future__ import annotations

import json
import os
import re
import urllib.request


KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{20,300}$")


def fetch_runtime_key(url: str, secret: str) -> str | None:
    return fetch_runtime_context(url, secret, "").get("geminiApiKey") or None


def fetch_runtime_context(url: str, secret: str, selection_id: str = "") -> dict:
    if not url or not secret:
        return {}
    body = json.dumps({"route": "runtime-key", "callbackSecret": secret, "selectionId": selection_id}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "text/plain;charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    value = str(data.get("geminiApiKey", "")).strip() if data.get("ok") else ""
    selected = data.get("selectedFileIds", []) if data.get("ok") else []
    return {
        "geminiApiKey": value if KEY_PATTERN.fullmatch(value) else "",
        "selectedFileIds": [str(item) for item in selected if isinstance(item, str) and re.fullmatch(r"[A-Za-z0-9_-]{10,200}", item)],
    }


def main() -> int:
    selection_id = os.getenv("INPUT_SELECTION_ID", "")
    try:
        context = fetch_runtime_context(
            os.getenv("APPS_SCRIPT_CALLBACK_URL", ""),
            os.getenv("APPS_SCRIPT_CALLBACK_SECRET", ""),
            selection_id,
        )
    except Exception as exc:
        if selection_id:
            print(f"Selected Drive queue unavailable; refusing to index the entire folder: {type(exc).__name__}")
            return 1
        print(f"User-updated Gemini key unavailable; existing GitHub Secret remains active: {type(exc).__name__}")
        return 0
    destination = os.getenv("GITHUB_ENV", "")
    if not destination:
        raise RuntimeError("GITHUB_ENV is unavailable")
    key = context.get("geminiApiKey", "")
    selected = context.get("selectedFileIds", [])
    if selection_id and not selected:
        print("Selected Drive queue is empty or expired; refusing to index the entire folder.")
        return 1
    with open(destination, "a", encoding="utf-8") as handle:
        if key:
            print(f"::add-mask::{key}")
            handle.write(f"GEMINI_API_KEY={key}\n")
        if selection_id:
            handle.write(f"SELECTED_FILE_IDS_JSON={json.dumps(selected, separators=(',', ':'))}\n")
    print("User-updated Gemini key loaded for this run." if key else "No user-updated Gemini key; existing GitHub Secret remains active.")
    if selection_id:
        print(f"Selected Drive queue loaded: {len(selected)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
