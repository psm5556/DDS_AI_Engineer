"""Flow(작업 흐름 그래프)와 아티팩트. 에이전트는 dds-flow MCP 툴로, 사용자는 UI로 접근한다."""
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models, schemas, work_service
from ..agent.hub import hub
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, get_or_404, get_project_for_member, require_jwt, require_member
from ..study.service import check_flow, flow_summaries, publish_artifact
from ..ws import manager

router = APIRouter(prefix="/api", tags=["flows"])
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _flow_for_member(flow_id: int, user, db) -> models.Flow:
    f = get_or_404(db, models.Flow, flow_id, "플로우")
    require_member(f.project_id, user, db)
    return f


def _changed(f: models.Flow):
    manager.publish({"type": "flow_updated", "project_id": f.project_id, "data": {"id": f.id, "status": f.status}})


@router.get("/projects/{project_id}/flows", response_model=list[schemas.FlowResponse])
def list_flows(scope: work_service.WorkScope = Depends(work_service.work_scope), db: Session = Depends(get_db)):
    q = db.query(models.Flow).filter_by(project_id=scope.project.id)
    return work_service.narrow(q, models.Flow, scope.read()).order_by(models.Flow.id.desc()).all()


@router.get("/projects/{project_id}/flows/summary", response_model=list[schemas.FlowSummary])
def flow_summary(scope: work_service.WorkScope = Depends(work_service.work_scope), db: Session = Depends(get_db)):
    """지난 흐름 찾기: 흐름마다 단계 이름과 연결된 스터디·모델을 짧게 돌려준다."""
    # 지금 돌리는 Task로 좁히지 않는다. '지난번처럼 다시'는 다른 Task의 흐름을 찾는 일이다. 헤더를 밝히면(2단계) 그 Task만, all_works면 전체다
    return flow_summaries(db, scope.project.id, None if scope.all_works else scope.hint)


@router.post("/projects/{project_id}/flows", response_model=schemas.FlowResponse)
def create_flow(body: schemas.FlowCreate, scope: work_service.WorkScope = Depends(work_service.work_scope),
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    ids = [n.id for n in body.nodes]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "노드 id가 중복됩니다")
    for e in body.edges:
        if e.source not in ids or e.target not in ids:
            raise HTTPException(400, f"엣지가 없는 노드를 가리킵니다: {e.source} → {e.target}")
    project, work = scope.project, scope.write()
    # 이 Task의 대화에서 나온 제안이다. 옆 Task에서 돌던 대화에 승인을 전하면 엉뚱한 AI가 시작한다
    f = models.Flow(project_id=project.id, work_id=work.id, title=body.title, goal=body.goal, chat_id=hub.active_chat(user.id, project.id, work.id),
                    nodes=[n.model_dump() for n in body.nodes], edges=[e.model_dump() for e in body.edges])
    db.add(f)
    db.commit()
    _changed(f)
    return f


