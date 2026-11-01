"""DDS Conversa 서버: REST API + 브라우저 WebSocket(/ws) + PC Agent WebSocket(/ws/agent) + 프론트엔드 정적 파일."""
import asyncio
import json
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import log_redact, market_service, models, work_service
from .agent.hub import hub
from .agent.link import REPLACED_CODE, AgentConnection, registry
from .config import ROOT
from .database import Base, SessionLocal, engine, ensure_columns
from .deps import SCOPE_DEVTEST, effective_admin, user_from_token
from .routers import agents, auth, devtest, flows, insights, market, projects, reports, sessions, studies, training, works
from .study import dataset, service, table_job
from .ws import manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dds")
log_redact.install()


def _backfill_project_uids():
    """uid 컬럼이 생기기 전에 만든 프로젝트에 uid를 채운다."""
    import uuid

    with SessionLocal() as db:
        for p in db.query(models.Project).filter(models.Project.uid.is_(None)):
            p.uid = uuid.uuid4().hex
        db.commit()


def _backfill_model_versions():
    """버전이 생기기 전의 모델: 스터디마다 번호를 매기고, 여러 개 배포되어 있으면 가장 최근 것만 현재 버전으로 둔다."""
    with SessionLocal() as db:
        by_study: dict[int, list] = {}
        for m in db.query(models.SurrogateModel).order_by(models.SurrogateModel.id):
            by_study.setdefault(m.study_id, []).append(m)
        for study_id, rows in by_study.items():
            used = max((m.version or 0 for m in rows), default=0)
            live = next((m for m in reversed(rows) if m.deployed), None)
            study = db.get(models.Study, study_id)
            project = db.get(models.Project, study.project_id) if study else None
            for m in rows:
                if m.version is None:
                    used += 1
                    m.version = used
                if m.created_by is None and project is not None:
                    m.created_by = project.owner_id
                if m.deployed and m is not live:
                    m.deployed = False
            if live is not None and live.deployed_at is None:
                live.deployed_at = live.created_at
        db.commit()


def _recover_stranded_flows():
    """예전 서버에서 승인만 되고 에이전트에게 전해지지 않아 멈춘 흐름을 승인 대기로 되돌린다. 며칠 뒤 저절로 돌면 안 되니 다시 승인을 받는다.
    전하던 중인 대화가 지워진 승인도 같다. 전할 곳이 없다."""
    with SessionLocal() as db:
        for f in db.query(models.Flow).filter(models.Flow.status == "approved"):
            started = any(n.get("status") not in (None, "pending") for n in f.nodes or [])
            if started or (f.handoff and f.chat_id):
                continue
            f.status, f.approved_by, f.handoff = "review", None, None
            f.version += 1
        db.commit()


def _on_agent_hello(conn: AgentConnection, msg: dict):
    try:
        hub.on_hello(conn, msg)
        if "active_cases" in msg:  # 옛 Agent는 보내지 않는다. 없다고 모두 끝난 것으로 치면 돌고 있는 해석을 지운다
            service.reconcile_cases(conn, msg["active_cases"], can_settle=bool(msg.get("settles_cases")))
    finally:  # 대화 상태를 먼저 맞춘다. 아래가 알리는 '연결됨'을 보고 화면이 다시 읽을 때 되살린 턴이 있어야 한다
        agents.record_instance(conn, msg)
        market_service.on_hello(conn, msg)


def _after_link_lost(conn: AgentConnection):
    """끊김 정리. 대화 상태를 먼저 맞춘다: '끊김' 방송을 보고 화면이 다시 읽을 때 reconnecting이어야 한다."""
    hub.on_link_lost(conn.user_id)
    manager.publish({"type": "agent_status", "user_id": conn.user_id, "connected": False})
    agents.mark_disconnected(conn)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    ensure_columns()
    work_service.migrate_works()
    _backfill_project_uids()
    _backfill_model_versions()
    _recover_stranded_flows()
    loop = asyncio.get_running_loop()
    manager.bind_loop(loop)

    def quiet_resets(lp, ctx):  # 브라우저 탭을 닫을 때 Windows가 내는 연결 끊김 오류는 무시
        if isinstance(ctx.get("exception"), ConnectionResetError):
            return
        lp.default_exception_handler(ctx)
    loop.set_exception_handler(quiet_resets)
    registry.on("case.status", lambda conn, m: service.on_case_status(m, conn.user_id))
    registry.on("case.train_sample", lambda conn, m: dataset.on_sample_status(m))
    registry.on("hello", _on_agent_hello)
    registry.on("heartbeat", agents.record_instance)
    table_job.resume_after_restart()
    registry.on("market.state", market_service.on_state)
    with SessionLocal() as db:  # 서버 재시작 시 연결 상태 초기화
        db.query(models.AgentInstance).update({models.AgentInstance.connected: False})
        db.commit()
    yield


app = FastAPI(title="DDS Conversa", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc: RequestValidationError):
    # 기본 처리는 잘못된 입력값을 응답에 되돌려 주는데, 그 값이 JSON의 NaN이면 응답을 만들다 500이 된다
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder([{k: e[k] for k in ("type", "loc", "msg") if k in e} for e in exc.errors()])})


@app.exception_handler(service.ModelFileError)
async def model_file_error(request, exc: service.ModelFileError):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def unexpected_error(request, exc: Exception):
    # 맨 "Internal Server Error"는 화면이 원인도 할 일도 보여 주지 못한다. 자세한 내용은 서버 로그에 남는다
    return JSONResponse(status_code=500, media_type="application/json; charset=utf-8",
                        content={"detail": f"서버에서 처리하지 못했습니다({type(exc).__name__}) → 잠시 뒤 다시 해 보고, 계속되면 관리자에게 서버 로그를 확인해 달라고 하세요."})


