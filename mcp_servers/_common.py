"""MCP 서버 공통: 사용자 PC에서 Gemini CLI가 stdio로 띄운다. 서버 REST는 사용자 API 토큰으로 호출한다."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.dds_agent.config import apply_tools, load_settings  # noqa: E402
from agent.dds_agent.server_api import ServerApi, ServerError  # noqa: E402

settings = load_settings()
api = ServerApi(settings)


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


__all__ = ["api", "settings", "resolve_pid", "fresh_tools", "tool_wait_limit", "ServerError", "ROOT"]
