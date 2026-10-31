"""케이스 중지: 해석 창이 숨어 있으니 웹앱에서 대기·실행 중인 케이스를 멈출 수 있어야 한다."""
from server.tests.test_api import _auth, client  # noqa: F401, I001 — test_api가 임시 데이터 폴더를 정한 뒤에 서버 코드를 불러와야 한다

import pytest  # noqa: E402

from server.app.agent.link import registry  # noqa: E402
from server.app.study import service  # noqa: E402


class _Conn:
    def __init__(self, user_id):
        self.user_id, self.info, self.sent = user_id, {}, []

    def send(self, m):
        self.sent.append(m)


@pytest.fixture
def running(client):  # noqa: F811
    """스크립트 어댑터 스터디의 케이스 3건을 내 PC Agent로 보낸 상태. 1건 실행 중, 2건 대기"""
    admin, me = _auth(client, "admin")
    pid = client.post("/api/projects", headers=admin, json={"name": "stop-test"}).json()["id"]
    study = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "Stop", "adapter": "script", "adapter_config": {"tool": "python", "script": "a.py"},
        "variables": [{"name": "x", "lower": 0, "upper": 1}], "objectives": [{"name": "y", "goal": "min"}]}).json()
    sid = study["id"]
    assert client.post(f"/api/studies/{sid}/doe", headers=admin, json={"n": 3, "method": "lhs", "seed": 1}).status_code == 200
    conn = _Conn(me["id"])
    registry.conns[me["id"]] = conn
    try:
        assert client.post(f"/api/studies/{sid}/run", headers=admin, json={}).json() == {"queued": 3}
        ids = [c["id"] for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json()]
        service.on_case_status({"case_id": ids[0], "status": "running", "run_dir": "D:/run"})
        yield admin, me, sid, ids, conn
    finally:
        registry.conns.pop(me["id"], None)
        with service._batch_lock:
            service._batches.pop(sid, None)


def test_stop_sends_cancel_and_clears_running(client, running):  # noqa: F811
    admin, me, sid, ids, conn = running
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["running"] is True
    r = client.post(f"/api/studies/{sid}/stop", headers=admin, json={})
    assert r.status_code == 200 and r.json()["stopping"] == 3 and r.json()["others"] == 0 and r.json()["released"] == 0
    assert conn.sent[-1] == {"type": "case.cancel", "case_ids": ids, "extract_ids": []}

    # PC Agent가 끝낸 뒤 알린다. 중지는 끝난 상태라 '실행 중'이 풀린다
    for case_id in ids:
        service.on_case_status({"case_id": case_id, "status": "cancelled", "error": "사용자가 중지했습니다"})
    st = client.get(f"/api/studies/{sid}", headers=admin).json()
    assert st["running"] is False and st["counts"] == {"cancelled": 3}
    assert {c["error"] for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json()} == {f"{me['display_name']}님이 중지했습니다"}

    # 설정을 고친 뒤 다시 돌릴 수 있다
    assert client.post(f"/api/studies/{sid}/run", headers=admin, json={"case_ids": ids[:1]}).json() == {"queued": 1}
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["counts"] == {"queued": 1, "cancelled": 2}


def test_stop_selected_cases_only(client, running):  # noqa: F811
    admin, _, sid, ids, conn = running
    assert client.post(f"/api/studies/{sid}/stop", headers=admin, json={"case_ids": ids[1:2]}).json()["stopping"] == 1
    assert conn.sent[-1]["case_ids"] == ids[1:2]


def test_stop_without_agent_settles_the_cases_and_says_so(client, running):  # noqa: F811
    """내 PC가 꺼져 있어도 내 케이스는 멈출 수 있다. 409로 막으면 '실행 중'이 그 PC가 켜질 때까지 남는다."""
    admin, me, sid, ids, _ = running
    registry.conns.pop(me["id"], None)
    r = client.post(f"/api/studies/{sid}/stop", headers=admin, json={})
    assert r.status_code == 200 and r.json()["released"] == len(ids) and r.json()["stopping"] == 0 and "꺼져" in r.json()["note"]
    st = client.get(f"/api/studies/{sid}", headers=admin).json()
    assert st["counts"].get("cancelled") == len(ids) and not st["running"]


