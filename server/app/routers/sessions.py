import hashlib
import re
import uuid

from cae_adapters.tabular import SNIFF_BYTES, TableEncrypted, TableNotATable, sniff_table_file
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import models, schemas, work_service
from ..agent.hub import hub
from ..agent.link import AgentError, registry, work_fields
from ..config import settings
from ..database import get_db
from ..deps import effective_admin, get_current_user, get_or_404, get_project_for_member, member_role, require_ai_can_work, require_jwt, require_member

router = APIRouter(prefix="/api", tags=["agent"])


def _session_for_member(session_id: int, user: models.User, db: Session) -> models.ChatSession:
    chat = get_or_404(db, models.ChatSession, session_id, "기록")
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
        owner = db.get(models.User, chat.user_id)
        raise HTTPException(403, work_service.not_mine_message(owner.display_name if owner else "다른 멤버"))
    return chat


@router.get("/projects/{project_id}/sessions", response_model=list[schemas.ChatSessionResponse])
def list_sessions(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    return db.query(models.ChatSession).filter_by(project_id=project.id).order_by(models.ChatSession.id.desc()).all()


@router.post("/projects/{project_id}/sessions", response_model=schemas.ChatSessionResponse)
def create_session(body: schemas.ChatSessionCreate | None = None, project: models.Project = Depends(get_project_for_member),
                   user: models.User = Depends(get_current_user), db: Session = Depends(get_db), header: int | None = Depends(work_service.header_hint)):
    """기록을 하나 더 연다. 화면은 쓰지 않고(Task를 만들 때 함께 생긴다) 스크립트·시험과 옛 호출을 위해 남긴다. 남의 Task에는 열지 않는다."""
    conn = registry.get(user.id)
    agent = (body.agent if body else None) or (conn.info.get("default_agent") if conn else None)
    if not agent:  # 정해진 값으로 채우면 사용자가 고르지 않은 CLI가 뜬다(Agent가 막 붙어 hello가 오기 전에 실제로 있었다)
        raise HTTPException(409, "사용할 AI 에이전트를 아직 알 수 없습니다 → 입력창에서 에이전트를 고르거나 PC Agent가 연결된 뒤 다시 시도하세요")
    _check_agent(conn, agent)
    work = work_service.write_work(db, project.id, user, body.work_id if body else None, header)
    if not work_service.is_mine(work, user.id):
        if body and body.work_id:
            raise HTTPException(403, work_service.not_mine_message(work_service.owner_label(db, work)))
        work = work_service.new_work(db, project.id, user.id)
    s = work_service.add_chat(db, work, user.id, agent)
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
    require_ai_can_work(chat.project_id, user, db)
    if not body.text.strip() and not body.attachment_ids:
        raise HTTPException(400, "내용을 입력하세요")
    atts = []
    for aid in body.attachment_ids:
        a = db.get(models.Attachment, aid)
        if not a or a.project_id != chat.project_id:
            raise HTTPException(404, "첨부 파일을 찾을 수 없습니다")
        if a.work_id is None:  # 놓을 때는 Task를 정하지 않고 보낼 때 정한다. 그래야 새 Task로 옮겨 갈 수 있다
            a.work_id = chat.work_id
        atts.append({"id": a.id, "filename": a.filename, "size": a.size, "sha256": a.sha256})
    text = body.text.strip() or "첨부한 파일을 확인해 주세요."
    _check_agent(registry.get(chat.user_id), chat.agent)
    db.commit()
    try:
        # 바로 돌아온다. CLI 준비는 뒤에서 하고 화면은 turn_started·turn_stage로 단계를 본다
        turn_id = hub.send_prompt(session_id, text, atts)
    except AgentError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "turn_id": turn_id}


