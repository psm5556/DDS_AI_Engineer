"""마켓 옵션: 관리자가 항목을 관리하고 사용자는 켜고 끈다. 화면과 구조만 있다.
켠 값은 계정에 저장만 하고 AI에게 전달하지 않는다. 전달 방식은 사내 개발에서 정한다(docs/market-package-spec.md 맨 위).
쓰기는 관리자 웹 로그인만 되고, 공개·내리기·삭제는 PIN을 다시 받는다. 켜고 끄기는 사람만 한다(토큰은 403)."""
from __future__ import annotations

import functools
import re
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import market_check, market_service, models, schemas
from ..config import ROOT
from ..database import get_db
from ..deps import get_current_user, require_admin, require_jwt, require_reauth_pin

router = APIRouter(prefix="/api/market", tags=["market-options"])

MAX_NAME, MAX_DESCRIPTION, MAX_URL = 100, 200, 2000
STATUS_LABEL = {"draft": "초안", "published": "공개", "lowered": "내림"}
# 앱이 이것 없이는 돌지 않아 끌 수 없다. agent/dds_agent/main.py의 MCP_SERVERS와 같아야 한다(시험이 본다)
BUILTIN_MCP = (("dds_flow", "Task Flow와 결과 기록", "Task Flow를 만들고 결과와 보고서를 Project에 올립니다."),
               ("dds_cae", "CAD·CAE 실행", "스크립트와 툴을 내 PC에서 실행하고 결과를 모읍니다."),
               ("dds_study", "실험 설계·학습·최적화", "실험 계획(DOE), 예측 모델 학습, 최적화, Simulator 배포를 맡습니다."),
               ("dds_live", "실행 중인 툴과 연결", "열려 있는 CAD·CAE 화면을 바로 다룹니다."))


def _option(option_id: int, db: Session) -> models.MarketOption:
    row = db.get(models.MarketOption, option_id)
    if not row:
        raise HTTPException(404, "옵션을 찾을 수 없습니다 → 화면을 새로 고쳐 주세요")
    return row


def _clean(body: schemas.MarketOptionBody, db: Session, skip: int | None = None) -> tuple[str, str, str]:
    """이름과 설명은 카드에 보이고 나중에 AI 안내로 갈 수 있다. 줄바꿈과 보이지 않는 글자를 남기지 않는다."""
    name, description = market_check.clean_label(body.name)[:MAX_NAME], market_check.clean_label(body.description)[:MAX_DESCRIPTION]
    url = body.service_url.strip()
    if not name:
        raise HTTPException(400, "이름이 비어 있습니다 → 카드에 보일 이름을 적어 주세요")
    problem = _url_problem(url)
    if problem:
        raise HTTPException(400, problem)
    same = db.query(models.MarketOption).filter(func.lower(models.MarketOption.name) == name.lower()).first()
    if same and same.id != skip:
        raise HTTPException(409, f"같은 이름 '{name}'의 옵션이 있습니다 → 다른 이름을 쓰세요")
    return name, description, url


def _url_problem(url: str) -> str | None:
    """비어 있어도 된다: 서비스 방식을 정하기 전에는 항목만 먼저 만들 수 있다. 값이 있으면 주소 모양만 본다. 연결하지 않는다."""
    if not url:
        return None
    if len(url) > MAX_URL or re.search(r"[\s\x00-\x1f]", url):
        return "주소를 읽지 못했습니다 → http:// 또는 https://로 시작하는 주소를 빈칸 없이 넣어 주세요"
    try:
        part = urlsplit(url)
    except ValueError:
        return "주소를 읽지 못했습니다 → http://서버:포트/경로 형식으로 넣어 주세요"
    if part.scheme not in ("http", "https") or not part.hostname:
        return "http 또는 https 주소만 받습니다 → http://서버:포트/경로 형식으로 넣어 주세요"
    if part.username or part.password:
        return "주소 안에 아이디나 비밀번호를 넣을 수 없습니다 → 빼고 넣어 주세요"
    return None


