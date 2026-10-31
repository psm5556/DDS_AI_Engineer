"""스터디(DOE·실행·학습·BO/능동학습)와 Surrogate 모델(배포·예측)."""
import json
import secrets
from datetime import datetime, timedelta
from urllib.parse import quote

import numpy as np
import base64
import hashlib
import os
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session, object_session

from cae_adapters import REGISTRY
from cae_adapters.tabular import SNIFF_BYTES, Table, TableEncrypted, TableError, TableNotATable, analyze, fill_guessing, join_tables, read_pasted, read_table, sniff_table_file

from .. import models, schemas
from ..agent.link import AgentError
from ..config import settings
from ..database import get_db
from ..deps import get_current_user, get_or_404, get_project_for_member, require_member
from ..study import importer, recipe, service, table_job, thumbnail
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
    return {"study": study, "counts": counts, "best": service.best_case(study), **service.stalled_view(object_session(study), study),
            "extracting": service.extracting(study.id), "pc_notice": study.pc_notice, "data_source": service.data_source(study)}


@router.get("/adapters")
def list_adapters(user=Depends(get_current_user)):
    return [a.describe() for a in REGISTRY.values()]


@router.get("/projects/{project_id}/studies", response_model=list[schemas.StudyResponse])
def list_studies(project: models.Project = Depends(get_project_for_member), db: Session = Depends(get_db)):
    return db.query(models.Study).filter_by(project_id=project.id).order_by(models.Study.id.desc()).all()


@router.post("/projects/{project_id}/studies", response_model=schemas.StudyResponse)
def create_study(body: schemas.StudyCreate, project: models.Project = Depends(get_project_for_member),
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    if body.adapter not in REGISTRY:
        raise HTTPException(400, f"알 수 없는 어댑터: {body.adapter}")
    for v in body.variables:
        if v.lower >= v.upper:
            raise HTTPException(400, f"변수 '{v.name}'의 하한이 상한보다 작아야 합니다")
    constraints = [c.model_dump(exclude_none=True) for c in body.constraints]
    try:
        service.check_constraints(constraints)
        service.check_config_scripts(body.adapter_config)
    except ValueError as e:
        raise HTTPException(400, str(e))
    # 결과 카드는 스터디 이름으로 묶인다. 같은 이름이면 옛 스터디의 카드가 새 데이터로 덮인다
    s = models.Study(project_id=project.id, name=service._unique_study_name(db, project.id, body.name), variables=[v.model_dump(exclude_none=True) for v in body.variables],
                     objectives=[o.model_dump() for o in body.objectives],
                     constraints=constraints, adapter=body.adapter,
                     outputs=[o.model_dump() for o in body.outputs], adapter_config=body.adapter_config, created_by=user.id)
    db.add(s)
    db.commit()
    manager.publish({"type": "study_updated", "project_id": project.id, "data": {"id": s.id}})
    return s


# ---------- 이미 가진 데이터 등록 (표·해석 폴더) ----------
def _read_upload(file: UploadFile) -> bytes:
    limit = settings.table_max_mb * 1024 * 1024
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"표 파일은 {settings.table_max_mb}MB까지 올릴 수 있습니다 → 필요한 행과 열만 남겨 주세요")
    return data


def _load_table(file: UploadFile, outputs_file: UploadFile | None, sheet: str | None, header_row: int | None, key: str | None,
                outputs: list[str] | None = None) -> tuple[Table, dict | None]:
    try:
        table = read_table(_read_upload(file), filename=file.filename or "table", sheet=sheet, header_row=header_row)
        # 화면 붙여넣기와 같은 규칙으로 병합 셀을 채운다. 입구에 따라 다른 모델이 되면 안 된다
        fill_guessing(table, outputs, has_output_file=outputs_file is not None)
        if outputs_file is None:
            return table, None
        other = read_table(_read_upload(outputs_file), filename=outputs_file.filename or "outputs")
        return join_tables(table, other, key)
    except TableError as e:
        raise HTTPException(400, str(e))


def _mapping(raw: str) -> schemas.TableMapping:
    try:
        return schemas.TableMapping.model_validate_json(raw or "{}")
    except ValidationError as e:
        where = ", ".join(str(x) for x in e.errors()[0]["loc"]) if e.errors() else ""
        raise HTTPException(400, f"열 지정(mapping) 형식이 잘못됐습니다({where}) → 입력·출력 열 이름 목록을 다시 보내 주세요")


def _columns_from_mapping(table: Table, cols: list[schemas.ImportColumn], label: str) -> list[dict]:
    by_name = {c.name: c for c in table.columns}
    missing = [c.name for c in cols if c.name not in by_name]
    if missing:
        raise HTTPException(400, f"표에 없는 {label} 열입니다: {', '.join(missing)} → 있는 열: {', '.join(table.names)}")
    return [{**c.model_dump(), "unit": c.unit or by_name[c.name].unit} for c in cols]


def _import_rejected(fn, *args):
    try:
        return fn(*args)
    except importer.ImportRejected as e:
        raise HTTPException(400, str(e))


