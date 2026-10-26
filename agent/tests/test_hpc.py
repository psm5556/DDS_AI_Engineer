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
