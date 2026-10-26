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

    # ---------- 세션 ----------
    def ensure_session(self, chat: models.ChatSession) -> str:
        runner = self.runner_for(chat.user_id)
        sid = chat.agent_session_id
        if sid and sid in self.turns:
            return sid
        if not (sid and runner.load_session(sid, chat.project_id, chat.agent)):
            sid = runner.new_session(chat.project_id, chat.agent)
        self.turns[sid] = TurnState(chat_id=chat.id, project_id=chat.project_id, user_id=chat.user_id)
        return sid

    def send_prompt(self, chat_id: int, text: str):
        """프롬프트를 백그라운드 스레드에서 실행. 이미 진행 중이면 AgentError."""
        with SessionLocal() as db:
            chat = db.get(models.ChatSession, chat_id)
            first_turn = not chat.agent_session_id
            sid = self.ensure_session(chat)
            if chat.agent_session_id != sid:
                chat.agent_session_id = sid
            st = self.turns[sid]
            if st.busy:
                raise AgentError("에이전트가 아직 이전 요청을 처리 중입니다")
            st.busy = True
            if chat.title == "새 대화":
                chat.title = text[:40]
            db.add(models.ChatMessage(session_id=chat.id, role="user", content=text))
            db.commit()
        self.active_project[st.user_id] = st.project_id
        st.agent_msg_id = st.thought_msg_id = None
        self._broadcast(st, {"kind": "turn_started"})

        context = (f"[DDS 컨텍스트] project_id={st.project_id}. "
                   "dds-* MCP 툴과 스킬을 사용하고 GEMINI.md 규칙(작업 전 Flow 제안 → 승인 후 실행)을 따르세요.\n\n") if first_turn else ""

        def _run():
            stop = "error"
            try:
                stop = self.runner_for(st.user_id).prompt(sid, context + text)
            except Exception as e:
                log.exception("프롬프트 실패")
                self._add_message(st, "system", f"에이전트 오류: {e}")
            finally:
                st.busy = False
                self._broadcast(st, {"kind": "turn_ended", "stop_reason": stop})

        threading.Thread(target=_run, daemon=True).start()

    def cancel(self, chat_id: int):
        for sid, st in self.turns.items():
            if st.chat_id == chat_id and st.busy:
                self.runner_for(st.user_id).cancel(sid)
                st.pending_permissions.clear()

    def respond_permission(self, chat_id: int, request_id: str, option_id: str | None):
        st = next((s for s in self.turns.values() if s.chat_id == chat_id), None)
        if not st or st.pending_permissions.pop(request_id, None) is None:
            raise AgentError("이미 처리되었거나 존재하지 않는 권한 요청입니다")
        self.runner_for(st.user_id).respond_permission(request_id, option_id)
        self._broadcast(st, {"kind": "permission_resolved", "request_id": request_id, "option_id": option_id})

    def status(self, chat_id: int) -> dict[str, Any]:
        st = next((s for s in self.turns.values() if s.chat_id == chat_id), None)
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


hub = AgentHub()
