"""판단 도우미 API: 권한, 캐시, 해석을 더 돌리지 않음, 다목적 제안, 공유 한 줄, 보고서 자료."""
import time

import numpy as np
import pytest
from scipy.stats import qmc

from server.tests.test_api import _auth, _wait_idle, client  # noqa: F401  서버 코드보다 먼저 불러 임시 폴더를 쓴다

from server.app.study import insights  # noqa: E402
from server.app.study.surrogate import Surrogate  # noqa: E402

insights.BENCH_ENABLED = False


def _ready(client, sid, headers, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = client.get(f"/api/studies/{sid}/insights", headers=headers).json()
        if v["status"] == "ready":
            return v
        time.sleep(0.2)
    raise TimeoutError


def _section(v, key):
    return next(x for x in v["sections"] if x["key"] == key)


def _washer_study(client, headers, n=24, objectives=None, outputs=("vibration", "floor_force")):
    pid = client.post("/api/projects", json={"name": "세탁기"}, headers=headers).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", headers=headers, json={
        "name": "탈수", "adapter": "mock", "adapter_config": {"function": "washer", "outputs": list(outputs)},
        "variables": [{"name": "spring_k", "lower": -10, "upper": 30, "note": "스프링 강성 [%]"}, {"name": "damper_c", "lower": -10, "upper": 50, "note": "댐퍼 감쇠력 [%]"},
                      {"name": "balancer_m", "lower": -20, "upper": 20}, {"name": "spring_pos", "lower": -20, "upper": 20}],
        "objectives": objectives or [{"name": "vibration", "goal": "min"}, {"name": "floor_force", "goal": "min"}],
        "outputs": [{"name": "vibration", "note": "진동 [mm]"}, {"name": "floor_force", "note": "바닥 전달력 [N]"}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"method": "lhs", "n": n, "seed": 3}, headers=headers)
    return pid, sid


def _run(client, sid, headers):
    client.post(f"/api/studies/{sid}/run", json={}, headers=headers)
    return _wait_idle(client, sid, headers, timeout=120)


def test_permissions_and_missing_study(client):
    admin, _ = _auth(client, "admin")
    kim, _ = _auth(client, "kim_ins", admin)
    pid, sid = _washer_study(client, admin, n=6)
    assert client.get("/api/studies/999999/insights", headers=admin).status_code == 404
    assert client.get(f"/api/studies/{sid}/insights", headers=kim).status_code == 403
    assert client.get(f"/api/studies/{sid}/insights/pareto?a=vibration&b=floor_force", headers=kim).status_code == 403
    assert client.post(f"/api/studies/{sid}/insights/publish", json={}, headers=kim).status_code == 403


def test_before_solving_shows_only_design_range_with_next_step(client):
    admin, _ = _auth(client, "admin")
    _, sid = _washer_study(client, admin, n=32)
    v = _ready(client, sid, admin)
    assert [x["key"] for x in v["sections"]] == ["coverage"]
    cov = v["sections"][0]
    assert cov["title"] == "설계 범위를 고르게 살폈나요?" and cov["term"] == "공간 충진성"
    assert cov["state_label"] in ("고르게 퍼짐", "조금 부족")
    assert v["overview"]["next"]["key"] == "run"


