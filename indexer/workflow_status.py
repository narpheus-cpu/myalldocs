from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

from indexer.progress import ProgressReporter


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_status(kind: str, env: dict[str, str] | None = None) -> dict:
    values = env or os.environ
    run_id = str(values.get("GITHUB_RUN_ID", ""))
    repository = str(values.get("GITHUB_REPOSITORY", ""))
    server = str(values.get("GITHUB_SERVER_URL", "https://github.com")).rstrip("/")
    folder_id = str(values.get("INPUT_FOLDER_ID", ""))
    base = {
        "folderId": folder_id,
        "runId": run_id,
        "runUrl": f"{server}/{repository}/actions/runs/{run_id}" if repository and run_id else "",
        "currentFileName": "",
        "currentFileIndex": 0,
        "totalFiles": 0,
        "complete": 0,
        "skipped": 0,
        "failed": 0,
        "allTargetsComplete": False,
    }
    if kind == "start":
        return {
            **base,
            "status": "RUNNING",
            "phase": "PREPARING",
            "message": "GitHub 실행기가 시작되어 설정과 테스트를 확인하고 있습니다.",
            "startedAt": _now(),
        }
    if kind == "error":
        message = "GitHub 준비 또는 사전 테스트 단계에서 실패했습니다. 인덱싱은 시작되지 않았습니다."
        return {
            **base,
            "status": "ERROR",
            "phase": "ERROR",
            "message": message,
            "lastError": message,
            "finishedAt": _now(),
        }
    raise ValueError(f"unsupported workflow status: {kind}")


def main() -> int:
    kind = sys.argv[1] if len(sys.argv) > 1 else "start"
    status = build_status(kind)
    delivered = ProgressReporter.from_env().emit(status, force=True)
    print(f"Workflow status {kind}: {'delivered' if delivered else 'relay unavailable'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
