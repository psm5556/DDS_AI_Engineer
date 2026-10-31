"""실제 uvicorn + 실제 PC Agent + 가짜 AI CLI(fake_acp)를 다른 포트·임시 폴더로 띄우는 시험 도구.
실제 Claude·Gemini·Codex와 data/를 건드리지 않고, 창도 띄우지 않는다(트레이 끔, 콘솔 숨김). pytest와 scripts/e2e_sessions.py가 함께 쓴다.

Agent는 LinkProxy를 거쳐 서버에 붙는다. 서버를 그대로 둔 채 연결만 끊어 재연결을 시험하기 위해서다.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from cae_adapters.hidden import hidden_kwargs, kill_tree

ROOT = Path(__file__).resolve().parents[2]
FAKE = Path(__file__).with_name("fake_acp.py")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(cond, timeout=60.0, interval=0.1, what=""):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(interval)
    raise TimeoutError(f"시간 초과: {what}")


class LinkProxy:
    """127.0.0.1 TCP 중계. cut()으로 지금 연결을 끊고, hold=True면 새 연결도 받지 않는다."""

    def __init__(self, target_port: int):
        self.target = target_port
        self.hold = False
        self._pairs: list[tuple[socket.socket, socket.socket]] = []
        self._frozen: set[int] = set()
        self._lock = threading.Lock()
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(16)
        self.port = self.srv.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self):
        while True:
            try:
                a, _ = self.srv.accept()
            except OSError:
                return
            if self.hold:
                a.close()
                continue
            try:
                b = socket.create_connection(("127.0.0.1", self.target), timeout=5)
                b.settimeout(None)
            except OSError:
                a.close()
                continue
            with self._lock:
                self._pairs.append((a, b))
            threading.Thread(target=self._pipe, args=(a, b), daemon=True).start()
            threading.Thread(target=self._pipe, args=(b, a), daemon=True).start()

    def _pipe(self, src: socket.socket, dst: socket.socket):
        try:
            while data := src.recv(65536):
                if id(src) not in self._frozen:
                    dst.sendall(data)
        except OSError:
            pass
        for s in (src, dst):
            try:
                s.close()
            except OSError:
                pass

    def freeze(self):
        """반쯤 끊긴 연결: 지금 연결은 닫지 않고 글자만 사라진다. 새 연결은 그대로 받는다."""
        with self._lock:
            for a, b in self._pairs:
                self._frozen.update((id(a), id(b)))

    def cut(self, hold: bool = True):
        self.hold = hold
        with self._lock:
            pairs, self._pairs = self._pairs, []
        for a, b in pairs:
            for s in (a, b):
                try:
                    s.close()
                except OSError:
                    pass

    def resume(self):
        self.hold = False

    def close(self):
        self.cut()
        self.srv.close()


class Stack:
    def __init__(self, tmp: Path, *, start_delay: float = 0.0, load: bool = True, agent_extra: str = "", cli_name: str = "fake"):
        self.tmp = Path(tmp)
        self.tmp.mkdir(parents=True, exist_ok=True)
        self.state = self.tmp / "cli"
        self.cli_name = cli_name
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env = {**os.environ, "DDS_DATA_DIR": str(self.tmp / "server"), "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)}
        self.server: subprocess.Popen | None = None
        self.agent: subprocess.Popen | None = None
        self.proxy = LinkProxy(self.port)
        self.logs: list = []
        self.c = httpx.Client(base_url=self.base, timeout=30)
        self.token = ""
        self.pid: int | None = None
        self.agent_extra = agent_extra
        self.relative_config = False  # dev_live.cmd --data data\dev 처럼 DDS_AGENT_CONFIG를 상대 경로로 준다
        self.agent_env: dict[str, str] = {}
        self.control(start_delay=start_delay, load=load)

    # ---------- 프로세스 ----------
    def _popen(self, cmd: list[str], env: dict, name: str) -> subprocess.Popen:
        log = open(self.tmp / f"{name}.log", "ab")
        self.logs.append(log)
        return subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **hidden_kwargs())

    def control(self, **kw):
        """다음에 뜨는 가짜 CLI의 동작을 바꾼다."""
        self.state.mkdir(parents=True, exist_ok=True)
        p = self.state / "control.json"
        c = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        p.write_text(json.dumps({**c, **kw}), encoding="utf-8")

    def start_server(self):
        self.server = self._popen([sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(self.port), "--log-level", "warning"],
                                  self.env, "server")
        wait_for(self._server_up, what="서버 시작", timeout=90)

    def _server_up(self):
        try:
            return self.c.post("/api/auth/login", json={}).status_code in (401, 422)
        except httpx.HTTPError:
            return False

    def stop_server(self):
        if self.server:
            self.server.kill()
            self.server.wait(timeout=15)
            self.server = None

    def login(self):
        self.c.post("/api/auth/register", json={"username": "admin", "pin": "123456", "display_name": "관리자"})
        tok = self.c.post("/api/auth/login", json={"username": "admin", "pin": "123456"}).json()["access_token"]
        self.c.headers["Authorization"] = f"Bearer {tok}"
        if not self.token:
            self.token = self.c.post("/api/tokens", json={"name": "e2e"}).json()["token"]
        if self.pid is None:
            self.pid = self.c.post("/api/projects", json={"name": "세션 시험"}).json()["id"]

    def write_agent_config(self):
        cfg = self.tmp / "agent" / "agent.toml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        # tools.json을 미리 두어 시험마다 PC 전체를 훑지 않게 한다
        (cfg.parent / "tools.json").write_text(json.dumps({"scanned_ts": time.time() + 86400}), encoding="utf-8")
        command = json.dumps(["{python}", str(FAKE), "--state", str(self.state)], ensure_ascii=False)
        cfg.write_text("\n".join([
            f"server_url = {json.dumps(f'http://127.0.0.1:{self.proxy.port}')}",
            f"token = {json.dumps(self.token)}",
            f"workspace_dir = {json.dumps(str(self.tmp / 'ws'))}",
            f"default_agent = {json.dumps(self.cli_name)}",
            "tray = false",
            self.agent_extra,
            f"[agents.{self.cli_name}]",
            'label = "가짜 AI 도구"',
            f"command = {command}",
            "packages = []",
            'login = "로그인 불필요"',
            "",
        ]), encoding="utf-8")
        return cfg

    def start_agent(self):
        cfg = self.write_agent_config()
        env = {k: v for k, v in self.env.items() if k not in ("DDS_DATA_DIR",)}
        env.update({"DDS_AGENT_CONFIG": str(cfg), "DDS_WORKSPACE": str(self.tmp / "ws")})
        env.pop("DDS_AGENT_HOME", None)  # 개발 모드: 자동 업데이트를 하지 않는다
        env.pop("DDS_SERVER_URL", None)
        env.pop("DDS_TOKEN", None)
        if self.relative_config:
            env["DDS_AGENT_CONFIG"] = os.path.relpath(cfg, ROOT)
            env.pop("DDS_WORKSPACE", None)
        env.update(self.agent_env)
        self.agent = self._popen([sys.executable, "-m", "agent.dds_agent.main"], env, "agent")
        wait_for(lambda: self.agent_connected(), what="Agent 연결", timeout=90)

    def agent_connected(self) -> bool:
        try:
            st = self.c.get("/api/agent/status").json()
            return bool(st.get("connected") and (st.get("info") or {}).get("boot"))
        except httpx.HTTPError:
            return False

    def stop_agent(self):
        if self.agent:
            kill_tree(self.agent)
            self.agent = None

    def kill_agent(self):
        """Agent만 비정상 종료. 그 아래 CLI는 Job이 끝낸다."""
        if self.agent:
            self.agent.kill()
            self.agent = None

    def up(self):
        self.start_server()
        self.login()
        self.start_agent()
        return self

    def down(self):
        for fn in (self.stop_agent, self.stop_server, self.proxy.close):
            try:
                fn()
            except Exception:
                pass
        self.c.close()
        for f in self.logs:
            try:
                f.close()
            except OSError:
                pass

    def log_tail(self, n: int = 4000) -> str:
        out = []
        for name in ("server", "agent"):
            p = self.tmp / f"{name}.log"
            if p.exists():
                out.append(f"----- {name}.log -----\n" + p.read_text(encoding="utf-8", errors="replace")[-n:])
        return "\n".join(out)

    # ---------- 대화 ----------
    def new_chat(self) -> int:
        r = self.c.post(f"/api/projects/{self.pid}/sessions", json={"agent": self.cli_name})
        r.raise_for_status()
        return r.json()["id"]

    def post_prompt(self, chat: int, text: str):
        return self.c.post(f"/api/sessions/{chat}/prompt", json={"text": text})

    def status(self, chat: int) -> dict:
        return self.c.get(f"/api/sessions/{chat}/status").json()

    def messages(self, chat: int) -> list[dict]:
        return self.c.get(f"/api/sessions/{chat}/messages").json()

    def agent_text(self, chat: int) -> str:
        return "".join(m["content"] for m in self.messages(chat) if m["role"] == "agent")

    def system_notes(self, chat: int) -> list[str]:
        return [m["content"] for m in self.messages(chat) if m["role"] == "system"]

    def wait_idle(self, chat: int, timeout: float = 60.0):
        wait_for(lambda: not self.status(chat)["busy"], timeout=timeout, what="턴 끝")

    def turn(self, chat: int, text: str, timeout: float = 60.0):
        r = self.post_prompt(chat, text)
        r.raise_for_status()
        self.wait_idle(chat, timeout)
