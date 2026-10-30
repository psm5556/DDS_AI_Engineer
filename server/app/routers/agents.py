"""PC Agent 배포(버전 관리)와 모니터링.
- 관리자: 릴리스 업로드(zip) / 최신 지정, 전체 Agent 현황, 원격 업데이트 지시
- 사용자·Agent: 최신 버전 조회, 번들 다운로드(API 토큰), 내 Agent 현황
"""
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from .. import models, schemas
from ..agent.link import AgentConnection, registry
from ..config import ROOT, settings
from ..database import SessionLocal, get_db
from ..deps import get_current_user, require_admin
from ..ws import manager

router = APIRouter(prefix="/api/agent", tags=["agent-releases"])
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
_build_lock = threading.Lock()  # 설치 프로그램 자동 빌드는 한 번에 하나만


def version_key(v: str | None) -> tuple:
    try:
        return tuple(int(x) for x in (v or "0.0.0").split("."))
    except ValueError:
        return (0, 0, 0)


def releases_dir():
    d = settings.data_dir / "agent_releases"
    d.mkdir(parents=True, exist_ok=True)
    return d


def latest_release(db: Session) -> models.AgentRelease | None:
    return db.query(models.AgentRelease).filter_by(is_latest=True).first()


# ---------- 릴리스 ----------
@router.get("/releases", response_model=list[schemas.AgentReleaseResponse])
def list_releases(user=Depends(get_current_user), db: Session = Depends(get_db)):
    rels = db.query(models.AgentRelease).all()
    return sorted(rels, key=lambda r: version_key(r.version), reverse=True)


@router.get("/releases/latest", response_model=schemas.AgentReleaseResponse)
def get_latest(user=Depends(get_current_user), db: Session = Depends(get_db)):
    r = latest_release(db)
    if not r:
        raise HTTPException(404, "배포된 Agent 버전이 없습니다")
    return r


