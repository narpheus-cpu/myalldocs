# 서재 지도 — Google Drive 개인 도서 지식베이스

Google Drive의 TXT/EPUB 원본을 조금씩 분석해 GitHub의 구조화된 JSON으로 축적하고, GitHub Pages에서 검색·탐색하는 개인용 시스템입니다. 원본 파일은 Drive 밖으로 복제하지 않습니다. 분석 엔진은 GitHub Actions의 Python, 수동 실행 연결은 Google Picker와 Apps Script, 요약·분석은 현재 `google-genai` SDK를 사용합니다.

## 코딩을 전혀 모른다면 여기만 보세요

아래의 긴 설명을 먼저 읽거나 명령어를 입력할 필요가 없습니다.

1. 프로젝트 압축을 풉니다.
2. **`시작하기.cmd`를 더블클릭**합니다.
3. 화면에서 요구하는 로그인·파일 선택·붙여넣기만 합니다.

설치 도우미가 프로젝트 업로드, GitHub Secrets 등록, Drive 폴더 ID 등록, 무료 Pages 설정과 첫 인덱싱 실행을 대신합니다. API Key와 서비스 계정 JSON은 저장소 파일에 저장하지 않습니다. 먼저 [그림 없이도 따라 하는 초보자 안내서](초보자-안내서.md)를 한 번 읽으면 됩니다.

Google의 보안상 사용자가 직접 해야 하는 일은 로그인, Drive API의 **사용** 버튼, 서비스 계정 JSON 다운로드, Drive 폴더 공유뿐입니다. 사이트 안의 Google Picker와 완료 이메일은 기본 인덱싱 성공 후 별도 선택 단계로 켤 수 있습니다.

## 절대 정책: 비용 0원

이 프로젝트는 **Google AI Studio 무료 티어 Gemini API Key 한 개만 이미 가지고 있다**고 가정합니다. 다음 기능은 설정으로 켤 수도 없고 코드 경로도 제공하지 않습니다.

- Google Cloud Billing 활성화 및 Gemini Paid Tier
- Google Search Grounding, 유료 검색 API, 외부 검색 SaaS
- GitHub larger runner, private 저장소의 유료 Actions 분, 유료 artifact/storage
- 유료 DB·서버·벡터 DB·호스팅·모니터링 SaaS
- quota 구매·증액·우회 또는 여러 key를 돌려 쓰는 방식

저장소는 **public**으로 유지하고 standard `ubuntu-latest` runner만 사용해야 합니다. workflow는 private 저장소에서 실행을 거부합니다. Google Cloud Console이나 AI Studio가 Billing 연결을 요구하면 진행하지 말고 중단하세요.

> 중요: GitHub Pages에 배포되는 `data/` 분석 결과는 인터넷에서 접근할 수 있습니다. 원문 파일은 올라가지 않지만 요약·인물·개념·논증 정보가 공개되어도 되는지 먼저 결정하세요. 비공개 분석이 필요하면 Pages 대신 인증된 별도 호스팅이 필요합니다.

## 구현된 기능

