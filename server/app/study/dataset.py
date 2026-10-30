"""학습 데이터셋: 케이스마다 학습 샘플(train.h5)을 모아 분할·통계를 담은 manifest.json을 만든다. docs/cae-data-hdf5.md 8절.

샘플을 어디서 만드나: 서버에 case.h5가 있으면 서버에서, PC에만 있으면 그 PC Agent로 case.train_sample을 보낸다.
빠진 케이스는 조용히 넘기지 않고 이유를 남긴다. 만든 데이터셋은 고치지 않는다(샘플 파일 이름에 조건 해시가 들어 있어 덮어쓰지 않는다).
"""
from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from sqlalchemy.orm import Session

from cae_adapters.train_h5 import build_sample, read_info, spec_hash
from cae_adapters.h5store import file_sha256

from .. import models
from ..config import settings
from ..database import SessionLocal
from ..ws import manager
from . import service

log = logging.getLogger(__name__)
MIN_SAMPLES = 4
TEST_FRACTION = VAL_FRACTION = 0.15
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="dataset")
_lock = threading.Lock()
_building: set[tuple[int, int]] = set()  # 서버에서 만드는 중인 (데이터셋, 케이스)
_finalizing: set[int] = set()


def training_dir() -> Path:
    return settings.data_dir / "training"


def sample_rel(project_id: int, study_id: int, case_id: int, h: str) -> str:
    return f"samples/project_{project_id}/study_{study_id}/case_{case_id}__{h}.train.h5"


def record_sample(db: Session, case: models.Case, study: models.Study, h: str, path: Path) -> dict:
    """샘플 파일을 케이스에 기록한다. 형식이 다르면 ValueError."""
    info = read_info(path)
    info.update(path=sample_rel(study.project_id, study.id, case.id, h), sha256=file_sha256(path), size=path.stat().st_size)
    case.train_samples = {**(case.train_samples or {}), h: info}
    db.commit()
    return info


def create_dataset(db: Session, study: models.Study, name: str, targets, frames, step, flow_id, node_id, user_id: int) -> models.TrainingDataset:
    from ..agent.link import registry

    done = [c for c in study.cases if c.status == "complete"]
    if len(done) < MIN_SAMPLES:
        raise ValueError(f"완료된 케이스가 {MIN_SAMPLES}개 이상 필요합니다 (현재 {len(done)}개)")
    spec = {"targets": list(targets) if targets else None, "frames": frames, "step": step}
    h = spec_hash(spec)
    project = db.get(models.Project, study.project_id)
    excluded = [{"case_id": c.id, "reason": f"해석이 완료되지 않았습니다({c.status})"} for c in study.cases if c.status != "complete"]
    server_jobs, pc_jobs = [], []
    for c in done:
        rel = (c.train_samples or {}).get(h, {}).get("path")
        if rel and (training_dir() / rel).is_file():
            continue
        a = c.archive or {}
        conn = registry.get(c.run_by or user_id)
        if service.archive_path(study.project_id, study.id, c.id).is_file():
            server_jobs.append(c.id)
        elif a.get("stored") == "pc" and a.get("ok") is not False:
            if conn is None:
                excluded.append({"case_id": c.id, "reason": "case.h5가 PC에만 있고 그 PC가 연결되어 있지 않습니다 → PC Agent를 켜고 다시 만듭니다"})
            else:
                pc_jobs.append((conn, c))
        else:
            excluded.append({"case_id": c.id, "reason": "이 케이스의 case.h5가 없습니다 → 케이스를 다시 해석하거나 PC의 케이스 폴더를 확인합니다"})
    ds = models.TrainingDataset(study_id=study.id, name=name or f"{study.name} 학습 데이터", spec=spec, status="building",
                                pending=server_jobs + [c.id for _, c in pc_jobs], excluded=excluded, flow_id=flow_id, node_id=node_id,
                                created_by=user_id)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    for cid in server_jobs:
        _building.add((ds.id, cid))
        _pool.submit(_build_on_server, ds.id, study.project_id, study.id, cid, spec, h)
    for conn, c in pc_jobs:
        conn.send({"type": "case.train_sample", "dataset_id": ds.id, "case_id": c.id, "study_id": study.id, "project_id": study.project_id,
                   "project_name": project.name, "project_uid": project.uid, "study_name": study.name, "run_dir": c.run_dir,
                   "spec": spec, "spec_hash": h})
    if not ds.pending:
        _start_finalize(ds.id)
    manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})
    return ds


