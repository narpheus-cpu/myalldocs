from __future__ import annotations

import json
import os
from pathlib import Path

from indexer.callback import send_completion_callback
from indexer.config import ROOT, Settings


def main() -> int:
    status_path = ROOT / "data" / "job-status.json"
    with status_path.open("r", encoding="utf-8") as handle:
        status = json.load(handle)
    if status.get("status") != "COMPLETE" or status.get("allTargetsComplete") is not True:
        print("Completion callback skipped: target set is not fully COMPLETE.")
        return 0
    url = os.getenv("APPS_SCRIPT_CALLBACK_URL")
    secret = os.getenv("APPS_SCRIPT_CALLBACK_SECRET")
    if not url or not secret:
        print("Completion callback skipped: callback secrets are not configured.")
        return 0
    settings = Settings.load()
    send_completion_callback(url, secret, {
        **status,
        "event": "indexing-complete",
        "completionEmail": settings.raw.get("completionEmail", "narepheus@gmail.com"),
        "pagesUrl": os.getenv("GITHUB_PAGES_URL", ""),
    })
    print("Completion callback delivered.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
