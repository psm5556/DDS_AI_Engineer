"""Live Link는 스스로 붙는다: 사용자에게 버튼·저널·플러그인 실행을 부탁하지 않는다.
실제 NX 없이 가짜 NXOpen으로 세션 안 실행기를, 가짜 창 함수로 dds-live의 연결 순서를 본다."""
import ctypes
import importlib.util
import json
import os
import re
import sys
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp_servers"))
import dds_live  # noqa: E402


@pytest.fixture()
def queue(tmp_path, monkeypatch):
    q = tmp_path / "queue"
    q.mkdir()
    monkeypatch.setattr(dds_live, "NX_QUEUE", q)
    return q


def _finish_jobs(queue):
    for job in queue.glob("job_*.py"):
        (queue / (job.stem + ".result.json")).write_text(json.dumps({"ok": True, "result": "done"}), encoding="utf-8")


def test_exec_plays_journal_in_open_nx_without_asking(queue, monkeypatch):
    played = []
    monkeypatch.setattr(dds_live, "_windows_of", lambda *names: [111])

    def play(hwnd, journal):
        played.append((hwnd, journal.name))
        _finish_jobs(queue)

    monkeypatch.setattr(dds_live, "_nx_play_journal", play)
    monkeypatch.setattr(dds_live.os, "name", "nt")
    res = dds_live._nx_exec("result = 1", 5)
    assert res["ok"] and played == [(111, "dds_live_nx.py")]


def test_listening_session_is_not_replayed(queue, monkeypatch):
    (queue / "listener.alive").write_text("1", encoding="utf-8")
    monkeypatch.setattr(dds_live, "_nx_play_journal", lambda *a: pytest.fail("이미 붙어 있으면 다시 실행하지 않는다"))
    monkeypatch.setattr(dds_live.time, "sleep", lambda s: _finish_jobs(queue))
    assert dds_live._nx_exec("result = 1", 5)["ok"]


def test_stale_mark_means_not_listening(queue):
    alive = queue / "listener.alive"
    alive.write_text("1", encoding="utf-8")
    assert dds_live._nx_listening()
    os.utime(alive, (time.time() - 60, time.time() - 60))  # NX가 꺼진 뒤 남은 표시
    assert not dds_live._nx_listening()


def test_launches_nx_when_none_is_open(queue, monkeypatch):
    seen = iter([[], [222]])
    launched = []
    monkeypatch.setattr(dds_live, "_windows_of", lambda *names: next(seen, [222]))
    monkeypatch.setattr(dds_live, "_nx_launch", lambda model=None: launched.append(model))
    monkeypatch.setattr(dds_live, "_nx_play_journal", lambda hwnd, journal: None)
    monkeypatch.setattr(dds_live.time, "sleep", lambda s: None)
    monkeypatch.setattr(dds_live.os, "name", "nt")
    assert dds_live._nx_start("a.prt") is None and launched == ["a.prt"]


def test_failure_goes_to_agent_and_never_runs_later(queue, monkeypatch):
    monkeypatch.setattr(dds_live, "_nx_start", lambda *a: "NX가 실행 창을 열지 않았습니다.")
    res = dds_live._nx_exec("result = 1", 5)
    assert not res["ok"] and "부탁하지 마세요" in res["error"]
    assert not list(queue.glob("job_*.py")), "못 붙은 작업이 나중에 뜻밖의 때 실행되면 안 된다"


def test_plugin_tool_launches_itself(monkeypatch):
    calls = []

    def fake_call(tool, op, timeout=120, **f):
        calls.append(op)
        if calls.count(op) == 1:
            raise OSError("연결 거부")
        return {"ok": True}

    monkeypatch.setattr(dds_live, "_call", fake_call)
    monkeypatch.setattr(dds_live, "_windows_of", lambda *names: [])
    monkeypatch.setattr(dds_live, "live_launch", lambda tool, model_path=None: {"ok": True})
    assert dds_live.live_exec("abaqus", "x = 1")["ok"]


def test_open_session_without_plugin_tells_agent_what_to_do(monkeypatch):
    monkeypatch.setattr(dds_live, "_call", lambda *a, **k: (_ for _ in ()).throw(OSError("연결 거부")))
    monkeypatch.setattr(dds_live, "_windows_of", lambda *names: [333])
    res = dds_live.live_exec("abaqus", "x = 1")
    assert not res["ok"] and "배치" in res["error"] and "부탁하지 마세요" in res["error"]


