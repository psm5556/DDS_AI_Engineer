"""대화와 마켓: 회수를 못 받은 PC의 대화를 막고, 마켓을 모르는 Agent는 그대로 지나가며, 읽음을 기록한다. 턴 앞의 왕복은 없다."""
from server.tests.test_api import _auth, client  # noqa: F401, I001 — test_api가 임시 데이터 폴더를 정한 뒤에 서버 코드를 불러와야 한다

import io  # noqa: E402
import threading  # noqa: E402
import zipfile  # noqa: E402

import pytest  # noqa: E402

from server.app import market_service, models  # noqa: E402
from server.app.agent.hub import hub  # noqa: E402
from server.app.agent.link import LinkLost, registry  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402

PIN = "123456"


def _zip(name):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{name}/SKILL.md", f"---\nname: {name}\ndescription: 설명\n---\n본문\n")
    return buf.getvalue()


class _Conn:
    def __init__(self, user_id, **info):
        self.user_id, self.sent, self.closed = user_id, [], False
        self.info = {"market_api": 1, "hostname": "PC-HUB", **info}
        self.boot = "b"

    def send(self, m):
        self.sent.append(m)


@pytest.fixture(scope="module")
def ctx(client):  # noqa: F811
    admin, a = _auth(client, "admin")
    park, p = _auth(client, "park_market", admin)
    return {"admin": admin, "park": park, "pid": p["id"]}


def _published(client, ctx, name):  # noqa: F811
    r = client.post("/api/market/submissions", files={"file": ("x.zip", _zip(name))}, headers=ctx["admin"])
    assert r.status_code == 200, r.text
    pid = r.json()["package"]["id"]
    assert client.post(f"/api/market/packages/{pid}/versions/1.0.0/approve", json={"pin": PIN, "fingerprint": r.json()["fingerprint"]}, headers=ctx["admin"]).status_code == 200
    return pid


def _withdraw(client, ctx, pid):  # noqa: F811
    assert client.post(f"/api/market/packages/{pid}/versions/1.0.0/withdraw", json={"pin": PIN, "reason": "오류"}, headers=ctx["admin"]).status_code == 200


def test_no_recall_no_block_no_message(client, ctx):  # noqa: F811
    conn = _Conn(ctx["pid"])
    assert market_service.recall_block(ctx["pid"], conn, wait_sec=0) is None
    assert conn.sent == []  # 회수가 없으면 왕복도 메시지도 없다


def test_recall_acked_passes_without_waiting(client, ctx):  # noqa: F811
    pid = _published(client, ctx, "recall-acked")
    client.put(f"/api/market/installs/{pid}", json={}, headers=ctx["park"])
    _withdraw(client, ctx, pid)
    with SessionLocal() as db:
        want = market_service.sync_payload(db, ctx["pid"])["recall_rev"]
    conn = _Conn(ctx["pid"], market_recall_rev=want)
    assert market_service.recall_block(ctx["pid"], conn, wait_sec=5) is None
    assert conn.sent == []
    client.delete(f"/api/market/installs/{pid}", headers=ctx["park"])


def test_unacked_recall_blocks_with_cause_and_next_step(client, ctx):  # noqa: F811
    pid = _published(client, ctx, "recall-unacked")
    client.put(f"/api/market/installs/{pid}", json={}, headers=ctx["park"])
    _withdraw(client, ctx, pid)
    conn = _Conn(ctx["pid"], market_recall_rev="old")
    msg = market_service.recall_block(ctx["pid"], conn, wait_sec=0.3)
    assert "회수 조치를 받지 못했습니다" in msg and "→" in msg
    assert conn.sent[0]["type"] == "market.sync"  # 한 번 더 보낸다


def test_late_ack_lets_turn_continue(client, ctx):  # noqa: F811
    with SessionLocal() as db:
        want = market_service.sync_payload(db, ctx["pid"])["recall_rev"]
    assert want
    conn = _Conn(ctx["pid"], market_recall_rev="old")
    threading.Timer(0.3, lambda: conn.info.__setitem__("market_recall_rev", want)).start()
    assert market_service.recall_block(ctx["pid"], conn, wait_sec=5) is None


