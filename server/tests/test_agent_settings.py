"""PC Agent 자동 업데이트 설정: 사용자마다 저장하고, 연결된 Agent에 바로 알리고, 연결할 때마다 다시 알린다."""
from server.tests.test_api import _auth, client  # noqa: F401, I001 — test_api가 임시 데이터 폴더를 정한 뒤에 서버 코드를 불러와야 한다

from server.app.agent.link import registry  # noqa: E402
from server.app.routers import agents  # noqa: E402


class _Conn:
    def __init__(self, user_id):
        self.user_id, self.info, self.sent = user_id, {}, []

    def send(self, m):
        self.sent.append(m)


def test_default_on_and_saved_per_user(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    kim, _ = _auth(client, "kim_settings", admin)
    assert client.get("/api/agent/settings", headers=admin).json() == {"auto_update": True}

    r = client.put("/api/agent/settings", json={"auto_update": False}, headers=admin)
    assert r.status_code == 200 and r.json() == {"auto_update": False}
    assert client.get("/api/agent/settings", headers=admin).json() == {"auto_update": False}
    assert client.get("/api/agent/settings", headers=kim).json() == {"auto_update": True}  # 다른 사람은 그대로
    client.put("/api/agent/settings", json={"auto_update": True}, headers=admin)


def test_change_reaches_connected_agent(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    conn = _Conn(me["id"])
    registry.conns[me["id"]] = conn
    try:
        client.put("/api/agent/settings", json={"auto_update": False}, headers=admin)
        assert conn.sent == [{"type": "agent.settings", "auto_update": False}]
    finally:
        registry.conns.pop(me["id"], None)
        client.put("/api/agent/settings", json={"auto_update": True}, headers=admin)


def test_saved_without_connected_agent(client):  # noqa: F811
    """PC가 꺼져 있어도 저장되고, 다음에 연결할 때 알린다."""
    admin, me = _auth(client, "admin")
    assert client.put("/api/agent/settings", json={"auto_update": False}, headers=admin).status_code == 200
    conn = _Conn(me["id"])
    agents.record_instance(conn, {"type": "hello", "hostname": "PC-1", "version": "1.0"})
    assert conn.sent == [{"type": "agent.settings", "auto_update": False}]
    conn.sent.clear()
    agents.record_instance(conn, {"type": "heartbeat", "hostname": "PC-1", "status": {}})
    assert conn.sent == []  # 하트비트마다 보내지 않는다
    client.put("/api/agent/settings", json={"auto_update": True}, headers=admin)


def test_needs_login(client):  # noqa: F811
    assert client.get("/api/agent/settings").status_code == 401
    assert client.put("/api/agent/settings", json={"auto_update": False}).status_code == 401