@router.get("/releases/{version}/download")
def download(version: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    r = latest_release(db) if version == "latest" else db.query(models.AgentRelease).filter_by(version=version).first()
    if not r:
        raise HTTPException(404, "해당 버전이 없습니다")
    return FileResponse(releases_dir() / r.filename, filename=r.filename, media_type="application/zip",
                        headers={"X-Agent-Version": r.version, "X-Agent-Sha256": r.sha256})


@router.post("/releases", response_model=schemas.AgentReleaseResponse)
async def upload_release(file: UploadFile = File(...), version: str = Form(...), notes: str = Form(""),
                         make_latest: bool = Form(True), admin=Depends(require_admin), db: Session = Depends(get_db)):
    data = await file.read()
    return _register_release(db, data, version, notes, admin.id, make_latest)


@router.post("/releases/from-server", response_model=schemas.AgentReleaseResponse)
def release_from_server(admin=Depends(require_admin), db: Session = Depends(get_db)):
    """서버에 있는 코드로 Agent 번들을 만들어 최신 버전으로 등록한다(첫 배포를 웹에서 한 번에)."""
    spec = importlib.util.spec_from_file_location("build_agent_bundle", ROOT / "scripts" / "build_agent_bundle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ver = mod.version()
    bundle = mod.build(ver)
    return _register_release(db, bundle.read_bytes(), ver, "서버 코드에서 만든 버전", admin.id, True)


def _register_release(db: Session, data: bytes, version: str, notes: str, uploaded_by: int, make_latest: bool):
    if not VERSION_RE.match(version):
        raise HTTPException(400, "버전은 1.2.3 형식이어야 합니다")
    if db.query(models.AgentRelease).filter_by(version=version).first():
        raise HTTPException(409, f"이미 있는 버전입니다: {version}")
    if not data.startswith(b"PK"):
        raise HTTPException(400, "zip 파일이 아닙니다")
    filename = f"dds-agent-{version}.zip"
    (releases_dir() / filename).write_bytes(data)
    r = models.AgentRelease(version=version, filename=filename, sha256=hashlib.sha256(data).hexdigest(),
                            size=len(data), notes=notes, uploaded_by=uploaded_by)
    db.add(r)
    db.flush()
    if make_latest:
        _set_latest(db, r)
    db.commit()
    return r


@router.put("/releases/{version}/latest", response_model=schemas.AgentReleaseResponse)
def set_latest(version: str, admin=Depends(require_admin), db: Session = Depends(get_db)):
    """최신 지정(롤백도 이 경로: 이전 버전을 최신으로 지정)."""
    r = db.query(models.AgentRelease).filter_by(version=version).first()
    if not r:
        raise HTTPException(404, "해당 버전이 없습니다")
    _set_latest(db, r)
    db.commit()
    return r


def _set_latest(db: Session, r: models.AgentRelease):
    db.query(models.AgentRelease).update({models.AgentRelease.is_latest: False})
    r.is_latest = True
    db.flush()
    payload = {"type": "agent.release", "version": r.version, "sha256": r.sha256}
    for conn in list(registry.conns.values()):
        try:
            conn.send(payload)  # 연결된 Agent에 새 버전 알림 → Agent가 유휴 시 자동 업데이트
        except Exception:
            pass
    manager.publish({"type": "agent_release", "data": {"version": r.version}})


# ---------- 모니터링 ----------
def _instance_rows(db: Session, user_id: int | None = None) -> list[dict]:
    latest = latest_release(db)
    q = db.query(models.AgentInstance)
    if user_id is not None:
        q = q.filter_by(user_id=user_id)
    rows = []
    for inst in q.order_by(models.AgentInstance.connected.desc(), models.AgentInstance.last_seen.desc()).all():
        u = db.get(models.User, inst.user_id)
        d = schemas.AgentInstanceResponse.model_validate(inst).model_dump()
        d["user_display_name"] = u.display_name if u else None
        d["outdated"] = bool(latest and version_key(inst.version) < version_key(latest.version))
        rows.append(d)
    return rows


@router.get("/instances", response_model=list[schemas.AgentInstanceResponse])
def list_instances(admin=Depends(require_admin), db: Session = Depends(get_db)):
    return _instance_rows(db)


@router.get("/instances/mine", response_model=list[schemas.AgentInstanceResponse])
def my_instances(user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _instance_rows(db, user.id)


@router.post("/instances/{user_id}/update")
def request_update(user_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """해당 사용자의 Agent에 지금 업데이트하도록 지시(본인 또는 관리자)."""
    if user_id != user.id and not user.is_admin:
        raise HTTPException(403, "본인 또는 관리자만 할 수 있습니다")
    conn = registry.get(user_id)
    if not conn:
        raise HTTPException(409, "Agent가 연결되어 있지 않습니다")
    r = latest_release(db)
    if not r:
        raise HTTPException(404, "배포된 Agent 버전이 없습니다")
    conn.send({"type": "agent.update", "version": r.version, "sha256": r.sha256, "force": True})
    return {"ok": True, "version": r.version}


def _auto_update_on(user: models.User) -> bool:
    return user.agent_auto_update is not False


@router.get("/settings", response_model=schemas.AgentSettings)
def get_agent_settings(user=Depends(get_current_user)):
    return {"auto_update": _auto_update_on(user)}


@router.put("/settings", response_model=schemas.AgentSettings)
def set_agent_settings(body: schemas.AgentSettings, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """저장은 서버에 한다: PC가 꺼져 있어도 바꿀 수 있고, 연결할 때 다시 알린다."""
    db.get(models.User, user.id).agent_auto_update = body.auto_update
    db.commit()
    conn = registry.get(user.id)
    if conn:
        conn.send({"type": "agent.settings", "auto_update": body.auto_update})
    return body


# ---------- Agent 메시지 핸들러 (main.py에서 등록) ----------
def record_instance(conn: AgentConnection, msg: dict, connected: bool = True):
    with SessionLocal() as db:
        host = msg.get("hostname") or conn.info.get("hostname") or "unknown"
        inst = db.query(models.AgentInstance).filter_by(user_id=conn.user_id, hostname=host).first()
        if not inst:
            inst = models.AgentInstance(user_id=conn.user_id, hostname=host)
            db.add(inst)
        now = datetime.now()
        if msg.get("type") == "hello":
            inst.version, inst.platform = msg.get("version"), msg.get("platform")
            inst.agents = msg.get("agents", [])
            if not inst.connected:
                inst.connected_at = now
        if "status" in msg:
            inst.status = msg["status"]
        inst.connected, inst.last_seen = connected, now
        db.commit()
        if msg.get("type") == "hello":
            conn.send({"type": "agent.settings", "auto_update": _auto_update_on(db.get(models.User, conn.user_id))})
    manager.publish({"type": "agent_status", "user_id": conn.user_id, "connected": connected})


def mark_disconnected(conn: AgentConnection):
    host = conn.info.get("hostname")
    if not host:
        return
    with SessionLocal() as db:
        inst = db.query(models.AgentInstance).filter_by(user_id=conn.user_id, hostname=host).first()
        if inst:
            inst.connected, inst.last_seen = False, datetime.now()
            db.commit()


# ---------- 설치: 포터블 런타임 · 설치 스크립트 ----------
def runtime_dir():
    return settings.data_dir / "agent_runtime"


def _manifest() -> dict:
    p = runtime_dir() / "manifest.json"
    if not p.exists():
        raise HTTPException(404, "서버에 Agent 런타임이 준비되지 않았습니다 (scripts/prepare_onsite.py 실행 — Linux 서버는 Windows PC에서 --pack으로 만든 묶음을 --import-pack으로 넣기)")
    return json.loads(p.read_text(encoding="utf-8"))


@router.get("/runtime/manifest")
def runtime_manifest(user=Depends(get_current_user)):
    return _manifest()


@router.get("/runtime/{name}")
def runtime_file(name: str, user=Depends(get_current_user)):
    m = _manifest()
    if name not in m:
        raise HTTPException(404, f"런타임 '{name}'이(가) 없습니다")
    return FileResponse(runtime_dir() / m[name]["file"], filename=m[name]["file"])


@router.get("/install.ps1", response_class=PlainTextResponse)
def install_script(request: Request):
    """원클릭 설치 스크립트. 웹앱의 설치 명령: $env:DDS_TOKEN='<토큰>'; irm <서버>/api/agent/install.ps1 | iex"""
    text = (ROOT / "scripts" / "install-agent.ps1").read_text(encoding="utf-8-sig")  # BOM 제거(iex용)
    server = str(request.base_url).rstrip("/")
    return text.replace("__DDS_SERVER_URL__", server)


def _setup_sources() -> list[Path]:
    src = ROOT / "installer" / "DdsAgentSetup.cs"
    return [src, src.with_name("dds-agent.ico"), ROOT / "frontend" / "public" / "brand" / "dds-logo.png"]


def _setup_stale(exe: Path) -> bool:
    """소스가 exe보다 새로우면 낡은 것이다. 소스가 없는 서버(묶음으로 설치)에서는 있는 exe를 그대로 쓴다."""
    srcs = [p.stat().st_mtime for p in _setup_sources() if p.exists()]
    return bool(srcs) and exe.stat().st_mtime < max(srcs)


def _setup_exe() -> Path | None:
    for cand in (runtime_dir() / "DDS-Agent-Setup.exe", ROOT / "dist" / "DDS-Agent-Setup.exe"):
        if cand.exists():
            # 코드를 받아도 예전 설치 프로그램을 계속 내려주지 않게 다시 빌드한다. 빌드할 수 없으면 있는 것을 쓴다
            return (_build_setup_exe() or cand) if _setup_stale(cand) else cand
    return _build_setup_exe()


def _build_setup_exe() -> Path | None:
    """설치 프로그램이 없거나 낡았으면(저장소에는 빌드 결과가 없다) 서버에서 바로 빌드한다.
    Windows 기본 .NET Framework의 csc.exe를 쓰므로 추가 설치가 필요 없다(scripts/build_setup_exe.ps1과 같은 명령)."""
    src, ico, logo = _setup_sources()
    csc = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not (src.exists() and csc.exists()):
        return None
    out = runtime_dir() / "DDS-Agent-Setup.exe"
    with _build_lock:
        if out.exists() and not _setup_stale(out):
            return out
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp.exe")  # 실패해도 쓰던 exe를 망가뜨리지 않는다
        try:
            r = subprocess.run([str(csc), "/nologo", "/target:winexe", "/codepage:65001", "/optimize+", f"/out:{tmp}",
                                f"/win32icon:{ico}", f"/resource:{logo},dds-logo.png",
                                "/r:System.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll", str(src)],
                               capture_output=True, timeout=180)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0 or not tmp.exists():
            return None
        tmp.replace(out)
        return out


def _runtime_ready() -> bool:
    m = runtime_dir() / "manifest.json"
    if not m.exists():
        return False
    try:
        files = [v["file"] for v in json.loads(m.read_text(encoding="utf-8")).values() if isinstance(v, dict) and v.get("file")]
    except (ValueError, OSError):
        return False
    return bool(files) and all((runtime_dir() / f).exists() for f in files)


@router.get("/setup/status")
def setup_status(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """PC Agent를 설치하는 데 필요한 것이 서버에 준비됐는지: 설치 프로그램 · 포터블 런타임 · 배포된 Agent 버전.
    (셋 다 저장소(git)에 들어 있지 않아 사내 서버에서는 따로 준비해야 한다)"""
    rel = latest_release(db)
    exe, runtime = _setup_exe() is not None, _runtime_ready()
    return {"setup_exe": exe, "runtime": runtime, "release": rel.version if rel else None, "ready": exe and runtime and bool(rel),
            "runtime_dir": str(runtime_dir()) if user.is_admin else None,
            "server_windows": os.name == "nt"}  # Linux 서버는 설치 프로그램·런타임을 Windows PC에서 만들어 넣는다


@router.get("/setup.exe")
def setup_exe(request: Request):
    """사용자용 설치 프로그램. exe 끝에 서버 주소를 붙여서 내려준다(설치 창에 미리 채워짐)."""
    exe = _setup_exe()
    if not exe:
        raise HTTPException(404, "서버에 설치 프로그램이 없습니다 (scripts/prepare_onsite.py 실행 — Linux 서버는 Windows PC에서 --pack으로 만든 묶음을 --import-pack으로 넣기)")
    server = str(request.base_url).rstrip("/")
    data = exe.read_bytes() + ("DDSCFG" + json.dumps({"server": server})).encode("utf-8")
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": 'attachment; filename="DDS-Agent-Setup.exe"'})
