"""Simulator = 스터디 하나, 모델 = 그 버전. 현재 버전은 하나이고 공유 링크는 현재 버전을 따라간다. 내 Simulator·공유받은 Simulator 목록."""
from server.tests.test_api import _auth, _wait_idle, client  # noqa: F401 — 같은 서버 fixture 재사용


def _study(client, h, name, function="beam3d", n=6):  # noqa: F811
    pid = client.post("/api/projects", json={"name": name}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", headers=h, json={
        "name": name, "adapter": "mock", "adapter_config": {"function": function},
        "variables": [{"name": "width", "lower": 10, "upper": 40}, {"name": "height", "lower": 20, "upper": 80}],
        "objectives": [{"name": "max_stress", "goal": "min"}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": n, "seed": 2}, headers=h)
    client.post(f"/api/studies/{sid}/run", json={}, headers=h)
    _wait_idle(client, sid, h)
    return pid, sid


def _train(client, sid, h):  # noqa: F811
    return client.post(f"/api/studies/{sid}/train", json={}, headers=h).json()


def _deploy(client, mid, h):  # noqa: F811
    r = client.post(f"/api/models/{mid}/deploy", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_versions_count_per_study_and_one_is_current(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid = _study(client, admin, "버전")
    v1, v2 = _train(client, sid, admin), _train(client, sid, admin)
    assert (v1["version"], v2["version"]) == (1, 2)
    _, other_sid = _study(client, admin, "다른 스터디", n=4)
    assert _train(client, other_sid, admin)["version"] == 1  # 스터디마다 따로 센다

    assert _deploy(client, v1["id"], admin)["deployed"]
    assert _deploy(client, v2["id"], admin)["deployed"]
    rows = {m["id"]: m for m in client.get(f"/api/projects/{pid}/models", headers=admin).json()}
    assert rows[v2["id"]]["deployed"] and not rows[v1["id"]]["deployed"]  # 현재 버전은 하나
    assert rows[v2["id"]]["deployed_at"]

    assert _deploy(client, v1["id"], admin)["deployed"]  # 되돌리기
    rows = {m["id"]: m for m in client.get(f"/api/projects/{pid}/models", headers=admin).json()}
    assert rows[v1["id"]]["deployed"] and not rows[v2["id"]]["deployed"]


def test_spec_says_which_version_is_current(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    _, sid = _study(client, admin, "현재 버전")
    v1, v2 = _train(client, sid, admin), _train(client, sid, admin)
    _deploy(client, v2["id"], admin)
    spec = client.get(f"/api/models/{v1['id']}/spec", headers=admin).json()
    assert spec["model"]["version"] == 1 and spec["current_model_id"] == v2["id"]
    assert [m["version"] for m in spec["versions"]] == [2, 1]  # 최신이 위


def test_share_link_follows_current_version(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    other, _ = _auth(client, "viewer1", admin)
    _, sid = _study(client, admin, "링크")
    v1, v2 = _train(client, sid, admin), _train(client, sid, admin)
    _deploy(client, v1["id"], admin)
    tok = client.post(f"/api/models/{v1['id']}/shares", json={"note": "설계팀"}, headers=admin).json()["token"]
    assert client.get(f"/api/shares/{tok}", headers=other).json()["model"]["version"] == 1
    _deploy(client, v2["id"], admin)
    assert client.get(f"/api/shares/{tok}", headers=other).json()["model"]["version"] == 2  # 같은 링크가 새 버전을 보여 준다
    # 옛 버전에서 만든 링크도 지금 버전의 공유 목록에 보인다(재학습해도 관리할 수 있게)
    assert [s["token"] for s in client.get(f"/api/models/{v2['id']}/shares", headers=admin).json()] == [tok]
    # 옛 버전으로는 새 링크를 만들지 않는다
    assert client.post(f"/api/models/{v1['id']}/shares", json={}, headers=admin).status_code == 400


def test_library_lists_mine_and_shared(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    other, _ = _auth(client, "viewer2", admin)
    _, sid = _study(client, admin, "목록")
    m = _train(client, sid, admin)
    assert all(x["study_id"] != sid for x in client.get("/api/simulators", headers=admin).json()["mine"])  # 배포 전에는 없다
    _deploy(client, m["id"], admin)
    mine = next(x for x in client.get("/api/simulators", headers=admin).json()["mine"] if x["study_id"] == sid)
    assert mine["url"] == f"/simulator/{m['id']}" and mine["version"] == 1 and mine["outputs"] == ["max_stress"]
    assert mine["project_name"] == "목록" and mine["source"] == "mine"

    # 아직 열어 보지 않은 링크는 받은 사람 목록에 없다
    tok = client.post(f"/api/models/{m['id']}/shares", json={"note": "설계팀"}, headers=admin).json()["token"]
    assert all(x["study_id"] != sid for x in client.get("/api/simulators", headers=other).json()["shared"])
    client.get(f"/api/shares/{tok}", headers=other)
    got = client.get("/api/simulators", headers=other).json()
    row = next(x for x in got["shared"] if x["study_id"] == sid)
    assert row["url"] == f"/s/{tok}" and row["source"] == "link" and row["status"] == "active"
    assert row["shared_by"] == me["display_name"] and row["note"] == "설계팀"
    assert row["project_id"] is None and row["project_name"] is None  # 프로젝트 정보는 받은 사람에게 보이지 않는다
    assert all(x["study_id"] != sid for x in got["mine"])
    # 만든 사람이 자기 링크를 열어도 공유받은 목록에 들어가지 않는다
    client.get(f"/api/shares/{tok}", headers=admin)
    assert all(x["study_id"] != sid for x in client.get("/api/simulators", headers=admin).json()["shared"])


def test_stopped_share_stays_listed_and_can_be_removed(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    other, _ = _auth(client, "viewer3", admin)
    _, sid = _study(client, admin, "중지")
    m = _train(client, sid, admin)
    _deploy(client, m["id"], admin)
    tok = client.post(f"/api/models/{m['id']}/shares", json={}, headers=admin).json()["token"]
    client.get(f"/api/shares/{tok}", headers=other)
    client.delete(f"/api/shares/{tok}", headers=admin)
    row = next(x for x in client.get("/api/simulators", headers=other).json()["shared"] if x["study_id"] == sid)
    assert row["status"] == "stopped"  # 사라지면 왜 없어졌는지 알 수 없다
    assert client.delete(f"/api/simulators/shared/{row['share_id']}", headers=other).status_code == 200
    assert all(x["study_id"] != sid for x in client.get("/api/simulators", headers=other).json()["shared"])
    assert client.delete(f"/api/simulators/shared/{row['share_id']}", headers=admin).status_code == 404  # 내 기록만 지운다


def test_project_member_sees_teammates_simulator_as_shared(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    member, member_user = _auth(client, "teammate1", admin)
    pid, sid = _study(client, admin, "같은 프로젝트")
    client.put(f"/api/projects/{pid}/members/{member_user['id']}", json={"role": "member"}, headers=admin)
    m = _train(client, sid, admin)
    _deploy(client, m["id"], admin)
    got = client.get("/api/simulators", headers=member).json()
    row = next(x for x in got["shared"] if x["study_id"] == sid)
    assert row["source"] == "project" and row["url"] == f"/simulator/{m['id']}" and row["project_name"] == "같은 프로젝트"
    # 링크로도 열었다면 한 번만 보인다
    tok = client.post(f"/api/models/{m['id']}/shares", json={}, headers=admin).json()["token"]
    client.get(f"/api/shares/{tok}", headers=member)
    got = client.get("/api/simulators", headers=member).json()
    assert [x["source"] for x in got["shared"] if x["study_id"] == sid] == ["project"]


def test_3d_model_is_tied_to_the_deployed_version(client):  # noqa: F811
    """되돌리면 3D도 그때 것으로 돌아간다. 예측값과 3D가 서로 다른 버전이 되지 않게 한다."""
    admin, _ = _auth(client, "admin")
    other, _ = _auth(client, "viewer4", admin)
    _, sid = _study(client, admin, "3D")
    v1 = _train(client, sid, admin)
    f1 = client.post(f"/api/studies/{sid}/train_field", json={"field": "stress"}, headers=admin).json()
    _deploy(client, v1["id"], admin)
    v2 = _train(client, sid, admin)
    f2 = client.post(f"/api/studies/{sid}/train_field", json={"field": "stress"}, headers=admin).json()
    # 현재 버전(v1)이 배포 중일 때 학습한 3D는 현재 버전에 붙는다
    ids = lambda mid: [f["id"] for f in client.get(f"/api/studies/{sid}/field-models", params={"model_id": mid}, headers=admin).json()]  # noqa: E731
    assert ids(v1["id"]) == [f2["id"]]
    _deploy(client, v2["id"], admin)
    assert ids(v2["id"]) == [f2["id"]]
    assert f1["id"] != f2["id"]
    # 같은 필드는 하나만 돌려준다(전에는 재학습마다 버튼이 늘었다)
    tok = client.post(f"/api/models/{v2['id']}/shares", json={}, headers=admin).json()["token"]
    assert [f["id"] for f in client.get(f"/api/shares/{tok}/field-models", headers=other).json()] == [f2["id"]]


def test_rollback_restores_3d_snapshot(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    _, sid = _study(client, admin, "3D 되돌리기")
    v1 = _train(client, sid, admin)
    f1 = client.post(f"/api/studies/{sid}/train_field", json={"field": "stress"}, headers=admin).json()
    _deploy(client, v1["id"], admin)
    v2 = _train(client, sid, admin)
    _deploy(client, v2["id"], admin)
    f2 = client.post(f"/api/studies/{sid}/train_field", json={"field": "stress"}, headers=admin).json()  # v2에 붙는다
    ids = lambda mid: [f["id"] for f in client.get(f"/api/studies/{sid}/field-models", params={"model_id": mid}, headers=admin).json()]  # noqa: E731
    assert ids(v2["id"]) == [f2["id"]] and ids(v1["id"]) == [f1["id"]]
    _deploy(client, v1["id"], admin)
    assert ids(v1["id"]) == [f1["id"]]


def test_old_data_is_backfilled(client):  # noqa: F811
    """버전이 없던 때의 모델: 번호를 매기고, 여러 개 배포되어 있으면 가장 최근 것만 현재 버전으로 둔다."""
    from server.app import models
    from server.app.database import SessionLocal
    from server.app.main import _backfill_model_versions

    admin, me = _auth(client, "admin")
    _, sid = _study(client, admin, "예전 데이터")
    a, b = _train(client, sid, admin), _train(client, sid, admin)
    with SessionLocal() as db:
        for mid in (a["id"], b["id"]):
            m = db.get(models.SurrogateModel, mid)
            m.version, m.created_by, m.deployed, m.deployed_at = None, None, True, None
        db.commit()
    _backfill_model_versions()
    with SessionLocal() as db:
        ma, mb = db.get(models.SurrogateModel, a["id"]), db.get(models.SurrogateModel, b["id"])
        assert (ma.version, mb.version) == (1, 2) and (ma.deployed, mb.deployed) == (False, True)
        assert mb.created_by == me["id"] and mb.deployed_at is not None
