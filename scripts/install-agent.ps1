# DDS PC Agent 원클릭 설치/업데이트/복구 스크립트 (Windows PowerShell 5.1+)
# 웹앱 > 설정 > PC Agent 에서 복사한 명령으로 실행합니다:
#   $env:DDS_TOKEN='dds_...'; $env:DDS_AGENTS='gemini'; irm http://<서버>/api/agent/install.ps1 | iex
# 사용자 PC에 Python/Node가 없어도 됩니다. 포터블 런타임을 서버에서 받아 사용자 폴더에만 설치하고, 관리자 권한도 필요 없습니다.
# 다시 실행하면 최신 버전으로 복구/업데이트됩니다.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:DDS_UTF8_OUTPUT -eq '1') { [Console]::OutputEncoding = [Text.Encoding]::UTF8 }   # Setup.exe 로그 창용
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Server = if ($env:DDS_SERVER_URL) { $env:DDS_SERVER_URL } else { '__DDS_SERVER_URL__' }
$Token = $env:DDS_TOKEN
$Agents = if ($env:DDS_AGENTS) { $env:DDS_AGENTS } else { '' }
$Home_ = if ($env:DDS_AGENT_HOME) { $env:DDS_AGENT_HOME } else { Join-Path $env:LOCALAPPDATA 'DDS-Agent' }
$NoSystem = ($env:DDS_INSTALL_NO_SYSTEM -eq '1')   # 테스트용: 사용자 PATH·시작프로그램을 바꾸지 않음

