"""dds-study MCP: 설계 탐색 스터디. 어디서 시작하든 Simulator까지 간다.
표·기존 해석 폴더를 등록하거나(inspect_table·import_table·scan_results·import_cases), DOE부터 돌려(create_study·generate_doe·run_cases)
학습 → 베이지안 최적화/능동학습 → Simulator 배포로 이어진다. 계산(학습·BO)은 서버에서, 케이스 실행과 파일 읽기는 이 PC에서 이뤄진다."""
import hashlib
import json
import re
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Literal

from _common import DdsMCP, ServerError, api, resolve_pid, settings, tool_wait_limit

from cae_adapters.recipe_bundle import build_bundle, config_scripts, inside, member_path
from cae_adapters.result_scan import brief as scan_brief
from cae_adapters.result_scan import scan_folder
from cae_adapters.tabular import TableError, analyze, cell_state, fill_guessing, join_tables, read_table

mcp = DdsMCP(
    "dds-study",
    instructions=(
        "사용자가 가진 것에서 시작한다. 표가 있으면 inspect_table → import_table, 해석을 끝낸 폴더가 있으면 scan_results → import_cases, "
        "만든 스터디·모델이 있으면 list_studies·list_models로 찾아 이어 간다. 그다음 train_surrogate → deploy_model. "
        "아무 데이터도 없을 때만 create_study → generate_doe → run_cases → train_surrogate 순으로 해석부터 한다. "
        "Flow 승인은 해석·긴 계산을 새로 돌리거나 사용자 모델을 바꾸는 일에만 받는다. 조회·후처리·기존 데이터로 학습·Simulator 배포·보고서는 승인 없이 바로 한다. "
        "최적화는 optimize(mode='bo'), 학습 효율화는 optimize(mode='al')를 쓴다. 불러온 데이터 스터디의 최적화는 해석으로 확인되지 않은 후보만 준다. "
        "각 단계 결과는 웹앱에 자동으로 게시된다. 사용자가 출력(Output) 항목을 더 원하면 add_outputs로 추가한다(이미 해석한 케이스에서도 추출된다)."
    ),
)
MAX_TABLE_BYTES = 200 * 1024 * 1024  # 서버의 표 파일 한도(table_max_mb)와 같다. 넘는 파일은 메모리에 읽기 전에 알린다
_JOBS: dict[str, dict] = {}  # 뒤에서 도는 등록·탐색 (같은 인자로 다시 부르면 이어서 기다린다)
_JOBS_LOCK = threading.Lock()


def _in_background(what: str, key_args: dict, work) -> dict:
    """도구 시간 제한(Codex 약 60초)을 넘을 수 있는 일을 뒤에서 돌린다. 넘으면 도구는 실패로 보이는데 서버는 등록을 마쳐
    AI가 다시 시도하게 되므로, 제한 안에 "아직 진행 중"으로 돌려주고 같은 인자로 다시 부를 때 같은 작업을 이어서 기다린다."""
    limit = tool_wait_limit()
    if not limit:
        return work()
    key = what + json.dumps(key_args, sort_keys=True, ensure_ascii=False, default=str)
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job is None:
            job = _JOBS[key] = {"done": threading.Event(), "result": None, "error": None, "started": time.time()}

            def body(job=job):
                try:
                    job["result"] = work()
                except Exception as e:  # 다시 부른 쪽에서 원래 오류 문구를 그대로 낸다
                    job["error"] = e
                finally:
                    job["done"].set()

            threading.Thread(target=body, daemon=True).start()
    if not job["done"].wait(timeout=limit):
        return {"status": "running", "elapsed_sec": int(time.time() - job["started"]),
                "note": f"실패가 아닙니다. {what}이(가) 아직 진행 중입니다. 같은 인자로 다시 호출하면 이어서 기다립니다(겹쳐 등록되지 않습니다)."}
    with _JOBS_LOCK:
        _JOBS.pop(key, None)
    if job["error"] is not None:
        raise job["error"]
    return job["result"]


def _summary(st: dict) -> dict:
    s = st["study"]
    best = st.get("best")
    out = {"study_id": s["id"], "name": s["name"], "adapter": s["adapter"], "counts": st["counts"],
           "running": st.get("running", False), "data_source": st.get("data_source"),
           "best": {"case_id": best["id"], "params": best["params"], "results": best["results"]} if best else None}
    if st.get("pc_notice"):  # 결과에는 문제가 없지만 PC에서 사용자가 할 일이 있다
        out["pc_notice"] = st["pc_notice"].get("text")
    if st.get("stalled"):  # 끝나기를 기다려도 소용없다. 이유를 사용자에게 말할 수 있게 준다
        out["stalled"] = {"cases": st["stalled"], "by": st.get("stalled_by") or [],
                          "note": "대기·실행 중으로 남았지만 그 PC와 연결이 끊겨 있습니다. 잠깐 끊긴 것이면 해석은 계속 돌고 있고 다시 연결되면 결과가 들어옵니다. 중지는 실행한 사람만 합니다. 기다리지 말고 사용자에게 이유를 알립니다."}
    return out


@mcp.tool()
def list_adapters() -> list[dict]:
    """케이스 실행 어댑터 목록과 adapter_config 예시. mock=해석함수 가짜 솔버, script=툴 스크립트."""
    return api.get("/api/adapters")


@mcp.tool()
def create_study(name: str, variables: list[dict[str, Any]], objectives: list[dict[str, str]] | None = None,
                 adapter: str = "script", adapter_config: dict[str, Any] | None = None,
                 constraints: list[dict[str, Any]] | None = None, outputs: list[dict[str, str]] | None = None,
                 project_id: int | None = None) -> dict:
    """스터디를 만든다. 웹앱 '데이터·모델' 탭에 입력(Input)과 출력(Output)이 나뉘어 보인다.
    variables: [{"name": "thickness", "lower": 1.0, "upper": 5.0}]  (입력)
    objectives: [{"name": "mass", "goal": "min"}]  (첫 번째가 최적화 대상, 이름은 results.json 키)
    constraints: [{"name": "max_stress", "upper": 250}]  (결과 제약 — 최적 케이스·BO가 이를 만족하는 쪽을 고른다.
                 BO에 반영하려면 train_surrogate의 outputs에 제약 항목도 넣는다)
    outputs: [{"name": "deflection", "note": "끝단 처짐 [mm]"}]  (목적·제약 말고도 뽑는 출력과 화면 설명. 나중에 add_outputs로 늘린다)
    adapter='script' 이면 adapter_config: {"tool": "abaqus_cae", "script": "scripts/plate.py", "inputs": ["models/plate.cae"], "post": {...}}
    adapter='mock' 이면 adapter_config: {"function": "branin" | "rosenbrock" | "beam", "outputs": [남길 출력]}
    """
    pid = resolve_pid(project_id)
    s = api.post(f"/api/projects/{pid}/studies", {"name": name, "variables": variables, "objectives": objectives or [],
                                                  "constraints": constraints or [], "outputs": outputs or [],
                                                  "adapter": adapter, "adapter_config": adapter_config or {}})
    return {"study_id": s["id"]}


