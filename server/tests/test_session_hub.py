"""서버 쪽 세션 유지: /ws/agent를 실제로 열고 시험이 PC Agent 역할을 한다. 실제 CLI는 어디에도 뜨지 않는다."""
import threading
import time

import pytest

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402

from server.app import models  # noqa: E402
from server.app.agent import link  # noqa: E402
from server.app.agent.hub import hub  # noqa: E402
from server.app.agent.link import AgentConnection, AgentError, LinkLost, registry  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


_n = [0]


@pytest.fixture
def me(client):
    """시험마다 새 사용자: hub와 registry는 프로세스 전체에서 하나다."""
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    headers, user = _auth(client, f"pc{_n[0]}", admin)
    token = client.post("/api/tokens", json={"name": "pc"}, headers=headers).json()["token"]
    pid = client.post("/api/projects", json={"name": f"세션{_n[0]}"}, headers=headers).json()["id"]
    return {"h": headers, "id": user["id"], "token": token, "pid": pid}


def _chat(client, me, agent="fake"):
    return client.post(f"/api/projects/{me['pid']}/sessions", json={"agent": agent}, headers=me["h"]).json()["id"]


def _status(client, me, chat):
    return client.get(f"/api/sessions/{chat}/status", headers=me["h"]).json()


def _msgs(client, me, chat):
    return client.get(f"/api/sessions/{chat}/messages", headers=me["h"]).json()


def _ws(client, me):
    return client.websocket_connect(f"/ws/agent?token={me['token']}")


def _hello(ws, boot="boot1", live=(), turns=(), running=("fake",)):
    ws.send_json({"type": "hello", "hostname": "PC", "version": "9", "boot": boot, "live_sessions": list(live), "active_turns": list(turns),
                  "running_agents": list(running), "start_times": {"fake": 192.0}, "default_agent": "fake",
                  "agents": [{"name": "fake", "label": "가짜", "installed": True}]})
    # 서버가 상태를 맞춘 뒤에 agent.settings를 보낸다. 그사이 온 메시지(다시 묻는 session.new 등)는 시험이 읽게 남겨 둔다
    early = _early[id(ws)] = []
    while True:
        m = ws.receive_json()
        if m.get("type") == "agent.settings":
            return
        early.append(m)


_early: dict[int, list] = {}


def _until(ws, *types):
    """그 종류의 메시지가 올 때까지 읽는다(ack 등은 건너뛴다)."""
    early = _early.get(id(ws), [])
    while early:
        m = early.pop(0)
        if m.get("type") in types:
            return m
    while True:
        m = ws.receive_json()
        if m.get("type") in types:
            return m


def _wait(cond, timeout=10.0, what=""):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.02)
    raise TimeoutError(what)


def _end(ws, turn_id, chat, sid, seq, stop="end_turn", **kw):
    ws.send_json({"type": "agent.turn_end", "turn_id": turn_id, "chat_id": chat, "session_id": sid, "stop_reason": stop,
                  "seq": seq, "boot": "boot1", **kw})


def _start_turn(client, me, ws, chat, text="요청", sid="sid-1", seq=1):
    """새 세션을 열고 턴을 보내고, 프롬프트를 받을 때까지 진행한다."""
    assert client.post(f"/api/sessions/{chat}/prompt", json={"text": text}, headers=me["h"]).status_code == 200
    m = _until(ws, "session.new")
    ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": sid, "agent": "fake"}})
    return _until(ws, "session.prompt")


