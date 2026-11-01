"""기존 자료를 '이전 기록' Task로 묶는다. 여러 번 돌아도 같고, 시각을 건드리지 않으며, 공유 링크는 같은 값을 준다."""
import pytest

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from server.app import models, work_service  # noqa: E402
from server.app.config import settings  # noqa: E402
from server.app.database import SessionLocal, engine  # noqa: E402

_n = [0]
TABLES = ("chat_sessions", "flows", "studies", "artifacts", "reports")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _make_old(client):
    """이전 전 모습을 만든다: 대화 여럿, 멤버 여럿, work_id가 빈 행. 직접 넣는 것은 지금 API가 work_id를 채우기 때문이다."""
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    a, ua = _auth(client, f"mg_a{_n[0]}", admin)
    b, ub = _auth(client, f"mg_b{_n[0]}", admin)
    c, uc = _auth(client, f"mg_c{_n[0]}", admin)
    pid = client.post("/api/projects", json={"name": f"옛 프로젝트{_n[0]}", "description": ""}, headers=a).json()["id"]
    for u in (ub, uc):
        client.put(f"/api/projects/{pid}/members/{u['id']}", json={"role": "member"}, headers=a)
    with SessionLocal() as db:
        c1 = models.ChatSession(project_id=pid, user_id=ua["id"], agent="demo", title="옛 대화 1")
        c2 = models.ChatSession(project_id=pid, user_id=ub["id"], agent="demo", title="옛 대화 2")
        db.add_all([c1, c2])
        db.flush()
        db.add(models.ChatMessage(session_id=c1.id, role="user", content="먼저"))
        db.add(models.ChatMessage(session_id=c2.id, role="user", content="나중"))
        f = models.Flow(project_id=pid, title="옛 흐름", nodes=[{"id": "n", "label": "n", "type": "doe", "status": "pending"}], edges=[])
        s = models.Study(project_id=pid, name="옛 스터디", variables=[{"name": "x", "lower": 0, "upper": 1}], adapter="mock", created_by=ua["id"])
        db.add_all([f, s])
        db.flush()
        db.add(models.Case(study_id=s.id, params={"x": 0.5}, run_by=uc["id"]))
        art = models.Artifact(project_id=pid, kind="markdown", title="옛 결과", payload={"text": "x"})
        rep = models.Report(project_id=pid, title="옛 보고서", body="## b", created_by=ua["id"])
        att = models.Attachment(project_id=pid, user_id=ua["id"], filename="old.prt", size=1, sha256="0" * 64, stored="none")
        db.add_all([art, rep, att])
        db.commit()
        ids = {"chat2": c2.id, "flow": f.id, "study": s.id, "art": art.id, "report": rep.id, "att": att.id}
        stamps = {"art": art.updated_at, "flow": f.updated_at, "study": s.updated_at, "chat": c2.updated_at}
    return {"a": a, "b": b, "c": c, "ua": ua, "ub": ub, "uc": uc, "pid": pid, "ids": ids, "stamps": stamps}


def test_groups_everything_into_one_legacy_work(client):
    old = _make_old(client)
    pid, ids = old["pid"], old["ids"]
    with engine.connect() as conn:
        before = {t: conn.execute(text(f"select count(*) from {t}")).scalar() for t in TABLES}
    assert work_service.migrate_works() >= 1
    with engine.connect() as conn:
        assert {t: conn.execute(text(f"select count(*) from {t}")).scalar() for t in TABLES} == before, "행 수가 바뀌면 안 된다"
        for t in TABLES:
            assert conn.execute(text(f"select count(*) from {t} where work_id is null")).scalar() == 0, t
    with SessionLocal() as db:
        works = db.query(models.Work).filter_by(project_id=pid).all()
        assert len(works) == 1
        w = works[0]
        assert (w.seq, w.legacy, w.name) == (0, True, "이전 기록")
        assert w.owner_id == old["ub"]["id"], "주인은 가장 최근 대화의 사용자다"
        assert set(w.legacy_users) == {old["ua"]["id"], old["ub"]["id"], old["uc"]["id"]}, "케이스만 돌린 멤버도 일하던 멤버다"
        assert db.get(models.Attachment, ids["att"]).work_id == w.id
        # 시각을 건드리면 Project 순서가 흔들리고 모든 보고서가 '뒤에 바뀐 결과가 있다'고 말한다
        assert db.get(models.Artifact, ids["art"]).updated_at == old["stamps"]["art"]
        assert db.get(models.Flow, ids["flow"]).updated_at == old["stamps"]["flow"]
        assert db.get(models.Study, ids["study"]).updated_at == old["stamps"]["study"]
        assert db.get(models.ChatSession, ids["chat2"]).updated_at == old["stamps"]["chat"]
        assert db.get(models.Project, pid).next_work_seq in (None, 1), "새 Task 번호는 1부터다"
    latest = client.get(f"/api/projects/{pid}/reports/latest?work_id={w.id}", headers=old["a"]).json()
    assert latest["new_results"] <= 1


def test_runs_twice_same_result(client):
    old = _make_old(client)
    work_service.migrate_works()
    with SessionLocal() as db:
        first = [(w.id, w.seq) for w in db.query(models.Work).filter_by(project_id=old["pid"])]
    assert work_service.migrate_works() == 0
    with SessionLocal() as db:
        assert [(w.id, w.seq) for w in db.query(models.Work).filter_by(project_id=old["pid"])] == first


