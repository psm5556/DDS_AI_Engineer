"""프로젝트 작업 폴더에 에이전트 CLI별 컨텍스트(작업 규칙 + 스킬)를 동기화한다.
- 작업 규칙: GEMINI.md(Gemini), CLAUDE.md(Claude Code), AGENTS.md(Codex) — 내용은 agent-context/INSTRUCTIONS.md
- 스킬: .gemini/skills, .claude/skills, .agents/skills 에 복사. 규칙 파일 끝에 스킬 목록(경로)도 붙여서
  스킬 자동 탐색을 지원하지 않는 CLI도 SKILL.md를 직접 읽을 수 있게 한다.
- 새 Agent 버전에서 빠진 스킬은 프로젝트 폴더에서도 지운다. 지난번에 복사한 목록(.dds-skills.json)에 있던 것만 지우므로
  사용자가 직접 만든 스킬은 남는다.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from .config import REPO_ROOT

CONTEXT_DIR = REPO_ROOT / "agent-context"
RULE_FILES = ("GEMINI.md", "CLAUDE.md", "AGENTS.md")
SKILL_DIRS = (".gemini/skills", ".claude/skills", ".agents/skills")
MANAGED = ".dds-skills.json"  # 이 Agent가 복사한 스킬 이름 목록
STAMP = ".dds-context-stamp"  # 마지막으로 복사한 규칙·스킬의 지문


def _skill_index(skills_root: Path) -> str:
    lines = ["", "## 부록: 사용 가능한 스킬", "필요할 때 아래 SKILL.md를 읽고 따르세요.", ""]
    for d in sorted(p for p in skills_root.iterdir() if (p / "SKILL.md").exists()):
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        m = re.search(r"^description:\s*(.+)$", text, re.M)
        lines.append(f"- **{d.name}** — {m.group(1).strip() if m else ''}  \n  `{(d / 'SKILL.md').as_posix()}`")
    return "\n".join(lines) + "\n"


def _source_stamp() -> str:
    """배포된 규칙·스킬의 지문. 파일 내용을 읽지 않고 이름·크기·수정 시각만 본다(보안 프로그램이 읽을 때마다 검사한다)."""
    h = hashlib.sha1()
    for p in sorted(CONTEXT_DIR.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            st = p.stat()
            h.update(f"{p.relative_to(CONTEXT_DIR).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()


def _up_to_date(project_dir: Path, stamp: str) -> bool:
    try:
        if (project_dir / STAMP).read_text(encoding="utf-8").strip() != stamp:
            return False
    except OSError:
        return False
    return all((project_dir / n).is_file() for n in RULE_FILES) and all((project_dir / d).is_dir() for d in SKILL_DIRS)


def sync_project_context(project_dir: Path) -> bool:
    """복사했으면 True, 내용이 그대로라 건너뛰었으면 False. 세션마다 파일 수백 개를 복사하면 보안 검사에 걸려 대화 시작이 늦어진다."""
    stamp = _source_stamp()
    if _up_to_date(project_dir, stamp):
        return False
    skills_src = CONTEXT_DIR / "skills"
    names = sorted(p.name for p in skills_src.iterdir() if p.is_dir())
    marker = project_dir / MANAGED
    try:
        before = json.loads(marker.read_text(encoding="utf-8"))
        # 폴더 이름만 받는다 — 목록이 망가져도 스킬 폴더 밖을 지우지 않게
        gone = {n for n in before if isinstance(n, str) and n == Path(n).name and not n.startswith(".")} - set(names)
    except (OSError, ValueError, TypeError):
        gone = set()  # 처음이거나 목록이 깨졌으면 지우지 않는다
    for rel in SKILL_DIRS:
        dst = project_dir / rel
        dst.mkdir(parents=True, exist_ok=True)
        for name in gone:
            shutil.rmtree(dst / name, ignore_errors=True)
        for name in names:
            shutil.copytree(skills_src / name, dst / name, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    marker.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
    rules = (CONTEXT_DIR / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    rules += _skill_index(project_dir / ".claude" / "skills")
    for name in RULE_FILES:
        (project_dir / name).write_text(rules, encoding="utf-8")
    (project_dir / STAMP).write_text(stamp, encoding="utf-8")  # 다 쓴 뒤에 남긴다. 중간에 끊기면 다음에 다시 복사한다
    return True
