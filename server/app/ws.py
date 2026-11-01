"""브라우저 WebSocket 브로드캐스트. project_id가 있는 메시지는 그 프로젝트 멤버(와 관리자)에게만 보낸다.
백그라운드 스레드에서도 publish()로 안전하게 보낼 수 있다.
결과에 관한 소식에는 어느 Task의 것인지(work_id)를 여기서 한 번에 붙인다. 발행하는 곳이 35곳이 넘어, 곳곳에서 붙이면 빠뜨린 곳에서 옆 Task의 소식이 지금 탭을 빼앗는다."""
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

    def _audience(self, project_id: int | None, message: dict[str, Any] | None = None) -> set[int] | None:
        if project_id is None:
            return None
        from . import models
        from .database import SessionLocal
        with SessionLocal() as db:
            if message is not None and "work_id" not in message:
                work_id = _work_of(db, message)
                if work_id is not None:
                    message["work_id"] = work_id
            return {m.user_id for m in db.query(models.ProjectMember).filter_by(project_id=project_id)}

    async def broadcast(self, message: dict[str, Any]):
        audience = self._audience(message.get("project_id"), message)
        text = json.dumps(jsonable_encoder(message), ensure_ascii=False)
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


def _work_of(db, message: dict[str, Any]) -> int | None:
    """이 소식이 어느 Task의 것인가. 지워진 행은 찾지 못하므로 지우는 곳이 work_id를 직접 넣는다."""
    from . import models

    kind, data = message.get("type"), message.get("data") or {}
    if kind == "flow_updated":
        row = db.get(models.Flow, data.get("id") or 0)
    elif kind == "artifact_updated":
        row = db.get(models.Artifact, data.get("id") or 0)
    elif kind == "report_updated":
        row = db.get(models.Report, data.get("id") or 0)
    elif kind == "study_updated":
        row = db.get(models.Study, data.get("id") or 0)
    elif kind == "case_updated":
        row = db.get(models.Study, data.get("study_id") or 0)
    elif kind == "model_updated":
        owner = db.get(models.FieldModel, data["field_model"]) if data.get("field_model") else db.get(models.SurrogateModel, data.get("id") or 0)
        row = db.get(models.Study, owner.study_id) if owner else None
    else:
        return None
    return row.work_id if row else None


manager = WebSocketManager()
