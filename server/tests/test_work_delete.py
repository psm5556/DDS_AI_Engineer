"""Task 삭제 세부: 공유 링크, 끊긴 Agent, 번호 재사용 없음, 서버 파일 정리. PC 폴더는 PC Agent에 아무 말도 하지 않아 그대로 남는다."""
import numpy as np
import pytest

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402

from server.app import models  # noqa: E402
from server.app.agent.link import registry  # noqa: E402
from server.app.config import settings  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.study import recipe, service  # noqa: E402

NODES = [{"id": "n", "label": "단계", "type": "doe"}]
_n = [0]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _world(client):
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    a, ua = _auth(client, f"del_a{_n[0]}", admin)
    linked, _ = _auth(client, f"del_link{_n[0]}", admin)
    pid = client.post("/api/projects", json={"name": f"삭제{_n[0]}"}, headers=a).json()["id"]
    w = client.post(f"/api/projects/{pid}/works", json={"name": "지울 Task", "name_auto": False, "agent": "demo"}, headers=a).json()
    with SessionLocal() as db:
        s = models.Study(project_id=pid, work_id=w["id"], name=f"스터디{w['id']}", variables=[{"name": "x", "lower": 0, "upper": 1}], objectives=[{"name": "y", "goal": "min"}], constraints=[],
                         outputs=[{"name": "y"}], adapter="data", adapter_config={}, created_by=ua["id"])
        db.add(s)
        db.flush()
        for i in range(6):
            db.add(models.Case(study_id=s.id, params={"x": i / 6}, results={"y": float(i)}, status="complete", source="import", run_by=ua["id"]))
        db.commit()
        sid = s.id
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=a).json()
    client.post(f"/api/models/{m['id']}/deploy", headers=a)
    token = client.post(f"/api/models/{m['id']}/shares", json={}, headers=a).json()["token"]
    assert client.get(f"/api/shares/{token}", headers=linked).status_code == 200  # 받은 사람 목록에 들어간다
    return {"a": a, "ua": ua, "linked": linked, "pid": pid, "w": w, "sid": sid, "mid": m["id"], "token": token}


@pytest.fixture
def world(client):
    return _world(client)


def test_delete_counts_links_and_does_not_block_on_them(client, world):
    a = world["a"]
    d = client.get(f"/api/works/{world['w']['id']}", headers=a).json()
    assert d["n_simulators"] == 1 and d["n_share_links"] == 1, "지우기 전에 수를 알린다"
    assert client.delete(f"/api/works/{world['w']['id']}", headers=a).status_code == 200


def test_share_link_dies_and_receivers_list_stays_clean(client, world):
    h, token = world["linked"], world["token"]
    assert any(x["study_id"] == world["sid"] for x in client.get("/api/simulators", headers=h).json()["shared"])
    assert client.post(f"/api/shares/{token}/predict", json={"params": {"x": 0.5}}, headers=h).status_code == 200
    assert client.delete(f"/api/works/{world['w']['id']}", headers=world["a"]).status_code == 200
    assert client.post(f"/api/shares/{token}/predict", json={"params": {"x": 0.5}}, headers=h).status_code == 404
    assert client.get(f"/api/shares/{token}", headers=h).status_code == 404
    lists = client.get("/api/simulators", headers=h)
    assert lists.status_code == 200 and not any(x["study_id"] == world["sid"] for x in lists.json()["shared"])
    assert not any(x["study_id"] == world["sid"] for x in client.get("/api/simulators", headers=world["a"]).json()["mine"])
    with SessionLocal() as db:
        assert db.query(models.ShareLink).filter_by(token=token).count() == 0
        assert db.get(models.SurrogateModel, world["mid"]) is None


def test_server_files_go_too(client, world):
    a, pid, sid, mid = world["a"], world["pid"], world["sid"], world["mid"]
    with SessionLocal() as db:
        study = db.get(models.Study, sid)
        cid = study.cases[0].id
        model_path = service.model_file(db.get(models.SurrogateModel, mid).path)
        files = [service.archive_path(pid, sid, cid), service.field_path(pid, sid, cid, "mises"), recipe.recipe_path(study),
                 settings.data_dir / "training" / "samples" / f"project_{pid}" / f"study_{sid}" / f"case_{cid}__a.train.h5", settings.data_dir / "thumbnails" / f"m{mid}_f1_a.png"]
    for p in files:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    assert model_path.is_file()
    other = _world(client)
    keep = service.archive_path(other["pid"], other["sid"], 1)
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_bytes(b"x")
    assert client.delete(f"/api/works/{world['w']['id']}", headers=a).status_code == 200
    for p in [*files, model_path]:
        assert not p.exists(), p
    assert keep.exists(), "다른 Task의 파일은 그대로다"


