"""스터디(DOE·실행·학습·BO/능동학습)와 Surrogate 모델(배포·예측)."""
import secrets
from datetime import datetime, timedelta

import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from cae_adapters import REGISTRY

from .. import models, schemas
from ..agent.link import AgentError
from ..database import get_db
from ..deps import get_current_user, get_or_404, get_project_for_member, require_member
from ..study import service
from ..ws import manager

router = APIRouter(prefix="/api", tags=["studies"])


def _study(study_id: int, user, db) -> models.Study:
    s = get_or_404(db, models.Study, study_id, "스터디")
    require_member(s.project_id, user, db)
    return s


def _status(study: models.Study) -> dict:
    counts: dict[str, int] = {}
    for c in study.cases:
        counts[c.status] = counts.get(c.status, 0) + 1
    return {"study": study, "counts": counts, "best": service.best_case(study), "running": service.is_running(study.id)}


@router.get("/adapters")
def list_adapters(user=Depends(get_current_user)):
    return [a.describe() for a in REGISTRY.values()]


@router.get("/projects/{project_id}/studies", response_model=list[schemas.StudyResponse])
def list_studies(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    return db.query(models.Study).filter_by(project_id=project.id).order_by(models.Study.id.desc()).all()


@router.post("/projects/{project_id}/studies", response_model=schemas.StudyResponse)
def create_study(body: schemas.StudyCreate, project: models.Project = Depends(get_project_for_member),
                 db: Session = Depends(get_db)):
    if body.adapter not in REGISTRY:
        raise HTTPException(400, f"알 수 없는 어댑터: {body.adapter}")
    for v in body.variables:
        if v.lower >= v.upper:
            raise HTTPException(400, f"변수 '{v.name}'의 하한이 상한보다 작아야 합니다")
    s = models.Study(project_id=project.id, name=body.name, variables=[v.model_dump() for v in body.variables],
                     objectives=[o.model_dump() for o in body.objectives], adapter=body.adapter,
                     adapter_config=body.adapter_config)
    db.add(s)
    db.commit()
    manager.publish({"type": "study_updated", "project_id": project.id, "data": {"id": s.id}})
    return s


@router.get("/studies/{study_id}", response_model=schemas.StudyStatus)
def get_study(study_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _status(_study(study_id, user, db))


@router.get("/studies/{study_id}/cases", response_model=list[schemas.CaseResponse])
def list_cases(study_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _study(study_id, user, db).cases


@router.post("/studies/{study_id}/doe", response_model=list[schemas.CaseResponse])
def create_doe(study_id: int, body: schemas.DoeRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = _study(study_id, user, db)
    try:
        return service.create_doe(db, s, body.method, body.n, body.seed, body.flow_id, body.node_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/studies/{study_id}/run")
def run_cases(study_id: int, body: schemas.RunRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _study(study_id, user, db)
    try:
        n = service.run_cases_async(study_id, body.case_ids, body.dry_run, user.id)
    except AgentError as e:
        raise HTTPException(409, str(e))
    return {"queued": n}


@router.post("/studies/{study_id}/train", response_model=schemas.ModelResponse)
def train(study_id: int, body: schemas.TrainRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = _study(study_id, user, db)
    try:
        return service.train(db, s, body.outputs, body.flow_id, body.node_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/studies/{study_id}/suggest", response_model=list[schemas.CaseResponse])
def suggest(study_id: int, body: schemas.SuggestRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = _study(study_id, user, db)
    try:
        return service.suggest(db, s, body.mode, body.n, body.model_id, body.flow_id, body.node_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ---------- 모델 / Simulator ----------
def _model(model_id: int, user, db) -> models.SurrogateModel:
    m = get_or_404(db, models.SurrogateModel, model_id, "모델")
    study = db.get(models.Study, m.study_id)
    require_member(study.project_id, user, db)
    return m


@router.get("/projects/{project_id}/models", response_model=list[schemas.ModelResponse])
def list_models(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    return (db.query(models.SurrogateModel).join(models.Study).filter(models.Study.project_id == project.id)
            .order_by(models.SurrogateModel.id.desc()).all())


@router.get("/models/{model_id}/spec")
def model_spec(model_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Simulator 화면용: 입력 변수 범위와 출력 목록."""
    m = _model(model_id, user, db)
    study = db.get(models.Study, m.study_id)
    return {"model": schemas.ModelResponse.model_validate(m), "variables": study.variables,
            "objectives": study.objectives, "study_name": study.name}


@router.post("/models/{model_id}/deploy", response_model=schemas.ModelResponse)
def deploy(model_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    m = _model(model_id, user, db)
    m.deployed = True
    db.commit()
    study = db.get(models.Study, m.study_id)
    manager.publish({"type": "model_updated", "project_id": study.project_id, "data": {"id": m.id}})
    return m


@router.post("/models/{model_id}/predict", response_model=schemas.PredictResponse)
def predict(model_id: int, body: schemas.PredictRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    m = _model(model_id, user, db)
    sur = service.load_surrogate(m)
    missing = [v for v in sur.variables if v not in body.params]
    if missing:
        raise HTTPException(400, f"입력값이 없습니다: {', '.join(missing)}")
    mean, std = sur.predict(np.array([[body.params[v] for v in sur.variables]]))
    return {"mean": {k: float(v[0]) for k, v in mean.items()}, "std": {k: float(v[0]) for k, v in std.items()}}


@router.post("/models/{model_id}/predict_batch", response_model=schemas.PredictBatchResponse)
def predict_batch(model_id: int, body: schemas.PredictBatchRequest, user=Depends(get_current_user),
                  db: Session = Depends(get_db)):
    """Simulator 민감도 곡선용 여러 점 예측."""
    m = _model(model_id, user, db)
    sur = service.load_surrogate(m)
    try:
        X = np.array([[p[v] for v in sur.variables] for p in body.points])
    except KeyError as e:
        raise HTTPException(400, f"입력값이 없습니다: {e}")
    mean, std = sur.predict(X)
    return {"mean": {k: v.tolist() for k, v in mean.items()}, "std": {k: v.tolist() for k, v in std.items()}}


# ---------- Simulator 공유 링크 ----------
def _share(token: str, db: Session) -> models.ShareLink:
    sh = db.query(models.ShareLink).filter_by(token=token).first()
    if not sh or not sh.enabled:
        raise HTTPException(404, "공유가 중지되었거나 없는 링크입니다")
    if sh.expires_at and sh.expires_at < datetime.now():
        raise HTTPException(410, "공유 기간이 끝난 링크입니다")
    return sh


@router.post("/models/{model_id}/shares", response_model=schemas.ShareResponse)
def create_share(model_id: int, body: schemas.ShareCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    m = _model(model_id, user, db)
    if not m.deployed:
        raise HTTPException(400, "배포된 Simulator만 공유할 수 있습니다")
    sh = models.ShareLink(token=secrets.token_urlsafe(16), model_id=m.id, created_by=user.id, note=body.note,
                          expires_at=datetime.now() + timedelta(days=body.expires_days) if body.expires_days else None)
    db.add(sh)
    db.commit()
    return sh


@router.get("/models/{model_id}/shares", response_model=list[schemas.ShareResponse])
def list_shares(model_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _model(model_id, user, db)
    return db.query(models.ShareLink).filter_by(model_id=model_id).order_by(models.ShareLink.id.desc()).all()


@router.delete("/shares/{token}")
def stop_share(token: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    sh = db.query(models.ShareLink).filter_by(token=token).first()
    if not sh:
        raise HTTPException(404, "없는 링크입니다")
    _model(sh.model_id, user, db)  # 프로젝트 멤버만 중지
    sh.enabled = False
    db.commit()
    return {"ok": True}


@router.get("/shares/{token}")
def share_spec(token: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """공유받은 사람(로그인한 사내 사용자)이 보는 Simulator 정보."""
    sh = _share(token, db)
    m = db.get(models.SurrogateModel, sh.model_id)
    study = db.get(models.Study, m.study_id)
    owner = db.get(models.User, sh.created_by)
    return {"model": schemas.ModelResponse.model_validate(m), "variables": study.variables, "objectives": study.objectives,
            "study_name": study.name, "shared_by": owner.display_name if owner else "", "note": sh.note}


@router.post("/shares/{token}/predict", response_model=schemas.PredictResponse)
def share_predict(token: str, body: schemas.PredictRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    sh = _share(token, db)
    sh.uses += 1
    db.commit()
    sur = service.load_surrogate(db.get(models.SurrogateModel, sh.model_id))
    missing = [v for v in sur.variables if v not in body.params]
    if missing:
        raise HTTPException(400, f"입력값이 없습니다: {', '.join(missing)}")
    mean, std = sur.predict(np.array([[body.params[v] for v in sur.variables]]))
    return {"mean": {k: float(v[0]) for k, v in mean.items()}, "std": {k: float(v[0]) for k, v in std.items()}}


@router.post("/shares/{token}/predict_batch", response_model=schemas.PredictBatchResponse)
def share_predict_batch(token: str, body: schemas.PredictBatchRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    sh = _share(token, db)
    sur = service.load_surrogate(db.get(models.SurrogateModel, sh.model_id))
    try:
        X = np.array([[p[v] for v in sur.variables] for p in body.points])
    except KeyError as e:
        raise HTTPException(400, f"입력값이 없습니다: {e}")
    mean, std = sur.predict(X)
    return {"mean": {k: v.tolist() for k, v in mean.items()}, "std": {k: v.tolist() for k, v in std.items()}}
