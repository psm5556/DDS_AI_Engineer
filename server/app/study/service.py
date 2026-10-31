"""스터디 오케스트레이션: DOE 생성, 케이스 실행(백그라운드), Surrogate 학습, BO/능동학습 제안, 아티팩트 자동 게시."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import re
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy.stats import qmc
from sqlalchemy import func
from sqlalchemy.orm import Session

from cae_adapters import REGISTRY, get_adapter
from cae_adapters.base import check_script_path
from cae_adapters.data import NOT_RUNNABLE
from cae_adapters.recipe_bundle import config_scripts

from .. import models
from ..config import settings
from ..database import SessionLocal
from ..ws import manager
from . import bayesopt, doe
from .surrogate import Surrogate, space_filling_subset

log = logging.getLogger(__name__)
_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="case")
_train_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="train")
CASES_TABLE_ROWS = 1000  # 결과 카드에 담는 케이스 행 수. 큰 표를 카드에 다 담으면 화면이 멈춘다


class NoRunMethod(ValueError):
    """해석 방법이 없는 스터디를 돌리려 할 때. 문구에 할 일이 들어 있다."""


class NeedsApproval(ValueError):
    """승인이 없거나 반려된 흐름으로 돌리려 할 때. 문구에 이유와 할 일이 들어 있다."""


def check_flow(db: Session, project_id: int, flow_id: int | None):
    """지워진 흐름 번호를 받으면 결과 카드를 쓸 때 외래 키 오류(500)로 터진다. 데이터를 건드리기 전에 이유를 알린다."""
    if not flow_id:
        return
    flow = db.get(models.Flow, flow_id)
    if flow is None or flow.project_id != project_id:
        raise ValueError(f"작업 흐름 {flow_id}번이 이 프로젝트에 없습니다 → 흐름 번호를 비우거나 지금 흐름의 번호를 주세요")


def check_config_scripts(config: dict | None):
    """스크립트 경로를 받는 곳(스터디 만들기·복제·해석 방법 연결·Output 추가)에서 부른다. PC Agent도 실행 직전에 다시 본다."""
    for script in config_scripts(config or {}):
        check_script_path(script)


def check_constraints(constraints: list[dict] | None):
    """제약값이 NaN이면 모든 후보가 탈락한다. 저장하지 않고 이유를 알린다."""
    for c in constraints or []:
        for key in ("upper", "lower"):
            v = c.get(key)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)):
                raise ValueError(f"제약 '{c.get('name')}'의 값이 숫자가 아닙니다 → 값을 고치거나 그 제약을 빼 주세요")


def _clean_results(results: dict | None) -> dict:
    """숫자만 남긴다. NaN·Inf나 글자가 들어가면 표 게시·학습·응답 직렬화가 차례로 깨진다."""
    return {k: float(v) for k, v in (results or {}).items()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}


def _cell(v, digits: int):
    return round(float(v), digits) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


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


def _remember(study: models.Study, flow_id, node_id):
    if flow_id:
        _ctx[study.id] = (flow_id, node_id)
        study.flow_id = flow_id  # 서버가 다시 시작해도 "지난번 흐름"에서 스터디를 찾을 수 있게 남긴다


def _complete_cases(study):
    return [c for c in study.cases if c.status == "complete"]


def constraint_state(constraints: list[dict] | None, results: dict | None) -> bool | None:
    """제약 판정의 단 하나의 정의. True: 모두 만족 · False: 하나라도 어김 · None: 어긴 것은 없지만 값이 없어 모름.
    값이 없는 케이스를 만족으로 치면 제약 값을 뽑지 못한 설계가 '최적'이 된다. 그래서 고를 때는 True만 쓴다(`is True`).
    제약이 없거나 범위가 없는 제약은 따질 것이 없어 True다."""
    known = True
    for c in constraints or []:
        upper, lower = c.get("upper"), c.get("lower")
        if upper is None and lower is None:
            continue
        v = (results or {}).get(c["name"])
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            known = False
            continue
        if (upper is not None and v > upper) or (lower is not None and v < lower):
            return False
    return True if known else None


def feasible(study: models.Study, results: dict | None) -> bool | None:
    """이 스터디의 제약으로 본 결과. 뜻은 constraint_state."""
    return constraint_state(study.constraints, results)


def best_case(study: models.Study, feasible_only: bool = True) -> models.Case | None:
    """목적함수 최적 케이스. 제약이 있으면 만족하는 케이스 중에서 고른다(없으면 None)."""
    if not study.objectives:
        return None
    obj = study.objectives[0]
    done = [c for c in _complete_cases(study) if obj["name"] in c.results
            and (not feasible_only or feasible(study, c.results) is True)]
    if not done:
        return None
    key = lambda c: c.results[obj["name"]]
    return min(done, key=key) if obj.get("goal", "min") == "min" else max(done, key=key)


# ---------- DOE ----------
def create_doe(db: Session, study: models.Study, method: str, n: int, seed: int | None,
               flow_id=None, node_id=None) -> list[models.Case]:
    check_flow(db, study.project_id, flow_id)
    _remember(study, flow_id, node_id)
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
CASE_SOURCE_KO = {"doe": "DOE", "bo": "최적화", "al": "능동학습", "manual": "직접", "import": "불러옴"}


def publish_cases_table(db, study, flow_id=None, node_id=None):
    db.refresh(study)
    names = _var_names(study)
    outs = output_names(study)
    cases = study.cases[:CASES_TABLE_ROWS]
    rows = [[c.id, CASE_SOURCE_KO.get(c.source, c.source), CASE_STATUS_KO.get(c.status, c.status), *[_cell((c.params or {}).get(v), 4) for v in names],
             *[_cell((c.results or {}).get(o), 5) for o in outs]] for c in cases]
    payload = {"columns": ["ID", "출처", "상태", *names, *outs], "rows": rows}
    if len(study.cases) > len(cases):
        payload["truncated"] = {"shown": len(cases), "total": len(study.cases)}
    publish_artifact(db, study.project_id, "table", f"[{study.name}] 케이스 결과", payload, flow_id, node_id)


def fixed_values(study: models.Study) -> dict:
    return (study.import_info or {}).get("fixed") or {}


def data_source(study: models.Study) -> dict:
    """학습 데이터가 어디서 왔는가. Simulator를 받은 사람이 해석 결과인지 표인지 알 수 있게 한다."""
    done = sum(c.status == "complete" for c in study.cases)
    imported = [c for c in study.cases if c.source == "import" or c.origin]
    if not imported:
        return {"kind": "solver", "label": "해석 결과", "rows": done, "total": done}
    info = study.import_info or {}
    folders = info.get("kind") == "folders" or any(c.run_dir for c in imported)
    label = "불러온 해석 결과" if folders else "불러온 표"
    return {"kind": "folders" if folders else "table", "label": label, "rows": len(imported), "total": done, "source": info.get("source") or ""}


def doe_csv(study: models.Study, status: str | None = None) -> str:
    """DOE 계획표. 밖(다른 팀 HPC 등)에서 해석한 뒤 출력 칸을 채워 그대로 다시 불러올 수 있는 모양이다.
    값을 전체 자릿수로 써서, 다시 불러올 때 대기 케이스와 같은 점으로 알아본다."""
    cases = list(study.cases)
    if status:
        cases = [c for c in cases if c.status == status]
    else:
        waiting = [c for c in cases if c.status == "pending"]
        cases = waiting or cases
    if not cases:
        raise ValueError("내보낼 DOE 점이 없습니다 → DOE 샘플을 먼저 만드세요")
    names = _var_names(study)
    outs = output_names(study)
    units = {v["name"]: v.get("unit") for v in study.variables}
    units.update({o["name"]: o.get("unit") for o in study.outputs or []})
    head = [f"{n} [{units[n]}]" if units.get(n) else n for n in [*names, *outs]]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(["case_id", "status", *head])
    for c in cases:
        w.writerow([c.id, c.status, *[repr(float(c.params[n])) if n in (c.params or {}) else "" for n in names],
                    *[repr(float(c.results[o])) if o in (c.results or {}) else "" for o in outs]])
    return "﻿" + buf.getvalue()  # 엑셀에서 한글이 깨지지 않게


def set_adapter(db: Session, study: models.Study, adapter: str, config: dict) -> models.Study:
    """해석 방법을 나중에 연결한다. 대기 중인 DOE 케이스는 그대로 두어 이어서 돌릴 수 있다."""
    if adapter not in REGISTRY:
        raise ValueError(f"알 수 없는 해석 방법입니다: {adapter} → 쓸 수 있는 것: {', '.join(REGISTRY)}")
    if adapter == "script" and not config.get("tool"):
        raise ValueError("해석 방법에 tool이 없습니다 → 예: {\"tool\": \"abaqus_cae\", \"script\": \"scripts/plate.py\"}")
    check_config_scripts(config)
    if is_running(study.id):
        gone = stalled_view(db, study)
        if gone["stalled"]:
            raise RuntimeError(f"{', '.join(gone['stalled_by'])}님의 PC와 연결이 끊겨 '실행 중'으로 남은 케이스가 {gone['stalled']}건 있습니다 → 그 PC가 다시 연결돼 끝나거나, 실행한 사람이 '중지'한 뒤에 바꿀 수 있습니다")
        raise RuntimeError("케이스가 도는 중에는 해석 방법을 바꿀 수 없습니다 → 끝난 뒤에 다시 하세요")
    study.adapter, study.adapter_config = adapter, config
    study.version += 1
    db.commit()
    manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})
    return study


def _unique_study_name(db: Session, project_id: int, name: str) -> str:
    # 결과 카드는 스터디 이름으로 묶이므로(publish_artifact) 이름이 겹치면 서로의 카드를 덮어쓴다
    used = {n for (n,) in db.query(models.Study.name).filter_by(project_id=project_id)}
    if name not in used:
        return name
    i = 2
    while f"{name} ({i})" in used:
        i += 1
    return f"{name} ({i})"


def clone_study(db: Session, study: models.Study, overrides: dict, project_id: int | None = None, owner_id: int | None = None) -> models.Study:
    """지난번과 같은 설정으로 새 스터디를 만든다. 케이스·모델은 옮기지 않으므로 옛 결과와 Simulator는 그대로다."""
    from .recipe import copy_to

    adapter = overrides.get("adapter") or study.adapter
    if adapter not in REGISTRY:
        raise ValueError(f"알 수 없는 해석 방법입니다: {adapter} → 쓸 수 있는 것: {', '.join(REGISTRY)}")
    check_constraints(overrides.get("constraints"))
    variables = overrides.get("variables") if overrides.get("variables") is not None else study.variables
    for v in variables:
        if v["lower"] >= v["upper"]:
            raise ValueError(f"변수 '{v['name']}'의 하한이 상한보다 작아야 합니다 → 범위를 고쳐 주세요")
    check_config_scripts(overrides.get("adapter_config"))
    pid = project_id or study.project_id
    base = (overrides.get("name") or "").strip() or f"{study.name} 복사"
    info: dict = {"cloned_from": {"study_id": study.id, "name": study.name}}
    if fixed_values(study):
        info["fixed"] = fixed_values(study)
    new = models.Study(project_id=pid, name=_unique_study_name(db, pid, base[:200]), variables=[dict(v) for v in variables],
                       objectives=overrides.get("objectives") if overrides.get("objectives") is not None else study.objectives,
                       constraints=overrides.get("constraints") if overrides.get("constraints") is not None else study.constraints,
                       outputs=overrides.get("outputs") if overrides.get("outputs") is not None else [o for o in study.outputs or [] if not o.get("auto")],
                       adapter=adapter, adapter_config=overrides.get("adapter_config") if overrides.get("adapter_config") is not None else study.adapter_config,
                       import_info=info, created_by=owner_id)
    db.add(new)
    db.flush()
    copy_to(study, new)
    db.commit()
    manager.publish({"type": "study_updated", "project_id": pid, "data": {"id": new.id}})
    return new


def flow_summaries(db: Session, project_id: int) -> list[dict]:
    """흐름 목록과 거기에 연결된 스터디. "지난번처럼 다시"에서 AI가 흐름 id 없이 찾을 수 있게 한다."""
    studies = db.query(models.Study).filter_by(project_id=project_id).all()
    by_name = {s.name: s for s in studies}
    links: dict[int, dict[int, models.Study]] = {}
    for s in studies:
        if s.flow_id:
            links.setdefault(s.flow_id, {})[s.id] = s
    # 흐름을 기록하기 전에 만든 스터디는 결과 카드 제목 "[스터디 이름] …"으로 찾는다
    for fid, title in db.query(models.Artifact.flow_id, models.Artifact.title).filter(models.Artifact.project_id == project_id,
                                                                                       models.Artifact.flow_id.isnot(None)):
        m = re.match(r"^\[(.+?)\]", title or "")
        if m and m.group(1) in by_name:
            s = by_name[m.group(1)]
            links.setdefault(fid, {})[s.id] = s
    counts: dict[int, dict[str, int]] = {}
    ids = [s.id for s in studies]
    for sid, status, n in db.query(models.Case.study_id, models.Case.status, func.count()).filter(models.Case.study_id.in_(ids)).group_by(models.Case.study_id, models.Case.status):
        counts.setdefault(sid, {})[status] = n
    latest: dict[int, int] = {}
    deployed: dict[int, int] = {}
    for mid, sid, dep in db.query(models.SurrogateModel.id, models.SurrogateModel.study_id, models.SurrogateModel.deployed).filter(models.SurrogateModel.study_id.in_(ids)).order_by(models.SurrogateModel.id):
        latest[sid] = mid
        if dep:
            deployed[sid] = mid
    out = []
    for f in db.query(models.Flow).filter_by(project_id=project_id).order_by(models.Flow.id.desc()):
        out.append({"id": f.id, "title": f.title, "goal": f.goal or "", "status": f.status, "created_at": f.created_at,
                    "nodes": [{k: n.get(k) for k in ("id", "label", "type", "status")} for n in f.nodes or []],
                    "studies": [{"id": s.id, "name": s.name, "adapter": s.adapter, "counts": counts.get(s.id, {}), "latest_model_id": latest.get(s.id),
                                 "deployed_model_id": deployed.get(s.id), "has_recipe": bool(s.recipe)} for s in links.get(f.id, {}).values()]})
    return out


# ---------- 실행 ----------
# 케이스는 실행을 요청한 사용자의 PC Agent로 보낸다(CAE 툴·라이선스가 그 PC/HPC에 있으므로).
# Agent가 없고 mock 어댑터면 개발/데모용으로 서버에서 직접 돌린다.
_batches: dict[int, set[int]] = {}  # study_id → 아직 끝나지 않은 case id
_batch_lock = threading.Lock()
FINAL = {"complete", "failed", "dry_run", "cancelled"}


def is_running(study_id: int) -> bool:
    return bool(_batches.get(study_id))


ACTIVE = ("queued", "running")


def _server_runs(study: models.Study) -> bool:
    return study.adapter == "mock" and settings.allow_server_mock


def _offline_cases(study: models.Study) -> list[models.Case]:
    """대기·실행 중으로 적혀 있는데 그 PC Agent가 꺼져 있는 케이스. 그 PC가 켜지기 전에는 아무도 상태를 바꿔 주지 않는다."""
    from ..agent.link import registry

    if _server_runs(study):
        return []
    return [c for c in study.cases if c.status in ACTIVE and registry.get(c.run_by) is None]


def _display_names(db: Session, user_ids: list[int]) -> list[str]:
    names = []
    for uid in dict.fromkeys(user_ids):
        user = db.get(models.User, uid)
        names.append(user.display_name if user else f"사용자 {uid}")
    return names


def _only_runner(db: Session, cases: list[models.Case], action: str) -> str:
    who = ", ".join(_display_names(db, [c.run_by for c in cases if c.run_by]))
    return (f"다른 멤버({who}님)가 실행한 케이스는 실행한 사람만 {action} 수 있습니다. 연결이 잠깐 끊긴 것이면 해석은 그 PC에서 계속 돌고 있습니다 "
            "→ 직접 돌리려면 '내 PC로 복사해서 하기'를 쓰세요")


def stalled_view(db: Session, study: models.Study) -> dict:
    """화면·AI가 '실행 중'을 기다리지 않고 이유를 말할 수 있게 한다."""
    gone = _offline_cases(study)
    names = _display_names(db, [c.run_by for c in gone if c.run_by])
    online = any(c.status in ACTIVE for c in study.cases if c not in gone)
    return {"stalled": len(gone), "stalled_by": names, "running": bool(is_running(study.id) and (online or not gone))}


def _restore_previous(case: models.Case, why: str | None) -> bool:
    """다시 돌리기가 끝나지 못했으면 이전 결과로 되돌린다. 돌려주는 값: 되돌렸는가."""
    prev = case.previous
    if not prev:
        return False
    case.status, case.results, case.run_by, case.run_dir = "complete", prev.get("results") or {}, prev.get("run_by"), prev.get("run_dir")
    case.archive, case.images, case.fields = prev.get("archive"), prev.get("images"), prev.get("fields")
    case.timing = prev.get("timing", case.timing)  # 지켜 둔 값을 낸 해석의 시각. 끝나지 못한 다시 돌리기의 시각으로 해석 시간을 재면 안 된다
    case.error = f"다시 돌리기가 끝나지 못해 이전 결과를 지켰습니다: {why}" if why else None
    case.previous = None
    return True


def _forget_active(study_id: int, case_ids: set[int]):
    with _batch_lock:
        for book in (_batches, _extracting):
            left = book.get(study_id)
            if left is not None:
                left -= case_ids
                if not left:
                    book.pop(study_id, None)


def _release(db: Session, study: models.Study, cases: list[models.Case], why: str) -> int:
    """그 PC에 물을 수 없는 케이스를 서버에서 끝낸 것으로 친다. 이전 결과가 있으면 되돌린다.
    솔버를 끝내는 것은 아니다. 그 PC가 돌아왔을 때 끝낼지는 stopped_by(실행한 사람의 중지)만으로 정한다."""
    for c in cases:
        if not _restore_previous(c, why):
            c.status, c.error = "cancelled", why
    db.commit()
    _forget_active(study.id, {c.id for c in cases})
    if cases:
        publish_cases_table(db, study)
        manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})
    return len(cases)


def reconcile_cases(conn, active: list[int], can_settle: bool = False):
    """PC Agent가 연결될 때 상태를 맞춘다. 그 PC에서 돌지 않는 케이스는 끝난 것으로 친다.
    Agent가 죽었다 살아나면 해석도 함께 끝났는데 알려 줄 쪽이 없어 '실행 중'이 영원히 남기 때문이다.
    돌고 있는 솔버는 실행한 사람이 중지했거나 다른 멤버가 넘겨받았을 때만 끝낸다. 연결이 잠깐 끊겼다는 것만으로는 끝내지 않는다.
    돌고 있지 않아도 끝난 결과를 전하기 전에 Agent가 다시 시작했을 수 있다. 끝낼 줄 아는 Agent(can_settle)에게는 먼저 묻고 답을 기다린다."""
    from ..agent.link import registry

    user_id = conn.user_id
    if registry.get(user_id) is not conn:  # 새 연결이 이미 들어왔다. 옛 연결의 목록으로 맞추면 돌고 있는 케이스를 지운다
        return
    running = {int(i) for i in active or []}
    with SessionLocal() as db:
        mine = db.query(models.Case).filter(models.Case.run_by == user_id, models.Case.status.in_(ACTIVE)).all()
        lost: dict[int, list[models.Case]] = {}
        for c in mine:
            if c.id in running:
                with _batch_lock:  # 서버가 다시 시작해 잊었으면 다시 적는다. 끝날 때 결과 카드를 올린다
                    _batches.setdefault(c.study_id, set()).add(c.id)
            else:
                lost.setdefault(c.study_id, []).append(c)
        settle: list[dict] = []
        for study_id, cases in lost.items():
            study = db.get(models.Study, study_id)
            if study is None or _server_runs(study):
                continue
            # 다시 돌리기(이전 값 있음)는 PC의 case.json이 이번 결과인지 옛 결과인지 가릴 수 없다. 지켜 둔 값으로 되돌린다
            ask = [c for c in cases if can_settle and not c.previous]
            rest = [c for c in cases if c not in ask]
            if ask:
                project = db.get(models.Project, study.project_id)
                settle += [{"case_id": c.id, "study_id": study.id, "project_id": study.project_id, "project_name": project.name, "project_uid": project.uid,
                            "study_name": study.name, "source": c.source, "run_dir": c.run_dir, "params": c.params, "adapter": study.adapter,
                            "adapter_config": study.adapter_config or {}} for c in ask]
            if rest:
                _release(db, study, rest, "실행하던 PC에 이 케이스가 남아 있지 않습니다. PC Agent가 다시 시작되어 해석이 끝나지 못했을 수 있습니다 → 다시 돌리려면 그 케이스를 지정해 다시 실행하세요")
        with _batch_lock:
            for study_id, ids in list(_extracting.items()):
                gone = {i for i in ids if i not in running and (c := db.get(models.Case, i)) is not None and c.run_by == user_id}
                if gone:
                    ids -= gone
                    if not ids:
                        _extracting.pop(study_id, None)
        # 상태만 보면 안 된다. cancelled는 위의 정리로도 되고, 이전 값으로 되돌린 중지는 complete다
        stray = [i for i in running if (c := db.get(models.Case, i)) is not None and (c.run_by != user_id or c.stopped_by is not None)]
    if settle:
        conn.send({"type": "case.settle", "cases": settle})
    if stray:  # 다른 멤버가 넘겨받았거나 실행한 사람이 중지한 케이스: 이 PC의 솔버를 끝낸다
        conn.send({"type": "case.cancel", "case_ids": stray, "extract_ids": []})


def _check_run_allowed(db: Session, study: models.Study, user_id: int, flow_id: int | None):
    """승인 규칙을 AI의 말에만 맡기지 않는다. 반려·승인 전 흐름에 묶인 스터디와, 내가 승인하지 않은 다른 사람의 스크립트는 돌리지 않는다."""
    check_flow(db, study.project_id, flow_id)
    if flow_id and study.flow_id != flow_id:
        study.flow_id = flow_id
    fid = flow_id or study.flow_id
    flow = db.get(models.Flow, fid) if fid else None
    if flow is not None and flow.status in ("review", "rejected"):
        state = "반려된" if flow.status == "rejected" else "아직 승인 전인"
        raise NeedsApproval(f"이 스터디는 {state} 작업 흐름 '{flow.title}'에 묶여 있어 돌리지 않습니다 → 작업 흐름을 승인하거나 고쳐서 다시 제안해 승인을 받으세요")
    recipe = study.recipe or {}
    author = recipe.get("created_by")
    if study.adapter == "script" and author and author != user_id:
        approved = flow is not None and flow.status in ("approved", "in_progress") and flow.approved_by == user_id
        if not approved:
            raise NeedsApproval(f"{recipe.get('created_by_name') or '다른 멤버'}님이 만든 스크립트입니다. 이 PC에서 돌리려면 승인을 받아야 합니다 "
                                "→ 작업 흐름 카드에 만든 사람을 적어 제안하고, 승인한 뒤 그 흐름 번호로 돌리세요")


def run_cases_async(study_id: int, case_ids: list[int] | None, dry_run: bool, user_id: int, flow_id: int | None = None) -> tuple[int, int]:
    """돌려주는 값: (보낸 케이스 수, 이미 돌고 있어 보내지 않은 수)."""
    from ..agent.link import AgentError, registry

    with SessionLocal() as db:
        study = db.get(models.Study, study_id)
        if study.adapter == "data":
            raise NoRunMethod(NOT_RUNNABLE)
        if study.adapter == "script" and not (study.adapter_config or {}).get("tool"):
            raise NoRunMethod("해석 방법이 아직 정해지지 않았습니다 → 해석 방법(tool·script)을 연결한 뒤 다시 돌리세요")
        if not dry_run:  # dry_run은 명령만 만들고 실행하지 않는다
            _check_run_allowed(db, study, user_id, flow_id)
        q = db.query(models.Case).filter(models.Case.study_id == study_id)
        q = q.filter(models.Case.id.in_(case_ids)) if case_ids else q.filter(models.Case.status == "pending")
        # 불러온 행은 다시 돌리지 않는다. 입력 모델이 없을 수 있고 사용자의 데이터를 덮어쓰면 안 된다
        cases = [c for c in q.all() if c.source != "import"]
        if not cases:
            return 0, 0
        # 다른 사람 PC에서 도는 케이스를 넘겨받지 않는다. 실행자가 바뀌면 그 PC가 다시 연결될 때 돌던 솔버가 끝나고 결과도 버려진다
        theirs = [c for c in cases if c.status in ACTIVE and c.run_by not in (None, user_id)]
        if theirs:
            raise NeedsApproval(_only_runner(db, theirs, "다시 돌릴"))
        # 내 PC에서 도는 케이스도 다시 보내지 않는다. 같은 해석이 두 번 돌고, 다시 보내면 Agent의 중지 신호도 덮인다
        already = sum(c.status in ACTIVE for c in cases)
        cases = [c for c in cases if c.status not in ACTIVE]
        if not cases:
            return 0, already
        conn = registry.get(user_id)
        if conn is None and not (study.adapter == "mock" and settings.allow_server_mock):
            raise AgentError("케이스를 실행할 PC Agent가 연결되어 있지 않습니다. 내 PC에서 dds-agent를 실행하세요.")
        with _batch_lock:
            _batches.setdefault(study_id, set()).update(c.id for c in cases)
        for c in cases:
            if c.status == "complete" and c.results and not c.previous:  # 다시 돌리기가 실패해도 지금 값을 잃지 않게 둔다
                c.previous = {"results": c.results, "timing": c.timing, "run_by": c.run_by, "run_dir": c.run_dir, "archive": c.archive, "images": c.images, "fields": c.fields}
            c.status, c.run_by, c.error, c.stopped_by = "queued", user_id, None, None
        db.commit()
        project = db.get(models.Project, study.project_id)
        fixed = fixed_values(study)  # 표에서 고정값으로 뺀 값도 해석에는 필요하다
        jobs = [{"case_id": c.id, "study_id": study_id, "project_id": study.project_id, "project_name": project.name, "project_uid": project.uid,
                 "study_name": study.name, "source": c.source, "params": {**fixed, **c.params}, "variables": study.variables,
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
    return len(jobs), already


def stop_cases(study_id: int, case_ids: list[int] | None, user_id: int) -> dict:
    """실행한 사람이 대기·실행 중인 케이스를 중지한다. 솔버 창이 숨어 있어 이것이 웹앱에서 해석을 멈출 유일한 길이다.
    실제 상태는 PC Agent가 끝낸 뒤 case.status로 알린다. 그 PC와 끊겨 있으면 서버에서 먼저 정리하고, 돌아왔을 때 솔버를 끝낸다.
    끊김은 PC가 꺼졌다는 뜻이 아니다. 솔버가 돌고 있을 수 있으니 다른 멤버는 멈추지 못한다."""
    from ..agent.link import AgentError, registry

    with SessionLocal() as db:
        study = db.get(models.Study, study_id)
        extracting_now = _extracting.get(study_id, set())
        chosen = [c for c in study.cases if not case_ids or c.id in case_ids]
        active = [c for c in chosen if c.status in ACTIVE or c.id in extracting_now]
        mine = [c for c in active if c.run_by in (None, user_id)]  # 실행자가 없는 옛 행은 아무도 못 멈추면 영원히 남는다
        others = len(active) - len(mine)
        if others and not mine:
            raise PermissionError(_only_runner(db, active, "중지할"))
        for c in mine:
            if c.status in ACTIVE:  # 보낸 중지가 반쯤 끊긴 연결에서 사라져도 다시 연결될 때 다시 보낸다
                c.stopped_by = user_id
        db.commit()
        offline = _offline_cases(study)
        gone = [c for c in mine if c in offline]
        name = _display_names(db, [user_id])[0]
        released = _release(db, study, gone, f"{name}님이 중지했습니다. 실행하던 PC와 연결이 끊겨 있어 서버에서 먼저 정리했습니다 → 다시 돌리려면 그 케이스를 지정해 다시 실행하세요") if gone else 0
        note = f"실행하던 PC가 꺼져 있던 {released}건을 중지로 처리했습니다. 그 PC가 다시 연결되면 남은 해석을 멈춥니다." if released else ""
        gone_ids = {c.id for c in gone}
        if not _server_runs(study):  # 꺼진 PC에 맡긴 Output 추출도 그 PC가 켜지기 전에는 끝나지 않는다. 값은 그대로 둔다
            lost_extract = {c.id for c in mine if c.id in extracting_now and c.id not in gone_ids and registry.get(c.run_by) is None}
            if lost_extract:
                _forget_active(study_id, lost_extract)
                gone_ids |= lost_extract
                released += len(lost_extract)
                note = f"{note} Output 추출 {len(lost_extract)}건은 그 PC가 꺼져 있어 기다리지 않습니다.".strip()
        left = [c for c in mine if c.id not in gone_ids]
        conn = registry.get(user_id)
        if conn is None and left and not _server_runs(study):
            raise AgentError("케이스를 실행한 PC Agent가 연결되어 있지 않아 멈출 수 없습니다 → PC Agent를 켠 뒤 다시 누르세요")
        run_ids = [c.id for c in left if c.status in ACTIVE]
        extract_ids = [c.id for c in left if c.id in extracting_now and c.status == "complete"]
    if conn is None:  # 서버에서 도는 연습용 mock은 바로 끝나므로 멈출 것이 없다
        return {"stopping": 0, "others": others, "released": released, "note": note}
    if run_ids or extract_ids:
        conn.send({"type": "case.cancel", "case_ids": run_ids, "extract_ids": extract_ids})
    return {"stopping": len(run_ids) + len(extract_ids), "others": others, "released": released, "note": note}


def _note_pc_notice(msg: dict, sender: int):
    """케이스를 돌린 PC가 알린 안내(cases.csv를 못 썼다 등). 케이스 오류가 아니므로 케이스마다 남기지 않고 스터디에 하나만 둔다."""
    with SessionLocal() as db:
        case = db.get(models.Case, msg["case_id"])
        study = db.get(models.Study, case.study_id) if case else None
        if study is None:
            return
        if msg["notice"]:
            study.pc_notice = {"by": sender, "text": msg["notice"]}
        elif (study.pc_notice or {}).get("by") == sender:
            study.pc_notice = None
        else:
            return
        db.commit()
        manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})


def on_case_status(msg: dict, sender: int | None = None):
    """Agent(또는 서버 mock 실행)가 보낸 케이스 상태 반영. extract=True는 Output 추가 추출 결과다(기존 결과에 합친다).
    sender: 보낸 PC의 사용자. 다른 멤버가 넘겨받아 돌리는 케이스에 옛 PC의 늦은 보고가 덮어쓰지 않게 한다."""
    if "notice" in msg and sender is not None:
        _note_pc_notice(msg, sender)
    if msg.get("extract"):
        return _on_extracted(msg)
    with SessionLocal() as db:
        case = db.get(models.Case, msg["case_id"])
        if not case:
            return
        if sender is not None and case.run_by is not None and case.run_by != sender:
            log.info("다른 PC의 늦은 보고를 무시합니다: 케이스 %s", case.id)
            return
        status = msg["status"]
        if case.stopped_by is not None:
            if case.status not in ACTIVE:  # 실행한 사람이 중지해 이미 정리했다. 돌아온 PC의 늦은 보고가 중지를 뒤집거나 지켜 둔 값을 덮지 않는다
                log.info("중지한 케이스의 늦은 보고를 무시합니다: 케이스 %s", case.id)
                return
            if status == "cancelled":  # PC는 누가 멈췄는지 모른다
                msg = {**msg, "error": f"{_display_names(db, [case.stopped_by])[0]}님이 중지했습니다"}
            elif status in FINAL:  # 중지가 닿기 전에 끝났다
                case.stopped_by = None
        if status in ("failed", "cancelled", "dry_run") and _restore_previous(case, msg.get("error") or ("명령만 만들었습니다" if status == "dry_run" else status)):
            pass  # 다시 돌리기가 끝나지 못했다. 저장된 결과를 비우지 않는다
        else:
            case.status = status
            # 해석 시간은 이 두 시각으로만 잰다. updated_at은 추출·묶음 올리기에도 바뀌어 시간이 되지 못한다
            if status == "running":  # 다시 돌리기면 지난번 끝난 시각을 지운다. 남겨 두면 돌고 있는 케이스의 시간이 음수가 된다
                case.timing = {"started_at": datetime.now().isoformat()}
            elif status in FINAL:
                case.timing = {**(case.timing or {}), "ended_at": datetime.now().isoformat()}
            for key in ("error", "run_dir", "images", "fields"):
                if key in msg:
                    setattr(case, key, msg[key])
            if "results" in msg:
                case.results = _clean_results(msg["results"])
            if msg.get("archive"):
                case.archive = {**(case.archive or {}), **msg["archive"]}
            if status == "complete":
                case.previous = None
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


def study_owner(study: models.Study) -> int | None:
    """스터디의 주인. 작업 구조가 들어오면 작업 주인으로 바뀐다. 주인 칸이 생기기 전 스터디는 처음 돌린 사람이 주인이다."""
    if study.created_by is not None:
        return study.created_by
    return next((c.run_by for c in study.cases if c.run_by is not None), None)


def _check_output_owner(db: Session, study: models.Study, user_id: int):
    """Output 추가는 그 사람 PC에서 스크립트를 돌리는 일이다. 멤버도 관리자도 남의 작업에는 하지 못한다."""
    owner = study_owner(study)
    if owner in (None, user_id):
        return
    name = _display_names(db, [owner])[0]
    if any(c.run_by in (owner, None) and c.run_dir for c in study.cases):
        raise PermissionError(f"이 Output은 {name}님 PC의 해석 결과에서 뽑아야 합니다. {name}님만 추가할 수 있습니다 "
                              f"→ {name}님에게 요청하거나 '내 PC로 복사해서 하기'로 다시 해석하세요.")
    raise PermissionError(f"{name}님의 작업이라 볼 수만 있습니다 → {name}님에게 요청하거나 '내 PC로 복사해서 하기'로 내 작업을 만드세요.")


def add_outputs(db: Session, study: models.Study, outputs: list[dict], post, extract_existing: bool, user_id: int) -> dict:
    """Output을 목록에 넣고, 추출 단계를 이후 케이스에 붙이고, 이미 해석을 마친 케이스에서도 추출한다."""
    _check_output_owner(db, study, user_id)
    steps = post if isinstance(post, list) else [post] if post else []
    if steps and study.adapter not in ("script", "data"):
        raise ValueError("추출 단계(post)는 script 어댑터 스터디에만 붙일 수 있습니다")
    check_config_scripts({"post": steps})
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
    queued, skipped, other_pc = extract_outputs_async(study.id, names, user_id) if extract_existing and can else (0, 0, [])
    publish_cases_table(db, study)
    manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})
    no_source = sum(c.status == "complete" and c.source == "import" and not c.run_dir for c in study.cases)
    notes = []
    if not can and no_source:
        notes.append("이 데이터는 표에서 왔습니다. 그 출력 열을 더한 표를 데이터 탭의 '행 추가'로 붙여 넣으면 값이 합쳐집니다.")
    elif not can:
        notes.append("추출 단계(post)가 없어 이미 해석한 케이스에서는 뽑지 않았습니다. 케이스 스크립트가 이 키를 results.json에 쓰면 이후 케이스부터 보입니다.")
    elif no_source:
        notes.append(f"표에서 온 {no_source}건은 원본 해석 파일이 없어 뽑지 못합니다 → 그 출력 열을 더한 표를 데이터 탭의 '행 추가'로 붙여 넣으세요.")
    if skipped:
        notes.append(f"{skipped}건은 해석 파일이 내 PC에 있는데 PC Agent가 꺼져 있어 뽑지 못했습니다 → 그 PC를 켠 뒤 다시 요청하세요. 학습·Simulator는 PC 없이 쓸 수 있습니다.")
    if other_pc:
        who = ", ".join(_display_names(db, sorted(set(other_pc))))
        notes.append(f"{len(other_pc)}건은 해석 파일이 {who}님 PC에 있어 뽑지 않았습니다. 다른 사람 PC에서 스크립트를 돌리지 않습니다 → {who}님에게 요청하세요.")
    return {"outputs": study.outputs, "queued": queued, "skipped": skipped, "other_pc": len(other_pc), "no_source": no_source, "note": " ".join(notes)}


def extract_outputs_async(study_id: int, names: list[str], user_id: int, case_ids: list[int] | None = None) -> tuple[int, int, list[int]]:
    """완료된 케이스를 내 PC Agent로 보내 뽑는다. 내 PC가 꺼져 있으면 건너뛴다(mock은 서버에서).
    다른 사람이 돌린 케이스는 파일이 그 사람 PC에 있지만, 동의 없이 그 PC에서 스크립트를 돌리지 않는다.
    돌려주는 값: (보낸 수, 내 PC가 꺼져 건너뛴 수, 다른 사람 PC에 있어 뺀 케이스의 실행자들)."""
    from ..agent.link import registry

    with SessionLocal() as db:
        study = db.get(models.Study, study_id)
        project = db.get(models.Project, study.project_id)
        steps = _steps_for(study, names)
        jobs, skipped, other_pc = [], 0, []
        for c in study.cases:
            if c.status != "complete" or (case_ids and c.id not in case_ids) or c.id in _extracting.get(study_id, set()):
                continue
            if c.source == "import" and not c.run_dir:  # 표에서 온 행: 뽑을 원본 해석 파일이 없다
                continue
            if c.run_by not in (None, user_id):
                other_pc.append(c.run_by)
                continue
            conn = registry.get(user_id)
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
    return len(jobs), skipped, other_pc


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
        case.results = {**(case.results or {}), **_clean_results(msg.get("results"))}
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


def check_field_npz(data: bytes) -> np.ndarray:
    """필드 파일을 저장하기 전에 읽어 본다. 틀린 파일을 받아 두면 학습 때에야 알 수 없는 오류가 난다. 돌려주는 값: values."""
    try:
        with np.load(io.BytesIO(data), allow_pickle=False) as z:
            if "points" not in z or "values" not in z:
                raise ValueError(f"points와 values 배열이 필요합니다(있는 배열: {', '.join(z.files) or '없음'})")
            points, values = np.asarray(z["points"], dtype=np.float64), np.asarray(z["values"], dtype=np.float64)
    except (ValueError, OSError, EOFError, TypeError, KeyError, zipfile.BadZipFile) as e:
        raise ValueError(f"필드 파일을 읽지 못했습니다: {e} → npz 파일에 points(노드 좌표)와 values(노드 값)를 넣어 주세요") from e
    if points.ndim != 2 or points.shape[1] != 3 or len(points) == 0:
        raise ValueError(f"points는 (노드 수, 3) 배열이어야 합니다(지금 {points.shape}) → 노드 좌표를 x, y, z로 넣어 주세요")
    if len(values) != len(points):
        raise ValueError(f"values 길이({len(values)})가 노드 수({len(points)})와 다릅니다 → 노드마다 값 하나씩 넣어 주세요")
    return values


def register_field_scalar(db: Session, study: models.Study, case: models.Case, name: str, values: np.ndarray) -> str | None:
    """스칼라 출력이 없는 스터디(필드 결과만 불러옴)는 필드 최대값을 출력으로 만든다.
    Simulator는 스칼라 모델을 기준으로 배포되므로, 이것이 없으면 3D 모델을 학습해도 배포할 대상이 없다."""
    auto = {o["name"] for o in study.outputs or [] if o.get("auto")}
    if study.objectives or study.constraints or any(not o.get("auto") for o in study.outputs or []) or case.status != "complete":
        return None
    if any(k not in auto for k in case.results or {}):
        return None
    mag = values if values.ndim == 1 else np.linalg.norm(values, axis=-1)
    mag = mag[np.isfinite(mag)]
    if mag.size == 0:
        return None
    key = f"{name}_max"
    case.results = {**(case.results or {}), key: float(mag.max())}
    if key not in auto:
        study.outputs = [*(study.outputs or []), {"name": key, "note": f"{name} 필드 최대값(자동)", "auto": True}]
    return key


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
        if feasible(study, c.results) is True:  # 최적값은 제약을 만족한다고 확인된 케이스로만
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


def _latest_model(db: Session, study_id: int) -> models.SurrogateModel | None:
    return db.query(models.SurrogateModel).filter_by(study_id=study_id).order_by(models.SurrogateModel.id.desc()).first()


def resolve_outputs(db: Session, study: models.Study, outputs: list[str] | None) -> tuple[list[str], list[str]]:
    """학습할 출력. 지정이 없으면 목적과 직전 모델의 출력을 잃지 않는다(재학습에서 제약 출력이 빠지면 다음 제안에서 제약이 사라진다).
    제약·추가 출력은 값이 모든 완료 케이스에 있을 때만 넣는다. 일부 행만 빠진 출력 때문에 학습 행이 줄지 않게 하려는 것이다.
    돌려주는 값: (학습할 출력, 값이 모자라 뺀 출력)."""
    if outputs:
        return list(dict.fromkeys(outputs)), []
    done = _complete_cases(study)
    latest = _latest_model(db, study.id)
    must = [o["name"] for o in study.objectives or []] + list((latest.outputs if latest else None) or [])
    extras, partial = [], []
    for name in [c["name"] for c in study.constraints or []] + [o["name"] for o in study.outputs or []]:
        have = sum(name in (c.results or {}) for c in done)
        # 표는 사용자가 고른 출력이다. #N/A 한 칸 때문에 AI 경로의 모델에서 그 출력이 통째로 사라지면 안 된다. 절반이 안 차면 추가 출력으로 보고 뺀다
        chosen = study.adapter == "data" and have >= max(3, len(done) / 2)
        if done and (have == len(done) or chosen):
            extras.append(name)
        elif have:
            partial.append((have, name))
    names = list(dict.fromkeys(must + extras))
    if not names and partial:  # 다 빠지느니 값이 가장 많은 출력으로 학습한다
        names = [max(partial)[1]]
    skipped = [n for _, n in partial if n not in names]
    if not names:
        raise ValueError("학습할 출력이 없습니다 → 예측할 출력(outputs)을 지정하거나 목적을 정하세요")
    return names, list(dict.fromkeys(skipped))


def training_rows(study: models.Study, outputs: list[str]) -> tuple[list[models.Case], list[dict]]:
    """학습에 쓸 수 있는 완료 케이스와, 뺀 케이스·이유. 이유 없이 빠지거나 값 하나로 학습 전체가 멈추면 안 된다.
    출력 하나가 빈 행은 그 출력에서만 뺀다(Surrogate.fit). 출력 열을 하나 더했다고 다른 출력의 학습 행이 줄면 안 된다.
    excluded에는 어느 출력에서든 빠진 행을 적고, whole=True는 어느 출력에도 쓰지 못한 행이다."""
    names = _var_names(study)
    good, excluded = [], []
    for c in _complete_cases(study):
        why = next((f"입력 '{v}' 값이 없습니다" for v in names if _cell((c.params or {}).get(v), 12) is None), None)
        if why:
            excluded.append({"case_id": c.id, "reason": why, "whole": True})
            continue
        missing = []
        for o in outputs:
            value = (c.results or {}).get(o)
            if value is None:
                missing.append(f"출력 '{o}' 값이 없습니다")
            elif _cell(value, 12) is None:
                missing.append(f"출력 '{o}' 값이 숫자가 아닙니다")
        if len(missing) == len(outputs):
            excluded.append({"case_id": c.id, "reason": missing[0], "whole": True})
        else:
            good.append(c)
            if missing:
                excluded.append({"case_id": c.id, "reason": missing[0], "whole": False})
    return good, excluded


def _data_hash(study: models.Study, rows: list[models.Case], outputs: list[str]) -> str:
    names = _var_names(study)
    body = [[[format(float(c.params[v]), ".12g") for v in names], [format(float(c.results[o]), ".12g") if _cell((c.results or {}).get(o), 12) is not None else "" for o in outputs]] for c in rows]
    return hashlib.sha256(json.dumps(body).encode()).hexdigest()[:32]


QUALITY_LABEL = {"good": "좋음", "fair": "보통", "poor": "낮음"}
_QUALITY_ORDER = {"good": 0, "fair": 1, "poor": 2}


def _r2_level(r2: float) -> str:
    return "good" if r2 >= 0.9 else "fair" if r2 >= 0.7 else "poor"


def quality_level(m: models.SurrogateModel) -> str | None:
    """정확도 수준. 품질을 저장하기 전에 학습한 옛 모델은 R²로 수준만 정한다(화면 ModelTrust와 같은 기준). 경고가 옛 모델만 건너뛰지 않게."""
    level = (m.quality or {}).get("level")
    if level:
        return level
    r2s = [v["r2"] for v in (m.metrics or {}).values() if isinstance(v, dict) and isinstance(v.get("r2"), (int, float))]
    return max(map(_r2_level, r2s), key=_QUALITY_ORDER.get) if r2s else None


def design_range_r2(actual, pred, keep) -> float | None:
    """설계 범위 전체를 말하는 R². keep은 점수에 넣는 점이다. 최적화 점은 한곳에 몰려 서로를 잘 맞히므로 빼고 잰다(Hartmann6: 전체 R² 0.93, 실제 0.49).
    점수에서만 빼는 것이고 학습에서 빼는 것은 아니다. 값이 모두 같아 잴 수 없으면 None."""
    a, p = np.asarray(actual, dtype=float)[keep], np.asarray(pred, dtype=float)[keep]
    tot = float(np.sum((a - a.mean()) ** 2))
    return 1 - float(np.sum((p - a) ** 2)) / tot if tot > 0 else None


def assess_quality(cv: dict, n_train: int, n_vars: int, adapter: str, constant: list[str], scored: dict | None = None) -> dict:
    """교차검증 R²와 표본 수로 품질을 정한다. R²는 Surrogate.fit의 K겹 교차검증 값이라 학습에 안 쓴 점으로 잰 것이다.
    표본이 적으면 R²가 높게 나와도 믿기 어려워 한 단계 낮춘다. 배포를 막지는 않고 화면과 공유에 경고로 보인다.
    scored는 출력별로 점수에 넣을 점(True)이다. 판단 도우미와 같은 값을 보여 주려고 같은 기준으로 잰다. 남는 점이 5개 미만이면 빼지 않는다."""
    by_output: dict[str, dict] = {}
    warnings: list[str] = []
    narrowed: dict[str, float] = {}
    for o, v in cv.items():
        keep = (scored or {}).get(o)
        if keep is None or o in constant or keep.all() or int(keep.sum()) < 5:
            continue
        r2 = design_range_r2(v["actual"], v["cv_pred"], keep)
        if r2 is not None:
            narrowed[o] = r2
    for o, v in cv.items():
        if o in constant:  # 값이 하나뿐이면 R²가 뜻이 없다
            by_output[o] = {"level": "fair", "r2": None}
            warnings.append(f"출력 '{o}'은(는) 학습 행에서 값이 모두 같아 정확도를 잴 수 없습니다")
            continue
        r2 = narrowed.get(o, v["r2"])
        by_output[o] = {"level": _r2_level(r2), "r2": round(r2, 4)}
        if _r2_level(r2) != _r2_level(v["r2"]):  # 다른 곳에 보이는 R²(모든 점)와 등급이 다르면 왜 다른지 밝힌다
            warnings.append(f"'{o}' 정확도는 최적화가 한곳에 몰아 돌린 점을 빼고 쟀습니다(모든 점으로 재면 R² {v['r2']:.2f}). 설계 범위 전체를 맞히는지를 보기 위해서입니다")
    level = max((b["level"] for b in by_output.values()), key=_QUALITY_ORDER.get, default="poor")
    more = "표에 행을 더하거나 빠진 입력 변수가 없는지 확인하세요" if adapter == "data" else "점을 더 해석하거나(능동학습) 빠진 입력 변수가 없는지 확인하세요"
    if n_vars > 30:  # 입력이 많으면 행을 더해도 잘 나아지지 않는다. 듣지 않는 처방을 시키지 않는다
        more = f"입력이 {n_vars}개로 많습니다. 결과에 영향이 있는 입력 열만 남겨 다시 복사하세요. 행을 더해도 잘 나아지지 않을 수 있습니다"
    low = [f"{o} R² {b['r2']:.2f}" for o, b in by_output.items() if b["level"] == "poor"]
    mid = [f"{o} R² {b['r2']:.2f}" for o, b in by_output.items() if b["level"] == "fair" and b["r2"] is not None]
    if low:
        warnings.append(f"예측 정확도가 낮습니다({', '.join(low)}). 이 값은 참고만 하세요 → {more}")
    if mid:
        warnings.append(f"예측 정확도가 보통입니다({', '.join(mid)}). 결정에 쓰기 전에 해석이나 시험으로 확인하세요 → {more}")
    needed = max(8, 5 * n_vars)
    if n_train < 5:
        level = "poor"
        warnings.append(f"학습 데이터가 {n_train}개뿐이라 정확도(R²)를 믿을 수 없습니다 → 변수 {n_vars}개면 {needed}개 이상을 권합니다")
    elif n_train < needed:
        if level == "good":
            level = "fair"
        warnings.append(f"학습 데이터가 {n_train}개로 적어 정확도(R²)가 실제보다 좋게 보일 수 있습니다 → 변수 {n_vars}개면 {needed}개 이상을 권합니다")
    return {"level": level, "label": QUALITY_LABEL[level], "by_output": by_output, "warnings": warnings, "method": "K겹 교차검증 R²"}


def train(db: Session, study: models.Study, outputs: list[str] | None, flow_id=None, node_id=None,
          created_by: int | None = None) -> models.SurrogateModel:
    check_flow(db, study.project_id, flow_id)
    _remember(study, flow_id, node_id)
    outputs, skipped = resolve_outputs(db, study, outputs)
    done, excluded = training_rows(study, outputs)
    if len(done) < 3:
        why = f" 제외 {len(excluded)}개: {excluded[0]['reason']} 등." if excluded else ""
        raise ValueError(f"학습에는 완료된 케이스가 3개 이상 필요합니다 (현재 {len(done)}개).{why} → 값이 있는 행을 더 넣거나 해석을 돌리세요")
    rows_total = len(done)
    data_hash = _data_hash(study, done, outputs)
    names = _var_names(study)
    X = np.array([[float(c.params[v]) for v in names] for c in done])
    from_bo = np.array([c.source == "bo" for c in done])

    def column(o: str) -> np.ndarray:
        cells = [_cell((c.results or {}).get(o), 12) for c in done]
        raw = [float(c.results[o]) if cell is not None else float("nan") for c, cell in zip(done, cells)]
        return np.array(raw)
    Y = {o: column(o) for o in outputs}
    few = [o for o, y in Y.items() if int(np.isfinite(y).sum()) < 3]
    if few and len(few) == len(outputs):
        raise ValueError(f"학습에는 출력마다 값이 있는 행이 3개 이상 필요합니다({', '.join(few)}) → 값이 있는 행을 더 넣거나 해석을 돌리세요")
    if few:
        outputs = [o for o in outputs if o not in few]
        Y = {o: Y[o] for o in outputs}
    sampled = None
    limit = settings.train_max_rows
    if rows_total > limit:  # GP 시간은 행 수의 세제곱으로 는다(측정: 400행 93초). 서버 스레드를 오래 잡지 않게 고르게 줄인다
        keep = space_filling_subset(X, limit)
        X = X[keep]
        from_bo = from_bo[keep]
        Y = {o: y[keep] for o, y in Y.items()}
        sampled = {"from": rows_total, "to": int(len(keep))}
    constant = [o for o, y in Y.items() if float(np.ptp(y[np.isfinite(y)])) == 0]
    rows_by_output = {o: int(np.isfinite(y).sum()) for o, y in Y.items()}
    sur = Surrogate(variables=names, outputs=outputs)
    sur.bounds = ([v["lower"] for v in study.variables], [v["upper"] for v in study.variables])
    try:
        cv = sur.fit(X, Y)
    except (ValueError, np.linalg.LinAlgError, ArithmeticError) as e:
        log.warning("학습 실패 스터디 %s: %s", study.id, e)
        raise ValueError(f"예측 모델을 학습하지 못했습니다({e.__class__.__name__}) → 값의 크기가 극단적이거나 입력이 거의 같은 행이 많지 않은지 확인하세요") from e
    for v in cv.values():
        v["r2"], v["rmse"] = float(np.nan_to_num(v["r2"], nan=0.0, posinf=1.0, neginf=-1e6)), float(np.nan_to_num(v["rmse"], nan=0.0, posinf=1e300))

    quality = assess_quality(cv, min(rows_by_output.values()), len(names), study.adapter, constant,
                             {o: ~from_bo[np.isfinite(Y[o])] for o in cv})
    whole = [e for e in excluded if e["whole"]]
    if whole:
        kinds = list(dict.fromkeys(e["reason"] for e in whole))
        quality["warnings"].append(f"케이스 {len(whole)}개를 학습에서 뺐습니다: {', '.join(kinds[:3])}{' 등' if len(kinds) > 3 else ''}")
    short = {o: n for o, n in rows_by_output.items() if n < len(X)}
    if short:
        quality["warnings"].append("값이 빈 행은 그 출력의 학습에서만 뺐습니다: " + ", ".join(f"{o} {n}행" for o, n in short.items()) + f" (전체 {len(X)}행)")
    if few:
        quality["warnings"].append(f"값이 있는 행이 3개가 안 되어 뺀 출력: {', '.join(few)} → 그 출력 값을 더 넣으면 다음 학습에 들어갑니다")
    if sampled:
        quality["warnings"].append(f"행이 {sampled['from']:,}개로 많아 입력 공간에 고르게 {sampled['to']:,}개를 골라 학습했습니다. 학습 시간을 줄이기 위해서입니다")
    if skipped:
        quality["warnings"].append(f"일부 행에만 값이 있는 출력은 뺐습니다: {', '.join(skipped)} → 그 출력도 예측하려면 outputs에 지정하세요(값이 있는 행만 씁니다)")
    quality.update({"excluded": excluded[:100], "excluded_count": len(excluded), "rows_total": rows_total, "sampled": sampled, "data_hash": data_hash, "rows_by_output": rows_by_output,
                    # 이 버전을 학습한 범위. 나중에 행을 더해 스터디 범위가 넓어져도 이 버전의 슬라이더는 넓어지지 않는다(외삽을 막는다)
                    "trained_range": {v["name"]: [v["lower"], v["upper"]] for v in study.variables}})

    last = db.query(func.max(models.SurrogateModel.version)).filter_by(study_id=study.id).scalar() or 0
    m = models.SurrogateModel(study_id=study.id, name=f"{study.name} GP #{len(X)}", algo="gp", outputs=outputs,
                              n_train=len(X), path="", version=last + 1, created_by=created_by)
    db.add(m)
    db.flush()
    path = _model_path(m.id)
    sur.save(path)
    m.path = str(path)
    m.metrics = {o: {"r2": v["r2"], "rmse": v["rmse"]} for o, v in cv.items()}
    m.quality = quality
    db.commit()

    for o, v in cv.items():
        publish_artifact(db, study.project_id, "scatter", f"[{study.name}] {o} 예측과 실제 비교",
                         {"x": "실제값", "y": "예측값", "diagonal": True,
                          "series": [{"name": f"R²={v['r2']:.3f}", "points": [{"x": a, "y": p} for a, p in zip(v["actual"], v["cv_pred"])]}]},
                         flow_id, node_id)
    publish_artifact(db, study.project_id, "table", f"[{study.name}] 예측 정확도 요약",
                     {"columns": ["모델", "출력", "R²", "RMSE", "학습 수"],
                      "rows": [[m.name, o, round(v["r2"], 4), round(v["rmse"], 5), m.n_train] for o, v in m.metrics.items()]},
                     flow_id, node_id)
    note_title = f"[{study.name}] 학습 데이터 안내"
    if quality["warnings"]:
        publish_artifact(db, study.project_id, "markdown", note_title,
                         {"text": f"- 정확도: **{quality['label']}**\n" + "\n".join(f"- {w}" for w in quality["warnings"])}, flow_id, node_id)
    elif db.query(models.Artifact).filter_by(project_id=study.project_id, title=note_title).first():
        publish_artifact(db, study.project_id, "markdown", note_title, {"text": f"- 정확도: **{quality['label']}**\n- 이번 학습에는 경고가 없습니다"}, flow_id, node_id)
    manager.publish({"type": "model_updated", "project_id": study.project_id, "data": {"id": m.id}})
    return m


def load_surrogate(m: models.SurrogateModel) -> Surrogate:
    return Surrogate.load(m.path)


def model_variables(study: models.Study, m: models.SurrogateModel) -> list[dict]:
    """이 버전의 입력 범위. 스터디 범위를 그대로 주면 행을 더한 순간 공유 링크의 슬라이더가 옛 모델로 외삽하는 곳까지 넓어진다."""
    trained = (m.quality or {}).get("trained_range")
    if trained is None:  # 범위를 기록하기 전에 학습한 모델은 모델 파일에 있다
        try:
            sur = load_surrogate(m)
            trained = {v: [lo, hi] for v, lo, hi in zip(sur.variables, *sur.bounds)} if sur.bounds else {}
        except Exception:  # noqa: BLE001 — 범위를 못 읽어도 Simulator는 열려야 한다
            trained = {}
    return [{**v, "lower": trained[v["name"]][0], "upper": trained[v["name"]][1]} if v["name"] in trained else v for v in study.variables]


def delete_case(db: Session, study: models.Study, case: models.Case) -> bool:
    """불러온 행을 지운다. 잘못 넣은 값은 고쳐 넣어도 두 행이 함께 남으므로 지울 수 있어야 한다. 배포된 모델은 다시 학습할 때까지 그대로다.
    표로 채운 DOE 점은 계획의 일부라 지우지 않고 값만 비워 대기로 돌린다. 돌려주는 값: 대기로 돌렸는가."""
    if case.status in ACTIVE:
        raise ValueError("실행 중인 케이스는 지울 수 없습니다 → 끝난 뒤에 다시 하세요")
    reset = case.source != "import" and bool(case.origin)
    if case.source != "import" and not reset:
        raise ValueError("불러온 행만 지울 수 있습니다 → 해석한 케이스는 지우지 않습니다. 결과를 빼려면 새 스터디로 다시 불러오세요")
    if reset:
        case.status, case.results, case.origin, case.error = "pending", {}, None, None
    else:
        db.delete(case)
    study.version += 1
    db.commit()
    db.refresh(study)
    publish_cases_table(db, study)
    manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})
    return reset


# ---------- 백그라운드 학습 ----------
# 학습은 수 분 걸릴 수 있는데 CLI의 도구 시간 제한은 짧다(Codex 60초). 요청이 끊겨도 서버는 계속 학습하므로,
# 한 번의 긴 요청 대신 시작 → 상태 확인으로 나눈다. 재시도가 와도 새 버전이 겹치지 않아야 한다.
_train_jobs: dict[str, dict] = {}
_train_lock = threading.Lock()
TRAIN_JOBS_KEPT = 200


def _job_view(job: dict) -> dict:
    return {k: job.get(k) for k in ("job_id", "study_id", "status", "outputs", "rows", "reused", "error", "message", "model_id")} | {
        "elapsed_sec": round((job.get("finished") or time.time()) - job["started"], 1)}


def start_train(db: Session, study: models.Study, outputs: list[str] | None, flow_id, node_id, user_id: int, force: bool = False) -> dict:
    """학습을 시작하고 바로 돌아온다. 같은 출력을 학습 중이면 그 작업을, 같은 데이터로 학습한 모델이 있으면 그 모델을 돌려준다."""
    check_flow(db, study.project_id, flow_id)
    names, _ = resolve_outputs(db, study, outputs)
    rows, excluded = training_rows(study, names)
    if len(rows) < 3:  # 작업을 만들기 전에 바로 알린다. 나중에 실패로 보이면 AI가 기다리다 시간을 쓴다
        why = f" 제외 {len(excluded)}개: {excluded[0]['reason']} 등." if excluded else ""
        raise ValueError(f"학습에는 완료된 케이스가 3개 이상 필요합니다 (현재 {len(rows)}개).{why} → 값이 있는 행을 더 넣거나 해석을 돌리세요")
    data_hash = _data_hash(study, rows, names)
    with _train_lock:
        for job in _train_jobs.values():
            if job["study_id"] == study.id and job["status"] == "running" and job["outputs"] == names and job["data_hash"] == data_hash:
                return _job_view(job)
        now = time.time()
        job = {"job_id": uuid.uuid4().hex[:16], "study_id": study.id, "status": "running", "outputs": names, "rows": len(rows), "data_hash": data_hash,
               "started": now, "finished": None, "reused": False, "error": None, "model_id": None,
               "message": "학습 중입니다. 멈춘 것이 아닙니다. 행이 많으면 몇 분 걸립니다"}
        if not force:
            for m in db.query(models.SurrogateModel).filter_by(study_id=study.id).order_by(models.SurrogateModel.id.desc()):
                if (m.quality or {}).get("data_hash") == data_hash and list(m.outputs or []) == names:
                    job.update(status="done", finished=now, reused=True, model_id=m.id,
                               message=f"같은 데이터로 학습한 모델 v{m.version}이 있어 그대로 씁니다. 다시 학습하려면 force를 켜세요")
                    break
        _train_jobs[job["job_id"]] = job
        while len(_train_jobs) > TRAIN_JOBS_KEPT:
            oldest = next((k for k, j in _train_jobs.items() if j["status"] != "running"), None)
            if oldest is None:
                break
            _train_jobs.pop(oldest)
    if job["status"] == "running":
        _train_pool.submit(_run_train_job, job, flow_id, node_id, user_id)
    return _job_view(job)


def _run_train_job(job: dict, flow_id, node_id, user_id: int):
    try:
        with SessionLocal() as db:
            study = db.get(models.Study, job["study_id"])
            m = train(db, study, job["outputs"], flow_id, node_id, created_by=user_id)
            job.update(status="done", model_id=m.id, message=f"학습을 마쳤습니다: v{m.version}")
    except ValueError as e:
        job.update(status="failed", error=str(e), message="")
    except Exception as e:  # noqa: BLE001 — 폴링하는 쪽이 끝없이 기다리지 않게 어떤 실패든 상태로 남긴다
        log.exception("백그라운드 학습 실패")
        job.update(status="failed", error=f"학습 중 오류가 났습니다({e.__class__.__name__}) → 다시 시도하고, 계속되면 관리자에게 서버 로그를 확인해 달라고 하세요", message="")
    job["finished"] = time.time()


def train_job(job_id: str) -> dict | None:
    job = _train_jobs.get(job_id)
    return _job_view(job) if job else None


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
def _search_model(db: Session, study: models.Study, model_id: int | None) -> models.SurrogateModel | None:
    """찾기에 쓸 모델. 보통 가장 최신이지만, 사용자가 옛 버전으로 되돌렸으면 그 버전이다. 되돌려 거부한 모델로 최적점을 찾으면 Simulator와 답이 어긋난다."""
    q = db.query(models.SurrogateModel).filter_by(study_id=study.id)
    if model_id:
        return q.filter_by(id=model_id).first()
    newest = q.order_by(models.SurrogateModel.id.desc()).first()
    live = current_model(db, study.id)
    if live is not None and newest is not None and live.id != newest.id and live.deployed_at and newest.created_at and live.deployed_at >= newest.created_at:
        return live
    return newest
def _suggest_multi(study: models.Study, sur: Surrogate, lo, hi, n: int):
    """목적이 2개 이상이면 첫 목표만 보지 않고 함께 좋아지는 쪽을 찾는다."""
    objs = study.objectives
    missing = [o["name"] for o in objs if o["name"] not in sur.outputs]
    if missing:
        raise ValueError(f"두 목표 최적화에는 목표를 모두 학습한 모델이 필요합니다. 빠진 목표: {', '.join(missing)} → train에 함께 넣으세요")
    # NaN·참거짓이 값 범위에 섞이면 점수가 모두 0이 되어 임의의 점을 고른다
    done = [c for c in _complete_cases(study) if all(
        isinstance(v := c.results.get(o["name"]), (int, float)) and not isinstance(v, bool) and np.isfinite(v) for o in objs)]
    ok = [c for c in done if feasible(study, c.results) is True] or done
    if not ok:
        raise ValueError("완료된 케이스가 없습니다")
    return bayesopt.suggest(sur, lo, hi, "mo", n, seed=len(study.cases), objectives=objs,
                            observed=np.array([[c.results[o["name"]] for o in objs] for c in ok], dtype=float),
                            constraints=[c for c in study.constraints or [] if c["name"] in sur.outputs])


def suggest(db: Session, study: models.Study, mode: str, n: int, model_id: int | None,
            flow_id=None, node_id=None) -> list[models.Case]:
    check_flow(db, study.project_id, flow_id)
    _remember(study, flow_id, node_id)
    m = _search_model(db, study, model_id)
    if not m:
        raise ValueError("먼저 Surrogate 모델을 학습하세요 (train)")
    sur = load_surrogate(m)
    names = _var_names(study)
    lo = np.array([v["lower"] for v in study.variables])
    hi = np.array([v["upper"] for v in study.variables])
    obj = study.objectives[0] if study.objectives else None
    if mode == "bo" and len(study.objectives or []) >= 2:
        pts = _suggest_multi(study, sur, lo, hi, n)
    elif mode == "bo":
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


UNVERIFIED_NOTE = "해석으로 확인되지 않은 예측입니다. 결정에 쓰기 전에 해석이나 시험으로 확인하세요 → 해석 방법을 연결하면 이 점을 돌려 확인할 수 있습니다"


def _pick_spaced(order: np.ndarray, cand: np.ndarray, lo: np.ndarray, hi: np.ndarray, n: int, gap: float = 0.05) -> list[int]:
    """비슷한 점만 고르지 않게 서로 떨어진 점을 고른다. 모자라면 간격을 무시하고 채운다."""
    span = np.where(hi > lo, hi - lo, 1.0)
    picked: list[int] = []
    for i in order:
        if len(picked) >= n:
            break
        if all(np.linalg.norm((cand[i] - cand[j]) / span) >= gap for j in picked):
            picked.append(int(i))
    for i in order:
        if len(picked) >= n:
            break
        if int(i) not in picked:
            picked.append(int(i))
    return picked


def model_candidates(db: Session, study: models.Study, mode: str, n: int, model_id: int | None = None, objective: str | None = None,
                     goal: str | None = None, constraints: list[dict] | None = None) -> dict:
    """모델로만 후보를 찾는다. 해석 방법이 없는 스터디에서 최적화를 요청받았을 때 쓴다.
    케이스를 만들지 않는다. 확인되지 않은 값이 실제 데이터처럼 다시 학습에 들어가면 안 되기 때문이다."""
    m = _search_model(db, study, model_id)
    if not m:
        raise ValueError("예측 모델이 아직 없습니다 → 먼저 학습하세요")
    sur = load_surrogate(m)
    # 모델을 학습한 범위 안에서만 찾는다. 나중에 넓힌 범위는 외삽이라 믿을 수 없다
    if sur.bounds:
        lo, hi = np.array(sur.bounds[0], dtype=float), np.array(sur.bounds[1], dtype=float)
    else:
        spec = {v["name"]: v for v in study.variables}
        lo, hi = np.array([spec[v]["lower"] for v in sur.variables], dtype=float), np.array([spec[v]["upper"] for v in sur.variables], dtype=float)
    cand = qmc.scale(qmc.Sobol(d=len(lo), scramble=True, seed=0).random(4096), lo, hi)
    mean, std = sur.predict(cand)
    note = UNVERIFIED_NOTE
    use = study.constraints if constraints is None else constraints
    # 후보의 제약 판정도 같은 정의를 쓴다. 모델이 배우지 않은 제약은 값이 없는 것이니 만족으로 치지 않는다
    states = [constraint_state(use, {k: mean[k][i] for k in sur.outputs}) for i in range(len(cand))]
    ok = np.array([st is not False for st in states], dtype=bool)
    best_known = None
    if mode == "bo":
        first = (study.objectives or [{}])[0]
        objective = objective or first.get("name")
        if not objective:
            raise ValueError(f"무엇을 좋게 할지 정해지지 않았습니다 → objective에 출력 이름을 주세요(모델의 출력: {', '.join(sur.outputs)})")
        if objective not in sur.outputs:
            raise ValueError(f"모델이 '{objective}'을(를) 학습하지 않았습니다 → 그 출력으로 다시 학습하세요(모델의 출력: {', '.join(sur.outputs)})")
        goal = goal or next((o.get("goal") for o in study.objectives or [] if o["name"] == objective), None)
        if goal not in ("min", "max"):  # 수명을 '최소'로 찾으면 가장 짧은 수명을 최적이라고 답하게 된다. 묻는다
            raise ValueError(f"'{objective}'을(를) 줄일까요, 키울까요? → goal에 min(줄이기) 또는 max(키우기)를 주세요")
        sign = 1.0 if goal == "min" else -1.0
        missing = [c["name"] for c in use or [] if c["name"] not in sur.outputs]
        if missing:
            note += f" 모델이 학습하지 않은 제약은 반영하지 못했습니다: {', '.join(missing)}"
        if not ok.any():
            note += " 제약을 모두 만족한다고 예측되는 점이 없어 목적이 가장 좋은 점을 보였습니다"
        order = np.lexsort((sign * mean[objective], ~ok))
        # 지금까지 가장 좋은 값도 제약을 만족한다고 확인된 케이스에서 고른다
        rows = [c for c in _complete_cases(study) if isinstance((c.results or {}).get(objective), (int, float)) and constraint_state(use, c.results) is True]
        if rows:
            best = min(rows, key=lambda c: sign * c.results[objective])
            best_known = {"case_id": best.id, "params": best.params, "results": best.results}
    else:
        objective, goal = None, None
        score = sum(std[k] / (np.std(mean[k]) + 1e-12) for k in sur.outputs)
        order = np.argsort(-np.nan_to_num(score))
        note = "모델이 가장 불확실한 점입니다. 여기를 해석하거나 시험해 표에 더하면 모델이 가장 좋아집니다. " + note
    picked = _pick_spaced(order, cand, lo, hi, n)
    candidates = [{"params": {v: float(cand[i][j]) for j, v in enumerate(sur.variables)},
                   "predicted": {k: float(mean[k][i]) for k in sur.outputs}, "std": {k: float(std[k][i]) for k in sur.outputs},
                   "feasible": states[i], "verified": False} for i in picked]
    publish_artifact(db, study.project_id, "table", f"[{study.name}] 모델 예측 후보 (확인 안 됨)",
                     {"columns": [*sur.variables, *[f"{k} 예측" for k in sur.outputs], "제약 만족 예측"],
                      "rows": [[*[round(c["params"][v], 4) for v in sur.variables], *[round(c["predicted"][k], 5) for k in sur.outputs],
                                {True: "예", False: "아니오"}.get(c["feasible"], "모름")] for c in candidates]})
    note += f" (예측 모델 v{m.version} 기준)"
    return {"study_id": study.id, "model_id": m.id, "model_version": m.version, "mode": mode, "verified": False, "note": note, "objective": objective, "goal": goal,
            "candidates": candidates, "best_known": best_known}