def test_full_flow_multi_objective_and_active_learning_no_extra_solver_runs(client, monkeypatch):
    admin, _ = _auth(client, "admin")
    pid, sid = _washer_study(client, admin, n=24)
    st = _run(client, sid, admin)
    assert st["counts"]["complete"] == 24
    v = _ready(client, sid, admin)
    assert _section(v, "quality")["state"] == "good"
    assert _section(v, "pareto")["state"] != "caution"
    assert v["overview"]["next"]["key"] == "train"
    assert _section(v, "trust")["next"]["label"] == "학습 요청"

    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    # 능동학습 6건 → 재학습
    for _ in range(6):
        client.post(f"/api/studies/{sid}/suggest", json={"mode": "al", "n": 1}, headers=admin)
        _run(client, sid, admin)
    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)

    # 다목적 최적화: 두 목표를 함께 보는 점을 제안한다
    for _ in range(6):
        made = client.post(f"/api/studies/{sid}/suggest", json={"mode": "bo", "n": 1}, headers=admin)
        assert made.status_code == 200, made.text
        assert made.json()[0]["source"] == "bo"
        _run(client, sid, admin)
    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)

    # 판단 호출은 해석을 돌리지 않는다
    before = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    calls = insights._compute_calls
    v = _ready(client, sid, admin)
    assert insights._compute_calls == calls + 1
    after = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert [(c["id"], c["status"]) for c in before] == [(c["id"], c["status"]) for c in after]

    # 같은 상태면 다시 계산하지 않는다
    v2 = client.get(f"/api/studies/{sid}/insights", headers=admin).json()
    assert v2["status"] == "ready" and insights._compute_calls == calls + 1
    assert v2["state_key"] == v["state_key"]

    keys = [x["key"] for x in v["sections"]]
    assert keys == ["coverage", "quality", "influence", "trust", "convergence", "optimum", "pareto", "active"]
    assert all(x["sentence"] and x["state_label"] for x in v["sections"])
    pareto = _section(v, "pareto")
    assert pareto["facts"]["front"] >= 3 and pareto["facts"]["conflict"]
    assert "늘어납니다" in pareto["sentence"] or "줄어듭니다" in pareto["sentence"]
    assert pareto["charts"][0]["kind"] == "pareto"
    assert pareto["charts"][0]["predicted"], "모델 정확도가 충분하면 예측 후보가 있어야 합니다"
    assert any(p for p in pareto["notes"] if "제안" in p)
    active = _section(v, "active")
    assert active["facts"]["al"] == 6 and "더 돌린 6건" in active["sentence"]
    assert active["facts"]["compared_with_random"] is False
    infl = _section(v, "influence")
    assert infl["basis"] == "predicted" and infl["basis_label"] == "AI 예측값"
    assert [q["name"] for q in infl["variants"]][:2] == ["vibration", "floor_force"]
    assert infl["charts"][0]["bars"][0]["name"] in ("spring_k", "damper_c")
    assert "스프링 강성" in infl["sentence"] or "댐퍼 감쇠력" in infl["sentence"], infl["sentence"]
    trust = _section(v, "trust")
    assert any(c["kind"] == "prior" for c in trust["charts"]) or trust["facts"].get("prior_count") is None
    assert "10번 중 8번" in trust["sentence"]
    assert _section(v, "convergence")["facts"].get("multi")

    # 요약 조회에는 그림 자료가 없고 문장은 같다
    s = client.get(f"/api/studies/{sid}/insights?detail=summary", headers=admin).json()
    assert all(not x.get("charts") for x in s["sections"])
    assert _section(s, "pareto")["sentence"] == pareto["sentence"]

    # 고른 두 출력 조회와 이름 오류
    r = client.get(f"/api/studies/{sid}/insights/pareto?a=vibration&b=spring_k", headers=admin)
    assert r.status_code == 400 and "spring_k" in r.json()["detail"]
    assert client.get(f"/api/studies/{sid}/insights/pareto?a=vibration&b=vibration", headers=admin).status_code == 400
    assert client.get(f"/api/studies/{sid}/insights/pareto?a=vibration&b=floor_force", headers=admin).json()["facts"]["front"] >= 3

    # 게시: 같은 제목이면 갱신하고, 보고서 자료에 같은 문장이 들어 있다
    p1 = client.post(f"/api/studies/{sid}/insights/publish", json={}, headers=admin).json()["published"]
    p2 = client.post(f"/api/studies/{sid}/insights/publish", json={}, headers=admin).json()["published"]
    assert [x["artifact_id"] for x in p1] == [x["artifact_id"] for x in p2]
    assert {x["kind"] for x in p1} >= {"table", "bar", "line", "scatter"}
    assert all(x["embed"].startswith("![") and "artifact:" in x["embed"] for x in p1)
    ctx = client.get(f"/api/projects/{pid}/report-context", headers=admin).json()
    studied = next(x for x in ctx["studies"] if x["study_id"] == sid)
    assert _section(studied["insights"], "pareto")["sentence"] == pareto["sentence"]

    # 새 케이스가 생기면 상태가 바뀌어 다시 계산한다
    client.post(f"/api/studies/{sid}/doe", json={"method": "lhs", "n": 1, "seed": 9}, headers=admin)
    v3 = client.get(f"/api/studies/{sid}/insights", headers=admin).json()
    assert v3["status"] == "computing" and v3["stale"]
    assert v3["overview"]["next"]["key"] == "computing" and not v3["overview"]["next"]["label"]  # 낡은 결과의 할 일을 내주지 않는다
    assert _ready(client, sid, admin)["state_key"] != v["state_key"]


