from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import get_current_user, get_or_404, get_project_for_member, member_role, require_master
from ..ws import manager

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _to_response(p: models.Project, user: models.User, db: Session) -> dict:
    members = [{"user_id": m.user_id, "display_name": m.user.display_name, "username": m.user.username,
                "role": m.role} for m in p.members]
    last = db.query(models.Flow).filter_by(project_id=p.id).order_by(models.Flow.id.desc()).first()
    return {**schemas.ProjectBase.model_validate(p).model_dump(), "members": members,
            "user_role": member_role(p.id, user, db) or ("master" if user.is_admin else None),
            "flow_status": last.status if last else None, "flow_title": last.title if last else None}


@router.get("", response_model=list[schemas.ProjectResponse])
def list_projects(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(models.Project)
    if not user.is_admin:
        q = q.join(models.ProjectMember).filter(models.ProjectMember.user_id == user.id)
    return [_to_response(p, user, db) for p in q.order_by(models.Project.updated_at.desc()).all()]


@router.post("", response_model=schemas.ProjectResponse)
async def create_project(body: schemas.ProjectCreate, user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    p = models.Project(name=body.name, description=body.description, owner_id=user.id)
    db.add(p)
    db.flush()
    db.add(models.ProjectMember(project_id=p.id, user_id=user.id, role="master"))
    db.commit()
    await manager.broadcast({"type": "project_updated", "project_id": p.id, "data": {"id": p.id}})
    return _to_response(p, user, db)


@router.get("/{project_id}", response_model=schemas.ProjectResponse)
def get_project(project: models.Project = Depends(get_project_for_member),
                user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _to_response(project, user, db)


@router.put("/{project_id}", response_model=schemas.ProjectResponse)
async def update_project(project_id: int, body: schemas.ProjectUpdate, user: models.User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    p = get_or_404(db, models.Project, project_id, "프로젝트")
    require_master(project_id, user, db)
    if p.version != body.version:
        raise HTTPException(409, "다른 사용자가 수정했습니다. 새로고침 후 다시 시도하세요.")
    for k in ("name", "description"):
        if getattr(body, k) is not None:
            setattr(p, k, getattr(body, k))
    p.version += 1
    db.commit()
    await manager.broadcast({"type": "project_updated", "project_id": p.id, "data": {"id": p.id}})
    return _to_response(p, user, db)


@router.delete("/{project_id}")
async def delete_project(project_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = get_or_404(db, models.Project, project_id, "프로젝트")
    require_master(project_id, user, db)
    await manager.broadcast({"type": "project_updated", "project_id": p.id, "data": {"id": p.id, "deleted": True}})
    db.delete(p)
    db.commit()
    return {"ok": True}


# ---------- 멤버(공유) ----------
@router.put("/{project_id}/members/{user_id}", response_model=schemas.ProjectResponse)
async def upsert_member(project_id: int, user_id: int, body: schemas.MemberUpsert,
                        user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    p = get_or_404(db, models.Project, project_id, "프로젝트")
    require_master(project_id, user, db)
    get_or_404(db, models.User, user_id, "사용자")
    m = db.query(models.ProjectMember).filter_by(project_id=project_id, user_id=user_id).first()
    if m:
        m.role = body.role
    else:
        db.add(models.ProjectMember(project_id=project_id, user_id=user_id, role=body.role))
    db.commit()
    db.refresh(p)
    await manager.broadcast({"type": "project_updated", "project_id": p.id, "data": {"id": p.id}})
    return _to_response(p, user, db)


@router.delete("/{project_id}/members/{user_id}", response_model=schemas.ProjectResponse)
async def remove_member(project_id: int, user_id: int, user: models.User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    p = get_or_404(db, models.Project, project_id, "프로젝트")
    require_master(project_id, user, db)
    masters = db.query(models.ProjectMember).filter_by(project_id=project_id, role="master").count()
    m = db.query(models.ProjectMember).filter_by(project_id=project_id, user_id=user_id).first()
    if not m:
        raise HTTPException(404, "멤버가 아닙니다")
    if m.role == "master" and masters <= 1:
        raise HTTPException(400, "마지막 관리자(master)는 제거할 수 없습니다")
    db.delete(m)
    db.commit()
    db.refresh(p)
    await manager.broadcast({"type": "project_updated", "project_id": p.id, "data": {"id": p.id}})
    return _to_response(p, user, db)
