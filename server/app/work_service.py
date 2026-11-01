"""Project > Task(코드 이름 Work). 어느 Task에 넣고 읽을지, 이전, 상태 합산을 한 곳에서 정한다.

어느 Task인가 (서버가 강제한다. AI는 요청마다 이 규칙을 탄다)
  쓰기: 명시한 work_id → 헤더 X-DDS-Work → 그 사람이 지금 돌리는 Task → 최근에 돌린 Task → 내 최근 Task → 이전 기록(일하던 멤버만) → 없으면 내 새 Task
  읽기: 명시한 work_id → 헤더 → 그 사람이 지금 돌리는 Task → 모르면 Project 전체
읽기를 넓게 두는 까닭: 조회는 아무것도 망가뜨리지 않고, 좁히면 스크립트나 다른 Project를 찾는 AI가 지난 결과를 못 찾는다.
옛 PC Agent는 헤더를 보내지 않는다. 그래도 끊기지 않고 위 순서로 맞는 Task를 찾는다.

2단계(폴더·세션·MCP)가 이어받는 곳: ServerApi가 X-DDS-Work를 붙이면 work_scope가 그대로 읽는다. 폴더를 정하는 hub._ensure_session에는 TurnState.work_id가 있다.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, Header, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from . import models
from .agent.hub import hub
from .config import settings
from .database import SessionLocal, engine, get_db
from .deps import effective_admin, get_current_user, get_project_for_member
from .study import service

log = logging.getLogger(__name__)

LEGACY_NAME = "이전 기록"
FIRST_NAME = "첫 Task"  # 새 Project의 첫 Task. AI가 첫 답에서 알맞은 이름으로 바꾼다(3단계)
NEW_NAME = "새 Task"
TABLE_NAME = "표로 만든 Simulator"
MODEL_EXT = (".prt", ".sldprt", ".catpart", ".stp", ".step", ".igs", ".iges", ".x_t", ".x_b", ".cae", ".inp", ".k", ".key", ".bdf", ".fem", ".sim")
SEARCH_LIMIT = 50


def is_mine(work: models.Work, user_id: int) -> bool:
    """이 사람이 요청을 보낼 수 있는 Task인가. 이전 기록은 옛 대화를 가진 멤버가 함께 쓴다."""
    return work.owner_id == user_id or user_id in (work.legacy_users or [])


def not_mine_message(owner_name: str) -> str:
    return f"{owner_name}님의 Task입니다 → 보기만 할 수 있습니다. 내 일은 왼쪽 '+ 새 Task'로 시작하세요."


def owner_label(db: Session, work: models.Work) -> str:
    owner = db.get(models.User, work.owner_id)
    return owner.display_name if owner else "다른 멤버"


# ---------- 만들기 ----------
def new_work(db: Session, project_id: int, owner_id: int, name: str | None = None, name_auto: bool = True) -> models.Work:
    """번호를 잡고 Task를 만든다. 번호는 DB의 UPDATE 한 번으로 잡아 동시에 만들어도 겹치지 않고, 지운 번호를 다시 쓰지 않는다."""
    # 번호 카운터를 올려도 Project의 updated_at은 그대로 둔다: 사이드바 순서는 _last_work가 실제 활동으로 정한다
    db.execute(update(models.Project).where(models.Project.id == project_id)
               .values(next_work_seq=func.coalesce(models.Project.next_work_seq, 1) + 1, updated_at=models.Project.updated_at)
               .execution_options(synchronize_session=False))
    seq = db.scalar(select(models.Project.next_work_seq).where(models.Project.id == project_id)) - 1
    label = (name or "").strip()[:200]
    if not label:
        has_any = db.query(models.Work.id).filter_by(project_id=project_id).first()
        label = NEW_NAME if has_any else FIRST_NAME
    w = models.Work(project_id=project_id, seq=seq, name=label, name_auto=bool(name_auto or not (name or "").strip()), owner_id=owner_id, legacy_users=[])
    db.add(w)
    db.flush()
    return w


def add_chat(db: Session, work: models.Work, user_id: int, agent: str) -> models.ChatSession:
    chat = models.ChatSession(project_id=work.project_id, user_id=user_id, agent=agent, work_id=work.id)
    db.add(chat)
    db.flush()
    return chat


def my_chat(db: Session, work: models.Work, user_id: int) -> models.ChatSession | None:
    return (db.query(models.ChatSession).filter_by(work_id=work.id, user_id=user_id)
            .order_by(models.ChatSession.id.desc()).first())


# ---------- 어느 Task인가 ----------
def _parse_hint(raw: str | None) -> int | None:
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def _in_project(db: Session, project_id: int, work_id: int | None) -> models.Work | None:
    w = db.get(models.Work, work_id) if work_id else None
    return w if w and w.project_id == project_id else None


def resolve(db: Session, project_id: int, user: models.User, explicit: int | None = None, header: int | None = None) -> models.Work | None:
    """쓰기 순서대로 찾는다. 없으면 None이다(조회로는 아무것도 만들지 않는다). 헤더는 다른 Project 호출에도 붙어 오므로 맞지 않으면 건너뛴다."""
    if explicit:
        w = _in_project(db, project_id, explicit)
        if not w:
            raise HTTPException(404, "이 Project에 없는 Task입니다 → 왼쪽 목록에서 다시 골라 주세요")
        return w
    w = _in_project(db, project_id, header)
    if w:
        return w
    w = _in_project(db, project_id, hub.active_work(user.id, project_id))
    if w:
        return w
    w = _in_project(db, project_id, hub.recent_work(user.id, project_id))
    if w and is_mine(w, user.id):
        return w
    rows = db.query(models.Work).filter_by(project_id=project_id).order_by(models.Work.seq.desc()).all()
    return next((x for x in rows if not x.legacy and x.owner_id == user.id), None) or next((x for x in rows if x.legacy and is_mine(x, user.id)), None)


def write_work(db: Session, project_id: int, user: models.User, explicit: int | None = None, header: int | None = None) -> models.Work:
    """쓸 Task. 모르면 내 Task를 새로 만든다. 스크립트나 옛 Agent의 요청을 NULL로 남기거나 실패시키지 않는다."""
    w = resolve(db, project_id, user, explicit, header)
    if w is None:
        w = new_work(db, project_id, user.id)
        db.commit()
    return w


@dataclass
class WorkScope:
    project: models.Project
    user: models.User
    db: Session
    explicit: int | None
    header: int | None
    all_works: bool

    @property
    def hint(self) -> int | None:
        """요청이 스스로 밝힌 Task(이 Project의 것일 때만)."""
        if self.explicit:
            return self.explicit
        return self.header if _in_project(self.db, self.project.id, self.header) else None

    def read(self) -> int | None:
        """목록을 좁힐 Task. None이면 Project 전체다."""
        if self.all_works:
            return None
        if self.explicit and not _in_project(self.db, self.project.id, self.explicit):
            raise HTTPException(404, "이 Project에 없는 Task입니다 → 왼쪽 목록에서 다시 골라 주세요")
        return self.hint or _in_project_id(self.db, self.project.id, hub.active_work(self.user.id, self.project.id))

    def write(self) -> models.Work:
        return write_work(self.db, self.project.id, self.user, self.explicit, self.header)


def _in_project_id(db: Session, project_id: int, work_id: int | None) -> int | None:
    w = _in_project(db, project_id, work_id)
    return w.id if w else None


def work_scope(project: models.Project = Depends(get_project_for_member), user: models.User = Depends(get_current_user),
               db: Session = Depends(get_db), work_id: int | None = Query(None), all_works: bool = Query(False),
               x_dds_work: str | None = Header(None)) -> WorkScope:
    return WorkScope(project, user, db, work_id, _parse_hint(x_dds_work), all_works)


def header_hint(x_dds_work: str | None = Header(None)) -> int | None:
    return _parse_hint(x_dds_work)


def narrow(q, model, work_id: int | None):
    """목록 쿼리를 Task로 좁힌다. None이면 Project 전체."""
    return q.filter(model.work_id == work_id) if work_id else q


# ---------- 상태 합산 ----------
RUNNING = ("in_progress", "approved")


def _status_of(flows: list[tuple[int, str, str]], busy: bool) -> tuple[str | None, str | None]:
    """Task 하나의 가장 급한 상태. flows: (id, status, title) id 순.
    승인 대기는 어느 흐름이든 남긴다. 실패는 그 뒤에 다시 한 흐름이 없을 때만: 고쳐서 끝낸 Task에 실패 표시가 남으면 뜻이 없어진다."""
    for wanted in ("review", "in_progress", "approved"):
        hit = next((f for f in reversed(flows) if f[1] == wanted), None)
        if hit:
            return wanted, hit[2]
    live = [f for f in flows if f[1] != "rejected"]
    if busy:
        return "in_progress", live[-1][2] if live else None
    if live:
        return live[-1][1], live[-1][2]
    return None, None


def briefs(db: Session, project_ids: list[int], user: models.User) -> dict[int, list[dict]]:
    """Project별 Task 줄(최신이 위). 목록을 읽을 때마다 도는 쿼리라 Project마다 묻지 않고 한 번에 묶어 읽는다."""
    out: dict[int, list[dict]] = {pid: [] for pid in project_ids}
    if not project_ids:
        return out
    W = models.Work
    rows = db.query(W).filter(W.project_id.in_(project_ids)).order_by(W.seq.desc()).all()
    if not rows:
        return out
    F, S, C, A = models.Flow, models.Study, models.Case, models.Attachment
    flows: dict[int, list[tuple[int, str, str]]] = {}
    for fid, wid, status, title in db.query(F.id, F.work_id, F.status, F.title).filter(F.project_id.in_(project_ids)).order_by(F.id):
        flows.setdefault(wid, []).append((fid, status, title))
    busy = hub.busy_works()
    running = [sid for sid in list(service._batches) if service.is_running(sid)]
    if running:
        busy |= {wid for wid, in db.query(S.work_id).filter(S.id.in_(running), S.project_id.in_(project_ids))}
    cases = dict(db.query(S.work_id, func.count(C.id)).join(C, C.study_id == S.id).filter(S.project_id.in_(project_ids)).group_by(S.work_id).all())
    files: dict[int, str] = {}
    for wid, name in db.query(A.work_id, A.filename).filter(A.project_id.in_(project_ids), A.work_id.isnot(None)).order_by(A.id):
        if name.lower().endswith(MODEL_EXT):
            files[wid] = name  # 마지막에 올린 모델
    names = {uid: name for uid, name in db.query(models.User.id, models.User.display_name).filter(models.User.id.in_({w.owner_id for w in rows}))}
    masters = {p for p, in db.query(models.ProjectMember.project_id).filter(models.ProjectMember.user_id == user.id, models.ProjectMember.role == "master",
                                                                            models.ProjectMember.project_id.in_(project_ids))}
    admin = effective_admin(user)
    for w in rows:
        status, title = _status_of(flows.get(w.id, []), w.id in busy)
        out[w.project_id].append({
            "id": w.id, "uid": w.uid, "project_id": w.project_id, "seq": w.seq, "name": w.name, "owner_id": w.owner_id, "owner_name": names.get(w.owner_id, ""),
            "legacy": bool(w.legacy), "mine": is_mine(w, user.id), "can_manage": w.owner_id == user.id or w.project_id in masters or admin,
            "status": status, "flow_title": title, "model_file": files.get(w.id), "n_cases": cases.get(w.id, 0), "source_label": w.source_label,
            "version": w.version, "created_at": w.created_at, "updated_at": w.updated_at})
    return out


def project_status(works: list[dict]) -> tuple[str | None, str | None]:
    """Project 줄에 올릴 상태 하나. 승인 대기·실패는 내 Task만, 남의 Task는 진행 중만 올린다. 남의 승인 대기로 점이 켜지면 내가 할 일로 읽힌다."""
    mine = [w for w in works if w["mine"]]
    for wanted in ("review", "failed"):
        hit = next((w for w in mine if w["status"] == wanted), None)
        if hit:
            return wanted, hit["flow_title"]
    hit = next((w for w in works if w["status"] in RUNNING), None)
    if hit:
        return hit["status"], hit["flow_title"]
    calm = next((w for w in works if w["status"] and w["status"] not in ("review", "failed")), None)
    return (calm["status"], calm["flow_title"]) if calm else (None, None)


# ---------- 찾기 ----------
def search(db: Session, q: str, user: models.User) -> dict:
    """Project 이름·설명, Task 이름, 모델 파일 이름, 결과 제목을 찾는다. 내가 볼 수 있는 Project 안에서만."""
    term = q.strip()
    if not term:
        return {"projects": [], "works": []}
    P, W, A, R = models.Project, models.Work, models.Attachment, models.Artifact
    visible = db.query(P.id)
    if not effective_admin(user):
        visible = visible.join(models.ProjectMember, models.ProjectMember.project_id == P.id).filter(models.ProjectMember.user_id == user.id)
    ids = [pid for pid, in visible]
    if not ids:
        return {"projects": [], "works": []}
    projects = [pid for pid, in db.query(P.id).filter(P.id.in_(ids), or_(P.name.contains(term, autoescape=True), P.description.contains(term, autoescape=True)))]
    hits: dict[int, tuple[str, str]] = {}
    for wid, name in db.query(W.id, W.name).filter(W.project_id.in_(ids), W.name.contains(term, autoescape=True)).limit(SEARCH_LIMIT):
        hits[wid] = ("name", name)
    for wid, name in db.query(A.work_id, A.filename).filter(A.project_id.in_(ids), A.work_id.isnot(None), A.filename.contains(term, autoescape=True)).limit(SEARCH_LIMIT):
        hits.setdefault(wid, ("file", name))
    for wid, title in db.query(R.work_id, R.title).filter(R.project_id.in_(ids), R.work_id.isnot(None), R.title.contains(term, autoescape=True)).limit(SEARCH_LIMIT):
        hits.setdefault(wid, ("result", title))
    rows = (db.query(W, P.name, models.User.display_name).join(P, W.project_id == P.id).join(models.User, W.owner_id == models.User.id)
            .filter(W.id.in_(hits)).order_by(W.id.desc()).limit(SEARCH_LIMIT).all())
    files: dict[int, str] = {}
    for wid, name in db.query(A.work_id, A.filename).filter(A.work_id.in_(hits)).order_by(A.id):
        if name.lower().endswith(MODEL_EXT):
            files[wid] = name
    return {"projects": projects, "works": [
        {"project_id": w.project_id, "project_name": pname, "work_id": w.id, "work_name": w.name, "owner_name": owner, "created_at": w.created_at,
         "model_file": hits[w.id][1] if hits[w.id][0] == "file" else files.get(w.id), "matched": hits[w.id][0], "matched_text": hits[w.id][1]}
        for w, pname, owner in rows]}


# ---------- 삭제 ----------
def counts_for_delete(db: Session, work_id: int) -> dict[str, int]:
    ids = [sid for sid, in db.query(models.Study.id).filter_by(work_id=work_id)]
    if not ids:
        return {"n_simulators": 0, "n_share_links": 0}
    sims = db.query(models.SurrogateModel.study_id).filter(models.SurrogateModel.study_id.in_(ids), models.SurrogateModel.deployed.is_(True)).distinct().count()
    links = (db.query(models.ShareLink.id).join(models.SurrogateModel, models.ShareLink.model_id == models.SurrogateModel.id)
             .filter(models.SurrogateModel.study_id.in_(ids)).count())
    return {"n_simulators": sims, "n_share_links": links}


def delete_work(db: Session, work: models.Work) -> list[int]:
    """Task와 그 안의 모든 것을 코드로 지운다. 기존 DB의 work_id 열에는 외래 키가 없어(ensure_columns는 열만 더한다) DB에 맡길 수 없다.
    PC 폴더는 건드리지 않는다. 지운 스터디 id를 돌려준다(판단 도우미 기억을 비우려고)."""
    wid = work.id
    for model in (models.Report, models.Artifact, models.Flow):
        db.query(model).filter_by(work_id=wid).delete(synchronize_session=False)
    study_ids = []
    for s in db.query(models.Study).filter_by(work_id=wid).all():  # ORM으로 지워야 케이스까지 함께 지워진다
        study_ids.append(s.id)
        db.delete(s)
    for c in db.query(models.ChatSession).filter_by(work_id=wid).all():
        hub.forget_chat(c.id)
        db.delete(c)
    atts = db.query(models.Attachment).filter_by(work_id=wid).all()
    stored = [a.stored for a in atts]
    for a in atts:
        db.delete(a)
    db.flush()
    db.delete(work)
    db.commit()
    for rel in stored:
        try:
            (settings.files_dir / rel).unlink(missing_ok=True)
        except OSError as e:  # 지운 뒤라 되돌릴 수 없다. 파일이 잠겨 남아도 Task 삭제는 끝난 것이다
            log.warning("첨부 파일을 지우지 못했습니다: %s (%s)", rel, e)
    return study_ids


# ---------- 이전 ----------
_FILLED = (models.ChatSession, models.Flow, models.Study, models.Artifact, models.Report)


def _backup_once():
    """처음 이전하기 전에 DB를 한 번 복사해 둔다. 켜진 DB도 안전하게 복사되도록 SQLite의 백업 기능을 쓴다."""
    if engine.dialect.name != "sqlite":
        return
    src = make_url(str(engine.url)).database
    if not src or src == ":memory:":
        return
    dest = Path(src).parent / "backups" / "app-before-works.db"
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_suffix(".tmp")
    a, b = sqlite3.connect(src), sqlite3.connect(temp)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()
    temp.replace(dest)
    log.info("Task 구조로 옮기기 전 DB를 백업했습니다: %s", dest)


def _legacy_owner_and_users(db: Session, project: models.Project) -> tuple[int, list[int]]:
    C, K, S, M, A = models.ChatSession, models.Case, models.Study, models.SurrogateModel, models.Attachment
    last = db.query(C.user_id).filter_by(project_id=project.id).order_by(C.id.desc()).first()
    owner = last[0] if last else project.owner_id
    seen = {u for u, in db.query(C.user_id).filter_by(project_id=project.id)}
    seen |= {u for u, in db.query(K.run_by).join(S, K.study_id == S.id).filter(S.project_id == project.id, K.run_by.isnot(None))}
    seen |= {u for u, in db.query(M.created_by).join(S, M.study_id == S.id).filter(S.project_id == project.id, M.created_by.isnot(None))}
    seen |= {u for u, in db.query(A.user_id).filter_by(project_id=project.id)}
    members = {u for u, in db.query(models.ProjectMember.user_id).filter_by(project_id=project.id)}
    return owner, sorted((seen & members) | {owner})


def migrate_works() -> int:
    """기존 자료를 '이전 기록' Task 하나로 묶는다. 대화별로 나누지 않는다(사용자 결정: 잇는 정보가 도구 메시지 190개 중 30개뿐이라 나누면 틀린다).
    여러 번 돌아도 결과가 같다. 옛 서버로 되돌렸다 오면 그때 생긴 행도 여기서 채운다. 옮긴 Project 수를 돌려준다.
    updated_at을 건드리지 않는다: 건드리면 모든 Project의 순서가 흔들리고, 모든 보고서가 '뒤에 바뀐 결과가 있다'고 잘못 말한다."""
    with SessionLocal() as db:
        need: set[int] = set()
        for model in _FILLED:
            need |= {pid for pid, in db.query(model.project_id).filter(model.work_id.is_(None)).distinct()}
        if not need:
            return 0
        _backup_once()
        for pid in sorted(need):
            project = db.get(models.Project, pid)
            had_works = db.query(models.Work.id).filter_by(project_id=pid).first() is not None
            work = db.query(models.Work).filter_by(project_id=pid, legacy=True).first()
            if work is None:
                owner, users = _legacy_owner_and_users(db, project)
                work = models.Work(project_id=pid, seq=0, name=LEGACY_NAME, name_auto=False, owner_id=owner, legacy=True, legacy_users=users,
                                   created_at=project.created_at, updated_at=project.created_at)
                db.add(work)
                db.flush()
            for model in _FILLED:
                values: dict = {model.work_id: work.id}
                if hasattr(model, "updated_at"):
                    values[model.updated_at] = model.updated_at
                db.query(model).filter(model.project_id == pid, model.work_id.is_(None)).update(values, synchronize_session=False)
            if not had_works:  # 첨부는 처음 옮길 때만. 그 뒤의 빈 칸은 보내기 전에 올려 둔 파일이다
                db.query(models.Attachment).filter(models.Attachment.project_id == pid, models.Attachment.work_id.is_(None)).update({models.Attachment.work_id: work.id}, synchronize_session=False)
            db.commit()
        log.info("기존 Project %d개의 기록을 '%s' Task로 묶었습니다", len(need), LEGACY_NAME)
        return len(need)