def test_stop_leaves_other_members_run_alone(client, running):  # noqa: F811
    admin, me, sid, ids, conn = running
    kim, kim_user = _auth(client, "kim_stop", admin)
    pid = client.get(f"/api/studies/{sid}", headers=admin).json()["study"]["project_id"]
    assert client.put(f"/api/projects/{pid}/members/{kim_user['id']}", headers=admin, json={"role": "member"}).status_code == 200
    registry.conns[kim_user["id"]] = _Conn(kim_user["id"])
    try:
        r = client.post(f"/api/studies/{sid}/stop", headers=kim, json={})
        assert r.status_code == 403 and "다른 멤버" in r.json()["detail"]
        assert conn.sent[-1]["type"] == "case.run"  # 중지를 보내지 않았다
    finally:
        registry.conns.pop(kim_user["id"], None)


@pytest.fixture
def member(client, running):  # noqa: F811
    """같은 프로젝트의 다른 멤버. PC Agent 없이 웹으로 본다"""
    admin, _, sid, _, _ = running
    kim, kim_user = _auth(client, "kim_drop", admin)
    pid = client.get(f"/api/studies/{sid}", headers=admin).json()["study"]["project_id"]
    assert client.put(f"/api/projects/{pid}/members/{kim_user['id']}", headers=admin, json={"role": "member"}).status_code == 200
    return kim


def _case(client, h, sid, case_id):  # noqa: F811
    return next(c for c in client.get(f"/api/studies/{sid}/cases", headers=h).json() if c["id"] == case_id)


def test_member_cannot_stop_a_case_during_a_brief_drop(client, running, member):  # noqa: F811
    """연결이 잠깐 끊긴 순간 멤버의 중지가 200으로 통과하면, 다시 연결될 때 살아 있는 솔버가 끝나고 누가 멈췄는지도 남지 않았다."""
    admin, me, sid, ids, _ = running
    registry.conns.pop(me["id"], None)  # 끊긴 바로 그 순간. 솔버는 내 PC에서 계속 돈다
    assert client.get(f"/api/studies/{sid}", headers=member).json()["stalled"] == 3

    r = client.post(f"/api/studies/{sid}/stop", headers=member, json={})
    assert r.status_code == 403
    assert "실행한 사람만" in r.json()["detail"] and "이 작업을 내 PC에서 이어서 하기" in r.json()["detail"] and "→" in r.json()["detail"]
    assert _case(client, admin, sid, ids[0])["status"] == "running"

    # 다시 돌리기로 넘겨받는 길도 막는다. 실행자가 바뀌면 다시 연결될 때 같은 솔버가 끝난다
    r = client.post(f"/api/studies/{sid}/run", headers=member, json={"case_ids": ids[:1]})
    assert r.status_code == 403 and "이 작업을 내 PC에서 이어서 하기" in r.json()["detail"]
    assert _case(client, admin, sid, ids[0])["run_by"] == me["id"]

    # 다시 연결: 돌던 솔버에 중지를 보내지 않고, 결과를 받는다
    back = _Conn(me["id"])
    registry.conns[me["id"]] = back
    service.reconcile_cases(back, ids)
    assert not [m for m in back.sent if m["type"] == "case.cancel"]
    service.on_case_status({"case_id": ids[0], "status": "complete", "results": {"y": 7.0}}, me["id"])
    done = _case(client, admin, sid, ids[0])
    assert done["status"] == "complete" and done["results"] == {"y": 7.0} and not done["error"]


def test_reconnect_listing_a_case_settled_without_its_owner_leaves_the_solver_running(client, running):  # noqa: F811
    """주인이 중지하지 않은 케이스는 cancelled로 적혀 있어도 솔버를 끝내지 않는다. 늦게 온 결과도 받는다."""
    admin, me, sid, ids, conn = running
    service.reconcile_cases(conn, ids[1:])  # 이 순간의 목록에 0번이 빠져 끝난 것으로 쳤다
    assert _case(client, admin, sid, ids[0])["status"] == "cancelled"

    service.reconcile_cases(conn, ids)  # 다음 연결에서 아직 돈다고 알린다
    assert not [m for m in conn.sent if m["type"] == "case.cancel"]
    service.on_case_status({"case_id": ids[0], "status": "complete", "results": {"y": 7.0}}, me["id"])
    assert _case(client, admin, sid, ids[0])["results"] == {"y": 7.0}


