[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

function Write-Title([string]$Text) {
    Write-Host ''
    Write-Host ('=' * 64) -ForegroundColor DarkCyan
    Write-Host $Text -ForegroundColor Cyan
    Write-Host ('=' * 64) -ForegroundColor DarkCyan
}

function Stop-Friendly([string]$Message) {
    Write-Host ''
    Write-Host $Message -ForegroundColor Red
    Write-Host '문제를 해결한 뒤 시작하기.cmd를 다시 더블클릭하세요.' -ForegroundColor Yellow
    exit 1
}

function Wait-Enter([string]$Message) {
    [void](Read-Host $Message)
}

function Get-FolderId([string]$Value) {
    $trimmed = $Value.Trim()
    if ($trimmed -match '/folders/([A-Za-z0-9_-]+)') { return $Matches[1] }
    if ($trimmed -match '^[A-Za-z0-9_-]{10,}$') { return $trimmed }
    throw 'Drive 폴더 주소 또는 ID 형식이 올바르지 않습니다.'
}

function Select-ServiceAccountFile {
    Add-Type -AssemblyName System.Windows.Forms
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $dialog.Title = 'Google에서 받은 서비스 계정 JSON 파일을 고르세요'
    $dialog.Filter = 'JSON 파일 (*.json)|*.json'
    $dialog.Multiselect = $false
    if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
        throw '서비스 계정 JSON 파일 선택을 취소했습니다.'
    }
    return $dialog.FileName
}

function Invoke-Gh([string[]]$Arguments, [string]$FailureMessage) {
    & gh @Arguments
    if ($LASTEXITCODE -ne 0) { throw $FailureMessage }
}

Write-Title '서재 지도 초보자용 처음 설정'
Write-Host '이 창이 GitHub의 비밀값 등록과 첫 실행을 대신 처리합니다.'
Write-Host 'API 키와 JSON 내용을 프로젝트 파일이나 로그에 복사하지 않습니다.'
Write-Host '비용이 발생하는 설정은 사용하지 않습니다.' -ForegroundColor Green
Wait-Enter '준비되면 Enter를 누르세요'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Start-Process 'https://git-scm.com/download/win'
    Stop-Friendly 'Git이 설치되어 있지 않아 공식 설치 페이지를 열었습니다.'
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Start-Process 'https://cli.github.com/'
    Stop-Friendly 'GitHub 로그인 도구가 설치되어 있지 않아 공식 설치 페이지를 열었습니다.'
}

Write-Title '1/5  GitHub 로그인과 저장소 확인'
& gh auth status --hostname github.com *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host '브라우저가 열리면 GitHub에 로그인하고 허용 버튼을 누르세요.' -ForegroundColor Yellow
    Invoke-Gh @('auth', 'login', '--hostname', 'github.com', '--web', '--git-protocol', 'https') 'GitHub 로그인이 완료되지 않았습니다.'
}
$repository = Read-Host 'GitHub 저장소 이름 (그대로 Enter: narpheus-cpu/myalldocs)'
if ([string]::IsNullOrWhiteSpace($repository)) { $repository = 'narpheus-cpu/myalldocs' }
if ($repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    Stop-Friendly '저장소 이름은 아이디/저장소 형식이어야 합니다.'
}
try {
    $repoJson = & gh repo view $repository --json isPrivate,nameWithOwner
    if ($LASTEXITCODE -ne 0 -or -not $repoJson) { throw 'repository lookup failed' }
    $repoInfo = $repoJson | ConvertFrom-Json
} catch {
    Stop-Friendly '저장소를 찾지 못했습니다. GitHub 주소와 로그인 계정을 확인하세요.'
}
if ($repoInfo.isPrivate) {
    Stop-Friendly '비용 0원 정책 때문에 비공개 저장소에서는 실행할 수 없습니다. 저장소를 Public으로 바꾸세요.'
}
Write-Host ('확인 완료: ' + $repoInfo.nameWithOwner + ' (Public)') -ForegroundColor Green

Write-Title '2/5  프로젝트를 GitHub에 올리기'
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.git'))) {
    & git init -b main
    if ($LASTEXITCODE -ne 0) { Stop-Friendly '로컬 Git 저장소를 만들지 못했습니다.' }
    & git remote add origin ('https://github.com/' + $repository + '.git')
}
$origin = (& git remote get-url origin 2>$null)
if (-not $origin) { & git remote add origin ('https://github.com/' + $repository + '.git') }
$login = (& gh api user --jq .login).Trim()
if (-not $login) { $login = 'book-indexer-user' }
& git config user.name $login
& git config user.email ($login + '@users.noreply.github.com')
& git add .
& git diff --cached --quiet
if ($LASTEXITCODE -ne 0) {
    & git commit -m 'Install personal book knowledge base'
    if ($LASTEXITCODE -ne 0) { Stop-Friendly '프로젝트 저장에 실패했습니다.' }
}
& git push -u origin main
if ($LASTEXITCODE -ne 0) {
    Stop-Friendly 'GitHub 업로드에 실패했습니다. 원격 저장소에 다른 파일이 있다면 README의 도움말을 확인하세요.'
}

