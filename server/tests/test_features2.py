"""프로젝트·대화 관리, 첨부 파일, 형상·필드 학습(3D) 테스트."""
import base64

import numpy as np

from server.tests.test_api import _auth, _wait_idle, client  # noqa: F401 — 같은 서버 fixture 재사용


def test_favorite_rename_delete(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    p = client.post("/api/projects", json={"name": "관리 테스트"}, headers=admin).json()
    assert p["is_favorite"] is False
    client.put(f"/api/projects/{p['id']}/favorite", headers=admin)
    assert client.get(f"/api/projects/{p['id']}", headers=admin).json()["is_favorite"] is True
    client.delete(f"/api/projects/{p['id']}/favorite", headers=admin)
    assert client.get(f"/api/projects/{p['id']}", headers=admin).json()["is_favorite"] is False

    r = client.put(f"/api/projects/{p['id']}", json={"name": "이름 바뀜", "version": p["version"]}, headers=admin)
    assert r.json()["name"] == "이름 바뀜"

    s = client.post(f"/api/projects/{p['id']}/sessions", json={"agent": "demo"}, headers=admin).json()
    assert client.put(f"/api/sessions/{s['id']}", json={"title": "DOE 논의"}, headers=admin).json()["title"] == "DOE 논의"
    # 진행 중인 대화의 에이전트를 바꾸면 CLI 세션을 새로 열고(agent_session_id 비움) 이전 대화를 요약해 넘긴다
    from server.app import models
    from server.app.agent.hub import _recap
    from server.app.database import SessionLocal
    with SessionLocal() as db:
        db.add_all([models.ChatMessage(session_id=s["id"], role="user", content="두께 DOE 해 줘"),
                    models.ChatMessage(session_id=s["id"], role="agent", content="Flow를 제안했습니다")])
        db.query(models.ChatSession).filter_by(id=s["id"]).update({"agent_session_id": "old-sid"})
        db.commit()
    r = client.put(f"/api/sessions/{s['id']}", json={"agent": "claude"}, headers=admin).json()
    assert r["agent"] == "claude" and r["agent_session_id"] is None and r["title"] == "DOE 논의"
    with SessionLocal() as db:
        rc = _recap(db, s["id"])
    assert "사용자: 두께 DOE 해 줘" in rc and "에이전트: Flow를 제안했습니다" in rc
    # 다른 멤버(일반)는 남의 대화를 지울 수 없다
    kim, kim_user = _auth(client, "kim2", admin)
    client.put(f"/api/projects/{p['id']}/members/{kim_user['id']}", json={"role": "member"}, headers=admin)
    assert client.put(f"/api/sessions/{s['id']}", json={"agent": "gemini"}, headers=kim).status_code == 403
    assert client.delete(f"/api/sessions/{s['id']}", headers=kim).status_code == 403
    assert client.delete(f"/api/sessions/{s['id']}", headers=admin).status_code == 200
    assert client.get(f"/api/projects/{p['id']}/sessions", headers=admin).json() == []
    assert client.delete(f"/api/projects/{p['id']}", headers=admin).status_code == 200


def test_local_folder_change(client, monkeypatch):  # noqa: F811
    """작업 폴더를 바꾸면 PC Agent에 uid와 경로를 넘기고, 내 대화는 다음 요청부터 새 세션(새 폴더)으로 이어 간다."""
    from server.app import models
    from server.app.agent.link import registry
    from server.app.database import SessionLocal

    calls = []

    class FakeRunner:
        def call(self, type_, payload, timeout=30):
            calls.append((type_, payload))
            return {"path": payload.get("path") or "C:/DDS_Workspace/P0001_x", "custom": bool(payload.get("path")), "root": "C:/DDS_Workspace"}

    monkeypatch.setattr(registry, "runner", lambda uid: FakeRunner())
    admin, _ = _auth(client, "admin")
    p = client.post("/api/projects", json={"name": "폴더 테스트"}, headers=admin).json()
    s = client.post(f"/api/projects/{p['id']}/sessions", json={"agent": "demo"}, headers=admin).json()
    with SessionLocal() as db:
        db.query(models.ChatSession).filter_by(id=s["id"]).update({"agent_session_id": "old-sid"})
        db.commit()
    r = client.put(f"/api/projects/{p['id']}/local/folder", json={"path": "D:/Work/브래킷"}, headers=admin)
    assert r.status_code == 200 and r.json()["custom"] is True
    t, payload = calls[-1]
    with SessionLocal() as db:
        uid = db.get(models.Project, p["id"]).uid
        assert db.get(models.ChatSession, s["id"]).agent_session_id is None
    assert t == "local.folder.set" and uid and payload["project_uid"] == uid and payload["path"] == "D:/Work/브래킷"
    assert client.get(f"/api/projects/{p['id']}/local/folder", headers=admin).json()["path"]
    assert client.post("/api/agent/check-folder", json={"path": ""}, headers=admin).status_code == 422
    client.delete(f"/api/projects/{p['id']}", headers=admin)


def test_agent_setup_readiness(client):  # noqa: F811
    """사내 서버(저장소만 받은 상태)에서 PC Agent 설치 준비: 상태 확인 → 서버 코드로 첫 버전 배포."""
    admin, _ = _auth(client, "admin")
    st = client.get("/api/agent/setup/status", headers=admin).json()
    assert st["runtime"] is False and st["ready"] is False and st["runtime_dir"]  # 테스트 데이터 폴더에는 런타임이 없다
    r = client.post("/api/agent/releases/from-server", headers=admin)
    assert r.status_code == 200, r.text
    ver = r.json()["version"]
    assert r.json()["is_latest"] and client.get("/api/agent/setup/status", headers=admin).json()["release"] == ver
    assert client.post("/api/agent/releases/from-server", headers=admin).status_code == 409  # 같은 버전은 한 번만
    kim, _ = _auth(client, "kim3", admin)
    assert client.post("/api/agent/releases/from-server", headers=kim).status_code == 403
    assert client.get("/api/agent/setup/status", headers=kim).json()["runtime_dir"] is None  # 서버 경로는 관리자에게만


def test_attachments(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "첨부"}, headers=admin).json()["id"]
    data = b"STEP-FILE-CONTENT" * 1000
    a = client.post(f"/api/projects/{pid}/attachments", files={"file": ("bracket v2.stp", data)}, headers=admin).json()
    assert a["filename"] == "bracket v2.stp" and a["size"] == len(data)
    assert client.get(f"/api/attachments/{a['id']}/download", headers=admin).content == data
    # 다른 프로젝트의 첨부는 프롬프트에 쓸 수 없다
    pid2 = client.post("/api/projects", json={"name": "다른"}, headers=admin).json()["id"]
    s2 = client.post(f"/api/projects/{pid2}/sessions", json={"agent": "demo"}, headers=admin).json()
    r = client.post(f"/api/sessions/{s2['id']}/prompt", json={"text": "봐줘", "attachment_ids": [a["id"]]}, headers=admin)
    assert r.status_code == 404


