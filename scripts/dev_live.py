"""개발용 실행: 서버 + (관리자 계정·토큰 자동 준비) + 내 PC Agent를 한 번에 띄운다. Ctrl+C로 함께 종료.
  .venv\\Scripts\\python scripts\\dev_live.py [--data <폴더>] [--agent demo|gemini|claude|codex] [--port 7013]
웹앱: http://127.0.0.1:<port>  (프론트엔드는 frontend\\dist 빌드본, 개발 중에는 `cd frontend && npm run dev`로 5173 사용)
처음 실행 시 관리자 계정 admin / PIN 123456 을 만든다(이미 있으면 그대로 사용).
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "dev"))
    ap.add_argument("--agent", default="demo", help="대화 기본 에이전트 (demo는 Gemini 없이 흐름 검증용)")
    ap.add_argument("--port", type=int, default=7013)
    ap.add_argument("--admin", default="admin")
    ap.add_argument("--pin", default="123456")
    a = ap.parse_args()
    data = Path(a.data)
    data.mkdir(parents=True, exist_ok=True)
    rt = ROOT / "data" / "agent_runtime"
    if rt.exists() and not (data / "agent_runtime").exists() and data != ROOT / "data":
        subprocess.call(["cmd", "/c", "mklink", "/J", str(data / "agent_runtime"), str(rt)], stdout=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{a.port}"
    env = {**os.environ, "DDS_DATA_DIR": str(data), "PYTHONIOENCODING": "utf-8"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app.main:app", "--host", "127.0.0.1", "--port", str(a.port)],
                              cwd=ROOT, env=env)
    agent = None
    try:
        c = httpx.Client(base_url=base, timeout=30)
        for _ in range(60):
            try:
                c.get("/api/auth/me")
                break
            except httpx.HTTPError:
                time.sleep(0.5)
        c.post("/api/auth/register", json={"username": a.admin, "pin": a.pin, "display_name": "관리자"})
        r = c.post("/api/auth/login", json={"username": a.admin, "pin": a.pin})
        if r.status_code != 200:
            sys.exit(f"관리자 로그인 실패: {r.text}")
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        cfg = data / "agent.toml"
        if not cfg.exists():
            tok = c.post("/api/tokens", json={"name": "개발 PC"}).json()["token"]
            ws = (data / "workspace").as_posix()
            cfg.write_text(f"server_url = '{base}'\ntoken = '{tok}'\nworkspace_dir = '{ws}'\ndefault_agent = '{a.agent}'\n",
                           encoding="utf-8")
        # 서버 주소는 이번 실행 포트로 (예전 agent.toml에 다른 포트가 적혀 있어도 따라온다)
        agent = subprocess.Popen([sys.executable, "-m", "agent.dds_agent.main"], cwd=ROOT,
                                 env={**env, "DDS_AGENT_CONFIG": str(cfg), "DDS_DEFAULT_AGENT": a.agent, "DDS_SERVER_URL": base})
        print(f"\n웹앱: {base}   (관리자 {a.admin} / PIN {a.pin})   기본 에이전트: {a.agent}\n종료: Ctrl+C\n", flush=True)
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in (agent, server):
            if p and p.poll() is None:
                p.terminate()


if __name__ == "__main__":
    main()
