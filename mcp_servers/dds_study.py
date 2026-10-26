"""dds-study MCP: 설계 탐색 스터디(DOE → 케이스 실행 → Surrogate 학습 → 베이지안 최적화/능동학습 → Simulator 배포).
계산(학습·BO)은 서버에서, 케이스 실행은 이 PC Agent(또는 HPC)에서 이뤄진다."""
import time
from typing import Any, Literal

from _common import api, resolve_pid
from mcp.server import MCPServer

mcp = MCPServer(
    "dds-study",
    instructions=(
        "설계변수·목적함수를 정해 create_study → generate_doe → run_cases → train_surrogate 순으로 진행한다. "
        "최적화는 optimize(mode='bo'), 학습 효율화는 optimize(mode='al')를 쓴다. 각 단계 결과는 웹앱에 자동으로 게시된다."
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
                 project_id: int | None = None) -> dict:
    """스터디를 만든다.
    variables: [{"name": "thickness", "lower": 1.0, "upper": 5.0}]
    objectives: [{"name": "mass", "goal": "min"}]  (첫 번째가 최적화 대상, 이름은 results.json 키)
    adapter='script' 이면 adapter_config: {"tool": "abaqus_cae", "script": "scripts/plate.py", "inputs": ["models/plate.cae"], "post": {...}}
    adapter='mock' 이면 adapter_config: {"function": "branin" | "rosenbrock" | "beam"}
    """
    pid = resolve_pid(project_id)
    s = api.post(f"/api/projects/{pid}/studies", {"name": name, "variables": variables, "objectives": objectives or [],
                                                  "adapter": adapter, "adapter_config": adapter_config or {}})
    return {"study_id": s["id"]}


@mcp.tool()
def generate_doe(study_id: int, n: int = 20, method: Literal["lhs", "sobol", "random"] = "lhs", seed: int | None = None,
                 flow_id: int | None = None, node_id: str | None = None) -> dict:
    """DOE 샘플을 만들어 실행 대기(pending) 케이스로 등록한다. 산점도·케이스 표가 웹앱에 게시된다."""
    cases = api.post(f"/api/studies/{study_id}/doe",
                     {"method": method, "n": n, "seed": seed, "flow_id": flow_id, "node_id": node_id})
    return {"created": len(cases), "case_ids": [c["id"] for c in cases]}


def _wait(study_id: int, wait_sec: int) -> dict:
    t0 = time.time()
    while True:
        st = api.get(f"/api/studies/{study_id}")
        if not st.get("running") or time.time() - t0 > wait_sec:
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
def get_study(study_id: int) -> dict:
    """스터디 진행 상황(상태별 케이스 수, 실행 중 여부, 현재 최적 케이스)."""
    return _summary(api.get(f"/api/studies/{study_id}"))


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
    for i in range(iterations):
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
def deploy_model(model_id: int) -> dict:
    """Surrogate 모델을 예측 Simulator로 배포한다. 웹앱 Simulator 화면에서 슬라이더로 바로 예측할 수 있다."""
    m = api.post(f"/api/models/{model_id}/deploy")
    return {"model_id": m["id"], "deployed": m["deployed"], "simulator_path": f"/simulator/{m['id']}"}


@mcp.tool()
def predict(model_id: int, params: dict[str, float]) -> dict:
    """Surrogate로 예측(평균, 표준편차)."""
    return api.post(f"/api/models/{model_id}/predict", {"params": params})


if __name__ == "__main__":
    mcp.run()