def test_prompt_returns_before_cli_is_ready_and_status_has_stage(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws, running=())
        t0 = time.time()
        r = client.post(f"/api/sessions/{chat}/prompt", json={"text": "오래 걸릴 요청"}, headers=me["h"])
        assert r.status_code == 200 and r.json()["turn_id"] and time.time() - t0 < 2
        st = _status(client, me, chat)
        for key in ("busy", "stage", "stage_detail", "started_at", "elapsed_sec", "typical_start_sec", "link", "pending_permissions"):
            assert key in st
        assert st["busy"] and st["stage"] == "agent_starting" and st["link"] == "ok" and st["typical_start_sec"] == 192.0
        assert [m["content"] for m in _msgs(client, me, chat)] == ["오래 걸릴 요청"]  # CLI가 뜨기 전에 메시지가 남는다
        m = _until(ws, "session.new")
        assert m["turn_id"] == r.json()["turn_id"] and "agent" in m
        # Agent가 알리는 단계가 그대로 보인다
        ws.send_json({"type": "session.stage", "turn_id": m["turn_id"], "agent": "fake", "stage": "initialize", "typical_start_sec": 200.0, "seq": 1, "boot": "b"})
        _wait(lambda: _status(client, me, chat)["stage_detail"] == "initialize")
        ws.send_json({"type": "session.stage", "turn_id": m["turn_id"], "agent": "fake", "stage": "session_new", "seq": 2, "boot": "b"})
        st = _wait(lambda: (lambda s: s if s["stage"] == "session_new" else None)(_status(client, me, chat)))
        assert st["typical_start_sec"] == 200.0
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-a", "agent": "fake"}})
        p = _until(ws, "session.prompt")
        assert "rid" not in p and p["turn_id"] == m["turn_id"] and p["chat_id"] == chat  # 알림으로 간다
        assert "[DDS 컨텍스트]" in p["text"] and p["text"].endswith("오래 걸릴 요청")
        _wait(lambda: _status(client, me, chat)["stage"] == "thinking")
        _end(ws, p["turn_id"], chat, "sid-a", 3, boot="b")
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_duplicate_send_is_409_during_start(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        assert client.post(f"/api/sessions/{chat}/prompt", json={"text": "첫"}, headers=me["h"]).status_code == 200
        r = client.post(f"/api/sessions/{chat}/prompt", json={"text": "또"}, headers=me["h"])
        assert r.status_code == 409 and "처리 중" in r.json()["detail"]
        assert [m["content"] for m in _msgs(client, me, chat)] == ["첫"]
        m = _until(ws, "session.new")
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-dup", "agent": "fake"}})
        p = _until(ws, "session.prompt")
        _end(ws, p["turn_id"], chat, "sid-dup", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_prompt_without_agent_is_409_and_saves_nothing(client, me):
    chat = _chat(client, me)
    r = client.post(f"/api/sessions/{chat}/prompt", json={"text": "x"}, headers=me["h"])
    assert r.status_code == 409 and "연결되어 있지 않습니다" in r.json()["detail"]
    assert _msgs(client, me, chat) == []


def test_no_fallback_to_a_cli_the_user_did_not_pick(client, me):
    # Agent가 없거나 hello 전이면 정해진 값(gemini)으로 채우지 않는다: 실제로 잘못된 CLI가 뜬 적이 있다
    r = client.post(f"/api/projects/{me['pid']}/sessions", json={}, headers=me["h"])
    assert r.status_code == 409 and "에이전트" in r.json()["detail"]
    with _ws(client, me) as ws:
        r = client.post(f"/api/projects/{me['pid']}/sessions", headers=me["h"])  # 연결됐지만 hello 전
        assert r.status_code == 409
        _hello(ws)
        r = client.post(f"/api/projects/{me['pid']}/sessions", headers=me["h"])
        assert r.status_code == 200 and r.json()["agent"] == "fake"  # Agent의 기본값은 따른다


def test_stop_during_start_ends_at_once_and_sends_no_prompt(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "멈출 요청"}, headers=me["h"])
        m = _until(ws, "session.new")
        assert client.post(f"/api/sessions/{chat}/cancel", headers=me["h"]).status_code == 200
        assert not _status(client, me, chat)["busy"]
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-stop", "agent": "fake"}})
        # 세션은 끝까지 받아 두고(다음 요청이 빠르다) 프롬프트는 보내지 않는다
        _wait(lambda: SessionLocal().get(models.ChatSession, chat).agent_session_id == "sid-stop")
        time.sleep(0.3)
        assert client.post(f"/api/sessions/{chat}/prompt", json={"text": "다시"}, headers=me["h"]).status_code == 200
        p = _until(ws, "session.prompt", "session.new", "session.load")
        assert p["type"] == "session.prompt" and p["session_id"] == "sid-stop"  # 프롬프트는 이번 것 하나뿐이고 세션 호출은 없다
        assert "[DDS 컨텍스트]" in p["text"] and p["text"].endswith("다시")  # 첫 요청이 전달되지 않았으니 컨텍스트는 이번에 간다
        _end(ws, p["turn_id"], chat, "sid-stop", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_reconnect_keeps_turn_and_skips_load_for_live_session(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-live")
        ws.send_json({"type": "agent.event", "session_id": "sid-live", "seq": 1, "boot": "boot1",
                      "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "앞부분 "}}})
        _until(ws, "agent.ack")
    # 끊겼다: 턴을 버리지 않는다
    st = _wait(lambda: (lambda s: s if s["link"] == "lost" else None)(_status(client, me, chat)))
    assert st["busy"] and st["stage"] == "reconnecting"
    assert not [m for m in _msgs(client, me, chat) if m["role"] == "system"]
    with _ws(client, me) as ws:
        turn = {"turn_id": p["turn_id"], "chat_id": chat, "session_id": "sid-live", "agent": "fake", "started_at": time.time() - 5}
        _hello(ws, live=["sid-live"], turns=[turn])
        st = _wait(lambda: (lambda s: s if s["link"] == "ok" else None)(_status(client, me, chat)))
        assert st["busy"] and st["stage"] == "thinking"
        # 끊긴 동안의 출력이 이어진다. 이미 받은 seq는 두 번 쌓이지 않는다
        for seq, text in ((1, "앞부분 "), (2, "뒷부분")):
            ws.send_json({"type": "agent.event", "session_id": "sid-live", "seq": seq, "boot": "boot1",
                          "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}})
        _end(ws, p["turn_id"], chat, "sid-live", 3)
        _wait(lambda: not _status(client, me, chat)["busy"])
        assert [m["content"] for m in _msgs(client, me, chat) if m["role"] == "agent"] == ["앞부분 뒷부분"]
        # 다음 요청은 session/load 없이 바로 프롬프트로 간다
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "다음"}, headers=me["h"])
        n = _until(ws, "session.prompt", "session.load", "session.new")
        assert n["type"] == "session.prompt" and n["session_id"] == "sid-live" and n["text"] == "다음"
        _end(ws, n["turn_id"], chat, "sid-live", 4)
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_turn_missing_from_new_boot_ends_once(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        _start_turn(client, me, ws, chat, sid="sid-gone")
    with _ws(client, me) as ws:
        _hello(ws, boot="boot2", live=[])  # Agent가 다시 시작했다
        _wait(lambda: not _status(client, me, chat)["busy"])
        _hello(ws, boot="boot2", live=[])  # hello가 또 와도
        notes = [m["content"] for m in _msgs(client, me, chat) if m["role"] == "system"]
        assert len(notes) == 1 and "다시 시작" in notes[0]


def test_undelivered_prompt_is_resent_to_same_boot(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-resend")
    with _ws(client, me) as ws:
        _hello(ws, live=["sid-resend"], turns=[])  # 같은 Agent인데 턴을 모른다: 전달 전에 끊긴 것이다
        again = _until(ws, "session.prompt")
        assert again["turn_id"] == p["turn_id"] and again["text"] == p["text"]
        _end(ws, p["turn_id"], chat, "sid-resend", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])


def _reset_hub(user_id):
    """서버 재시작 흉내: 메모리 상태만 사라진다. 실제 프로세스 재시작은 agent/tests/test_sessions_stack.py"""
    for chat_id in [c for c, st in hub.chats.items() if st.user_id == user_id]:
        hub.chats.pop(chat_id)
    for sid in [s for s, st in hub.by_sid.items() if st.user_id == user_id]:
        hub.by_sid.pop(sid)
    hub.live.pop(user_id, None)
    hub.running.pop(user_id, None)
    hub._greeted.discard(user_id)


def test_server_restart_restores_turn_permission_and_tools(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-r")
        ws.send_json({"type": "agent.event", "session_id": "sid-r", "seq": 1, "boot": "boot1",
                      "update": {"sessionUpdate": "tool_call", "toolCallId": "tc1", "title": "도구", "status": "in_progress"}})
        _until(ws, "agent.ack")
    _reset_hub(me["id"])
    assert _status(client, me, chat)["busy"] is False
    with _ws(client, me) as ws:
        started = time.time() - 100
        _hello(ws, live=["sid-r"], turns=[{"turn_id": p["turn_id"], "chat_id": chat, "session_id": "sid-r", "agent": "fake", "started_at": started}])
        st = _status(client, me, chat)
        assert st["busy"] and st["elapsed_sec"] >= 99 and st["stage"] == "thinking"  # 경과 시간이 이어진다
        # 다시 온 권한 요청은 복원되고, 한 번 더 와도 카드가 하나다
        for _ in range(2):
            ws.send_json({"type": "agent.permission", "session_id": "sid-r", "request_id": "fake:9",
                          "params": {"toolCall": {"title": "실행"}, "options": [{"optionId": "ok", "name": "허용", "kind": "allow_once"}]}})
        _wait(lambda: _status(client, me, chat)["pending_permissions"])
        assert len(_status(client, me, chat)["pending_permissions"]) == 1
        assert client.post(f"/api/sessions/{chat}/permission", json={"request_id": "fake:9", "option_id": "ok"}, headers=me["h"]).status_code == 200
        assert _until(ws, "permission.reply")["option_id"] == "ok"
        # 재시작 전의 도구 메시지를 찾아 마무리한다
        ws.send_json({"type": "agent.event", "session_id": "sid-r", "seq": 2, "boot": "boot1",
                      "update": {"sessionUpdate": "tool_call_update", "toolCallId": "tc1", "status": "completed"}})
        _end(ws, p["turn_id"], chat, "sid-r", 3)
        _wait(lambda: not _status(client, me, chat)["busy"])
        tool = next(m for m in _msgs(client, me, chat) if m["role"] == "tool")
        assert tool["meta"]["status"] == "completed"


def test_server_restart_notes_request_that_never_reached_the_cli(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "시작 중 서버가 꺼짐"}, headers=me["h"])
        _until(ws, "session.new")
    _reset_hub(me["id"])
    with _ws(client, me) as ws:
        _hello(ws, live=[], turns=[])
        notes = [m["content"] for m in _msgs(client, me, chat) if m["role"] == "system"]
        assert len(notes) == 1 and "다시 보내" in notes[0]


def test_server_restart_does_not_note_turn_user_stopped(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "멈춘 요청"}, headers=me["h"])
        _until(ws, "session.new")
        client.post(f"/api/sessions/{chat}/cancel", headers=me["h"])
    _reset_hub(me["id"])
    with _ws(client, me) as ws:
        _hello(ws, live=[], turns=[])
        assert not [m for m in _msgs(client, me, chat) if m["role"] == "system"]


def test_events_from_another_users_agent_are_ignored(client, me):
    chat = _chat(client, me)
    admin, _ = _auth(client, "admin")
    other_token = client.post("/api/tokens", json={"name": "other"}, headers=admin).json()["token"]
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-mine")
        with client.websocket_connect(f"/ws/agent?token={other_token}") as other:
            _hello(other, boot="other")
            other.send_json({"type": "agent.event", "session_id": "sid-mine", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "끼어든 글"}}})
            other.send_json({"type": "agent.turn_end", "turn_id": p["turn_id"], "chat_id": chat, "session_id": "sid-mine", "stop_reason": "end_turn"})
            time.sleep(0.3)
            assert _status(client, me, chat)["busy"] and not [m for m in _msgs(client, me, chat) if m["role"] == "agent"]
        _end(ws, p["turn_id"], chat, "sid-mine", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_late_response_with_old_rid_does_not_complete_new_request(client, me):
    """rid는 서버 전체에서 유일하다. 옛 연결의 응답이 재연결 뒤 다른 요청을 완료시키지 않는다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "x"}, headers=me["h"])
        first = _until(ws, "session.new")
    with _ws(client, me) as ws:
        _hello(ws)
        second = _until(ws, "session.new")  # 끊겨서 같은 턴으로 다시 묻는다(Agent가 세션을 또 만들지 않는다)
        assert second["rid"] != first["rid"] and second["turn_id"] == first["turn_id"]
        ws.send_json({"type": "response", "rid": first["rid"], "result": {"session_id": "옛세션", "agent": "fake"}})
        time.sleep(0.2)
        assert SessionLocal().get(models.ChatSession, chat).agent_session_id is None
        ws.send_json({"type": "response", "rid": second["rid"], "result": {"session_id": "새세션", "agent": "fake"}})
        p = _until(ws, "session.prompt")
        assert p["session_id"] == "새세션"
        _end(ws, p["turn_id"], chat, "새세션", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_rids_are_unique_across_connections():
    first = next(link._rids)
    assert next(link._rids) > first


def test_stale_connection_finally_leaves_new_connection_alone(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as old:
        _hello(old)
        with _ws(client, me) as new:  # 새 연결이 들어오면 옛 소켓을 닫는다
            _hello(new)
            closed = old.receive()
            assert closed["type"] == "websocket.close" and closed["code"] == link.REPLACED_CODE  # Agent가 곧바로 다시 붙지 않고 기다리는 신호
            p = _start_turn(client, me, new, chat, sid="sid-new")
            time.sleep(0.3)
            # 옛 연결의 정리가 끝난 뒤에도 새 연결·턴·연결 상태가 그대로다
            st = _status(client, me, chat)
            assert st["busy"] and st["link"] == "ok"
            assert client.get("/api/agent/status", headers=me["h"]).json()["connected"] is True
            with SessionLocal() as db:
                inst = db.query(models.AgentInstance).filter_by(user_id=me["id"]).first()
                assert inst.connected is True
            assert not [m for m in _msgs(client, me, chat) if m["role"] == "system"]
            _end(new, p["turn_id"], chat, "sid-new", 1)
            _wait(lambda: not _status(client, me, chat)["busy"])


def test_handler_exception_does_not_drop_the_link(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        registry.on("boom.test", lambda conn, m: (_ for _ in ()).throw(RuntimeError("처리 실패")))
        ws.send_json({"type": "boom.test"})
        ws.send_text("이건 JSON이 아니다")
        ws.send_json({"type": "agent.permission", "session_id": None, "request_id": "x"})  # 모르는 세션
        p = _start_turn(client, me, ws, chat, sid="sid-boom")
        assert p["session_id"] == "sid-boom"  # 예외 뒤에도 연결이 그대로다
        _end(ws, p["turn_id"], chat, "sid-boom", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_empty_error_is_still_an_error(client, me):
    """시간 초과 예외는 str()이 빈 글자다. 빈 오류가 성공으로 읽혀 NoneType 오류·500이 되던 길을 막는다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "x"}, headers=me["h"])
        m = _until(ws, "session.new")
        ws.send_json({"type": "response", "rid": m["rid"], "error": ""})
        _wait(lambda: not _status(client, me, chat)["busy"])
        notes = [x["content"] for x in _msgs(client, me, chat) if x["role"] == "system"]
        assert notes and "NoneType" not in notes[0] and "원인 없는 오류" in notes[0]


def test_request_timeout_becomes_agent_error_with_guidance():
    class Ws:
        async def send_text(self, text):
            pass

    import asyncio

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=loop.run_forever, daemon=True)
    t.start()
    try:
        conn = AgentConnection(Ws(), 0, loop)
        with pytest.raises(AgentError) as e:
            conn.request("local.tree", {}, timeout=0.05)
        assert "답하지 않았습니다" in str(e.value) and "→" in str(e.value)
        assert not isinstance(e.value, LinkLost)
        conn.close()
        with pytest.raises(LinkLost):
            conn.request("local.tree", {}, timeout=0.05)
    finally:
        loop.call_soon_threadsafe(loop.stop)


def test_load_failure_replaces_session_with_recap_and_one_notice(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, text="브래킷 두께", sid="sid-old")
        ws.send_json({"type": "agent.event", "session_id": "sid-old", "seq": 1, "boot": "boot1",
                      "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "두께를 바꿨습니다"}}})
        _end(ws, p["turn_id"], chat, "sid-old", 2)
        _wait(lambda: not _status(client, me, chat)["busy"])
        # CLI가 죽었다: 그 세션을 잊고 다음 요청은 load부터
        ws.send_json({"type": "agent.runner_exit", "agent": "fake", "session_ids": ["sid-old"], "code": 1, "seq": 3, "boot": "boot1"})
        _wait(lambda: "sid-old" not in hub.live.get(me["id"], set()))
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "이어서"}, headers=me["h"])
        m = _until(ws, "session.load")
        assert m["session_id"] == "sid-old"
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"ok": False}})  # 지원 안 함/없음
        n = _until(ws, "session.new")
        ws.send_json({"type": "response", "rid": n["rid"], "result": {"session_id": "sid-next", "agent": "fake"}})
        q = _until(ws, "session.prompt")
        assert q["session_id"] == "sid-next"
        assert "[이전 대화" in q["text"] and "브래킷 두께" in q["text"] and "두께를 바꿨습니다" in q["text"]
        assert q["text"].endswith("이어서") and "사용자: 이어서" not in q["text"]  # 이번 요청은 요약에 넣지 않는다
        _end(ws, q["turn_id"], chat, "sid-next", 4)
        _wait(lambda: not _status(client, me, chat)["busy"])
        notes = [x["content"] for x in _msgs(client, me, chat) if x["role"] == "system"]
        assert len(notes) == 1 and "새 세션" in notes[0]
        assert SessionLocal().get(models.ChatSession, chat).agent_session_id == "sid-next"


