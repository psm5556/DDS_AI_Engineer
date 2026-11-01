"""마켓: 사내 스킬을 올리고, 승인하고, 설치하고, 회수한다. 규격은 docs/market-package-spec.md.

사람이 내릴 결정(설치·공개 범위·승인·회수)은 JWT 전용이다. PC의 AI와 스크립트는 토큰으로 보고 받을 수만 있다.
승인·반려·내림·회수와 마켓 스위치는 PIN을 다시 받는다. 올린 코드는 서버에서 실행하지 않고 디스크에 풀지 않는다.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import json
import logging
import threading
import time
import zipfile
from datetime import datetime
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from .. import market_check, market_service, models, schemas
from ..agent.link import registry
from ..config import ROOT
from ..database import get_db
from ..deps import check_reauth_pin, effective_admin, get_current_user, require_admin, require_jwt
from ..market_check import CAP_LABEL, clean_label
from ..market_service import GRADE_LABEL, PUBLIC_STATUS, STATUS_LABEL, ext_of, fingerprint, is_code, key_of, publish_updated, push_sync, recipients, resolve, versions_of, vkey
from ..study.recipe import MAX_FILE, MAX_FILES, RecipeRejected

router = APIRouter(prefix="/api/market", tags=["market"])
log = logging.getLogger(__name__)
SOURCES = ("web", "cli", "chat", "capture")
MAX_VIEW_BYTES = 512 * 1024
MAX_PREVIEW_IMAGE = 2 * 1024 * 1024
MAX_DIFF_LINES = 2000
MAX_DIFF_INPUT_BYTES = 1024 * 1024  # 비교는 입력 크기에 약하다. 올린 사람이 관리자 화면을 멈추게 할 수 있으면 안 된다
MAX_DIFF_INPUT_LINES = 20000
MAX_EVIDENCE_BYTES = 20 * 1024
MAX_UPLOAD_BODY = market_check.MAX_BUNDLE * 3 + 10 * 1024 * 1024  # main.py가 본문을 받기 전에 본다. 폴더 한도 + multipart 머리글
MAX_PARALLEL_CHECKS = 2
SLOW_CHECK_SEC = 2.0
SPEC_FILE = ROOT / "docs" / "market-package-spec.md"
TEMPLATE_DIR = ROOT / "market-templates"
TEMPLATES = {"skill-text-example": ("지침만 있는 스킬", "SKILL.md 하나로 된 시작 틀입니다. 코드가 없어 설치 때 확인 창이 없습니다."),
             "skill-code-example": ("스크립트가 있는 스킬", "SKILL.md와 scripts/로 된 시작 틀입니다. 설치할 때 내 PC에서 실행된다는 것을 확인합니다.")}

# 검사는 파이썬에서 돌아 다른 요청과 CPU를 나눠 쓴다. 한도 안의 일만 하지만 한 사람이 쉬지 않고 보내 서버를 차지하지 못하게 한다
_check_slots = threading.BoundedSemaphore(MAX_PARALLEL_CHECKS)
_cooldown: dict[int, float] = {}
_cooldown_lock = threading.Lock()


# ---------- 찾기·권한 ----------
def _package(ref: str, user: models.User, db: Session) -> models.MarketPackage:
    """숫자면 번호, 아니면 이름. 이름은 내 것을 먼저 본다: 공개 전 이름은 사람마다 따로 쓸 수 있다."""
    pkg = None
    if ref.isdigit():
        pkg = db.get(models.MarketPackage, int(ref))
    else:
        pkg = db.query(models.MarketPackage).filter_by(owner_id=user.id, name=ref).first() or \
            db.query(models.MarketPackage).filter_by(public_name=ref).first()
    if not pkg or not _can_see_package(pkg, user, db):
        raise HTTPException(404, f"항목 '{ref}'을(를) 찾을 수 없습니다 → 마켓 화면에서 이름을 확인하세요")
    return pkg


def _can_see_package(pkg: models.MarketPackage, user: models.User, db: Session) -> bool:
    if pkg.owner_id == user.id or effective_admin(user):
        return True
    return any(v.status in PUBLIC_STATUS or _holds(v, pkg, user, db) for v in versions_of(db, pkg.id))


def _holds(v: models.MarketVersion, pkg: models.MarketPackage, user: models.User, db: Session) -> bool:
    """회수된 항목을 설치해 둔 사람은 카드에서 회수 사유를 봐야 한다. 파일은 받을 수 없다."""
    return v.status == "withdrawn" and db.query(models.MarketInstall).filter_by(user_id=user.id, package_id=pkg.id).first() is not None


def _version(pkg: models.MarketPackage, v: str, db: Session) -> models.MarketVersion:
    row = db.query(models.MarketVersion).filter_by(package_id=pkg.id, version=v).first()
    if not row:
        raise HTTPException(404, f"버전 {v}이(가) 없습니다 → 항목의 버전 목록을 확인하세요")
    return row


def _can_read_files(v: models.MarketVersion, pkg: models.MarketPackage, user: models.User) -> bool:
    if pkg.owner_id == user.id:
        return v.status != "withdrawn"  # 회수한 코드는 만든 사람도 다시 받아 쓰지 못한다. 내용은 관리자가 본다
    return effective_admin(user) or v.status in PUBLIC_STATUS


def _readable(pkg: models.MarketPackage, v: str, user: models.User, db: Session) -> models.MarketVersion:
    row = _version(pkg, v, db)
    if not _can_read_files(row, pkg, user):
        raise HTTPException(403, "아직 공개되지 않았거나 회수된 버전이라 받을 수 없습니다 → 관리부서의 승인을 기다리거나 다른 버전을 쓰세요")
    return row


def _own(pkg: models.MarketPackage, user: models.User):
    if pkg.owner_id != user.id:
        raise HTTPException(403, "만든 사람만 할 수 있습니다 → 고칠 내용을 만든 사람에게 알려 주세요")


def _own_or_submitted(pkg: models.MarketPackage, v: models.MarketVersion, user: models.User):
    """공식 항목의 새 버전은 다른 관리자도 올린다. 그 사람이 검토를 요청하거나 버릴 수 없으면 소유자가 없을 때 고친 판을 낼 길이 없다."""
    if not (v.submitted_by == user.id and pkg.official and effective_admin(user)):
        _own(pkg, user)


def _check_pin(user: models.User, pin: str, db: Session):
    if not check_reauth_pin(db.get(models.User, user.id), pin, db):
        raise HTTPException(403, "PIN이 맞지 않습니다 → 다시 입력하세요. 5번 틀리면 잠깁니다")


def _require_on(db: Session):
    if not market_service.get_setting(db).enabled:
        raise HTTPException(409, "관리부서가 마켓을 잠시 껐습니다 → 다시 켜지면 설치·등록할 수 있습니다")


def _same_content(v: models.MarketVersion, seen: str | None):
    """사람이 본 내용과 지금 내용이 같은지. 토큰은 초안을 다시 올릴 수 있으므로, 보고 누르는 사이에 바꿔치기하면 다른 내용이 공개된다."""
    if seen != fingerprint(v):
        raise HTTPException(409, "화면을 연 뒤에 내용이 바뀌었습니다 → 항목을 다시 열어 내용을 확인한 뒤 다시 눌러 주세요")


# ---------- 화면에 보낼 값 ----------
def _caps(v: models.MarketVersion | None) -> list[dict]:
    return [{"key": c, "label": CAP_LABEL.get(c, c)} for c in ((v.capabilities or []) if v else [])]


def _version_row(v: models.MarketVersion, db: Session) -> dict:
    who = db.get(models.User, v.submitted_by) if v.submitted_by else None
    reviewer = db.get(models.User, v.reviewed_by) if v.reviewed_by else None
    repl = db.get(models.MarketPackage, v.replacement_id) if v.replacement_id else None
    return {"version": v.version, "status": v.status, "status_label": STATUS_LABEL.get(v.status, v.status), "grade": v.grade,
            "grade_label": GRADE_LABEL.get(v.grade, ""), "size": v.size, "sha256": v.sha256, "fingerprint": fingerprint(v), "notes": v.notes or "", "submitted_by": who.display_name if who else None,
            "reviewed_by": reviewer.display_name if reviewer else None, "reviewed_at": v.reviewed_at, "review_note": v.review_note or "",
            "recall_reason": v.recall_reason, "recall_fallback": v.recall_fallback, "replacement": (repl.public_name or repl.name) if repl else None,
            "evidence": v.evidence, "created_at": v.created_at, "capabilities": _caps(v), "kind": (ext_of(v).get("kinds") or ["skill_text"])[0],
            "file_count": len(v.files or []), "provenance": v.provenance or {}}


def _reported(user_id: int, key: str, db: Session) -> dict | None:
    """이 사람의 PC가 알린 항목 상태. 연결된 PC가 없으면 마지막으로 연결된 PC의 값을 쓴다: Agent를 껐다고 설치한 것이 사라지지는 않는다."""
    conn = registry.get(user_id)
    host = conn.info.get("hostname") if conn else None
    q = db.query(models.AgentInstance).filter_by(user_id=user_id)
    inst = q.filter_by(hostname=host).first() if host else q.order_by(models.AgentInstance.last_seen.desc()).first()
    if not inst or not inst.market:
        return None
    return next((r for r in market_service.state_rows(inst.market) if r["key"] == key), None)


def _install_view(pkg: models.MarketPackage, versions: list[models.MarketVersion], user: models.User, db: Session, enabled: bool = True) -> dict:
    inst = db.query(models.MarketInstall).filter_by(user_id=user.id, package_id=pkg.id).first()
    conn = registry.get(user.id)
    if not inst:
        return {"installed": False, "state": "none", "version": None, "error": None, "needs_consent": False}
    chosen, need = resolve(versions, pkg, user.id, inst)
    if not chosen:
        last = next((v for v in versions if v.status == "withdrawn"), None)
        state = "needs_consent" if need else "recalled" if last else "unavailable"
        return {"installed": True, "state": state, "version": None, "error": None, "needs_consent": need,
                "new_caps": _new_caps(versions, inst, pkg, user)}
    if not enabled:  # PC는 스킬을 지웠고 다시 켜지면 저절로 돌아온다. "설치 중"이라고 하면 끝나지 않는 일을 기다리게 된다
        return {"installed": True, "state": "paused", "version": chosen.version, "error": None, "needs_consent": False, "new_caps": []}
    row = _reported(user.id, key_of(pkg), db) or {}
    offline = not conn
    error = None
    current = row.get("version") == chosen.version and row.get("state") in ("installed", "conflict")
    failed = row.get("state") in ("failed", "agent_too_old") and (row.get("target") or row.get("version")) == chosen.version
    if conn and not conn.info.get("market_api"):
        state = "agent_old"
    elif current or failed:
        state, error = row["state"], row.get("error")
    else:
        state = "reserved" if offline else "installing"  # 꺼진 PC는 연결될 때 설치한다
    return {"installed": True, "state": "needs_consent" if need and state == "installed" else state, "version": chosen.version, "offline": offline,
            "error": error, "needs_consent": need, "new_caps": _new_caps(versions, inst, pkg, user) if need else [],
            "conflicts": _with_project_names(row.get("conflicts"), user, db) if current else [], "pinned": _with_project_names(row.get("pins"), user, db) if current else []}


def _with_project_names(rows: list[dict] | None, user: models.User, db: Session) -> list[dict]:
    """PC가 아는 이름은 폴더 이름일 수 있다. 그 사람이 속한 프로젝트면 서버의 이름을 보여 준다. 남의 프로젝트 번호로 이름을 알아낼 수 없게 멤버만 본다."""
    out = []
    for r in rows or []:
        pid = r.get("project_id")
        project = db.get(models.Project, pid) if isinstance(pid, int) and db.query(models.ProjectMember).filter_by(project_id=pid, user_id=user.id).first() else None
        out.append({**r, "project": project.name} if project else r)
    return out


def _new_caps(versions, inst, pkg, user) -> list[dict]:
    newest = market_service.latest_usable(versions, pkg, user.id)
    if not newest:
        return []
    return [{"key": c, "label": CAP_LABEL.get(c, c)} for c in newest.capabilities or [] if c not in (inst.consent_caps or [])]


def _search_text(pkg: models.MarketPackage, v: models.MarketVersion | None) -> str:
    """찾기에 쓰는 글. 카드에 보이는 요약만 보면 설명 뒤쪽의 말로 찾지 못한다."""
    ext = ext_of(v)
    return " ".join([pkg.public_name or pkg.name, pkg.category or "", (v.manifest or {}).get("displayName") or "" if v else "", (v.manifest or {}).get("description") or "" if v else "",
                     *(ext.get("skills") or []), *(ext.get("skillDescriptions") or {}).values(), *(ext.get("examples") or []), *(ext.get("requiresTools") or [])]).lower()


def _view(pkg: models.MarketPackage, user: models.User, db: Session, tools_ready: set[str] | None = None, *, detail: bool = False) -> dict:
    versions = versions_of(db, pkg.id)
    mine = pkg.owner_id == user.id
    latest = market_service.latest_usable(versions, pkg, user.id)
    # 남에게는 공개했던 버전의 이름표만 보인다. 회수 뒤에 올린 비공개 새 버전의 내용이 새 나가면 안 된다
    shown = latest or next((v for v in versions if mine or effective_admin(user) or v.status in (*PUBLIC_STATUS, "withdrawn")), None)
    ext = ext_of(shown)
    required = ext.get("requiresTools") or []
    ready_ids = _ready_ids(tools_ready)
    install = _install_view(pkg, versions, user, db, bool(market_service.get_setting(db).enabled))
    recalled = next((v for v in versions if v.status == "withdrawn"), None)
    repl = db.get(models.MarketPackage, recalled.replacement_id) if recalled and recalled.replacement_id else None
    # 관리부서는 공식 항목의 새 버전을 올릴 수 있으므로 자기가 올린 초안의 검사 결과를 봐야 한다
    draft = next((v for v in versions if v.status == "draft" and (mine or v.submitted_by == user.id)), None)
    out = {"id": pkg.id, "key": key_of(pkg), "name": pkg.public_name or pkg.name, "display_name": (shown.manifest or {}).get("displayName") if shown else pkg.display_name,
           "description": (shown.manifest or {}).get("description") if shown else pkg.description, "category": (shown.manifest or {}).get("category") or pkg.category or "" if shown else pkg.category or "", "official": pkg.official,
           "owner_name": _user_name(pkg.owner_id, db), "mine": mine, "public": bool(pkg.public_name) and any(v.status in PUBLIC_STATUS for v in versions),
           "kind": (ext.get("kinds") or ["skill_text"])[0], "code": is_code(shown) if shown else False, "version": shown.version if shown else None,
           "status": shown.status if shown else None, "status_label": STATUS_LABEL.get(shown.status, "") if shown else "", "grade": shown.grade if shown else None,
           "grade_label": GRADE_LABEL.get(shown.grade, "") if shown else "", "requires_tools": required, "tools_missing": [t for t in required if ready_ids is not None and t not in ready_ids],
           "capabilities": _caps(shown), "examples": ext.get("examples") or [], "installs": db.query(models.MarketInstall).filter_by(package_id=pkg.id).count(),
           "install": install, "evidence": shown.evidence if shown else None, "installable": bool(latest) and latest.status != "deprecated",
           "deprecated_note": (shown.review_note or "") if shown and shown.status == "deprecated" else None, "draft_version": draft.version if draft else None,
           "search_text": _search_text(pkg, shown),
           "recalled": {"reason": recalled.recall_reason, "at": recalled.reviewed_at, "replacement": (repl.public_name or repl.name) if repl else None} if recalled and not latest else None}
    out["recommended"] = bool(ready_ids and required and not out["tools_missing"] and not install["installed"] and out["public"] and out["installable"])
    if detail:
        visible = [v for v in versions if mine or effective_admin(user) or v.status in (*PUBLIC_STATUS, "withdrawn")]
        out["versions"] = [_version_row(v, db) for v in visible]
        out["skills"] = ext.get("skills") or []
        out["review_note"] = shown.review_note if shown and mine else None
        out["check_report"] = shown.check_report if shown and (mine or effective_admin(user)) else None
        out["self_approve_blocked"] = _self_approve_blocked(shown, user, db) if shown else False
        out["fingerprint"] = fingerprint(shown) if shown and (mine or effective_admin(user)) else None
        out["evidence_verified"] = False  # 지금은 등록자가 적은 값뿐이다. 관리부서가 확인하는 길이 생기면 그때 켠다
        if draft:  # 서랍을 닫아도 이어서 고칠 수 있게 초안의 값을 함께 보낸다
            dm, de = draft.manifest or {}, ext_of(draft)
            out["draft"] = {"version": draft.version, "fingerprint": fingerprint(draft), "check_report": draft.check_report, "self_approve_blocked": _self_approve_blocked(draft, user, db),
                            "display_name": dm.get("displayName"), "description": dm.get("description"), "category": dm.get("category") or "",
                            "examples": de.get("examples") or [], "requires_tools": de.get("requiresTools") or [], "code": is_code(draft), "notes": draft.notes or ""}
    return out


def _user_name(uid: int, db: Session) -> str:
    u = db.get(models.User, uid)
    return u.display_name if u else ""


def _ready_ids(names: set[str] | None) -> set[str] | None:
    """hello.tools_ready는 툴 표시 이름이다. 항목의 필요한 툴은 id다."""
    if names is None:
        return None
    from cae_adapters.catalog import TOOLS

    return {t["id"] for t in TOOLS if t["name"] in names or t["id"] in names}


def _tools_ready(user: models.User) -> set[str] | None:
    conn = registry.get(user.id)
    ready = conn.info.get("tools_ready") if conn else None
    return set(ready) if ready is not None else None  # 모르면 추천 줄을 숨긴다


# ---------- 목록 ----------
@router.get("/packages", response_model=schemas.MarketList)
def list_packages(q: str = "", kind: str = "", mine: bool = False, user=Depends(get_current_user), db: Session = Depends(get_db)):
    cfg = market_service.get_setting(db)
    ready = _tools_ready(user)
    conn = registry.get(user.id)
    items = []
    terms = q.lower().split()
    for pkg in db.query(models.MarketPackage).order_by(models.MarketPackage.id).all():
        if mine and pkg.owner_id != user.id:
            continue
        # 관리자도 남의 비공개 항목은 카드에 섞지 않는다. 그것은 관리 > 마켓에서 본다
        if pkg.owner_id != user.id and not any(v.status in PUBLIC_STATUS or _holds(v, pkg, user, db) for v in versions_of(db, pkg.id)):
            continue
        row = _view(pkg, user, db, ready)
        if kind and row["kind"] != kind:
            continue
        if not all(t in row["search_text"] for t in terms):  # "용접 피로"처럼 띄어 쓴 말은 단어마다 찾는다
            continue
        items.append(row)
    order = {"official": 0, "reviewed": 1}
    items.sort(key=lambda r: (not r["recommended"], order.get(r["grade"], 2), -r["installs"], r["name"]))
    return {"enabled": bool(cfg.enabled), "items": items, "base_skills": len(market_service.BASE_SKILLS),
            "agent": {"connected": conn is not None, "market_api": bool(conn and conn.info.get("market_api")), "tools_ready": sorted(ready or [])}}


@router.get("/packages/{ref}", response_model=schemas.MarketPackageView)
def get_package(ref: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _view(_package(ref, user, db), user, db, _tools_ready(user), detail=True)


@router.get("/packages/{ref}/versions/{version}/files")
def list_files(ref: str, version: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    pkg = _package(ref, user, db)
    v = _readable(pkg, version, user, db)
    return {"files": v.files or [], "sha256": v.sha256}


@router.get("/packages/{ref}/versions/{version}/files/{path:path}")
def read_file(ref: str, version: str, path: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    pkg = _package(ref, user, db)
    v = _readable(pkg, version, user, db)
    if path not in {f["path"] for f in v.files or []}:
        raise HTTPException(404, f"파일이 없습니다: {path}")
    body = _zip_members(pkg, v).get(path, b"")
    ext = PurePosixPath(path).suffix.lower()
    if ext in market_check.CHECKED_IMAGE_EXT and len(body) <= MAX_PREVIEW_IMAGE:  # 그림은 검토자가 눈으로 봐야 한다
        mime = "image/png" if ext == ".png" else "image/jpeg"
        return {"path": path, "text": None, "image": f"data:{mime};base64,{base64.b64encode(body).decode()}", "size": len(body), "truncated": False}
    if b"\0" in body[:4096] or ext in market_check.IMAGE_EXT - {".svg"}:
        return {"path": path, "text": None, "size": len(body), "truncated": False}
    return {"path": path, "text": market_check._decode_any(body[:MAX_VIEW_BYTES]), "size": len(body), "truncated": len(body) > MAX_VIEW_BYTES}


def _zip_file(pkg: models.MarketPackage, v: models.MarketVersion):
    f = market_service.zip_path(pkg, v.version)
    if not f.is_file():
        raise HTTPException(410, f"서버에 이 버전의 파일이 없습니다({pkg.name} {v.version}) → 관리부서에 알려 주세요. 다시 올려야 설치할 수 있습니다")
    return f


def _zip_members(pkg: models.MarketPackage, v: models.MarketVersion) -> dict[str, bytes]:
    """검사를 통과해 서버가 다시 쓴 zip을 메모리에서 읽는다. 디스크에 풀지 않는다."""
    with zipfile.ZipFile(_zip_file(pkg, v)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}


@router.get("/packages/{ref}/versions/{version}/download")
def download(ref: str, version: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    pkg = _package(ref, user, db)
    v = _readable(pkg, version, user, db)
    return FileResponse(_zip_file(pkg, v), media_type="application/zip", filename=f"{pkg.name}-{v.version}.zip",
                        headers={"X-Market-Sha256": v.sha256})


def _diff_text(path: str, old: bytes, new: bytes) -> dict:
    if b"\0" in old[:4096] or b"\0" in new[:4096]:
        return {"diff": None, "reason": "binary"}
    a, b = market_check._decode_any(old).splitlines(), market_check._decode_any(new).splitlines()
    if len(old) + len(new) > MAX_DIFF_INPUT_BYTES or len(a) + len(b) > MAX_DIFF_INPUT_LINES:
        return {"diff": None, "reason": "too_big"}
    rows = list(difflib.unified_diff(a, b, f"이전 {path}", f"이번 {path}", lineterm="", n=2))
    # 잘렸는데 전부인 것처럼 보이면 검토자가 끝에 붙은 코드를 놓친다
    return {"diff": "\n".join(rows[:MAX_DIFF_LINES]), "truncated": len(rows) > MAX_DIFF_LINES, "total_lines": len(rows)}


@router.get("/packages/{ref}/versions/{version}/diff")
def diff(ref: str, version: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """직전 버전과 무엇이 달라졌나. 새 버전에서 나빠지는 일을 검토자가 알아보는 근거다."""
    pkg = _package(ref, user, db)
    v = _version(pkg, version, db)
    if not (pkg.owner_id == user.id or effective_admin(user)):
        raise HTTPException(403, "만든 사람과 관리자만 볼 수 있습니다")
    prev = next((x for x in versions_of(db, pkg.id) if vkey(x.version) < vkey(v.version) and x.status != "draft"), None)
    new = _zip_members(pkg, v)
    old = _zip_members(pkg, prev) if prev else {}
    files = []
    for path in sorted(set(new) | set(old)):
        if path in new and path in old and new[path] == old[path]:
            continue
        status = "added" if path not in old else "removed" if path not in new else "changed"
        files.append({"path": path, "status": status, **_diff_text(path, old.get(path, b""), new.get(path, b""))})
    return {"previous": prev.version if prev else None, "files": files}


# ---------- 형식 ----------
@router.get("/format")
def package_format(user=Depends(get_current_user)):
    """형식과 기준을 등록 화면에서 볼 수 있게 한다. 엔지니어에게는 웹앱과 PC Agent뿐이고 저장소가 없다."""
    try:
        spec = SPEC_FILE.read_text(encoding="utf-8")
    except OSError:
        spec = ""
    return {"spec": spec, "templates": [{"name": n, "title": t, "description": d} for n, (t, d) in TEMPLATES.items() if (TEMPLATE_DIR / n).is_dir()]}


@router.get("/templates/{name}")
def template_zip(name: str, user=Depends(get_current_user)):
    folder = TEMPLATE_DIR / name
    if name not in TEMPLATES or not folder.is_dir():
        raise HTTPException(404, f"시작 틀을 찾을 수 없습니다: {name} → 등록 화면에 보이는 틀을 고르세요")
    files = [(f"{name}/{p.relative_to(folder).as_posix()}", p.read_bytes()) for p in sorted(folder.rglob("*")) if p.is_file() and "__pycache__" not in p.parts]
    return Response(market_check.build_zip(files), media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{name}.zip"'})


# ---------- 등록 ----------
@router.post("/submissions", response_model=schemas.MarketSubmission)
def submit(file: UploadFile | None = File(None), files: list[UploadFile] = File(default=[]), paths: str = Form(""), name_hint: str = Form(""),
           source: str = Form("web"), capture_id: str = Form(""), evidence: str = Form(""), notes: str = Form(""),
           user=Depends(require_jwt), db: Session = Depends(get_db)):
    """zip 하나 또는 폴더의 파일들. 결과는 항상 초안이다. 공개 범위를 정하는 것은 사람이다.
    등록도 사람만 한다: AI가 사람 이름으로 올린 초안이 그 사람의 목록에 섞이면 안 된다. 토큰을 쓰는 호출자는 아직 없다.
    검사에 걸리면 400이고 아무것도 저장하지 않는다. 자산화 제출도 source=capture로 이 길을 지난다."""
    _require_on(db)
    if source not in SOURCES:
        raise HTTPException(400, f"알 수 없는 출처입니다: {source} → {', '.join(SOURCES)} 중 하나를 쓰세요")
    res = _run_check(file, files, paths, name_hint, user, db)
    pkg = _target_package(res, user, db)
    versions = versions_of(db, pkg.id) if pkg else []
    # 공식 항목은 여러 관리자가 올린다. 다른 사람이 고치던 초안은 지우지 않고 그 번호를 비켜 간다
    drafts = [v for v in versions if v.status == "draft" and v.submitted_by == user.id]
    versions = [v for v in versions if v not in drafts]
    blob = market_check.build_zip(res.files)
    sha = hashlib.sha256(blob).hexdigest()
    same = next((v for v in versions if v.sha256 == sha), None)
    if same:
        raise HTTPException(409, f"바뀐 내용이 없습니다(버전 {same.version}과 같습니다) → 파일을 고친 뒤 다시 올려 주세요. 이름표만 고치려면 항목의 '이름표 고치기'를 누르세요")
    used = [v.version for v in versions]
    if res.version and res.version in used:
        raise HTTPException(409, f"버전 {res.version}은(는) 이미 있습니다 → plugin.json의 version을 올리거나 빼 주세요. 빼면 서버가 올립니다")
    if res.version and used and vkey(res.version) < vkey(max(used, key=vkey)):
        raise HTTPException(409, f"버전 {res.version}이(가) 이미 있는 버전보다 낮습니다 → {max(used, key=vkey)}보다 높게 쓰거나 빼 주세요")
    for old in drafts:  # 올리다 고친 초안은 번호를 쓰지 않는다. 불변인 것은 초안을 벗어난 버전이다
        market_service.zip_path(pkg, old.version).unlink(missing_ok=True)
        db.delete(old)
    db.flush()
    version = res.version or market_service.next_version(used)
    if not pkg:
        pkg = models.MarketPackage(name=res.name, owner_id=user.id, display_name=res.manifest["displayName"], description=res.manifest["description"],
                                   category=res.manifest["category"])
        db.add(pkg)
        db.flush()
    manifest = json.loads(json.dumps(res.manifest))
    manifest["version"], manifest["author"] = version, user.display_name
    if versions:
        _inherit_labels(manifest, versions[0].manifest or {}, res.name)
    prov = {"source": source, "projectId": None, "captureId": capture_id[:200] or None}
    manifest["extensions"]["dds.conversa"]["provenance"] = prov
    warnings = list(res.warnings)
    taken = db.query(models.MarketPackage).filter_by(public_name=res.name).first()
    if taken and taken.id != pkg.id:
        warnings.insert(0, f"같은 이름 '{res.name}'의 공개 항목이 있습니다 → 나만 쓸 수는 있지만 공개하려면 이름을 바꿔야 합니다. 두 항목을 함께 설치하면 충돌합니다")
    report = {**res.report, "warnings": warnings}
    row = models.MarketVersion(package_id=pkg.id, version=version, status="draft", grade="mine", sha256=sha, size=len(blob), manifest=manifest,
                               files=market_check.file_rows(res.files), capabilities=res.capabilities, check_report=report, provenance=prov,
                               evidence=_parse_evidence(evidence), notes=notes.strip()[:2000], submitted_by=user.id)
    db.add(row)
    db.flush()
    dest = market_service.zip_path(pkg, version)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    tmp.write_bytes(blob)
    tmp.replace(dest)
    market_service.event(db, user, "submit", pkg, version, source=source)
    db.commit()
    return {"package": _view(pkg, user, db, _tools_ready(user), detail=True), "version": version, "report": report,
            "self_approve_blocked": _self_approve_blocked(row, user, db), "fingerprint": fingerprint(row)}


def _run_check(file, files, paths, name_hint, user: models.User, db: Session) -> market_check.CheckResult:
    """검사는 서버 전체와 CPU를 나눠 쓴다. 함께 도는 수를 묶고, 오래 걸린 사람은 그만큼 쉬게 한다."""
    now = time.monotonic()
    with _cooldown_lock:
        wait = _cooldown.get(user.id, 0) - now
    if wait > 0:
        raise HTTPException(429, f"방금 올린 묶음을 검사하느라 시간이 오래 걸렸습니다 → {int(wait) + 1}초 뒤에 다시 올려 주세요. 스크립트와 문서만 넣으면 빨라집니다")
    if not _check_slots.acquire(blocking=False):
        raise HTTPException(429, "다른 등록을 검사하고 있습니다 → 잠시 뒤 다시 올려 주세요")
    try:
        raw, hint = _read_upload(file, files, paths, name_hint)
        ctx = market_check.CheckContext(base_skills=market_service.BASE_SKILLS, public_names=_public_names(user, db), public_skills=_public_skills(user, db),
                                        project_names=_project_names(user.id, db), item_no=_next_item_no(db))
        return market_check.check_package(raw, ctx, hint)
    except RecipeRejected as e:
        raise HTTPException(400, str(e)) from None
    except HTTPException:
        raise
    except Exception:  # 올린 내용이 일으킨 오류다. 빈 500 대신 할 일을 알린다
        log.exception("마켓 묶음을 검사하지 못했습니다")
        raise HTTPException(400, "묶음을 읽지 못했습니다 → 폴더를 다시 묶어 올려 주세요. 반복되면 관리부서에 알려 주세요") from None
    finally:
        _check_slots.release()
        spent = time.monotonic() - now
        if spent > SLOW_CHECK_SEC:
            with _cooldown_lock:
                _cooldown[user.id] = time.monotonic() + spent


def _inherit_labels(manifest: dict, prev: dict, name: str):
    """등록 화면에서 고친 이름표가 새 버전을 올릴 때마다 사라지면 안 된다. 폴더에 값을 적었으면 그것이 우선이다."""
    ext, old = manifest["extensions"]["dds.conversa"], prev.get("extensions", {}).get("dds.conversa", {})
    if manifest.get("displayName") == name and prev.get("displayName"):
        manifest["displayName"] = prev["displayName"]
    if not manifest.get("category") and prev.get("category"):
        manifest["category"] = prev["category"]
    if not ext.get("examples") and old.get("examples"):
        ext["examples"] = old["examples"]


def _too_big(name: str, size: int) -> market_check.PackageRejected:
    return market_check.PackageRejected(f"파일이 {MAX_FILE // (1024 * 1024)}MB를 넘습니다: {clean_label(name)[:80]} ({size // (1024 * 1024)}MB) → 해석 결과·모델 파일은 빼고 스크립트와 문서만 올려 주세요")


def _read_upload(file: UploadFile | None, files: list[UploadFile], paths: str, name_hint: str) -> tuple[bytes, str]:
    """크기를 먼저 보고 읽는다. 읽고 나서 거절하면 odb 하나가 공유 서버의 메모리를 다 쓴다."""
    limit = market_check.MAX_BUNDLE
    if file is not None and file.filename:
        if (file.size or 0) > limit:
            raise market_check.PackageRejected(f"묶음이 {limit // (1024 * 1024)}MB를 넘습니다 → 스크립트와 문서만 넣어 주세요. 해석 결과·모델 파일은 넣지 않습니다")
        body = file.file.read(limit + 1)
        if len(body) > limit:
            raise market_check.PackageRejected(f"묶음이 {limit // (1024 * 1024)}MB를 넘습니다 → 스크립트와 문서만 넣어 주세요. 해석 결과·모델 파일은 넣지 않습니다")
        return body, name_hint or (file.filename or "")
    if not files:
        raise market_check.PackageRejected("올린 파일이 없습니다 → 폴더나 zip을 놓아 주세요")
    try:
        names = json.loads(paths) if paths else [f.filename or "" for f in files]
    except ValueError:
        raise market_check.PackageRejected("파일 경로 목록을 읽지 못했습니다 → 다시 올려 주세요") from None
    if not isinstance(names, list) or len(names) != len(files):
        raise market_check.PackageRejected("파일 경로 목록과 파일 수가 다릅니다 → 다시 올려 주세요")
    picked = [(str(n), f) for n, f in zip(names, files) if not market_check.is_junk(str(n))]
    if not picked:
        raise market_check.PackageRejected("올릴 파일이 없습니다 → .venv·.git·__pycache__·node_modules만 있는 폴더는 받을 수 없습니다")
    if len(picked) > MAX_FILES:
        raise market_check.PackageRejected(f"파일이 {MAX_FILES}개를 넘습니다({len(picked)}개) → 필요한 스크립트와 문서만 남기거나 해석 결과 폴더를 빼 주세요")
    for name, f in picked:
        if (f.size or 0) > MAX_FILE:
            raise _too_big(name, f.size or 0)
    if sum(f.size or 0 for _, f in picked) > limit * 3:
        raise market_check.PackageRejected(f"폴더가 너무 큽니다 → 스크립트와 문서만 올려 주세요. 압축한 크기가 {limit // (1024 * 1024)}MB 안이어야 합니다")
    rows = []
    for name, f in picked:
        body = f.file.read(MAX_FILE + 1)
        if len(body) > MAX_FILE:
            raise _too_big(name, len(body))
        rows.append((name, body))
    return market_check.pack_files(rows), name_hint


def _parse_evidence(raw: str):
    if not raw:
        return None
    if len(raw.encode("utf-8", "ignore")) > MAX_EVIDENCE_BYTES:
        raise HTTPException(400, f"시험 기록이 {MAX_EVIDENCE_BYTES // 1024}KB를 넘습니다 → 요약만 적어 주세요")
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        raise HTTPException(400, "시험 기록(evidence)을 읽지 못했습니다 → JSON으로 보내 주세요") from None


def _public_rows(db: Session, skip_owner: int | None = None, skip_pkg: int | None = None):
    """공개된 항목과 그 안의 스킬 이름. 항목 이름만 비교하면 묶음 안의 스킬로 남의 공개 스킬을 밀어낼 수 있다."""
    for p in db.query(models.MarketPackage).filter(models.MarketPackage.public_name.isnot(None)):
        if p.id == skip_pkg or p.owner_id == skip_owner:
            continue
        skills: set[str] = set()
        for v in versions_of(db, p.id):
            if v.status in PUBLIC_STATUS:
                skills.update(ext_of(v).get("skills") or [])
        yield p, skills


def _public_names(user: models.User, db: Session) -> set[str]:
    return {p.public_name for p, _ in _public_rows(db, skip_owner=user.id)}


def _public_skills(user: models.User, db: Session) -> set[str]:
    # 관리부서는 공식 항목의 새 버전을 올린다. 제 항목과 겹친다고 표시하면 검토자가 표시를 믿지 않게 된다. 다른 항목과 겹치면 공개할 때 _name_free가 막는다
    return {s for p, skills in _public_rows(db, skip_owner=user.id) if not (p.official and effective_admin(user)) for s in skills}


def _project_names(user_id: int, db: Session) -> set[str]:
    rows = db.query(models.Project.name).join(models.ProjectMember, models.ProjectMember.project_id == models.Project.id).filter(models.ProjectMember.user_id == user_id)
    return {r[0] for r in rows}


def _next_item_no(db: Session) -> int:
    last = db.query(models.MarketPackage).order_by(models.MarketPackage.id.desc()).first()
    return (last.id if last else 0) + 1


def _target_package(res: market_check.CheckResult, user: models.User, db: Session) -> models.MarketPackage | None:
    """내 항목의 새 버전이다. 관리부서는 다른 관리자가 유지하던 공식 항목의 새 버전도 올릴 수 있다. 다른 사람 항목에 새 버전을 얹는 길은 없다."""
    mine = db.query(models.MarketPackage).filter_by(owner_id=user.id, name=res.name).first()
    if mine:
        return mine
    pub = db.query(models.MarketPackage).filter_by(public_name=res.name).first()
    if pub and pub.official and effective_admin(user):
        return pub
    return None


def _apply_meta(pkg: models.MarketPackage, v: models.MarketVersion, meta: schemas.MarketMeta | None, db: Session):
    """이름표를 고치고 다시 검사한다. 이름표는 규칙 파일과 프롬프트 안내, 카드로 가므로 파일과 같은 대우를 받는다.
    줄바꿈과 보이지 않는 글자가 남으면 안내 줄을 흉내 내거나 사람이 못 보는 지시가 된다."""
    if meta:
        manifest = json.loads(json.dumps(v.manifest or {}))
        ext = manifest.setdefault("extensions", {}).setdefault("dds.conversa", {})
        raw = {"display_name": manifest.get("displayName"), "description": manifest.get("description"), "category": manifest.get("category"), "examples": ext.get("examples") or []}
        if meta.display_name is not None:
            raw["display_name"] = meta.display_name
            manifest["displayName"] = clean_label(meta.display_name)[:100] or manifest.get("displayName")
        if meta.description is not None:
            raw["description"] = meta.description
            manifest["description"] = market_check.summary_of(meta.description) or manifest.get("description")
        if meta.category is not None:
            raw["category"] = meta.category
            manifest["category"] = clean_label(meta.category)[:40]
        if meta.requires_tools is not None:
            from cae_adapters.catalog import TOOLS

            known = {t["id"] for t in TOOLS}
            ext["requiresTools"] = sorted(t for t in meta.requires_tools if t in known)
            ext["needsTool"] = bool(ext["requiresTools"])
        if meta.examples is not None:
            raw["examples"] = meta.examples
            ext["examples"] = [clean_label(x)[:200] for x in meta.examples if clean_label(x)][:5]
        v.manifest = manifest
        ctx = market_check.CheckContext(project_names=_project_names(pkg.owner_id, db))
        v.check_report = market_check.merge_label_flags(v.check_report or {}, market_check.label_flags(raw, ctx))
        if meta.notes is not None:
            v.notes = meta.notes.strip()[:2000]
    m = v.manifest or {}
    pkg.display_name, pkg.description, pkg.category = m.get("displayName") or pkg.display_name, m.get("description") or "", m.get("category") or ""


def _name_free(pkg: models.MarketPackage, v: models.MarketVersion, db: Session):
    """공개 전에 이름 충돌을 알린다. 남의 비공개 항목은 이름을 선점하지 못하고, 공개된 이름만 비켜 간다.
    묶음 안의 스킬도 본다: PC의 프로젝트 폴더에서는 스킬 이름이 경로라 겹치면 한쪽이 밀려난다."""
    taken = db.query(models.MarketPackage).filter_by(public_name=pkg.name).first()
    if taken and taken.id != pkg.id:
        raise HTTPException(409, f"같은 이름 '{pkg.name}'의 공개 항목이 있습니다 → 폴더 이름이나 plugin.json의 name을 바꿔 새로 올려 주세요")
    mine = set(ext_of(v).get("skills") or [])
    for other, skills in _public_rows(db, skip_pkg=pkg.id):
        clash = sorted(mine & (skills | {other.public_name}))
        if clash:
            raise HTTPException(409, f"스킬 이름 '{clash[0]}'을(를) 공개 항목 '{other.public_name}'이(가) 이미 씁니다 → 스킬 폴더 이름을 바꿔 새로 올려 주세요")


@router.post("/packages/{ref}/versions/{version}/private", response_model=schemas.MarketPackageView)
def make_private(ref: str, version: str, body: schemas.MarketAction | None = None, user=Depends(require_jwt), db: Session = Depends(get_db)):
    """나만 쓰기: 내 PC, 내 권한이므로 검토 없이 바로 쓴다."""
    pkg = _package(ref, user, db)
    _own(pkg, user)
    v = _version(pkg, version, db)
    if v.status != "draft":
        raise HTTPException(409, f"이미 '{STATUS_LABEL.get(v.status)}' 상태입니다 → 파일을 고치려면 새 버전을 올려 주세요")
    _same_content(v, body.fingerprint if body else None)
    _apply_meta(pkg, v, body.meta if body else None, db)
    v.status, v.grade = "private", "mine"
    _ensure_install(pkg, v, user, db)
    market_service.event(db, user, "private", pkg, version)
    db.commit()
    push_sync([user.id])
    return _view(pkg, user, db, _tools_ready(user), detail=True)


def _ensure_install(pkg, v, user, db):
    inst = db.query(models.MarketInstall).filter_by(user_id=user.id, package_id=pkg.id).first()
    if inst:
        inst.enabled = True
        inst.consent_caps = sorted({*(inst.consent_caps or []), *(v.capabilities or [])})
    else:
        db.add(models.MarketInstall(user_id=user.id, package_id=pkg.id, enabled=True, consent_caps=list(v.capabilities or [])))
    db.flush()


@router.post("/packages/{ref}/versions/{version}/request-review", response_model=schemas.MarketPackageView)
def request_review(ref: str, version: str, body: schemas.MarketAction | None = None, user=Depends(require_jwt), db: Session = Depends(get_db)):
    """부서에 검토 요청. 동료와 나누는 길도 이것 하나다. 반려된 버전은 이름표를 고쳐 다시 요청할 수 있다."""
    pkg = _package(ref, user, db)
    v = _version(pkg, version, db)
    _own_or_submitted(pkg, v, user)
    if v.status not in ("draft", "private", "rejected"):
        raise HTTPException(409, f"이미 '{STATUS_LABEL.get(v.status)}' 상태입니다 → 파일을 고치려면 새 버전을 올려 주세요")
    _same_content(v, body.fingerprint if body else None)
    _name_free(pkg, v, db)
    _apply_meta(pkg, v, body.meta if body else None, db)
    v.status = "review"
    market_service.event(db, user, "request_review", pkg, version)
    db.commit()
    push_sync([user.id])
    publish_updated(market_service.admins(db))
    return _view(pkg, user, db, _tools_ready(user), detail=True)


@router.patch("/packages/{ref}/versions/{version}/labels", response_model=schemas.MarketPackageView)
def edit_labels(ref: str, version: str, body: schemas.MarketLabels, user=Depends(require_jwt), db: Session = Depends(get_db)):
    """이름표(화면 이름·설명·분류·예시)만 고친다. 파일은 그대로라 새 버전이 필요 없다.
    공개한 버전은 검토를 받은 글이고 AI 안내로 가므로 만든 사람이 혼자 바꾸지 못한다."""
    pkg = _package(ref, user, db)
    v = _version(pkg, version, db)
    if v.status == "withdrawn":
        raise HTTPException(409, "회수한 버전은 고칠 수 없습니다 → 새 버전을 올려 주세요")
    mine = pkg.owner_id == user.id
    if not mine and not effective_admin(user):
        raise HTTPException(403, "만든 사람과 관리부서만 고칠 수 있습니다")
    if v.status in PUBLIC_STATUS or not mine:
        if not effective_admin(user):
            raise HTTPException(403, "공개한 항목의 이름표는 관리부서가 고칩니다 → 고칠 내용을 관리부서에 알려 주세요")
        if not body.pin:
            raise HTTPException(400, "PIN이 필요합니다 → 내 PIN을 입력해 주세요")
        _check_pin(user, body.pin, db)
    _apply_meta(pkg, v, body, db)
    market_service.event(db, user, "labels", pkg, version)
    users = recipients(db, pkg)
    db.commit()
    push_sync(users)  # 이름표는 안내로 간다. 바뀐 값을 PC에도 알린다
    return _view(pkg, user, db, _tools_ready(user), detail=True)


@router.delete("/packages/{ref}/versions/{version}")
def discard(ref: str, version: str, user=Depends(require_jwt), db: Session = Depends(get_db)):
    """공개한 적 없는 버전을 버린다. 공개한 버전은 지우지 않고 회수한다: 누가 언제 무엇을 썼는지 남아야 한다."""
    pkg = _package(ref, user, db)
    v = _version(pkg, version, db)
    _own_or_submitted(pkg, v, user)
    if v.status not in ("draft", "private", "rejected"):
        raise HTTPException(409, "공개했거나 검토 중인 버전은 지울 수 없습니다 → 공개한 버전은 관리부서가 회수합니다")
    market_service.zip_path(pkg, version).unlink(missing_ok=True)
    db.delete(v)
    db.flush()
    gone = not versions_of(db, pkg.id)
    users = recipients(db, pkg)
    if gone:
        market_service.event(db, user, "discard_package", pkg, version)
        db.query(models.MarketInstall).filter_by(package_id=pkg.id).delete()
        db.query(models.MarketNotice).filter_by(package_id=pkg.id).delete()
        db.delete(pkg)
    else:
        market_service.event(db, user, "discard", pkg, version)
    db.commit()
    push_sync(users)
    return {"ok": True, "package_removed": gone}


# ---------- 관리부서 ----------
def _review_row(pkg: models.MarketPackage, v: models.MarketVersion, admin: models.User, db: Session, counts: dict | None = None) -> dict:
    m, ext = v.manifest or {}, ext_of(v)
    return {"package_id": pkg.id, "name": pkg.public_name or pkg.name, "display_name": m.get("displayName") or pkg.display_name,
            "description": m.get("description") or "", "owner_name": _user_name(pkg.owner_id, db), "official": pkg.official,
            "category": m.get("category") or "", "examples": ext.get("examples") or [],
            "installs": db.query(models.MarketInstall).filter_by(package_id=pkg.id).count(), "pcs_on_version": (counts or {}).get((key_of(pkg), v.version), 0),
            "code": is_code(v), "report": v.check_report or {},
            "requires_tools": ext.get("requiresTools") or [], "self_approve_blocked": _self_approve_blocked(v, admin, db), **_version_row(v, db)}


def _self_approve_blocked(v: models.MarketVersion, user: models.User, db: Session) -> bool:
    """코드 항목은 만든 사람이 아닌 관리자가 승인한다. 관리자가 혼자면 기록을 남기고 허용한다."""
    if not (is_code(v) and v.submitted_by == user.id and effective_admin(user)):
        return False
    return db.query(models.User).filter(models.User.is_admin.is_(True), models.User.is_active.is_(True), models.User.id != user.id).count() > 0


@router.get("/review", response_model=schemas.MarketReview)
def review_queue(admin=Depends(require_admin), db: Session = Depends(get_db)):
    pkgs = {p.id: p for p in db.query(models.MarketPackage).all()}
    rows = db.query(models.MarketVersion).all()
    queue = [(pkgs[v.package_id], v) for v in rows if v.status == "review" and v.package_id in pkgs]
    queue.sort(key=lambda x: (not is_code(x[1]), x[1].created_at))  # 코드 있는 것부터, 오래된 것부터
    published = [(pkgs[v.package_id], v) for v in rows if v.status in (*PUBLIC_STATUS, "withdrawn") and v.package_id in pkgs]
    published.sort(key=lambda x: (x[0].id, vkey(x[1].version)), reverse=True)
    cfg = market_service.get_setting(db)
    counts = market_service.version_counts(db)
    return {"queue": [_review_row(p, v, admin, db, counts) for p, v in queue], "published": [_review_row(p, v, admin, db, counts) for p, v in published],
            "settings": {"enabled": bool(cfg.enabled), "gateway_on": bool(cfg.gateway_on)}}


@router.post("/packages/{ref}/versions/{version}/approve", response_model=schemas.MarketPackageView)
def approve(ref: str, version: str, body: schemas.MarketApprove, admin=Depends(require_admin), db: Session = Depends(get_db)):
    """사람이 내용을 읽었다는 기록이다. 등록 화면의 '바로 공개'도 이 길 하나를 지난다."""
    pkg = _package(ref, admin, db)
    v = _version(pkg, version, db)
    _check_pin(admin, body.pin, db)
    if v.status not in ("draft", "private", "review"):
        raise HTTPException(409, f"'{STATUS_LABEL.get(v.status)}' 상태는 승인할 수 없습니다 → 파일을 고치려면 새 버전을 올려 주세요")
    if v.status in ("draft", "private") and v.submitted_by != admin.id:  # 남이 나만 쓰려고 올린 것을 동의 없이 전사에 공개하면 안 된다
        raise HTTPException(409, "아직 검토 요청을 받지 않은 항목입니다 → 만든 사람이 검토를 요청한 뒤 승인하세요")
    if _self_approve_blocked(v, admin, db):
        raise HTTPException(409, "코드가 있는 항목은 만든 사람이 스스로 승인할 수 없습니다 → 부서에 검토를 요청해 다른 관리자가 승인하게 하세요")
    _same_content(v, body.fingerprint)
    _apply_meta(pkg, v, body.meta, db)  # 양식의 값까지 검사한 뒤에 표시 항목을 본다
    wanted = {f["rule"] for f in (v.check_report or {}).get("flags") or []}
    missing = wanted - set(body.acks or [])
    if missing:
        titles = [f["title"] for f in v.check_report["flags"] if f["rule"] in missing]
        raise HTTPException(400, f"확인하지 않은 항목이 있습니다: {', '.join(titles)} → 내용을 읽고 항목마다 체크하세요")
    _name_free(pkg, v, db)
    self_ok = is_code(v) and v.submitted_by == admin.id
    pkg.public_name = pkg.name
    if body.official:
        pkg.official = True
    v.status, v.grade = "approved", "official" if pkg.official else "reviewed"
    v.acks, v.reviewed_by, v.reviewed_at, v.review_note = sorted(wanted), admin.id, datetime.now(), (body.note or "").strip()[:2000]
    market_service.event(db, admin, "self_approve" if self_ok else "approve", pkg, version, official=bool(body.official))
    market_service.notify(db, [pkg.owner_id], pkg, version, "approved", f"'{pkg.display_name}' {version}이(가) 공개되었습니다. 이제 다른 사람도 설치할 수 있습니다.", skip=admin.id)
    users = recipients(db, pkg)
    db.commit()
    push_sync(users)
    publish_updated([*users, *market_service.admins(db)])
    return _view(pkg, admin, db, _tools_ready(admin), detail=True)


@router.post("/packages/{ref}/versions/{version}/reject", response_model=schemas.MarketPackageView)
def reject(ref: str, version: str, body: schemas.MarketPinNote, admin=Depends(require_admin), db: Session = Depends(get_db)):
    pkg = _package(ref, admin, db)
    v = _version(pkg, version, db)
    _check_pin(admin, body.pin, db)
    if v.status != "review":
        raise HTTPException(409, f"'{STATUS_LABEL.get(v.status)}' 상태는 반려할 수 없습니다 → 공개한 버전은 회수하세요")
    if not (body.note or "").strip():
        raise HTTPException(400, "반려 사유를 적어 주세요 → 만든 사람이 무엇을 고칠지 알 수 있어야 합니다")
    v.status, v.reviewed_by, v.reviewed_at, v.review_note = "rejected", admin.id, datetime.now(), body.note.strip()[:2000]
    market_service.event(db, admin, "reject", pkg, version)
    market_service.notify(db, [pkg.owner_id], pkg, version, "rejected",
                          f"'{pkg.display_name}' {version}의 전사 공개가 반려되었습니다. 사유: {v.review_note} → 나는 계속 쓸 수 있습니다. 고쳐서 다시 요청해 주세요.", skip=admin.id)
    db.commit()
    push_sync([pkg.owner_id])
    publish_updated([pkg.owner_id, *market_service.admins(db)])
    return _view(pkg, admin, db, _tools_ready(admin), detail=True)


@router.post("/packages/{ref}/versions/{version}/deprecate", response_model=schemas.MarketPackageView)
def deprecate(ref: str, version: str, body: schemas.MarketPinNote, admin=Depends(require_admin), db: Session = Depends(get_db)):
    """내림: 새 설치만 막는다. 이미 설치한 사람은 그대로 쓴다. 문제가 있으면 회수를 쓴다."""
    pkg = _package(ref, admin, db)
    v = _version(pkg, version, db)
    _check_pin(admin, body.pin, db)
    if v.status != "approved":
        raise HTTPException(409, "공개 중인 버전만 내릴 수 있습니다")
    v.status, v.review_note = "deprecated", (body.note or "").strip()[:2000]
    market_service.event(db, admin, "deprecate", pkg, version)
    users = recipients(db, pkg)
    market_service.notify(db, users, pkg, version, "deprecated",
                          f"관리부서가 '{pkg.display_name}' {version}을(를) 내렸습니다." + (f" 사유: {v.review_note}" if v.review_note else "") + " 새로 설치할 수 없고, 이미 설치한 사람은 계속 씁니다.", skip=admin.id)
    db.commit()
    push_sync(users)
    publish_updated(users)
    return _view(pkg, admin, db, _tools_ready(admin), detail=True)


@router.post("/packages/{ref}/versions/{version}/withdraw", response_model=schemas.MarketPackageView)
def withdraw(ref: str, version: str, body: schemas.MarketWithdraw, admin=Depends(require_admin), db: Session = Depends(get_db)):
    """회수. 기본은 이전 버전으로 돌아가지 않는다: 고친 버전을 회수했을 때 옛 오류 버전으로 조용히 돌아가면 안 된다."""
    pkg = _package(ref, admin, db)
    v = _version(pkg, version, db)
    _check_pin(admin, body.pin, db)
    if v.status not in PUBLIC_STATUS:
        raise HTTPException(409, "공개했던 버전만 회수할 수 있습니다 → 비공개 버전은 만든 사람이 지웁니다")
    if not (body.reason or "").strip():
        raise HTTPException(400, "회수 사유를 적어 주세요 → 설치한 사람의 카드에 그대로 보입니다")
    v.status, v.recall_reason, v.recall_fallback = "withdrawn", body.reason.strip()[:2000], body.fallback == "previous"
    v.reviewed_by, v.reviewed_at = admin.id, datetime.now()
    if body.replacement:
        repl = db.query(models.MarketPackage).filter_by(public_name=body.replacement).first()
        if not repl or repl.id == pkg.id:
            raise HTTPException(400, f"대체 항목 '{body.replacement}'을(를) 찾을 수 없습니다 → 공개된 다른 항목의 이름을 쓰거나 비워 두세요")
        v.replacement_id = repl.id
    market_service.event(db, admin, "withdraw", pkg, version, fallback=body.fallback)
    users = recipients(db, pkg)
    # 지금은 설치를 지웠어도 AI가 읽은 적이 있으면 알린다. 어제 낸 보고서가 이 스킬에 기댔을 수 있다
    told = users | market_service.readers(db, pkg)
    market_service.notify(db, told, pkg, version, "recalled", market_service.recall_text(pkg, v, db), skip=admin.id)
    db.commit()
    push_sync(users)
    publish_updated(told)
    return _view(pkg, admin, db, _tools_ready(admin), detail=True)


@router.get("/packages/{ref}/coverage")
def recall_coverage(ref: str, admin=Depends(require_admin), db: Session = Depends(get_db)):
    return market_service.coverage(db, _package(ref, admin, db))


@router.get("/settings", response_model=schemas.MarketSettings)
def get_settings(user=Depends(get_current_user), db: Session = Depends(get_db)):
    cfg = market_service.get_setting(db)
    return {"enabled": bool(cfg.enabled), "gateway_on": bool(cfg.gateway_on)}


@router.put("/settings", response_model=schemas.MarketSettings)
def put_settings(body: schemas.MarketSettingsUpdate, admin=Depends(require_admin), db: Session = Depends(get_db)):
    """끄면 설치·등록을 막고 모든 PC에서 마켓 스킬을 쓰지 않게 한다. 회수는 꺼져 있어도 계속 전달한다."""
    _check_pin(admin, body.pin, db)
    cfg = db.query(models.MarketSetting).first()
    if not cfg:
        cfg = models.MarketSetting(enabled=True, gateway_on=False)
        db.add(cfg)
    cfg.enabled, cfg.updated_by = body.enabled, admin.id
    market_service.event(db, admin, "settings", enabled=body.enabled)
    db.commit()
    push_sync(u.user_id for u in db.query(models.MarketInstall).all())
    return {"enabled": bool(cfg.enabled), "gateway_on": bool(cfg.gateway_on)}


# ---------- 알림 ----------
@router.get("/notices", response_model=schemas.MarketNotices)
def notices(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """사이드바의 숫자와 마켓 첫머리의 안내. 검토 결과와 회수는 찾아가야만 보이면 안 된다."""
    rows = db.query(models.MarketNotice).filter_by(user_id=user.id, read_at=None).order_by(models.MarketNotice.id.desc()).limit(50).all()
    waiting = db.query(models.MarketVersion).filter_by(status="review").count() if effective_admin(user) else 0
    return {"items": rows, "review_waiting": waiting}


@router.post("/notices/{notice_id}/read")
def notice_read(notice_id: int, user=Depends(require_jwt), db: Session = Depends(get_db)):
    row = db.get(models.MarketNotice, notice_id)
    if not row or row.user_id != user.id:
        raise HTTPException(404, "알림을 찾을 수 없습니다 → 화면을 새로 고쳐 주세요")
    row.read_at = datetime.now()
    db.commit()
    return {"ok": True}


# ---------- 설치 ----------
@router.get("/installs/mine")
def my_installs(user=Depends(get_current_user), db: Session = Depends(get_db)):
    out = []
    for inst in db.query(models.MarketInstall).filter_by(user_id=user.id):
        pkg = db.get(models.MarketPackage, inst.package_id)
        if pkg:
            out.append(_view(pkg, user, db, _tools_ready(user)))
    return {"items": out}


@router.put("/installs/{ref}", response_model=schemas.MarketPackageView)
def install(ref: str, body: schemas.MarketInstallRequest | None = None, user=Depends(require_jwt), db: Session = Depends(get_db)):
    """사람만 설치한다. AI가 스스로 설치하지 못하게 하는 지점이다. 내 계정의 모든 프로젝트에 적용된다.
    PC가 꺼져 있어도 저장하고, 연결될 때 설치한다."""
    _require_on(db)
    pkg = _package(ref, user, db)
    versions = versions_of(db, pkg.id)
    newest = market_service.latest_usable(versions, pkg, user.id)
    if not newest:
        raise HTTPException(409, "지금 설치할 수 있는 버전이 없습니다 → 관리부서가 회수를 풀거나 새 버전이 공개되면 설치할 수 있습니다")
    inst = db.query(models.MarketInstall).filter_by(user_id=user.id, package_id=pkg.id).first()
    if newest.status == "deprecated" and not inst:
        raise HTTPException(409, "관리부서가 내린 항목이라 새로 설치할 수 없습니다 → 비슷한 다른 항목을 찾아보세요")
    if is_code(newest) and not (body and body.consent):
        raise HTTPException(409, "내 PC에서 내 권한으로 실행되는 코드가 있습니다 → 자동 검사가 찾은 일을 확인하고 설치를 눌러 주세요")
    if inst:
        inst.enabled = True
        inst.consent_caps = sorted({*(inst.consent_caps or []), *(newest.capabilities or [])})
    else:
        inst = models.MarketInstall(user_id=user.id, package_id=pkg.id, enabled=True, consent_caps=list(newest.capabilities or []))
        db.add(inst)
    if body and body.use_latest:
        inst.version_floor = newest.version
    market_service.event(db, user, "install", pkg, newest.version)
    db.commit()
    push_sync([user.id])
    return _view(pkg, user, db, _tools_ready(user), detail=True)


@router.post("/installs/{ref}/retry", response_model=schemas.MarketPackageView)
def install_retry(ref: str, user=Depends(require_jwt), db: Session = Depends(get_db)):
    """실패한 설치를 지금 다시 시도한다. 동의한 일은 바꾸지 않고 목록만 다시 보낸다: 새 메시지면 PC Agent가 받기부터 다시 한다."""
    _require_on(db)
    pkg = _package(ref, user, db)
    if not db.query(models.MarketInstall).filter_by(user_id=user.id, package_id=pkg.id).first():
        raise HTTPException(409, "설치한 항목이 아닙니다 → 먼저 설치를 눌러 주세요")
    push_sync([user.id])
    return _view(pkg, user, db, _tools_ready(user), detail=True)


@router.delete("/installs/{ref}")
def uninstall(ref: str, user=Depends(require_jwt), db: Session = Depends(get_db)):
    """제거. PC의 스킬 복사본은 Agent가 지운다. AI가 scripts/나 레시피로 복사한 것은 남는다(화면에 그렇게 적는다)."""
    pkg = _package(ref, user, db)
    inst = db.query(models.MarketInstall).filter_by(user_id=user.id, package_id=pkg.id).first()
    if inst:
        db.delete(inst)
        market_service.event(db, user, "uninstall", pkg)
        db.commit()
    push_sync([user.id])
    return {"ok": True}


@router.get("/events")
def events(limit: int = Query(100, le=500), admin=Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.query(models.MarketEvent).order_by(models.MarketEvent.id.desc()).limit(limit).all()
    return [{"kind": r.kind, "actor": _user_name(r.actor_id, db) if r.actor_id else None, "via": r.via, "package_id": r.package_id, "version": r.version,
             "detail": r.detail, "at": r.created_at} for r in rows]
