"""남의 PC에서 스크립트를 돌리지 않는다(상황 지도 16행) · 도는 케이스를 다시 시작하지 않는다(86행) · 3D 모델 캐시(19행 일부)."""
from server.tests.test_api import _auth, client  # noqa: F401, I001 — test_api가 임시 데이터 폴더를 정한 뒤에 서버 코드를 불러와야 한다

import os
import time

import pytest  # noqa: E402

from server.app import models  # noqa: E402
from server.app.agent.link import registry  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.routers import studies as studies_router  # noqa: E402
from server.app.study import service  # noqa: E402

POST = {"tool": "python", "script": "scripts/extract.py"}


class _Conn:
    def __init__(self, user_id):
        self.user_id, self.info, self.sent = user_id, {}, []

    def send(self, m):
        self.sent.append(m)


@pytest.fixture
def pcs():
    """연결된 척하는 PC Agent. 시험 뒤에 연결과 진행 표시를 지운다."""
    made: list[int] = []

    def connect(user_id: int) -> _Conn:
        conn = _Conn(user_id)
        registry.conns[user_id] = conn
        made.append(user_id)
        return conn
    yield connect
    for uid in made:
        registry.conns.pop(uid, None)
    with service._batch_lock:
        service._batches.clear()
        service._extracting.clear()


def _team(client, name):  # noqa: F811
    admin, me = _auth(client, "admin")
    member, member_user = _auth(client, f"{name}_member", admin)
    pid = client.post("/api/projects", headers=admin, json={"name": name}).json()["id"]
    client.put(f"/api/projects/{pid}/members/{member_user['id']}", json={"role": "member"}, headers=admin)
    return admin, me, member, member_user, pid


def _script_study(client, h, pid, n=2, name="스크립트"):  # noqa: F811
    sid = client.post(f"/api/projects/{pid}/studies", headers=h, json={
        "name": name, "adapter": "script", "adapter_config": {"tool": "python", "script": "scripts/run.py"},
        "variables": [{"name": "x", "lower": 0, "upper": 1}], "objectives": [{"name": "y", "goal": "min"}]}).json()["id"]
    assert client.post(f"/api/studies/{sid}/doe", headers=h, json={"n": n, "method": "lhs", "seed": 1}).status_code == 200
    return sid


def _finished(client, h, sid, conn):  # noqa: F811
    """이 사람 PC에서 돌아 끝난 케이스들."""
    assert client.post(f"/api/studies/{sid}/run", headers=h, json={}).status_code == 200
    ids = [m["case_id"] for m in conn.sent if m.get("type") == "case.run" and m["study_id"] == sid]
    for i in ids:
        service.on_case_status({"case_id": i, "status": "complete", "results": {"y": 1.0}, "run_dir": f"D:/run/{i}"}, conn.user_id)
    conn.sent.clear()
    return ids


# ---------- Output 추가: 작업 주인만 ----------
def test_member_cannot_add_output_on_someone_elses_study(client, pcs):  # noqa: F811
    admin, me, member, _, pid = _team(client, "owner_block")
    alice = pcs(me["id"])
    sid = _script_study(client, admin, pid)
    _finished(client, admin, sid, alice)
    before = client.get(f"/api/studies/{sid}", headers=admin).json()["study"]

    r = client.post(f"/api/studies/{sid}/outputs", headers=member, json={"outputs": [{"name": "mass"}], "post": POST})
    assert r.status_code == 403
    name = me["display_name"]
    assert f"{name}님 PC의 해석 결과" in r.json()["detail"] and f"{name}님만 추가할 수 있습니다" in r.json()["detail"]
    assert "내 PC로 복사해서 하기" in r.json()["detail"] and "→" in r.json()["detail"]
    assert alice.sent == []  # 남의 PC로 아무것도 가지 않는다
    after = client.get(f"/api/studies/{sid}", headers=admin).json()["study"]
    assert after["outputs"] == before["outputs"] and after["adapter_config"] == before["adapter_config"]