def test_load_transient_error_keeps_session_id(client, me):
    """연결·CLI 문제로 load가 실패하면 새 세션으로 덮어쓰지 않는다."""
    chat = _chat(client, me)
    with SessionLocal() as db:
        db.query(models.ChatSession).filter_by(id=chat).update({"agent_session_id": "sid-keep"})
        db.commit()
    with _ws(client, me) as ws:
        _hello(ws)
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "x"}, headers=me["h"])
        m = _until(ws, "session.load")
        ws.send_json({"type": "response", "rid": m["rid"], "error": "에이전트 프로세스가 종료되었습니다"})
        _wait(lambda: not _status(client, me, chat)["busy"])
    assert SessionLocal().get(models.ChatSession, chat).agent_session_id == "sid-keep"


def test_runner_exit_ends_running_turn_once(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-die")
        ws.send_json({"type": "agent.runner_exit", "agent": "fake", "session_ids": ["sid-die"], "code": 7, "stderr_tail": "죽음", "seq": 1, "boot": "boot1"})
        _wait(lambda: not _status(client, me, chat)["busy"])
        _end(ws, p["turn_id"], chat, "sid-die", 2, stop="error", error="에이전트 프로세스가 종료되었습니다")  # 뒤따라온 알림은 무시한다
        time.sleep(0.2)
        notes = [m["content"] for m in _msgs(client, me, chat) if m["role"] == "system"]
        assert len(notes) == 1 and "종료" in notes[0] and "죽음" in notes[0]


def test_force_restart_message_and_when_disconnected(client, me):
    chat = _chat(client, me)
    assert client.post(f"/api/sessions/{chat}/force-restart", headers=me["h"]).status_code == 409
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-f")
        assert client.post(f"/api/sessions/{chat}/cancel", headers=me["h"]).status_code == 200
        assert _until(ws, "session.cancel")["session_id"] == "sid-f"
        st = _status(client, me, chat)
        assert st["busy"] and st["cancelling"]  # CLI가 답할 때까지 끝나지 않는다
        assert client.post(f"/api/sessions/{chat}/force-restart", headers=me["h"]).status_code == 200
        f = _until(ws, "session.force_restart")
        assert f["agent"] == "fake" and f["turn_id"] == p["turn_id"]
        ws.send_json({"type": "response", "rid": f["rid"], "result": {"ok": True}})  # 받았다고 답하면 다시 보내지 않는다
        assert not _status(client, me, chat)["busy"]
        assert "sid-f" not in hub.live[me["id"]]
        _wait(lambda: not hub.pending_force.get(me["id"]) and not any(t.name.startswith("force-") for t in threading.enumerate()))
    # 끊긴 채로 눌러도 대화가 막히지 않고, 다시 연결되면 전달한다
    chat2 = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        _start_turn(client, me, ws, chat2, sid="sid-g")
    _wait(lambda: _status(client, me, chat2)["link"] == "lost")
    assert client.post(f"/api/sessions/{chat2}/force-restart", headers=me["h"]).status_code == 200
    assert not _status(client, me, chat2)["busy"]
    _wait(lambda: hub.pending_force.get(me["id"]))
    with _ws(client, me) as ws:
        _hello(ws)
        assert _until(ws, "session.force_restart")["agent"] == "fake"


def test_force_restart_without_ack_is_resent_on_next_hello(client, me, monkeypatch):
    """반쯤 끊긴 연결: 보내기는 성공하는데 메시지가 사라진다. 답이 없으면 다음 연결에 다시 보낸다."""
    monkeypatch.setattr("server.app.agent.hub.FORCE_ACK_SEC", 0.2)
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        _start_turn(client, me, ws, chat, sid="sid-h")
        client.post(f"/api/sessions/{chat}/cancel", headers=me["h"])
        assert client.post(f"/api/sessions/{chat}/force-restart", headers=me["h"]).status_code == 200
        _until(ws, "session.force_restart")  # 답하지 않는다
        _wait(lambda: hub.pending_force.get(me["id"]) == {"fake": "boot1"})
    with _ws(client, me) as ws:
        _hello(ws, boot="boot1")
        m = _until(ws, "session.force_restart")
        assert m["agent"] == "fake" and not hub.pending_force.get(me["id"])


def test_force_restart_not_resent_when_agent_restarted_meanwhile(client, me, monkeypatch):
    """Agent가 그사이 다시 시작했으면 그 CLI는 이미 없다. 미리 띄운 새 CLI를 끝내면 또 몇 분을 기다린다."""
    monkeypatch.setattr("server.app.agent.hub.FORCE_ACK_SEC", 0.2)
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        _start_turn(client, me, ws, chat, sid="sid-i")
        client.post(f"/api/sessions/{chat}/force-restart", headers=me["h"])
        _wait(lambda: hub.pending_force.get(me["id"]))
    with _ws(client, me) as ws:
        _hello(ws, boot="boot2")
        assert not hub.pending_force.get(me["id"])
        chat2 = _chat(client, me)
        client.post(f"/api/sessions/{chat2}/prompt", json={"text": "다음"}, headers=me["h"])
        assert _until(ws, "session.new", "session.force_restart")["type"] == "session.new"


def test_force_restart_on_old_agent_says_so_instead_of_pretending(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        ws.send_json({"type": "hello", "hostname": "PC", "version": "0.2.16", "default_agent": "fake",
                      "agents": [{"name": "fake", "label": "가짜", "installed": True}]})
        _early.pop(id(ws), None)
        _until(ws, "agent.settings")
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "멈춘 요청"}, headers=me["h"])
        m = _until(ws, "session.new")
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-old-force", "agent": "fake"}})
        p = _until(ws, "session.prompt")
        r = client.post(f"/api/sessions/{chat}/force-restart", headers=me["h"])
        assert r.status_code == 409 and "옛 버전" in r.json()["detail"] and "→" in r.json()["detail"]
        assert _status(client, me, chat)["busy"]  # 끝낸 척하지 않는다
        assert not [x for x in _msgs(client, me, chat) if x["role"] == "system"]
        ws.send_json({"type": "response", "rid": p["rid"], "result": {"stop_reason": "end_turn"}})
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_old_agent_turn_recovers_from_link_lost_stage(client, me):
    """옛 Agent는 _reconcile을 거치지 않는다. 다시 연결되면 '연결을 다시 잡는 중'에서 풀려야 한다."""
    chat = _chat(client, me)
    old = {"type": "hello", "hostname": "PC", "version": "0.2.16", "default_agent": "fake", "agents": [{"name": "fake", "label": "가짜", "installed": True}]}
    with _ws(client, me) as ws:
        ws.send_json(old)
        _early.pop(id(ws), None)
        _until(ws, "agent.settings")
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "준비 중 끊김"}, headers=me["h"])
        _until(ws, "session.new")
    _wait(lambda: _status(client, me, chat)["stage"] == "reconnecting")
    with _ws(client, me) as ws:
        ws.send_json(old)
        _early.pop(id(ws), None)
        _until(ws, "agent.settings")
        st = _status(client, me, chat)
        assert st["link"] == "ok" and st["stage"] != "reconnecting"
        m = _until(ws, "session.new")
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-old-link", "agent": "fake"}})
        p = _until(ws, "session.prompt")
        _wait(lambda: _status(client, me, chat)["stage"] == "thinking")
        ws.send_json({"type": "response", "rid": p["rid"], "result": {"stop_reason": "end_turn"}})
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_old_agent_socket_is_not_closed_when_replaced(client, me):
    """0.2.16까지의 Agent는 4000을 모르고 1초 뒤 다시 붙어 서로 밀어낸다. 열어 두면 예전처럼 조용하다."""
    old = {"type": "hello", "hostname": "PC-A", "version": "0.2.16", "default_agent": "fake", "agents": [{"name": "fake", "label": "가짜", "installed": True}]}
    with _ws(client, me) as a:
        a.send_json(old)
        _until(a, "agent.settings")
        with _ws(client, me) as b:
            _hello(b)
            a.send_json({"type": "heartbeat", "hostname": "PC-A", "status": {}})  # 밀려난 뒤의 메시지로도 닫지 않는다
            time.sleep(0.3)
            assert registry.get(me["id"]).boot == "boot1"
            assert client.get("/api/agent/status", headers=me["h"]).json()["connected"] is True