@mcp.tool()
def add_outputs(study_id: int, outputs: list[dict[str, str]], post: dict[str, Any] | list[dict[str, Any]] | None = None,
                extract_existing: bool = True, wait_sec: int = 600) -> dict:
    """Output(출력) 항목을 추가한다. 사용자가 "OO도 Output에 넣어 줘"라고 하면 쓴다.
    - 이미 해석한 케이스: 해석은 다시 돌리지 않고 케이스 폴더의 결과 파일에서 새 Output만 뽑아 기존 결과에 합친다.
    - 이후 케이스: 해석할 때 post 단계가 함께 돌아 계속 추출된다.
    outputs: [{"name": "floor_force", "note": "바닥 전달력 [N]"}]  (name은 results.json 키)
    post: script 어댑터일 때 추출 단계 {"tool": "abaqus_python", "script": "scripts/extract_disp.py", "args": []}
          추출 스크립트는 케이스 폴더(cwd)에서 돌고 새 키만 results.json에 써도 된다(기존 값은 플랫폼이 지킨다).
          붙이기 전에 dds-cae.run_script로 한 케이스에서 먼저 시험한다. 결과 파일 경로는 args로 넘긴다. 큰 결과 파일을 inputs로 복사하지 않는다.
          불러온 해석 폴더의 케이스는 원본에 쓰지 않으려고 작업 폴더에서 돈다. 스크립트는 args의 {source_dir} 또는 환경변수 DDS_SOURCE_DIR로 원본을 찾는다.
    mock 어댑터는 post 없이 해석함수에서 다시 계산한다.
    원본 폴더가 있는 PC가 꺼져 있거나 다른 멤버 PC면 추출할 수 없다. skipped_no_agent·note를 사용자에게 알린다.
    """
    r = api.post(f"/api/studies/{study_id}/outputs", {"outputs": outputs, "post": post, "extract_existing": extract_existing})
    st = _wait(study_id, wait_sec)
    cases = api.get(f"/api/studies/{study_id}/cases")
    names = [o["name"] for o in outputs]
    done = [c for c in cases if c["status"] == "complete"]
    out = {"queued": r["queued"], "skipped_no_agent": r["skipped"], "still_extracting": len(st.get("extracting") or []),
           "extracted": {n: sum(1 for c in done if n in (c.get("results") or {})) for n in names}, "complete_cases": len(done)}
    fails = [{"case_id": c["id"], "error": c["error"][-500:]} for c in done if (c.get("error") or "").startswith("Output 추출 실패")][-3:]
    if fails:
        out["recent_failures"] = fails
    if r.get("note"):
        out["note"] = r["note"]
    return out


@mcp.tool()
def generate_doe(study_id: int, n: int = 20, method: Literal["lhs", "sobol", "random"] = "lhs", seed: int | None = None,
                 flow_id: int | None = None, node_id: str | None = None) -> dict:
    """DOE 샘플을 만들어 실행 대기(pending) 케이스로 등록한다. 산점도·케이스 표가 웹앱에 게시된다."""
    cases = api.post(f"/api/studies/{study_id}/doe",
                     {"method": method, "n": n, "seed": seed, "flow_id": flow_id, "node_id": node_id})
    return {"created": len(cases), "case_ids": [c["id"] for c in cases]}


def _wait_cap(wait_sec: int) -> int:
    limit = tool_wait_limit()  # CLI의 도구 시간 제한 안에 돌아온다. 그래야 실패로 보이지 않고 get_study로 이어서 확인한다
    return min(wait_sec, limit) if limit else wait_sec


def _wait(study_id: int, wait_sec: int) -> dict:
    wait_sec = _wait_cap(wait_sec)
    t0 = time.time()
    while True:
        st = api.get(f"/api/studies/{study_id}")
        if not (st.get("running") or st.get("extracting")) or time.time() - t0 > wait_sec:
            return st
        time.sleep(2)


@mcp.tool()
def run_cases(study_id: int, case_ids: list[int] | None = None, dry_run: bool = False, wait_sec: int = 480, flow_id: int | None = None) -> dict:
    """대기 케이스(또는 지정한 케이스)를 이 PC Agent에서 실행하고 최대 wait_sec 동안 기다린다.
    아직 running이면 get_study로 다시 확인한다. dry_run=True면 스크립트·명령만 만들고 실행하지 않는다.
    flow_id: 이 실행을 승인받은 작업 흐름. 다른 멤버가 만든 스크립트는 내가 승인한 흐름이 있어야 돈다. 서버가 확인한다.
    끝난 케이스를 다시 돌리다 실패하면 이전 결과가 그대로 남고 그 이유가 케이스 오류에 적힌다."""
    r = api.post(f"/api/studies/{study_id}/run", {"case_ids": case_ids, "dry_run": dry_run, "flow_id": flow_id})
    out = _summary(_wait(study_id, wait_sec))
    out["queued"] = r["queued"]
    if r.get("note"):
        out["note"] = r["note"]
    failed = [c for c in api.get(f"/api/studies/{study_id}/cases") if c["status"] == "failed"][-3:]
    if failed:
        out["recent_failures"] = [{"case_id": c["id"], "error": (c["error"] or "")[-800:]} for c in failed]
    return out


@mcp.tool()
def stop_cases(study_id: int, case_ids: list[int] | None = None) -> dict:
    """대기·실행 중인 케이스를 중지한다(case_ids를 주지 않으면 전체). 실행 중인 솔버는 끝나고 결과가 남지 않으며, 대기 케이스는 시작하지 않는다.
    케이스는 'cancelled'가 되고, 다시 돌리려면 run_cases에 case_ids로 지정한다. 사용자가 멈춰 달라고 할 때만 쓴다.
    실행한 사람의 케이스만 멈춘다. 다른 사람의 케이스는 403이다. 정리하거나 넘겨받으려고 쓰지 않는다."""
    return api.post(f"/api/studies/{study_id}/stop", {"case_ids": case_ids})


@mcp.tool()
def get_study(study_id: int, include_cases: bool = False, limit: int = 200, offset: int = 0) -> dict:
    """스터디 설정과 진행 상황(입력·출력·목적·제약, 해석 방법 adapter·adapter_config, 상태별 케이스 수, 실행 중 여부, 현재 최적 케이스, 스크립트 묶음 유무).
    include_cases=True면 케이스별 params·results·status도 준다(제약조건을 만족하는 최적 설계를 직접 고를 때).
    불러온 표는 행이 많을 수 있어 한 번에 limit개만 준다. 뒤가 더 있으면 truncated=true이고 offset을 늘려 이어 읽는다."""
    st = api.get(f"/api/studies/{study_id}")
    s = st["study"]
    out = _summary(st)
    out.update({k: s.get(k) for k in ("project_id", "variables", "outputs", "objectives", "constraints", "adapter_config", "flow_id", "has_recipe")})
    if s.get("import_info"):
        out["fixed"] = s["import_info"].get("fixed") or {}
        out["cloned_from"] = s["import_info"].get("cloned_from")
    if include_cases:
        total = sum(st["counts"].values())
        limit = max(1, limit)
        offset = max(0, offset)
        out["cases"] = [{"case_id": c["id"], "status": c["status"], "params": c["params"], "results": c.get("results"), "source": c.get("source"),
                         "data": {k: (c.get("archive") or {}).get(k) for k in ("stored", "size", "field_names", "ok")} if c.get("archive") else None}
                        for c in api.get(f"/api/studies/{study_id}/cases", limit=limit, offset=offset)]
        out["cases_total"] = total
        out["truncated"] = offset + len(out["cases"]) < total
    return out


@mcp.tool()
def case_data(case_id: int) -> dict:
    """케이스 해석 데이터(case.h5, HDF5)에 무엇이 담겼는지: 설계변수·결과, 메쉬(파트·노드·요소 수), 필드(스텝·프레임·성분·단위),
    시간 이력, 함께 담긴 파일, PC에 원본으로만 남은 결과 파일(raw), 검증 결과(checks). stored=server면 서버에도 있다.
    서버 사본은 3D 학습에 쓰인다. Output 추가는 그 케이스를 돌린 PC에서만 되고, 그 PC에서 원본을 지웠으면 추출 스크립트가 케이스 폴더의 case.h5를 읽게 한다(cae_adapters.h5store, docs/cae-data-hdf5.md)."""
    s = api.get(f"/api/cases/{case_id}/archive")
    if s.get("files") and len(s["files"]) > 40:
        s["files"] = s["files"][:40] + [f"... {len(s['files']) - 40}개 더"]
    return s


def _train(study_id: int, outputs: list[str] | None, flow_id: int | None, node_id: str | None, wait_sec: int, force: bool = False) -> dict:
    """학습을 시작하고 도구 시간 제한 안에서 기다린다. 긴 요청 하나로 두면 Codex 60초 제한에 실패로 보이는데 서버는 계속 돌아,
    재시도할 때마다 버전이 겹친다. 서버 작업은 같은 요청에 같은 작업을 돌려주므로 다시 불러도 안전하다."""
    wait_sec = _wait_cap(wait_sec)
    t0 = time.time()
    job = api.post(f"/api/studies/{study_id}/train/start", {"outputs": outputs, "flow_id": flow_id, "node_id": node_id, "force": force})
    while job["status"] == "running" and time.time() - t0 <= wait_sec:
        time.sleep(2)
        job = api.get(f"/api/train-jobs/{job['job_id']}")
    return job


