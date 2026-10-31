from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import (PIN_RE, SCOPE_AGENT, check_pin_with_lock, create_access_token, effective_admin, get_current_user, hash_password,
                    new_api_token, require_admin, require_jwt)

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    pin: str


class RegisterRequest(LoginRequest):
    display_name: str


def _check_pin(pin: str):
    if not PIN_RE.match(pin or ""):
        raise HTTPException(400, "PIN은 숫자 6자리여야 합니다")


@router.post("/auth/register", response_model=schemas.UserResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    _check_pin(body.pin)
    if not body.username.strip():
        raise HTTPException(400, "아이디를 입력하세요")
    if db.query(models.User).filter_by(username=body.username).first():
        raise HTTPException(409, "이미 사용 중인 아이디입니다")
    first = db.query(models.User).count() == 0
    u = models.User(username=body.username.strip(), display_name=body.display_name.strip() or body.username,
                    pin_hash=hash_password(body.pin), is_admin=first, is_active=first)
    db.add(u)
    db.commit()
    return u


@router.post("/auth/login", response_model=schemas.LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    """PIN 6자리 로그인. 연속 5회 틀리면 10분 잠금(무작위 대입 방지)."""
    u = db.query(models.User).filter_by(username=body.username).first()
    if not u or not check_pin_with_lock(u, body.pin, db):
        raise HTTPException(401, "아이디 또는 PIN이 올바르지 않습니다")
    if not u.is_active:
        raise HTTPException(403, "관리자 승인을 기다리고 있습니다")
    return {"access_token": create_access_token(u), "user": u}


@router.put("/auth/pin")
def change_pin(body: schemas.PinChange, user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    # 웹 로그인 전용이고 로그인과 같은 잠금을 쓴다. 맞히면 그 PIN으로 관리자 로그인까지 되므로 대입 통로가 되면 안 된다
    if not check_pin_with_lock(user, body.current_pin, db):
        raise HTTPException(400, "현재 PIN이 올바르지 않습니다 → 여러 번 틀리면 잠깁니다. 잊었다면 관리자에게 초기화를 요청하세요")
    _check_pin(body.new_pin)
    user.pin_hash = hash_password(body.new_pin)
    db.commit()
    return {"ok": True}


@router.get("/auth/me", response_model=schemas.UserResponse)
def me(user: models.User = Depends(get_current_user)):
    return user


# ---------- API 토큰 (PC Agent용) ----------
@router.get("/tokens", response_model=list[schemas.ApiTokenResponse])
def list_tokens(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(models.ApiToken).filter_by(user_id=user.id).order_by(models.ApiToken.id.desc()).all()


@router.post("/tokens", response_model=schemas.ApiTokenCreated)
def create_token(body: schemas.ApiTokenCreate, user: models.User = Depends(require_jwt),
                 db: Session = Depends(get_db)):
    # 토큰이 토큰을 만들지 못하게 한다. 그러지 않으면 새 나간 토큰을 폐기해도 다른 토큰이 남는다
    raw, hashed = new_api_token()
    t = models.ApiToken(user_id=user.id, name=body.name, token_hash=hashed, scope=SCOPE_AGENT)
    db.add(t)
    db.commit()
    return {"id": t.id, "name": t.name, "scope": t.scope, "created_at": t.created_at, "last_used_at": None, "token": raw}


@router.delete("/tokens/{token_id}")
def delete_token(token_id: int, user: models.User = Depends(require_jwt), db: Session = Depends(get_db)):
    t = db.query(models.ApiToken).filter_by(id=token_id, user_id=user.id).first()
    if not t:
        raise HTTPException(404, "토큰을 찾을 수 없습니다")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ---------- 사용자 (멤버 추가용 목록 / 관리자 승인) ----------
@router.get("/users", response_model=list[schemas.UserResponse])
def list_users(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(models.User)
    if not effective_admin(user):
        q = q.filter_by(is_active=True)
    return q.order_by(models.User.display_name).all()


@router.put("/users/{user_id}", response_model=schemas.UserResponse)
def update_user(user_id: int, body: schemas.UserAdminUpdate, admin: models.User = Depends(require_admin),
                db: Session = Depends(get_db)):
    u = db.get(models.User, user_id)
    if not u:
        raise HTTPException(404, "사용자를 찾을 수 없습니다")
    if u.id == admin.id and (body.is_admin is False or body.is_active is False):
        raise HTTPException(400, "자기 자신의 관리자 권한/활성 상태는 바꿀 수 없습니다")
    for k in ("is_active", "is_admin", "display_name"):
        if getattr(body, k) is not None:
            setattr(u, k, getattr(body, k))
    if body.pin is not None:
        _check_pin(body.pin)
        u.pin_hash, u.failed_logins, u.locked_until = hash_password(body.pin), 0, None
        u.reauth_failed, u.reauth_locked_until = 0, None
    db.commit()
    return u


@router.get("/config/public")
def public_config(user: models.User = Depends(get_current_user)):
    """웹앱이 쓰는 서버 설정(비밀 아닌 것만)."""
    from ..config import settings
    return {"web_viewer_url": settings.web_viewer_url, "max_upload_mb": settings.max_upload_mb}