@router.post("/sessions/{session_id}/cancel")
def cancel(session_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    _session_for_owner(session_id, user, db)
    try:
        hub.cancel(session_id)
    except AgentError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/sessions/{session_id}/force-restart")
def force_restart(session_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """중지했는데 AI 도구가 답하지 않을 때: 그 AI 도구를 끝내고 다시 띄운다. 멈춘 스크립트도 함께 끝나고, 창이 보이는 CAD는 남는다."""
    _session_for_owner(session_id, user, db)
    try:
        hub.force_restart(session_id)
    except AgentError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


def reply_permission(session_id: int, body: schemas.PermissionReply, user: models.User, db: Session) -> dict:
    _session_for_owner(session_id, user, db)
    try:
        hub.respond_permission(session_id, body.request_id, body.option_id)
    except AgentError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@router.post("/sessions/{session_id}/permission")
def permission(session_id: int, body: schemas.PermissionReply, user: models.User = Depends(require_jwt),
               db: Session = Depends(get_db)):
    """AI가 물은 권한에 답하는 것은 사람이다. 토큰으로 되면 AI가 자기 질문에 스스로 답한다."""
    return reply_permission(session_id, body, user, db)


@router.get("/sessions/{session_id}/status")
def status(session_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    chat = _session_for_member(session_id, user, db)
    return hub.status(session_id, chat.user_id)


@router.get("/agent/status")
def agent_status(user: models.User = Depends(get_current_user)):
    """내 PC Agent 연결 상태."""
    conn = registry.get(user.id)
    return {"connected": conn is not None, "info": conn.info if conn else None,
            "active_project_id": hub.active_project.get(user.id)}


@router.post("/agent/install")
def install_agent(body: schemas.AgentInstall, user: models.User = Depends(require_jwt)):
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
        raise HTTPException(404, "활성 Project가 없습니다")
    return {"project_id": pid}


# ---------- 대화 이름 변경·삭제 ----------
def _session_for_editor(session_id: int, user: models.User, db: Session) -> models.ChatSession:
    chat = _session_for_member(session_id, user, db)
    if chat.user_id != user.id and member_role(chat.project_id, user, db) != "master" and not effective_admin(user):
        raise HTTPException(403, "Task를 만든 사람이나 Project 관리자만 바꿀 수 있습니다")
    return chat


@router.put("/sessions/{session_id}", response_model=schemas.ChatSessionResponse)
def rename_session(session_id: int, body: schemas.SessionUpdate, user: models.User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    chat = _session_for_editor(session_id, user, db)
    if body.title is not None:
        chat.title = body.title.strip()
    if body.agent and body.agent != chat.agent:
        if chat.user_id != user.id:
            raise HTTPException(403, "이 Task의 주인만 AI를 바꿀 수 있습니다")
        if hub.status(session_id)["busy"]:
            raise HTTPException(409, "에이전트가 작업 중입니다. 먼저 중지하세요")
        _check_agent(registry.get(user.id), body.agent)
        hub.forget_session(chat.agent_session_id)
        chat.agent = body.agent
        chat.agent_session_id = None  # 다음 요청 때 새 CLI로 세션을 열고 이전 대화를 요약해 넘긴다
        flag_modified(chat, "agent_session_id")  # 읽은 값이 이미 None이어도 쓴다: 그사이 중지한 턴의 준비가 값을 썼을 수 있다
    db.commit()
    return chat


@router.delete("/sessions/{session_id}")
def delete_session(session_id: int, user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    chat = _session_for_editor(session_id, user, db)
    if hub.status(session_id)["busy"]:
        raise HTTPException(409, "AI가 답을 쓰는 중이라 지울 수 없습니다 → 먼저 중지하세요")
    hub.forget_chat(chat.id)
    for f in db.query(models.Flow).filter_by(chat_id=chat.id):
        if f.start_queued and f.status == "approved":  # 승인을 전할 대화가 없어졌다. 멈춘 채 두지 않고 승인 대기로 되돌린다
            f.status, f.approved_by = "review", None
            f.version += 1
        f.chat_id, f.handoff = None, None
    db.flush()
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
    # 표 파일이면 앞부분을 본다. 암호화된 표를 그대로 받으면 AI가 시작될 때까지 몇 분 기다린 뒤에야 읽을 수 없다는 말을 듣는다
    with open(dest, "rb") as f:
        head = f.read(SNIFF_BYTES)
    try:
        sniff_table_file(head, name)
    except (TableEncrypted, TableNotATable) as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    a = models.Attachment(project_id=project.id, user_id=user.id, filename=name, size=size, sha256=h.hexdigest(), stored=rel)
    db.add(a)
    db.commit()
    return a


@router.get("/projects/{project_id}/attachments", response_model=list[schemas.AttachmentResponse])
def list_attachments(scope: work_service.WorkScope = Depends(work_service.work_scope), db: Session = Depends(get_db)):
    q = db.query(models.Attachment).filter_by(project_id=scope.project.id)
    return work_service.narrow(q, models.Attachment, scope.read()).order_by(models.Attachment.id.desc()).all()


@router.get("/attachments/{attachment_id}/download")
def download_attachment(attachment_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = get_or_404(db, models.Attachment, attachment_id, "첨부 파일")
    require_member(a.project_id, user, db)
    return FileResponse(settings.files_dir / a.stored, filename=a.filename)


# ---------- 내 PC 폴더 (PC Agent 경유) ----------
def _local_work(scope: work_service.WorkScope, user: models.User) -> dict:
    """work_id를 주면 그 Task 폴더를 본다. 폴더를 만들지 않는다. 주지 않으면(Project 메뉴, 옛 화면) Project 폴더다."""
    wid = scope.hint
    if not wid:
        return {}
    w = scope.db.get(models.Work, wid)
    if not w or w.project_id != scope.project.id:
        raise HTTPException(404, "이 Project에 없는 Task입니다 → 왼쪽 목록에서 다시 골라 주세요")
    return work_fields(registry.get(user.id), w)


@router.post("/projects/{project_id}/local/open")
def open_local(body: schemas.LocalOpen, scope: work_service.WorkScope = Depends(work_service.work_scope),
               user: models.User = Depends(require_jwt)):
    """내 PC에서 Task 폴더나 Project 폴더(또는 케이스 폴더·결과 파일)를 탐색기나 CAE 뷰어로 연다."""
    project = scope.project
    try:
        return registry.runner(user.id).call("local.open", {"project_id": project.id, "project_name": project.name, "project_uid": project.uid,
                                                            "path": body.path, "with_viewer": body.with_viewer, **_local_work(scope, user)})
    except AgentError as e:
        raise HTTPException(409, str(e))


@router.get("/projects/{project_id}/local/tree")
def local_tree(path: str = "", scope: work_service.WorkScope = Depends(work_service.work_scope),
               user: models.User = Depends(get_current_user)):
    """내 PC 폴더 탐색 (폴더·파일 목록, 크기, 수정 시각). 이 PC에 Task 폴더가 없으면 missing이 온다."""
    project = scope.project
    try:
        return registry.runner(user.id).call("local.tree", {"project_id": project.id, "project_name": project.name, "project_uid": project.uid,
                                                            "path": path, **_local_work(scope, user)})
    except AgentError as e:
        raise HTTPException(409, str(e))


def _agent_call(user: models.User, type_: str, payload: dict, timeout: float = 30):
    try:
        return registry.runner(user.id).call(type_, payload, timeout=timeout)
    except AgentError as e:
        raise HTTPException(409, str(e))


@router.get("/projects/{project_id}/local/folder")
def get_local_folder(scope: work_service.WorkScope = Depends(work_service.work_scope), user: models.User = Depends(get_current_user)):
    """이 Project(work_id를 주면 그 Task)의 내 PC 폴더 (직접 정했는지 여부 포함). 폴더는 사람마다·PC마다 다르다."""
    project = scope.project
    return _agent_call(user, "local.folder", {"project_id": project.id, "project_name": project.name, "project_uid": project.uid, **_local_work(scope, user)})


@router.put("/projects/{project_id}/local/folder")
def set_local_folder(body: schemas.LocalFolder, project: models.Project = Depends(get_project_for_member),
                     user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    """내 PC에서 이 프로젝트의 작업 폴더를 바꾼다(path 없으면 기본 위치). 기존 파일은 옮기지 않는다.
    에이전트 CLI 세션은 시작 폴더가 고정이므로, 내 대화들은 다음 요청부터 새 폴더에서 새 세션으로 이어 간다(지난 대화 요약 전달)."""
    chats = db.query(models.ChatSession).filter_by(project_id=project.id, user_id=user.id).all()
    if any(hub.status(c.id).get("busy") for c in chats):
        raise HTTPException(409, "AI가 이 Project에서 일하는 중입니다 → 끝난 뒤 바꾸세요")
    info = _agent_call(user, "local.folder.set", {"project_id": project.id, "project_name": project.name,
                                                   "project_uid": project.uid, "path": body.path})
    for c in chats:
        hub.forget_chat(c.id)  # 시작 중 중지한 턴의 준비가 옛 폴더의 세션을 뒤늦게 묶지 못하게 한다
        c.agent_session_id = None
        flag_modified(c, "agent_session_id")  # 읽은 값이 이미 None이어도 쓴다
    db.commit()
    return info


@router.post("/agent/check-folder")
def check_folder(body: schemas.LocalFolder, user: models.User = Depends(require_jwt)):
    """새 프로젝트를 만들기 전에 고른 폴더를 작업 폴더로 쓸 수 있는지 확인한다."""
    if not body.path:
        raise HTTPException(422, "폴더 경로를 입력하세요")
    return _agent_call(user, "local.folder.check", {"path": body.path})


@router.post("/agent/pick-folder")
def pick_folder(body: schemas.FolderPick, user: models.User = Depends(require_jwt)):
    """PC Agent가 있는 내 PC에 Windows 폴더 선택 창을 띄운다. 사용자가 고를 때까지 기다린다(취소하면 빈 경로)."""
    return _agent_call(user, "local.pick_folder", {"initial": body.initial, "title": body.title}, timeout=900)


@router.get("/tools/catalog")
def tools_catalog(user: models.User = Depends(get_current_user)):
    """지원하는 CAD/CAE 툴 목록(분야·할 수 있는 일·스킬). 내 PC 설치 상태는 /agent/diagnose의 catalog."""
    from cae_adapters.catalog import CATEGORIES, public_catalog

    return {"categories": CATEGORIES, "tools": public_catalog()}


@router.get("/agent/diagnose")
def agent_diagnose(rescan: bool = False, user: models.User = Depends(get_current_user)):
    """내 PC 진단: CAD/CAE 툴 경로·버전, 뷰어, 작업 폴더, AI CLI (PC Agent가 확인).
    rescan=true면 PC Agent가 이 PC의 툴을 지금 다시 찾는다(설치된 앱 목록·시작 메뉴·환경변수·모든 드라이브)."""
    try:
        return registry.runner(user.id).call("local.diagnose", {"rescan": rescan}, timeout=240 if rescan else 120)
    except AgentError as e:
        raise HTTPException(409, str(e))


@router.post("/agent/tools/{key}/pick")
def pick_tool(key: str, user: models.User = Depends(require_jwt)):
    """내 PC에 파일 선택 창을 띄워 툴 실행 파일을 고르게 하고 PC Agent에 저장한다(자동으로 못 찾았을 때). 취소하면 빈 경로."""
    return _agent_call(user, "tools.pick", {"key": key}, timeout=900)


@router.put("/agent/tools/{key}")
def set_tool(key: str, body: schemas.ToolPath, user: models.User = Depends(require_jwt)):
    """툴 실행 파일 경로를 정한다. path가 비면 직접 고른 값을 지우고 자동으로 찾은 것을 쓴다."""
    return _agent_call(user, "tools.set", {"key": key, "path": body.path})
