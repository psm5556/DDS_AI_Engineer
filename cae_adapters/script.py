"""범용 스크립트 어댑터: 케이스마다 inputs 복사 → params.json 작성 → 툴 배치 실행 → results.json 읽기.
NX, Simcenter, Abaqus, LS-DYNA, Ansys, Altair, STAR-CCM+, Amesim 모두 이 어댑터 + 툴별 스킬 스크립트로 연결한다.
"""
import shutil
from pathlib import Path

from .base import CaeAdapter, CaseOutcome, read_results, write_json
from .tool_commands import build_command, tool_env


class ScriptAdapter(CaeAdapter):
    name = "script"
    label = "툴 스크립트"
    description = ("tool 키(nx, simcenter, abaqus_cae, lsdyna, ansys_wb, starccm, amesim ...)로 지정한 툴에서 "
                   "스크립트를 케이스마다 실행. 스크립트는 params.json을 읽고 results.json을 써야 한다.")
    config_example = {
        "tool": "abaqus_cae",
        "script": "scripts/plate.py",
        "args": [],
        "inputs": ["models/plate.cae"],
        "post": {"tool": "abaqus_python", "script": "scripts/extract.py", "args": []},
        "timeout_sec": 3600,
    }

    def run_case(self, params, run_dir, config, runner, tools, dry_run=False, project_dir: Path | None = None,
                 tool_commands=None, case_meta=None, tool_envs=None):
        project_dir = project_dir or run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "images").mkdir(exist_ok=True)
        files = []
        try:
            for rel in config.get("inputs", []):
                src = (project_dir / rel).resolve()
                if not src.exists():
                    return CaseOutcome("failed", error=f"입력 파일이 없습니다: {rel}")
                dst = run_dir / src.name
                (shutil.copytree(src, dst, dirs_exist_ok=True) if src.is_dir() else shutil.copy2(src, dst))
                files.append(src.name)
            write_json(run_dir / "params.json", params)
            write_json(run_dir / "case.json", {**(case_meta or {}), "inputs": [Path(r).name for r in config.get("inputs", [])]})

            steps = [config] + ([config["post"]] if config.get("post") else [])
            commands, envs = [], []
            for step in steps:
                script = None
                if step.get("script"):
                    src = (project_dir / step["script"]).resolve()
                    if not src.exists():
                        return CaseOutcome("failed", error=f"스크립트가 없습니다: {step['script']}")
                    shutil.copy2(src, run_dir / src.name)
                    script = src.name
                commands.append(build_command(step["tool"], script, [str(a) for a in step.get("args", [])],
                                              tools, tool_commands))
                envs.append(tool_env(step["tool"], tools, tool_envs, str(run_dir)))
            write_json(run_dir / "commands.json", commands)
        except ValueError as e:
            return CaseOutcome("failed", error=str(e))

        if dry_run:
            return CaseOutcome("dry_run", files=files + ["params.json", "commands.json"])

        for cmd, env in zip(commands, envs):
            res = runner.run(cmd, run_dir, timeout=config.get("timeout_sec"), env=env)
            if not res.ok:
                return CaseOutcome("failed", error=f"명령 실패({res.returncode}): {' '.join(cmd)}\n{res.stderr[-2000:]}")
        try:
            results = read_results(run_dir / "results.json")
        except Exception as e:
            return CaseOutcome("failed", error=str(e))
        return CaseOutcome("complete", results=results, files=files)
