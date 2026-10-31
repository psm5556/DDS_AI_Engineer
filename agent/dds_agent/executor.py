"""서버가 보낸 케이스(case.run)를 이 PC에서 실행하고 상태를 보고한다."""
from __future__ import annotations

import errno
import logging
import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable

from cae_adapters import get_adapter
from cae_adapters.base import CaseOutcome
from cae_adapters.recipe_bundle import build_bundle, config_scripts

from .config import AgentSettings
from .job_runner import STOPPED_MESSAGE, get_runner
from .server_api import ServerApi

log = logging.getLogger(__name__)
MAX_IMAGES = 6
# 케이스가 끝나면 케이스 폴더를 case.h5로 묶는다(빠짐없이: 메타·필드 전 프레임·이력·작은 파일·원본 목록).
# upload_max_mb 이하면 서버에도 올린다(넘으면 PC에만 두고 정보만 보낸다). float32=True면 실수를 4바이트로 줄인다(손실 있음).
ARCHIVE_DEFAULTS = {"enabled": True, "upload_max_mb": 500, "text_max_mb": 5, "hash_max_mb": 512,
                    "compression": "gzip", "level": 4, "float32": False}


CSV_NOTICE = "cases.csv를 갱신하지 못했습니다. 해석 결과는 저장했습니다 → 파일을 닫으면 다음 케이스 때 함께 맞춥니다."
NOT_FINISHED = "실행하던 PC에 끝난 결과가 없습니다. PC Agent가 다시 시작되어 해석이 끝나지 못했을 수 있습니다 → 다시 돌리려면 그 케이스를 지정해 다시 실행하세요"
_FINISHED = ("complete", "failed", "cancelled", "dry_run")
_AGAIN = "그 케이스를 지정해 다시 실행하세요"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def explain_failure(e: Exception) -> str:
    """케이스를 기록·실행하지 못한 이유와 할 일. 사용자는 PC Agent 로그를 보지 않는다."""
    if not isinstance(e, OSError):
        return f"케이스를 처리하지 못했습니다: {e} → PC Agent 로그를 확인한 뒤 {_AGAIN}"
    at = f": {e.filename}" if e.filename else ""
    code = getattr(e, "winerror", None)
    if e.errno == errno.ENOSPC or code in (39, 112):
        return f"디스크 공간이 부족해 케이스를 기록하지 못했습니다{at} → 공간을 비운 뒤 {_AGAIN}"
    if code == 206 or e.errno == errno.ENAMETOOLONG:
        return f"작업 폴더 경로가 너무 길어 케이스를 기록하지 못했습니다{at} → 프로젝트 작업 폴더를 더 짧은 경로로 정한 뒤 {_AGAIN}"
    if isinstance(e, PermissionError):
        return f"작업 폴더의 파일에 쓰지 못했습니다{at}. 다른 프로그램이 열었거나 권한이 없습니다 → 그 파일을 닫거나 폴더 권한을 확인한 뒤 {_AGAIN}"
    if isinstance(e, (FileNotFoundError, NotADirectoryError)) or code in (3, 21, 53, 67, 1231):
        return f"작업 폴더에 접근할 수 없습니다{at} → 드라이브·네트워크 연결을 확인하거나 프로젝트 작업 폴더를 다시 정한 뒤 {_AGAIN}"
    return f"작업 폴더에 기록하지 못했습니다: {e} → 폴더 상태를 확인한 뒤 {_AGAIN}"


