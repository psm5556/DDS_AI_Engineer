"""Task 권한 표(docs/product-spec.md 4-1절)를 서버에서 잰다. 쓰기 경로 전부가 이 표에 있어야 한다.
새 쓰기 경로를 만들고 표에 넣지 않으면 test_every_write_route_is_in_the_table이 실패한다: 어느 행에 속하는지 정하지 않은 입구를 남기지 않기 위해서다.
역할: 주인(Task를 만든 멤버) · 멤버 · master(Project 관리자) · 관리자(멤버가 아닌 웹 관리자) · 공유 링크만 받은 사람(멤버 아님)."""
import io
import zipfile

import pytest

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402

from server.app import models  # noqa: E402
from server.app.config import settings  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.study import recipe, service  # noqa: E402

NODES = [{"id": "n", "label": "단계", "type": "doe"}]
VAR = [{"name": "x", "lower": 0, "upper": 1}]
PNG = ("a.png", b"\x89PNG\r\n", "image/png")
_n = [0]


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scripts/run.py", "print(1)\n")
    return buf.getvalue()


# ---------- 표 ----------
# 주인만: 멤버·master·관리자·공유 링크 받은 사람 모두 403. (메서드, 경로) → 요청 인자.
# 경로의 {p} {s} {m} {c} {f} {w} {a} {t} 는 시험 세상의 번호로 채운다. {w}는 주인의 Task다
OWNER = {
    ("POST", "/api/studies/{s}/cases/import"): {"json": {"rows": [{"x": 0.5, "y": 1.0}], "outputs": [{"name": "y"}]}},
    ("POST", "/api/studies/{s}/cases/import/file"): {"files": {"file": ("t.csv", b"x,y\n0.5,1\n", "text/csv")}, "data": {"mapping": "{}"}},
    ("POST", "/api/studies/{s}/cases/import/text"): {"json": {"text": "x\ty\n0.5\t1\n0.6\t2\n0.7\t3\n"}},
    ("PUT", "/api/studies/{s}/adapter"): {"json": {"adapter": "script", "adapter_config": {"tool": "python", "script": "scripts/run.py"}}},
    ("POST", "/api/studies/{s}/recipe"): {"files": {"file": ("r.zip", _zip(), "application/zip")}},
    ("DELETE", "/api/studies/{s}/recipe"): {},
    ("POST", "/api/studies/{s}/doe"): {"json": {"n": 1, "method": "lhs", "seed": 1}},
    ("POST", "/api/studies/{s}/run"): {"json": {}},
    ("POST", "/api/studies/{s}/stop"): {"json": {}},
    ("DELETE", "/api/studies/{s}/cases/{c}"): {},
    ("POST", "/api/studies/{s}/outputs"): {"json": {"outputs": [{"name": "m"}]}},
    ("POST", "/api/studies/{s}/train"): {"json": {}},
    ("POST", "/api/studies/{s}/train/start"): {"json": {}},
    ("POST", "/api/studies/{s}/candidates"): {"json": {"mode": "bo", "n": 1}},
    ("POST", "/api/studies/{s}/suggest"): {"json": {"mode": "bo", "n": 1}},
    ("POST", "/api/studies/{s}/train_field"): {"json": {"field": "mises"}},
    ("POST", "/api/studies/{s}/insights/publish"): {"json": {}},
    ("POST", "/api/studies/{s}/datasets"): {"json": {}},
    ("POST", "/api/models/{m}/deploy"): {},
    ("POST", "/api/models/{m}/shares"): {"json": {}},
    ("POST", "/api/cases/{c}/fields"): {"files": {"file": ("f.npz", b"x", "application/octet-stream")}},
    ("POST", "/api/cases/{c}/archive"): {"files": {"file": ("c.h5", b"x", "application/octet-stream")}},
    ("POST", "/api/cases/{c}/train_sample"): {"params": {"spec_hash": "a" * 12}, "files": {"file": ("t.h5", b"x", "application/octet-stream")}},
    ("PUT", "/api/flows/{f}/status"): {"json": {"status": "approved", "version": 1}},
    ("PUT", "/api/flows/{f}/nodes/{{node_id}}"): {"json": {"status": "complete"}},
    ("DELETE", "/api/artifacts/{a}"): {},
    ("POST", "/api/works/{w}/chat"): {"json": {"agent": "demo"}},
    ("POST", "/api/projects/{p}/studies?work_id={w}"): {"json": {"name": "새", "adapter": "mock", "variables": VAR}},
    ("POST", "/api/projects/{p}/studies/import?work_id={w}"): {"json": {"name": "표", "source": "t.csv", "inputs": [{"name": "x"}], "outputs": [{"name": "y"}],
                                                                       "rows": [{"x": float(i), "y": float(i)} for i in range(4)]}},
    ("POST", "/api/projects/{p}/studies/import/text?work_id={w}"): {"json": {"text": "x\ty\n0\t1\n1\t2\n2\t3\n"}},
    ("POST", "/api/projects/{p}/studies/import/file?work_id={w}"): {"files": {"file": ("t.csv", b"x,y\n0,1\n1,2\n2,3\n", "text/csv")}, "data": {"mapping": "{}"}},
    ("POST", "/api/projects/{p}/flows?work_id={w}"): {"json": {"title": "남의 곳", "goal": "", "nodes": NODES, "edges": []}},
    ("POST", "/api/projects/{p}/artifacts?work_id={w}"): {"json": {"kind": "markdown", "title": "남의 곳", "payload": {"text": "x"}}},
    ("POST", "/api/projects/{p}/reports?work_id={w}"): {"json": {"title": "남의 곳", "body": "## 본문"}},
    ("PUT", "/api/projects/{p}/works/current?work_id={w}"): {"json": {"name": "남의 이름"}},
    ("POST", "/api/projects/{p}/sessions"): {"json": {"agent": "demo", "work_id": "{w}"}},
    ("POST", "/api/projects/{p}/route-suggestion?work_id={w}"): {"json": {"kind": "new_model", "reason": "새 모델"}},
    ("POST", "/api/works/{w}/undo"): {},
    ("POST", "/api/works/{w}/warm"): {},
    ("POST", "/api/works/{w}/end-session"): {},
    ("POST", "/api/sessions/{t}/prompt"): {"json": {"text": "안녕"}},
    ("POST", "/api/sessions/{t}/cancel"): {},
    ("POST", "/api/sessions/{t}/force-restart"): {},
    ("POST", "/api/sessions/{t}/permission"): {"json": {"request_id": "r", "option_id": "allow"}},
}