Write-Title '3/5  Google Drive 읽기 권한 준비'
Write-Host '브라우저에서 다음 네 가지만 합니다:'
Write-Host '  ① 무료 Google Cloud 프로젝트 선택 또는 만들기'
Write-Host '  ② Google Drive API 사용 버튼 누르기'
Write-Host '  ③ 서비스 계정 만들기'
Write-Host '  ④ 서비스 계정의 JSON 키 내려받기'
Start-Process 'https://console.cloud.google.com/apis/library/drive.googleapis.com'
Start-Process 'https://console.cloud.google.com/iam-admin/serviceaccounts'
Wait-Enter 'JSON 파일을 내려받았으면 Enter를 누르세요'
try {
    $servicePath = Select-ServiceAccountFile
    $serviceRaw = Get-Content -LiteralPath $servicePath -Raw -Encoding UTF8
    $service = $serviceRaw | ConvertFrom-Json
} catch {
    Stop-Friendly ('JSON 파일을 읽지 못했습니다: ' + $_.Exception.Message)
}
if ($service.type -ne 'service_account' -or -not $service.client_email -or -not $service.private_key) {
    Stop-Friendly '선택한 파일은 올바른 Google 서비스 계정 JSON이 아닙니다.'
}
Write-Host ''
Write-Host 'Drive에서 [book] 폴더를 이 이메일에 "뷰어"로 공유하세요:' -ForegroundColor Yellow
Write-Host $service.client_email -ForegroundColor Cyan
Start-Process 'https://drive.google.com/'
Wait-Enter '폴더 공유를 마쳤으면 Enter를 누르세요'
$folderInput = Read-Host '[book] 폴더 주소를 붙여넣으세요'
try { $folderId = Get-FolderId $folderInput } catch { Stop-Friendly $_.Exception.Message }

Write-Title '4/5  Gemini 무료 키와 비밀값 자동 등록'
Write-Host 'Google AI Studio Free Tier에서 만든 키 한 개를 붙여넣으세요.'
$secureKey = Read-Host 'GEMINI API Key (입력 문자는 화면에 보이지 않음)' -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
try {
    $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($plainKey)) { throw 'Gemini API Key가 비어 있습니다.' }
    $plainKey | & gh secret set GEMINI_API_KEY --repo $repository
    if ($LASTEXITCODE -ne 0) { throw 'Gemini key 등록에 실패했습니다.' }
} catch {
    Stop-Friendly $_.Exception.Message
} finally {
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    $plainKey = $null
    $secureKey = $null
}
$serviceRaw | & gh secret set GOOGLE_SERVICE_ACCOUNT_JSON --repo $repository
if ($LASTEXITCODE -ne 0) { Stop-Friendly '서비스 계정 비밀값 등록에 실패했습니다.' }
$serviceRaw = $null
$service = $null
Invoke-Gh @('variable', 'set', 'DRIVE_ROOT_FOLDER_ID', '--body', $folderId, '--repo', $repository) 'Drive 폴더 설정에 실패했습니다.'
Write-Host '비밀값은 GitHub Secrets로만 등록했습니다.' -ForegroundColor Green

Write-Title '5/5  무료 Pages와 첫 인덱싱 시작'
& gh api --method POST ('repos/' + $repository + '/pages') -f build_type=workflow *> $null
& gh workflow run deploy-pages.yml --repo $repository
if ($LASTEXITCODE -ne 0) { Write-Host 'Pages는 이미 설정되었거나 GitHub에서 한 번 확인이 필요할 수 있습니다.' -ForegroundColor Yellow }
Invoke-Gh @('workflow', 'run', 'index-books.yml', '--repo', $repository, '-f', ('folder_id=' + $folderId), '-f', 'recursive=true', '-f', 'force_reindex=false', '-f', 'analysis_profile=') '첫 인덱싱 시작에 실패했습니다.'

$repoUrl = 'https://github.com/' + $repository
Start-Process ($repoUrl + '/actions')
Write-Host ''
Write-Host '기본 설정이 끝났고 첫 인덱싱을 시작했습니다.' -ForegroundColor Green
Write-Host '열린 GitHub Actions 화면에서 노란색은 실행 중, 초록색은 완료입니다.'
Write-Host '웹의 Drive 폴더 선택 버튼과 완료 이메일은 초보자 안내서의 "선택 기능"에서 나중에 켤 수 있습니다.'
Write-Host ('내 사이트 주소: https://' + $repository.Split('/')[0] + '.github.io/' + $repository.Split('/')[1] + '/') -ForegroundColor Cyan
Wait-Enter 'Enter를 누르면 이 창을 닫습니다'