def _train_result(job: dict) -> dict:
    if job["status"] == "running":
        return {"status": "running", "job_id": job["job_id"], "elapsed_sec": job["elapsed_sec"], "rows": job["rows"],
                "note": "실패가 아닙니다. 학습이 아직 진행 중입니다. train_surrogate를 같은 인자로 다시 호출하면 이어서 기다립니다(새 버전이 겹치지 않습니다)."}
    if job["status"] == "failed":
        raise ServerError(job.get("error") or "학습에 실패했습니다")
    m = job["model"]
    q = m.get("quality") or {}
    out = {"status": "done", "model_id": m["id"], "version": m.get("version"), "n_train": m["n_train"], "metrics": m["metrics"], "reused": job.get("reused", False),
           "quality": q.get("level"), "quality_label": q.get("label"), "warnings": q.get("warnings") or [], "excluded_count": q.get("excluded_count", 0),
           "sampled": q.get("sampled")}
    if job.get("message") and job.get("reused"):
        out["note"] = job["message"]
    return out


@mcp.tool()
def train_surrogate(study_id: int, outputs: list[str] | None = None, flow_id: int | None = None,
                    node_id: str | None = None, wait_sec: int = 480, force: bool = False) -> dict:
    """완료된 케이스로 Gaussian Process Surrogate를 학습한다. 교차검증 R²/RMSE와 예측 정확도 그림이 게시된다.
    결과의 quality(good/fair/poor)와 warnings(뺀 행, 표본 축소, 정확도 경고)를 사용자에게 그대로 알린다.
    status가 running이면 실패가 아니다. 같은 인자로 다시 호출하면 이어서 기다리고 버전이 겹치지 않는다.
    같은 데이터로 이미 학습했으면 그 모델을 돌려준다(reused). 그래도 새로 학습하려면 force=True."""
    return _train_result(_train(study_id, outputs, flow_id, node_id, wait_sec, force))


@mcp.tool()
def suggest_next(study_id: int, mode: Literal["bo", "al"] = "bo", n: int = 1, flow_id: int | None = None,
                 node_id: str | None = None, objective: str | None = None, goal: Literal["min", "max"] | None = None) -> dict:
    """최신 Surrogate로 다음 케이스를 제안한다. bo=기대개선(EI) 최적화, al=불확실도 최대(능동학습).
    불러온 데이터 스터디는 케이스 대신 모델로만 찾은 후보를 돌려준다(verified=false, 해석으로 확인되지 않은 예측).
    목적을 정하지 않은 표면 objective(출력 이름)와 goal(min·max)을 준다."""
    if objective:  # 스터디에 목적이 없는 표: 목적을 받는 경로는 후보 찾기뿐이다
        return api.post(f"/api/studies/{study_id}/candidates", {"mode": mode, "n": n, "objective": objective, "goal": goal})
    cases = api.post(f"/api/studies/{study_id}/suggest", {"mode": mode, "n": n, "flow_id": flow_id, "node_id": node_id})
    if isinstance(cases, dict):
        return cases
    return {"case_ids": [c["id"] for c in cases], "params": [c["params"] for c in cases]}


@mcp.tool()
def optimize(study_id: int, iterations: int = 10, mode: Literal["bo", "al"] = "bo", batch: int = 1,
             flow_id: int | None = None, node_id: str | None = None, wait_sec: int = 480, objective: str | None = None,
             goal: Literal["min", "max"] | None = None) -> dict:
    """제안 → 실행 → 재학습을 iterations번 반복한다. bo는 목적함수 최적화, al은 Surrogate 정확도 향상.
    한 번 호출이 오래 걸리면 iterations를 나눠 여러 번 호출한다. 수렴 그래프가 게시된다.
    불러온 데이터 스터디(adapter='data')는 돌릴 해석이 없어 모델로만 찾은 후보를 돌려준다. verified=false이고
    해석으로 확인되지 않은 예측이므로 사용자에게 그렇게 말한다. 케이스는 늘어나지 않는다.
    목적을 정하지 않은 표면 objective(출력 이름)와 goal(min·max)을 준다(불러온 데이터 스터디에서만 쓴다). 줄일지 키울지 모르면 도구가 오류로 묻는다. 그대로 사용자에게 묻는다."""
    study = api.get(f"/api/studies/{study_id}")["study"]
    if study["adapter"] == "data":
        return api.post(f"/api/studies/{study_id}/candidates", {"mode": mode, "n": max(batch, 5), "objective": objective, "goal": goal})
    history = []
    t0, limit = time.time(), tool_wait_limit()
    for i in range(iterations):
        if i and limit and time.time() - t0 > limit * 0.5:  # 반복 전체가 CLI의 도구 시간 제한을 넘지 않게 여기까지만 하고 돌려준다
            return {"history": history, "stopped_at": i, "model_id": m["id"],
                    "reason": "실패가 아닙니다. 한 번에 도는 시간을 넘겨 여기까지 했습니다. 남은 반복을 iterations로 나눠 다시 호출하세요.",
                    **_summary(api.get(f"/api/studies/{study_id}"))}
        cases = api.post(f"/api/studies/{study_id}/suggest", {"mode": mode, "n": batch, "flow_id": flow_id, "node_id": node_id})
        # 제안한 점만 돌린다. {}를 보내면 사용자가 일부러 남겨 둔 대기 DOE까지 전부 돌아간다
        api.post(f"/api/studies/{study_id}/run", {"case_ids": [c["id"] for c in cases], "flow_id": flow_id})
        st = _wait(study_id, wait_sec)
        if st.get("running"):
            return {"stopped_at": i, "reason": "케이스가 아직 실행 중입니다. get_study로 확인 후 다시 호출하세요.",
                    **_summary(st)}
        job = _train(study_id, None, flow_id, node_id, wait_sec, force=True)
        if job["status"] == "running":
            return {"stopped_at": i, "history": history, "job_id": job["job_id"],
                    "reason": "실패가 아닙니다. 재학습이 아직 진행 중입니다. train_surrogate로 끝난 것을 확인한 뒤 남은 반복을 다시 호출하세요.",
                    **_summary(st)}
        if job["status"] == "failed":
            raise ServerError(job.get("error") or "학습에 실패했습니다")
        m = job["model"]
        best = st.get("best")
        history.append({"iter": i + 1, "best": best["results"] if best else None,
                        "r2": {k: round(v["r2"], 4) for k, v in m["metrics"].items()}})
    return {"history": history, "model_id": m["id"], **_summary(api.get(f"/api/studies/{study_id}"))}


@mcp.tool()
def train_field_model(study_id: int, field: str, flow_id: int | None = None, node_id: str | None = None) -> dict:
    """형상·필드 모델 학습: 케이스마다 남긴 fields/<field>.npz(points, values, triangles)로
    '설계변수 → 3D 형상 + 노드 필드(응력·변위 등)'를 예측하는 모델(PCA + GP)을 만든다. Simulator에서 3D로 보인다.
    케이스 스크립트가 fields/ 규약(docs/skill-contract.md 1-1절)대로 필드를 저장해야 한다. 노드 수·순서가 케이스마다 같아야 한다."""
    fm = api.post(f"/api/studies/{study_id}/train_field", {"field": field, "flow_id": flow_id, "node_id": node_id})
    return {"field_model_id": fm["id"], "nodes": fm["n_nodes"], "shape_varies": fm["shape_varies"], "info": fm["info"]}


