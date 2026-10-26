"""서버 ⇄ 사용자 PC Agent 연결(WebSocket /ws/agent).

서버 → Agent 요청 (응답 필요: {"type":"response","rid",...,"result"|"error"})
  session.new     {project_id, agent}                  → {"session_id", "agent"}
  session.load    {project_id, session_id, agent}      → {"ok": bool}
  agent.install   {agent}                              → {"installed", "login"}  (npm 전역 설치)
  local.open      {project_id, path?, with_viewer?}    → {"ok"}  (내 PC 탐색기/CAE 뷰어로 열기)
  local.tree      {project_id, path?}                  → {"entries": [...]} (로컬 작업 폴더 탐색)
  session.prompt  {session_id, text}                   → {"stop_reason"}   (턴이 끝날 때 응답)
서버 → Agent 알림
  session.cancel  {session_id}
  permission.reply {request_id, option_id|null}
  case.run        {case_id, study_id, project_id, params, adapter, adapter_config, dry_run}
Agent → 서버 알림
  hello           {hostname, version, agents:[{name,label,installed,login}], default_agent, tools}
  agent.event     {session_id, update}                 (ACP session/update)
  agent.permission {session_id, request_id, params}    (ACP session/request_permission)
  case.status     {case_id, status, results?, error?, run_dir?, images?}
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
from concurrent.futures import Future
from typing import Any, Callable

from fastapi import WebSocket

log = logging.getLogger(__name__)


class AgentError(Exception):
    pass


class AgentConnection:
    def __init__(self, ws: WebSocket, user_id: int, loop: asyncio.AbstractEventLoop):
        self.ws, self.user_id, self.loop = ws, user_id, loop
        self.info: dict[str, Any] = {}
        self._ids = itertools.count(1)
        self._pending: dict[int, Future] = {}

    # 어느 스레드에서든 호출 가능
    def send(self, msg: dict):
        text = json.dumps(msg, ensure_ascii=False, default=str)
        fut = asyncio.run_coroutine_threadsafe(self.ws.send_text(text), self.loop)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            fut.result(timeout=30)  # 동기 스레드에서는 전송 완료까지 대기

    def request(self, type_: str, payload: dict, timeout: float | None = 120) -> Any:
        rid = next(self._ids)
        fut: Future = Future()
        self._pending[rid] = fut
        self.send({"type": type_, "rid": rid, **payload})
        try:
            return fut.result(timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    def resolve(self, msg: dict):
        fut = self._pending.get(msg.get("rid"))
        if fut and not fut.done():
            if msg.get("error"):
                fut.set_exception(AgentError(msg["error"]))
            else:
                fut.set_result(msg.get("result"))

    def close(self):
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(AgentError("PC Agent 연결이 끊어졌습니다"))


class RemoteRunner:
    """서버 쪽에서 본 에이전트 러너. 실제 Gemini CLI는 사용자 PC Agent가 구동한다."""

    def __init__(self, conn: AgentConnection):
        self.conn = conn

    def new_session(self, project_id: int, agent: str, project_name: str | None = None) -> str:
        return self.conn.request("session.new", {"project_id": project_id, "agent": agent, "project_name": project_name},
                                 timeout=180)["session_id"]

    def load_session(self, session_id: str, project_id: int, agent: str, project_name: str | None = None) -> bool:
        try:
            return bool(self.conn.request("session.load", {"project_id": project_id, "session_id": session_id,
                                                           "agent": agent, "project_name": project_name}, timeout=180).get("ok"))
        except AgentError:
            return False

    def prompt(self, session_id: str, text: str, attachments: list[dict] | None = None) -> str:
        return self.conn.request("session.prompt", {"session_id": session_id, "text": text, "attachments": attachments or []},
                                 timeout=None)["stop_reason"]

    def call(self, type_: str, payload: dict, timeout: float = 30) -> dict:
        """PC Agent에 로컬 작업 요청(폴더 열기·뷰어 실행·로컬 데이터 조회 등)."""
        return self.conn.request(type_, payload, timeout=timeout)

    def cancel(self, session_id: str):
        self.conn.send({"type": "session.cancel", "session_id": session_id})

    def respond_permission(self, request_id: str, option_id: str | None):
        self.conn.send({"type": "permission.reply", "request_id": request_id, "option_id": option_id})

    def install(self, agent: str) -> dict:
        return self.conn.request("agent.install", {"agent": agent}, timeout=960)


class AgentRegistry:
    def __init__(self):
        self.conns: dict[int, AgentConnection] = {}  # user_id → 연결 (사용자당 1개, 최근 연결 우선)
        self.handlers: dict[str, Callable[[AgentConnection, dict], None]] = {}

    def on(self, type_: str, fn: Callable[[AgentConnection, dict], None]):
        self.handlers[type_] = fn

    def add(self, conn: AgentConnection):
        old = self.conns.get(conn.user_id)
        if old:
            old.close()
        self.conns[conn.user_id] = conn

    def remove(self, conn: AgentConnection):
        if self.conns.get(conn.user_id) is conn:
            del self.conns[conn.user_id]
        conn.close()

    def get(self, user_id: int) -> AgentConnection | None:
        return self.conns.get(user_id)

    def runner(self, user_id: int) -> RemoteRunner:
        conn = self.get(user_id)
        if not conn:
            raise AgentError("PC Agent가 연결되어 있지 않습니다. 내 PC에서 dds-agent를 실행하세요.")
        return RemoteRunner(conn)

    def dispatch(self, conn: AgentConnection, msg: dict):
        t = msg.get("type")
        if t == "response":
            conn.resolve(msg)
        elif t == "hello":
            conn.info = msg
            if "hello" in self.handlers:
                self.handlers["hello"](conn, msg)
        elif t in self.handlers:
            self.handlers[t](conn, msg)
        else:
            log.warning("알 수 없는 Agent 메시지: %s", t)


registry = AgentRegistry()