@app.middleware("http")
async def limit_market_upload(request, call_next):
    # 본문을 받기 전에 크기를 본다. 엔드포인트에 닿을 때는 이미 다 받은 뒤라, 결과 폴더를 잘못 고른 한 번이 공유 서버의 디스크와 메모리를 쓴다
    if request.url.path == "/api/market/submissions" and request.method == "POST":
        size = request.headers.get("content-length")
        if size is None or not size.isdigit():
            return JSONResponse({"detail": "올린 크기를 알 수 없습니다 → 웹앱의 등록 화면이나 scripts/market_publish.py로 올려 주세요"}, status_code=411)
        if int(size) > market.MAX_UPLOAD_BODY:
            return JSONResponse({"detail": f"너무 큽니다({int(size) // (1024 * 1024)}MB) → 해석 결과·모델 파일은 빼고 스크립트와 문서만 올려 주세요"}, status_code=413)
    return await call_next(request)


@app.middleware("http")
async def json_charset(request, call_next):
    # Windows PowerShell 5.1(Invoke-RestMethod)은 charset이 없으면 JSON 한글을 잘못 디코딩한다
    resp = await call_next(request)
    if resp.headers.get("content-type") == "application/json":
        resp.headers["content-type"] = "application/json; charset=utf-8"
    return resp
for r in (auth.router, projects.router, works.router, sessions.router, flows.router, reports.router, studies.router, insights.router, training.router, agents.router, devtest.router, market.router):
    app.include_router(r)


@app.api_route("/api", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
@app.api_route("/api/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
def no_such_api(request: Request):
    """없는 API는 JSON 404다. 화면 경로로 넘기면 index.html이 200으로 가서 받는 쪽이 'Expecting value...'만 보게 된다.
    실제로 PC Agent(MCP)가 서버보다 새 버전이라 없는 주소를 부를 때 그랬다."""
    return JSONResponse(status_code=404, content={"detail": f"서버에 없는 API입니다({request.method} {request.url.path}) → 서버가 PC Agent보다 옛 버전일 수 있습니다. 서버를 최신 코드로 다시 시작하세요."})


def _auth_ws(token: str | None) -> models.User | None:
    with SessionLocal() as db:
        user = user_from_token(token, db)
    # 원격 검증 토큰은 /api/devtest만 쓴다. Agent로 붙으면 진짜 Agent를 밀어내고 사용자의 요청을 받게 된다
    return None if user and user.auth_via == SCOPE_DEVTEST else user


@app.websocket("/ws")
async def ws_browser(ws: WebSocket, token: str | None = None):
    user = _auth_ws(token)
    if not user:
        await ws.close(code=4401)
        return
    await manager.connect(ws, user.id, effective_admin(user))
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
    registry.add(conn)  # 턴과 세션은 건드리지 않는다. hello가 오면 hub가 Agent가 아는 것과 맞춘다
    log.info("PC Agent 연결: %s", user.username)
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
                # 밀려난 연결: 새 Agent(boot가 있음)는 닫아서 대기시킨다. 0.2.16까지의 Agent는 4000을 모르고 곧바로 다시 붙어
                # 서로 밀어내므로 예전처럼 소켓을 열어 두고 메시지(해석 결과 등)만 받는다
                if registry.get(user.id) is not conn and (conn.boot or msg.get("boot")):
                    await ws.close(code=REPLACED_CODE, reason="replaced")
                    break
                # 핸들러는 DB 작업을 하므로 이벤트 루프를 막지 않게 스레드에서 처리
                await asyncio.to_thread(registry.dispatch, conn, msg)
            except Exception:  # 메시지 하나의 예외로 연결을 끊으면 그 사용자의 모든 대화가 흔들린다
                log.exception("Agent 메시지 처리 실패: %s", raw[:300])
    except (WebSocketDisconnect, RuntimeError):  # RuntimeError: 이미 닫힌 소켓을 읽었다(밀려나 닫힌 연결)
        pass
    finally:
        current = registry.remove(conn)
        log.info("PC Agent 연결 끊김: %s%s", user.username, "" if current else " (옛 연결)")
        if current:  # 옛 연결의 뒤늦은 정리는 새 연결의 상태를 지우지도, '끊김'을 방송하지도 않는다
            # 따로 스레드에서 한다: on_link_lost는 대화 잠금을 잡는데, 잠금을 쥔 쪽이 conn.send로 이 이벤트 루프를 기다리면 서버 전체가 멈춘다.
            # run_in_executor로 기다리면 이 작업이 취소될 때 아직 시작 안 한 정리까지 함께 취소되어 끊김 표시가 사라진다
            threading.Thread(target=_after_link_lost, args=(conn,), daemon=True, name="link-lost").start()


# ---------- 프론트엔드 (빌드본) ----------
DIST = ROOT / "frontend" / "dist"


def spa_file(dist, full_path: str):
    """주소에 맞는 빌드 파일. 없거나 빌드 폴더 밖이면 index.html(화면 경로)이다.
    로그인 없이 부르는 경로라, 밖으로 나가면 DB와 비밀 키까지 그대로 내주게 된다."""
    index = dist / "index.html"
    if not full_path:
        return index
    try:
        f = (dist / full_path).resolve()
        return f if dist.resolve() in f.parents and f.is_file() else index
    except (OSError, ValueError):  # 경로에 쓸 수 없는 글자(널 문자 등)
        return index


if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        return FileResponse(spa_file(DIST, full_path))
