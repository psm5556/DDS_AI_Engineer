"""DDS Conversa 서버: REST API + 브라우저 WebSocket(/ws) + PC Agent WebSocket(/ws/agent) + 프론트엔드 정적 파일."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import models
from .agent.hub import hub
from .agent.link import AgentConnection, registry
from .config import ROOT
from .database import Base, SessionLocal, engine, ensure_columns
from .deps import user_from_token
from .routers import agents, auth, flows, projects, reports, sessions, studies
from .study import service
from .ws import manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dds")


def _backfill_project_uids():
    """uid 컬럼이 생기기 전에 만든 프로젝트에 uid를 채운다."""
    import uuid

    with SessionLocal() as db:
        for p in db.query(models.Project).filter(models.Project.uid.is_(None)):
            p.uid = uuid.uuid4().hex
        db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    ensure_columns()
    _backfill_project_uids()
    loop = asyncio.get_running_loop()
    manager.bind_loop(loop)

    def quiet_resets(lp, ctx):  # 브라우저 탭을 닫을 때 Windows가 내는 연결 끊김 오류는 무시
        if isinstance(ctx.get("exception"), ConnectionResetError):
            return
        lp.default_exception_handler(ctx)
    loop.set_exception_handler(quiet_resets)
    registry.on("case.status", lambda conn, m: service.on_case_status(m))
    registry.on("hello", agents.record_instance)
    registry.on("heartbeat", agents.record_instance)
    with SessionLocal() as db:  # 서버 재시작 시 연결 상태 초기화
        db.query(models.AgentInstance).update({models.AgentInstance.connected: False})
        db.commit()
    yield


app = FastAPI(title="DDS Conversa", lifespan=lifespan)


@app.middleware("http")
async def json_charset(request, call_next):
    # Windows PowerShell 5.1(Invoke-RestMethod)은 charset이 없으면 JSON 한글을 잘못 디코딩한다
    resp = await call_next(request)
    if resp.headers.get("content-type") == "application/json":
        resp.headers["content-type"] = "application/json; charset=utf-8"
    return resp
for r in (auth.router, projects.router, sessions.router, flows.router, reports.router, studies.router, agents.router):
    app.include_router(r)


def _auth_ws(token: str | None) -> models.User | None:
    with SessionLocal() as db:
        return user_from_token(token, db)


@app.websocket("/ws")
async def ws_browser(ws: WebSocket, token: str | None = None):
    user = _auth_ws(token)
    if not user:
        await ws.close(code=4401)
        return
    await manager.connect(ws, user.id, user.is_admin)
    try:
        while True:
            await ws.receive_text()  # 클라이언트 ping
    except WebSocketDisconnect:
        manager.disconnect(ws)


@app.websocket("/ws/agent")
async def ws_agent(ws: WebSocket, token: str | None = None):
    user = _auth_ws(token)
    await ws.accept()
    if not user:
        await ws.close(code=4401, reason="invalid token")  # Agent가 '토큰 오류'로 구분할 수 있게 accept 후 닫는다
        return
    conn = AgentConnection(ws, user.id, asyncio.get_running_loop())
    registry.add(conn)
    hub.forget_user(user.id)
    log.info("PC Agent 연결: %s", user.username)
    try:
        while True:
            msg = json.loads(await ws.receive_text())
            # 핸들러는 DB 작업을 하므로 이벤트 루프를 막지 않게 스레드에서 처리
            await asyncio.to_thread(registry.dispatch, conn, msg)
    except WebSocketDisconnect:
        pass
    finally:
        registry.remove(conn)
        await asyncio.to_thread(agents.mark_disconnected, conn)
        hub.forget_user(user.id)
        log.info("PC Agent 연결 끊김: %s", user.username)
        manager.publish({"type": "agent_status", "user_id": user.id, "connected": False})


# ---------- 프론트엔드 (빌드본) ----------
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        f = DIST / full_path
        return FileResponse(f if full_path and f.is_file() else DIST / "index.html")
