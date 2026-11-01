"""AI CLI 환경에 토큰이 없고, MCP는 그래도 토큰을 얻는지(situ 5단계). 시험 값만 쓴다."""
import json
import os
import subprocess
import sys

import pytest

from agent.dds_agent import config
from agent.dds_agent import main as M
from cae_adapters.hidden import hidden_kwargs

TOKEN = "dds_test_only_value"


def _agent(monkeypatch, tmp_path, in_file: bool, in_env: bool):
    cfg = tmp_path / "agent.toml"
    lines = [f"workspace_dir = {json.dumps(str(tmp_path / 'ws'))}", "tray = false"]
    if in_file:
        lines.append(f"token = {json.dumps(TOKEN)}")
    cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    monkeypatch.setattr(M, "CONFIG_PATH", cfg)
    monkeypatch.setenv("DDS_AGENT_CONFIG", str(cfg))
    monkeypatch.setenv("DDS_WORKSPACE", str(tmp_path / "ws"))
    if in_env:
        monkeypatch.setenv("DDS_TOKEN", TOKEN)
    else:
        monkeypatch.delenv("DDS_TOKEN", raising=False)
    return M.Agent()


def _mcp_env(agent) -> dict:
    defs = agent.mcp_servers(1, "claude", None, "u")
    assert len(defs) == len(M.MCP_SERVERS)
    envs = [{e["name"]: e["value"] for e in d["env"]} for d in defs]
    assert all(e == envs[0] for e in envs)
    return envs[0]


@pytest.mark.parametrize("name", ["claude", "gemini", "codex"])
@pytest.mark.parametrize("in_file,in_env", [(True, False), (True, True), (False, True)])
def test_cli_environment_has_no_token(monkeypatch, tmp_path, name, in_file, in_env):
    a = _agent(monkeypatch, tmp_path, in_file, in_env)
    try:
        assert a.s.token == TOKEN
        env = a._runner_env(name)
        assert "DDS_TOKEN" not in env and TOKEN not in json.dumps(env)
    finally:
        a.shutdown()


@pytest.mark.parametrize("in_env", [False, True])
def test_installed_agent_keeps_token_out_of_mcp_definition(monkeypatch, tmp_path, in_env):
    """정의는 CLI 안으로 들어가고 CLI에 따라 명령줄에 실린다. 토큰이 파일에 있으면 적지 않는다."""
    a = _agent(monkeypatch, tmp_path, True, in_env)
    try:
        assert "DDS_TOKEN" not in _mcp_env(a) and TOKEN not in json.dumps(a.mcp_servers(1, "codex", None, "u"))
    finally:
        a.shutdown()


def test_env_only_agent_passes_token_through_mcp_definition(monkeypatch, tmp_path):
    a = _agent(monkeypatch, tmp_path, False, True)
    try:
        assert _mcp_env(a)["DDS_TOKEN"] == TOKEN
    finally:
        a.shutdown()


def test_case_process_does_not_inherit_token_from_first_run(monkeypatch, tmp_path):
    """설치 직후 첫 실행은 환경 변수로 토큰을 받는다. 케이스 실행은 Agent의 환경을 그대로 물려받으므로(tool_commands) 파일에 있으면 지운다."""
    a = _agent(monkeypatch, tmp_path, True, True)
    try:
        assert a.s.token == TOKEN and "DDS_TOKEN" not in os.environ
    finally:
        a.shutdown()


def test_env_only_agent_keeps_token_it_cannot_read_again(monkeypatch, tmp_path):
    a = _agent(monkeypatch, tmp_path, False, True)
    try:
        assert os.environ["DDS_TOKEN"] == TOKEN
    finally:
        a.shutdown()


def test_real_mcp_process_finds_token_without_it_in_environment(monkeypatch, tmp_path):
    """CLI가 MCP를 띄우는 환경 그대로(CLI 환경 + 정의의 env). 그래도 서버를 부를 토큰을 찾아야 MCP가 망가지지 않는다."""
    a = _agent(monkeypatch, tmp_path, True, False)
    try:
        env = {**a._runner_env("claude"), **_mcp_env(a)}
    finally:
        a.shutdown()
    assert "DDS_TOKEN" not in env
    code = ("import os, sys\n"
            f"sys.path.insert(0, {json.dumps(str(M.REPO_ROOT / 'mcp_servers'))})\n"
            "from _common import settings\n"
            f"sys.exit(0 if settings.token == {TOKEN!r} and 'DDS_TOKEN' not in os.environ else 1)\n")
    p = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                       stdin=subprocess.DEVNULL, **hidden_kwargs())
    assert p.returncode == 0, p.stderr[-800:]
