"""에이전트 러너 인터페이스.
러너는 AI CLI(또는 데모 에이전트)와 통신하고, 이벤트를 ACP session/update 형식으로 정규화해 on_event로 넘긴다.
  {"sessionUpdate": "agent_message_chunk" | "agent_thought_chunk", "content": {"type": "text", "text": ...}}
  {"sessionUpdate": "tool_call", "toolCallId", "title", "kind", "status", "rawInput"}
  {"sessionUpdate": "tool_call_update", "toolCallId", "status", "content"}
  {"sessionUpdate": "plan", "entries": [...]}
권한 요청은 on_permission(session_id, request_id, payload)로 넘기고, respond_permission()으로 답한다.
report(stage)는 시작 단계(cli_start·initialize·session_new·session_load)를 알리는 콜백이다. 기다리는 사용자에게 보여 준다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

EventCallback = Callable[[str, dict[str, Any]], None]
PermissionCallback = Callable[[str, str, dict[str, Any]], None]
StageReport = Callable[[str], None]


class AgentError(Exception):
    pass


class SessionGone(AgentError):
    """CLI가 이 세션을 모른다(재시작 등). 서버가 session/load부터 다시 하게 구분한다."""


class LoginRequired(AgentError):
    """느린 것이 아니라 로그인이 안 된 것이다. 기다려도 소용없음을 바로 알린다."""


class CliDied(AgentError):
    """CLI 프로세스가 끝났거나 보낼 수 없다. 다음 호출 때 다시 띄우면 되므로 세션을 버릴 이유가 아니다."""


class AgentRunner(ABC):
    def __init__(self, on_event: EventCallback, on_permission: PermissionCallback):
        self.on_event = on_event
        self.on_permission = on_permission
        self.on_exit: Callable[[int | None, str, set[str]], None] | None = None  # (종료 코드, stderr 끝부분, 그 프로세스의 세션)
        self.on_timing: Callable[[str, float], None] | None = None  # ("start"|"session", 초)

    @abstractmethod
    def new_session(self, cwd: str, mcp_servers: list[dict] | None = None, report: StageReport | None = None) -> str: ...

    @abstractmethod
    def prompt(self, session_id: str, text: str) -> str:
        """턴이 끝날 때까지 블로킹. stopReason을 반환."""

    @abstractmethod
    def cancel(self, session_id: str) -> None: ...

    def respond_permission(self, request_id: str, option_id: str | None) -> None:
        raise AgentError("이 러너는 권한 요청을 지원하지 않습니다")

    def load_session(self, session_id: str, cwd: str, mcp_servers: list[dict] | None = None, report: StageReport | None = None) -> bool:
        """기존 세션 재개. 지원하지 않거나 세션이 없으면 False. 그 밖의 실패는 AgentError(세션을 새로 만들지 않게)."""
        return False

    def live_sessions(self) -> set[str]:
        """지금 프로세스 안에 살아 있는 세션. session/load 없이 그대로 쓸 수 있다."""
        return set()

    def alive(self) -> bool:
        """CLI 프로세스가 떠 있는지."""
        return False

    def warm_up(self, report: StageReport | None = None) -> None:
        """CLI를 미리 띄워 첫 요청의 대기를 없앤다. 프롬프트마다 뜨는 러너는 할 일이 없다."""

    def working_in_background(self) -> bool:
        """턴과 상관없이 이 러너 아래에서 계산이 돌고 있는지(자동 업데이트가 끼어들지 않게)."""
        return False

    def shutdown(self) -> None:
        pass