@mcp.tool()
def build_dataset(study_id: int, targets: list[str] | None = None, frames: str | int = "last", step: str | None = None, name: str = "",
                  flow_id: int | None = None, node_id: str | None = None, wait_sec: int = 600) -> dict:
    """신경망 형상 학습용 데이터셋을 만든다: 케이스마다 case.h5에서 학습 샘플(train.h5)을 뽑아 학습·검증·시험으로 나누고 통계를 낸다.
    서버에 case.h5가 없는 큰 케이스는 그 PC Agent가 샘플만 만들어 올린다. 해석은 다시 하지 않는다.
    targets: 표준 이름 displacement · von_mises · stress · strain · plastic_strain · temperature 또는 case.h5의 필드 이름. 비우면 있는 것 전부.
    frames: last(마지막 프레임) | all | 등간격 프레임 수. step: 스텝 이름(비우면 원하는 출력이 가장 많은 스텝).
    결과의 excluded(빠진 케이스와 이유)와 inputs_dropped(추출 못 한 입력)를 사용자에게 알린다.
    단위를 알려면 스터디 adapter_config.units에 {"length":"mm","force":"N","stress":"MPa","time":"s"}를 넣어 해석해야 한다."""
    d = api.post(f"/api/studies/{study_id}/datasets", {"name": name, "targets": targets, "frames": frames, "step": step,
                                                        "flow_id": flow_id, "node_id": node_id})
    t0 = time.time()
    wait_sec = _wait_cap(wait_sec)
    while d["status"] == "building" and time.time() - t0 < wait_sec:
        time.sleep(2)
        d = api.get(f"/api/datasets/{d['id']}")
    return {k: d.get(k) for k in ("id", "status", "n_samples", "splits", "targets", "inputs", "inputs_dropped", "units_known", "same_mesh",
                                  "excluded", "error", "pending")}


@mcp.tool()
def deploy_model(model_id: int) -> dict:
    """모델을 그 스터디 Simulator의 현재 버전으로 배포한다. 웹앱 Simulator 탭에서 슬라이더로 바로 예측할 수 있다.
    학습할 때마다 새 버전(v1, v2…)이 생긴다. 옛 버전의 model_id를 주면 되돌리기다. 공유 링크는 다시 보내지 않아도 현재 버전을 따라간다."""
    m = api.post(f"/api/models/{model_id}/deploy")
    return {"model_id": m["id"], "deployed": m["deployed"], "simulator_path": f"/simulator/{m['id']}"}


@mcp.tool()
def delete_case(study_id: int, case_id: int) -> dict:
    """잘못 넣은 불러온 행을 지운다. 고친 값을 다시 넣어도 옛 행이 함께 남으므로 먼저 지운다. 표로 채운 DOE 점은 값만 비워 대기로 돌린다(reset=true). 해석한 케이스는 지우지 않는다.
    사용자가 지우라고 할 때만 쓴다. 배포된 모델은 다시 학습해 배포할 때까지 그대로다."""
    return api.delete(f"/api/studies/{study_id}/cases/{case_id}")


@mcp.tool()
def predict(model_id: int, params: dict[str, float]) -> dict:
    """Surrogate로 예측(평균, 표준편차)."""
    return api.post(f"/api/models/{model_id}/predict", {"params": params})


@mcp.tool()
def predict_batch(model_id: int, points: list[dict[str, float]]) -> dict:
    """여러 점을 한 번에 예측한다(민감도 곡선·표 만들기). points: [{"thickness": 2.0, "width": 10.0}, ...] 최대 2000점.
    반환: mean·std는 출력별로 점 순서대로 된 목록."""
    if len(points) > 2000:
        raise ValueError(f"한 번에 2000점까지 예측합니다({len(points)}점 받음) → 나눠서 호출하세요")
    return api.post(f"/api/models/{model_id}/predict_batch", {"points": points})


# ---------- 찾기 ----------
def _model_brief(m: dict) -> dict:
    q = m.get("quality") or {}
    return {"model_id": m["id"], "study_id": m["study_id"], "version": m.get("version"), "outputs": m["outputs"], "n_train": m["n_train"],
            "r2": {k: round(v["r2"], 4) for k, v in m["metrics"].items() if isinstance(v, dict) and v.get("r2") is not None},
            "quality": q.get("level"), "quality_label": q.get("label"), "warnings": q.get("warnings") or [], "deployed": m["deployed"],
            "simulator_path": f"/simulator/{m['id']}", "field_models": m.get("field_models")}


def _project_studies(pid: int, limit: int) -> list[dict]:
    models = api.get(f"/api/projects/{pid}/models")
    out = []
    for s in api.get(f"/api/projects/{pid}/studies")[:limit]:
        st = api.get(f"/api/studies/{s['id']}")
        mine = [m for m in models if m["study_id"] == s["id"]]  # 최신이 앞이다
        cfg = s.get("adapter_config") or {}
        out.append({"study_id": s["id"], "project_id": s["project_id"], "name": s["name"], "adapter": s["adapter"], "tool": cfg.get("tool"), "script": cfg.get("script"),
                    "counts": st["counts"], "running": st.get("running", False), "data_source": st.get("data_source"),
                    "inputs": [{k: v.get(k) for k in ("name", "lower", "upper", "unit")} for v in s["variables"]],
                    "outputs": [o["name"] for o in s.get("outputs") or []], "objectives": s["objectives"], "constraints": s.get("constraints") or [],
                    "flow_id": s.get("flow_id"), "has_recipe": s.get("has_recipe", False),
                    "latest_model_id": mine[0]["id"] if mine else None,
                    "deployed_model_id": next((m["id"] for m in mine if m["deployed"]), None)})
    return out


@mcp.tool()
def list_studies(project_id: int | None = None, all_projects: bool = False, limit: int = 30) -> dict:
    """프로젝트의 스터디 목록(최신이 앞): 입력·출력, 해석 방법, 상태별 케이스 수, 데이터 출처, 최신·배포 모델, 스크립트 묶음 유무.
    "지난 스터디에 10점 더", "지난번 모델로"처럼 id 없이 말할 때 먼저 부른다. 읽기만 하므로 승인이 필요 없다.
    all_projects=True면 내가 멤버인 모든 프로젝트에서 찾는다(다른 프로젝트 스터디 재사용: clone_study)."""
    limit = max(1, limit)
    if all_projects:
        out: list[dict] = []
        for p in api.get("/api/projects"):
            if len(out) >= limit:
                break
            out += [{**s, "project_name": p["name"]} for s in _project_studies(p["id"], limit - len(out))]
        return {"studies": out}
    pid = resolve_pid(project_id)
    return {"project_id": pid, "studies": _project_studies(pid, limit)}


@mcp.tool()
def list_models(project_id: int | None = None, study_id: int | None = None, limit: int = 30) -> dict:
    """프로젝트의 예측 모델 목록(최신이 앞): 버전, 출력, 학습 행 수, R²(예측이 실제를 얼마나 잘 따르는지), 품질과 경고, 배포 여부.
    deploy_model에 옛 버전을 주면 되돌리기다. 읽기만 하므로 승인이 필요 없다."""
    models = api.get(f"/api/projects/{resolve_pid(project_id)}/models")
    if study_id:
        models = [m for m in models if m["study_id"] == study_id]
    return {"models": [_model_brief(m) for m in models[:max(1, limit)]], "total": len(models)}


# ---------- 표로 시작 ----------
def _table_file(path: str, project_id: int | None) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = settings.project_dir(resolve_pid(project_id)) / p
    if p.is_dir():
        raise TableError(f"'{p}'은(는) 폴더입니다 → 해석 폴더라면 scan_results를 쓰고, 표라면 파일 경로를 주세요")
    if not p.is_file():
        raise TableError(f"표 파일을 찾지 못했습니다: {p} → 경로를 확인하세요. 대화 첨부 파일은 inputs/ 아래에 있습니다")
    if p.stat().st_size > MAX_TABLE_BYTES:
        raise TableError(f"표 파일은 {MAX_TABLE_BYTES // (1024 * 1024)}MB까지 받습니다: {p.name} → 필요한 행과 열만 남겨 주세요")
    return p