# 주인·master·관리자. 실행 권한과 다르다: 관리이지 일이 아니다
MANAGE = {("PUT", "/api/works/{w}"): {"json": {"name": "새 이름"}}, ("DELETE", "/api/works/{w}"): {},
          ("DELETE", "/api/sessions/{t}"): {}, ("PUT", "/api/sessions/{t}"): {"json": {"title": "새 제목"}}}
SHARE_STOP = {("DELETE", "/api/shares/{{token}}"): {}}

# Task 밖의 일. 왜 이 표의 주인 칸을 타지 않는지 적는다
OUTSIDE = {
    # 누구나: 자기 일
    ("POST", "/api/auth/register"): "가입", ("POST", "/api/auth/login"): "로그인", ("PUT", "/api/auth/pin"): "내 PIN",
    ("POST", "/api/tokens"): "내 토큰", ("DELETE", "/api/tokens/{token_id}"): "내 토큰", ("PUT", "/api/users/{user_id}"): "관리자 계정 관리",
    ("POST", "/api/agent/check-folder"): "내 PC", ("POST", "/api/agent/install"): "내 PC", ("POST", "/api/agent/pick-folder"): "내 PC",
    ("POST", "/api/agent/tools/{key}/pick"): "내 PC", ("PUT", "/api/agent/tools/{key}"): "내 PC", ("PUT", "/api/agent/settings"): "내 PC 설정",
    ("POST", "/api/agent/instances/{user_id}/update"): "내 PC 또는 관리자", ("POST", "/api/agent/releases"): "관리자 배포",
    ("POST", "/api/agent/releases/from-server"): "관리자 배포", ("PUT", "/api/agent/releases/{version}/latest"): "관리자 배포",
    ("POST", "/api/devtest/deploy"): "원격 시험", ("PUT", "/api/devtest/flows/{flow_id}/status"): "원격 시험: apply_flow_status가 주인을 다시 본다",
    ("PUT", "/api/devtest/permission"): "원격 시험", ("POST", "/api/devtest/run"): "원격 시험",
    ("POST", "/api/devtest/sessions/{session_id}/permission"): "원격 시험: reply_permission이 주인을 다시 본다",
    ("POST", "/api/tables/check"): "저장하지 않음", ("POST", "/api/tables/preview/text"): "저장하지 않음",
    ("POST", "/api/projects/{project_id}/studies/import/preview"): "저장하지 않음",
    ("POST", "/api/table-jobs"): "남의 Task면 내 새 Task에 넣는다(test_table_job_goes_to_my_work_or_a_new_one)",
    ("POST", "/api/route-check"): "저장하지 않음",
    ("POST", "/api/projects/{project_id}/attachments/adopt"): "내가 올리고 아직 보내지 않은 첨부만 옮긴다",
    ("POST", "/api/table-jobs/{job_id}/seen"): "내 알림", ("DELETE", "/api/simulators/shared/{share_id}"): "내 목록에서만 뺀다",
    # 프로젝트 단위: require_master 등 프로젝트 권한
    ("POST", "/api/projects"): "프로젝트 만들기", ("PUT", "/api/projects/{project_id}"): "master", ("DELETE", "/api/projects/{project_id}"): "master",
    ("PUT", "/api/projects/{project_id}/favorite"): "내 즐겨찾기", ("DELETE", "/api/projects/{project_id}/favorite"): "내 즐겨찾기",
    ("PUT", "/api/projects/{project_id}/members/{user_id}"): "master", ("DELETE", "/api/projects/{project_id}/members/{user_id}"): "master",
    ("PUT", "/api/projects/{project_id}/local/folder"): "내 PC 폴더", ("POST", "/api/projects/{project_id}/local/open"): "내 PC 폴더",
    # 멤버 누구나: 아직 어느 Task의 것도 아니다. 보낼 때 정해지고 그때 주인을 본다
    ("POST", "/api/projects/{project_id}/attachments"): "놓을 때는 Task가 없다", ("POST", "/api/projects/{project_id}/files"): "그림 저장. 결과로 게시할 때 주인을 본다",
    ("POST", "/api/projects/{project_id}/works"): "멤버는 내 Task를 만든다", ("POST", "/api/works/{work_id}/takeover"): "멤버는 내 Task로 복사한다",
    ("POST", "/api/studies/{study_id}/clone"): "원본은 읽기만 한다. 복제본은 내 Task에 생긴다",
    # 예측: 모두
    ("POST", "/api/models/{model_id}/predict"): "예측", ("POST", "/api/models/{model_id}/predict_batch"): "예측", ("POST", "/api/field-models/{field_model_id}/predict"): "예측",
    ("POST", "/api/shares/{token}/predict"): "공유 링크 예측", ("POST", "/api/shares/{token}/predict_batch"): "공유 링크 예측",
    ("POST", "/api/shares/{token}/field-models/{field_model_id}/predict"): "공유 링크 예측",
}