def test_project_admin_account_cannot_add_output_on_a_members_study_either(client, pcs):  # noqa: F811
    """결정: 관리자 계정도 남의 작업을 실행하지 못한다."""
    admin, _, member, member_user, pid = _team(client, "owner_admin")
    bob = pcs(member_user["id"])
    sid = _script_study(client, member, pid)
    _finished(client, member, sid, bob)
    r = client.post(f"/api/studies/{sid}/outputs", headers=admin, json={"outputs": [{"name": "mass"}], "post": POST})
    assert r.status_code == 403 and bob.sent == []


def test_table_study_owner_only_with_plain_wording(client):  # noqa: F811
    """표에는 PC의 해석 결과가 없다. 그래도 남의 작업이고, 없는 PC 이야기를 하지 않는다."""
    admin, me, member, _, pid = _team(client, "owner_table")
    rows = [{"t": float(i), "y": float(i * 2)} for i in range(6)]
    sid = client.post(f"/api/projects/{pid}/studies/import", headers=admin, json={
        "name": "표", "source": "t.csv", "inputs": [{"name": "t"}], "outputs": [{"name": "y"}], "rows": rows}).json()["study_id"]
    r = client.post(f"/api/studies/{sid}/outputs", headers=member, json={"outputs": [{"name": "m"}]})
    assert r.status_code == 403 and "볼 수만 있습니다" in r.json()["detail"] and "PC의 해석 결과" not in r.json()["detail"]
    mine = client.post(f"/api/studies/{sid}/outputs", headers=admin, json={"outputs": [{"name": "m"}]})
    assert mine.status_code == 200 and mine.json()["no_source"] == 6


def test_owner_extracts_only_on_own_pc_never_on_a_members(client, pcs):  # noqa: F811
    """멤버가 내 스터디에서 돌린 케이스의 파일은 멤버 PC에 있다. 내 스크립트를 그 PC에서 돌리지 않고 이유를 말한다."""
    admin, me, member, member_user, pid = _team(client, "owner_other_pc")
    alice, bob = pcs(me["id"]), pcs(member_user["id"])
    sid = _script_study(client, admin, pid, n=2)
    mine = _finished(client, admin, sid, alice)
    assert client.post(f"/api/studies/{sid}/doe", headers=admin, json={"n": 1, "method": "lhs", "seed": 2}).status_code == 200
    theirs = _finished(client, member, sid, bob)
    assert len(theirs) == 1

    r = client.post(f"/api/studies/{sid}/outputs", headers=admin, json={"outputs": [{"name": "mass"}], "post": POST}).json()
    assert r["queued"] == 2 and r["skipped"] == 0 and r["other_pc"] == 1
    assert sorted(m["case_id"] for m in alice.sent if m["type"] == "case.extract") == mine
    assert bob.sent == []
    assert member_user["display_name"] in r["note"] and "→" in r["note"]


def test_study_made_before_owner_column_belongs_to_who_ran_it(client, pcs):  # noqa: F811
    admin, me, member, _, pid = _team(client, "owner_legacy")
    alice = pcs(me["id"])
    sid = _script_study(client, admin, pid)
    _finished(client, admin, sid, alice)
    with SessionLocal() as db:
        db.get(models.Study, sid).created_by = None
        db.commit()
    assert client.post(f"/api/studies/{sid}/outputs", headers=member, json={"outputs": [{"name": "m"}], "post": POST}).status_code == 403


def test_clone_belongs_to_the_person_who_cloned(client, pcs):  # noqa: F811
    admin, me, member, member_user, pid = _team(client, "owner_clone")
    sid = _script_study(client, admin, pid)
    copy = client.post(f"/api/studies/{sid}/clone", headers=member, json={"name": "내 복사"}).json()
    r = client.post(f"/api/studies/{copy['id']}/outputs", headers=member, json={"outputs": [{"name": "m"}], "post": POST})
    assert r.status_code == 200, r.text


