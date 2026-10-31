"""dds-study MCP: 설계 탐색 스터디(DOE → 케이스 실행 → Surrogate 학습 → 베이지안 최적화/능동학습 → Simulator 배포).
계산(학습·BO)은 서버에서, 케이스 실행은 이 PC Agent(또는 HPC)에서 이뤄진다."""
import time
from typing import Any, Literal

from _common import api, resolve_pid, tool_wait_limit
from mcp.server import MCPServer

mcp = MCPServer(
    "dds-study",
    instructions=(
        "설계변수·목적함수를 정해 create_study → generate_doe → run_cases → train_surrogate 순으로 진행한다. "
        "최적화는 optimize(mode='bo'), 학습 효율화는 optimize(mode='al')를 쓴다. 각 단계 결과는 웹앱에 자동으로 게시된다. "
        "사용자가 출력(Output) 항목을 더 원하면 add_outputs로 추가한다(이미 해석한 케이스에서도 추출된다)."
    ),
)


def _summary(st: dict) -> dict:
    s = st["study"]
    best = st.get("best")
    return {"study_id": s["id"], "name": s["name"], "adapter": s["adapter"], "counts": st["counts"],
            "running": st.get("running", False),
            "best": {"case_id": best["id"], "params": best["params"], "results": best["results"]} if best else None}


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
          붙이기 전에 dds-cae.run_script로 한 케이스의 결과 파일(inputs=["studies/S001_…/cases/C0001_doe/model.odb"])에서 먼저 시험한다.
    mock 어댑터는 post 없이 해석함수에서 다시 계산한다.
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
def run_cases(study_id: int, case_ids: list[int] | None = None, dry_run: bool = False, wait_sec: int = 480) -> dict:
    """대기 케이스(또는 지정한 케이스)를 이 PC Agent에서 실행하고 최대 wait_sec 동안 기다린다.
    아직 running이면 get_study로 다시 확인한다. dry_run=True면 스크립트·명령만 만들고 실행하지 않는다."""
    r = api.post(f"/api/studies/{study_id}/run", {"case_ids": case_ids, "dry_run": dry_run})
    out = _summary(_wait(study_id, wait_sec))
    out["queued"] = r["queued"]
    failed = [c for c in api.get(f"/api/studies/{study_id}/cases") if c["status"] == "failed"][-3:]
    if failed:
        out["recent_failures"] = [{"case_id": c["id"], "error": (c["error"] or "")[-800:]} for c in failed]
    return out


@mcp.tool()
def stop_cases(study_id: int, case_ids: list[int] | None = None) -> dict:
    """대기·실행 중인 케이스를 중지한다(case_ids를 주지 않으면 전체). 실행 중인 솔버는 끝나고 결과가 남지 않으며, 대기 케이스는 시작하지 않는다.
    케이스는 'cancelled'가 되고, 다시 돌리려면 run_cases에 case_ids로 지정한다. 사용자가 멈춰 달라고 할 때만 쓴다."""
    return api.post(f"/api/studies/{study_id}/stop", {"case_ids": case_ids})


@mcp.tool()
def get_study(study_id: int, include_cases: bool = False) -> dict:
    """스터디 진행 상황(상태별 케이스 수, 실행 중 여부, 현재 최적 케이스).
    include_cases=True면 케이스별 params·results·status도 준다(제약조건을 만족하는 최적 설계를 직접 고를 때)."""
    out = _summary(api.get(f"/api/studies/{study_id}"))
    if include_cases:
        out["cases"] = [{"case_id": c["id"], "status": c["status"], "params": c["params"], "results": c.get("results"),
                         "data": {k: (c.get("archive") or {}).get(k) for k in ("stored", "size", "field_names", "ok")} if c.get("archive") else None}
                        for c in api.get(f"/api/studies/{study_id}/cases")]
    return out


