"""헤드리스 러너(대안): 프롬프트마다 `gemini -p <text> --output-format stream-json` 을 실행한다.
ACP를 쓸 수 없는 CLI 버전용. 권한 요청 UI가 없으므로 approval-mode 설정(config.headless_extra_args)에 따른다.
세션 연속성은 `--resume latest`로 유지하며, stream-json 이벤트를 ACP 형식으로 변환한다.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import uuid

from .runner import AgentError, AgentRunner

log = logging.getLogger(__name__)


class HeadlessRunner(AgentRunner):
    def __init__(self, command: list[str], on_event, on_permission, extra_args: list[str], env: dict | None = None):
        super().__init__(on_event, on_permission)
        self.command, self.extra_args, self.env = command, extra_args, env
        self._cwd: dict[str, str] = {}
        self._turns: dict[str, int] = {}
        self._procs: dict[str, subprocess.Popen] = {}

    def new_session(self, cwd: str, mcp_servers=None) -> str:
        sid = f"headless-{uuid.uuid4().hex[:8]}"
        self._cwd[sid], self._turns[sid] = cwd, 0
        return sid

    def load_session(self, session_id: str, cwd: str, mcp_servers=None) -> bool:
        self._cwd[session_id] = cwd
        self._turns.setdefault(session_id, 1)
        return True

    def prompt(self, session_id: str, text: str) -> str:
        args = [*self.command, "-p", text, "--output-format", "stream-json", *self.extra_args]
        if self._turns.get(session_id):
            args += ["--resume", "latest"]
        try:
            proc = subprocess.Popen(args, cwd=self._cwd.get(session_id), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace", env=self.env)
        except FileNotFoundError as e:
            raise AgentError(f"gemini 실행 파일을 찾을 수 없습니다: {e}")
        self._procs[session_id] = proc
        stderr: list[str] = []
        threading.Thread(target=lambda: stderr.extend(proc.stderr), daemon=True).start()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._translate(session_id, ev)
        code = proc.wait()
        self._procs.pop(session_id, None)
        self._turns[session_id] = self._turns.get(session_id, 0) + 1
        if code not in (0, None) and code > 0:
            raise AgentError(f"gemini 종료코드 {code}:\n{''.join(stderr)[-2000:]}")
        return "end_turn" if code == 0 else "cancelled"

    def _translate(self, sid: str, ev: dict):
        # stream-json 스키마는 CLI 버전에 따라 다를 수 있다 — TODO(사내검증): 사내 버전 출력으로 키 확인
        t = ev.get("type")
        if t == "message" and ev.get("role") == "assistant":
            self.on_event(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": ev.get("content", "")}})
        elif t == "tool_use":
            self.on_event(sid, {"sessionUpdate": "tool_call", "toolCallId": ev.get("tool_id") or ev.get("id"),
                                "title": ev.get("tool_name"), "kind": "other", "status": "in_progress",
                                "rawInput": ev.get("parameters")})
        elif t == "tool_result":
            self.on_event(sid, {"sessionUpdate": "tool_call_update", "toolCallId": ev.get("tool_id") or ev.get("id"),
                                "status": "completed" if ev.get("status") != "error" else "failed",
                                "content": [{"type": "content", "content": {"type": "text", "text": str(ev.get("output", ""))[:4000]}}]})
        elif t == "error":
            self.on_event(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": f"\n[오류] {ev.get('message')}\n"}})

    def cancel(self, session_id: str) -> None:
        proc = self._procs.get(session_id)
        if proc and proc.poll() is None:
            proc.terminate()
