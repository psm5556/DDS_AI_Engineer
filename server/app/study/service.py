"""스터디 오케스트레이션: DOE 생성, 케이스 실행(백그라운드), Surrogate 학습, BO/능동학습 제안, 아티팩트 자동 게시."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy import func
from sqlalchemy.orm import Session

from cae_adapters import get_adapter

from .. import models
from ..config import settings
from ..database import SessionLocal
from ..ws import manager
from . import bayesopt, doe
from .surrogate import Surrogate

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="case")


# ---------- 아티팩트 ----------
def publish_artifact(db: Session, project_id: int, kind: str, title: str, payload: dict,
                     flow_id: int | None = None, node_id: str | None = None) -> models.Artifact:
    """같은 프로젝트·같은 제목이면 갱신(upsert)해서 패널이 같은 카드를 업데이트하도록 한다."""
    art = db.query(models.Artifact).filter_by(project_id=project_id, title=title).first()
    if art:
        art.kind, art.payload = kind, payload
        art.flow_id = flow_id or art.flow_id
        art.node_id = node_id or art.node_id
    else:
        art = models.Artifact(project_id=project_id, kind=kind, title=title, payload=payload,
                              flow_id=flow_id, node_id=node_id)
        db.add(art)
    db.commit()
    db.refresh(art)
    manager.publish({"type": "artifact_updated", "project_id": project_id,
                     "data": {"id": art.id, "flow_id": art.flow_id, "node_id": art.node_id}})
    return art


def _var_names(study):
    return [v["name"] for v in study.variables]


def output_names(study: models.Study) -> list[str]:
    """출력(Output) 순서: 목적 → 제약 → 추가한 출력 → 그 밖에 케이스 결과에 있는 키."""
    names = [o["name"] for o in study.objectives or []] + [c["name"] for c in study.constraints or []]
    names += [o["name"] for o in study.outputs or []]
    names += sorted({k for c in study.cases for k in (c.results or {})})
    return list(dict.fromkeys(names))


# 스터디가 마지막으로 쓴 작업 흐름 단계 — 케이스가 끝난 뒤 자동 게시하는 결과(수렴 이력 등)도 그 단계에 붙인다
_ctx: dict[int, tuple[int, str | None]] = {}


def _remember(study_id: int, flow_id, node_id):
    if flow_id:
        _ctx[study_id] = (flow_id, node_id)


def _complete_cases(study):
    return [c for c in study.cases if c.status == "complete"]


def feasible(study: models.Study, results: dict) -> bool:
    """제약(constraints)을 모두 만족하는가. 결과에 없는 제약 항목은 판단하지 않는다."""
    for c in study.constraints or []:
        v = results.get(c["name"])
        if v is None:
            continue
        if (c.get("upper") is not None and v > c["upper"]) or (c.get("lower") is not None and v < c["lower"]):
            return False
    return True


def best_case(study: models.Study, feasible_only: bool = True) -> models.Case | None:
    """목적함수 최적 케이스. 제약이 있으면 만족하는 케이스 중에서 고른다(없으면 None)."""
    if not study.objectives:
        return None
    obj = study.objectives[0]
    done = [c for c in _complete_cases(study) if obj["name"] in c.results
            and (not feasible_only or feasible(study, c.results))]
    if not done:
        return None
    key = lambda c: c.results[obj["name"]]
    return min(done, key=key) if obj.get("goal", "min") == "min" else max(done, key=key)


# ---------- DOE ----------
def create_doe(db: Session, study: models.Study, method: str, n: int, seed: int | None,
               flow_id=None, node_id=None) -> list[models.Case]:
    _remember(study.id, flow_id, node_id)
    pts = doe.sample(study.variables, n, method, seed)
    cases = [models.Case(study_id=study.id, params=p, source="doe") for p in pts]
    db.add_all(cases)
    db.commit()
    names = _var_names(study)
    if len(names) >= 2:
        payload = {"x": names[0], "y": names[1],
                   "series": [{"name": f"{method.upper()} {n}점", "points": [{"x": p[names[0]], "y": p[names[1]]} for p in pts]}]}
        publish_artifact(db, study.project_id, "scatter", f"[{study.name}] DOE 샘플 분포", payload, flow_id, node_id)
    publish_cases_table(db, study, flow_id, node_id)
    manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})
    return cases


CASE_STATUS_KO = {"pending": "대기", "queued": "대기", "running": "실행 중", "complete": "완료", "failed": "실패",
                  "dry_run": "스크립트만 생성", "cancelled": "취소"}
CASE_SOURCE_KO = {"doe": "DOE", "bo": "최적화", "al": "능동학습", "manual": "직접"}


def publish_cases_table(db, study, flow_id=None, node_id=None):
    db.refresh(study)
    names = _var_names(study)
    outs = output_names(study)
    rows = [[c.id, CASE_SOURCE_KO.get(c.source, c.source), CASE_STATUS_KO.get(c.status, c.status), *[round(c.params.get(v, float("nan")), 4) for v in names],
             *[round(c.results[o], 5) if o in (c.results or {}) else None for o in outs]] for c in study.cases]
    publish_artifact(db, study.project_id, "table", f"[{study.name}] 케이스 결과",
                     {"columns": ["ID", "출처", "상태", *names, *outs], "rows": rows}, flow_id, node_id)


# ---------- 실행 ----------
# 케이스는 실행을 요청한 사용자의 PC Agent로 보낸다(CAE 툴·라이선스가 그 PC/HPC에 있으므로).
# Agent가 없고 mock 어댑터면 개발/데모용으로 서버에서 직접 돌린다.
_batches: dict[int, set[int]] = {}  # study_id → 아직 끝나지 않은 case id
_batch_lock = threading.Lock()
FINAL = {"complete", "failed", "dry_run"}


def is_running(study_id: int) -> bool:
    return bool(_batches.get(study_id))


def run_cases_async(study_id: int, case_ids: list[int] | None, dry_run: bool, user_id: int) -> int:
    from ..agent.link import AgentError, registry

    with SessionLocal() as db:
        study = db.get(models.Study, study_id)
        q = db.query(models.Case).filter(models.Case.study_id == study_id)
        q = q.filter(models.Case.id.in_(case_ids)) if case_ids else q.filter(models.Case.status == "pending")
        cases = q.all()
        if not cases:
            return 0
        conn = registry.get(user_id)
        if conn is None and not (study.adapter == "mock" and settings.allow_server_mock):
            raise AgentError("케이스를 실행할 PC Agent가 연결되어 있지 않습니다. 내 PC에서 dds-agent를 실행하세요.")
        with _batch_lock:
            _batches.setdefault(study_id, set()).update(c.id for c in cases)
        for c in cases:
            c.status, c.run_by, c.error = "queued", user_id, None
        db.commit()
        project = db.get(models.Project, study.project_id)
        jobs = [{"case_id": c.id, "study_id": study_id, "project_id": study.project_id, "project_name": project.name, "project_uid": project.uid,
                 "study_name": study.name, "source": c.source, "params": c.params, "variables": study.variables,
                 "objectives": study.objectives, "adapter": study.adapter, "adapter_config": study.adapter_config or {},
                 "dry_run": dry_run}
                for c in cases]
        project_id = study.project_id
    manager.publish({"type": "study_updated", "project_id": project_id, "data": {"id": study_id}})
    for job in jobs:
        if conn is not None:
            conn.send({"type": "case.run", **job})
        else:
            _pool.submit(_run_on_server, job)
    return len(jobs)


def on_case_status(msg: dict):
    """Agent(또는 서버 mock 실행)가 보낸 케이스 상태 반영. extract=True는 Output 추가 추출 결과다(기존 결과에 합친다)."""
    if msg.get("extract"):
        return _on_extracted(msg)
    with SessionLocal() as db:
        case = db.get(models.Case, msg["case_id"])
        if not case:
            return
        case.status = msg["status"]
        for key in ("results", "error", "run_dir", "images", "fields"):
            if key in msg:
                setattr(case, key, msg[key])
        if msg.get("archive"):
            case.archive = {**(case.archive or {}), **msg["archive"]}
        db.commit()
        study = db.get(models.Study, case.study_id)
        manager.publish({"type": "case_updated", "project_id": study.project_id,
                         "data": {"id": case.id, "study_id": study.id, "status": case.status}})
        if case.status not in FINAL:
            return
        if case.status == "complete":
            _extract_missing(study, case)
        with _batch_lock:
            left = _batches.get(study.id, set())
            left.discard(case.id)
            done = not left
            if done:
                _batches.pop(study.id, None)
        if done:
            ctx = _ctx.get(study.id, (None, None))
            publish_cases_table(db, study)
            publish_convergence(db, study, *ctx)
            manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})


# ---------- Output 추가 추출 ----------
# 해석은 다시 돌리지 않고, 케이스 폴더에 남은 해석 결과에서 새 Output만 뽑아 기존 결과에 합친다.
_extracting: dict[int, set[int]] = {}  # study_id → 추출 중인 case id


def extracting(study_id: int) -> list[int]:
    return sorted(_extracting.get(study_id, set()))


def _steps_for(study: models.Study, names: list[str]) -> list[dict]:
    """이 Output들을 추가할 때 함께 받은 추출 단계(post)."""
    steps: list[dict] = []
    for o in study.outputs or []:
        if o["name"] in names:
            steps += [s for s in o.get("post") or [] if s not in steps]
    return steps


def add_outputs(db: Session, study: models.Study, outputs: list[dict], post, extract_existing: bool, user_id: int) -> dict:
    """Output을 목록에 넣고, 추출 단계를 이후 케이스에 붙이고, 이미 해석을 마친 케이스에서도 추출한다."""
    steps = post if isinstance(post, list) else [post] if post else []
    if steps and study.adapter != "script":
        raise ValueError("추출 단계(post)는 script 어댑터 스터디에만 붙일 수 있습니다")
    names = [o["name"] for o in outputs]
    have = {o["name"]: o for o in study.outputs or []}
    for o in outputs:
        have[o["name"]] = {**have.get(o["name"], {}), "name": o["name"], "note": o.get("note") or have.get(o["name"], {}).get("note", ""),
                           "post": steps or have.get(o["name"], {}).get("post") or []}
    study.outputs = list(have.values())
    cfg = dict(study.adapter_config or {})
    if steps:  # 이후 케이스: 해석 → 기존 post → 새 추출 단계 순서로 실행된다
        old = cfg.get("post") or []
        old = old if isinstance(old, list) else [old]
        cfg["post"] = old + [s for s in steps if s not in old]
    if study.adapter == "mock" and cfg.get("outputs"):
        cfg["outputs"] = list(dict.fromkeys([*cfg["outputs"], *names]))
    study.adapter_config = cfg
    study.version += 1
    db.commit()
    can = study.adapter == "mock" or bool(_steps_for(study, names))
    queued, skipped = extract_outputs_async(study.id, names, user_id) if extract_existing and can else (0, 0)
    publish_cases_table(db, study)
    manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})
    return {"outputs": study.outputs, "queued": queued, "skipped": skipped,
            "note": "" if can else "추출 단계(post)가 없어 이미 해석한 케이스에서는 뽑지 않았습니다. 케이스 스크립트가 이 키를 results.json에 쓰면 이후 케이스부터 보입니다."}


def extract_outputs_async(study_id: int, names: list[str], user_id: int, case_ids: list[int] | None = None) -> tuple[int, int]:
    """완료된 케이스마다 그 케이스를 해석한 PC Agent로 case.extract를 보낸다. 연결이 없으면 건너뛴다(mock은 서버에서)."""
    from ..agent.link import registry

    with SessionLocal() as db:
        study = db.get(models.Study, study_id)
        project = db.get(models.Project, study.project_id)
        steps = _steps_for(study, names)
        jobs, skipped = [], 0
        for c in study.cases:
            if c.status != "complete" or (case_ids and c.id not in case_ids) or c.id in _extracting.get(study_id, set()):
                continue
            conn = registry.get(c.run_by or user_id)
            if conn is None and not (study.adapter == "mock" and settings.allow_server_mock):
                skipped += 1
                continue
            jobs.append((conn, {"type": "case.extract", "case_id": c.id, "study_id": study.id, "project_id": study.project_id,
                                "project_name": project.name, "project_uid": project.uid, "study_name": study.name,
                                "source": c.source, "params": c.params, "results": c.results or {}, "run_dir": c.run_dir,
                                "adapter": study.adapter, "adapter_config": study.adapter_config or {},
                                "steps": steps, "names": names}))
    with _batch_lock:
        _extracting.setdefault(study_id, set()).update(j["case_id"] for _, j in jobs)
    for conn, job in jobs:
        if conn is not None:
            conn.send(job)
        else:
            _pool.submit(_extract_on_server, job)
    return len(jobs), skipped


def _extract_missing(study: models.Study, case: models.Case):
    """추가한 Output이 빠진 채 끝난 케이스(Output을 추가할 때 이미 실행 중이던 케이스)는 바로 추출한다."""
    missing = [o["name"] for o in study.outputs or [] if o["name"] not in (case.results or {})]
    if missing and (study.adapter == "mock" or _steps_for(study, missing)):
        extract_outputs_async(study.id, missing, case.run_by or 0, [case.id])


def _on_extracted(msg: dict):
    with SessionLocal() as db:
        case = db.get(models.Case, msg["case_id"])
        if not case:
            return
        case.results = {**(case.results or {}), **(msg.get("results") or {})}
        if msg.get("archive"):
            case.archive = {**(case.archive or {}), **msg["archive"]}
        if msg.get("error"):
            case.error = msg["error"]
        elif (case.error or "").startswith("Output 추출 실패"):
            case.error = None
        db.commit()
        study = db.get(models.Study, case.study_id)
        with _batch_lock:
            left = _extracting.get(study.id, set())
            left.discard(case.id)
            done = not left
            if done:
                _extracting.pop(study.id, None)
        manager.publish({"type": "case_updated", "project_id": study.project_id,
                         "data": {"id": case.id, "study_id": study.id, "status": case.status}})
        if done:
            publish_cases_table(db, study)
            manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})


def _extract_on_server(job: dict):
    run_dir = settings.data_dir / "runs" / f"study_{job['study_id']}" / f"case_{job['case_id']:04d}"
    try:
        out = get_adapter(job["adapter"]).extract(job["params"], run_dir, job["adapter_config"], job["steps"], job["names"],
                                                  None, {})
    except Exception as e:
        log.exception("서버 mock 추출 실패")
        from cae_adapters.base import CaseOutcome
        out = CaseOutcome("failed", error=str(e))
    on_case_status({"case_id": job["case_id"], "status": "complete", "extract": True, "results": out.results,
                    "error": None if out.status == "complete" else f"Output 추출 실패: {out.error}",
                    "archive": _pack_on_server(job, run_dir) if run_dir.is_dir() else None})


def _run_on_server(job: dict):
    from cae_adapters.base import CaseOutcome

    run_dir = settings.data_dir / "runs" / f"study_{job['study_id']}" / f"case_{job['case_id']:04d}"
    on_case_status({"case_id": job["case_id"], "status": "running", "run_dir": str(run_dir)})
    try:
        out = get_adapter(job["adapter"]).run_case(job["params"], run_dir, job["adapter_config"], None, {}, job["dry_run"])
    except Exception as e:
        log.exception("서버 mock 실행 실패")
        out = CaseOutcome("failed", error=str(e))
    fields = []
    for f in sorted((run_dir / "fields").glob("*.npz")) if (run_dir / "fields").is_dir() else []:
        dest = field_path(job["project_id"], job["study_id"], job["case_id"], f.stem)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f.read_bytes())
        fields.append(f.stem)
    on_case_status({"case_id": job["case_id"], "status": out.status, "results": out.results, "error": out.error,
                    "fields": fields, "archive": _pack_on_server(job, run_dir) if out.status != "dry_run" else None})


def archive_path(project_id: int, study_id: int, case_id: int) -> Path:
    """서버에 둔 케이스 해석 데이터(case.h5, HDF5)."""
    return settings.files_dir / f"project_{project_id}" / "h5" / f"study_{study_id}" / f"case_{case_id}.h5"


def _pack_on_server(job: dict, run_dir: Path) -> dict | None:
    from cae_adapters.h5store import archive_info, file_sha256, pack_case

    dest = archive_path(job["project_id"], job["study_id"], job["case_id"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        s = pack_case(run_dir, out=dest, meta={"case_id": job["case_id"], "study_id": job["study_id"],
                                                "project_uid": job.get("project_uid", ""), "adapter": job["adapter"]},
                      units=(job.get("adapter_config") or {}).get("units"))
    except Exception as e:  # 묶지 못해도 해석 결과(스칼라)는 그대로 둔다
        log.exception("case.h5 묶기 실패")
        return {"ok": False, "error": str(e)}
    return {**archive_info(s), "stored": "server", "sha256": file_sha256(dest)}


def field_path(project_id: int, study_id: int, case_id: int, name: str) -> Path:
    return settings.files_dir / f"project_{project_id}" / "fields" / f"study_{study_id}" / f"case_{case_id}" / f"{name}.npz"


def _has_field(study, c, field: str) -> bool:
    if field in (c.fields or []):
        return True
    a = c.archive or {}
    return a.get("stored") == "server" and field in (a.get("field_names") or []) and         archive_path(study.project_id, study.id, c.id).is_file()


def _snapshot(study, c, field: str, load_npz):
    """케이스의 필드 스냅샷: 올린 fields/<이름>.npz가 있으면 그것, 없으면 서버의 case.h5(HDF5)에서 마지막 프레임."""
    p = field_path(study.project_id, study.id, c.id, field)
    if p.is_file():
        return load_npz(p)
    from cae_adapters.h5store import read_snapshot
    return read_snapshot(archive_path(study.project_id, study.id, c.id), field)


def train_field(db: Session, study: models.Study, field: str, flow_id=None, node_id=None) -> models.FieldModel:
    from .fieldmodel import load_snapshot, train_field_rom
    names = _var_names(study)
    done = [c for c in study.cases if c.status == "complete" and _has_field(study, c, field)]
    # 빠진 케이스를 조용히 넘기지 않는다: 큰 case.h5는 PC에만 있어 서버에서 읽을 수 없다
    excluded = [{"case_id": c.id, "reason": "case.h5가 PC에만 있습니다" if (c.archive or {}).get("stored") == "pc" else f"'{field}' 필드가 없습니다"}
                for c in study.cases if c.status == "complete" and c not in done]
    if len(done) < 4:
        raise ValueError(f"'{field}' 필드가 있는 완료 케이스가 4개 이상 필요합니다 (현재 {len(done)}개, 제외 {len(excluded)}개)")
    snaps = [_snapshot(study, c, field, load_snapshot) for c in done]
    X = np.array([[c.params[v] for v in names] for c in done])
    rom = train_field_rom(names, field, X, snaps)
    rom.info["excluded"] = excluded
    fm = models.FieldModel(study_id=study.id, field=field, n_nodes=rom.n_nodes, n_train=len(done),
                           shape_varies=rom.shape_varies, info=rom.info, path="")
    db.add(fm)
    db.flush()
    path = settings.data_dir / "models" / f"field_{fm.id}.joblib"
    rom.save(path)
    fm.path = str(path)
    live = current_model(db, study.id)
    if live is not None:  # 배포 중에 다시 학습한 3D는 현재 버전에 붙인다
        live.field_models = {**(live.field_models or {}), field: fm.id}
    db.commit()
    i = rom.info
    publish_artifact(db, study.project_id, "markdown", f"[{study.name}] {field} 형상·필드 모델",
                     {"text": f"- 학습 케이스 **{i['n_train']}개**, 노드 {rom.n_nodes:,}개, 형상 변화 {'있음(형상도 예측)' if rom.shape_varies else '없음'}\n"
                              + (f"- 제외 {len(excluded)}개: 케이스 {', '.join(str(x['case_id']) for x in excluded[:10])} ({excluded[0]['reason']} 등)\n" if excluded else "")
                              +
                              f"- PCA 모드 **{i['modes']}개** (에너지 {i['energy'] * 100:.2f}%, 절단 오차 {i['truncation_error'] * 100:.2f}%)\n"
                              f"- 모드별 GP R²: " + ", ".join(f"{k} {v}" for k, v in i["mode_r2"].items())
                              + "\n- Simulator에서 3D로 볼 수 있습니다."}, flow_id, node_id)
    manager.publish({"type": "model_updated", "project_id": study.project_id, "data": {"field_model": fm.id}})
    return fm


def publish_convergence(db, study, flow_id=None, node_id=None):
    if not study.objectives:
        return
    obj = study.objectives[0]
    sign = 1 if obj.get("goal", "min") == "min" else -1
    best, pts_best, pts_raw = None, [], []
    for i, c in enumerate([c for c in study.cases if c.status == "complete" and obj["name"] in c.results], 1):
        v = c.results[obj["name"]]
        if feasible(study, c.results):  # 최적값은 제약을 만족하는 케이스로만
            best = v if best is None or sign * v < sign * best else best
        pts_raw.append({"x": i, "y": v})
        if best is not None:
            pts_best.append({"x": i, "y": best})
    if pts_raw:
        publish_artifact(db, study.project_id, "line", f"[{study.name}] {obj['name']} 수렴 이력",
                         {"x": "평가 횟수", "series": [{"name": "평가값", "points": pts_raw},
                                                    {"name": "최적값", "points": pts_best}]}, flow_id, node_id)


# ---------- 학습 ----------
def _model_path(model_id: int) -> Path:
    return settings.data_dir / "models" / f"model_{model_id}.joblib"


def train(db: Session, study: models.Study, outputs: list[str] | None, flow_id=None, node_id=None,
          created_by: int | None = None) -> models.SurrogateModel:
    _remember(study.id, flow_id, node_id)
    outputs = outputs or [o["name"] for o in study.objectives]
    done = [c for c in _complete_cases(study) if all(o in c.results for o in outputs)]
    if not outputs:
        raise ValueError("학습할 출력(outputs) 또는 목적함수가 필요합니다")
    if len(done) < 3:
        raise ValueError(f"학습에는 완료된 케이스가 3개 이상 필요합니다 (현재 {len(done)}개)")
    names = _var_names(study)
    X = np.array([[c.params[v] for v in names] for c in done])
    Y = {o: np.array([c.results[o] for c in done]) for o in outputs}
    sur = Surrogate(variables=names, outputs=outputs)
    sur.bounds = ([v["lower"] for v in study.variables], [v["upper"] for v in study.variables])
    cv = sur.fit(X, Y)

    last = db.query(func.max(models.SurrogateModel.version)).filter_by(study_id=study.id).scalar() or 0
    m = models.SurrogateModel(study_id=study.id, name=f"{study.name} GP #{len(done)}", algo="gp", outputs=outputs,
                              n_train=len(done), path="", version=last + 1, created_by=created_by)
    db.add(m)
    db.flush()
    path = _model_path(m.id)
    sur.save(path)
    m.path = str(path)
    m.metrics = {o: {"r2": v["r2"], "rmse": v["rmse"]} for o, v in cv.items()}
    db.commit()

    for o, v in cv.items():
        publish_artifact(db, study.project_id, "scatter", f"[{study.name}] {o} 예측 정확도 (교차검증)",
                         {"x": "실제값", "y": "예측값", "diagonal": True,
                          "series": [{"name": f"R²={v['r2']:.3f}", "points": [{"x": a, "y": p} for a, p in zip(v["actual"], v["cv_pred"])]}]},
                         flow_id, node_id)
    publish_artifact(db, study.project_id, "table", f"[{study.name}] Surrogate 성능",
                     {"columns": ["모델", "출력", "R²", "RMSE", "학습 수"],
                      "rows": [[m.name, o, round(v["r2"], 4), round(v["rmse"], 5), m.n_train] for o, v in m.metrics.items()]},
                     flow_id, node_id)
    manager.publish({"type": "model_updated", "project_id": study.project_id, "data": {"id": m.id}})
    return m


def load_surrogate(m: models.SurrogateModel) -> Surrogate:
    return Surrogate.load(m.path)


# ---------- Simulator 버전 ----------
# Simulator는 스터디 하나이고, 모델은 그 버전이다. 현재 버전은 스터디마다 하나(deployed)이며 공유 링크는 현재 버전을 따라간다.
def current_model(db: Session, study_id: int) -> models.SurrogateModel | None:
    return (db.query(models.SurrogateModel).filter_by(study_id=study_id, deployed=True)
            .order_by(models.SurrogateModel.id.desc()).first())


def _latest_field_models(db: Session, study_id: int) -> dict[str, models.FieldModel]:
    latest: dict[str, models.FieldModel] = {}
    for fm in db.query(models.FieldModel).filter_by(study_id=study_id).order_by(models.FieldModel.id.desc()):
        latest.setdefault(fm.field, fm)
    return latest


def field_models_for(db: Session, study_id: int, model: models.SurrogateModel | None) -> list[models.FieldModel]:
    """이 버전이 쓰는 3D 모델(필드마다 하나). 버전에 묶인 것이 없는 필드는 최신을 쓴다."""
    out = _latest_field_models(db, study_id)
    for field, fid in ((model.field_models if model else None) or {}).items():
        fm = db.get(models.FieldModel, fid)
        if fm is not None and fm.study_id == study_id:
            out[field] = fm
    return sorted(out.values(), key=lambda f: f.id, reverse=True)


def deploy_version(db: Session, m: models.SurrogateModel) -> models.SurrogateModel:
    """이 버전을 현재 버전으로 둔다. 처음 배포할 때 그때의 3D 모델을 묶어, 되돌려도 예측값과 3D가 어긋나지 않게 한다."""
    db.query(models.SurrogateModel).filter(models.SurrogateModel.study_id == m.study_id, models.SurrogateModel.id != m.id).update({"deployed": False})
    m.deployed = True
    m.deployed_at = datetime.now()
    m.field_models = {**{f: fm.id for f, fm in _latest_field_models(db, m.study_id).items()}, **(m.field_models or {})}
    db.commit()
    return m


# ---------- 제안 (BO / 능동학습) ----------
def suggest(db: Session, study: models.Study, mode: str, n: int, model_id: int | None,
            flow_id=None, node_id=None) -> list[models.Case]:
    _remember(study.id, flow_id, node_id)
    q = db.query(models.SurrogateModel).filter_by(study_id=study.id)
    m = q.filter_by(id=model_id).first() if model_id else q.order_by(models.SurrogateModel.id.desc()).first()
    if not m:
        raise ValueError("먼저 Surrogate 모델을 학습하세요 (train)")
    sur = load_surrogate(m)
    names = _var_names(study)
    lo = np.array([v["lower"] for v in study.variables])
    hi = np.array([v["upper"] for v in study.variables])
    obj = study.objectives[0] if study.objectives else None
    if mode == "bo":
        if not obj or obj["name"] not in sur.outputs:
            raise ValueError("BO에는 모델이 학습한 목적함수(objectives[0])가 필요합니다")
        best = best_case(study) or best_case(study, feasible_only=False)
        if best is None:
            raise ValueError("완료된 케이스가 없습니다")
        pts = bayesopt.suggest(sur, lo, hi, "bo", n, obj["name"], obj.get("goal", "min"),
                               best.results[obj["name"]], seed=len(study.cases),
                               constraints=[c for c in study.constraints or [] if c["name"] in sur.outputs])
    else:
        pts = bayesopt.suggest(sur, lo, hi, "al", n, seed=len(study.cases))

    cases = [models.Case(study_id=study.id, params=dict(zip(names, map(float, p))), source=mode) for p in pts]
    db.add_all(cases)
    db.commit()
    publish_cases_table(db, study, flow_id, node_id)
    return cases
