"""CAE 툴 어댑터 규격.
새 툴(LS-DYNA, Ansys, STAR-CCM+ ...)은 CaeAdapter를 상속해 run_case()만 구현하고 REGISTRY에 등록하면
DOE/최적화/에이전트 MCP 툴에서 바로 쓸 수 있다.
"""
from __future__ import annotations

import json
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TEMPLATES = Path(__file__).parent / "templates"


@dataclass
class CaseOutcome:
    status: str  # complete | failed | dry_run
    results: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    files: list[str] = field(default_factory=list)


class CaeAdapter(ABC):
    name: str = ""
    label: str = ""
    description: str = ""
    config_example: dict[str, Any] = {}

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "description": self.description,
                "config_example": self.config_example}

    @abstractmethod
    def run_case(self, params: dict[str, float], run_dir: Path, config: dict[str, Any],
                 runner, tools: dict[str, str], dry_run: bool = False, **kwargs) -> CaseOutcome:
        ...

    def extract(self, params: dict[str, float], run_dir: Path, config: dict[str, Any], steps: list[dict],
                names: list[str], runner, tools: dict[str, str], **kwargs) -> CaseOutcome:
        """이미 해석을 마친 케이스 폴더에서 Output(names)만 다시 뽑는다(해석은 다시 하지 않는다). results에는 새로 뽑은 값만 담는다."""
        return CaseOutcome("failed", error=f"'{self.label or self.name}' 실행 방식은 Output 추가 추출을 지원하지 않습니다")


def resolve_cmd(exe: str) -> list[str]:
    """Windows .bat/.cmd(abaqus.bat 등)는 cmd /c 로 감싼다."""
    path = shutil.which(exe) or exe
    if path.lower().endswith((".bat", ".cmd")):
        return ["cmd", "/c", path]
    return [path]


def write_json(path: Path, data: Any):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def read_results(path: Path) -> dict[str, float]:
    if not path.exists():
        raise FileNotFoundError(f"결과 파일이 생성되지 않았습니다: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}


def read_results_if_any(path: Path) -> dict[str, float]:
    """results.json이 있으면 읽고, 없거나 깨졌으면 빈 결과(단계마다 쓴 값을 모을 때)."""
    try:
        return read_results(path)
    except (OSError, ValueError, AttributeError):
        return {}
