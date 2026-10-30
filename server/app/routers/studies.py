"""스터디(DOE·실행·학습·BO/능동학습)와 Surrogate 모델(배포·예측)."""
import secrets
from datetime import datetime, timedelta

import numpy as np
import base64
import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from cae_adapters import REGISTRY

from .. import models, schemas
from ..agent.link import AgentError
from ..config import settings
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
    return {"study": study, "counts": counts, "best": service.best_case(study), "running": service.is_running(study.id),
            "extracting": service.extracting(study.id)}


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
                     objectives=[o.model_dump() for o in body.objectives],
                     constraints=[c.model_dump(exclude_none=True) for c in body.constraints], adapter=body.adapter,
                     outputs=[o.model_dump() for o in body.outputs], adapter_config=body.adapter_config)
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


@router.post("/studies/{study_id}/outputs")
def add_outputs(study_id: int, body: schemas.OutputAddRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Output 추가: 이미 해석한 케이스에서도 추출하고, 이후 케이스는 해석할 때 함께 추출한다."""
    s = _study(study_id, user, db)
    try:
        return service.add_outputs(db, s, [o.model_dump() for o in body.outputs], body.post, body.extract_existing, user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


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
    project = db.get(models.Project, study.project_id)
    return {"model": schemas.ModelResponse.model_validate(m), "variables": study.variables,
            "objectives": study.objectives, "study_name": study.name,
            "project_id": study.project_id, "project_name": project.name if project else ""}


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


# ---------- 형상·필드 데이터와 모델 ----------
@router.post("/cases/{case_id}/fields")
async def upload_field(case_id: int, file: UploadFile = File(...), user=Depends(get_current_user), db: Session = Depends(get_db)):
    """PC Agent가 케이스의 fields/<이름>.npz(points, values, triangles)를 올린다."""
    c = get_or_404(db, models.Case, case_id, "케이스")
    study = db.get(models.Study, c.study_id)
    require_member(study.project_id, user, db)
    name = Path(file.filename or "field.npz").stem
    if not name.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(400, "필드 이름은 영문·숫자·_- 만 쓸 수 있습니다")
    dest = service.field_path(study.project_id, study.id, c.id, name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    if len(data) > 200 * 1024 * 1024:
        raise HTTPException(413, "필드 파일은 200MB 이하만 올릴 수 있습니다")
    dest.write_bytes(data)
    c.fields = sorted(set((c.fields or []) + [name]))
    db.commit()
    return {"ok": True, "field": name}


# ---------- 케이스 해석 데이터(case.h5, HDF5) ----------
def _case_member(case_id: int, user, db) -> tuple[models.Case, models.Study]:
    c = get_or_404(db, models.Case, case_id, "케이스")
    study = db.get(models.Study, c.study_id)
    require_member(study.project_id, user, db)
    return c, study


async def _receive(file: UploadFile, tmp: Path, limit_mb: int, label: str) -> str:
    """큰 파일도 메모리에 다 올리지 않고 나눠 쓴다. 돌려주는 값: sha256."""
    limit, size = limit_mb * 1024 * 1024, 0
    h = hashlib.sha256()
    with open(tmp, "wb") as out:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                out.close()
                tmp.unlink(missing_ok=True)
                raise HTTPException(413, f"{label}은(는) {limit_mb} MB까지 올릴 수 있습니다 → PC에 그대로 둡니다")
            h.update(chunk)
            out.write(chunk)
    return h.hexdigest()


@router.post("/cases/{case_id}/archive")
async def upload_archive(case_id: int, file: UploadFile = File(...), user=Depends(get_current_user), db: Session = Depends(get_db)):
    """PC Agent가 케이스 폴더를 묶은 case.h5를 올린다. 크기 제한(archive_max_mb)을 넘으면 PC에만 둔다."""
    from cae_adapters.h5store import FORMAT, archive_info, summary

    c, study = _case_member(case_id, user, db)
    dest = service.archive_path(study.project_id, study.id, c.id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    sha = await _receive(file, tmp, settings.archive_max_mb, "case.h5")
    try:
        s = summary(tmp)
        if s["format"] != FORMAT:
            raise ValueError("DDS case.h5 형식이 아닙니다")
    except Exception as e:
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"case.h5를 읽지 못했습니다: {e}")
    tmp.replace(dest)
    c.archive = {**(c.archive or {}), **archive_info(s), "stored": "server", "sha256": sha}
    db.commit()
    manager.publish({"type": "case_updated", "project_id": study.project_id, "data": {"id": c.id, "study_id": study.id}})
    return c.archive


@router.get("/cases/{case_id}/archive")
def archive_summary(case_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """case.h5에 담긴 것: 메타·메쉬·필드(스텝·프레임)·이력·파일, PC에 원본으로 남은 파일 목록, 검증 결과."""
    from cae_adapters.h5store import summary

    c, study = _case_member(case_id, user, db)
    path = service.archive_path(study.project_id, study.id, c.id)
    if path.is_file():
        return {**summary(path), "stored": "server"}
    if c.archive:
        return {**c.archive, "stored": c.archive.get("stored", "pc")}
    raise HTTPException(404, "이 케이스의 해석 데이터(case.h5)가 아직 없습니다")


@router.get("/cases/{case_id}/archive/file")
def archive_file(case_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    c, study = _case_member(case_id, user, db)
    path = service.archive_path(study.project_id, study.id, c.id)
    if not path.is_file():
        raise HTTPException(404, "서버에 case.h5가 없습니다 → 케이스를 실행한 PC의 케이스 폴더에 있습니다")
    return FileResponse(path, filename=f"{study.name}_C{c.id:04d}.h5", media_type="application/x-hdf5")


@router.post("/studies/{study_id}/train_field", response_model=schemas.FieldModelResponse)
def train_field(study_id: int, body: schemas.TrainFieldRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = _study(study_id, user, db)
    try:
        return service.train_field(db, s, body.field, body.flow_id, body.node_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/studies/{study_id}/field-models", response_model=list[schemas.FieldModelResponse])
def list_field_models(study_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _study(study_id, user, db)
    return db.query(models.FieldModel).filter_by(study_id=study_id).order_by(models.FieldModel.id.desc()).all()


def _b64(a) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


_rom_cache: dict[int, object] = {}


def _rom(fm: models.FieldModel):
    if fm.id not in _rom_cache:
        from ..study.fieldmodel import FieldROM
        _rom_cache[fm.id] = FieldROM.load(fm.path)
    return _rom_cache[fm.id]


def _field_mesh(fm: models.FieldModel) -> dict:
    rom = _rom(fm)
    tris = rom.triangles if rom.triangles is not None else np.zeros((0, 3), np.int32)
    return {"field": fm.field, "n_nodes": rom.n_nodes, "shape_varies": rom.shape_varies, "variables": rom.variables,
            "triangles_b64": _b64(tris.astype(np.uint32)), "n_triangles": int(len(tris))}


def _field_predict(fm: models.FieldModel, params: dict[str, float]) -> dict:
    rom = _rom(fm)
    missing = [v for v in rom.variables if v not in params]
    if missing:
        raise HTTPException(400, f"입력값이 없습니다: {', '.join(missing)}")
    pts, vals, unc = rom.predict(params)
    mag = vals if vals.ndim == 1 else np.linalg.norm(vals, axis=1)
    return {"points_b64": _b64(pts.astype(np.float32)), "values_b64": _b64(mag.astype(np.float32)),
            "uncertainty_b64": _b64(unc.astype(np.float32)), "min": float(mag.min()), "max": float(mag.max()),
            "max_uncertainty": float(unc.max())}


def _fm_member(field_model_id: int, user, db) -> models.FieldModel:
    fm = get_or_404(db, models.FieldModel, field_model_id, "필드 모델")
    require_member(db.get(models.Study, fm.study_id).project_id, user, db)
    return fm


@router.get("/field-models/{field_model_id}/mesh")
def field_mesh(field_model_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _field_mesh(_fm_member(field_model_id, user, db))


@router.post("/field-models/{field_model_id}/predict")
def field_predict(field_model_id: int, body: schemas.PredictRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _field_predict(_fm_member(field_model_id, user, db), body.params)


def _fm_shared(token: str, field_model_id: int, db) -> models.FieldModel:
    sh = _share(token, db)
    m = db.get(models.SurrogateModel, sh.model_id)
    fm = get_or_404(db, models.FieldModel, field_model_id, "필드 모델")
    if fm.study_id != m.study_id:
        raise HTTPException(404, "공유되지 않은 모델입니다")
    return fm


@router.get("/shares/{token}/field-models", response_model=list[schemas.FieldModelResponse])
def shared_field_models(token: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    m = db.get(models.SurrogateModel, _share(token, db).model_id)
    return db.query(models.FieldModel).filter_by(study_id=m.study_id).order_by(models.FieldModel.id.desc()).all()


@router.get("/shares/{token}/field-models/{field_model_id}/mesh")
def shared_field_mesh(token: str, field_model_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _field_mesh(_fm_shared(token, field_model_id, db))


@router.post("/shares/{token}/field-models/{field_model_id}/predict")
def shared_field_predict(token: str, field_model_id: int, body: schemas.PredictRequest, user=Depends(get_current_user),
                         db: Session = Depends(get_db)):
    return _field_predict(_fm_shared(token, field_model_id, db), body.params)
