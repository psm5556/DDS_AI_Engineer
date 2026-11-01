"""경로 규칙: 문장 표와 파일 내용 비교. 버튼을 누르지 않으면 폴더도 Task도 생기지 않는다. 모르면 묻지 않는다."""
import pytest

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402

from server.app import models, route_check  # noqa: E402
from server.app.agent.link import registry  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.deps import new_api_token  # noqa: E402

NODES = [{"id": "n", "label": "단계", "type": "doe"}]
_n = [0]

# (문장, 결과 있는 내 Task에서 나올 카드). 오탐이 여기 들어 있다: 카드가 잘못 뜨면 클릭이 하나 늘고, 놓치면 AI가 잡는다
SENTENCES = [
    # 새 모델
    ("새 모델로 다시 해 줘", "A"),
    ("신규 모델 나왔어", "A"),
    ("신규 모델 나왔어. 지난번처럼 다시 돌려 줘", "A"),
    ("새 버전으로 해석해 줘", "A"),
    ("개정판으로 다시 돌려 줘", "A"),
    ("개정된 형상으로 해 줘", "A"),
    ("다음 모델 시작할게", "A"),
    ("M9 새 모델 작업해 줘", "A"),
    ("이번 건은 새 모델이야. 같은 절차로 해 줘", "A"),
    # 다른 부품
    ("다른 부품 해석할게", "B"),
    ("다른 제품으로 넘어갈게", "B"),
    ("완전히 다른 일을 시작할게", "B"),
    ("새 프로젝트로 시작해 줘", "B"),
    ("이건 다른 부품이야. 처음부터 해 줘", "B"),
    # 오탐: 예측 모델 이야기
    ("새 모델 학습해 줘", None),
    ("새 버전 배포해 줘", None),
    ("새 모델로 예측해 줘", None),
    ("Simulator 새 버전 만들어 줘", None),
    ("시뮬레이터 개정해 줘", None),
    ("신규 모델 학습 결과 보여 줘", None),
    # 오탐: 같은 문장이 아니면 다른 말이다
    # 오탐: 일상어
    ("새로 돌려 줘", None),
    ("다시 돌려 줘", None),
    ("하중을 3 kN으로 바꿔 다시 돌려 줘", None),
    ("두께 범위를 2~8로 넓혀 줘", None),
    ("재료를 알루미늄으로 바꿔 줘", None),
    ("지금 최대 응력이 어느 케이스야?", None),
    ("보고서 써 줘", None),
    ("Output 추가: 최대 변위", None),
    ("지난 결과와 비교해 줘", None),
    ("이 작업 이어서 해 줘", None),
    ("모델 파일 확인해 줘", None),
    ("", None),
    # 오탐: 묻는 말·부정·비교·결과·프로그램
    ("새 모델이 뭐야?", None),
    ("새 버전 있어?", None),
    ("새 모델 아니고 지금 모델로 해 줘", None),
    ("다른 부품 말고 이 부품으로 해 줘", None),
    ("다른 제품과 비교해 줘", None),
    ("완전히 다른 결과가 나왔어", None),
    ("완전히 다른 값이 나왔는데 왜 그래", None),
    ("PC Agent 새 버전 받았어", None),
    ("에이전트 새 버전 나왔어", None),
    ("개정 이력 정리해 줘", None),
    ("개정 내용을 보고서에 적어 줘", None),
    # 한 문장에만 있으면 그 문장으로 판단한다
    ("새 모델 학습해 줘. 그리고 배포해 줘", None),
    ("하중을 바꿔 줘. 그리고 신규 모델로 해 줘", "A"),
]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def world(client):
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    a, ua = _auth(client, f"route_a{_n[0]}", admin)
    b, ub = _auth(client, f"route_b{_n[0]}", admin)
    pid = client.post("/api/projects", json={"name": f"스파이더 경량화 {_n[0]}"}, headers=a).json()["id"]
    client.put(f"/api/projects/{pid}/members/{ub['id']}", json={"role": "member"}, headers=a)
    w = client.post(f"/api/projects/{pid}/works", json={"name": "1차 경량화", "name_auto": False, "agent": "demo"}, headers=a).json()
    return {"a": a, "b": b, "ua": ua, "ub": ub, "pid": pid, "w": w, "admin": admin}


