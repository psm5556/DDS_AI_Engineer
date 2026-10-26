"""인증·권한 공통 의존성.
- 브라우저: JWT (Authorization: Bearer <jwt>)
- PC Agent / MCP 서버: API 토큰 (Authorization: Bearer dds_<...>)
사내 SSO/AD로 바꿀 때는 이 파일의 토큰 검증 부분만 교체한다."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import get_db

API_TOKEN_PREFIX = "dds_"
_bearer = HTTPBearer(auto_error=False)


# ---------- PIN / 토큰 ----------
PIN_RE = re.compile(r"^\d{6}$")
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 10


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 240_000).hex()
    return f"pbkdf2_sha256$240000${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iters)).hex()
    return hmac.compare_digest(calc, digest)


def create_access_token(user: models.User) -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=settings.access_token_hours)
    return jwt.encode({"sub": str(user.id), "exp": exp}, settings.secret_key, algorithm="HS256")


def new_api_token() -> tuple[str, str]:
    raw = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_api_token(raw)


def hash_api_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def user_from_token(token: str | None, db: Session) -> models.User | None:
    if not token:
        return None
    if token.startswith(API_TOKEN_PREFIX):
        t = db.query(models.ApiToken).filter_by(token_hash=hash_api_token(token)).first()
        if not t:
            return None
        t.last_used_at = datetime.now()
        db.commit()
        user = db.get(models.User, t.user_id)
    else:
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None
        user = db.get(models.User, int(payload["sub"]))
    return user if user and user.is_active else None


# ---------- 의존성 ----------
def get_current_user(cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
                     db: Session = Depends(get_db)) -> models.User:
    user = user_from_token(cred.credentials if cred else None, db)
    if not user:
        raise HTTPException(401, "로그인이 필요합니다")
    return user


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if not user.is_admin:
        raise HTTPException(403, "관리자만 할 수 있습니다")
    return user


def member_role(project_id: int, user: models.User, db: Session) -> str | None:
    m = db.query(models.ProjectMember).filter_by(project_id=project_id, user_id=user.id).first()
    return m.role if m else None


def require_member(project_id: int, user: models.User, db: Session) -> str:
    role = member_role(project_id, user, db)
    if role is None and not user.is_admin:
        raise HTTPException(403, "이 프로젝트의 멤버가 아닙니다")
    return role or "master"


def require_master(project_id: int, user: models.User, db: Session) -> None:
    if member_role(project_id, user, db) != "master" and not user.is_admin:
        raise HTTPException(403, "프로젝트 관리자(master)만 할 수 있습니다")


def get_or_404(db: Session, model, obj_id: int, label: str):
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(404, f"{label}을(를) 찾을 수 없습니다")
    return obj


def get_project_for_member(project_id: int, user: models.User = Depends(get_current_user),
                           db: Session = Depends(get_db)) -> models.Project:
    p = get_or_404(db, models.Project, project_id, "프로젝트")
    require_member(project_id, user, db)
    return p
