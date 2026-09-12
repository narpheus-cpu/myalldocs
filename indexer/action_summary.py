from __future__ import annotations

import json
import os
from pathlib import Path

from indexer.config import ROOT


def main() -> None:
    with (ROOT / "data" / "job-status.json").open("r", encoding="utf-8") as handle:
        status = json.load(handle)
    lines = [
        "## Book Indexer 결과",
        "",
        "| 항목 | 값 |",
        "|---|---:|",
        f"| 상태 | {status.get('status', '-')} |",
        f"| 대상 파일 | {status.get('totalFiles', 0)} |",
        f"| 완료 | {status.get('complete', 0)} |",
        f"| 건너뜀 | {status.get('skipped', 0)} |",
        f"| 실패 | {status.get('failed', 0)} |",
        f"| 메타데이터 검토 필요 | {status.get('metadataReview', 0)} |",
        f"| 처리 chunk | {status.get('processedChunks', 0)} |",
        f"| API 요청 | {status.get('apiRequests', 0)} |",
        f"| 입력/출력 token | {status.get('inputTokens', 0)} / {status.get('outputTokens', 0)} |",
        f"| Drive quota units | {status.get('driveQuotaUnits', 0)} |",
        f"| Drive 다운로드 bytes | {status.get('driveDownloadedBytes', 0)} |",
        f"| 모델 | {status.get('model', '-')} |",
    ]
    output = "\n".join(lines) + "\n"
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