# ---------- 사용자 ----------
@functools.lru_cache(maxsize=1)
def _base_tools() -> tuple[dict, ...]:
    """내장 CAD·CAE 도구. 숫자를 박아 넣지 않고 폴더에서 읽는다."""
    rows = []
    for name in sorted(market_service.BASE_SKILLS):
        try:
            head = (ROOT / "agent-context" / "skills" / name / "SKILL.md").read_bytes()[:4096].decode("utf-8", "replace")
        except OSError:
            head = ""
        m = re.search(r"^description:\s*(.+)$", head, re.M)
        rows.append({"id": name, "name": name, "description": market_check.summary_of(m.group(1).strip().strip("\"'")) if m else ""})
    return tuple(rows)


@router.get("/builtin", response_model=schemas.MarketBuiltin)
def builtin(user=Depends(get_current_user)):
    return {"mcp": [{"id": i, "name": n, "description": d} for i, n, d in BUILTIN_MCP], "tools": list(_base_tools())}


@router.get("/options", response_model=schemas.MarketOptionList)
def list_options(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """공개 중인 옵션과 이 사람이 켠 값. 서비스 주소는 보내지 않는다."""
    on = {c.option_id for c in db.query(models.MarketOptionChoice).filter_by(user_id=user.id, on=True)}
    rows = db.query(models.MarketOption).filter_by(status="published").order_by(func.lower(models.MarketOption.name)).all()
    return {"enabled": bool(market_service.get_setting(db).enabled), "items": [{"id": r.id, "name": r.name, "description": r.description, "on": r.id in on} for r in rows]}


@router.put("/options/{option_id}/choice", response_model=schemas.MarketOptionView)
def set_choice(option_id: int, body: schemas.MarketOptionChoiceBody, user=Depends(require_jwt), db: Session = Depends(get_db)):
    """켜기·끄기. 끄는 것은 언제든 된다. 내려갔거나 마켓이 꺼졌으면 켤 수 없다."""
    opt = _option(option_id, db)
    if body.on and opt.status != "published":
        raise HTTPException(409, "공개 중인 옵션이 아닙니다 → 관리자가 내렸을 수 있습니다. 화면을 새로 고쳐 주세요")
    if body.on and not market_service.get_setting(db).enabled:
        raise HTTPException(409, "관리자가 마켓을 잠시 껐습니다 → 다시 켜지면 켤 수 있습니다")
    row = db.query(models.MarketOptionChoice).filter_by(user_id=user.id, option_id=opt.id).first()
    if row:
        row.on = body.on
    else:
        db.add(models.MarketOptionChoice(user_id=user.id, option_id=opt.id, on=body.on))
    market_service.event(db, user, "option_on" if body.on else "option_off", option_id=opt.id, name=opt.name)
    db.commit()
    return {"id": opt.id, "name": opt.name, "description": opt.description, "on": body.on}


# ---------- 관리자 ----------
def _admin_row(opt: models.MarketOption, counts: dict[int, int], db: Session) -> dict:
    who = db.get(models.User, opt.created_by) if opt.created_by else None
    return {"id": opt.id, "name": opt.name, "description": opt.description, "service_url": opt.service_url, "status": opt.status, "status_label": STATUS_LABEL[opt.status],
            "on_count": counts.get(opt.id, 0), "updated_at": opt.updated_at, "created_by": who.display_name if who else None}


def _counts(db: Session) -> dict[int, int]:
    rows = db.query(models.MarketOptionChoice.option_id, func.count()).filter(models.MarketOptionChoice.on.is_(True)).group_by(models.MarketOptionChoice.option_id).all()
    return {oid: n for oid, n in rows}


def _on_users(opt: models.MarketOption, db: Session) -> list[models.MarketOptionChoice]:
    return db.query(models.MarketOptionChoice).filter_by(option_id=opt.id, on=True).all()


def _tell(db: Session, users, kind: str, text: str, skip: int) -> None:
    for uid in users:
        if uid != skip:
            db.add(models.MarketNotice(user_id=uid, package_id=None, kind=kind, text=text[:2000]))


@router.get("/admin/options", response_model=schemas.MarketOptionAdminList)
def admin_options(admin=Depends(require_admin), db: Session = Depends(get_db)):
    """옵션 표. 이전 방식으로 올린 스킬 항목은 화면이 없으므로 수만 알린다. 설치한 PC는 그대로 쓴다."""
    counts = _counts(db)
    rows = db.query(models.MarketOption).order_by(models.MarketOption.id.desc()).all()
    cfg = market_service.get_setting(db)
    return {"items": [_admin_row(o, counts, db) for o in rows], "settings": {"enabled": bool(cfg.enabled), "gateway_on": bool(cfg.gateway_on)},
            "legacy_count": db.query(models.MarketPackage).count()}


@router.post("/admin/options", response_model=schemas.MarketOptionAdminRow)
def add_option(body: schemas.MarketOptionBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    """추가는 언제나 초안이다. 공개할 때 PIN을 받는다."""
    name, description, url = _clean(body, db)
    opt = models.MarketOption(name=name, description=description, service_url=url, status="draft", created_by=admin.id)
    db.add(opt)
    db.flush()
    market_service.event(db, admin, "option_add", option_id=opt.id, name=name)  # 주소에는 비밀이 있을 수 있어 기록에 넣지 않는다
    db.commit()
    return _admin_row(opt, _counts(db), db)


@router.patch("/admin/options/{option_id}", response_model=schemas.MarketOptionAdminRow)
def edit_option(option_id: int, body: schemas.MarketOptionBody, admin=Depends(require_admin), db: Session = Depends(get_db)):
    opt = _option(option_id, db)
    opt.name, opt.description, opt.service_url = _clean(body, db, skip=opt.id)
    market_service.event(db, admin, "option_edit", option_id=opt.id, name=opt.name)
    db.commit()
    return _admin_row(opt, _counts(db), db)


@router.post("/admin/options/{option_id}/publish", response_model=schemas.MarketOptionAdminRow)
def publish_option(option_id: int, body: schemas.MarketPinNote, admin=Depends(require_admin), db: Session = Depends(get_db)):
    """공개. 내렸던 항목도 다시 공개할 수 있다. 켰던 사람의 값은 저절로 켜지지 않는다."""
    opt = _option(option_id, db)
    require_reauth_pin(admin, body.pin, db)
    if opt.status == "published":
        raise HTTPException(409, "이미 공개 중입니다 → 화면을 새로 고쳐 주세요")
    opt.status = "published"
    market_service.event(db, admin, "option_publish", option_id=opt.id, name=opt.name)
    db.commit()
    market_service.publish_updated(market_service.admins(db))
    return _admin_row(opt, _counts(db), db)


@router.post("/admin/options/{option_id}/lower", response_model=schemas.MarketOptionAdminRow)
def lower_option(option_id: int, body: schemas.MarketPinNote, admin=Depends(require_admin), db: Session = Depends(get_db)):
    """내리기: 목록에서 사라지고 켠 사람의 값을 끄며 이유를 한 번 알린다."""
    opt = _option(option_id, db)
    require_reauth_pin(admin, body.pin, db)
    if opt.status != "published":
        raise HTTPException(409, "공개 중인 옵션만 내릴 수 있습니다 → 화면을 새로 고쳐 주세요")
    note = market_check.clean_label(body.note)[:500]
    opt.status = "lowered"
    rows = _on_users(opt, db)
    users = [c.user_id for c in rows]
    for c in rows:
        c.on = False
    _tell(db, users, "deprecated", f"관리자가 옵션 '{opt.name}'을(를) 내려 꺼졌습니다." + (f" 사유: {note}" if note else "") + " → 필요하면 관리자에게 문의해 주세요.", admin.id)
    market_service.event(db, admin, "option_lower", option_id=opt.id, name=opt.name)
    db.commit()
    market_service.publish_updated(users)
    return _admin_row(opt, _counts(db), db)


@router.delete("/admin/options/{option_id}")
def delete_option(option_id: int, body: schemas.MarketPinNote, admin=Depends(require_admin), db: Session = Depends(get_db)):
    """삭제. 켠 사람에게 사라진 이유를 한 번 알린다. 기록(MarketEvent)은 남는다."""
    opt = _option(option_id, db)
    require_reauth_pin(admin, body.pin, db)
    users = [c.user_id for c in _on_users(opt, db)]
    _tell(db, users, "deprecated", f"관리자가 옵션 '{opt.name}'을(를) 없애 꺼졌습니다. → 필요하면 관리자에게 문의해 주세요.", admin.id)
    market_service.event(db, admin, "option_delete", option_id=opt.id, name=opt.name)
    db.query(models.MarketOptionChoice).filter_by(option_id=opt.id).delete()
    db.delete(opt)
    db.commit()
    market_service.publish_updated(users)
    return {"ok": True}
