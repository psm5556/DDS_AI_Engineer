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
import uuid
from concurrent.futures import Future
from pathlib import Path

import websockets

from cae_adapters.hidden import console_python, hidden_kwargs, spawn_gui_detached

from . import __version__, devtest, installer, toolscan, updater
from .acp_runner import AcpRunner
from .config import CONFIG_PATH, REPO_ROOT, load_settings
from .context import sync_project_context
from .executor import CaseExecutor
from .headless_runner import HeadlessRunner
from .session_history import move_claude_session
from .outbox import Outbox, shrink_permission, shrink_update
from .runner import AgentError, AgentRunner, SessionGone
from .server_api import ServerApi
from .state import AgentState
from .tray import Tray

log = logging.getLogger("dds-agent")
REPLACED_CODE = 4000  # 서버: 같은 사용자의 새 연결이 들어왔다(server/app/agent/link.py)
STANDBY_POLL_SEC = 60
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
IDLE_SEC = 300  # 일이 끝나고 이만큼 조용해야 자동 업데이트를 받아 둔다
IDLE_RESTART_SEC = 3600  # CLI가 떠 있으면 다시 시작이 그 CLI를 끝낸다. 다음 요청이 또 몇 분 걸리므로 더 오래 조용할 때까지 미룬다
IDLE_POLL_SEC = 10
WORK_MSGS = ("session.", "case.", "permission.", "agent.install", "tools.set", "tools.pick")  # 사람이 일을 시킨 메시지
MCP_SERVERS = ("dds_flow", "dds_cae", "dds_study", "dds_live")
# MCP 시작·도구 시간 제한. 긴 해석이 실패로 보이거나 MCP가 늦게 떠 dds 도구 없이 대화가 시작되지 않게 한다
MCP_START_MS, MCP_TOOL_MS = 30 * 60 * 1000, 25 * 3600 * 1000  # Claude Code: MCP_TIMEOUT · MCP_TOOL_TIMEOUT
CODEX_DOC_BYTES = 256 * 1024
# 시간 제한을 바꿀 수 없는 CLI는 긴 도구가 이 안에 돌아오게 한다(dds_cae.script_status·dds_study._wait로 이어서 확인).
# Gemini 기본 10분. Codex는 기본 60초이고 codex-acp가 서버별 값을 넘길 길이 CODEX_CONFIG뿐이라 믿지 않고 짧게 잡는다
MCP_WAIT_LIMIT_SEC = {"gemini": 540, "codex": 50}
SEEN_TURNS = 500
# 서버가 그때의 프로젝트 이름을 담아 보내는 메시지. 대기 중인 케이스·session.prompt·state.json의 이름은 낡았을 수 있어 믿지 않는다
NAME_MSGS = ("session.new", "session.load", "local.open", "local.tree", "local.folder", "local.folder.set")
NAME_RETRY_SEC = 300  # 이름 바꾸기를 기다리는 프로젝트를 다시 보는 간격(한가할 때)
NAME_TRY_GAP_SEC = 20  # 화면을 열어 둔 조회가 잠긴 폴더를 계속 두드리지 않게


