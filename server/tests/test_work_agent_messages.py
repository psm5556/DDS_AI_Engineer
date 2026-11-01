"""서버 쪽 Task 폴더 규칙: 옛 Agent·새 Agent에게 무엇을 보내는가. 끊기지 않게 짝을 맞춘다."""
from types import SimpleNamespace

import pytest

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402

from server.app import models, work_service  # noqa: E402
from server.app.agent.hub import hub  # noqa: E402
from server.app.agent.link import registry, work_fields  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.routers.agents import work_names_message  # noqa: E402

_n = [0]


class FakeConn:
    def __init__(self, user_id: int, folders: bool):
        self.user_id = user_id
        self.info = {"work_folders": 1} if folders else {}
        self.sent: list[dict] = []
        self.boot = "b"

    def send(self, msg):
        self.sent.append(msg)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def team(client):
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    a, ua = _auth(client, f"wf_a{_n[0]}", admin)
    pid = client.post("/api/projects", json={"name": f"부품{_n[0]}", "description": ""}, headers=a).json()["id"]
    return {"a": a, "ua": ua, "pid": pid}


def _work(client, team, name="일"):
    r = client.post(f"/api/projects/{team['pid']}/works", json={"name": name, "agent": "demo"}, headers=team["a"])
    assert r.status_code == 200, r.text
    return r.json()


def _session_work(conn, chat_id, old=None):
    with SessionLocal() as db:
        return hub._session_work(db, conn, db.get(models.ChatSession, chat_id), old)


def test_new_agent_gets_the_task(client, team):
    w = _work(client, team)
    work, note = _session_work(FakeConn(team["ua"]["id"], True), w["active_chat_id"])
    assert note is None and work["uid"] == w["uid"] and work["seq"] == w["seq"] and work["legacy"] is False and "carry" in work


def test_old_agent_first_start_uses_project_folder_and_says_once(client, team):
    w = _work(client, team)
    conn = FakeConn(team["ua"]["id"], False)
    work, note = _session_work(conn, w["active_chat_id"])
    assert work is None and "업데이트" in note and "→" in note
    with SessionLocal() as db:
        assert db.get(models.Work, w["id"]).legacy is True
    assert _session_work(conn, w["active_chat_id"]) == (None, None), "안내는 한 번만"
    # 나중에 새 Agent로 와도 이 Task는 세션과 결과가 Project 폴더에 있다
    work, note = _session_work(FakeConn(team["ua"]["id"], True), w["active_chat_id"])
    assert work["legacy"] is True and note is None


def test_old_agent_on_started_task_keeps_task_as_is(client, team):
    """새 Agent로 시작한 Task를 옛 Agent PC에서 이어도 표시를 바꾸지 않는다. 그 폴더는 다른 PC에 그대로 있다."""
    w = _work(client, team)
    with SessionLocal() as db:
        db.get(models.ChatSession, w["active_chat_id"]).agent_session_id = "s1"
        db.commit()
    assert _session_work(FakeConn(team["ua"]["id"], False), w["active_chat_id"], "s1") == (None, None)
    with SessionLocal() as db:
        assert db.get(models.Work, w["id"]).legacy is False


def test_work_fields_skip_old_agent():
    w = SimpleNamespace(folder_view=lambda: {"id": 1})
    assert work_fields(FakeConn(1, True), w) == {"work": {"id": 1}}
    assert work_fields(FakeConn(1, False), w) == {} and work_fields(None, w) == {} and work_fields(FakeConn(1, True), None) == {}


def test_rename_tells_new_agents_only(client, team):
    w = _work(client, team, "처음")
    uid = team["ua"]["id"]
    conn = FakeConn(uid, True)
    registry.conns[uid] = conn
    try:
        r = client.put(f"/api/works/{w['id']}", json={"name": "새 이름", "version": w["version"]}, headers=team["a"])
        assert r.status_code == 200, r.text
        msg = next(m for m in conn.sent if m["type"] == "work.renamed")
        assert msg["work"]["name"] == "새 이름" and msg["work"]["uid"] == w["uid"] and msg["project_id"] == team["pid"]
        conn.info = {}
        conn.sent.clear()
        client.put(f"/api/works/{w['id']}", json={"name": "또 새 이름"}, headers=team["a"])
        assert not [m for m in conn.sent if m["type"] == "work.renamed"], "옛 Agent는 이 메시지를 모른다"
    finally:
        registry.conns.pop(uid, None)


def test_names_on_connect_are_mine_and_not_legacy(client, team):
    mine = _work(client, team, "내 것")
    old = _work(client, team, "옛 Agent로 시작한 것")
    with SessionLocal() as db:
        db.get(models.Work, old["id"]).legacy = True
        db.commit()
        msg = work_names_message(db, db.get(models.User, team["ua"]["id"]))
    uids = {row["work"]["uid"] for row in msg["works"]}
    assert mine["uid"] in uids and old["uid"] not in uids
    assert all(row["project_uid"] and row["project_id"] for row in msg["works"])


def test_ai_names_task_once(client, team):
    w = _work(client, team, None)
    h = {**team["a"], "X-DDS-Work": str(w["id"])}
    url = f"/api/projects/{team['pid']}/works/current"
    r = client.put(url, json={"name": "1차 경량화"}, headers=h).json()
    assert r["changed"] is True and r["name"] == "1차 경량화"
    r = client.put(url, json={"name": "또 바꿈"}, headers=h).json()
    assert r["changed"] is False and r["name"] == "1차 경량화" and "force" in r["note"]
    r = client.put(url, json={"name": "사용자 요청", "force": True}, headers=h).json()
    assert r["changed"] is True and r["name"] == "사용자 요청"


def test_ai_does_not_rename_a_persons_name(client, team):
    r = client.post(f"/api/projects/{team['pid']}/works", json={"name": "사람이 지음", "name_auto": False, "agent": "demo"}, headers=team["a"]).json()
    h = {**team["a"], "X-DDS-Work": str(r["id"])}
    got = client.put(f"/api/projects/{team['pid']}/works/current", json={"name": "AI가 지음"}, headers=h).json()
    assert got["changed"] is False and got["name"] == "사람이 지음"


def test_legacy_marked_task_does_not_take_old_rows(client, team):
    """옛 Agent로 시작한 Task도 legacy다. 이전(이관)이 빈 행을 그 Task에 몰아넣으면 안 된다."""
    w = _work(client, team)
    with SessionLocal() as db:
        db.get(models.Work, w["id"]).legacy = True
        db.add(models.Flow(project_id=team["pid"], title="옛 흐름", goal="", nodes=[], edges=[], status="review"))
        db.commit()
    work_service.migrate_works()
    with SessionLocal() as db:
        f = db.query(models.Flow).filter_by(project_id=team["pid"], title="옛 흐름").one()
        assert f.work_id != w["id"] and db.get(models.Work, f.work_id).seq == 0
