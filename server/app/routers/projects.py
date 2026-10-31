from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..agent.hub import hub
from ..database import get_db
from ..deps import get_current_user, get_or_404, get_project_for_member, member_role, require_master
from ..ws import manager

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _to_response(p: models.Project, user: models.User, db: Session) -> dict:
    members = [{"user_id": m.user_id, "display_name": m.user.display_name, "username": m.user.username,
                "role": m.role} for m in p.members]
    last = db.query(models.Flow).filter_by(project_id=p.id).order_by(models.Flow.id.desc()).first()
    fav = db.query(models.ProjectFavorite).filter_by(user_id=user.id, project_id=p.id).first() is not None
    return {**schemas.ProjectBase.model_validate(p).model_dump(), "members": members, "is_favorite": fav,
            "user_role": member_role(p.id, user, db) or ("master" if user.is_admin else None),
            "flow_status": last.status if last else None, "flow_title": last.title if last else None}


def _last_work(db: Session, ids: list[int]) -> dict[int, datetime]:
    """프로젝트별 마지막 작업 시각. 프로젝트 행은 이름을 바꿀 때만 갱신되므로, 하루 종일 일한 프로젝트도 행의 시각만 보면 아래에 머문다.
    대화·작업 흐름·결과·스터디를 함께 본다."""
    F, A, S, C, M = models.Flow, models.Artifact, models.Study, models.ChatSession, models.ChatMessage
    sources = (
        db.query(F.project_id, func.max(F.updated_at)).filter(F.project_id.in_(ids)).group_by(F.project_id),
        db.query(A.project_id, func.max(func.coalesce(A.updated_at, A.created_at))).filter(A.project_id.in_(ids)).group_by(A.project_id),
        db.query(S.project_id, func.max(S.updated_at)).filter(S.project_id.in_(ids)).group_by(S.project_id),
        db.query(C.project_id, func.max(M.created_at)).join(M, M.session_id == C.id).filter(C.project_id.in_(ids)).group_by(C.project_id),
    )
    last: dict[int, datetime] = {}
    for source in sources:
        for project_id, at in source:
            if at and at > last.get(project_id, datetime.min):
                last[project_id] = at
    return last


@router.get("", response_model=list[schemas.ProjectResponse])
def list_projects(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """최근 작업 순. updated_at에도 같은 시각을 담아 화면에 보이는 시각과 순서가 어긋나지 않게 한다."""
    q = db.query(models.Project)
    if not user.is_admin:
        q = q.join(models.ProjectMember).filter(models.ProjectMember.user_id == user.id)
    projects = q.all()
    last = _last_work(db, [p.id for p in projects])
    rows = [{**_to_response(p, user, db), "updated_at": max(p.updated_at, last.get(p.id, p.updated_at))} for p in projects]
    return sorted(rows, key=lambda r: (r["updated_at"], r["id"]), reverse=True)


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
    for chat_id, in db.query(models.ChatSession.id).filter_by(project_id=p.id):
        hub.forget_chat(chat_id)
    db.delete(p)
    db.commit()
    return {"ok": True}


# ---------- 즐겨찾기 ----------
@router.put("/{project_id}/favorite")
def add_favorite(project: models.Project = Depends(get_project_for_member), user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    if not db.query(models.ProjectFavorite).filter_by(user_id=user.id, project_id=project.id).first():
        db.add(models.ProjectFavorite(user_id=user.id, project_id=project.id))
        db.commit()
    return {"ok": True}


@router.delete("/{project_id}/favorite")
def remove_favorite(project_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.query(models.ProjectFavorite).filter_by(user_id=user.id, project_id=project_id).delete()
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