class Agent:
    def __init__(self):
        self.s = load_settings()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.ws = None
        self.env = {**os.environ, "PYTHONIOENCODING": "utf-8", "DDS_SERVER_URL": self.s.server_url,
                    "DDS_TOKEN": self.s.token}
        self.env.setdefault("MCP_TIMEOUT", str(MCP_START_MS))
        self.env.setdefault("MCP_TOOL_TIMEOUT", str(MCP_TOOL_MS))
        self.runners: dict[str, AgentRunner] = {}  # 에이전트 이름 → 러너(상주 프로세스)
        self.session_runner: dict[str, str] = {}  # 세션 id → 에이전트 이름
        self.session_project: dict[str, tuple[int, str | None, str | None]] = {}  # 세션 id → (프로젝트 id, 이름, uid)
        self.session_owner: dict[str, int] = {}  # 세션 id → 프로젝트 id. 폴더를 바꿔 잊은 세션도 CLI가 끝날 때까지 옛 폴더를 쓴다
        self._busy_projects: dict[int, int] = {}  # 프로젝트 id → 세션을 여는 중인 수
        self._wanted_names: dict[int, tuple[str | None, str]] = {}  # 프로젝트 id → 서버가 알려 준 이름(마지막 것이 이긴다)
        self._known_names: dict[int, tuple[str | None, str]] = {}  # 이미 맞춘 이름. 자주 오는 조회가 파일을 읽지 않게
        self._name_lock = threading.Lock()  # 이름 판단과 바꾸기를 한 번에 하나만
        self._name_tried: dict[int, float] = {}
        self._names_at = 0.0
        self._lock = threading.Lock()
        self.executor = CaseExecutor(self.s, self._reliable)  # 해석은 연결과 상관없이 끝난다. 끊긴 사이의 결과를 잃지 않게 outbox로 보낸다
        self.api = ServerApi(self.s)
        self.boot = uuid.uuid4().hex  # 이 프로세스 표시. 서버가 "같은 Agent인데 턴을 모른다"와 "Agent가 다시 시작했다"를 가린다
        self.outbox = Outbox(self.boot)
        self.state = AgentState()
        self.active_turns: dict[str, dict] = {}  # 턴 id → {turn_id, chat_id, session_id, agent, started_at, cancelled}
        self._seen_turns: dict[str, None] = {}  # 재연결로 다시 온 프롬프트를 두 번 돌리지 않는다
        self._turn_lock = threading.Lock()  # 턴이 끝나 outbox로 옮겨 가는 순간에 hello가 그 턴을 빠뜨리지 않게
        self.open_perms: dict[str, dict] = {}  # 사람이 아직 답하지 않은 권한 요청(재연결 때 다시 보낸다)
        self._new_by_key: dict[str, Future] = {}  # 턴 id → session/new. 끊겨서 서버가 다시 물어도 세션을 또 만들지 않는다
        self._sid_locks: dict[str, threading.Lock] = {}
        self.preparing = 0  # CLI 시작·세션 준비 중인 수(미리 띄우기 포함)
        self._forced: set[str] = set()  # 사용자가 다시 시작을 고른 에이전트
        self._force_done: dict[str, threading.Event] = {}  # 다시 시작 중인 에이전트. 그 사이 온 세션 요청이 죽어 가는 CLI에 닿지 않게 기다린다
        self.server_acks = True  # 서버가 agent.ack를 보내는지. 옛 서버(되돌린 경우)면 agent.settings에 표시가 없다
        self._sent_seq = 0
        self._warm_lock = threading.Lock()
        self._out_wake: asyncio.Event | None = None
        self.closing = False
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
                    meta = self.s.agents.get(name) or {}
                    r = AcpRunner(cmd, self._on_event, on_perm, self._runner_env(name), start_timeout=self.s.cli_start_timeout_sec,
                                  label=meta.get("label"), login=meta.get("login"))
                r.on_exit = lambda code, tail, sids, n=name: self._on_runner_exit(n, code, tail, sids)
                r.on_timing = lambda kind, sec, n=name: self.state.record_time(n, kind, sec)
                self.runners[name] = r
            return self.runners[name]

    def _runner_env(self, name: str) -> dict:
        """CLI별 환경. Codex 전역 설정 파일은 고치지 않는다. codex-acp는 CODEX_CONFIG를 세션 설정에 합친다(번들 확인).
        점으로 잇는 키라 MCP 정의 뒤에 적용될 때만 먹는다. 사내에서 확인 전까지는 MCP_WAIT_LIMIT_SEC를 믿고, 이 값은 덤이다."""
        env = dict(self.env)
        if name == "codex" and "CODEX_CONFIG" not in env:
            config = {}
            for server in MCP_SERVERS:
                key = f"mcp_servers.{server.replace('_', '-')}"
                config[f"{key}.startup_timeout_sec"] = MCP_START_MS // 1000
                config[f"{key}.tool_timeout_sec"] = MCP_TOOL_MS // 1000
            # Codex는 AGENTS.md를 기본 32KiB에서 자른다. 사용자 글 뒤에 붙는 DDS 규칙이 잘리지 않게 넉넉히 둔다
            config["project_doc_max_bytes"] = CODEX_DOC_BYTES
            env["CODEX_CONFIG"] = json.dumps(config)
        return env

    def runner_of(self, session_id: str) -> AgentRunner:
        name = self.session_runner.get(session_id)
        if not name:
            raise SessionGone("이 세션을 다시 불러와야 합니다 → 같은 요청을 다시 보내면 이전 대화를 이어 붙입니다.")
        return self.runner(name)

    def live_sessions(self) -> list[str]:
        out = []
        for sid, name in list(self.session_runner.items()):
            r = self.runners.get(name)
            if r and sid in r.live_sessions():
                out.append(sid)
        return out

    def running_agents(self) -> list[str]:
        return [n for n, r in list(self.runners.items()) if getattr(r, "alive", lambda: False)()]

    # ---------- 서버로 보내기 ----------
    def send_threadsafe(self, msg: dict):
        if self.loop and self.ws:
            asyncio.run_coroutine_threadsafe(self.ws.send(json.dumps(msg, ensure_ascii=False, default=str)), self.loop)

    def _reliable(self, msg: dict):
        """끊겨 있어도 잃으면 안 되는 메시지. 서버가 받았다고 답할 때까지 outbox에 남는다."""
        self.outbox.add(msg)
        if self.loop and self._out_wake:
            try:
                self.loop.call_soon_threadsafe(self._out_wake.set)
            except RuntimeError:  # 루프가 닫히는 중이다
                pass

    async def _pump(self, ws):
        """outbox를 이 연결로 순서대로 보낸다. 연결이 바뀌면 답을 못 받은 것부터 다시 보낸다."""
        sent = 0
        try:
            while True:
                batch = self.outbox.after(sent)
                if not batch:
                    self._out_wake.clear()
                    if not self.outbox.after(sent):
                        await self._out_wake.wait()
                    continue
                for seq, text in batch:
                    await ws.send(text)
                    sent = self._sent_seq = seq
                    if not self.server_acks:  # 답이 오지 않는 서버: 쥐고 있으면 끝없이 쌓이고 재연결마다 글자가 두 번 쌓인다
                        self.outbox.ack(seq)
        except (OSError, websockets.WebSocketException) as e:  # 연결이 끊겼다. 다음 연결이 처음부터 다시 보낸다
            log.debug("outbox 전송 중단: %s", e)

    def _on_event(self, session_id: str, update: dict):
        for small in shrink_update(update):  # 큰 도구 입출력 하나로 WebSocket 한도를 넘겨 연결이 끊기지 않게
            self._reliable({"type": "agent.event", "session_id": session_id, "update": small})

    def _on_permission(self, session_id: str, request_id: str, params: dict):
        msg = {"type": "agent.permission", "session_id": session_id, "request_id": request_id, "params": shrink_permission(params)}
        self.open_perms[request_id] = msg
        self._reliable(msg)

    def _on_runner_exit(self, name: str, code, tail: str, sessions: set[str]):
        if self.closing:
            return
        r = self.runners.get(name)
        # 다시 띄운 CLI가 이미 불러온 세션은 살아 있다. 옛 프로세스의 뒤늦은 알림이 지우면 바로 다음 프롬프트가 실패한다
        sessions = sessions - (r.live_sessions() if r else set())
        for sid in sessions:
            self.session_runner.pop(sid, None)
            self.session_project.pop(sid, None)
            self.session_owner.pop(sid, None)
        prefix = f"{name}:"
        for rid in [r for r in self.open_perms if r.startswith(prefix)]:
            self.open_perms.pop(rid, None)
        forced = name in self._forced
        self._forced.discard(name)
        self._reliable({"type": "agent.runner_exit", "agent": name, "session_ids": sorted(sessions), "code": code,
                        "stderr_tail": tail[-2000:], "reason": "force_restart" if forced else "exit"})
        if r and getattr(r, "alive", lambda: False)():  # 이미 다시 떴다. 서버가 "없음"으로 남겨 두지 않게 한다
            self._send_live()
        # 그 아래 프로세스(claude.exe·MCP)가 끝나는 데 시간이 걸린다. 아직 잠겨 있으면 다음 번에 또 시도한다
        self._settle_soon(2)
        self._settle_soon(20)

    def _send_live(self):
        self._reliable({"type": "agent.live", "live_sessions": self.live_sessions(), "running_agents": self.running_agents(),
                        "start_times": self.state.start_times()})

    def _stage(self, turn_id: str | None, agent: str, stage: str):
        if turn_id:
            typical = self.state.typical_for_stage(agent, stage) if stage not in ("thinking", "attachments") else None
            self._reliable({"type": "session.stage", "turn_id": turn_id, "agent": agent, "stage": stage, "typical_start_sec": typical})

    def hello(self) -> dict:
        with self._turn_lock:
            turns = [{k: t.get(k) for k in ("turn_id", "chat_id", "session_id", "agent", "started_at")} for t in self.active_turns.values()]
            turns += self.outbox.ended_turns()  # 끝났어도 서버가 모르면 함께 알린다. 이어서 오는 출력을 서버가 받을 수 있게
        return {"type": "hello", "hostname": socket.gethostname(), "version": __version__, "platform": platform.platform(),
                "agents": self.s.available_agents(), "default_agent": self.s.default_agent,
                "workspace": str(self.s.workspace_dir), "tools": sorted(self.s.tools), "tools_rev": self.s.tools_rev,
                "boot": self.boot, "live_sessions": self.live_sessions(), "active_turns": turns,
                "active_cases": sorted({*self.executor.active_cases(), *self.outbox.pending_cases()}),  # 전하지 못한 결과도 '끝나지 못한 케이스'가 아니다
                "settles_cases": True,  # 돌지 않는 케이스를 물으면 case.json으로 답한다(case.settle)
                "running_agents": self.running_agents(), "start_times": self.state.start_times()}

    def mcp_servers(self, project_id: int, agent: str | None = None, cwd: str | None = None, uid: str | None = None) -> list[dict]:
        """ACP session/new에 넘길 MCP 서버 정의. 세션별로 DDS_PROJECT_ID를 고정한다.
        MCP의 cwd는 프로젝트 폴더다. 설정·작업 공간을 절대 경로로 주어 Agent와 다른 곳에 폴더를 만들지 않게 한다.
        Codex는 환경변수를 골라 넘기므로 물려받기를 믿지 않고 여기에 적는다."""
        env = {"DDS_PROJECT_ID": str(project_id), "DDS_SERVER_URL": self.s.server_url, "DDS_TOKEN": self.s.token,
               "PYTHONPATH": str(REPO_ROOT), "PYTHONIOENCODING": "utf-8",
               "DDS_AGENT_CONFIG": str(CONFIG_PATH), "DDS_WORKSPACE": str(self.s.workspace_dir)}
        if cwd:
            env["DDS_PROJECT_DIR"] = str(cwd)
        if uid:
            env["DDS_PROJECT_UID"] = uid
        if agent in MCP_WAIT_LIMIT_SEC:
            env["DDS_MCP_WAIT_LIMIT_SEC"] = str(MCP_WAIT_LIMIT_SEC[agent])
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
            # 시간 초과 예외는 str()이 빈 글자다. 빈 오류는 원인이 사라지고 성공으로 읽힐 수 있다
            msg = {"type": "response", "rid": rid, "error": str(e) or repr(e)}
        ws = self.ws
        if ws is None:  # 그새 끊겼다. 서버가 같은 요청을 다시 묻는다
            return
        try:
            await ws.send(json.dumps(msg, ensure_ascii=False, default=str))
        except (OSError, websockets.WebSocketException) as e:
            log.info("응답을 보내지 못했습니다: %s", e)

    # ---------- 프로젝트 폴더 이름 (workspace.sync_name) ----------
    def _project_free(self, project_id: int, mine: int = 0) -> bool:
        """지금 실제 폴더 이름을 바꿔도 되는가. rename이 성공하는지로 판단하지 않는다:
        Codex·Gemini는 폴더를 잠그지 않아 성공해도 메모리의 cwd가 끊긴다. HPC 해석은 PC에서 아무것도 잠그지 않는다."""
        if self.executor.running or self.executor.queued or self.devtests:
            return False
        if self._busy_projects.get(project_id, 0) > mine:
            return False
        for r in list(self.runners.values()):
            for sid in getattr(r, "live_sessions", set)():
                owner = self.session_owner.get(sid)
                if owner is None or owner == project_id:  # 모르는 세션은 쓰고 있다고 본다
                    return False
        return True

    def _note_name(self, msg: dict):
        """수신 루프에서 순서대로 부른다. 처리는 스레드에서 해도 마지막 이름이 이기게 한다."""
        pid, uid, name = msg.get("project_id"), msg.get("project_uid"), msg.get("project_name")
        if pid is None or not name or self._known_names.get(pid) == (uid, name):
            return
        self._wanted_names[pid] = (uid, name)

    def _settle_names(self, project_id: int | None = None, mine: int = 0, gentle: bool = False):
        """서버가 알려 준 이름과 기다리던 이름을 맞춘다. 세션·솔버를 끝내지 않는다. 안 되면 디스크에 남겨 다음 기회에 한다.
        gentle: 화면을 열어 두면 계속 오는 조회. 잠긴 폴더를 간격을 두고만 다시 시도한다(시도마다 잠깐 기다린다)."""
        moved = []
        with self._name_lock:
            ws = self.s.workspace
            todo = {p: v for p, v in self._wanted_names.items() if project_id is None or p == project_id}
            for p in todo:
                self._wanted_names.pop(p, None)
            try:
                pending = ws.pending_projects()
            except OSError as e:  # 다음 기회에 본다. 파일 탭·세션 시작을 막지 않는다
                log.info("기다리는 이름을 읽지 못했습니다: %s", e)
                pending = []
            for p, u in pending:
                if (project_id is None or p == project_id) and p not in todo:
                    todo[p] = (u, None)
            now = time.monotonic()
            for p, (uid, name) in todo.items():
                skip = gentle and name is None and now - self._name_tried.get(p, -1e9) < NAME_TRY_GAP_SEC
                may = False if skip else (lambda p=p: self._project_free(p, mine if p == project_id else 0))
                try:
                    res = ws.sync_name(p, uid, name, may_move=may)
                except OSError as e:  # 폴더 이름 때문에 대화·해석을 막지 않는다
                    log.warning("프로젝트 폴더 이름을 맞추지 못했습니다(P%04d): %s", p, e)
                    continue
                if name is not None:
                    self._known_names[p] = (uid, name)
                if res.get("pending"):
                    self._name_tried[p] = now
                else:
                    self._name_tried.pop(p, None)
                if res.get("moved"):
                    log.info("프로젝트 폴더 이름을 바꿨습니다: %s", res.get("path"))
                    moved.append(Path(res["path"]))
        for d in moved:  # 저장소 안이면 제외 목록을 새 경로로 맞춘다. 스킬 복사가 길 수 있어 다른 프로젝트의 시작을 막지 않게 잠금 밖에서 한다
            try:
                sync_project_context(d)
            except (OSError, RuntimeError) as e:  # 복사 실패는 대화에 보이도록 RuntimeError로 온다. 여기는 파일 탭·폴더 열기 안이라 막지 않는다
                log.info("이름을 바꾼 폴더의 규칙 동기화 실패(다음 세션에 다시 한다): %s", e)

    def _settle_quietly(self):
        try:
            self._settle_names()
        except Exception as e:  # 어떤 실패도 턴·연결·종료를 막지 않는다
            log.warning("폴더 이름 맞추기 실패: %s", str(e) or repr(e))
        self._names_at = time.monotonic()

    def _settle_soon(self, delay: float = 0.0):
        """턴 끝·CLI 종료처럼 잠금이 풀릴 만한 때. 호출한 쪽을 기다리게 하지 않는다."""
        t = threading.Timer(delay, self._settle_quietly)
        t.daemon = True
        t.start()

    def _session_cwd(self, session_id: str, d: Path | str) -> str:
        """시작한 경로 문자열이 옛 이름 접합으로 아직 이 폴더를 가리키면 그대로 쓴다. Gemini는 경로 문자열로 기록을 찾는다."""
        old = self.state.session_cwd(session_id)
        try:
            if old and Path(old).is_dir() and os.path.samefile(old, d):
                return old
        except OSError:
            pass
        return str(d)

    def _load_cwds(self, agent: str, session_id: str, d: Path) -> list[str]:
        """session/load에 시도할 작업 폴더. CLI마다 지난 대화를 찾는 법이 달라 따로 둔다(실제 프로그램으로 확인, docs/local-data.md).
        - Claude: 접합을 풀어 실제 경로로 찾는다. 어느 이름을 넘겨도 같으므로 기록을 옮겨 두고 지금 경로를 넘긴다.
        - Codex: 세션 id로 찾는다. 어느 경로든 찾으므로 지금 경로를 넘긴다(옛 이름으로 세션을 열 이유가 없다).
        - Gemini: 경로 문자열 그대로 찾는다. 시작한 문자열을 넘긴다. 이 버전 전에 만든 세션은 기억이 없어 옛 이름 접합을 차례로 본다.
        - 그 밖의 CLI: 찾는 법을 모르니 시작한 문자열을 그대로 넘긴다."""
        if agent == "claude":
            try:
                move_claude_session(session_id, d, self._runner_env(agent))
            except OSError as e:  # 못 옮기면 불러오기가 False를 돌려주고, 서버가 요약을 넘겨 새 세션으로 잇는다
                log.warning("Claude 세션 기록을 옮기지 못했습니다(%s): %s", session_id, e)
            return [str(d)]
        if agent == "codex":
            return [str(d)]
        first = self._session_cwd(session_id, d)
        if agent == "gemini" and not self.state.session_cwd(session_id):
            try:
                return [first, *self.s.workspace.old_paths(d)]
            except OSError as e:
                log.info("옛 이름을 읽지 못했습니다: %s", e)
        return [first]

    def _busy_project(self, project_id: int, n: int):
        with self._lock:
            self._busy_projects[project_id] = self._busy_projects.get(project_id, 0) + n
            if self._busy_projects[project_id] <= 0:
                self._busy_projects.pop(project_id, None)

    def _prepare(self, project_id: int, project_name: str | None = None, project_uid: str | None = None, report=None) -> str:
        # CLI가 뜨기 전이 가장 좋은 때다. 뜨면 며칠씩 폴더를 잡고 있을 수 있다. 부른 쪽이 _busy_project로 자기 몫을 세어 두었다
        mine = self._busy_projects.get(project_id, 0)
        try:
            self._settle_names(project_id, mine=min(mine, 1))
        except Exception as e:
            log.warning("폴더 이름 맞추기 실패: %s", str(e) or repr(e))
        d = self.s.workspace.project_dir(project_id, project_name, project_uid)
        if report:
            report("context_sync")
        sync_project_context(d)
        return str(d)

    def _new_session(self, project_id: int, agent: str | None, project_name: str | None = None,
                     project_uid: str | None = None, turn_id: str | None = None) -> dict:
        name = agent or self.s.default_agent
        self._wait_force(name)
        owner, fut = True, None
        if turn_id:
            with self._lock:
                fut = self._new_by_key.get(turn_id)
                owner = fut is None
                if owner:
                    fut = self._new_by_key[turn_id] = Future()
                    while len(self._new_by_key) > SEEN_TURNS:
                        self._new_by_key.pop(next(iter(self._new_by_key)))
        if not owner:
            return fut.result()  # 끊겨서 서버가 다시 물었다. 준비 중이거나 이미 만든 세션을 쓴다
        self.preparing += 1
        self._busy_project(project_id, 1)
        report = lambda stage: self._stage(turn_id, name, stage)  # noqa: E731
        try:
            cwd = self._prepare(project_id, project_name, project_uid, report)
            sid = self.runner(name).new_session(cwd, self.mcp_servers(project_id, name, cwd, project_uid), report)
            self.session_runner[sid] = name
            self.session_project[sid] = (project_id, project_name, project_uid)
            self.session_owner[sid] = project_id
            self.state.remember_cwd(sid, cwd)
            res = {"session_id": sid, "agent": name}
            if fut:
                fut.set_result(res)
            return res
        except Exception as e:
            if fut:
                fut.set_exception(e)
                self._new_by_key.pop(turn_id, None)  # 실패는 기억하지 않는다. 다시 시도할 수 있어야 한다
            raise
        finally:
            self.preparing -= 1
            self._busy_project(project_id, -1)

    def _sid_lock(self, session_id: str) -> threading.Lock:
        with self._lock:
            return self._sid_locks.setdefault(session_id, threading.Lock())

    def _load_session(self, project_id: int, session_id: str, agent: str | None, project_name: str | None = None,
                      project_uid: str | None = None, turn_id: str | None = None) -> dict:
        name = agent or self.s.default_agent
        self._wait_force(name)
        self.preparing += 1
        self._busy_project(project_id, 1)
        report = lambda stage: self._stage(turn_id, name, stage)  # noqa: E731
        try:
            with self._sid_lock(session_id):  # 미리 띄우기가 같은 세션을 불러오는 중이면 끝날 때까지 기다린다
                r = self.runner(name)
                if self.session_runner.get(session_id) == name and session_id in r.live_sessions():
                    return {"ok": True, "live": True}  # 살아 있는 세션에 load하면 Gemini가 세션을 dispose한다
                real = Path(self._prepare(project_id, project_name, project_uid, report))
                for cwd in self._load_cwds(name, session_id, real):
                    if r.load_session(session_id, cwd, self.mcp_servers(project_id, name, cwd, project_uid), report):
                        self.session_runner[session_id] = name
                        self.session_project[session_id] = (project_id, project_name, project_uid)
                        self.session_owner[session_id] = project_id
                        self.state.remember_cwd(session_id, cwd)
                        return {"ok": True}
                return {"ok": False}
        finally:
            self.preparing -= 1
            self._busy_project(project_id, -1)

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

    def _start_turn(self, msg: dict):
        """프롬프트를 턴으로 받는다. 응답을 기다리지 않으므로 연결이 끊겨도 턴은 계속되고 끝은 agent.turn_end로 알린다.
        서버를 옛 버전으로 되돌리면 rid만 있고 turn_id가 없다. 그때는 끝날 때 응답으로 답한다(Agent는 되돌릴 수 없으니 여기서 맞춘다)."""
        if not msg.get("turn_id"):
            msg = {**msg, "turn_id": uuid.uuid4().hex}
        turn_id = msg["turn_id"]
        with self._turn_lock:
            if turn_id in self._seen_turns:  # 서버가 전달을 확신하지 못해 다시 보낸 것
                return
            self._seen_turns[turn_id] = None
            while len(self._seen_turns) > SEEN_TURNS:
                self._seen_turns.pop(next(iter(self._seen_turns)))
            self.active_turns[turn_id] = {"turn_id": turn_id, "chat_id": msg.get("chat_id"), "session_id": msg["session_id"],
                                          "agent": msg.get("agent") or self.session_runner.get(msg["session_id"]),
                                          "started_at": msg.get("started_at"), "cancelled": False}
        threading.Thread(target=self._run_turn, args=(msg,), daemon=True, name=f"turn-{turn_id[:8]}").start()

    def _run_turn(self, msg: dict):
        turn_id, sid = msg["turn_id"], msg["session_id"]
        turn = self.active_turns[turn_id]
        stop, error, gone = "error", None, False
        with self._lock:
            self.active_prompts += 1
        try:
            text = msg["text"]
            attachments = msg.get("attachments") or []
            if attachments:
                self._stage(turn_id, turn["agent"], "attachments")
                text += self._fetch_attachments(sid, attachments)
                self._stage(turn_id, turn["agent"], "thinking")
            if turn["cancelled"]:
                stop = "cancelled"
            else:
                runner = self.runner_of(sid)
                self.state.remember_session(self.session_runner[sid], sid, self.session_project.get(sid))
                stop = runner.prompt(sid, text)
        except SessionGone as e:
            error, gone = str(e), True
        except Exception as e:
            log.exception("턴 실패")
            error = str(e) or repr(e)
        finally:
            with self._lock:
                self.active_prompts -= 1
            with self._turn_lock:  # 지우기와 outbox에 넣기를 한 번에: 그 사이에 hello가 나가면 서버가 턴을 잃은 줄 안다
                self.active_turns.pop(turn_id, None)
                end = {"type": "agent.turn_end", "turn_id": turn_id, "chat_id": turn.get("chat_id"), "session_id": sid,
                       "agent": turn.get("agent"), "started_at": turn.get("started_at"), "stop_reason": stop}
                if error:
                    end["error"] = error
                if gone:
                    end["session_gone"] = True
                dropped = self.outbox.take_dropped(sid)
                if dropped:
                    end["dropped_events"] = dropped
                if msg.get("rid") is None:
                    self._reliable(end)
            if msg.get("rid") is not None:
                reply = {"type": "response", "rid": msg["rid"]}
                self.send_threadsafe({**reply, "error": error} if error else {**reply, "result": {"stop_reason": stop}})
            self._settle_soon()

    def _cancel_session(self, session_id: str, turn_id: str | None):
        turn = self.active_turns.get(turn_id) if turn_id else None
        if turn:
            turn["cancelled"] = True  # 첨부를 받는 중이면 프롬프트를 보내지 않는다
        for rid, m in list(self.open_perms.items()):
            if m.get("session_id") == session_id:
                self.open_perms.pop(rid, None)
        self.runner_of(session_id).cancel(session_id)

    def _permission_reply(self, request_id: str, option_id: str | None):
        self.open_perms.pop(request_id, None)
        name, _, req = request_id.partition(":")
        self.runner(name).respond_permission(req, option_id)

    def _begin_force(self, name: str):
        """수신 루프에서 바로 표시한다: 바로 뒤에 오는 session.load가 아직 살아 있는 CLI를 보고 "살아 있다"고 답하면 안 된다."""
        self._force_done[name] = threading.Event()

    def _wait_force(self, name: str):
        ev = self._force_done.get(name)
        if ev:
            ev.wait(timeout=60)

    def _force_restart(self, name: str):
        """사용자가 멈췄는데 응답이 없다. CLI와 그 아래(멈춘 스크립트·솔버)를 끝낸다. 창이 보이는 CAD는 Job.stop이 남긴다.
        끝나면 다시 띄워 다음 요청을 기다리지 않게 한다."""
        ev = self._force_done.get(name)
        try:
            r = self.runners.get(name)
            if not r:
                return
            if getattr(r, "alive", lambda: False)():
                self._forced.add(name)  # 감시 스레드가 runner_exit를 보낼 때 "사용자가 끊었다"로 알린다
            r.shutdown()
        finally:
            if ev:
                ev.set()
        self._warm_up(name)

    def _background_task(self, fn, *args):
        """수신 루프를 막지 않는다. 멈춘 CLI에 쓰다 막히거나 죽어 가는 CLI에서 예외가 나도 서버 연결과 다른 대화는 그대로다."""
        async def run():
            try:
                await asyncio.to_thread(fn, *args)
            except Exception as e:
                log.warning("%s 실패: %s", getattr(fn, "__name__", fn), str(e) or repr(e))
        asyncio.create_task(run())

    def _warm_up(self, name: str | None = None):
        """마지막에 쓴 CLI를 미리 띄우고(initialize까지) 마지막 대화를 불러온다. 없으면 띄우지 않는다.
        설치되지 않았거나 로그인되지 않은 것은 조용히 건너뛴다."""
        if not self.s.warm_up or not self._warm_lock.acquire(blocking=False):
            return
        self.preparing += 1
        try:
            data = self.state.data
            name = name or data.get("last_agent")
            if not name or name not in self.s.agents:
                return
            if not next((a["installed"] for a in self.s.available_agents() if a["name"] == name), False):
                return
            runner = self.runner(name)
            runner.warm_up()
            self._send_live()
            last = data.get("last_session") or {}
            sid, pid = last.get("session_id"), last.get("project_id")
            if sid and pid and last.get("agent") == name:
                res = self._load_session(pid, sid, name, last.get("project_name"), last.get("project_uid"))
                if not res.get("ok"):
                    self.state.forget_session(sid)  # 다음에 또 시도해 시간을 쓰지 않게
                self._send_live()
        except Exception as e:  # 준비에 실패해도 요청이 오면 그때 이유와 함께 알린다
            log.info("AI 도구 미리 띄우기 실패(%s): %s", name, str(e) or repr(e))
        finally:
            self.preparing -= 1
            self._warm_lock.release()

    def _start_warm_up(self):
        if self.s.warm_up:
            threading.Thread(target=self._warm_up, daemon=True, name="warm-up").start()

    # ---------- 내 PC 작업 폴더 ----------
    def _local_open(self, project_id: int, project_name: str | None, path: str, with_viewer: bool,
                    project_uid: str | None = None) -> dict:
        self._settle_names(project_id, gentle=True)
        ws = self.s.workspace
        target = ws.resolve(project_id, path, project_name, project_uid)
        if not target.exists():
            raise FileNotFoundError(f"없는 경로입니다: {path}")
        target = ws.shown_path(project_id, target, project_name, project_uid)  # 숨겨 둔 실제 폴더 이름을 사람에게 보이지 않는다
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
        self._settle_names(project_id, gentle=True)
        return self.s.workspace.tree(project_id, path, project_name, project_uid=project_uid)

    def _local_folder(self, project_id: int, project_name: str | None, project_uid: str | None = None) -> dict:
        self._settle_names(project_id, gentle=True)
        return self.s.workspace.folder_info(project_id, project_name, project_uid)

    def _set_folder(self, project_id: int, project_name: str | None, project_uid: str, path: str | None) -> dict:
        """작업 폴더를 바꾼다. 에이전트 CLI 세션은 시작 폴더(cwd)가 고정이라 이 프로젝트의 세션은 잊는다
        — 서버가 다음 요청에서 새 폴더로 세션을 새로 열고 지난 대화 내용을 넘긴다."""
        if self.executor.running or self.executor.queued:
            raise ValueError("이 PC에서 케이스를 실행하는 중에는 작업 폴더를 바꿀 수 없습니다. 끝난 뒤 다시 시도하세요")
        info = self.s.workspace.set_folder(project_id, project_name, project_uid, path)
        for sid in [s for s, (_, _, uid) in self.session_project.items() if uid == project_uid]:
            self.session_project.pop(sid, None)
            self.session_runner.pop(sid, None)
        # 기본 위치로 돌아왔다면 그동안 바뀐 이름을 따라간다. 직접 정한 폴더는 이름을 바꾸지 않는다
        self._known_names.pop(project_id, None)
        self._note_name({"project_id": project_id, "project_uid": project_uid, "project_name": project_name})
        self._settle_names(project_id)
        info = self.s.workspace.folder_info(project_id, project_name, project_uid)
        sync_project_context(Path(info["real"]))  # 접합으로 맞추면 저장소 안 경로 계산이 어긋난다
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
                "sessions": len(self.session_runner), "cli_start_sec": self.state.start_times()}  # 사내 환경의 실제 시작 시간을 재는 근거

    def busy(self) -> bool:
        if self.active_prompts or self.installing or self.devtests or self.preparing or self.executor.running or self.executor.queued:
            return True
        # 턴이 끝나도 AI가 백그라운드로 남긴 솔버가 돌 수 있다. 다시 시작하면 그 해석이 함께 끝나므로 기다린다
        return any(r.working_in_background() for r in list(self.runners.values()))

    def touch(self, msg_type: str | None):
        """사람이 일을 시킨 메시지면 시각을 남긴다. 화면을 열어 두면 계속 오는 조회(local.*)는 일로 치지 않는다."""
        if msg_type and msg_type.startswith(WORK_MSGS):
            self._last_work = time.monotonic()

    async def _wait_idle(self, for_restart: bool = False):
        """일이 없고, 마지막 일에서 IDLE_SEC가 지날 때까지 기다린다(대화를 주고받는 사이에 끼어들지 않게).
        for_restart: 다시 시작은 살아 있는 CLI를 끝낸다. CLI가 떠 있으면 IDLE_RESTART_SEC까지 미룬다(그 뒤 미리 띄우기로 복구)."""
        while True:
            need = IDLE_RESTART_SEC if for_restart and self.running_agents() else IDLE_SEC
            # 전하지 못한 답·턴 끝이 남아 있으면 다시 시작하지 않는다: outbox는 메모리에만 있어 함께 사라지고, 끝난 턴이 '중단'으로 남는다
            if self.busy() or (for_restart and len(self.outbox)):
                self._last_work = time.monotonic()
            elif time.monotonic() - self._last_work >= need:
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
                await self._wait_idle(for_restart=True)  # 설치하는 사이에 시작한 일과 살아 있는 CLI를 끊지 않는다
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
            if time.monotonic() - self._names_at >= NAME_RETRY_SEC:  # 한가할 때도 기다리던 이름을 다시 본다
                await asyncio.to_thread(self._settle_quietly)

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
        if t in NAME_MSGS:
            self._note_name(msg)
        if t == "agent.ack":
            self.outbox.ack(int(msg.get("seq") or 0))
        elif t == "project.renamed":  # 웹에서 또는 AI가 set_project_info로 바꿨다. 옛 Agent는 아래 else에서 로그만 남긴다
            self._note_name(msg)
            self._background_task(self._settle_names, msg.get("project_id"))
        elif t == "project.names":  # 연결할 때 온다. 꺼져 있는 동안 바뀐 이름을 따라간다
            for p in msg.get("projects") or []:
                self._note_name(p)
            self._background_task(self._settle_names)
        elif t == "session.new":
            asyncio.create_task(self._respond(rid, self._new_session, msg["project_id"], msg.get("agent"), msg.get("project_name"),
                                              msg.get("project_uid"), msg.get("turn_id")))
        elif t == "session.load":
            asyncio.create_task(self._respond(rid, self._load_session, msg["project_id"], msg["session_id"],
                                              msg.get("agent"), msg.get("project_name"), msg.get("project_uid"), msg.get("turn_id")))
        elif t == "session.prompt":
            self._start_turn(msg)
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
            asyncio.create_task(self._respond(rid, self._local_folder, msg["project_id"], msg.get("project_name"),
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
            self._background_task(self._cancel_session, msg["session_id"], msg.get("turn_id"))
        elif t == "session.force_restart":
            name = msg.get("agent") or ""
            self._begin_force(name)
            if rid is not None:  # 받았다고 바로 답한다. 답이 없으면 서버는 반쯤 끊긴 연결로 보고 다시 연결될 때 보낸다
                asyncio.create_task(self._respond(rid, lambda: {"ok": True}))
            self._background_task(self._force_restart, name)
        elif t == "permission.reply":
            self._background_task(self._permission_reply, str(msg["request_id"]), msg.get("option_id"))
        # case.extract: Output 추가 — 해석한 케이스에서 새 Output만 뽑는다 · case.train_sample: 학습 샘플을 만들어 올린다
        elif t in ("case.run", "case.extract", "case.train_sample"):
            self.executor.submit(msg)
        elif t == "case.settle":
            self.executor.settle(msg.get("cases") or [])
        elif t == "case.cancel":  # 창이 없으니 사용자가 웹앱에서 해석을 멈출 수 있는 유일한 길이다
            self.executor.cancel(msg.get("case_ids") or [], msg.get("extract_ids"))
        elif t in ("agent.release", "agent.update"):
            if self.auto_update or msg.get("force"):
                self.schedule_update(msg["version"], msg.get("sha256"), bool(msg.get("force")))
        elif t == "agent.settings":  # 연결할 때마다, 사용자가 설정을 바꿀 때마다 온다
            self.auto_update = bool(msg.get("auto_update", True))
            self.remote_test_until = float(msg.get("remote_test_until") or 0)
            self.server_acks = bool(msg.get("acks"))
            if not self.server_acks:
                self.outbox.ack(self._sent_seq)
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
        self._out_wake = asyncio.Event()
        self._start_tray()
        # 지금은 이 Agent가 띄운 CLI가 없다. 미리 띄우기가 폴더를 잡기 전에 기다리던 이름을 바꾼다
        await asyncio.to_thread(self._settle_quietly)
        asyncio.create_task(self._background())
        asyncio.create_task(self._watch_tools())
        while not (self.restart or self.quit):
            standby = False
            try:
                async with websockets.connect(f"{self.s.ws_url}?token={self.s.token}", max_size=16 * 1024 * 1024,
                                              ping_interval=20) as ws:
                    self.ws, delay = ws, 1
                    log.info("서버에 연결됨: %s", self.s.server_url)
                    if self.tray:
                        if self.tray.state != "connected":
                            self.tray.notify("웹앱에 연결되었습니다")
                        self.tray.set("connected", self.s.server_url)
                    await ws.send(json.dumps(self.hello(), ensure_ascii=False, default=str))
                    await self._send_heartbeat()  # 최신 버전 확인은 서버가 hello에 답하는 agent.settings를 받고 한다
                    for perm in list(self.open_perms.values()):  # 서버가 다시 시작했으면 답을 받을 카드가 사라졌다. 다시 띄운다(서버가 중복을 거른다)
                        self._reliable(perm)
                    pump = asyncio.create_task(self._pump(ws))
                    self._start_warm_up()
                    try:
                        async for raw in ws:
                            try:
                                await self.handle(json.loads(raw))
                            except Exception:  # 메시지 하나의 예외로 연결과 CLI 세션을 잃지 않는다
                                log.exception("서버 메시지 처리 실패: %s", raw[:300])
                    finally:
                        pump.cancel()
            except (OSError, websockets.WebSocketException) as e:
                code = getattr(getattr(e, "rcvd", None), "code", None)
                status = getattr(getattr(e, "response", None), "status_code", None)
                standby = code == REPLACED_CODE
                if code == 4401 or status in (401, 403):
                    log.error("토큰이 유효하지 않습니다. 새 토큰을 발급하세요.")
                    if self.tray:
                        self.tray.set("error", "토큰이 유효하지 않음 — 다시 설치하세요")
                        self.tray.notify("토큰이 유효하지 않습니다. 웹앱 설정에서 새 토큰으로 다시 설치하세요.")
                    while not self.quit:  # 트레이에서 상태를 볼 수 있게 프로세스는 유지
                        await asyncio.sleep(5)
                    return
                log.warning("서버 연결 끊김(%s). %d초 후 재시도", e, delay)
            except Exception:  # 무엇이 나든 Agent가 끝나면 CLI 세션이 모두 죽는다. 다시 연결을 시도한다
                log.exception("연결 처리 중 예상 못 한 오류. %d초 후 재시도", delay)
            finally:
                self.ws = None
            if self.restart or self.quit:
                break
            if standby:
                await self._standby()
                continue
            if self.tray:
                self.tray.set("connecting", "서버에 다시 연결하는 중")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def _standby(self):
        """같은 계정의 다른 Agent가 연결을 가져갔다. 곧바로 다시 붙으면 서로 밀어내기를 반복하므로, 그쪽이 끊길 때까지 기다린다."""
        log.warning("다른 PC의 Agent가 연결되어 이 PC는 대기합니다")
        if self.tray:
            self.tray.set("connecting", "다른 PC의 Agent가 연결되어 대기 중")
        while not (self.restart or self.quit):
            await asyncio.sleep(STANDBY_POLL_SEC)
            try:
                if not (await asyncio.to_thread(self.api.get, "/api/agent/status")).get("connected"):
                    return
            except Exception as e:  # 서버가 안 되면 연결 시도 쪽이 다시 기다린다
                log.debug("연결 상태 확인 실패: %s", e)
                return

    def shutdown(self):
        self.closing = True  # 스스로 끝내는 것이다. CLI가 죽었다고 알리지 않는다
        for r in self.runners.values():
            r.shutdown()
        self._settle_quietly()  # 끝나기 전에 기다리던 이름을 바꾼다. 아직 잠겨 있으면 다음 시작 때 한다
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
