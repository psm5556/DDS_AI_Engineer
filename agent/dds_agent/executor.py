"""서버가 보낸 케이스(case.run)를 이 PC에서 실행하고 상태를 보고한다."""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from cae_adapters import get_adapter
from cae_adapters.base import CaseOutcome

from .config import AgentSettings
from .job_runner import get_runner
from .server_api import ServerApi

log = logging.getLogger(__name__)
MAX_IMAGES = 6


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class CaseExecutor:
    def __init__(self, settings: AgentSettings, report: Callable[[dict], None]):
        self.s, self.report = settings, report
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.max_parallel_cases), thread_name_prefix="case")
        self.api = ServerApi(settings)
        self.queued = 0
        self.running = 0

    def submit(self, job: dict):
        self.queued += 1
        self.pool.submit(self._run, job)

    def _run(self, job: dict):
        self.queued -= 1
        self.running += 1
        try:
            self._run_case(job)
        finally:
            self.running -= 1

    def _run_case(self, job: dict):
        ws = self.s.workspace
        project_dir = ws.project_dir(job["project_id"], job.get("project_name"))
        sdir = ws.study_dir(job["project_id"], job["study_id"], job.get("study_name"), job.get("project_name"))
        ws.write_study(sdir, {k: job.get(k) for k in ("study_id", "study_name", "project_id", "variables", "objectives",
                                                       "adapter", "adapter_config")})
        run_dir = ws.case_dir(sdir, job["case_id"], job.get("source", "doe"))
        meta = {"case_id": job["case_id"], "study_id": job["study_id"], "source": job.get("source"), "params": job["params"],
                "adapter": job["adapter"], "host": os.environ.get("COMPUTERNAME", "")}
        ws.record_case(sdir, run_dir, {**meta, "status": "running", "started": _now()})
        self.report({"type": "case.status", "case_id": job["case_id"], "status": "running", "run_dir": str(run_dir)})
        try:
            out = get_adapter(job["adapter"]).run_case(
                job["params"], run_dir, job.get("adapter_config") or {}, get_runner(self.s.runner), self.s.tools,
                job.get("dry_run", False), project_dir=project_dir, tool_commands=self.s.tool_commands,
                case_meta={"case_id": job["case_id"], "study_id": job["study_id"]}, tool_envs=self.s.tool_env,
            )
        except Exception as e:
            log.exception("케이스 실행 오류")
            out = CaseOutcome("failed", error=str(e))
        ws.record_case(sdir, run_dir, {**meta, "status": out.status, "results": out.results, "error": out.error, "finished": _now()})
        images = self._upload_images(job["project_id"], run_dir / "images")
        fields = self._upload_fields(job["case_id"], run_dir / "fields")
        self.report({"type": "case.status", "case_id": job["case_id"], "status": out.status,
                     "results": out.results, "error": out.error, "images": images, "fields": fields})

    def _upload_fields(self, case_id: int, folder: Path) -> list[str]:
        """필드·형상 데이터(fields/*.npz: points, values, triangles)를 서버로 올려 형상·필드 학습에 쓴다."""
        if not folder.is_dir():
            return []
        names = []
        for f in sorted(folder.glob("*.npz"))[:20]:
            try:
                self.api.upload(f"/api/cases/{case_id}/fields", f)
                names.append(f.stem)
            except Exception as e:
                log.warning("필드 업로드 실패 %s: %s", f, e)
        return names

    def _upload_images(self, project_id: int, folder: Path) -> list[str]:
        if not folder.is_dir():
            return []
        paths = []
        for f in sorted(folder.glob("*"))[:MAX_IMAGES]:
            if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"):
                try:
                    paths.append(self.api.upload_image(project_id, f, "cases"))
                except Exception as e:
                    log.warning("이미지 업로드 실패 %s: %s", f, e)
        return paths