# ---------- 스크립트 경로: 프로젝트 안 상대 경로만 ----------
BAD_SCRIPTS = ["C:/Windows/evil.py", "C:\\Windows\\evil.py", "/etc/evil.py", "\\\\서버\\공유\\evil.py", "../evil.py",
               "scripts/../../evil.py", "scripts\\..\\..\\evil.py", "c:evil.py", "scripts/a.py:stream", "... /evil.py"]


@pytest.mark.parametrize("script", BAD_SCRIPTS)
def test_server_refuses_script_outside_project(client, script):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", headers=admin, json={"name": "경로"}).json()["id"]
    made = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "밖", "adapter": "script", "adapter_config": {"tool": "python", "script": script}, "variables": [{"name": "x", "lower": 0, "upper": 1}]})
    assert made.status_code == 400 and "상대 경로" in made.json()["detail"] and "→" in made.json()["detail"]
    nested = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "밖 후처리", "adapter": "script", "adapter_config": {"tool": "python", "script": "scripts/ok.py", "post": [{"tool": "python", "script": script}]},
        "variables": [{"name": "x", "lower": 0, "upper": 1}]})
    assert nested.status_code == 400
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "안", "adapter": "script", "adapter_config": {"tool": "python", "script": "scripts/ok.py"}, "variables": [{"name": "x", "lower": 0, "upper": 1}]}).json()["id"]
    r = client.post(f"/api/studies/{sid}/outputs", headers=admin, json={"outputs": [{"name": "m"}], "post": {"tool": "python", "script": script}})
    assert r.status_code == 400 and "상대 경로" in r.json()["detail"]
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["study"]["outputs"] == []  # 거절하면 아무것도 바뀌지 않는다
    a = client.put(f"/api/studies/{sid}/adapter", headers=admin, json={"adapter": "script", "adapter_config": {"tool": "python", "script": script}})
    assert a.status_code == 400 and "상대 경로" in a.json()["detail"]
    c = client.post(f"/api/studies/{sid}/clone", headers=admin, json={"name": "복사", "adapter_config": {"tool": "python", "script": script}})
    assert c.status_code == 400 and "상대 경로" in c.json()["detail"]


@pytest.mark.parametrize("script", ["scripts/run.py", "./scripts/run.py", ".claude/skills/abaqus/scripts/a.py", "scripts\\run.py", "run.py"])
def test_server_accepts_relative_scripts(client, script):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", headers=admin, json={"name": "경로 안"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "안", "adapter": "script", "adapter_config": {"tool": "python", "script": script}, "variables": [{"name": "x", "lower": 0, "upper": 1}]})
    assert r.status_code == 200, r.text


# ---------- 도는 케이스를 다시 시작하지 않는다 ----------
def _queued(client, h, pid, conn, n=3):  # noqa: F811
    sid = _script_study(client, h, pid, n=n)
    assert client.post(f"/api/studies/{sid}/run", headers=h, json={}).json() == {"queued": n}
    ids = [c["id"] for c in client.get(f"/api/studies/{sid}/cases", headers=h).json()]
    conn.sent.clear()
    return sid, ids


def test_my_running_case_is_not_started_twice_and_caller_is_told(client, pcs):  # noqa: F811
    admin, me = _auth(client, "admin")
    pid = client.post("/api/projects", headers=admin, json={"name": "중복"}).json()["id"]
    conn = pcs(me["id"])
    sid, ids = _queued(client, admin, pid, conn)
    service.on_case_status({"case_id": ids[0], "status": "running", "run_dir": "D:/run"}, me["id"])

    r = client.post(f"/api/studies/{sid}/run", headers=admin, json={"case_ids": ids[:2]}).json()
    assert r["queued"] == 0 and r["already_running"] == 2
    assert r["note"] == "케이스 2건은 이미 돌고 있어 다시 시작하지 않았습니다."
    assert conn.sent == []
    assert {c["id"]: c["status"] for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json()}[ids[0]] == "running"


