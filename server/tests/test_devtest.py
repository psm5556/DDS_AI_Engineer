"""원격 검증의 서버 쪽: 관리자 PC가 직접 허용한 때만, 관리자만 시험할 수 있고 내역이 남는다."""
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta

from server.tests.test_api import _auth, client  # noqa: F401, I001 — test_api가 임시 데이터 폴더를 정한 뒤에 서버 코드를 불러온다

from server.app import models  # noqa: E402
from server.app.agent.link import AgentError, registry  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.routers import agents  # noqa: E402


class _Conn:
    def __init__(self, user_id, result=None, error=None):
        self.user_id, self.info, self.sent, self.calls = user_id, {"hostname": "PC-1", "version": "1.0"}, [], []
        self.result, self.error = result if result is not None else {"ok": True, "returncode": 0, "seconds": 0.1}, error

    def send(self, m):
        self.sent.append(m)

    def request(self, type_, payload, timeout=None):
        self.calls.append((type_, payload, timeout))
        if self.error:
            raise self.error
        return self.result


def _setup(client, conn_kw=None):  # noqa: F811
    admin, me = _auth(client, "admin")
    conn = _Conn(me["id"], **(conn_kw or {}))
    registry.conns[me["id"]] = conn
    return admin, me, conn


def _off(client, admin, me):  # noqa: F811
    client.put("/api/devtest/permission", json={"allow": False}, headers=admin)
    registry.conns.pop(me["id"], None)


def test_off_by_default_and_run_refused(client):  # noqa: F811
    admin, me, conn = _setup(client)
    try:
        assert client.get("/api/devtest/permission", headers=admin).json()["allow"] is False
        assert client.get("/api/devtest/targets", headers=admin).json() == []
        r = client.post("/api/devtest/run", json={"op": "info"}, headers=admin)
        assert r.status_code == 409 and "허용" in r.json()["detail"]
        assert conn.calls == []
    finally:
        _off(client, admin, me)


def test_allow_tells_agent_and_expires_in_seven_days(client):  # noqa: F811
    admin, me, conn = _setup(client)
    try:
        r = client.put("/api/devtest/permission", json={"allow": True}, headers=admin).json()
        assert r["allow"] and timedelta(days=6) < datetime.fromisoformat(r["until"]) - datetime.now() <= timedelta(days=7)
        assert conn.sent[-1]["remote_test_until"] > 0
        assert client.get("/api/devtest/targets", headers=admin).json()[0]["hostname"] == "PC-1"
        client.put("/api/devtest/permission", json={"allow": False}, headers=admin)
        assert conn.sent[-1]["remote_test_until"] == 0
    finally:
        _off(client, admin, me)


def test_run_reaches_agent_and_is_recorded(client):  # noqa: F811
    admin, me, conn = _setup(client, {"result": {"ok": True, "returncode": 0, "seconds": 1.5, "stdout": "x"}})
    try:
        client.put("/api/devtest/permission", json={"allow": True}, headers=admin)
        r = client.post("/api/devtest/run", json={"op": "tool", "tool": "nx", "script": "print(1)", "timeout_sec": 30}, headers=admin)
        assert r.status_code == 200 and r.json()["stdout"] == "x"
        kind, payload, timeout = conn.calls[0]
        assert kind == "devtest.run" and payload["op"] == "tool" and payload["by"] == "admin" and timeout == 90
        h = client.get("/api/devtest/history", headers=admin).json()[0]
        assert h["op"] == "tool" and h["tool"] == "nx" and h["ok"] and h["hostname"] == "PC-1"
    finally:
        _off(client, admin, me)


def test_only_admin_can_allow_or_run(client):  # noqa: F811
    admin, me, conn = _setup(client)
    kim, kim_user = _auth(client, "kim_devtest", admin)
    try:
        assert client.put("/api/devtest/permission", json={"allow": True}, headers=kim).status_code == 403
        assert client.post("/api/devtest/run", json={"op": "info"}, headers=kim).status_code == 403
        assert client.get("/api/devtest/targets", headers=kim).status_code == 403
        # 일반 사용자 PC는 시험 대상이 될 수 없다
        with SessionLocal() as db:
            db.get(models.User, kim_user["id"]).remote_test_until = datetime.now() + timedelta(days=1)
            db.commit()
        registry.conns[kim_user["id"]] = _Conn(kim_user["id"])
        assert client.post("/api/devtest/run", json={"op": "info", "user_id": kim_user["id"]}, headers=admin).status_code == 403
        with SessionLocal() as db:
            assert agents.settings_message(db.get(models.User, kim_user["id"]))["remote_test_until"] == 0
    finally:
        registry.conns.pop(kim_user["id"], None)
        _off(client, admin, me)


def test_expired_permission_blocks(client):  # noqa: F811
    admin, me, conn = _setup(client)
    try:
        with SessionLocal() as db:
            db.get(models.User, me["id"]).remote_test_until = datetime.now() - timedelta(seconds=1)
            db.commit()
        assert client.get("/api/devtest/permission", headers=admin).json() == {"allow": False, "until": None}
        assert client.post("/api/devtest/run", json={"op": "info"}, headers=admin).status_code == 409
    finally:
        _off(client, admin, me)


def test_errors_tell_what_to_do_and_are_recorded(client):  # noqa: F811
    admin, me, conn = _setup(client, {"error": AgentError("PC가 거절했습니다")})
    try:
        client.put("/api/devtest/permission", json={"allow": True}, headers=admin)
        r = client.post("/api/devtest/run", json={"op": "info"}, headers=admin)
        assert r.status_code == 502 and "거절" in r.json()["detail"]
        conn.error = FutureTimeout()
        r = client.post("/api/devtest/run", json={"op": "info", "timeout_sec": 5}, headers=admin)
        assert r.status_code == 504 and "시간을 늘리" in r.json()["detail"]
        h = client.get("/api/devtest/history", headers=admin).json()
        assert h[0]["ok"] is False and h[0]["note"] == "응답 시간 초과"
    finally:
        _off(client, admin, me)


def test_rejects_unknown_op(client):  # noqa: F811
    admin, me, conn = _setup(client)
    try:
        client.put("/api/devtest/permission", json={"allow": True}, headers=admin)
        assert client.post("/api/devtest/run", json={"op": "shell"}, headers=admin).status_code == 422
    finally:
        _off(client, admin, me)