def _build_on_server(ds_id: int, project_id: int, study_id: int, case_id: int, spec: dict, h: str):
    error = None
    dest = training_dir() / sample_rel(project_id, study_id, case_id, h)
    tmp = dest.with_suffix(".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        build_sample(service.archive_path(project_id, study_id, case_id), tmp, spec)
        tmp.replace(dest)
        with SessionLocal() as db:
            record_sample(db, db.get(models.Case, case_id), db.get(models.Study, study_id), h, dest)
    except ValueError as e:
        error = str(e)
    except Exception as e:  # noqa: BLE001 — 한 케이스의 실패가 데이터셋 전체를 멈추지 않게 이유를 남긴다
        log.exception("학습 샘플 실패 C%s", case_id)
        error = f"학습 샘플을 만들지 못했습니다: {e}"
    finally:
        tmp.unlink(missing_ok=True)
    try:
        on_sample_status({"dataset_id": ds_id, "case_id": case_id, "ok": error is None, "error": error})
    finally:  # 결과를 기록한 뒤에 뺀다. 먼저 빼면 그 사이에 reconcile이 끊긴 케이스로 잘못 본다
        _building.discard((ds_id, case_id))


def on_sample_status(msg: dict):
    """샘플 하나가 끝났다(PC Agent의 case.train_sample 또는 서버). 기다리는 케이스가 없으면 마무리한다."""
    with _lock, SessionLocal() as db:
        ds = db.get(models.TrainingDataset, msg.get("dataset_id"))
        if ds is None or ds.status != "building" or msg.get("case_id") not in (ds.pending or []):
            return
        ds.pending = [c for c in ds.pending if c != msg["case_id"]]
        if not msg.get("ok"):
            ds.excluded = [*(ds.excluded or []), {"case_id": msg["case_id"], "reason": msg.get("error") or "학습 샘플을 만들지 못했습니다"}]
        db.commit()
        finished = not ds.pending
    if finished:
        _start_finalize(msg["dataset_id"])


def reconcile(db: Session, ds: models.TrainingDataset):
    """서버 재시작·PC 연결 끊김으로 답이 오지 않을 케이스를 정리한다. 영원히 '만드는 중'으로 남지 않게 한다."""
    if ds.status != "building":
        return
    from ..agent.link import registry

    with _lock:
        lost = []
        for cid in ds.pending or []:
            if (ds.id, cid) in _building:
                continue
            c = db.get(models.Case, cid)
            if registry.get((c.run_by if c else None) or ds.created_by) is None:
                lost.append(cid)
        if lost:
            ds.pending = [c for c in ds.pending if c not in lost]
            ds.excluded = [*(ds.excluded or []), *[{"case_id": c, "reason": "샘플을 만들던 PC나 서버의 연결이 끊겼습니다 → 다시 만듭니다"} for c in lost]]
            db.commit()
        finished = not ds.pending and ds.id not in _finalizing
    if finished:
        _start_finalize(ds.id)


def _start_finalize(ds_id: int):
    with _lock:
        if ds_id in _finalizing:
            return
        _finalizing.add(ds_id)
    _pool.submit(_finalize, ds_id)


def _split(samples: list[dict], variables: list[dict], seed: int = 0) -> None:
    """케이스 단위로 나눈다. 설계변수가 같은 케이스는 같은 쪽에 둔다(정답이 새지 않게). 설계 공간 가장자리도 시험에 남긴다."""
    groups: dict[tuple, list[dict]] = {}
    for s in samples:
        groups.setdefault(tuple(round(float(s["params"].get(v["name"], 0.0)), 9) for v in variables), []).append(s)
    keys = list(groups)
    n = len(keys)
    n_test, n_val = max(1, round(n * TEST_FRACTION)), max(1, round(n * VAL_FRACTION))

    def edge(key: tuple) -> float:
        return max((abs((x - v["lower"]) / ((v["upper"] - v["lower"]) or 1) - 0.5) * 2 for x, v in zip(key, variables)), default=0.0)

    rng = np.random.default_rng(seed)
    order = [keys[i] for i in rng.permutation(n)]
    n_edge = int(np.ceil(n_test / 3))
    test = sorted(order, key=edge, reverse=True)[:n_edge]
    rest = [k for k in order if k not in test]
    test += rest[:n_test - n_edge]
    val = rest[n_test - n_edge:n_test - n_edge + n_val]
    side = {**{k: "test" for k in test}, **{k: "val" for k in val}}
    for k, members in groups.items():
        for s in members:
            s["split"] = side.get(k, "train")


def _statistics(samples: list[dict], targets: list[str]) -> dict:
    """학습 분할의 성분별 평균·표준편차·최소·최대. 프레임씩 읽는다. NaN(값 없는 노드)은 뺀다."""
    import h5py

    acc: dict[str, dict] = {}
    for s in samples:
        if s["split"] != "train":
            continue
        with h5py.File(training_dir() / s["path"], "r") as f:
            for name in targets:
                ds = f[f"targets/{name}"]
                a = acc.setdefault(name, {"n": np.zeros(ds.shape[2]), "sum": np.zeros(ds.shape[2]), "sq": np.zeros(ds.shape[2]),
                                          "min": np.full(ds.shape[2], np.inf), "max": np.full(ds.shape[2], -np.inf)})
                for i in range(ds.shape[0]):
                    frame = np.asarray(ds[i], dtype=np.float64)
                    good = ~np.isnan(frame)
                    a["n"] += good.sum(axis=0)
                    a["sum"] += np.nansum(frame, axis=0)
                    a["sq"] += np.nansum(frame ** 2, axis=0)
                    a["min"] = np.fmin(a["min"], np.nanmin(frame, axis=0))
                    a["max"] = np.fmax(a["max"], np.nanmax(frame, axis=0))
    out = {}
    for name, a in acc.items():
        n = np.maximum(a["n"], 1)
        mean = a["sum"] / n
        out[name] = {"count": a["n"].astype(int).tolist(), "mean": mean.tolist(), "std": np.sqrt(np.maximum(a["sq"] / n - mean ** 2, 0)).tolist(),
                     "min": a["min"].tolist(), "max": a["max"].tolist()}
    return out


def _finalize(ds_id: int):
    try:
        with SessionLocal() as db:
            _finalize_in(db, ds_id)
    except Exception as e:  # noqa: BLE001 — 실패 이유를 화면에 보여 준다
        log.exception("데이터셋 마무리 실패 #%s", ds_id)
        with SessionLocal() as db:
            ds = db.get(models.TrainingDataset, ds_id)
            if ds:
                ds.status, ds.error = "failed", f"데이터셋을 마무리하지 못했습니다: {e}"
                db.commit()
    finally:
        with _lock:
            _finalizing.discard(ds_id)


def _finalize_in(db: Session, ds_id: int):
    ds = db.get(models.TrainingDataset, ds_id)
    study = db.get(models.Study, ds.study_id)
    h = spec_hash(ds.spec)
    excluded = list(ds.excluded or [])
    gone = {x["case_id"] for x in excluded}
    samples = []
    for c in study.cases:
        if c.status != "complete" or c.id in gone:
            continue
        info = (c.train_samples or {}).get(h)
        if not info or not (training_dir() / info["path"]).is_file():
            excluded.append({"case_id": c.id, "reason": "학습 샘플이 서버에 없습니다"})
            continue
        samples.append({"case_id": c.id, "params": c.params, "results": c.results or {}, **info})

    # 모든 샘플에 있는 출력만 쓴다. 프레임 수·성분이 다른 샘플은 뺀다(한 묶음으로 학습할 수 없다)
    targets = sorted(set.intersection(*[set(s["targets"]) for s in samples])) if samples else []
    if samples and targets:
        shapes = Counter(tuple((t, s["targets"][t][0], s["targets"][t][2]) for t in targets) for s in samples)
        common = shapes.most_common(1)[0][0]
        kept = []
        for s in samples:
            if tuple((t, s["targets"][t][0], s["targets"][t][2]) for t in targets) == common:
                kept.append(s)
            else:
                excluded.append({"case_id": s["case_id"], "reason": "프레임 수나 성분 수가 다른 케이스와 다릅니다"})
        samples = kept
    ds.excluded = excluded
    ds.n_samples = len(samples)
    if len(samples) < MIN_SAMPLES or not targets:
        ds.status = "failed"
        ds.error = (f"학습 샘플이 {len(samples)}개뿐입니다. {MIN_SAMPLES}개 이상 필요합니다. 빠진 케이스의 이유를 확인합니다" if targets or not samples
                    else "모든 케이스에 공통으로 있는 출력이 없습니다")
        db.commit()
        _announce(study, ds)
        return

    all_inputs = sorted({i for s in samples for i in s["inputs"]} | {"fixed_mask", "prescribed_u", "nodal_load"})
    inputs = [i for i in all_inputs if all(i in s["inputs"] for s in samples)]
    item_of = {"fixed_mask": "boundary", "prescribed_u": "boundary", "nodal_load": "loads"}
    dropped = [{"name": i, "reason": f"{sum(i not in s['inputs'] for s in samples)}개 케이스에서 추출하지 못했습니다({item_of.get(i, i)})"}
               for i in all_inputs if i not in inputs]
    _split(samples, study.variables or [])
    stats = _statistics(samples, targets)
    splits = dict(Counter(s["split"] for s in samples))
    manifest = {"format": "dds-dataset", "schema": 1, "dataset_id": ds.id, "study_id": study.id, "project_id": study.project_id,
                "name": ds.name, "created": datetime.now().isoformat(timespec="seconds"), "spec": ds.spec, "spec_hash": h,
                "variables": [v["name"] for v in study.variables or []], "targets": targets, "inputs": inputs, "inputs_dropped": dropped,
                "units_known": all(s["units_known"] for s in samples), "same_mesh": len({s["same_mesh_key"] for s in samples}) == 1,
                "stats": stats, "splits": splits, "excluded": excluded,
                "samples": [{k: s.get(k) for k in ("case_id", "path", "sha256", "size", "split", "params", "results", "nodes", "frames", "step",
                                                   "geometry_source", "extraction")} for s in samples]}
    path = training_dir() / "datasets" / str(ds.id) / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    ds.manifest_path, ds.status, ds.splits, ds.targets, ds.inputs = str(path), "ready", splits, targets, inputs
    ds.inputs_dropped, ds.units_known, ds.same_mesh = dropped, manifest["units_known"], manifest["same_mesh"]
    ds.size = int(sum(s["size"] for s in samples))
    db.commit()
    _announce(study, ds)


def _announce(study: models.Study, ds: models.TrainingDataset):
    with SessionLocal() as db:
        lines = [f"- 상태: {'준비됨' if ds.status == 'ready' else '실패'} · 샘플 **{ds.n_samples}개**"
                 + (f" (학습 {ds.splits.get('train', 0)} · 검증 {ds.splits.get('val', 0)} · 시험 {ds.splits.get('test', 0)})" if ds.splits else "")]
        if ds.targets:
            lines.append(f"- 출력: {', '.join(ds.targets)} · 입력: {', '.join(ds.inputs) or '형상과 설계변수만'}")
        if ds.status == "ready" and not ds.units_known:
            lines.append("- 단위를 모릅니다(adapter_config.units 없음): 다른 스터디와 섞으면 안 됩니다")
        if ds.excluded:
            reasons = Counter(x["reason"] for x in ds.excluded).most_common(3)
            lines.append(f"- 제외 {len(ds.excluded)}개: " + " / ".join(f"{r} ({n}개)" for r, n in reasons))
        if ds.error:
            lines.append(f"- {ds.error}")
        service.publish_artifact(db, study.project_id, "markdown", f"[{study.name}] 학습 데이터 — {ds.name}", {"text": "\n".join(lines)},
                                 ds.flow_id, ds.node_id)
    manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})
