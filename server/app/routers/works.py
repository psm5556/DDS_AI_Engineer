"""Task(코드 이름 Work): Project 안의 일 한 건. 기록·흐름·결과는 Task에 묶이고, 지우면 함께 사라진다."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas, work_service
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


def _detail(db: Session, w: models.Work, user: models.User) -> dict:
    brief = next(b for b in work_service.briefs(db, [w.project_id], user)[w.project_id] if b["id"] == w.id)
    chats = _chats(db, w.id)
    mine = [c for c in chats if c["user_id"] == user.id]
    # 내 Task면 내 최근 기록으로 보낸다. 남의 Task면 주인의 기록을 본다(진행 상황을 그대로 볼 수 있게)
    active = mine[-1] if brief["mine"] and mine else None if brief["mine"] else next((c for c in reversed(chats) if c["user_id"] == w.owner_id), chats[-1] if chats else None)
    return {**brief, "chats": chats, "active_chat_id": active["id"] if active else None, **work_service.counts_for_delete(db, w.id)}


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
    w = work_service.new_work(db, project.id, user.id, body.name, body.name_auto)
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
    """사람이 정한 이름은 AI가 바꾸지 않는다(name_auto). PC 폴더는 3단계에서 따라간다."""
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
    return _detail(db, w, user)


@router.delete("/works/{work_id}")
def delete_work(work_id: int, user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    """서버의 기록·흐름·결과·보고서·Simulator를 지운다. PC 폴더는 건드리지 않는다. 돌고 있으면 막는다: 끝나지 않은 일의 결과가 갈 곳을 잃는다."""
    w = _manageable(work_id, user, db)
    studies = [sid for sid, in db.query(models.Study.id).filter_by(work_id=w.id)]
    if any(service.is_running(sid) for sid in studies):
        raise HTTPException(409, "해석이 돌고 있어 지울 수 없습니다 → 데이터 탭에서 먼저 중지하세요")
    chats = [cid for cid, in db.query(models.ChatSession.id).filter_by(work_id=w.id)]
    if any(hub.status(cid).get("busy") for cid in chats):
        raise HTTPException(409, "AI가 답을 쓰는 중이라 지울 수 없습니다 → 먼저 중지하세요")
    if db.query(models.TableJob.id).filter_by(work_id=w.id, status="running").first():
        raise HTTPException(409, "표로 Simulator를 만드는 중이라 지울 수 없습니다 → 끝난 뒤에 지우세요")
    pid, wid = w.project_id, w.id
    insights.forget(work_service.delete_work(db, w))
    _changed(pid, wid, deleted=True)
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