def test_model_without_all_objectives_says_so(client):
    admin, _ = _auth(client, "admin")
    _, sid = _washer_study(client, admin, n=12)
    _run(client, sid, admin)
    client.post(f"/api/studies/{sid}/train", json={"outputs": ["vibration"]}, headers=admin)
    r = client.post(f"/api/studies/{sid}/suggest", json={"mode": "bo", "n": 1}, headers=admin)
    assert r.status_code == 400 and "floor_force" in r.json()["detail"]


def test_multi_objective_suggestion_respects_constraints():
    """제약 확률이 곱해져 위반 쪽을 고르지 않는다."""
    from server.app.study import bayesopt
    from server.app.study.surrogate import _make_gp

    U = qmc.LatinHypercube(d=2, seed=1).random(24)
    f = lambda A: (A[:, 0] - 0.2) ** 2 + (A[:, 1] - 0.2) ** 2  # noqa: E731
    g = lambda A: (A[:, 0] - 0.8) ** 2 + (A[:, 1] - 0.8) ** 2  # noqa: E731
    s = lambda A: A[:, 0]  # noqa: E731
    sur = Surrogate(variables=["x", "y"], outputs=["a", "b", "s"])
    sur.models = {"a": _make_gp(2).fit(U, f(U)), "b": _make_gp(2).fit(U, g(U)), "s": _make_gp(2).fit(U, s(U))}
    obs = np.column_stack([f(U), g(U)])
    p = bayesopt.suggest(sur, np.zeros(2), np.ones(2), "mo", 1, seed=2, n_candidates=1024,
                         objectives=[{"name": "a"}, {"name": "b"}], observed=obs, constraints=[{"name": "s", "upper": 0.5}])
    assert p[0, 0] <= 0.55, p


def test_strange_results_never_break(client):
    """값이 문자열·NaN·None이거나 어댑터가 없는 스터디, 실패만 있는 스터디에서도 500이 나지 않는다."""
    from server.app import models
    from server.app.database import SessionLocal

    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "이상한 값"}, headers=admin).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "이상", "adapter": "mock", "adapter_config": {"function": "branin"},
        "variables": [{"name": "x1", "lower": -5, "upper": 10}, {"name": "x2", "lower": 0, "upper": 15}],
        "objectives": [{"name": "f", "goal": "min"}], "constraints": [{"name": "s", "upper": 1}]}).json()["id"]
    with SessionLocal() as db:
        values = ["문자열", None, 1.0, 2.0, {"a": 1}, True, -3.0, 5.0, 6.0, 7.0]
        for i, v in enumerate(values):
            db.add(models.Case(study_id=sid, params={"x1": i - 5.0, "x2": float(i)}, results={"f": v, "s": 0.5, "t": "x"}, status="complete", source="doe"))
        db.add(models.Case(study_id=sid, params={"x1": 0.0}, results={}, status="complete", source="doe"))  # 변수 값 없음
        db.add(models.Case(study_id=sid, params={"x1": 1.0, "x2": 1.0}, results={"f": "NaN"}, status="failed", source="doe"))
        db.commit()
    r = client.get(f"/api/studies/{sid}/insights", headers=admin)
    assert r.status_code == 200, r.text
    v = r.json()
    assert _section(v, "quality")["sentence"]
    assert not any(x["sentence"] == insights.T["error"] for x in v["sections"]), [x["sentence"] for x in v["sections"]]
    # NaN 결과를 JSON으로 낼 수 있어야 한다
    assert client.get(f"/api/studies/{sid}/insights?detail=summary", headers=admin).status_code == 200

    only_fail = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "실패뿐", "adapter": "mock", "adapter_config": {"function": "없는함수"},
        "variables": [{"name": "x", "lower": 0, "upper": 1}], "objectives": [{"name": "y", "goal": "min"}]}).json()["id"]
    client.post(f"/api/studies/{only_fail}/doe", json={"method": "lhs", "n": 8, "seed": 1}, headers=admin)
    _run(client, only_fail, admin)
    v = _ready(client, only_fail, admin)
    q = _section(v, "quality")
    assert q["state"] == "caution" and q["facts"]["failed"] == 8
    assert v["overview"]["next"]["key"] == "fail"


