"""MCP가 어느 Task에 있는지: 환경값·헤더. 폴더를 다시 계산하지 않고, 옛 서버·옛 Agent와도 끊기지 않는다."""
import re
from pathlib import Path
from types import SimpleNamespace

from agent.dds_agent import main as M
from agent.dds_agent.config import AgentSettings
from agent.dds_agent.server_api import ServerApi

ROOT = Path(__file__).resolve().parents[2]
WORK = {"id": 7, "uid": "w" * 32, "seq": 2, "name": "2차", "legacy": False}


def _env(work=None, cwd="C:/ws/P1/T02_2차", project_dir="C:/ws/P1") -> dict:
    s = SimpleNamespace(server_url="http://s", workspace_dir=Path("C:/ws"), token="t", token_in_file=True)
    fake = SimpleNamespace(s=s)
    return {e["name"]: e["value"] for e in M.Agent.mcp_servers(fake, 1, None, cwd=cwd, uid="u1", work=work, project_dir=project_dir)[0]["env"]}


def test_task_session_gets_work_env():
    env = _env(WORK)
    assert env["DDS_WORK_ID"] == "7" and env["DDS_WORK_UID"] == WORK["uid"] and env["DDS_WORK_DIR"] == "C:/ws/P1/T02_2차"
    assert env["DDS_PROJECT_DIR"] == "C:/ws/P1"


def test_legacy_session_has_id_for_the_server_but_no_task_folder():
    env = _env({"id": 3, "uid": "x", "seq": 0, "name": "이전 기록", "legacy": True}, cwd="C:/ws/P1", project_dir=None)
    assert env["DDS_WORK_ID"] == "3" and "DDS_WORK_DIR" not in env and "DDS_WORK_UID" not in env
    assert env["DDS_PROJECT_DIR"] == "C:/ws/P1"


def test_old_server_session_has_no_work_env():
    env = _env(None, cwd="C:/ws/P1", project_dir=None)
    assert not [k for k in env if k.startswith("DDS_WORK_")] and env["DDS_PROJECT_DIR"] == "C:/ws/P1"


def test_header_only_when_session_says_so(monkeypatch):
    s = AgentSettings(server_url="http://s", token="t")
    monkeypatch.delenv("DDS_WORK_ID", raising=False)
    assert "x-dds-work" not in ServerApi(s).client.headers
    monkeypatch.setenv("DDS_WORK_ID", "7")
    assert ServerApi(s).client.headers["x-dds-work"] == "7"


def test_work_base_uses_session_folder(monkeypatch, tmp_path):
    t = tmp_path / "T02"
    t.mkdir()
    s = AgentSettings(server_url="http://s", token="t", workspace_dir=tmp_path)
    monkeypatch.setenv("DDS_PROJECT_ID", "1")
    monkeypatch.setenv("DDS_WORK_DIR", str(t))
    assert s.work_base(1) == t
    other = s.work_base(2, "다른", "u2")  # 다른 Project를 말하면 그 Project 폴더다
    assert other.name == "P0002_다른" and other != t
    monkeypatch.delenv("DDS_WORK_DIR")
    monkeypatch.setenv("DDS_PROJECT_DIR", str(tmp_path / "P0001_x"))
    (tmp_path / "P0001_x").mkdir()
    assert s.work_base(1) == tmp_path / "P0001_x", "이전 기록은 Project 폴더다"


def test_work_base_ignores_missing_folder(monkeypatch, tmp_path):
    s = AgentSettings(server_url="http://s", token="t", workspace_dir=tmp_path)
    monkeypatch.setenv("DDS_PROJECT_ID", "1")
    monkeypatch.setenv("DDS_WORK_DIR", str(tmp_path / "없음"))
    assert s.work_base(1, "이름", "u1").name == "P0001_이름"


def test_no_bare_project_dir_in_mcp():
    """MCP의 상대 경로 기준은 Task 폴더다. project_dir을 바로 부르면 이전 기록 자리에 파일이 흩어진다."""
    for f in (ROOT / "mcp_servers").glob("*.py"):
        hits = re.findall(r".*settings\.project_dir\(.*", f.read_text(encoding="utf-8"))
        assert not hits, f"{f.name}: {hits}"


def test_runner_sends_extra_dirs_only_when_advertised():
    from agent.dds_agent.acp_runner import AcpRunner

    r = AcpRunner(["x"], lambda *a: None, lambda *a: None)
    assert r._dirs_params(None) == {}
    r.agent_info = {"agentCapabilities": {}}
    got = r._dirs_params(["C:/ws/P1"])
    assert "additionalDirectories" not in got and got["_meta"]["additionalRoots"]
    for cap in ({}, True):
        r.agent_info = {"agentCapabilities": {"sessionCapabilities": {"additionalDirectories": cap}}}
        assert r._dirs_params(["C:/ws/P1"])["additionalDirectories"]


def test_gemini_trust_list_is_our_own_file(tmp_path, monkeypatch):
    """사용자의 전역 파일을 고치지 않는다. 작업 공간과 직접 정한 폴더만 적는다."""
    monkeypatch.setattr(M, "CONFIG_PATH", tmp_path / "home" / "agent.toml")
    links = {"u1": "D:/mine"}
    s = SimpleNamespace(workspace_dir=tmp_path / "ws", workspace=SimpleNamespace(_links=lambda: links))
    path = M.Agent._gemini_trust_file(SimpleNamespace(s=s))
    import json

    assert Path(path).parent == tmp_path / "home"
    assert json.loads(Path(path).read_text(encoding="utf-8")) == {str(tmp_path / "ws"): "TRUST_FOLDER", "D:/mine": "TRUST_FOLDER"}
