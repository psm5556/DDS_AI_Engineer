"""사이드바 목록 순서: 최근에 일한 프로젝트가 위에 온다. 프로젝트 행은 이름을 바꿀 때만 갱신되므로 그 시각만으로는 부족하다."""
from server.tests.test_api import _auth, client  # noqa: F401 — 같은 서버 fixture 재사용

from sqlalchemy import inspect, text

from server.app import models
from server.app.database import SessionLocal, engine, ensure_columns


def _order(client, h, ids):  # noqa: F811
    return [p["id"] for p in client.get("/api/projects", headers=h).json() if p["id"] in ids]


def test_work_moves_a_project_up(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    h, _ = _auth(client, "order_user", admin)
    a, b, c = (client.post("/api/projects", json={"name": n, "description": ""}, headers=h).json()["id"] for n in ("가", "나", "다"))
    ids = {a, b, c}
    assert _order(client, h, ids) == [c, b, a]  # 처음에는 만든 순서의 역순

    # 작업 흐름
    assert client.post(f"/api/projects/{a}/flows", headers=h, json={"title": "흐름", "goal": "", "nodes": [{"id": "n", "label": "n", "type": "doe"}], "edges": []}).status_code == 200
    assert _order(client, h, ids) == [a, c, b]

    # 결과
    assert client.post(f"/api/projects/{b}/artifacts", headers=h, json={"kind": "markdown", "title": "요약", "payload": {"text": "x"}}).status_code == 200
    assert _order(client, h, ids) == [b, a, c]

    # 대화
    s = client.post(f"/api/projects/{c}/sessions", json={"agent": "demo"}, headers=h).json()
    with SessionLocal() as db:
        db.add(models.ChatMessage(session_id=s["id"], role="user", content="요청"))
        db.commit()
    assert _order(client, h, ids) == [c, b, a]

    # 이름을 바꿔도 위로 온다
    version = client.get(f"/api/projects/{a}", headers=h).json()["version"]
    assert client.put(f"/api/projects/{a}", headers=h, json={"name": "가2", "version": version}).status_code == 200
    rows = [p for p in client.get("/api/projects", headers=h).json() if p["id"] in ids]
    assert [p["id"] for p in rows] == [a, c, b]
    # 화면에 보이는 시각과 순서가 어긋나지 않는다
    assert [p["updated_at"] for p in rows] == sorted((p["updated_at"] for p in rows), reverse=True)


def test_work_in_other_users_project_stays_hidden(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    h1, _ = _auth(client, "order_a", admin)
    h2, _ = _auth(client, "order_b", admin)
    mine = client.post("/api/projects", json={"name": "내 것", "description": ""}, headers=h1).json()["id"]
    theirs = client.post("/api/projects", json={"name": "남의 것", "description": ""}, headers=h2).json()["id"]
    client.post(f"/api/projects/{theirs}/artifacts", headers=h2, json={"kind": "markdown", "title": "요약", "payload": {"text": "x"}})
    assert [p["id"] for p in client.get("/api/projects", headers=h1).json()] == [mine]
    assert theirs in [p["id"] for p in client.get("/api/projects", headers=admin).json()]  # 관리자는 모두 받는다


def test_order_query_reads_indexes_not_large_rows(client):  # noqa: F811
    """목록을 다시 읽을 때마다 도는 쿼리다. 메시지 본문·결과 payload를 훑으면 자료가 쌓일수록 느려진다."""
    _auth(client, "admin")
    with SessionLocal() as db:
        for sql in (
            "SELECT s.project_id, max(m.created_at) FROM chat_sessions s JOIN chat_messages m ON m.session_id = s.id WHERE s.project_id IN (1) GROUP BY s.project_id",
            "SELECT project_id, max(coalesce(updated_at, created_at)) FROM artifacts WHERE project_id IN (1) GROUP BY project_id",
        ):
            plan = " ".join(str(row[-1]) for row in db.execute(text(f"EXPLAIN QUERY PLAN {sql}")))
            assert "COVERING INDEX ix_" in plan, plan


def test_existing_database_gets_the_indexes():
    """create_all은 이미 있는 테이블에 색인을 더하지 않으므로, 전에 만든 DB는 시작할 때 채운다."""
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX IF EXISTS ix_chat_messages_session_time"))
        conn.execute(text("DROP INDEX IF EXISTS ix_artifacts_project_time"))
    ensure_columns()
    ensure_columns()  # 다시 시작해도 오류가 없다
    names = {i["name"] for t in ("chat_messages", "artifacts") for i in inspect(engine).get_indexes(t)}
    assert {"ix_chat_messages_session_time", "ix_artifacts_project_time"} <= names
