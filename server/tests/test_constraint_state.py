"""제약 판정은 한 곳(service.constraint_state)에서 정한다. 값이 없는 케이스는 어느 화면에서도 '만족'으로 읽히지 않아야 한다."""
from types import SimpleNamespace

import numpy as np
import pytest

import server.tests.test_api  # noqa: F401  서버 코드보다 먼저 불러 DDS_DATA_DIR을 임시 폴더로 둔다

from server.app.study import insights, service  # noqa: E402
from server.app.study.insights import Ctx, ModelRow  # noqa: E402
from server.tests.test_insights_calc import _row, _snap, no_model_ei  # noqa: E402,F401

insights.BENCH_ENABLED = False
CON = [{"name": "s", "upper": 2.0}]


@pytest.mark.parametrize("results, want", [
    ({"s": 1.0}, True),
    ({"s": 2.0}, True),  # 경계는 만족
    ({"s": 3.0}, False),
    ({}, None),
    ({"s": None}, None),
    ({"s": float("nan")}, None),
    ({"s": "없음"}, None),
    ({"s": True}, None),  # 참거짓은 값이 아니다
    (None, None),
])
def test_three_states(results, want):
    assert service.constraint_state(CON, results) is want


def test_violation_wins_over_unknown_and_no_condition_means_true():
    two = [{"name": "s", "upper": 2.0}, {"name": "t", "lower": 1.0}]
    assert service.constraint_state(two, {"t": 0.0}) is False  # s는 몰라도 t를 어겼다
    assert service.constraint_state(two, {"s": 1.0}) is None
    assert service.constraint_state(two, {"s": 1.0, "t": 1.0}) is True
    assert service.constraint_state([], {}) is True  # 제약이 없으면 따질 것이 없다
    assert service.constraint_state(None, {}) is True
    assert service.constraint_state([{"name": "s"}], {}) is True  # 범위가 없는 제약


def _study(cases, constraints=CON):
    return SimpleNamespace(constraints=constraints, objectives=[{"name": "y", "goal": "min"}], cases=cases)


def _case(i, **results):
    return SimpleNamespace(id=i, status="complete", results=results)


def test_best_case_never_picks_a_case_without_constraint_data():
    good, unknown, bad = _case(1, y=5.0, s=1.0), _case(2, y=0.1), _case(3, y=0.0, s=9.0)
    assert service.best_case(_study([good, unknown, bad])) is good
    assert service.best_case(_study([unknown, bad])) is None
    assert service.best_case(_study([unknown, bad]), feasible_only=False) is bad


def _rows():
    """값은 가장 좋지만 제약 값이 없는 케이스(id 99)와 만족 케이스들."""
    rows = [_row(i + 1, {"a": i / 12}, {"y": 5.0 - 0.1 * i, "s": 1.0}) for i in range(12)]
    return rows + [_row(99, {"a": 0.5}, {"y": -100.0})]


def _one_goal(rows):
    return _snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], constraints=CON)


def test_insights_exclude_unknown_from_best_and_keep_three_states(no_model_ei):
    no_model_ei(0.0)
    snap = _one_goal(_rows())
    snap.best_failed = True  # best_case가 고르지 못했을 때 직접 고르는 길
    ctx = Ctx(snap, True)
    assert ctx.feasible(_rows()[-1]) is None
    assert insights._best(ctx).id != 99
    assert 99 not in [r["case_id"] for r in insights._candidates(ctx, "y", 1.0)[0]["rows"]]
    assert insights._total_gain(ctx, "y", 1.0) == 0.0  # 처음 계획의 최고 값 3.9에서 나아진 것이 없다. -100은 값으로 치지 않는다
    curve = insights.convergence(ctx)["charts"][0]
    assert curve["kind"] == "curve" and curve["feasible"][-1] is None and curve["feasible"][0] is True
    assert min(v for v in curve["best"] if v is not None) > 0, "값이 없는 케이스가 최고 값이 되었습니다"


def test_all_unknown_is_not_called_infeasible(no_model_ei):
    no_model_ei(0.0)
    rows = [_row(i + 1, {"a": i / 12}, {"y": 5.0 - i}) for i in range(12)]
    rows += [_row(50 + i, {"a": 0.5}, {"y": 1.0}, source="bo") for i in range(6)]
    ctx = Ctx(_one_goal(rows), True)
    sec = insights.convergence(ctx)
    assert sec["state"] == "unknown" and "제약 값이 없어" in sec["sentence"], sec
    opt = insights.optimum(ctx)
    assert opt["state"] == "unknown" and "제약 값이 없어" in opt["sentence"], opt
    # 한 건이라도 어겼으면 '못 찾았다'가 맞다
    ctx2 = Ctx(_one_goal(rows + [_row(70, {"a": 0.1}, {"y": 0.0, "s": 9.0}, source="bo")]), True)
    assert insights.convergence(ctx2)["state"] == "caution"


def test_pareto_marks_unknown_as_unknown():
    rows = [_row(i + 1, {"a": i / 8}, {"y": float(i), "z": float(8 - i), "s": 1.0}) for i in range(8)]
    rows.append(_row(9, {"a": 0.5}, {"y": 0.0, "z": 0.0}))  # 제약 값이 없다. 값은 앞면 위지만 앞면에 넣지 않는다
    snap = _snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}, {"name": "z", "goal": "min"}], constraints=CON)
    sec = insights.pareto(Ctx(snap, True))
    chart = next(c for c in sec["charts"] if c["kind"] == "pareto")
    assert next(p for p in chart["all"] if p[0] == 9)[3] is None
    assert 9 not in [p[0] for p in chart["front"]]


