"""E2E 검증: 원클릭 설치(포터블 Python/Node) → Agent 연결 → 새 버전 배포 → 자동 업데이트·재시작.
사용자 PATH·시작프로그램은 건드리지 않는 테스트 모드(DDS_INSTALL_NO_SYSTEM=1)로, 임시 폴더에 설치한다.
실행: .venv\\Scripts\\python scripts\\e2e_install.py   (먼저 scripts/fetch_runtimes.py 로 data/agent_runtime 준비)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORT = 8798
BASE = f"http://127.0.0.1:{PORT}"


def wait_for(cond, timeout=180, interval=1.0, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            v = cond()
        except Exception:
            v = None
        if v:
            return v
        time.sleep(interval)
    raise TimeoutError(f"시간 초과: {what}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="dds_inst_"))
    data = tmp / "server"
    data.mkdir()
    # 런타임은 복사 대신 링크(용량 큼)
    subprocess.check_call(["cmd", "/c", "mklink", "/J", str(data / "agent_runtime"), str(ROOT / "data" / "agent_runtime")],
                          stdout=subprocess.DEVNULL)
    env = {**os.environ, "DDS_DATA_DIR": str(data), "PYTHONIOENCODING": "utf-8"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(PORT)],
                              cwd=ROOT, env=env, stdout=open(tmp / "server.log", "w"), stderr=subprocess.STDOUT)
    home = tmp / "DDS-Agent"
    try:
        c = httpx.Client(base_url=BASE, timeout=60)
        wait_for(lambda: c.get("/api/auth/me").status_code == 401, what="서버 시작")
        c.post("/api/auth/register", json={"username": "admin", "pin": "123456", "display_name": "관리자"})
        jwt = c.post("/api/auth/login", json={"username": "admin", "pin": "123456"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {jwt}"
        token = c.post("/api/tokens", json={"name": "설치 테스트"}).json()["token"]

        pub = [sys.executable, "scripts/build_agent_bundle.py", "--publish", "--server", BASE,
               "--user", "admin", "--pin", "123456"]
        subprocess.check_call(pub + ["--version", "0.9.0", "--notes", "첫 배포"], cwd=ROOT)

        print("▶ 원클릭 설치 실행 (irm | iex)")
        ps_env = {**os.environ, "DDS_TOKEN": token, "DDS_AGENT_HOME": str(home), "DDS_INSTALL_NO_SYSTEM": "1",
                  "DDS_AGENT_CONFIG": str(tmp / "cfg" / "agent.toml"), "DDS_WORKSPACE": str(tmp / "ws"),
                  "DDS_DEFAULT_AGENT": "demo"}
        t0 = time.time()
        p = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                            f"irm {BASE}/api/agent/install.ps1 | iex"], env=ps_env, capture_output=True,
                           text=True, encoding="cp949", errors="replace", timeout=1200)
        print(p.stdout[-3000:])
        if p.returncode != 0:
            print(p.stderr[-3000:])
            raise SystemExit("설치 스크립트 실패")
        print(f"✓ 설치 {time.time() - t0:.0f}초")

        inst = wait_for(lambda: next((i for i in c.get("/api/agent/instances").json() if i["connected"]), None),
                        what="Agent 연결")
        print(f"✓ 연결: {inst['hostname']} v{inst['version']} / CLI: {[a['name'] for a in inst['agents'] if a['installed']]}")
        assert inst["version"] == "0.9.0"

        subprocess.check_call(pub + ["--version", "0.9.1", "--notes", "자동 업데이트 테스트"], cwd=ROOT)
        print("▶ 0.9.1 배포 → 자동 업데이트 대기")
        inst = wait_for(lambda: next((i for i in c.get("/api/agent/instances").json()
                                      if i["connected"] and i["version"] == "0.9.1"), None), timeout=600,
                        what="자동 업데이트")
        print(f"✓ 자동 업데이트 완료: v{inst['version']} (outdated={inst['outdated']})")
        print((home / "logs" / "launcher.log").read_text(encoding="utf-8"))
        print("E2E 설치/업데이트 통과")
    except BaseException:
        print("----- server.log -----")
        print((tmp / "server.log").read_text(encoding="utf-8", errors="replace")[-3000:])
        log = home / "logs" / "agent.log"
        if log.exists():
            print("----- agent.log -----")
            print(log.read_text(encoding="utf-8", errors="replace")[-4000:])
        raise
    finally:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Get-CimInstance Win32_Process | ? {{ $_.CommandLine -like '*{home}*' }} | % {{ Stop-Process -Id $_.ProcessId -Force }}"],
                       capture_output=True)
        server.terminate()
        print(f"(임시 폴더: {tmp})")


if __name__ == "__main__":
    main()
