"""AgentHub: 채팅 세션 ↔ 사용자 PC Agent의 Gemini 세션을 잇고, 에이전트 이벤트를 채팅 기록(DB)과 브라우저 WebSocket으로 중계한다."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from .. import models
from ..database import SessionLocal
from ..ws import manager
from .link import AgentError, RemoteRunner, registry

log = logging.getLogger(__name__)


@dataclass
class TurnState:
    chat_id: int
    project_id: int
    user_id: int
    busy: bool = False
    agent_msg_id: int | None = None
    thought_msg_id: int | None = None
    tool_msgs: dict[str, int] = field(default_factory=dict)
    pending_permissions: dict[str, dict] = field(default_factory=dict)


class AgentHub:
    def __init__(self):
        self.turns: dict[str, TurnState] = {}  # agent session id → 상태
        self.chat_sid: dict[int, str] = {}  # 대화 id → 지금 쓰는 agent session id
        self.active_project: dict[int, int] = {}  # user_id → 최근 대화한 project_id
        registry.on("agent.event", lambda conn, m: self._on_event(m.get("session_id"), m.get("update") or {}))
        registry.on("agent.permission", lambda conn, m: self._on_permission(
            m.get("session_id"), str(m.get("request_id")), m.get("params") or {}))

    def runner_for(self, user_id: int) -> RemoteRunner:
        return registry.runner(user_id)

    def forget_user(self, user_id: int):
        """Agent가 다시 연결되면 이전 Gemini 세션 상태는 무효(세션은 load_session으로 재개 시도)."""
        for sid in [s for s, st in self.turns.items() if st.user_id == user_id]:
            st = self.turns.pop(sid)
            if st.busy:
                self._add_message(st, "system", "PC Agent 연결이 끊어져 요청이 중단되었습니다.")
                self._broadcast(st, {"kind": "turn_ended", "stop_reason": "disconnected"})

    def forget_session(self, sid: str | None):
        """대화의 에이전트를 바꿀 때 이전 CLI 세션 상태를 버린다."""
        if sid:
            st = self.turns.pop(sid, None)
            if st and self.chat_sid.get(st.chat_id) == sid:
                self.chat_sid.pop(st.chat_id, None)

    def forget_chat(self, chat_id: int):
        """대화(또는 프로젝트)를 지우면 그 대화의 세션 상태도 버린다 — SQLite가 지운 id를 다시 쓰므로 꼭 필요하다."""
        for sid in [k for k, st in self.turns.items() if st.chat_id == chat_id]:
            self.turns.pop(sid, None)
        self.chat_sid.pop(chat_id, None)

    def _turn(self, chat_id: int) -> TurnState | None:
        return self.turns.get(self.chat_sid.get(chat_id, ""))

    # ---------- 세션 ----------
    def ensure_session(self, chat: models.ChatSession) -> str:
        runner = self.runner_for(chat.user_id)
        sid = chat.agent_session_id
        if sid and sid in self.turns:
            self.chat_sid[chat.id] = sid
            return sid
        pname = chat.project.name if chat.project else None
        puid = chat.project.uid if chat.project else None
        if not (sid and runner.load_session(sid, chat.project_id, chat.agent, pname, puid)):
            sid = runner.new_session(chat.project_id, chat.agent, pname, puid)
        self.turns[sid] = TurnState(chat_id=chat.id, project_id=chat.project_id, user_id=chat.user_id)
        self.chat_sid[chat.id] = sid
        return sid

    def send_prompt(self, chat_id: int, text: str, attachments: list[dict] | None = None):
        """프롬프트를 백그라운드 스레드에서 실행. 이미 진행 중이면 AgentError.
        attachments: [{id, filename, size, sha256}] — PC Agent가 프로젝트 폴더 inputs/로 내려받은 뒤 에이전트에 전달한다."""
        attachments = attachments or []
        with SessionLocal() as db:
            chat = db.get(models.ChatSession, chat_id)
            first_turn = not chat.agent_session_id
            recap = _recap(db, chat.id) if first_turn else ""  # 에이전트·작업 폴더를 바꾼 대화면 이전 내용을 넘긴다
            sid = self.ensure_session(chat)
            if chat.agent_session_id != sid:
                chat.agent_session_id = sid
            st = self.turns[sid]
            if st.busy:
                raise AgentError("에이전트가 아직 이전 요청을 처리 중입니다")
            st.busy = True
            if chat.title == "새 대화":
                chat.title = text[:40]
            db.add(models.ChatMessage(session_id=chat.id, role="user", content=text,
                                      meta={"attachments": attachments} if attachments else {}))
            db.commit()
        self.active_project[st.user_id] = st.project_id
        st.agent_msg_id = st.thought_msg_id = None
        self._broadcast(st, {"kind": "turn_started"})

        context = (f"[DDS 컨텍스트] project_id={st.project_id}. "
                   "dds-* MCP 툴과 스킬을 사용하고 작업 규칙(작업 전 Flow 제안 → 승인 후 실행, 데이터는 로컬 폴더 규칙대로)을 따르세요.\n\n"
                   + recap) if first_turn else ""

        def _run():
            stop = "error"
            try:
                stop = self.runner_for(st.user_id).prompt(sid, context + text, attachments)
            except Exception as e:
                log.exception("프롬프트 실패")
                self._add_message(st, "system", f"에이전트 오류: {e}")
            finally:
                st.busy = False
                self._broadcast(st, {"kind": "turn_ended", "stop_reason": stop})

        threading.Thread(target=_run, daemon=True).start()

    def cancel(self, chat_id: int):
        sid, st = self.chat_sid.get(chat_id), self._turn(chat_id)
        if st and st.busy:
            self.runner_for(st.user_id).cancel(sid)
            st.pending_permissions.clear()

    def respond_permission(self, chat_id: int, request_id: str, option_id: str | None):
        st = self._turn(chat_id)
        if not st or st.pending_permissions.pop(request_id, None) is None:
            raise AgentError("이미 처리되었거나 존재하지 않는 권한 요청입니다")
        self.runner_for(st.user_id).respond_permission(request_id, option_id)
        self._broadcast(st, {"kind": "permission_resolved", "request_id": request_id, "option_id": option_id})

    def status(self, chat_id: int) -> dict[str, Any]:
        st = self._turn(chat_id)
        return {"busy": bool(st and st.busy),
                "pending_permissions": list(st.pending_permissions.values()) if st else []}

    # ---------- 이벤트 ----------
    def _broadcast(self, st: TurnState, event: dict):
        manager.publish({"type": "agent_event", "project_id": st.project_id, "session_id": st.chat_id, "event": event})

    def _add_message(self, st: TurnState, role: str, content: str, meta: dict | None = None) -> int:
        with SessionLocal() as db:
            m = models.ChatMessage(session_id=st.chat_id, role=role, content=content, meta=meta or {})
            db.add(m)
            db.commit()
            return m.id

    def _append(self, msg_id: int, text: str):
        with SessionLocal() as db:
            m = db.get(models.ChatMessage, msg_id)
            m.content = (m.content or "") + text
            db.commit()

    def _on_event(self, sid: str, update: dict):
        st = self.turns.get(sid)
        if not st:
            return
        kind = update.get("sessionUpdate")
        if kind in ("agent_message_chunk", "agent_thought_chunk"):
            text = (update.get("content") or {}).get("text", "")
            if kind == "agent_message_chunk":
                if st.agent_msg_id is None:
                    st.agent_msg_id = self._add_message(st, "agent", "")
                self._append(st.agent_msg_id, text)
                msg_id = st.agent_msg_id
            else:
                if st.thought_msg_id is None:
                    st.thought_msg_id = self._add_message(st, "thought", "")
                self._append(st.thought_msg_id, text)
                msg_id = st.thought_msg_id
            self._broadcast(st, {"kind": "chunk", "role": "agent" if kind == "agent_message_chunk" else "thought",
                                 "message_id": msg_id, "text": text})
        elif kind == "tool_call":
            # 툴 호출 이후의 텍스트는 새 말풍선으로
            st.agent_msg_id = st.thought_msg_id = None
            meta = {"tool_call_id": update.get("toolCallId"), "title": update.get("title"), "kind": update.get("kind"),
                    "status": update.get("status", "pending"), "raw_input": update.get("rawInput")}
            msg_id = self._add_message(st, "tool", update.get("title") or "", meta)
            st.tool_msgs[update.get("toolCallId")] = msg_id
            self._broadcast(st, {"kind": "tool_call", "message_id": msg_id, "meta": meta})
        elif kind == "tool_call_update":
            msg_id = st.tool_msgs.get(update.get("toolCallId"))
            if msg_id is None:
                return
            with SessionLocal() as db:
                m = db.get(models.ChatMessage, msg_id)
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
        elif kind == "plan":
            self._broadcast(st, {"kind": "plan", "entries": update.get("entries", [])})

    def _on_permission(self, sid: str, request_id: str, params: dict):
        st = self.turns.get(sid)
        if not st:
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


def _recap(db, chat_id: int, limit: int = 12, chars: int = 6000) -> str:
    """CLI 세션을 새로 연 대화(에이전트·작업 폴더를 바꿈)의 첫 요청에 붙일 이전 대화 요약(최근 사용자·에이전트 메시지)."""
    msgs = (db.query(models.ChatMessage).filter(models.ChatMessage.session_id == chat_id,
                                                 models.ChatMessage.role.in_(("user", "agent")))
            .order_by(models.ChatMessage.id.desc()).limit(limit).all())
    if not msgs:
        return ""
    lines = [f"{'사용자' if m.role == 'user' else '에이전트'}: {m.content.strip()[:1500]}" for m in reversed(msgs)]
    body = "\n".join(lines)[-chars:]
    return f"[이전 대화 — 이 대화에서 앞서 나눈 내용이다. 이어서 진행하라]\n{body}\n[이전 대화 끝]\n\n"


hub = AgentHub()
