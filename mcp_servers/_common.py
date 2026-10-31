"""MCP 서버 공통: 사용자 PC에서 Gemini CLI가 stdio로 띄운다. 서버 REST는 사용자 API 토큰으로 호출한다."""
import functools
import inspect
import os
import sys
from pathlib import Path

import httpx
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.dds_agent.config import apply_tools, load_settings  # noqa: E402
from agent.dds_agent.server_api import ServerApi, ServerError  # noqa: E402

settings = load_settings()
api = ServerApi(settings)
settings.project_lookup = lambda pid: api.get(f"/api/projects/{pid}")


def failure_text(e: Exception) -> str:
    if isinstance(e, httpx.TimeoutException):
        return "서버 응답이 제한 시간 안에 오지 않았습니다. 서버는 일을 계속하고 있을 수 있습니다 → 결과를 조회해 확인한 뒤 필요하면 다시 하세요"
    if isinstance(e, httpx.TransportError):
        return f"서버에 연결하지 못했습니다({e.__class__.__name__}) → 네트워크와 서버 상태를 확인한 뒤 다시 하세요"
    return str(e) or repr(e)


class DdsMCP(MCPServer):
    """mcp 2.x는 ToolError가 아닌 예외를 사고로 보고 문구를 지운다. AI에는 "Error executing tool X"만 가서
    서버가 적어 준 "이유 → 할 일"을 사용자에게 전할 수 없다. 그래서 도구의 모든 실패를 ToolError로 바꿔 문구를 살린다.
    직접 부르면 원래 함수이므로 원래 예외가 난다."""

    def tool(self, *args, **kwargs):
        register = super().tool(*args, **kwargs)

        def decorate(fn):
            if inspect.iscoroutinefunction(fn):
                return register(fn)

            @functools.wraps(fn)
            def guarded(*a, **kw):
                try:
                    return fn(*a, **kw)
                except (ToolError, MCPError):
                    raise
                except Exception as e:  # noqa: BLE001 — 받는 쪽은 이 PC의 AI 도구다. 무엇이든 이유를 보여 주는 편이 낫다
                    raise ToolError(failure_text(e)) from e

            register(guarded)
            return fn

        return decorate


def fresh_tools():
    """PC Agent가 실행 중에 새로 찾거나 사용자가 고른 툴 경로(tools.json)를 다시 읽는다 — 긴 대화 중에 툴을 설치해도 따라가게."""
    apply_tools(settings)


def resolve_pid(explicit: int | None = None) -> int:
    """우선순위: 인자 → 세션 환경변수(DDS_PROJECT_ID) → 서버의 최근 대화 프로젝트."""
    if explicit:
        return int(explicit)
    if os.environ.get("DDS_PROJECT_ID"):
        return int(os.environ["DDS_PROJECT_ID"])
    return api.resolve_project(None)


def tool_wait_limit() -> int:
    """도구 한 번이 기다려도 되는 시간(초). 0이면 제한 없음.
    CLI의 MCP 도구 시간 제한을 바꿀 수 없는 경우(Gemini 10분, Codex 60초) PC Agent가 정해 준다.
    이 안에 끝나지 않은 일은 실패로 보이지 않게 "아직 진행 중"으로 돌려주고 AI가 다시 확인한다."""
    try:
        return max(0, int(os.environ.get("DDS_MCP_WAIT_LIMIT_SEC") or 0))
    except ValueError:
        return 0


__all__ = ["api", "settings", "resolve_pid", "fresh_tools", "tool_wait_limit", "ServerError", "ROOT", "DdsMCP"]
