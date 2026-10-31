"""토큰 범위와 PIN 잠금(마켓 0단계). AI가 쓰는 토큰과 PC에서 도는 코드가 관리자 권한을 쓰지 못하게 고정한다."""
import importlib
import io
import pkgutil
import re
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

from server.tests.test_api import _auth, client  # noqa: F401, I001 — test_api가 임시 데이터 폴더를 정한 뒤에 서버 코드를 불러온다

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from server.app import models  # noqa: E402
from server.app.agent.link import registry  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.deps import (MAX_FAILED_LOGINS, get_current_user, new_api_token, require_admin, require_devtest_admin,  # noqa: E402
                             require_jwt)
from server.app import routers  # noqa: E402
from server.app.routers import agents  # noqa: E402
from server.app.study.recipe import RecipeRejected, _safe_path, check_bundle  # noqa: E402
from server.app.ws import manager  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _issued(client, jwt_headers):  # noqa: F811
    """설치 프로그램·설정 화면과 같은 길: 웹 로그인으로 토큰을 받는다."""
    r = client.post("/api/tokens", json={"name": "내 PC"}, headers=jwt_headers)
    assert r.status_code == 200, r.text
    return _bearer(r.json()["token"]), r.json()


def _db_token(user_id, scope):
    """pc_verify token처럼 서버 DB에 바로 넣는다. scope=None은 이 컬럼이 생기기 전에 만든 토큰이다."""
    raw, hashed = new_api_token()
    with SessionLocal() as db:
        db.add(models.ApiToken(user_id=user_id, name="시험", token_hash=hashed, scope=scope))
        db.commit()
    return _bearer(raw)


class _Conn:
    def __init__(self, user_id):
        self.user_id, self.info, self.sent = user_id, {"hostname": "PC-1", "version": "1.0"}, []

    def send(self, m):
        self.sent.append(m)

    def request(self, type_, payload, timeout=None):
        return {"ok": True, "returncode": 0, "seconds": 0.1}


# ---------- agent 토큰 ----------
ADMIN_ONLY = [
    ("POST", "/api/agent/releases", {"data": {"version": "9.9.9"}, "files": {"file": ("a.zip", b"PK")}}),
    ("POST", "/api/agent/releases/from-server", {}),
    ("PUT", "/api/agent/releases/1.0.0/latest", {}),
    ("GET", "/api/agent/instances", {}),
    ("PUT", "/api/users/1", {"json": {"display_name": "x"}}),
    ("PUT", "/api/devtest/permission", {"json": {"allow": True}}),
    ("GET", "/api/devtest/targets", {}),
    ("POST", "/api/devtest/run", {"json": {"op": "info"}}),
    ("POST", "/api/devtest/deploy", {"json": {}}),
]


@pytest.mark.parametrize("scope", ["agent", None, "모르는값"])
@pytest.mark.parametrize("method,path,kw", ADMIN_ONLY, ids=[f"{m} {p}" for m, p, _ in ADMIN_ONLY])
def test_admin_token_has_no_admin_power(client, method, path, kw, scope):  # noqa: F811
    _, me = _auth(client, "admin")
    r = client.request(method, path, headers=_db_token(me["id"], scope), **kw)
    assert r.status_code == 403, r.text
    assert "→" in r.json()["detail"]  # 원인과 할 일


