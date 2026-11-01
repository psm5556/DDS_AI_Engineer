"""마켓 옵션: 쓰기는 관리자만, 켜고 끄기는 사람만, 일반 사용자의 등록 길은 서버에서 막힌다. 켠 값은 저장만 되고 PC로 가지 않는다."""
from server.tests.test_api import _auth, client  # noqa: F401, I001 — test_api가 임시 데이터 폴더를 정한 뒤에 서버 코드를 불러와야 한다

import io  # noqa: E402
import json  # noqa: E402
import uuid  # noqa: E402
import zipfile  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

from server.app import market_service, models  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.routers.market_options import BUILTIN_MCP  # noqa: E402

PIN = "123456"
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def users(client):  # noqa: F811
    admin, a = _auth(client, "admin")
    kim, k = _auth(client, "kim_option", admin)
    lee, l = _auth(client, "lee_option", admin)
    token = client.post("/api/tokens", json={"name": "t"}, headers=kim).json()["token"]
    admin_token = client.post("/api/tokens", json={"name": "t"}, headers=admin).json()["token"]
    return {"admin": admin, "kim": kim, "lee": lee, "kim_token": {"Authorization": f"Bearer {token}"}, "admin_token": {"Authorization": f"Bearer {admin_token}"},
            "ids": {"admin": a["id"], "kim": k["id"], "lee": l["id"]}}


def _add(client, users, **kw):  # noqa: F811
    body = {"name": f"옵션 {uuid.uuid4().hex[:8]}", "description": "한 줄 설명", "service_url": "http://svc.corp:9000/mcp", **kw}
    r = client.post("/api/market/admin/options", json=body, headers=users["admin"])
    assert r.status_code == 200, r.text
    return r.json()


def _published(client, users, **kw):  # noqa: F811
    opt = _add(client, users, **kw)
    assert client.post(f"/api/market/admin/options/{opt['id']}/publish", json={"pin": PIN}, headers=users["admin"]).status_code == 200
    return opt


def _row(client, headers, oid):  # noqa: F811
    return next((i for i in client.get("/api/market/options", headers=headers).json()["items"] if i["id"] == oid), None)


def _admin_row(client, users, oid):  # noqa: F811
    return next((i for i in client.get("/api/market/admin/options", headers=users["admin"]).json()["items"] if i["id"] == oid), None)


# ---------- 기본 제공 ----------
def test_builtin_lists_four_mcp_and_tools_read_from_code(client, users):  # noqa: F811
    from agent.dds_agent.main import MCP_SERVERS

    b = client.get("/api/market/builtin", headers=users["kim"]).json()
    assert tuple(m["id"] for m in b["mcp"]) == MCP_SERVERS == tuple(i for i, _, _ in BUILTIN_MCP)
    assert {t["id"] for t in b["tools"]} == {p.name for p in (ROOT / "agent-context" / "skills").iterdir() if p.is_dir()}  # 숫자를 박아 넣지 않는다
    assert all(t["description"] for t in b["tools"])


# ---------- 쓰기는 관리자만 ----------
@pytest.mark.parametrize("who", ["kim", "kim_token", "admin_token"])
def test_writes_are_admin_login_only(client, users, who):  # noqa: F811
    opt = _add(client, users)
    url = f"/api/market/admin/options/{opt['id']}"
    body = {"name": "x", "description": "", "service_url": ""}
    for method, path, kw in (("GET", "/api/market/admin/options", {}), ("POST", "/api/market/admin/options", {"json": body}), ("PATCH", url, {"json": body}),
                             ("POST", f"{url}/publish", {"json": {"pin": PIN}}), ("POST", f"{url}/lower", {"json": {"pin": PIN}}), ("DELETE", url, {"json": {"pin": PIN}})):
        r = client.request(method, path, headers=users[who], **kw)
        assert r.status_code == 403 and "→" in r.json()["detail"], (method, path, r.text)
    assert _admin_row(client, users, opt["id"])["status"] == "draft"


@pytest.mark.parametrize("action,method", [("publish", "POST"), ("lower", "POST"), ("", "DELETE")])
def test_publish_lower_delete_need_pin(client, users, action, method):  # noqa: F811
    opt = _published(client, users) if action == "lower" else _add(client, users)
    before = _admin_row(client, users, opt["id"])["status"]
    url = f"/api/market/admin/options/{opt['id']}" + (f"/{action}" if action else "")
    r = client.request(method, url, json={"pin": "000000"}, headers=users["admin"])
    assert r.status_code == 403 and "PIN" in r.json()["detail"]
    assert _admin_row(client, users, opt["id"])["status"] == before
    with SessionLocal() as db:
        u = db.get(models.User, users["ids"]["admin"])
        u.reauth_failed, u.reauth_locked_until = 0, None
        db.commit()