def _read_local(path: Path, outputs_path: Path | None, sheet: str | None, header_row: int | None, key: str | None, outputs: list[str] | None = None) -> tuple[Any, dict | None]:
    table = read_table(path, sheet=sheet, header_row=header_row)
    fill_guessing(table, outputs, has_output_file=outputs_path is not None)  # 서버 등록과 같게. 분석과 등록의 답이 다르면 AI가 잘못 말한다
    if outputs_path is None:
        return table, None
    return join_tables(table, read_table(outputs_path), key)


@mcp.tool()
def inspect_table(path: str, sheet: str | None = None, header_row: int | None = None, outputs_path: str | None = None, key: str | None = None,
                  project_id: int | None = None) -> dict:
    """표 파일(CSV·XLSX)을 이 PC에서 읽어 열을 분석한다. 아무것도 저장하지 않으므로 승인이 필요 없다.
    path: 절대 경로(네트워크 경로 포함) 또는 프로젝트 폴더 기준(첨부 파일은 inputs/이름). 행을 도구 인자로 옮기지 않는다.
    열마다 kind(numeric·constant·categorical·id·empty)와 role(input·output·fixed·exclude) 추측, 단위, 범위, 경고를 준다.
    출력이 다른 파일에 있으면 outputs_path와 잇는 열 key를 준다. 엑셀 시트는 sheet, 머리글 행은 header_row(1부터)로 정한다.
    대화에 붙여 넣은 표는 inputs/붙여넣은_표_*.tsv로 온다. 요청에 출력이 없으면 outputs 추측이 맞는지만 사용자에게 한 번 묻는다. 나머지는 묻지 않는다.
    '보안 프로그램으로 암호화되어 읽을 수 없습니다' 오류가 오면 추측하지 말고 엑셀에서 표를 복사해 입력창에 붙여 넣어 달라고 한다.
    추측이 맞으면 import_table을 그대로 부르고, 틀리면 outputs를 지정한다(나머지 숫자 열은 입력이 된다. 결과처럼 보이는 열이 입력에 들면 warnings로 돌아오니 사용자에게 알리고, 그 값도 결과라면 outputs에 넣어 다시 부른다)."""
    file = _table_file(path, project_id)
    other = _table_file(outputs_path, project_id) if outputs_path else None

    def work():
        table, join = _read_local(file, other, sheet, header_row, key)
        info = analyze(table)
        keep = ("name", "unit", "kind", "role", "role_reason", "min", "max", "n_numeric", "n_missing", "n_text", "n_nonfinite", "values", "value", "warnings")
        columns = [{k: c[k] for k in keep if k in c} for c in info["columns"]]
        out = {**{k: info[k] for k in ("source", "sheet", "sheets", "header_row", "skipped_rows", "n_rows", "inputs", "outputs", "fixed", "excluded", "warnings")},
               "columns": columns[:60], "columns_total": len(columns), "sample_rows": info["sample_rows"][:3]}
        if join:
            out["join"] = join
        return out

    return _in_background("표 분석", {"path": str(file), "other": str(other), "sheet": sheet, "header_row": header_row, "key": key}, work)


def _columns(spec: list[str | dict[str, Any]] | None) -> list[dict[str, Any]]:
    out = []
    for c in spec or []:
        out.append({"name": c} if isinstance(c, str) else dict(c))
    return out


def _complete_mapping(info: dict, inputs: list[str | dict] | None, outputs: list[str | dict] | None) -> tuple[list[dict], list[dict], list[str]]:
    """입력·출력 중 하나만 지정해도 나머지를 채운다. 지정하지 않은 열을 조용히 버리지 않게, 쓰지 않은 열 이름을 돌려준다."""
    by = {c["name"]: c for c in info["columns"]}
    ins, outs = _columns(inputs), _columns(outputs)
    unknown = [c.get("name") for c in [*ins, *outs] if c.get("name") not in by]
    if unknown:
        raise TableError(f"표에 없는 열입니다: {', '.join(map(str, unknown))} → 있는 열: {', '.join(by)}")
    used = {c["name"] for c in [*ins, *outs]}
    rest = [c for c in info["columns"] if c["name"] not in used]
    usable = [c for c in rest if c["kind"] == "numeric"]
    if inputs is None:
        # 출력만 고르면 나머지 숫자 열은 입력이다(화면 붙여넣기와 같은 규칙. 입구에 따라 다른 모델이 되면 안 된다)
        ins = [{"name": c["name"]} for c in usable]
    elif outputs is None:
        outs = [{"name": c["name"]} for c in usable if c.get("role") == "output"] or [{"name": c["name"]} for c in usable]
    used = {c["name"] for c in [*ins, *outs]}
    # 상수·글자 열은 넘겨서 등록 경로의 규칙(고정값 기록, 이유와 함께 제외)을 탄다
    ins += [{"name": c["name"]} for c in rest if c["kind"] in ("constant", "categorical") and c["name"] not in used]
    used |= {c["name"] for c in ins}
    return ins, outs, [c["name"] for c in rest if c["name"] not in used and c["kind"] == "numeric"]


def _import_summary(rep: dict) -> dict:
    """AI에게 줄 보고. case_ids처럼 행 수만큼 커지는 값은 빼고, 뺀 행의 이유는 앞부분만 준다."""
    keep = ("study_id", "study_name", "reused", "received", "registered", "updated", "filled_pending", "duplicates", "excluded_count", "excluded_by_reason",
            "partial_count", "fixed", "removed_columns", "ignored_columns", "range_changes", "variables", "outputs", "warnings", "join")
    out = {k: rep.get(k) for k in keep if rep.get(k) not in (None, [], {})}
    out["registered"] = rep.get("registered", 0)
    out["excluded_count"] = rep.get("excluded_count", 0)
    if rep.get("excluded"):
        out["excluded_examples"] = rep["excluded"][:10]
    out["next"] = "train_surrogate로 학습하고 deploy_model로 배포한다(해석을 돌리지 않으므로 승인 없이 바로). 뺀 행·고정값·경고는 사용자에게 알린다."
    return out


@mcp.tool()
def import_table(path: str, inputs: list[str | dict[str, Any]] | None = None, outputs: list[str | dict[str, Any]] | None = None,
                 objectives: list[dict[str, str]] | None = None, constraints: list[dict[str, Any]] | None = None, name: str = "",
                 sheet: str | None = None, header_row: int | None = None, outputs_path: str | None = None, key: str | None = None,
                 study_id: int | None = None, fixed: dict[str, Any] | None = None, expects_fields: bool = False,
                 flow_id: int | None = None, node_id: str | None = None, project_id: int | None = None) -> dict:
    """표 파일의 행을 해석 없이 케이스로 등록해 스터디를 만든다. 파일을 이 PC에서 직접 올리므로 행을 도구 인자로 옮기지 않는다.
    path: 절대 경로 또는 프로젝트 폴더 기준(첨부 파일은 inputs/이름). 승인 없이 바로 한다.
    inputs·outputs: 열 이름 또는 {"name","unit","note","lower","upper"} 목록. 둘 다 비우면 inspect_table의 추측대로, 하나만 주면 나머지를 채운다.
    objectives: [{"name": "mass", "goal": "min"}]  constraints: [{"name": "stress", "upper": 250}]  (출력 열에서 고른다)
    study_id를 주면 그 스터디에 행을 더한다(같은 행은 중복되지 않고, 범위 밖 값은 범위를 넓히고 알리며, 대기 중인 DOE 점과 입력이 같으면 그 점을 채운다).
      이때 outputs는 새로 더할 출력 열이다. 행을 더한 뒤 train_surrogate를 다시 부르면 새 모델 버전이 되고, 배포하면 공유 링크가 따라간다.
    같은 표를 두 번 불러도 스터디·케이스가 늘지 않는다(reused). 결과의 뺀 행(excluded)·고정값(fixed)·warnings는 사용자에게 그대로 알린다.
    status='running'이면 실패가 아니다. 같은 인자로 다시 호출하면 이어서 기다린다."""
    file = _table_file(path, project_id)
    other = _table_file(outputs_path, project_id) if outputs_path else None
    mapping: dict[str, Any] = {"name": name, "objectives": objectives or [], "constraints": constraints or [], "fixed": fixed or {}, "sheet": sheet,
                               "header_row": header_row, "key": key, "expects_fields": expects_fields, "flow_id": flow_id, "node_id": node_id}
    files = {"file": file, **({"outputs_file": other} if other else {})}

    def work():
        unused: list[str] = []
        looks_output: list[str] = []
        if study_id:
            mapping["outputs"] = _columns(outputs)
            url = f"/api/studies/{study_id}/cases/import/file"
        else:
            if inputs is not None or outputs is not None:
                table, _ = _read_local(file, other, sheet, header_row, key, [c["name"] for c in _columns(outputs)] if outputs is not None else None)
                info = analyze(table)
                mapping["inputs"], mapping["outputs"], unused = _complete_mapping(info, inputs, outputs)
                if inputs is None:  # 결과 열이 입력에 들면 결과로 결과를 예측하는 모델이 된다. 막지는 않되 사용자가 알게 한다
                    chosen = {c["name"] for c in mapping["inputs"]}
                    looks_output = [c["name"] for c in info["columns"] if c["kind"] == "numeric" and c.get("role") == "output" and c["name"] in chosen]
            url = f"/api/projects/{resolve_pid(project_id)}/studies/import/file"
        rep = api.upload_form(url, files, {"mapping": json.dumps(mapping, ensure_ascii=False)})
        out = _import_summary(rep)
        if unused:
            out["unused_columns"] = unused
            out.setdefault("warnings", []).append(f"지정하지 않아 쓰지 않은 열이 있습니다: {', '.join(unused[:10])} → 필요하면 입력·출력에 넣어 다시 불러오세요")
        if looks_output:
            out.setdefault("warnings", []).append(f"결과처럼 보이는 열을 입력으로 썼습니다: {', '.join(looks_output[:10])} → 이 값도 결과라면 outputs에 넣어 다시 불러오세요")
        return out

    return _in_background("표 등록", {"path": str(file), "other": str(other), "mapping": mapping, "inputs": inputs, "outputs": outputs, "study_id": study_id,
                                   "pid": project_id}, work)


