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