def test_issued_token_is_agent_scope_and_web_still_admin(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    tok, created = _issued(client, admin)
    assert created["scope"] == "agent"
    assert client.get("/api/auth/me", headers=tok).json()["username"] == "admin"  # 설치 스크립트가 쓰는 확인
    assert any(t["id"] == created["id"] and t["scope"] == "agent" for t in client.get("/api/tokens", headers=admin).json())  # 설정 화면
    assert client.get("/api/agent/instances", headers=admin).status_code == 200
    assert client.get("/api/agent/instances", headers=tok).status_code == 403
    assert client.delete(f"/api/tokens/{created['id']}", headers=admin).status_code == 200
    assert client.get("/api/auth/me", headers=tok).status_code == 401


def test_token_cannot_make_tokens(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    kim, kim_user = _auth(client, "sc_kim", admin)
    for tok in (_issued(client, admin)[0], _issued(client, kim)[0], _db_token(me["id"], "devtest")):
        assert client.post("/api/tokens", json={"name": "더 만들기"}, headers=tok).status_code == 403


def test_agent_token_keeps_ordinary_work_in_own_projects(client):  # noqa: F811
    """MCP가 실제로 부르는 길: 프로젝트 만들기, 작업 흐름, 결과 게시, 스터디, 멤버인 프로젝트 조회."""
    admin, _ = _auth(client, "admin")
    kim, kim_user = _auth(client, "sc_kim", admin)
    for who in (admin, kim):
        tok, _ = _issued(client, who)
        pid = client.post("/api/projects", json={"name": "AI가 만든 프로젝트"}, headers=tok).json()["id"]
        assert client.get(f"/api/projects/{pid}", headers=tok).json()["user_role"] == "master"
        flow = client.post(f"/api/projects/{pid}/flows", headers=tok, json={"title": "t", "nodes": [{"id": "a", "label": "a", "type": "cad"}], "edges": []})
        assert flow.status_code == 200, flow.text
        art = client.post(f"/api/projects/{pid}/artifacts", headers=tok, json={"kind": "table", "title": "t", "payload": {"rows": []}})
        assert art.status_code == 200, art.text
        st = client.post(f"/api/projects/{pid}/studies", headers=tok, json={
            "name": "s", "adapter": "mock", "variables": [{"name": "x", "lower": 0, "upper": 1}], "objectives": [{"name": "f", "direction": "minimize"}]})
        assert st.status_code == 200, st.text
        assert client.get("/api/adapters", headers=tok).status_code == 200
        assert client.get("/api/agent/settings", headers=tok).status_code == 200
        assert client.get("/api/agent/releases/latest", headers=tok).status_code in (200, 404)  # 자동 업데이트 확인


def test_admin_ai_works_in_member_project_but_not_elsewhere(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    kim, _ = _auth(client, "sc_kim", admin)
    tok, _ = _issued(client, admin)
    pid = client.post("/api/projects", json={"name": "김의 프로젝트"}, headers=kim).json()["id"]

    # 멤버가 아니면 웹(JWT)만 된다
    assert client.get(f"/api/projects/{pid}", headers=admin).json()["user_role"] == "master"
    r = client.get(f"/api/projects/{pid}", headers=tok)
    assert r.status_code == 403 and "멤버" in r.json()["detail"] and "→" in r.json()["detail"]
    assert pid in [p["id"] for p in client.get("/api/projects", headers=admin).json()]
    assert pid not in [p["id"] for p in client.get("/api/projects", headers=tok).json()]
    assert client.put(f"/api/projects/{pid}", json={"name": "x", "version": 1}, headers=tok).status_code == 403

    # 멤버로 넣으면 AI도 일한다. 역할은 멤버 그대로이고 관리자라고 master가 되지 않는다
    assert client.put(f"/api/projects/{pid}/members/{me['id']}", json={"role": "member"}, headers=admin).status_code == 200
    assert client.get(f"/api/projects/{pid}", headers=tok).json()["user_role"] == "member"
    assert client.post(f"/api/projects/{pid}/artifacts", headers=tok, json={"kind": "table", "title": "t", "payload": {"rows": []}}).status_code == 200
    assert client.put(f"/api/projects/{pid}", json={"name": "x", "version": 1}, headers=tok).status_code == 403
    assert client.get(f"/api/projects/{pid}", headers=admin).json()["user_role"] == "member"


def test_caller_checks_use_effective_admin(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    kim, kim_user = _auth(client, "sc_kim", admin)
    tok, _ = _issued(client, admin)
    client.post("/api/auth/register", json={"username": "sc_waiting", "pin": "123456", "display_name": "대기"})

    # list_users: 승인 전 계정은 웹 관리자만 본다
    assert "sc_waiting" in [u["username"] for u in client.get("/api/users", headers=admin).json()]
    assert "sc_waiting" not in [u["username"] for u in client.get("/api/users", headers=tok).json()]

    # request_update: 남의 PC에 지시는 웹 관리자만. 자기 PC는 토큰으로도 된다
    conn = _Conn(kim_user["id"])
    registry.conns[kim_user["id"]] = conn
    try:
        assert client.post(f"/api/agent/instances/{kim_user['id']}/update", headers=tok).status_code == 403
        assert client.post(f"/api/agent/instances/{kim_user['id']}/update", headers=admin).status_code != 403
    finally:
        registry.conns.pop(kim_user["id"], None)
    assert client.post(f"/api/agent/instances/{me['id']}/update", headers=tok).status_code != 403

    # 서버 경로는 웹 관리자에게만 보인다
    assert client.get("/api/agent/setup/status", headers=tok).json()["runtime_dir"] is None
    assert client.get("/api/agent/setup/status", headers=admin).json()["runtime_dir"]

    # 대화 이름 바꾸기: 만든 사람·master·웹 관리자만
    pid = client.post("/api/projects", json={"name": "대화"}, headers=kim).json()["id"]
    client.put(f"/api/projects/{pid}/members/{me['id']}", json={"role": "member"}, headers=admin)
    sid = client.post(f"/api/projects/{pid}/sessions", json={"agent": "claude"}, headers=kim).json()["id"]
    assert client.put(f"/api/sessions/{sid}", json={"title": "토큰"}, headers=tok).status_code == 403
    assert client.put(f"/api/sessions/{sid}", json={"title": "웹"}, headers=admin).status_code == 200


def test_every_is_admin_read_is_accounted_for():
    """호출자 권한을 is_admin으로 직접 보면 토큰이 관리자가 된다. 새로 쓰면 이 표를 고치면서 대상 계정인지 호출자인지 따져 본다."""
    expected = {
        "deps.py": 4,  # effective_admin, require_admin·require_devtest_admin의 관리자 아님 구분, require_member의 안내 문구 고르기
        "routers/agents.py": 1,  # settings_message: 대상 계정의 설정(토큰과 무관)
        "routers/auth.py": 1,  # update_user의 body.is_admin
        "routers/devtest.py": 1,  # _allowed: 대상 PC의 계정
    }
    found = {}
    app = ROOT / "server" / "app"
    for f in [app / "deps.py", app / "main.py", *sorted((app / "routers").glob("*.py"))]:
        n = len(re.findall(r"(?<!models\.User)\.is_admin\b", f.read_text(encoding="utf-8")))
        if n:
            found[f.relative_to(app).as_posix()] = n
    assert found == expected


# ---------- 관리자의 AI가 멤버 아닌 프로젝트에서 일하려 할 때 ----------
def test_admin_ai_blocked_before_start_in_non_member_project(client):  # noqa: F811
    """토큰에 관리자 권한이 없어 AI의 결과 올리기가 전부 실패한다. 조용히 망가지지 않게 시작할 때 할 일을 알린다."""
    admin, me = _auth(client, "admin")
    kim, _ = _auth(client, "sc_kim", admin)
    pid = client.post("/api/projects", json={"name": "멤버 아님"}, headers=kim).json()["id"]
    sid = client.post(f"/api/projects/{pid}/sessions", json={"agent": "claude"}, headers=admin).json()["id"]
    r = client.post(f"/api/sessions/{sid}/prompt", json={"text": "해 줘"}, headers=admin)
    assert r.status_code == 403 and "멤버" in r.json()["detail"] and "→" in r.json()["detail"]

    study = client.post(f"/api/projects/{pid}/studies", headers=kim, json={
        "name": "s", "adapter": "mock", "variables": [{"name": "x", "lower": 0, "upper": 1}], "objectives": [{"name": "f", "direction": "minimize"}]}).json()
    for path, body in ((f"/api/studies/{study['id']}/run", {}),
                       (f"/api/studies/{study['id']}/outputs", {"outputs": [{"name": "g", "direction": "minimize"}]}),
                       (f"/api/studies/{study['id']}/datasets", {"name": "d", "targets": []})):
        r = client.post(path, json=body, headers=admin)
        assert r.status_code == 403 and "멤버" in r.json()["detail"], f"{path}: {r.text}"

    client.put(f"/api/projects/{pid}/members/{me['id']}", json={"role": "member"}, headers=admin)
    assert client.post(f"/api/sessions/{sid}/prompt", json={"text": "해 줘"}, headers=admin).status_code != 403


# ---------- devtest 토큰 ----------
def test_devtest_token_reaches_only_devtest(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    tok = _db_token(me["id"], "devtest")
    for method, path in (("GET", "/api/auth/me"), ("GET", "/api/projects"), ("GET", "/api/tokens"), ("POST", "/api/projects"),
                         ("GET", "/api/users"), ("GET", "/api/agent/settings"), ("GET", "/api/agent/releases/latest"),
                         ("GET", "/api/agent/instances/mine"), ("GET", "/api/config/public")):
        r = client.request(method, path, headers=tok, json={"name": "x"} if method == "POST" else None)
        assert r.status_code == 403, f"{path}: {r.status_code} {r.text}"
    assert client.get("/api/devtest/permission", headers=tok).status_code == 200
    assert client.get("/api/devtest/targets", headers=tok).status_code == 200
    assert client.get("/api/devtest/history", headers=tok).status_code == 200
    # 허용 스위치는 사람이 웹에서 켠다. 토큰이 스스로 켜면 허용의 뜻이 없어진다
    assert client.put("/api/devtest/permission", json={"allow": True}, headers=tok).status_code == 403


def _guarded_routes(guard):
    """이 의존성이 걸린 모든 API 경로. 앞으로 생기는 경로도 저절로 시험에 들어온다."""
    def uses(dep):
        return dep.call is guard or any(uses(d) for d in dep.dependencies)
    out = []
    for info in pkgutil.iter_modules(routers.__path__):
        for r in importlib.import_module(f"{routers.__name__}.{info.name}").router.routes:
            if isinstance(r, APIRoute) and uses(r.dependant):
                out += [(m, re.sub(r"{[^}]+}", "1", r.path)) for m in sorted(r.methods)]
    assert out
    return out


def test_every_login_route_refuses_devtest_token_outside_devtest(client):  # noqa: F811
    _, me = _auth(client, "admin")
    tok = _db_token(me["id"], "devtest")
    for method, path in _guarded_routes(get_current_user):
        if not path.startswith("/api/devtest/"):
            r = client.request(method, path, headers=tok)
            assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text[:200]}"


def test_every_admin_route_refuses_agent_token_of_admin(client):  # noqa: F811
    _, me = _auth(client, "admin")
    tok = _db_token(me["id"], "agent")
    for guard in (require_admin, require_devtest_admin, require_jwt):
        for method, path in _guarded_routes(guard):
            r = client.request(method, path, headers=tok)
            assert r.status_code == 403, f"{method} {path}: {r.status_code} {r.text[:200]}"


def test_devtest_token_runs_on_allowed_pc_and_needs_admin_owner(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    kim, kim_user = _auth(client, "sc_kim", admin)
    tok = _db_token(me["id"], "devtest")
    assert client.get("/api/devtest/targets", headers=_db_token(kim_user["id"], "devtest")).status_code == 403
    assert client.post("/api/devtest/run", json={"op": "info"}, headers=_db_token(kim_user["id"], "devtest")).status_code == 403

    conn = _Conn(me["id"])
    registry.conns[me["id"]] = conn
    try:
        assert client.post("/api/devtest/run", json={"op": "info"}, headers=tok).status_code == 409  # 허용 전
        assert client.put("/api/devtest/permission", json={"allow": True}, headers=admin).status_code == 200
        assert [t["user_id"] for t in client.get("/api/devtest/targets", headers=tok).json()] == [me["id"]]
        assert client.post("/api/devtest/run", json={"op": "info"}, headers=tok).json()["ok"] is True
    finally:
        client.put("/api/devtest/permission", json={"allow": False}, headers=admin)
        registry.conns.pop(me["id"], None)


def test_devtest_deploy_publishes_and_tells_the_pc(client, monkeypatch):  # noqa: F811
    admin, me = _auth(client, "admin")
    tok = _db_token(me["id"], "devtest")
    assert client.post("/api/devtest/deploy", json={}, headers=tok).status_code == 409  # 허용한 PC가 없으면 아무것도 배포하지 않는다

    conn = _Conn(me["id"])
    registry.conns[me["id"]] = conn
    client.put("/api/devtest/permission", json={"allow": True}, headers=admin)
    conn.sent.clear()
    rel = SimpleNamespace(version="9.9.1", sha256="abc")
    try:
        monkeypatch.setattr(agents, "release_from_server", lambda admin, db: rel)
        r = client.post("/api/devtest/deploy", json={}, headers=tok)
        assert r.status_code == 200, r.text
        assert r.json() == {"version": "9.9.1", "published": True, "user_id": me["id"]}
        assert conn.sent == [{"type": "agent.update", "version": "9.9.1", "sha256": "abc", "force": True}]

        def already(admin, db):
            raise HTTPException(409, "이미 있는 버전입니다")
        monkeypatch.setattr(agents, "release_from_server", already)
        monkeypatch.setattr(agents, "latest_release", lambda db: rel)
        assert client.post("/api/devtest/deploy", json={}, headers=tok).json()["published"] is False
        assert client.get("/api/devtest/history", headers=admin).json()[0]["op"] == "deploy"
    finally:
        client.put("/api/devtest/permission", json={"allow": False}, headers=admin)
        registry.conns.pop(me["id"], None)


def test_pc_verify_chat_token_is_ordinary_and_devtest_is_narrow(client):  # noqa: F811
    """pc_verify chat은 웹앱과 똑같이 일반 API를 쓰므로 agent 토큰, 나머지는 devtest 토큰을 쓴다."""
    text = (ROOT / "scripts" / "pc_verify.py").read_text(encoding="utf-8")
    assert "/api/agent/releases/from-server" not in text and "/api/agent/instances" not in text and "/api/auth/me" not in text
    assert 'scope=SCOPE_DEVTEST' in text and 'scope=SCOPE_AGENT' in text


# ---------- WebSocket ----------
def _wait(cond):
    for _ in range(100):
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_agent_websocket_with_agent_token_of_admin(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    tok, _ = _issued(client, admin)
    raw = tok["Authorization"].split()[1]
    with client.websocket_connect(f"/ws/agent?token={raw}"):
        assert _wait(lambda: registry.get(me["id"]) is not None)
    assert _wait(lambda: registry.get(me["id"]) is None)


def test_devtest_token_cannot_be_an_agent_or_listen(client):  # noqa: F811
    _, me = _auth(client, "admin")
    raw = _db_token(me["id"], "devtest")["Authorization"].split()[1]
    with pytest.raises(WebSocketDisconnect) as e, client.websocket_connect(f"/ws/agent?token={raw}") as ws:
        ws.receive_text()
    assert e.value.code == 4401
    assert registry.get(me["id"]) is None
    with pytest.raises(WebSocketDisconnect), client.websocket_connect(f"/ws?token={raw}") as ws:
        ws.receive_text()


def test_browser_websocket_gives_admin_view_only_to_web_login(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    jwt = admin["Authorization"].split()[1]
    raw = _issued(client, admin)[0]["Authorization"].split()[1]
    before = set(manager.active)
    with client.websocket_connect(f"/ws?token={jwt}"):
        assert _wait(lambda: set(manager.active) - before)
        assert manager.active[next(iter(set(manager.active) - before))] == (me["id"], True)
    assert _wait(lambda: set(manager.active) == before)
    with client.websocket_connect(f"/ws?token={raw}"):
        assert _wait(lambda: set(manager.active) - before)
        assert manager.active[next(iter(set(manager.active) - before))] == (me["id"], False)  # 멤버 아닌 프로젝트의 소식을 받지 않는다


# ---------- PIN ----------
def _pin_user(client, name):  # noqa: F811
    admin, _ = _auth(client, "admin")
    headers, user = _auth(client, name, admin)
    return admin, headers, user


def test_pin_change_refuses_tokens(client):  # noqa: F811
    _, jwt, user = _pin_user(client, "sc_pin_token")
    tok, _ = _issued(client, jwt)
    r = client.put("/api/auth/pin", json={"current_pin": "123456", "new_pin": "654321"}, headers=tok)
    assert r.status_code == 403
    r = client.put("/api/auth/pin", json={"current_pin": "123456", "new_pin": "654321"}, headers=_db_token(user["id"], "devtest"))
    assert r.status_code == 403
    assert client.post("/api/auth/login", json={"username": "sc_pin_token", "pin": "123456"}).status_code == 200


def test_pin_change_locks_like_login(client):  # noqa: F811
    admin, jwt, user = _pin_user(client, "sc_pin_lock")
    body = {"current_pin": "000000", "new_pin": "654321"}
    for _ in range(MAX_FAILED_LOGINS - 1):
        assert client.put("/api/auth/pin", json=body, headers=jwt).status_code == 400
    r = client.put("/api/auth/pin", json=body, headers=jwt)
    assert r.status_code == 400  # 이 시도로 잠긴다
    # 잠기면 맞는 PIN도 받지 않고, 로그인도 같이 잠긴다
    r = client.put("/api/auth/pin", json={"current_pin": "123456", "new_pin": "654321"}, headers=jwt)
    assert r.status_code == 423 and "잠겼" in r.json()["detail"]
    assert client.post("/api/auth/login", json={"username": "sc_pin_lock", "pin": "123456"}).status_code == 423
    # 관리자가 초기화하면 풀린다
    assert client.put(f"/api/users/{user['id']}", json={"pin": "123456"}, headers=admin).status_code == 200
    assert client.post("/api/auth/login", json={"username": "sc_pin_lock", "pin": "123456"}).status_code == 200


def test_pin_failures_are_counted_together_with_login(client):  # noqa: F811
    _, jwt, _ = _pin_user(client, "sc_pin_share")
    wrong = {"username": "sc_pin_share", "pin": "000000"}
    for _ in range(MAX_FAILED_LOGINS - 1):
        assert client.post("/api/auth/login", json=wrong).status_code == 401
    assert client.put("/api/auth/pin", json={"current_pin": "000000", "new_pin": "654321"}, headers=jwt).status_code == 400
    assert client.post("/api/auth/login", json={"username": "sc_pin_share", "pin": "123456"}).status_code == 423


def test_pin_change_success_resets_count_and_old_pin_stops(client):  # noqa: F811
    _, jwt, _ = _pin_user(client, "sc_pin_ok")
    for _ in range(MAX_FAILED_LOGINS - 1):
        assert client.put("/api/auth/pin", json={"current_pin": "000000", "new_pin": "654321"}, headers=jwt).status_code == 400
    assert client.put("/api/auth/pin", json={"current_pin": "123456", "new_pin": "654321"}, headers=jwt).status_code == 200
    assert client.post("/api/auth/login", json={"username": "sc_pin_ok", "pin": "123456"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "sc_pin_ok", "pin": "654321"}).status_code == 200
    assert client.put("/api/auth/pin", json={"current_pin": "654321", "new_pin": "12"}, headers=jwt).status_code == 400  # 형식 오류


def test_login_still_locks_and_keeps_its_messages(client):  # noqa: F811
    admin, _, _ = _pin_user(client, "sc_login_lock")
    wrong = {"username": "sc_login_lock", "pin": "000000"}
    for _ in range(MAX_FAILED_LOGINS):
        r = client.post("/api/auth/login", json=wrong)
        assert r.status_code == 401 and "올바르지 않습니다" in r.json()["detail"]
    assert client.post("/api/auth/login", json=wrong).status_code == 423
    assert client.post("/api/auth/login", json={"username": "없는사람", "pin": "000000"}).status_code == 401
    client.post("/api/auth/register", json={"username": "sc_login_wait", "pin": "123456", "display_name": "대기"})
    assert client.post("/api/auth/login", json={"username": "sc_login_wait", "pin": "123456"}).status_code == 403


# ---------- 묶음의 파일 이름 ----------
def _zip(*names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(n, b"print('x')\n")
    return buf.getvalue()


@pytest.mark.parametrize("name", [
    "helper.pyc.", "server/helper.pyc.", "server/helper.pyc ", "helper.pyc. .", "dir./a.py", "dir /a.py",
    "CON.py", "con", "nul.txt", "a/NUL/b.py", "AUX.tar.gz", "PRN", "COM1.py", "lpt9.txt", "COM¹.py", "CONIN$", "CON .py",
    "a?.py", "a*.py", 'a".py', "a<b.py", "a|b.py", "a\x01.py",
])
def test_safe_path_rejects_names_windows_would_rewrite(name):
    with pytest.raises(RecipeRejected) as e:
        _safe_path(name)
    assert "→" in str(e.value)


def test_compiled_code_with_trailing_dot_no_longer_passes_the_bundle_check():
    with pytest.raises(RecipeRejected):
        check_bundle(_zip("server/helper.pyc."))
    with pytest.raises(RecipeRejected):
        check_bundle(_zip("CON.py"))


@pytest.mark.parametrize("name", [
    "a.py", "scripts/run_all.py", ".gitignore", "console.py", "config/nul_check.txt", "com10.py", "lpt.txt", "v1.2/a.b.py", "한글 폴더/해석 결과.py", "aux_data.csv",
])
def test_safe_path_keeps_ordinary_names(name):
    assert _safe_path(name) == name
    assert check_bundle(_zip(name), restorable=False)[0][0] == name  # .gitignore처럼 경로로는 안전해도 레시피를 받는 PC가 따로 거절하는 이름이 있다


@pytest.mark.parametrize("name", ["../a.py", "a/../../b.py", "/a.py", "C:/a.py", "a:b.py", "a//b.py", "... /a.py"])
def test_safe_path_still_rejects_escapes(name):
    with pytest.raises(RecipeRejected):
        _safe_path(name)


# ---------- project_folder ----------
def test_project_folder_is_not_filled_by_dot_folders(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "mcp_servers"))
    import dds_cae as mod

    proj = tmp_path / "studies" / "proj"  # 작업 폴더 위쪽 경로에 studies가 있어도 프로젝트 파일은 보여야 한다
    for hidden in (".agents", ".claude", ".gemini"):
        d = proj / hidden / "skills" / "a"
        d.mkdir(parents=True)
        for i in range(120):
            (d / f"{i}.md").write_text("x", encoding="utf-8")
    (proj / "model").mkdir()
    (proj / "model" / "bracket.prt").write_text("x", encoding="utf-8")
    (proj / "studies" / "s1").mkdir(parents=True)
    (proj / "studies" / "s1" / "case.h5").write_text("x", encoding="utf-8")
    (proj / "CLAUDE.md").write_text("x", encoding="utf-8")
    (proj / ".keep").write_text("x", encoding="utf-8")

    monkeypatch.setattr(mod, "settings", SimpleNamespace(project_dir=lambda pid: proj))
    monkeypatch.setattr(mod, "resolve_pid", lambda pid: 1)
    res = mod.project_folder()
    names = [f.replace("\\", "/") for f in res["files"]]
    assert "model/bracket.prt" in names and "CLAUDE.md" in names and ".keep" in names
    assert not [n for n in names if n.startswith((".agents/", ".claude/", ".gemini/", "studies/"))]
    assert names == sorted(names)
    assert res["folder"] == str(proj) and res["project_id"] == 1


def test_project_folder_still_stops_at_200(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "mcp_servers"))
    import dds_cae as mod

    for i in range(205):
        (tmp_path / f"{i:03}.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(mod, "settings", SimpleNamespace(project_dir=lambda pid: tmp_path))
    monkeypatch.setattr(mod, "resolve_pid", lambda pid: 1)
    assert len(mod.project_folder()["files"]) == 200