@mcp.tool()
def export_doe(study_id: int, status: str | None = None, path: str | None = None) -> dict:
    """DOE 계획표를 CSV로 이 PC에 저장한다(기본: 프로젝트 폴더 reports/). 다른 팀이나 HPC에 넘길 때 쓴다.
    밖에서 해석한 뒤 출력 칸을 채워 import_table(study_id=…)로 되돌리면 그 DOE 점이 채워진다.
    status: pending·complete 등으로 거른다. 비우면 대기 케이스(없으면 전체). path: 저장할 곳(있는 파일을 덮어쓰지 않는다)."""
    st = api.get(f"/api/studies/{study_id}")
    s = st["study"]
    if path:
        dest = Path(path).expanduser()
        if not dest.is_absolute():
            dest = settings.project_dir(s["project_id"]) / dest
        if dest.exists():
            raise ValueError(f"이미 있는 파일입니다: {dest} → 다른 이름을 주세요. 사용자 파일을 덮어쓰지 않습니다")
    else:
        slug = re.sub(r'[\\/:*?"<>|\s]+', "_", s["name"])[:60]
        dest = settings.project_dir(s["project_id"]) / "reports" / f"S{study_id:03d}_{slug}_DOE_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    api.download(f"/api/studies/{study_id}/doe.csv" + (f"?status={status}" if status else ""), dest)
    rows = max(0, len(dest.read_text(encoding="utf-8-sig").splitlines()) - 1)
    return {"path": str(dest), "rows": rows, "study_id": study_id,
            "next": "출력 칸을 채운 파일을 import_table(path, study_id=…)로 되돌리면 그 점이 채워진다. 이 PC에서 돌리려면 set_study_adapter로 해석 방법을 연결한다."}


@mcp.tool()
def set_study_adapter(study_id: int, adapter: str, adapter_config: dict[str, Any] | None = None) -> dict:
    """해석 방법을 나중에 연결한다(불러온 데이터 스터디, DOE 계획만 있던 스터디). 대기 케이스는 그대로 두고 run_cases로 이어 간다. 불러온 행은 다시 돌리지 않는다.
    adapter='script'이면 adapter_config: {"tool": "abaqus_cae", "script": "scripts/plate.py", "inputs": ["inputs/plate.cae"], "post": {...}}.
    해석을 새로 돌리기 전에 Flow 승인을 받는다."""
    st = api.put(f"/api/studies/{study_id}/adapter", {"adapter": adapter, "adapter_config": adapter_config or {}})
    return {**_summary(st), "adapter_config": st["study"]["adapter_config"]}


# ---------- 기존 해석 폴더로 시작 ----------
@mcp.tool()
def scan_results(folder: str, max_depth: int = 3, project_id: int | None = None) -> dict:
    """이미 해석을 끝낸 폴더에서 케이스를 찾는다. 읽기만 하고 원본에 아무것도 쓰지 않으므로 승인이 필요 없다. 네트워크 경로도 된다. 프로젝트 작업 폴더로 바꾸지 않는다.
    케이스마다 params·results를 params.json·results.json·case.json·요약 CSV·폴더 이름(t=3.0_w=30)에서 읽어 ready(바로 등록 가능)를 표시한다.
    반환: cases_total·ready·not_ready, inputs·outputs(찾은 이름), file_kinds(종류별 파일 수), fields(3D 필드), cases(앞 50개),
    tables(폴더의 표 파일), extract(값이 없는 케이스에 쓸 추출 스킬·스크립트), truncated(다 못 봤으면 이유), failed(해석 실패 표시 폴더 수), encrypted(요약 CSV가 암호화되어 값을 못 읽은 폴더 수).
    케이스의 encrypted=true면 값이 없는 것이 아니라 못 읽은 것이다. 추측하지 말고 표를 복사해 붙여 넣어 달라고 한다.
    ready면 import_cases(folder=…). 결과 파일만 있으면 existing-data-to-simulator 스킬대로 값을 뽑아 import_cases(cases=[…])로 넣는다."""
    p = Path(folder).expanduser()
    if not p.is_absolute():
        p = settings.project_dir(resolve_pid(project_id)) / p
    limit = tool_wait_limit()
    time_limit = min(30.0, limit * 0.6) if limit else 30.0  # 느린 공유 폴더라도 도구 제한 안에 그때까지 본 것을 돌려준다
    return scan_brief(scan_folder(p, max_depth=max_depth, time_limit=time_limit))


def _abs_case_dir(run_dir: str | None, project_id: int | None) -> Path | None:
    if not run_dir:
        return None
    p = Path(run_dir).expanduser()
    return p if p.is_absolute() else settings.project_dir(resolve_pid(project_id)) / p


def _upload_fields(cases: list[dict], rep: dict) -> dict:
    by_row = {x["row"]: x["case_id"] for x in rep.get("cases") or []}
    done, autos, errors = 0, set(), []
    for i, c in enumerate(cases, 1):
        cid = by_row.get(i)
        if cid is None or not c.get("_dir"):
            continue
        for f in sorted((Path(c["_dir"]) / "fields").glob("*.npz")):
            try:
                r = api.upload(f"/api/cases/{cid}/fields", f)
            except ServerError as e:
                errors.append({"case_id": cid, "file": f.name, "error": str(e)[:300]})
                continue
            done += 1
            if r.get("auto_output"):
                autos.add(r["auto_output"])
    out: dict[str, Any] = {}
    if done:
        out["fields_uploaded"] = done
    if autos:
        out["auto_outputs"] = sorted(autos)
    if errors:
        out["field_errors"] = errors[:10]
    return out


