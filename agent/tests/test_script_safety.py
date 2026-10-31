"""PC Agent·MCP 쪽 안전 규칙(상황 지도 16·73·86행)과 번호만 있는 폴더 재현(MCP가 uid 없이 먼저 부르는 경우)."""
import sys
import threading
from pathlib import Path

import pytest

from agent.dds_agent.config import AgentSettings
from agent.dds_agent.executor import CaseExecutor
from agent.dds_agent.workspace import Workspace, _is_alias
from cae_adapters.base import check_script_path
from cae_adapters.script import ScriptAdapter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp_servers"))
import _common  # noqa: E402
import dds_cae  # noqa: E402


# ---------- 16행: 스크립트는 프로젝트 안 상대 경로만 ----------
@pytest.mark.parametrize("script", ["C:/Windows/evil.py", "C:\\Windows\\evil.py", "/etc/evil.py", "\\\\서버\\공유\\evil.py", "../evil.py",
                                    "scripts/../../evil.py", "scripts\\..\\..\\evil.py", "c:evil.py", "scripts/a.py:stream", "... /evil.py", "", " "])
def test_check_script_path_refuses(script):
    with pytest.raises(ValueError, match="상대 경로"):
        check_script_path(script)


@pytest.mark.parametrize("script", ["scripts/run.py", "./scripts/run.py", ".claude/skills/abaqus/scripts/a.py", "scripts\\run.py", "run.py", "한글 폴더/해석.py"])
def test_check_script_path_accepts(script):
    check_script_path(script)


def _outside_step(tmp_path, script):
    project, run_dir = tmp_path / "P0001_x", tmp_path / "P0001_x" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    return ScriptAdapter._commands([{"tool": "python", "script": script}], run_dir, project, {}, None, None)


def test_agent_refuses_absolute_script_even_if_the_server_let_it_through(tmp_path):
    """옛 서버·옛 스터디 설정이 와도 PC에서 다시 막는다. 받는 쪽이 마지막 관문이다."""
    evil = tmp_path / "evil.py"
    evil.write_text("print('밖')", encoding="utf-8")
    with pytest.raises(ValueError, match="상대 경로"):
        _outside_step(tmp_path, str(evil))
    with pytest.raises(ValueError, match="상대 경로"):
        _outside_step(tmp_path, "../evil.py")
    assert not (tmp_path / "P0001_x" / "run" / "evil.py").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="접합은 Windows")
def test_agent_refuses_script_reached_through_a_junction(tmp_path):
    """상대 경로처럼 보여도 폴더 접합으로 밖을 가리키면 프로젝트 안이 아니다."""
    import subprocess
    outside = tmp_path / "밖"
    outside.mkdir()
    (outside / "evil.py").write_text("print('밖')", encoding="utf-8")
    project = tmp_path / "P0001_x"
    (project / "run").mkdir(parents=True)
    subprocess.run(["cmd", "/c", "mklink", "/J", str(project / "link"), str(outside)], check=True, capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    with pytest.raises(ValueError, match="프로젝트 폴더 밖"):
        ScriptAdapter._commands([{"tool": "python", "script": "link/evil.py"}], project / "run", project, {}, None, None)


def test_agent_runs_a_script_inside_the_project(tmp_path):
    project = tmp_path / "P0001_x"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "a.py").write_text("print(1)", encoding="utf-8")
    (project / "run").mkdir()
    cmds, _ = ScriptAdapter._commands([{"tool": "python", "script": "scripts/a.py"}], project / "run", project, {}, None, None)
    assert "a.py" in cmds[0] and (project / "run" / "a.py").is_file()


def test_full_case_with_outside_script_fails_with_reason_and_runs_nothing(tmp_path):
    project = tmp_path / "P0001_x"
    project.mkdir()
    ran = []

    class Runner:
        def run(self, *a, **k):
            ran.append(a)
    out = ScriptAdapter().run_case({"x": 1}, project / "run", {"tool": "python", "script": "../evil.py"}, Runner(), {}, project_dir=project)
    assert out.status == "failed" and "상대 경로" in out.error and "→" in out.error and ran == []