@router.post("/projects/{project_id}/studies/import", response_model=schemas.ImportReport)
def import_study(body: schemas.StudyImportRequest, project: models.Project = Depends(get_project_for_member),
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    """입력·출력 행을 등록해 스터디를 만든다. 해석·PC Agent·AI 없이 학습과 Simulator까지 갈 수 있다."""
    return _import_rejected(importer.import_new, db, project.id, body.model_dump(), user.id)


@router.post("/projects/{project_id}/studies/import/preview", response_model=schemas.TablePreview)
def import_preview(file: UploadFile = File(...), outputs_file: UploadFile | None = File(None), sheet: str | None = Form(None),
                   header_row: int | None = Form(None), key: str | None = Form(None),
                   project: models.Project = Depends(get_project_for_member)):
    """표 파일의 열을 분석만 한다. 아무것도 저장하지 않는다."""
    table, join = _load_table(file, outputs_file, sheet, header_row, key)
    return {**analyze(table), "join": join}


def _read_paste(text: str, header_row: int | None, outputs: list[str] | None) -> Table:
    """붙여 넣은 글을 표로 읽는다. 병합 셀 빈칸은 출력이 아닌 열만 채운다. 출력을 안 줬으면 추측한 출력을 지킨다."""
    size = len(text.encode("utf-8", errors="ignore"))
    if size > settings.paste_max_mb * 1024 * 1024:
        raise HTTPException(413, f"붙여 넣은 표가 {size / 1024 / 1024:.0f}MB로 너무 큽니다. {settings.paste_max_mb}MB까지 받습니다 → 필요한 행과 열만 골라 다시 복사해 주세요")
    try:
        table = read_pasted(text, header_row=header_row)
    except TableError as e:
        raise HTTPException(400, str(e))
    fill_guessing(table, outputs)
    return table


@router.post("/tables/preview/text", response_model=schemas.TablePreview)
def table_preview_text(body: schemas.TablePasteRequest, user=Depends(get_current_user)):
    """붙여 넣은 표를 분석만 한다. 프로젝트가 없어도 되고 아무것도 저장하지 않는다. 첫 화면에서 보고 닫아도 빈 프로젝트가 남지 않는다."""
    return analyze(_read_paste(body.text, body.header_row, None))


@router.post("/projects/{project_id}/studies/import/text", response_model=schemas.ImportReport)
def import_text(body: schemas.TablePasteImport, project: models.Project = Depends(get_project_for_member),
                user=Depends(get_current_user), db: Session = Depends(get_db)):
    """붙여 넣은 표를 등록한다. 파일 등록과 같은 검증을 탄다. AI·PC Agent가 없어도 된다."""
    names = [c.name for c in body.outputs] if body.outputs is not None else None
    return _import_table(_read_paste(body.text, body.header_row, names), None, body, project.id, user, db)


@router.post("/tables/check")
def table_check(file: UploadFile = File(...), user=Depends(get_current_user)):
    """첨부할 표 파일을 앞부분만 본다. 암호화된 표를 첨부하고 AI가 시작될 때까지 몇 분 기다린 뒤에야 듣지 않게 한다. 저장하지 않는다."""
    try:
        sniff_table_file(file.file.read(SNIFF_BYTES), file.filename or "file")
    except (TableEncrypted, TableNotATable) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@router.post("/table-jobs", response_model=schemas.TableJobResponse)
def table_job_start(body: schemas.TableJobCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """표로 Simulator: 등록 → 학습 → 배포를 서버에서 끝까지 한다. 화면을 닫아도 이어지고, 돌아오면 상태를 읽는다."""
    if body.project_id is not None:
        require_member(body.project_id, user, db)
    size = len(body.text.encode("utf-8", errors="ignore"))
    if size > settings.paste_max_mb * 1024 * 1024:
        raise HTTPException(413, f"붙여 넣은 표가 {size / 1024 / 1024:.0f}MB로 너무 큽니다. {settings.paste_max_mb}MB까지 받습니다 → 필요한 행과 열만 골라 다시 복사해 주세요")
    names = [c.name for c in body.outputs] if body.outputs is not None else None

    def register(session: Session, job: models.TableJob, owner: models.User) -> dict:
        return _import_table(_read_paste(body.text, body.header_row, names), None, body, job.project_id, owner, session)

    job = table_job.start(db, user, (body.name or "표 예측 모델").strip() or "표 예측 모델", body.project_id, names, register)
    return table_job.view(job)


def _table_job(job_id: int, user, db: Session) -> models.TableJob:
    job = db.get(models.TableJob, job_id)
    if job is None or job.user_id != user.id:  # 내가 시작한 작업만 보인다. 다른 사람의 표 이름도 프로젝트 정보다
        raise HTTPException(404, "표 작업을 찾지 못했습니다 → 표를 다시 붙여 넣어 주세요")
    return job


@router.get("/table-jobs", response_model=list[schemas.TableJobResponse])
def table_jobs(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """화면이 돌아와 읽는 목록: 도는 작업과, 끝났지만 아직 확인하지 않은 작업.
    프로젝트를 지운 뒤의 완료는 뺀다. 만든 Simulator가 함께 사라져 '만들었습니다 [열기]'가 거짓이 된다."""
    j = models.TableJob
    rows = (db.query(j).filter(j.user_id == user.id, (j.status == "running") | (j.seen.is_(False) & ((j.status != "done") | j.project_id.isnot(None))))
            .order_by(j.id.desc()).limit(20).all())
    return [table_job.view(j) for j in rows]


@router.get("/table-jobs/{job_id}", response_model=schemas.TableJobResponse)
def table_job_status(job_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return table_job.view(_table_job(job_id, user, db))


@router.post("/table-jobs/{job_id}/seen")
def table_job_seen(job_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _table_job(job_id, user, db).seen = True
    db.commit()
    return {"ok": True}


def _import_table(table: Table, join: dict | None, m: schemas.TableMapping, project_id: int, user, db: Session) -> dict:
    if 0 < len(table.rows) < 3:  # 한두 행이면 모든 열이 상수로 보여 "출력 열이 없다"는 엉뚱한 이유가 나온다. 진짜 이유를 먼저 말한다
        raise HTTPException(400, f"데이터 행이 {len(table.rows)}개뿐입니다. 예측 모델에는 3행 이상이 필요합니다 → 행을 더 복사해 붙여 넣으세요")
    info = analyze(table)
    pre_removed: list[dict] = []
    if m.inputs is None:
        # 입력을 안 줬으면 출력 말고 숫자 열이 전부 입력이다. 글자 열·상수 열도 넘겨 등록 경로의 규칙(고정값 기록, 이유와 함께 제외)을 그대로 탄다
        by = {c["name"]: c for c in info["columns"]}
        outputs = _columns_from_mapping(table, m.outputs, "출력") if m.outputs is not None else [{"name": n, "unit": by[n].get("unit") or ""} for n in info["outputs"]]
        used = {c["name"] for c in outputs}
        inputs = [{"name": c["name"], "unit": c.get("unit") or ""} for c in info["columns"] if c["kind"] == "numeric" and c["name"] not in used]
        inputs += [{"name": c["name"], "unit": c.get("unit") or ""} for c in info["columns"]
                   if c["kind"] in ("categorical", "constant") and not c.get("row_label") and c["name"] not in used]
        pre_removed = [{"name": c["name"], "reason": c["role_reason"]} for c in info["columns"]
                       if (c["kind"] in ("id", "empty") or c.get("row_label")) and c["name"] not in used]
    else:
        inputs = _columns_from_mapping(table, m.inputs, "입력")
        outputs = _columns_from_mapping(table, m.outputs or [], "출력")
    body = {"name": m.name, "source": table.source, "kind": "table", "inputs": inputs, "outputs": outputs,
            "objectives": [o.model_dump() for o in m.objectives], "constraints": [c.model_dump(exclude_none=True) for c in m.constraints],
            "rows": table.records(numeric=True), "fixed": m.fixed, "expects_fields": m.expects_fields, "flow_id": m.flow_id, "node_id": m.node_id,
            "pre_removed": pre_removed, "pre_warnings": info["warnings"]}
    return {**_import_rejected(importer.import_new, db, project_id, body, user.id), "join": join}


@router.post("/projects/{project_id}/studies/import/file", response_model=schemas.ImportReport)
def import_file(file: UploadFile = File(...), outputs_file: UploadFile | None = File(None), mapping: str = Form("{}"),
                project: models.Project = Depends(get_project_for_member), user=Depends(get_current_user), db: Session = Depends(get_db)):
    """표 파일을 바로 등록한다. mapping을 비우면 미리보기의 추측대로 등록한다."""
    m = _mapping(mapping)
    table, join = _load_table(file, outputs_file, m.sheet, m.header_row, m.key, [c.name for c in m.outputs] if m.outputs is not None else None)
    return _import_table(table, join, m, project.id, user, db)


@router.post("/studies/{study_id}/cases/import", response_model=schemas.ImportReport)
def import_cases(study_id: int, body: schemas.CaseImportRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """있는 스터디에 행을 더한다. 같은 행은 중복되지 않고, 대기 중인 DOE 점과 입력이 같으면 그 점을 채운다."""
    s = _study(study_id, user, db)
    return _import_rejected(importer.import_rows, db, s, body.model_dump(), user.id)


@router.post("/studies/{study_id}/cases/import/file", response_model=schemas.ImportReport)
def import_cases_file(study_id: int, file: UploadFile = File(...), outputs_file: UploadFile | None = File(None), mapping: str = Form("{}"),
                      user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = _study(study_id, user, db)
    m = _mapping(mapping)
    table, join = _load_table(file, outputs_file, m.sheet, m.header_row, m.key, _output_names(s, m))
    return _import_rows_table(s, table, join, m, user, db)


def _output_names(s: models.Study, m: schemas.TableMapping) -> list[str]:
    """있는 스터디의 출력은 빈칸을 위 값으로 채우지 않는다. 없는 결과를 지어내게 된다."""
    return [*service.output_names(s), *[c.name for c in m.outputs or []]]


@router.post("/studies/{study_id}/cases/import/text", response_model=schemas.ImportReport)
def import_cases_text(study_id: int, body: schemas.TablePasteImport, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """있는 스터디에 붙여 넣은 표의 행을 더한다. AI·PC Agent가 없어도 된다."""
    s = _study(study_id, user, db)
    return _import_rows_table(s, _read_paste(body.text, body.header_row, _output_names(s, body)), None, body, user, db)


def _import_rows_table(s: models.Study, table: Table, join: dict | None, m: schemas.TableMapping, user, db: Session) -> dict:
    outputs = _columns_from_mapping(table, m.outputs or [], "출력")
    warnings = list(table.warnings)
    units = {v["name"]: v.get("unit") for v in s.variables} | {o["name"]: o.get("unit") for o in s.outputs or []}
    for c in table.columns:  # 값은 그대로 두고 알린다. 단위를 맘대로 환산하면 더 찾기 어려운 오류가 된다
        if c.unit and units.get(c.name) and c.unit != units[c.name]:
            warnings.append(f"'{c.name}'의 단위가 스터디({units[c.name]})와 표({c.unit})가 다릅니다. 값은 바꾸지 않았습니다 → 단위를 맞춘 표로 다시 불러오세요")
    body = {"rows": table.records(numeric=True), "outputs": outputs, "source": table.source, "expects_fields": m.expects_fields,
            "flow_id": m.flow_id, "node_id": m.node_id, "pre_warnings": warnings}
    return {**_import_rejected(importer.import_rows, db, s, body, user.id), "join": join}


@router.get("/studies/{study_id}", response_model=schemas.StudyStatus)
def get_study(study_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _status(_study(study_id, user, db))


@router.put("/studies/{study_id}/adapter", response_model=schemas.StudyStatus)
def set_study_adapter(study_id: int, body: schemas.StudyAdapterRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """해석 방법을 나중에 연결한다. 대기 중인 DOE 케이스는 이어서 돌릴 수 있다."""
    s = _study(study_id, user, db)
    try:
        service.set_adapter(db, s, body.adapter, body.adapter_config)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return _status(s)


@router.post("/studies/{study_id}/clone", response_model=schemas.StudyResponse)
def clone_study(study_id: int, body: schemas.StudyCloneRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """신규 모델로 지난번처럼: 설정을 복사한 새 스터디를 만든다. 케이스·모델을 옮기지 않아 옛 결과는 그대로다."""
    s = _study(study_id, user, db)
    if body.project_id and body.project_id != s.project_id:
        require_member(body.project_id, user, db)
    fields = body.model_dump(exclude_none=True, exclude={"project_id"})
    try:
        return service.clone_study(db, s, fields, body.project_id, owner_id=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/studies/{study_id}/doe.csv")
def export_doe(study_id: int, status: str | None = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """DOE 계획표 CSV. 밖에서 해석한 뒤 출력 칸을 채워 cases/import로 되돌리면 그 점이 채워진다."""
    s = _study(study_id, user, db)
    try:
        text = service.doe_csv(s, status)
    except ValueError as e:
        raise HTTPException(400, str(e))
    filename = quote(f"{s.name}_DOE.csv")
    return Response(content=text.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


# ---------- 레시피: 스크립트 묶음 ----------
@router.post("/studies/{study_id}/recipe", response_model=schemas.RecipeResponse)
def upload_recipe(study_id: int, file: UploadFile = File(...), note: str = Form(""), adapter_config: str | None = Form(None),
                  user=Depends(get_current_user), db: Session = Depends(get_db)):
    """스크립트 zip을 서버에 둔다. 다른 멤버 PC에서 내려받아 쓴다. 서버는 검사와 보관만 하고 실행하지 않는다."""
    s = _study(study_id, user, db)
    data = file.file.read(recipe.MAX_BUNDLE + 1)
    config = None
    if adapter_config:
        try:
            config = json.loads(adapter_config)
        except RecursionError:
            raise HTTPException(400, "adapter_config가 너무 깊게 중첩되어 있습니다 → 설정을 단순하게 고치거나 비워 두면 스터디 설정을 씁니다")
        except ValueError:
            raise HTTPException(400, "adapter_config가 JSON이 아닙니다 → 형식을 고치거나 비워 두면 스터디 설정을 씁니다")
        if not isinstance(config, dict):
            raise HTTPException(400, "adapter_config는 객체여야 합니다 → 형식을 고치거나 비워 두면 스터디 설정을 씁니다")
    try:
        recipe.store(s, data, config, note[:500], user.id, user.display_name)
    except recipe.RecipeRejected as e:
        raise HTTPException(400, str(e))
    except RecursionError:
        raise HTTPException(400, "adapter_config가 너무 깊게 중첩되어 있습니다 → 설정을 단순하게 고치거나 비워 두면 스터디 설정을 씁니다")
    db.commit()
    manager.publish({"type": "study_updated", "project_id": s.project_id, "data": {"id": s.id}})
    return recipe.view(s)


@router.get("/studies/{study_id}/recipe", response_model=schemas.RecipeResponse)
def get_recipe(study_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return recipe.view(_study(study_id, user, db))


@router.get("/studies/{study_id}/recipe/file")
def download_recipe(study_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = _study(study_id, user, db)
    path = recipe.recipe_path(s)
    if not s.recipe or not path.is_file():
        raise HTTPException(404, "이 스터디의 스크립트 묶음이 서버에 없습니다 → 스크립트를 만든 멤버의 PC가 켜져 있을 때 묶음을 올려 달라고 하세요")
    return FileResponse(path, filename=f"{s.name}_recipe.zip", media_type="application/zip")


@router.delete("/studies/{study_id}/recipe")
def delete_recipe(study_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = _study(study_id, user, db)
    recipe.recipe_path(s).unlink(missing_ok=True)
    s.recipe = None
    db.commit()
    return {"ok": True}


@router.get("/studies/{study_id}/cases", response_model=list[schemas.CaseResponse])
def list_cases(study_id: int, limit: int | None = None, offset: int = 0, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """limit을 주면 나눠 준다. 불러온 표는 행이 많을 수 있어 화면·AI가 한 번에 다 받지 않게 한다."""
    cases = _study(study_id, user, db).cases
    start = max(offset, 0)
    return cases[start:start + limit] if limit is not None and limit >= 0 else cases[start:]


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
        n, already = service.run_cases_async(study_id, body.case_ids, body.dry_run, user.id, body.flow_id)
    except service.NoRunMethod as e:
        raise HTTPException(400, str(e))
    except service.NeedsApproval as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except AgentError as e:
        raise HTTPException(409, str(e))
    if already:
        return {"queued": n, "already_running": already, "note": f"케이스 {already}건은 이미 돌고 있어 다시 시작하지 않았습니다."}
    if n:
        return {"queued": n}
    # 아무것도 돌리지 않았으면 왜 그런지 말한다. queued 0만 보면 돌아가는 줄 안다
    s = db.get(models.Study, study_id)
    db.refresh(s)
    gone = service.stalled_view(db, s)
    note = (f"{', '.join(gone['stalled_by'])}님의 PC가 꺼져 있거나 연결이 끊겨 '실행 중'으로 남은 케이스가 {gone['stalled']}건 있습니다 → 그 PC가 다시 연결되면 결과가 들어옵니다. 기다리지 않으려면 실행한 사람이 '중지'한 뒤 케이스를 지정해 다시 돌립니다" if gone["stalled"]
            else "돌릴 대기 케이스가 없습니다 → 끝난 케이스를 다시 돌리려면 케이스를 지정하세요. 불러온 행은 다시 돌리지 않습니다")
    return {"queued": 0, "note": note}


@router.post("/studies/{study_id}/stop", response_model=schemas.StopResponse)
def stop_cases(study_id: int, body: schemas.StopRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    _study(study_id, user, db)
    try:
        return service.stop_cases(study_id, body.case_ids, user.id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except AgentError as e:
        raise HTTPException(409, str(e))


@router.delete("/studies/{study_id}/cases/{case_id}")
def delete_case(study_id: int, case_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """불러온 행을 지운다. 잘못 넣은 값을 빼고 다시 학습할 수 있게 한다."""
    s = _study(study_id, user, db)
    case = db.get(models.Case, case_id)
    if case is None or case.study_id != s.id:
        raise HTTPException(404, "이 스터디에 없는 케이스입니다 → 케이스 번호를 확인하세요")
    try:
        reset = service.delete_case(db, s, case)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "cases": len(s.cases), "reset": reset}


@router.post("/studies/{study_id}/outputs")
def add_outputs(study_id: int, body: schemas.OutputAddRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Output 추가: 이미 해석한 케이스에서도 추출하고, 이후 케이스는 해석할 때 함께 추출한다."""
    s = _study(study_id, user, db)
    try:
        return service.add_outputs(db, s, [o.model_dump() for o in body.outputs], body.post, body.extract_existing, user.id)
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/studies/{study_id}/train", response_model=schemas.ModelResponse)
def train(study_id: int, body: schemas.TrainRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = _study(study_id, user, db)
    try:
        return service.train(db, s, body.outputs, body.flow_id, body.node_id, created_by=user.id)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _train_job_view(job: dict, db: Session) -> dict:
    model = db.get(models.SurrogateModel, job["model_id"]) if job.get("model_id") else None
    return {**job, "model": schemas.ModelResponse.model_validate(model) if model else None}


@router.post("/studies/{study_id}/train/start", response_model=schemas.TrainJobResponse)
def train_start(study_id: int, body: schemas.TrainStartRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """학습을 시작하고 바로 돌아온다. 같은 요청을 다시 보내도 학습 중인 작업이나 이미 만든 모델을 돌려줘 버전이 겹치지 않는다."""
    s = _study(study_id, user, db)
    try:
        return _train_job_view(service.start_train(db, s, body.outputs, body.flow_id, body.node_id, user.id, body.force), db)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/train-jobs/{job_id}", response_model=schemas.TrainJobResponse)
def train_status(job_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    job = service.train_job(job_id)
    if job is None:
        raise HTTPException(404, "학습 작업을 찾지 못했습니다. 서버가 다시 시작됐을 수 있습니다 → 학습을 다시 시작하세요")
    _study(job["study_id"], user, db)
    return _train_job_view(job, db)


@router.post("/studies/{study_id}/candidates", response_model=schemas.CandidatesResponse)
def candidates(study_id: int, body: schemas.CandidatesRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """모델로만 후보를 찾는다. 케이스를 만들지 않고 해석도 돌리지 않는다. 결과는 확인되지 않은 예측이다."""
    s = _study(study_id, user, db)
    try:
        return service.model_candidates(db, s, body.mode, body.n, body.model_id, body.objective, body.goal,
                                        [c.model_dump(exclude_none=True) for c in body.constraints] if body.constraints is not None else None)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/studies/{study_id}/suggest", response_model=list[schemas.CaseResponse] | schemas.CandidatesResponse)
def suggest(study_id: int, body: schemas.SuggestRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """다음 케이스를 제안한다. 불러온 데이터 스터디는 돌릴 해석이 없어 케이스 대신 확인되지 않은 후보를 돌려준다."""
    s = _study(study_id, user, db)
    try:
        if s.adapter == "data":
            return service.model_candidates(db, s, body.mode, body.n, body.model_id)
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
    live = service.current_model(db, study.id)
    versions = db.query(models.SurrogateModel).filter_by(study_id=study.id).order_by(models.SurrogateModel.id.desc()).all()
    return {"model": schemas.ModelResponse.model_validate(m), "variables": service.model_variables(study, m),
            "objectives": study.objectives, "study_name": study.name,
            "project_id": study.project_id, "project_name": project.name if project else "",
            "current_model_id": live.id if live else None, "data_source": service.data_source(study),
            "versions": [schemas.ModelResponse.model_validate(x) for x in versions]}


@router.post("/models/{model_id}/deploy", response_model=schemas.ModelResponse)
def deploy(model_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """이 버전을 Simulator의 현재 버전으로 둔다. 옛 버전을 고르면 되돌리기다. 공유 링크는 그대로 현재 버전을 따라간다."""
    m = service.deploy_version(db, _model(model_id, user, db))
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


def _share_model(sh: models.ShareLink, db: Session) -> models.SurrogateModel:
    """링크는 만들 때의 모델이 아니라 그 Simulator의 현재 버전을 보여 준다. 재학습·되돌리기를 해도 링크를 다시 보낼 필요가 없다."""
    m = db.get(models.SurrogateModel, sh.model_id)
    return service.current_model(db, m.study_id) or m


@router.post("/models/{model_id}/shares", response_model=schemas.ShareResponse)
def create_share(model_id: int, body: schemas.ShareCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    m = _model(model_id, user, db)
    if not m.deployed:
        raise HTTPException(400, "배포된 현재 버전의 Simulator만 공유할 수 있습니다")
    sh = models.ShareLink(token=secrets.token_urlsafe(16), model_id=m.id, created_by=user.id, note=body.note,
                          expires_at=datetime.now() + timedelta(days=body.expires_days) if body.expires_days else None)
    db.add(sh)
    db.commit()
    if service.quality_level(m) == "poor":  # 막지는 않는다. 받는 사람에게도 경고가 보인다는 것을 만든 사람이 알게 한다
        sh.warning = "이 모델은 예측 정확도가 낮습니다. 받는 사람의 화면에도 같은 경고가 보입니다 → 데이터를 보강해 다시 학습한 뒤 배포하면 이 링크가 새 버전을 따라갑니다"
    return sh


@router.get("/models/{model_id}/shares", response_model=list[schemas.ShareResponse])
def list_shares(model_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    m = _model(model_id, user, db)  # 어느 버전에서 만든 링크든 같은 Simulator의 링크다
    ids = db.query(models.SurrogateModel.id).filter_by(study_id=m.study_id)
    return db.query(models.ShareLink).filter(models.ShareLink.model_id.in_(ids)).order_by(models.ShareLink.id.desc()).all()


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
    """공유받은 사람(로그인한 사내 사용자)이 보는 Simulator 정보. 처음 열면 그 사람의 '공유받은 Simulator' 목록에 들어간다."""
    sh = _share(token, db)
    m = _share_model(sh, db)
    study = db.get(models.Study, m.study_id)
    if user.id != sh.created_by:
        seen = db.get(models.ShareAccess, (sh.id, user.id))
        if seen is None:
            db.add(models.ShareAccess(share_id=sh.id, user_id=user.id))
        else:
            seen.hidden, seen.last_opened = False, datetime.now()
        db.commit()
    owner = db.get(models.User, sh.created_by)
    src = service.data_source(study)
    return {"model": schemas.ModelResponse.model_validate(m), "variables": service.model_variables(study, m), "objectives": study.objectives,
            "study_name": study.name, "shared_by": owner.display_name if owner else "", "note": sh.note,
            "data_source": {k: src.get(k) for k in ("kind", "label", "rows", "total")}}  # 링크로 받은 사람에게 파일 이름은 보이지 않는다


@router.post("/shares/{token}/predict", response_model=schemas.PredictResponse)
def share_predict(token: str, body: schemas.PredictRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    sh = _share(token, db)
    sh.uses += 1
    db.commit()
    sur = service.load_surrogate(_share_model(sh, db))
    missing = [v for v in sur.variables if v not in body.params]
    if missing:
        raise HTTPException(400, f"입력값이 없습니다: {', '.join(missing)}")
    mean, std = sur.predict(np.array([[body.params[v] for v in sur.variables]]))
    return {"mean": {k: float(v[0]) for k, v in mean.items()}, "std": {k: float(v[0]) for k, v in std.items()}}


@router.post("/shares/{token}/predict_batch", response_model=schemas.PredictBatchResponse)
def share_predict_batch(token: str, body: schemas.PredictBatchRequest, user=Depends(get_current_user), db: Session = Depends(get_db)):
    sh = _share(token, db)
    sur = service.load_surrogate(_share_model(sh, db))
    try:
        X = np.array([[p[v] for v in sur.variables] for p in body.points])
    except KeyError as e:
        raise HTTPException(400, f"입력값이 없습니다: {e}")
    mean, std = sur.predict(X)
    return {"mean": {k: v.tolist() for k, v in mean.items()}, "std": {k: v.tolist() for k, v in std.items()}}


# ---------- 내 Simulator · 공유받은 Simulator ----------
def _sim_item(m: models.SurrogateModel, study: models.Study, **kw) -> dict:
    src = service.data_source(study)
    return {"study_id": study.id, "study_name": study.name, "model_id": m.id, "version": m.version, "outputs": m.outputs or [],
            "n_variables": len(study.variables or []), "n_train": m.n_train or 0, "updated_at": m.deployed_at or m.created_at,
            "quality": service.quality_level(m), "data_source": {k: src.get(k) for k in ("kind", "label", "rows", "total")}, **kw}


@router.get("/simulators", response_model=schemas.SimulatorList)
def my_simulators(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """프로젝트에 들어가지 않고 바로 여는 목록. 내가 만든 것, 같은 프로젝트 멤버가 만든 것, 링크로 받아 연 것.
    같은 Simulator는 한 번만 보인다(내 것 > 같은 프로젝트 > 링크)."""
    mine, shared, seen = [], [], set()
    rows = (db.query(models.SurrogateModel, models.Study, models.Project)
            .join(models.Study, models.SurrogateModel.study_id == models.Study.id)
            .join(models.Project, models.Study.project_id == models.Project.id)
            .join(models.ProjectMember, models.ProjectMember.project_id == models.Project.id)
            .filter(models.ProjectMember.user_id == user.id, models.SurrogateModel.deployed.is_(True))
            .order_by(models.SurrogateModel.deployed_at.desc()).all())
    for m, study, project in rows:
        if study.id in seen:
            continue
        seen.add(study.id)
        own = m.created_by == user.id
        owner = None if own else db.get(models.User, m.created_by) if m.created_by else None
        (mine if own else shared).append(_sim_item(m, study, url=f"/simulator/{m.id}", source="mine" if own else "project", project_id=project.id,
                                                   project_name=project.name, shared_by=owner.display_name if owner else None,
                                                   thumbnail_url=f"/api/models/{m.id}/thumbnail"))
    now = datetime.now()
    access = (db.query(models.ShareAccess, models.ShareLink).join(models.ShareLink, models.ShareAccess.share_id == models.ShareLink.id)
              .filter(models.ShareAccess.user_id == user.id, models.ShareAccess.hidden.is_(False))
              .order_by(models.ShareAccess.last_opened.desc()).all())
    for _, sh in access:
        m = _share_model(sh, db)
        study = db.get(models.Study, m.study_id)
        if study.id in seen:
            continue
        seen.add(study.id)
        owner = db.get(models.User, sh.created_by)
        status = "stopped" if not sh.enabled else "expired" if sh.expires_at and sh.expires_at < now else "active"
        # ?v=: 같은 링크가 새 버전을 보여 줄 때 화면이 그림을 다시 받게 한다. 끊긴 공유에는 모델 그림을 주지 않는다
        shared.append(_sim_item(m, study, url=f"/s/{sh.token}", source="link", status=status, share_id=sh.id, note=sh.note or "",
                                shared_by=owner.display_name if owner else None,  # 링크로 받은 사람에게 프로젝트 정보는 보이지 않는다
                                thumbnail_url=f"/api/shares/{sh.token}/thumbnail?v={m.id}" if status == "active" else None))
    return {"mine": mine, "shared": shared}


def _thumbnail_file(path: Path, tag: str, media_type: str, request: Request) -> Response:
    """브라우저 캐시를 쓰되 매번 서버에 확인한다(no-cache). 그래야 권한이 끊긴 뒤에는 옛 그림도 받지 못한다."""
    headers = {"ETag": f'"{tag}"', "Cache-Control": "private, no-cache"}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type=media_type, headers=headers)


async def _thumbnail(m: models.SurrogateModel, request: Request, db: Session, project_images: bool) -> Response:
    """권한 검사를 마친 뒤에만 부른다. 그림이 없거나 못 그리면 204: 오류가 아니라 '보일 그림이 없음'이고, 화면이 대체 표시를 쓴다.
    DB와 파일을 보는 일은 모두 이벤트 루프 밖에서 한다. 여기서 멈추면 서버 전체(HTTP·PC Agent 연결)가 함께 멈춘다."""
    def plan():
        study = db.get(models.Study, m.study_id)
        out = study.project_id, study.name, thumbnail.field_jobs(db, m, study)
        db.close()  # 그리는 차례를 기다리는 요청이 쌓여도 DB 연결을 쥐고 있지 않는다
        return out

    project_id, study_name, jobs = await run_in_threadpool(plan)
    for job in jobs:
        if await thumbnail.render(job):
            return _thumbnail_file(job.dest, job.dest.stem, "image/png", request)
    image = await run_in_threadpool(thumbnail.project_image, db, project_id, study_name) if project_images else None
    if image:
        return _thumbnail_file(image.path, image.tag, image.media_type, request)
    return Response(status_code=204)


@router.get("/models/{model_id}/thumbnail")
async def model_thumbnail(model_id: int, request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Simulator 카드 그림. 이 Simulator(/simulator/:id)를 열 수 있는 프로젝트 멤버·관리자만 받는다."""
    m = await run_in_threadpool(_model, model_id, user, db)
    return await _thumbnail(m, request, db, project_images=True)


@router.get("/shares/{token}/thumbnail")
async def share_thumbnail(token: str, request: Request, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """링크로 받은 사람의 카드 그림. 링크(/s/:token)를 열 수 있을 때만 받고, 그 사람이 볼 수 없는 프로젝트 결과 이미지는 쓰지 않는다."""
    def load():
        try:
            return _share_model(_share(token, db), db)
        except HTTPException as e:  # 기간이 끝난 링크(410)도 '그림 없음'으로 같게 답한다
            raise HTTPException(404, e.detail) from None

    return await _thumbnail(await run_in_threadpool(load), request, db, project_images=False)


@router.delete("/simulators/shared/{share_id}")
def hide_shared(share_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """공유받은 목록에서 뺀다. 내 목록에서만 빠지고, 링크를 다시 열면 돌아온다."""
    row = db.get(models.ShareAccess, (share_id, user.id))
    if row is None:
        raise HTTPException(404, "목록에 없습니다")
    row.hidden = True
    db.commit()
    return {"ok": True}


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
    try:
        values = await run_in_threadpool(service.check_field_npz, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    dest.write_bytes(data)
    c.fields = sorted(set((c.fields or []) + [name]))
    auto = service.register_field_scalar(db, study, c, name, values)
    db.commit()
    return {"ok": True, "field": name, "auto_output": auto}


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
def list_field_models(study_id: int, model_id: int | None = None, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """model_id를 주면 그 버전이 쓰는 3D 모델(필드마다 하나)만, 없으면 학습한 것 전부."""
    _study(study_id, user, db)
    if model_id is not None:
        m = db.get(models.SurrogateModel, model_id)
        return service.field_models_for(db, study_id, m if m and m.study_id == study_id else None)
    return db.query(models.FieldModel).filter_by(study_id=study_id).order_by(models.FieldModel.id.desc()).all()


def _b64(a) -> str:
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode("ascii")


_rom_cache: dict[tuple[str, int], object] = {}  # (경로, 파일 시각) → 모델. 번호는 지운 뒤 다시 쓰이므로 키로 쓰면 지운 형상이 새 모델 자리에 보인다


def _rom(fm: models.FieldModel):
    try:
        key = (fm.path, os.stat(fm.path).st_mtime_ns)
    except OSError:
        raise HTTPException(404, "3D 모델 파일이 서버에 없습니다 → 이 스터디의 3D 모델을 다시 학습하세요") from None
    if key not in _rom_cache:
        from ..study.fieldmodel import FieldROM
        rom = FieldROM.load(fm.path)
        for old in [k for k in _rom_cache if k[0] == fm.path]:  # 다시 학습한 파일의 옛 모델은 다시 쓰이지 않는다
            _rom_cache.pop(old, None)
        _rom_cache[key] = rom
    return _rom_cache[key]


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
    m = _share_model(_share(token, db), db)
    return service.field_models_for(db, m.study_id, m)


@router.get("/shares/{token}/field-models/{field_model_id}/mesh")
def shared_field_mesh(token: str, field_model_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return _field_mesh(_fm_shared(token, field_model_id, db))


@router.post("/shares/{token}/field-models/{field_model_id}/predict")
def shared_field_predict(token: str, field_model_id: int, body: schemas.PredictRequest, user=Depends(get_current_user),
                         db: Session = Depends(get_db)):
    return _field_predict(_fm_shared(token, field_model_id, db), body.params)
