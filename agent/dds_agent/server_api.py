"""PC Agent와 MCP 서버가 공유하는 서버 REST 클라이언트(사용자 API 토큰 인증)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx

from .config import AgentSettings, load_settings


class ServerError(Exception):
    pass


class ServerApi:
    def __init__(self, settings: AgentSettings | None = None):
        self.s = settings or load_settings()
        self.client = httpx.Client(base_url=self.s.server_url, timeout=60,
                                   headers={"Authorization": f"Bearer {self.s.token}"})

    def _check(self, r: httpx.Response) -> Any:
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = r.text
            raise ServerError(f"서버 오류 {r.status_code}: {detail}")
        return r.json()

    def get(self, path: str, **params) -> Any:
        return self._check(self.client.get(path, params=params or None))

    def post(self, path: str, body: dict | None = None, timeout: float | None = None) -> Any:
        # timeout: 행이 많은 등록처럼 60초를 넘을 수 있는 호출만 늘린다
        kw = {"timeout": timeout} if timeout else {}
        return self._check(self.client.post(path, json=body or {}, **kw))

    def put(self, path: str, body: dict | None = None) -> Any:
        return self._check(self.client.put(path, json=body or {}))

    def delete(self, path: str) -> Any:
        return self._check(self.client.delete(path))

    def upload_image(self, project_id: int, file: Path, subdir: str = "images") -> str:
        with open(file, "rb") as f:
            r = self.client.post(f"/api/projects/{project_id}/files", files={"file": (file.name, f)},
                                 data={"subdir": subdir})
        return self._check(r)["path"]

    def upload(self, path: str, file: Path) -> dict:
        with open(file, "rb") as f:
            return self._check(self.client.post(path, files={"file": (file.name, f)}, timeout=600))

    def upload_form(self, path: str, files: dict[str, Path | tuple[str, bytes]], data: dict[str, str] | None = None, timeout: float = 600) -> Any:
        """파일 여러 개와 폼 값을 함께 올린다. 파일은 경로 또는 (이름, 내용)."""
        opened, parts = [], {}
        try:
            for key, f in files.items():
                if isinstance(f, tuple):
                    parts[key] = f
                else:
                    fh = open(f, "rb")
                    opened.append(fh)
                    parts[key] = (Path(f).name, fh)
            return self._check(self.client.post(path, files=parts, data=data or {}, timeout=timeout))
        finally:
            for fh in opened:
                fh.close()

    def download(self, path: str, dest: Path) -> str:
        """파일을 받아 저장하고 sha256을 돌려준다."""
        h = hashlib.sha256()
        with self.client.stream("GET", path, timeout=600) as r:
            if r.status_code >= 400:
                r.read()
                self._check(r)
            with open(dest, "wb") as f:
                for chunk in r.iter_bytes(1 << 16):
                    f.write(chunk)
                    h.update(chunk)
        return h.hexdigest()

    def resolve_project(self, project_id: int | None) -> int:
        if project_id:
            return int(project_id)
        return int(self.get("/api/agent/active-project")["project_id"])