def test_link_lost_does_not_block(client, ctx):  # noqa: F811
    """끊겼으면 다시 연결될 때 hello가 목록을 다시 보내고 PC Agent의 관문이 처리한다. 잠깐의 끊김을 '오류'로 만들지 않는다."""
    class Lost(_Conn):
        def send(self, m):
            raise LinkLost("끊김")

    assert market_service.recall_block(ctx["pid"], Lost(ctx["pid"], market_recall_rev="old"), wait_sec=5) is None
    closed = _Conn(ctx["pid"], market_recall_rev="old")
    closed.closed = True
    assert market_service.recall_block(ctx["pid"], closed, wait_sec=5) is None


def test_hub_old_agent_passes_without_touching_db(client, ctx, monkeypatch):  # noqa: F811
    monkeypatch.setattr(market_service, "recall_block", lambda *a, **k: pytest.fail("옛 Agent에는 묻지 않는다"))
    conn = _Conn(ctx["pid"])
    conn.info = {"hostname": "PC-OLD", "boot": "b"}
    registry.conns[ctx["pid"]] = conn
    try:
        st = hub.chats.setdefault(-1, _state(ctx["pid"]))
        assert hub._market_recall_block(st) is None
    finally:
        registry.conns.pop(ctx["pid"], None)
        hub.chats.pop(-1, None)


def _state(user_id):
    from server.app.agent.hub import TurnState

    return TurnState(chat_id=-1, project_id=1, user_id=user_id)


def test_blocked_turn_never_sends_the_prompt(client, ctx, monkeypatch):  # noqa: F811
    st = _state(ctx["pid"])
    st.busy, st.turn_id = True, "t1"
    notes, ends = [], []
    monkeypatch.setattr(hub, "_prepare", lambda *a: "sid")
    monkeypatch.setattr(hub, "_current", lambda s, t: True)
    monkeypatch.setattr(hub, "_market_recall_block", lambda s: "회수를 받지 못했습니다 → 다시 시작하세요")
    monkeypatch.setattr(hub, "_deliver", lambda *a: pytest.fail("막힌 턴은 프롬프트를 보내지 않는다"))
    monkeypatch.setattr(hub, "_note", lambda s, t: notes.append(t))
    monkeypatch.setattr(hub, "_finish", lambda s, r: ends.append(r))
    hub._run_turn(st, "t1", "요청", [], 1, "demo")
    assert notes == ["회수를 받지 못했습니다 → 다시 시작하세요"] and ends == ["error"]


def test_unblocked_turn_text_is_unchanged_by_hub(client, ctx, monkeypatch):  # noqa: F811
    """적용과 안내는 PC Agent가 한다. 서버가 보내는 프롬프트 글자는 마켓과 무관하게 그대로다"""
    st = _state(ctx["pid"])
    st.busy, st.turn_id = True, "t2"
    sent = []
    monkeypatch.setattr(hub, "_prepare", lambda *a: "sid")
    monkeypatch.setattr(hub, "_current", lambda s, t: True)
    monkeypatch.setattr(hub, "_market_recall_block", lambda s: None)
    monkeypatch.setattr(hub, "_deliver", lambda s, t, sid, payload: sent.append(payload))
    hub._run_turn(st, "t2", "요청", [], 1, "demo")
    assert sent[0]["text"] == "요청" and sent[0]["type"] == "session.prompt"


def test_hub_check_failure_does_not_block(client, ctx, monkeypatch):  # noqa: F811
    def boom(*a, **k):
        raise RuntimeError("DB 잠김")

    monkeypatch.setattr(market_service, "recall_block", boom)
    registry.conns[ctx["pid"]] = _Conn(ctx["pid"])
    try:
        assert hub._market_recall_block(_state(ctx["pid"])) is None
    finally:
        registry.conns.pop(ctx["pid"], None)


def test_state_is_stored_per_pc_and_updates_connection(client, ctx):  # noqa: F811
    with SessionLocal() as db:
        db.add(models.AgentInstance(user_id=ctx["pid"], hostname="PC-HUB", connected=True))
        db.commit()
    conn = _Conn(ctx["pid"])
    market_service.on_state(conn, {"rev": "r1", "recall_rev": "c1", "items": [{"key": "a", "version": "1.0.0", "state": "installed"}], "incomplete": []})
    assert conn.info["market_recall_rev"] == "c1"
    with SessionLocal() as db:
        inst = db.query(models.AgentInstance).filter_by(user_id=ctx["pid"], hostname="PC-HUB").one()
        assert inst.market["rev"] == "r1" and inst.market["items"][0]["key"] == "a"
        db.delete(inst)
        db.commit()


