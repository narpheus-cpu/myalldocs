# Apps Script relay

설정·배포 순서는 루트 [README.md](../README.md)의 7장을 따르세요. 이 폴더의 `Code.gs`와 `appsscript.json`에는 실제 token/secret을 넣지 않습니다. 모든 민감값은 Apps Script의 Script Properties에만 저장합니다.

relay는 세 가지 방어선을 둡니다.

1. 요청 사용자의 Google 이메일 확인
2. 선택 folder가 `DRIVE_ROOT_FOLDER_ID` 아래인지 확인
3. 완료 callback secret 확인

완료 메일은 callback이 `status=COMPLETE` 및 `allTargetsComplete=true`를 동시에 보낼 때만 발송합니다.
