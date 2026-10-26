"""브라우저 WebSocket 브로드캐스트. project_id가 있는 메시지는 그 프로젝트 멤버(와 관리자)에게만 보낸다.
백그라운드 스레드에서도 publish()로 안전하게 보낼 수 있다."""
import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

log = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self):
        self.active: dict[WebSocket, tuple[int, bool]] = {}  # ws → (user_id, is_admin)
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop

    async def connect(self, ws: WebSocket, user_id: int, is_admin: bool):
        await ws.accept()
        self.active[ws] = (user_id, is_admin)

    def disconnect(self, ws: WebSocket):
        self.active.pop(ws, None)

    def _audience(self, project_id: int | None) -> set[int] | None:
        if project_id is None:
            return None
        from . import models
        from .database import SessionLocal
        with SessionLocal() as db:
            return {m.user_id for m in db.query(models.ProjectMember).filter_by(project_id=project_id)}

    async def broadcast(self, message: dict[str, Any]):
        text = json.dumps(jsonable_encoder(message), ensure_ascii=False)
        audience = self._audience(message.get("project_id"))
        for ws, (uid, admin) in list(self.active.items()):
            if audience is not None and uid not in audience and not admin:
                continue
            try:
                await ws.send_text(text)
            except Exception:
                self.disconnect(ws)

    def publish(self, message: dict[str, Any]):
        """동기 코드/다른 스레드에서 호출용."""
        if self.loop is None or self.loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self.loop.create_task(self.broadcast(message))
        else:
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)


manager = WebSocketManager()
