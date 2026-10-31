"""마켓 계산과 PC Agent 동기화. 어느 버전을 쓰는지·rev는 저장하지 않고 계산한다: 표가 적고, DB를 복원해도 예외 처리가 필요 없다.

서버 → Agent: market.sync(전체 목록을 맞춘다. 메시지를 잃어도 다음 연결에서 복구된다)
Agent → 서버: market.state(결과), market.note(이 대화의 사용자에게 알릴 글)
턴 앞의 왕복은 없다. 적용은 PC Agent가 턴 시작 때 스스로 한다. 서버는 "회수를 받았다고 답했는가"만 본다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import datetime

from sqlalchemy.orm import Session

from . import market_check, models
from .agent.link import AgentError, registry
from .config import settings
from .database import SessionLocal
from .ws import manager

log = logging.getLogger(__name__)

USABLE_OTHERS = ("approved", "deprecated")
# 반려는 "전사 공개 불가"이지 "나도 쓰지 마"가 아니다. 누구나 올려 본인은 쓴다는 결정이다
USABLE_OWNER = ("private", "review", "approved", "deprecated", "rejected")
PUBLIC_STATUS = ("approved", "deprecated")
GRADE_LABEL = {"mine": "내 것 · 나만", "reviewed": "검토 완료", "official": "관리부서 제공"}
STATUS_LABEL = {"draft": "초안", "private": "나만 쓰기", "review": "검토 대기", "approved": "공개", "rejected": "반려", "deprecated": "내림", "withdrawn": "회수"}
RECALL_WAIT_SEC = 15.0
BASE_SKILLS = market_check.base_skill_names()
_SKILL_PATH = re.compile(r"skills[/\\]+([a-z][a-z0-9-]{0,39})[/\\]+SKILL\.md")
MAX_STATE_ROWS = 1000


def vkey(v: str | None) -> tuple:
    try:
        return tuple(int(x) for x in (v or "0.0.0").split("."))
    except ValueError:
        return (0, 0, 0)


def next_version(prev: list[str]) -> str:
    if not prev:
        return "1.0.0"
    a, b, c = vkey(max(prev, key=vkey))
    return f"{a}.{b}.{c + 1}"


def market_dir():
    d = settings.data_dir / "market"
    d.mkdir(parents=True, exist_ok=True)
    return d


def zip_path(pkg: models.MarketPackage, version: str):
    return market_dir() / str(pkg.id) / f"{version}.zip"


def get_setting(db: Session) -> models.MarketSetting:
    s = db.query(models.MarketSetting).first()
    return s or models.MarketSetting(enabled=True, gateway_on=False)


def versions_of(db: Session, pkg_id: int) -> list[models.MarketVersion]:
    rows = db.query(models.MarketVersion).filter_by(package_id=pkg_id).all()
    return sorted(rows, key=lambda v: vkey(v.version), reverse=True)


def key_of(pkg: models.MarketPackage) -> str:
    """PC에서 항목을 부르는 이름. 비공개 항목에 사용자 번호를 붙여 남의 공개 항목과 섞이지 않게 한다."""
    return pkg.public_name or f"u{pkg.owner_id}.{pkg.name}"


def is_code(v: models.MarketVersion) -> bool:
    return "skill_code" in ((v.manifest or {}).get("extensions", {}).get("dds.conversa", {}).get("kinds") or [])


def ext_of(v: models.MarketVersion | None) -> dict:
    return ((v.manifest or {}) if v else {}).get("extensions", {}).get("dds.conversa", {})


def usable_statuses(pkg: models.MarketPackage, user_id: int) -> tuple:
    return USABLE_OWNER if pkg.owner_id == user_id else USABLE_OTHERS


def fingerprint(v: models.MarketVersion) -> str:
    """사람이 화면에서 본 내용의 지문: 파일과 이름표. 공개·승인할 때 이 값이 다르면 그 사이에 내용이 바뀐 것이다."""
    m, ext = v.manifest or {}, ext_of(v)
    labels = {"displayName": m.get("displayName"), "description": m.get("description"), "category": m.get("category"),
              "examples": ext.get("examples"), "requiresTools": ext.get("requiresTools")}
    return hashlib.sha256(f"{v.sha256}|{json.dumps(labels, ensure_ascii=False, sort_keys=True)}".encode()).hexdigest()[:32]


def resolve(versions: list[models.MarketVersion], pkg: models.MarketPackage, user_id: int,
            inst: models.MarketInstall | None) -> tuple[models.MarketVersion | None, bool]:
    """이 사용자가 지금 써야 할 버전과, 새 버전이 더 많은 일을 해 다시 물어야 하는지.
    최신을 회수하면 복귀를 고른 회수만 아래로 내려간다. 복귀 없음을 고른 회수를 만나면 거기서 멈춘다."""
    usable = usable_statuses(pkg, user_id)
    consent = set(inst.consent_caps or []) if inst else None
    need_consent = False
    for v in versions:
        if inst and inst.version_pin and v.version != inst.version_pin:
            continue
        if v.status == "withdrawn":
            if v.recall_fallback:
                continue
            return None, need_consent
        if v.status not in usable:
            continue
        if consent is not None and not set(v.capabilities or []) <= consent:
            need_consent = True
            continue
        return v, need_consent
    return None, need_consent


def latest_usable(versions: list[models.MarketVersion], pkg: models.MarketPackage, user_id: int) -> models.MarketVersion | None:
    return resolve(versions, pkg, user_id, None)[0]


# ---------- PC가 알린 값 ----------
def state_rows(market) -> list[dict]:
    """AgentInstance.market의 행. 값은 PC의 토큰으로 온 것이다. 다른 사람의 승인·회수 계산에서 읽으므로 망가진 행이 있어도 지나간다."""
    rows = market.get("items") if isinstance(market, dict) else None
    return [r for r in rows if isinstance(r, dict) and isinstance(r.get("key"), str)] if isinstance(rows, list) else []


def _short(value, limit: int = 2000) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _clean_list(raw, fields: dict[str, type]) -> list[dict]:
    out = []
    for r in raw if isinstance(raw, list) else []:
        if isinstance(r, dict):
            row = {k: (r.get(k) if isinstance(r.get(k), t) and not isinstance(r.get(k), bool) else None) for k, t in fields.items()}
            for k in row:
                if isinstance(row[k], str):
                    row[k] = row[k][:500]
            out.append(row)
    return out[:200]


def clean_state(msg: dict) -> dict:
    """market.state를 저장할 모양으로 고른다. 검사 없이 저장하면 한 사람의 행이 회사 전체의 승인·회수를 500으로 멈춘다."""
    items = []
    for r in state_rows(msg)[:MAX_STATE_ROWS]:
        row = {"key": r["key"][:120], **{k: _short(r.get(k)) for k in ("name", "version", "state", "error", "target")}}
        conflicts = _clean_list(r.get("conflicts"), {"project_id": int, "project": str, "message": str})
        pins = _clean_list(r.get("pins"), {"project_id": int, "project": str, "version": str})
        if conflicts:
            row["conflicts"] = conflicts
        if pins:
            row["pins"] = pins
        items.append(row)
    left = msg.get("incomplete")
    return {"rev": _short(msg.get("rev"), 64), "recall_rev": _short(msg.get("recall_rev"), 64), "items": items,
            "incomplete": [x[:120] for x in left if isinstance(x, str)][:MAX_STATE_ROWS] if isinstance(left, list) else []}


# ---------- 동기화 ----------
def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def _held_keys(db: Session, user_id: int) -> set[str]:
    keys: set[str] = set()
    for inst in db.query(models.AgentInstance).filter_by(user_id=user_id):
        for row in state_rows(inst.market):
            if row["key"]:
                keys.add(row["key"])
    return keys


def _sync_item(pkg: models.MarketPackage, v: models.MarketVersion, inst: models.MarketInstall | None = None) -> dict:
    ext = ext_of(v)
    item = {"key": key_of(pkg), "name": pkg.public_name or pkg.name, "package_id": pkg.id, "version": v.version, "sha256": v.sha256, "size": v.size,
            "kind": (ext.get("kinds") or ["skill_text"])[0], "code": is_code(v), "skills": ext.get("skills") or [],
            "summaries": ext.get("skillSummaries") or {}, "display_name": (v.manifest or {}).get("displayName") or pkg.display_name,
            "description": (v.manifest or {}).get("description") or "", "min_agent": ext.get("minAgentVersion") or "", "has_tool": False, "tools_fp": v.tools_fp}
    if inst is not None and inst.version_floor:
        item["pin_floor"] = inst.version_floor
    return item


def sync_payload(db: Session, user_id: int) -> dict:
    """이 사용자의 PC가 가져야 할 전체 목록. rev는 이 내용의 해시다."""
    cfg = get_setting(db)
    items, recalled = [], []
    pkgs: dict[int, models.MarketPackage] = {}
    installs = db.query(models.MarketInstall).filter_by(user_id=user_id).all()
    by_pkg = {i.package_id: i for i in installs}
    for i in installs:
        p = db.get(models.MarketPackage, i.package_id)
        if p:
            pkgs[p.id] = p
    held = _held_keys(db, user_id)
    if held:  # 설치를 지운 뒤에 회수되어도 PC에 남은 복사본을 지워야 한다
        for p in db.query(models.MarketPackage).all():
            if key_of(p) in held:
                pkgs.setdefault(p.id, p)
    for p in sorted(pkgs.values(), key=key_of):
        vs = versions_of(db, p.id)
        inst = by_pkg.get(p.id)
        chosen = resolve(vs, p, user_id, inst)[0] if inst and inst.enabled else None
        if chosen and cfg.enabled:
            items.append(_sync_item(p, chosen, inst))
        gone = [v.version for v in vs if v.status == "withdrawn"]
        if gone:
            last = next(v for v in vs if v.status == "withdrawn")
            repl = db.get(models.MarketPackage, last.replacement_id) if last.replacement_id else None
            recalled.append({"key": key_of(p), "name": p.public_name or p.name, "display_name": p.display_name, "versions": gone,
                             "active": bool(chosen), "replacement": (repl.public_name or repl.name) if repl else None})
    body = {"enabled": bool(cfg.enabled), "gateway_on": False, "items": items, "recalled": recalled}
    return {"type": "market.sync", "rev": _hash(body), "recall_rev": _hash(recalled), **body}


def push_sync(user_ids) -> None:
    """연결된 PC에 지금 맞출 목록을 보낸다. 꺼진 PC는 연결될 때 받는다. 한 사람의 실패가 나머지 PC의 회수를 막으면 안 된다."""
    for uid in set(user_ids):
        conn = registry.get(uid)
        if not conn or not conn.info.get("market_api"):
            continue
        try:
            with SessionLocal() as db:
                conn.send(sync_payload(db, uid))
        except AgentError as e:
            log.info("마켓 목록을 보내지 못했습니다(다음 연결 때 보냅니다): %s", e)
        except Exception:
            log.exception("마켓 목록을 만들지 못했습니다: 사용자 %s", uid)


def recipients(db: Session, pkg: models.MarketPackage) -> set[int]:
    users = {i.user_id for i in db.query(models.MarketInstall).filter_by(package_id=pkg.id)}
    users.add(pkg.owner_id)
    key = key_of(pkg)
    for inst in db.query(models.AgentInstance).all():
        if any(r["key"] == key for r in state_rows(inst.market)):
            users.add(inst.user_id)
    return users


def readers(db: Session, pkg: models.MarketPackage) -> set[int]:
    """AI가 이 항목을 읽은 대화의 사용자. 지금은 설치를 지웠어도 그 결과로 보고서를 냈을 수 있다."""
    return {r.user_id for r in db.query(models.MarketCall).filter_by(package_id=pkg.id, kind="skill_read") if r.user_id}


def version_counts(db: Session) -> dict[tuple[str, str], int]:
    """(항목, 버전)별로 지금 그 버전이 설치된 PC 수. PC가 알린 값이라 꺼진 PC는 마지막으로 알린 값이다."""
    out: dict[tuple[str, str], int] = {}
    for inst in db.query(models.AgentInstance).all():
        for r in state_rows(inst.market):
            if r.get("state") in ("installed", "conflict") and r.get("version"):
                k = (r["key"], r["version"])
                out[k] = out.get(k, 0) + 1
    return out


def on_hello(conn, msg: dict):
    """연결할 때마다 목록을 맞춘다. 옛 Agent(market_api 없음)에는 보내지 않는다."""
    if not msg.get("market_api"):
        return
    with SessionLocal() as db:
        conn.send(sync_payload(db, conn.user_id))


def on_state(conn, msg: dict):
    host = conn.info.get("hostname") or "unknown"
    state = clean_state(msg)
    # 회수를 받았는지는 턴 시작 때 본다. DB를 다시 읽지 않게 연결에도 둔다
    if state["recall_rev"] is not None:
        conn.info["market_recall_rev"] = state["recall_rev"]
    if state["rev"] is not None:
        conn.info["market_rev"] = state["rev"]
    with SessionLocal() as db:
        inst = db.query(models.AgentInstance).filter_by(user_id=conn.user_id, hostname=host).first()
        if inst:
            inst.market = {**state, "at": datetime.now().isoformat(timespec="seconds")}
            db.commit()
    manager.publish({"type": "market_updated", "user_id": conn.user_id})


def recall_block(user_id: int, conn, wait_sec: float = RECALL_WAIT_SEC) -> str | None:
    """이 사용자에게 회수가 있고 PC Agent가 받았다고 답하지 않았으면 턴을 막을 문구를 돌려준다. 그 밖에는 None."""
    with SessionLocal() as db:
        payload = sync_payload(db, user_id)
    if not payload["recalled"]:
        return None
    want = payload["recall_rev"]
    if conn.info.get("market_recall_rev") == want:
        return None
    try:
        conn.send(payload)
    except AgentError:
        return None  # 연결이 없으면 다시 연결될 때 이 문을 다시 지난다
    end = time.monotonic() + wait_sec
    while time.monotonic() < end:
        if conn.closed:
            return None
        if conn.info.get("market_recall_rev") == want:
            return None
        time.sleep(0.2)
    return "PC Agent가 관리부서의 회수 조치를 받지 못했습니다 → 트레이에서 Agent를 다시 시작한 뒤 같은 요청을 다시 보내 주세요"


# ---------- 사람에게 알리기 ----------
def notify(db: Session, users, pkg: models.MarketPackage, version: str | None, kind: str, text: str, skip: int | None = None):
    for uid in set(users):
        if uid != skip:
            db.add(models.MarketNotice(user_id=uid, package_id=pkg.id, version=version, kind=kind, text=text[:2000]))


def publish_updated(user_ids):
    for uid in set(user_ids):
        manager.publish({"type": "market_updated", "user_id": uid})


def admins(db: Session) -> list[int]:
    return [u.id for u in db.query(models.User).filter(models.User.is_admin.is_(True), models.User.is_active.is_(True))]


def recall_notes(user_id: int, project_id: int, chat_id: int) -> list[str]:
    """이 대화에 알릴 회수. AI에게만 말하면 엔지니어는 어제 낸 결과가 회수된 스킬에 기댔다는 것을 모른다.
    회수 전에 시작한 대화이거나 이 프로젝트에서 AI가 읽은 항목만, 대화마다 한 번 알린다."""
    out: list[str] = []
    with SessionLocal() as db:
        chat = db.get(models.ChatSession, chat_id)
        pkg_ids = {i.package_id for i in db.query(models.MarketInstall).filter_by(user_id=user_id)}
        read_here = {r.package_id for r in db.query(models.MarketCall).filter_by(project_id=project_id, kind="skill_read") if r.package_id}
        for pid in pkg_ids | read_here:
            pkg = db.get(models.MarketPackage, pid)
            if not pkg:
                continue
            for v in versions_of(db, pid):
                if v.status != "withdrawn" or not v.reviewed_at:
                    continue
                started_before = chat is not None and chat.created_at is not None and chat.created_at < v.reviewed_at
                if pid not in read_here and not started_before:
                    continue
                if db.query(models.MarketCall).filter_by(chat_id=chat_id, package_id=pid, version=v.version, kind="recall_note").first():
                    continue
                db.add(models.MarketCall(user_id=user_id, project_id=project_id, chat_id=chat_id, package_id=pid, version=v.version, tool=pkg.name, kind="recall_note"))
                out.append(recall_text(pkg, v, db))
        db.commit()
    return out


def recall_text(pkg: models.MarketPackage, v: models.MarketVersion, db: Session) -> str:
    repl = db.get(models.MarketPackage, v.replacement_id) if v.replacement_id else None
    text = f"관리부서가 '{pkg.display_name}' {v.version}을(를) 회수했습니다. 사유: {v.recall_reason} → 이 스킬로 낸 결과와 보고서를 다시 확인하세요."
    return text + (f" 대체 항목: {repl.public_name or repl.name}" if repl else "")


# ---------- 읽음 기록 ----------
def _pc_version(db: Session, user_id: int, hostname: str | None, project_id: int | None, key: str) -> str | None:
    """대화가 실제로 읽은 버전. 코드 항목은 프로젝트에서 옛 버전을 쓰는 동안 서버가 고른 버전과 다르다."""
    inst = db.query(models.AgentInstance).filter_by(user_id=user_id, hostname=hostname).first() if hostname else None
    row = next((r for r in state_rows(inst.market) if r["key"] == key), None) if inst else None
    for pin in (row or {}).get("pins") or []:
        if pin.get("project_id") == project_id and pin.get("version"):
            return pin["version"]
    return None


def note_skill_read(user_id: int, hostname: str | None, project_id: int, chat_id: int, update: dict, seen: set[str]) -> None:
    """AI가 설치한 스킬의 SKILL.md를 읽은 흔적을 남긴다. 인자 내용은 저장하지 않고 항목과 버전만 남긴다.
    CLI마다 경로를 주는 칸이 달라 찾지 못할 수 있으니 참고 기록이다."""
    probe = [update.get("title") or ""]
    for key in ("rawInput", "locations"):
        if update.get(key):
            probe.append(json.dumps(update[key], ensure_ascii=False, default=str))
    names = {m for text in probe for m in _SKILL_PATH.findall(text)} - BASE_SKILLS - seen
    if not names:
        return
    with SessionLocal() as db:
        for inst in db.query(models.MarketInstall).filter_by(user_id=user_id, enabled=True):
            pkg = db.get(models.MarketPackage, inst.package_id)
            if not pkg:
                continue
            versions = versions_of(db, pkg.id)
            chosen = resolve(versions, pkg, user_id, inst)[0]
            for skill in names & set(ext_of(chosen).get("skills") or []):
                seen.add(skill)
                version = _pc_version(db, user_id, hostname, project_id, key_of(pkg)) or chosen.version
                db.add(models.MarketCall(user_id=user_id, hostname=hostname, project_id=project_id, chat_id=chat_id,
                                         package_id=pkg.id, version=version, tool=skill, kind="skill_read"))
        db.commit()


def used_in_project(db: Session, project_id: int) -> list[dict]:
    """보고서 자료. 회수된 버전은 표시해 AI가 보고서에 적게 한다: 그 결과를 믿어도 되는지 읽는 사람이 판단해야 한다."""
    rows = db.query(models.MarketCall).filter_by(project_id=project_id, kind="skill_read").order_by(models.MarketCall.id).all()
    out: dict[tuple, dict] = {}
    for r in rows:
        pkg = db.get(models.MarketPackage, r.package_id) if r.package_id else None
        if pkg:
            item = {"name": pkg.public_name or pkg.name, "display_name": pkg.display_name, "version": r.version}
            v = db.query(models.MarketVersion).filter_by(package_id=pkg.id, version=r.version).first()
            if v is not None and v.status == "withdrawn":
                item["recalled"] = True
                item["recall_reason"] = v.recall_reason
            out[(pkg.id, r.version)] = item
    return list(out.values())


# ---------- 회수 현황 ----------
def coverage(db: Session, pkg: models.MarketPackage) -> dict:
    """PC별 회수 적용 상태. PC가 알린 값과 지금 보내야 할 값이 같으면 적용된 것이다.
    마켓을 모르는 Agent는 받은 적이 없으니 지울 것도 없다. 적용 중이라고 하면 관리자가 오지 않을 답을 기다린다."""
    users = recipients(db, pkg)
    rows = []
    for uid in sorted(users):
        want = sync_payload(db, uid)["recall_rev"]
        user = db.get(models.User, uid)
        conn = registry.get(uid)
        for inst in db.query(models.AgentInstance).filter_by(user_id=uid):
            state = inst.market if isinstance(inst.market, dict) else {}
            live = conn is not None and conn.info.get("hostname") == inst.hostname
            old = bool(live and not conn.info.get("market_api"))
            if inst.market is None and not live:
                continue  # 마켓을 쓴 적이 없는 PC. 지울 것이 없다
            left = state.get("incomplete")
            incomplete = [k for k in left if k == key_of(pkg)] if isinstance(left, list) else []
            applied = state.get("recall_rev") == want and not incomplete
            label = "일부 남음" if incomplete else "적용됨" if applied else "업데이트 필요" if old else "꺼져 있어 기다리는 중" if not inst.connected else "적용 중"
            rows.append({"user": user.display_name if user else str(uid), "hostname": inst.hostname, "connected": inst.connected, "applied": applied, "state": label,
                         "outdated": old and not applied, "market_api": not old and inst.market is not None, "last_seen": inst.last_seen})
    return {"rows": rows, "applied": sum(1 for r in rows if r["applied"]), "off": sum(1 for r in rows if not r["applied"] and not r["connected"] and not r["outdated"]),
            "pending": sum(1 for r in rows if not r["applied"] and r["connected"] and not r["outdated"]), "outdated": sum(1 for r in rows if r["outdated"])}


def event(db: Session, user: models.User | None, kind: str, pkg: models.MarketPackage | None = None, version: str | None = None, **detail):
    db.add(models.MarketEvent(actor_id=user.id if user else None, via=getattr(user, "auth_via", None), kind=kind,
                              package_id=pkg.id if pkg else None, version=version, detail=detail or None))
