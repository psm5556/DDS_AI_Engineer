import hashlib
import re
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..agent.hub import hub
from ..agent.link import AgentError, registry
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, get_or_404, get_project_for_member, member_role, require_member

router = APIRouter(prefix="/api", tags=["agent"])


def _session_for_member(session_id: int, user: models.User, db: Session) -> models.ChatSession:
    chat = get_or_404(db, models.ChatSession, session_id, "대화")
    require_member(chat.project_id, user, db)
    return chat


def _check_agent(conn, agent: str):
    """내 PC Agent에 설정·설치된 AI CLI인지 확인한다(Agent 미연결이면 통과 — 연결 후 요청 때 다시 확인)."""
    if not conn:
        return
    known = {a["name"]: a for a in conn.info.get("agents", [])}
    if known and agent not in known:
        raise HTTPException(400, f"내 PC Agent에 설정되지 않은 에이전트입니다: {agent}")
    if known and not known[agent]["installed"]:
        raise HTTPException(409, f"{known[agent]['label']}이(가) 내 PC에 설치되어 있지 않습니다. 설정에서 설치하세요.")


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
    _check_agent(conn, agent)
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
    chat = _session_for_owner(session_id, user, db)
    if not body.text.strip() and not body.attachment_ids:
        raise HTTPException(400, "내용을 입력하세요")
    atts = []
    for aid in body.attachment_ids:
        a = db.get(models.Attachment, aid)
        if not a or a.project_id != chat.project_id:
            raise HTTPException(404, "첨부 파일을 찾을 수 없습니다")
        atts.append({"id": a.id, "filename": a.filename, "size": a.size, "sha256": a.sha256})
    text = body.text.strip() or "첨부한 파일을 확인해 주세요."
    try:
        hub.send_prompt(session_id, text, atts)
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


# ---------- 대화 이름 변경·삭제 ----------
def _session_for_editor(session_id: int, user: models.User, db: Session) -> models.ChatSession:
    chat = _session_for_member(session_id, user, db)
    if chat.user_id != user.id and member_role(chat.project_id, user, db) != "master" and not user.is_admin:
        raise HTTPException(403, "대화를 만든 사람이나 프로젝트 관리자만 바꿀 수 있습니다")
    return chat


@router.put("/sessions/{session_id}", response_model=schemas.ChatSessionResponse)
def rename_session(session_id: int, body: schemas.SessionUpdate, user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    chat = _session_for_editor(session_id, user, db)
    if body.title is not None:
        chat.title = body.title.strip()
    if body.agent and body.agent != chat.agent:
        if chat.user_id != user.id:
            raise HTTPException(403, "대화를 시작한 사람만 에이전트를 바꿀 수 있습니다")
        if hub.status(session_id)["busy"]:
            raise HTTPException(409, "에이전트가 작업 중입니다. 먼저 중지하세요")
        _check_agent(registry.get(user.id), body.agent)
        hub.forget_session(chat.agent_session_id)
        chat.agent = body.agent
        chat.agent_session_id = None  # 다음 요청 때 새 CLI로 세션을 열고 이전 대화를 요약해 넘긴다
    db.commit()
    return chat


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    chat = _session_for_editor(session_id, user, db)
    if hub.status(session_id)["busy"]:
        raise HTTPException(409, "에이전트가 작업 중인 대화는 지울 수 없습니다. 먼저 중지하세요")
    hub.forget_chat(chat.id)
    db.delete(chat)
    db.commit()
    return {"ok": True}


# ---------- 첨부 파일 ----------
SAFE_NAME = re.compile(r'[\\/:*?"<>|]+')


@router.post("/projects/{project_id}/attachments", response_model=schemas.AttachmentResponse)
async def upload_attachment(file: UploadFile = File(...), project: models.Project = Depends(get_project_for_member),
                            user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """대화 첨부 파일 업로드(CAD 모델·입력 파일 등). 큰 파일도 메모리에 다 올리지 않고 나눠 저장한다."""
    name = SAFE_NAME.sub("_", (file.filename or "file").strip()) or "file"
    rel = f"project_{project.id}/attachments/{uuid.uuid4().hex[:12]}_{name}"
    dest = settings.files_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    h, size, limit = hashlib.sha256(), 0, settings.max_upload_mb * 1024 * 1024
    with open(dest, "wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"{settings.max_upload_mb}MB보다 큰 파일은 올릴 수 없습니다")
            h.update(chunk)
            f.write(chunk)
    a = models.Attachment(project_id=project.id, user_id=user.id, filename=name, size=size, sha256=h.hexdigest(), stored=rel)
    db.add(a)
    db.commit()
    return a


@router.get("/projects/{project_id}/attachments", response_model=list[schemas.AttachmentResponse])
def list_attachments(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    return db.query(models.Attachment).filter_by(project_id=project.id).order_by(models.Attachment.id.desc()).all()


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = get_or_404(db, models.Attachment, attachment_id, "첨부 파일")
    require_member(a.project_id, user, db)
    return FileResponse(settings.files_dir / a.stored, filename=a.filename)


# ---------- 내 PC 작업 폴더 (PC Agent 경유) ----------
@router.post("/projects/{project_id}/local/open")
def open_local(body: schemas.LocalOpen, project: models.Project = Depends(get_project_for_member),
               user: models.User = Depends(get_current_user)):
    """내 PC에서 프로젝트 작업 폴더(또는 케이스 폴더·결과 파일)를 탐색기나 CAE 뷰어로 연다."""
    try:
        return registry.runner(user.id).call("local.open", {"project_id": project.id, "project_name": project.name, "project_uid": project.uid,
                                                            "path": body.path, "with_viewer": body.with_viewer})
    except AgentError as e:
        raise HTTPException(409, str(e))


@router.get("/projects/{project_id}/local/tree")
def local_tree(path: str = "", project: models.Project = Depends(get_project_for_member),
               user: models.User = Depends(get_current_user)):
    """내 PC 작업 폴더 탐색 (폴더·파일 목록, 크기, 수정 시각)."""
    try:
        return registry.runner(user.id).call("local.tree", {"project_id": project.id, "project_name": project.name, "project_uid": project.uid,
                                                            "path": path})
    except AgentError as e:
        raise HTTPException(409, str(e))


@router.get("/tools/catalog")
def tools_catalog(user: models.User = Depends(get_current_user)):
    """지원하는 CAD/CAE 툴 목록(분야·할 수 있는 일·스킬). 내 PC 설치 상태는 /agent/diagnose의 catalog."""
    from cae_adapters.catalog import CATEGORIES, public_catalog

    return {"categories": CATEGORIES, "tools": public_catalog()}


@router.get("/agent/diagnose")
def agent_diagnose(user: models.User = Depends(get_current_user)):
    """내 PC 진단: CAD/CAE 툴 경로·버전, 뷰어, 작업 폴더, AI CLI (PC Agent가 확인)."""
    try:
        return registry.runner(user.id).call("local.diagnose", {}, timeout=120)
    except AgentError as e:
        raise HTTPException(409, str(e))