- Drive 폴더 재귀 탐색, TXT/EPUB만 선별, 원본 다운로드
- UTF-8/CP949 TXT 처리, EPUB OPF 메타데이터 및 spine 순서 본문 추출
- 약 5,000자 문단 경계 chunk와 소량 overlap
- 파일명 비신뢰 메타데이터 판정: OPF, 본문 표제부, 폴더, 파일명을 독립 evidence로 보관
- `confirmed`, `inferred`, `NEEDS_METADATA_REVIEW`, confidence, conflict, source 기록
- `data/metadata-overrides.json`의 수동 수정값을 재인덱싱보다 우선
- confidence가 낮거나 evidence가 충돌하면 인터넷 검색 없이 `NEEDS_METADATA_REVIEW`
- 메타데이터 AI도 로컬 evidence 후보 중에서만 선택하며 외부 지식 사용 금지
- `models.list()`로 실제 key에 노출된 모델을 조회한 뒤 공식 Free Tier 게시 목록·stable 정책을 모두 통과한 모델만 선택
- preview/experimental/latest/deprecated/retired/legacy 이름 차단, 미발견 시 `NO_SUPPORTED_MODEL`
- 사용자 설정 RPM/TPM/실행 예산, safety margin, `Retry-After`, 지수 backoff+jitter, circuit breaker
- Gemini 무료 quota 또는 자체 실행 예산 중단 시 checkpoint와 `PAUSED_RATE_LIMIT`, 다음 실행에서 미완료 chunk부터 재개
- 한 모델이 429에 막히면 런타임에서 확인된 다음 무료·Stable 모델을 순서대로 탐색하고, 모든 후보가 막힌 뒤에만 `PAUSED_RATE_LIMIT`
- Gemini `503`은 quota로 오인하지 않고 재시도 후 다른 승인된 무료 모델로 최대 2회 전환, 모두 일시 장애면 `PAUSED_SERVICE_UNAVAILABLE`
- Gemini가 문법이 깨진 JSON을 반환하면 엄격한 JSON 지시로 자동 재요청하고 다음 무료 모델까지 시도
- Drive 호출 quota-unit·다운로드 byte 자체 예산, Drive 403/429 시 추가 과금 없이 `PAUSED_RATE_LIMIT`
- 인덱싱 화면에서 Gemini API Key를 교체해 Apps Script의 비공개 Script Properties에 저장하고 다음 실행부터 적용
- 현재/이전/시도 모델·HTTP 상태·재시도 횟수와 대기·책 파일명·실제 완료율·파일/청크 순서·성공/실패 호출·토큰·Drive 사용량을 5초 간격으로 보여 주는 인증된 실시간 모니터
- Picker 설정 누락, Google SDK 로딩 실패, 인증 취소·시간초과를 화면에 명확히 표시
- source 변경, schema/prompt/profile/parser 버전 변경 감지와 멱등 skip
- 소설·학술·철학·역사·과학기술·에세이·실용·희곡·시·혼합 문집별 분석 profile
- manifest의 `tabs`로 상세 메뉴를 동적 생성하는 Pages UI
- GitHub Actions 데이터 commit, Actions Summary, Pages 배포
- 선택 폴더의 모든 TXT/EPUB가 `COMPLETE`일 때만 Apps Script가 `narepheus@gmail.com`으로 완료 메일 발송
- 빠른 중복 클릭은 브라우저와 Apps Script 잠금으로 차단하고, 대기 실행도 최신 `main`에서 완료 책을 확인해 재분석하지 않음
- 인덱싱 결과 커밋 뒤 `Deploy GitHub Pages`가 명시적으로 실행되어 새 카탈로그가 사이트에 반영됨

## 전체 흐름

```text
GitHub Pages ─ Google Picker로 폴더 선택
      │
      └─ Apps Script relay ─ workflow_dispatch
                              │
Google Drive ─ TXT/EPUB ─ GitHub Actions/Python ─ Gemini API
                              │
                              ├─ data/catalog.json + 책별 JSON commit
                              ├─ GitHub Pages 자동 재배포
                              └─ 전부 완료 후 Apps Script callback → Gmail
```

## 0. 시작 전 보안 조치

이전 대화나 다른 장소에 붙여 넣었던 API 키는 이 프로젝트에서 사용하지 마세요. 해당 제공자 화면에서 폐기·회전하고 새 자격증명을 만듭니다. 실제 키, 서비스 계정 JSON, GitHub token은 README·소스·테스트·issue에 붙이지 않습니다.

필요한 계정/권한:

1. `narpheus-cpu/myalldocs` 저장소의 Settings/Actions/Pages를 바꿀 권한
2. 도서가 있는 Google 계정
3. Google Cloud 프로젝트 한 개
4. Gemini API 키를 만들 수 있는 Google AI Studio/Cloud 환경

결제수단이나 Cloud Billing account는 필요하지 않으며 연결하지 않습니다.

## 1. 저장소 연결 또는 clone

빈 로컬 폴더에서 다음을 실행합니다.

```bash
git clone https://github.com/narpheus-cpu/myalldocs.git
cd myalldocs
```

이 프로젝트 파일을 별도로 받은 경우에는 해당 폴더에서 원격을 확인합니다.

```bash
git remote -v
```

원격이 없다면 다음 한 번만 실행합니다.

```bash
git remote add origin https://github.com/narpheus-cpu/myalldocs.git
git branch -M main
git push -u origin main
```

## 2. Google Cloud 프로젝트와 API

