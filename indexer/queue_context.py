from __future__ import annotations

import json
import hashlib
import os
import re
import time
import urllib.request


ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{10,200}$")


def service_account_email(raw: str) -> str:
    try:
        email = str(json.loads(raw or "{}").get("client_email") or "").strip().lower()
    except (TypeError, ValueError, json.JSONDecodeError):
        return ""
    return email if re.fullmatch(r"[^@\s]+@[^@\s]+\.iam\.gserviceaccount\.com", email) else ""


def fetch_queue_context(url: str, secret: str, worker_email: str = "") -> dict:
    if not url or not secret:
        return {}
    body = json.dumps({
        "route": "private-queue", "callbackSecret": secret,
        "serviceAccountEmail": worker_email,
    }).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "text/plain;charset=utf-8"}, method="POST")
    value = None
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                value = json.loads(response.read().decode("utf-8"))
            break
        except (TimeoutError, OSError) as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(2)
    if value is None:
        raise RuntimeError(f"Apps Script 대기열 조회 시간이 초과되었습니다: {type(last_error).__name__}")
    if not isinstance(value, dict) or value.get("ok") is not True:
        raise RuntimeError("Apps Script가 비공개 대기열을 제공하지 못했습니다.")
    manifest_id = str(value.get("manifestId") or "")
    return {"manifestId": manifest_id if ID_PATTERN.fullmatch(manifest_id) else ""}


def notify_queue_result(url: str, secret: str, manifest_id: str, status: str, summary: dict) -> None:
    result_payload = {
        "manifestId": manifest_id, "status": status, "summary": summary,
    }
    result_id = hashlib.sha256(json.dumps(result_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    body = json.dumps({
        "route": "queue-result", "callbackSecret": secret, "manifestId": manifest_id,
        "status": status, "allTargetsComplete": summary.get("allTargetsComplete") is True,
        "summary": summary, "resultId": result_id,
    }, ensure_ascii=False).encode("utf-8")
    last_error: Exception | None = None
    for attempt in range(3):
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "text/plain;charset=utf-8"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                value = json.loads(response.read().decode("utf-8"))
            if isinstance(value, dict) and value.get("ok") is True:
                return
            detail = str(value.get("error") or "")[:300] if isinstance(value, dict) else "응답 형식 오류"
            raise RuntimeError(f"Apps Script가 대기열 결과를 저장하지 못했습니다: {detail}")
        except (TimeoutError, OSError, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Apps Script 대기열 결과 통지가 3회 실패했습니다: {last_error}")


def main() -> int:
    try:
        context = fetch_queue_context(
            os.getenv("APPS_SCRIPT_CALLBACK_URL", ""),
            os.getenv("APPS_SCRIPT_CALLBACK_SECRET", ""),
            service_account_email(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")),
        )
    except Exception as exc:
        print(f"Private queue lookup failed safely: {type(exc).__name__}")
        return 1
    destination = os.getenv("GITHUB_ENV", "")
    if not destination:
        raise RuntimeError("GITHUB_ENV is unavailable")
    with open(destination, "a", encoding="utf-8") as handle:
        handle.write(f"PRIVATE_QUEUE_MANIFEST_ID={context.get('manifestId', '')}\n")
    print("Private queue is ready." if context.get("manifestId") else "Private queue is empty; no API request will be made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
