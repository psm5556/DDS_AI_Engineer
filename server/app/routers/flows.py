"""Flow(작업 흐름 그래프)와 아티팩트. 에이전트는 dds-flow MCP 툴로, 사용자는 UI로 접근한다."""
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, get_or_404, get_project_for_member, require_member
from .. import report
from ..study.service import publish_artifact
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
def list_flows(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    return db.query(models.Flow).filter_by(project_id=project.id).order_by(models.Flow.id.desc()).all()


@router.post("/projects/{project_id}/flows", response_model=schemas.FlowResponse)
def create_flow(body: schemas.FlowCreate, project: models.Project = Depends(get_project_for_member),
                db: Session = Depends(get_db)):
    ids = [n.id for n in body.nodes]
    if len(set(ids)) != len(ids):
        raise HTTPException(400, "노드 id가 중복됩니다")
    for e in body.edges:
        if e.source not in ids or e.target not in ids:
            raise HTTPException(400, f"엣지가 없는 노드를 가리킵니다: {e.source} → {e.target}")
    f = models.Flow(project_id=project.id, title=body.title, goal=body.goal,
                    nodes=[n.model_dump() for n in body.nodes], edges=[e.model_dump() for e in body.edges])
    db.add(f)
    db.commit()
    _changed(f)
    return f


@router.get("/flows/{flow_id}", response_model=schemas.FlowResponse)
def get_flow(flow_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _flow_for_member(flow_id, user, db)


@router.put("/flows/{flow_id}/status", response_model=schemas.FlowResponse)
def set_flow_status(flow_id: int, body: schemas.FlowStatusUpdate, user=Depends(get_current_user),
                    db: Session = Depends(get_db)):
    f = _flow_for_member(flow_id, user, db)
    if f.version != body.version:
        raise HTTPException(409, "다른 사용자가 수정했습니다. 새로고침 후 다시 시도하세요.")
    f.status = body.status
    f.version += 1
    db.commit()
    _changed(f)
    return f


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
    if f.status in ("approved", "in_progress", "review"):
        if states <= {"complete", "skipped"}:
            f.status = "complete"
        elif "failed" in states:
            f.status = "failed"
        elif f.status != "review":
            f.status = "in_progress"
    f.version += 1
    db.commit()
    _changed(f)
    return f


# ---------- 아티팩트 ----------
@router.get("/projects/{project_id}/artifacts", response_model=list[schemas.ArtifactResponse])
def list_artifacts(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    return db.query(models.Artifact).filter_by(project_id=project.id).order_by(models.Artifact.id.desc()).all()


@router.post("/projects/{project_id}/artifacts", response_model=schemas.ArtifactResponse)
def create_artifact(body: schemas.ArtifactCreate, project: models.Project = Depends(get_project_for_member),
                    db: Session = Depends(get_db)):
    return publish_artifact(db, project.id, body.kind, body.title, body.payload, body.flow_id, body.node_id)


@router.delete("/artifacts/{artifact_id}")
def delete_artifact(artifact_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    a = get_or_404(db, models.Artifact, artifact_id, "아티팩트")
    require_member(a.project_id, user, db)
    db.delete(a)
    db.commit()
    manager.publish({"type": "artifact_updated", "project_id": a.project_id, "data": {"id": a.id, "deleted": True}})
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


@router.get("/projects/{project_id}/report")
def project_report(format: str = "html", flow_id: int | None = None, project: models.Project = Depends(get_project_for_member),
                   db: Session = Depends(get_db)):
    """결과(요약·표·그래프·이미지)를 모은 보고서. format: html | docx. flow_id를 주면 그 작업 흐름의 결과만."""
    q = db.query(models.Artifact).filter_by(project_id=project.id)
    flow = None
    if flow_id:
        flow = get_or_404(db, models.Flow, flow_id, "플로우")
        q = q.filter_by(flow_id=flow_id)
    else:
        flow = db.query(models.Flow).filter_by(project_id=project.id).order_by(models.Flow.id.desc()).first()
    arts = q.all()
    name = f"{project.name}_보고서_{date.today():%Y%m%d}"
    if format == "docx":
        data = report.build_docx(project, arts, flow)
        return Response(data, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.docx"})
    return HTMLResponse(report.build_html(project, arts, flow),
                        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.html"})
