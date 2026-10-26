"""E2E 검증: 서버 + PC Agent(데모 모드) + MCP 서버를 실제 프로세스로 띄우고 대화 시나리오를 끝까지 돌린다.
실행: .venv\\Scripts\\python scripts\\e2e_demo.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


def wait_for(cond, timeout=120, interval=0.5, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cond()
        if v:
            return v
        time.sleep(interval)
    raise TimeoutError(f"시간 초과: {what}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="dds_e2e_"))
    env = {**os.environ, "DDS_DATA_DIR": str(tmp / "server"), "PYTHONIOENCODING": "utf-8"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(PORT)],
                              cwd=ROOT, env=env, stdout=open(tmp / "server.log", "w"), stderr=subprocess.STDOUT)
    agent = None
    try:
        c = httpx.Client(base_url=BASE, timeout=30)
        wait_for(lambda: _ok(c), what="서버 시작")
        c.post("/api/auth/register", json={"username": "admin", "password": "password123", "display_name": "관리자"})
        tok = c.post("/api/auth/login", json={"username": "admin", "password": "password123"}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        api_token = c.post("/api/tokens", json={"name": "e2e"}).json()["token"]

        agent_env = {**env, "DDS_SERVER_URL": BASE, "DDS_TOKEN": api_token, "DDS_DEFAULT_AGENT": "demo",
                     "DDS_WORKSPACE": str(tmp / "ws"), "DDS_AGENT_CONFIG": str(tmp / "none.toml")}
        agent = subprocess.Popen([sys.executable, "-m", "agent.dds_agent.main"], cwd=ROOT, env=agent_env,
                                 stdout=open(tmp / "agent.log", "w"), stderr=subprocess.STDOUT)
        wait_for(lambda: c.get("/api/agent/status").json()["connected"], what="Agent 연결")
        print("✓ PC Agent 연결")

        pid = c.post("/api/projects", json={"name": "E2E 데모"}).json()["id"]
        sid = c.post(f"/api/projects/{pid}/sessions").json()["id"]
        c.post(f"/api/sessions/{sid}/prompt", json={"text": "mock 솔버로 Branin 최적화하고 Simulator 배포해줘"}).raise_for_status()
        wait_for(lambda: not c.get(f"/api/sessions/{sid}/status").json()["busy"] and
                 c.get(f"/api/projects/{pid}/flows").json(), what="플로우 제안")
        flow = c.get(f"/api/projects/{pid}/flows").json()[0]
        assert flow["status"] == "review", flow
        print(f"✓ 플로우 제안: {flow['title']} ({len(flow['nodes'])}단계)")

        c.put(f"/api/flows/{flow['id']}/status", json={"status": "approved", "version": flow["version"]}).raise_for_status()
        c.post(f"/api/sessions/{sid}/prompt", json={"text": "플로우를 승인했습니다. 진행하세요."}).raise_for_status()
        perm = wait_for(lambda: c.get(f"/api/sessions/{sid}/status").json()["pending_permissions"], what="권한 요청")[0]
        print(f"✓ 권한 요청 수신: {perm['title']}")
        c.post(f"/api/sessions/{sid}/permission", json={"request_id": perm["request_id"], "option_id": "allow"}).raise_for_status()
        wait_for(lambda: not c.get(f"/api/sessions/{sid}/status").json()["busy"], timeout=300, what="시나리오 완료")

        flow = c.get(f"/api/flows/{flow['id']}").json()
        msgs = c.get(f"/api/sessions/{sid}/messages").json()
        models = c.get(f"/api/projects/{pid}/models").json()
        arts = c.get(f"/api/projects/{pid}/artifacts").json()
        print(f"✓ 플로우 상태: {flow['status']} / 노드: {[n['status'] for n in flow['nodes']]}")
        print(f"✓ 메시지 {len(msgs)}개 (툴 호출 {sum(m['role'] == 'tool' for m in msgs)}개)")
        print(f"✓ 아티팩트 {len(arts)}개: {sorted({a['kind'] for a in arts})}")
        print(f"✓ 배포 모델: {[(m['id'], m['deployed']) for m in models]}")
        print("마지막 응답:", next(m['content'] for m in reversed(msgs) if m['role'] == 'agent'))
        assert flow["status"] == "complete"
        assert any(m["deployed"] for m in models)
        errors = [m for m in msgs if m["role"] == "system"]
        assert not errors, errors
        print("E2E 통과")
    except Exception:
        for name in ("server.log", "agent.log"):
            print(f"----- {name} -----")
            print((tmp / name).read_text(encoding="utf-8", errors="replace")[-6000:])
        raise
    finally:
        for p in (agent, server):
            if p:
                p.terminate()


def _ok(c):
    try:
        return c.post("/api/auth/login", json={}).status_code in (401, 422)
    except httpx.HTTPError:
        return False


if __name__ == "__main__":
    main()