@mcp.tool()
def case_data(case_id: int) -> dict:
    """케이스 해석 데이터(case.h5, HDF5)에 무엇이 담겼는지: 설계변수·결과, 메쉬(파트·노드·요소 수), 필드(스텝·프레임·성분·단위),
    시간 이력, 함께 담긴 파일, PC에 원본으로만 남은 결과 파일(raw), 검증 결과(checks). stored=server면 서버에도 있다.
    Output을 추가할 때 원본 결과 파일이 없으면 case.h5에서 뽑는 추출 스크립트(cae_adapters.h5store)를 쓴다(docs/cae-data-hdf5.md)."""
    s = api.get(f"/api/cases/{case_id}/archive")
    if s.get("files") and len(s["files"]) > 40:
        s["files"] = s["files"][:40] + [f"... {len(s['files']) - 40}개 더"]
    return s


@mcp.tool()
def train_surrogate(study_id: int, outputs: list[str] | None = None, flow_id: int | None = None,
                    node_id: str | None = None) -> dict:
    """완료된 케이스로 Gaussian Process Surrogate를 학습한다. 교차검증 R²/RMSE와 예측 정확도 그림이 게시된다."""
    m = api.post(f"/api/studies/{study_id}/train", {"outputs": outputs, "flow_id": flow_id, "node_id": node_id})
    return {"model_id": m["id"], "n_train": m["n_train"], "metrics": m["metrics"]}


@mcp.tool()
def suggest_next(study_id: int, mode: Literal["bo", "al"] = "bo", n: int = 1, flow_id: int | None = None,
                 node_id: str | None = None) -> dict:
    """최신 Surrogate로 다음 케이스를 제안한다. bo=기대개선(EI) 최적화, al=불확실도 최대(능동학습)."""
    cases = api.post(f"/api/studies/{study_id}/suggest", {"mode": mode, "n": n, "flow_id": flow_id, "node_id": node_id})
    return {"case_ids": [c["id"] for c in cases], "params": [c["params"] for c in cases]}


@mcp.tool()
def optimize(study_id: int, iterations: int = 10, mode: Literal["bo", "al"] = "bo", batch: int = 1,
             flow_id: int | None = None, node_id: str | None = None, wait_sec: int = 480) -> dict:
    """제안 → 실행 → 재학습을 iterations번 반복한다. bo는 목적함수 최적화, al은 Surrogate 정확도 향상.
    한 번 호출이 오래 걸리면 iterations를 나눠 여러 번 호출한다. 수렴 그래프가 게시된다."""
    history = []
    t0, limit = time.time(), tool_wait_limit()
    for i in range(iterations):
        if i and limit and time.time() - t0 > limit * 0.5:  # 반복 전체가 CLI의 도구 시간 제한을 넘지 않게 여기까지만 하고 돌려준다
            return {"history": history, "stopped_at": i, "model_id": m["id"],
                    "reason": "실패가 아닙니다. 한 번에 도는 시간을 넘겨 여기까지 했습니다. 남은 반복을 iterations로 나눠 다시 호출하세요.",
                    **_summary(api.get(f"/api/studies/{study_id}"))}
        api.post(f"/api/studies/{study_id}/suggest", {"mode": mode, "n": batch, "flow_id": flow_id, "node_id": node_id})
        api.post(f"/api/studies/{study_id}/run", {})
        st = _wait(study_id, wait_sec)
        if st.get("running"):
            return {"stopped_at": i, "reason": "케이스가 아직 실행 중입니다. get_study로 확인 후 다시 호출하세요.",
                    **_summary(st)}
        m = api.post(f"/api/studies/{study_id}/train", {"flow_id": flow_id, "node_id": node_id})
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
def predict(model_id: int, params: dict[str, float]) -> dict:
    """Surrogate로 예측(평균, 표준편차)."""
    return api.post(f"/api/models/{model_id}/predict", {"params": params})


if __name__ == "__main__":
    mcp.run()
