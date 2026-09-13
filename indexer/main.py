from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from indexer.config import Settings
from indexer.drive_client import DriveClient
from indexer.gemini_client import GeminiClient
from indexer.model_selector import NoSupportedModel
from indexer.pipeline import IndexPipeline
from indexer.progress import ProgressReporter
from indexer.rate_limiter import RateLimiter


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Index TXT/EPUB books from Google Drive")
    result.add_argument("--folder-id", required=True)
    result.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    result.add_argument("--force", action="store_true")
    result.add_argument("--profile", default=None)
    result.add_argument("--file-ids-json", default="", help="Optional JSON array of Drive file IDs selected in the web library")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.load()
    settings.assert_zero_cost()
    progress = ProgressReporter.from_env()
    try:
        drive = DriveClient(settings.secret("GOOGLE_SERVICE_ACCOUNT_JSON") or "", settings.raw.get("driveQuota", {}))
        limiter = RateLimiter(settings.quota)
        gemini = GeminiClient(settings.secret("GEMINI_API_KEY") or "", settings.model_policy, limiter)
    except NoSupportedModel as exc:
        logging.error("%s", exc)
        from indexer.checkpoint import atomic_write_json
        atomic_write_json(settings.root / "data" / "job-status.json", {"status": "NO_SUPPORTED_MODEL", "message": str(exc)})
        progress.emit({"status": "NO_SUPPORTED_MODEL", "phase": "MODEL_SELECTION", "message": str(exc)}, force=True)
        return 2
    selected_file_ids = None
    if args.file_ids_json:
        parsed_ids = json.loads(args.file_ids_json)
        if not isinstance(parsed_ids, list) or not all(isinstance(item, str) and item for item in parsed_ids):
            raise ValueError("--file-ids-json must be a JSON string array")
        selected_file_ids = list(dict.fromkeys(parsed_ids))
    status = IndexPipeline(settings, drive, gemini, progress=progress).run(args.folder_id, args.recursive, args.force, args.profile, selected_file_ids)
    logging.info("Final status: %s", status["status"])
    return 0 if status["status"] in {"COMPLETE", "PAUSED_RATE_LIMIT", "PAUSED_SERVICE_UNAVAILABLE"} else 1


if __name__ == "__main__":
    sys.exit(main())
