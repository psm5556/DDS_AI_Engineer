"""DDS PC Agent: 사내 서버에 먼저 접속(WebSocket)해서
- 사용자가 고른 에이전트 CLI(Gemini / Claude Code / Codex)를 ACP로 구동하고 대화 이벤트·권한 요청을 서버로 중계하고
- 서버가 보낸 CAE 케이스를 이 PC(또는 HPC)에서 실행하고
- 웹앱에서 요청하면 에이전트 CLI를 설치한다.
실행: python -m agent.dds_agent.main   (설정: %USERPROFILE%\\.dds-agent\\agent.toml)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import hashlib
import socket
import subprocess
import sys
import threading
from pathlib import Path

import websockets

from . import __version__, installer, updater
from .acp_runner import AcpRunner
from .config import REPO_ROOT, load_settings
from .context import sync_project_context
from .executor import CaseExecutor
from .headless_runner import HeadlessRunner
from .runner import AgentError, AgentRunner
from .server_api import ServerApi
from .tray import Tray

log = logging.getLogger("dds-agent")
HEARTBEAT_SEC = 30


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
UPDATE_CHECK_SEC = 3600
MCP_SERVERS = ("dds_flow", "dds_cae", "dds_study", "dds_live")


class Agent:
    def __init__(self):
        self.s = load_settings()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws = None
        self.env = {**os.environ, "PYTHONIOENCODING": "utf-8", "DDS_SERVER_URL": self.s.server_url,
                    "DDS_TOKEN": self.s.token}
        self.runners: dict[str, AgentRunner] = {}  # 에이전트 이름 → 러너(상주 프로세스)
        self.session_runner: dict[str, str] = {}  # 세션 id → 에이전트 이름
        self.session_project: dict[str, tuple[int, str | None]] = {}  # 세션 id → (프로젝트 id, 이름)
        self._lock = threading.Lock()
        self.executor = CaseExecutor(self.s, self.send_threadsafe)
        self.api = ServerApi(self.s)
        self.active_prompts = 0
        self.update_state = "idle"  # idle | pending:<ver> | installing:<ver> | restarting:<ver> | failed: ...
        self._update_task: asyncio.Task | None = None
        self.restart = False
        self.quit = False
        self.tray: Tray | None = None

    # ---------- 러너 ----------
    def runner(self, name: str) -> AgentRunner:
        with self._lock:
            if name not in self.runners:
                cmd = self.s.agent_command(name)
                # 권한 요청 id는 러너마다 겹칠 수 있으므로 "<에이전트>:<id>"로 구분한다
                on_perm = lambda sid, req, params, n=name: self._on_permission(sid, f"{n}:{req}", params)
                if name == "gemini" and self.s.gemini_headless:
                    r = HeadlessRunner(cmd[:1], self._on_event, on_perm, self.s.headless_extra_args, self.env)
                else:
                    r = AcpRunner(cmd, self._on_event, on_perm, self.env)
                self.runners[name] = r
            return self.runners[name]

    def runner_of(self, session_id: str) -> AgentRunner:
        name = self.session_runner.get(session_id)
        if not name:
            raise AgentError("알 수 없는 세션입니다. 새 대화를 시작하세요.")
        return self.runner(name)

    # ---------- 서버로 보내기 ----------
    def send_threadsafe(self, msg: dict):
        if self.loop and self.ws:
            asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(msg, ensure_ascii=False, default=str)), self.loop)

    def _on_event(self, session_id: str, update: dict):
        self.send_threadsafe({"type": "agent.event", "session_id": session_id, "update": update})

    def _on_permission(self, session_id: str, request_id: str, params: dict):
        self.send_threadsafe({"type": "agent.permission", "session_id": session_id, "request_id": request_id,
                              "params": params})

    def hello(self) -> dict:
        return {"type": "hello", "hostname": socket.gethostname(), "version": __version__, "platform": platform.platform(),
                "agents": self.s.available_agents(), "default_agent": self.s.default_agent,
                "workspace": str(self.s.workspace_dir), "tools": sorted(self.s.tools)}

    def mcp_servers(self, project_id: int) -> list[dict]:
        """ACP session/new에 넘길 MCP 서버 정의. 세션별로 DDS_PROJECT_ID를 고정한다."""
        env = {"DDS_PROJECT_ID": str(project_id), "DDS_SERVER_URL": self.s.server_url, "DDS_TOKEN": self.s.token,
               "PYTHONPATH": str(REPO_ROOT), "PYTHONIOENCODING": "utf-8"}
        return [{"name": name.replace("_", "-"), "command": sys.executable,
                 "args": [str(REPO_ROOT / "mcp_servers" / f"{name}.py")],
                 "env": [{"name": k, "value": v} for k, v in env.items()]} for name in MCP_SERVERS]

    # ---------- 서버 요청 처리 ----------
    async def _respond(self, rid, fn, *args):
        try:
            result = await asyncio.to_thread(fn, *args)
            msg = {"type": "response", "rid": rid, "result": result}
        except Exception as e:
            log.exception("요청 처리 실패")
            msg = {"type": "response", "rid": rid, "error": str(e)}
        await self.ws.send(json.dumps(msg, ensure_ascii=False, default=str))

    def _prepare(self, project_id: int, project_name: str | None = None) -> str:
        d = self.s.project_dir(project_id, project_name)
        sync_project_context(d)
        return str(d)

    def _new_session(self, project_id: int, agent: str | None, project_name: str | None = None) -> dict:
        name = agent or self.s.default_agent
        cwd = self._prepare(project_id, project_name)
        sid = self.runner(name).new_session(cwd, self.mcp_servers(project_id))
        self.session_runner[sid] = name
        self.session_project[sid] = (project_id, project_name)
        return {"session_id": sid, "agent": name}

    def _load_session(self, project_id: int, session_id: str, agent: str | None, project_name: str | None = None) -> dict:
        name = agent or self.s.default_agent
        cwd = self._prepare(project_id, project_name)
        ok = self.runner(name).load_session(session_id, cwd, self.mcp_servers(project_id))
        if ok:
            self.session_runner[session_id] = name
            self.session_project[session_id] = (project_id, project_name)
        return {"ok": ok}

    def _fetch_attachments(self, session_id: str, attachments: list[dict]) -> str:
        """대화 첨부 파일을 프로젝트 inputs/로 내려받고, 에이전트에게 알려 줄 안내문을 만든다."""
        if not attachments:
            return ""
        pid, pname = self.session_project.get(session_id, (None, None))
        if pid is None:
            return ""
        inputs = self.s.project_dir(pid, pname) / "inputs"
        lines = []
        for a in attachments:
            dest = inputs / a["filename"]
            if dest.exists() and file_sha256(dest) != a["sha256"]:  # 같은 이름의 다른 파일은 덮지 않는다
                dest = inputs / f"{dest.stem}_{a['id']}{dest.suffix}"
            if not dest.exists():
                self.api.download(f"/api/attachments/{a['id']}/download", dest)
            lines.append(f"- inputs/{dest.name} ({a['size'] / 1e6:.1f} MB)")
        return "\n\n[첨부 파일 — 프로젝트 작업 폴더 기준, 원본이므로 수정하지 말고 복사해서 사용]\n" + "\n".join(lines)

    def _prompt(self, session_id: str, text: str, attachments: list[dict] | None = None) -> dict:
        self.active_prompts += 1
        try:
            text += self._fetch_attachments(session_id, attachments or [])
            return {"stop_reason": self.runner_of(session_id).prompt(session_id, text)}
        finally:
            self.active_prompts -= 1

    # ---------- 내 PC 작업 폴더 ----------
    def _local_open(self, project_id: int, project_name: str | None, path: str, with_viewer: bool) -> dict:
        target = self.s.workspace.resolve(project_id, path, project_name)
        if not target.exists():
            raise FileNotFoundError(f"없는 경로입니다: {path}")
        if with_viewer and target.is_file():
            cmd = self.s.viewers.get(target.suffix.lower()) or self.s.viewers.get("*")
            if cmd:
                subprocess.Popen([c.replace("{file}", str(target)) for c in cmd], cwd=str(target.parent))
                return {"ok": True, "opened": str(target), "with": cmd[0]}
        if os.name == "nt":
            if target.is_file() and not with_viewer:
                subprocess.Popen(["explorer", "/select,", str(target)])  # 파일은 탐색기에서 선택된 상태로
            else:
                os.startfile(str(target))  # noqa: S606 — 폴더 열기 또는 기본 연결 프로그램
        return {"ok": True, "opened": str(target)}

    def _local_tree(self, project_id: int, project_name: str | None, path: str) -> dict:
        return self.s.workspace.tree(project_id, path, project_name)

    # ---------- 상태 보고 / 자동 업데이트 ----------
    def status(self) -> dict:
        return {"running_cases": self.executor.running, "queued_cases": self.executor.queued,
                "active_prompts": self.active_prompts, "update_state": self.update_state,
                "sessions": len(self.session_runner)}

    def busy(self) -> bool:
        return bool(self.active_prompts or self.executor.running or self.executor.queued)

    def schedule_update(self, version: str, sha256: str | None, force: bool = False):
        if not updater.is_newer(version) and not force:
            return
        if self._update_task and not self._update_task.done():
            return
        self._update_task = asyncio.create_task(self._update(version, sha256, force))

    async def _update(self, version: str, sha256: str | None, force: bool):
        if updater.install_home() is None:
            self.update_state = f"dev-mode (최신 {version})"
            return
        self.update_state = f"pending:{version}"
        while self.busy() and not force:  # 작업 중이면 끝날 때까지 기다린다
            await asyncio.sleep(10)
        self.update_state = f"installing:{version}"
        if self.tray:
            self.tray.set("updating", version)
        try:
            ok = await asyncio.to_thread(updater.apply_update, self.api, version, sha256)
        except Exception as e:
            log.exception("업데이트 실패")
            self.update_state = f"failed: {e}"[:300]
            return
        if ok:
            self.update_state = f"restarting:{version}"
            if self.tray:
                self.tray.notify(f"새 버전 {version}(으)로 다시 시작합니다")
            await self._send_heartbeat()
            self.restart = True
            if self.ws:
                await self.ws.close()

    async def _check_latest(self, manual: bool = False):
        if not (self.s.auto_update or manual):
            return
        try:
            latest = await asyncio.to_thread(self.api.get, "/api/agent/releases/latest")
            if manual and self.tray and not updater.is_newer(latest["version"]):
                self.tray.notify(f"최신 버전입니다 ({__version__})")
            self.schedule_update(latest["version"], latest["sha256"])
        except Exception as e:
            log.debug("최신 버전 확인 실패: %s", e)
            if manual and self.tray:
                self.tray.notify("배포된 버전 정보를 가져오지 못했습니다")

    # ---------- 트레이 ----------
    def _start_tray(self):
        if not self.s.tray:
            return
        home = updater.install_home()
        call = lambda coro: asyncio.run_coroutine_threadsafe(coro, self.loop)  # noqa: E731 — 트레이 스레드 → 이벤트 루프
        self.tray = Tray(__version__, self.s.server_url, self.s.workspace_dir, home / "logs" if home else None,
                         on_check_update=lambda: call(self._check_latest(manual=True)),
                         on_restart=lambda: call(self._stop(restart=True)),
                         on_quit=lambda: call(self._stop(restart=False)))
        if not self.tray.start():
            self.tray = None

    async def _stop(self, restart: bool):
        self.restart, self.quit = restart, not restart
        if self.ws:
            await self.ws.close()

    async def _send_heartbeat(self):
        if self.ws:
            await self.ws.send(json.dumps({"type": "heartbeat", "hostname": socket.gethostname(),
                                           "status": self.status()}, ensure_ascii=False))

    async def _background(self):
        n = 0
        while True:
            await asyncio.sleep(HEARTBEAT_SEC)
            n += 1
            try:
                await self._send_heartbeat()
            except Exception:
                pass
            if n % (UPDATE_CHECK_SEC // HEARTBEAT_SEC) == 0:
                await self._check_latest()

    def _install(self, name: str) -> dict:
        res = installer.install(self.s, name)
        self.send_threadsafe(self.hello())  # 설치 상태 갱신
        return res

    async def handle(self, msg: dict):
        t, rid = msg.get("type"), msg.get("rid")
        if t == "session.new":
            asyncio.create_task(self._respond(rid, self._new_session, msg["project_id"], msg.get("agent"), msg.get("project_name")))
        elif t == "session.load":
            asyncio.create_task(self._respond(rid, self._load_session, msg["project_id"], msg["session_id"],
                                              msg.get("agent"), msg.get("project_name")))
        elif t == "session.prompt":
            asyncio.create_task(self._respond(rid, self._prompt, msg["session_id"], msg["text"], msg.get("attachments")))
        elif t == "local.open":
            asyncio.create_task(self._respond(rid, self._local_open, msg["project_id"], msg.get("project_name"),
                                              msg.get("path", ""), bool(msg.get("with_viewer"))))
        elif t == "local.diagnose":
            from .diagnose import diagnose
            asyncio.create_task(self._respond(rid, diagnose, self.s))
        elif t == "local.tree":
            asyncio.create_task(self._respond(rid, self._local_tree, msg["project_id"], msg.get("project_name"), msg.get("path", "")))
        elif t == "agent.install":
            asyncio.create_task(self._respond(rid, self._install, msg["agent"]))
        elif t == "session.cancel":
            try:
                await asyncio.to_thread(self.runner_of(msg["session_id"]).cancel, msg["session_id"])
            except AgentError as e:
                log.warning("취소 실패: %s", e)
        elif t == "permission.reply":
            name, _, req = str(msg["request_id"]).partition(":")
            try:
                await asyncio.to_thread(self.runner(name).respond_permission, req, msg.get("option_id"))
            except (AgentError, KeyError) as e:
                log.warning("권한 응답 실패: %s", e)
        elif t == "case.run":
            self.executor.submit(msg)
        elif t in ("agent.release", "agent.update"):
            if self.s.auto_update or msg.get("force"):
                self.schedule_update(msg["version"], msg.get("sha256"), bool(msg.get("force")))
        else:
            log.warning("알 수 없는 메시지: %s", t)

    # ---------- 연결 유지 ----------
    async def run(self):
        self.loop = asyncio.get_running_loop()
        if not self.s.token:
            log.error("토큰이 없습니다. 웹앱 > 설정 > PC Agent 토큰을 발급해 agent.toml의 token에 넣으세요.")
            return
        delay = 1
        self._start_tray()
        asyncio.create_task(self._background())
        while not (self.restart or self.quit):
            try:
                async with websockets.connect(f"{self.s.ws_url}?token={self.s.token}", max_size=16 * 1024 * 1024,
                                              ping_interval=20) as ws:
                    self.ws, delay = ws, 1
                    log.info("서버에 연결됨: %s", self.s.server_url)
                    if self.tray:
                        if self.tray.state != "connected":
                            self.tray.notify("웹앱에 연결되었습니다")
                        self.tray.set("connected", self.s.server_url)
                    await ws.send(json.dumps(self.hello(), ensure_ascii=False))
                    await self._send_heartbeat()
                    asyncio.create_task(self._check_latest())
                    async for raw in ws:
                        await self.handle(json.loads(raw))
            except (OSError, websockets.WebSocketException) as e:
                code = getattr(getattr(e, "rcvd", None), "code", None)
                status = getattr(getattr(e, "response", None), "status_code", None)
                if code == 4401 or status in (401, 403):
                    log.error("토큰이 유효하지 않습니다. 새 토큰을 발급하세요.")
                    if self.tray:
                        self.tray.set("error", "토큰이 유효하지 않음 — 다시 설치하세요")
                        self.tray.notify("토큰이 유효하지 않습니다. 웹앱 설정에서 새 토큰으로 다시 설치하세요.")
                    while not self.quit:  # 트레이에서 상태를 볼 수 있게 프로세스는 유지
                        await asyncio.sleep(5)
                    return
                log.warning("서버 연결 끊김(%s). %d초 후 재시도", e, delay)
            finally:
                self.ws = None
            if self.restart or self.quit:
                break
            if self.tray:
                self.tray.set("connecting", "서버에 다시 연결하는 중")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    def shutdown(self):
        for r in self.runners.values():
            r.shutdown()
        if self.tray:
            self.tray.stop()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    agent = Agent()
    log.info("DDS PC Agent %s 시작", __version__)
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        pass
    finally:
        agent.shutdown()
    if agent.restart:
        sys.exit(updater.RESTART_CODE)  # launcher가 새 버전으로 재시작


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    main()
