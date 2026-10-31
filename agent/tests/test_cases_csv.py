"""cases.csv는 보기용 사본이다. 엑셀로 열었거나 보안 프로그램이 암호화했어도 해석 결과를 막지 않는다.
어떤 실패든 케이스는 끝난 상태를 알린다. 스레드 풀이 예외를 삼켜 '실행 중'으로 영원히 남던 문제의 회귀 시험."""
import csv
import ctypes
import errno
import json
import os
import string
import time
from pathlib import Path

import pytest

from agent.dds_agent import workspace as workspace_module
from agent.dds_agent.config import load_settings
from agent.dds_agent.executor import CaseExecutor

SOLVER = """import json
p = json.load(open("params.json"))
json.dump({"y": p["x"] + 1}, open("results.json", "w"))
"""


def _job(case_id: int) -> dict:
    return {"type": "case.run", "case_id": case_id, "study_id": 1, "project_id": 1, "project_name": "p", "project_uid": "u", "study_name": "s",
            "source": "doe", "params": {"x": float(case_id)}, "variables": [], "objectives": [], "adapter": "script",
            "adapter_config": {"tool": "python", "script": "scripts/solver.py"}}


@pytest.fixture
def pc(monkeypatch, tmp_path):
    monkeypatch.setenv("DDS_AGENT_CONFIG", str(tmp_path / "none.toml"))
    monkeypatch.setenv("DDS_TOKEN", "t")
    monkeypatch.setenv("DDS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("DDS_AGENT_HOME", str(tmp_path / "home"))
    settings = load_settings()
    settings.max_parallel_cases = 1
    settings.archive = {"enabled": False}  # 여기서 재는 것은 기록과 보고다
    reports: list[dict] = []
    ex = CaseExecutor(settings, reports.append)
    ex._share_recipe = lambda *a: None  # 서버 없이 시험한다
    scripts = settings.workspace.project_dir(1, "p", "u") / "scripts"
    (scripts / "solver.py").write_text(SOLVER, encoding="utf-8")
    yield ex, reports
    ex.pool.shutdown(wait=True)


def _final(reports: list[dict], case_id: int, timeout: float = 30.0) -> dict:
    """끝난 상태 보고를 기다린다. 오지 않으면 사용자 화면에 '실행 중'으로 영원히 남는다는 뜻이다."""
    end = time.time() + timeout
    while time.time() < end:
        for r in reports:
            if r.get("case_id") == case_id and r.get("status") != "running":
                return r
        time.sleep(0.05)
    raise AssertionError(f"케이스 {case_id}의 끝난 상태 보고가 오지 않았습니다: {reports}")


def _study(ex) -> Path:
    return ex.s.workspace.study_dir(1, 1, "s", "p", "u")


def _rows(ex) -> dict[str, dict]:
    with open(_study(ex) / "cases.csv", encoding="utf-8-sig", newline="") as f:
        return {r["case_id"]: r for r in csv.DictReader(f)}


class _Excel:
    """엑셀처럼 파일을 잠근다. 공유 없이 연 실제 핸들이라 읽기도 바꿔치기도 실패한다."""

    def __init__(self, path: Path):
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.CreateFileW.restype = ctypes.c_void_p
        self.k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        self.k.CloseHandle.argtypes = [ctypes.c_void_p]
        self.h = self.k.CreateFileW(str(path), 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)
        assert self.h not in (None, ctypes.c_void_p(-1).value), ctypes.get_last_error()

    def close(self):
        self.k.CloseHandle(self.h)


@pytest.mark.skipif(os.name != "nt", reason="공유 없는 핸들은 Windows의 잠금이다")
def test_csv_locked_by_a_real_handle_keeps_the_result_and_says_so(pc):
    ex, reports = pc
    ex.submit(_job(1))
    assert _final(reports, 1)["status"] == "complete"
    path = _study(ex) / "cases.csv"
    excel = _Excel(path)
    try:
        with pytest.raises(PermissionError):  # 시험이 실제로 잠갔는지 확인한다
            path.read_bytes()
        ex.submit(_job(2))
        got = _final(reports, 2)
        assert got["status"] == "complete" and got["results"] == {"y": 3.0} and not got["error"]
        assert got["notice"] == "cases.csv를 갱신하지 못했습니다. 해석 결과는 저장했습니다 → 파일을 닫으면 다음 케이스 때 함께 맞춥니다."
        case = json.loads(next((_study(ex) / "cases").glob("C0002_*/case.json")).read_text(encoding="utf-8"))
        assert case["status"] == "complete" and case["results"] == {"y": 3.0}
    finally:
        excel.close()
    # 파일을 닫으면 다음 케이스 때 빠진 줄까지 맞춘다
    ex.submit(_job(3))
    assert _final(reports, 3)["notice"] is None
    rows = _rows(ex)
    assert {k: (r["status"], float(r["out:y"])) for k, r in rows.items()} == {"1": ("complete", 2.0), "2": ("complete", 3.0), "3": ("complete", 4.0)}


def test_unreadable_random_bytes_csv_does_not_stop_a_case(pc):
    """보안 프로그램이 csv를 암호화하면 읽은 내용은 난수와 같다. 읽어 오는 논리가 없어야 다음 케이스가 멈추지 않는다."""
    ex, reports = pc
    ex.submit(_job(1))
    assert _final(reports, 1)["status"] == "complete"
    path = _study(ex) / "cases.csv"
    path.write_bytes(bytes(range(256)) * 8 + b"\x00\xff\xfe" + os.urandom(4096))
    ex.submit(_job(2))
    got = _final(reports, 2)
    assert got["status"] == "complete" and got["results"] == {"y": 3.0} and got["notice"] is None
    assert set(_rows(ex)) == {"1", "2"}  # 앞 케이스의 줄도 case.json에서 다시 만든다


def _missing_folder() -> str:
    if os.name != "nt":
        return "/proc/dds_no_such_folder/project"
    used = {p for p in string.ascii_uppercase if Path(f"{p}:\\").exists()}
    return f"{sorted(set(string.ascii_uppercase) - used)[-1]}:\\DDS\\project"


def test_missing_folder_fails_with_cause_and_next_step(pc):
    ex, reports = pc
    root = ex.s.workspace.root
    idx = json.loads((root / "index.json").read_text(encoding="utf-8"))
    idx.setdefault("links", {})["u"] = _missing_folder()  # 직접 정한 작업 폴더의 드라이브가 빠졌다
    (root / "index.json").write_text(json.dumps(idx), encoding="utf-8")
    ex.submit(_job(1))
    got = _final(reports, 1)
    assert got["status"] == "failed" and "폴더" in got["error"] and "→" in got["error"]
    _settled(ex)


def _settled(ex):
    end = time.time() + 10
    while (ex._stops or ex.running) and time.time() < end:
        time.sleep(0.05)
    assert not ex._stops and ex.running == 0


def test_full_disk_before_the_solver_fails_with_cause_and_next_step(pc, monkeypatch):
    ex, reports = pc

    def full(path, data):
        raise OSError(errno.ENOSPC, "No space left on device", str(path))
    monkeypatch.setattr(workspace_module, "_write_json", full)
    ex.submit(_job(1))
    got = _final(reports, 1)
    assert got["status"] == "failed" and "공간" in got["error"] and "→" in got["error"]
    assert not [r for r in reports if r.get("status") == "running"]  # 해석을 시작하지 않았다
    _settled(ex)


def test_full_disk_after_the_solver_keeps_the_values_it_produced(pc, monkeypatch):
    ex, reports = pc
    real = workspace_module._write_json

    def full_at_the_end(path, data):
        if data.get("finished"):
            raise OSError(errno.ENOSPC, "No space left on device", str(path))
        real(path, data)
    monkeypatch.setattr(workspace_module, "_write_json", full_at_the_end)
    ex.submit(_job(1))
    got = _final(reports, 1)
    assert got["status"] == "failed" and "공간" in got["error"] and "→" in got["error"]
    assert got["results"] == {"y": 2.0}  # 값을 버리지 않는다
    _settled(ex)


def test_any_unexpected_failure_still_reports_a_final_status(pc, monkeypatch):
    ex, reports = pc
    monkeypatch.setattr(workspace_module.Workspace, "project_dir", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("뜻밖의 실패")))
    ex.submit(_job(1))
    got = _final(reports, 1)
    assert got["status"] == "failed" and "뜻밖의 실패" in got["error"] and "→" in got["error"]
    ex.submit({**_job(2), "type": "case.extract", "steps": [], "names": ["z"], "results": {"y": 1.0}})
    got = _final(reports, 2)  # 추출 실패가 해석 결과를 지우지 않는다
    assert got["extract"] is True and got["status"] == "complete" and "뜻밖의 실패" in got["error"]
    _settled(ex)