@mcp.tool()
def import_cases(cases: list[dict[str, Any]] | None = None, folder: str | None = None, inputs: list[str | dict[str, Any]] | None = None,
                 outputs: list[str | dict[str, Any]] | None = None, objectives: list[dict[str, str]] | None = None, constraints: list[dict[str, Any]] | None = None,
                 name: str = "", study_id: int | None = None, max_depth: int = 3, upload_fields: bool = True, flow_id: int | None = None, node_id: str | None = None,
                 project_id: int | None = None) -> dict:
    """이미 해석한 케이스를 해석 없이 등록한다. 승인 없이 바로 한다. 원본 폴더는 경로만 기록하고 쓰지 않는다.
    folder: scan_results로 찾은 ready 케이스를 그대로 등록한다. 또는 cases: [{"params": {...}, "results": {...}, "run_dir": "원본 폴더(선택)", "label": "이름(선택)"}].
    inputs·outputs: 쓸 이름. 비우면 찾은 값 전부. study_id를 주면 그 스터디에 더한다. 같은 케이스를 두 번 넣어도 늘지 않는다.
    케이스 폴더의 fields/*.npz(points·values)는 함께 올린다. 스칼라 출력이 없으면 필드 최대값이 출력이 되어 Simulator가 성립한다. 그다음 train_surrogate·train_field_model.
    나중에 Output을 추가하면 추출 스크립트는 작업 폴더에서 돌고 원본은 {source_dir}로 찾는다. 원본이 있는 PC에서만 된다.
    status='running'이면 실패가 아니다. 같은 인자로 다시 호출하면 이어서 기다린다."""
    if bool(cases) == bool(folder):
        raise ValueError("cases와 folder 중 하나만 주세요 → 폴더를 먼저 보려면 scan_results를 쓰세요")
    in_names = {c["name"] for c in _columns(inputs)}
    out_names = {c["name"] for c in _columns(outputs)}

    def work():
        skipped: list[dict] = []
        failed = 0
        echoed: set[str] = set()
        source = ""
        rows_from: list[dict] = []
        if folder:
            p = Path(folder).expanduser()
            if not p.is_absolute():
                p = settings.project_dir(resolve_pid(project_id)) / p
            scan = scan_folder(p, max_depth=max_depth)
            source = p.name or str(p)
            rows_from = [{"params": c["params"], "results": c["results"], "run_dir": c["folder"], "label": c["label"]} for c in scan["cases"] if c["ready"]]
            skipped = [{"label": c["label"], "missing": c.get("missing")} for c in scan["cases"] if not c["ready"]]
            failed = scan.get("failed") or 0
            if not rows_from:
                hint = ", ".join(f"{e['kind']} {e['cases']}건: {e['skill']}" for e in scan["extract"])
                raise ValueError(f"바로 등록할 수 있는 케이스가 없습니다(찾은 폴더 {scan['cases_total']}개). 입력값과 결과값이 함께 있어야 합니다"
                                 f"{' — 값을 뽑을 스킬: ' + hint if hint else ''} → 결과 파일에서 값을 뽑아 cases로 넣거나, 입력·출력 표를 주세요")
        else:
            for c in cases:
                if not isinstance(c, dict) or not isinstance(c.get("params"), dict):
                    raise ValueError('cases 항목은 {"params": {...}, "results": {...}, "run_dir": "..."} 모양이어야 합니다 → params를 넣어 주세요')
            rows_from = list(cases)
        rows, prepared = [], []
        for i, c in enumerate(rows_from, 1):
            params, results = dict(c.get("params") or {}), dict(c.get("results") or {})
            for k in sorted(params.keys() & results.keys()):
                # results.json에 입력값을 함께 적어 두는 스크립트가 흔하다. 원본 폴더는 고칠 수 없으니 여기서 어느 쪽인지 정한다
                a, b = cell_state(params[k])[0], cell_state(results[k])[0]
                if k in out_names and k not in in_names:
                    params.pop(k)
                elif k in in_names or (a is not None and a == b):
                    results.pop(k)
                    echoed.add(k)
                else:
                    raise ValueError(f"'{k}' 값이 입력({params[k]})과 출력({results[k]})에서 다릅니다 → inputs·outputs로 어느 쪽인지 지정해 주세요. 원본 폴더는 고치지 않습니다")
            c["params"], c["results"] = params, results
            d = _abs_case_dir(c.get("run_dir"), project_id)
            row: dict[str, Any] = {**params, **results, "_row": i}
            if d:
                row["_run_dir"] = str(d)
            if c.get("label"):
                row["_label"] = str(c["label"])
            rows.append(row)
            prepared.append({"_dir": d})
        has_fields = upload_fields and any(d["_dir"] and any((Path(d["_dir"]) / "fields").glob("*.npz")) for d in prepared)
        ins = _columns(inputs) if inputs is not None else [{"name": n} for n in dict.fromkeys(k for c in rows_from for k in (c.get("params") or {}))]
        outs = _columns(outputs) if outputs is not None else [{"name": n} for n in dict.fromkeys(k for c in rows_from for k in (c.get("results") or {}))]
        common = {"rows": rows, "source": source, "expects_fields": bool(has_fields), "flow_id": flow_id, "node_id": node_id}
        if study_id:
            rep = api.post(f"/api/studies/{study_id}/cases/import", {**common, "outputs": outs}, timeout=600)
        else:
            rep = api.post(f"/api/projects/{resolve_pid(project_id)}/studies/import",
                           {**common, "name": name, "kind": "folders" if folder or any(c.get("run_dir") for c in rows_from) else "table", "inputs": ins, "outputs": outs,
                            "objectives": objectives or [], "constraints": constraints or []}, timeout=600)
        out = _import_summary(rep)
        if has_fields:
            out.update(_upload_fields(prepared, rep))
        if skipped:
            out["skipped_folders"] = skipped[:20]
            out["skipped_count"] = len(skipped)
        if failed:
            out.setdefault("warnings", []).append(f"해석 실패 표시가 있는 폴더 {failed}개는 학습에 넣지 않았습니다 → 다시 해석했다면 그 폴더의 실패 표시를 확인하세요")
        if echoed:
            out.setdefault("warnings", []).append(f"결과 파일에 입력값이 함께 적혀 있어 입력으로만 썼습니다: {', '.join(sorted(echoed)[:10])}")
        return out

    return _in_background("케이스 등록", {"cases": cases, "folder": folder, "inputs": inputs, "outputs": outputs, "name": name, "study_id": study_id, "pid": project_id}, work)


