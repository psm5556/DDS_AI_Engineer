"""ACP(Agent Client Protocol) 러너: `gemini --acp` 를 상주 프로세스로 띄우고 JSON-RPC(줄 단위, stdio)로 통신한다.
이벤트 루프 종류와 무관하게 동작하도록 subprocess + 리더 스레드로 구현했다.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import subprocess
import threading
from concurrent.futures import Future
from typing import Any

from .runner import AgentError, AgentRunner

log = logging.getLogger(__name__)
PROTOCOL_VERSION = 1


class AcpRunner(AgentRunner):
    def __init__(self, command: list[str], on_event, on_permission, env: dict | None = None):
        super().__init__(on_event, on_permission)
        self.command = command
        self.env = env
        self.proc: subprocess.Popen | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, Future] = {}
        self._perm: dict[str, int | str] = {}  # request_id → JSON-RPC id
        self._write_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self.agent_info: dict[str, Any] = {}
        self.stderr_tail: list[str] = []

    # ---------- 프로세스 ----------
    def _ensure_started(self):
        with self._start_lock:
            if self.proc and self.proc.poll() is None:
                return
            log.info("에이전트 시작: %s", self.command)
            try:
                self.proc = subprocess.Popen(
                    self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    env=self.env, bufsize=1, text=True, encoding="utf-8", errors="replace",
                )
            except FileNotFoundError as e:
                raise AgentError(f"에이전트 실행 파일을 찾을 수 없습니다: {self.command[0]} ({e})")
            threading.Thread(target=self._read_stdout, daemon=True).start()
            threading.Thread(target=self._read_stderr, daemon=True).start()
            self.agent_info = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
            }, timeout=60)

    def shutdown(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    # ---------- JSON-RPC ----------
    def _send(self, msg: dict):
        if not self.proc or self.proc.poll() is not None:
            raise AgentError("에이전트 프로세스가 종료되었습니다:\n" + "\n".join(self.stderr_tail[-20:]))
        line = json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False)
        with self._write_lock:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()

    def _request(self, method: str, params: dict, timeout: float | None = None) -> Any:
        rid = next(self._ids)
        fut: Future = Future()
        self._pending[rid] = fut
        self._send({"id": rid, "method": method, "params": params})
        try:
            return fut.result(timeout=timeout)
        finally:
            self._pending.pop(rid, None)

    def _notify(self, method: str, params: dict):
        self._send({"method": method, "params": params})

    def _read_stderr(self):
        for line in self.proc.stderr:
            self.stderr_tail = (self.stderr_tail + [line.rstrip()])[-200:]
            log.debug("[agent stderr] %s", line.rstrip())

    def _read_stdout(self):
        proc = self.proc
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.debug("[agent stdout] %s", line)
                continue
            try:
                self._dispatch(msg)
            except Exception:
                log.exception("ACP 메시지 처리 실패: %s", line[:500])
        err = AgentError("에이전트 프로세스가 종료되었습니다:\n" + "\n".join(self.stderr_tail[-20:]))
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(err)

    def _dispatch(self, msg: dict):
        if "method" not in msg:  # 응답
            fut = self._pending.get(msg.get("id"))
            if fut and not fut.done():
                if "error" in msg:
                    e = msg["error"]
                    fut.set_exception(AgentError(f"{e.get('message')} ({e.get('code')}) {e.get('data') or ''}".strip()))
                else:
                    fut.set_result(msg.get("result"))
            return

        method, params = msg["method"], msg.get("params") or {}
        if method == "session/update":
            self.on_event(params.get("sessionId"), params.get("update") or {})
        elif method == "session/request_permission":
            request_id = str(msg["id"])
            self._perm[request_id] = msg["id"]
            self.on_permission(params.get("sessionId"), request_id, params)
        elif "id" in msg:  # fs/terminal 등 미지원 요청
            self._send({"id": msg["id"], "error": {"code": -32601, "message": f"클라이언트 미지원: {method}"}})

    # ---------- AgentRunner ----------
    def new_session(self, cwd: str, mcp_servers: list[dict] | None = None) -> str:
        self._ensure_started()
        try:
            res = self._request("session/new", {"cwd": os.path.abspath(cwd), "mcpServers": mcp_servers or []}, timeout=120)
        except AgentError as e:
            if "auth" in str(e).lower():
                methods = [m.get("id") for m in self.agent_info.get("authMethods", [])]
                raise AgentError(f"Gemini CLI 인증이 필요합니다. 터미널에서 `gemini`를 실행해 로그인하세요. (인증 방식: {methods})")
            raise
        return res["sessionId"]

    def load_session(self, session_id: str, cwd: str, mcp_servers: list[dict] | None = None) -> bool:
        self._ensure_started()
        if not (self.agent_info.get("agentCapabilities") or {}).get("loadSession"):
            return False
        try:
            self._request("session/load", {"sessionId": session_id, "cwd": os.path.abspath(cwd), "mcpServers": mcp_servers or []},
                          timeout=120)
            return True
        except AgentError:
            return False

    def prompt(self, session_id: str, text: str) -> str:
        self._ensure_started()
        res = self._request("session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]})
        return (res or {}).get("stopReason", "end_turn")

    def cancel(self, session_id: str) -> None:
        self._notify("session/cancel", {"sessionId": session_id})
        for request_id in list(self._perm):
            self.respond_permission(request_id, None)

    def respond_permission(self, request_id: str, option_id: str | None) -> None:
        rpc_id = self._perm.pop(request_id, None)
        if rpc_id is None:
            raise AgentError("이미 처리되었거나 존재하지 않는 권한 요청입니다")
        outcome = {"outcome": "selected", "optionId": option_id} if option_id else {"outcome": "cancelled"}
        self._send({"id": rpc_id, "result": {"outcome": outcome}})
