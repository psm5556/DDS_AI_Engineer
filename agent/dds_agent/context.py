"""프로젝트 작업 폴더에 에이전트 CLI별 컨텍스트(작업 규칙 + 스킬)를 동기화한다.
- 작업 규칙: GEMINI.md(Gemini), CLAUDE.md(Claude Code), AGENTS.md(Codex) — 내용은 agent-context/INSTRUCTIONS.md
- 스킬: .gemini/skills, .claude/skills, .agents/skills 에 복사. 규칙 파일 끝에 스킬 목록(경로)도 붙여서
  스킬 자동 탐색을 지원하지 않는 CLI도 SKILL.md를 직접 읽을 수 있게 한다.
- 새 Agent 버전에서 빠진 스킬은 프로젝트 폴더에서도 지운다. 지난번에 복사한 목록(.dds-skills.json)에 있던 것만 지우므로
  사용자가 직접 만든 스킬은 남는다.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .config import REPO_ROOT

CONTEXT_DIR = REPO_ROOT / "agent-context"
RULE_FILES = ("GEMINI.md", "CLAUDE.md", "AGENTS.md")
SKILL_DIRS = (".gemini/skills", ".claude/skills", ".agents/skills")
MANAGED = ".dds-skills.json"  # 이 Agent가 복사한 스킬 이름 목록


def _skill_index(skills_root: Path) -> str:
    lines = ["", "## 부록: 사용 가능한 스킬", "필요할 때 아래 SKILL.md를 읽고 따르세요.", ""]
    for d in sorted(p for p in skills_root.iterdir() if (p / "SKILL.md").exists()):
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        m = re.search(r"^description:\s*(.+)$", text, re.M)
        lines.append(f"- **{d.name}** — {m.group(1).strip() if m else ''}  \n  `{(d / 'SKILL.md').as_posix()}`")
    return "\n".join(lines) + "\n"


def sync_project_context(project_dir: Path) -> None:
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
