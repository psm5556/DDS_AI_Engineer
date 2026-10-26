"""자동 업데이트. 설치 폴더(DDS_AGENT_HOME, launcher가 설정) 구조:
  venv/  versions/<버전>/  current.txt  previous.txt  launcher.py
새 버전을 versions/<버전>/에 받아 검증(sha256)·의존성 설치 후 current.txt를 바꾸고,
Agent가 종료코드 75로 끝나면 launcher가 새 버전으로 다시 띄운다. 개발 모드(DDS_AGENT_HOME 없음)에서는 업데이트하지 않는다.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from . import __version__
from .server_api import ServerApi

log = logging.getLogger(__name__)
RESTART_CODE = 75


def version_key(v: str | None) -> tuple:
    try:
        return tuple(int(x) for x in (v or "0").split("."))
    except ValueError:
        return (0,)


def install_home() -> Path | None:
    h = os.environ.get("DDS_AGENT_HOME")
    return Path(h) if h else None


def is_newer(version: str) -> bool:
    return version_key(version) > version_key(__version__)


def apply_update(api: ServerApi, version: str, sha256: str | None = None) -> bool:
    """새 버전을 설치하고 current.txt를 전환한다. 성공하면 True(재시작 필요)."""
    home = install_home()
    if home is None:
        log.info("개발 모드라 자동 업데이트를 건너뜁니다 (새 버전 %s)", version)
        return False
    target = home / "versions" / version
    if not (target / ".installed").exists():
        dl = home / "downloads" / f"dds-agent-{version}.zip"
        dl.parent.mkdir(parents=True, exist_ok=True)
        got_sha = api.download(f"/api/agent/releases/{version}/download", dl)
        if sha256 and got_sha != sha256:
            dl.unlink(missing_ok=True)
            raise RuntimeError(f"다운로드 파일 검증 실패 (sha256 불일치): {version}")
        tmp = target.with_name(version + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(dl) as z:
            z.extractall(tmp)
        req = tmp / "requirements-agent.txt"
        if req.exists():
            log.info("의존성 설치 중...")
            p = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(req)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            if p.returncode != 0:
                raise RuntimeError(f"의존성 설치 실패:\n{p.stderr[-1500:]}")
        shutil.rmtree(target, ignore_errors=True)
        tmp.rename(target)
        (target / ".installed").write_text(version, encoding="utf-8")
    current = (home / "current.txt").read_text(encoding="utf-8").strip() if (home / "current.txt").exists() else ""
    if current and current != version:
        (home / "previous.txt").write_text(current, encoding="utf-8")
    (home / "current.txt").write_text(version, encoding="utf-8")
    log.info("업데이트 준비 완료: %s → %s (재시작)", __version__, version)
    return True