def test_owner_stop_during_a_drop_ends_the_solver_on_reconnect_and_says_who(client, running):  # noqa: F811
    admin, me, sid, ids, _ = running
    registry.conns.pop(me["id"], None)
    r = client.post(f"/api/studies/{sid}/stop", headers=admin, json={"case_ids": ids[:1]})
    assert r.status_code == 200 and r.json()["released"] == 1 and "꺼져" in r.json()["note"]
    stopped = _case(client, admin, sid, ids[0])
    assert stopped["status"] == "cancelled" and stopped["stopped_by"] == me["id"] and me["display_name"] in stopped["error"]

    back = _Conn(me["id"])
    registry.conns[me["id"]] = back
    service.reconcile_cases(back, ids)
    assert back.sent[-1] == {"type": "case.cancel", "case_ids": ids[:1], "extract_ids": []}
    # 중지한 뒤 돌아온 결과가 중지를 뒤집지 않는다
    service.on_case_status({"case_id": ids[0], "status": "complete", "results": {"y": 7.0}}, me["id"])
    late = _case(client, admin, sid, ids[0])
    assert late["status"] == "cancelled" and not late["results"]

    # 다시 돌리면 중지 표시가 사라지고 결과를 받는다
    assert client.post(f"/api/studies/{sid}/run", headers=admin, json={"case_ids": ids[:1]}).json() == {"queued": 1}
    assert _case(client, admin, sid, ids[0])["stopped_by"] is None
    service.reconcile_cases(back, ids)
    assert not [m for m in back.sent[1:] if m["type"] == "case.cancel"]
    service.on_case_status({"case_id": ids[0], "status": "complete", "results": {"y": 8.0}}, me["id"])
    assert _case(client, admin, sid, ids[0])["results"] == {"y": 8.0}


def test_owner_stop_during_a_drop_keeps_the_previous_result(client, running):  # noqa: F811
    """이전 값으로 되돌리면 상태가 complete라 cancelled만 보던 정리가 솔버를 놓치고, 늦은 결과가 지킨 값을 덮었다."""
    admin, me, sid, ids, conn = running
    service.on_case_status({"case_id": ids[0], "status": "complete", "results": {"y": 1.0}}, me["id"])
    assert client.post(f"/api/studies/{sid}/run", headers=admin, json={"case_ids": ids[:1]}).json() == {"queued": 1}
    service.on_case_status({"case_id": ids[0], "status": "running"}, me["id"])
    registry.conns.pop(me["id"], None)
    assert client.post(f"/api/studies/{sid}/stop", headers=admin, json={"case_ids": ids[:1]}).json()["released"] == 1
    assert _case(client, admin, sid, ids[0])["results"] == {"y": 1.0}

    back = _Conn(me["id"])
    registry.conns[me["id"]] = back
    service.reconcile_cases(back, ids)
    assert ids[0] in back.sent[-1]["case_ids"]
    service.on_case_status({"case_id": ids[0], "status": "complete", "results": {"y": 9.0}}, me["id"])
    assert _case(client, admin, sid, ids[0])["results"] == {"y": 1.0}


def test_stop_sent_on_a_half_dead_link_is_sent_again_on_reconnect(client, running):  # noqa: F811
    """연결이 있어 보여도 반쯤 끊겼으면 중지가 사라진다. 다시 연결될 때 다시 보내 주인의 중지를 잃지 않는다."""
    admin, me, sid, ids, _ = running
    assert client.post(f"/api/studies/{sid}/stop", headers=admin, json={"case_ids": ids[:1]}).json()["stopping"] == 1
    back = _Conn(me["id"])
    registry.conns[me["id"]] = back
    service.reconcile_cases(back, ids)
    assert back.sent[-1] == {"type": "case.cancel", "case_ids": ids[:1], "extract_ids": []}
    service.on_case_status({"case_id": ids[0], "status": "cancelled", "error": "사용자가 중지했습니다"}, me["id"])
    assert me["display_name"] in _case(client, admin, sid, ids[0])["error"]


