"""지난 Task처럼 시작 · 내 PC로 복사해서 하기. 복사이며 연결이 아니다: 원본을 고치거나 지워도 내 Task는 그대로이고, 내 Task가 원본을 바꾸지 않는다.
상태 × 사건: 주인 PC 꺼짐, 복사 뒤 원본 삭제, 레시피 없음, 다른 Project에서 가져오기, 승인 대기 중 복사, 이전 기록의 멤버, 옛 PC Agent."""
import hashlib
import io
import json
import zipfile

import pytest

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402

from server.app import models  # noqa: E402
from server.app.agent import hub as hub_module  # noqa: E402
from server.app.agent.link import registry, work_fields  # noqa: E402
from server.app.config import settings  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402

NODES = [{"id": "a", "label": "DOE", "type": "doe"}, {"id": "b", "label": "해석", "type": "solve", "tool": "abaqus"}]
_n = [0]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _bundle(text=b"print(1)\n") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scripts/run.py", text)
    return buf.getvalue()


@pytest.fixture
def team(client):
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    n = _n[0]
    a, ua = _auth(client, f"src_a{n}", admin)
    b, ub = _auth(client, f"src_b{n}", admin)
    out, _ = _auth(client, f"src_out{n}", admin)
    pid = client.post("/api/projects", json={"name": f"스파이더{n}"}, headers=a).json()["id"]
    client.put(f"/api/projects/{pid}/members/{ub['id']}", json={"role": "member"}, headers=a)
    return {"a": a, "b": b, "out": out, "admin": admin, "ua": ua, "ub": ub, "pid": pid}


def _source(client, h, pid, recipe=True, name="1차 경량화", tool="python"):
    """끝난 해석이 있는 Task. 케이스는 주인 PC에서 돈 것으로 적는다."""
    w = client.post(f"/api/projects/{pid}/works", json={"name": name, "name_auto": False, "agent": "demo"}, headers=h).json()
    s = client.post(f"/api/projects/{pid}/studies?work_id={w['id']}", json={
        "name": f"스터디{w['id']}", "adapter": "script", "adapter_config": {"tool": tool, "script": "scripts/run.py", "inputs": ["inputs/m.dat"]},
        "variables": [{"name": "x", "lower": 0, "upper": 1}], "objectives": [{"name": "y", "goal": "min"}]}, headers=h).json()
    with SessionLocal() as db:
        owner = db.get(models.Work, w["id"]).owner_id
        for i in range(5):
            db.add(models.Case(study_id=s["id"], params={"x": i / 5}, results={"y": float(i)}, status="complete", source="doe", run_by=owner, run_dir=f"D:/r/{i}"))
        db.add(models.Case(study_id=s["id"], params={"x": 0.9}, results={}, status="running", source="doe", run_by=owner))
        db.add(models.Case(study_id=s["id"], params={"x": 0.95}, results={}, status="failed", source="doe", run_by=owner, error="라이선스"))
        db.commit()
    f = client.post(f"/api/projects/{pid}/flows?work_id={w['id']}", json={"title": "경량화 흐름", "goal": "", "nodes": NODES, "edges": []}, headers=h).json()
    if recipe:
        assert client.post(f"/api/studies/{s['id']}/recipe", files={"file": ("r.zip", _bundle())}, headers=h).status_code == 200
    att = client.post(f"/api/projects/{pid}/attachments", files={"file": ("m.dat", b"model")}, headers=h).json()
    with SessionLocal() as db:
        db.get(models.Attachment, att["id"]).work_id = w["id"]
        db.commit()
    return {"wid": w["id"], "sid": s["id"], "fid": f["id"], "att": att["id"]}


def _dump(client, h, src) -> str:
    """원본 Task의 해시. 복사 뒤에 한 글자라도 바뀌면 안 된다."""
    parts = [client.get(f"/api/studies/{src['sid']}", headers=h).json()["study"], client.get(f"/api/studies/{src['sid']}/cases", headers=h).json(),
             client.get(f"/api/flows/{src['fid']}", headers=h).json(), client.get(f"/api/works/{src['wid']}", headers=h).json()["name"],
             client.get(f"/api/studies/{src['sid']}/recipe", headers=h).json()]
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


