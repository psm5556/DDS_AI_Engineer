"""승인한 작업 흐름은 서버가 끝까지 책임진다. 흐름 #25: 답하는 중에 누른 승인이 사라져 approved로 멈췄다.
상태 × 사건: 답하는 중·쉬는 중·PC Agent 없음 × 승인·턴 끝·중지·끊김·재연결·서버 재시작."""
import pytest

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402

from server.app import main, models  # noqa: E402
from server.app.agent.hub import FLOW_NUDGES, hub  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.tests.test_session_hub import _chat, _end, _hello, _msgs, _start_turn, _status, _until, _wait, _ws  # noqa: E402

NODES = [{"id": "doe", "label": "DOE", "type": "doe"}, {"id": "train", "label": "학습", "type": "train"}]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


_n = [0]


@pytest.fixture
def me(client):
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    headers, user = _auth(client, f"flow{_n[0]}", admin)
    token = client.post("/api/tokens", json={"name": "pc"}, headers=headers).json()["token"]
    pid = client.post("/api/projects", json={"name": f"흐름{_n[0]}"}, headers=headers).json()["id"]
    return {"h": headers, "id": user["id"], "token": token, "pid": pid}


def _propose(client, me, title="브래킷"):
    r = client.post(f"/api/projects/{me['pid']}/flows", json={"title": title, "goal": "", "nodes": NODES, "edges": []}, headers=me["h"])
    assert r.status_code == 200
    return r.json()


def _decide(client, me, flow, status="approved", chat=None):
    r = client.put(f"/api/flows/{flow['id']}/status", json={"status": status, "version": flow["version"], "session_id": chat}, headers=me["h"])
    assert r.status_code == 200, r.text
    return r.json()


def _flow(client, me, flow_id):
    return client.get(f"/api/flows/{flow_id}", headers=me["h"]).json()


def _prompts_for_user(client, me, chat):
    return [m for m in _msgs(client, me, chat) if m["role"] == "user"]


