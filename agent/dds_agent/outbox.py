"""서버로 꼭 전해야 하는 메시지(대화 이벤트·턴 끝·권한 요청·케이스 결과)를 답을 받을 때까지 쥐고 있는 상자.

연결이 끊겨도 PC의 CLI는 계속 돈다. 그동안의 출력과 턴 끝을 버리면 화면이 "중단"으로 남고 사용자는 멀쩡한 작업을 다시 시킨다.
그래서 서버가 ack할 때까지 지우지 않고, 다시 연결되면 순서대로 처음부터 보낸다(서버는 boot·seq로 중복을 거른다).
반쯤 끊긴 TCP에 써 버린 메시지도 ack가 없으니 사라지지 않는다.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import deque
from dataclasses import dataclass

log = logging.getLogger(__name__)

MAX_BYTES = 64 * 1024 * 1024  # 넘으면 오래된 대화 출력부터 버린다. 턴 끝·권한 요청은 버리지 않는다
MAX_EVENT_CHARS = 256 * 1024  # 서버 WebSocket 한도(16MB)에 걸리면 연결이 끊긴다. 도구 입출력은 넉넉히 줄여 보낸다
MAX_ITEM_BYTES = 8 * 1024 * 1024  # 마지막 안전장치. 답을 받기 전에는 지우지 않으므로 한도를 넘는 메시지 하나가 재연결마다 연결을 끊어 대화 전체를 막는다
_TEXT_LIMIT = 32 * 1024
_CUT_NOTE = "\n…(너무 길어 잘렸습니다. 전체는 내 PC의 작업 폴더에 있습니다)"


@dataclass
class _Item:
    seq: int
    text: str
    type: str
    session_id: str | None
    turn: dict | None = None  # agent.turn_end의 턴 정보(hello에 함께 알린다)
    case_id: int | None = None  # case.status의 케이스(서버가 받기 전에는 hello에 함께 알린다)


class Outbox:
    def __init__(self, boot: str, max_bytes: int = MAX_BYTES):
        self.boot, self.max_bytes = boot, max_bytes
        self._items: deque[_Item] = deque()
        self._lock = threading.Lock()
        self._seq = 0
        self._bytes = 0
        self._dropped: dict[str, int] = {}  # 세션 → 버린 출력 수(턴 끝에 함께 알린다)

    def add(self, msg: dict) -> int:
        msg = fit_message(msg)
        with self._lock:
            self._seq += 1
            full = {**msg, "seq": self._seq, "boot": self.boot}
            text = json.dumps(full, ensure_ascii=False, default=str)
            turn = None
            if msg.get("type") == "agent.turn_end":
                turn = {k: msg.get(k) for k in ("turn_id", "chat_id", "session_id", "agent", "started_at")}
            case_id = msg.get("case_id") if msg.get("type") == "case.status" else None
            self._items.append(_Item(self._seq, text, msg.get("type", ""), msg.get("session_id"), turn, case_id))
            self._bytes += len(text)
            if self._bytes > self.max_bytes:
                self._trim()
            return self._seq

    def _trim(self):
        keep: deque[_Item] = deque()
        for it in self._items:
            if self._bytes > self.max_bytes and it.type == "agent.event":
                self._bytes -= len(it.text)
                if it.session_id:
                    self._dropped[it.session_id] = self._dropped.get(it.session_id, 0) + 1
            else:
                keep.append(it)
        self._items = keep

    def take_dropped(self, session_id: str | None) -> int:
        with self._lock:
            return self._dropped.pop(session_id, 0) if session_id else 0

    def ack(self, seq: int):
        with self._lock:
            while self._items and self._items[0].seq <= seq:
                self._bytes -= len(self._items.popleft().text)

    def pending_cases(self) -> list[int]:
        """결과를 아직 서버에 전하지 못한 케이스."""
        with self._lock:
            return sorted({it.case_id for it in self._items if it.case_id is not None})

    def after(self, seq: int) -> list[tuple[int, str]]:
        """seq보다 뒤의 메시지(순서대로)."""
        with self._lock:
            return [(it.seq, it.text) for it in self._items if it.seq > seq]

    def ended_turns(self) -> list[dict]:
        """끝났지만 서버가 아직 모르는 턴. hello에 함께 보내, 서버가 이어 오는 출력을 받을 준비를 하게 한다."""
        with self._lock:
            return [it.turn for it in self._items if it.turn]

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)


def _cut(text, limit: int = _TEXT_LIMIT):
    return text[:limit] + _CUT_NOTE if isinstance(text, str) and len(text) > limit else text


def _cut_input(raw):
    """rawInput(도구 인자)이 크면 앞부분만 글자로 남긴다. 스크립트 본문이나 큰 표가 여기로 온다."""
    try:
        size = len(json.dumps(raw, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return "(표시할 수 없는 입력)"
    if size <= _TEXT_LIMIT:
        return raw
    return {"잘림": True, "크기": size, "앞부분": json.dumps(raw, ensure_ascii=False, default=str)[:_TEXT_LIMIT // 4]}


def _json_size(obj) -> int:
    try:
        return len(json.dumps(obj, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return MAX_EVENT_CHARS + 1


def shrink_update(update: dict) -> list[dict]:
    """ACP session/update를 보낼 크기로 만든다. 답변 글자는 잃지 않게 나눠서, 도구 입출력은 잘라서 보낸다."""
    kind = update.get("sessionUpdate")
    if kind in ("agent_message_chunk", "agent_thought_chunk"):
        content = update.get("content")
        text = content.get("text") if isinstance(content, dict) else None
        if isinstance(text, str):
            if len(text) <= MAX_EVENT_CHARS:
                return [update]
            return [{**update, "content": {**content, "text": text[i:i + MAX_EVENT_CHARS]}} for i in range(0, len(text), MAX_EVENT_CHARS)]
        if _json_size(update) <= MAX_EVENT_CHARS:  # 글자가 아닌 조각(이미지 등)
            return [update]
        return [{**update, "content": {"type": "text", "text": "\n(너무 큰 이미지·자료라 대화에 보여 주지 못했습니다)\n"}}]
    if _json_size(update) <= MAX_EVENT_CHARS:
        return [update]
    return [_shrink_tool(update)]


def _shrink_tool(update: dict) -> dict:
    """도구 호출 정보(session/update와 권한 요청의 toolCall)의 큰 입출력을 줄인다. 무엇을 하는 도구인지는 남긴다."""
    small = dict(update)
    if "rawInput" in small:
        small["rawInput"] = _cut_input(small["rawInput"])
    if "rawOutput" in small:
        small["rawOutput"] = _cut_input(small["rawOutput"])
    content = small.get("content")
    if isinstance(content, list):
        out = []
        for c in content:
            inner = c.get("content") if isinstance(c, dict) else None
            if isinstance(inner, dict) and "text" in inner:
                out.append({**c, "content": {**inner, "text": _cut(inner["text"])}})
            elif isinstance(c, dict) and c.get("type") == "text":
                out.append({**c, "text": _cut(c.get("text"))})
            elif isinstance(c, dict) and c.get("type") == "diff":  # 큰 파일 수정. 글자가 어디에 쓰이는지와 상관없이 줄인다
                out.append({"type": c.get("type"), "path": c.get("path")})
            else:
                out.append(c)
        small["content"] = out
    if _json_size(small) > MAX_EVENT_CHARS:  # 모르는 필드까지 큰 경우의 마지막 안전장치
        small = {k: small[k] for k in ("sessionUpdate", "toolCallId", "title", "kind", "status") if k in small and _json_size(small[k]) < _TEXT_LIMIT}
    return small


def shrink_permission(params: dict) -> dict:
    """권한 요청. Gemini는 AI가 고칠 파일 전체를 toolCall.content의 diff로 보낸다(큰 입력 덱이면 수십 MB). 카드에는 제목과 입력만 쓴다."""
    tc = params.get("toolCall")
    if not isinstance(tc, dict) or _json_size(tc) <= MAX_EVENT_CHARS:
        return params
    return {**params, "toolCall": _shrink_tool(tc)}


def _cut_deep(obj, depth: int = 0):
    if isinstance(obj, str):
        return _cut(obj)
    if depth > 8:
        return None
    if isinstance(obj, dict):
        return {k: _cut_deep(v, depth + 1) for k, v in list(obj.items())[:200]}
    if isinstance(obj, (list, tuple)):
        return [_cut_deep(v, depth + 1) for v in list(obj)[:200]]
    return obj


def fit_message(msg: dict) -> dict:
    """어떤 경로로 오든 outbox에는 한도 안의 메시지만 넣는다. 위의 줄이기가 모르는 모양이 와도 연결이 막히지 않게 한다."""
    try:
        size = len(json.dumps(msg, ensure_ascii=False, default=str).encode("utf-8"))
    except (TypeError, ValueError):
        size = MAX_ITEM_BYTES + 1
    if size <= MAX_ITEM_BYTES:
        return msg
    log.warning("서버로 보낼 메시지가 너무 커서 줄입니다: %s, %d바이트", msg.get("type"), size)
    if msg.get("type") == "case.status":  # 결과를 잘라 보내면 값 일부만 완료로 남는다. 어느 케이스가 왜 안 됐는지 알린다
        why = "결과가 너무 커서 서버로 보내지 못했습니다 → results.json에는 숫자 결과만 쓰고 큰 데이터는 케이스 폴더에 파일로 두세요"
        if msg.get("extract"):
            return {"type": "case.status", "case_id": msg.get("case_id"), "status": "complete", "extract": True, "results": {}, "error": f"Output 추출 실패: {why}"}
        return {"type": "case.status", "case_id": msg.get("case_id"), "status": "failed", "error": why}
    small = _cut_deep(msg)
    if len(json.dumps(small, ensure_ascii=False, default=str).encode("utf-8")) <= MAX_ITEM_BYTES:
        return small
    return {k: msg[k] for k in ("type", "session_id", "request_id", "turn_id", "chat_id", "agent", "stop_reason") if k in msg}
