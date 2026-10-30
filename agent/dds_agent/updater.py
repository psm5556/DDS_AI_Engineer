"""자동 업데이트. 설치 폴더(DDS_AGENT_HOME, launcher가 설정) 구조:
  venv/  versions/<버전>/  current.txt  previous.txt  launcher.py
새 버전을 versions/<버전>/에 받아 검증(sha256)·의존성 설치까지 해 두고(prepare_update), 실행 중인 Agent에는 손대지 않는다.
일이 없을 때 current.txt를 바꾸고(activate) Agent가 종료코드 75로 끝나면 launcher가 새 버전으로 다시 띄운다.
개발 모드(DDS_AGENT_HOME 없음)에서는 업데이트하지 않는다.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from cae_adapters.hidden import console_python, hidden_kwargs

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


def prepare_update(api: ServerApi, version: str, sha256: str | None = None) -> None:
    """새 버전을 받아 설치만 해 둔다. 이미 받아 둔 버전이면 다시 받지 않는다."""
    home = install_home()
    if home is None:
        raise RuntimeError("개발 모드에서는 업데이트하지 않습니다")
    target = home / "versions" / version
    if (target / ".installed").exists():
        return
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
        p = subprocess.run([console_python(), "-m", "pip", "install", "-q", "-r", str(req)], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", **hidden_kwargs())
        if p.returncode != 0:
            raise RuntimeError(f"의존성 설치 실패:\n{p.stderr[-1500:]}")
    shutil.rmtree(target, ignore_errors=True)
    tmp.rename(target)
    (target / ".installed").write_text(version, encoding="utf-8")


def activate(version: str) -> None:
    """다음 실행부터 새 버전을 쓰게 current.txt를 바꾼다. 바로 다음에 Agent를 다시 시작해야 한다."""
    home = install_home()
    if home is None:
        raise RuntimeError("개발 모드에서는 업데이트하지 않습니다")
    current = (home / "current.txt").read_text(encoding="utf-8").strip() if (home / "current.txt").exists() else ""
    if current and current != version:
        (home / "previous.txt").write_text(current, encoding="utf-8")
    (home / "current.txt").write_text(version, encoding="utf-8")
    log.info("업데이트 준비 완료: %s → %s (재시작)", __version__, version)