def test_on_hello_sends_only_to_market_agents(client, ctx):  # noqa: F811
    old = _Conn(ctx["pid"])
    market_service.on_hello(old, {"hostname": "x"})
    assert old.sent == []
    new = _Conn(ctx["pid"])
    market_service.on_hello(new, {"hostname": "x", "market_api": 1})
    assert new.sent[0]["type"] == "market.sync" and new.sent[0]["rev"]


# ---------- 읽음 기록 ----------
def _reads(pkg_id):
    with SessionLocal() as db:
        return [(c.chat_id, c.version, c.tool, c.kind) for c in db.query(models.MarketCall).filter_by(package_id=pkg_id)]


def test_skill_read_is_recorded_from_each_field_once_without_arguments(client, ctx):  # noqa: F811
    pid = _published(client, ctx, "read-me")
    client.put(f"/api/market/installs/{pid}", json={}, headers=ctx["park"])
    seen = set()
    secret = {"file_path": "C:/w/.claude/skills/read-me/SKILL.md", "other": "비밀 내용"}
    market_service.note_skill_read(ctx["pid"], "PC", None, 77, {"rawInput": secret}, seen)
    market_service.note_skill_read(ctx["pid"], "PC", None, 77, {"title": "Read .gemini/skills/read-me/SKILL.md"}, seen)  # 같은 대화의 두 번째는 세지 않는다
    assert _reads(pid) == [(77, "1.0.0", "read-me", "skill_read")]
    with SessionLocal() as db:
        row = db.query(models.MarketCall).filter_by(package_id=pid).one()
        assert "비밀" not in str(vars(row))  # 인자 내용은 저장하지 않는다
    seen2 = set()
    market_service.note_skill_read(ctx["pid"], "PC", None, 78, {"locations": [{"path": "C:\\w\\.agents\\skills\\read-me\\SKILL.md"}]}, seen2)
    assert (78, "1.0.0", "read-me", "skill_read") in _reads(pid)
    client.delete(f"/api/market/installs/{pid}", headers=ctx["park"])


def test_base_skill_and_unrelated_reads_are_not_recorded(client, ctx):  # noqa: F811
    with SessionLocal() as db:
        before = db.query(models.MarketCall).count()
    market_service.note_skill_read(ctx["pid"], "PC", None, 1, {"rawInput": {"p": ".claude/skills/abaqus-automation/SKILL.md"}}, set())
    market_service.note_skill_read(ctx["pid"], "PC", None, 1, {"rawInput": {"p": "C:/x/notes.md"}}, set())
    market_service.note_skill_read(ctx["pid"], "PC", None, 1, {"rawInput": {"p": ".claude/skills/not-installed/SKILL.md"}}, set())
    with SessionLocal() as db:
        assert db.query(models.MarketCall).count() == before


def test_report_context_has_market_items_only_when_read(client, ctx):  # noqa: F811
    pid = _published(client, ctx, "report-item")
    client.put(f"/api/market/installs/{pid}", json={}, headers=ctx["park"])
    project = client.post("/api/projects", json={"name": "보고서 근거"}, headers=ctx["park"]).json()["id"]
    assert "market_items_read" not in client.get(f"/api/projects/{project}/report-context", headers=ctx["park"]).json()
    market_service.note_skill_read(ctx["pid"], "PC", project, 5, {"rawInput": {"p": ".claude/skills/report-item/SKILL.md"}}, set())
    used = client.get(f"/api/projects/{project}/report-context", headers=ctx["park"]).json()["market_items_read"]
    assert used == [{"name": "report-item", "display_name": "report-item", "version": "1.0.0"}]
    _withdraw(client, ctx, pid)
    used = client.get(f"/api/projects/{project}/report-context", headers=ctx["park"]).json()["market_items_read"]
    assert used[0]["recalled"] is True and used[0]["recall_reason"] == "오류"  # 보고서에 적을 수 있게 표시한다
    client.delete(f"/api/market/installs/{pid}", headers=ctx["park"])


# ---------- 대화에 알리기 ----------
def _chat(client, ctx):  # noqa: F811
    project = client.post("/api/projects", json={"name": "회수 안내"}, headers=ctx["park"]).json()["id"]
    chat = client.post(f"/api/projects/{project}/sessions", json={"agent": "demo"}, headers=ctx["park"]).json()["id"]
    return project, chat