def test_number_is_not_reused_and_project_stays(client, world):
    a, pid, w = world["a"], world["pid"], world["w"]
    assert client.delete(f"/api/works/{w['id']}", headers=a).status_code == 200
    assert client.get(f"/api/projects/{pid}", headers=a).status_code == 200 and client.get(f"/api/projects/{pid}/works", headers=a).json() == []
    assert client.post(f"/api/projects/{pid}/works", json={"name": "다음"}, headers=a).json()["seq"] == w["seq"] + 1


def test_no_message_touches_the_pc_folder_but_ai_is_ended(client, world):
    sent = []

    class Conn:
        user_id = world["ua"]["id"]
        info = {"work_folders": 1}

        def send(self, m):
            sent.append(m)
    with SessionLocal() as db:
        db.get(models.ChatSession, world["w"]["active_chat_id"]).agent_session_id = "sid-del"
        db.commit()
    registry.conns[world["ua"]["id"]] = Conn()
    try:
        assert client.delete(f"/api/works/{world['w']['id']}", headers=world["a"]).status_code == 200
    finally:
        registry.conns.pop(world["ua"]["id"], None)
    assert sent == [{"type": "session.close", "session_id": "sid-del"}], "삭제는 work.discard를 보내지 않는다. PC 폴더는 남는다"


def test_stranded_case_blocks_delete_until_stopped_without_the_pc(client, world):
    """PC가 꺼진 채 실행 중으로 남은 케이스: 서버가 다시 시작해 도는 목록을 잊었어도 막고, 중지는 PC 없이 된다."""
    a, sid = world["a"], world["sid"]
    with SessionLocal() as db:
        c = db.query(models.Case).filter_by(study_id=sid).first()
        c.status, c.source, cid = "running", "doe", c.id
        db.commit()
    assert not service.is_running(sid)
    r = client.delete(f"/api/works/{world['w']['id']}", headers=a)
    assert r.status_code == 409 and "중지" in r.json()["detail"] and "→" in r.json()["detail"]
    assert client.post(f"/api/studies/{sid}/stop", json={}, headers=a).json()["released"] == 1
    assert client.delete(f"/api/works/{world['w']['id']}", headers=a).status_code == 200
    with SessionLocal() as db:
        assert db.get(models.Case, cid) is None


def test_training_blocks_delete(client, world):
    with service._train_lock:
        service._train_jobs["t"] = {"study_id": world["sid"], "status": "running", "outputs": [], "data_hash": "", "job_id": "t"}
    try:
        r = client.delete(f"/api/works/{world['w']['id']}", headers=world["a"])
        assert r.status_code == 409 and "학습" in r.json()["detail"]
    finally:
        with service._train_lock:
            service._train_jobs.pop("t", None)


def test_agent_coming_back_after_delete(client, world):
    """끊긴 PC Agent가 돌아온다. 지운 케이스의 늦은 결과는 조용히 버리고, 도는 솔버는 끝낸다. 연결을 망치지 않는다."""
    a, uid = world["a"], world["ua"]["id"]
    with SessionLocal() as db:
        c = db.query(models.Case).filter_by(study_id=world["sid"]).first()
        cid = c.id
    assert client.delete(f"/api/works/{world['w']['id']}", headers=a).status_code == 200
    service.on_case_status({"case_id": cid, "status": "complete", "results": {"y": 1.0}}, uid)
    sent = []

    class Conn:
        user_id = uid
        info = {}

        def send(self, m):
            sent.append(m)
    conn = Conn()
    registry.conns[uid] = conn
    try:
        service.reconcile_cases(conn, [cid])
    finally:
        registry.conns.pop(uid, None)
    assert {"type": "case.cancel", "case_ids": [cid], "extract_ids": []} in sent