def _norm(path: str) -> str:
    """표의 경로를 앱의 경로 모양으로 바꾼다."""
    path = path.split("?")[0]
    for short, long in (("{p}", "{project_id}"), ("{s}", "{study_id}"), ("{m}", "{model_id}"), ("{c}", "{case_id}"), ("{f}", "{flow_id}"), ("{w}", "{work_id}"),
                        ("{a}", "{artifact_id}"), ("{t}", "{session_id}"), ("{{node_id}}", "{node_id}"), ("{{token}}", "{token}")):
        path = path.replace(short, long)
    return path


def test_every_write_route_is_in_the_table():
    """새 쓰기 경로가 표에 없으면 실패한다. 어느 칸인지 정한 뒤에 들어온다. 마켓은 자기 권한 시험이 있다(test_market_api)."""
    seen = {(m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops if m in ("post", "put", "delete", "patch") and p.startswith("/api/") and "/market/" not in p}
    table = {(m, _norm(p)) for m, p in [*OWNER, *MANAGE, *SHARE_STOP]} | set(OUTSIDE)
    missing = sorted(seen - table)
    assert not missing, f"권한 표에 없는 쓰기 경로: {missing}"
    stale = sorted(table - seen)
    assert not stale, f"앱에 없는 경로가 표에 있습니다: {stale}"


# ---------- 세상 ----------
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _world(client):
    _n[0] += 1
    n = _n[0]
    admin, _ = _auth(client, "admin")
    master, um = _auth(client, f"perm_master{n}", admin)
    owner, uo = _auth(client, f"perm_owner{n}", admin)
    member, ub = _auth(client, f"perm_member{n}", admin)
    linked, _ = _auth(client, f"perm_linked{n}", admin)
    pid = client.post("/api/projects", json={"name": f"권한{n}"}, headers=master).json()["id"]
    for u in (uo, ub):
        assert client.put(f"/api/projects/{pid}/members/{u['id']}", json={"role": "member"}, headers=master).status_code == 200
    w = client.post(f"/api/projects/{pid}/works", json={"name": "주인 Task", "name_auto": False, "agent": "demo"}, headers=owner).json()
    with SessionLocal() as db:
        s = models.Study(project_id=pid, work_id=w["id"], name=f"스터디{n}", variables=VAR, objectives=[{"name": "y", "goal": "min"}], constraints=[], outputs=[{"name": "y"}],
                         adapter="data", adapter_config={}, created_by=uo["id"])
        db.add(s)
        db.flush()
        for i in range(6):
            db.add(models.Case(study_id=s.id, params={"x": i / 6}, results={"y": float(i)}, status="complete", source="import", run_by=uo["id"]))
        c = models.Case(study_id=s.id, params={"x": 0.99}, results={"y": 9.0}, status="complete", source="import", run_by=uo["id"])
        db.add(c)
        db.commit()
        sid, cid = s.id, c.id
    mid = client.post(f"/api/studies/{sid}/train", json={}, headers=owner).json()["id"]
    assert client.post(f"/api/models/{mid}/deploy", headers=owner).status_code == 200
    token = client.post(f"/api/models/{mid}/shares", json={}, headers=owner).json()["token"]
    f = client.post(f"/api/projects/{pid}/flows?work_id={w['id']}", json={"title": "흐름", "goal": "", "nodes": NODES, "edges": []}, headers=owner).json()
    a = client.post(f"/api/projects/{pid}/artifacts?work_id={w['id']}", json={"kind": "markdown", "title": "요약", "payload": {"text": "x"}}, headers=owner).json()
    return {"owner": owner, "member": member, "master": master, "admin": admin, "linked": linked, "uo": uo, "pid": pid, "wid": w["id"], "sid": sid, "mid": mid, "cid": cid,
            "fid": f["id"], "aid": a["id"], "chat": w["active_chat_id"], "token": token}


def _call(client, w, method, path, spec, who):
    url = (path.replace("{p}", str(w["pid"])).replace("{s}", str(w["sid"])).replace("{m}", str(w["mid"])).replace("{c}", str(w["cid"])).replace("{f}", str(w["fid"]))
           .replace("{w}", str(w["wid"])).replace("{a}", str(w["aid"])).replace("{t}", str(w["chat"])).replace("{{node_id}}", "n").replace("{{token}}", w["token"]))
    kw = dict(spec)
    if "json" in kw:
        kw["json"] = {k: (w["wid"] if v == "{w}" else v) for k, v in kw["json"].items()}
    return client.request(method, url, headers=w[who], **kw)


@pytest.fixture(scope="module")
def world(client):
    return _world(client)


@pytest.mark.parametrize("who", ["member", "master", "admin", "linked"])
@pytest.mark.parametrize("key", list(OWNER), ids=lambda k: f"{k[0]} {k[1]}")
def test_only_the_owner_can_do_it(client, world, key, who):
    r = _call(client, world, key[0], key[1], OWNER[key], who)
    assert r.status_code == 403, f"{who} {key}: {r.status_code} {r.text[:200]}"
    detail = r.json()["detail"]
    assert "→" in detail, detail
    # 멤버가 아니면 멤버 안내를, 멤버면 복사 버튼을 안내한다
    if who in ("member", "master", "admin") and "실행한 사람" not in detail:
        assert "내 PC로 복사해서 하기" in detail or "PC의 해석 결과" in detail or "볼 수만 있습니다" in detail or "요청하세요" in detail or "멤버" in detail, detail


@pytest.mark.parametrize("key", list(OWNER), ids=lambda k: f"{k[0]} {k[1]}")
def test_the_owner_gets_past_the_gate(client, key):
    """지워지는 일이 많아 경로마다 새 세상을 쓴다. 주인이 막히지 않는 것만 본다(내용이 틀리면 400은 괜찮다)."""
    w = _world(client)
    r = _call(client, w, key[0], key[1], OWNER[key], "owner")
    assert r.status_code != 403, f"{key}: {r.text[:300]}"


@pytest.mark.parametrize("key", list(MANAGE), ids=lambda k: f"{k[0]} {k[1]}")
def test_manage_routes_allow_owner_master_admin_only(client, key):
    w = _world(client)
    denied = _call(client, w, key[0], key[1], MANAGE[key], "member")
    assert denied.status_code == 403, denied.text
    assert _call(client, w, key[0], key[1], MANAGE[key], "linked").status_code == 403
    assert _call(client, w, key[0], key[1], MANAGE[key], "master").status_code == 200
    w = _world(client)
    assert _call(client, w, key[0], key[1], MANAGE[key], "admin").status_code == 200
    w = _world(client)
    assert _call(client, w, key[0], key[1], MANAGE[key], "owner").status_code == 200


@pytest.mark.parametrize("who,code", [("member", 403), ("linked", 403), ("owner", 200), ("master", 200), ("admin", 200)])
def test_stopping_a_share(client, who, code):
    w = _world(client)
    r = _call(client, w, "DELETE", "/api/shares/{{token}}", {}, who)
    assert r.status_code == code, r.text
    assert (client.post(f"/api/shares/{w['token']}/predict", json={"params": {"x": 0.5}}, headers=w["linked"]).status_code == 200) == (code != 200)


@pytest.mark.parametrize("who", ["owner", "member", "master", "admin"])
def test_everyone_can_look_and_download(client, world, who):
    h = world[who]
    for url in (f"/api/works/{world['wid']}", f"/api/works/{world['wid']}/messages", f"/api/flows/{world['fid']}", f"/api/studies/{world['sid']}", f"/api/studies/{world['sid']}/cases",
                f"/api/projects/{world['pid']}/artifacts?work_id={world['wid']}", f"/api/projects/{world['pid']}/report?work_id={world['wid']}", f"/api/studies/{world['sid']}/doe.csv"):
        r = client.get(url, headers=h)
        assert r.status_code == 200, f"{url}: {r.text[:200]}"


@pytest.mark.parametrize("who", ["owner", "member", "master", "admin"])
def test_everyone_with_access_can_predict(client, world, who):
    assert client.post(f"/api/models/{world['mid']}/predict", json={"params": {"x": 0.5}}, headers=world[who]).status_code == 200


def test_link_holder_only_predicts(client, world):
    h = world["linked"]
    assert client.get(f"/api/shares/{world['token']}", headers=h).status_code == 200
    assert client.post(f"/api/shares/{world['token']}/predict", json={"params": {"x": 0.5}}, headers=h).status_code == 200
    for url in (f"/api/works/{world['wid']}", f"/api/studies/{world['sid']}", f"/api/flows/{world['fid']}", f"/api/models/{world['mid']}/spec"):
        assert client.get(url, headers=h).status_code == 403, url


def test_member_can_make_own_task_and_admin_only_when_member(client, world):
    assert client.post(f"/api/projects/{world['pid']}/works", json={"name": "내 일"}, headers=world["member"]).status_code == 200
    assert client.post(f"/api/projects/{world['pid']}/works", json={"name": "관리자"}, headers=world["admin"]).status_code == 403
    assert client.post(f"/api/works/{world['wid']}/takeover", json={}, headers=world["admin"]).status_code == 403
    assert client.post(f"/api/projects/{world['pid']}/works", json={"name": "밖"}, headers=world["linked"]).status_code == 403


def test_refused_message_tells_what_to_do(client, world):
    r = client.post(f"/api/studies/{world['sid']}/train", json={}, headers=world["member"])
    assert r.status_code == 403
    assert r.json()["detail"].startswith(f"{world['uo']['display_name']}님의 Task입니다 → ") and "내 PC로 복사해서 하기" in r.json()["detail"]


# ---------- 옆 Task ----------
def test_next_task_of_mine_is_refused_from_the_session_and_says_clone(client):
    """세션은 T02인데 T01의 스터디에 쓰면 스크립트는 T02에서 찾고 케이스는 T01에서 돈다. save_recipe·add_outputs의 폴더 어긋남을 여기서 막는다."""
    w = _world(client)
    other = client.post(f"/api/projects/{w['pid']}/works", json={"name": "옆", "name_auto": False, "agent": "demo"}, headers=w["owner"]).json()
    h = {**w["owner"], "X-DDS-Work": str(other["id"])}
    for method, url, kw in (("POST", f"/api/studies/{w['sid']}/recipe", {"files": {"file": ("r.zip", _zip(), "application/zip")}}),
                            ("POST", f"/api/studies/{w['sid']}/outputs", {"json": {"outputs": [{"name": "m"}]}}),
                            ("POST", f"/api/studies/{w['sid']}/train", {"json": {}}),
                            ("POST", f"/api/models/{w['mid']}/deploy", {})):
        r = client.request(method, url, headers=h, **kw)
        assert r.status_code == 403 and "clone_study" in r.json()["detail"] and "→" in r.json()["detail"], f"{url} {r.text}"
    # 복제는 된다. 복제본은 지금 Task에 생기고 원본은 그대로다
    made = client.post(f"/api/studies/{w['sid']}/clone", json={"name": "복제"}, headers=h)
    assert made.status_code == 200 and made.json()["work_id"] == other["id"]
    # 화면은 헤더를 보내지 않는다. 내 Task면 그대로 된다
    assert client.post(f"/api/studies/{w['sid']}/train", json={}, headers=w["owner"]).status_code == 200


def test_check_flow_message_says_clone_not_drop_the_flow(client):
    w = _world(client)
    other = client.post(f"/api/projects/{w['pid']}/works", json={"name": "옆", "agent": "demo"}, headers=w["owner"]).json()
    f = client.post(f"/api/projects/{w['pid']}/flows?work_id={other['id']}", json={"title": "옆 흐름", "goal": "", "nodes": NODES, "edges": []}, headers=w["owner"]).json()
    r = client.post(f"/api/studies/{w['sid']}/doe", json={"n": 1, "method": "lhs", "flow_id": f["id"]}, headers=w["owner"])
    assert r.status_code == 400, r.text
    assert "clone_study" in r.json()["detail"] and "비우" not in r.json()["detail"]


# ---------- 이전 기록의 멤버 ----------
def test_legacy_members_work_and_others_only_look(client):
    w = _world(client)
    with SessionLocal() as db:
        work = db.get(models.Work, w["wid"])
        work.legacy, work.seq, work.legacy_users = True, 0, [work.owner_id, *[u for u, in db.query(models.ProjectMember.user_id).filter_by(project_id=w["pid"], role="master")]]
        db.commit()
    assert client.post(f"/api/studies/{w['sid']}/train", json={}, headers=w["master"]).status_code == 200, "이전 기록에서 일하던 멤버는 그대로 일한다"
    denied = client.post(f"/api/studies/{w['sid']}/train", json={}, headers=w["member"])
    assert denied.status_code == 403 and "내 PC로 복사해서 하기" in denied.json()["detail"]


# ---------- 돌린 사람의 결과 ----------
def test_runner_keeps_uploading_and_stopping_after_losing_the_task(client):
    """주인이 바뀌어도 그 사람 PC에서 돌던 케이스의 결과와 중지는 그 사람 것이다."""
    w = _world(client)
    with SessionLocal() as db:
        case = db.get(models.Case, w["cid"])
        member_id = next(u.user_id for u in db.query(models.ProjectMember).filter_by(project_id=w["pid"], role="member") if u.user_id != w["uo"]["id"])
        case.run_by, case.status = member_id, "running"
        db.commit()
    r = client.post(f"/api/cases/{w['cid']}/archive", files={"file": ("c.h5", b"x", "application/octet-stream")}, headers=w["member"])
    assert r.status_code == 400, "주인 검사는 지나고 파일 검사에서 걸린다: " + r.text
    assert client.post(f"/api/studies/{w['sid']}/stop", json={}, headers=w["member"]).status_code != 403


def test_recipe_path_is_inside_files_dir(client):
    """표의 묶음 시험이 다른 프로젝트 폴더를 건드리지 않게 하는 확인."""
    w = _world(client)
    with SessionLocal() as db:
        assert settings.files_dir in recipe.recipe_path(db.get(models.Study, w["sid"])).parents
    assert service.ACTIVE
