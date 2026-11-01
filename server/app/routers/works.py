"""Task(코드 이름 Work): Project 안의 일 한 건. 기록·흐름·결과는 Task에 묶이고, 지우면 함께 사라진다."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError, StaleDataError

from .. import models, route_check, schemas, work_service
from ..agent.hub import hub
from ..agent.link import registry
from ..database import get_db
from ..deps import effective_admin, get_current_user, get_or_404, get_project_for_member, member_role, require_ai_can_work, require_jwt, require_member
from ..study import insights, service
from ..ws import manager
from .sessions import _check_agent

router = APIRouter(prefix="/api", tags=["works"])


def _visible(work_id: int, user: models.User, db: Session) -> models.Work:
    w = get_or_404(db, models.Work, work_id, "Task")
    require_member(w.project_id, user, db)
    return w


def _manageable(work_id: int, user: models.User, db: Session) -> models.Work:
    """이름 변경·삭제: 주인, 프로젝트 master, 관리자. 실행 권한과 다르다."""
    w = _visible(work_id, user, db)
    if w.owner_id != user.id and member_role(w.project_id, user, db) != "master" and not effective_admin(user):
        raise HTTPException(403, f"{work_service.owner_label(db, w)}님의 Task입니다 → Task를 만든 사람이나 Project 관리자만 바꿀 수 있습니다.")
    return w


def _chats(db: Session, work_id: int) -> list[dict]:
    C, U = models.ChatSession, models.User
    rows = db.query(C, U.display_name).join(U, C.user_id == U.id).filter(C.work_id == work_id).order_by(C.id).all()
    return [{"id": c.id, "user_id": c.user_id, "display_name": name, "agent": c.agent, "created_at": c.created_at} for c, name in rows]


def _source(db: Session, work_id: int, user: models.User) -> models.Work:
    """가져올 Task. 내가 멤버인 Project의 것만 된다. 관리자여도 멤버가 아니면 안 된다: 값이 다른 Project로 옮겨 가는 길이다."""
    w = get_or_404(db, models.Work, work_id, "가져올 Task")
    if member_role(w.project_id, user, db) is None:
        raise HTTPException(403, "내가 멤버인 Project의 Task에서만 가져올 수 있습니다 → 그 Project 관리자에게 멤버 추가를 요청하세요")
    return w


def _detail(db: Session, w: models.Work, user: models.User) -> dict:
    brief = next(b for b in work_service.briefs(db, [w.project_id], user)[w.project_id] if b["id"] == w.id)
    chats = _chats(db, w.id)
    mine = [c for c in chats if c["user_id"] == user.id]
    # 내 Task면 내 최근 기록으로 보낸다. 남의 Task면 주인의 기록을 본다(진행 상황을 그대로 볼 수 있게)
    active = mine[-1] if brief["mine"] and mine else None if brief["mine"] else next((c for c in reversed(chats) if c["user_id"] == w.owner_id), chats[-1] if chats else None)
    can_undo = brief["mine"] and work_service.undo_blocker(db, w) is None
    return {**brief, "chats": chats, "active_chat_id": active["id"] if active else None, "can_undo": can_undo, **work_service.counts_for_delete(db, w.id)}


def _changed(w_project_id: int, work_id: int, deleted: bool = False):
    # 사이드바는 Project 목록에 Task 줄을 함께 받는다
    manager.publish({"type": "project_updated", "project_id": w_project_id, "work_id": work_id, "data": {"id": w_project_id, "work_id": work_id, **({"deleted_work": True} if deleted else {})}})


def _agent_for(user: models.User, wanted: str | None) -> str | None:
    conn = registry.get(user.id)
    agent = wanted or (conn.info.get("default_agent") if conn else None)
    if agent:
        _check_agent(conn, agent)
    return agent


@router.get("/works/search", response_model=schemas.WorkSearchResult)
def search_works(q: str = "", user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """사이드바 검색: Project 이름·설명, Task 이름, 모델 파일 이름, 결과 제목."""
    return work_service.search(db, q, user)


@router.get("/works/sources", response_model=list[schemas.WorkSource])
def work_sources(project_id: int | None = None, q: str = "", user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """지난 Task처럼 시작 목록. 지난 Task가 없으면 빈 목록이고, 화면은 이 칸을 보이지 않는다."""
    return work_service.sources(db, user, project_id, q)


@router.get("/projects/{project_id}/works", response_model=list[schemas.WorkBrief])
def list_works(project: models.Project = Depends(get_project_for_member), user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return work_service.briefs(db, [project.id], user)[project.id]


@router.post("/projects/{project_id}/works", response_model=schemas.WorkDetail)
def create_work(body: schemas.WorkCreate | None = None, project: models.Project = Depends(get_project_for_member),
                user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """내 Task와 기록을 만든다. 번호는 다시 쓰지 않는다. 멤버는 누구나 자기 Task를 만들 수 있다."""
    require_ai_can_work(project.id, user, db)
    body = body or schemas.WorkCreate()
    agent = _agent_for(user, body.agent)  # 쓸 수 없는 AI면 Task를 만들기 전에 알린다
    source = _source(db, body.from_work_id, user) if body.from_work_id else None  # 가져올 수 없으면 번호를 쓰기 전에 알린다
    w = work_service.new_work(db, project.id, user.id, body.name, body.name_auto)
    if source:
        work_service.start_from(db, w, source, user)
    if agent:
        work_service.add_chat(db, w, user.id, agent)
    db.commit()
    _changed(project.id, w.id)
    return _detail(db, w, user)


@router.get("/works/{work_id}", response_model=schemas.WorkDetail)
def get_work(work_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _detail(db, _visible(work_id, user, db), user)


@router.put("/works/{work_id}", response_model=schemas.WorkDetail)
def rename_work(work_id: int, body: schemas.WorkUpdate, user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    """사람이 정한 이름은 AI가 바꾸지 않는다(name_auto). PC 폴더도 따라간다."""
    w = _manageable(work_id, user, db)
    if body.version is not None and w.version != body.version:
        raise HTTPException(409, "다른 사람이 먼저 바꿨습니다 → 새로고침한 뒤 다시 하세요")
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "이름을 입력하세요")
    w.name, w.name_auto = name, False
    w.version += 1
    db.commit()
    _changed(w.project_id, w.id)
    work_service.tell_agents_renamed(db, w)
    return _detail(db, w, user)


@router.delete("/works/{work_id}")
def delete_work(work_id: int, user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    """서버의 기록·흐름·결과·보고서·Simulator를 지운다. PC 폴더는 건드리지 않는다. 돌고 있으면 막는다: 끝나지 않은 일의 결과가 갈 곳을 잃는다."""
    w = _manageable(work_id, user, db)
    studies = [sid for sid, in db.query(models.Study.id).filter_by(work_id=w.id)]
    # 서버가 다시 시작하면 도는 목록을 잊는다. PC가 꺼진 채 '실행 중'으로 남은 케이스도 표로 본다. 중지는 PC 없이 된다
    active = db.query(models.Case.id).filter(models.Case.study_id.in_(studies), models.Case.status.in_(service.ACTIVE)).first() if studies else None
    if active or any(service.is_running(sid) for sid in studies):
        raise HTTPException(409, "해석이 돌고 있어 지울 수 없습니다 → 데이터 탭에서 먼저 중지하세요")
    if any(service.training_now(sid) for sid in studies):
        raise HTTPException(409, "예측 모델을 학습하는 중이라 지울 수 없습니다 → 끝난 뒤에 지우세요")
    chats = [cid for cid, in db.query(models.ChatSession.id).filter_by(work_id=w.id)]
    if any(hub.status(cid).get("busy") for cid in chats):
        raise HTTPException(409, "AI가 답을 쓰는 중이라 지울 수 없습니다 → 먼저 중지하세요")
    if db.query(models.TableJob.id).filter_by(work_id=w.id, status="running").first():
        raise HTTPException(409, "표로 Simulator를 만드는 중이라 지울 수 없습니다 → 끝난 뒤에 지우세요")
    for cid in chats:  # 끊긴 PC Agent에는 전하지 못한다. 그 CLI 세션은 다음 시작까지 떠 있을 뿐 결과는 갈 곳이 없어 받지 않는다
        hub.end_session(cid)
    pid, wid = w.project_id, w.id
    insights.forget(work_service.delete_work(db, w))
    _changed(pid, wid, deleted=True)
    return {"ok": True}


@router.put("/projects/{project_id}/works/current")
def name_current_work(body: schemas.WorkAiInfo, scope: work_service.WorkScope = Depends(work_service.work_scope),
                      user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """AI가 지금 Task의 이름을 정한다(dds-flow.set_work_info). 처음 자동으로 붙인 이름만 바꾸고, 그 뒤로는 사용자가 바꿔 달라고 했을 때만 바꾼다."""
    w = work_service.resolve(db, scope.project.id, user, scope.explicit, scope.header)
    if w is None:
        raise HTTPException(404, "이 Project에 내 Task가 없습니다 → 먼저 Task를 시작하세요")
    if not work_service.is_mine(w, user.id):
        raise HTTPException(403, work_service.not_mine_message(work_service.owner_label(db, w)))
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "이름을 입력하세요")
    if w.legacy or (not w.name_auto and not body.force):
        return {"work_id": w.id, "name": w.name, "changed": False,
                "note": "이전 기록은 이름을 바꾸지 않습니다" if w.legacy else "이미 정한 이름입니다. 사용자가 바꿔 달라고 하면 force=True로 바꾸세요"}
    w.name, w.name_auto = name, False
    w.version += 1
    db.commit()
    _changed(w.project_id, w.id)
    work_service.tell_agents_renamed(db, w)
    return {"work_id": w.id, "name": w.name, "changed": True}


@router.get("/works/{work_id}/takeover-preview", response_model=schemas.TakeoverPreview)
def takeover_preview(work_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """내 PC로 복사해서 하기 전에 알릴 것: 없는 툴, 받지 못하는 원본. PC Agent가 꺼져 있어도 답한다."""
    return work_service.takeover_preview(db, _source(db, work_id, user), user)


@router.post("/works/{work_id}/takeover", response_model=schemas.WorkDetail)
def take_over(work_id: int, body: schemas.TakeoverRequest | None = None, user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    """내 PC로 복사해서 하기. 사람이 누를 때만 한다. 주인의 PC가 꺼져 있어도 서버에 있는 것으로 된다. 원본은 그대로다."""
    source = _source(db, work_id, user)
    body = body or schemas.TakeoverRequest()
    agent = _agent_for(user, body.agent)
    try:
        w = work_service.take_over(db, source, user, body.name, agent)
    except (ObjectDeletedError, StaleDataError, IntegrityError) as e:  # 복사하는 사이 원본이 지워졌다. 반쪽 Task를 남기지 않는다
        db.rollback()
        raise HTTPException(409, "복사하는 사이 원본 Task가 지워졌습니다 → 왼쪽 목록을 새로고침한 뒤 다시 고르세요") from e
    _changed(w.project_id, w.id)
    return _detail(db, w, user)


@router.get("/works/{work_id}/carry/{study_id}")
def carry_recipe(work_id: int, study_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """PC Agent가 받는 이 Task 몫의 스크립트 묶음. 원본 스터디가 지워져도 그대로 있다."""
    w = _visible(work_id, user, db)
    path = work_service._carry_dir(w.project_id, w.id) / f"study_{study_id}.zip"
    if not path.is_file():
        raise HTTPException(404, "받을 스크립트 묶음이 서버에 없습니다 → AI가 스크립트를 새로 만들게 요청하세요")
    return FileResponse(path, filename=path.name, media_type="application/zip")


@router.post("/route-check", response_model=schemas.RouteResult)
def check_route(body: schemas.RouteCheck, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """이 요청을 어디서 할까. AI 없이 바로 답하고 아무것도 만들지 않는다. 모르면 card가 비어 있고, 그대로 보내면 된다."""
    work = _visible(body.work_id, user, db) if body.work_id else None
    if body.project_id and not work:
        require_member(body.project_id, user, db)
    return route_check.check(db, user, work, body.project_id, body.text, [f.model_dump() for f in body.files], body.kept)


@router.post("/projects/{project_id}/route-suggestion")
def suggest_route(body: schemas.RouteSuggestion, scope: work_service.WorkScope = Depends(work_service.work_scope), user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """AI가 첫 단계에서 새 모델이나 다른 부품을 알아본 경우(dds-flow.suggest_route). 기록에 같은 카드를 남긴다. 고르는 것은 사람이다."""
    w = scope.write()
    chat_id = hub.active_chat(user.id, scope.project.id, w.id) or (lambda c: c.id if c else None)(work_service.my_chat(db, w, user.id))
    if chat_id is None:
        raise HTTPException(409, "이 Task에 기록이 없습니다 → 먼저 요청을 보내세요")
    M = models.ChatMessage
    last = db.query(M).filter(M.session_id == chat_id, M.role == "user").order_by(M.id.desc()).first()
    if last and (last.meta or {}).get("route_kept"):  # 사람이 이미 골랐다. 다시 묻지 않는다
        return {"shown": False, "note": "사용자가 이미 '여기서 계속'을 골랐습니다. 다시 묻지 말고 이 Task에서 진행하세요"}
    if db.query(M.id).filter(M.session_id == chat_id, M.role == "route", M.id > (last.id if last else 0)).first():
        return {"shown": False, "note": "이미 카드를 올렸습니다. 턴을 끝내세요"}
    card = "A" if body.kind == "new_model" else "B"
    attachments = (last.meta or {}).get("attachments") or [] if last else []
    db.add(M(session_id=chat_id, role="route", content=body.reason.strip(), meta={"card": card, "request": last.content if last else "", "attachments": attachments,
                                                                                  "source_work": {"id": w.id, "name": w.name}}))
    db.commit()
    return {"shown": True, "note": "카드를 올렸습니다. 아무것도 돌리지 말고 턴을 끝내세요. 사용자가 고릅니다"}


@router.post("/projects/{project_id}/attachments/adopt")
def adopt_attachments(body: schemas.AttachmentAdopt, project: models.Project = Depends(get_project_for_member), user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    """요청과 첨부를 새 Project로 옮긴다(카드 B). 내가 올리고 아직 어느 Task에도 보내지 않은 첨부만 된다."""
    for aid in body.ids:
        a = db.get(models.Attachment, aid)
        if a is None or a.user_id != user.id or a.work_id is not None:
            raise HTTPException(404, "옮길 수 있는 첨부가 아닙니다 → 파일을 다시 끌어 놓으세요")
        require_member(a.project_id, user, db)
        a.project_id = project.id
    db.commit()
    return {"ok": True}


@router.post("/works/{work_id}/undo", response_model=schemas.WorkUndone)
def undo(work_id: int, user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    """새 Task를 잘못 시작했을 때. 승인·결과 전에만 된다. AI를 끝내고 Task를 지우고 요청과 첨부를 원래 Task의 입력창으로 돌려놓는다. 폴더는 가져온 파일뿐일 때만 지운다."""
    w = work_service.require_mine(db, _visible(work_id, user, db), user)
    why = work_service.undo_blocker(db, w)
    if why:
        raise HTTPException(409, why)
    chats = [c for c, in db.query(models.ChatSession.id).filter_by(work_id=w.id)]
    if any(hub.status(c).get("busy") for c in chats):
        raise HTTPException(409, "AI가 답을 쓰는 중이라 되돌릴 수 없습니다 → 먼저 중지하세요")
    for c in chats:
        hub.end_session(c)
    pid, wid = w.project_id, w.id
    out = work_service.undo_work(db, w)
    insights.forget(out.pop("_studies"))
    _changed(pid, wid, deleted=True)
    return out


@router.post("/works/{work_id}/warm")
def warm(work_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """입력창에 처음 타자를 칠 때 AI를 뒤에서 준비한다. 구경만으로는 세션이 살아나지 않는다(주인이 타자를 칠 때만)."""
    w = work_service.require_mine(db, _visible(work_id, user, db), user)
    chat = work_service.my_chat(db, w, user.id)
    return {"warming": bool(chat) and hub.warm(chat.id)}


@router.post("/works/{work_id}/end-session")
def end_session(work_id: int, user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    """이 Task의 AI 끝내기. 기록과 결과는 그대로이고, 다음 요청은 AI를 다시 띄우는 시간이 걸린다. 끝내는 길은 이 하나다."""
    w = work_service.require_mine(db, _visible(work_id, user, db), user)
    chats = [c for c, in db.query(models.ChatSession.id).filter_by(work_id=w.id, user_id=user.id)]
    if any(hub.status(c).get("busy") for c in chats):
        raise HTTPException(409, "AI가 답을 쓰는 중이라 끝낼 수 없습니다 → 먼저 중지하세요")
    if any(service.is_running(s) for s, in db.query(models.Study.id).filter_by(work_id=w.id)):
        raise HTTPException(409, "해석이 돌고 있어 AI를 끝낼 수 없습니다 → 데이터 탭에서 먼저 중지하세요")
    for c in chats:
        hub.end_session(c)
    return {"ok": True}


@router.get("/works/{work_id}/messages", response_model=list[schemas.ChatMessageResponse])
def work_messages(work_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """이 Task의 기록. 이전 기록은 대화 여러 개를 시간순으로 합친다. 경계는 화면이 session_id로 그린다."""
    w = _visible(work_id, user, db)
    M, C = models.ChatMessage, models.ChatSession
    return (db.query(M).join(C, M.session_id == C.id).filter(C.work_id == w.id).order_by(M.created_at, M.id).all())


@router.post("/works/{work_id}/chat", response_model=schemas.ChatSessionResponse)
def open_chat(work_id: int, body: schemas.WorkChatCreate | None = None, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """입력창이 보내는 곳: 이 Task 안 내 최근 기록. 없으면 만든다(표로 시작한 Task, 이전 기록의 다른 멤버). 남의 Task에는 만들지 않는다.
    '내 새 대화'로 말없이 넘어가던 동작을 없앤 자리다."""
    w = _visible(work_id, user, db)
    if not work_service.is_mine(w, user.id):
        raise HTTPException(403, work_service.not_mine_message(work_service.owner_label(db, w)))
    chat = work_service.my_chat(db, w, user.id)
    if chat:
        return chat
    agent = _agent_for(user, body.agent if body else None)
    if not agent:
        raise HTTPException(409, "사용할 AI를 아직 알 수 없습니다 → 입력창에서 AI를 고르거나 PC Agent가 연결된 뒤 다시 시도하세요")
    chat = work_service.add_chat(db, w, user.id, agent)
    db.commit()
    return chat
