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

function New-ConnectedWorkingCopy([string]$SourceRoot, [string]$RemoteUrl) {
    $parent = Split-Path -Parent $SourceRoot
    $target = Join-Path $parent 'myalldocs-connected'
    if (Test-Path -LiteralPath $target) {
        $target = Join-Path $parent ('myalldocs-connected-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
    }
    Write-Host 'GitHub의 기존 파일을 먼저 안전한 새 폴더로 내려받습니다.' -ForegroundColor Yellow
    $cloneMessages = & git clone --branch main --single-branch $RemoteUrl $target 2>&1
    $cloneExitCode = $LASTEXITCODE
    $cloneMessages | ForEach-Object { Write-Host $_ }
    if ($cloneExitCode -ne 0) { throw 'GitHub 기존 파일을 내려받지 못했습니다.' }

    # Existing indexed data and browser configuration belong to the user. Keep the
    # remote copies while overlaying application files from this installer.
    $copyMessages = & robocopy.exe $SourceRoot $target /E /XD `
        (Join-Path $SourceRoot '.git') `
        (Join-Path $SourceRoot 'data') `
        (Join-Path $SourceRoot '.venv') `
        (Join-Path $SourceRoot '.venv-run') `
        (Join-Path $SourceRoot '.runtime-tmp') `
        (Join-Path $SourceRoot '.test-tmp') `
        /XF (Join-Path $SourceRoot 'config\public-config.js') `
        /NFL /NDL /NJH /NJS /NP 2>&1
    $copyExitCode = $LASTEXITCODE
    $copyMessages | Where-Object { $_ -and $_.Trim() } | ForEach-Object { Write-Host $_ }
    if ($copyExitCode -gt 7) { throw '프로젝트 파일을 안전한 새 폴더로 복사하지 못했습니다.' }
    if (-not (Test-Path -LiteralPath (Join-Path $target 'data'))) {
        Copy-Item -LiteralPath (Join-Path $SourceRoot 'data') -Destination (Join-Path $target 'data') -Recurse
    }
    $publicConfig = Join-Path $target 'config\public-config.js'
    if (-not (Test-Path -LiteralPath $publicConfig)) {
        Copy-Item -LiteralPath (Join-Path $SourceRoot 'config\public-config.js') -Destination $publicConfig
    }
    Write-Host ('기존 파일을 보존한 연결 폴더: ' + $target) -ForegroundColor Green
    Write-Output -NoEnumerate ([string]$target)
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
$remoteUrl = 'https://github.com/' + $repository + '.git'
$remoteHead = & git ls-remote --heads $remoteUrl refs/heads/main 2>$null
if ($LASTEXITCODE -ne 0) { Stop-Friendly 'GitHub 저장소 내용을 확인하지 못했습니다.' }
$projectAlreadyInstalled = $false
if ($remoteHead) {
    & gh api ('repos/' + $repository + '/contents/indexer/main.py?ref=main') --silent *> $null
    $projectAlreadyInstalled = ($LASTEXITCODE -eq 0)
}

if ($projectAlreadyInstalled) {
    Write-Host '프로젝트가 이미 GitHub에 올라가 있어 2번을 자동으로 건너뜁니다.' -ForegroundColor Green
} else {
    if ($remoteHead) {
        $canUseCurrent = $false
        if (Test-Path -LiteralPath (Join-Path $ProjectRoot '.git')) {
            $currentOrigin = & git remote get-url origin 2>$null
            if ($LASTEXITCODE -eq 0 -and $currentOrigin -match [regex]::Escape($repository)) {
                & git fetch origin main
                if ($LASTEXITCODE -eq 0) {
                    & git merge-base --is-ancestor origin/main HEAD
                    if ($LASTEXITCODE -eq 0) {
                        $canUseCurrent = $true
                    } else {
                        & git merge-base --is-ancestor HEAD origin/main
                        if ($LASTEXITCODE -eq 0) {
                            & git pull --ff-only origin main
                            $canUseCurrent = ($LASTEXITCODE -eq 0)
                        }
                    }
                }
            }
        }
        if (-not $canUseCurrent) {
            try {
                $connectedResult = @(New-ConnectedWorkingCopy $ProjectRoot $remoteUrl)
                $ProjectRoot = [string]$connectedResult[-1]
                Set-Location -LiteralPath $ProjectRoot
            } catch {
                Stop-Friendly $_.Exception.Message
            }
        }
    } elseif (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.git'))) {
        & git init -b main
        if ($LASTEXITCODE -ne 0) { Stop-Friendly '로컬 Git 저장소를 만들지 못했습니다.' }
        & git remote add origin $remoteUrl
    }
    $origin = (& git remote get-url origin 2>$null)
    if (-not $origin) { & git remote add origin $remoteUrl }
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
        Stop-Friendly 'GitHub 업로드에 실패했습니다. 기존 파일은 변경되지 않았습니다.'
    }
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