def test_recall_is_written_into_the_chat_once(client, ctx):  # noqa: F811
    """AI에게만 말하면 어제 보고서를 낸 엔지니어는 모른다"""
    pid = _published(client, ctx, "recall-into-chat")
    client.put(f"/api/market/installs/{pid}", json={}, headers=ctx["park"])
    project, chat = _chat(client, ctx)
    assert market_service.recall_notes(ctx["pid"], project, chat) == []
    assert client.post(f"/api/market/packages/{pid}/versions/1.0.0/withdraw", json={"pin": PIN, "reason": "S-N 곡선 계수 오류"}, headers=ctx["admin"]).status_code == 200
    notes = market_service.recall_notes(ctx["pid"], project, chat)
    assert len(notes) == 1 and "S-N 곡선 계수 오류" in notes[0] and "→" in notes[0]
    assert market_service.recall_notes(ctx["pid"], project, chat) == []  # 대화마다 한 번
    with SessionLocal() as db:  # 읽음 기록으로 치지 않는다
        assert not market_service.used_in_project(db, project)
    client.delete(f"/api/market/installs/{pid}", headers=ctx["park"])


def test_chat_started_after_the_recall_is_not_told_unless_item_was_read_there(client, ctx):  # noqa: F811
    pid = _published(client, ctx, "recall-later-chat")
    client.put(f"/api/market/installs/{pid}", json={}, headers=ctx["park"])
    project, first = _chat(client, ctx)
    market_service.note_skill_read(ctx["pid"], "PC", project, first, {"rawInput": {"p": ".claude/skills/recall-later-chat/SKILL.md"}}, set())
    _withdraw(client, ctx, pid)
    other_project, other = _chat(client, ctx)
    assert market_service.recall_notes(ctx["pid"], other_project, other) == []  # 나중에 시작한 다른 프로젝트의 대화
    later = client.post(f"/api/projects/{project}/sessions", json={"agent": "demo"}, headers=ctx["park"]).json()["id"]
    assert len(market_service.recall_notes(ctx["pid"], project, later)) == 1  # 읽었던 프로젝트의 새 대화는 알아야 한다
    client.delete(f"/api/market/installs/{pid}", headers=ctx["park"])


def test_read_record_uses_the_version_the_project_really_has(client, ctx):  # noqa: F811
    pid = _published(client, ctx, "pinned-read")
    client.put(f"/api/market/installs/{pid}", json={}, headers=ctx["park"])
    project = client.post("/api/projects", json={"name": "버전 고정"}, headers=ctx["park"]).json()["id"]
    with SessionLocal() as db:
        db.add(models.AgentInstance(user_id=ctx["pid"], hostname="PC-PIN", connected=True, market={"items": [{"key": "pinned-read", "version": "1.0.0", "state": "installed", "pins": [{"project_id": project, "project": "P", "version": "0.9.0"}]}]}))
        db.commit()
    try:
        market_service.note_skill_read(ctx["pid"], "PC-PIN", project, 41, {"rawInput": {"p": ".claude/skills/pinned-read/SKILL.md"}}, set())
        assert (41, "0.9.0", "pinned-read", "skill_read") in _reads(pid)
    finally:
        with SessionLocal() as db:
            db.query(models.AgentInstance).filter_by(hostname="PC-PIN").delete()
            db.commit()
    client.delete(f"/api/market/installs/{pid}", headers=ctx["park"])


def test_market_note_goes_only_to_that_persons_chat(client, ctx, monkeypatch):  # noqa: F811
    st = _state(ctx["pid"])
    notes = []
    monkeypatch.setattr(hub, "_note", lambda s, t: notes.append(t))
    hub.by_sid["note-sid"] = st
    try:
        hub._on_market_note(_Conn(ctx["pid"]), {"session_id": "note-sid", "text": "겹침\n숨은​글 <!-- x -->"})
        assert notes == ["겹침 숨은글 x"]
        hub._on_market_note(_Conn(ctx["pid"] + 1000), {"session_id": "note-sid", "text": "남의 대화"})
        hub._on_market_note(_Conn(ctx["pid"]), {"session_id": "모름", "text": "없는 대화"})
        hub._on_market_note(_Conn(ctx["pid"]), {"session_id": "note-sid", "text": {"a": 1}})
        assert len(notes) == 1
    finally:
        hub.by_sid.pop("note-sid", None)
