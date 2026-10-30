"""원격 검증의 PC 쪽: 허용한 때만 돌고, 시험 폴더 안에서 실행하고, 토큰은 내주지 않는다."""
import asyncio
import base64
import os
import sys
import time
from pathlib import Path

import pytest

from agent.dds_agent import devtest
from agent.dds_agent import main as M
from agent.dds_agent.config import AgentSettings


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(devtest.Path, "home", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def settings(tmp_path):
    s = AgentSettings()
    s.workspace_dir = tmp_path / "ws"
    s.workspace_dir.mkdir()
    return s


def _run(settings, **req):
    return devtest.run(settings, dict(os.environ), req)


def test_python_runs_in_own_folder_and_returns_out_files(home, settings):
    script = "import os, sys, pathlib\nprint('hello', sys.argv[1])\npathlib.Path(os.environ['DDS_TEST_OUT'], 'a.txt').write_text('결과', encoding='utf-8')\nprint(open('in.txt', encoding='utf-8').read())"
    res = _run(settings, op="python", script=script, args=["x"], files={"in.txt": base64.b64encode("입력".encode()).decode()})
    assert res["ok"] and res["returncode"] == 0 and "hello x" in res["stdout"] and "입력" in res["stdout"]
    assert base64.b64decode(res["files"]["a.txt"]).decode("utf-8") == "결과"
    assert Path(res["dir"]).parent == home / ".dds-agent" / "devtest"


def test_script_can_use_agent_code(home, settings):
    res = _run(settings, op="python", script="import cae_adapters.catalog, agent.dds_agent\nprint('ok')")
    assert res["ok"], res["stderr"]


def test_failure_and_timeout_are_reported(home, settings):
    bad = _run(settings, op="python", script="raise SystemExit(3)")
    assert not bad["ok"] and bad["returncode"] == 3
    slow = _run(settings, op="python", script="import time; time.sleep(30)", timeout_sec=1)
    assert not slow["ok"] and slow["timed_out"]


def test_tool_uses_same_command_keys_as_chat(home, settings):
    res = _run(settings, op="tool", tool="python", script="print('from tool')")
    assert res["ok"] and "from tool" in res["stdout"] and res["command"]


def test_input_cannot_escape_test_folder(home, settings):
    with pytest.raises(ValueError, match="밖의 경로"):
        _run(settings, op="python", script="pass", files={"../../evil.txt": "eA=="})


def test_fetch_only_allowed_places_and_no_tokens(home, settings, tmp_path):
    inside = settings.workspace_dir / "a.txt"
    inside.write_text("x", encoding="utf-8")
    assert _run(settings, op="fetch", path=str(inside))["ok"]
    outside = tmp_path / "private.txt"
    outside.write_text("x", encoding="utf-8")
    assert "밖의 파일" in _run(settings, op="fetch", path=str(outside))["error"]
    for name in ("agent.toml", "live.token"):
        f = home / ".dds-agent" / name
        f.parent.mkdir(exist_ok=True)
        f.write_text("secret", encoding="utf-8")
        assert "토큰" in _run(settings, op="fetch", path=str(f))["error"]


def test_info_lists_version_and_tools(home, settings):
    res = _run(settings, op="info")
    assert res["ok"] and res["version"] and "agents" in res and sys.version.split()[0] == res["python"]


def test_unknown_op_rejected(home, settings):
    with pytest.raises(ValueError):
        _run(settings, op="shell")


def test_history_is_written(home, settings):
    _run(settings, op="python", script="pass", by="admin")
    text = (home / ".dds-agent" / "devtest" / "history.jsonl").read_text(encoding="utf-8")
    assert '"by": "admin"' in text and '"op": "python"' in text


@pytest.fixture
def agent(monkeypatch, tmp_path):
    monkeypatch.setenv("DDS_AGENT_CONFIG", str(tmp_path / "none.toml"))
    monkeypatch.setenv("DDS_TOKEN", "t")
    monkeypatch.setenv("DDS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(devtest, "run", lambda s, env, msg: {"ok": True, "ran": msg["op"]})
    return M.Agent()


def test_agent_refuses_until_allowed_on_this_pc(agent):
    with pytest.raises(RuntimeError, match="허용하지 않았습니다"):
        agent._devtest({"op": "info"})
    asyncio.run(agent.handle({"type": "agent.settings", "auto_update": False, "remote_test_until": time.time() + 60}))
    assert agent._devtest({"op": "info"})["ran"] == "info"
    asyncio.run(agent.handle({"type": "agent.settings", "auto_update": False, "remote_test_until": time.time() - 1}))  # 기간 만료
    with pytest.raises(RuntimeError):
        agent._devtest({"op": "info"})


def test_running_test_blocks_auto_update(agent):
    agent.devtests = 1
    assert agent.busy()
