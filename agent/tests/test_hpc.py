"""HPC 실행기 테스트: 가짜 스케줄러(제출 = bash로 백그라운드 실행, 상태 = 프로세스 생존 여부)로 제출→폴링→종료코드 판정 흐름 검증."""
import shutil
import sys
import textwrap
from pathlib import Path

import pytest

from agent.dds_agent.job_runner import HpcRunner

BASH = shutil.which("bash")


def _fake_scheduler(tmp: Path) -> dict:
    sub = tmp / "fake_submit.py"
    sub.write_text(textwrap.dedent(f"""
        import subprocess, sys
        p = subprocess.Popen([{BASH!r}, sys.argv[1]], stdout=open(sys.argv[1] + ".out", "w"), stderr=subprocess.STDOUT)
        print(p.pid)
    """), encoding="utf-8")
    st = tmp / "fake_status.py"
    st.write_text(textwrap.dedent("""
        import ctypes, sys
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(sys.argv[1]))
        code = ctypes.c_ulong()
        alive = bool(h) and ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code)) and code.value == 259
        print("RUNNING" if alive else "")
    """), encoding="utf-8")
    py = Path(sys.executable).as_posix()
    return {"scheduler": "slurm", "submit": [sys.executable, str(sub), "{script}"], "status": [sys.executable, str(st), "{job}"],
            "cancel": [sys.executable, "-c", "pass"], "id_regex": r"^(\d+)", "header": "", "poll_sec": 0.5,
            "exe_map": {"python.exe": py}}


@pytest.mark.skipif(not BASH or sys.platform != "win32", reason="Windows + bash(git) 필요")
def test_hpc_runner_success_and_failure(tmp_path):
    r = HpcRunner(_fake_scheduler(tmp_path))
    case = tmp_path / "C0001_doe"
    case.mkdir()
    ok = r.run([sys.executable, "-c", "open('results.json','w').write('{\"f\": 1.5}')"], case, timeout=60)
    assert ok.ok, ok.stderr
    assert (case / "results.json").read_text() == '{"f": 1.5}'
    assert (case / "hpc_job_id.txt").exists() and (case / "hpc_job.sh").exists()
    bad = r.run([sys.executable, "-c", "import sys; sys.exit(3)"], case, timeout=60)
    assert bad.returncode == 3 and not bad.ok


def test_path_and_exe_mapping():
    r = HpcRunner({"scheduler": "slurm", "path_map": {"Z:/": "/shared/"}, "exe_map": {"abaqus.bat": "abaqus"}})
    assert r.remote_path("Z:\\proj\\P0001\\case") == "/shared/proj/P0001/case"
    assert r.remote_command(["cmd", "/c", "C:\\SIMULIA\\Commands\\abaqus.bat", "job=a", "input=Z:\\x\\a.inp"]) == \
        ["abaqus", "job=a", "input=/shared/x/a.inp"]
    assert r.remote_command(["C:\\LSDYNA\\lsdyna_smp.exe", "i=main.k"]) == ["lsdyna_smp", "i=main.k"]


def test_bracket_demo_mock_and_intent():
    """브래킷 E2E 데모: 같은 토폴로지 메쉬 + 응력/질량 트레이드오프, CAD~보고서 요청 판별."""
    from agent.dds_agent.demo_agent import wants_e2e
    from cae_adapters.mock import _bracket, _bracket3d

    a, fa = _bracket3d({"thickness": 6, "rib_height": 10})
    b, fb = _bracket3d({"thickness": 14, "rib_height": 40})
    assert fa["stress"]["triangles"].shape == fb["stress"]["triangles"].shape
    assert a["mass"] < b["mass"] and a["max_stress"] > b["max_stress"]
    # 리브를 키우는 편이 두께를 키우는 것보다 가볍고 강하다
    rib, thick = _bracket({"thickness": 6, "rib_height": 40}), _bracket({"thickness": 14, "rib_height": 10})
    assert rib["mass"] < thick["mass"] and rib["max_stress"] < thick["max_stress"]
    assert wants_e2e("브래킷 CAD를 만들고 … 보고서까지 작성해 줘")
    assert not wants_e2e("3D 외팔보 응력 분포 Simulator")


def test_project_folder_identity(tmp_path):
    """서버 DB가 새로 만들어져 번호가 겹쳐도 다른 프로젝트의 폴더를 쓰지 않는다(uid로 확인)."""
    from agent.dds_agent.workspace import Workspace

    ws = Workspace(tmp_path, "http://srv")
    a = ws.project_dir(1, "브래킷 설계", "aaa111")
    (a / "inputs" / "a.stp").write_text("A")
    assert ws.project_dir(1, "브래킷 설계 v2", "aaa111") == a  # 이름을 바꿔도 같은 폴더
    b = ws.project_dir(1, "다른 프로젝트", "bbb222")  # 같은 번호, 다른 프로젝트
    assert b != a and not (b / "inputs" / "a.stp").exists()
    c = ws.project_dir(1, "브래킷 설계 v2", "ccc333")  # 같은 번호·이름이어도 uid가 다르면 따로
    assert c not in (a, b)
    assert ws.project_dir(1) == c  # uid 모르는 호출(MCP)은 가장 최근 폴더


def test_tool_catalog_consistent():
    """지원 툴 목록(catalog)의 스킬 폴더·실행 키·경로 키가 실제로 모두 있다."""
    from pathlib import Path

    from cae_adapters.catalog import CATEGORIES, TOOLS
    from cae_adapters.tool_commands import DEFAULT_TOOL_COMMANDS, DEFAULT_TOOL_PATHS

    skills = Path(__file__).resolve().parents[2] / "agent-context" / "skills"
    assert len({t["id"] for t in TOOLS}) == len(TOOLS)
    for t in TOOLS:
        assert t["category"] in CATEGORIES, t["id"]
        assert (skills / t["skill"] / "SKILL.md").is_file(), t["skill"]
        assert all(k in DEFAULT_TOOL_COMMANDS for k in t["run"]), t["id"]
        assert all(k in DEFAULT_TOOL_PATHS for k in t["paths"]), t["id"]