# ---------- 목록 ----------
def test_sources_only_from_my_projects_and_only_with_something_to_bring(client, team):
    a, b, out, pid = team["a"], team["b"], team["out"], team["pid"]
    src = _source(client, a, pid)
    client.post(f"/api/projects/{pid}/works", json={"name": "빈 Task", "name_auto": False}, headers=a)
    rows = client.get(f"/api/works/sources?project_id={pid}", headers=b).json()
    assert [r["work_id"] for r in rows] == [src["wid"]], "빈 Task는 가져올 것이 없다"
    row = rows[0]
    assert row["n_steps"] == 2 and row["result"] == "y 0" and row["has_recipe"] and row["other_project"] is False
    assert row["script_author"] == team["ua"]["display_name"] and row["mine"] is False
    assert client.get("/api/works/sources", headers=out).json() == [], "멤버가 아니면 보이지 않는다"
    assert client.get(f"/api/works/sources?project_id={pid}", headers=out).json() == []
    assert client.get("/api/works/sources", headers=team["admin"]).json() == [] or all(r["project_id"] != pid for r in client.get("/api/works/sources", headers=team["admin"]).json()), "관리자도 멤버가 아니면 가져오지 못한다"
    # 첫 화면: 내 Project 전체, 다른 Project 표시
    home = client.get("/api/works/sources", headers=b).json()
    assert home and all(r["other_project"] for r in home)
    assert client.get("/api/works/sources?q=경량", headers=b).json()[0]["work_id"] == src["wid"]
    assert client.get("/api/works/sources?q=없는이름", headers=b).json() == []


def test_first_user_sees_nothing(client):
    admin, _ = _auth(client, "admin")
    fresh, _ = _auth(client, f"src_new{_n[0]}", admin)
    assert client.get("/api/works/sources", headers=fresh).json() == []


# ---------- 지난 Task처럼 시작 ----------
def _start(client, h, pid, from_id, name="spider_m9", agent="demo"):
    return client.post(f"/api/projects/{pid}/works", json={"name": name, "name_auto": False, "agent": agent, "from_work_id": from_id}, headers=h)


def test_same_again_copies_order_and_settings_not_results(client, team):
    a, pid = team["a"], team["pid"]
    src = _source(client, a, pid)
    before = _dump(client, a, src)
    r = _start(client, a, pid, src["wid"])
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["source_label"] == "1차 경량화" and w["source_work_id"] == src["wid"] and w["source_kind"] == "same_again"
    # 결과·모델 파일을 가져오지 않는다
    assert client.get(f"/api/projects/{pid}/studies?work_id={w['id']}", headers=a).json() == []
    assert client.get(f"/api/projects/{pid}/attachments?work_id={w['id']}", headers=a).json() == []
    with SessionLocal() as db:
        carry = db.get(models.Work, w["id"]).carry
    assert carry["kind"] == "same_again" and [s["source_study_id"] for s in carry["studies"]] == [src["sid"]]
    assert carry["flows"][-1]["steps"][1]["label"] == "해석" and carry["attachments"] == []
    assert carry["recipes"][0]["study_id"] == src["sid"]
    assert _dump(client, a, src) == before, "원본은 그대로"


def test_copy_is_not_a_link_source_edit_or_delete_changes_nothing(client, team):
    a, pid = team["a"], team["pid"]
    src = _source(client, a, pid)
    w = _start(client, a, pid, src["wid"]).json()
    blob = client.get(f"/api/works/{w['id']}/carry/{src['sid']}", headers=a).content
    # 원본 스크립트를 고치고 지워도 내 몫은 그대로다
    client.post(f"/api/studies/{src['sid']}/recipe", files={"file": ("r.zip", _bundle(b"print('changed')\n"))}, headers=a)
    assert client.get(f"/api/works/{w['id']}/carry/{src['sid']}", headers=a).content == blob
    assert client.delete(f"/api/works/{src['wid']}", headers=a).status_code == 200
    after = client.get(f"/api/works/{w['id']}", headers=a).json()
    assert after["source_label"] == "1차 경량화" and after["source_work_id"] is None, "원본이 지워져도 어디서 왔는지 남는다"
    assert client.get(f"/api/works/{w['id']}/carry/{src['sid']}", headers=a).content == blob


