from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import (create_access_token, get_current_user, hash_password, new_api_token, require_admin,
                    verify_password)

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(LoginRequest):
    display_name: str


@router.post("/auth/register", response_model=schemas.UserResponse)
def register(body: RegisterRequest, db: Session = Depends(get_db)):
    if len(body.password) < 8:
        raise HTTPException(400, "비밀번호는 8자 이상이어야 합니다")
    if db.query(models.User).filter_by(username=body.username).first():
        raise HTTPException(409, "이미 사용 중인 아이디입니다")
    first = db.query(models.User).count() == 0
    u = models.User(username=body.username, display_name=body.display_name,
                    password_hash=hash_password(body.password), is_admin=first, is_active=first)
    db.add(u)
    db.commit()
    return u


@router.post("/auth/login", response_model=schemas.LoginResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    u = db.query(models.User).filter_by(username=body.username).first()
    if not u or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다")
    if not u.is_active:
        raise HTTPException(403, "관리자 승인을 기다리고 있습니다")
    return {"access_token": create_access_token(u), "user": u}


@router.get("/auth/me", response_model=schemas.UserResponse)
def me(user: models.User = Depends(get_current_user)):
    return user


# ---------- API 토큰 (PC Agent용) ----------
@router.get("/tokens", response_model=list[schemas.ApiTokenResponse])
def list_tokens(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(models.ApiToken).filter_by(user_id=user.id).order_by(models.ApiToken.id.desc()).all()


@router.post("/tokens", response_model=schemas.ApiTokenCreated)
def create_token(body: schemas.ApiTokenCreate, user: models.User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    raw, hashed = new_api_token()
    t = models.ApiToken(user_id=user.id, name=body.name, token_hash=hashed)
    db.add(t)
    db.commit()
    return {"id": t.id, "name": t.name, "created_at": t.created_at, "last_used_at": None, "token": raw}


@router.delete("/tokens/{token_id}")
def delete_token(token_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
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
    if not user.is_admin:
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
    db.commit()
    return u