def test_general_user_cannot_register_a_skill(client, users):  # noqa: F811
    """올리는 길은 서버에서 막힌다. 화면에서만 숨기지 않는다."""
    buf = io.BytesIO()
    name = f"reg-{uuid.uuid4().hex[:8]}"
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{name}/SKILL.md", f"---\nname: {name}\ndescription: 설명\n---\n본문\n")
    r = client.post("/api/market/submissions", files={"file": ("x.zip", buf.getvalue())}, headers=users["kim"])
    assert r.status_code == 403 and "→" in r.json()["detail"]
    with SessionLocal() as db:
        assert not db.query(models.MarketPackage).filter_by(name=name).first()
    for action in ("private", "request-review"):
        assert client.post(f"/api/market/packages/{name}/versions/1.0.0/{action}", json={}, headers=users["kim"]).status_code == 403
    assert client.post("/api/market/submissions", files={"file": ("x.zip", buf.getvalue())}, headers=users["admin"]).status_code == 200  # 관리자는 지금 그대로다


# ---------- 입력 ----------
@pytest.mark.parametrize("url", ["ftp://a/b", "javascript:alert(1)", "file:///c:/x", "http://", "http://user:pw@a/b", "http://a b/c", "서버주소"])
def test_bad_service_url_is_refused_with_what_to_do(client, users, url):  # noqa: F811
    r = client.post("/api/market/admin/options", json={"name": f"u {uuid.uuid4().hex[:6]}", "service_url": url}, headers=users["admin"])
    assert r.status_code == 400 and "→" in r.json()["detail"]


def test_name_is_required_cleaned_and_unique(client, users):  # noqa: F811
    assert client.post("/api/market/admin/options", json={"name": "  "}, headers=users["admin"]).status_code == 400
    tag = uuid.uuid4().hex[:6]
    opt = _add(client, users, name=f"숨은​이름\n{tag}", description="설명\n둘째 줄", service_url="")
    assert opt["name"] == f"숨은이름 {tag}" and opt["description"] == "설명 둘째 줄" and opt["service_url"] == ""
    r = client.post("/api/market/admin/options", json={"name": f"숨은이름 {tag.upper()}"}, headers=users["admin"])
    assert r.status_code == 409 and "→" in r.json()["detail"]
    other = _add(client, users)
    assert client.patch(f"/api/market/admin/options/{other['id']}", json={"name": opt["name"]}, headers=users["admin"]).status_code == 409
    assert client.patch(f"/api/market/admin/options/{opt['id']}", json={"name": opt["name"], "description": "고침"}, headers=users["admin"]).json()["description"] == "고침"  # 자기 이름은 그대로 쓴다


# ---------- 사용자 ----------
def test_user_sees_only_published_and_never_the_url(client, users):  # noqa: F811
    draft, live = _add(client, users), _published(client, users)
    res = client.get("/api/market/options", headers=users["kim"]).json()
    ids = [i["id"] for i in res["items"]]
    assert live["id"] in ids and draft["id"] not in ids and res["enabled"] is True
    assert "svc.corp" not in json.dumps(res)
    assert "svc.corp" in json.dumps(client.get("/api/market/admin/options", headers=users["admin"]).json())


def test_choice_is_per_account_and_counted(client, users):  # noqa: F811
    opt = _published(client, users)
    assert _row(client, users["kim"], opt["id"])["on"] is False
    assert client.put(f"/api/market/options/{opt['id']}/choice", json={"on": True}, headers=users["kim"]).json()["on"] is True
    assert _row(client, users["kim"], opt["id"])["on"] is True and _row(client, users["lee"], opt["id"])["on"] is False
    assert _admin_row(client, users, opt["id"])["on_count"] == 1
    assert client.put(f"/api/market/options/{opt['id']}/choice", json={"on": False}, headers=users["kim"]).json()["on"] is False
    assert _admin_row(client, users, opt["id"])["on_count"] == 0


def test_token_cannot_choose_for_the_person(client, users):  # noqa: F811
    opt = _published(client, users)
    assert client.put(f"/api/market/options/{opt['id']}/choice", json={"on": True}, headers=users["kim_token"]).status_code == 403
    assert _row(client, users["kim"], opt["id"])["on"] is False
    assert client.get("/api/market/options", headers=users["kim_token"]).status_code == 200  # 보는 것은 된다


