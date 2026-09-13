from __future__ import annotations

import json


SOURCE_RULES = """절대 규칙:
- 제공된 원문과 명시된 로컬 evidence만 사용한다.
- 외부 지식, 기억, 웹 정보를 보충하지 않는다.
- 원문에 없는 사실, 인물, 사건, 개념, 인용문을 만들지 않는다.
- 사실 추출과 해석을 구분하고, 불확실하면 uncertain=true로 표시한다.
- 모든 분석 항목에는 가능한 sourceChunkIds를 붙인다.
- 독자에게 보이는 제목과 문장은 모두 자연스러운 한국어로 작성한다.
- 사람이 읽는 내용에 JSON 필드명, chunk ID, sourceChunkIds 같은 내부 명칭을 노출하지 않는다.
- description 필드는 데이터 구조상 사용할 수 있지만, 내용 앞에 '설명'이라는 항목명을 반복해서 쓰지 않는다.
"""


def classify_document(excerpts: list[dict]) -> str:
    return f"""{SOURCE_RULES}
아래 원문 발췌만으로 문서 유형을 분류하라.
허용 유형: fiction, drama, poetry, academic, philosophy, history_biography,
science_technical, essay_general_nonfiction, practical_manual, mixed_anthology, unknown.
JSON 필드: documentType, genre, topicTags(array), confidence(0..1), reasons(array), uncertain(boolean).
genre는 반드시 원문 내용에서 추론한 구체적인 1차 장르 한 개를 자연스러운 한국어로 작성한다.
genre를 빈 문자열, unknown, 미분류로 쓰지 말고, novel 같은 영어 분류어도 쓰지 않는다. 요리책이면 '요리'처럼 문서 유형보다 구체적인 내용 장르를 우선한다.
topicTags에는 원문 내용에서 직접 도출한 주제·대상·형식 태그를 3~8개까지 한국어로 작성하고 숫자만으로 된 태그는 만들지 않는다.
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
summary와 keyPoints는 짧고 구체적인 개조식 보고서 재료로 작성한다. 한 항목에는 한 가지 사실이나 해석만 담는다.
JSON 필드: chunkId, summary, keyPoints(array), analysis(object; 각 key는 배열), uncertainties(array).
각 분석 항목은 description과 sourceChunkIds=[{chunk['chunkId']}]를 포함한다.
{chunk['chunkId']}번째 원문 구간:
{chunk['text']}"""


def synthesize(partials: list[dict], profile: dict, final: bool) -> str:
    stage = "최종 통합" if final else "중간 계층 통합"
    return f"""{SOURCE_RULES}
{stage} 단계다. 아래 구간 분석만 통합하고 새로운 사실을 추가하지 않는다.
분석 프로필: {json.dumps(profile, ensure_ascii=False)}
중복은 합치되 시간적 변화, 논증 흐름, 인과관계를 보존한다.
JSON 필드: summaryShort, summaryLong, sections(array), analysis(object), relationships(array), timeline(array), uncertainties(array).
모든 세부 항목의 sourceChunkIds를 합쳐 추적 가능하게 유지한다.
summaryLong은 반드시 다음과 같은 한국어 마크다운 개조식 요약보고서로 작성한다.
1. 핵심 사건이나 논점 제목
- 구체적인 사실 또는 주장.
- 다음 사실 또는 변화.
  - 필요한 경우에만 하위 항목.
2. 다음 핵심 사건이나 논점 제목
- 구체적인 사실 또는 주장.
번호 제목과 하이픈 목록을 사용하고, 긴 산문 문단으로 작성하지 않는다.
summaryLong은 sections를 그대로 다시 나열하지 말고, 여러 구간의 내용을 연관된 사건·논점·변화 단위로 재분류하여 다시 요약한다.
summaryShort보다 훨씬 풍부해야 하지만 sections 전체보다 분명히 짧아야 하며, 원문 구간 수와 일대일로 맞춘 목차를 만들지 않는다.
summaryShort는 책 전체를 빠르게 파악할 수 있는 압축 요약이고, summaryLong은 그보다 풍부한 구조화 요약이며, sections는 각 원문 구간별 기록이라는 세 단계의 차이를 지킨다.
sections의 각 항목도 title, summary 또는 bullets, sourceChunkIds를 사용하여 같은 순서와 위계를 보존한다.
'설명', 'description', 'chunk ID' 같은 내부 항목명은 사람이 읽는 문장에 쓰지 않는다.
구간 분석:
{json.dumps(partials, ensure_ascii=False)}"""
