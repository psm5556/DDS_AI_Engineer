"""MCP 서버 공통: 사용자 PC에서 Gemini CLI가 stdio로 띄운다. 서버 REST는 사용자 API 토큰으로 호출한다."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.dds_agent.config import load_settings  # noqa: E402
from agent.dds_agent.server_api import ServerApi, ServerError  # noqa: E402

settings = load_settings()
api = ServerApi(settings)


def resolve_pid(explicit: int | None = None) -> int:
    """우선순위: 인자 → 세션 환경변수(DDS_PROJECT_ID) → 서버의 최근 대화 프로젝트."""
    if explicit:
        return int(explicit)
    if os.environ.get("DDS_PROJECT_ID"):
        return int(os.environ["DDS_PROJECT_ID"])
    return api.resolve_project(None)


__all__ = ["api", "settings", "resolve_pid", "ServerError", "ROOT"]