@router.get("/flows/{flow_id}", response_model=schemas.FlowResponse)
def get_flow(flow_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _flow_for_member(flow_id, user, db)


def apply_flow_status(flow_id: int, body: schemas.FlowStatusUpdate, user: models.User, db: Session) -> models.Flow:
    f = _flow_for_member(flow_id, user, db)
    if f.version != body.version:
        raise HTTPException(409, "다른 사용자가 수정했습니다. 새로고침 후 다시 시도하세요.")
    if body.status == "review" and f.status == "approved" and not f.start_queued:  # 이미 에이전트에게 전한 승인은 되돌릴 수 없다. 멈추려면 중지다
        raise HTTPException(409, "에이전트가 이미 시작했습니다 → 멈추려면 입력창의 중지 버튼을 누르세요.")
    f.status = body.status
    if body.status == "approved":
        f.approved_by = user.id
    # 승인·반려는 서버가 에이전트에게 전한다. 답하는 중이거나 PC Agent가 없으면 줄을 세우고, 턴이 끝나거나 다시 연결될 때 보낸다(hub)
    chat_id = _decision_chat(db, f, user, body.session_id) if body.status in ("approved", "rejected") else None
    if chat_id:
        f.chat_id = chat_id
    f.handoff = {"state": "queued", "kind": body.status} if chat_id else None
    if body.status == "review":
        f.approved_by = None
    f.version += 1
    db.commit()
    _changed(f)
    if chat_id:
        hub.flow_decided(chat_id)
        db.refresh(f)
    return f


@router.put("/flows/{flow_id}/status", response_model=schemas.FlowResponse)
def set_flow_status(flow_id: int, body: schemas.FlowStatusUpdate, user=Depends(require_jwt), db: Session = Depends(get_db)):
    """승인·반려는 사람만 한다. 개발 AI의 원격 검증은 /api/devtest/flows의 좁은 길을 쓴다."""
    return apply_flow_status(flow_id, body, user, db)


def _decision_chat(db: Session, f: models.Flow, user, session_id: int | None) -> int | None:
    """승인을 전할 대화: 지금 보는 대화 → 흐름을 제안한 대화. 내 대화만이다(남의 PC에서 돌리지 않는다).
    흐름을 REST로 직접 만든 스크립트처럼 대화가 없으면 전할 곳도 없다."""
    for cid in (session_id, f.chat_id):
        chat = db.get(models.ChatSession, cid) if cid else None
        if chat and chat.user_id == user.id and chat.project_id == f.project_id and chat.work_id == f.work_id:
            return chat.id
    return None


@router.put("/flows/{flow_id}/nodes/{node_id}", response_model=schemas.FlowResponse)
def update_node(flow_id: int, node_id: str, body: schemas.FlowNodeUpdate, user=Depends(get_current_user),
                db: Session = Depends(get_db)):
    """에이전트가 진행 상황을 갱신하는 경로. 버전 충돌 없이 노드 단위로 덮어쓴다."""
    f = _flow_for_member(flow_id, user, db)
    nodes = [dict(n) for n in f.nodes]
    node = next((n for n in nodes if n["id"] == node_id), None)
    if not node:
        raise HTTPException(404, f"노드 '{node_id}'가 없습니다")
    if body.status:
        node["status"] = body.status
    if body.detail is not None:
        node["detail"] = body.detail
    f.nodes = nodes
    states = {n["status"] for n in nodes}
    if f.status in ("approved", "in_progress", "review", "stopped", "failed"):  # 멈춘·실패한 흐름도 AI가 다시 움직이면 진행 중이다
        if states <= {"complete", "skipped"}:
            f.status = "complete"
        elif "failed" in states:
            f.status = "failed"
        elif f.status != "review":
            f.status = "in_progress"
    f.version += 1
    db.commit()
    _changed(f)
    if f.status == "in_progress":
        hub.adopt_flow(f.id, f.chat_id, user.id, f.project_id, f.work_id)
    return f


# ---------- 아티팩트 ----------
@router.get("/projects/{project_id}/artifacts", response_model=list[schemas.ArtifactResponse])
def list_artifacts(scope: work_service.WorkScope = Depends(work_service.work_scope), db: Session = Depends(get_db)):
    q = db.query(models.Artifact).filter_by(project_id=scope.project.id)
    return work_service.narrow(q, models.Artifact, scope.read()).order_by(models.Artifact.id.desc()).all()


@router.post("/projects/{project_id}/artifacts", response_model=schemas.ArtifactResponse)
def create_artifact(body: schemas.ArtifactCreate, scope: work_service.WorkScope = Depends(work_service.work_scope),
                    db: Session = Depends(get_db)):
    project = scope.project
    try:
        check_flow(db, project.id, body.flow_id, scope.hint)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 흐름을 밝혔으면 그 흐름의 Task가 가장 정확하다. 헤더가 없는 옛 Agent도 여기서 맞는다
    flow = db.get(models.Flow, body.flow_id) if body.flow_id else None
    work_id = (flow.work_id if flow else None) or scope.write().id
    return publish_artifact(db, project.id, body.kind, body.title, body.payload, body.flow_id, body.node_id, work_id=work_id)


@router.delete("/artifacts/{artifact_id}")
def delete_artifact(artifact_id: int, user=Depends(require_jwt), db: Session = Depends(get_db)):
    a = get_or_404(db, models.Artifact, artifact_id, "아티팩트")
    require_member(a.project_id, user, db)
    db.delete(a)
    db.commit()
    manager.publish({"type": "artifact_updated", "project_id": a.project_id, "work_id": a.work_id, "data": {"id": a.id, "deleted": True}})
    return {"ok": True}


@router.post("/projects/{project_id}/files")
async def upload_file(file: UploadFile = File(...), subdir: str = Form("images"),
                      project: models.Project = Depends(get_project_for_member)):
    """PC Agent/MCP 서버가 이미지(컨투어 캡처 등)를 올리는 경로. 반환된 path를 image 아티팩트에 쓴다."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in IMAGE_EXT:
        raise HTTPException(400, "이미지 파일만 올릴 수 있습니다")
    safe_sub = "".join(c for c in subdir if c.isalnum() or c in "-_") or "images"
    rel = f"{safe_sub}/{uuid.uuid4().hex[:12]}{ext}"
    dest = settings.files_dir / f"project_{project.id}" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(400, "20MB 이하 파일만 올릴 수 있습니다")
    dest.write_bytes(data)
    return {"path": rel, "url": f"/api/projects/{project.id}/files/{rel}"}


@router.get("/projects/{project_id}/files/{path:path}")
def get_file(path: str, project: models.Project = Depends(get_project_for_member)):
    root = (settings.files_dir / f"project_{project.id}").resolve()
    target = (root / path).resolve()
    if root not in target.parents:
        raise HTTPException(400, "잘못된 경로입니다")
    if not target.is_file():
        raise HTTPException(404, "파일이 없습니다")
    return FileResponse(target)

