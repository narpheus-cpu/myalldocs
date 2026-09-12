# 서재 지도 — Google Drive 개인 도서 지식베이스

Google Drive의 TXT/EPUB 원본을 조금씩 분석해 GitHub의 구조화된 JSON으로 축적하고, GitHub Pages에서 검색·탐색하는 개인용 시스템입니다. 원본 파일은 Drive 밖으로 복제하지 않습니다. 분석 엔진은 GitHub Actions의 Python, 수동 실행 연결은 Google Picker와 Apps Script, 요약·분석은 현재 `google-genai` SDK를 사용합니다.

> 중요: GitHub Pages에 배포되는 `data/` 분석 결과는 인터넷에서 접근할 수 있습니다. 원문 파일은 올라가지 않지만 요약·인물·개념·논증 정보가 공개되어도 되는지 먼저 결정하세요. 비공개 분석이 필요하면 Pages 대신 인증된 별도 호스팅이 필요합니다.

## 구현된 기능

- Drive 폴더 재귀 탐색, TXT/EPUB만 선별, 원본 다운로드
- UTF-8/CP949 TXT 처리, EPUB OPF 메타데이터 및 spine 순서 본문 추출
- 약 5,000자 문단 경계 chunk와 소량 overlap
- 파일명 비신뢰 메타데이터 판정: OPF, 본문 표제부, 폴더, 파일명을 독립 evidence로 보관
- `confirmed`, `inferred`, `NEEDS_METADATA_REVIEW`, confidence, conflict, source 기록
- `data/metadata-overrides.json`의 수동 수정값을 재인덱싱보다 우선
- 낮은 confidence 또는 evidence 충돌 때만 선택적으로 Google Search grounding 검증
- 검색 결과는 `external: true`인 보조 evidence로 분리하고 원문 분석에는 넣지 않음
- `models.list()`로 `generateContent` 지원 모델을 실행 때 탐색하고 stable 정책으로 선택
- preview/experimental/latest/deprecated/retired/legacy 이름 차단, 미발견 시 `NO_SUPPORTED_MODEL`
- 사용자 설정 RPM/TPM/실행 예산, safety margin, `Retry-After`, 지수 backoff+jitter, circuit breaker
- quota/실행 예산 중단 시 checkpoint와 `PAUSED_RATE_LIMIT`, 다음 실행에서 미완료 chunk부터 재개
- source 변경, schema/prompt/profile/parser 버전 변경 감지와 멱등 skip
- 소설·학술·철학·역사·과학기술·에세이·실용·희곡·시·혼합 문집별 분석 profile
- manifest의 `tabs`로 상세 메뉴를 동적 생성하는 Pages UI
- GitHub Actions 데이터 commit, Actions Summary, Pages 배포
- 선택 폴더의 모든 TXT/EPUB가 `COMPLETE`일 때만 Apps Script가 `narepheus@gmail.com`으로 완료 메일 발송

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
3. 허용 referrer에 `https://narpheus-cpu.github.io/myalldocs/*`를 넣습니다.
4. API 제한은 **Google Picker API**로 제한합니다.

브라우저용 Picker API key와 OAuth Client ID는 원래 브라우저에서 보이는 식별자입니다. 그래도 referrer/API 제한 없이 두면 안 됩니다.

`config/public-config.js`의 네 placeholder를 채웁니다.

```js
window.BOOK_APP_CONFIG = {
  googleClientId: "발급한 OAuth Client ID",
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

무료 한도는 계정·모델·시점에 따라 달라질 수 있으므로 코드가 공식 고정 숫자로 간주하지 않습니다. 실제 계정 한도보다 보수적으로 `config/indexer.json`을 조정하세요.

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
6. Execute as는 본인, Who has access는 가능한 가장 좁은 범위(개인용이면 본인만)를 선택합니다.
7. 최초 권한 승인에서 Drive 읽기, 외부 요청, Gmail 발송을 검토하고 승인합니다.
8. 배포 후 `/exec` URL을 `config/public-config.js`와 GitHub Secret `APPS_SCRIPT_CALLBACK_URL`에 입력합니다.
9. Code.gs를 수정한 뒤에는 **Manage deployments → Edit → New version → Deploy**를 해야 실제 URL 코드가 갱신됩니다.

브라우저의 교차 출처 로그인 정책 때문에 개인 전용 Apps Script `/exec` 호출이 차단되는 환경도 있습니다. 그 경우 endpoint 공개 범위를 넓히지 말고 아래의 **GitHub Actions에서 직접 실행하는 비상 경로**를 사용하세요. 이 제약은 실제 배포 계정으로 확인해야 합니다.

## 8. GitHub Pages 켜기

1. 저장소 **Settings → Pages**로 이동합니다.
2. Build and deployment의 Source를 **GitHub Actions**로 바꿉니다.
3. `main`에 push하면 `.github/workflows/deploy-pages.yml`이 정적 파일을 배포합니다.
4. **Actions** 탭에서 `Deploy GitHub Pages`가 초록색인지 확인합니다.
5. 주소는 일반적으로 `https://narpheus-cpu.github.io/myalldocs/`입니다.

