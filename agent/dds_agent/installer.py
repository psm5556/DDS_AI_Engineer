"""에이전트 CLI 설치(npm 전역 설치). 사내 npm 미러는 `npm config set registry <미러주소>`로 설정한다."""
from __future__ import annotations

import shutil
import subprocess

from .config import AgentSettings


def install(settings: AgentSettings, name: str) -> dict:
    spec = settings.agents.get(name)
    if not spec or not spec.get("packages"):
        raise ValueError(f"설치할 수 없는 에이전트입니다: {name}")
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("npm을 찾을 수 없습니다. Node.js(20 이상)를 먼저 설치하세요.")
    cmd = [npm, "install", "-g", *spec["packages"]]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if p.returncode != 0:
        raise RuntimeError(f"설치 실패({p.returncode}):\n{(p.stderr or p.stdout)[-2000:]}")
    return {"name": name, "installed": True, "login": spec.get("login", ""), "log": p.stdout[-1000:]}
