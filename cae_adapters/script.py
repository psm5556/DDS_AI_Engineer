"""범용 스크립트 어댑터: 케이스마다 inputs 복사 → params.json 작성 → 툴 배치 실행 → results.json 읽기.
NX, Simcenter, Abaqus, LS-DYNA, Ansys, Altair, STAR-CCM+, Amesim 모두 이 어댑터 + 툴별 스킬 스크립트로 연결한다.
"""
import shutil
from pathlib import Path

from .base import CaeAdapter, CaseOutcome, read_results, read_results_if_any, write_json
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
        "post": [{"tool": "abaqus_python", "script": "scripts/extract.py", "args": []}],  # 한 개면 객체로 써도 됨
        "timeout_sec": 3600,
    }

    @staticmethod
    def _commands(steps, run_dir, project_dir, tools, tool_commands, tool_envs):
        """단계마다 스크립트를 케이스 폴더로 복사하고 실행 명령·환경을 만든다. 스크립트가 없으면 ValueError."""
        commands, envs = [], []
        for step in steps:
            script = None
            if step.get("script"):
                src = (project_dir / step["script"]).resolve()
                if not src.exists():
                    raise ValueError(f"스크립트가 없습니다: {step['script']}")
                shutil.copy2(src, run_dir / src.name)
                script = src.name
            commands.append(build_command(step["tool"], script, [str(a) for a in step.get("args", [])],
                                          tools, tool_commands))
            envs.append(tool_env(step["tool"], tools, tool_envs, str(run_dir)))
        return commands, envs

    @staticmethod
    def _run(commands, envs, runner, run_dir, timeout):
        """명령을 차례로 실행하며 단계마다 results.json에 쓴 값을 모은다.
        뒤 단계(추가한 Output 추출 등)가 results.json을 새로 써도 앞 단계 값이 남게 하기 위해서다."""
        acc: dict[str, float] = {}
        for cmd, env in zip(commands, envs):
            res = runner.run(cmd, run_dir, timeout=timeout, env=env)
            if not res.ok:
                return None, f"명령 실패({res.returncode}): {' '.join(cmd)}\n{res.stderr[-2000:]}"
            acc.update(read_results_if_any(run_dir / "results.json"))
        return acc, None

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

            post = config.get("post") or []
            steps = [config] + (post if isinstance(post, list) else [post])  # post: 한 단계 또는 여러 단계(필드 여러 개 추출 등)
            commands, envs = self._commands(steps, run_dir, project_dir, tools, tool_commands, tool_envs)
            write_json(run_dir / "commands.json", commands)
        except ValueError as e:
            return CaseOutcome("failed", error=str(e))

        if dry_run:
            return CaseOutcome("dry_run", files=files + ["params.json", "commands.json"])

        results, err = self._run(commands, envs, runner, run_dir, config.get("timeout_sec"))
        if err:
            return CaseOutcome("failed", error=err)
        if not results:
            try:
                read_results(run_dir / "results.json")  # 결과 파일이 없다는 오류 문구를 그대로 쓴다
            except Exception as e:
                return CaseOutcome("failed", error=str(e))
        write_json(run_dir / "results.json", results)
        return CaseOutcome("complete", results=results, files=files)

    def extract(self, params, run_dir, config, steps, names, runner, tools, project_dir: Path | None = None,
                tool_commands=None, tool_envs=None, **_):
        """케이스 폴더에 남은 해석 결과(odb·d3plot 등)에서 추출 단계만 다시 돌려 새 Output을 뽑는다."""
        if not run_dir.is_dir():
            return CaseOutcome("failed", error=f"케이스 폴더가 없습니다: {run_dir}")
        if not steps:
            return CaseOutcome("failed", error="추출 단계(post)가 없습니다")
        before = read_results_if_any(run_dir / "results.json")
        try:
            commands, envs = self._commands(steps, run_dir, project_dir or run_dir, tools, tool_commands, tool_envs)
        except ValueError as e:
            return CaseOutcome("failed", error=str(e))
        new, err = self._run(commands, envs, runner, run_dir, config.get("timeout_sec"))
        write_json(run_dir / "results.json", {**before, **(new or {})})  # 추출 스크립트가 덮어써도 기존 결과를 지킨다
        if err:
            return CaseOutcome("failed", error=err)
        got = {k: v for k, v in new.items() if k in names}
        if not got:
            return CaseOutcome("failed", error=f"추출 단계가 {', '.join(names)} 값을 results.json에 쓰지 않았습니다")
        return CaseOutcome("complete", results=got)
