from __future__ import annotations

import json
from typing import Any


def prior_knowledge_prompt(entry: dict[str, Any]) -> str:
    safe = {
        "filename": str(entry.get("filename") or "")[:500],
        "relative_path": str(entry.get("relative_path") or entry.get("relativePath") or "")[:2000],
        "filename_raw": str(entry.get("filename_raw") or "")[:500],
        "filename_parts": [str(item)[:300] for item in (entry.get("filename_parts") or [])[:20]],
        "text_length": int(entry.get("text_length") or 0),
        "excerpt_start": str(entry.get("excerpt_start") or "")[:3500],
        "excerpt_middle": str(entry.get("excerpt_middle") or "")[:3500],
        "excerpt_late": str(entry.get("excerpt_late") or "")[:3500],
        "manual_identity": entry.get("manual_identity") if isinstance(entry.get("manual_identity"), dict) else None,
    }
    return f"""당신은 개인 서재의 도서 식별 및 독서 안내 데이터를 만드는 편집자다.

중요한 경계:
- 아래 발췌문은 작품명과 작가를 식별하는 데만 사용한다.
- 분석 콘텐츠는 해당 작품에 관해 모델이 사전학습으로 확실히 아는 정보만 사용한다.
- 인터넷 검색, 검색 그라운딩, 도구 호출을 하지 않는다.
- 판본·번역본·축약본 차이를 구별할 수 없으면 identityStatus를 NEEDS_METADATA_REVIEW로 둔다.
- 작품을 식별했더라도 작품 전체 지식이 부족하면 knowledgeSufficient=false로 하고 콘텐츠를 지어내지 않는다.
- identityStatus가 NEEDS_METADATA_REVIEW이거나 knowledgeSufficient=false이면 content의 문자열은 빈 문자열, 배열은 빈 배열로 둔다.
- 최초 생성에서는 sectionSummaries를 반드시 빈 배열로 둔다.
- 감상문, 근거 없는 작가 의도, 무관한 일반론을 쓰지 않는다.
- 사람에게 보이는 모든 내용은 한국어로 쓴다.

입력:
{json.dumps(safe, ensure_ascii=False)}

다음 필드를 가진 JSON 객체 하나만 출력한다.
{{
  "identityDecision": {{
    "title": "작품명",
    "author": "작가명",
    "confidence": 0.0,
    "identityStatus": "CONFIRMED 또는 INFERRED 또는 NEEDS_METADATA_REVIEW",
    "knowledgeSufficient": true,
    "reason": "간결한 판단 이유"
  }},
  "identity": {{
    "title": "작품명",
    "author": "작가명",
    "genre": "한국어 장르",
    "tags": ["한국어 태그"],
    "workProfile": {{"primary": "fiction|drama|poetry|academic|philosophy|history_biography|science_technical|essay_general_nonfiction|practical_manual|mixed_anthology|unknown", "secondary": []}}
  }},
  "content": {{
    "authorIntroduction": "작품 이해에 필요한 범위의 작가 소개",
    "oneLineSummary": "한 문장 요약",
    "overallSummary": "줄거리 또는 논지의 전체 흐름을 놓치지 않도록 구조화한 상세 요약",
    "sectionSummaries": [],
    "keyEntities": {{"type": "characters|concepts|persons|organizations|principles|systems|mixed", "items": [{{"name": "", "role": "", "description": ""}}]}},
    "setting": {{
      "internal": {{"time": "", "places": [], "socialContext": "", "historicalContext": ""}},
      "external": {{"historicalContext": "", "publicationContext": ""}}
    }},
    "adaptiveAnalysis": [{{"key": "표준 영문 키 또는 custom", "title": "한국어 제목", "content": "구조화된 분석 내용"}}]
  }}
}}"""


def valid_prior_result(value: Any, threshold: float) -> tuple[bool, str]:
    if not isinstance(value, dict):
        return False, "응답이 객체가 아닙니다."
    decision = value.get("identityDecision")
    identity = value.get("identity")
    content = value.get("content")
    if not all(isinstance(item, dict) for item in (decision, identity, content)):
        return False, "필수 객체가 없습니다."
    status = str(decision.get("identityStatus") or "")
    try:
        confidence = float(decision.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    if status == "NEEDS_METADATA_REVIEW" or confidence < threshold:
        return False, "작품 식별 확인이 필요합니다."
    if decision.get("knowledgeSufficient") is not True:
        return False, "모델의 작품 지식이 충분하지 않습니다."
    if not str(identity.get("title") or "").strip() or not str(identity.get("author") or "").strip():
        return False, "작품명 또는 작가명이 없습니다."
    if content.get("sectionSummaries") not in ([], None):
        return False, "최초 구간별 요약은 빈 배열이어야 합니다."
    return True, ""
