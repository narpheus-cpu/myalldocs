from __future__ import annotations

import argparse
import logging
import os
import sys

from indexer.config import Settings
from indexer.drive_client import DriveClient
from indexer.gemini_client import GeminiClient
from indexer.model_selector import NoSupportedModel
from indexer.pipeline import IndexPipeline
from indexer.rate_limiter import RateLimiter


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Index TXT/EPUB books from Google Drive")
    result.add_argument("--folder-id", required=True)
    result.add_argument("--recursive", action=argparse.BooleanOptionalAction, default=True)
    result.add_argument("--force", action="store_true")
    result.add_argument("--profile", default=None)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings.load()
    try:
        drive = DriveClient(settings.secret("GOOGLE_SERVICE_ACCOUNT_JSON") or "")
        limiter = RateLimiter(settings.quota)
        gemini = GeminiClient(settings.secret("GEMINI_API_KEY") or "", settings.model_policy, limiter)
    except NoSupportedModel as exc:
        logging.error("%s", exc)
        from indexer.checkpoint import atomic_write_json
        atomic_write_json(settings.root / "data" / "job-status.json", {"status": "NO_SUPPORTED_MODEL", "message": str(exc)})
        return 2
    status = IndexPipeline(settings, drive, gemini).run(args.folder_id, args.recursive, args.force, args.profile)
    logging.info("Final status: %s", status["status"])
    return 0 if status["status"] in {"COMPLETE", "PAUSED_RATE_LIMIT"} else 1


if __name__ == "__main__":
    sys.exit(main())