def test_hypermesh_startup_line_added_once(tmp_path, monkeypatch):
    monkeypatch.setattr(dds_live.Path, "home", lambda: tmp_path)
    (tmp_path / "hmcustom.tcl").write_text("puts mine\n", encoding="utf-8")
    script = tmp_path / "dds_live_hm.tcl"
    dds_live._register_hypermesh(script)
    dds_live._register_hypermesh(script)
    text = (tmp_path / "hmcustom.tcl").read_text(encoding="utf-8")
    assert text.startswith("puts mine\n") and text.count("dds_live_hm.tcl") == 1


# ---------- 사용자에게 시키는 문구가 다시 들어오지 않게 ----------
ASK = re.compile(r"(눌러\s*(주세요|달라|주시)|클릭해\s*(주세요|달라|주시)|실행해\s*(주세요|달라|주시)|'DDS 실행'|리본에 등록|사용자에게 안내|사용자에게 .*안내하세요|사용자가 직접 열)")


@pytest.mark.parametrize("path", [
    "mcp_servers/dds_live.py",
    "agent-context/live/nx/dds_live_nx.py",
    "agent-context/live/PROTOCOL.md",
    "agent-context/skills/live-modeling/SKILL.md",
    ".claude/skills/live-link-speedup/references/nx/mcp_fast_tools.py",
])
def test_no_request_to_click_or_run(path):
    bad = [line for line in (ROOT / path).read_text(encoding="utf-8").splitlines()
           if ASK.search(line) and "부탁하지" not in line and "요청하지" not in line]
    assert not bad, bad


# ---------- 세션 안 실행기 ----------
def _load_runner(monkeypatch, tmp_path):
    nx = types.ModuleType("NXOpen")

    class Marks:
        Visible = 1

    class Unload:
        Immediately, AtTermination = 0, 1

    class Session:
        MarkVisibility = Marks
        LibraryUnloadOption = Unload
        Parts = types.SimpleNamespace(Work="work", Display="display")
        UpdateManager = types.SimpleNamespace(DoUpdate=lambda mark: None)

        @staticmethod
        def GetSession():
            return Session

        @staticmethod
        def SetUndoMark(kind, name):
            return name

    nx.Session = Session
    monkeypatch.setitem(sys.modules, "NXOpen", nx)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    spec = importlib.util.spec_from_file_location("dds_live_nx_t", ROOT / "agent-context/live/nx/dds_live_nx.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    os.makedirs(m.QUEUE)
    return m


def test_runner_runs_jobs_and_reports_errors(monkeypatch, tmp_path):
    m = _load_runner(monkeypatch, tmp_path)
    Path(m.QUEUE, "job_1.py").write_text("result = work_part", encoding="utf-8")
    Path(m.QUEUE, "job_2.py").write_text("1 / 0", encoding="utf-8")
    m.run_pending(m.NXOpen.Session)
    one = json.loads(Path(m.QUEUE, "job_1.result.json").read_text(encoding="utf-8"))
    two = json.loads(Path(m.QUEUE, "job_2.result.json").read_text(encoding="utf-8"))
    assert one == {"ok": True, "stdout": "", "result": "work"}
    assert not two["ok"] and "ZeroDivisionError" in two["error"]
    assert m.pending() == []


def test_runner_stays_loaded_unless_oneshot(monkeypatch, tmp_path):
    m = _load_runner(monkeypatch, tmp_path)
    assert m.GetUnloadOption("") == 1
    Path(m.ROOT, "oneshot").write_text("", encoding="utf-8")
    assert m.GetUnloadOption("") == 0 and m.start_listener() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows 타이머")
def test_timer_handles_queue_on_gui_thread(monkeypatch, tmp_path):
    """실제 Windows 타이머: 메시지를 돌리는 스레드(NX의 GUI 스레드에 해당)에서 작업을 처리하고 표시를 남긴다."""
    m = _load_runner(monkeypatch, tmp_path)
    Path(m.QUEUE, "job_1.py").write_text("result = 'ran'", encoding="utf-8")
    listener = m.start_listener()
    try:
        user32 = ctypes.windll.user32
        msg = ctypes.create_string_buffer(64)
        t0 = time.time()
        res = Path(m.QUEUE, "job_1.result.json")
        while not res.exists() and time.time() - t0 < 5:
            while user32.PeekMessageW(msg, None, 0, 0, 1):
                user32.DispatchMessageW(msg)
            time.sleep(0.01)
        assert res.exists() and json.loads(res.read_text(encoding="utf-8"))["result"] == "ran"
        assert Path(m.ALIVE).exists()
    finally:
        listener.stop()
        delattr(sys, m.STATE)
