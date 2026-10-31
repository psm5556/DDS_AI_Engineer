"""서버 ⇄ 사용자 PC Agent 연결(WebSocket /ws/agent).

서버 → Agent 요청 (응답 필요: {"type":"response","rid",...,"result"|"error"})
  session.new     {project_id, agent, turn_id?}        → {"session_id", "agent"}  (같은 turn_id면 세션을 또 만들지 않는다)
  session.load    {project_id, session_id, agent, turn_id?} → {"ok": bool}  (False는 "지원 안 함/세션 없음"일 때만)
  agent.install   {agent}                              → {"installed", "login"}  (npm 전역 설치)
  local.open      {project_id, path?, with_viewer?}    → {"ok"}  (내 PC 탐색기/CAE 뷰어로 열기)
  local.tree      {project_id, path?}                  → {"entries": [...]} (로컬 작업 폴더 탐색)
서버 → Agent 알림
  session.prompt  {turn_id, chat_id, session_id, agent, text, attachments, started_at}
                  (응답을 기다리지 않는다. 끝은 agent.turn_end로 온다. 끊겨도 턴이 사라지지 않게 하기 위해서다)
  session.cancel  {session_id, turn_id?}               (그 세션만 멈춘다. CLI는 그대로 둔다)
  session.force_restart {agent, turn_id?, rid?}        (사용자가 멈췄는데 답이 없을 때. CLI를 끝내고 다시 띄운다. rid가 있으면 받았다고 바로 답한다.
                  답이 없으면 반쯤 끊긴 연결로 보고 다음 hello에 다시 보낸다)
  agent.ack       {seq}                                (seq가 붙은 메시지를 처리했다. Agent가 outbox에서 지운다)
  permission.reply {request_id, option_id|null}
  case.run        {case_id, study_id, project_id, params, adapter, adapter_config, dry_run}
  case.extract    {case_id, study_id, project_id, params, results, run_dir, adapter, adapter_config, steps, names}
                  (Output 추가 — 해석 없이 추출 단계만 실행, case.status {extract: true, results: 새 값만}로 답한다)
  case.cancel     {case_ids, extract_ids}              (사용자가 중지. 대기는 시작하지 않고 실행 중이면 솔버 트리를 끝낸다.
                  case.status {status: "cancelled"}로 답한다. 추출은 case.status {extract: true, error}로 답한다)
  case.train_sample {dataset_id, case_id, study_id, project_id, run_dir, spec, spec_hash}
                  (학습 샘플을 만들어 POST /api/cases/{id}/train_sample로 올리고 case.train_sample {ok, error}로 답한다)
  agent.release   {version, sha256}                    (새 버전 배포. 자동 업데이트가 켜져 있으면 유휴 시 설치)
  agent.update    {version, sha256, force}             (지금 업데이트)
  agent.settings  {auto_update, remote_test_until, acks} (hello에 대한 답, 그리고 사용자가 설정을 바꿀 때. acks: 이 서버는 agent.ack를 보낸다)
  devtest.run     {op, script?, tool?, ...}            → 실행 결과 (개발 AI의 원격 검증, 응답 필요. routers/devtest.py)
Agent → 서버 알림
  hello           {hostname, version, agents:[{name,label,installed,login}], default_agent, tools,
                   boot, live_sessions:[sid], active_turns:[{turn_id, chat_id, session_id, agent, started_at}],
                   running_agents:[이름], start_times:{이름: 초}}
  아래 seq가 붙은 메시지(agent.*·session.stage)는 끊긴 동안 Agent가 모아 두었다가 순서대로 다시 보낸다. 서버는 (boot, seq)로 중복을 거른다.
  agent.event     {session_id, update}                 (ACP session/update)
  agent.permission {session_id, request_id, params}    (ACP session/request_permission. 답이 없으면 재연결 때 다시 온다)
  agent.turn_end  {turn_id, chat_id, session_id, stop_reason, error?, session_gone?, dropped_events?}
  agent.runner_exit {agent, session_ids, code, stderr_tail, reason}  (CLI가 죽었다. reason=force_restart면 사용자가 끊은 것)
  agent.live      {live_sessions, running_agents, start_times}       (미리 띄우기 등으로 살아 있는 세션이 바뀌었다)
  session.stage   {turn_id, agent, stage, typical_start_sec}
                  (context_sync · cli_start · initialize · session_new · session_load · attachments · thinking)
  case.status     {case_id, status, results?, error?, run_dir?, images?}
  case.train_sample {dataset_id, case_id, ok, error}
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
import time
from concurrent.futures import Future
from typing import Any, Callable

from fastapi import WebSocket
from sqlalchemy.exc import OperationalError

log = logging.getLogger(__name__)
REPLACED_CODE = 4000  # 같은 사용자의 새 연결이 들어왔다. Agent가 이 코드를 받으면 곧바로 다시 붙지 않고 기다린다(서로 밀어내지 않게)
DB_RETRY_SEC = 120.0  # DB가 잠겨도 메시지를 버리지 않고 기다리는 시간. Agent 쪽 outbox는 ack를 받으면 지우므로 여기서 버리면 다시 받을 수 없다
DB_RETRY_POLL_SEC = 0.5


class AgentError(Exception):
    pass


class LinkLost(AgentError):
    """PC Agent와의 연결이 끊겼다. 다시 연결되면 이어 갈 수 있다(CLI 쪽 실패와 구분한다)."""


class AgentTimeout(AgentError, TimeoutError):
    """정해 둔 시간 안에 답이 없다. 그냥 두면 str()이 빈 TimeoutError가 500이 되어 원인이 사라진다."""


_rids = itertools.count(1)  # 서버 전체에서 유일: 재연결 뒤 옛 요청의 응답이 새 요청을 완료시키지 않게 한다


class AgentConnection:
    def __init__(self, ws: WebSocket, user_id: int, loop: asyncio.AbstractEventLoop):
        self.ws, self.user_id, self.loop = ws, user_id, loop
        self.info: dict[str, Any] = {}
        self.boot: str | None = None  # Agent 프로세스 표시. 없으면 응답 대기형 session.prompt를 쓰는 옛 Agent다
        self.ready = threading.Event()  # hello를 처리해 살아 있는 세션 목록을 안다
        self.closed = False
        self._pending: dict[int, Future] = {}

    # 어느 스레드에서든 호출 가능
    def send(self, msg: dict):
        if self.closed:
            raise LinkLost("PC Agent 연결이 끊어졌습니다")
        text = json.dumps(msg, ensure_ascii=False, default=str)
        try:
            fut = asyncio.run_coroutine_threadsafe(self.ws.send_text(text), self.loop)
        except RuntimeError as e:  # 이벤트 루프가 닫혔다
            raise LinkLost("PC Agent 연결이 끊어졌습니다") from e
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                fut.result(timeout=30)  # 동기 스레드에서는 전송 완료까지 대기
            except Exception as e:  # 보낼 수 없으면 원인이 무엇이든 연결 문제다. 빈 오류로 사라지지 않게 바꾼다
                raise LinkLost("PC Agent 연결이 끊어졌습니다") from e

    def request(self, type_: str, payload: dict, timeout: float | None = 120) -> Any:
        rid = next(_rids)
        fut: Future = Future()
        self._pending[rid] = fut
        try:
            self.send({"type": type_, "rid": rid, **payload})
            return fut.result(timeout=timeout)
        except TimeoutError:
            raise AgentTimeout(f"PC Agent가 {timeout:.0f}초 안에 답하지 않았습니다 → PC가 바쁘거나 Agent가 멈췄을 수 있습니다. "
                             "잠시 후 다시 시도하세요. 계속되면 트레이 아이콘에서 Agent를 다시 시작하세요.") from None
        finally:
            self._pending.pop(rid, None)

    def resolve(self, msg: dict):
        fut = self._pending.get(msg.get("rid"))
        if fut and not fut.done():
            if "error" in msg:  # 빈 글자도 오류다. 파이썬의 시간 초과 예외는 str()이 빈 글자라 성공으로 잘못 읽었다
                fut.set_exception(AgentError(msg["error"] or "PC Agent가 원인 없는 오류를 돌려주었습니다"))
            else:
                fut.set_result(msg.get("result"))

    def close(self):
        self.closed = True
        for fut in list(self._pending.values()):
            if not fut.done():
                fut.set_exception(LinkLost("PC Agent 연결이 끊어졌습니다"))

    def close_socket(self, code: int = REPLACED_CODE, reason: str = "replaced"):
        """옛 소켓을 닫는다. 닫지 않으면 옛 Agent가 연결된 줄 알고 남아 있다가 나중에 끊기며 새 연결의 상태를 건드린다."""
        try:
            asyncio.run_coroutine_threadsafe(self.ws.close(code=code, reason=reason), self.loop)
        except RuntimeError as e:
            log.debug("옛 연결을 닫지 못했습니다: %s", e)


class RemoteRunner:
    """서버 쪽에서 본 에이전트 러너. 실제 AI CLI는 사용자 PC Agent가 구동한다."""

    def __init__(self, conn: AgentConnection):
        self.conn = conn

    # CLI 시작은 사내 보안 검사로 몇 분이 걸릴 수 있어 서버는 시간 제한을 두지 않는다. 한계는 Agent의 cli_start_timeout_sec 하나다
    def new_session(self, project_id: int, agent: str, project_name: str | None = None, project_uid: str | None = None,
                    turn_id: str | None = None) -> str:
        return self.conn.request("session.new", {"project_id": project_id, "agent": agent, "project_name": project_name,
                                                 "project_uid": project_uid, "turn_id": turn_id},
                                 timeout=None)["session_id"]

    def load_session(self, session_id: str, project_id: int, agent: str, project_name: str | None = None,
                     project_uid: str | None = None, turn_id: str | None = None) -> bool:
        """False는 "지원 안 함/세션 없음"일 때만 온다. 연결·CLI 문제는 예외로 올려 멀쩡한 세션을 새 세션으로 덮어쓰지 않는다."""
        return bool(self.conn.request("session.load", {"project_id": project_id, "session_id": session_id,
                                                       "agent": agent, "project_name": project_name,
                                                       "project_uid": project_uid, "turn_id": turn_id},
                                      timeout=None).get("ok"))

    def call(self, type_: str, payload: dict, timeout: float = 30) -> dict:
        """PC Agent에 로컬 작업 요청(폴더 열기·뷰어 실행·로컬 데이터 조회 등)."""
        return self.conn.request(type_, payload, timeout=timeout)

    def cancel(self, session_id: str, turn_id: str | None = None):
        self.conn.send({"type": "session.cancel", "session_id": session_id, "turn_id": turn_id})

    def force_restart(self, agent: str, turn_id: str | None = None):
        self.conn.send({"type": "session.force_restart", "agent": agent, "turn_id": turn_id})

    def respond_permission(self, request_id: str, option_id: str | None):
        self.conn.send({"type": "permission.reply", "request_id": request_id, "option_id": option_id})

    def install(self, agent: str) -> dict:
        return self.conn.request("agent.install", {"agent": agent}, timeout=960)


class AgentRegistry:
    def __init__(self):
        self.conns: dict[int, AgentConnection] = {}  # user_id → 연결 (사용자당 1개, 최근 연결 우선)
        self.handlers: dict[str, Callable[[AgentConnection, dict], None]] = {}
        self._seen: dict[int, tuple[Any, int]] = {}  # user_id → (boot, 처리한 마지막 seq)

    def on(self, type_: str, fn: Callable[[AgentConnection, dict], None]):
        self.handlers[type_] = fn

    def add(self, conn: AgentConnection):
        old = self.conns.get(conn.user_id)
        self.conns[conn.user_id] = conn
        if old:
            old.close()
            if old.boot:  # 0.2.16까지의 Agent는 이 코드를 모르고 1초 뒤 다시 붙어 서로 밀어낸다. 그런 소켓은 예전처럼 열어 둔다
                old.close_socket()

    def remove(self, conn: AgentConnection) -> bool:
        """지금 연결이었으면 True. 옛 연결의 뒤늦은 정리가 새 연결의 상태를 지우지 않게 호출한 쪽이 이 값을 본다."""
        current = self.conns.get(conn.user_id) is conn
        if current:
            del self.conns[conn.user_id]
        conn.close()
        return current

    def get(self, user_id: int) -> AgentConnection | None:
        return self.conns.get(user_id)

    def runner(self, user_id: int) -> RemoteRunner:
        conn = self.get(user_id)
        if not conn:
            raise AgentError("PC Agent가 연결되어 있지 않습니다. 내 PC에서 dds-agent를 실행하세요.")
        return RemoteRunner(conn)

    def dispatch(self, conn: AgentConnection, msg: dict):
        seq = msg.get("seq")
        if seq is None:
            self._handle(conn, msg)
            return
        boot, last = self._seen.get(conn.user_id, (None, 0))
        if boot == msg.get("boot") and seq <= last:  # 재연결 때 다시 온 것. 글자가 두 번 쌓이지 않게 거른다
            self._ack(conn, seq)
            return
        try:
            self._handle_waiting_db(conn, msg)
        except Exception:
            if conn.closed:  # 표시하지 않으면 다시 연결될 때 Agent가 다시 보내 이어서 처리한다
                raise
            # 끝내 못 했다. 표시하고 넘어간다: 같은 메시지를 계속 다시 받아 막히면 안 된다
            if "dropped" in self.handlers:
                self.handlers["dropped"](conn, msg)
            self._seen[conn.user_id] = (msg.get("boot"), seq)
            self._ack(conn, seq)
            raise
        self._seen[conn.user_id] = (msg.get("boot"), seq)
        self._ack(conn, seq)

    def _handle_waiting_db(self, conn: AgentConnection, msg: dict):
        """DB 잠김은 금방 풀린다. 바로 ack하면 Agent가 지워 버려 턴 끝이 사라지고 대화가 '진행 중'에 갇힌다."""
        end = time.monotonic() + DB_RETRY_SEC
        while True:
            try:
                return self._handle(conn, msg)
            except OperationalError as e:
                if "locked" not in str(e).lower() or conn.closed or time.monotonic() > end:
                    raise
                log.warning("DB가 잠겨 Agent 메시지 처리를 다시 시도합니다: %s", msg.get("type"))
                time.sleep(DB_RETRY_POLL_SEC)

    def _ack(self, conn: AgentConnection, seq: int):
        try:
            conn.send({"type": "agent.ack", "seq": seq})
        except AgentError as e:  # 못 보내면 Agent가 재연결 때 다시 보내고 위에서 걸러진다
            log.debug("ack를 보내지 못했습니다: %s", e)

    def _handle(self, conn: AgentConnection, msg: dict):
        t = msg.get("type")
        if t == "response":
            conn.resolve(msg)
        elif t == "hello":
            conn.info = msg
            conn.boot = msg.get("boot")
            if "hello" in self.handlers:
                self.handlers["hello"](conn, msg)
        elif t in self.handlers:
            self.handlers[t](conn, msg)
        else:
            log.warning("알 수 없는 Agent 메시지: %s", t)


registry = AgentRegistry()
