from __future__ import annotations

import json


SOURCE_RULES = """절대 규칙:
- 제공된 원문과 명시된 로컬 evidence만 사용한다.
- 외부 지식, 기억, 웹 정보를 보충하지 않는다.
- 원문에 없는 사실, 인물, 사건, 개념, 인용문을 만들지 않는다.
- 사실 추출과 해석을 구분하고, 불확실하면 uncertain=true로 표시한다.
- 모든 분석 항목에는 가능한 sourceChunkIds를 붙인다.
"""


def classify_document(excerpts: list[dict]) -> str:
    return f"""{SOURCE_RULES}
아래 원문 발췌만으로 문서 유형을 분류하라.
허용 유형: fiction, drama, poetry, academic, philosophy, history_biography,
science_technical, essay_general_nonfiction, practical_manual, mixed_anthology, unknown.
JSON 필드: documentType, genre, confidence(0..1), reasons(array), uncertain(boolean).
발췌:
{json.dumps(excerpts, ensure_ascii=False)}"""


def resolve_local_metadata(evidence: list[dict]) -> str:
    return f"""{SOURCE_RULES}
아래 evidence 후보만 비교하여 제목과 저자를 고른다. 후보에 없는 제목/저자를 만들지 않는다.
파일명과 폴더명은 틀릴 수 있고 낮은 우선순위다. OPF와 본문 표제부를 강하게 보되 단일 source를 절대시하지 않는다.
JSON 필드: title, author, confidence(0..1), rationale, selectedSources(array), conflictDetected(boolean).
evidence:
{json.dumps(evidence, ensure_ascii=False)}"""


def analyze_chunk(chunk: dict, profile: dict) -> str:
    return f"""{SOURCE_RULES}
분석 프로필: {json.dumps(profile, ensure_ascii=False)}
다음 한 구간을 고밀도로 분석하라. 줄거리 나열에 그치지 말고 프로필 dimensions를 채운다.
JSON 필드: chunkId, summary, keyPoints(array), analysis(object; 각 key는 배열), uncertainties(array).
각 분석 항목은 description과 sourceChunkIds=[{chunk['chunkId']}]를 포함한다.
원문 구간 ID {chunk['chunkId']}:
{chunk['text']}"""


def synthesize(partials: list[dict], profile: dict, final: bool) -> str:
    stage = "최종 통합" if final else "중간 계층 통합"
    return f"""{SOURCE_RULES}
{stage} 단계다. 아래 구간 분석만 통합하고 새로운 사실을 추가하지 않는다.
분석 프로필: {json.dumps(profile, ensure_ascii=False)}
중복은 합치되 시간적 변화, 논증 흐름, 인과관계를 보존한다.
JSON 필드: summaryShort, summaryLong, sections(array), analysis(object), relationships(array), timeline(array), uncertainties(array).
모든 세부 항목의 sourceChunkIds를 합쳐 추적 가능하게 유지한다.
구간 분석:
{json.dumps(partials, ensure_ascii=False)}"""