def _f32(b64: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(b64), dtype=np.float32)


def test_field_model_3d(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "3D 필드"}, headers=admin).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "Beam3D", "adapter": "mock", "adapter_config": {"function": "beam3d"},
        "variables": [{"name": "width", "lower": 10, "upper": 40}, {"name": "height", "lower": 20, "upper": 80}],
        "objectives": [{"name": "max_stress", "goal": "min"}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": 16, "seed": 5}, headers=admin)
    client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    _wait_idle(client, sid, admin)
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert all(c["fields"] == ["stress"] for c in cases)

    fm = client.post(f"/api/studies/{sid}/train_field", json={"field": "stress"}, headers=admin).json()
    assert fm["shape_varies"] is True and fm["info"]["modes"] >= 1 and fm["n_nodes"] == 1218
    mesh = client.get(f"/api/field-models/{fm['id']}/mesh", headers=admin).json()
    assert mesh["n_triangles"] == 2432
    pred = client.post(f"/api/field-models/{fm['id']}/predict", json={"params": {"width": 20, "height": 50}}, headers=admin).json()
    vals, pts = _f32(pred["values_b64"]), _f32(pred["points_b64"]).reshape(-1, 3)
    assert len(vals) == 1218 and pts.shape == (1218, 3)
    assert abs(vals.max() - 120.0) / 120.0 < 0.15, vals.max()  # 해석해 최대 응력 120 MPa
    assert abs(np.ptp(pts[:, 1]) - 20) < 2.0  # 예측 형상의 폭 ≈ 20 mm (형상 학습)

    # Simulator 공유 링크로도 3D 예측
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    client.post(f"/api/models/{m['id']}/deploy", headers=admin)
    tok = client.post(f"/api/models/{m['id']}/shares", json={}, headers=admin).json()["token"]
    other, _ = _auth(client, "lee3d", admin)
    fms = client.get(f"/api/shares/{tok}/field-models", headers=other).json()
    assert fms[0]["field"] == "stress"
    r = client.post(f"/api/shares/{tok}/field-models/{fms[0]['id']}/predict", json={"params": {"width": 30, "height": 60}}, headers=other)
    assert r.status_code == 200 and r.json()["max"] > 0


def test_constraints_best_and_bo(client):  # noqa: F811
    """제약(max_stress ≤ 250)이 있으면 '현재 최적'과 BO가 제약을 만족하는 설계를 고른다."""
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "제약"}, headers=admin).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "브래킷", "adapter": "mock", "adapter_config": {"function": "bracket"},
        "variables": [{"name": "thickness", "lower": 6, "upper": 14}, {"name": "rib_height", "lower": 10, "upper": 40}],
        "objectives": [{"name": "mass", "goal": "min"}], "constraints": [{"name": "max_stress", "upper": 250}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"method": "lhs", "n": 16, "seed": 3}, headers=admin)
    client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    st = _wait_idle(client, sid, admin)
    assert st["study"]["constraints"] == [{"name": "max_stress", "upper": 250.0}]
    done = [c for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json() if c["status"] == "complete"]
    assert min(c["results"]["mass"] for c in done) < st["best"]["results"]["mass"]  # 제약 없는 최소는 응력 초과
    assert st["best"]["results"]["max_stress"] <= 250
    client.post(f"/api/studies/{sid}/train", json={"outputs": ["mass", "max_stress"]}, headers=admin)
    for _ in range(3):
        client.post(f"/api/studies/{sid}/suggest", json={"mode": "bo", "n": 1}, headers=admin)
        client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
        _wait_idle(client, sid, admin)
    best = client.get(f"/api/studies/{sid}", headers=admin).json()["best"]["results"]
    assert best["max_stress"] <= 250 and best["mass"] <= st["best"]["results"]["mass"]
