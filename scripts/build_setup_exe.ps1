# DDS-Agent-Setup.exe 빌드 (Windows 기본 .NET Framework csc 사용 — 추가 설치 불필요)
# 결과: dist\DDS-Agent-Setup.exe  → 서버 data\agent_runtime\ 에 복사하면 /api/agent/setup.exe 로 배포된다.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $csc)) { throw "csc.exe를 찾을 수 없습니다: $csc" }
New-Item -ItemType Directory -Force (Join-Path $root 'dist') | Out-Null
$out = Join-Path $root 'dist\DDS-Agent-Setup.exe'
& $csc /nologo /target:winexe /codepage:65001 /optimize+ "/out:$out" `
    "/win32icon:$(Join-Path $root 'installer\dds-agent.ico')" `
    "/resource:$(Join-Path $root 'frontend\public\brand\dds-logo.png'),dds-logo.png" `
    /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll `
    (Join-Path $root 'installer\DdsAgentSetup.cs')
if ($LASTEXITCODE -ne 0) { throw 'csc 빌드 실패' }
$rt = Join-Path $root 'data\agent_runtime'
if (Test-Path $rt) { Copy-Item $out $rt -Force; Write-Host "서버 배포 폴더에 복사: $rt" }
Write-Host "빌드 완료: $out"