def test_later_attachments_stay_unassigned(client):
    """보내기 전 임시 첨부가 이전 기록에 끼면 안 된다."""
    old = _make_old(client)
    work_service.migrate_works()
    up = client.post(f"/api/projects/{old['pid']}/attachments", files={"file": ("new.prt", b"x")}, headers=old["a"]).json()
    with SessionLocal() as db:  # 옛 서버로 되돌렸다 온 것처럼 다른 행도 하나 빈 칸으로 만든다
        db.add(models.Flow(project_id=old["pid"], title="옛 서버에서 만듦", nodes=[], edges=[]))
        db.commit()
    assert work_service.migrate_works() == 1
    with SessionLocal() as db:
        assert db.get(models.Attachment, up["id"]).work_id is None
        legacy = db.query(models.Work).filter_by(project_id=old["pid"], legacy=True).one()
        assert db.query(models.Flow).filter_by(work_id=legacy.id, title="옛 서버에서 만듦").count() == 1


def test_project_without_records_gets_no_work(client):
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    a, _u = _auth(client, f"mg_empty{_n[0]}", admin)
    pid = client.post("/api/projects", json={"name": "빈", "description": ""}, headers=a).json()["id"]
    work_service.migrate_works()
    with SessionLocal() as db:
        assert db.query(models.Work).filter_by(project_id=pid).count() == 0


def test_legacy_members_each_send_to_their_own_chat(client):
    """이전 기록은 함께 쓴다. 입력창은 그 기록 안 내 최근 대화로 보내므로 AI가 그대로 이어진다."""
    old = _make_old(client)
    work_service.migrate_works()
    with SessionLocal() as db:
        wid = db.query(models.Work).filter_by(project_id=old["pid"]).one().id
    mine = client.get(f"/api/works/{wid}", headers=old["a"]).json()
    theirs = client.get(f"/api/works/{wid}", headers=old["b"]).json()
    assert mine["mine"] and theirs["mine"] and mine["active_chat_id"] != theirs["active_chat_id"]
    assert len(mine["chats"]) == 2
    # 케이스만 돌린 멤버는 대화가 없다. 보내면 그 사람의 기록이 이전 기록 안에 생기고 새 Task는 생기지 않는다
    c = client.get(f"/api/works/{wid}", headers=old["c"]).json()
    assert c["mine"] and c["active_chat_id"] is None
    made = client.post(f"/api/works/{wid}/chat", json={"agent": "demo"}, headers=old["c"]).json()
    assert made["work_id"] == wid
    # 메시지는 한 기록으로 합쳐 시간순이다
    texts = [m["content"] for m in client.get(f"/api/works/{wid}/messages", headers=old["a"]).json()]
    assert texts == ["먼저", "나중"]
    assert client.post(f"/api/projects/{old['pid']}/works", json={"name": "새 일"}, headers=old["a"]).json()["seq"] == 1


def test_backup_made_once(client):
    dest = settings.data_dir / "backups" / "app-before-works.db"
    _make_old(client)
    work_service.migrate_works()
    assert dest.exists()
    stamp = dest.stat().st_mtime_ns
    _make_old(client)
    work_service.migrate_works()
    assert dest.stat().st_mtime_ns == stamp, "백업은 처음 한 번만 한다"


def test_share_link_predicts_the_same_after_migration(client):
    """공유 링크는 토큰 → 모델 → 스터디로 찾으므로 이전 전후 같은 값을 준다."""
    admin, _ = _auth(client, "admin")
    _n[0] += 1
    a, _u = _auth(client, f"mg_share{_n[0]}", admin)
    pid = client.post("/api/projects", json={"name": "공유", "description": ""}, headers=a).json()["id"]
    text_table = "x\ty\tz\n" + "\n".join(f"{i}\t{i * i % 7}\t{i * 3 + (i * i % 5)}" for i in range(1, 12))
    job = client.post("/api/table-jobs", json={"text": text_table, "outputs": [{"name": "z", "unit": ""}], "name": "공유 모델", "project_id": pid}, headers=a).json()
    import time
    for _ in range(240):
        job = client.get(f"/api/table-jobs/{job['id']}", headers=a).json()
        if job["status"] != "running":
            break
        time.sleep(0.25)
    assert job["status"] == "done", job
    share = client.post(f"/api/models/{job['model_id']}/shares", json={"note": ""}, headers=a)
    assert share.status_code == 200, share.text
    token = share.json()["token"]
    before = client.post(f"/api/shares/{token}/predict", json={"params": {"x": 3, "y": 2}}, headers=a).json()
    with SessionLocal() as db:  # 이전 전처럼 비우고 옮긴다
        wid = job["work_id"]
        db.query(models.Study).filter_by(work_id=wid).update({models.Study.work_id: None})
        db.query(models.Artifact).filter_by(work_id=wid).update({models.Artifact.work_id: None})
        db.delete(db.get(models.Work, wid))
        db.commit()
    work_service.migrate_works()
    after = client.post(f"/api/shares/{token}/predict", json={"params": {"x": 3, "y": 2}}, headers=a).json()
    assert after == before