# ---------- 86행: 같은 케이스를 두 번 돌리지 않고 중지 신호를 덮지 않는다 ----------
@pytest.fixture
def executor(tmp_path):
    s = AgentSettings(server_url="http://127.0.0.1:1", token="t", workspace_dir=tmp_path / "ws")
    ex = CaseExecutor(s, lambda _m: None)
    yield ex
    ex.pool.shutdown(wait=True, cancel_futures=True)


def test_second_run_of_an_active_case_is_ignored_and_stop_survives(executor):
    started, release = threading.Event(), threading.Event()
    runs: list[int] = []

    def slow(job):
        runs.append(job["case_id"])
        started.set()
        release.wait(5)
    executor._run_case = slow
    job = {"type": "case.run", "case_id": 5, "study_id": 1, "project_id": 1, "params": {}, "adapter": "mock"}
    executor.submit(job)
    assert started.wait(5)
    stop = executor._stops[5]
    executor.submit(job)
    assert executor._stops[5] is stop  # 중지 신호가 그대로다
    release.set()
    executor.pool.shutdown(wait=True)
    assert runs == [5]


def test_run_after_extract_is_not_mistaken_for_a_duplicate(executor):
    """추출이 도는 중에 온 해석을 조용히 버리면 서버에 '대기'로 영원히 남는다."""
    started, release, seen = threading.Event(), threading.Event(), []

    def slow(job):
        seen.append(job["type"])
        started.set()
        release.wait(5)
    executor._extract, executor._run_case = slow, slow
    base = {"case_id": 6, "study_id": 1, "project_id": 1, "params": {}, "adapter": "mock"}
    executor.submit({**base, "type": "case.extract"})
    assert started.wait(5)
    executor.submit({**base, "type": "case.run"})
    release.set()
    executor.pool.shutdown(wait=True)
    assert seen == ["case.extract", "case.run"]


# ---------- 73행: run_script의 파일 이름 ----------
@pytest.fixture
def cae(tmp_path, monkeypatch):
    monkeypatch.setattr(_common.settings, "workspace_dir", tmp_path / "ws")
    monkeypatch.setenv("DDS_PROJECT_ID", "77")
    monkeypatch.delenv("DDS_PROJECT_DIR", raising=False)
    monkeypatch.setattr(_common.settings, "project_lookup", None, raising=False)
    monkeypatch.setattr(dds_cae, "fresh_tools", lambda: None)
    return tmp_path


@pytest.mark.parametrize("name", ["../../밖.py", "..\\밖.py", "scripts/a.py", "scripts\\a.py", "C:\\Temp\\a.py", "C:a.py", "/tmp/a.py", "a.py:숨김", "..", ".", "", " ", "a.py.", "a.py ", "CON.py", "nul"])
def test_run_script_takes_a_name_only(cae, name):
    r = dds_cae.run_script("python", "print(1)", filename=name)
    assert "이름만 주세요" in r["error"] and "→" in r["error"]
    assert not list(cae.rglob("*.py")), "아무 파일도 쓰지 않는다"


@pytest.mark.parametrize("name", ["script.py", "해석 스크립트.py", "a.b.py", ".hidden.py"])
def test_run_script_accepts_plain_names(cae, name):
    r = dds_cae.run_script("python", "print(1)", filename=name, dry_run=True)
    assert r.get("dry_run") is True, r


def test_dry_run_writes_nothing(cae):
    r = dds_cae.run_script("python", "print(1)", filename="a.py", params={"x": 1}, dry_run=True)
    assert r["dry_run"] is True and "a.py" in " ".join(r["command"])
    assert not Path(r["run_dir"]).exists()
    assert not list(cae.rglob("a.py")) and not list(cae.rglob("params.json"))


def test_dry_run_still_says_when_an_input_is_missing(cae):
    assert "입력 파일이 없습니다" in dds_cae.run_script("python", "print(1)", inputs=["inputs/없음.cae"], dry_run=True)["error"]


