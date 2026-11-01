"""인증·권한 공통 의존성.
- 브라우저: JWT (Authorization: Bearer <jwt>)
- PC Agent / MCP 서버: API 토큰 (Authorization: Bearer dds_<...>)
토큰은 사용자 PC에서 AI와 스크립트 곁에 있다. 그래서 토큰에는 관리자 권한을 주지 않는다. 관리자 권한을 보는 곳은 effective_admin을 쓴다.
사내 SSO/AD로 바꿀 때는 이 파일의 토큰 검증 부분만 교체한다."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import models
from .config import settings
from .database import get_db

API_TOKEN_PREFIX = "dds_"
AUTH_JWT, SCOPE_AGENT, SCOPE_DEVTEST = "jwt", "agent", "devtest"  # User.auth_via 값
DEVTEST_PREFIX = "/api/devtest/"
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


def check_pin_with_lock(user: models.User, pin: str, db: Session) -> bool:
    """PIN을 다시 확인한다. 로그인과 PIN 변경 등 PIN을 받는 모든 곳이 이 함수로 같은 실패 횟수를 쓴다.
    따로 세면 잠기지 않는 길로 6자리 PIN을 대입할 수 있다. 잠겨 있으면 423, 틀리면 False."""
    now = datetime.now()
    if user.locked_until and user.locked_until > now:
        left = int((user.locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(423, f"PIN을 여러 번 틀려 잠겼습니다. {left}분 뒤 다시 시도하거나 관리자에게 초기화를 요청하세요")
    if not verify_password(pin, user.pin_hash):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.failed_logins, user.locked_until = 0, now + timedelta(minutes=LOCK_MINUTES)
        db.commit()
        return False
    user.failed_logins, user.locked_until = 0, None
    db.commit()
    return True


def check_reauth_pin(user: models.User, pin: str, db: Session) -> bool:
    """이미 로그인한 사람의 PIN 재확인(마켓 승인·회수 등). 공개 로그인과 횟수를 나눈다.
    같이 세면 로그인 없이 틀린 PIN을 다섯 번 보내는 것만으로 관리자의 회수를 막을 수 있다. 이 길은 유효한 로그인이 있어야 하므로 밖에서 잠그지 못하고,
    자기 횟수와 잠금이 따로 있어 PIN 대입 통로도 되지 않는다."""
    now = datetime.now()
    if user.reauth_locked_until and user.reauth_locked_until > now:
        left = int((user.reauth_locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(423, f"PIN을 여러 번 틀려 잠겼습니다. {left}분 뒤 다시 시도하거나 다른 관리자에게 초기화를 요청하세요")
    if not verify_password(pin, user.pin_hash):
        user.reauth_failed = (user.reauth_failed or 0) + 1
        if user.reauth_failed >= MAX_FAILED_LOGINS:
            user.reauth_failed, user.reauth_locked_until = 0, now + timedelta(minutes=LOCK_MINUTES)
        db.commit()
        return False
    user.reauth_failed, user.reauth_locked_until = 0, None
    db.commit()
    return True


def require_reauth_pin(user: models.User, pin: str, db: Session) -> None:
    """위험한 결정 앞에서 PIN을 다시 받는다. 틀리면 403, 잠겼으면 423."""
    if not check_reauth_pin(db.get(models.User, user.id), pin, db):
        raise HTTPException(403, "PIN이 맞지 않습니다 → 다시 입력하세요. 5번 틀리면 잠깁니다")


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
        via = SCOPE_DEVTEST if t.scope == SCOPE_DEVTEST else SCOPE_AGENT  # 모르는 값은 가장 약한 쪽으로 본다
    else:
        try:
            payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        except jwt.PyJWTError:
            return None
        user = db.get(models.User, int(payload["sub"]))
        via = AUTH_JWT
    if not user or not user.is_active:
        return None
    user.auth_via = via
    return user


def effective_admin(user: models.User) -> bool:
    """이 요청의 호출자가 관리자 권한을 쓸 수 있는지. 웹 로그인만 된다.
    대상 계정이 관리자인지 보는 곳(settings_message, devtest._allowed)은 호출자 이야기가 아니므로 이 함수를 쓰지 않는다."""
    return bool(user.is_admin and user.auth_via == AUTH_JWT)


# ---------- 의존성 ----------
def get_current_user(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(_bearer),
                     db: Session = Depends(get_db)) -> models.User:
    user = user_from_token(cred.credentials if cred else None, db)
    if not user:
        raise HTTPException(401, "로그인이 필요합니다")
    if user.auth_via == SCOPE_DEVTEST and not request.url.path.startswith(DEVTEST_PREFIX):
        raise HTTPException(403, "원격 검증 토큰은 원격 검증 API(/api/devtest)만 쓸 수 있습니다 → 다른 일은 웹앱에 로그인해서 하세요")
    return user


def require_jwt(user: models.User = Depends(get_current_user)) -> models.User:
    """사람이 웹에 로그인했을 때만. 승인·허용, 공유, 멤버, 삭제, 토큰, PIN, 마켓 등록처럼 AI나 PC의 코드가 스스로 하면 안 되는 일에 쓴다.
    토큰은 AI 곁에 있다. AI가 스스로 승인할 수 있으면 다른 모든 관문이 뜻을 잃는다."""
    if user.auth_via != AUTH_JWT:
        raise HTTPException(403, "PC Agent·AI 토큰으로는 할 수 없습니다 → 웹앱에 로그인해서 하세요")
    return user


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if not user.is_admin:
        raise HTTPException(403, "관리자만 할 수 있습니다 → 관리자에게 요청하세요")
    if not effective_admin(user):
        raise HTTPException(403, "PC Agent·AI 토큰에는 관리자 권한이 없습니다 → 관리자 계정으로 웹앱에 로그인해서 하세요. 원격 검증이면 python scripts/pc_verify.py token으로 새 토큰을 만드세요")
    return user


def require_devtest_admin(user: models.User = Depends(get_current_user)) -> models.User:
    """원격 시험: 웹 관리자 또는 관리자 계정의 devtest 토큰(서버 파일에 쓸 수 있는 사람만 만든다)."""
    if not user.is_admin:
        raise HTTPException(403, "관리자만 할 수 있습니다 → 관리자에게 요청하세요")
    if user.auth_via not in (AUTH_JWT, SCOPE_DEVTEST):
        raise HTTPException(403, "이 토큰은 원격 검증에 쓸 수 없습니다 → python scripts/pc_verify.py token으로 새 토큰을 만드세요")
    return user


def member_role(project_id: int, user: models.User, db: Session) -> str | None:
    m = db.query(models.ProjectMember).filter_by(project_id=project_id, user_id=user.id).first()
    return m.role if m else None


def require_member(project_id: int, user: models.User, db: Session) -> str:
    role = member_role(project_id, user, db)
    if role is None and not effective_admin(user):
        if user.is_admin:  # 웹에서는 보이는 프로젝트인데 AI만 막히므로 이유를 따로 알린다
            raise HTTPException(403, "이 Project의 멤버가 아닙니다. PC Agent·AI 토큰에는 관리자 권한이 없습니다 → Project 메뉴의 멤버·공유에서 자신을 멤버로 추가하세요")
        raise HTTPException(403, "이 Project의 멤버가 아닙니다 → Project 관리자에게 멤버 추가를 요청하세요")
    return role or "master"


def require_ai_can_work(project_id: int, user: models.User, db: Session) -> None:
    """AI와 PC Agent를 움직이는 요청 앞에 둔다. 그들은 토큰으로 결과를 올리므로 관리자라도 멤버가 아니면 전부 실패한다.
    일을 시작한 뒤 조용히 망가지지 않게 먼저 막는다."""
    if member_role(project_id, user, db) is None:
        raise HTTPException(403, "이 Project의 멤버가 아니라서 AI와 PC Agent가 결과를 올릴 수 없습니다 → Project 메뉴의 멤버·공유에서 자신을 멤버로 추가한 뒤 다시 하세요")


def require_master(project_id: int, user: models.User, db: Session) -> None:
    if member_role(project_id, user, db) != "master" and not effective_admin(user):
        raise HTTPException(403, "Project 관리자(master)만 할 수 있습니다")


def get_or_404(db: Session, model, obj_id: int, label: str):
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(404, f"{label}을(를) 찾을 수 없습니다")
    return obj


def get_project_for_member(project_id: int, user: models.User = Depends(get_current_user),
                           db: Session = Depends(get_db)) -> models.Project:
    p = get_or_404(db, models.Project, project_id, "Project")
    require_member(project_id, user, db)
    return p
