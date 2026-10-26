"""프로젝트 작업 폴더에 에이전트 CLI별 컨텍스트(작업 규칙 + 스킬)를 동기화한다.
- 작업 규칙: GEMINI.md(Gemini), CLAUDE.md(Claude Code), AGENTS.md(Codex) — 내용은 agent-context/INSTRUCTIONS.md
- 스킬: .gemini/skills, .claude/skills, .agents/skills 에 복사. 규칙 파일 끝에 스킬 목록(경로)도 붙여서
  스킬 자동 탐색을 지원하지 않는 CLI도 SKILL.md를 직접 읽을 수 있게 한다.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .config import REPO_ROOT

CONTEXT_DIR = REPO_ROOT / "agent-context"
RULE_FILES = ("GEMINI.md", "CLAUDE.md", "AGENTS.md")
SKILL_DIRS = (".gemini/skills", ".claude/skills", ".agents/skills")


def _skill_index(skills_root: Path) -> str:
    lines = ["", "## 부록: 사용 가능한 스킬", "필요할 때 아래 SKILL.md를 읽고 따르세요.", ""]
    for d in sorted(p for p in skills_root.iterdir() if (p / "SKILL.md").exists()):
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        m = re.search(r"^description:\s*(.+)$", text, re.M)
        lines.append(f"- **{d.name}** — {m.group(1).strip() if m else ''}  \n  `{(d / 'SKILL.md').as_posix()}`")
    return "\n".join(lines) + "\n"


def sync_project_context(project_dir: Path) -> None:
    skills_src = CONTEXT_DIR / "skills"
    for rel in SKILL_DIRS:
        dst = project_dir / rel
        dst.mkdir(parents=True, exist_ok=True)
        for skill in skills_src.iterdir():
            if skill.is_dir():
                shutil.copytree(skill, dst / skill.name, dirs_exist_ok=True)
    rules = (CONTEXT_DIR / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    rules += _skill_index(project_dir / ".claude" / "skills")
    for name in RULE_FILES:
        (project_dir / name).write_text(rules, encoding="utf-8")