def test_no_recipe_brings_order_and_settings_only(client, team):
    a, pid = team["a"], team["pid"]
    src = _source(client, a, pid, recipe=False)
    row = next(r for r in client.get(f"/api/works/sources?project_id={pid}", headers=a).json() if r["work_id"] == src["wid"])
    assert row["has_recipe"] is False
    w = _start(client, a, pid, src["wid"]).json()
    with SessionLocal() as db:
        carry = db.get(models.Work, w["id"]).carry
    assert carry["recipes"] == [] and carry["studies"] and carry["flows"]
    assert "스크립트를 새로 만드세요" in hub_module._carry_text({**carry, "carried": {"scripts": [], "unchanged": []}})


def test_other_project_needs_membership_and_warns_about_secrets(client, team):
    a, b, pid = team["a"], team["b"], team["pid"]
    src = _source(client, a, pid)
    theirs = client.post("/api/projects", json={"name": "다른 부품"}, headers=b).json()["id"]
    # 내가 멤버인 Project면 된다. 값이 옮겨 가므로 AI 첫 턴 글에 확인하라고 적는다
    w = _start(client, b, theirs, src["wid"]).json()
    with SessionLocal() as db:
        carry = db.get(models.Work, w["id"]).carry
    assert carry["other_project"] is True and w["project_id"] == theirs
    assert "치수·경로·품번" in hub_module._carry_text(carry)
    # 멤버가 아니면 가져오지 못한다
    r = _start(client, team["out"], client.post("/api/projects", json={"name": "밖"}, headers=team["out"]).json()["id"], src["wid"])
    assert r.status_code == 403 and "→" in r.json()["detail"]


def test_clone_in_session_goes_to_the_session_task_even_from_other_project(client, team):
    a, b, pid = team["a"], team["b"], team["pid"]
    src = _source(client, a, pid)
    theirs = client.post("/api/projects", json={"name": "새 부품"}, headers=b).json()["id"]
    w = _start(client, b, theirs, src["wid"]).json()
    made = client.post(f"/api/studies/{src['sid']}/clone", json={"name": "복제"}, headers={**b, "X-DDS-Work": str(w["id"])}).json()
    assert made["project_id"] == theirs and made["work_id"] == w["id"], "복제본이 옛 Project에 생기면 새 Task에 결과가 없다"


# ---------- 내 PC로 복사해서 하기 ----------
def test_takeover_preview_lists_missing_tools_and_raw_files(client, team):
    a, b, pid = team["a"], team["b"], team["pid"]
    src = _source(client, a, pid, tool="abaqus_cae")
    off = client.get(f"/api/works/{src['wid']}/takeover-preview", headers=b).json()
    assert off["agent_connected"] is False and off["missing_tools"] == [], "PC Agent가 꺼져 있으면 툴을 모른다. 없다고 말하지 않는다"
    assert off["n_raw"] == 5 and off["n_copied"] == 5 and off["has_recipe"] and off["owner_name"] == team["ua"]["display_name"] and off["n_inputs"] == 1

    class Conn:
        info = {"tools_ready": []}
    registry.conns[team["ub"]["id"]] = Conn()
    try:
        on = client.get(f"/api/works/{src['wid']}/takeover-preview", headers=b).json()
        assert on["agent_connected"] and on["missing_tools"] == [{"key": "abaqus", "label": "Abaqus"}]
        Conn.info = {"tools_ready": ["abaqus"]}
        assert client.get(f"/api/works/{src['wid']}/takeover-preview", headers=b).json()["missing_tools"] == []
    finally:
        registry.conns.pop(team["ub"]["id"], None)


