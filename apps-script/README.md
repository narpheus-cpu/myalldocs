# Apps Script relay: 기본 설치가 끝난 뒤 하는 선택 단계

`시작하기.cmd`로 첫 인덱싱이 성공했다면 이 기능 없이도 서재를 사용할 수 있습니다. 아래 설정은 사이트에서 Drive 폴더를 고르고, 전체 완료 메일을 받고 싶을 때 한 번만 합니다.

Google의 보안 정책 때문에 **새 Apps Script 만들기, 권한 승인, 배포** 세 버튼은 설치 도우미가 대신 누를 수 없습니다. 나머지는 아래 값을 그대로 복사하면 됩니다.

1. [Apps Script](https://script.google.com/)에서 **새 프로젝트**를 누릅니다.
2. 화면의 `Code.gs` 내용을 모두 지우고 이 폴더의 `Code.gs` 내용을 붙여넣은 뒤 저장합니다.
3. 왼쪽 **프로젝트 설정**에서 `appsscript.json` 표시를 켜고, 편집기에 나타난 파일 내용을 이 폴더의 `appsscript.json` 내용으로 바꿉니다. 이 설정은 기본 Apps Script Cloud 프로젝트에서 무료 Google Drive API 서비스를 자동 활성화합니다.
4. 같은 프로젝트 설정 화면의 **스크립트 속성**에서 루트 README 7장 표의 값을 한 줄씩 추가합니다. 비밀값은 코드에 쓰지 않습니다. `GITHUB_TOKEN`은 선택 사항입니다. 유효한 토큰이 없거나 만료되어도 업로드는 실패하지 않고, GitHub의 정기 실행이 보통 20분 이내에 대기열을 가져갑니다(스케줄 지연 가능).
5. 오른쪽 위 **배포 → 새 배포 → 웹 앱**을 선택합니다. 실행 사용자는 본인, 액세스 사용자는 **Anyone**으로 둡니다. 실제 기능은 Google 로그인 이메일 또는 callback secret을 다시 검사합니다.
6. Script Properties에 서비스 계정 JSON의 `client_email`을 `SERVICE_ACCOUNT_EMAIL`로 추가합니다. 반드시 `...iam.gserviceaccount.com`으로 끝나는 주소여야 하며 일반 Gmail 주소를 넣으면 안 됩니다.
7. 함수 선택에서 `setupPrivateStorage`를 골라 **실행**하고, 권한 확인 화면에서 Drive 원본 읽기, 이 앱이 만든 비공개 관리 파일 쓰기, GitHub 요청, 완료 메일 발송 권한을 승인합니다. 실행 완료가 표시되면 `서재지도_비공개_관리` 폴더 생성과 서비스 계정 공유가 확인된 것입니다.
8. 마지막 `/exec` 주소를 복사해 루트 README 7장의 두 위치에 넣습니다.

설정·배포 순서는 루트 [README.md](../README.md)의 7장을 따르세요. 이 폴더의 `Code.gs`와 `appsscript.json`에는 실제 token/secret을 넣지 않습니다. 모든 민감값은 Apps Script의 Script Properties에만 저장합니다. `Code.gs`를 갱신한 뒤에는 반드시 **배포 관리 → 수정 → 새 버전 → 배포**를 눌러야 JSONL 업로드·비공개 대기열·API Key 변경·실시간 모니터가 실제 `/exec` URL에 적용됩니다.

relay는 세 가지 방어선을 둡니다.

1. 요청 사용자의 Google 이메일 확인
2. 선택 folder가 `DRIVE_ROOT_FOLDER_ID` 아래인지 확인
3. 완료 callback secret 확인
4. API Key 저장·진행 상태 조회 전에 Google access token의 이메일을 `AUTHORIZED_EMAIL`과 대조

완료 메일은 callback이 `status=COMPLETE` 및 `allTargetsComplete=true`를 동시에 보낼 때만 발송합니다. 실시간 상태에는 원문과 API Key를 넣지 않습니다.