def test_variable_note_kept_and_used_in_sentence(client):
    admin, _ = _auth(client, "admin")
    _, sid = _washer_study(client, admin, n=4)
    s = client.get(f"/api/studies/{sid}", headers=admin).json()["study"]
    assert s["variables"][0]["note"] == "스프링 강성 [%]"


def test_case_timing_gives_elapsed_when_measured(client):
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "시간"}, headers=admin).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "시간", "adapter": "mock", "adapter_config": {"function": "branin", "delay_sec": 0.02},
        "variables": [{"name": "x1", "lower": -5, "upper": 10}, {"name": "x2", "lower": 0, "upper": 15}],
        "objectives": [{"name": "f", "goal": "min"}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"method": "lhs", "n": 6, "seed": 1}, headers=admin)
    _run(client, sid, admin)
    v = _ready(client, sid, admin)
    assert v["overview"]["facts"]["run_text"], v["overview"]


def test_trust_line_member_and_share_has_no_project_or_case_data(client):
    admin, _ = _auth(client, "admin")
    kim, _ = _auth(client, "kim_ins2", admin)
    pid, sid = _washer_study(client, admin, n=24, objectives=[{"name": "vibration", "goal": "min"}])
    _run(client, sid, admin)
    m = client.post(f"/api/studies/{sid}/train", json={"outputs": ["vibration"]}, headers=admin).json()
    client.post(f"/api/models/{m['id']}/deploy", headers=admin)
    line = client.get(f"/api/models/{m['id']}/trust-line", headers=admin).json()
    assert line["state_label"] in ("믿을 만함", "참고용", "믿기 어려움") and "10번 중 8번" in line["sentence"] and "mm" in line["sentence"]
    assert line["outputs"]["vibration"]["p80"] > 0 and line["outputs"]["vibration"]["label"] == "진동"
    assert client.get(f"/api/models/{m['id']}/trust-line", headers=kim).status_code == 403
    token = client.post(f"/api/models/{m['id']}/shares", json={"note": "", "expires_days": 7}, headers=admin).json()["token"]
    shared = client.get(f"/api/shares/{token}/trust-line", headers=kim)
    assert shared.status_code == 200
    body = shared.text
    assert "세탁기" not in body and "case" not in body.lower() and "케이스" not in body and "project" not in body.lower()


def test_project_delete_removes_saved_results(client):
    admin, _ = _auth(client, "admin")
    pid, sid = _washer_study(client, admin, n=8)
    _run(client, sid, admin)
    _ready(client, sid, admin)
    path = insights._cache_path(sid)
    assert path.exists()
    client.delete(f"/api/projects/{pid}", headers=admin)
    assert not path.exists() and sid not in insights._memory


def test_ei_stop_rule_on_real_branin_run_and_units(client):
    """실제 코드 경로로 돌린 Branin: 수렴 문장에 숫자가 있고, 판단은 해석값으로만 한다."""
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "branin2"}, headers=admin).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "B", "adapter": "mock", "adapter_config": {"function": "branin"},
        "variables": [{"name": "x1", "lower": -5, "upper": 10}, {"name": "x2", "lower": 0, "upper": 15}],
        "objectives": [{"name": "f", "goal": "min"}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"method": "lhs", "n": 20, "seed": 7}, headers=admin)
    _run(client, sid, admin)
    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    for _ in range(6):
        client.post(f"/api/studies/{sid}/suggest", json={"mode": "bo", "n": 1}, headers=admin)
        _run(client, sid, admin)
        client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    con = _section(_ready(client, sid, admin), "convergence")
    assert con["state"] in ("good", "fair")
    assert con["facts"]["now"] <= con["facts"]["start"]
    assert con["basis"] == "verified"


# ---------- 돌아가는 방식: 낡은 값·줄 서기·지운 스터디 ----------
def test_report_context_never_quotes_stale_sections(client, monkeypatch):
    """계산이 안 끝났으면 낡은 문장을 주지 않는다. 보고서 숫자가 화면과 어긋나면 안 된다."""
    admin, _ = _auth(client, "admin")
    pid, sid = _washer_study(client, admin, n=24)
    _run(client, sid, admin)
    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    monkeypatch.setattr(insights, "wait_ready", lambda db, study, seconds: {"status": "computing", "sections": [{"key": "quality", "sentence": "낡은 문장"}]})
    studied = next(x for x in client.get(f"/api/projects/{pid}/report-context", headers=admin).json()["studies"] if x["study_id"] == sid)
    assert studied["insights"]["status"] == "computing" and studied["insights"]["sections"] == []
    assert "인용하지" in studied["insights"]["note"]
    r = client.post(f"/api/studies/{sid}/insights/publish", json={}, headers=admin)
    assert r.status_code == 409 and "계산 중" in r.json()["detail"]


