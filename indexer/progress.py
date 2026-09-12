from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable


LOG = logging.getLogger("book-indexer.progress")


def _send(url: str, body: bytes) -> None:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "text/plain;charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if response.status >= 300:
            raise RuntimeError(f"progress relay returned HTTP {response.status}")


class ProgressReporter:
    """Best-effort live progress relay; indexing never fails because the UI is offline."""

    def __init__(
        self,
        url: str = "",
        secret: str = "",
        min_interval_seconds: float = 3.0,
        sender: Callable[[str, bytes], None] = _send,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self.secret = secret
        self.min_interval_seconds = min_interval_seconds
        self.sender = sender
        self.clock = clock
        self.last_sent_at = float("-inf")

    @classmethod
    def from_env(cls) -> "ProgressReporter":
        return cls(
            os.getenv("APPS_SCRIPT_CALLBACK_URL", ""),
            os.getenv("APPS_SCRIPT_CALLBACK_SECRET", ""),
        )

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.secret)

    def emit(self, status: dict[str, Any], force: bool = False) -> bool:
        if not self.enabled:
            return False
        now = self.clock()
        if not force and now - self.last_sent_at < self.min_interval_seconds:
            return False
        safe = _safe_status(status)
        body = json.dumps(
            {
                "route": "progress",
                "callbackSecret": self.secret,
                "progress": safe,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        try:
            self.sender(self.url, body)
        except Exception as exc:  # monitoring must never interrupt book processing
            LOG.warning("Live progress delivery skipped: %s", exc)
            return False
        self.last_sent_at = now
        return True


def _safe_status(status: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "status", "phase", "message", "model", "currentFileName",
        "currentFileIndex", "totalFiles", "currentChunk", "totalChunks",
        "complete", "skipped", "failed", "metadataReview", "processedChunks",
        "apiRequests", "apiSuccessfulRequests", "apiRequestAttempts", "apiFailedAttempts", "inputTokens", "outputTokens", "driveQuotaUnits",
        "driveDownloadedBytes", "startedAt", "finishedAt", "allTargetsComplete",
        "attemptedModels", "modelSwitchCount", "lastModelError",
        "lastHttpStatus", "retryAttempt", "retryMaxAttempts", "retryDelaySeconds", "previousModel",
    }
    result = {key: value for key, value in status.items() if key in allowed}
    result["updatedAt"] = datetime.now(timezone.utc).isoformat()
    return result
