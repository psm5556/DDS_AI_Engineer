"""학습 데이터: 학습 샘플(train.h5) 올리기, 데이터셋 만들기·보기. docs/cae-data-hdf5.md 8절."""
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from cae_adapters.train_h5 import read_info

from .. import models, schemas
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, get_or_404, require_member
from ..study import dataset as ds_service
from .studies import _case_member, _receive, _study

router = APIRouter(prefix="/api", tags=["training"])


@router.post("/cases/{case_id}/train_sample")
async def upload_train_sample(case_id: int, spec_hash: str = Query(...), file: UploadFile = File(...), user=Depends(get_current_user),
                              db: Session = Depends(get_db)):
    """PC Agent가 case.h5에서 만든 학습 샘플을 올린다. 조건 해시가 파일 이름에 들어가 다른 조건의 샘플을 덮어쓰지 않는다."""
    if not re.fullmatch(r"[0-9a-f]{12}", spec_hash):
        raise HTTPException(400, "조건 해시 형식이 아닙니다")
    c, study = _case_member(case_id, user, db)
    dest = ds_service.training_dir() / ds_service.sample_rel(study.project_id, study.id, c.id, spec_hash)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    await _receive(file, tmp, settings.archive_max_mb, "학습 샘플")
    try:
        read_info(tmp)
    except Exception as e:  # noqa: BLE001 — 깨진 파일·다른 형식 모두 거절한다
        tmp.unlink(missing_ok=True)
        raise HTTPException(400, f"학습 샘플을 읽지 못했습니다: {e}")
    tmp.replace(dest)
    return ds_service.record_sample(db, c, study, spec_hash, dest)


@router.post("/studies/{study_id}/datasets", response_model=schemas.DatasetResponse)
def create_dataset(study_id: int, body: schemas.DatasetCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """완료된 케이스로 학습 데이터셋을 만든다. 오래 걸릴 수 있어 바로 돌려주고, status가 building → ready | failed로 바뀐다."""
    s = _study(study_id, user, db)
    try:
        return ds_service.create_dataset(db, s, body.name, body.targets, body.frames, body.step, body.flow_id, body.node_id, user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/studies/{study_id}/datasets", response_model=list[schemas.DatasetResponse])
def list_datasets(study_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _study(study_id, user, db)
    rows = db.query(models.TrainingDataset).filter_by(study_id=study_id).order_by(models.TrainingDataset.id.desc()).all()
    for ds in rows:
        ds_service.reconcile(db, ds)
    return rows


def _dataset(dataset_id: int, user, db) -> models.TrainingDataset:
    ds = get_or_404(db, models.TrainingDataset, dataset_id, "학습 데이터셋")
    require_member(db.get(models.Study, ds.study_id).project_id, user, db)
    return ds


@router.get("/datasets/{dataset_id}", response_model=schemas.DatasetResponse)
def get_dataset(dataset_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    ds = _dataset(dataset_id, user, db)
    ds_service.reconcile(db, ds)
    return ds


@router.get("/datasets/{dataset_id}/manifest")
def dataset_manifest(dataset_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """학습기가 읽는 목록: 샘플·분할·통계·입력. 샘플 경로는 data/training 기준이다."""
    ds = _dataset(dataset_id, user, db)
    if not ds.manifest_path or not Path(ds.manifest_path).is_file():
        raise HTTPException(404, ds.error or "데이터셋이 아직 준비되지 않았습니다")
    return json.loads(Path(ds.manifest_path).read_text(encoding="utf-8"))
