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
# 케이스가 끝나면 케이스 폴더를 case.h5로 묶는다(빠짐없이: 메타·필드 전 프레임·이력·작은 파일·원본 목록).
# upload_max_mb 이하면 서버에도 올린다(넘으면 PC에만 두고 정보만 보낸다). float32=True면 실수를 4바이트로 줄인다(손실 있음).
ARCHIVE_DEFAULTS = {"enabled": True, "upload_max_mb": 500, "text_max_mb": 5, "hash_max_mb": 512,
                    "compression": "gzip", "level": 4, "float32": False}


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
        self.pool.submit(self._run, self._extract if job.get("type") == "case.extract" else self._run_case, job)

    def _run(self, fn: Callable[[dict], None], job: dict):
        self.queued -= 1
        self.running += 1
        try:
            fn(job)
        finally:
            self.running -= 1

    def _extract(self, job: dict):
        """Output 추가: 이미 해석한 케이스 폴더에서 새 Output만 뽑아 보고한다(해석은 다시 하지 않는다)."""
        ws = self.s.workspace
        sdir = ws.study_dir(job["project_id"], job["study_id"], job.get("study_name"), job.get("project_name"),
                            job.get("project_uid"))
        run_dir = Path(job["run_dir"]) if job.get("run_dir") else None
        if not (run_dir and run_dir.is_dir()):  # 작업 폴더를 옮겼으면 스터디 폴더에서 다시 찾는다
            found = sorted((sdir / "cases").glob(f"C{job['case_id']:04d}_*"))
            run_dir = found[0] if found else sdir / "cases" / f"C{job['case_id']:04d}"
        try:
            out = get_adapter(job["adapter"]).extract(
                job["params"], run_dir, job.get("adapter_config") or {}, job.get("steps") or [], job.get("names") or [],
                get_runner((job.get("adapter_config") or {}).get("runner") or self.s.runner, self.s.hpc), self.s.tools,
                project_dir=ws.project_dir(job["project_id"], job.get("project_name"), job.get("project_uid")),
                tool_commands=self.s.tool_commands, tool_envs=self.s.tool_env)
        except Exception as e:
            log.exception("Output 추출 오류")
            out = CaseOutcome("failed", error=str(e))
        if out.results and run_dir.is_dir():
            ws.record_case(sdir, run_dir, {"results": {**(job.get("results") or {}), **out.results}})
        archive = self._archive(job, run_dir) if out.results else None  # 새 Output도 case.h5에 반영
        self.report({"type": "case.status", "case_id": job["case_id"], "status": "complete", "extract": True,
                     "results": out.results, "error": None if out.status == "complete" else f"Output 추출 실패: {out.error}",
                     "archive": archive})

    def _run_case(self, job: dict):
        ws = self.s.workspace
        project_dir = ws.project_dir(job["project_id"], job.get("project_name"), job.get("project_uid"))
        sdir = ws.study_dir(job["project_id"], job["study_id"], job.get("study_name"), job.get("project_name"),
                            job.get("project_uid"))
        ws.write_study(sdir, {k: job.get(k) for k in ("study_id", "study_name", "project_id", "variables", "objectives",
                                                       "adapter", "adapter_config")})
        run_dir = ws.case_dir(sdir, job["case_id"], job.get("source", "doe"))
        meta = {"case_id": job["case_id"], "study_id": job["study_id"], "source": job.get("source"), "params": job["params"],
                "adapter": job["adapter"], "host": os.environ.get("COMPUTERNAME", "")}
        ws.record_case(sdir, run_dir, {**meta, "status": "running", "started": _now()})
        self.report({"type": "case.status", "case_id": job["case_id"], "status": "running", "run_dir": str(run_dir)})
        try:
            out = get_adapter(job["adapter"]).run_case(
                job["params"], run_dir, job.get("adapter_config") or {},
                get_runner((job.get("adapter_config") or {}).get("runner") or self.s.runner, self.s.hpc), self.s.tools,
                job.get("dry_run", False), project_dir=project_dir, tool_commands=self.s.tool_commands,
                case_meta={"case_id": job["case_id"], "study_id": job["study_id"]}, tool_envs=self.s.tool_env,
            )
        except Exception as e:
            log.exception("케이스 실행 오류")
            out = CaseOutcome("failed", error=str(e))
        ws.record_case(sdir, run_dir, {**meta, "status": out.status, "results": out.results, "error": out.error, "finished": _now()})
        images = self._upload_images(job["project_id"], run_dir / "images")
        fields = self._upload_fields(job["case_id"], run_dir / "fields")
        archive = self._archive(job, run_dir) if out.status != "dry_run" else None
        self.report({"type": "case.status", "case_id": job["case_id"], "status": out.status,
                     "results": out.results, "error": out.error, "images": images, "fields": fields, "archive": archive})

    def _archive(self, job: dict, run_dir: Path) -> dict | None:
        """케이스 폴더 → case.h5(HDF5). 실패해도 케이스 결과는 그대로 보고한다."""
        from cae_adapters.h5store import H5_NAME, archive_info, file_sha256, pack_case

        cfg = {**ARCHIVE_DEFAULTS, **(self.s.archive or {})}
        if not cfg["enabled"] or not run_dir.is_dir():
            return None
        try:
            s = pack_case(run_dir, meta={"case_id": job["case_id"], "study_id": job["study_id"], "adapter": job.get("adapter", ""),
                                         "project_uid": job.get("project_uid") or "", "host": os.environ.get("COMPUTERNAME", "")},
                          text_max_mb=cfg["text_max_mb"], hash_max_mb=cfg["hash_max_mb"], compression=cfg["compression"],
                          level=cfg["level"], float32=cfg["float32"])
        except Exception as e:
            log.exception("case.h5 묶기 실패")
            return {"ok": False, "error": f"case.h5 묶기 실패: {e}", "stored": "pc"}
        h5 = run_dir / H5_NAME
        info = {**archive_info(s), "stored": "pc", "pc_path": str(h5), "sha256": file_sha256(h5)}
        if s["size"] <= cfg["upload_max_mb"] * 1e6:
            try:
                self.api.upload(f"/api/cases/{job['case_id']}/archive", h5)
                info["stored"] = "server"
            except Exception as e:
                log.warning("case.h5 올리기 실패 %s: %s", h5, e)
                info["upload_error"] = str(e)[:300]
        return info

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
