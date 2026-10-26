"""해석/스크립트 실행기. 로컬 실행은 LocalRunner, HPC 제출은 HpcRunner(사내 스케줄러 확정 후 구현)."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class JobResult:
    command: list[str]
    cwd: str
    returncode: int | None
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def _tail(text: str, limit: int = 8000) -> str:
    return text if len(text) <= limit else "...(앞부분 생략)...\n" + text[-limit:]


class JobRunner:
    def run(self, command: list[str], cwd: Path, timeout: int | None = None, env: dict | None = None) -> JobResult:
        raise NotImplementedError


class LocalRunner(JobRunner):
    def run(self, command, cwd, timeout=None, env=None):
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            p = subprocess.run(
                command, cwd=cwd, env=env, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
            )
            res = JobResult(command, str(cwd), p.returncode, _tail(p.stdout), _tail(p.stderr))
        except FileNotFoundError as e:
            res = JobResult(command, str(cwd), None, "", f"실행 파일을 찾을 수 없습니다: {e}")
        except subprocess.TimeoutExpired as e:
            res = JobResult(command, str(cwd), None, _tail(e.stdout or ""), f"시간 초과({timeout}s)")
        (cwd / "run.log").write_text(
            f"$ {' '.join(command)}\n[returncode] {res.returncode}\n[stdout]\n{res.stdout}\n[stderr]\n{res.stderr}\n",
            encoding="utf-8",
        )
        return res


class HpcRunner(JobRunner):
    """사내 HPC 스케줄러(LSF/PBS/Slurm) 연동 자리.
    구현 시: submit(배치 스크립트 생성 + bsub/qsub/sbatch) → status 폴링 → fetch(결과 동기화) 로 run()을 구성한다."""

    def submit(self, command: list[str], cwd: Path, resources: dict) -> str:
        raise NotImplementedError("HPC 스케줄러 연동이 아직 설정되지 않았습니다")

    def status(self, job_id: str) -> str:
        raise NotImplementedError

    def fetch(self, job_id: str, dest: Path) -> None:
        raise NotImplementedError

    def run(self, command, cwd, timeout=None, env=None):
        raise NotImplementedError("HPC 스케줄러 연동이 아직 설정되지 않았습니다")


def get_runner(kind: str = "local") -> JobRunner:
    return HpcRunner() if kind == "hpc" else LocalRunner()