def test_turn_of_deleted_chat_on_reconnect_is_cancelled(client, world):
    from server.app.agent.hub import hub

    uid, chat = world["ua"]["id"], world["w"]["active_chat_id"]
    assert client.delete(f"/api/works/{world['w']['id']}", headers=world["a"]).status_code == 200
    sent = []

    class Conn:
        user_id = uid
        info = {}

        def send(self, m):
            sent.append(m)
    hub._restore_turn(Conn(), {"chat_id": chat, "session_id": "s", "turn_id": "t"})
    assert sent and sent[0]["type"] == "session.cancel", "주인 없는 턴이 계속 돌지 않는다"


def test_copy_survives_source_delete_and_flow_on_study(client, world):
    """스터디가 흐름을 가리켜도 지워진다(지우는 순서). 복사해 간 Task는 이름만 남는다."""
    a, pid, wid, sid = world["a"], world["pid"], world["w"]["id"], world["sid"]
    f = client.post(f"/api/projects/{pid}/flows?work_id={wid}", json={"title": "흐름", "goal": "", "nodes": NODES, "edges": []}, headers=a).json()
    with SessionLocal() as db:
        db.get(models.Study, sid).flow_id = f["id"]
        db.commit()
    copy = client.post(f"/api/projects/{pid}/works", json={"name": "복사", "from_work_id": wid}, headers=a).json()
    assert client.delete(f"/api/works/{wid}", headers=a).status_code == 200
    d = client.get(f"/api/works/{copy['id']}", headers=a).json()
    assert d["source_work_id"] is None and d["source_label"] == "지울 Task"


def test_finished_table_job_notice_goes_with_the_task(client, world):
    a, pid, wid, sid = world["a"], world["pid"], world["w"]["id"], world["sid"]
    with SessionLocal() as db:
        db.add(models.TableJob(user_id=world["ua"]["id"], project_id=pid, work_id=wid, study_id=sid, name="표", status="done", seen=False))
        db.commit()
    assert any(j["study_id"] == sid for j in client.get("/api/table-jobs", headers=a).json())
    assert client.delete(f"/api/works/{wid}", headers=a).status_code == 200
    assert not any(j.get("study_id") == sid for j in client.get("/api/table-jobs", headers=a).json())
    assert all(j["status"] != "done" for j in client.get("/api/table-jobs", headers=a).json()), "'만들었습니다 [열기]'가 거짓이 되지 않는다"