@mcp.tool()
def upload_field(case_id: int, path: str, field: str | None = None, project_id: int | None = None) -> dict:
    """케이스 하나에 3D 필드 파일(npz: points(N,3), values(N,) 또는 (N,3), triangles(선택))을 올린다. 해석 결과 폴더에 fields/가 있으면 import_cases가 알아서 올리므로,
    다른 곳에 있는 필드에만 쓴다. field: 필드 이름(영문·숫자·_-, 비우면 파일 이름). 스칼라 출력이 없는 스터디는 필드 최대값이 출력으로 생긴다(auto_output)."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = settings.project_dir(resolve_pid(project_id)) / p
    if not p.is_file():
        raise ValueError(f"필드 파일을 찾지 못했습니다: {p} → 경로를 확인하세요")
    return api.upload_form(f"/api/cases/{case_id}/fields", {"file": (f"{field or p.stem}.npz", p.read_bytes())})


# ---------- 지난번처럼 다시, 다른 멤버가 이어받기 ----------
@mcp.tool()
def clone_study(study_id: int, name: str = "", variables: list[dict[str, Any]] | None = None, objectives: list[dict[str, str]] | None = None,
                constraints: list[dict[str, Any]] | None = None, outputs: list[dict[str, str]] | None = None, adapter: str | None = None,
                adapter_config: dict[str, Any] | None = None, project_id: int | None = None, model_files: list[str] | None = None) -> dict:
    """지난번과 같은 설정으로 새 스터디를 만든다(신규 모델 출시 때 같은 흐름 다시). 변수·범위·출력·목적·제약·해석 방법·스크립트 묶음을 복사하고 바꿀 값만 준다.
    케이스와 모델은 복사하지 않아 옛 스터디·모델·Simulator는 그대로다. project_id를 주면 그 프로젝트로 만든다(양쪽 멤버여야 한다).
    새 모델을 inspect해 옛 모델과 달라진 점을 먼저 알린 뒤 부르고, 해석을 돌리기 전에 Flow 승인을 받는다.
    model_files: 새 모델 파일(프로젝트 폴더 기준, 예: inputs/new.cae). adapter_config.inputs의 옛 모델을 바꾼다. 주지 않으면 옛 모델로 돈다.
    스크립트가 모델 파일 이름을 고정해 두었으면 스크립트도 고쳐 scripts/에 새로 두고 adapter_config.script를 바꾼다."""
    if model_files:
        old = api.get(f"/api/studies/{study_id}")["study"]
        adapter_config = _with_new_models(adapter_config if adapter_config is not None else old.get("adapter_config") or {}, model_files)
    fields = {"name": name, "project_id": project_id, "variables": variables, "objectives": objectives, "constraints": constraints, "outputs": outputs,
              "adapter": adapter, "adapter_config": adapter_config}
    s = api.post(f"/api/studies/{study_id}/clone", {k: v for k, v in fields.items() if v not in (None, "")})
    out = {"study_id": s["id"], "project_id": s["project_id"], "name": s["name"], "adapter": s["adapter"], "adapter_config": s["adapter_config"],
           "variables": s["variables"], "outputs": s.get("outputs") or [], "objectives": s["objectives"], "constraints": s.get("constraints") or [],
           "cloned_from": study_id, "has_recipe": s.get("has_recipe", False)}
    inputs = (s.get("adapter_config") or {}).get("inputs")
    if s["adapter"] == "script" and inputs and not model_files and adapter_config is None:
        # 설정을 그대로 복사하면 새 모델이라고 말하면서 옛 모델로 돈다. 결과만 봐서는 알 수 없다
        out["warning"] = f"입력 모델이 지난번과 같습니다: {', '.join(map(str, inputs))} → 새 모델로 돌리려면 model_files로 새 파일을 주세요"
    return out


def _with_new_models(config: dict, model_files: list[str]) -> dict:
    """adapter_config.inputs에서 같은 종류(확장자)의 옛 모델을 새 파일로 바꾼다. 종류가 맞는 것이 없으면 옛 입력이 하나일 때 그것을 바꾸고, 아니면 더한다."""
    inputs = [str(x) for x in config.get("inputs") or []]
    for new in model_files:
        ext = Path(new).suffix.lower()
        same = [x for x in inputs if Path(x).suffix.lower() == ext]
        if same:
            inputs = [new if x == same[0] else x for x in inputs]
        elif len(inputs) == 1:
            inputs = [new]
        else:
            inputs.append(new)
    return {**config, "inputs": list(dict.fromkeys(inputs))}


@mcp.tool()
def save_recipe(study_id: int, files: list[str] | None = None, note: str = "") -> dict:
    """스터디의 스크립트를 서버에 묶어 둔다. 스크립트는 이 PC에만 있어서 올려 두지 않으면 다른 멤버 PC에서 "스크립트가 없습니다"로 막힌다.
    adapter_config의 script·post 스크립트를 자동으로 넣고, 그 스크립트가 쓰는 파일이 더 있으면 files(프로젝트 폴더 기준 파일·폴더)로 더한다.
    해석 결과와 모델 파일은 넣지 않는다. 비밀값이 든 파일과 실행 파일은 서버가 거절한다.
    케이스가 처음 끝나면 PC Agent가 script·post 스크립트를 자동으로 올린다. 이 도구는 그 스크립트가 쓰는 파일을 더하거나, 해석을 돌리기 전에 미리 올릴 때 쓴다."""
    s = api.get(f"/api/studies/{study_id}")["study"]
    root = settings.project_dir(s["project_id"])
    wanted = config_scripts(s.get("adapter_config") or {}) + list(files or [])
    if not wanted:
        raise ValueError("묶을 스크립트가 없습니다 → 이 스터디에 script 해석 방법이 없으면 files로 스크립트를 지정하세요")
    bundle, _ = build_bundle(root, wanted)
    v = api.upload_form(f"/api/studies/{study_id}/recipe", {"file": ("recipe.zip", bundle)}, {"note": note[:500]})
    return {k: v.get(k) for k in ("study_id", "version", "created_by_name", "size", "warnings")} | {"files": [f["path"] for f in v.get("files") or []]}


@mcp.tool()
def restore_recipe(study_id: int, overwrite: bool = False) -> dict:
    """서버에 둔 스크립트 묶음을 이 PC 프로젝트 폴더로 받는다. 받기만 하고 실행하지 않으므로 승인 없이 부를 수 있다.
    다른 멤버 PC에서 스크립트가 없을 때 쓴다. 이미 있고 내용이 다른 파일은 덮어쓰지 않고 conflicts로 알린다(overwrite=True면 덮어쓴다).
    만든 사람(created_by_name)이 내가 아니면 그 스크립트를 돌리기 전에 Flow 승인 카드에 "○○님이 만든 스크립트"라고 적어 승인을 받는다.
    입력 모델은 묶음에 없다. input_files_missing에 있는 파일은 사용자에게 첨부를 부탁한다."""
    s = api.get(f"/api/studies/{study_id}")["study"]
    info = api.get(f"/api/studies/{study_id}/recipe")
    if not info.get("has"):
        raise ValueError("이 스터디의 스크립트 묶음이 서버에 없습니다. 스크립트는 그 PC에서 해석이 끝날 때 자동으로 올라가는데 그 전에 돌린 스터디입니다 "
                         "→ 스크립트를 만든 멤버의 PC가 켜져 있을 때 묶음을 올려 달라고 하세요. 결과 조회·재학습·Simulator는 그 PC 없이 됩니다")
    root = settings.project_dir(s["project_id"])
    tmp = Path(tempfile.mkdtemp(prefix="dds_recipe_"))
    try:
        blob = tmp / "recipe.zip"
        digest = api.download(f"/api/studies/{study_id}/recipe/file", blob)
        if digest != info.get("sha256"):
            raise ValueError("받은 묶음이 서버 기록과 다릅니다 → 다시 받거나 만든 멤버에게 다시 올려 달라고 하세요")
        expected = {f["path"]: f for f in info.get("files") or []}
        members: list[tuple[str, Path, bytes]] = []
        with zipfile.ZipFile(blob) as zf:  # 전부 검사한 뒤에 쓴다. 중간에 멈춰 일부만 받은 상태를 남기지 않는다
            for item in zf.infolist():
                if item.is_dir() or item.filename == "recipe.json":
                    continue
                if item.filename not in expected:
                    raise ValueError(f"서버 기록에 없는 파일이 묶음에 있습니다: {item.filename[:80]} → 만든 멤버에게 다시 올려 달라고 하세요")
                target = inside(root, member_path(item.filename))
                body = zf.read(item)
                if hashlib.sha256(body).hexdigest() != expected[item.filename]["sha256"]:
                    raise ValueError(f"파일 내용이 서버 기록과 다릅니다: {item.filename[:80]} → 만든 멤버에게 다시 올려 달라고 하세요")
                members.append((item.filename, target, body))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    restored, unchanged, conflicts = [], [], []
    for name, target, body in members:
        if target.exists():
            if target.is_file() and target.read_bytes() == body:
                unchanged.append(name)
                continue
            if not overwrite:
                conflicts.append(name)
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(f"{target.name}.{uuid.uuid4().hex[:8]}.part")
        part.write_bytes(body)
        part.replace(target)
        restored.append(name)
    config = s.get("adapter_config") or info.get("adapter_config") or {}  # 복제한 스터디는 새 모델을 가리킨다. 묶음의 옛 설정으로 안내하면 옛 모델을 첨부하라고 한다
    missing = [str(r) for r in config.get("inputs") or [] if not (root / str(r)).exists()]
    out = {"study_id": study_id, "version": info.get("version"), "created_by_name": info.get("created_by_name"), "note": info.get("note"), "folder": str(root),
           "restored": restored, "unchanged": unchanged, "conflicts": conflicts, "input_files_missing": missing, "adapter_config": config,
           "before_running": f"{info.get('created_by_name') or '다른 멤버'}님이 만든 스크립트입니다. 돌리기 전에 Flow 승인 카드에 만든 사람을 적어 승인을 받습니다."}
    if info.get("warnings"):
        out["warnings"] = info["warnings"]
    return out


if __name__ == "__main__":
    mcp.run()
