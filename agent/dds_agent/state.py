"""PC Agent가 다시 시작해도 기억할 것(agent.toml 옆 state.json): 마지막에 쓴 AI CLI·대화, CLI별 시작 시간.
- 다시 시작하면 마지막 CLI를 미리 띄워 두어 다음 요청의 대기를 없앤다.
- 시작 시간은 화면에 "지난번에는 3분 12초 걸렸습니다"로 보여 주고, 사내 환경의 실제 값을 재는 근거가 된다.
파일이 없거나 깨져도 동작한다(미리 띄우기만 하지 않는다).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def state_path() -> Path:
    # 시험·개발 실행이 실제 ~/.dds-agent를 건드리지 않게 그때의 설정 위치를 따른다
    cfg = os.environ.get("DDS_AGENT_CONFIG")
    return (Path(cfg).parent if cfg else Path.home() / ".dds-agent") / "state.json"


class AgentState:
    def __init__(self, path: Path | None = None):
        self.path = path or state_path()
        self._lock = threading.Lock()
        self.data: dict = self._read()

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.debug("state.json을 저장하지 못했습니다: %s", e)

    def remember_session(self, agent: str, session_id: str, project: tuple | None = None):
        with self._lock:
            self.data["last_agent"] = agent
            pid, pname, puid = project or (None, None, None)
            self.data["last_session"] = {"agent": agent, "session_id": session_id, "project_id": pid,
                                         "project_name": pname, "project_uid": puid}
            self._save()

    def forget_session(self, session_id: str):
        with self._lock:
            last = self.data.get("last_session") or {}
            if last.get("session_id") == session_id:
                self.data.pop("last_session", None)
                self._save()

    def record_time(self, agent: str, kind: str, seconds: float):
        """kind: start(프로그램 시작~initialize) | session(session/new·load)"""
        with self._lock:
            self.data.setdefault(f"{kind}_sec", {})[agent] = round(seconds, 1)
            self._save()

    def typical_start_sec(self, agent: str) -> float | None:
        """시작부터 대화를 받을 수 있을 때까지 걸린 지난번 시간."""
        start = (self.data.get("start_sec") or {}).get(agent)
        session = (self.data.get("session_sec") or {}).get(agent)
        if start is None and session is None:
            return None
        return round((start or 0) + (session or 0), 1)

    def typical_for_stage(self, agent: str, stage: str) -> float | None:
        """지금 단계에서 앞으로 기다릴 시간에 맞는 값. 프로그램이 이미 떠 있으면 세션을 여는 시간만 보여 준다."""
        if stage in ("session_new", "session_load"):
            return (self.data.get("session_sec") or {}).get(agent)
        return self.typical_start_sec(agent)

    def start_times(self) -> dict[str, float]:
        names = set(self.data.get("start_sec") or {}) | set(self.data.get("session_sec") or {})
        return {n: self.typical_start_sec(n) for n in names}
