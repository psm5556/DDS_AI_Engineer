"""Windows 트레이 아이콘: PC Agent 설치·연결 상태를 작업 표시줄에서 바로 확인한다.
아이콘 색: 초록=연결됨, 회색=연결 중, 빨강=오류(토큰/서버), 주황=업데이트 중.
메뉴: 웹앱 열기 · 상태 · 작업 폴더 · 로그 · 업데이트 확인 · 다시 시작 · 종료
pystray/Pillow가 없거나 트레이를 쓸 수 없는 환경이면 조용히 건너뛴다.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

COLORS = {"connected": (22, 163, 74), "connecting": (161, 161, 170), "error": (220, 38, 38), "updating": (245, 158, 11)}
LABELS = {"connected": "연결됨", "connecting": "연결 중", "error": "오류", "updating": "업데이트 중"}


def _icon_image(state: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(37, 99, 235))  # 앱 로고 파랑
    d.line((14, 44, 24, 20, 33, 38, 40, 28, 50, 46), fill="white", width=6, joint="curve")
    c = COLORS.get(state, COLORS["connecting"])
    d.ellipse((40, 40, 62, 62), fill=c, outline="white", width=3)  # 상태 점
    return img


class Tray:
    def __init__(self, version: str, server_url: str, workspace: Path, log_dir: Path | None,
                 on_check_update: Callable[[], None], on_restart: Callable[[], None], on_quit: Callable[[], None]):
        self.version, self.server_url, self.workspace, self.log_dir = version, server_url, workspace, log_dir
        self.on_check_update, self.on_restart, self.on_quit = on_check_update, on_restart, on_quit
        self.state, self.detail = "connecting", ""
        self.icon = None

    def start(self) -> bool:
        try:
            import pystray
        except Exception as e:  # pystray/Pillow 미설치 또는 트레이 미지원
            log.info("트레이 아이콘을 쓰지 않습니다: %s", e)
            return False
        item = pystray.MenuItem
        menu = pystray.Menu(
            item("웹앱 열기", lambda: webbrowser.open(self.server_url), default=True),
            item(lambda _: f"상태: {LABELS.get(self.state, self.state)}{(' · ' + self.detail) if self.detail else ''}", None, enabled=False),
            item(lambda _: f"버전 {self.version}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            item("작업 폴더 열기", lambda: _open(self.workspace)),
            item("로그 보기", lambda: _open(self.log_dir or self.workspace), visible=bool(self.log_dir)),
            item("업데이트 확인", lambda: self.on_check_update()),
            item("다시 시작", lambda: self.on_restart()),
            pystray.Menu.SEPARATOR,
            item("종료", lambda: self._quit()),
        )
        self.icon = pystray.Icon("dds-agent", _icon_image(self.state), self._title(), menu)
        threading.Thread(target=self.icon.run, daemon=True, name="tray").start()
        log.info("트레이 아이콘 표시")
        return True

    def _title(self) -> str:
        return f"DDS Agent {self.version} — {LABELS.get(self.state, self.state)}"[:127]

    def set(self, state: str, detail: str = ""):
        if (state, detail) == (self.state, self.detail):
            return
        self.state, self.detail = state, detail
        if self.icon:
            try:
                self.icon.icon = _icon_image(state)
                self.icon.title = self._title()
                self.icon.update_menu()
            except Exception:
                pass

    def notify(self, text: str):
        if self.icon:
            try:
                self.icon.notify(text, "DDS Agent")
            except Exception:
                pass

    def _quit(self):
        self.on_quit()
        self.stop()

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


def _open(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 — 탐색기로 폴더 열기
    else:
        subprocess.Popen(["xdg-open", str(path)])
