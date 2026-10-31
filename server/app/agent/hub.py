"""AgentHub: 채팅 세션 ↔ 사용자 PC Agent의 AI CLI 세션을 잇고, 에이전트 이벤트를 채팅 기록(DB)과 브라우저 WebSocket으로 중계한다.

원칙: 느린 CLI를 실패시키지 않는다. 세션은 죽었거나 사용자가 끊었을 때만 버린다. 기다리는 동안 무엇을 왜 기다리는지 항상 보인다.
- 요청은 바로 저장하고 바로 돌아온다(POST가 CLI 시작을 기다리지 않는다). CLI 준비는 턴마다 뒤의 스레드에서 한다.
- PC Agent와 끊겨도 턴과 세션을 버리지 않는다. CLI는 PC에서 계속 돌고, 이벤트는 Agent가 모아 두었다가 다시 보낸다.
- hello의 live_sessions·active_turns로 서버가 상태를 맞춘다. 서버를 다시 시작해도 이것으로 턴을 되살린다.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .. import models
from ..database import SessionLocal
from ..ws import manager
from .link import AgentConnection, AgentError, LinkLost, RemoteRunner, registry

log = logging.getLogger(__name__)

# Agent가 보고하는 시작 단계 → 화면 단계와 세부 내용
AGENT_STAGE = {
    "context_sync": ("agent_starting", "context_sync"), "cli_start": ("agent_starting", "cli_start"),
    "initialize": ("agent_starting", "initialize"), "session_new": ("session_new", None), "session_load": ("session_load", None),
    "attachments": ("thinking", "attachments"), "thinking": ("thinking", None),
}
LOST_TURN_WINDOW_SEC = 3600  # 표시가 없는 옛 기록은 이보다 오래되면 건드리지 않는다
FORCE_ACK_SEC = 10.0  # 다시 시작 요청을 받았다는 답을 기다리는 시간. 넘기면 반쯤 끊긴 연결로 보고 다시 연결될 때 보낸다


@dataclass
class TurnState:
    """대화 하나의 상태. 세션이 생기기 전(CLI 시작 중)에도 턴을 잡아야 하므로 CLI 세션이 아니라 대화에 묶는다."""
    chat_id: int
    project_id: int
    user_id: int
    agent: str = ""
    sid: str | None = None
    busy: bool = False
    turn_id: str | None = None
    stage: str = "idle"
    stage_detail: str | None = None
    stage_before: str | None = None  # 재연결이 끝나면 돌아갈 단계
    started_at: float = 0.0
    stage_at: float = 0.0
    last_output_at: float = 0.0
    typical_start_sec: float | None = None
    link: str = "ok"
    user_msg_id: int | None = None
    prompt_sent: bool = False
    prompt_msg: dict | None = None  # 전달되지 못했으면 재연결 때 다시 보낸다
    sent_boot: str | None = None
    cancel_requested: bool = False
    cancel_at: float = 0.0
    agent_msg_id: int | None = None
    thought_msg_id: int | None = None
    tool_msgs: dict[str, int] = field(default_factory=dict)
    open_tools: set[str] = field(default_factory=set)
    pending_permissions: dict[str, dict] = field(default_factory=dict)
    lock: threading.RLock = field(default_factory=threading.RLock)
    # 한 대화에서 세션을 동시에 두 번 열지 않는다. CLI마다 따로 잡는다: 시작이 멈춘 CLI를 두고 다른 CLI로 바꾼 사용자를 기다리게 하면 안 된다
    ensure_locks: dict[str, threading.Lock] = field(default_factory=dict)
    ensure_turns: dict[str, str] = field(default_factory=dict)  # CLI 이름 → 지금 그 세션을 준비하는 턴
    lost_output: int = 0  # DB 문제로 저장하지 못한 출력 수(턴이 끝날 때 한 번 알린다)


class AgentHub:
    def __init__(self):
        self.chats: dict[int, TurnState] = {}  # 대화 id → 상태
        self.by_sid: dict[str, TurnState] = {}  # agent session id → 상태
        self.live: dict[int, set[str]] = {}  # user_id → Agent가 살아 있다고 알린 세션(session/load 없이 그대로 쓴다)
        self.running: dict[int, set[str]] = {}  # user_id → 프로세스가 떠 있는 에이전트 이름
        self.start_times: dict[int, dict[str, float]] = {}  # user_id → 에이전트별 지난번 시작 시간(초)
        self.needs_context: set[str] = set()  # 새로 열어서 아직 [DDS 컨텍스트]와 지난 대화를 받지 못한 세션
        self.active_project: dict[int, int] = {}  # user_id → 최근 대화한 project_id
        self.pending_force: dict[int, dict[str, str | None]] = {}  # 전달하지 못한 다시 시작(에이전트 → 그때의 Agent boot). 재연결 때 전달
        self.last_boot: dict[int, str | None] = {}
        self._ended: dict[str, None] = {}  # 끝낸 턴. 재연결의 hello가 되살리지 않게 한다
        self._greeted: set[int] = set()
        self._lock = threading.RLock()
        registry.on("agent.event", lambda conn, m: self._on_event(m.get("session_id"), m.get("update") or {}, conn.user_id))
        registry.on("agent.permission", lambda conn, m: self._on_permission(
            conn, m.get("session_id"), str(m.get("request_id")), m.get("params") or {}))
        registry.on("agent.turn_end", self._on_turn_end)
        registry.on("agent.runner_exit", self._on_runner_exit)
        registry.on("agent.live", self._on_live)
        registry.on("session.stage", self._on_stage)
        registry.on("dropped", self._on_dropped)

    def runner_for(self, user_id: int) -> RemoteRunner:
        return registry.runner(user_id)

    def _state(self, chat: models.ChatSession) -> TurnState:
        with self._lock:
            st = self.chats.get(chat.id)
            if st is None or st.user_id != chat.user_id:  # SQLite가 지운 id를 다시 쓸 수 있다
                st = self.chats[chat.id] = TurnState(chat_id=chat.id, project_id=chat.project_id, user_id=chat.user_id,
                                                     agent=chat.agent, sid=chat.agent_session_id)
            return st

    def _user_states(self, user_id: int) -> list[TurnState]:
        with self._lock:
            return [st for st in self.chats.values() if st.user_id == user_id]

    def forget_session(self, sid: str | None):
        """대화의 에이전트를 바꿀 때 이전 CLI 세션 상태를 버린다."""
        if sid:
            with self._lock:
                st = self.by_sid.pop(sid, None)
                if st and st.sid == sid:
                    st.sid = None

    def forget_chat(self, chat_id: int):
        """대화(또는 프로젝트)를 지우거나 작업 폴더를 바꾸면 세션 상태를 버린다 — SQLite가 지운 id를 다시 쓰므로 꼭 필요하다.
        상태 객체가 바뀌므로, 시작 중 중지한 턴의 준비가 뒤늦게 옛 세션을 이 대화에 묶지 못한다(_ours)."""
        st = self.chats.get(chat_id)
        if st:
            with st.lock:  # _ensure_session의 확인과 저장 사이에 끼어들지 않게
                self._drop_chat(chat_id)
        else:
            self._drop_chat(chat_id)

    def _drop_chat(self, chat_id: int):
        with self._lock:
            for sid in [k for k, st in self.by_sid.items() if st.chat_id == chat_id]:
                self.by_sid.pop(sid, None)
            self.chats.pop(chat_id, None)

    # ---------- 연결 ----------
    def on_link_lost(self, user_id: int):
        """지금 연결이 끊겼다. 턴과 세션은 그대로 둔다: PC에서 CLI는 계속 돌고, 다시 연결되면 hello로 맞춘다."""
        if registry.get(user_id):  # 이 정리가 스레드를 기다리는 사이 벌써 다시 붙었다. '연결 중' 표시에 갇히지 않게 hello에 맡긴다
            return
        for st in self._user_states(user_id):
            with st.lock:
                if not st.busy:
                    continue
                st.link = "lost"
                if st.stage != "reconnecting":
                    st.stage_before = st.stage
                    st.stage = "reconnecting"
            self._broadcast_stage(st)

    def on_hello(self, conn: AgentConnection, m: dict):
        """Agent가 아는 것과 맞춘다. 끝난 턴은 정리하고, 서버가 모르는 턴은 되살린다."""
        uid = conn.user_id
        if registry.get(uid) is not conn and uid in registry.conns:
            conn.ready.set()
            return
        with self._lock:
            self.live[uid] = set(m.get("live_sessions") or [])
            self.running[uid] = set(m.get("running_agents") or [])
            self.start_times[uid] = dict(m.get("start_times") or {})
            self.last_boot[uid] = conn.boot
        turns = {t["turn_id"]: t for t in m.get("active_turns") or [] if t.get("turn_id")}
        try:
            if conn.boot:
                for st in self._user_states(uid):
                    self._reconcile(conn, st, turns)
                bound = {st.turn_id for st in self._user_states(uid) if st.busy}
                for tid, t in turns.items():
                    if tid not in bound and tid not in self._ended:
                        self._restore_turn(conn, t)
                self._flush_force(conn)
            else:  # 옛 Agent는 응답 대기형이라 맞출 턴이 없다. 그래도 끊겼다는 표시는 풀어야 답이 오는 동안 '연결 중'에 갇히지 않는다
                for st in self._user_states(uid):
                    if self._link_back(st):
                        self._broadcast_stage(st)
            first = uid not in self._greeted
            self._greeted.add(uid)
            if first:
                self._note_lost_turns(uid, {t.get("chat_id") for t in turns.values()})
        finally:
            conn.ready.set()

    def _link_back(self, st: TurnState) -> bool:
        with st.lock:
            if not st.busy:
                return False
            st.link = "ok"
            if st.stage == "reconnecting":
                st.stage = st.stage_before or "thinking"
            return True

    def _flush_force(self, conn: AgentConnection):
        """전달하지 못한 다시 시작을 보낸다. Agent가 그사이 다시 시작했으면 그 CLI는 이미 없으니 미리 띄운 새 CLI를 끝내지 않는다."""
        with self._lock:
            pending = self.pending_force.pop(conn.user_id, {})
        for agent, boot in pending.items():
            if boot and boot != conn.boot:
                continue
            try:
                RemoteRunner(conn).force_restart(agent)
            except AgentError:
                with self._lock:
                    self.pending_force.setdefault(conn.user_id, {})[agent] = boot

    def _reconcile(self, conn: AgentConnection, st: TurnState, turns: dict):
        if not self._link_back(st):
            return
        with st.lock:
            if not st.busy:
                return
            known = st.turn_id in turns
            resend = st.prompt_msg if (st.prompt_sent and not known and st.sent_boot == conn.boot and st.prompt_msg) else None
            ended = st.prompt_sent and not known and st.sent_boot != conn.boot
            cancel = st.prompt_sent and known and st.cancel_requested
            if known and st.sid:
                self.live.setdefault(st.user_id, set()).add(st.sid)
        try:
            if resend:  # 같은 Agent 프로세스인데 모른다면 전달되기 전에 끊긴 것이다. 같은 turn_id면 Agent가 두 번 돌리지 않는다
                conn.send(resend)
            elif cancel:
                RemoteRunner(conn).cancel(st.sid, st.turn_id)
        except AgentError as e:
            log.debug("재연결 전달 실패: %s", e)
        if ended:  # Agent가 다시 시작되어 턴이 사라졌다. 한 번만 안내한다
            self._note(st, "PC Agent가 다시 시작되어 이 요청이 끝까지 진행되지 못했습니다. 이어서 하려면 다시 요청해 주세요.")
            self._finish(st, "disconnected")
        else:
            self._broadcast_stage(st)

    def _restore_turn(self, conn: AgentConnection, t: dict):
        """서버가 다시 시작되어 모르는 턴을 되살린다. 이후 오는 이벤트가 이 대화에 쌓이고 끝나면 정상으로 닫힌다."""
        sid = t.get("session_id")
        with SessionLocal() as db:
            chat = db.get(models.ChatSession, t.get("chat_id"))
            if not chat or chat.user_id != conn.user_id:  # 끊긴 동안 대화가 지워졌다. 주인 없는 작업이 계속 돌지 않게 멈춘다
                if sid:
                    RemoteRunner(conn).cancel(sid, t.get("turn_id"))
                return
            st = self._state(chat)
            if not chat.agent_session_id and sid:
                chat.agent_session_id = sid
                db.commit()
            tools = (db.query(models.ChatMessage).filter_by(session_id=chat.id, role="tool")
                     .order_by(models.ChatMessage.id.desc()).limit(200).all())
            asked = (db.query(models.ChatMessage).filter_by(session_id=chat.id, role="user")
                     .order_by(models.ChatMessage.id.desc()).first())
            tool_msgs = {(m.meta or {}).get("tool_call_id"): m.id for m in tools}
            open_tools = {(m.meta or {}).get("tool_call_id") for m in tools if (m.meta or {}).get("status") not in ("completed", "failed")}
        with st.lock:
            if st.busy and st.turn_id != t["turn_id"]:
                return  # 서버가 그새 다른 턴을 시작했다. 그쪽을 살린다
            now = time.time()
            st.busy, st.turn_id, st.prompt_sent, st.sent_boot = True, t["turn_id"], True, conn.boot
            st.agent = t.get("agent") or st.agent
            st.started_at = float(t.get("started_at") or now)
            st.stage, st.stage_detail, st.stage_at, st.last_output_at = "thinking", None, now, now
            st.link, st.sid, st.cancel_requested = "ok", sid, False
            st.agent_msg_id = st.thought_msg_id = None
            st.tool_msgs, st.open_tools = tool_msgs, open_tools
            st.user_msg_id = asked.id if asked else None  # 끝날 때 표시해야 다음 재시작에서 "끊긴 요청"으로 잘못 안내하지 않는다
            st.typical_start_sec = self.start_times.get(st.user_id, {}).get(st.agent)
        with self._lock:
            if sid:
                self.by_sid[sid] = st
                self.live.setdefault(st.user_id, set()).add(sid)
            self.active_project[st.user_id] = st.project_id
        self._broadcast(st, {"kind": "turn_started", **self._view(st)})

    def _note_lost_turns(self, user_id: int, live_chats: set):
        """서버가 다시 시작되는 사이 끝을 알 수 없게 된 요청은 답이 오지 않는다. 기다리지 않게 대화별로 한 번 안내하고, 끝나지 않은 도구를 닫는다.
        서버와 Agent가 함께 다시 시작하면 Agent도 그 턴을 모른다. 답이 조금 나온 뒤였어도 끝난 답처럼 남으면 안 된다."""
        try:
            since = datetime.now() - timedelta(seconds=LOST_TURN_WINDOW_SEC)
            with SessionLocal() as db:
                for chat in db.query(models.ChatSession).filter_by(user_id=user_id).all():
                    if chat.id in live_chats or (chat.id in self.chats and self.chats[chat.id].busy):
                        continue
                    asked = (db.query(models.ChatMessage).filter_by(session_id=chat.id, role="user")
                             .order_by(models.ChatMessage.id.desc()).first())
                    if not asked or (asked.meta or {}).get("turn_done"):  # 중지 등으로 끝난 턴은 안내할 것이 없다
                        continue
                    last = (db.query(models.ChatMessage).filter_by(session_id=chat.id)
                            .order_by(models.ChatMessage.id.desc()).first())
                    # turn_open이 없는 것은 이 표시가 생기기 전의 기록이다. 끝났는지 알 수 없어 답이 없는 최근 요청만 본다
                    if not (asked.meta or {}).get("turn_open") and not (last.id == asked.id and asked.created_at and asked.created_at >= since):
                        continue
                    closed = []
                    for m in db.query(models.ChatMessage).filter(models.ChatMessage.session_id == chat.id, models.ChatMessage.role == "tool",
                                                                 models.ChatMessage.id > asked.id).all():
                        if (m.meta or {}).get("status") not in ("completed", "failed"):
                            m.meta = {**(m.meta or {}), "status": "failed", "output": (m.meta or {}).get("output") or "끝나기 전에 요청이 중단되었습니다."}
                            closed.append(m)
                    asked.meta = {**(asked.meta or {}), "turn_done": True}
                    db.add(models.ChatMessage(session_id=chat.id, role="system", meta={},
                                              content="서버가 다시 시작되는 동안 이 요청이 끊겼습니다. 여기까지의 내용은 남아 있습니다 → 이어서 하려면 다시 보내 주세요."
                                              if last.id != asked.id else "서버가 다시 시작되는 동안 이 요청이 끊겼습니다 → 같은 요청을 다시 보내 주세요."))
                    db.commit()
                    for m in closed:
                        manager.publish({"type": "agent_event", "project_id": chat.project_id, "session_id": chat.id,
                                         "event": {"kind": "tool_call_update", "message_id": m.id, "meta": m.meta}})
                    manager.publish({"type": "agent_event", "project_id": chat.project_id, "session_id": chat.id,
                                     "event": {"kind": "turn_ended", "stop_reason": "disconnected"}})
        except Exception:  # 안내를 못 해도 연결은 살린다
            log.exception("끊긴 요청 안내 실패")

    # ---------- 세션 ----------
    def _ours(self, st: TurnState, chat: models.ChatSession | None, agent: str) -> bool:
        """시작 중 중지한 턴의 준비는 뒤에서 계속된다. 그사이 대화가 지워졌거나 에이전트·작업 폴더가 바뀌었으면 그 세션을 이 대화에 묶지 않는다."""
        return bool(chat and chat.agent == agent and self.chats.get(st.chat_id) is st)

    def _ensure_session(self, st: TurnState, conn: AgentConnection, turn_id: str, agent: str) -> str | None:
        """이 대화의 CLI 세션을 돌려준다. 살아 있으면 그대로(session/load는 Gemini에서 세션을 dispose한다),
        없으면 load, load가 "지원 안 함/없음"이면 new. 턴이 그새 끝났어도 끝까지 해 둔다(다음 요청이 빨라진다)."""
        with SessionLocal() as db:
            chat = db.get(models.ChatSession, st.chat_id)
            if not self._ours(st, chat, agent):
                return None
            old, pid = chat.agent_session_id, chat.project_id
            pname = chat.project.name if chat.project else None
            puid = chat.project.uid if chat.project else None
        if old and old in self.live.setdefault(st.user_id, set()):
            self._bind(st, old)
            return old
        runner = RemoteRunner(conn)
        warm = agent in self.running.get(st.user_id, set())  # 프로그램이 떠 있지 않으면 Agent가 시작 단계를 알린다
        if old:
            if warm and self._current(st, turn_id):
                self._set_stage(st, "session_load")
            if runner.load_session(old, pid, agent, pname, puid, turn_id):
                self.running.setdefault(st.user_id, set()).add(agent)
                with SessionLocal() as db:
                    if not self._ours(st, db.get(models.ChatSession, st.chat_id), agent):
                        return None
                self.live.setdefault(st.user_id, set()).add(old)
                self._bind(st, old)
                return old
        if warm and self._current(st, turn_id):
            self._set_stage(st, "session_new")
        sid = runner.new_session(pid, agent, pname, puid, turn_id)
        self.running.setdefault(st.user_id, set()).add(agent)
        with st.lock, SessionLocal() as db:  # 확인과 저장을 한 번에: 사이에 작업 폴더가 바뀌면 옛 폴더의 세션이 남는다
            chat = db.get(models.ChatSession, st.chat_id)
            if not self._ours(st, chat, agent):
                return None
            chat.agent_session_id = sid
            db.commit()
        self.live.setdefault(st.user_id, set()).add(sid)
        self.needs_context.add(sid)
        if old:
            with self._lock:
                self.by_sid.pop(old, None)
            # 조용히 맥락을 잃지 않게 사용자에게 한 번 알린다
            self._note(st, "이전 AI 세션을 이어 붙일 수 없어 새 세션으로 이어 갑니다. 지금까지의 대화 요약을 함께 넘겼습니다.")
        self._bind(st, sid)
        return sid

    def _bind(self, st: TurnState, sid: str):
        with self._lock:
            st.sid = sid
            self.by_sid[sid] = st

    def _current(self, st: TurnState, turn_id: str) -> bool:
        return st.busy and st.turn_id == turn_id

    def _wait_ready(self, st: TurnState, turn_id: str) -> AgentConnection | None:
        """Agent 연결이 준비될 때까지 기다린다. 사용자가 중지하면 None."""
        while self._current(st, turn_id):
            conn = registry.get(st.user_id)
            if conn and conn.ready.is_set():
                return conn
            time.sleep(0.1)
        return None

    def _prepare(self, st: TurnState, turn_id: str, agent: str) -> str | None:
        lock = st.ensure_locks.setdefault(agent, threading.Lock())
        while not lock.acquire(timeout=0.2):
            if not self._current(st, turn_id):
                return None
        try:
            st.ensure_turns[agent] = turn_id
            while True:
                conn = self._wait_ready(st, turn_id)
                if conn is None:
                    return None
                try:
                    return self._ensure_session(st, conn, turn_id, agent)
                except LinkLost:  # 다시 연결되면 이어서 한다. 같은 turn_id면 Agent가 세션을 또 만들지 않는다
                    log.info("세션 준비 중 연결이 끊겨 재연결을 기다립니다: 대화 %s", st.chat_id)
        finally:
            lock.release()

    # ---------- 턴 ----------
    def send_prompt(self, chat_id: int, text: str, attachments: list[dict] | None = None) -> str:
        """요청을 저장하고 바로 돌아온다. CLI 준비와 전달은 뒤의 스레드에서 한다. 이미 진행 중이면 AgentError.
        attachments: [{id, filename, size, sha256}] — PC Agent가 프로젝트 폴더 inputs/로 내려받은 뒤 에이전트에 전달한다."""
        attachments = attachments or []
        with SessionLocal() as db:
            chat = db.get(models.ChatSession, chat_id)
            if not registry.get(chat.user_id):
                raise AgentError("PC Agent가 연결되어 있지 않습니다. 내 PC에서 dds-agent를 실행하세요.")
            st = self._state(chat)
            uid, turn_id = chat.user_id, uuid.uuid4().hex
            with st.lock:
                if st.busy:  # 시작 중이어도 막는다. 새로고침 뒤 다시 보내 세션이 둘 열리는 일을 막는다
                    raise AgentError("에이전트가 아직 이전 요청을 처리 중입니다")
                now = time.time()
                st.busy, st.turn_id, st.agent = True, turn_id, chat.agent
                st.prompt_sent, st.prompt_msg, st.sent_boot, st.cancel_requested = False, None, None, False
                st.started_at = st.stage_at = st.last_output_at = now
                st.link, st.stage_before = "ok", None
                st.agent_msg_id = st.thought_msg_id = None
                st.open_tools = set()
                st.lost_output = 0
                st.stage = self._first_stage(uid, chat.agent, chat.agent_session_id)
                st.stage_detail = None
                st.typical_start_sec = self.start_times.get(uid, {}).get(chat.agent)
            try:
                if chat.title == "새 대화":
                    chat.title = text[:40]
                # turn_open: 서버가 다시 시작되면 끝을 알 길이 없다. 끝날 때 turn_done으로 바꿔 끊긴 요청을 가린다
                m = models.ChatMessage(session_id=chat.id, role="user", content=text,
                                       meta={"turn_open": True, **({"attachments": attachments} if attachments else {})})
                db.add(m)
                db.commit()
                msg_id = st.user_msg_id = m.id
                agent = chat.agent
            except Exception:
                st.busy = False
                raise
        self.active_project[uid] = st.project_id
        self._broadcast(st, {"kind": "turn_started", **self._view(st)})
        threading.Thread(target=self._run_turn, args=(st, turn_id, text, attachments, msg_id, agent), daemon=True,
                         name=f"turn-{chat_id}").start()
        return turn_id

    def _first_stage(self, uid: int, agent: str, sid: str | None) -> str:
        if sid and sid in self.live.get(uid, set()):
            return "thinking"
        if agent in self.running.get(uid, set()):
            return "session_load" if sid else "session_new"
        return "agent_starting"

    def _run_turn(self, st: TurnState, turn_id: str, text: str, attachments: list[dict], msg_id: int, agent: str):
        try:
            sid = self._prepare(st, turn_id, agent)
            if sid is None or not self._current(st, turn_id):
                return  # 시작 중에 중지했다. 프롬프트는 보내지 않고 CLI는 따뜻하게 둔다
            context = ""
            if sid in self.needs_context:  # first_turn 판단은 세션을 얻은 뒤: 어떤 이유로 새로 열렸든 지난 대화를 넘긴다
                with SessionLocal() as db:
                    recap = _recap(db, st.chat_id, before_id=msg_id)
                context = (f"[DDS 컨텍스트] project_id={st.project_id}. "
                           "dds-* MCP 툴과 스킬을 사용하고 작업 규칙을 따르세요. 사용자가 가진 것에서 시작합니다(표·해석 폴더·모델이 있으면 해석부터 하지 않습니다). "
                           "해석·긴 계산을 새로 돌리거나 사용자 모델을 바꾸는 일만 Flow 승인 후 실행하고, "
                           "조회·후처리·기존 데이터로 학습·Simulator 배포·보고서는 승인 없이 바로 합니다. 데이터는 로컬 폴더 규칙대로 둡니다.\n\n"
                           + recap)
            payload = {"type": "session.prompt", "turn_id": turn_id, "chat_id": st.chat_id, "session_id": sid, "agent": st.agent,
                       "text": context + text, "attachments": attachments, "started_at": st.started_at}
            self._deliver(st, turn_id, sid, payload)
        except Exception as e:
            if self._current(st, turn_id):
                if not isinstance(e, AgentError):
                    log.exception("턴 준비 실패")
                self._note(st, f"에이전트 오류: {str(e) or repr(e)}")
                self._finish(st, "error")
            else:  # 사용자가 이미 끝낸 턴이다. 여기서 낸 오류는 보여 줄 곳이 없다
                log.info("끝난 턴의 준비 실패: %s", e)

    def _deliver(self, st: TurnState, turn_id: str, sid: str, payload: dict):
        while True:
            conn = self._wait_ready(st, turn_id)
            if conn is None:
                return
            if not conn.boot:  # 옛 Agent: 응답을 기다리는 방식으로 돌린다. 업데이트되면 이 길은 쓰지 않는다
                self._run_legacy(st, turn_id, conn, payload)
                return
            with st.lock:  # 중지와 엇갈리지 않게: 중지가 먼저면 보내지 않고, 보낸 뒤면 중지도 전달된다
                if not self._current(st, turn_id):
                    return
                try:
                    st.prompt_sent, st.prompt_msg, st.sent_boot = True, payload, conn.boot
                    conn.send(payload)
                except LinkLost:
                    st.prompt_sent, st.sent_boot = False, None
                    continue
                self.needs_context.discard(sid)
                self._set_stage(st, "thinking", quiet=True)
            self._broadcast_stage(st)
            return

    def _run_legacy(self, st: TurnState, turn_id: str, conn: AgentConnection, payload: dict):
        with st.lock:
            st.prompt_sent, st.sent_boot = True, None
            self.needs_context.discard(payload["session_id"])
            self._set_stage(st, "thinking")
        stop = "error"
        try:
            stop = (conn.request("session.prompt", payload, timeout=None) or {}).get("stop_reason", "end_turn")
        except AgentError as e:
            if self._current(st, turn_id):
                self._note(st, f"에이전트 오류: {e}")
        if self._current(st, turn_id):
            self._finish(st, stop)

    def _finish(self, st: TurnState, stop_reason: str):
        with st.lock:
            if not st.busy:
                return
            st.busy, st.stage, st.stage_detail, st.prompt_msg = False, "idle", None, None
            st.cancel_requested = False
            st.pending_permissions.clear()
            open_tools, st.open_tools = st.open_tools, set()
            lost, st.lost_output = st.lost_output, 0
            reached_cli = st.prompt_sent
            self._ended[st.turn_id] = None
            if len(self._ended) > 1000:
                self._ended.pop(next(iter(self._ended)))
        # 턴을 끝내는 일이 DB 문제로 멈추면 대화가 '진행 중'에 갇힌다. 아래는 실패해도 끝까지 간다
        try:
            self._close_tools(st, open_tools)
        except Exception:
            log.exception("끝나지 않은 도구를 닫지 못했습니다")
        if lost:
            self._note(st, f"서버가 저장하지 못해 이 답의 출력 일부({lost}건)가 빠졌습니다. 작업 자체는 계속되었습니다.")
        self._mark_done(st, stop_reason, reached_cli)
        self._broadcast(st, {"kind": "turn_ended", "stop_reason": stop_reason})

    def _mark_done(self, st: TurnState, stop_reason: str = "", reached_cli: bool = True):
        """요청 메시지에 끝났다고 남긴다. 서버를 다시 시작한 뒤 "끊긴 요청"과 가리고, 중지한 요청을 지난 대화 요약에서 가리는 데 쓴다."""
        if st.user_msg_id is None:
            return
        try:
            with SessionLocal() as db:
                m = db.get(models.ChatMessage, st.user_msg_id)
                if m:
                    meta = {**(m.meta or {}), "turn_done": True}
                    if stop_reason == "cancelled":
                        meta["stopped"] = "sent" if reached_cli else "unsent"
                    m.meta = meta
                    db.commit()
        except Exception:
            log.exception("턴 끝 표시 실패")

    def _close_tools(self, st: TurnState, tool_ids: set[str]):
        """턴이 끝났는데 끝나지 않은 도구: 멈춘 스크립트를 끝낸 경우다. 기록에 '실행 중'이 영원히 남지 않게 닫는다."""
        for tool_id in tool_ids:
            msg_id = st.tool_msgs.get(tool_id)
            if msg_id is None:
                continue
            with SessionLocal() as db:
                m = db.get(models.ChatMessage, msg_id)
                if not m or (m.meta or {}).get("status") in ("completed", "failed"):
                    continue
                meta = {**(m.meta or {}), "status": "failed", "output": (m.meta or {}).get("output") or "끝나기 전에 요청이 중단되었습니다."}
                m.meta = meta
                db.commit()
            self._broadcast(st, {"kind": "tool_call_update", "message_id": msg_id, "meta": meta})

    def cancel(self, chat_id: int):
        st = self.chats.get(chat_id)
        if not st or not st.busy:
            return
        with st.lock:
            if not st.busy:
                return
            if not st.prompt_sent:
                # 시작 중: 사용자에게는 바로 끝난다. 세션 준비는 뒤에서 계속되고 프롬프트만 보내지 않는다. CLI를 죽이면 다음 요청이 또 몇 분 걸린다
                self._finish(st, "cancelled")
                return
            st.cancel_requested, st.cancel_at = True, time.time()
            perms = list(st.pending_permissions)
            st.pending_permissions.clear()
            sid, turn_id = st.sid, st.turn_id
            try:
                self.runner_for(st.user_id).cancel(sid, turn_id)
            except AgentError as e:  # 끊겨 있으면 재연결 때(_reconcile) 전달한다
                log.info("중지 전달 보류: %s", e)
        for rid in perms:
            self._broadcast(st, {"kind": "permission_resolved", "request_id": rid, "option_id": None})
        self._broadcast_stage(st)

    def force_restart(self, chat_id: int):
        """사용자가 멈췄는데 응답이 없을 때: 그 CLI를 끝내고(그 아래 멈춘 스크립트까지) 이 턴을 끝낸다. 사용자가 끊는 유일한 경로다."""
        st = self.chats.get(chat_id)
        if not st or not st.busy:
            raise AgentError("진행 중인 요청이 없습니다")
        agent, turn_id, uid = st.agent, st.turn_id, st.user_id
        conn = registry.get(uid)
        if conn and conn.ready.is_set() and not conn.boot:  # 0.2.16까지는 이 메시지를 모른다. 끝낸 척하면 멈춘 CLI가 그대로 남는다
            raise AgentError("이 PC의 Agent가 옛 버전이라 AI 도구를 다시 시작할 수 없습니다 → 트레이 아이콘에서 Agent를 다시 시작하면 AI 도구도 함께 끝나고, 새 버전으로 업데이트됩니다.")
        with self._lock:  # 죽을 세션에 다음 요청을 보내지 않는다. 다음 요청은 session/load부터 한다
            live = self.live.setdefault(uid, set())
            for sid, other in self.by_sid.items():
                if other.user_id == uid and other.agent == agent:
                    live.discard(sid)
            self.running.setdefault(uid, set()).discard(agent)
        # 턴을 먼저 끝낸다. CLI가 바로 죽어 runner_exit가 먼저 도착하면 같은 안내가 두 번 남는다
        self._note(st, "AI 도구를 다시 시작합니다. 진행 중이던 작업은 끝납니다. 다음 요청 때 이전 대화를 이어 붙입니다.")
        self._finish(st, "cancelled")
        threading.Thread(target=self._deliver_force, args=(uid, conn, agent, turn_id), daemon=True, name=f"force-{agent}").start()

    def _deliver_force(self, uid: int, conn: AgentConnection | None, agent: str, turn_id: str | None):
        """다시 시작은 받았다는 답을 확인한다. '중지'에 답이 없던 이유가 반쯤 끊긴 연결일 수 있고, 그런 연결은 보내기만 성공하고 메시지가 사라진다."""
        try:
            if conn is None:
                raise LinkLost("PC Agent 연결이 끊어졌습니다")
            conn.request("session.force_restart", {"agent": agent, "turn_id": turn_id}, timeout=FORCE_ACK_SEC)
        except AgentError:  # PC가 꺼져 있어도 대화가 막히면 안 된다. 다시 연결되면 전달한다
            with self._lock:
                self.pending_force.setdefault(uid, {})[agent] = conn.boot if conn else self.last_boot.get(uid)
            now = registry.get(uid)
            if now and now is not conn and now.ready.is_set():  # hello를 이미 처리한 새 연결이면 여기서 보낸다
                self._flush_force(now)

    def respond_permission(self, chat_id: int, request_id: str, option_id: str | None):
        st = self.chats.get(chat_id)
        if not st or request_id not in st.pending_permissions:
            raise AgentError("이미 처리되었거나 존재하지 않는 권한 요청입니다")
        self.runner_for(st.user_id).respond_permission(request_id, option_id)  # 못 보내면 카드를 남겨 다시 누를 수 있게 한다
        st.pending_permissions.pop(request_id, None)
        self._broadcast(st, {"kind": "permission_resolved", "request_id": request_id, "option_id": option_id})

    def _view(self, st: TurnState) -> dict[str, Any]:
        now = time.time()
        return {"stage": st.stage, "stage_detail": st.stage_detail, "started_at": datetime.fromtimestamp(st.started_at).isoformat() if st.started_at else None,
                "elapsed_sec": round(now - st.started_at, 1), "stage_elapsed_sec": round(now - st.stage_at, 1),
                "idle_sec": round(now - st.last_output_at, 1), "typical_start_sec": st.typical_start_sec, "link": st.link,
                "cancelling": st.cancel_requested, "cancel_elapsed_sec": round(now - st.cancel_at, 1) if st.cancel_requested else None,
                "agent": st.agent}

    def status(self, chat_id: int, user_id: int | None = None) -> dict[str, Any]:
        st = self.chats.get(chat_id)
        owner = st.user_id if st else user_id
        if not st or not st.busy:
            return {"busy": False, "stage": "idle", "stage_detail": None, "started_at": None, "elapsed_sec": 0, "stage_elapsed_sec": 0,
                    "idle_sec": 0, "typical_start_sec": None, "link": "ok" if registry.get(owner) else "lost", "cancelling": False,
                    "cancel_elapsed_sec": None, "agent": st.agent if st else None, "pending_permissions": []}
        return {"busy": True, **self._view(st), "pending_permissions": list(st.pending_permissions.values())}

    # ---------- 이벤트 ----------
    def _broadcast(self, st: TurnState, event: dict):
        manager.publish({"type": "agent_event", "project_id": st.project_id, "session_id": st.chat_id, "event": event})

    def _broadcast_stage(self, st: TurnState):
        if st.busy:
            self._broadcast(st, {"kind": "turn_stage", **self._view(st)})

    def _set_stage(self, st: TurnState, stage: str, detail: str | None = None, quiet: bool = False):
        with st.lock:
            changed = (st.stage, st.stage_detail) != (stage, detail)
            if st.stage != stage:
                st.stage_at = time.time()
            if st.link == "lost":  # 재연결 표시가 먼저다. 돌아갈 단계만 바꾼다
                st.stage_before = stage
                return
            st.stage, st.stage_detail = stage, detail
        if changed and not quiet:
            self._broadcast_stage(st)

    def _on_stage(self, conn: AgentConnection, m: dict):
        stage = AGENT_STAGE.get(m.get("stage"))
        if not stage:
            return
        turn_id = m.get("turn_id")
        with self._lock:
            # 시작 중 중지 뒤 바로 다시 보낸 턴은 앞 턴의 준비를 함께 기다린다. 같은 CLI의 준비일 때만 그 단계를 이 턴의 것으로 보여 준다
            st = next((s for s in self.chats.values() if s.user_id == conn.user_id and s.busy
                       and turn_id in (s.turn_id, s.ensure_turns.get(s.agent))), None)
        if not st:
            return
        if st.prompt_sent and stage[0] != "thinking":
            return
        with st.lock:
            if m.get("typical_start_sec") is not None:
                st.typical_start_sec = m["typical_start_sec"]
        self._set_stage(st, stage[0], stage[1])

    def _on_live(self, conn: AgentConnection, m: dict):
        with self._lock:
            self.live[conn.user_id] = set(m.get("live_sessions") or [])
            self.running[conn.user_id] = set(m.get("running_agents") or [])
            self.start_times[conn.user_id] = dict(m.get("start_times") or {})

    def _on_turn_end(self, conn: AgentConnection, m: dict):
        with self._lock:
            st = self.chats.get(m.get("chat_id"))
        if not st or not st.busy or st.turn_id != m.get("turn_id") or st.user_id != conn.user_id:  # 이미 끝낸 턴(runner_exit·다시 시작)의 뒤늦은 알림
            return
        if m.get("session_gone"):
            self.live.setdefault(st.user_id, set()).discard(m.get("session_id"))
        if m.get("dropped_events"):
            self._note(st, f"연결이 오래 끊겨 그동안의 출력 일부({m['dropped_events']}건)는 보관 한도를 넘어 빠졌습니다. 작업 자체는 계속되었습니다.")
        if m.get("error"):
            self._note(st, f"에이전트 오류: {m['error']}")
        self._finish(st, m.get("stop_reason") or ("error" if m.get("error") else "end_turn"))

    def _on_runner_exit(self, conn: AgentConnection, m: dict):
        """CLI가 죽었다. 그 세션을 잊고 진행 중 턴을 끝낸다. 다음 요청은 CLI 재시작 + load, 안 되면 new + 지난 대화 요약."""
        uid, agent, sids = conn.user_id, m.get("agent"), set(m.get("session_ids") or [])
        with self._lock:
            self.live.setdefault(uid, set()).difference_update(sids)
            self.running.setdefault(uid, set()).discard(agent)
        forced = m.get("reason") == "force_restart"
        tail = (m.get("stderr_tail") or "").strip()
        for st in self._user_states(uid):
            with st.lock:
                hit = st.busy and st.prompt_sent and st.agent == agent and st.sid in sids
            if not hit:
                continue
            if forced:
                note = "AI 도구를 다시 시작해서 이 요청이 끝났습니다. 다음 요청 때 이전 대화를 이어 붙입니다."
            else:
                note = "AI 도구가 갑자기 종료되어 이 요청이 끝났습니다 → 다시 보내면 AI 도구를 다시 시작해 이전 대화에 이어 갑니다."
                if tail:
                    note += f"\n\n종료 전 기록:\n{tail[-800:]}"
            self._note(st, note)
            self._finish(st, "error")

    def _add_message(self, st: TurnState, role: str, content: str, meta: dict | None = None) -> int:
        with SessionLocal() as db:
            m = models.ChatMessage(session_id=st.chat_id, role=role, content=content, meta=meta or {})
            db.add(m)
            db.commit()
            return m.id

    def _note(self, st: TurnState, content: str):
        """안내를 남긴다. 저장에 실패해도 턴을 끝내는 일을 막지 않는다: 'database is locked' 하나로 대화가 '진행 중'에 영원히 갇히면 안 된다."""
        try:
            self._add_message(st, "system", content)
        except Exception:
            log.exception("안내를 저장하지 못했습니다: %s", content[:100])

    def _on_dropped(self, conn: AgentConnection, m: dict):
        """처리 코드 문제로 넘어간 메시지(저장 실패는 여기 오지 않고 Agent가 다시 보낸다). 빠진 줄 모르게 두지 않고 턴이 끝날 때 알린다."""
        st = self.by_sid.get(m.get("session_id"))
        if st and st.user_id == conn.user_id and m.get("type") == "agent.event":
            st.lost_output += 1

    def _append(self, msg_id: int, text: str) -> int:
        with SessionLocal() as db:
            m = db.get(models.ChatMessage, msg_id)
            if not m:  # 대화가 그새 지워졌을 수 있다. 예외 하나로 연결을 끊지 않는다
                return 0
            before = m.content or ""
            m.content = before + text
            db.commit()
            return len(before.encode("utf-16-le")) // 2  # 브라우저 문자열 단위. 화면이 기록을 읽는 사이 온 글자를 빠짐·겹침 없이 맞춘다

    def _on_event(self, sid: str, update: dict, user_id: int | None = None):
        st = self.by_sid.get(sid)
        if not st or (user_id is not None and st.user_id != user_id):
            return
        kind = update.get("sessionUpdate")
        if not st.busy and kind != "tool_call_update":  # 턴 밖의 글자는 담을 곳이 없다(취소된 턴의 뒤늦은 출력 등)
            return
        now = time.time()
        if kind in ("agent_message_chunk", "agent_thought_chunk", "tool_call", "tool_call_update"):
            st.last_output_at = now
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            text = (update.get("content") or {}).get("text", "")
            if kind == "agent_message_chunk":
                if st.agent_msg_id is None:
                    st.agent_msg_id = self._add_message(st, "agent", "")
                offset = self._append(st.agent_msg_id, text)
                msg_id = st.agent_msg_id
            else:
                if st.thought_msg_id is None:
                    st.thought_msg_id = self._add_message(st, "thought", "")
                offset = self._append(st.thought_msg_id, text)
                msg_id = st.thought_msg_id
            self._broadcast(st, {"kind": "chunk", "role": "agent" if kind == "agent_message_chunk" else "thought",
                                 "message_id": msg_id, "text": text, "offset": offset})
            if not st.open_tools:
                self._set_stage(st, "thinking")
        elif kind == "tool_call":
            # 툴 호출 이후의 텍스트는 새 말풍선으로
            st.agent_msg_id = st.thought_msg_id = None
            meta = {"tool_call_id": update.get("toolCallId"), "title": update.get("title"), "kind": update.get("kind"),
                    "status": update.get("status", "pending"), "raw_input": update.get("rawInput")}
            msg_id = self._add_message(st, "tool", update.get("title") or "", meta)
            st.tool_msgs[update.get("toolCallId")] = msg_id
            self._broadcast(st, {"kind": "tool_call", "message_id": msg_id, "meta": meta})
            if update.get("status") not in ("completed", "failed"):
                st.open_tools.add(update.get("toolCallId"))
                self._set_stage(st, "tool", update.get("title"))
        elif kind == "tool_call_update":
            msg_id = st.tool_msgs.get(update.get("toolCallId"))
            if msg_id is None:
                return
            with SessionLocal() as db:
                m = db.get(models.ChatMessage, msg_id)
                if not m:
                    return
                meta = dict(m.meta or {})
                if update.get("status"):
                    meta["status"] = update["status"]
                if update.get("title"):
                    meta["title"] = update["title"]
                out = _content_text(update.get("content"))
                if out:
                    meta["output"] = out[:4000]
                m.meta = meta
                db.commit()
            self._broadcast(st, {"kind": "tool_call_update", "message_id": msg_id, "meta": meta})
            if update.get("status") in ("completed", "failed"):
                st.open_tools.discard(update.get("toolCallId"))
                if st.busy and not st.open_tools:
                    self._set_stage(st, "thinking")
        elif kind == "plan":
            self._broadcast(st, {"kind": "plan", "entries": update.get("entries", [])})

    def _on_permission(self, conn: AgentConnection, sid: str, request_id: str, params: dict):
        st = self.by_sid.get(sid)
        if not st or not st.busy or st.user_id != conn.user_id:
            # 답할 사람이 없다. 그냥 두면 CLI가 영원히 기다리고 자동 업데이트도 막힌다
            RemoteRunner(conn).respond_permission(request_id, None)
            return
        if request_id in st.pending_permissions:  # 재연결 때 다시 온 요청. 카드를 두 번 띄우지 않는다
            return
        tc = params.get("toolCall") or {}
        payload = {"request_id": request_id, "title": tc.get("title") or "툴 실행", "tool_kind": tc.get("kind"),
                   "raw_input": tc.get("rawInput"), "options": params.get("options", [])}
        st.pending_permissions[request_id] = payload
        self._broadcast(st, {"kind": "permission_request", **payload})


def _content_text(content) -> str:
    if not content:
        return ""
    parts = []
    for c in content if isinstance(content, list) else [content]:
        inner = c.get("content", c) if isinstance(c, dict) else c
        if isinstance(inner, dict) and inner.get("type") == "text":
            parts.append(inner.get("text", ""))
    return "\n".join(parts)


def _recap(db, chat_id: int, limit: int = 12, chars: int = 6000, before_id: int | None = None) -> str:
    """CLI 세션을 새로 연 대화의 첫 요청에 붙일 이전 대화 요약(최근 사용자·에이전트 메시지). before_id: 이번 요청은 빼고."""
    q = db.query(models.ChatMessage).filter(models.ChatMessage.session_id == chat_id, models.ChatMessage.role.in_(("user", "agent")))
    if before_id is not None:
        q = q.filter(models.ChatMessage.id < before_id)
    rows = q.order_by(models.ChatMessage.id.desc()).limit(limit * 2).all()
    # CLI에 닿기 전에 중지한 요청은 넘기지 않는다: 사용자가 취소한 일을 AI가 할 일로 읽으면 안 된다
    msgs = [m for m in rows if (m.meta or {}).get("stopped") != "unsent"][:limit]
    if not msgs:
        return ""
    lines = [f"{_recap_who(m)}: {m.content.strip()[:1500]}" for m in reversed(msgs)]
    body = "\n".join(lines)[-chars:]
    return f"[이전 대화 — 이 대화에서 앞서 나눈 내용이다. 맥락으로만 참고하고, 아래 새 요청을 처리하라]\n{body}\n[이전 대화 끝]\n\n"


def _recap_who(m: models.ChatMessage) -> str:
    if m.role != "user":
        return "에이전트"
    return "사용자(도중에 중지한 요청 — 다시 실행하지 말 것)" if (m.meta or {}).get("stopped") == "sent" else "사용자"


hub = AgentHub()