def test_cannot_turn_on_a_draft_or_lowered_or_missing_option(client, users):  # noqa: F811
    draft = _add(client, users)
    assert client.put(f"/api/market/options/{draft['id']}/choice", json={"on": True}, headers=users["kim"]).status_code == 409
    assert client.put("/api/market/options/999999/choice", json={"on": True}, headers=users["kim"]).status_code == 404


def test_lower_turns_off_hides_and_tells_once(client, users):  # noqa: F811
    opt = _published(client, users)
    client.put(f"/api/market/options/{opt['id']}/choice", json={"on": True}, headers=users["kim"])
    assert client.post(f"/api/market/admin/options/{opt['id']}/lower", json={"pin": PIN, "note": "서비스를 옮깁니다"}, headers=users["admin"]).status_code == 200
    assert _row(client, users["kim"], opt["id"]) is None
    assert client.put(f"/api/market/options/{opt['id']}/choice", json={"on": True}, headers=users["kim"]).status_code == 409
    notices = client.get("/api/market/notices", headers=users["kim"]).json()["items"]
    told = [n for n in notices if opt["name"] in n["text"]]
    assert len(told) == 1 and "서비스를 옮깁니다" in told[0]["text"] and "→" in told[0]["text"]
    assert not [n for n in client.get("/api/market/notices", headers=users["lee"]).json()["items"] if opt["name"] in n["text"]]  # 켜지 않은 사람에게는 알리지 않는다
    # 다시 공개해도 저절로 켜지지 않는다
    assert client.post(f"/api/market/admin/options/{opt['id']}/publish", json={"pin": PIN}, headers=users["admin"]).status_code == 200
    assert _row(client, users["kim"], opt["id"])["on"] is False


def test_delete_removes_choices_and_tells_who_had_it_on(client, users):  # noqa: F811
    opt = _published(client, users)
    client.put(f"/api/market/options/{opt['id']}/choice", json={"on": True}, headers=users["lee"])
    assert client.request("DELETE", f"/api/market/admin/options/{opt['id']}", json={"pin": PIN}, headers=users["admin"]).status_code == 200
    assert _row(client, users["lee"], opt["id"]) is None and _admin_row(client, users, opt["id"]) is None
    with SessionLocal() as db:
        assert db.query(models.MarketOptionChoice).filter_by(option_id=opt["id"]).count() == 0
    assert any(opt["name"] in n["text"] for n in client.get("/api/market/notices", headers=users["lee"]).json()["items"])


def test_market_off_blocks_turning_on_but_not_off(client, users):  # noqa: F811
    opt, on = _published(client, users), _published(client, users)
    client.put(f"/api/market/options/{on['id']}/choice", json={"on": True}, headers=users["kim"])
    assert client.put("/api/market/settings", json={"pin": PIN, "enabled": False}, headers=users["admin"]).status_code == 200
    try:
        assert client.get("/api/market/options", headers=users["kim"]).json()["enabled"] is False
        r = client.put(f"/api/market/options/{opt['id']}/choice", json={"on": True}, headers=users["kim"])
        assert r.status_code == 409 and "→" in r.json()["detail"]
        assert client.put(f"/api/market/options/{on['id']}/choice", json={"on": False}, headers=users["kim"]).status_code == 200
    finally:
        client.put("/api/market/settings", json={"pin": PIN, "enabled": True}, headers=users["admin"])


# ---------- 거짓 표시 금지 ----------
def test_choice_never_reaches_the_pc(client, users):  # noqa: F811
    """켠 값은 저장만 된다. PC로 가는 동기화 내용이 그대로여야 화면의 '전달되지 않습니다'가 사실이다."""
    with SessionLocal() as db:
        before = market_service.sync_payload(db, users["ids"]["kim"])
    opt = _published(client, users)
    client.put(f"/api/market/options/{opt['id']}/choice", json={"on": True}, headers=users["kim"])
    with SessionLocal() as db:
        assert market_service.sync_payload(db, users["ids"]["kim"]) == before


def test_url_is_not_written_into_the_event_log(client, users):  # noqa: F811
    opt = _add(client, users, service_url="http://svc.corp:9000/mcp?key=비밀값")
    client.patch(f"/api/market/admin/options/{opt['id']}", json={"name": opt["name"], "service_url": "http://svc.corp:9000/mcp?key=다른비밀"}, headers=users["admin"])
    assert "비밀" not in json.dumps(client.get("/api/market/events", headers=users["admin"]).json(), ensure_ascii=False, default=str)
