"""PC Agent 설치 프로그램: 웹앱과 같은 색, Agent가 실제로 쓰는 명령 확인, 낡은 exe 다시 빌드."""
import os
import re
import subprocess
from pathlib import Path

import pytest

from server.tests.test_api import client  # noqa: F401, I001 — 임시 데이터 폴더를 먼저 정한다

from agent.dds_agent.config import AgentSettings  # noqa: E402
from server.app.routers import agents as router  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "installer" / "DdsAgentSetup.cs").read_text(encoding="utf-8")
PS1 = (ROOT / "scripts" / "install-agent.ps1").read_text(encoding="utf-8-sig")


def _light_tokens():
    css = (ROOT / "frontend" / "src" / "design" / "tokens.css").read_text(encoding="utf-8")
    light = css.split('\n:root[data-theme="dark"]')[0]  # 머리 주석에도 같은 글자가 있어 줄 시작만 본다
    return {k: v.lower() for k, v in re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,8})", light)}


def test_colors_follow_web_tokens():
    tokens = _light_tokens()
    pairs = re.findall(r'FromHtml\("(#[0-9a-fA-F]{6})"\);\s*// (--[\w-]+)', SRC)
    assert len(pairs) >= 20
    wrong = [(tok, value, tokens.get(tok)) for value, tok in pairs if tokens.get(tok) != value.lower()]
    assert not wrong, f"설치 프로그램 색이 tokens.css와 다릅니다: {wrong}"
    assert SRC.count("FromHtml(") == len(pairs), "색은 토큰 이름을 적어 둔다"


def test_checks_every_command_the_agent_runs():
    """설치됨으로 표시하는 기준은 Agent가 실행하는 명령이다. claude만 있고 연결 구성요소가 없으면 대화가 시작되지 않는다."""
    cfg = AgentSettings().agents
    for key in ("gemini", "claude", "codex"):
        bins = re.search(rf'Key = "{key}".*?Bins = new\[\] \{{ (.*?) \}}', SRC, re.S).group(1)
        assert cfg[key]["command"][0] in re.findall(r'"([^"]+)"', bins), key


def test_install_script_knows_every_package():
    """이미 있는 CLI를 건너뛰는 표에 빠진 패키지가 있으면 설치 스크립트가 멈춘다."""
    cfg = AgentSettings().agents
    for key in ("gemini", "claude", "codex"):
        for pkg in cfg[key]["packages"]:
            assert f"'{pkg}' = '" in PS1, pkg


def test_install_script_skips_existing_cli(tmp_path):
    """이미 있는 명령은 npm으로 다시 설치하지 않는다. 표 만들기와 고르기 부분만 떼어 실제 PowerShell로 돌린다."""
    if os.name != "nt":
        pytest.skip("Windows 전용")
    table = PS1[PS1.index("$pkgs = @{"):PS1.index("$login = @{")]
    (tmp_path / "claude.cmd").write_text("@echo off", encoding="ascii")
    script = tmp_path / "t.ps1"
    script.write_text(
        "\ufeff$env:Path = '" + str(tmp_path) + "'\n" + table
        + "$need = @($pkgs['claude'] | Where-Object { -not (Get-Command $bins[$_] -ErrorAction SilentlyContinue) })\n"
        + "Write-Output ($need -join ',')\n", encoding="utf-8")
    p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       capture_output=True, text=True, errors="replace", timeout=60)
    assert p.stdout.strip() == "@agentclientprotocol/claude-agent-acp", p.stdout + p.stderr


def test_login_window_tells_user_not_to_close():
    for text in ("이 창을 닫지 마세요", "멈춘 것이 아닙니다", "지남", "로그인 완료"):
        assert text in SRC, text


def _csc():
    return Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"


def test_stale_exe_is_rebuilt(tmp_path, monkeypatch):
    if not _csc().exists():
        pytest.skip("csc.exe 없음")
    monkeypatch.setattr(router, "runtime_dir", lambda: tmp_path)
    exe = router._build_setup_exe()
    assert exe == tmp_path / "DDS-Agent-Setup.exe" and exe.stat().st_size > 50_000
    made = exe.stat().st_mtime
    assert not router._setup_stale(exe) and router._setup_exe() == exe and exe.stat().st_mtime == made  # 새것이면 다시 빌드하지 않는다

    os.utime(exe, (1, 1))
    assert router._setup_stale(exe)
    assert router._setup_exe() == exe and not router._setup_stale(exe)
    assert b"dds-logo.png" in exe.read_bytes()  # 로고를 넣어 빌드했다


def test_failed_rebuild_keeps_existing_exe(tmp_path, monkeypatch):
    monkeypatch.setattr(router, "runtime_dir", lambda: tmp_path)
    exe = tmp_path / "DDS-Agent-Setup.exe"
    exe.write_bytes(b"old")
    os.utime(exe, (1, 1))
    monkeypatch.setattr(router, "_build_setup_exe", lambda: None)  # 빌드할 수 없는 서버(Linux 등)
    assert router._setup_exe() == exe and exe.read_bytes() == b"old"