# ---------- 품질 값과 맞추기 ----------
def _cv(actual, pred):
    return {"r2": 1 - float(np.sum((np.array(pred) - actual) ** 2)) / float(np.sum((actual - actual.mean()) ** 2)),
            "rmse": 1.0, "actual": list(map(float, actual)), "cv_pred": list(map(float, pred))}


def test_quality_scores_design_range_without_optimizer_points():
    """최적화 점이 몰려 전체 R²가 높아도 설계 범위를 못 맞히면 '좋음'이라고 하지 않는다."""
    wide = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    wide_pred = np.array([3.0, 0.5, 4.0, 1.0, 5.0, 2.0, 6.0, 3.0])  # 처음 점은 잘 못 맞힌다
    bo = np.full(40, 50.0)
    actual, pred = np.concatenate([wide, bo]), np.concatenate([wide_pred, bo])
    keep = np.array([True] * 8 + [False] * 40)
    cv = {"y": _cv(actual, pred)}
    assert cv["y"]["r2"] > 0.9
    all_points = service.assess_quality(cv, 48, 2, "mock", [])
    assert all_points["by_output"]["y"]["level"] == "good"
    scored = service.assess_quality(cv, 48, 2, "mock", [], {"y": keep})
    assert scored["by_output"]["y"]["level"] == "poor" and scored["by_output"]["y"]["r2"] < 0.7
    assert any("최적화가 한곳에 몰아" in w for w in scored["warnings"])
    # 남는 점이 너무 적으면 빼지 않는다
    few = service.assess_quality(cv, 48, 2, "mock", [], {"y": np.array([True] * 4 + [False] * 44)})
    assert few["by_output"]["y"]["level"] == "good"


def _model(quality, r2=0.95):
    return ModelRow(1, 1, ["y"], {"y": {"r2": r2, "rmse": 1.0}}, 40, "", None, False, quality)


def test_verdict_is_never_better_than_stored_quality(monkeypatch):
    snap = _one_goal(_rows())
    ctx = Ctx(snap, True)
    monkeypatch.setattr(Ctx, "score", lambda self, m, o: None)
    monkeypatch.setattr(Ctx, "near_flat", lambda self, m, o: False)
    assert ctx.verdict(_model({}), "y") == "good"  # 옛 모델: 품질이 없으면 R²로
    assert ctx.verdict(_model({"by_output": {"y": {"level": "good", "r2": 0.95}}}), "y") == "good"
    assert ctx.verdict(_model({"by_output": {"y": {"level": "fair", "r2": 0.8}}}), "y") == "fair"
    assert ctx.verdict(_model({"by_output": {"y": {"level": "poor", "r2": 0.1}}}), "y") == "caution"


def test_rows_per_output_decide_verdict_and_staleness():
    rows = [_row(i + 1, {"a": i / 30}, {"y": float(i), "s": 1.0}) for i in range(30)]
    ctx = Ctx(_one_goal(rows), True)
    short = _model({"rows_by_output": {"y": 3}})  # 이 출력은 3행으로만 학습했다
    assert ctx.rows_of(short, "y") == 3 and ctx.verdict(short, "y") == "unknown"
    # 행이 많아 골라 학습했으면 골라 뺀 행을 새 해석으로 치지 않는다
    picked = ModelRow(1, 1, ["y"], {"y": {"r2": 0.95, "rmse": 1.0}}, 10, "", None, False, {"sampled": {"from": 30, "to": 10}, "rows_by_output": {"y": 10}})
    assert ctx.stale_by(picked, "y") == 0 and not ctx.is_stale(picked, "y")
    newer = ModelRow(1, 1, ["y"], {"y": {"r2": 0.95, "rmse": 1.0}}, 10, "", None, False, {"rows_by_output": {"y": 10}})
    assert ctx.stale_by(newer, "y") == 20


def test_next_step_does_not_repeat_deploy_when_already_deployed():
    """표로 만들면 Simulator가 이미 배포돼 있다. 한 일을 또 하라고 하지 않는다."""
    rows = [_row(i + 1, {"a": i / 30}, {"y": float(i)}) for i in range(30)]
    deployed = ModelRow(1, 1, ["y"], {"y": {"r2": 0.95, "rmse": 1.0}}, 30, "", None, True)
    by = {k: {"state": "good", "facts": {}} for k in ("coverage", "quality", "trust", "convergence", "optimum")}
    nx = insights._next(Ctx(_snap([("a", 0, 1)], rows, models=[deployed]), True), by)
    assert nx["key"] == "report" and "배포" not in nx["text"] and "배포" not in nx["request"]
    nx = insights._next(Ctx(_snap([("a", 0, 1)], rows, models=[ModelRow(1, 1, ["y"], {"y": {"r2": 0.95, "rmse": 1.0}}, 30, "", None, False)]), True), by)
    assert nx["key"] == "finish"


def test_failed_rerun_restores_run_times_with_results():
    old = {"started_at": "2026-01-01T00:00:00", "ended_at": "2026-01-01T01:00:00"}
    case = SimpleNamespace(status="running", results={}, run_by=2, run_dir="", archive=None, images=None, fields=None, error=None, timing={"started_at": "2026-02-01T00:00:00"},
                           previous={"results": {"y": 1.0}, "timing": old, "run_by": 1, "run_dir": "a"})
    assert service._restore_previous(case, "실패")
    assert case.status == "complete" and case.results == {"y": 1.0} and case.timing == old and case.previous is None
