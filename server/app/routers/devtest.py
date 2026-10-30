"""개발 AI의 원격 검증: 서버에서 개발하는 AI가 관리자 PC의 CAD/CAE 연동을 스스로 시험한다(docs/pc-verification.md).
시험할 수 있는 PC는 관리자 계정이 설정에서 직접 허용한 PC뿐이고, 7일이 지나면 저절로 꺼진다. 실행 내역은 모두 남긴다."""
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..agent.link import AgentError, registry
from ..database import get_db
from ..deps import get_current_user, require_admin
from .agents import settings_message

router = APIRouter(prefix="/api/devtest", tags=["devtest"])
ALLOW_DAYS = 7


def _allowed(u: models.User | None) -> bool:
    return bool(u and u.is_admin and u.remote_test_until and u.remote_test_until > datetime.now())


@router.get("/permission", response_model=schemas.DevTestPermission)
def get_permission(user=Depends(get_current_user)):
    return {"allow": _allowed(user), "until": user.remote_test_until if _allowed(user) else None}


@router.put("/permission", response_model=schemas.DevTestPermission)
def set_permission(body: schemas.DevTestAllow, user=Depends(require_admin), db: Session = Depends(get_db)):
    me = db.get(models.User, user.id)
    me.remote_test_until = datetime.now() + timedelta(days=ALLOW_DAYS) if body.allow else None
    db.commit()
    conn = registry.get(me.id)
    if conn:
        conn.send(settings_message(me))
    return {"allow": body.allow, "until": me.remote_test_until}


@router.get("/targets")
def targets(admin=Depends(require_admin), db: Session = Depends(get_db)):
    """지금 시험할 수 있는 PC"""
    out = []
    for u in db.query(models.User).filter(models.User.is_admin.is_(True)).all():
        conn = registry.get(u.id)
        if _allowed(u) and conn:
            out.append({"user_id": u.id, "username": u.username, "hostname": conn.info.get("hostname"),
                        "version": conn.info.get("version"), "until": u.remote_test_until})
    return out


def _pick(user_id: int | None, db: Session) -> tuple[models.User, object]:
    if user_id is not None:
        u = db.get(models.User, user_id)
        if not _allowed(u):
            raise HTTPException(403, "이 PC는 원격 시험을 허용하지 않았습니다 → 그 계정으로 웹앱 설정에서 허용하세요")
        conn = registry.get(user_id)
        if not conn:
            raise HTTPException(409, "PC Agent가 연결되어 있지 않습니다 → 그 PC에서 Agent가 켜져 있는지 확인하세요")
        return u, conn
    found = [u for u in db.query(models.User).filter(models.User.is_admin.is_(True)).all() if _allowed(u) and registry.get(u.id)]
    if not found:
        raise HTTPException(409, "시험할 수 있는 PC가 없습니다 → 관리자 계정으로 PC Agent를 연결하고 웹앱 설정에서 원격 시험을 허용하세요")
    if len(found) > 1:
        raise HTTPException(409, "시험할 수 있는 PC가 여럿입니다 → user_id를 지정하세요: " + ", ".join(f"{u.id}({u.username})" for u in found))
    return found[0], registry.get(found[0].id)


@router.post("/run")
def run(body: schemas.DevTestRun, admin=Depends(require_admin), db: Session = Depends(get_db)):
    target, conn = _pick(body.user_id, db)
    payload = body.model_dump(exclude={"user_id"}, exclude_none=True)
    t0 = datetime.now()
    rec = models.DevTestRecord(by_user_id=admin.id, target_user_id=target.id, op=body.op, tool=body.tool,
                               hostname=conn.info.get("hostname"), created_at=t0)
    try:
        res = conn.request("devtest.run", {**payload, "by": admin.username}, timeout=body.timeout_sec + 60)
    except FutureTimeout:
        rec.ok, rec.note = False, "응답 시간 초과"
        db.add(rec)
        db.commit()
        raise HTTPException(504, f"{body.timeout_sec}초 안에 PC가 답하지 않았습니다 → 시간을 늘리거나 작업을 나누세요")
    except AgentError as e:
        rec.ok, rec.note = False, str(e)[:300]
        db.add(rec)
        db.commit()
        raise HTTPException(502, str(e))
    rec.ok, rec.returncode, rec.seconds = bool(res.get("ok")), res.get("returncode"), res.get("seconds")
    db.add(rec)
    db.commit()
    return res


@router.get("/history")
def history(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """내 PC에서 실행된 시험 내역"""
    rows = db.query(models.DevTestRecord).filter_by(target_user_id=user.id).order_by(models.DevTestRecord.id.desc()).limit(10).all()
    return [{"id": r.id, "op": r.op, "tool": r.tool, "ok": r.ok, "returncode": r.returncode, "seconds": r.seconds,
             "hostname": r.hostname, "note": r.note, "created_at": r.created_at} for r in rows]