function Step($msg) { Write-Host "`n▶ $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "  ✗ $msg" -ForegroundColor Red; throw $msg }

if (-not $Token) { $Token = Read-Host 'PC Agent 토큰을 입력하세요 (웹앱 > 설정 > PC Agent)' }
$H = @{ Authorization = "Bearer $Token" }

function Api($path) { Invoke-RestMethod -Uri "$Server$path" -Headers $H -UseBasicParsing }
function Download($path, $dest, $sha) {
    Invoke-WebRequest -Uri "$Server$path" -Headers $H -OutFile $dest -UseBasicParsing
    if ($sha -and ((Get-FileHash $dest -Algorithm SHA256).Hash.ToLower() -ne $sha.ToLower())) {
        Remove-Item $dest -Force
        Fail "파일 검증 실패: $dest"
    }
}

Write-Host '==============================================' -ForegroundColor DarkCyan
Write-Host ' DDS AI Engineer — PC Agent 설치' -ForegroundColor White
Write-Host " 서버: $Server"
Write-Host " 설치 위치: $Home_"
Write-Host '==============================================' -ForegroundColor DarkCyan

# 0. 서버 연결·토큰 확인
Step '서버 연결과 토큰 확인'
try { $me = Api '/api/auth/me' } catch { Fail "서버에 연결할 수 없거나 토큰이 올바르지 않습니다: $($_.Exception.Message)" }
Ok "사용자: $($me.display_name) ($($me.username))"

foreach ($d in 'runtime', 'versions', 'downloads', 'logs', 'npm-global') { New-Item -ItemType Directory -Force (Join-Path $Home_ $d) | Out-Null }
$Dl = Join-Path $Home_ 'downloads'

# 실행 중인 Agent 중지(업데이트/복구 시)
Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Home_*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

$m = Api '/api/agent/runtime/manifest'

# 1. 포터블 Python
Step "포터블 Python $($m.python.version)"
$PyDir = Join-Path $Home_ 'runtime\python'
$Py = Join-Path $PyDir 'python.exe'
$PyVerFile = Join-Path $PyDir '.dds-version'
if ((Test-Path $Py) -and (Test-Path $PyVerFile) -and ((Get-Content $PyVerFile) -eq $m.python.sha256)) {
    Ok '이미 설치됨'
} else {
    $f = Join-Path $Dl $m.python.file
    Download '/api/agent/runtime/python' $f $m.python.sha256
    if (Test-Path $PyDir) { Remove-Item $PyDir -Recurse -Force }
    & (Join-Path $env:SystemRoot 'System32\tar.exe') -xzf $f -C (Join-Path $Home_ 'runtime')   # 'python' 폴더로 풀린다
    if (-not (Test-Path $Py)) { Fail 'Python 압축 해제 실패' }
    Set-Content $PyVerFile $m.python.sha256
    Ok '설치 완료'
}

# 2. 포터블 Node.js (에이전트 CLI 설치·실행용)
Step "포터블 Node.js $($m.node.version)"
$NodeDir = Join-Path $Home_ 'runtime\node'
$NodeVerFile = Join-Path $NodeDir '.dds-version'
if ((Test-Path (Join-Path $NodeDir 'node.exe')) -and (Test-Path $NodeVerFile) -and ((Get-Content $NodeVerFile) -eq $m.node.sha256)) {
    Ok '이미 설치됨'
} else {
    $f = Join-Path $Dl $m.node.file
    Download '/api/agent/runtime/node' $f $m.node.sha256
    if (Test-Path $NodeDir) { Remove-Item $NodeDir -Recurse -Force }
    Expand-Archive $f -DestinationPath (Join-Path $Home_ 'runtime') -Force
    Rename-Item (Join-Path $Home_ "runtime\$($m.node.folder)") 'node'
    Set-Content $NodeVerFile $m.node.sha256
    Ok '설치 완료'
}

# 사용자 PATH에 node·npm 전역 폴더 추가(터미널에서 gemini/claude/codex 로그인용) — 사용자 범위만 변경
$NpmGlobal = Join-Path $Home_ 'npm-global'
if (-not $NoSystem) {
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    foreach ($p in @($NodeDir, $NpmGlobal)) {
        if (-not ($userPath -split ';' | Where-Object { $_ -eq $p })) { $userPath = "$p;$userPath" }
    }
    [Environment]::SetEnvironmentVariable('Path', $userPath, 'User')
    [Environment]::SetEnvironmentVariable('NPM_CONFIG_PREFIX', $NpmGlobal, 'User')
}
$env:Path = "$NodeDir;$NpmGlobal;$env:Path"
$env:NPM_CONFIG_PREFIX = $NpmGlobal

# 3. 최신 Agent
$rel = Api '/api/agent/releases/latest'
Step "DDS Agent $($rel.version)"
$VerDir = Join-Path $Home_ "versions\$($rel.version)"
if (-not (Test-Path (Join-Path $VerDir '.installed'))) {
    $f = Join-Path $Dl $rel.filename
    Download "/api/agent/releases/$($rel.version)/download" $f $rel.sha256
    if (Test-Path $VerDir) { Remove-Item $VerDir -Recurse -Force }
    Expand-Archive $f -DestinationPath $VerDir -Force

    Step '의존성 설치 (pip)'
    $req = Join-Path $VerDir 'requirements-agent.txt'
    $installed = $false
    if ($m.wheels) {
        $wz = Join-Path $Dl $m.wheels.file
        if (-not (Test-Path $wz) -or ((Get-FileHash $wz -Algorithm SHA256).Hash.ToLower() -ne $m.wheels.sha256)) {
            Download '/api/agent/runtime/wheels' $wz $m.wheels.sha256
        }
        $wd = Join-Path $Dl 'wheels'
        if (Test-Path $wd) { Remove-Item $wd -Recurse -Force }
        Expand-Archive $wz -DestinationPath $wd -Force
        & $Py -m pip install -q --no-index --find-links $wd -r $req
        $installed = ($LASTEXITCODE -eq 0)
    }
    if (-not $installed) {
        Write-Host '  서버 wheel로 부족해 사내 pip 미러에서 설치합니다...'
        & $Py -m pip install -q -r $req
        if ($LASTEXITCODE -ne 0) { Fail 'pip 설치 실패 (사내 pip 미러 설정을 확인하세요: pip config list)' }
    }
    Set-Content (Join-Path $VerDir '.installed') $rel.version
}
$cur = Join-Path $Home_ 'current.txt'
if ((Test-Path $cur) -and ((Get-Content $cur) -ne $rel.version)) { Copy-Item $cur (Join-Path $Home_ 'previous.txt') -Force }
Set-Content $cur $rel.version
Copy-Item (Join-Path $VerDir 'agent\launcher.py') (Join-Path $Home_ 'launcher.py') -Force
Ok "설치 완료 ($VerDir)"

# 4. 설정 파일 (%USERPROFILE%\.dds-agent\agent.toml) — 서버·토큰만 갱신, 나머지 사용자 설정은 유지
Step '설정 파일'
$CfgDir = if ($env:DDS_AGENT_CONFIG) { Split-Path $env:DDS_AGENT_CONFIG } else { Join-Path $env:USERPROFILE '.dds-agent' }
New-Item -ItemType Directory -Force $CfgDir | Out-Null
$Cfg = Join-Path $CfgDir 'agent.toml'
if (Test-Path $Cfg) {
    $lines = Get-Content $Cfg -Encoding UTF8 | Where-Object { $_ -notmatch '^\s*(server_url|token)\s*=' }
} else {
    $lines = @('# DDS PC Agent 설정. 툴 경로 예: [tools] abaqus = "C:\\SIMULIA\\Commands\\abaqus.bat"',
               "workspace_dir = '$($env:USERPROFILE)\DDS_Workspace'", '')
}
$head = @("server_url = '$Server'", "token = '$Token'")
[IO.File]::WriteAllLines($Cfg, [string[]]($head + $lines), (New-Object Text.UTF8Encoding $false))
Ok $Cfg

# 5. 에이전트 CLI (선택)
$pkgs = @{
    'gemini' = @('@google/gemini-cli');
    'claude' = @('@anthropic-ai/claude-code', '@agentclientprotocol/claude-agent-acp');
    'codex'  = @('@openai/codex', '@agentclientprotocol/codex-acp')
}
$login = @{ 'gemini' = 'gemini'; 'claude' = 'claude  (실행 후 /login)'; 'codex' = 'codex login' }
$chosen = @($Agents -split '[,; ]' | Where-Object { $_ })
foreach ($a in $chosen) {
    if (-not $pkgs.ContainsKey($a)) { Write-Host "  알 수 없는 에이전트: $a (gemini, claude, codex 중 선택)"; continue }
    Step "에이전트 CLI 설치: $a"
    & (Join-Path $NodeDir 'npm.cmd') install -g --silent @($pkgs[$a])
    if ($LASTEXITCODE -ne 0) { Write-Host "  ✗ $a 설치 실패 (사내 npm 미러 설정 확인: npm config get registry)" -ForegroundColor Yellow }
    else { Ok "$a 설치 완료" }
}

# 6. 로그인 시 자동 실행 + 지금 실행
Step '자동 실행 등록'
$Pyw = Join-Path $PyDir 'pythonw.exe'
$Launcher = Join-Path $Home_ 'launcher.py'
if (-not $NoSystem) {
    $Startup = [Environment]::GetFolderPath('Startup')
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $Startup 'DDS Agent.lnk'))
    $sc.TargetPath = $Pyw
    $sc.Arguments = "`"$Launcher`""
    $sc.WorkingDirectory = $Home_
    $sc.Description = 'DDS AI Engineer PC Agent'
    $sc.Save()
}
Start-Process -FilePath $Pyw -ArgumentList "`"$Launcher`"" -WorkingDirectory $Home_ -WindowStyle Hidden
Ok ('시작됨 (로그: ' + (Join-Path $Home_ 'logs\agent.log') + ')')

Write-Host "`n==============================================" -ForegroundColor DarkCyan
Write-Host ' 설치가 끝났습니다. 웹앱에서 "PC Agent 연결됨"을 확인하세요.' -ForegroundColor White
if ($chosen.Count -gt 0) {
    Write-Host ' 처음 한 번은 새 터미널을 열고 로그인하세요:' -ForegroundColor Yellow
    foreach ($a in $chosen) { if ($login.ContainsKey($a)) { Write-Host "   $($login[$a])" } }
}
Write-Host '==============================================' -ForegroundColor DarkCyan