def test_reconnect_asks_the_pc_before_calling_a_case_cancelled(client, running):  # noqa: F811
    """결과를 전하기 전에 Agent가 다시 시작했다. PC의 case.json이 답이다. 묻지 않고 cancelled로 바꾸면 끝난 해석이 중지로 보인다."""
    admin, me, sid, ids, conn = running
    service.on_case_status({"case_id": ids[2], "status": "complete", "results": {"y": 1.0}}, me["id"])
    assert client.post(f"/api/studies/{sid}/run", headers=admin, json={"case_ids": ids[2:]}).json() == {"queued": 1}  # 이전 값을 지키는 다시 돌리기
    service.reconcile_cases(conn, [], can_settle=True)
    asked = conn.sent[-1]
    assert asked["type"] == "case.settle" and [c["case_id"] for c in asked["cases"] if c["study_id"] == sid] == ids[:2]  # 앞 시험의 케이스가 섞일 수 있다
    assert _case(client, admin, sid, ids[0])["status"] == "running" and _case(client, admin, sid, ids[1])["status"] == "queued"
    assert not [m for m in conn.sent if m["type"] == "case.cancel"]
    # 옛 결과와 가릴 수 없는 다시 돌리기는 지켜 둔 값으로 되돌린다
    kept = _case(client, admin, sid, ids[2])
    assert kept["status"] == "complete" and kept["results"] == {"y": 1.0}

    service.on_case_status({"case_id": ids[0], "status": "complete", "results": {"y": 2.0}}, me["id"])
    service.on_case_status({"case_id": ids[1], "status": "cancelled", "error": "끝난 결과가 없습니다"}, me["id"])
    assert _case(client, admin, sid, ids[0])["status"] == "complete" and _case(client, admin, sid, ids[0])["results"] == {"y": 2.0}
    assert _case(client, admin, sid, ids[1])["status"] == "cancelled"
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["running"] is False


def test_reconnect_of_an_old_agent_still_settles_its_cases(client, running):  # noqa: F811
    """옛 Agent는 case.settle을 모른다. 물으면 답이 오지 않아 '실행 중'이 영원히 남는다."""
    admin, _, sid, ids, conn = running
    service.reconcile_cases(conn, [])
    assert not [m for m in conn.sent if m["type"] == "case.settle"]
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["counts"] == {"cancelled": 3}


def test_pc_notice_is_shown_to_that_person_and_clears_itself(client, running, member):  # noqa: F811
    admin, me, sid, ids, _ = running
    text = "cases.csv를 갱신하지 못했습니다. 해석 결과는 저장했습니다 → 파일을 닫으면 다음 케이스 때 함께 맞춥니다."
    service.on_case_status({"case_id": ids[0], "status": "complete", "results": {"y": 1.0}, "notice": text}, me["id"])
    assert _case(client, admin, sid, ids[0])["status"] == "complete" and not _case(client, admin, sid, ids[0])["error"]  # 케이스 오류가 아니다
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["pc_notice"] == {"by": me["id"], "text": text}
    service.on_case_status({"case_id": ids[1], "status": "complete", "results": {"y": 2.0}, "notice": None}, me["id"])
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["pc_notice"] is None


def test_stop_extraction_keeps_results(client, running):  # noqa: F811
    """Output 추출을 멈춰도 해석 결과는 그대로다"""
    admin, _, sid, ids, conn = running
    service.on_case_status({"case_id": ids[1], "status": "complete", "results": {"y": 1.0}})
    with service._batch_lock:
        service._extracting.setdefault(sid, set()).add(ids[1])
    try:
        assert client.post(f"/api/studies/{sid}/stop", headers=admin, json={"case_ids": [ids[1]]}).json()["stopping"] == 1
        assert conn.sent[-1] == {"type": "case.cancel", "case_ids": [], "extract_ids": [ids[1]]}
        service.on_case_status({"case_id": ids[1], "status": "complete", "extract": True, "results": {}, "error": "Output 추출 실패: 사용자가 중지했습니다"})
        case = next(c for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json() if c["id"] == ids[1])
        assert case["status"] == "complete" and case["results"] == {"y": 1.0}
        assert client.get(f"/api/studies/{sid}", headers=admin).json()["extracting"] == []
    finally:
        with service._batch_lock:
            service._extracting.pop(sid, None)