def test_takeover_while_owner_pc_is_off_copies_numbers_and_leaves_source_alone(client, team):
    a, b, pid = team["a"], team["b"], team["pid"]
    src = _source(client, a, pid)
    assert registry.get(team["ua"]["id"]) is None
    before = _dump(client, a, src)
    r = client.post(f"/api/works/{src['wid']}/takeover", json={}, headers=b)
    assert r.status_code == 200, r.text
    w = r.json()
    assert w["owner_id"] == team["ub"]["id"] and w["mine"] and w["name"] == "1차 경량화 복사" and w["source_kind"] == "takeover" and w["project_id"] == pid
    studies = client.get(f"/api/projects/{pid}/studies?work_id={w['id']}", headers=b).json()
    assert len(studies) == 1 and studies[0]["id"] != src["sid"] and studies[0]["adapter_config"]["script"] == "scripts/run.py"
    copied = client.get(f"/api/studies/{studies[0]['id']}/cases", headers=b).json()
    assert len(copied) == 5 and {c["status"] for c in copied} == {"complete"}, "끝난 것만 복사한다. 돌던 케이스·실패는 가져오지 않는다"
    with SessionLocal() as db:
        rows = db.query(models.Case).filter_by(study_id=studies[0]["id"]).all()
        origin = {c.id for c in db.query(models.Case).filter_by(study_id=src["sid"])}
        assert all(c.from_case_id in origin and c.run_dir is None and c.run_by is None for c in rows)
    # 다시 돌리지 않고 재학습한다. 주인 PC도 내 PC도 필요 없다
    m = client.post(f"/api/studies/{studies[0]['id']}/train", json={}, headers=b)
    assert m.status_code == 200 and m.json()["n_train"] == 5, m.text
    # 입력 첨부는 내 Task의 사본이다
    mine = client.get(f"/api/projects/{pid}/attachments?work_id={w['id']}", headers=b).json()
    assert [x["filename"] for x in mine] == ["m.dat"] and mine[0]["id"] != src["att"]
    assert _dump(client, a, src) == before, "원본 해시 변화 0"
    # 스크립트 만든 사람은 그대로라 돌리기 전에 승인을 받는다
    assert client.get(f"/api/studies/{studies[0]['id']}/recipe", headers=b).json()["created_by_name"] == team["ua"]["display_name"]


def test_takeover_then_source_deleted_keeps_my_copy_and_my_attachment(client, team):
    """복사 중 원본 삭제의 뒤: 사본은 독립이다. 원본을 지우면 그쪽 파일만 지워진다."""
    a, b, pid = team["a"], team["b"], team["pid"]
    src = _source(client, a, pid)
    w = client.post(f"/api/works/{src['wid']}/takeover", json={}, headers=b).json()
    sid = client.get(f"/api/projects/{pid}/studies?work_id={w['id']}", headers=b).json()[0]["id"]
    att = client.get(f"/api/projects/{pid}/attachments?work_id={w['id']}", headers=b).json()[0]["id"]
    assert client.delete(f"/api/works/{src['wid']}", headers=a).status_code == 200
    assert client.get(f"/api/attachments/{att}/download", headers=b).content == b"model"
    assert len(client.get(f"/api/studies/{sid}/cases", headers=b).json()) == 5
    assert client.get(f"/api/studies/{sid}/recipe/file", headers=b).status_code == 200
    assert client.get(f"/api/works/{w['id']}", headers=b).json()["source_label"] == "1차 경량화"


def test_takeover_of_a_deleted_task_says_what_to_do(client, team):
    a, b, pid = team["a"], team["b"], team["pid"]
    src = _source(client, a, pid)
    assert client.delete(f"/api/works/{src['wid']}", headers=a).status_code == 200
    r = client.post(f"/api/works/{src['wid']}/takeover", json={}, headers=b)
    assert r.status_code == 404
    with SessionLocal() as db:
        assert db.query(models.Work).filter_by(project_id=pid, owner_id=team["ub"]["id"]).count() == 0, "반쪽 Task를 남기지 않는다"


def test_takeover_failing_midway_leaves_nothing(client, team, monkeypatch):
    """복사 중 원본이 사라지면 번호만 쓰이고 Task는 남지 않는다."""
    from sqlalchemy.orm.exc import ObjectDeletedError

    from server.app import work_service

    a, b, pid = team["a"], team["b"], team["pid"]
    src = _source(client, a, pid)

    def gone(*_a, **_k):
        raise ObjectDeletedError.__new__(ObjectDeletedError)
    monkeypatch.setattr(work_service, "_copy_attachments", gone)
    with TestClient(app, raise_server_exceptions=False) as quiet:
        r = quiet.post(f"/api/works/{src['wid']}/takeover", json={}, headers=b)
    assert r.status_code == 409 and "→" in r.json()["detail"], r.text
    assert [x for x in client.get(f"/api/projects/{pid}/works", headers=b).json() if x["owner_id"] == team["ub"]["id"]] == []
    assert client.get(f"/api/works/{src['wid']}", headers=a).status_code == 200


def test_takeover_during_review_keeps_the_owners_card(client, team):
    """승인 대기 중인 원본을 복사해도 주인의 카드는 그대로이고, 내 Task에는 승인 대기가 없다."""
    a, b, pid = team["a"], team["b"], team["pid"]
    src = _source(client, a, pid)
    assert client.get(f"/api/flows/{src['fid']}", headers=a).json()["status"] == "review"
    w = client.post(f"/api/works/{src['wid']}/takeover", json={}, headers=b).json()
    assert client.get(f"/api/flows/{src['fid']}", headers=a).json()["status"] == "review"
    assert client.get(f"/api/projects/{pid}/flows?work_id={w['id']}", headers=b).json() == []
    denied = client.put(f"/api/flows/{src['fid']}/status", json={"status": "approved", "version": 1}, headers=b)
    assert denied.status_code == 403 and "내 PC로 복사해서 하기" in denied.json()["detail"]