1. [Google Cloud Console](https://console.cloud.google.com/)에서 새 프로젝트를 만듭니다.
2. 상단 프로젝트 선택기가 방금 만든 프로젝트인지 확인합니다.
3. **API 및 서비스 → 라이브러리**에서 다음 두 API를 각각 검색해 **사용**을 누릅니다.
   - Google Drive API
   - Google Picker API
4. **API 및 서비스 → OAuth 동의 화면**으로 이동합니다.
5. 개인 Google 계정이면 보통 External을 선택하고 앱 이름/지원 이메일을 입력합니다.
6. 테스트 상태라면 자신의 Google 계정을 Test users에 추가합니다.
7. 필요한 scope는 `https://www.googleapis.com/auth/drive.readonly`입니다. 이 시스템은 원본을 수정하지 않습니다.

이 과정에서 **Billing 계정 연결, 무료 체험 결제 등록, quota 구매**를 요구하는 화면이 나오면 중단합니다. Drive API와 Picker의 표준 무료 범위만 사용합니다.

Google Workspace 조직 정책이 있는 계정은 관리자가 앱 또는 scope를 허용해야 할 수 있습니다.

## 3. Google Picker용 OAuth Client와 브라우저 API key

Picker는 브라우저에서 사용자가 폴더를 고르는 UI입니다. 서비스 계정과 목적이 다릅니다.

### OAuth Client ID

1. **API 및 서비스 → 사용자 인증 정보 → 사용자 인증 정보 만들기 → OAuth 클라이언트 ID**를 선택합니다.
2. 유형은 **웹 애플리케이션**입니다.
3. Authorized JavaScript origins에 Pages 주소를 넣습니다. 저장소 이름이 그대로라면:

   `https://narpheus-cpu.github.io`

4. 로컬 확인이 필요하면 `http://localhost:8000`도 별도로 추가할 수 있습니다.
5. 생성된 `...apps.googleusercontent.com` Client ID를 복사합니다.

### Browser API key

1. **사용자 인증 정보 만들기 → API 키**를 선택합니다.
2. 키 제한에서 **웹사이트**를 선택합니다.
3. 허용 referrer에 아래 두 주소를 모두 넣습니다.
   - `https://narpheus-cpu.github.io/*` (Picker가 도메인 루트의 relay/favicon 경로도 사용하므로 저장소 하위 경로만 넣지 않음)
   - `https://docs.google.com/*` (Picker가 이 주소의 iframe에서 실행되므로 필수)
4. API 제한은 **Google Picker API**로 제한합니다.

브라우저용 Picker API key와 OAuth Client ID는 원래 브라우저에서 보이는 식별자입니다. 그래도 referrer/API 제한 없이 두면 안 됩니다.

`config/public-config.js`의 다섯 placeholder를 채웁니다. `googleCloudProjectNumber`에는 OAuth Client ID 앞부분이 아니라 Cloud Console의 **프로젝트 번호**를 입력합니다. 같은 프로젝트라면 보통 Client ID의 첫 숫자 묶음과 일치합니다.

```js
window.BOOK_APP_CONFIG = {
  googleClientId: "발급한 OAuth Client ID",
  googleCloudProjectNumber: "Google Cloud 프로젝트 번호",
  googlePickerApiKey: "브라우저 제한 API key",
  driveRootFolderId: "[book] 루트 폴더 ID",
  appsScriptWebAppUrl: "Apps Script 배포 뒤 받은 /exec URL"
};
```

Drive 폴더 URL이 `https://drive.google.com/drive/folders/ABC...`라면 `ABC...` 부분이 folder ID입니다.

## 4. GitHub Actions용 Service Account

1. Cloud Console에서 **IAM 및 관리자 → 서비스 계정 → 서비스 계정 만들기**로 이동합니다.
2. 이름만 정하고 프로젝트 전체 역할은 부여하지 않아도 됩니다. Drive 공유 권한으로만 읽습니다.
3. 만든 서비스 계정의 **키 → 키 추가 → 새 키 만들기 → JSON**을 선택합니다.
4. JSON 파일을 열어 `client_email`을 확인합니다.
5. Google Drive의 `[book]` 루트 폴더를 그 `client_email`과 **뷰어** 권한으로 공유합니다.
6. JSON 전체는 다음 단계에서 GitHub Secret에만 저장합니다. 저장소 파일로 복사하지 않습니다.

조직 정책이 서비스 계정 key 생성을 금지하면, 향후 Workload Identity Federation adapter를 추가해야 합니다. 현재 구현의 기본 인증은 서비스 계정 JSON입니다.

## 5. 새 Gemini API key

1. [Google AI Studio](https://aistudio.google.com/apikey) 또는 조직에서 허용한 공식 발급 화면에서 새 key를 만듭니다.
2. 과거에 노출했던 문자열은 재사용하지 않습니다.
3. 모델 이름은 여기서 고르지 않습니다. 실행 시 API의 모델 목록과 `config/model-policy.json`을 대조합니다.

무료 한도는 계정·모델·시점에 따라 달라질 수 있으므로 코드가 공식 고정 숫자로 간주하지 않습니다. 실제 AI Studio Free Tier 한도보다 보수적으로 `config/indexer.json`을 조정하세요. Billing 연결을 통해 한도를 올리지 않습니다.

## 6. GitHub Secrets와 Variable

저장소에서 **Settings → Secrets and variables → Actions**를 엽니다.

### Repository secrets

각각 **New repository secret**으로 추가합니다.

| 이름 | 값 |
|---|---|
| `GEMINI_API_KEY` | 새 Gemini API key |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | 서비스 계정 JSON 전체 |
| `APPS_SCRIPT_CALLBACK_URL` | Apps Script `/exec` 배포 URL |
| `APPS_SCRIPT_CALLBACK_SECRET` | 직접 만든 길고 무작위인 callback secret |

### Repository variable

Variables 탭에서 추가합니다.

| 이름 | 값 |
|---|---|
| `DRIVE_ROOT_FOLDER_ID` | `[book]` 루트 folder ID |
| `GEMINI_API_KEY` | 선택 사항. 인덱싱 화면에서 새 키를 저장하면 자동 생성/갱신됨 |

이 변수는 보안 비밀이 아니라 선택 폴더가 허용 루트 아래인지 재검증하는 경계입니다.

## 7. Apps Script relay 배포

Apps Script는 Gemini 분석을 하지 않습니다. 로그인한 본인의 폴더 선택 요청을 GitHub Actions로 전달하고, 완전 완료 callback에만 Gmail을 보냅니다.

1. [script.google.com](https://script.google.com/)에서 새 프로젝트를 만듭니다.
2. `apps-script/Code.gs` 내용을 기본 `Code.gs`에 붙여 넣습니다.
3. Project Settings에서 manifest 표시를 켜고 `apps-script/appsscript.json` 내용과 맞춥니다.
4. **Project Settings → Script properties**에 다음 값을 추가합니다.

| 이름 | 값 |
|---|---|
| `GITHUB_TOKEN` | `narpheus-cpu/myalldocs` Actions workflow 실행 권한이 있는 fine-grained token |
| `GITHUB_OWNER` | `narpheus-cpu` |
| `GITHUB_REPO` | `myalldocs` |
| `CALLBACK_SECRET` | GitHub의 `APPS_SCRIPT_CALLBACK_SECRET`과 정확히 같은 값 |
| `COMPLETION_EMAIL` | `narepheus@gmail.com` |
| `AUTHORIZED_EMAIL` | 웹 UI를 사용할 본인 Google 이메일 |
| `DRIVE_ROOT_FOLDER_ID` | `[book]` 루트 folder ID |

GitHub fine-grained token은 이 저장소 하나만 선택하고 Actions: Read and write 권한만 허용합니다. token을 `public-config.js`에 넣으면 안 됩니다.

5. **Deploy → New deployment → Web app**을 선택합니다.
6. Execute as는 본인, Who has access는 **Anyone**을 선택합니다. GitHub Actions가 로그인 쿠키 없이 진행 상태와 완료 callback을 보내야 하기 때문입니다.
7. 최초 권한 승인에서 Drive 읽기, 외부 요청, Gmail 발송을 검토하고 승인합니다.
8. 배포 후 `/exec` URL을 `config/public-config.js`와 GitHub Secret `APPS_SCRIPT_CALLBACK_URL`에 입력합니다.
9. Code.gs를 수정한 뒤에는 **Manage deployments → Edit → New version → Deploy**를 해야 실제 URL 코드가 갱신됩니다.

웹 앱 URL 자체는 Pages 설정에 포함되어 공개되지만 기능이 공개되는 것은 아닙니다. 폴더 선택·실행·API Key 변경·상태 조회는 매 요청마다 Google access token의 이메일을 `AUTHORIZED_EMAIL`과 대조하고, workflow의 key 조회·진행 보고·완료 callback은 `CALLBACK_SECRET`을 검증합니다. callback secret은 절대 게시하지 마세요.

### 인덱싱 화면의 API Key 변경

1. 사이트의 **인덱싱 → Gemini API Key 변경**에 새 Free Tier key를 입력합니다.
2. **안전하게 저장**을 누르고 본인 Google 계정으로 인증합니다.
3. key는 Pages, 브라우저 저장소, GitHub 파일에 기록되지 않고 Apps Script의 비공개 `GEMINI_API_KEY` 속성으로 저장됩니다.
4. 다음 workflow는 `indexer.runtime_secret` 단계에서 이 값을 받아 기존 GitHub Secret보다 우선 사용합니다. Apps Script에 새 key가 없거나 일시적으로 연결되지 않으면 기존 `GEMINI_API_KEY` Secret을 그대로 사용합니다.

### 실시간 모니터

인덱싱 화면에서 **Google 계정으로 연결**을 누르면 5초마다 다음 정보를 갱신합니다.

- 실제 runtime에서 선택된 Gemini 모델
- 현재 처리 중인 책 파일명과 전체 파일 순서
- Drive 다운로드, 메타데이터 판정, 유형 분류, chunk 분석, 통합, 저장 단계
- 현재/전체 chunk, 완료·건너뜀·실패·메타데이터 확인 필요 수
- Gemini 요청 수와 입출력 token, Drive quota units와 다운로드 byte

원문이나 API Key는 진행 상태에 포함하지 않습니다. 파일명처럼 개인 정보가 될 수 있는 항목은 Google access token으로 `AUTHORIZED_EMAIL`을 검증한 사용자에게만 반환합니다. 토큰은 메모리에만 두며 새로고침하면 사라집니다.

## 8. GitHub Pages 켜기

1. 저장소가 **Public**인지 확인합니다. private 저장소에서는 Actions workflow가 비용 안전을 위해 즉시 거부됩니다.
2. 저장소 **Settings → Pages**로 이동합니다.
3. Build and deployment의 Source를 **GitHub Actions**로 바꿉니다.
4. `main`에 push하면 `.github/workflows/deploy-pages.yml`이 정적 파일을 배포합니다.
5. **Actions** 탭에서 `Deploy GitHub Pages`가 초록색인지 확인합니다.
6. 주소는 일반적으로 `https://narpheus-cpu.github.io/myalldocs/`입니다.

Public 저장소의 standard GitHub-hosted runner와 GitHub Pages 무료 사용만 전제로 합니다. larger runner를 만들거나 workflow의 `runs-on`을 larger runner label로 바꾸지 마세요.

## 9. 첫 실행

### 웹에서 실행

1. Pages의 **인덱싱** 메뉴를 엽니다.
2. **Google Drive 폴더 선택**을 누르고 허용한 Google 계정으로 로그인합니다.
3. `[book]` 아래 폴더를 고릅니다. Picker와 Apps Script/Python이 각각 루트 포함 관계를 검사합니다.
4. **대상 미리 보기**로 TXT/EPUB 수를 확인합니다.
5. **인덱싱 시작**을 누릅니다.
6. GitHub 저장소 **Actions → Index books**에서 실행을 확인합니다.

### GitHub Actions에서 직접 실행하는 비상 경로

1. **Actions → Index books → Run workflow**를 누릅니다.
2. `folder_id`에 선택 폴더 ID를 입력합니다.
3. `recursive`, `force_reindex`, 선택적 `analysis_profile`을 정합니다.
4. Run workflow를 누릅니다.

한 번에 하나만 실행되며 새 실행이 기존 실행을 취소하지 않습니다.

## 10. 데이터와 수동 override

책 ID는 Drive `fileId`에서 안정적으로 만들며 파일명 변경에 흔들리지 않습니다.

```text
data/catalog.json
data/job-status.json
data/checkpoints/{bookId}.json
data/books/{bookId}/manifest.json
data/books/{bookId}/summary.json
data/books/{bookId}/chunks.json
data/books/{bookId}/analysis.json
data/books/{bookId}/timeline.json
data/books/{bookId}/relationships.json
```

제목/저자 또는 문서 유형을 사람이 수정하려면 `data/metadata-overrides.json`을 편집합니다.

```json
{
  "byDriveFileId": {
    "실제_DRIVE_FILE_ID": {
      "title": "확정한 제목",
      "author": "확정한 저자",
      "documentType": "philosophy"
    }
  }
}
```

이 파일은 indexer가 자동으로 덮어쓰지 않으며 다음 재인덱싱에도 최우선입니다. 허용 profile 값은 `config/analysis-profiles.json`의 key입니다.

## 11. 모델 정책

구체 모델명은 코드에 고정되어 있지 않습니다.

1. `google-genai`의 `client.models.list()`를 호출합니다.
2. `generateContent`를 지원하는지 확인합니다.
3. `config/model-policy.json`의 공식 Free Tier 게시 모델 정규식과 차단 단어를 적용합니다.
4. Flash/Flash-Lite stable 형태를 우선순위대로 고릅니다. Paid-only 모델과 Pro/preview/experimental/latest alias는 선택하지 않습니다.
5. 호출 시 unavailable이면 다음 허용 Free Tier 모델로 fallback합니다.
6. 허용 모델이 하나도 없으면 API를 억지 호출하거나 Billing을 안내하지 않고 `NO_SUPPORTED_MODEL`로 끝냅니다.

Google이 Free Tier 제공 모델을 바꾸면 공식 가격·모델 페이지를 모두 확인하고 `freeTierModelPatterns`만 갱신합니다. `models.list()`에 보인다는 사실만으로 무료라고 간주하지 않습니다.

Gemini 모델 API는 해당 key가 연결된 프로젝트의 Billing 상태 자체를 판정해 주지 않습니다. 따라서 **AI Studio 프로젝트 화면에서 Tier가 Free인 key만 Secret에 등록**해야 합니다. 코드 측에서는 Free Tier 공식 게시 목록과 런타임 가용성의 교집합만 허용하고, 목록이 낡거나 비어 있으면 `NO_SUPPORTED_MODEL`로 정지합니다.

## 12. quota와 장기 실행 조절

`config/indexer.json`의 숫자는 Google의 공식 고정 한도가 아니라 **이 저장소의 자체 무료 안전 예산**입니다.

- `requestsPerMinute`, `tokensPerMinute`: 계정 한도 이하의 속도
- `maxRequestsPerRun`, `maxTokensPerRun`: 한 실행의 최대 소비
- `maxBooksPerRun`, `maxChunksPerRun`: 하루/회차 작업량
- `maxRuntimeMinutes`: Actions timeout보다 작은 값
- `safetyMargin`: 설정 한도의 실제 사용 비율
- `maxRetries`, backoff, circuit breaker: 반복 오류 때 안전 일시정지

429의 `Retry-After`가 있으면 우선 따르고, 이후 지수 backoff와 jitter를 적용합니다. 첫 모델의 재시도를 소진하면 `models.list()`와 Free Tier·Stable 정책을 통과한 다음 모델을 순서대로 한 번씩 확인합니다. 사용 가능한 모델을 찾으면 그 모델로 계속 처리하고, 모든 허용 무료 모델이 429일 때만 `data/checkpoints/`에 진행을 저장하고 `PAUSED_RATE_LIMIT`로 종료합니다. 자체 `maxRequestsPerRun`·token·runtime 예산에 닿은 경우에는 비용 안전장치를 우회하지 않고 즉시 멈춥니다. 실패 호출도 실행 예산과 화면의 호출 시도 횟수에 포함합니다.

503은 무료 quota 소진이 아니라 Gemini 서비스의 일시 과부하 또는 사용 불가로 다룹니다. 먼저 같은 모델에서 backoff 재시도하고, 계속 503이면 `models.list()`와 Free Tier 정책을 이미 통과한 다음 모델로 전환합니다. `config/model-policy.json`의 `maxServiceFallbacks` 횟수만큼 전환해도 모두 실패하면 checkpoint를 보존한 채 `PAUSED_SERVICE_UNAVAILABLE`로 끝냅니다.

Drive에도 별도 무료 안전 예산이 있습니다.

```json
"driveQuota": {
  "maxQuotaUnitsPerRun": 50000,
  "maxDownloadBytesPerRun": 536870912,
  "pauseOn403Or429": true
}
```

각 Drive `files.get`, `files.list`, 다운로드 요청의 quota unit을 보수적으로 누적합니다. 실행 예산을 넘기기 전에 멈추고, Drive가 quota 관련 403/429를 반환해도 quota 구매나 상향 요청을 하지 않습니다. 파일 크기를 알 수 없는 무제한 다운로드도 거부합니다. 이 값은 더 낮출 수 있지만 표준 무료 범위를 넘기기 위해 올려서는 안 됩니다.

`unitCosts`는 2026-09-12 공식 Drive 한도 문서의 method별 값을 설정으로 옮긴 것입니다. Google이 값을 바꾸면 공식 문서를 확인해 설정만 갱신하며, 코드가 오래된 수치를 영구적인 무료 한도로 가정하지 않습니다.

제목·저자 confidence가 낮거나 OPF/표제부가 충돌해도 웹 검색은 하지 않습니다. 로컬 evidence와 그 후보만 보는 Gemini 판정으로 확정할 수 없으면 `NEEDS_METADATA_REVIEW`로 남기고 `data/metadata-overrides.json`에서 사람이 수정합니다.

## 13. 테스트

GitHub Actions는 실제 Drive 호출 전에 다음을 실행합니다.

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

테스트에는 임의 생성 TXT/EPUB만 들어 있습니다. UTF-8/CP949, EPUB spine/메타데이터, 5,000자 chunk, 충돌/override, Free Tier model filtering, 웹 검색 코드 부재, Gemini/Drive 예산, 429 전체 무료 모델 순차 탐색, 503 무료 모델 fallback, Retry-After/circuit breaker, 43→44 checkpoint 재개, profile fallback, catalog 멱등성, credential 문자열 부재를 검사합니다.

로컬 Python이 패키지를 설치할 수 없는 제한 환경에서는 표준 라이브러리 전용 보조 실행도 가능합니다.

```bash
python -m tests.local_runner
```

실제 Drive·Gemini·Picker·메일은 개인 credential과 외부 서비스가 필요하므로 unit test가 대신 증명할 수 없습니다. 첫 설정 후 작은 테스트 폴더 한 개로 end-to-end 실행을 확인하세요.

## 14. 장애 복구

### `NO_SUPPORTED_MODEL`

Gemini 공식 가격표와 모델 목록 양쪽에서 **Free Tier + stable + `generateContent`**를 확인한 뒤 `config/model-policy.json`을 수정합니다. 임시로 Paid Tier, Pro, preview/latest를 허용하지 않습니다.

### `PAUSED_RATE_LIMIT`

실패가 아니라 보존된 일시정지입니다. 화면의 `시도한 모델`에 표시된 모든 허용 무료 모델이 429였거나 자체 무료 실행 예산에 도달했을 때만 나타납니다. 무료 quota reset 뒤 같은 folder ID로 다시 실행합니다. Billing 연결, quota 구매·증액, key 회전은 하지 않고 `force_reindex`도 끕니다.

### `PAUSED_SERVICE_UNAVAILABLE`

무료 한도 소진이 아니라 Gemini 서버가 일시적으로 응답할 수 없다는 뜻입니다. 프로그램은 backoff 재시도와 허용된 무료 모델 fallback을 먼저 마친 상태입니다. 결제하거나 키를 바꾸지 말고 잠시 뒤 같은 folder ID로 다시 실행하세요. 저장된 checkpoint부터 이어집니다.

### `ERROR`와 JSON 문법 오류

Gemini가 드물게 쉼표·따옴표가 빠진 JSON을 보내면 같은 무료 모델에 엄격한 JSON 형식으로 다시 요청하고, 계속 깨지면 다음 허용 무료 모델을 한 번 시도합니다. 그래도 복구되지 않으면 실제 오류 위치만 표시하고 응답 전문이나 원문은 로그에 남기지 않습니다. 이때도 마지막으로 성공한 청크까지 즉시 checkpoint에 저장되므로 다음 실행에서 해당 지점부터 이어집니다.

### 401/403

- Gemini key가 폐기됐는지 확인
- 서비스 계정 JSON이 온전한지 확인
- `[book]` 폴더가 서비스 계정 `client_email`에 공유됐는지 확인
- 조직 정책과 Drive readonly scope 확인

### Picker가 열리지 않음

- Pages origin이 OAuth Client의 Authorized JavaScript origins에 있는지 확인
- 브라우저 key referrer가 `.../myalldocs/*`를 포함하는지 확인
- Drive API와 Picker API가 같은 Cloud 프로젝트에서 켜졌는지 확인
- OAuth 테스트 사용자에 로그인 계정이 있는지 확인

### Apps Script 요청 실패

- `/dev`가 아니라 배포된 `/exec` URL인지 확인
- 수정 후 새 버전으로 재배포했는지 확인
- `AUTHORIZED_EMAIL`과 로그인 계정 일치 확인
- GitHub token의 저장소 범위와 Actions write 권한 확인

### commit/push 충돌

workflow는 concurrency로 한 번에 하나만 인덱싱합니다. 사람이 동시에 `data/`를 고친 경우 Action의 rebase 단계가 실패할 수 있습니다. 변경을 main에 먼저 반영한 뒤 workflow를 다시 실행합니다. checkpoint가 남아 있으면 완료 chunk를 재호출하지 않습니다.

### 완료 메일이 오지 않음

`Actions → Retry completion email → Run workflow`를 누르면 마지막 `COMPLETE` 결과의 메일만 다시 보낼 수 있습니다. 콜백은 Apps Script 응답의 `ok=true`와 `emailSent=true`를 모두 확인하므로, 메일 권한이나 배포 버전 문제를 더 이상 성공으로 숨기지 않습니다. 이 작업이 실패하면 표시된 오류에 따라 Apps Script에서 `MailApp` 권한을 다시 승인하고 **배포 관리 → 수정 → 새 버전 → 배포**를 수행하세요.

### 완료됐는데 다시 같은 책을 분석함

인덱싱 버튼을 여러 번 눌러도 새 실행은 하나만 접수합니다. 이미 대기열에 들어간 실행이 있더라도 작업 시작 시 최신 `main`의 manifest를 읽어 원본과 버전이 같은 `COMPLETE` 책은 `UNCHANGED`로 건너뜁니다. 강제 재처리가 필요할 때만 `force_reindex`를 켭니다.

### 도서 탐색이 0권으로 남음

`Index books`가 끝날 때마다 `Deploy GitHub Pages`가 별도로 실행됩니다. 이 배포가 초록색이 된 뒤 페이지를 새로고침하면 최신 `data/catalog.json`이 표시됩니다.

`data/job-status.json`에서 `status=COMPLETE`, `allTargetsComplete=true`, `failed=0`인지 확인합니다. `PARTIAL`, `PAUSED_RATE_LIMIT`, `PAUSED_SERVICE_UNAVAILABLE`, `ERROR`에서는 설계상 메일을 보내지 않습니다. Apps Script 실행 기록과 Gmail send 권한도 확인합니다.

## 15. 버전 변경과 재인덱싱

`config/indexer.json`의 다음 버전은 독립적으로 관리됩니다.

- `indexSchema`
- `prompt`
- `analysisProfile`
- `parser`

source checksum 또는 이 버전이 바뀌면 해당 책은 재인덱싱 대상입니다. 같은 source와 같은 버전이면 반복 실행해도 catalog 중복을 만들지 않고 skip합니다.

## 공식 사양 확인 링크

- [Gemini API 모델 목록과 models.list](https://ai.google.dev/api/models)
- [Gemini Developer API 가격과 Free Tier](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini Free Tier rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Gemini generateContent와 구조화 JSON](https://ai.google.dev/api/generate-content)
- [Google Drive API v3 files](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
- [Google Drive API 사용 한도와 가격](https://developers.google.com/workspace/drive/api/guides/limits)
- [Google Picker 표시 가이드](https://developers.google.com/workspace/drive/api/guides/picker)
- [GitHub workflow_dispatch](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#workflow_dispatch)
- [GitHub Pages custom workflow](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [GitHub Actions billing과 public standard runner 무료 조건](https://docs.github.com/en/billing/concepts/product-billing/github-actions)
