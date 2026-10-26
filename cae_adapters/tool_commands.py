"""tool 키 → 배치 실행 명령 템플릿 (docs/skill-contract.md 2절).
{script}=스크립트 파일, {args}=추가 인자(펼쳐짐), 그 외 {키}는 config.toml [tools]의 실행파일 경로.
config.toml의 [tool_commands]로 덮어쓸 수 있다.
"""
from __future__ import annotations

import os
import sys

from .base import resolve_cmd

DEFAULT_TOOL_COMMANDS: dict[str, list[str]] = {
    "nx": ["{nx_run_journal}", "{script}", "-args", "{args}"],
    "simcenter": ["{nx_run_journal}", "{script}", "-args", "{args}"],
    "abaqus_cae": ["{abaqus}", "cae", "noGUI={script}", "--", "{args}"],
    "abaqus_python": ["{abaqus}", "python", "{script}", "{args}"],
    "abaqus_solver": ["{abaqus}", "{args}"],
    "lsdyna": ["{lsdyna}", "{args}"],
    "lsprepost": ["{lsprepost}", "-nographics", "c={script}"],
    "ansys_python": ["{ansys_python}", "{script}", "{args}"],
    "ansys_wb": ["{ansys_runwb2}", "-B", "-R", "{script}"],
    "mapdl": ["{ansys_mapdl}", "-b", "-i", "{script}", "-o", "solve.out"],
    "altair_hm": ["{altair_hmbatch}", "-tcl", "{script}"],
    "altair_hv": ["{altair_hw}", "-b", "-tcl", "{script}"],
    "optistruct": ["{altair_optistruct}", "{args}"],
    "simcenter_chain": ["{python}", "{script}", "--run-journal", "{nx_run_journal}", "{args}"],
    "starccm": ["{starccm}", "-batch", "{script}", "{args}"],
    "starccm_run": ["{starccm}", "-batch", "run", "{args}"],
    "amesim": ["{amesim_python}", "{script}", "{args}"],
    "python": ["{python}", "{script}", "{args}"],
}

DEFAULT_TOOL_PATHS: dict[str, str] = {
    "nx_run_journal": r"C:\Program Files\Siemens\NX\NXBIN\run_journal.exe",
    "abaqus": "abaqus",
    "lsdyna": "lsdyna",
    "lsprepost": "lsprepost",
    "ansys_python": "python",
    "ansys_runwb2": "runwb2",
    "ansys_mapdl": "mapdl",
    "altair_hmbatch": "hmbatch",
    "altair_hw": "hw",
    "altair_optistruct": "optistruct",
    "starccm": "starccm+",
    "amesim_python": "amesim_python",
}


def build_command(tool: str, script: str | None, args: list[str], paths: dict[str, str],
                  commands: dict[str, list[str]] | None = None) -> list[str]:
    table = {**DEFAULT_TOOL_COMMANDS, **(commands or {})}
    if tool not in table:
        raise ValueError(f"알 수 없는 tool 키: {tool} (사용 가능: {', '.join(sorted(table))})")
    values = {**DEFAULT_TOOL_PATHS, **paths, "python": sys.executable}
    cmd: list[str] = []
    for i, part in enumerate(table[tool]):
        if part == "{args}":
            cmd.extend(args)
        elif part == "{script}":
            if script is None:
                raise ValueError(f"tool '{tool}' 은 스크립트가 필요합니다")
            cmd.append(script)
        elif i == 0 and part.startswith("{") and part.endswith("}"):
            cmd.extend(resolve_cmd(values.get(part[1:-1], part[1:-1])))
        else:
            cmd.append(part.format(script=script or "", **values) if "{" in part else part)
    return cmd


def tool_env(tool: str, paths: dict[str, str], tool_envs: dict[str, dict[str, str]] | None = None,
             case_dir: str | None = None) -> dict[str, str]:
    """툴 실행 환경변수.
    - 모든 툴 경로를 DDS_TOOL_<KEY> 와 DDS_<KEY> 로 노출(예: DDS_TOOL_ANSYS_MAPDL = DDS_ANSYS_MAPDL)
      → python 드라이버 스크립트가 솔버를 직접 호출할 때 사용
    - NX_RUN_JOURNAL 별칭, DDS_CASE_DIR(케이스 실행 폴더, Workbench 저널처럼 인자를 못 받는 경우용)
    - config [tool_env.<tool>] 의 값(예: Amesim의 AME, PATH 앞에 붙일 경로)을 덮어쓴다. "{PATH}"는 기존 PATH로 치환.
    """
    values = {**DEFAULT_TOOL_PATHS, **paths}
    env = dict(os.environ)
    env.update({f"DDS_TOOL_{k.upper()}": v for k, v in values.items()})
    env.update({f"DDS_{k.upper()}": v for k, v in values.items()})
    if case_dir:
        env["DDS_CASE_DIR"] = case_dir
    env["NX_RUN_JOURNAL"] = values.get("nx_run_journal", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    for k, v in ((tool_envs or {}).get(tool) or {}).items():
        env[k] = v.replace("{PATH}", os.environ.get("PATH", ""))
    return env
