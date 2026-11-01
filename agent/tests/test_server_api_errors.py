"""서버가 JSON이 아닌 답을 줄 때 MCP·PC Agent가 하는 말. 'Expecting value: line 1 column 1 (char 0)'이 그대로 AI와 사용자에게 갔다.
옛 서버는 없는 API에 화면(index.html)을 200으로 준다. PC Agent(MCP)가 서버보다 새 버전이면 그렇게 된다."""
from pathlib import Path

import httpx
import pytest

from agent.dds_agent.server_api import ServerApi, ServerError

ROOT = Path(__file__).resolve().parents[2]


def _api(handler) -> ServerApi:
    api = ServerApi.__new__(ServerApi)
    api.s = type("S", (), {"server_url": "http://서버:7014"})()
    api.client = httpx.Client(base_url="http://server", transport=httpx.MockTransport(handler))
    return api


def test_html_answer_says_why_and_what_to_do():
    api = _api(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, text="<!doctype html>"))
    with pytest.raises(ServerError) as e:
        api.get("/api/projects/1/flows/summary")
    text = str(e.value)
    assert "Expecting value" not in text
    assert "/api/projects/1/flows/summary" in text and "text/html" in text and "→" in text and "최신 코드" in text


def test_json_404_passes_the_servers_words_through():
    api = _api(lambda r: httpx.Response(404, json={"detail": "서버에 없는 API입니다 → 서버를 최신 코드로 다시 시작하세요."}))
    with pytest.raises(ServerError) as e:
        api.get("/api/없는주소")
    assert "서버에 없는 API입니다" in str(e.value)


def test_normal_json_still_works():
    assert _api(lambda r: httpx.Response(200, json=[1, 2])).get("/api/x") == [1, 2]


def test_mcp_tool_keeps_the_reason_for_the_ai():
    """DdsMCP가 ToolError로 바꿔 문구를 살린다. 안 그러면 AI에는 'Error executing tool'만 간다."""
    src = (ROOT / "mcp_servers" / "_common.py").read_text(encoding="utf-8")
    assert "raise ToolError(failure_text(e))" in src


def test_flow_decision_is_not_sent_from_the_browser():
    """승인 말을 화면이 보내면 답하는 중에 409로 사라진다(흐름 #25). 서버가 보낸다."""
    src = (ROOT / "frontend" / "src" / "canvas" / "views.tsx").read_text(encoding="utf-8")
    start = src.index("export function useFlowDecision")
    body = src[start:src.index("export function FlowView")]
    assert "sendToAgent" not in body and "session_id" in body
