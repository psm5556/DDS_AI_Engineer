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
    admin, _, sid, ids, conn = running
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["running"] is True
    r = client.post(f"/api/studies/{sid}/stop", headers=admin, json={})
    assert r.status_code == 200 and r.json() == {"stopping": 3, "others": 0}
    assert conn.sent[-1] == {"type": "case.cancel", "case_ids": ids, "extract_ids": []}

    # PC Agent가 끝낸 뒤 알린다. 중지는 끝난 상태라 '실행 중'이 풀린다
    for case_id in ids:
        service.on_case_status({"case_id": case_id, "status": "cancelled", "error": "사용자가 중지했습니다"})
    st = client.get(f"/api/studies/{sid}", headers=admin).json()
    assert st["running"] is False and st["counts"] == {"cancelled": 3}
    assert {c["error"] for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json()} == {"사용자가 중지했습니다"}

    # 설정을 고친 뒤 다시 돌릴 수 있다
    assert client.post(f"/api/studies/{sid}/run", headers=admin, json={"case_ids": ids[:1]}).json() == {"queued": 1}
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["counts"] == {"queued": 1, "cancelled": 2}


def test_stop_selected_cases_only(client, running):  # noqa: F811
    admin, _, sid, ids, conn = running
    assert client.post(f"/api/studies/{sid}/stop", headers=admin, json={"case_ids": ids[1:2]}).json()["stopping"] == 1
    assert conn.sent[-1]["case_ids"] == ids[1:2]


def test_stop_without_agent_tells_what_to_do(client, running):  # noqa: F811
    admin, me, sid, _, _ = running
    registry.conns.pop(me["id"], None)
    r = client.post(f"/api/studies/{sid}/stop", headers=admin, json={})
    assert r.status_code == 409 and "PC Agent" in r.json()["detail"] and "→" in r.json()["detail"]


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