def test_rerun_keeps_a_stop_that_is_on_its_way(client, pcs):  # noqa: F811
    """중지 신호가 닿기 전에 다시 돌려도 중지 표시를 지우지 않는다. 지우면 중지가 듣지 않는다."""
    admin, me = _auth(client, "admin")
    pid = client.post("/api/projects", headers=admin, json={"name": "중지 유지"}).json()["id"]
    conn = pcs(me["id"])
    sid, ids = _queued(client, admin, pid, conn, n=1)
    service.on_case_status({"case_id": ids[0], "status": "running", "run_dir": "D:/run"}, me["id"])
    assert client.post(f"/api/studies/{sid}/stop", headers=admin, json={}).status_code == 200
    conn.sent.clear()
    r = client.post(f"/api/studies/{sid}/run", headers=admin, json={"case_ids": ids}).json()
    assert r["queued"] == 0 and conn.sent == []
    with SessionLocal() as db:
        assert db.get(models.Case, ids[0]).stopped_by == me["id"]


def test_mixed_rerun_starts_only_the_finished_ones(client, pcs):  # noqa: F811
    admin, me = _auth(client, "admin")
    pid = client.post("/api/projects", headers=admin, json={"name": "일부"}).json()["id"]
    conn = pcs(me["id"])
    sid, ids = _queued(client, admin, pid, conn, n=2)
    service.on_case_status({"case_id": ids[0], "status": "complete", "results": {"y": 1.0}}, me["id"])
    r = client.post(f"/api/studies/{sid}/run", headers=admin, json={"case_ids": ids}).json()
    assert r["queued"] == 1 and r["already_running"] == 1 and "1건은 이미 돌고 있어" in r["note"]
    assert [m["case_id"] for m in conn.sent if m["type"] == "case.run"] == [ids[0]]


def test_someone_elses_running_case_is_still_refused(client, pcs):  # noqa: F811
    admin, me, member, _, pid = _team(client, "rerun_other")
    conn = pcs(me["id"])
    sid, ids = _queued(client, admin, pid, conn, n=1)
    r = client.post(f"/api/studies/{sid}/run", headers=member, json={"case_ids": ids})
    assert r.status_code == 403 and "실행한 사람만" in r.json()["detail"]


# ---------- 3D 모델 캐시 ----------
class _Rom:
    def __init__(self, tag):
        self.tag = tag


def test_field_model_cache_follows_the_file_not_the_number(tmp_path, monkeypatch):
    """번호는 다시 쓰인다. 번호로 기억하면 지운 형상이 새 모델 자리에 다른 사람에게 보인다."""
    loads: list[str] = []

    def load(path):
        loads.append(path)
        return _Rom(open(path, encoding="utf-8").read())

    from server.app.study import fieldmodel
    monkeypatch.setattr(fieldmodel.FieldROM, "load", staticmethod(load))
    monkeypatch.setattr(studies_router, "_rom_cache", {})
    a, b = tmp_path / "a.npz", tmp_path / "b.npz"
    a.write_text("옛 형상", encoding="utf-8")
    b.write_text("새 형상", encoding="utf-8")

    class Fm:
        id = 7
        path = str(a)
    assert studies_router._rom(Fm).tag == "옛 형상" and studies_router._rom(Fm).tag == "옛 형상" and len(loads) == 1

    # 같은 번호, 다른 파일(번호 재사용)
    Fm.path = str(b)
    assert studies_router._rom(Fm).tag == "새 형상"

    # 같은 경로, 파일을 새로 씀(다시 학습): 지난 값을 쓰지 않고 옛 항목도 남기지 않는다
    b.write_text("다시 학습", encoding="utf-8")
    later = time.time() + 5
    os.utime(b, (later, later))
    assert studies_router._rom(Fm).tag == "다시 학습"
    assert sum(1 for k in studies_router._rom_cache if k[0] == str(b)) == 1


def test_field_model_file_gone_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setattr(studies_router, "_rom_cache", {})

    class Fm:
        id = 8
        path = str(tmp_path / "없음.npz")
    with pytest.raises(studies_router.HTTPException) as e:
        studies_router._rom(Fm)
    assert e.value.status_code == 404 and "→" in e.value.detail