def test_new_agent_replaced_before_hello_is_closed_with_4000(client, me):
    """hello 전에 밀려난 새 Agent: 그 hello의 boot를 보고 대기 신호로 닫는다."""
    with _ws(client, me) as a:
        with _ws(client, me) as b:
            _hello(b)
            a.send_json({"type": "hello", "hostname": "PC-A", "version": "9", "boot": "boot-a", "agents": []})
            closed = a.receive()
            assert closed["type"] == "websocket.close" and closed["code"] == link.REPLACED_CODE


def test_stop_during_start_then_folder_change_does_not_bind_old_session(client, me):
    """시작 중 중지 뒤 작업 폴더를 바꾸면 늦게 끝난 session/new의 세션(옛 폴더)을 이 대화에 묶지 않는다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws, running=())
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "폴더를 잘못 골랐다"}, headers=me["h"])
        m = _until(ws, "session.new")
        client.post(f"/api/sessions/{chat}/cancel", headers=me["h"])
        assert not _status(client, me, chat)["busy"]
        # local.folder.set은 서버 라우트가 Agent에 묻는다. 시험이 Agent 역할을 해 답한다
        results = []
        t = threading.Thread(target=lambda: results.append(client.put(f"/api/projects/{me['pid']}/local/folder", json={"path": "C:/new"}, headers=me["h"])))
        t.start()
        q = _until(ws, "local.folder.set")
        ws.send_json({"type": "response", "rid": q["rid"], "result": {"path": "C:/new"}})
        t.join(timeout=10)
        assert results[0].status_code == 200
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-old-folder", "agent": "fake"}})
        time.sleep(0.5)
        with SessionLocal() as db:
            assert db.get(models.ChatSession, chat).agent_session_id is None
        assert hub.by_sid.get("sid-old-folder") is None
        # 다음 요청은 새 폴더로 새 세션을 연다
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "다시"}, headers=me["h"])
        n = _until(ws, "session.new", "session.load")
        assert n["type"] == "session.new"
        ws.send_json({"type": "response", "rid": n["rid"], "result": {"session_id": "sid-new-folder", "agent": "fake"}})
        p = _until(ws, "session.prompt")
        assert p["session_id"] == "sid-new-folder"
        _end(ws, p["turn_id"], chat, "sid-new-folder", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_switching_agent_after_stop_does_not_wait_for_the_stuck_cli(client, me):
    """Gemini가 보안 검사에 걸려 시작이 멈춘 채 Claude로 바꾸면(자연스러운 복구) 새 CLI가 옛 CLI의 시작을 기다리지 않는다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        ws.send_json({"type": "hello", "hostname": "PC", "version": "9", "boot": "boot1", "live_sessions": [], "active_turns": [], "running_agents": [], "start_times": {},
                      "default_agent": "fake", "agents": [{"name": "fake", "label": "가짜", "installed": True}, {"name": "fake2", "label": "가짜2", "installed": True}]})
        _until(ws, "agent.settings")
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "첫 요청"}, headers=me["h"])
        stuck = _until(ws, "session.new")
        assert stuck["agent"] == "fake"
        client.post(f"/api/sessions/{chat}/cancel", headers=me["h"])
        assert client.put(f"/api/sessions/{chat}", json={"agent": "fake2"}, headers=me["h"]).status_code == 200
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "두 번째"}, headers=me["h"])
        n = _until(ws, "session.new")  # 옛 CLI가 아직 답하지 않았는데 새 CLI의 준비가 나간다
        assert n["agent"] == "fake2"
        ws.send_json({"type": "response", "rid": n["rid"], "result": {"session_id": "sid-fake2", "agent": "fake2"}})
        p = _until(ws, "session.prompt")
        assert p["session_id"] == "sid-fake2" and p["agent"] == "fake2"
        # 옛 CLI의 단계 소식은 새 CLI의 말풍선에 섞이지 않는다
        ws.send_json({"type": "session.stage", "turn_id": stuck["turn_id"], "agent": "fake", "stage": "initialize", "seq": 1, "boot": "boot1"})
        time.sleep(0.2)
        assert _status(client, me, chat)["stage"] == "thinking"
        # 옛 CLI가 늦게 끝나도 이 대화의 세션을 덮지 않는다
        ws.send_json({"type": "response", "rid": stuck["rid"], "result": {"session_id": "sid-late", "agent": "fake"}})
        time.sleep(0.3)
        with SessionLocal() as db:
            assert db.get(models.ChatSession, chat).agent_session_id == "sid-fake2"
        _end(ws, p["turn_id"], chat, "sid-fake2", 2)
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_stopped_request_is_left_out_of_recap(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws, running=())
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "전체 DOE 200케이스 해석을 돌려 줘"}, headers=me["h"])
        first = _until(ws, "session.new")
        client.post(f"/api/sessions/{chat}/cancel", headers=me["h"])
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "아니, 모델 파일만 확인해 줘"}, headers=me["h"])
        ws.send_json({"type": "response", "rid": first["rid"], "result": {"session_id": "sid-stop", "agent": "fake"}})
        p = _until(ws, "session.prompt")
        assert "200케이스" not in p["text"] and p["text"].endswith("아니, 모델 파일만 확인해 줘")
        _end(ws, p["turn_id"], chat, "sid-stop", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])
        # CLI에 닿은 뒤 중지한 요청: 요약에는 남지만 다시 하라는 뜻으로 읽히지 않게 표시한다
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "오래 걸리는 일"}, headers=me["h"])
        q = _until(ws, "session.prompt")
        client.post(f"/api/sessions/{chat}/cancel", headers=me["h"])
        _end(ws, q["turn_id"], chat, "sid-stop", 2, stop="cancelled")
        _wait(lambda: not _status(client, me, chat)["busy"])
        ws.send_json({"type": "agent.runner_exit", "agent": "fake", "session_ids": ["sid-stop"], "code": 1, "seq": 3, "boot": "boot1"})
        _wait(lambda: "sid-stop" not in hub.live.get(me["id"], set()))
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "다음 일"}, headers=me["h"])
        m = _until(ws, "session.load")
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"ok": False}})
        n = _until(ws, "session.new")
        ws.send_json({"type": "response", "rid": n["rid"], "result": {"session_id": "sid-next", "agent": "fake"}})
        r = _until(ws, "session.prompt")
        assert "200케이스" not in r["text"] and "중지한 요청" in r["text"] and "오래 걸리는 일" in r["text"]
        _end(ws, r["turn_id"], chat, "sid-next", 4)
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_server_and_agent_restart_notes_turn_with_output_and_closes_tools(client, me):
    """서버와 Agent가 함께 다시 시작하면 답이 조금 나온 턴도 Agent가 모른다. 끝난 답처럼 남거나 도구가 영원히 '실행 중'이면 안 된다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        _start_turn(client, me, ws, chat, sid="sid-both")
        ws.send_json({"type": "agent.event", "session_id": "sid-both", "seq": 1, "boot": "boot1",
                      "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "반쯤 쓴 답"}}})
        ws.send_json({"type": "agent.event", "session_id": "sid-both", "seq": 2, "boot": "boot1",
                      "update": {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "스크립트", "status": "in_progress"}})
        _wait(lambda: any(m["role"] == "tool" for m in _msgs(client, me, chat)))
    _reset_hub(me["id"])
    with _ws(client, me) as ws:
        _hello(ws, boot="boot2", live=[], turns=[])
        msgs = _msgs(client, me, chat)
        notes = [m["content"] for m in msgs if m["role"] == "system"]
        assert len(notes) == 1 and "끊겼습니다" in notes[0] and "다시 보내" in notes[0]
        assert next(m for m in msgs if m["role"] == "tool")["meta"]["status"] == "failed"
    _reset_hub(me["id"])
    with _ws(client, me) as ws:  # 또 다시 시작해도 안내는 한 번이다
        _hello(ws, boot="boot3", live=[], turns=[])
        assert len([m for m in _msgs(client, me, chat) if m["role"] == "system"]) == 1


def test_restored_turn_is_marked_done_so_next_restart_stays_quiet(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-r")
    _reset_hub(me["id"])
    turn = {"turn_id": p["turn_id"], "chat_id": chat, "session_id": "sid-r", "agent": "fake", "started_at": time.time()}
    with _ws(client, me) as ws:
        _hello(ws, live=["sid-r"], turns=[turn])
        _end(ws, p["turn_id"], chat, "sid-r", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])
    _reset_hub(me["id"])
    with _ws(client, me) as ws:
        _hello(ws, boot="boot2", live=[], turns=[])
        assert not [m for m in _msgs(client, me, chat) if m["role"] == "system"]


def _locked_handler(monkeypatch, name: str, times: int):
    """처음 몇 번은 'database is locked'를 낸다(사내 보안 프로그램이 DB 파일을 잡은 상태)."""
    from sqlalchemy.exc import OperationalError

    calls = {"n": 0}
    real = registry.handlers[name]

    def flaky(conn, m):
        calls["n"] += 1
        if calls["n"] <= times:
            raise OperationalError("UPDATE", {}, Exception("database is locked"))
        return real(conn, m)
    monkeypatch.setitem(registry.handlers, name, flaky)
    return calls


def test_db_lock_on_turn_end_is_retried_not_lost(client, me, monkeypatch):
    monkeypatch.setattr(link, "DB_RETRY_POLL_SEC", 0.02)
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-lock")
        calls = _locked_handler(monkeypatch, "agent.turn_end", 3)
        _end(ws, p["turn_id"], chat, "sid-lock", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])
        assert calls["n"] == 4
        assert _until(ws, "agent.ack")["seq"] == 1


def test_db_lock_gives_up_with_notice_and_chat_is_not_stuck(client, me, monkeypatch):
    monkeypatch.setattr(link, "DB_RETRY_SEC", 0.1)
    monkeypatch.setattr(link, "DB_RETRY_POLL_SEC", 0.02)
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-lost")
        _locked_handler(monkeypatch, "agent.event", 10 ** 6)
        ws.send_json({"type": "agent.event", "session_id": "sid-lost", "seq": 1, "boot": "boot1",
                      "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "못 저장한 글자"}}})
        assert _until(ws, "agent.ack")["seq"] == 1  # 끝내 못 하면 넘어간다. 같은 메시지로 막히지 않는다
        monkeypatch.undo()
        _end(ws, p["turn_id"], chat, "sid-lost", 2)
        _wait(lambda: not _status(client, me, chat)["busy"])
        # 안내는 턴을 끝낸 다음에 저장한다(저장이 실패해도 턴이 갇히지 않게)
        notes = _wait(lambda: [m["content"] for m in _msgs(client, me, chat) if m["role"] == "system"])
        assert len(notes) == 1 and "빠졌습니다" in notes[0]


def test_turn_end_finishes_even_if_note_cannot_be_saved(client, me, monkeypatch):
    """안내를 저장하지 못해도 턴은 끝난다. 'database is locked' 하나로 대화가 진행 중에 영원히 갇히면 안 된다."""
    from sqlalchemy.exc import OperationalError

    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-note")

        def boom(*a, **k):
            raise OperationalError("INSERT", {}, Exception("database is locked"))
        monkeypatch.setattr(hub, "_add_message", boom)
        _end(ws, p["turn_id"], chat, "sid-note", 1, error="CLI 오류")
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_link_lost_does_not_block_the_event_loop_while_lock_is_held(client, me):
    """대화 잠금을 쥔 스레드가 conn.send로 이벤트 루프를 기다리는 사이 끊김 정리가 루프에서 그 잠금을 기다리면 서버 전체가 멈춘다."""
    chat = _chat(client, me)
    cm = _ws(client, me)
    ws = cm.__enter__()
    _hello(ws)
    _start_turn(client, me, ws, chat, sid="sid-dead")
    st = hub.chats[chat]
    st.lock.acquire()
    answered = []
    closer = threading.Thread(target=lambda: cm.__exit__(None, None, None), daemon=True)
    asker = threading.Thread(target=lambda: answered.append(client.get("/api/agent/status", headers=me["h"]).status_code), daemon=True)
    try:
        closer.start()  # 잠금을 쥔 채로 끊긴다
        time.sleep(0.5)
        asker.start()
        asker.join(timeout=5)
        assert answered == [200]  # 루프가 막혔다면 다른 사용자의 요청까지 돌아오지 않는다
    finally:
        st.lock.release()
    closer.join(timeout=10)
    _wait(lambda: _status(client, me, chat)["link"] == "lost")


def test_late_link_lost_after_reconnect_keeps_link_ok(client, me):
    """끊김 정리는 스레드에서 돈다. 그사이 다시 붙었으면 '연결 중'으로 되돌리지 않는다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-late-lost")
        hub.on_link_lost(me["id"])  # 지금 연결이 있다: 무시한다
        st = _status(client, me, chat)
        assert st["link"] == "ok" and st["stage"] != "reconnecting"
        _end(ws, p["turn_id"], chat, "sid-late-lost", 1)
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_cancel_while_disconnected_is_delivered_on_reconnect(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        p = _start_turn(client, me, ws, chat, sid="sid-c")
    _wait(lambda: _status(client, me, chat)["link"] == "lost")
    assert client.post(f"/api/sessions/{chat}/cancel", headers=me["h"]).status_code == 200
    with _ws(client, me) as ws:
        turn = {"turn_id": p["turn_id"], "chat_id": chat, "session_id": "sid-c", "agent": "fake", "started_at": time.time()}
        _hello(ws, live=["sid-c"], turns=[turn])
        assert _until(ws, "session.cancel")["session_id"] == "sid-c"
        _end(ws, p["turn_id"], chat, "sid-c", 1, stop="cancelled")
        _wait(lambda: not _status(client, me, chat)["busy"])


def test_orphan_permission_is_answered_so_cli_does_not_wait_forever(client, me):
    with _ws(client, me) as ws:
        _hello(ws)
        ws.send_json({"type": "agent.permission", "session_id": "모르는세션", "request_id": "fake:1", "params": {}})
        m = _until(ws, "permission.reply")
        assert m["request_id"] == "fake:1" and m["option_id"] is None


def test_old_agent_without_boot_still_works(client, me):
    """서버를 먼저 올리면 업데이트 전의 Agent가 잠시 붙어 있다. 응답 대기형으로 돌린다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        ws.send_json({"type": "hello", "hostname": "PC", "version": "0.2.16", "default_agent": "fake",
                      "agents": [{"name": "fake", "label": "가짜", "installed": True}]})
        _early.pop(id(ws), None)
        _until(ws, "agent.settings")
        client.post(f"/api/sessions/{chat}/prompt", json={"text": "옛 Agent"}, headers=me["h"])
        m = _until(ws, "session.new")
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-old-agent", "agent": "fake"}})
        p = _until(ws, "session.prompt")
        assert p["rid"]
        ws.send_json({"type": "agent.event", "session_id": "sid-old-agent", "update": {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "답"}}})
        ws.send_json({"type": "response", "rid": p["rid"], "result": {"stop_reason": "end_turn"}})
        _wait(lambda: not _status(client, me, chat)["busy"])
        assert [x["content"] for x in _msgs(client, me, chat) if x["role"] == "agent"] == ["답"]
