"""표 → 등록 → 학습 → 배포를 서버 작업 하나로 돌린다. 진행 상태는 DB에 있어 화면을 닫거나 새로고침해도 돌아와 읽을 수 있다.

브라우저가 단계를 이어 부르면 학습 중에 새로고침했을 때 배포되지 않은 모델만 남고 아무도 그 사실을 모른다.
붙여 넣은 표 글자는 저장하지 않는다. 그래서 서버가 다시 시작되면 등록을 마친 작업만 이어 간다.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..database import SessionLocal
from ..ws import manager
from . import importer, service

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="table-job")
POLL_SEC = 0.5
Register = Callable[[Session, models.TableJob, models.User], dict]


def view(job: models.TableJob) -> dict[str, Any]:
    now = job.finished_at or datetime.now()
    return {"id": job.id, "project_id": job.project_id, "study_id": job.study_id, "model_id": job.model_id, "name": job.name, "status": job.status, "phase": job.phase,
            "rows": job.rows or 0, "excluded_count": job.excluded_count or 0, "reused": bool(job.reused), "error": job.error, "seen": bool(job.seen),
            "phase_sec": max(0, int((now - (job.phase_at or job.created_at)).total_seconds())), "elapsed_sec": max(0, int((now - job.created_at).total_seconds()))}


def _phase(db: Session, job: models.TableJob, phase: str):
    job.phase, job.phase_at = phase, datetime.now()
    db.commit()


def _fail(db: Session, job: models.TableJob, error: str):
    job.status, job.error, job.finished_at = "failed", error, datetime.now()
    db.commit()


def _finish(db: Session, job: models.TableJob, model_id: int, reused: bool = False):
    job.status, job.phase, job.model_id, job.reused, job.finished_at = "done", "done", model_id, reused, datetime.now()
    db.commit()
    if job.project_id:
        manager.publish({"type": "model_updated", "project_id": job.project_id, "data": {"id": model_id}})


def start(db: Session, user: models.User, name: str, project_id: int | None, outputs: list[str] | None, register: Register) -> models.TableJob:
    job = models.TableJob(user_id=user.id, project_id=project_id, name=name[:200], outputs=outputs, status="running", phase="import", phase_at=datetime.now())
    db.add(job)
    db.commit()
    db.refresh(job)
    _pool.submit(_run, job.id, register)
    return job


def _run(job_id: int, register: Register):
    created = None
    try:
        with SessionLocal() as db:
            job = db.get(models.TableJob, job_id)
            user = db.get(models.User, job.user_id)
            if job.project_id is None:  # 첫 화면: 표를 받을 수 있을 때만 프로젝트가 남는다
                project = models.Project(name=job.name, description="", owner_id=user.id)
                db.add(project)
                db.flush()
                db.add(models.ProjectMember(project_id=project.id, user_id=user.id, role="master"))
                job.project_id = created = project.id
                db.commit()
            try:
                rep = register(db, job, user)
            except HTTPException as e:
                _reject(db, job, str(e.detail), created)
                return
            except importer.ImportRejected as e:
                _reject(db, job, str(e), created)
                return
            job.study_id, job.rows, job.excluded_count = rep["study_id"], rep.get("registered") or rep.get("duplicates") or 0, rep.get("excluded_count") or 0
            db.commit()
            if rep.get("reused"):
                # 같은 표를 다시 붙여 넣었다. 사용자가 되돌린 버전을 조용히 바꾸지 않는다(공유 링크도 따라간다)
                live = service.current_model(db, rep["study_id"])
                if live is not None:
                    _finish(db, job, live.id, reused=True)
                    return
        _train_and_deploy(job_id)
    except Exception as e:  # noqa: BLE001 — 화면이 끝없이 기다리지 않게 어떤 실패든 상태로 남긴다
        log.exception("표 작업 실패 %s", job_id)
        with SessionLocal() as db:
            job = db.get(models.TableJob, job_id)
            if job is not None and job.status == "running":
                _fail(db, job, f"표로 Simulator를 만들지 못했습니다({e.__class__.__name__}) → 다시 시도하고, 계속되면 관리자에게 서버 로그를 확인해 달라고 하세요")


def _reject(db: Session, job: models.TableJob, why: str, created: int | None):
    if created is not None:  # 표를 못 받았으면 빈 프로젝트를 남기지 않는다
        project = db.get(models.Project, created)
        job.project_id = None
        db.commit()
        if project is not None:
            db.delete(project)
            db.commit()
    _fail(db, job, why)


def _train_and_deploy(job_id: int):
    with SessionLocal() as db:
        job = db.get(models.TableJob, job_id)
        study = db.get(models.Study, job.study_id)
        _phase(db, job, "train")
        try:
            # 고른 출력을 그대로 넘긴다. 비우면 일부 행이 빈 출력은 서버가 빼는데, 사용자가 고른 열이 모델에서 사라지면 안 된다
            train = service.start_train(db, study, job.outputs, None, None, job.user_id)
        except ValueError as e:
            _fail(db, job, f"표는 등록했지만 예측 모델을 만들지 못했습니다. {e}")
            return
        job_view = train
        while job_view["status"] == "running":
            time.sleep(POLL_SEC)
            job_view = service.train_job(train["job_id"]) or {"status": "failed", "error": "학습 작업을 잃었습니다 → 다시 시도해 주세요"}
        if job_view["status"] != "done" or not job_view.get("model_id"):
            _fail(db, job, f"표는 등록했지만 예측 모델을 만들지 못했습니다. {job_view.get('error') or '다시 시도해 주세요'}")
            return
        _phase(db, job, "deploy")
        db.expire_all()
        model = db.get(models.SurrogateModel, job_view["model_id"])
        service.deploy_version(db, model)
        _finish(db, job, model.id)


def resume_after_restart():
    """서버가 다시 시작되면 돌던 스레드가 없다. 등록을 마친 작업은 이어 가고, 등록 전이면 표 글자가 없어 다시 붙여 넣어 달라고 한다."""
    with SessionLocal() as db:
        for job in db.query(models.TableJob).filter_by(status="running"):
            if job.study_id:
                _pool.submit(_resume, job.id)
            else:
                _fail(db, job, "서버가 다시 시작되어 표를 등록하지 못했습니다 → 표를 다시 붙여 넣어 주세요")


def _resume(job_id: int):
    try:
        _train_and_deploy(job_id)
    except Exception as e:  # noqa: BLE001
        log.exception("표 작업 이어 가기 실패 %s", job_id)
        with SessionLocal() as db:
            job = db.get(models.TableJob, job_id)
            if job is not None and job.status == "running":
                _fail(db, job, f"표는 등록했지만 예측 모델을 만들지 못했습니다({e.__class__.__name__}) → 데이터 탭에서 다시 학습해 주세요")
