"""PC Agent 자가 진단: 이 PC에서 CAD/CAE 툴·뷰어·작업 폴더·AI CLI가 준비됐는지 확인한다(웹앱 설정 > 내 PC 진단)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from cae_adapters.tool_commands import DEFAULT_TOOL_COMMANDS, DEFAULT_TOOL_PATHS

from .config import AgentSettings

# 툴 키 → 사람이 알아보는 이름, 버전 확인 인자(안전하게 바로 끝나는 것만)
LABELS = {
    "nx_run_journal": ("NX run_journal", None), "abaqus": ("Abaqus", ["information=release"]),
    "lsdyna": ("LS-DYNA", None), "lsprepost": ("LS-PrePost", None), "ansys_python": ("Ansys Python(PyAnsys)", ["--version"]),
    "ansys_runwb2": ("Ansys Workbench", None), "ansys_mapdl": ("Ansys MAPDL", None), "altair_hmbatch": ("HyperMesh batch", None),
    "altair_hw": ("HyperWorks", None), "altair_optistruct": ("OptiStruct", None), "starccm": ("STAR-CCM+", ["-version"]),
    "amesim_python": ("Amesim Python", ["--version"]),
}


def _resolve(exe: str) -> str | None:
    if not exe:
        return None
    p = Path(exe)
    if p.exists():
        return str(p)
    return shutil.which(exe)


def _version(path: str, args: list[str] | None) -> str | None:
    if not args:
        return None
    try:
        cmd = (["cmd", "/c", path] if path.lower().endswith((".bat", ".cmd")) else [path]) + args
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25)
        out = (p.stdout or p.stderr).strip().splitlines()
        return out[0][:160] if out else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def diagnose(s: AgentSettings) -> dict:
    paths = {**DEFAULT_TOOL_PATHS, **s.tools}
    tools = []
    for key, exe in paths.items():
        label, vargs = LABELS.get(key, (key, None))
        found = _resolve(exe)
        uses = sorted(k for k, t in {**DEFAULT_TOOL_COMMANDS, **s.tool_commands}.items() if t and t[0] == "{" + key + "}")
        tools.append({"key": key, "label": label, "configured": exe, "found": bool(found), "path": found,
                      "version": _version(found, vargs) if found else None, "tool_keys": uses,
                      "env": s.tool_env.get(key) or None})
    viewers = [{"ext": ext, "command": cmd, "found": bool(_resolve(cmd[0]))} for ext, cmd in s.viewers.items()]
    ws = s.workspace_dir
    usage = shutil.disk_usage(ws if ws.exists() else ws.parent)
    return {
        "workspace": str(ws), "workspace_free_gb": round(usage.free / 1e9, 1),
        "tools": tools, "viewers": viewers, "agents": s.available_agents(),
        "hints": [t["label"] + ": agent.toml [tools]에 경로를 지정하세요" for t in tools if not t["found"]],
    }