def test_stopped_case_is_not_reported_as_failed_when_recording_fails(pc, monkeypatch):
    ex, reports = pc

    def full(path, data):
        ex._stops[1].set()
        raise OSError(errno.ENOSPC, "No space left on device", str(path))
    monkeypatch.setattr(workspace_module, "_write_json", full)
    ex.submit(_job(1))
    assert _final(reports, 1)["status"] == "cancelled"


# ---------- 다시 연결: PC의 case.json이 답이다
def _settle_job(case_id: int) -> dict:
    return {k: _job(case_id)[k] for k in ("case_id", "study_id", "project_id", "project_name", "project_uid", "study_name", "source", "adapter", "adapter_config", "params")}


def test_settle_reports_the_finished_result_from_case_json(pc):
    """결과를 전하기 전에 Agent가 다시 시작했다. 끝난 해석을 '중지'로 바꾸지 않는다."""
    ex, reports = pc
    ex.submit(_job(1))
    assert _final(reports, 1)["status"] == "complete"
    _settled(ex)
    reports.clear()
    ex.settle([_settle_job(1)])
    got = _final(reports, 1)
    assert got["status"] == "complete" and got["results"] == {"y": 2.0}


def test_settle_says_cancelled_only_when_the_pc_has_no_finished_result(pc):
    ex, reports = pc
    ws = ex.s.workspace
    d = ws.case_dir(_study(ex), 2, "doe")
    ws.record_case(_study(ex), d, {"case_id": 2, "source": "doe", "status": "running"})  # 도는 중에 Agent가 죽었다
    ex.settle([_settle_job(2), _settle_job(3)])  # 3번은 시작도 못 했다
    assert _final(reports, 2)["status"] == "cancelled" and "→" in _final(reports, 2)["error"]
    assert _final(reports, 3)["status"] == "cancelled"


def test_settle_leaves_a_case_this_pc_is_running_alone(pc):
    ex, reports = pc
    import threading

    ex._stops[5] = threading.Event()
    ex.settle([_settle_job(5)])
    time.sleep(0.3)
    ex._stops.pop(5)
    assert not reports


def test_settle_never_raises_out_of_its_thread(pc, monkeypatch):
    ex, reports = pc
    monkeypatch.setattr(workspace_module.Workspace, "study_dir", lambda *a, **k: (_ for _ in ()).throw(OSError(errno.ENOENT, "없음", "Q:\\x")))
    ex.settle([_settle_job(6)])
    got = _final(reports, 6)
    assert got["status"] == "failed" and "→" in got["error"]


