from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..agent.hub import hub
from ..agent.link import AgentError, registry
from ..database import get_db
from ..deps import get_current_user, get_or_404, get_project_for_member, require_member

router = APIRouter(prefix="/api", tags=["agent"])


def _session_for_member(session_id: int, user: models.User, db: Session) -> models.ChatSession:
    chat = get_or_404(db, models.ChatSession, session_id, "대화")
    require_member(chat.project_id, user, db)
    return chat


def _session_for_owner(session_id: int, user: models.User, db: Session) -> models.ChatSession:
    chat = _session_for_member(session_id, user, db)
    if chat.user_id != user.id:
        raise HTTPException(403, "대화를 시작한 사람만 이 대화에 요청할 수 있습니다. 새 대화를 만들어 주세요.")
    return chat


@router.get("/projects/{project_id}/sessions", response_model=list[schemas.ChatSessionResponse])
def list_sessions(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    return db.query(models.ChatSession).filter_by(project_id=project.id).order_by(models.ChatSession.id.desc()).all()


@router.post("/projects/{project_id}/sessions", response_model=schemas.ChatSessionResponse)
def create_session(body: schemas.ChatSessionCreate | None = None, project: models.Project = Depends(get_project_for_member),
                   user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    conn = registry.get(user.id)
    agent = (body.agent if body else None) or (conn.info.get("default_agent") if conn else None) or "gemini"
    if conn:
        known = {a["name"]: a for a in conn.info.get("agents", [])}
        if known and agent not in known:
            raise HTTPException(400, f"내 PC Agent에 설정되지 않은 에이전트입니다: {agent}")
        if known and not known[agent]["installed"]:
            raise HTTPException(409, f"{known[agent]['label']}이(가) 내 PC에 설치되어 있지 않습니다. 설정에서 설치하세요.")
    s = models.ChatSession(project_id=project.id, user_id=user.id, agent=agent)
    db.add(s)
    db.commit()
    return s


@router.get("/sessions/{session_id}/messages", response_model=list[schemas.ChatMessageResponse])
def list_messages(session_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    _session_for_member(session_id, user, db)
    return db.query(models.ChatMessage).filter_by(session_id=session_id).order_by(models.ChatMessage.id).all()


@router.post("/sessions/{session_id}/prompt")
def send_prompt(session_id: int, body: schemas.PromptRequest, user: models.User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    _session_for_owner(session_id, user, db)
    if not body.text.strip():
        raise HTTPException(400, "내용을 입력하세요")
    try:
        hub.send_prompt(session_id, body.text)
    except AgentError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/sessions/{session_id}/cancel")
def cancel(session_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    _session_for_owner(session_id, user, db)
    try:
        hub.cancel(session_id)
    except AgentError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/sessions/{session_id}/permission")
def permission(session_id: int, body: schemas.PermissionReply, user: models.User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    _session_for_owner(session_id, user, db)
    try:
        hub.respond_permission(session_id, body.request_id, body.option_id)
    except AgentError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.get("/sessions/{session_id}/status")
def status(session_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    _session_for_member(session_id, user, db)
    return hub.status(session_id)


@router.get("/agent/status")
def agent_status(user: models.User = Depends(get_current_user)):
    """내 PC Agent 연결 상태."""
    conn = registry.get(user.id)
    return {"connected": conn is not None, "info": conn.info if conn else None,
            "active_project_id": hub.active_project.get(user.id)}


@router.post("/agent/install")
def install_agent(body: schemas.AgentInstall, user: models.User = Depends(get_current_user)):
    """내 PC Agent에 에이전트 CLI(gemini/claude/codex) 설치를 요청한다. 수 분 걸릴 수 있다."""
    try:
        return registry.runner(user.id).install(body.agent)
    except AgentError as e:
        raise HTTPException(409, str(e))


@router.get("/agent/active-project")
def active_project(user: models.User = Depends(get_current_user)):
    """MCP 서버가 project_id를 받지 못했을 때 이 사용자가 최근 대화한 프로젝트."""
    pid = hub.active_project.get(user.id)
    if pid is None:
        raise HTTPException(404, "활성 프로젝트가 없습니다")
    return {"project_id": pid}
