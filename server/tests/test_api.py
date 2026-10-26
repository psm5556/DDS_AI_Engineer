import os
import tempfile
import time

os.environ["DDS_DATA_DIR"] = tempfile.mkdtemp(prefix="dds_test_")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _auth(client, username, admin_headers=None):
    client.post("/api/auth/register", json={"username": username, "pin": "123456", "display_name": username})
    if admin_headers:
        uid = next(u["id"] for u in client.get("/api/users", headers=admin_headers).json() if u["username"] == username)
        client.put(f"/api/users/{uid}", json={"is_active": True}, headers=admin_headers)
    r = client.post("/api/auth/login", json={"username": username, "pin": "123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["user"]


def test_auth_and_sharing(client):
    admin, admin_user = _auth(client, "admin")
    assert admin_user["is_admin"]
    # 두 번째 사용자는 승인 전 로그인 불가
    client.post("/api/auth/register", json={"username": "kim", "pin": "123456", "display_name": "김"})
    assert client.post("/api/auth/login", json={"username": "kim", "pin": "123456"}).status_code == 403
    kim, kim_user = _auth(client, "kim", admin)

    p = client.post("/api/projects", json={"name": "브래킷 최적화"}, headers=admin).json()
    assert p["user_role"] == "master"
    assert client.get(f"/api/projects/{p['id']}", headers=kim).status_code == 403
    client.put(f"/api/projects/{p['id']}/members/{kim_user['id']}", json={"role": "member"}, headers=admin)
    assert client.get(f"/api/projects/{p['id']}", headers=kim).json()["user_role"] == "member"
    # member는 프로젝트 수정 불가
    assert client.put(f"/api/projects/{p['id']}", json={"name": "x", "version": 1}, headers=kim).status_code == 403
    # API 토큰으로도 인증
    tok = client.post("/api/tokens", json={"name": "내 PC"}, headers=kim).json()["token"]
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()["username"] == "kim"


def test_flow_lifecycle(client):
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "flow"}, headers=admin).json()["id"]
    body = {"title": "브래킷 DOE", "nodes": [{"id": "cad", "label": "NX 형상 변경", "type": "cad"},
                                           {"id": "solve", "label": "Abaqus 해석", "type": "solve"}],
            "edges": [{"source": "cad", "target": "solve"}]}
    f = client.post(f"/api/projects/{pid}/flows", json=body, headers=admin).json()
    assert f["status"] == "review"
    f = client.put(f"/api/flows/{f['id']}/status", json={"status": "approved", "version": f["version"]}, headers=admin).json()
    f = client.put(f"/api/flows/{f['id']}/nodes/cad", json={"status": "complete"}, headers=admin).json()
    assert f["status"] == "in_progress"
    f = client.put(f"/api/flows/{f['id']}/nodes/solve", json={"status": "complete"}, headers=admin).json()
    assert f["status"] == "complete"
    bad = dict(body, edges=[{"source": "cad", "target": "nope"}])
    assert client.post(f"/api/projects/{pid}/flows", json=bad, headers=admin).status_code == 400


def _wait_idle(client, sid, headers, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/studies/{sid}", headers=headers).json()
        if not st["running"]:
            return st
        time.sleep(0.2)
    raise TimeoutError


def test_doe_train_bo_on_server_mock(client):
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "branin"}, headers=admin).json()["id"]
    study = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "Branin", "adapter": "mock", "adapter_config": {"function": "branin"},
        "variables": [{"name": "x1", "lower": -5, "upper": 10}, {"name": "x2", "lower": 0, "upper": 15}],
        "objectives": [{"name": "f", "goal": "min"}]}).json()
    sid = study["id"]
    cases = client.post(f"/api/studies/{sid}/doe", json={"method": "lhs", "n": 25, "seed": 1}, headers=admin).json()
    assert len(cases) == 25
    assert client.post(f"/api/studies/{sid}/run", json={}, headers=admin).json()["queued"] == 25
    st = _wait_idle(client, sid, admin)
    assert st["counts"].get("complete") == 25

    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert m["metrics"]["f"]["r2"] > 0.8, m["metrics"]

    first_best = st["best"]["results"]["f"]
    for _ in range(8):
        client.post(f"/api/studies/{sid}/suggest", json={"mode": "bo", "n": 1}, headers=admin)
        client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
        _wait_idle(client, sid, admin)
        client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    best = client.get(f"/api/studies/{sid}", headers=admin).json()["best"]["results"]["f"]
    assert best <= first_best
    assert best < 1.0, best  # Branin 전역최소 0.398

    model_id = client.get(f"/api/projects/{pid}/models", headers=admin).json()[0]["id"]
    assert client.post(f"/api/models/{model_id}/deploy", headers=admin).json()["deployed"]
    pred = client.post(f"/api/models/{model_id}/predict", json={"params": {"x1": 3.14, "x2": 2.27}}, headers=admin).json()
    assert abs(pred["mean"]["f"] - 0.398) < 2.0
    arts = client.get(f"/api/projects/{pid}/artifacts", headers=admin).json()
    kinds = {a["kind"] for a in arts}
    assert {"scatter", "table", "line"} <= kinds


def test_pin_rules_and_lockout(client):
    admin, _ = _auth(client, "admin")
    r = client.post("/api/auth/register", json={"username": "lee", "pin": "12ab56", "display_name": "이"})
    assert r.status_code == 400  # 숫자 6자리만
    client.post("/api/auth/register", json={"username": "lee", "pin": "654321", "display_name": "이"})
    uid = next(u["id"] for u in client.get("/api/users", headers=admin).json() if u["username"] == "lee")
    client.put(f"/api/users/{uid}", json={"is_active": True}, headers=admin)
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "lee", "pin": "000000"}).status_code == 401
    # 5회 실패 → 맞는 PIN도 잠김
    assert client.post("/api/auth/login", json={"username": "lee", "pin": "654321"}).status_code == 423
    # 관리자 PIN 초기화 → 잠금 해제
    client.put(f"/api/users/{uid}", json={"pin": "111222"}, headers=admin)
    assert client.post("/api/auth/login", json={"username": "lee", "pin": "111222"}).status_code == 200
