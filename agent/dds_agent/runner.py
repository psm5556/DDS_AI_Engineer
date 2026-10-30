"""에이전트 러너 인터페이스.
러너는 Gemini CLI(또는 데모 에이전트)와 통신하고, 이벤트를 ACP session/update 형식으로 정규화해 on_event로 넘긴다.
  {"sessionUpdate": "agent_message_chunk" | "agent_thought_chunk", "content": {"type": "text", "text": ...}}
  {"sessionUpdate": "tool_call", "toolCallId", "title", "kind", "status", "rawInput"}
  {"sessionUpdate": "tool_call_update", "toolCallId", "status", "content"}
  {"sessionUpdate": "plan", "entries": [...]}
권한 요청은 on_permission(session_id, request_id, payload)로 넘기고, respond_permission()으로 답한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

EventCallback = Callable[[str, dict[str, Any]], None]
PermissionCallback = Callable[[str, str, dict[str, Any]], None]


class AgentError(Exception):
    pass


class AgentRunner(ABC):
    def __init__(self, on_event: EventCallback, on_permission: PermissionCallback):
        self.on_event = on_event
        self.on_permission = on_permission

    @abstractmethod
    def new_session(self, cwd: str, mcp_servers: list[dict] | None = None) -> str: ...

    @abstractmethod
    def prompt(self, session_id: str, text: str) -> str:
        """턴이 끝날 때까지 블로킹. stopReason을 반환."""

    @abstractmethod
    def cancel(self, session_id: str) -> None: ...

    def respond_permission(self, request_id: str, option_id: str | None) -> None:
        raise AgentError("이 러너는 권한 요청을 지원하지 않습니다")

    def load_session(self, session_id: str, cwd: str, mcp_servers: list[dict] | None = None) -> bool:
        """기존 세션 재개. 지원하지 않으면 False."""
        return False

    def working_in_background(self) -> bool:
        """턴과 상관없이 이 러너 아래에서 계산이 돌고 있는지(자동 업데이트가 끼어들지 않게)."""
        return False

    def shutdown(self) -> None:
        pass
