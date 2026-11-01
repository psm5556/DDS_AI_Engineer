"""Project > Task. 서버가 규칙을 강제한다: 번호, 범위, 합산 상태, 헤더 없는 옛 Agent, 남의 Task, 삭제.
상태 × 사건을 미리 정해 막는다. AI가 무엇을 하든 결과가 옆 Task로 새면 안 된다."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402

from server.app import models  # noqa: E402
from server.app.agent.hub import TurnState, hub  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.study import service  # noqa: E402
from server.app.ws import _work_of  # noqa: E402

NODES = [{"id": "n", "label": "단계", "type": "doe"}]
_n = [0]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def team(client):
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    a, ua = _auth(client, f"wk_a{_n[0]}", admin)
    b, ub = _auth(client, f"wk_b{_n[0]}", admin)
    pid = client.post("/api/projects", json={"name": f"스파이더{_n[0]}", "description": ""}, headers=a).json()["id"]
    assert client.put(f"/api/projects/{pid}/members/{ub['id']}", json={"role": "member"}, headers=a).status_code == 200
    return {"a": a, "b": b, "ua": ua, "ub": ub, "pid": pid, "admin": admin}


def _work(client, h, pid, name=None, agent="demo"):
    r = client.post(f"/api/projects/{pid}/works", json={"name": name, "name_auto": False, "agent": agent}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _flow(client, h, pid, wid=None, title="흐름"):
    q = f"?work_id={wid}" if wid else ""
    r = client.post(f"/api/projects/{pid}/flows{q}", json={"title": title, "goal": "", "nodes": NODES, "edges": []}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _art(client, h, pid, wid, title="요약", text="x"):
    r = client.post(f"/api/projects/{pid}/artifacts?work_id={wid}", json={"kind": "markdown", "title": title, "payload": {"text": text}}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _status(client, h, pid):
    return client.get(f"/api/projects/{pid}", headers=h).json()


def test_seq_is_never_reused_and_first_name(client, team):
    a, pid = team["a"], team["pid"]
    first = client.post(f"/api/projects/{pid}/works", json={}, headers=a).json()
    assert (first["seq"], first["name"]) == (1, "첫 Task")
    second = _work(client, a, pid, "2차")
    assert second["seq"] == 2
    assert client.delete(f"/api/works/{second['id']}", headers=a).status_code == 200
    third = _work(client, a, pid, "3차")
    assert third["seq"] == 3, "지운 번호를 다시 쓰면 PC 폴더가 옛 Task와 섞인다"


def test_concurrent_create_gets_distinct_numbers(client, team):
    a, pid = team["a"], team["pid"]
    with ThreadPoolExecutor(6) as ex:
        rows = list(ex.map(lambda i: client.post(f"/api/projects/{pid}/works", json={"name": f"동시{i}"}, headers=a), range(6)))
    assert all(r.status_code == 200 for r in rows), [r.text for r in rows]
    assert len({r.json()["seq"] for r in rows}) == 6


def test_same_title_does_not_overwrite_other_work(client, team):
    """앞선 코드는 Project + 제목으로 덮어써서 새 모델의 결과가 옛 결과를 지웠다."""
    a, pid = team["a"], team["pid"]
    w1, w2 = _work(client, a, pid, "1차"), _work(client, a, pid, "2차")
    one = _art(client, a, pid, w1["id"], text="옛 값")
    two = _art(client, a, pid, w2["id"], text="새 값")
    assert one["id"] != two["id"]
    again = _art(client, a, pid, w2["id"], text="더 새 값")
    assert again["id"] == two["id"], "같은 Task·같은 제목은 그대로 갱신한다"
    only = client.get(f"/api/projects/{pid}/artifacts?work_id={w1['id']}", headers=a).json()
    assert [x["payload"]["text"] for x in only] == ["옛 값"]


def test_lists_are_scoped_to_the_work(client, team):
    a, pid = team["a"], team["pid"]
    w1, w2 = _work(client, a, pid, "1차"), _work(client, a, pid, "2차")
    f1 = _flow(client, a, pid, w1["id"])
    f2 = _flow(client, a, pid, w2["id"])
    r = client.post(f"/api/projects/{pid}/studies?work_id={w2['id']}", json={"name": "s", "variables": [{"name": "x", "lower": 0, "upper": 1}], "adapter": "mock"}, headers=a)
    assert r.status_code == 200 and r.json()["work_id"] == w2["id"]
    assert [x["id"] for x in client.get(f"/api/projects/{pid}/flows?work_id={w1['id']}", headers=a).json()] == [f1["id"]]
    assert [x["id"] for x in client.get(f"/api/projects/{pid}/flows?work_id={w2['id']}", headers=a).json()] == [f2["id"]]
    assert client.get(f"/api/projects/{pid}/studies?work_id={w1['id']}", headers=a).json() == []
    assert len(client.get(f"/api/projects/{pid}/flows?all_works=true", headers=a).json()) == 2
    # 다른 Project의 Task를 대면 조용히 넘어가지 않는다
    other = client.post("/api/projects", json={"name": "다른", "description": ""}, headers=a).json()["id"]
    assert client.get(f"/api/projects/{other}/flows?work_id={w1['id']}", headers=a).status_code == 404


def test_report_is_scoped_and_new_results_ignore_other_work(client, team):
    a, pid = team["a"], team["pid"]
    w1, w2 = _work(client, a, pid, "1차"), _work(client, a, pid, "2차")
    _art(client, a, pid, w1["id"])
    r = client.post(f"/api/projects/{pid}/reports?work_id={w1['id']}", json={"title": "보고서", "body": "## 본문"}, headers=a)
    assert r.status_code == 200 and r.json()["work_id"] == w1["id"]
    _art(client, a, pid, w2["id"], title="옆 Task 결과")
    latest = client.get(f"/api/projects/{pid}/reports/latest?work_id={w1['id']}", headers=a).json()
    assert latest["new_results"] == 0, "옆 Task의 결과 때문에 이 보고서를 다시 쓰라고 하면 안 된다"
    assert client.get(f"/api/projects/{pid}/reports/latest?work_id={w2['id']}", headers=a).json() is None
    ctx = client.get(f"/api/projects/{pid}/report-context?work_id={w2['id']}", headers=a).json()
    assert [x["title"] for x in ctx["results"]] == ["옆 Task 결과"]


def test_project_status_keeps_earlier_review(client, team):
    """앞선 코드는 마지막 흐름 하나만 봐서 앞선 승인 대기가 사라졌다."""
    a, b, pid = team["a"], team["b"], team["pid"]
    w1, w2 = _work(client, a, pid, "1차"), _work(client, a, pid, "2차")
    _flow(client, a, pid, w1["id"], "승인 기다림")
    done = _flow(client, a, pid, w2["id"], "끝남")
    assert client.put(f"/api/flows/{done['id']}/nodes/n", json={"status": "complete"}, headers=a).status_code == 200
    me = _status(client, a, pid)
    assert me["flow_status"] == "review" and me["flow_title"] == "승인 기다림"
    rows = {w["id"]: w for w in me["works"]}
    assert rows[w1["id"]]["status"] == "review"
    assert rows[w2["id"]]["status"] == "complete"
    # 남의 승인 대기는 내 Project 줄에 올라오지 않는다. 줄에는 그대로 보인다
    them = _status(client, b, pid)
    assert them["flow_status"] != "review"
    assert {w["id"]: w["status"] for w in them["works"]}[w1["id"]] == "review"


def test_failed_alerts_only_until_redone(client, team):
    a, pid = team["a"], team["pid"]
    w = _work(client, a, pid, "1차")
    f = _flow(client, a, pid, w["id"], "실패")
    client.put(f"/api/flows/{f['id']}/status", json={"status": "approved", "version": f["version"]}, headers=a)
    assert client.put(f"/api/flows/{f['id']}/nodes/n", json={"status": "failed"}, headers=a).status_code == 200
    assert _status(client, a, pid)["flow_status"] == "failed"
    redo = _flow(client, a, pid, w["id"], "다시")
    client.put(f"/api/flows/{redo['id']}/nodes/n", json={"status": "complete"}, headers=a)
    assert _status(client, a, pid)["flow_status"] == "complete", "고쳐서 끝낸 Task에 실패 표시가 남으면 뜻이 없어진다"


def test_others_running_shows_on_project_line(client, team):
    a, b, pid = team["a"], team["b"], team["pid"]
    w = _work(client, a, pid, "1차")
    f = _flow(client, a, pid, w["id"])
    client.put(f"/api/flows/{f['id']}/status", json={"status": "approved", "version": f["version"]}, headers=a)
    assert _status(client, b, pid)["flow_status"] in ("approved", "in_progress")


def test_check_flow_rejects_other_works_flow(client, team):
    a, pid = team["a"], team["pid"]
    w1, w2 = _work(client, a, pid, "1차"), _work(client, a, pid, "2차")
    f1 = _flow(client, a, pid, w1["id"])
    r = client.post(f"/api/projects/{pid}/artifacts?work_id={w2['id']}", json={"kind": "markdown", "title": "t", "payload": {}, "flow_id": f1["id"]}, headers=a)
    assert r.status_code == 400 and "다른 Task" in r.json()["detail"]
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            service.check_flow(db, pid, f1["id"], w2["id"])
        service.check_flow(db, pid, f1["id"], w1["id"])


def test_header_scopes_and_foreign_header_is_ignored(client, team):
    a, pid = team["a"], team["pid"]
    w1, w2 = _work(client, a, pid, "1차"), _work(client, a, pid, "2차")
    f = client.post(f"/api/projects/{pid}/flows", json={"title": "헤더", "goal": "", "nodes": NODES, "edges": []}, headers={**a, "X-DDS-Work": str(w1["id"])}).json()
    assert f["work_id"] == w1["id"]
    # 헤더는 다른 Project 호출에도 붙어 온다. 맞지 않거나 숫자가 아니면 끊지 않고 다음 규칙으로 넘어간다
    other = client.post("/api/projects", json={"name": "다른2", "description": ""}, headers=a).json()["id"]
    assert client.post(f"/api/projects/{other}/flows", json={"title": "x", "goal": "", "nodes": NODES, "edges": []}, headers={**a, "X-DDS-Work": str(w1["id"])}).status_code == 200
    assert client.post(f"/api/projects/{pid}/flows", json={"title": "y", "goal": "", "nodes": NODES, "edges": []}, headers={**a, "X-DDS-Work": "abc"}).json()["work_id"] == w2["id"]


def test_old_agent_without_header_follows_active_turn(client, team):
    """옛 PC Agent는 헤더를 보내지 않는다. 그 사람이 지금 돌리는 Task로 들어가고, 끊기지 않는다."""
    a, pid, uid = team["a"], team["pid"], team["ua"]["id"]
    w1, w2 = _work(client, a, pid, "1차"), _work(client, a, pid, "2차")
    with SessionLocal() as db:
        chat = db.query(models.ChatSession).filter_by(work_id=w1["id"]).first()
        chat_id = chat.id
    st = hub.chats[chat_id] = TurnState(chat_id=chat_id, project_id=pid, user_id=uid, work_id=w1["id"], agent="demo", busy=True, started_at=1.0)
    try:
        got = client.post(f"/api/projects/{pid}/flows", json={"title": "옛 Agent", "goal": "", "nodes": NODES, "edges": []}, headers=a).json()
        assert got["work_id"] == w1["id"], "최근 Task(2차)가 아니라 지금 돌리는 Task(1차)에 들어가야 한다"
        listed = client.get(f"/api/projects/{pid}/flows", headers=a).json()
        assert [x["id"] for x in listed] == [got["id"]], "AI가 읽는 목록도 지금 돌리는 Task로 좁아진다"
        with SessionLocal() as db:
            assert db.get(models.Flow, got["id"]).chat_id == chat_id
    finally:
        st.busy = False
        hub.chats.pop(chat_id, None)
    assert client.post(f"/api/projects/{pid}/flows", json={"title": "끝난 뒤", "goal": "", "nodes": NODES, "edges": []}, headers=a).json()["work_id"] == w2["id"]


def test_project_without_works_is_not_filled_by_reads(client, team):
    a, pid = team["a"], team["pid"]
    assert client.get(f"/api/projects/{pid}/flows", headers=a).json() == []
    assert client.get(f"/api/projects/{pid}", headers=a).json()["works"] == [], "조회로는 Task를 만들지 않는다"
    r = client.post(f"/api/projects/{pid}/flows", json={"title": "스크립트", "goal": "", "nodes": NODES, "edges": []}, headers=a).json()
    works = client.get(f"/api/projects/{pid}/works", headers=a).json()
    assert [w["id"] for w in works] == [r["work_id"]], "Task가 없는 Project에 스크립트가 쓰면 NULL로 남기지 않고 내 Task를 만든다"


def test_others_work_is_read_only_for_prompt(client, team):
    a, b, pid = team["a"], team["b"], team["pid"]
    w = _work(client, a, pid, "1차")
    assert client.get(f"/api/works/{w['id']}", headers=b).status_code == 200
    r = client.post(f"/api/works/{w['id']}/chat", json={"agent": "demo"}, headers=b)
    assert r.status_code == 403 and "→" in r.json()["detail"]
    chat = client.get(f"/api/works/{w['id']}", headers=a).json()["active_chat_id"]
    r = client.post(f"/api/sessions/{chat}/prompt", json={"text": "해 줘"}, headers=b)
    assert r.status_code == 403 and "보기만" in r.json()["detail"]
    assert client.post(f"/api/projects/{pid}/sessions", json={"agent": "demo", "work_id": w["id"]}, headers=b).status_code == 403
    # 남의 Task를 보는 사람에게는 주인의 기록을 보여 준다. 말없이 내 Task를 만들지 않는다
    seen = client.get(f"/api/works/{w['id']}", headers=b).json()
    assert seen["mine"] is False and seen["active_chat_id"] == chat
    assert len(client.get(f"/api/projects/{pid}/works", headers=b).json()) == 1


def test_rename_permissions_and_name_auto(client, team):
    a, b, pid, admin = team["a"], team["b"], team["pid"], team["admin"]
    w = _work(client, a, pid, "1차")
    assert client.put(f"/api/works/{w['id']}", json={"name": "남이 바꿈"}, headers=b).status_code == 403
    r = client.put(f"/api/works/{w['id']}", json={"name": "바꿈"}, headers=a)
    assert r.status_code == 200 and r.json()["name"] == "바꿈"
    assert client.put(f"/api/works/{w['id']}", json={"name": "또", "version": 1}, headers=a).status_code == 409
    assert client.put(f"/api/works/{w['id']}", json={"name": "관리자"}, headers=admin).status_code == 200
    with SessionLocal() as db:
        assert db.get(models.Work, w["id"]).name_auto is False


def test_delete_removes_contents_and_keeps_siblings(client, team):
    a, pid = team["a"], team["pid"]
    w1, w2 = _work(client, a, pid, "1차"), _work(client, a, pid, "2차")
    f = _flow(client, a, pid, w1["id"])
    art = _art(client, a, pid, w1["id"])
    keep = _art(client, a, pid, w2["id"], title="남는 것")
    study = client.post(f"/api/projects/{pid}/studies?work_id={w1['id']}", json={"name": "지울 스터디", "variables": [{"name": "x", "lower": 0, "upper": 1}], "adapter": "mock"}, headers=a).json()
    assert client.delete(f"/api/works/{w1['id']}", headers=team["b"]).status_code == 403
    assert client.delete(f"/api/works/{w1['id']}", headers=a).status_code == 200
    with SessionLocal() as db:
        assert db.get(models.Work, w1["id"]) is None
        assert db.get(models.Flow, f["id"]) is None
        assert db.get(models.Artifact, art["id"]) is None
        assert db.get(models.Study, study["id"]) is None
        assert db.query(models.ChatSession).filter_by(work_id=w1["id"]).count() == 0
        assert db.get(models.Artifact, keep["id"]) is not None
    # Task가 0개가 된 Project는 남는다
    assert client.delete(f"/api/works/{w2['id']}", headers=a).status_code == 200
    got = client.get(f"/api/projects/{pid}", headers=a)
    assert got.status_code == 200 and got.json()["works"] == []


def test_delete_blocked_while_busy_or_running(client, team):
    a, pid, uid = team["a"], team["pid"], team["ua"]["id"]
    w = _work(client, a, pid, "1차")
    chat = client.get(f"/api/works/{w['id']}", headers=a).json()["active_chat_id"]
    hub.chats[chat] = TurnState(chat_id=chat, project_id=pid, user_id=uid, work_id=w["id"], agent="demo", busy=True, started_at=1.0)
    try:
        r = client.delete(f"/api/works/{w['id']}", headers=a)
        assert r.status_code == 409 and "→" in r.json()["detail"]
    finally:
        hub.chats.pop(chat, None)
    study = client.post(f"/api/projects/{pid}/studies?work_id={w['id']}", json={"name": "돌림", "variables": [{"name": "x", "lower": 0, "upper": 1}], "adapter": "mock"}, headers=a).json()
    service._batches[study["id"]] = {1}
    try:
        r = client.delete(f"/api/works/{w['id']}", headers=a)
        assert r.status_code == 409 and "중지" in r.json()["detail"]
        assert {x["id"]: x["status"] for x in client.get(f"/api/projects/{pid}/works", headers=a).json()}[w["id"]] == "in_progress"
    finally:
        service._batches.pop(study["id"], None)


def test_project_delete_takes_works_along(client, team):
    a, pid = team["a"], team["pid"]
    w = _work(client, a, pid, "1차")
    _flow(client, a, pid, w["id"])
    assert client.delete(f"/api/projects/{pid}", headers=a).status_code == 200
    with SessionLocal() as db:
        assert db.get(models.Work, w["id"]) is None


def test_attachment_gets_work_on_send_only(client, team):
    a, pid = team["a"], team["pid"]
    w = _work(client, a, pid, "1차")
    up = client.post(f"/api/projects/{pid}/attachments", files={"file": ("spider_m9.prt", b"abc")}, headers=a).json()
    assert up["work_id"] is None, "놓을 때는 정하지 않는다. 그래야 새 Task로 옮겨 갈 수 있다"
    assert client.get(f"/api/projects/{pid}/attachments?work_id={w['id']}", headers=a).json() == []
    with SessionLocal() as db:  # 보내는 쪽 규칙은 router에 있다. PC Agent 없이도 볼 수 있게 직접 흉내 낸다
        row = db.get(models.Attachment, up["id"])
        row.work_id = w["id"]
        db.commit()
    brief = client.get(f"/api/projects/{pid}/works", headers=a).json()[0]
    assert brief["model_file"] == "spider_m9.prt"


def test_search_finds_work_file_and_result(client, team):
    a, b, pid = team["a"], team["b"], team["pid"]
    w = _work(client, a, pid, "경량화고유이름")
    _art(client, a, pid, w["id"], title="최대응력고유결과")
    up = client.post(f"/api/projects/{pid}/attachments", files={"file": ("spider_uniq77.prt", b"abc")}, headers=a).json()
    with SessionLocal() as db:
        db.get(models.Attachment, up["id"]).work_id = w["id"]
        db.commit()
    assert client.get("/api/works/search?q=경량화고유", headers=a).json()["works"][0]["matched"] == "name"
    hit = client.get("/api/works/search?q=uniq77", headers=a).json()["works"][0]
    assert hit["matched"] == "file" and hit["work_id"] == w["id"] and hit["model_file"] == "spider_uniq77.prt"
    assert client.get("/api/works/search?q=최대응력고유", headers=a).json()["works"][0]["matched"] == "result"
    assert pid in client.get("/api/works/search?q=스파이더", headers=b).json()["projects"]
    admin, _ = _auth(client, "admin")
    outsider, _ = _auth(client, f"wk_out{_n[0]}", admin)
    assert client.get("/api/works/search?q=uniq77", headers=outsider).json()["works"] == [], "멤버가 아닌 Project는 찾아지면 안 된다"


def test_table_job_goes_to_my_work_or_a_new_one(client, team):
    a, b, pid = team["a"], team["b"], team["pid"]
    w = _work(client, a, pid, "1차")
    text = "x\ty\tz\n" + "\n".join(f"{i}\t{i * i % 7}\t{i * 3 + (i * i % 5)}" for i in range(1, 12))
    outputs = [{"name": "z", "unit": ""}]

    def run(h, **kw):
        import time
        job = client.post("/api/table-jobs", json={"text": text + kw.pop("extra", ""), "outputs": outputs, "name": "응력", "project_id": pid, **kw}, headers=h).json()
        for _ in range(240):
            job = client.get(f"/api/table-jobs/{job['id']}", headers=h).json()
            if job["status"] != "running":
                return job
            time.sleep(0.25)
        raise AssertionError("표 작업이 끝나지 않았습니다")

    mine = run(a, work_id=w["id"])
    assert mine["status"] == "done" and mine["work_id"] == w["id"]
    # 남의 Task를 보다 붙여 넣으면 내 새 Task에 들어간다. 남의 Task에는 넣지 않는다
    other = run(b, work_id=w["id"], extra="\n50\t1\t2")
    assert other["status"] == "done" and other["work_id"] != w["id"]
    with SessionLocal() as db:
        new = db.get(models.Work, other["work_id"])
        assert new.owner_id == team["ub"]["id"] and new.name == "표로 만든 Simulator"
    # 같은 표를 새 Task 화면에서 다시 붙여 넣으면 빈 Task를 남기지 않고 그 스터디가 있는 Task를 연다
    before = len(client.get(f"/api/projects/{pid}/works", headers=a).json())
    again = run(a)
    assert again["reused"] is True and again["work_id"] == w["id"]
    assert len(client.get(f"/api/projects/{pid}/works", headers=a).json()) == before
    # 표를 못 받으면 아무것도 남지 않는다
    bad = client.post("/api/table-jobs", json={"text": "a\tb\n1\t2\n3\t4\n5\t6\n", "outputs": [{"name": "없는 열", "unit": ""}], "name": "나쁨", "project_id": pid}, headers=a).json()
    import time
    for _ in range(100):
        bad = client.get(f"/api/table-jobs/{bad['id']}", headers=a).json()
        if bad["status"] != "running":
            break
        time.sleep(0.1)
    assert bad["status"] == "failed" and bad["work_id"] is None
    assert len(client.get(f"/api/projects/{pid}/works", headers=a).json()) == before


def test_events_carry_work_id(client, team):
    a, pid = team["a"], team["pid"]
    w = _work(client, a, pid, "1차")
    f = _flow(client, a, pid, w["id"])
    art = _art(client, a, pid, w["id"])
    study = client.post(f"/api/projects/{pid}/studies?work_id={w['id']}", json={"name": "s", "variables": [{"name": "x", "lower": 0, "upper": 1}], "adapter": "mock"}, headers=a).json()
    with SessionLocal() as db:
        for msg in ({"type": "flow_updated", "data": {"id": f["id"]}}, {"type": "artifact_updated", "data": {"id": art["id"]}},
                    {"type": "study_updated", "data": {"id": study["id"]}}, {"type": "case_updated", "data": {"id": 1, "study_id": study["id"]}}):
            assert _work_of(db, msg) == w["id"], msg
        assert _work_of(db, {"type": "project_updated", "data": {"id": pid}}) is None
        assert _work_of(db, {"type": "flow_updated", "data": {"id": 99999999}}) is None


def test_flow_nudge_ignores_new_proposal_in_other_work(client, team):
    """옆 Task의 새 제안이 이 Task의 승인된 흐름을 멈추면 안 된다."""
    a, pid, uid = team["a"], team["pid"], team["ua"]["id"]
    w1, w2 = _work(client, a, pid, "1차"), _work(client, a, pid, "2차")
    f = _flow(client, a, pid, w1["id"])
    client.put(f"/api/flows/{f['id']}/status", json={"status": "approved", "version": f["version"]}, headers=a)
    _flow(client, a, pid, w2["id"], "옆 Task 제안")
    with SessionLocal() as db:
        chat = db.query(models.ChatSession).filter_by(work_id=w1["id"]).first().id
    st = TurnState(chat_id=chat, project_id=pid, user_id=uid, work_id=w1["id"], agent="demo")
    hub._flow_turn_over(st, f["id"])
    assert client.get(f"/api/flows/{f['id']}", headers=a).json()["status"] != "stopped"


def test_approval_chat_must_be_in_same_work(client, team):
    a, pid = team["a"], team["pid"]
    w1, w2 = _work(client, a, pid, "1차"), _work(client, a, pid, "2차")
    other_chat = client.get(f"/api/works/{w2['id']}", headers=a).json()["active_chat_id"]
    f = _flow(client, a, pid, w1["id"])
    r = client.put(f"/api/flows/{f['id']}/status", json={"status": "approved", "version": f["version"], "session_id": other_chat}, headers=a).json()
    with SessionLocal() as db:
        assert db.get(models.Flow, r["id"]).chat_id != other_chat, "옆 Task의 AI에게 승인을 전하면 엉뚱한 AI가 시작한다"