def test_report_context_waits_for_the_answer_it_started(client):
    """학습 직후 부르면 자기 호출이 계산을 시작한다. 그 결과를 기다려서 준다."""
    admin, _ = _auth(client, "admin")
    pid, sid = _washer_study(client, admin, n=24)
    _run(client, sid, admin)
    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    studied = next(x for x in client.get(f"/api/projects/{pid}/report-context", headers=admin).json()["studies"] if x["study_id"] == sid)
    assert studied["insights"]["status"] == "ready" and "owner" not in studied["insights"]
    assert _section(studied["insights"], "trust")["sentence"] == _section(_ready(client, sid, admin), "trust")["sentence"]


def _snap_for(study_id, key="k"):
    return insights.Snap(study_id, 1, "s", [], [], [], [], "mock", False, None, owner="o")


def test_queue_keeps_only_the_latest_state_per_study(monkeypatch):
    """케이스가 하나씩 끝날 때마다 낡은 계산이 줄을 서면 다른 스터디까지 몇 분씩 밀린다."""
    import threading
    gate, ran = threading.Event(), []
    monkeypatch.setattr(insights, "_work", lambda snap, key: (ran.append((snap.study_id, key)), gate.wait(5), insights._inflight.pop(snap.study_id, None)))
    sid, other = 900001, 900002
    insights._submit(_snap_for(sid), "s0")
    t0 = time.time()
    while not ran and time.time() - t0 < 5:
        time.sleep(0.01)
    for k in ("s1", "s2", "s3", "s4"):
        insights._submit(_snap_for(sid), k)
    insights._submit(_snap_for(other), "o1")
    assert [k for k, _ in insights._queue.items()] == [sid, other] and insights._queue[sid][1] == "s4"
    gate.set()
    t0 = time.time()
    while len(ran) < 3 and time.time() - t0 < 5:
        time.sleep(0.01)
    assert ran == [(sid, "s0"), (sid, "s4"), (other, "o1")]
    assert insights._thread.daemon  # 서버를 끌 때 큐를 기다리지 않는다


def test_deleted_study_result_is_not_written_back_or_shown(client):
    """지운 뒤 끝난 계산이 다시 저장되고, id를 다시 받은 다른 사람의 스터디로 보이면 안 된다."""
    from server.app.database import SessionLocal
    from server.app import models

    admin, _ = _auth(client, "admin")
    pid, sid = _washer_study(client, admin, n=8)
    _run(client, sid, admin)
    with SessionLocal() as db:
        snap = insights.snapshot(db, db.get(models.Study, sid))
    client.delete(f"/api/projects/{pid}", headers=admin)
    insights._work(snap, "늦게 끝난 계산")
    assert not insights._cache_path(sid).exists() and sid not in insights._memory

    # id를 다시 쓰는 경우: 앞 스터디의 저장 결과가 남아 있어도 새 스터디에 보이지 않는다
    pid2, sid2 = _washer_study(client, admin, n=8)
    insights._memory.pop(sid2, None)
    insights._store(sid2, {"schema": insights.SCHEMA, "owner": "다른 프로젝트", "state_key": "x", "complete_layer": True, "study_name": "SECRET-STUDY", "sections": [], "overview": {}})
    v = client.get(f"/api/studies/{sid2}/insights", headers=admin).json()
    assert v.get("study_name") != "SECRET-STUDY"
    client.delete(f"/api/projects/{pid2}", headers=admin)