# ---------- 재현: MCP가 uid 없이 먼저 불러 번호만 있는 P0001이 생기던 문제 ----------
def test_mcp_call_without_uid_never_makes_a_bare_folder_and_executor_uses_the_same_one(tmp_path, monkeypatch):
    """예전에는 MCP가 P0001을 만들고 실행기는 P0001_<이름>을 만들어 입력과 결과가 갈라졌다."""
    monkeypatch.delenv("DDS_PROJECT_ID", raising=False)
    monkeypatch.delenv("DDS_PROJECT_DIR", raising=False)
    s = AgentSettings(server_url="http://x", token="t", workspace_dir=tmp_path)
    s.project_lookup = None  # 서버에 물어보지 못하는 가장 나쁜 경우

    first = s.project_dir(1)  # MCP: 이름도 uid도 모른다
    assert first.name == "P0001_untitled" and not (tmp_path / "P0001").exists()
    (first / "inputs" / "model.dat").write_text("입력", encoding="utf-8")

    ws = Workspace(tmp_path, "http://x")
    second = ws.project_dir(1, "브래킷", "uid-1")  # 실행기: 이름과 uid를 안다
    assert second == first  # 새 폴더를 만들지 않고 받아들인다
    sdir = ws.study_dir(1, 3, "스터디", "브래킷", "uid-1")
    case = ws.case_dir(sdir, 9, "doe")
    assert case.is_relative_to(first)

    ws.sync_name(1, "uid-1", "브래킷", may_move=True)
    # 옛 이름은 접합으로 남을 수 있다(세션이 옛 경로를 들고 있어도 돌게). 실제 폴더는 하나다
    folders = sorted(p.name for p in tmp_path.iterdir() if p.is_dir() and p.name.startswith("P0001") and not _is_alias(p))
    assert folders == ["P0001_브래킷"]
    assert (tmp_path / "P0001_브래킷" / "inputs" / "model.dat").read_text(encoding="utf-8") == "입력"
    # 이름이 바뀐 뒤 MCP가 다시 불러도 같은 폴더다
    assert s.project_dir(1).name == "P0001_브래킷"
    assert not (tmp_path / "P0001").exists()


def test_mcp_with_server_lookup_starts_with_the_real_name(tmp_path, monkeypatch):
    monkeypatch.delenv("DDS_PROJECT_ID", raising=False)
    s = AgentSettings(server_url="http://x", token="t", workspace_dir=tmp_path)
    s.project_lookup = lambda pid: {"name": "스파이더", "uid": "uid-2"}
    d = s.project_dir(2)
    assert d.name == "P0002_스파이더" and Workspace(tmp_path, "http://x").project_dir(2, "스파이더", "uid-2") == d


# ---------- 재기반 확인: 폴더 이름이 바뀌어도 내 케이스를 '불러온 원본'으로 오해하지 않는다 ----------
def test_case_under_old_project_name_is_not_taken_for_an_external_source(tmp_path):
    """서버에는 옛 이름의 run_dir이 남아 있다. 원본 취급하면 추출을 엉뚱한 작업 폴더에서 돌리고 source_dir을 잘못 남긴다."""
    s = AgentSettings(server_url="http://x", token="t", workspace_dir=tmp_path)
    ws = s.workspace
    sdir = ws.study_dir(25, 3, "DOE", "처음", "u25")
    case = ws.case_dir(sdir, 7, "doe")
    old_path = str(case)
    ws.sync_name(25, "u25", "다음", may_move=True)
    new_sdir = ws.study_dir(25, 3, "DOE", "다음", "u25")
    ex = CaseExecutor(s, lambda _m: None)
    job = {"run_dir": old_path, "project_id": 25, "study_id": 3, "case_id": 7, "study_name": "DOE", "project_name": "다음", "project_uid": "u25"}
    try:
        found = ex._case_dir(job)
        assert found.is_dir() and not ws.is_external(new_sdir, found)
        assert not ws.is_external(new_sdir, Path(old_path))  # 옛 이름 접합으로 와도 스터디 폴더 안이다
        assert ws.record_case(new_sdir, Path(old_path), {"case_id": 7, "status": "complete", "params": {}, "results": {"y": 1}})
        assert "source_dir" not in ws.read_case(found)
        # 진짜 불러온 원본은 그대로 원본이다
        outside = tmp_path / "내결과" / "c0"
        outside.mkdir(parents=True)
        assert ws.is_external(new_sdir, outside)
    finally:
        ex.pool.shutdown(wait=True)
