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
import time
from pathlib import Path

import websockets

from cae_adapters.hidden import console_python, hidden_kwargs, spawn_gui_detached

from . import __version__, devtest, installer, toolscan, updater
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
TOOL_WATCH_SEC = 300  # 설치 상태가 바뀌었는지 보는 간격
TOOL_FULL_SCAN_SEC = 12 * 3600  # 바뀐 흔적이 없어도 이 간격으로 다시 찾는다(폴더만 복사해 설치한 툴 등)


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
UPDATE_CHECK_SEC = 3600
IDLE_SEC = 300  # 일이 끝나고 이만큼 조용해야 자동 업데이트를 시작한다
IDLE_POLL_SEC = 10
WORK_MSGS = ("session.", "case.", "permission.", "agent.install", "tools.set", "tools.pick")  # 사람이 일을 시킨 메시지
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
        self.session_project: dict[str, tuple[int, str | None, str | None]] = {}  # 세션 id → (프로젝트 id, 이름, uid)
        self._lock = threading.Lock()
        self.executor = CaseExecutor(self.s, self.send_threadsafe)
        self.api = ServerApi(self.s)
        self.active_prompts = 0
        self.installing = 0  # AI CLI 설치 중인 수 (설치 도중 다시 시작하면 반쯤 깔린 채로 남는다)
        self.devtests = 0  # 개발 AI의 원격 시험이 도는 수
        self.remote_test_until = 0.0  # 이 시각까지 원격 시험 허용. 서버 설정을 따르고 기본은 꺼짐
        self._devtest_noticed = 0.0
        self.auto_update = self.s.auto_update  # 연결되면 서버의 설정(웹앱 > 설정)을 따른다
        self.update_state = "idle"  # idle | pending:<ver> | installing:<ver> | restarting:<ver> | failed: ...
        self._update_task: asyncio.Task | None = None
        self._update_version: str | None = None
        self._update_firm = False  # 취소하지 않는 업데이트(사람이 지시했거나 전환을 시작함)
        self._last_work = time.monotonic()
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
                "workspace": str(self.s.workspace_dir), "tools": sorted(self.s.tools), "tools_rev": self.s.tools_rev}

    def mcp_servers(self, project_id: int) -> list[dict]:
        """ACP session/new에 넘길 MCP 서버 정의. 세션별로 DDS_PROJECT_ID를 고정한다."""
        env = {"DDS_PROJECT_ID": str(project_id), "DDS_SERVER_URL": self.s.server_url, "DDS_TOKEN": self.s.token,
               "PYTHONPATH": str(REPO_ROOT), "PYTHONIOENCODING": "utf-8"}
        # launcher는 자동 업데이트로 교체되지 않아 Agent가 pythonw일 수 있다. MCP가 pythonw면 그 아래 솔버 창이 다시 보인다
        return [{"name": name.replace("_", "-"), "command": console_python(),
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

    def _prepare(self, project_id: int, project_name: str | None = None, project_uid: str | None = None) -> str:
        d = self.s.project_dir(project_id, project_name, project_uid)
        sync_project_context(d)
        return str(d)

    def _new_session(self, project_id: int, agent: str | None, project_name: str | None = None,
                     project_uid: str | None = None) -> dict:
        name = agent or self.s.default_agent
        cwd = self._prepare(project_id, project_name, project_uid)
        sid = self.runner(name).new_session(cwd, self.mcp_servers(project_id))
        self.session_runner[sid] = name
        self.session_project[sid] = (project_id, project_name, project_uid)
        return {"session_id": sid, "agent": name}

    def _load_session(self, project_id: int, session_id: str, agent: str | None, project_name: str | None = None,
                      project_uid: str | None = None) -> dict:
        name = agent or self.s.default_agent
        cwd = self._prepare(project_id, project_name, project_uid)
        ok = self.runner(name).load_session(session_id, cwd, self.mcp_servers(project_id))
        if ok:
            self.session_runner[session_id] = name
            self.session_project[session_id] = (project_id, project_name, project_uid)
        return {"ok": ok}

    def _fetch_attachments(self, session_id: str, attachments: list[dict]) -> str:
        """대화 첨부 파일을 프로젝트 inputs/로 내려받고, 에이전트에게 알려 줄 안내문을 만든다."""
        if not attachments:
            return ""
        pid, pname, puid = self.session_project.get(session_id, (None, None, None))
        if pid is None:
            return ""
        inputs = self.s.project_dir(pid, pname, puid) / "inputs"
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
    def _local_open(self, project_id: int, project_name: str | None, path: str, with_viewer: bool,
                    project_uid: str | None = None) -> dict:
        target = self.s.workspace.resolve(project_id, path, project_name, project_uid)
        if not target.exists():
            raise FileNotFoundError(f"없는 경로입니다: {path}")
        if with_viewer and target.is_file():
            cmd = self.s.viewers.get(target.suffix.lower()) or self.s.viewers.get("*")
            if cmd:
                # 뷰어 본체는 보이고, .bat으로 설정한 뷰어의 콘솔만 숨는다
                spawn_gui_detached([c.replace("{file}", str(target)) for c in cmd], cwd=str(target.parent))
                return {"ok": True, "opened": str(target), "with": cmd[0]}
        if os.name == "nt":
            if target.is_file() and not with_viewer:
                subprocess.Popen(["explorer", "/select,", str(target)])  # 파일은 탐색기에서 선택된 상태로
            else:
                os.startfile(str(target))  # noqa: S606 — 폴더 열기 또는 기본 연결 프로그램
        return {"ok": True, "opened": str(target)}

    def _local_tree(self, project_id: int, project_name: str | None, path: str, project_uid: str | None = None) -> dict:
        return self.s.workspace.tree(project_id, path, project_name, project_uid=project_uid)

    def _set_folder(self, project_id: int, project_name: str | None, project_uid: str, path: str | None) -> dict:
        """작업 폴더를 바꾼다. 에이전트 CLI 세션은 시작 폴더(cwd)가 고정이라 이 프로젝트의 세션은 잊는다
        — 서버가 다음 요청에서 새 폴더로 세션을 새로 열고 지난 대화 내용을 넘긴다."""
        if self.executor.running or self.executor.queued:
            raise ValueError("이 PC에서 케이스를 실행하는 중에는 작업 폴더를 바꿀 수 없습니다. 끝난 뒤 다시 시도하세요")
        info = self.s.workspace.set_folder(project_id, project_name, project_uid, path)
        for sid in [s for s, (_, _, uid) in self.session_project.items() if uid == project_uid]:
            self.session_project.pop(sid, None)
            self.session_runner.pop(sid, None)
        sync_project_context(Path(info["path"]))
        return info

    @staticmethod
    def _dialog(ask: str, *args: str) -> str:
        """이 PC에 Windows 선택 창(tkinter filedialog)을 띄워 고른 경로를 돌려준다(취소하면 빈 값). Tk는 별도 프로세스로 띄운다."""
        code = ("import sys, tkinter as tk\nfrom tkinter import filedialog\n"
                "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
                f"p = {ask}\nsys.stdout.write(p or '')\n")
        try:
            out = subprocess.run([sys.executable, "-c", code, *args], capture_output=True, text=True, encoding="utf-8",
                                 timeout=900, **hidden_kwargs())
        except subprocess.TimeoutExpired:
            return ""
        if out.returncode != 0:
            raise RuntimeError("선택 창을 열지 못했습니다")
        return str(Path(out.stdout.strip())) if out.stdout.strip() else ""

    def _pick_folder(self, initial: str | None, title: str | None) -> dict:
        try:
            path = self._dialog("filedialog.askdirectory(parent=r, initialdir=sys.argv[1] or None, title=sys.argv[2], mustexist=False)",
                                initial or "", title or "작업 폴더 선택")
        except RuntimeError:
            raise RuntimeError("폴더 선택 창을 열지 못했습니다. 경로를 직접 입력하세요") from None
        return {"path": path}

    # ---------- CAD/CAE 툴 경로 (사용자는 적지 않는다: 자동으로 찾고, 못 찾으면 웹에서 파일을 고른다) ----------
    def _pick_tool(self, key: str) -> dict:
        """툴 실행 파일을 이 PC의 파일 선택 창으로 고르게 하고 저장한다(취소하면 빈 값)."""
        names = toolscan.file_names(key)
        cur = self.s.tools.get(key) or ""
        initial = str(Path(cur).parent) if Path(cur).is_absolute() else os.environ.get("ProgramFiles", "C:\\")
        path = self._dialog("filedialog.askopenfilename(parent=r, initialdir=sys.argv[1], title=sys.argv[2], "
                            "filetypes=[('실행 파일', '*.exe *.bat *.cmd'), ('모든 파일', '*.*')])",
                            initial, f"{' 또는 '.join(names) or key} 선택")
        return self._set_tool(key, path) if path else {"key": key, "path": ""}

    def _set_tool(self, key: str, path: str | None) -> dict:
        info = toolscan.set_picked(key, path)
        toolscan.refresh(self.s, max_age=10 ** 9)  # 다시 찾지 않고 반영만
        self.send_threadsafe(self.hello())  # 웹의 툴 화면이 새로 고치게
        return info

    def _diagnose(self, rescan: bool = False) -> dict:
        from .diagnose import diagnose

        if rescan:  # 툴 화면 '다시 확인' — 지금 PC를 다시 훑는다(방금 찾았으면 그 결과를 쓴다)
            rev = self.s.tools_rev
            toolscan.refresh(self.s, max_age=15)
            if self.s.tools_rev != rev:
                self.send_threadsafe(self.hello())
        return diagnose(self.s)

    async def _watch_tools(self):
        """CAD/CAE 툴 설치 상태를 계속 따라간다. 시작할 때 찾고(설치 직후 찾았으면 그 결과를 쓴다), 이후 5분마다
        설치된 앱 목록·시작 메뉴·환경변수가 바뀌었거나(설치·제거·업그레이드) 쓰던 실행 파일이 없어졌으면 다시 찾는다.
        바뀌면 서버에 알려 웹의 툴 화면이 저절로 새로 고쳐진다."""
        from cae_adapters.catalog import resolve

        first = True
        while not (self.restart or self.quit):
            try:
                st = toolscan.load_state()
                fp = await asyncio.to_thread(toolscan.fingerprint)
                gone = [k for k, src in self.s.tool_source.items() if not resolve(self.s.tools.get(k))]
                stale = time.time() - st.get("scanned_ts", 0) > TOOL_FULL_SCAN_SEC
                if first or fp != st.get("fingerprint") or gone or stale:
                    rev = self.s.tools_rev
                    st = await asyncio.to_thread(toolscan.refresh, self.s, 600 if first and not gone else 0)
                    if self.s.tools_rev != rev:
                        log.info("CAD/CAE 툴 경로가 바뀌었습니다 — 바로 사용: %s", ", ".join(st.get("ready", [])) or "없음")
                        self.send_threadsafe(self.hello())
                first = False
            except Exception:
                log.exception("CAD/CAE 툴 찾기 실패")
            await asyncio.sleep(TOOL_WATCH_SEC)

    # ---------- 상태 보고 / 자동 업데이트 ----------
    def status(self) -> dict:
        return {"running_cases": self.executor.running, "queued_cases": self.executor.queued,
                "active_prompts": self.active_prompts, "update_state": self.update_state,
                "sessions": len(self.session_runner)}

    def busy(self) -> bool:
        if self.active_prompts or self.installing or self.devtests or self.executor.running or self.executor.queued:
            return True
        # 턴이 끝나도 AI가 백그라운드로 남긴 솔버가 돌 수 있다. 다시 시작하면 그 해석이 함께 끝나므로 기다린다
        return any(r.working_in_background() for r in list(self.runners.values()))

    def touch(self, msg_type: str | None):
        """사람이 일을 시킨 메시지면 시각을 남긴다. 화면을 열어 두면 계속 오는 조회(local.*)는 일로 치지 않는다."""
        if msg_type and msg_type.startswith(WORK_MSGS):
            self._last_work = time.monotonic()

    async def _wait_idle(self):
        """일이 없고, 마지막 일에서 IDLE_SEC가 지날 때까지 기다린다(대화를 주고받는 사이에 끼어들지 않게)."""
        while True:
            if self.busy():
                self._last_work = time.monotonic()
            elif time.monotonic() - self._last_work >= IDLE_SEC:
                return
            await asyncio.sleep(IDLE_POLL_SEC)

    def schedule_update(self, version: str, sha256: str | None, force: bool = False):
        if not updater.is_newer(version) and not force:
            return
        if updater.install_home() is None:
            self.update_state = f"dev-mode (최신 {version})"
            return
        task = self._update_task
        if task and not task.done():
            if self._update_firm or (version == self._update_version and not force):
                return
            task.cancel()  # 기다리던 자동 업데이트를 '지금 업데이트'나 더 새 버전으로 바꾼다
        self._update_version, self._update_firm = version, force
        self.update_state = f"pending:{version}"
        self._update_task = asyncio.create_task(self._update(version, sha256, force))

    def _cancel_update(self):
        task = self._update_task
        if task and not task.done() and not self._update_firm:
            task.cancel()
            self.update_state = "idle"

    async def _update(self, version: str, sha256: str | None, force: bool):
        if not force:
            await self._wait_idle()
        self.update_state = f"installing:{version}"
        if self.tray:
            self.tray.set("updating", version)
        try:
            await asyncio.to_thread(updater.prepare_update, self.api, version, sha256)
            if not force:
                await self._wait_idle()  # 설치하는 사이에 시작한 일을 끊지 않는다
            self._update_firm = True  # 여기부터는 되돌리지 않는다
            updater.activate(version)
        except Exception as e:
            log.exception("업데이트 실패")
            self.update_state = f"failed: {e}"[:300]
            return
        finally:
            if self.tray:
                self.tray.set("connected" if self.ws else "connecting", self.s.server_url)
        self.update_state = f"restarting:{version}"
        if force and self.tray:  # 자동 업데이트는 뒤에서 조용히 한다
            self.tray.notify(f"새 버전 {version}(으)로 다시 시작합니다")
        self.restart = True
        await self._send_heartbeat()
        if self.ws:
            await self.ws.close()

    async def _check_latest(self, manual: bool = False):
        if not (self.auto_update or manual):
            return
        try:
            latest = await asyncio.to_thread(self.api.get, "/api/agent/releases/latest")
            if manual and self.tray:
                self.tray.notify(f"새 버전 {latest['version']}이 있습니다. 작업이 없을 때 설치합니다"
                                 if updater.is_newer(latest["version"]) else f"최신 버전입니다 ({__version__})")
            if self.auto_update or manual:  # 묻는 사이에 꺼졌을 수 있다
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
        self.installing += 1
        try:
            res = installer.install(self.s, name)
        finally:
            self.installing -= 1
        self.send_threadsafe(self.hello())  # 설치 상태 갱신
        return res

    def _devtest(self, msg: dict) -> dict:
        """개발 AI의 원격 시험. 서버가 확인하지만 이 PC의 설정으로 한 번 더 막는다."""
        if time.time() > self.remote_test_until:
            raise RuntimeError("이 PC는 원격 시험을 허용하지 않았습니다 → 웹앱 설정에서 허용하세요")
        if self.tray and time.time() - self._devtest_noticed > 3600:  # 무엇이 돌고 있는지 PC 주인이 알게 한다
            self._devtest_noticed = time.time()
            self.tray.notify("개발 AI가 이 PC에서 CAD·CAE 연동을 시험하고 있습니다. 끄려면 웹앱 설정에서 해제하세요")
        self.devtests += 1
        try:
            return devtest.run(self.s, self.env, msg)
        finally:
            self.devtests -= 1

    async def handle(self, msg: dict):
        t, rid = msg.get("type"), msg.get("rid")
        self.touch(t)
        if t == "session.new":
            asyncio.create_task(self._respond(rid, self._new_session, msg["project_id"], msg.get("agent"), msg.get("project_name"),
                                              msg.get("project_uid")))
        elif t == "session.load":
            asyncio.create_task(self._respond(rid, self._load_session, msg["project_id"], msg["session_id"],
                                              msg.get("agent"), msg.get("project_name"), msg.get("project_uid")))
        elif t == "session.prompt":
            asyncio.create_task(self._respond(rid, self._prompt, msg["session_id"], msg["text"], msg.get("attachments")))
        elif t == "local.open":
            asyncio.create_task(self._respond(rid, self._local_open, msg["project_id"], msg.get("project_name"),
                                              msg.get("path", ""), bool(msg.get("with_viewer")), msg.get("project_uid")))
        elif t == "local.diagnose":
            asyncio.create_task(self._respond(rid, self._diagnose, bool(msg.get("rescan"))))
        elif t == "tools.pick":
            asyncio.create_task(self._respond(rid, self._pick_tool, msg["key"]))
        elif t == "tools.set":
            asyncio.create_task(self._respond(rid, self._set_tool, msg["key"], msg.get("path")))
        elif t == "local.tree":
            asyncio.create_task(self._respond(rid, self._local_tree, msg["project_id"], msg.get("project_name"), msg.get("path", ""),
                                              msg.get("project_uid")))
        elif t == "local.folder":
            asyncio.create_task(self._respond(rid, self.s.workspace.folder_info, msg["project_id"], msg.get("project_name"),
                                              msg.get("project_uid")))
        elif t == "local.folder.set":
            asyncio.create_task(self._respond(rid, self._set_folder, msg["project_id"], msg.get("project_name"),
                                              msg.get("project_uid"), msg.get("path")))
        elif t == "local.folder.check":
            asyncio.create_task(self._respond(rid, lambda p: {"path": str(self.s.workspace.check_folder(p))}, msg.get("path", "")))
        elif t == "local.pick_folder":
            asyncio.create_task(self._respond(rid, self._pick_folder, msg.get("initial"), msg.get("title")))
        elif t == "agent.install":
            asyncio.create_task(self._respond(rid, self._install, msg["agent"]))
        elif t == "devtest.run":
            asyncio.create_task(self._respond(rid, self._devtest, msg))
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
        # case.extract: Output 추가 — 해석한 케이스에서 새 Output만 뽑는다 · case.train_sample: 학습 샘플을 만들어 올린다
        elif t in ("case.run", "case.extract", "case.train_sample"):
            self.executor.submit(msg)
        elif t == "case.cancel":  # 창이 없으니 사용자가 웹앱에서 해석을 멈출 수 있는 유일한 길이다
            self.executor.cancel(msg.get("case_ids") or [], msg.get("extract_ids"))
        elif t in ("agent.release", "agent.update"):
            if self.auto_update or msg.get("force"):
                self.schedule_update(msg["version"], msg.get("sha256"), bool(msg.get("force")))
        elif t == "agent.settings":  # 연결할 때마다, 사용자가 설정을 바꿀 때마다 온다
            self.auto_update = bool(msg.get("auto_update", True))
            self.remote_test_until = float(msg.get("remote_test_until") or 0)
            if self.auto_update:
                asyncio.create_task(self._check_latest())
            else:
                self._cancel_update()
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
        asyncio.create_task(self._watch_tools())
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
                    await self._send_heartbeat()  # 최신 버전 확인은 서버가 hello에 답하는 agent.settings를 받고 한다
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