def test_old_models_are_measured_once_not_on_every_state(tmp_path, monkeypatch):
    """옛 모델(교차검증 예측 없음)을 상태가 바뀔 때마다 다시 재면 작업 스레드를 몇 분씩 붙잡는다."""
    from server.app.study.insights import Ctx, ModelRow

    U = qmc.LatinHypercube(d=2, seed=1).random(16)
    sur = Surrogate(variables=["x0", "x1"], outputs=["y"])
    sur.fit(U, {"y": U.sum(axis=1)})
    sur.cv = {}
    calls = []
    real = insights.cross_val_predict
    monkeypatch.setattr(insights, "cross_val_predict", lambda *a, **k: calls.append(1) or real(*a, **k))
    m = ModelRow(987654, 1, ["y"], {"y": {"r2": 0.99, "rmse": 0.01}}, 16, "", None, True)
    snap = insights.Snap(1, 1, "s", [{"name": "x0", "lower": 0, "upper": 1}, {"name": "x1", "lower": 0, "upper": 1}], [], [], [], "mock", False, None)
    assert Ctx(snap, True).cv(sur, "y", m) and len(calls) == 1
    assert Ctx(snap, True).cv(sur, "y", m) and len(calls) == 1  # 파일로 남겨 다시 재지 않는다
    assert Ctx(snap, True, light=True).cv(sur, "y", m)
    insights._cv_path(987654).unlink()
    assert Ctx(snap, True, light=True).cv(sur, "y", m) is None and len(calls) == 1  # 요청 경로는 재지 않는다


def test_lost_agent_link_does_not_freeze_the_panel(monkeypatch):
    """연결이 끊기면 is_running이 영영 참이다. 판단이 멈추고 스피너가 돌면 안 된다."""
    from datetime import datetime, timedelta

    from server.app.study.insights import CaseRow

    def snap(adapter, updated, run_by=7):
        return insights.Snap(1, 1, "s", [], [], [], [], adapter, True, None, cases=[CaseRow(1, {}, {}, "running", "doe", None, None, run_by, updated)])
    now = datetime.now()
    assert insights._running_state(snap("script", now)) == (False, True)  # 연결 없음
    assert insights._running_state(snap("mock", now)) == (True, False)  # 서버에서 도는 연습용
    assert insights._running_state(snap("mock", now - timedelta(seconds=insights.RUN_QUIET_SEC + 5))) == (False, False)  # 오래 조용하면 한 번 잰다
    idle = insights.Snap(1, 1, "s", [], [], [], [], "mock", True, None, cases=[CaseRow(1, {}, {}, "complete", "doe", None, None, 7, now)])
    assert insights._running_state(idle) == (False, False)
    # 화면도 그렇게 말한다
    lost = snap("script", now)
    lost.link_lost, lost.running = True, False
    assert insights.compute(lost, True)["overview"]["next"]["key"] == "stuck"


def test_partial_error_is_retried_not_saved(client, monkeypatch):
    """한 번의 오류를 정상 결과처럼 저장하면 '잠시 뒤 다시 계산합니다'가 거짓말이 된다."""
    admin, _ = _auth(client, "admin")
    pid, sid = _washer_study(client, admin, n=24)
    _run(client, sid, admin)
    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    real = insights.optimum

    def boom(ctx):
        raise RuntimeError("한 번의 오류")
    boom.__name__ = "optimum"
    monkeypatch.setattr(insights, "optimum", boom)
    monkeypatch.setattr(insights, "RETRY_SEC", 0.0)
    v = _ready(client, sid, admin)
    assert _section(v, "optimum")["sentence"] == insights.T["error"] and v["had_error"]
    assert not insights._cache_path(sid).exists() or insights.json.loads(insights._cache_path(sid).read_text(encoding="utf-8")).get("state_key") != v["state_key"]
    monkeypatch.setattr(insights, "optimum", real)
    t0 = time.time()
    while time.time() - t0 < 30:
        v = client.get(f"/api/studies/{sid}/insights", headers=admin).json()
        if not v.get("had_error"):
            break
        time.sleep(0.2)
    assert _section(v, "optimum")["sentence"] != insights.T["error"] and not v.get("had_error")


def test_summary_table_has_no_cut_off_column(client):
    admin, _ = _auth(client, "admin")
    pid, sid = _washer_study(client, admin, n=24)
    _run(client, sid, admin)
    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    _ready(client, sid, admin)
    pub = client.post(f"/api/studies/{sid}/insights/publish", json={"keys": ["summary"]}, headers=admin).json()["published"]
    arts = client.get(f"/api/projects/{pid}/artifacts", headers=admin).json()
    a = next(x for x in arts if x["id"] == pub[0]["artifact_id"])
    assert a["payload"]["columns"] == ["질문", "상태", "결론"] and "데이터 탭" in a["title"]