class CaseExecutor:
    def __init__(self, settings: AgentSettings, report: Callable[[dict], None]):
        self.s, self.report = settings, report
        self.pool = ThreadPoolExecutor(max_workers=max(1, settings.max_parallel_cases), thread_name_prefix="case")
        self.api = ServerApi(settings)
        self.queued = 0
        self.running = 0
        self._stops: dict[int, threading.Event] = {}  # 대기·실행 중인 케이스 → 중지 신호
        self._kinds: dict[int, str | None] = {}  # 대기·실행 중인 케이스 → 해석인가 추출인가. 추출 중에 온 해석은 중복이 아니다
        self._recipes: dict[int, dict[str, str]] = {}  # 스터디 → 서버에 있다고 확인한 스크립트 내용
        self._recipe_lock = threading.Lock()
        self._settling: set[int] = set()  # 다시 연결될 때 서버가 물어 답하는 중인 케이스
        self._settle_lock = threading.Lock()

    def active_cases(self) -> list[int]:
        """이 PC에서 대기·실행 중인 케이스. 연결할 때 서버가 이것과 맞춰 '실행 중'으로 남은 케이스를 정리한다."""
        return sorted(self._stops)

    def _share_recipe(self, job: dict, project_dir: Path):
        """스크립트 스터디의 케이스가 끝나면 스크립트를 서버에 올려 둔다. 사람이 올리기를 기다리면 이 PC가 꺼진 뒤에 멤버가 이어받지 못한다.
        실패해도 케이스 결과에는 영향이 없다. 동료가 올린 묶음은 자동으로 덮어쓰지 않는다(바꿔 올리는 것은 AI·사용자가 명시적으로 한다)."""
        if job.get("adapter") != "script":
            return
        study_id = job["study_id"]
        try:
            wanted = config_scripts(job.get("adapter_config") or {})
            if not wanted:
                return
            with self._recipe_lock:
                bundle, files = build_bundle(project_dir, wanted)
                if self._recipes.get(study_id) == files:
                    return
                info = self.api.get(f"/api/studies/{study_id}/recipe")
                if info.get("has"):
                    stored = {f["path"]: f["sha256"] for f in info.get("files") or []}
                    if all(stored.get(k) == v for k, v in files.items()):  # save_recipe로 더한 파일이 있어도 지금 스크립트가 그대로면 할 일이 없다
                        self._recipes[study_id] = files
                        return
                    if info.get("created_by") != self.api.get("/api/auth/me").get("id"):
                        return
                    # 내 묶음을 새로 올린다. 사람이 더해 둔 보조 파일을 잃지 않게 함께 묶는다
                    extras = [k for k in stored if k not in files and (project_dir / k).is_file()]
                    bundle, _ = build_bundle(project_dir, wanted + extras)
                self.api.upload_form(f"/api/studies/{study_id}/recipe", {"file": ("recipe.zip", bundle)}, {"note": "해석이 끝나 자동으로 올림"})
                self._recipes[study_id] = files
        except Exception as e:  # noqa: BLE001 — 비밀값이 든 스크립트는 서버가 거절한다. 사용자의 해석 결과를 막을 일이 아니다
            log.warning("스크립트 묶음을 올리지 못했습니다 S%s: %s", study_id, e)

    def submit(self, job: dict):
        kind = job.get("type")
        # 서버가 걸러도 옛 서버·엇갈린 요청이 있을 수 있다. 다시 받아 등록하면 같은 해석이 두 번 돌고 중지 신호가 덮인다
        if kind == "case.run" and self._kinds.get(job["case_id"]) == "case.run":
            log.warning("이미 돌고 있는 케이스를 다시 받아 무시합니다: C%s", job["case_id"])
            return
        self.queued += 1
        fn = {"case.extract": self._extract, "case.train_sample": self._train_sample}.get(kind, self._run_case)
        stop = threading.Event()
        if kind != "case.train_sample":  # 학습 샘플은 솔버를 돌리지 않아 금방 끝난다
            self._stops[job["case_id"]] = stop
            self._kinds[job["case_id"]] = kind
        self.pool.submit(self._run, fn, job, stop)

    def _run(self, fn: Callable[[dict], None], job: dict, stop: threading.Event):
        self.queued -= 1
        self.running += 1
        try:
            if stop.is_set():  # 차례를 기다리다 중지됐다. 해석을 시작하지 않는다
                self._report_stopped(job["case_id"], job.get("type") == "case.extract")
            else:
                fn(job)
        except Exception as e:  # noqa: BLE001 — 스레드 풀은 예외를 삼킨다. 알리지 않으면 화면에 '실행 중'으로 영원히 남는다
            log.exception("케이스 처리 실패 C%s", job.get("case_id"))
            self._report_crash(job, stop, e)
        finally:
            self.running -= 1
            if self._stops.get(job.get("case_id")) is stop:
                self._stops.pop(job["case_id"], None)
                self._kinds.pop(job["case_id"], None)

    def cancel(self, case_ids: list[int], extract_ids: list[int] | None = None):
        """사용자가 중지한 케이스: 대기 중이면 시작하지 않고, 실행 중이면 솔버 트리를 끝낸다.
        이 PC에서 돌고 있지 않은 케이스(Agent가 다시 시작해 잊은 것)는 화면에 '실행 중'으로 남지 않게 바로 알린다."""
        for ids, extract in ((case_ids, False), (extract_ids or [], True)):
            for case_id in ids:
                stop = self._stops.get(case_id)
                if stop:
                    stop.set()
                else:
                    self._report_stopped(case_id, extract)

    def _report_crash(self, job: dict, stop: threading.Event, e: Exception):
        why = explain_failure(e)
        kind = job.get("type")
        if kind == "case.train_sample":
            self.report({"type": "case.train_sample", "dataset_id": job.get("dataset_id"), "case_id": job["case_id"], "ok": False, "error": why[:500]})
        elif kind == "case.extract":  # 해석 결과는 그대로 두고 추출만 실패로 알린다
            self.report({"type": "case.status", "case_id": job["case_id"], "status": "complete", "extract": True, "results": {}, "error": f"Output 추출 실패: {why}"})
        elif stop.is_set():
            self._report_stopped(job["case_id"], False)
        else:
            self.report({"type": "case.status", "case_id": job["case_id"], "status": "failed", "error": why})

    def settle(self, cases: list[dict]):
        """다시 연결될 때 서버가 '이 PC에서 돌고 있지 않은' 케이스를 묻는다. 끝낸 결과를 전하기 전에 Agent가 다시 시작했을 수 있으니
        이 PC의 case.json으로 답한다. 업로드가 있어 해석 차례를 기다리지 않게 따로 돈다."""
        threading.Thread(target=self._settle_all, args=(list(cases),), name="case-settle", daemon=True).start()

    def _settle_all(self, cases: list[dict]):
        for job in cases:
            case_id = job["case_id"]
            with self._settle_lock:
                if case_id in self._stops or case_id in self._settling:  # hello와 case.run이 엇갈렸거나 이미 답하는 중이다
                    continue
                self._settling.add(case_id)
            try:
                self._settle_one(job)
            except Exception as e:  # noqa: BLE001 — 폴더에 닿지 못해도 답은 한다
                log.exception("케이스 상태 확인 실패 C%s", case_id)
                self.report({"type": "case.status", "case_id": case_id, "status": "failed", "error": explain_failure(e)})
            finally:
                with self._settle_lock:
                    self._settling.discard(case_id)

    def _settle_one(self, job: dict):
        run_dir = self._case_dir(job)
        info = self.s.workspace.read_case(run_dir)
        if info.get("status") not in _FINISHED:
            self.report({"type": "case.status", "case_id": job["case_id"], "status": "cancelled", "error": NOT_FINISHED})
            return
        project_dir = self.s.workspace.project_dir(job["project_id"], job.get("project_name"), job.get("project_uid"))
        self._deliver(job, run_dir, CaseOutcome(info["status"], results=info.get("results") or {}, error=info.get("error")), project_dir)

    def _deliver(self, job: dict, run_dir: Path, out: CaseOutcome, project_dir: Path, notice: str | None = None):
        """끝난 결과를 보고한다. 이미지·3D·묶음은 덧붙이는 자료라 실패해도 결과를 막지 않는다."""
        images, fields, archive = [], [], None
        try:
            images = self._upload_images(job["project_id"], run_dir / "images")
            fields = self._upload_fields(job["case_id"], run_dir / "fields")
            archive = self._archive(job, run_dir) if out.status != "dry_run" else None
            if out.status == "complete":
                self._share_recipe(job, project_dir)
        except Exception:  # noqa: BLE001
            log.exception("결과 자료 올리기 실패 C%s", job["case_id"])
        self.report({"type": "case.status", "case_id": job["case_id"], "status": out.status, "results": out.results, "error": out.error,
                     "images": images, "fields": fields, "archive": archive, "notice": notice})

    def _report_stopped(self, case_id: int, extract: bool):
        if extract:  # 해석 결과는 그대로 두고 추출만 멈춘다
            self.report({"type": "case.status", "case_id": case_id, "status": "complete", "extract": True, "results": {},
                         "error": f"Output 추출 실패: {STOPPED_MESSAGE}"})
        else:
            self.report({"type": "case.status", "case_id": case_id, "status": "cancelled", "error": STOPPED_MESSAGE})

    def _runner_for(self, job: dict):
        runner = get_runner((job.get("adapter_config") or {}).get("runner") or self.s.runner, self.s.hpc)
        stop = self._stops.get(job["case_id"])
        if stop:
            runner.stop = stop
        return runner

    def _case_dir(self, job: dict) -> Path:
        run_dir = Path(job["run_dir"]) if job.get("run_dir") else None
        if run_dir and run_dir.is_dir():
            # 서버에 남은 경로가 프로젝트의 옛 이름(접합)일 수 있다. 접합으로 cwd를 두면 실제 폴더가 잠기므로 실제 경로로 돈다
            return Path(os.path.realpath(run_dir))
        # 작업 폴더를 옮겼으면 스터디 폴더에서 다시 찾는다
        sdir = self.s.workspace.study_dir(job["project_id"], job["study_id"], job.get("study_name"), job.get("project_name"),
                                          job.get("project_uid"))
        found = sorted((sdir / "cases").glob(f"C{job['case_id']:04d}_*"))
        return found[0] if found else sdir / "cases" / f"C{job['case_id']:04d}"

    def _train_sample(self, job: dict):
        """학습 데이터: case.h5에서 학습 샘플(train.h5)을 만들어 올린다. case.h5가 너무 커서 PC에만 있어도 학습에 쓸 수 있게 한다."""
        from cae_adapters.h5store import H5_NAME
        from cae_adapters.train_h5 import build_sample

        error = None
        try:
            h5 = self._case_dir(job) / H5_NAME
            if not h5.is_file():  # 불러온 케이스는 원본 폴더가 아니라 스터디 폴더 쪽 작업 폴더에 case.h5가 있다
                sdir = self.s.workspace.study_dir(job["project_id"], job["study_id"], job.get("study_name"), job.get("project_name"),
                                                  job.get("project_uid"))
                h5 = next((d / H5_NAME for d in sorted((sdir / "cases").glob(f"C{job['case_id']:04d}_*")) if (d / H5_NAME).is_file()), h5)
            if not h5.is_file():
                raise FileNotFoundError(f"이 PC에 case.h5가 없습니다: {h5}")
            with tempfile.TemporaryDirectory(prefix="dds_train_") as tmp:  # 케이스 폴더에 남기지 않는다(case.h5 목록에 섞인다)
                out = Path(tmp) / "train.h5"
                build_sample(h5, out, job.get("spec") or {})
                self.api.upload(f"/api/cases/{job['case_id']}/train_sample?spec_hash={job['spec_hash']}", out)
        except Exception as e:  # noqa: BLE001 — 어떤 실패든 이유를 서버로 보내 화면에 보여 준다
            log.warning("학습 샘플 실패 C%s: %s", job["case_id"], e)
            error = str(e)[:500]
        self.report({"type": "case.train_sample", "dataset_id": job["dataset_id"], "case_id": job["case_id"], "ok": error is None, "error": error})

    def _extract(self, job: dict):
        """Output 추가: 이미 해석한 케이스 폴더에서 새 Output만 뽑아 보고한다(해석은 다시 하지 않는다).
        불러온 케이스의 run_dir은 사용자의 원본 폴더다. 원본에는 아무것도 쓰지 않고 스터디 폴더 쪽 작업 폴더에서 돌린다."""
        ws = self.s.workspace
        sdir = ws.study_dir(job["project_id"], job["study_id"], job.get("study_name"), job.get("project_name"),
                            job.get("project_uid"))
        source = self._case_dir(job)
        given = Path(job["run_dir"]) if job.get("run_dir") else None
        if given is not None and not source.is_dir():
            source = given  # 옮겨진 원본: 스터디 폴더에도 없으면 어느 경로가 없는지 그대로 알린다
        kw: dict = {}
        run_dir = source
        if ws.is_external(sdir, source):
            run_dir = ws.case_dir(sdir, job["case_id"], job.get("source") or "import")
            kw["source_dir"] = source
        try:
            out = get_adapter(job["adapter"]).extract(
                job["params"], run_dir, job.get("adapter_config") or {}, job.get("steps") or [], job.get("names") or [],
                self._runner_for(job), self.s.tools,
                project_dir=ws.project_dir(job["project_id"], job.get("project_name"), job.get("project_uid")),
                tool_commands=self.s.tool_commands, tool_envs=self.s.tool_env, **kw)
        except Exception as e:
            log.exception("Output 추출 오류")
            out = CaseOutcome("failed", error=str(e))
        notice = None
        if out.results and run_dir.is_dir():
            try:
                if not ws.record_case(sdir, run_dir, {"case_id": job["case_id"], "source": job.get("source"), "params": job.get("params") or {},
                                                      "results": {**(job.get("results") or {}), **out.results},
                                                      **({"source_dir": str(source)} if kw else {})}):
                    notice = CSV_NOTICE
            except Exception:  # noqa: BLE001 — 뽑은 값은 서버로 보낸다. 이 PC의 기록만 빠진다
                log.exception("Output 추출 기록 실패 C%s", job["case_id"])
        archive = self._archive(job, run_dir) if out.results else None  # 새 Output도 case.h5에 반영
        self.report({"type": "case.status", "case_id": job["case_id"], "status": "complete", "extract": True,
                     "results": out.results, "error": None if out.status == "complete" else f"Output 추출 실패: {out.error}",
                     "archive": archive, "notice": notice})

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
                self._runner_for(job), self.s.tools,
                job.get("dry_run", False), project_dir=project_dir, tool_commands=self.s.tool_commands,
                case_meta={"case_id": job["case_id"], "study_id": job["study_id"]}, tool_envs=self.s.tool_env,
            )
        except Exception as e:
            log.exception("케이스 실행 오류")
            out = CaseOutcome("failed", error=str(e))
        stop = self._stops.get(job["case_id"])
        if stop and stop.is_set() and out.status == "failed":  # 중지로 끝난 것을 해석 실패로 보이지 않게 한다
            out = CaseOutcome("cancelled", error=STOPPED_MESSAGE)
            try:
                ws.record_case(sdir, run_dir, {**meta, "status": out.status, "error": out.error, "finished": _now()})
            except Exception:  # noqa: BLE001 — 기록을 못 해도 중지는 중지다
                log.exception("중지 기록 실패 C%s", job["case_id"])
            self.report({"type": "case.status", "case_id": job["case_id"], "status": out.status, "error": out.error})
            return
        notice = None
        try:
            if not ws.record_case(sdir, run_dir, {**meta, "status": out.status, "results": out.results, "error": out.error, "finished": _now()}):
                notice = CSV_NOTICE
        except Exception as e:  # noqa: BLE001 — 디스크 부족·폴더 사라짐. 끝난 것으로 기록하지 못했다고 알리되 솔버가 낸 값은 버리지 않는다
            log.exception("케이스 기록 실패 C%s", job["case_id"])
            out = CaseOutcome("failed", results=out.results, error=explain_failure(e))
        self._deliver(job, run_dir, out, project_dir, notice)

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
                          level=cfg["level"], float32=cfg["float32"], units=(job.get("adapter_config") or {}).get("units"))
        except Exception as e:
            log.exception("case.h5 묶기 실패")
            return {"ok": False, "error": f"case.h5 묶기 실패: {e}", "stored": "pc"}
        h5 = run_dir / H5_NAME
        try:
            info = {**archive_info(s), "stored": "pc", "pc_path": str(h5), "sha256": file_sha256(h5)}
        except Exception as e:
            log.exception("case.h5 정보 읽기 실패")
            return {"ok": False, "error": f"case.h5 정보를 읽지 못했습니다: {e}", "stored": "pc"}
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