def test_legacy_member_sees_mine_and_unlisted_member_copies(client, team):
    a, b, pid = team["a"], team["b"], team["pid"]
    src = _source(client, a, pid)
    with SessionLocal() as db:
        w = db.get(models.Work, src["wid"])
        w.legacy, w.seq, w.legacy_users = True, 0, [team["ua"]["id"]]
        db.commit()
    assert client.get(f"/api/works/{src['wid']}", headers=b).json()["mine"] is False
    r = client.post(f"/api/works/{src['wid']}/takeover", json={}, headers=b)
    assert r.status_code == 200, r.text
    # 복사본은 이전 기록이 아니다. 자기 폴더가 생긴다
    assert r.json()["legacy"] is False and r.json()["seq"] > 0


def test_takeover_needs_a_person_and_membership(client, team):
    from server.app.deps import new_api_token

    b, pid = team["b"], team["pid"]
    src = _source(client, team["a"], pid)
    raw, hashed = new_api_token()
    with SessionLocal() as db:
        db.add(models.ApiToken(user_id=team["ub"]["id"], name="t", token_hash=hashed))
        db.commit()
    assert client.post(f"/api/works/{src['wid']}/takeover", json={}, headers={"Authorization": f"Bearer {raw}"}).status_code == 403, "AI가 스스로 복사하지 못한다"
    assert client.post(f"/api/works/{src['wid']}/takeover", json={}, headers=team["out"]).status_code == 403
    assert client.get(f"/api/works/{src['wid']}", headers=b).status_code == 200


# ---------- PC Agent 메시지 ----------
def test_old_agent_gets_no_carry_and_is_told_to_restore(client, team):
    a, pid = team["a"], team["pid"]
    src = _source(client, a, pid)
    w = _start(client, a, pid, src["wid"]).json()
    with SessionLocal() as db:
        work = db.get(models.Work, w["id"])

        class Old:
            info = {}

        class New:
            info = {"work_folders": 1}
        assert work_fields(Old(), work) == {}, "옛 Agent에는 보내지 않는다"
        sent = work_fields(New(), work)["work"]
        assert sent["carry"]["recipes"] and "flows" not in sent["carry"], "AI에게 넘길 글은 PC로 보내지 않는다"
        text = hub_module._carry_text(work.carry)
    assert "restore_recipe" in text, "옛 Agent는 복사하지 않았다. AI가 받아야 한다"
    assert f"study_id={src['sid']}" in text and "clone_study" in text


def test_carried_is_recorded_and_not_sent_twice(client, team):
    a, pid = team["a"], team["pid"]
    src = _source(client, a, pid)
    w = _start(client, a, pid, src["wid"]).json()
    with SessionLocal() as db:
        work = db.get(models.Work, w["id"])
        chat = db.query(models.ChatSession).filter_by(work_id=w["id"]).first()
        hub_module._note_carried(db, chat, {"scripts": [], "errors": ["네트워크"]})
        db.commit()
        assert work.folder_view()["carry"], "못 받았으면 다음 세션에 다시 보낸다"
        hub_module._note_carried(db, chat, {"scripts": ["scripts/run.py"], "errors": []})
        db.commit()
        assert work.folder_view()["carry"] is None
        cid = chat.id
    with SessionLocal() as db:
        start, _ = hub_module._carry_context(db, cid)
        assert "scripts/run.py" in start and start.startswith("[시작점]")
    with SessionLocal() as db:
        assert hub_module._carry_context(db, cid) == ("", ""), "한 번만 알린다"


def test_delete_removes_my_carried_copy(client, team):
    a, pid = team["a"], team["pid"]
    src = _source(client, a, pid)
    w = _start(client, a, pid, src["wid"]).json()
    folder = settings.files_dir / f"project_{pid}" / "carry" / f"work_{w['id']}"
    assert folder.is_dir()
    assert client.delete(f"/api/works/{w['id']}", headers=a).status_code == 200
    assert not folder.exists()
