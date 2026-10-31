"""해석/스크립트 실행기. 로컬 실행은 LocalRunner, HPC 제출은 HpcRunner(Slurm/PBS/LSF 프리셋, agent.toml [hpc])."""
from __future__ import annotations

import re
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from cae_adapters.hidden import RunStopped, decode_output, hidden_kwargs, run_hidden

STOPPED_MESSAGE = "사용자가 중지했습니다"


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
    def __init__(self):
        self.stop = threading.Event()  # 사용자가 케이스를 중지하면 켜진다. 창이 없어 이것이 해석을 멈출 유일한 길이다

    def run(self, command: list[str], cwd: Path, timeout: int | None = None, env: dict | None = None) -> JobResult:
        raise NotImplementedError


class LocalRunner(JobRunner):
    def run(self, command, cwd, timeout=None, env=None):
        cwd.mkdir(parents=True, exist_ok=True)
        try:
            if self.stop.is_set():  # 여러 단계를 차례로 돌 때 다음 단계를 시작하지 않는다
                raise RunStopped(command)
            # 창 없이, 입력 대기 없이. 시간 초과·중지 때는 솔버 자손까지 끝낸다(숨은 솔버가 라이선스를 쥔 채 남지 않게)
            p = run_hidden(command, cwd=cwd, env=env, timeout=timeout, stop=self.stop)
            res = JobResult(command, str(cwd), p.returncode, _tail(decode_output(p.stdout)), _tail(decode_output(p.stderr)))
        except FileNotFoundError as e:
            res = JobResult(command, str(cwd), None, "", f"실행 파일을 찾을 수 없습니다: {command[0] if command else ''} ({e}) → 웹앱 'CAD·CAE 툴' 화면에서 그 툴의 실행 파일을 고르거나, 이 PC에 설치한 뒤 다시 실행하세요")
        except RunStopped as e:
            res = JobResult(command, str(cwd), None, _tail(decode_output(e.stdout)), STOPPED_MESSAGE)
        except subprocess.TimeoutExpired as e:
            res = JobResult(command, str(cwd), None, _tail(decode_output(e.stdout)),
                            f"시간 초과({timeout}s): 솔버까지 끝냈습니다 → 더 오래 걸리는 해석이면 timeout_sec를 늘려 다시 실행하세요")
        (cwd / "run.log").write_text(
            f"$ {' '.join(command)}\n[returncode] {res.returncode}\n[stdout]\n{res.stdout}\n[stderr]\n{res.stderr}\n",
            encoding="utf-8",
        )
        return res


# 스케줄러 프리셋: {script}=작업 스크립트(클러스터 경로), {job}=작업 ID. agent.toml [hpc]에서 바꿀 수 있다.
SCHEDULERS: dict[str, dict] = {
    "slurm": {"submit": ["sbatch", "--parsable", "{script}"], "id_regex": r"^(\d+)", "status": ["squeue", "-h", "-j", "{job}", "-o", "%T"],
              "cancel": ["scancel", "{job}"], "header": "#SBATCH -J {name}\n#SBATCH -o {log}\n#SBATCH -n {ncpu}"},
    "pbs": {"submit": ["qsub", "{script}"], "id_regex": r"^(\S+)", "status": ["qstat", "{job}"],
            "cancel": ["qdel", "{job}"], "header": "#PBS -N {name}\n#PBS -o {log}\n#PBS -j oe\n#PBS -l select=1:ncpus={ncpu}"},
    "lsf": {"submit": ["bsub", "-J", "{name}", "-o", "{log}", "-n", "{ncpu}", "sh", "{script}"], "id_regex": r"Job <(\d+)>",
            "status": ["bjobs", "-noheader", "-o", "stat", "{job}"], "cancel": ["bkill", "{job}"], "header": ""},
}
FINISHED = re.compile(r"^\s*$|DONE|EXIT|COMPLETED|FAILED|CANCELLED|TIMEOUT|not found|Unknown Job|Invalid job", re.I)