# ---------- 보고서: 표지와 범위 ----------
def _report(client, h, pid, wid, body="## 본문\n\n내용", title="경량화 보고"):
    r = client.post(f"/api/projects/{pid}/reports?work_id={wid}", json={"title": title, "body": body}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_cover_says_project_and_task_and_edition_counts_in_the_task(client, world):
    a, pid, w = world["a"], world["pid"], world["w"]
    pname = client.get(f"/api/projects/{pid}", headers=a).json()["name"]
    one = _report(client, a, pid, w["id"])
    assert one["project_name"] == pname and one["work_name"] == "지울 Task" and one["edition"] == 1 and one["doc_no"].startswith(f"DDS-P{pid:04d}-R")
    html = client.get(f"/api/projects/{pid}/report?format=html&work_id={w['id']}", headers=a).text
    assert f"{pname} · 지울 Task" in html
    docx = client.get(f"/api/projects/{pid}/report?format=docx&work_id={w['id']}", headers=a)
    assert docx.status_code == 200 and docx.content[:2] == b"PK"
    other = client.post(f"/api/projects/{pid}/works", json={"name": "옆", "name_auto": False, "agent": "demo"}, headers=a).json()
    assert _report(client, a, pid, other["id"])["edition"] == 1, "판은 그 Task 안에서 센다"
    assert _report(client, a, pid, w["id"])["edition"] == 2
    assert [x["id"] for x in client.get(f"/api/projects/{pid}/reports?work_id={other['id']}", headers=a).json()] != [x["id"] for x in client.get(f"/api/projects/{pid}/reports?work_id={w['id']}", headers=a).json()]


def test_legacy_cover_has_no_task_name(client, world):
    a, pid, w = world["a"], world["pid"], world["w"]
    with SessionLocal() as db:
        work = db.get(models.Work, w["id"])
        work.legacy, work.seq, work.name = True, 0, "이전 기록"
        db.commit()
    rep = _report(client, a, pid, w["id"])
    assert rep["work_name"] is None
    assert " · 이전 기록" not in client.get(f"/api/projects/{pid}/report?format=html&work_id={w['id']}", headers=a).text


def test_compared_result_survives_when_the_other_task_is_deleted(client, world):
    """비교 보고서가 옆 Task의 결과를 인용했다. 그 Task를 지워도 저장된 판은 문서다."""
    a, pid, w = world["a"], world["pid"], world["w"]
    other = client.post(f"/api/projects/{pid}/works", json={"name": "옛 비교 대상", "name_auto": False, "agent": "demo"}, headers=a).json()
    art = client.post(f"/api/projects/{pid}/artifacts?work_id={other['id']}", json={"kind": "markdown", "title": "옛 모델 요약", "payload": {"text": "옛 값 1.34"}}, headers=a).json()
    rep = _report(client, a, pid, w["id"], body=f"## 비교\n\n![옛 모델 요약](artifact:{art['id']})")
    assert client.delete(f"/api/works/{other['id']}", headers=a).status_code == 200
    html = client.get(f"/api/projects/{pid}/report?format=html&work_id={w['id']}&report_id={rep['id']}", headers=a)
    assert html.status_code == 200 and "옛 값 1.34" in html.text
    assert client.get(f"/api/projects/{pid}/report?format=docx&work_id={w['id']}&report_id={rep['id']}", headers=a).status_code == 200
    again = client.get(f"/api/reports/{rep['id']}", headers=a).json()
    assert again["frozen"][str(art["id"])]["payload"]["text"] == "옛 값 1.34"


def test_report_context_stays_inside_the_task(client, world):
    a, pid, w = world["a"], world["pid"], world["w"]
    other = client.post(f"/api/projects/{pid}/works", json={"name": "옆", "name_auto": False, "agent": "demo"}, headers=a).json()
    client.post(f"/api/projects/{pid}/artifacts?work_id={other['id']}", json={"kind": "markdown", "title": "옆 결과", "payload": {"text": "옆"}}, headers=a)
    ctx = client.get(f"/api/projects/{pid}/report-context?work_id={w['id']}", headers=a).json()
    assert not any(r["title"] == "옆 결과" for r in ctx["results"]) and [s["study_id"] for s in ctx["studies"]] == [world["sid"]]
    wide = client.get(f"/api/projects/{pid}/report-context?all_works=true", headers=a).json()
    assert any(r["title"] == "옆 결과" for r in wide["results"])


# ---------- Simulator 목록 ----------
def test_simulator_list_says_task_and_owner_and_link_hides_them(client, world):
    a, pid, sid = world["a"], world["pid"], world["sid"]
    mine = next(x for x in client.get("/api/simulators", headers=a).json()["mine"] if x["study_id"] == sid)
    assert mine["work_name"] == "지울 Task" and mine["work_id"] == world["w"]["id"] and mine["owner_name"] == world["ua"]["display_name"] and mine["shared_by"] is None
    linked = next(x for x in client.get("/api/simulators", headers=world["linked"]).json()["shared"] if x["study_id"] == sid)
    assert linked["project_name"] is None and linked["work_name"] is None and linked["owner_name"] is None
    # 같은 Project의 멤버에게는 주인과 함께 보인다. 내 것으로 보이지 않는다
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    b, ub = _auth(client, f"del_b{_n[0]}", admin)
    client.put(f"/api/projects/{pid}/members/{ub['id']}", json={"role": "member"}, headers=a)
    theirs = client.get("/api/simulators", headers=b).json()
    row = next(x for x in theirs["shared"] if x["study_id"] == sid)
    assert row["source"] == "project" and row["owner_name"] == world["ua"]["display_name"] and row["work_name"] == "지울 Task" and row["shared_by"] == world["ua"]["display_name"]
    assert not any(x["study_id"] == sid for x in theirs["mine"])
    assert np.isfinite(client.post(f"/api/models/{world['mid']}/predict", json={"params": {"x": 0.5}}, headers=b).json()["mean"]["y"])
