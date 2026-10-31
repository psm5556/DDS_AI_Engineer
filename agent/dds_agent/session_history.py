"""프로젝트 폴더의 실제 이름을 바꾼 뒤에도 Claude Code가 지난 대화를 찾게 한다.

Claude는 <설정 폴더>/projects/<실제 경로를 인코딩한 이름>/<세션>.jsonl 한 곳만 본다. cwd가 접합이어도 실제 경로로 풀어 찾으므로
옛 이름 접합을 넘겨도 소용이 없다(claude-agent-acp 0.96과 번들 claude.exe로 확인). 그래서 불러오기 직전에 기록을 지금 경로의 폴더로 옮긴다.
Gemini는 경로 문자열로, Codex는 세션 id로 찾으므로 옮기지 않는다(main.py _load_cwds).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

log = logging.getLogger("dds_agent.session_history")

# 이보다 길면 CLI가 이름을 자르고 해시를 붙이는데, 해시 계산이 CLI(Bun)와 SDK(Node)에서 다르다. 맞히려 하지 않는다
CLAUDE_NAME_MAX = 200
_SAFE_ID = re.compile(r"[\w-]+")


def claude_projects_dir(env: dict | None = None) -> Path:
    config = (env or os.environ).get("CLAUDE_CONFIG_DIR")
    return (Path(config) if config else Path.home() / ".claude") / "projects"


def encode_claude_path(path: str) -> str:
    """영숫자가 아닌 글자를 -로 바꾼다. JS 문자열 기준이라 UTF-16 두 칸을 쓰는 글자는 -- 가 된다."""
    return re.sub(r"[^a-zA-Z0-9]", lambda m: "--" if ord(m.group()) > 0xFFFF else "-", path)


def move_claude_session(session_id: str, cwd: Path | str, env: dict | None = None) -> bool:
    """기록이 다른 프로젝트 폴더(옛 이름)에 있으면 지금 경로의 폴더로 옮긴다. 옮겼으면 True.
    Claude의 내부 구조에 기대므로, 기록을 못 찾거나 구조가 달라져도 아무것도 지우지 않고 넘어간다. 그때는 불러오기가 실패해 요약으로 잇는다."""
    if not _SAFE_ID.fullmatch(session_id or ""):
        return False
    root = claude_projects_dir(env)
    name = encode_claude_path(os.path.realpath(cwd))
    if len(name) > CLAUDE_NAME_MAX:
        log.info("경로가 길어 Claude 세션 기록을 옮기지 않습니다: %s", cwd)
        return False
    target = root / name / f"{session_id}.jsonl"
    if target.exists():
        return False
    found = next(root.glob(f"*/{session_id}.jsonl"), None)
    if found is None:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(found, target)
    extra = found.with_suffix("")  # 서브에이전트 기록·도구 결과. 세션 id 이름의 폴더
    if extra.is_dir() and not target.with_suffix("").exists():
        os.rename(extra, target.with_suffix(""))
    log.info("Claude 세션 기록을 옮겼습니다: %s → %s", found.parent.name, target.parent.name)
    return True