class HpcRunner(JobRunner):
    """사내 HPC 스케줄러(Slurm/PBS/LSF) 실행기.
    케이스 폴더가 클러스터와 공유되는 스토리지에 있어야 한다(path_map으로 Windows 경로 → 클러스터 경로).
    흐름: 작업 스크립트 작성(cd → 명령 → 종료코드 파일) → 제출 → 상태 폴링 → 종료코드 파일로 성공/실패 판정.
    설정 예 (agent.toml):
      [hpc]
      scheduler = "slurm"            # slurm | pbs | lsf (submit/status/cancel/header/id_regex 를 직접 지정해도 됨)
      ssh = "me@login01"             # 비우면 스케줄러 명령을 이 PC에서 바로 실행
      path_map = { 'Z:/' = '/shared/' }
      exe_map = { 'abaqus.bat' = 'abaqus', 'run_journal.exe' = '/opt/nx/run_journal' }  # Windows 실행파일 → 클러스터 명령
      ncpu = 8
      header = "#SBATCH -p cae"      # 프리셋 헤더 뒤에 덧붙임
      poll_sec = 20
    """

    def __init__(self, cfg: dict | None = None):
        super().__init__()
        cfg = dict(cfg or {})
        preset = SCHEDULERS.get(cfg.get("scheduler", "slurm"), {})
        self.cfg = {**preset, **{k: v for k, v in cfg.items() if k in preset}, **cfg}
        self.cfg["header"] = "\n".join(x for x in (preset.get("header", ""), cfg.get("header", "")) if x)
        self.poll = float(self.cfg.get("poll_sec", 20))

    # ---------- 경로·명령 변환 ----------
    def remote_path(self, p: str | Path) -> str:
        s = str(p).replace("\\", "/")
        for local, remote in (self.cfg.get("path_map") or {}).items():
            loc = str(local).replace("\\", "/")
            if s.lower().startswith(loc.lower()):
                return remote + s[len(loc):]
        return s

    def remote_command(self, command: list[str]) -> list[str]:
        exe_map = {k.lower(): v for k, v in (self.cfg.get("exe_map") or {}).items()}
        cmd = list(command)
        if cmd[:2] == ["cmd", "/c"]:  # Windows .bat 감싸기 제거
            cmd = cmd[2:]
        base = Path(cmd[0]).name.lower()
        cmd[0] = exe_map.get(base) or exe_map.get(cmd[0].lower()) or Path(cmd[0]).stem
        return [self._map_arg(c) for c in cmd]

    def _map_arg(self, arg: str) -> str:
        """인자 안의 Windows 경로(예: input=Z:\\x\\a.inp)도 클러스터 경로로 바꾼다."""
        if not re.search(r"[A-Za-z]:[\\/]|^\\\\", arg):
            return arg
        s = arg.replace("\\", "/")
        for local, remote in (self.cfg.get("path_map") or {}).items():
            loc = str(local).replace("\\", "/")
            s = re.sub(re.escape(loc), lambda _m: remote, s, flags=re.I)
        return s

    def _sched(self, key: str, **kw) -> subprocess.CompletedProcess:
        args = [str(a).format(**kw) for a in self.cfg[key]]
        if self.cfg.get("ssh"):
            args = ["ssh", "-o", "BatchMode=yes", self.cfg["ssh"], " ".join(shlex.quote(a) for a in args)]
        # 상태 조회가 poll_sec마다 돌므로 창이 뜨면 그때마다 포커스를 뺏는다
        return subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                              timeout=120, **hidden_kwargs())

    # ---------- 실행 ----------
    def run(self, command, cwd, timeout=None, env=None):
        cwd = Path(cwd)
        if self.stop.is_set():
            return JobResult(command, str(cwd), None, "", STOPPED_MESSAGE)
        rdir = self.remote_path(cwd.resolve())
        rcmd = self.remote_command(command)
        name = f"dds_{cwd.name}"[:60]
        log = f"{rdir}/hpc.log"
        (cwd / ".dds_exitcode").unlink(missing_ok=True)
        hdr = self.cfg.get("header", "").format(name=name, log=log, ncpu=self.cfg.get("ncpu", 1))
        script = cwd / "hpc_job.sh"
        script.write_text("#!/bin/bash\n" + (hdr + "\n" if hdr else "") +
                          f"cd {shlex.quote(rdir)} || exit 97\n{' '.join(shlex.quote(c) for c in rcmd)}\n"
                          "echo $? > .dds_exitcode\n", encoding="utf-8", newline="\n")
        p = self._sched("submit", script=f"{rdir}/hpc_job.sh", name=name, log=log, ncpu=self.cfg.get("ncpu", 1))
        m = re.search(self.cfg.get("id_regex", r"(\S+)"), (p.stdout or "") + (p.stderr or ""), re.M)
        if p.returncode != 0 or not m:
            return JobResult(rcmd, str(cwd), None, p.stdout, f"HPC 제출 실패: {p.stderr or p.stdout}")
        job = m.group(1)
        (cwd / "hpc_job_id.txt").write_text(job, encoding="utf-8")
        t0 = time.time()
        while True:
            self.stop.wait(self.poll)  # 중지하면 다음 조회까지 기다리지 않는다
            done_file = cwd / ".dds_exitcode"
            if done_file.exists():
                break
            if self.stop.is_set():
                self._sched("cancel", job=job)
                return JobResult(rcmd, str(cwd), None, _read_tail(cwd / "hpc.log"), f"{STOPPED_MESSAGE} — HPC 작업 {job} 취소")
            st = self._sched("status", job=job)
            if FINISHED.search((st.stdout or "") + (st.stderr or "")) and not done_file.exists():
                time.sleep(self.poll)  # 공유 스토리지 반영 지연 대비 한 번 더 기다림
                if not done_file.exists():
                    return JobResult(rcmd, str(cwd), None, _read_tail(cwd / "hpc.log"), f"HPC 작업 {job}이 종료코드 없이 끝났습니다(취소·시간 초과·노드 오류)")
            if timeout and time.time() - t0 > timeout:
                self._sched("cancel", job=job)
                return JobResult(rcmd, str(cwd), None, _read_tail(cwd / "hpc.log"), f"시간 초과({timeout}s) — HPC 작업 {job} 취소")
        code = int((cwd / ".dds_exitcode").read_text().strip() or 1)
        res = JobResult(rcmd, str(cwd), code, _read_tail(cwd / "hpc.log"), "" if code == 0 else f"HPC 작업 {job} 종료코드 {code}")
        (cwd / "run.log").write_text(f"[HPC {job}] $ {' '.join(rcmd)}\n[returncode] {code}\n{res.stdout}\n", encoding="utf-8")
        return res


def _read_tail(p: Path) -> str:
    try:
        return _tail(p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


def get_runner(kind: str = "local", hpc: dict | None = None) -> JobRunner:
    return HpcRunner(hpc) if kind == "hpc" else LocalRunner()