def test_approve_while_idle_sends_at_once(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        assert client.get(f"/api/projects/{me['pid']}/flows", headers=me["h"]).json()[0]["id"] == flow["id"]
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        _wait(lambda: not _status(client, me, chat)["busy"], what="턴 끝")
        got = _decide(client, me, flow, chat=chat)
        assert got["status"] == "approved" and got["start_queued"] is False
        p = _until(ws, "session.prompt")
        assert "승인했습니다" in p["text"] and "묻지" in p["text"]
        users = _prompts_for_user(client, me, chat)
        assert "승인했습니다" in users[-1]["content"] and users[-1]["meta"]["flow_id"] == flow["id"]
        assert "묻지" not in users[-1]["content"]  # 대화에는 짧은 말만 보인다


def test_approve_during_a_running_turn_starts_when_the_turn_ends(client, me):
    """흐름 #25의 재현: 답하는 중에 눌렀다. 옛 화면은 409로 말을 잃었다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        got = _decide(client, me, flow, chat=chat)
        assert got["status"] == "approved" and got["start_queued"] is True
        assert len(_prompts_for_user(client, me, chat)) == 1  # 아직 보내지 않았다
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        p = _until(ws, "session.prompt")
        assert "승인했습니다" in p["text"]
        _wait(lambda: not _flow(client, me, flow["id"])["start_queued"], what="전달 표시")
        assert any("승인했습니다" in m["content"] for m in _prompts_for_user(client, me, chat))


def test_flow_remembers_the_chat_that_proposed_it(client, me):
    """화면이 대화를 알려 주지 않아도 AI가 제안한 대화로 전한다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        _decide(client, me, flow)  # session_id 없음
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        assert "승인했습니다" in _until(ws, "session.prompt")["text"]


def test_rest_flow_without_a_chat_stays_silent(client, me):
    """대화 없이 REST로 만든 흐름: 전할 곳이 없다. 스크립트가 직접 돌린다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        flow = _propose(client, me)
        got = _decide(client, me, flow)
        assert got["status"] == "approved" and got["start_queued"] is False
        assert not _status(client, me, chat)["busy"] and not _msgs(client, me, chat)


def test_approve_without_pc_agent_waits_and_starts_on_connect(client, me):
    chat = _chat(client, me)
    flow = _propose(client, me)
    got = _decide(client, me, flow, chat=chat)
    assert got["status"] == "approved" and got["start_queued"] is True  # 화면이 'PC Agent가 연결되면 시작'을 보여 준다
    assert not _msgs(client, me, chat)
    with _ws(client, me) as ws:
        _hello(ws)
        m = _until(ws, "session.new")  # 연결되자마자 서버가 시작한다
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-1", "agent": "fake"}})
        assert "승인했습니다" in _until(ws, "session.prompt")["text"]
    # 다시 연결해도 두 번 보내지 않는다
    assert sum("승인했습니다" in m["content"] for m in _prompts_for_user(client, me, chat)) == 1


def test_waiting_approval_survives_a_server_restart(client, me):
    """줄 세운 승인은 DB에 있다. 서버를 다시 시작해도(메모리 상태 없음) 연결되면 시작한다."""
    chat = _chat(client, me)
    flow = _propose(client, me)
    _decide(client, me, flow, chat=chat)
    hub.forget_chat(chat)
    main._recover_stranded_flows()
    assert _flow(client, me, flow["id"])["status"] == "approved"  # 전할 곳이 있는 승인은 되돌리지 않는다
    with _ws(client, me) as ws:
        _hello(ws)
        m = _until(ws, "session.new")
        ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-1", "agent": "fake"}})
        assert "승인했습니다" in _until(ws, "session.prompt")["text"]


def test_old_stranded_approval_returns_to_review(client, me):
    """예전 서버에서 멈춘 흐름(승인 표시만 있고 전한 기록이 없다): 며칠 뒤 저절로 돌면 안 되니 다시 승인을 받는다."""
    flow = _propose(client, me)
    with SessionLocal() as db:
        f = db.get(models.Flow, flow["id"])
        f.status, f.approved_by = "approved", me["id"]
        db.commit()
    main._recover_stranded_flows()
    got = _flow(client, me, flow["id"])
    assert got["status"] == "review"


def test_stop_before_the_queued_approval_runs_returns_it_to_review(client, me):
    """중지를 누른 뒤에 일이 시작되면 안 된다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        _decide(client, me, flow, chat=chat)
        client.post(f"/api/sessions/{chat}/cancel", headers=me["h"])
        _end(ws, first["turn_id"], chat, "sid-1", 1, stop="cancelled")
        _wait(lambda: not _status(client, me, chat)["busy"], what="중지")
        got = _flow(client, me, flow["id"])
        assert got["status"] == "review" and not got["start_queued"]
        assert any("승인을 되돌렸습니다" in m["content"] for m in _msgs(client, me, chat) if m["role"] == "system")


def test_cancel_the_approval_while_it_waits(client, me):
    chat = _chat(client, me)
    flow = _propose(client, me)
    got = _decide(client, me, flow, chat=chat)
    back = _decide(client, me, got, "review", chat)
    assert back["status"] == "review" and not back["start_queued"]
    with _ws(client, me) as ws:
        _hello(ws)
        assert not _status(client, me, chat)["busy"]  # 연결돼도 시작하지 않는다


def test_started_approval_cannot_be_taken_back(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        _wait(lambda: not _status(client, me, chat)["busy"], what="턴 끝")
        got = _decide(client, me, flow, chat=chat)
        r = client.put(f"/api/flows/{flow['id']}/status", json={"status": "review", "version": got["version"]}, headers=me["h"])
        assert r.status_code == 409 and "중지" in r.json()["detail"]


def test_ending_the_turn_with_steps_left_sends_it_back_without_asking_the_user(client, me):
    """AI가 묻고 끝내도(중간에 묻지 않기로 했다) 서버가 다시 시킨다. 진전이 있으면 한도가 새로 시작한다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        _wait(lambda: not _status(client, me, chat)["busy"], what="턴 끝")
        _decide(client, me, flow, chat=chat)
        p = _until(ws, "session.prompt")
        client.put(f"/api/flows/{flow['id']}/nodes/doe", json={"status": "complete"}, headers=me["h"])
        _end(ws, p["turn_id"], chat, "sid-1", 2)  # 물어보고 끝냈다
        again = _until(ws, "session.prompt")
        assert "남은 단계: 학습" in again["text"] and "묻지 말고" in again["text"]
        last = _prompts_for_user(client, me, chat)[-1]
        assert last["meta"]["auto"] == "continue" and "이어서 진행합니다" in last["content"]
        client.put(f"/api/flows/{flow['id']}/nodes/train", json={"status": "complete"}, headers=me["h"])
        assert _flow(client, me, flow["id"])["status"] == "complete"
        before = len(_prompts_for_user(client, me, chat))
        _end(ws, again["turn_id"], chat, "sid-1", 3)
        _wait(lambda: not _status(client, me, chat)["busy"], what="끝")
        assert len(_prompts_for_user(client, me, chat)) == before  # 끝난 흐름은 더 시키지 않는다


def test_no_progress_ends_in_failure_with_reason_and_action(client, me):
    """무한히 돌지 않는다. 멈춘 채 남지도 않는다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        _wait(lambda: not _status(client, me, chat)["busy"], what="턴 끝")
        _decide(client, me, flow, chat=chat)
        seq = 2
        for _ in range(FLOW_NUDGES + 1):
            p = _until(ws, "session.prompt")
            _end(ws, p["turn_id"], chat, "sid-1", seq)
            seq += 1
        _wait(lambda: _flow(client, me, flow["id"])["status"] == "failed", what="실패 표시")
        got = _flow(client, me, flow["id"])
        assert got["nodes"][0]["status"] == "failed" and "이어 가지 못했습니다" in got["nodes"][0]["detail"]
        note = next(m["content"] for m in _msgs(client, me, chat) if m["role"] == "system")
        assert "→" in note and "DOE" in note
        assert not _status(client, me, chat)["busy"]


def test_crash_during_the_flow_is_retried_and_counts_toward_the_limit(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        _wait(lambda: not _status(client, me, chat)["busy"], what="턴 끝")
        _decide(client, me, flow, chat=chat)
        p = _until(ws, "session.prompt")
        ws.send_json({"type": "agent.runner_exit", "agent": "fake", "session_ids": ["sid-1"], "reason": "crash", "seq": 2, "boot": "boot1"})
        m = _until(ws, "session.new", "session.load", "session.prompt")  # CLI가 죽었다. 서버가 다시 시작시킨다
        if m["type"] != "session.prompt":
            ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-2", "agent": "fake", "ok": True}})
            m = _until(ws, "session.prompt")
        assert "남은 단계" in m["text"] and m["turn_id"] != p["turn_id"]


def test_turn_lost_while_agent_restarted_is_retried(client, me):
    """승인 후 연결이 끊기고 Agent가 다시 시작되어 그 턴이 사라졌다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        _wait(lambda: not _status(client, me, chat)["busy"], what="턴 끝")
        _decide(client, me, flow, chat=chat)
        _until(ws, "session.prompt")
    _wait(lambda: _status(client, me, chat)["link"] == "lost", what="끊김")
    assert _status(client, me, chat)["busy"]  # 끊겨도 턴은 버리지 않는다
    with _ws(client, me) as ws:
        _hello(ws, boot="boot2")
        m = _until(ws, "session.new", "session.load", "session.prompt")
        if m["type"] != "session.prompt":
            ws.send_json({"type": "response", "rid": m["rid"], "result": {"session_id": "sid-2", "agent": "fake", "ok": True}})
            m = _until(ws, "session.prompt")
        assert "남은 단계" in m["text"]


def test_stop_during_the_flow_marks_it_stopped_and_user_can_resume(client, me):
    """사용자는 중지하고 프롬프트로 계획을 바꿀 수 있다. 그 뒤에 묻지 않고 시키지 않는다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        _wait(lambda: not _status(client, me, chat)["busy"], what="턴 끝")
        _decide(client, me, flow, chat=chat)
        p = _until(ws, "session.prompt")
        client.put(f"/api/flows/{flow['id']}/nodes/doe", json={"status": "in_progress"}, headers=me["h"])
        client.post(f"/api/sessions/{chat}/cancel", headers=me["h"])
        _end(ws, p["turn_id"], chat, "sid-1", 2, stop="cancelled")
        _wait(lambda: _flow(client, me, flow["id"])["status"] == "stopped", what="멈춤")
        assert any("멈췄습니다" in m["content"] and "→" in m["content"] for m in _msgs(client, me, chat) if m["role"] == "system")
        # 사용자의 말로 이어 간다. AI가 단계를 움직이면 다시 그 흐름의 턴이고, 남기고 끝내면 서버가 이어서 시킨다
        assert client.post(f"/api/sessions/{chat}/prompt", json={"text": "DOE를 10점으로 줄여서 이어서 진행해 줘"}, headers=me["h"]).status_code == 200
        q = _until(ws, "session.prompt")
        assert "10점" in q["text"]
        client.put(f"/api/flows/{flow['id']}/nodes/doe", json={"status": "complete"}, headers=me["h"])
        assert _flow(client, me, flow["id"])["status"] == "in_progress"
        _end(ws, q["turn_id"], chat, "sid-1", 3)
        assert "남은 단계: 학습" in _until(ws, "session.prompt")["text"]


def test_question_turn_after_a_stop_does_not_restart_the_flow(client, me):
    """멈춘 뒤의 일반 질문: 답만 하고 흐름을 다시 돌리지 않는다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        _wait(lambda: not _status(client, me, chat)["busy"], what="턴 끝")
        _decide(client, me, flow, chat=chat)
        p = _until(ws, "session.prompt")
        client.post(f"/api/sessions/{chat}/cancel", headers=me["h"])
        _end(ws, p["turn_id"], chat, "sid-1", 2, stop="cancelled")
        _wait(lambda: not _status(client, me, chat)["busy"], what="중지")
        assert client.post(f"/api/sessions/{chat}/prompt", json={"text": "지금 어디까지 했어?"}, headers=me["h"]).status_code == 200
        q = _until(ws, "session.prompt")
        _end(ws, q["turn_id"], chat, "sid-1", 3)
        _wait(lambda: not _status(client, me, chat)["busy"], what="답")
        before = len(_prompts_for_user(client, me, chat))
        assert before == 3 and _flow(client, me, flow["id"])["status"] in ("review", "stopped")


def test_new_proposal_supersedes_the_old_flow(client, me):
    """AI가 계획을 바꿔 새 흐름을 올렸다. 옛 흐름을 더 밀면 두 계획이 함께 돈다."""
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        _wait(lambda: not _status(client, me, chat)["busy"], what="턴 끝")
        _decide(client, me, flow, chat=chat)
        p = _until(ws, "session.prompt")
        client.put(f"/api/flows/{flow['id']}/nodes/doe", json={"status": "in_progress"}, headers=me["h"])
        _propose(client, me, "브래킷 v2")
        _end(ws, p["turn_id"], chat, "sid-1", 2)
        _wait(lambda: _flow(client, me, flow["id"])["status"] == "stopped", what="옛 흐름 멈춤")
        assert not _status(client, me, chat)["busy"]


def test_rejection_is_delivered_too_and_runs_nothing(client, me):
    chat = _chat(client, me)
    with _ws(client, me) as ws:
        _hello(ws)
        first = _start_turn(client, me, ws, chat)
        flow = _propose(client, me)
        got = _decide(client, me, flow, "rejected", chat)
        assert got["status"] == "rejected" and got["start_queued"] is True
        _end(ws, first["turn_id"], chat, "sid-1", 1)
        p = _until(ws, "session.prompt")
        assert "반려했습니다" in p["text"]
        _end(ws, p["turn_id"], chat, "sid-1", 2)
        _wait(lambda: not _status(client, me, chat)["busy"], what="턴 끝")
        assert _flow(client, me, flow["id"])["status"] == "rejected"  # 반려는 다시 시키지 않는다


def test_deleting_the_chat_returns_a_waiting_approval_to_review(client, me):
    chat = _chat(client, me)
    flow = _propose(client, me)
    _decide(client, me, flow, chat=chat)
    assert client.delete(f"/api/sessions/{chat}", headers=me["h"]).status_code == 200
    got = _flow(client, me, flow["id"])
    assert got["status"] == "review" and not got["start_queued"]


def test_member_cannot_send_approval_into_someone_elses_chat(client, me):
    admin, _ = _auth(client, "admin")
    other_h, other = _auth(client, "flowother", admin)
    client.put(f"/api/projects/{me['pid']}/members/{other['id']}", json={"role": "member"}, headers=me["h"])
    chat = _chat(client, me)
    flow = _propose(client, me)
    r = client.put(f"/api/flows/{flow['id']}/status", json={"status": "approved", "version": flow["version"], "session_id": chat}, headers=other_h)
    assert r.status_code == 403  # 승인은 Task 주인만 한다
    assert not _msgs(client, me, chat)