def _results(client, h, pid, wid):
    r = client.post(f"/api/projects/{pid}/studies?work_id={wid}", json={"name": f"s{wid}", "adapter": "mock", "variables": [{"name": "x", "lower": 0, "upper": 1}]}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _check(client, h, **body):
    r = client.post("/api/route-check", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _attach(client, h, pid, wid, name="spider_m7.prt", body=b"m7"):
    a = client.post(f"/api/projects/{pid}/attachments", files={"file": (name, body)}, headers=h).json()
    with SessionLocal() as db:
        db.get(models.Attachment, a["id"]).work_id = wid
        db.commit()
    return a


def test_table_has_at_least_forty_sentences():
    assert len(SENTENCES) >= 40


@pytest.mark.parametrize("text,card", SENTENCES)
def test_sentence_table(client, world, text, card):
    _results(client, world["a"], world["pid"], world["w"]["id"])
    r = _check(client, world["a"], work_id=world["w"]["id"], text=text)
    assert r["card"] == card, text
    if card == "A":
        assert r["source_work"]["name"] == "1차 경량화" and r["file"] is None and r["kind"] == "text"


@pytest.mark.parametrize("text", ["새 모델로 다시 해 줘", "개정판으로 돌려 줘", "새 버전으로 해석해 줘"])
def test_no_results_no_question_for_new_model(client, world, text):
    """아직 결과가 없으면 어떤 모델이든 여기서 시작하면 된다."""
    assert _check(client, world["a"], work_id=world["w"]["id"], text=text)["card"] is None


def test_other_part_is_asked_even_without_results(client, world):
    assert _check(client, world["a"], work_id=world["w"]["id"], text="다른 부품 해석할게")["card"] == "B"
    assert _check(client, world["a"], project_id=world["pid"], text="다른 부품 해석할게")["card"] == "B"


def test_new_task_screen_does_not_ask_about_new_model(client, world):
    assert _check(client, world["a"], project_id=world["pid"], text="새 모델로 해 줘")["card"] is None


# ---------- 파일 ----------
FILE_RULES = [
    # (올린 파일 이름, 내용이 이 Task 첨부와 같은가, 결과 있음, 카드)
    ("spider_m9.prt", False, True, "A"),
    ("SPIDER_M9.STP", False, True, "A"),
    ("deck.inp", False, True, "A"),
    ("deck.k", False, True, "A"),
    ("spider_m7.prt", False, True, "A"),  # 같은 이름, 다른 내용: 개정판이다
    ("new_name.prt", True, True, None),  # 다른 이름, 같은 내용: 같은 모델이다
    ("spider_m9.prt", False, False, None),  # 결과 없음
    ("data.dat", False, True, None),  # .dat은 표일 수 있다
    ("readme.pdf", False, True, None),  # 모델이 아니다
    ("표.csv", False, True, None),
]


@pytest.mark.parametrize("name,same,results,card", FILE_RULES)
def test_file_rule(client, world, name, same, results, card):
    a, pid, wid = world["a"], world["pid"], world["w"]["id"]
    have = _attach(client, a, pid, wid)
    if results:
        _results(client, a, pid, wid)
    r = _check(client, a, work_id=wid, text="", files=[{"name": name, "sha256": have["sha256"] if same else "f" * 64}])
    assert r["card"] == card
    if card:
        assert r["file"] == name and r["kind"] == "file"


def test_kept_file_is_not_asked_again_and_approved_flow_counts_as_result(client, world):
    a, pid, wid = world["a"], world["pid"], world["w"]["id"]
    f = client.post(f"/api/projects/{pid}/flows?work_id={wid}", json={"title": "흐름", "goal": "", "nodes": NODES, "edges": []}, headers=a).json()
    file = {"name": "m9.prt", "sha256": "e" * 64}
    assert _check(client, a, work_id=wid, files=[file])["card"] is None, "승인 전 흐름만 있으면 아직 결과가 없다"
    client.put(f"/api/flows/{f['id']}/status", json={"status": "approved", "version": f["version"]}, headers=a)
    assert _check(client, a, work_id=wid, files=[file])["card"] == "A"
    assert _check(client, a, work_id=wid, files=[file], kept=[file["sha256"]])["card"] is None, "'여기서 계속'을 고른 파일은 다시 묻지 않는다"


# ---------- 남의 Task · 첫 화면 ----------
def test_others_task_is_card_c_whatever_the_words(client, world):
    for text in ("새 모델로 해 줘", "그냥 질문", "다른 부품"):
        r = _check(client, world["b"], work_id=world["w"]["id"], text=text)
        assert r["card"] == "C" and r["owner_name"] == world["ua"]["display_name"]


def test_home_finds_my_project_by_full_name_only(client, world):
    a, b = world["a"], world["b"]
    pid = world["pid"]
    name = client.get(f"/api/projects/{pid}", headers=a).json()["name"]
    src = world["w"]["id"]
    _results(client, a, pid, src)
    r = _check(client, b, text=f"{name} 응력 다시 봐 줘")
    assert r["card"] == "D" and r["project"]["id"] == pid and r["source_work"]["id"] == src
    assert _check(client, b, text="스파이더 경량화")["card"] is None, "이름 일부로는 묻지 않는다"
    admin = world["admin"]
    assert _check(client, admin, text=f"{name} 다시 봐 줘")["card"] is None, "멤버가 아니면 있는지도 알리지 않는다"
    short = client.post("/api/projects", json={"name": "표"}, headers=b).json()["id"]
    assert short and _check(client, b, text="표로 만들어 줘")["card"] is None, "짧은 이름은 아무 문장에나 들어 있다"


def test_nothing_is_created_until_a_button(client, world):
    a, pid, wid = world["a"], world["pid"], world["w"]["id"]
    _results(client, a, pid, wid)
    before = [x["id"] for x in client.get(f"/api/projects/{pid}/works", headers=a).json()]
    for text in ("새 모델로 다시 해 줘", "다른 부품 해석할게"):
        assert _check(client, a, work_id=wid, text=text)["card"]
    assert [x["id"] for x in client.get(f"/api/projects/{pid}/works", headers=a).json()] == before
    with SessionLocal() as db:
        assert db.query(models.ChatMessage).join(models.ChatSession).filter(models.ChatSession.work_id == wid).count() == 0


def test_route_check_needs_membership(client, world):
    admin, _ = _auth(client, "admin")
    out, _ = _auth(client, f"route_out{_n[0]}", admin)
    assert client.post("/api/route-check", json={"work_id": world["w"]["id"], "text": "x"}, headers=out).status_code == 403
    assert client.post("/api/route-check", json={"project_id": world["pid"], "text": "x"}, headers=out).status_code == 403


def test_words_are_kept_narrow():
    assert route_check.says_new_model("새 모델로 해 줘") and not route_check.says_new_model("새로 해 줘")


# ---------- AI가 다시 볼 때(suggest_route) ----------
def test_suggest_route_leaves_a_card_once_and_respects_the_choice(client, world):
    a, pid, wid = world["a"], world["pid"], world["w"]["id"]
    chat = world["w"]["active_chat_id"]
    with SessionLocal() as db:
        db.add(models.ChatMessage(session_id=chat, role="user", content="허브 베어링 해석해 줘", meta={"attachments": [{"id": 1, "filename": "hub.prt", "size": 3}]}))
        db.commit()
    url = f"/api/projects/{pid}/route-suggestion?work_id={wid}"
    r = client.post(url, json={"kind": "other_part", "reason": "허브 베어링은 이 Project의 부품이 아닙니다"}, headers=a).json()
    assert r["shown"] is True
    assert client.post(url, json={"kind": "other_part"}, headers=a).json()["shown"] is False, "한 턴에 카드는 하나"
    msgs = client.get(f"/api/works/{wid}/messages", headers=a).json()
    card = next(m for m in msgs if m["role"] == "route")
    assert card["meta"]["card"] == "B" and card["meta"]["request"] == "허브 베어링 해석해 줘" and card["meta"]["attachments"][0]["filename"] == "hub.prt"
    assert "베어링" in card["content"]
    # 사람이 '여기서 계속'을 고른 뒤에는 다시 묻지 않는다
    with SessionLocal() as db:
        db.add(models.ChatMessage(session_id=chat, role="user", content="여기서 계속", meta={"route_kept": True}))
        db.commit()
    r = client.post(url, json={"kind": "other_part"}, headers=a).json()
    assert r["shown"] is False and "여기서 계속" in r["note"]
    assert client.post(url, json={"kind": "bad"}, headers=a).status_code == 422


def test_ai_token_can_suggest_but_not_undo(client, world):
    raw, hashed = new_api_token()
    with SessionLocal() as db:
        db.add(models.ApiToken(user_id=world["ua"]["id"], name="t", token_hash=hashed))
        db.commit()
    tok = {"Authorization": f"Bearer {raw}", "X-DDS-Work": str(world["w"]["id"])}
    assert client.post(f"/api/projects/{world['pid']}/route-suggestion", json={"kind": "new_model"}, headers=tok).status_code == 200
    assert client.post(f"/api/works/{world['w']['id']}/undo", headers=tok).status_code == 403


def test_prompt_tells_the_ai_about_the_choice(client, world):
    sent = []

    class Conn:
        user_id = world["ua"]["id"]
        info = {"agents": []}

        def send(self, m):
            sent.append(m)
    from server.app.agent.hub import hub
    calls = {}
    real = hub.send_prompt

    def fake(chat_id, text, atts=None, flow_id=None, shown=None, auto=None, route_kept=False):
        calls.update(text=text, shown=shown, route_kept=route_kept)
        return "t"
    hub.send_prompt = fake
    registry.conns[world["ua"]["id"]] = Conn()
    try:
        chat = world["w"]["active_chat_id"]
        assert client.post(f"/api/sessions/{chat}/prompt", json={"text": "해 줘", "route_kept": True}, headers=world["a"]).status_code == 200
        assert "여기서 계속" in calls["text"] and calls["shown"] == "해 줘" and calls["route_kept"] is True
        assert client.post(f"/api/sessions/{chat}/prompt", json={"text": "해 줘"}, headers=world["a"]).status_code == 200
        assert calls["text"] == "해 줘" and calls["route_kept"] is False
    finally:
        hub.send_prompt = real
        registry.conns.pop(world["ua"]["id"], None)


# ---------- 되돌리기 ----------
def _start(client, world):
    a, pid, wid = world["a"], world["pid"], world["w"]["id"]
    _results(client, a, pid, wid)
    return client.post(f"/api/projects/{pid}/works", json={"name": "spider_m9", "agent": "demo", "from_work_id": wid}, headers=a).json()


def test_undo_removes_the_task_and_returns_request_and_files(client, world):
    a, pid = world["a"], world["pid"]
    t = _start(client, world)
    assert t["can_undo"] is True
    att = client.post(f"/api/projects/{pid}/attachments", files={"file": ("m9.prt", b"m9")}, headers=a).json()
    with SessionLocal() as db:
        db.get(models.Attachment, att["id"]).work_id = t["id"]
        db.add(models.ChatMessage(session_id=t["active_chat_id"], role="user", content="새 모델 돌려 줘", meta={}))
        db.commit()
    r = client.post(f"/api/works/{t['id']}/undo", headers=a)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "source_work_id": world["w"]["id"], "text": "새 모델 돌려 줘", "attachment_ids": [att["id"]]}
    assert client.get(f"/api/works/{t['id']}", headers=a).status_code == 404, "행이 남지 않는다"
    with SessionLocal() as db:
        assert db.query(models.ChatSession).filter_by(work_id=t["id"]).count() == 0
        back = db.get(models.Attachment, att["id"])
        assert back is not None and back.work_id is None, "입력창으로 돌아간 첨부는 지우지 않는다"
    assert client.get(f"/api/attachments/{att['id']}/download", headers=a).content == b"m9"
    # 번호는 다시 쓰지 않는다
    seq = client.post(f"/api/projects/{pid}/works", json={"name": "또"}, headers=a).json()["seq"]
    assert seq == t["seq"] + 1


@pytest.mark.parametrize("what", ["approved", "artifact", "case", "report"])
def test_undo_is_refused_once_something_has_run(client, world, what):
    a, pid = world["a"], world["pid"]
    t = _start(client, world)
    wid = t["id"]
    if what == "approved":
        f = client.post(f"/api/projects/{pid}/flows?work_id={wid}", json={"title": "f", "goal": "", "nodes": NODES, "edges": []}, headers=a).json()
        client.put(f"/api/flows/{f['id']}/status", json={"status": "approved", "version": f["version"]}, headers=a)
    elif what == "artifact":
        client.post(f"/api/projects/{pid}/artifacts?work_id={wid}", json={"kind": "markdown", "title": "결과", "payload": {"text": "x"}}, headers=a)
    elif what == "report":
        client.post(f"/api/projects/{pid}/reports?work_id={wid}", json={"title": "보고서", "body": "## 본문"}, headers=a)
    else:
        sid = _results(client, a, pid, wid)
        with SessionLocal() as db:
            db.add(models.Case(study_id=sid, params={"x": 0.5}, results={"y": 1.0}, status="complete"))
            db.commit()
    assert client.get(f"/api/works/{wid}", headers=a).json()["can_undo"] is False
    r = client.post(f"/api/works/{wid}/undo", headers=a)
    assert r.status_code == 409 and "→" in r.json()["detail"]
    assert client.get(f"/api/works/{wid}", headers=a).status_code == 200


def test_undo_with_a_review_flow_and_empty_clone_is_still_fine(client, world):
    a, pid = world["a"], world["pid"]
    t = _start(client, world)
    client.post(f"/api/projects/{pid}/flows?work_id={t['id']}", json={"title": "f", "goal": "", "nodes": NODES, "edges": []}, headers=a)
    _results(client, a, pid, t["id"])
    assert client.post(f"/api/works/{t['id']}/undo", headers=a).status_code == 200


def test_plain_task_cannot_be_undone_only_deleted(client, world):
    a, pid = world["a"], world["pid"]
    t = client.post(f"/api/projects/{pid}/works", json={"name": "그냥", "agent": "demo"}, headers=a).json()
    assert t["can_undo"] is False
    assert client.post(f"/api/works/{t['id']}/undo", headers=a).status_code == 409


def test_undo_tells_the_pc_and_closes_the_ai(client, world):
    sent = []

    class Conn:
        user_id = world["ua"]["id"]
        info = {"work_folders": 1}

        def send(self, m):
            sent.append(m)
    t = _start(client, world)
    with SessionLocal() as db:
        db.get(models.ChatSession, t["active_chat_id"]).agent_session_id = "sid-1"
        work = db.get(models.Work, t["id"])
        work.carry = {**work.carry, "carried": {"scripts": ["scripts/run.py"], "inputs": []}}
        db.commit()
    registry.conns[world["ua"]["id"]] = Conn()
    try:
        assert client.post(f"/api/works/{t['id']}/undo", headers=world["a"]).status_code == 200
    finally:
        registry.conns.pop(world["ua"]["id"], None)
    assert {"type": "session.close", "session_id": "sid-1"} in sent
    tell = next(m for m in sent if m["type"] == "work.discard")
    assert tell["work"]["uid"] == t["uid"] and tell["keep"] == ["scripts/run.py"]


def test_undo_with_old_agent_sends_no_discard(client, world):
    sent = []

    class Conn:
        user_id = world["ua"]["id"]
        info = {}

        def send(self, m):
            sent.append(m)
    t = _start(client, world)
    registry.conns[world["ua"]["id"]] = Conn()
    try:
        assert client.post(f"/api/works/{t['id']}/undo", headers=world["a"]).status_code == 200
    finally:
        registry.conns.pop(world["ua"]["id"], None)
    assert not [m for m in sent if m["type"] == "work.discard"]


# ---------- 카드 B: 첨부를 새 Project로 ----------
def test_attachments_move_to_the_new_project_only_when_mine_and_unsent(client, world):
    a, b, pid = world["a"], world["b"], world["pid"]
    att = client.post(f"/api/projects/{pid}/attachments", files={"file": ("hub.prt", b"hub")}, headers=a).json()
    other = client.post("/api/projects", json={"name": "허브 베어링"}, headers=a).json()["id"]
    theirs = client.post("/api/projects", json={"name": "남의 것"}, headers=b).json()["id"]
    assert client.post(f"/api/projects/{theirs}/attachments/adopt", json={"ids": [att["id"]]}, headers=b).status_code == 404, "내가 올린 것만 옮긴다"
    assert client.post(f"/api/projects/{other}/attachments/adopt", json={"ids": [att["id"]]}, headers=a).status_code == 200
    with SessionLocal() as db:
        assert db.get(models.Attachment, att["id"]).project_id == other
    assert client.get(f"/api/attachments/{att['id']}/download", headers=a).content == b"hub"
    sent = _attach(client, a, pid, world["w"]["id"], "sent.prt", b"s")
    assert client.post(f"/api/projects/{other}/attachments/adopt", json={"ids": [sent["id"]]}, headers=a).status_code == 404, "이미 보낸 첨부는 그 Task의 것이다"