Pages는 저장소가 private이어도 플랜/설정에 따라 사이트가 공개될 수 있으므로 실제 URL을 로그아웃 창에서 확인하세요.

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
3. `config/model-policy.json`의 허용 정규식과 차단 단어를 적용합니다.
4. stable 형태를 우선순위대로 고릅니다.
5. 호출 중 model unavailable이면 다음 실행에서 목록을 다시 확인합니다.
6. 허용 모델이 하나도 없으면 API를 억지 호출하지 않고 `NO_SUPPORTED_MODEL`로 끝냅니다.

Google이 naming/status metadata를 바꾸면 공식 모델 페이지를 확인하고 정책 파일만 갱신합니다. `latest`, preview, experimental alias를 기본 허용하지 마세요.

## 12. quota와 장기 실행 조절

`config/indexer.json`의 숫자는 Google의 공식 고정 한도가 아니라 **이 저장소의 자체 안전 예산**입니다.

- `requestsPerMinute`, `tokensPerMinute`: 계정 한도 이하의 속도
- `maxRequestsPerRun`, `maxTokensPerRun`: 한 실행의 최대 소비
- `maxBooksPerRun`, `maxChunksPerRun`: 하루/회차 작업량
- `maxRuntimeMinutes`: Actions timeout보다 작은 값
- `safetyMargin`: 설정 한도의 실제 사용 비율
- `maxRetries`, backoff, circuit breaker: 반복 오류 때 안전 일시정지

429의 `Retry-After`가 있으면 우선 따르고, 이후 지수 backoff와 jitter를 적용합니다. 재시도 한도나 자체 예산에 닿으면 `data/checkpoints/`에 진행을 저장하고 `PAUSED_RATE_LIMIT`로 정상 종료합니다. 완료 메일은 보내지 않습니다. 다음 수동 실행은 저장된 `chunkId` 이후부터 계속합니다.

웹 검증은 기본 `false`입니다. 켜려면:

```json
"metadataResolution": {
  "confirmationThreshold": 0.75,
  "webVerificationEnabled": true,
  "webVerificationThreshold": 0.75
}
```

이때도 모든 책을 검색하지 않습니다. 로컬 evidence가 임계값 미만이거나 충돌한 책만 Google Search grounding을 사용하고 URL을 외부 evidence로 기록합니다. 작품 요약·인물·논증 분석 prompt에는 웹 결과가 전달되지 않습니다.

## 13. 테스트

GitHub Actions는 실제 Drive 호출 전에 다음을 실행합니다.

```bash
python -m pip install -r requirements.txt
python -m pytest -q
```

테스트에는 임의 생성 TXT/EPUB만 들어 있습니다. UTF-8/CP949, EPUB spine/메타데이터, 5,000자 chunk, 충돌/override/web 조건, model filtering, 429/Retry-After/circuit breaker, 43→44 checkpoint 재개, profile fallback, catalog 멱등성, credential 문자열 부재를 검사합니다.

로컬 Python이 패키지를 설치할 수 없는 제한 환경에서는 표준 라이브러리 전용 보조 실행도 가능합니다.

```bash
python -m tests.local_runner
```

실제 Drive·Gemini·Picker·메일은 개인 credential과 외부 서비스가 필요하므로 unit test가 대신 증명할 수 없습니다. 첫 설정 후 작은 테스트 폴더 한 개로 end-to-end 실행을 확인하세요.

## 14. 장애 복구

### `NO_SUPPORTED_MODEL`

Gemini 공식 모델 목록에서 stable 및 `generateContent` 지원 여부를 확인한 뒤 `config/model-policy.json`을 수정합니다. 임시로 preview/latest를 허용하기보다 공식 stable 이름을 정책에 추가합니다.

### `PAUSED_RATE_LIMIT`

실패가 아니라 보존된 일시정지입니다. 계정 quota reset 뒤 같은 folder ID로 다시 실행합니다. `force_reindex`는 끕니다.

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

`data/job-status.json`에서 `status=COMPLETE`, `allTargetsComplete=true`, `failed=0`인지 확인합니다. `PARTIAL`, `PAUSED_RATE_LIMIT`, `ERROR`에서는 설계상 메일을 보내지 않습니다. Apps Script 실행 기록과 Gmail send 권한도 확인합니다.

## 15. 버전 변경과 재인덱싱

`config/indexer.json`의 다음 버전은 독립적으로 관리됩니다.

- `indexSchema`
- `prompt`
- `analysisProfile`
- `parser`

source checksum 또는 이 버전이 바뀌면 해당 책은 재인덱싱 대상입니다. 같은 source와 같은 버전이면 반복 실행해도 catalog 중복을 만들지 않고 skip합니다.

## 공식 사양 확인 링크

- [Gemini API 모델 목록과 models.list](https://ai.google.dev/api/models)
- [Gemini generateContent와 구조화 JSON](https://ai.google.dev/api/generate-content)
- [Google Drive API v3 files](https://developers.google.com/workspace/drive/api/reference/rest/v3/files)
- [Google Picker 표시 가이드](https://developers.google.com/workspace/drive/api/guides/picker)
- [GitHub workflow_dispatch](https://docs.github.com/en/actions/using-workflows/events-that-trigger-workflows#workflow_dispatch)
- [GitHub Pages custom workflow](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
