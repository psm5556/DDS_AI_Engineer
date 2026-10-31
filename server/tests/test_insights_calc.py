"""스터디 판단 도우미의 계산과 판정을 정답을 아는 입력으로 검사한다. DB·서버 없이 돈다."""
import re
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import qmc

import server.tests.test_api  # noqa: F401  서버 코드보다 먼저 불러 DDS_DATA_DIR을 임시 폴더로 둔다

from server.app.study import bayesopt, insights, insights_calc as calc  # noqa: E402
from server.app.study.insights import CaseRow, Ctx, ModelRow, Snap  # noqa: E402
from server.app.study.surrogate import Surrogate  # noqa: E402

insights.BENCH_ENABLED = False


# ---------- 영향도(Sobol) ----------
def ishigami(U):
    x = U * 2 * np.pi - np.pi
    return np.sin(x[:, 0]) + 7 * np.sin(x[:, 1]) ** 2 + 0.1 * x[:, 2] ** 4 * np.sin(x[:, 0])


def test_sobol_ishigami_matches_analytic():
    r = calc.saltelli(ishigami, 3, 4096)
    assert np.allclose(r["S"], [0.314, 0.442, 0.0], atol=0.01), r["S"]
    assert np.allclose(r["T"], [0.558, 0.442, 0.244], atol=0.01), r["T"]


def test_sobol_additive_is_all_first_order():
    r = calc.saltelli(lambda U: U[:, 0] + 2 * U[:, 1], 2, 4096)
    assert np.allclose(r["S"], [0.2, 0.8], atol=0.01)
    assert np.allclose(r["T"], [0.2, 0.8], atol=0.01)


def test_sobol_product_is_all_interaction():
    f = lambda U: (2 * U[:, 0] - 1) * (2 * U[:, 1] - 1)  # noqa: E731
    r = calc.saltelli(f, 2, 4096)
    assert np.all(np.abs(r["S"]) < 0.02), r["S"]
    assert np.allclose(r["T"], [1, 1], atol=0.02), r["T"]
    assert calc.second_order(f, r, 0, 1) == pytest.approx(1.0, abs=0.03)


def test_constant_function_has_no_variance():
    r = calc.saltelli(lambda U: np.ones(len(U)), 2, 256)
    assert r["var"] == 0 and np.all(r["T"] == 0)


# ---------- 방향 ----------
def test_direction_shapes():
    x = np.linspace(0, 1, 21).tolist()
    assert calc.direction(x, x)["kind"] == "up"
    assert calc.direction(x, [-v for v in x])["kind"] == "down"
    d = calc.direction(x, [(v - 0.5) ** 2 for v in x])
    assert d["kind"] == "min" and d["x"] == pytest.approx(0.5, abs=0.06)
    d = calc.direction(x, [-(v - 0.5) ** 2 for v in x])
    assert d["kind"] == "max" and d["x"] == pytest.approx(0.5, abs=0.06)
    assert calc.direction(x, [1.0] * 21)["kind"] == "flat"


# ---------- 비지배 ----------
def test_non_dominated_hand_checked_min():
    S = np.array([[1, 5], [2, 3], [3, 4], [4, 1], [2, 3], [5, 5]], float)
    # (3,4)는 (2,3)에, (5,5)는 전부에 진다. 같은 값 (2,3) 두 개는 서로 지배하지 않는다
    assert calc.non_dominated(S).tolist() == [True, True, False, True, True, False]


def test_non_dominated_three_objectives():
    S = np.array([[1, 1, 3], [1, 3, 1], [3, 1, 1], [2, 2, 2], [1, 1, 4]], float)
    assert calc.non_dominated(S).tolist() == [True, True, True, True, False]


def _row(i, params, results, status="complete", source="doe", at=None, timing=None):
    return CaseRow(i, params, results, status, source, timing, at or datetime(2026, 1, 1) + timedelta(minutes=i))


def _snap(variables, cases, objectives=(), constraints=(), outputs=(), models=(), adapter="mock", best_id=None):
    s = Snap(1, 1, "시험", [{"name": n, "lower": lo, "upper": hi} for n, lo, hi in variables], list(objectives), list(constraints), list(outputs),
             adapter, False, best_id, cases=list(cases), models=list(models))
    return s


def test_pareto_respects_goals_and_constraints_and_missing_values():
    """손으로 고른 표. 목표 a는 작게, b는 크게. 제약 s ≤ 10. 제약 위반·제약 값 없음·문자열·NaN은 후보에서 빠진다."""
    rows = [
        _row(1, {"x": 0.1}, {"a": 1.0, "b": 1.0, "s": 5}),   # 후보
        _row(2, {"x": 0.2}, {"a": 2.0, "b": 3.0, "s": 5}),   # 후보
        _row(3, {"x": 0.3}, {"a": 3.0, "b": 2.0, "s": 5}),   # 2에 진다
        _row(4, {"x": 0.4}, {"a": 4.0, "b": 5.0, "s": 5}),   # 후보
        _row(5, {"x": 0.5}, {"a": 0.5, "b": 9.0, "s": 20}),  # 가장 좋아 보이지만 제약 위반
        _row(6, {"x": 0.6}, {"a": 0.1, "b": 9.0}),           # 제약 값이 없어 판단 불가
        _row(7, {"x": 0.7}, {"a": "오류", "b": 9.0, "s": 5}),  # 값이 문자열
        _row(8, {"x": 0.8}, {"a": float("nan"), "b": 9.0, "s": 5}),  # NaN
        _row(9, {"x": 0.9}, {"a": 5.0, "b": 4.0, "s": 5}),   # 4에 진다
        _row(10, {"x": 0.95}, {}, status="failed"),
    ]
    snap = _snap([("x", 0, 1)], rows, objectives=[{"name": "a", "goal": "min"}, {"name": "b", "goal": "max"}],
                 constraints=[{"name": "s", "upper": 10}])
    sec = insights.pareto(Ctx(snap, True))
    assert sec["facts"]["front_cases"] == [1, 2, 4]
    assert sec["state"] != "caution"
    # 교환 문장은 목표 방향을 따른다: b는 클수록 좋으니 '줄어듭니다'
    assert "줄이면" in sec["sentence"] and "줄어듭니다" in sec["sentence"], sec["sentence"]


def test_pareto_too_few_and_infeasible_and_one_point():
    v = [("x", 0, 1)]
    obj = [{"name": "a", "goal": "min"}, {"name": "b", "goal": "min"}]
    few = insights.pareto(Ctx(_snap(v, [_row(1, {"x": 0.1}, {"a": 1, "b": 1})], objectives=obj), True))
    assert few["state"] == "unknown" and "4건 더" in few["sentence"]
    bad = _snap(v, [_row(i, {"x": i / 10}, {"a": 1, "b": 1, "s": 99}) for i in range(1, 7)], objectives=obj, constraints=[{"name": "s", "upper": 1}])
    assert insights.pareto(Ctx(bad, True))["state"] == "caution"
    one = _snap(v, [_row(i, {"x": i / 10}, {"a": float(i), "b": float(i)}) for i in range(1, 7)], objectives=obj)
    s = insights.pareto(Ctx(one, True))
    assert s["state"] == "good" and "케이스 1" in s["sentence"]


def test_pareto_small_front_is_not_called_a_conflict():
    """앞면이 전체 값 범위의 일부에만 있으면 두 목표가 크게 부딪히지 않는다고 말한다."""
    v = [("x", 0, 1)]
    obj = [{"name": "a", "goal": "min"}, {"name": "b", "goal": "min"}]
    rows = [_row(1, {"x": 0.1}, {"a": 1.00, "b": 1.02}), _row(2, {"x": 0.2}, {"a": 1.01, "b": 1.01}), _row(3, {"x": 0.3}, {"a": 1.02, "b": 1.00})]
    rows += [_row(i, {"x": i / 20}, {"a": 2.0 + i, "b": 3.0 + i}) for i in range(4, 12)]
    sec = insights.pareto(Ctx(_snap(v, rows, objectives=obj), True))
    assert not sec["facts"]["conflict"] and "크게 부딪히지 않습니다" in sec["sentence"]


def test_pareto_collapses_for_one_objective_with_other_outputs():
    rows = [_row(i, {"x": i / 10}, {"a": float(i), "b": float(-i)}) for i in range(1, 7)]
    s = insights.pareto(Ctx(_snap([("x", 0, 1)], rows, objectives=[{"name": "a", "goal": "min"}]), True))
    assert s["collapsed"] and "하나만 목표" in s["sentence"] and s["pair_choices"][0]["name"] == "b" and s["state_label"] == "선택 사항"
    # 다른 출력이 없으면 아예 내지 않는다
    rows = [_row(i, {"x": i / 10}, {"a": float(i)}) for i in range(1, 7)]
    assert insights.pareto(Ctx(_snap([("x", 0, 1)], rows, objectives=[{"name": "a", "goal": "min"}]), True)) is None


def test_knee_curve_and_straight_line():
    x = np.linspace(0.5, 2, 61)
    F = np.column_stack([x, 1 / x])
    k, _ = calc.knee(F)
    assert F[k, 0] == pytest.approx(1.0, abs=0.1) and F[k, 1] == pytest.approx(1.0, abs=0.1)
    line = np.column_stack([x, 3 - x])
    assert calc.knee(line)[0] is None


def test_hypervolume_exact_and_monte_carlo():
    F = np.array([[1.0, 3.0], [2.0, 1.0]])
    # 기준점 (4,4): (1,3)이 3×1, (2,1)이 추가로 2×2 → 7
    assert calc.hypervolume(F, np.array([4.0, 4.0])) == pytest.approx(7.0)
    F3 = np.array([[1.0, 1.0, 1.0]])
    assert calc.hypervolume(F3, np.array([2.0, 2.0, 3.0])) == pytest.approx(2.0, rel=0.01)


def test_tradeoff_interpolates_on_straight_front():
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([5.0, 3.0, 1.0])
    t = calc.tradeoff(a, b, 2.0, 0.5)
    assert t["a_to"] == pytest.approx(1.5) and t["b_to"] == pytest.approx(4.0) and t["within"]
    t = calc.tradeoff(a, b, 2.0, 5.0)  # 앞면 밖이면 끝점까지만 말한다
    assert t["a_to"] == pytest.approx(1.0) and not t["within"]


# ---------- DOE 판정 ----------
VARS4 = [("a", 0, 1), ("b", 0, 1), ("c", 0, 1), ("d", 0, 1)]


def _doe(U, status="complete"):
    return _snap(VARS4[:U.shape[1]], [_row(i + 1, {n: float(v) for n, v in zip("abcd", row)}, {"y": 1.0 + float(i)}, status=status) for i, row in enumerate(U)])


def test_doe_lhs_is_good_most_of_the_time():
    secs = [insights.coverage(Ctx(_doe(qmc.LatinHypercube(d=4, seed=s).random(40)), True)) for s in range(20)]
    states = [x["state"] for x in secs]
    assert states.count("good") >= 16, states
    # 표준 배치라도 우연히 두 변수가 함께 움직일 수 있고 그것은 사실이다. 그 밖의 이유로 주의가 되면 안 된다
    assert all("상관" in x["sentence"] for x in secs if x["state"] == "caution")


def test_doe_random_is_rarely_good():
    secs = [insights.coverage(Ctx(_doe(np.random.default_rng(s).random((40, 4))), True)) for s in range(20)]
    assert sum(x["state"] == "good" for x in secs) <= 2


def test_doe_clustered_is_caution_about_spread():
    U = np.random.default_rng(0).random((40, 4)) * 0.3
    sec = insights.coverage(Ctx(_doe(U), True))
    assert sec["state"] == "caution"
    assert "구간" in sec["sentence"] or "퍼지지" in sec["sentence"]


def test_doe_correlated_variables_named():
    U = qmc.LatinHypercube(d=4, seed=1).random(40)
    U[:, 1] = U[:, 0]
    sec = insights.coverage(Ctx(_doe(U), True))
    assert sec["state"] == "caution" and "a" in sec["sentence"] and "b" in sec["sentence"] and "상관" in sec["sentence"]


def test_doe_empty_quadrant_names_variable_and_range():
    """변수 a의 절반을 비우면 그 변수와 구간을 말한다."""
    U = qmc.LatinHypercube(d=4, seed=2).random(40)
    U[:, 0] = U[:, 0] * 0.5
    sec = insights.coverage(Ctx(_doe(U), True))
    assert sec["state"] == "caution"
    assert re.search(r"a의 0\.\d+~1 구간", sec["sentence"]), sec["sentence"]


def test_doe_count_boundaries():
    d = 4
    need, rec = max(8, 5 * d), 8 * d
    few = insights.coverage(Ctx(_doe(qmc.LatinHypercube(d=4, seed=3).random(need - 1)), True))
    assert few["state"] == "caution" and str(need) in few["sentence"]
    mid = insights.coverage(Ctx(_doe(qmc.LatinHypercube(d=4, seed=3).random(need)), True))
    assert insights.coverage(Ctx(_doe(qmc.LatinHypercube(d=4, seed=3).random(need)), True))["next"]["action"] == "doe"
    assert mid["state"] == "fair" and str(rec) in " ".join([mid["sentence"], *mid["notes"]])


def test_doe_count_includes_ai_points_but_spread_uses_first_points():
    d4 = qmc.LatinHypercube(d=4, seed=3).random(24)
    rows = [_row(i + 1, dict(zip("abcd", map(float, r))), {"y": 1.0}) for i, r in enumerate(d4)]
    rows += [_row(100 + i, {"a": 0.9, "b": 0.9, "c": 0.9, "d": 0.9}, {"y": 1.0}, source="al") for i in range(10)]  # 일부러 몰린 AI 점
    sec = insights.coverage(Ctx(_snap(VARS4, rows), True))
    assert sec["facts"]["n"] == 24 and sec["facts"]["n_all"] == 34
    assert "적은 편" not in sec["sentence"] and "몰려" not in sec["sentence"] and "24건" in sec["sentence"]


def test_doe_before_solving_uses_planned_points():
    U = qmc.LatinHypercube(d=2, seed=4).random(20)
    sec = insights.coverage(Ctx(_snap(VARS4[:2], [_row(i + 1, {"a": float(r[0]), "b": float(r[1])}, {}, status="pending") for i, r in enumerate(U)]), True))
    assert sec["facts"]["n"] == 20 and sec["state"] in ("good", "fair")


def test_doe_one_variable():
    U = qmc.LatinHypercube(d=1, seed=5).random(10)
    sec = insights.coverage(Ctx(_snap([("a", 0, 1)], [_row(i + 1, {"a": float(u[0])}, {"y": 1.0}) for i, u in enumerate(U)]), True))
    assert sec["state"] in ("good", "fair") and sec["facts"]["corr_pair"] is None


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_too_little_data_never_judges(n):
    """자료가 거의 없으면 어느 항목도 판정하지 않고 몇 건이 더 필요한지 말한다."""
    rows = [_row(i + 1, {"a": 0.1 * i, "b": 0.2 * i}, {"y": 1.0 * i, "z": 2.0 * i}) for i in range(n)]
    snap = _snap(VARS4[:2], rows, objectives=[{"name": "y", "goal": "min"}, {"name": "z", "goal": "min"}], best_id=1 if n else None)
    out = insights.compute(snap, True)
    for sec in out["sections"]:
        assert sec["state"] == "unknown", (n, sec["key"], sec["sentence"])
    cov = next(x for x in out["sections"] if x["key"] == "coverage")
    assert "최소" in cov["sentence"] and "권장" in cov["sentence"]


# ---------- 결과 점검 ----------
def test_failure_cluster_detected_and_uniform_not():
    rng = np.random.default_rng(0)
    U = rng.random((60, 2))
    failed = U[:, 0] < 0.2
    c = calc.failure_cluster(U, failed)
    assert c and c["var"] == 0 and c["bin"] == 0
    assert calc.failure_cluster(U, rng.random(60) < 0.2) is None


def test_quality_names_failure_cluster_and_range():
    rng = np.random.default_rng(1)
    rows = []
    for i, u in enumerate(rng.random((60, 2))):
        fail = u[0] < 0.2
        rows.append(_row(i + 1, {"a": float(u[0]), "b": float(u[1])}, {} if fail else {"y": float(u[1])}, status="failed" if fail else "complete"))
    sec = insights.quality(Ctx(_snap(VARS4[:2], rows), True))
    assert sec["state"] == "caution" and "a" in sec["sentence"] and "몰려" in sec["sentence"]
    assert sec["next"]["label"] == "원인 찾기 요청"


def test_outlier_flagged_and_long_tail_not():
    y = np.array([10.0, 10.5, 9.8, 10.2, 9.9, 10.1, 10.3, 9.7, 100.0, 10.0])
    assert calc.outliers(y) == [8]
    # 꼬리가 길어도 값이 이어져 있으면 표시하지 않는다(Branin 정상값)
    U = qmc.LatinHypercube(d=2, seed=1).random(40)
    x1, x2 = U[:, 0] * 15 - 5, U[:, 1] * 15
    branin = (x2 - 5.1 / (4 * np.pi ** 2) * x1 ** 2 + 5 / np.pi * x1 - 6) ** 2 + 10 * (1 - 1 / (8 * np.pi)) * np.cos(x1) + 10
    assert calc.outliers(branin) == []


def test_outlier_needs_model_error_and_skips_range_edge():
    y = np.array([10.0, 10.5, 9.8, 10.2, 9.9, 10.1, 10.3, 9.7, 100.0, 10.0])
    err = np.array([0.1, -0.1, 0.1, 0.1, -0.1, 0.1, 0.1, -0.1, 0.1, 0.1])  # 모델이 그 값도 잘 맞추면 해석 오류가 아니다
    assert calc.outliers(y, cv_err=err) == []
    err = err.copy()
    err[8] = 50.0
    assert calc.outliers(y, cv_err=err) == [8]
    edge = np.ones(10, dtype=bool)
    edge[8] = False  # 범위 끝의 점은 다른 점으로 맞춰 볼 수 없다
    assert calc.outliers(y, cv_err=err, eligible=edge) == []


def test_numbers_are_readable():
    assert insights.n3(0.000462) == "0.00046" and insights.n3(2.606) == "2.61" and insights.n3(12345.6) == "12,346"
    assert insights.n3(0) == "0" and insights.n3(None) == "-"


def test_quality_bad_values_and_constant_output():
    rows = [_row(i + 1, {"a": 0.1 * i}, {"y": 3.0, "bad": "문자열", "nan": float("nan"), "none": None}) for i in range(8)]
    sec = insights.quality(Ctx(_snap([("a", 0, 1)], rows), True))
    assert sec["state"] == "caution" and "y" in sec["sentence"] and "모두 같습니다" in sec["sentence"]


def test_quality_constraint_pass_rate():
    v = [("a", 0, 1)]
    con = [{"name": "s", "upper": 5}]
    none = _snap(v, [_row(i + 1, {"a": i / 10}, {"s": 9.0 + i}) for i in range(8)], constraints=con)
    assert "하나도 없습니다" in insights.quality(Ctx(none, True))["sentence"]
    low = _snap(v, [_row(i + 1, {"a": i / 20}, {"s": 1.0 if i < 2 else 9.0 + i}) for i in range(12)], constraints=con)
    assert insights.quality(Ctx(low, True))["state"] == "fair"
    missing = _snap(v, [_row(i + 1, {"a": i / 10}, {"s": 1.0 + 0.1 * i} if i else {"y": 1.0}) for i in range(8)], constraints=con)
    assert any("판단하지 못한 케이스가 1건" in x for x in insights.quality(Ctx(missing, True))["notes"])


# ---------- 수렴 ----------
def _sequence(values, n_base=10, goal="min", fake_ei=0.0):
    """처음 n_base건은 DOE, 나머지는 최적화인 손으로 만든 이력."""
    rows = [_row(i + 1, {"a": (i % 10) / 10}, {"y": float(v)}, source="doe" if i < n_base else "bo") for i, v in enumerate(values)]
    return _snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": goal}])


@pytest.fixture
def no_model_ei(monkeypatch):
    """수렴 규칙만 시험한다. 모델이 보는 남은 여지(EI)는 값을 정해 넣는다."""
    def set_ei(value):
        monkeypatch.setattr(insights, "_ei_left", lambda ctx, m, o, now, sign: value)
        monkeypatch.setattr(insights.Ctx, "model_for", lambda self, o: ModelRow(1, 1, [o], {o: {"r2": 0.95, "rmse": 0.1}}, 40, "", None, False))
        monkeypatch.setattr(insights.Ctx, "surrogate", lambda self, m: None)
    return set_ei


def test_convergence_stop_advice(no_model_ei):
    no_model_ei(0.0)
    doe = [10, 9, 8, 7, 6, 5.5, 5.2, 5.1, 5.05, 5.0]
    # 최적화로 2.0까지 왔고 최근 5건은 거의 그대로다
    sec = insights.convergence(Ctx(_sequence(doe + [4, 3, 2.5, 2.1, 2.0, 2.0, 2.0, 2.0, 2.0, 1.999]), True))
    assert sec["state"] == "good" and "더 돌려도 효과가 작습니다" in sec["sentence"]
    assert "5" in sec["sentence"] and "최근 5건" in sec["sentence"]
    assert sec["facts"]["start"] == 5.0 and sec["facts"]["now"] == pytest.approx(1.999)


def test_convergence_still_improving(no_model_ei):
    no_model_ei(1.0)
    doe = [10, 9, 8, 7, 6, 5.5, 5.2, 5.1, 5.05, 5.0]
    sec = insights.convergence(Ctx(_sequence(doe + [4.8, 4.5, 4, 3.5, 3]), True))
    assert sec["state"] == "fair" and "아직 좋아지는 중" in sec["sentence"] and sec["next"]


def test_convergence_no_improvement(no_model_ei):
    no_model_ei(1.0)
    doe = [10, 9, 8, 7, 6, 5.5, 5.2, 5.1, 5.05, 5.0]
    sec = insights.convergence(Ctx(_sequence(doe + [5.5] * 10), True))
    assert sec["state"] == "caution" and "나아지지 않았습니다" in sec["sentence"]
    no_model_ei(0.0)
    sec = insights.convergence(Ctx(_sequence(doe + [5.5] * 6), True))
    assert sec["state"] == "fair" and "넘지 못했고" in sec["sentence"]


def test_convergence_too_few_is_unknown_with_count():
    doe = [10, 9, 8, 7, 6, 5.5, 5.2, 5.1, 5.05, 5.0]
    sec = insights.convergence(Ctx(_sequence(doe + [4, 3]), True))
    assert sec["state"] == "unknown" and "3건 더" in sec["sentence"]
    assert insights.convergence(Ctx(_sequence(doe), True))["sentence"] == insights.T["con_none"]


def test_convergence_max_goal(no_model_ei):
    no_model_ei(0.0)
    doe = [1, 2, 3, 4, 5, 5.5, 5.8, 5.9, 5.95, 6.0]
    sec = insights.convergence(Ctx(_sequence(doe + [7, 8, 8.5, 8.9, 9, 9, 9, 9, 9, 9.001], goal="max"), True))
    assert sec["state"] == "good" and sec["facts"]["now"] == pytest.approx(9.001) and "올라가다" in sec["how_to_read"]


def test_convergence_ignores_infeasible_best(no_model_ei):
    no_model_ei(0.0)
    rows = [_row(i + 1, {"a": i / 10}, {"y": 5.0 - 0.1 * i, "s": 1.0}) for i in range(10)]
    rows += [_row(11 + i, {"a": 0.5}, {"y": 0.1, "s": 99.0}, source="bo") for i in range(5)]  # 값은 좋지만 제약 위반
    snap = _snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], constraints=[{"name": "s", "upper": 2}])
    sec = insights.convergence(Ctx(snap, True))
    assert sec["facts"]["now"] == pytest.approx(4.1)


def test_percent_not_used_when_start_near_zero(no_model_ei):
    no_model_ei(0.0)
    doe = [10, 8, 6, 4, 2, 1, 0.5, 0.2, 0.1, 0.01]
    sec = insights.convergence(Ctx(_sequence(doe + [-1, -2, -3, -4, -5, -5, -5, -5, -5, -5.001]), True))
    assert sec["state"] == "good" and "%" not in sec["sentence"], sec["sentence"]


def test_convergence_nan_and_failed_cases_are_ignored(no_model_ei):
    no_model_ei(0.0)
    doe = [10, 9, 8, 7, 6, 5.5, 5.2, 5.1, 5.05, 5.0]
    snap = _sequence(doe + [4, 3, 2.5, 2.1, 2.0, 2.0, 2.0, 2.0, 2.0, 1.999])
    snap.cases.append(_row(90, {"a": 0.5}, {"y": float("nan")}, source="bo"))
    snap.cases.append(_row(91, {"a": 0.5}, {"y": -999.0}, source="bo", status="failed"))
    snap.cases.append(_row(92, {"a": 0.5}, {"y": "오류"}, source="bo"))
    sec = insights.convergence(Ctx(snap, True))
    assert sec["facts"]["now"] == pytest.approx(1.999)


# ---------- 모델 기반 ----------
def _fit(X, Y, tmp_path, name="m"):
    sur = Surrogate(variables=[f"x{i}" for i in range(X.shape[1])], outputs=list(Y))
    metrics = sur.fit(X, Y)
    path = tmp_path / f"{name}.joblib"
    sur.save(path)
    return sur, ModelRow(int(abs(hash(name)) % 10000), 1, list(Y), {o: {"r2": v["r2"], "rmse": v["rmse"]} for o, v in metrics.items()}, len(X), str(path), datetime(2026, 1, 1), True)


def _washer(U):
    k, c, b, p = U[:, 0] * 40 - 10, U[:, 1] * 60 - 10, U[:, 2] * 40 - 20, U[:, 3] * 40 - 20
    return 2.6 + 3.67 * (2 * ((k - 12) / 37.5) ** 2 + 1.6 * ((c - 25) / 65.8) ** 2) + 0.3 * ((b - 5) / 25) ** 2 + 0.15 * ((p - 5) / 20) ** 2


def _washer_snap(n, tmp_path, seed=3):
    U = qmc.LatinHypercube(d=4, seed=seed).random(n)
    y = _washer(U)
    _, m = _fit(U, {"y": y}, tmp_path, f"w{n}")
    rows = [_row(i + 1, {f"x{j}": float(U[i, j]) for j in range(4)}, {"y": float(y[i])}) for i in range(n)]
    return _snap([(f"x{j}", 0, 1) for j in range(4)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=int(np.argmin(y)) + 1, outputs=[{"name": "y", "note": "진동 [mm]"}])


def test_gp_influence_matches_known_washer_shares(tmp_path):
    """정답 T=(0.692, 0.292, 0.010, 0.006). GP를 거쳐도 재현되고 판정은 좋음이어야 한다."""
    snap = _washer_snap(60, tmp_path)
    ctx = Ctx(snap, True)
    sec = insights.influence(ctx)
    t = sec["facts"]["total_order"]
    assert np.allclose([t[f"x{i}"] for i in range(4)], [0.692, 0.292, 0.010, 0.006], atol=0.02), t
    assert sec["state"] == "good"
    assert "x0" in sec["sentence"] and "%" in sec["sentence"]
    assert sec["basis"] == "predicted"


def test_weak_model_gets_no_confident_ranking(tmp_path):
    """표본이 적어 순위가 틀어지는 경우(세탁기 16건). 확정 문장을 쓰지 않는다."""
    ctx = Ctx(_washer_snap(16, tmp_path), True)
    sec = insights.influence(ctx)
    assert sec["state"] in ("fair", "caution", "unknown")
    assert "좌우합니다" not in sec["sentence"]


def test_trust_plain_words_with_units_and_cv_matches_metrics(tmp_path):
    from sklearn.metrics import r2_score

    snap = _washer_snap(60, tmp_path)
    ctx = Ctx(snap, True)
    sec = insights.trust(ctx)
    assert "10번 중 8번" in sec["sentence"] and "mm" in sec["sentence"]
    sur = ctx.surrogate(snap.models[0])
    assert r2_score(sur.cv["y"]["actual"], sur.cv["y"]["cv_pred"]) == pytest.approx(snap.models[0].metrics["y"]["r2"])
    # 옛 모델(cv 필드 없음)은 저장된 파이프라인으로 같은 값을 다시 잰다
    old = {k: v for k, v in sur.cv["y"].items()}
    sur.cv = {}
    again = ctx.cv(sur, "y")
    assert r2_score(again["actual"], again["cv_pred"]) == pytest.approx(r2_score(old["actual"], old["cv_pred"]), abs=1e-6)
    assert sec["charts"][0]["kind"] == "pva" and len(sec["charts"][0]["worst"]) == 3


def test_trust_levels_follow_shared_boundaries():
    ctx = Ctx(_snap([("a", 0, 1)], []), True)
    m = lambda r2, n: ModelRow(1, 1, ["y"], {"y": {"r2": r2, "rmse": 1.0}}, n, "", None, False)  # noqa: E731
    assert ctx.level(m(0.95, 40), "y") == "good"
    assert ctx.level(m(0.95, 4), "y") == "unknown"
    assert ctx.level(m(0.95, 7), "y") == "fair"  # 표본이 적으면 한 단계 낮춘다
    assert ctx.level(m(0.8, 40), "y") == "fair"
    assert ctx.level(m(0.5, 40), "y") == "caution"


def test_assess_quality_agrees_if_present():
    """메인의 품질 판정이 합쳐지면 두 판정이 같아야 한다."""
    from server.app.study import service
    fn = getattr(service, "assess_quality", None)
    if fn is None:
        pytest.skip("assess_quality가 아직 이 브랜치에 없습니다")
    ctx = Ctx(_snap([("a", 0, 1)], []), True)
    mapping = {"good": "good", "fair": "fair", "poor": "caution"}
    for r2, n in [(0.95, 40), (0.95, 7), (0.8, 40), (0.5, 40)]:
        m = ModelRow(1, 1, ["y"], {"y": {"r2": r2, "rmse": 1.0}}, n, "", None, False)
        assert mapping[fn({"y": {"r2": r2}}, n, 1, "mock", [])["level"]] == ctx.level(m, "y")


def test_prior_prediction_uses_model_that_never_saw_the_case(tmp_path):
    U = qmc.LatinHypercube(d=2, seed=1).random(12)
    f = lambda A: (A[:, 0] - 0.3) ** 2 + A[:, 1]  # noqa: E731
    _, old = _fit(U, {"y": f(U)}, tmp_path, "old")
    old.version, old.created_at, old.n_train = 1, datetime(2026, 1, 1, 0, 0), 12
    extra = np.array([[0.9, 0.9], [0.1, 0.1]])
    allX = np.vstack([U, extra])
    _, new = _fit(allX, {"y": f(allX)}, tmp_path, "new")
    new.id, new.version, new.created_at = old.id + 1, 2, datetime(2026, 1, 1, 2, 0)
    rows = [_row(i + 1, {"a": float(r[0]), "b": float(r[1])}, {"y": float(f(r[None])[0])}, at=datetime(2025, 12, 1)) for i, r in enumerate(U)]
    rows += [_row(20 + i, {"a": float(r[0]), "b": float(r[1])}, {"y": float(f(r[None])[0])}, source="al", at=datetime(2026, 1, 1, 1, 0)) for i, r in enumerate(extra)]
    snap = _snap([("a", 0, 1), ("b", 0, 1)], rows, objectives=[{"name": "y"}], models=[new, old])
    prior = insights.prior_predictions(Ctx(snap, True), "y")
    assert [p["model_version"] for p in prior] == [1, 1] and all(p["n_train"] == 12 for p in prior)


def test_optimum_edge_warning_follows_influence(tmp_path):
    U = qmc.LatinHypercube(d=2, seed=1).random(20)
    # x0는 결과를 좌우하고 상한에서 최선, x1은 거의 영향이 없고 상한에 붙어 있다
    f = lambda A: -3 * A[:, 0] + 0.001 * A[:, 1]  # noqa: E731
    X = np.vstack([U, [[1.0, 1.0]]])
    _, m = _fit(X, {"y": f(X)}, tmp_path, "edge")
    rows = [_row(i + 1, {"x0": float(r[0]), "x1": float(r[1])}, {"y": float(f(r[None])[0])}) for i, r in enumerate(X)]
    snap = _snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=21)
    ctx = Ctx(snap, True)
    ctx.influence["y"] = {"facts": {"shares": {"x0": 0.99, "x1": 0.01}}}
    sec = insights.optimum(ctx)
    assert [e["variable"] for e in sec["facts"]["edges"]] == ["x0"]
    assert "범위를 넓혀" in " ".join([sec["sentence"], *sec["notes"]])


def test_optimum_margin_boundaries(monkeypatch):
    """여유 판정만 본다. 앞 항목에서 이어받는 제한은 다른 시험에서 본다."""
    monkeypatch.setattr(insights, "_optimum_caps", lambda ctx, o: [])
    v = [("a", 0, 1)]
    obj = [{"name": "y", "goal": "min"}]
    con = [{"name": "s", "upper": 100}]
    for s, state in [(90.0, "good"), (97.0, "fair"), (99.5, "caution")]:
        rows = [_row(1, {"a": 0.5}, {"y": 1.0, "s": s})] + [_row(i, {"a": 0.09 * i}, {"y": 2.0 + i, "s": 10.0}) for i in range(2, 10)]
        sec = insights.optimum(Ctx(_snap(v, rows, objectives=obj, constraints=con, best_id=1), True))
        assert sec["state"] == state, (s, sec["sentence"])
        assert sec["sentence"].startswith("지금 가장 좋은 설계(케이스 1"), sec["sentence"]
    assert "그대로 쓰지 마세요" in sec["sentence"] and sec["next"]["label"] == "여유 있는 설계 찾기 요청"


def test_robustness_flat_and_steep(tmp_path):
    def run(f, name):
        U = qmc.LatinHypercube(d=2, seed=1).random(30)
        X = np.vstack([U, [[0.5, 0.5]]])
        _, m = _fit(X, {"y": f(X)}, tmp_path, name)
        rows = [_row(i + 1, {"x0": float(r[0]), "x1": float(r[1])}, {"y": float(f(r[None])[0])}) for i, r in enumerate(X)]
        snap = _snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=len(X))
        return insights.optimum(Ctx(snap, True))
    flat = run(lambda A: (A[:, 0] - 0.5) ** 2 + (A[:, 1] - 0.5) ** 2, "flat")
    assert flat["facts"]["robust"]["width"] < 0.01 and flat["facts"]["robust"]["basis"] == "predicted"
    steep = run(lambda A: 5 * np.abs(A[:, 0] - 0.5) + 5 * np.abs(A[:, 1] - 0.5) + 2 * np.sin(40 * A[:, 0]) * 0, "steep")
    assert steep["facts"]["robust"]["width"] > flat["facts"]["robust"]["width"]


# ---------- 능동학습 ----------
def _al_snap(tmp_path, n_base=14, n_add=8, seed=1):
    """시작 점이 일부 구간에 몰려 있고 추가 점이 빈 곳을 채운다. 정답을 아니 오차가 줄어야 한다."""
    f = lambda A: np.sin(4 * A[:, 0]) + A[:, 1] ** 2  # noqa: E731
    base = qmc.LatinHypercube(d=2, seed=seed).random(n_base) * np.array([0.5, 1.0])
    add = qmc.LatinHypercube(d=2, seed=seed + 1).random(n_add) * np.array([0.5, 1.0]) + np.array([0.5, 0.0])
    X = np.vstack([base, add])
    _, m = _fit(X, {"y": f(X)}, tmp_path, f"al{seed}")
    rows = [_row(i + 1, {"x0": float(r[0]), "x1": float(r[1])}, {"y": float(f(r[None])[0])}, source="doe") for i, r in enumerate(base)]
    rows += [_row(100 + i, {"x0": float(r[0]), "x1": float(r[1])}, {"y": float(f(r[None])[0])}, source="al") for i, r in enumerate(add)]
    return _snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], outputs=[{"name": "y", "note": "진동 [mm]"}], best_id=1)


def test_learning_curve_error_goes_down_when_points_help():
    f = lambda A: np.sin(4 * A[:, 0]) + A[:, 1]  # noqa: E731
    from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
    kernel = ConstantKernel(1.0) * RBF(0.3) + WhiteKernel(1e-6)
    Xb = qmc.LatinHypercube(d=2, seed=1).random(20)
    Xa = qmc.LatinHypercube(d=2, seed=2).random(20)
    c = calc.learning_curve(Xb, f(Xb), Xa, f(Xa), kernel)
    assert c[0]["m"] == 0 and c[-1]["m"] == 20
    assert c[-1]["rmse"] < c[0]["rmse"]


def test_active_section_is_honest(tmp_path):
    sec = insights.active(Ctx(_al_snap(tmp_path), True))
    joined = " ".join([sec["sentence"], *sec["notes"]])
    assert sec["state"] in ("good", "fair", "caution")
    assert "더 돌린 8건" in sec["sentence"]
    assert "아무 데나 골랐을 때" in sec["sentence"] or insights.T["al_no_compare"] in sec["notes"] or "처음 계획한 점" in sec["sentence"]
    assert sec["facts"]["compared_with_random"] is False and "random" not in {k.lower() for k in sec["facts"]}
    assert "무작위보다" not in joined  # 재지 않은 비교를 말하지 않는다
    assert sec["basis"] == "predicted"


def test_active_flat_error_on_good_model_is_not_alarm(tmp_path, monkeypatch):
    monkeypatch.setattr(insights.calc, "learning_curve", lambda *a, **k: [{"m": 0, "rmse": 1.0, "r2": 0.99}, {"m": 8, "rmse": 0.99, "r2": 0.99}])
    snap = _al_snap(tmp_path, seed=11)
    snap.models[0].metrics["y"]["r2"] = 0.99
    snap.models[0].n_train = 22
    sec = insights.active(Ctx(snap, True))
    assert sec["state"] == "fair" and "이미 잘 맞아" in sec["sentence"]
    # 약한 모델이어도 '효과 없음'이라고 단정하지 않는다. 가린 점이 처음 점이라 범위 끝쪽 개선은 안 보일 수 있다
    monkeypatch.setattr(insights.Ctx, "verdict", lambda self, m, o: "caution")
    weak = insights.active(Ctx(snap, True))
    assert weak["state"] == "fair" and "드러나지 않습니다" in weak["sentence"] and "더 돌리기보다" not in weak["sentence"]


def test_active_too_few_is_unknown(tmp_path):
    assert insights.active(Ctx(_al_snap(tmp_path, n_add=2, seed=5), True))["state"] == "unknown"
    assert insights.active(Ctx(_al_snap(tmp_path, n_base=6, seed=7), True))["state"] == "unknown"
    plain = _snap([("a", 0, 1)], [_row(1, {"a": 0.5}, {"y": 1.0})])
    assert insights.active(Ctx(plain, True)) is None  # 돌리지 않았으면 줄 자체를 내지 않는다


def _no_error(out):
    assert not [x["key"] for x in out["sections"] if x["sentence"] == insights.T["error"]], out["sections"]
    for sec in out["sections"]:
        assert not any(q["sentence"] == insights.T["error"] for q in sec.get("variants") or []), sec


def test_one_variable_full_compute_with_model(tmp_path):
    U = qmc.LatinHypercube(d=1, seed=1).random(12)
    y = (U[:, 0] - 0.4) ** 2
    _, m = _fit(U, {"y": y}, tmp_path, "one")
    rows = [_row(i + 1, {"x0": float(u[0])}, {"y": float(v)}) for i, (u, v) in enumerate(zip(U, y))]
    rows += [_row(50 + i, {"x0": float(0.1 * i)}, {"y": float((0.1 * i - 0.4) ** 2)}, source="bo") for i in range(5)]
    out = insights.compute(_snap([("x0", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True)
    _no_error(out)
    inf = next(x for x in out["sections"] if x["key"] == "influence")
    assert inf["facts"]["shares"]["x0"] == pytest.approx(1.0) and "하나뿐" in inf["sentence"] and "0.4 근처" in inf["sentence"]


def test_constant_output_full_compute_with_model(tmp_path):
    U = qmc.LatinHypercube(d=2, seed=2).random(16)
    y = U[:, 0] + U[:, 1]
    z = np.full(16, 3.0)
    _, m = _fit(U, {"y": y, "z": z}, tmp_path, "const")
    rows = [_row(i + 1, {"x0": float(u[0]), "x1": float(u[1])}, {"y": float(a), "z": 3.0}) for i, (u, a) in enumerate(zip(U, y))]
    snap = _snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=[{"name": "z", "goal": "min"}, {"name": "y", "goal": "min"}], models=[m], best_id=1)
    out = insights.compute(snap, True)
    _no_error(out)
    by = {x["key"]: x for x in out["sections"]}
    assert by["influence"]["state"] == "unknown" and "거의 변하지 않아" in by["influence"]["sentence"]
    assert by["quality"]["state"] == "caution" and "모두 같습니다" in by["quality"]["sentence"]
    assert by["trust"]["variants"][0]["state"] == "unknown"


def test_nan_and_failed_cases_full_compute(tmp_path):
    U = qmc.LatinHypercube(d=2, seed=3).random(20)
    y = U[:, 0] ** 2 + U[:, 1]
    _, m = _fit(U, {"y": y}, tmp_path, "nan")
    rows = [_row(i + 1, {"x0": float(u[0]), "x1": float(u[1])}, {"y": float(a)}) for i, (u, a) in enumerate(zip(U, y))]
    rows += [_row(30, {"x0": 0.5, "x1": 0.5}, {"y": float("nan")}), _row(31, {"x0": 0.5, "x1": 0.6}, {"y": "오류"}), _row(32, {"x0": 0.5, "x1": 0.7}, {"y": None}),
             _row(33, {"x0": 0.5, "x1": 0.8}, {}, status="failed"), _row(34, {"x0": None, "x1": 0.8}, {"y": 1.0}), _row(35, {"x0": 0.2}, {"y": 1.0})]
    out = insights.compute(_snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=int(np.argmin(y)) + 1), True)
    _no_error(out)
    q = next(x for x in out["sections"] if x["key"] == "quality")
    assert q["facts"]["failed"] == 1
    import json
    json.dumps(out, allow_nan=False)  # 저장·응답에 NaN이 남지 않는다


# ---------- 정직성: 틀린 말을 하지 않는다 ----------
TWO_GOALS = [{"name": "a", "goal": "min"}, {"name": "b", "goal": "min"}]


def _two_goal_history(extra, model=None):
    """처음 12건은 앞면 (i, 12-i). extra는 최적화로 더한 (a, b) 값."""
    rows = [_row(i + 1, {"x": i / 12}, {"a": float(i), "b": float(12 - i)}) for i in range(12)]
    rows += [_row(100 + i, {"x": 0.5}, {"a": float(u), "b": float(v)}, source="bo") for i, (u, v) in enumerate(extra)]
    return _snap([("x", 0, 1)], rows, objectives=TWO_GOALS, models=[model] if model else [])


def test_multi_convergence_without_any_gain_is_not_called_good():
    """최적화가 후보를 전혀 낫게 하지 못했는데 '거의 다 찾음'이라고 하면 안 된다."""
    useless = [(11.0, 11.0)] * 5
    sec = insights.convergence(Ctx(_two_goal_history(useless), True))
    assert sec["state"] == "fair" and "나아지지 않았습니다" in sec["sentence"], sec["sentence"]
    sec = insights.convergence(Ctx(_two_goal_history([(11.0, 11.0)] * 10), True))
    assert sec["state"] == "caution" and "나아지지 않았습니다" in sec["sentence"], sec["sentence"]


def test_multi_convergence_stop_and_going_on_known_area():
    """처음 앞면은 계단이고 최적화 첫 점이 크게 늘린 뒤 멈춘다. 짧게 평평한 것만으로는 멈추라고 하지 않는다."""
    gain_then_flat = [(0.5, 0.5)] + [(11.0, 11.0)] * 5
    sec = insights.convergence(Ctx(_two_goal_history(gain_then_flat), True))
    assert sec["state"] == "fair" and "확인할 만합니다" in sec["sentence"] and "더 돌려도 효과가 작습니다" not in sec["sentence"], sec["sentence"]
    assert "후보 범위" not in sec["sentence"]
    going = [(11.0, 11.0)] * 4 + [(0.5, 0.5)]
    sec = insights.convergence(Ctx(_two_goal_history(going), True))
    assert sec["state"] == "fair" and sec["facts"]["improving"] and "나아지는 중" in sec["sentence"]
    assert sec["charts"][0]["bo_from"] == 13 and "종합 점수" in sec["charts"][0]["axis"] and sec["charts"][0]["zoom"]


def test_multi_convergence_stops_only_with_enough_runs_and_a_good_model(monkeypatch):
    """창의 2배 이상 돌렸고, 예측으로 봐도 남은 몫이 작고, 예측이 좋을 때만 멈추라고 한다."""
    good = ModelRow(1, 1, ["a", "b"], {"a": {"r2": 0.95, "rmse": 1.0}, "b": {"r2": 0.95, "rmse": 1.0}}, 40, "", None, False)
    monkeypatch.setattr(insights.Ctx, "model_for", lambda self, o: good)
    monkeypatch.setattr(insights, "_hv_left", lambda *a: 0.001)
    long_flat = [(0.5, 0.5)] + [(11.0, 11.0)] * 11
    sec = insights.convergence(Ctx(_two_goal_history(long_flat, good), True))
    assert sec["state"] == "good" and "더 돌려도 효과가 작습니다" in sec["sentence"], sec["sentence"]
    assert any("놓친" in n for n in sec["notes"])  # 확정처럼 말하지 않는다
    monkeypatch.setattr(insights, "_hv_left", lambda *a: 0.3)
    assert insights.convergence(Ctx(_two_goal_history(long_flat, good), True))["state"] == "fair"
    short = [(0.5, 0.5)] + [(11.0, 11.0)] * 5
    monkeypatch.setattr(insights, "_hv_left", lambda *a: 0.001)
    assert insights.convergence(Ctx(_two_goal_history(short, good), True))["state"] == "fair"


def test_multi_convergence_poor_model_cannot_say_it_is_finished(monkeypatch):
    """모델이 틀리면 평평해 보여도 '다 찾았다'고 말하지 않는다. 한 목표일 때와 같은 규칙이다."""
    poor = ModelRow(1, 1, ["a", "b"], {"a": {"r2": 0.3, "rmse": 1.0}, "b": {"r2": 0.95, "rmse": 1.0}}, 40, "", None, False)
    monkeypatch.setattr(insights.Ctx, "model_for", lambda self, o: poor)
    sec = insights.convergence(Ctx(_two_goal_history([(0.5, 0.5)] + [(11.0, 11.0)] * 5), True))
    assert sec["state"] == "caution" and sec["sentence"] == insights.T["con_poor"]
    assert sec["next"]["label"] == "정확도 높이는 해석 요청"


def test_start_value_is_the_real_first_doe_not_active_learning(no_model_ei):
    """'처음 DOE 최고'는 정말 DOE 값이어야 한다. 능동학습이 먼저 찾은 값을 최적화 개선으로 치지도 않는다."""
    no_model_ei(0.0)
    rows = [_row(i + 1, {"a": i / 10}, {"y": 5.0 + i}) for i in range(10)]  # DOE 최고 5
    rows += [_row(20 + i, {"a": 0.5}, {"y": 3.0 + i}, source="al") for i in range(3)]  # 능동학습이 3을 찾았다
    rows += [_row(30 + i, {"a": 0.5}, {"y": 3.5}, source="bo") for i in range(5)]  # 최적화는 3을 못 넘었다
    sec = insights.convergence(Ctx(_snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}]), True))
    assert sec["facts"]["start"] == 5.0 and sec["facts"]["now"] == 3.0
    assert sec["facts"]["recent"] == 0.0, sec["facts"]  # 최적화 5건의 개선은 0이다


def test_optimum_ignores_nan_picked_as_best():
    """결과에 NaN이 있으면 best_case가 그 케이스를 고를 수 있다. '해석이 없다'고 잘못 말하지 않고 값이 있는 최고를 점검한다."""
    rows = [_row(1, {"a": 0.1}, {"y": float("nan")})] + [_row(i + 2, {"a": 0.2 + i / 20}, {"y": 3.0 + i}) for i in range(6)]
    sec = insights.optimum(Ctx(_snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], best_id=1), True))
    assert sec["facts"]["case_id"] == 2 and sec["facts"]["value"] == 3.0, sec["sentence"]


def test_multi_objective_suggestion_skips_nan_results(monkeypatch):
    """NaN이 범위 계산에 섞이면 점수가 모두 0이 되어 임의의 점을 고른다. 값이 있는 케이스만 넘겨야 한다."""
    from types import SimpleNamespace

    from server.app.study import service
    seen = {}
    monkeypatch.setattr(bayesopt, "suggest", lambda *a, **k: seen.update(k) or np.zeros((1, 1)))
    cases = [SimpleNamespace(status="complete", results={"a": float(i), "b": 5.0 - i}) for i in range(4)]
    cases.append(SimpleNamespace(status="complete", results={"a": float("nan"), "b": 1.0}))
    cases.append(SimpleNamespace(status="complete", results={"a": float("inf"), "b": 1.0}))
    cases.append(SimpleNamespace(status="complete", results={"a": True, "b": 1.0}))
    study = SimpleNamespace(objectives=TWO_GOALS, constraints=[], cases=cases)
    service._suggest_multi(study, SimpleNamespace(outputs=["a", "b"]), np.zeros(1), np.ones(1), 1)
    assert seen["observed"].shape == (4, 2) and np.isfinite(seen["observed"]).all()


def test_doe_many_points_with_failures_never_builds_a_full_distance_matrix(monkeypatch):
    """표가 커도 계산이 멈추면 안 된다. 불러온 표는 20만 행까지 가능하고, 그 거리 행렬은 메모리에 들어가지 않는다.
    실패가 섞여도 거리 계산은 500점까지만 한다."""
    real, sizes = calc.doe_stats, []
    monkeypatch.setattr(calc, "doe_stats", lambda U, levels=None: sizes.append(len(U)) or real(U, levels))
    U = qmc.LatinHypercube(d=3, seed=7).random(1200)
    rows = [_row(i + 1, dict(zip("abc", map(float, r))), {"y": float(r[0])}) for i, r in enumerate(U[:1100])]
    rows += [_row(i + 30000, dict(zip("abc", map(float, r))), {}, status="failed") for i, r in enumerate(U[1100:])]
    sec = insights.coverage(Ctx(_snap(VARS4[:3], rows), True))
    assert sizes and max(sizes) <= 500, sizes
    assert sec["state"] != "unknown" and any("500건" in n or "500" in n for n in sec["notes"]), sec["notes"]


def test_failure_hole_is_reported_when_failures_empty_a_range():
    """실패 때문에 빈 구간은 계획에는 있었던 구간이다. 그 사실을 말한다."""
    xs = np.linspace(0.01, 0.99, 20)
    rows = [_row(i + 1, {"a": float(x)}, {"y": float(x)}) for i, x in enumerate(xs) if not 0.3 < x < 0.7]
    rows += [_row(100 + i, {"a": float(x)}, {}, status="failed") for i, x in enumerate(xs) if 0.3 < x < 0.7]
    sec = insights.coverage(Ctx(_snap([("a", 0, 1)], rows), True))
    assert any("때문에" in n and "구간이 비었습니다" in n for n in sec["notes"]), sec["notes"]


def _button_labels(out):
    """접어도 펼쳐도 화면에 실제로 보이는 버튼 이름들."""
    labels = [out["overview"]["next"]["label"]] if out["overview"]["next"] and out["overview"]["next"]["label"] else []
    for x in out["sections"]:
        for holder in [x, *(x.get("variants") or [])]:
            nx = holder.get("next")
            if nx and not holder.get("next_shown_above"):
                labels.append(nx["label"])
    return labels


def test_same_action_has_one_button_when_model_is_poor(tmp_path):
    """모델이 약하면 영향도·정확도 구역이 같은 '점 추가'를 말한다. 버튼은 맨 위 하나만 남는다."""
    U = qmc.LatinHypercube(d=2, seed=1).random(24)
    noise = np.random.default_rng(0).normal(size=24)
    _, m = _fit(U, {"y": noise}, tmp_path, "noise")
    rows = [_row(i + 1, {"x0": float(u[0]), "x1": float(u[1])}, {"y": float(v)}) for i, (u, v) in enumerate(zip(U, noise))]
    out = insights.compute(_snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=int(np.argmin(noise)) + 1), True)
    assert out["overview"]["next"]["key"] == "poor"
    labels = _button_labels(out)
    assert labels.count("정확도 높이는 해석 요청") == 1, labels


def test_tradeoff_has_no_percent_when_value_is_near_zero():
    """기준값이 0에 가까우면 '10%'는 뜻이 없다(0.001의 10%). 단위 값으로 말한다."""
    pts = [(-10.0, 10.0), (0.001, 2.0), (10.0, 1.0), (20.0, 0.5), (30.0, 30.0)]  # 마지막은 앞면 밖. 균형 후보는 a≈0
    rows = [_row(i + 1, {"x": i / 10}, {"a": u, "b": v}) for i, (u, v) in enumerate(pts)]
    sec = insights.pareto(Ctx(_snap([("x", 0, 1)], rows, objectives=TWO_GOALS), True))
    t = sec["facts"]["tradeoff"]
    assert sec["facts"]["knee_case"] == 2 and t["a"] == "a"
    assert t["a_percent"] is None and "%" not in sec["sentence"], sec["sentence"]
    assert t["a_change"] == pytest.approx(3.0, rel=0.01)  # 앞면 폭 30의 10%
    assert t["b_change"] == pytest.approx(8 * 3 / 10.001, rel=0.01)  # (0.001, 2)와 (-10, 10) 사이 직선 보간


def test_tradeoff_turns_to_the_other_goal_when_almost_no_room_is_left():
    """균형 후보가 첫 목표의 끝에 붙어 있으면 '0.001 더 줄이면'은 쓸모가 없다. 다른 목표 쪽으로 말한다."""
    pts = [(0.001, 9.0), (0.002, 5.0), (0.003, 3.0), (8.0, 1.0), (16.0, 0.5)]
    rows = [_row(i + 1, {"x": i / 10}, {"a": u, "b": v}) for i, (u, v) in enumerate(pts)]
    sec = insights.pareto(Ctx(_snap([("x", 0, 1)], rows, objectives=TWO_GOALS), True))
    assert sec["facts"]["tradeoff"]["a"] == "b", sec["facts"]["tradeoff"]


def test_imported_data_never_claims_a_solver_run():
    """표로 불러온 값은 해석으로 확인한 값이 아니다. 어느 문장에도 그렇게 쓰지 않는다."""
    U = qmc.LatinHypercube(d=2, seed=1).random(24)
    rows = [_row(i + 1, {"x0": float(u[0]), "x1": float(u[1])}, {"y": float(u[0] + u[1])}, source="import") for i, u in enumerate(U)]
    out = insights.compute(_snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], adapter="data", best_id=1), True)
    assert out["overview"]["word"] == "데이터"
    for x in out["sections"]:
        for text in [x["sentence"], x["how_to_read"], *x["notes"]]:
            assert "해석으로 확인" not in text and "해석 확인" not in text and "해석값" not in text, text
    assert all(x["basis"] == "imported" for x in out["sections"] if x["key"] in ("coverage", "quality"))


def test_planned_points_are_not_described_as_solved():
    """돌리기 전에는 점을 계획했을 뿐이다. '살폈습니다'나 '해석으로 확인한 값'이라고 쓰지 않는다."""
    U = qmc.LatinHypercube(d=2, seed=4).random(20)
    snap = _snap(VARS4[:2], [_row(i + 1, {"a": float(r[0]), "b": float(r[1])}, {}, status="pending") for i, r in enumerate(U)])
    sec = insights.coverage(Ctx(snap, True))
    assert "살폈습니다" not in sec["sentence"], sec["sentence"]
    assert sec["basis"] == "planned" and "해석" not in sec["basis_label"].replace("아직 해석 전", "")


def test_all_failed_is_not_described_as_planned():
    """모두 실패했으면 돌린 것이다. '아직 해석 전'이라고 쓰면 안 된다."""
    rows = [_row(i + 1, {"a": i / 8}, {}, status="failed") for i in range(8)]
    sec = insights.coverage(Ctx(_snap([("a", 0, 1)], rows), True))
    assert sec["basis"] == "verified" and sec["state"] == "unknown"


def test_few_failures_do_not_claim_they_are_spread_out():
    """실패 1~2건으로는 몰렸는지 알 수 없다. 재지 않은 것을 말하지 않는다."""
    rows = [_row(i + 1, {"a": i / 20}, {"y": float(i)}) for i in range(19)] + [_row(40, {"a": 0.5}, {}, status="failed")]
    sec = insights.quality(Ctx(_snap([("a", 0, 1)], rows), True))
    assert sec["state"] == "fair" and "실패가 1건" in sec["sentence"] and "몰리지" not in sec["sentence"], sec["sentence"]


def test_spread_is_compared_with_standard_and_random_layouts():
    """'무작위·표준 배치와 비교한 값'을 펼치면 볼 수 있어야 한다."""
    sec = insights.coverage(Ctx(_doe(qmc.LatinHypercube(d=4, seed=1).random(40)), True))
    note = next((n for n in sec["notes"] if "아무렇게나" in n or "고른 배치" in n), None)
    assert note and "LHS" in note and "치우침" not in note and "고릅니다" not in note, sec["notes"]
    assert sec["facts"]["cd_random_median"] and sec["facts"]["cd_lhs_median"]  # 숫자는 AI가 물으면 답하도록 남긴다


def test_memory_keeps_only_recent_results():
    for i in range(insights.MEMORY_LIMIT + 5):
        insights._store(100000 + i, {"schema": insights.SCHEMA}, to_disk=False)
    assert len(insights._memory) <= insights.MEMORY_LIMIT and 100000 not in insights._memory
    for i in range(insights.MEMORY_LIMIT + 5):
        insights._memory.pop(100000 + i, None)


def test_benchmark_is_labelled_and_computed_from_real_code(tmp_path):
    f = {"쉬운 함수": (lambda X: X[:, 0] ** 2 + X[:, 1], 2)}
    v = insights.run_benchmark(seeds=1, n_start=8, n_add=2, functions=f)
    assert v["total"] == 1 and "쉬운 함수" in v["functions"]
    line = insights.bench_line(v)
    assert line.startswith("연습 함수 기준(") and "내 결과가 아닙니다" in line
    assert insights.bench_line(None) is None  # 값이 없으면 줄 자체를 내지 않는다
    assert "항상 낫지는 않습니다" in insights.bench_line({"functions": {"a": {}}, "total": 3, "wins": 7, "runs": 15})
    assert "같다는 뜻은 아닙니다" in insights.bench_line({"functions": {"a": {}}, "total": 3, "wins": 15, "runs": 15})


# ---------- 획득함수 ----------
class _Fake:
    outputs = ["y"]

    def predict(self, X):
        m = ((X - 0.3) ** 2).sum(axis=1)
        return {"y": m}, {"y": np.full(len(X), 0.05)}


@pytest.mark.parametrize("scale", [1e-4, 1e-3, 1.0, 1e3])
def test_ei_is_scale_free(scale):
    """출력 단위가 바뀌어도 같은 곳을 고른다. 전에는 값이 작으면 EI가 모두 0이 되어 임의의 점을 골랐다."""
    class S(_Fake):
        def predict(self, X):
            m, s = _Fake.predict(self, X)
            return {"y": m["y"] * scale}, {"y": s["y"] * scale}
    lo, hi = np.zeros(2), np.ones(2)
    p = bayesopt.suggest(S(), lo, hi, "bo", 1, "y", "min", y_best=0.05 * scale, seed=1)[0]
    assert np.linalg.norm(p - 0.3) < 0.2, (scale, p)


def test_multi_objective_score_respects_max_goal():
    """'b를 크게'는 '-b를 작게'와 같은 문제다. 점수가 정확히 같아야 한다."""
    rng = np.random.default_rng(0)
    mean_a, mean_b = rng.random(50), rng.random(50)
    std = {"a": np.full(50, 0.1), "b": np.full(50, 0.2)}
    obs = rng.random((12, 2))
    low = bayesopt.chebyshev_ei({"a": mean_a, "b": mean_b}, std, TWO_GOALS, obs, seed=3)
    high = bayesopt.chebyshev_ei({"a": mean_a, "b": -mean_b}, std, [{"name": "a", "goal": "min"}, {"name": "b", "goal": "max"}], obs * [1, -1], seed=3)
    assert np.allclose(low, high) and low.max() > 0


def test_multi_objective_beats_first_goal_only_on_two_spheres():
    """두 구 함수에서 함께 최적화한 앞면이 첫 목표만 본 것보다 넓다(초부피)."""
    f1 = lambda X: ((X - 0.25) ** 2).sum(axis=1)  # noqa: E731
    f2 = lambda X: ((X - 0.75) ** 2).sum(axis=1)  # noqa: E731
    objs = [{"name": "a", "goal": "min"}, {"name": "b", "goal": "min"}]
    lo, hi = np.zeros(2), np.ones(2)
    start = qmc.LatinHypercube(d=2, seed=1).random(10)

    def run(mode):
        X = start.copy()
        for i in range(10):
            sur = Surrogate(variables=["x", "y"], outputs=["a", "b"])
            sur.models = {"a": __import__("server.app.study.surrogate", fromlist=["_make_gp"])._make_gp(2).fit(X, f1(X)),
                          "b": __import__("server.app.study.surrogate", fromlist=["_make_gp"])._make_gp(2).fit(X, f2(X))}
            if mode == "mo":
                obs = np.column_stack([f1(X), f2(X)])
                p = bayesopt.suggest(sur, lo, hi, "mo", 1, seed=i, objectives=objs, observed=obs, n_candidates=1024)
            else:
                p = bayesopt.suggest(sur, lo, hi, "bo", 1, "a", "min", y_best=float(f1(X).min()), seed=i, n_candidates=1024)
            X = np.vstack([X, p])
        return calc.hypervolume(np.column_stack([f1(X), f2(X)]), np.array([1.5, 1.5]))
    assert run("mo") > run("bo")


# ---------- 재검토에서 찾은 것: 첫 줄이 틀린 결정으로 이끌지 않는다 ----------
def _ishigami_snap(tmp_path, seed, name="ish"):
    U = qmc.LatinHypercube(d=3, seed=seed).random(40)
    y = ishigami(U)
    _, m = _fit(U, {"y": y}, tmp_path, f"{name}{seed}")
    rows = [_row(i + 1, {f"x{j}": float(U[i, j]) for j in range(3)}, {"y": float(y[i])}) for i in range(40)]
    return _snap([(f"x{j}", 0, 1) for j in range(3)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=int(np.argmin(y)) + 1)


def _section_of(out, key):
    return next(x for x in out["sections"] if x["key"] == key)


def test_optimum_is_not_good_when_the_search_never_happened(tmp_path):
    """무작위 DOE의 최고점을 답으로 내주면 안 된다(Ishigami 40건: 정답 약 -10.7인데 '좋음'이었다)."""
    for seed in range(3):
        out = insights.compute(_ishigami_snap(tmp_path, seed), True)
        opt = _section_of(out, "optimum")
        assert opt["state"] == "caution" and opt["state_label"] == "아직 쓰지 마세요", opt["sentence"]
        assert opt["sentence"].startswith("아직 정하지 마세요") and "케이스" in opt["sentence"]
        assert out["overview"]["next"]["key"] == "poor"


def test_optimum_too_few_cases_gives_no_verdict():
    U = qmc.LatinHypercube(d=12, seed=0).random(8)
    names = [f"x{i}" for i in range(12)]
    rows = [_row(i + 1, dict(zip(names, map(float, u))), {"y": float(u.sum())}) for i, u in enumerate(U)]
    out = insights.compute(_snap([(n, 0, 1) for n in names], rows, objectives=[{"name": "y", "goal": "min"}], best_id=1), True)
    opt = _section_of(out, "optimum")
    assert opt["state"] == "unknown" and "52건 더" in opt["sentence"]
    # 학습·최적화보다 건수가 먼저다
    assert out["overview"]["next"]["key"] == "count" and "60건" in out["overview"]["next"]["text"]
    assert "60건쯤" in _section_of(out, "trust")["sentence"]


def test_optimum_without_optimization_is_capped():
    """예측이 좋아도 최적화를 안 돌렸으면 '써도 됨'이 아니다."""
    v = [("a", 0, 1)]
    rows = [_row(i + 1, {"a": 0.05 + i / 12}, {"y": 3.0 + (i - 5) ** 2}) for i in range(11)]
    sec = insights.optimum(Ctx(_snap(v, rows, objectives=[{"name": "y", "goal": "min"}], best_id=6), True))
    assert sec["state"] == "fair" and "처음 계획한 해석 중 가장 좋은 값일 뿐" in sec["sentence"], sec["sentence"]


def test_random_failures_are_not_called_a_cluster():
    """구간을 많이 살피면 무작위 실패도 어딘가에는 몰려 보인다. 건수가 적으면 몰렸다고 말하지 않는다."""
    for n, d, f in [(30, 4, 3), (30, 8, 4), (64, 8, 4), (100, 12, 5)]:
        hits = 0
        for t in range(300):
            rng = np.random.default_rng(t)
            U = qmc.LatinHypercube(d=d, seed=t).random(n)
            mask = np.zeros(n, dtype=bool)
            mask[rng.choice(n, f, replace=False)] = True
            hits += calc.failure_cluster(U, mask) is not None
        assert hits / 300 <= 0.05, (n, d, f, hits)


def test_few_failures_in_one_spot_are_not_called_spread_out():
    """모두 한 구간이어도 우연으로 설명되는 건수면 '몰리지 않았다'고도 말하지 않는다."""
    xs = np.linspace(0.02, 0.98, 30)
    names = list("abcd")
    rows = [_row(i + 1, {**{n: float(xs[(i * (k + 3)) % 30]) for k, n in enumerate(names)}}, {"y": float(i)}) for i in range(27)]
    rows += [_row(100 + i, {n: 0.1 for n in names}, {}, status="failed") for i in range(3)]
    sec = insights.quality(Ctx(_snap([(n, 0, 1) for n in names], rows), True))
    assert "건수가 적어" in sec["sentence"] and "뚜렷하게" not in sec["sentence"], sec["sentence"]


def _clustered_bo_snap(tmp_path, seed=0):
    """처음 점만으로는 잘 못 맞히는 물결 함수 + 한곳에 몰린 최적화 점. 몰린 점끼리는 서로 잘 맞힌다."""
    f = lambda A: np.sin(14 * A[:, 0]) * np.cos(11 * A[:, 1]) + 0.5 * A[:, 2]  # noqa: E731
    U = qmc.LatinHypercube(d=3, seed=seed).random(30)
    B = np.clip(np.array([0.3, 0.3, 0.3]) + 0.03 * np.random.default_rng(seed).standard_normal((60, 3)), 0, 1)
    X = np.vstack([U, B])
    y = f(X)
    _, m = _fit(X, {"y": y}, tmp_path, f"bo{seed}")
    rows = [_row(i + 1, {f"x{j}": float(X[i, j]) for j in range(3)}, {"y": float(y[i])}, source="doe" if i < 30 else "bo") for i in range(90)]
    return _snap([(f"x{j}", 0, 1) for j in range(3)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=int(np.argmin(y)) + 1, outputs=[{"name": "y", "note": "진동 [mm]"}])


def test_trust_is_measured_without_the_clustered_optimization_points(tmp_path):
    """몰린 최적화 점을 포함하면 정확도가 부풀려진다. 믿어도 되냐는 질문은 설계 범위 전체에 대한 것이다."""
    snap = _clustered_bo_snap(tmp_path)
    ctx = Ctx(snap, True)
    m = snap.models[0]
    sc = ctx.score(m, "y")
    assert sc["dropped"] == 60 and sc["n"] == 30
    assert sc["r2"] < ctx.r2(m, "y") - 0.2, (sc["r2"], ctx.r2(m, "y"))
    out = insights.compute(snap, True)
    tr = _section_of(out, "trust")
    assert tr["state"] == "caution", tr["sentence"]
    assert any("최적화로 더한 60건" in n for n in tr["notes"])
    assert tr["facts"]["r2_all_points"] > tr["facts"]["r2"]
    assert _section_of(out, "influence")["state"] == "caution"  # 영향 순위도 같은 판단을 따른다
    assert out["overview"]["next"]["key"] == "poor"
    # 그림에서도 뺀 점을 구분한다
    pva = next(c for c in tr["charts"] if c["kind"] == "pva")
    assert sum(1 for p in pva["points"] if p[3] is False) == 60


def test_nearly_constant_output_does_not_block_forever(tmp_path):
    """값이 거의 일정하면 R²는 0에 가깝지만 오차는 무시할 만하다. 점을 더 넣어도 R²는 오르지 않으니 그쪽으로 보내면 안 된다."""
    U = qmc.LatinHypercube(d=3, seed=0).random(40)
    z = U[:, 0] ** 2 + U[:, 1]
    mass = 100 + 0.001 * np.random.default_rng(0).standard_normal(40)
    _, m = _fit(U, {"z": z, "mass": mass}, tmp_path, "flat")
    rows = [_row(i + 1, {f"x{j}": float(U[i, j]) for j in range(3)}, {"z": float(z[i]), "mass": float(mass[i])}) for i in range(40)]
    make = lambda upper: _snap([(f"x{j}", 0, 1) for j in range(3)], rows, objectives=[{"name": "z", "goal": "min"}], constraints=[{"name": "mass", "upper": upper}], models=[m], best_id=int(np.argmin(z)) + 1)  # noqa: E731
    out = insights.compute(make(200), True)
    tr = _section_of(out, "trust")
    v = next(q for q in tr["variants"] if q["name"] == "mass")
    assert v["state"] == "good" and "거의 일정" in v["sentence"], v["sentence"]
    assert out["overview"]["next"]["key"] != "poor"
    # 제약 경계가 그 오차 안에 있으면 무시할 수 없다
    tight = insights.compute(make(100.001), True)
    assert next(q for q in _section_of(tight, "trust")["variants"] if q["name"] == "mass")["state"] == "caution"


def test_small_units_are_not_called_constant(tmp_path):
    """미터 단위 변위처럼 값이 아주 작아도 일정한 것이 아니다."""
    U = qmc.LatinHypercube(d=2, seed=0).random(40)
    y = 1e-10 * (1 + U[:, 0] + U[:, 1] ** 2)
    _, m = _fit(U, {"y": y}, tmp_path, "tiny")
    rows = [_row(i + 1, {"x0": float(U[i, 0]), "x1": float(U[i, 1])}, {"y": float(y[i])}) for i in range(40)]
    out = insights.compute(_snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True)
    assert "모두 같습니다" not in _section_of(out, "quality")["sentence"]
    assert "변하지 않아" not in _section_of(out, "influence")["sentence"]
    assert "변하지 않아" not in _section_of(out, "trust")["sentence"]
    assert insights.is_constant([3.0] * 5) and insights.is_constant([0.0] * 5) and not insights.is_constant([1e-10, 3e-10])


def test_trust_needs_the_minimum_count():
    """몇 건으로 잰 80백분위는 우연이다."""
    m = ModelRow(1, 1, ["y"], {"y": {"r2": 0.95, "rmse": 1.0}}, 8, "", None, False)
    ctx = Ctx(_snap([(f"x{i}", 0, 1) for i in range(3)], []), True)  # 변수 3개면 최소 15건
    assert ctx.verdict(m, "y") == "unknown"
    assert ctx.level(m, "y") == "fair"  # assess_quality와 맞춘 등급은 그대로다


def test_direction_is_not_stated_when_it_depends_on_another_variable(tmp_path):
    """y = (x0-0.5)(x1-0.5): x0을 올리면 x1 값에 따라 늘기도 줄기도 한다. 한쪽으로 단정하면 설계를 나쁘게 만든다."""
    U = qmc.LatinHypercube(d=2, seed=0).random(60)
    y = (U[:, 0] - 0.5) * (U[:, 1] - 0.5)
    _, m = _fit(U, {"y": y}, tmp_path, "inter")
    rows = [_row(i + 1, {"x0": float(U[i, 0]), "x1": float(U[i, 1])}, {"y": float(y[i])}) for i in range(60)]
    sec = insights.influence(Ctx(_snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True))
    text = " ".join([sec["sentence"], *sec["notes"]])
    assert "올리면 y가 줄어듭니다" not in text and "올리면 y가 늘어납니다" not in text, text
    assert "달라집니다" in text
    assert {d["kind"] for d in sec["facts"]["directions"].values()} == {"depends"}


def test_tied_inputs_are_not_separated(tmp_path):
    """x1이 x0을 따라 움직이면 어느 쪽 효과인지 나눌 수 없다. y와 관계없는 x1을 1위로 말하면 안 된다."""
    for s in range(3):
        rng = np.random.default_rng(s)
        U = qmc.LatinHypercube(d=3, seed=s).random(40)
        U[:, 1] = np.clip(U[:, 0] + 0.02 * rng.standard_normal(40), 0, 1)
        y = 2 * U[:, 0] + 0.3 * U[:, 2] + 0.12 * rng.standard_normal(40)
        _, m = _fit(U, {"y": y}, tmp_path, f"tied{s}")
        rows = [_row(i + 1, {f"x{j}": float(U[i, j]) for j in range(3)}, {"y": float(y[i])}) for i in range(40)]
        sec = insights.influence(Ctx(_snap([(f"x{j}", 0, 1) for j in range(3)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True))
        assert sec["state"] != "good" and "나눠 볼 수 없습니다" in sec["sentence"], sec["sentence"]


def test_influence_does_not_state_a_direction_from_the_unsampled_range(tmp_path):
    """x0을 낮은 30%에서만 돌렸으면 나머지 70%는 예측으로 이어 그린 곡선이다. 방향은 해석이 있는 구간에서만 말한다."""
    U = qmc.LatinHypercube(d=2, seed=0).random(40)
    U[:, 0] *= 0.3
    y = np.sin(6 * U[:, 0]) + U[:, 1]
    _, m = _fit(U, {"y": y}, tmp_path, "gap")
    rows = [_row(i + 1, {"x0": float(U[i, 0]), "x1": float(U[i, 1])}, {"y": float(y[i])}) for i in range(40)]
    sec = insights.influence(Ctx(_snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=[{"name": "y", "goal": "max"}], models=[m], best_id=1), True))
    assert sec["state"] == "fair" and "해석이 없어" in sec["sentence"], sec["sentence"]
    assert "늘어납니다" in sec["notes"][0] and "구간에서 그렇습니다" in sec["notes"][0], sec["notes"]
    assert sec["charts"][0]["effects"]["x0"]["seen"][1] < 0.31


def test_weak_model_names_no_variable_on_noise_and_no_interaction(tmp_path):
    rng = np.random.default_rng(1)
    U = qmc.LatinHypercube(d=4, seed=1).random(40)
    y = rng.standard_normal(40)
    _, m = _fit(U, {"y": y}, tmp_path, "noise2")
    rows = [_row(i + 1, {f"x{j}": float(U[i, j]) for j in range(4)}, {"y": float(y[i])}) for i in range(40)]
    sec = insights.influence(Ctx(_snap([(f"x{j}", 0, 1) for j in range(4)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True))
    assert sec["state"] == "caution" and "뚜렷하게" in sec["sentence"] and not sec["notes"], (sec["sentence"], sec["notes"])
    assert sec["charts"][0]["weak"] is True


def test_closed_row_reports_a_weak_second_goal(tmp_path, monkeypatch):
    """첫 출력만 말하면 다른 목표의 약한 판단이 가려진다."""
    U = qmc.LatinHypercube(d=2, seed=2).random(30)
    a, b = U[:, 0] + 0.5 * U[:, 1], np.random.default_rng(0).standard_normal(30)
    _, m = _fit(U, {"a": a, "b": b}, tmp_path, "two")
    rows = [_row(i + 1, {"x0": float(U[i, 0]), "x1": float(U[i, 1])}, {"a": float(a[i]), "b": float(b[i])}) for i in range(30)]
    sec = insights.influence(Ctx(_snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=TWO_GOALS, models=[m], best_id=1), True))
    assert sec["state"] == "caution" and "(b의 순위는 예측이 약해 믿기 어렵습니다.)" in sec["sentence"], sec["sentence"]


def test_conflict_survives_one_bad_value():
    """이상한 값 하나가 전체 폭을 늘려도 실제 상충을 '안 부딪힌다'고 말하면 안 된다."""
    U = qmc.LatinHypercube(d=2, seed=0).random(40)
    a, b = U[:, 0] + 0.3 * U[:, 1], 1 - U[:, 0] + 0.3 * U[:, 1]
    b_bad = b.copy()
    b_bad[5] = 100.0
    for bb in (b, b_bad):
        rows = [_row(i + 1, {"x0": float(U[i, 0]), "x1": float(U[i, 1])}, {"a": float(a[i]), "b": float(bb[i])}) for i in range(40)]
        sec = insights.pareto(Ctx(_snap([("x0", 0, 1), ("x1", 0, 1)], rows, objectives=TWO_GOALS), True))
        assert sec["facts"]["conflict"] and "맞바꿔야" in sec["sentence"], sec["sentence"]


def test_tradeoff_says_where_it_starts_and_where_it_ends():
    """'3.6% 더 줄이면'은 어디서부터인지 말해야 한다. 기준 설계의 값과 도착 값을 함께 쓴다."""
    x = np.linspace(0.5, 2, 21)
    rows = [_row(i + 1, {"x": float(i) / 20}, {"a": float(v), "b": float(1 / v)}) for i, v in enumerate(x)]
    snap = _snap([("x", 0, 1)], rows, objectives=TWO_GOALS, outputs=[{"name": "a", "note": "진동 [mm]"}, {"name": "b", "note": "바닥 전달력 [N]"}])
    sec = insights.pareto(Ctx(snap, True))
    t = sec["facts"]["tradeoff"]
    kid = sec["facts"]["knee_case"]
    assert f"케이스 {kid}(진동" in sec["sentence"] and "바닥 전달력" in sec["sentence"], sec["sentence"]
    assert f"{insights.n3(t['a_to'])} mm까지" in sec["sentence"], sec["sentence"]
    assert sec["next"]["label"] == "이 후보 자세히 보기 요청" and f"케이스 {kid}" in sec["next"]["request"]
    table = next(c for c in sec["charts"] if c["kind"] == "front_table")
    assert table["knee"] == kid and any(r["case_id"] == kid for r in table["rows"])
    assert "왼쪽 아래" in sec["how_to_read"] and "속 빈 점" not in sec["how_to_read"]  # 그리지 않은 것을 설명하지 않는다


def test_three_goals_use_a_table_not_parallel_coordinates():
    rows = [_row(i + 1, {"x": i / 12}, {"a": float(i), "b": float(12 - i), "c": float((i - 6) ** 2)}) for i in range(12)]
    objs = [{"name": n, "goal": "min"} for n in "abc"]
    sec = insights.pareto(Ctx(_snap([("x", 0, 1)], rows, objectives=objs), True))
    assert sec["title"] == "목표들을 함께 좋게 할 수 있나요?" and sec["sentence"].startswith("세 목표 중 a와 b를 먼저 비교했습니다.")
    assert {c["kind"] for c in sec["charts"]} >= {"pareto", "front_table"} and "parallel" not in {c["kind"] for c in sec["charts"]}
    assert sec["pair_default"] == "b" and [c["name"] for c in sec["pair_choices"]] == ["b", "c"]


def test_level_designs_are_described_as_levels():
    """2·3수준 격자의 수준 사이는 비어 있는 것이 설계다. '한 번도 보지 않았다'는 말은 틀리다."""
    import itertools
    for lv, d in [(2, 4), (3, 3), (5, 2)]:
        U = np.array(list(itertools.product(np.linspace(0, 1, lv), repeat=d)))
        names = list("abcd")[:d]
        rows = [_row(i + 1, dict(zip(names, map(float, u))), {"y": float(u.sum())}) for i, u in enumerate(U)]
        sec = insights.coverage(Ctx(_snap([(n, 0, 1) for n in names], rows), True))
        assert sec["state"] == "fair" and f"{lv}수준" in sec["sentence"], sec["sentence"]
        assert "한 번도" not in " ".join([sec["sentence"], *sec["notes"]])
        assert sec["facts"]["grid"] is True


def test_levels_that_miss_the_range_end_are_still_reported():
    """수준이어도 양 끝을 안 돌렸으면 그 구간은 정말 비어 있다."""
    import itertools
    U = np.array(list(itertools.product([0.4, 0.5, 0.6], repeat=3)))
    rows = [_row(i + 1, dict(zip("abc", map(float, u))), {"y": float(u.sum())}) for i, u in enumerate(U)]
    sec = insights.coverage(Ctx(_snap([(n, 0, 1) for n in "abc"], rows), True))
    assert sec["state"] == "caution" and "한 번도" in sec["sentence"], sec["sentence"]


def test_plain_random_layout_is_not_treated_as_a_mistake():
    """무작위 배치도 절반은 무작위 중앙값을 넘는다. 동전 던지기로 '주의'가 되면 안 된다."""
    for n, d in [(40, 4), (64, 8)]:
        names = [f"x{i}" for i in range(d)]
        secs = []
        for s in range(30):
            U = np.random.default_rng(s).random((n, d))
            rows = [_row(i + 1, dict(zip(names, map(float, u))), {"y": 1.0 + i}) for i, u in enumerate(U)]
            secs.append(insights.coverage(Ctx(_snap([(m, 0, 1) for m in names], rows), True)))
        assert sum("퍼지지 않았습니다" in x["sentence"] for x in secs) <= 3, [x["sentence"][:20] for x in secs]


def test_clustered_optimization_points_do_not_count_as_range_coverage():
    """6건 + 최적화 60건을 '66건으로 고르게 살폈다'고 하면 틀리다."""
    U = qmc.LatinHypercube(d=4, seed=1).random(6)
    B = np.clip(0.3 + 0.05 * np.random.default_rng(0).standard_normal((60, 4)), 0, 1)
    rows = [_row(i + 1, dict(zip("abcd", map(float, u))), {"y": 1.0}) for i, u in enumerate(U)]
    rows += [_row(100 + i, dict(zip("abcd", map(float, u))), {"y": 1.0}, source="bo") for i, u in enumerate(B)]
    sec = insights.coverage(Ctx(_snap(VARS4, rows), True))
    assert sec["state"] == "caution" and "6건" in sec["sentence"] and "66건" not in sec["sentence"], sec["sentence"]
    assert sec["facts"]["n_spread"] == 6 and sec["facts"]["n_all"] == 66


def test_fast_layer_makes_no_claim_about_outliers(tmp_path):
    """빠른 층은 튀는 값을 재지 않는다. '없습니다'라고 하면 재지 않은 말이고, 앞서 찾은 경고를 덮으면 안 된다."""
    U = qmc.LatinHypercube(d=3, seed=1).random(40)
    y = U[:, 0] + 1
    y[5] = 9999
    _, m = _fit(U, {"y": y}, tmp_path, "fast")
    rows = [_row(i + 1, {f"x{j}": float(U[i, j]) for j in range(3)}, {"y": float(y[i])}) for i in range(40)]
    snap = _snap([(f"x{j}", 0, 1) for j in range(3)], rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1)
    fast = insights.compute(snap, False)
    q = _section_of(fast, "quality")
    assert "튀는 값은 없습니다" not in q["sentence"] and "다시 확인합니다" in q["sentence"], q["sentence"]
    assert fast["overview"]["next"]["key"] == "computing" and not fast["overview"]["next"]["label"]  # 모델 판단 전에는 할 일을 말하지 않는다
    # 앞서 경고가 있었으면 빠른 층이 지우지 않는다
    full = {"sections": [{"key": "quality", "state": "fair", "sentence": "케이스 6의 y가 많이 다릅니다.", "facts": {"outliers": [{"case_id": 6}]}}], "overview": {}}
    merged = insights._merge_fast(full, fast)
    assert _section_of(merged, "quality")["sentence"] == "케이스 6의 y가 많이 다릅니다."


def test_no_feasible_first_doe_is_said_plainly(no_model_ei):
    no_model_ei(0.0)
    rows = [_row(i + 1, {"a": i / 15}, {"y": 1.0, "s": 9.0}) for i in range(15)]
    rows += [_row(100 + i, {"a": 0.5}, {"y": 2.0 - 0.1 * i, "s": 1.0}, source="bo") for i in range(7)]
    sec = insights.convergence(Ctx(_snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], constraints=[{"name": "s", "upper": 2}]), True))
    assert "처음 계획한 해석에는 제약을 만족한 설계가 없었고" in sec["sentence"] and sec["state"] == "fair", sec["sentence"]
    assert "최고" not in sec["sentence"]


def test_stop_advice_is_conditional_and_capped_by_range_accuracy(no_model_ei, monkeypatch):
    """남은 여지는 예측의 짐작이다. 설계 범위 전체를 잘 맞힐 때만 믿고, 그때도 조건을 붙인다."""
    no_model_ei(0.0)
    doe = [10, 9, 8, 7, 6, 5.5, 5.2, 5.1, 5.05, 5.0]
    snap = _sequence(doe + [4, 3, 2.5, 2.1, 2.0, 2.0, 2.0, 2.0, 2.0, 1.999])
    sec = insights.convergence(Ctx(snap, True))
    assert "예측 모델이 보기에는" in sec["sentence"] and any("놓친" in n for n in sec["notes"]), sec
    monkeypatch.setattr(insights.Ctx, "verdict", lambda self, m, o: "fair")
    soft = insights.convergence(Ctx(snap, True))
    assert soft["state"] == "fair" and soft["next"] and "이릅니다" in soft["sentence"], soft["sentence"]


def _al_rows(base, add, f):
    rows = [_row(i + 1, {"x0": float(r[0]), "x1": float(r[1])}, {"y": float(f(r[None])[0])}) for i, r in enumerate(base)]
    rows += [_row(100 + i, {"x0": float(r[0]), "x1": float(r[1])}, {"y": float(f(r[None])[0])}, source="al") for i, r in enumerate(add)]
    return rows


def test_copied_points_do_not_look_like_improvement(tmp_path):
    """가린 점의 복사본이 학습에 있으면 그 점을 그대로 맞힌다. 정보 없는 추가가 큰 개선으로 보이면 안 된다."""
    U = qmc.LatinHypercube(d=2, seed=1).random(16)
    f = lambda A: (A[:, 1] * 15 - 5.1 / (4 * np.pi ** 2) * (A[:, 0] * 15 - 5) ** 2 + 5 / np.pi * (A[:, 0] * 15 - 5) - 6) ** 2 + 10 * np.cos(A[:, 0] * 15 - 5)  # noqa: E731
    add = U[:3].copy()
    X = np.vstack([U, add])
    _, m = _fit(X, {"y": f(X)}, tmp_path, "copy")
    sec = insights.active(Ctx(_snap([("x0", 0, 1), ("x1", 0, 1)], _al_rows(U, add, f), objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True))
    assert sec["state"] != "good" and "거의 그대로" in sec["sentence"], sec["sentence"]
    assert any("거의 같아" in n for n in sec["notes"])


def test_error_that_rises_is_called_a_rise(tmp_path, monkeypatch):
    """오차가 세 배가 됐는데 '거의 줄지 않았습니다'라고 하면 안 된다. 불확실도도 음수 %로 말하지 않는다."""
    monkeypatch.setattr(insights.calc, "learning_curve", lambda *a, **k: [{"m": 0, "rmse": 16.7, "r2": 0.5}, {"m": 6, "rmse": 40.0, "r2": 0.1}, {"m": 12, "rmse": 59.1, "r2": -1.0}])
    snap = _al_snap(tmp_path, n_add=12, seed=21)
    sec = insights.active(Ctx(snap, True))
    assert sec["state"] == "caution" and sec["state_label"] == "오차 늘어남" and "오히려" in sec["sentence"] and "254%" in sec["sentence"], sec["sentence"]
    assert not any("멈춰도" in n or "더 돌릴 만합니다" in n or "%" in n and "불확실" in n for n in sec["notes"]), sec["notes"]


def test_uncertainty_is_compared_on_one_scale():
    """극단값이 추가되면 y 크기로 나눈 σ가 커져 음수 %가 나왔다. 같은 kernel이면 점을 더해 평균 σ가 늘 수 없다."""
    from sklearn.gaussian_process.kernels import RBF, ConstantKernel, WhiteKernel
    kernel = ConstantKernel(1.0) * RBF(0.3) + WhiteKernel(1e-6)
    Xb = qmc.LatinHypercube(d=2, seed=1).random(16)
    yb = Xb.sum(axis=1)
    Xa = np.array([[1.0, 1.0], [0.0, 1.0], [1.0, 0.0]])
    ya = np.array([500.0, 800.0, 1000.0])
    u = calc.uncertainty_change(Xb, yb, np.vstack([Xb, Xa]), np.r_[yb, ya], kernel, qmc.Sobol(d=2, seed=2).random_base2(9))
    assert u["mean_after"] <= u["mean_before"], u


def test_stop_advice_needs_several_added_points(tmp_path, monkeypatch):
    """한 건의 흔들림으로 '여기서 멈춰도 됩니다'라고 하지 않는다."""
    monkeypatch.setattr(insights.calc, "drop_interval", lambda *a, **k: (1.0, -1.0))  # 멈춤 조언만 본다. 우연과 구분되지 않는 경우는 test_insights_review.py
    sec = insights.active(Ctx(_al_snap(tmp_path, n_add=4, seed=31), True))
    joined = " ".join(sec["notes"])
    assert "아직 말하지 않습니다" in joined and "멈춰도" not in joined and sec["facts"]["stop"] is None, sec["notes"]
    longer = insights.active(Ctx(_al_snap(tmp_path, n_add=8, seed=33), True))
    assert longer["facts"]["stop"] in (True, False)


def test_overview_does_not_ask_for_more_points_after_active_learning_says_it_will_not_help():
    by = {"trust": {"state": "caution", "facts": {}, "variants": []}, "active": {"state": "fair", "facts": {"stop_poor": True}}}
    snap = _snap([("a", 0, 1)], [_row(i + 1, {"a": i / 10}, {"y": float(i)}) for i in range(10)], models=[ModelRow(1, 1, ["y"], {"y": {"r2": 0.1, "rmse": 1.0}}, 10, "", None, False)])
    n = insights._next(Ctx(snap, True), by)
    assert n["key"] == "check_inputs", n


def test_all_failed_is_one_row():
    rows = [_row(i + 1, {"a": i / 8}, {}, status="failed") for i in range(8)]
    out = insights.compute(_snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}]), True)
    assert [x["key"] for x in out["sections"]] == ["quality"]
    assert "8건이 모두 실패" in out["sections"][0]["sentence"] and out["overview"]["next"]["key"] == "fail"


def test_variable_units_appear_in_sentences(tmp_path):
    """'스프링 강성 12'는 N/mm로 읽힌다. 설명에 적힌 단위를 값과 함께 쓴다."""
    snap = _washer_snap(60, tmp_path, seed=5)
    snap.variables[0]["note"] = "스프링 강성 [%]"
    snap.variables[0]["lower"] = -10
    ctx = Ctx(snap, True)
    assert ctx.val("x0", 12.0) == "+12%" and ctx.val("x0", -5.07) == "-5.07%"
    assert insights._var_rows(ctx)[0]["unit"] == "%" and insights._var_rows(ctx)[0]["label"] == "스프링 강성"


def test_cost_is_stated_only_when_measured():
    t0 = datetime(2026, 1, 1, 0, 0)
    timing = lambda i: {"started_at": (t0 + timedelta(hours=7 * i)).isoformat(), "ended_at": (t0 + timedelta(hours=7 * i + 6)).isoformat()}  # noqa: E731
    rows = [_row(i + 1, {"a": i / 10}, {"y": float(i)}, timing=timing(i)) for i in range(3)]
    ctx = Ctx(_snap([("a", 0, 1)], rows), True)
    assert ctx.cost(5) == "(지금 속도로 약 30.0시간)", ctx.cost(5)
    assert Ctx(_snap([("a", 0, 1)], rows[:2]), True).cost(5) == ""  # 3건 미만이면 말하지 않는다
    short = [_row(i + 1, {"a": i / 10}, {"y": float(i)}, timing={"started_at": t0.isoformat(), "ended_at": (t0 + timedelta(seconds=1)).isoformat()}) for i in range(3)]
    assert Ctx(_snap([("a", 0, 1)], short), True).cost(5) == ""  # 몇 초면 판단에 쓸모가 없다


def test_watch_list_leads_with_cautions():
    """초록 줄만 보고 끝났다고 여기지 않게 확정 전에 볼 것을 맨 위에 모은다."""
    rows = [_row(i + 1, {"x": i / 12}, {"a": float(i), "b": float(12 - i)}) for i in range(12)]
    out = insights.compute(_snap([("x", 0, 1)], rows, objectives=TWO_GOALS, best_id=1), True)
    keys = [w["key"] for w in out["overview"]["watch"]]
    assert keys and len(keys) <= 3
    states = {x["key"]: x["state"] for x in out["sections"]}
    assert all(states[k] in ("fair", "caution") for k in keys)
    ranks = [insights._RANK[states[k]] for k in keys]
    assert ranks == sorted(ranks, reverse=True)


def test_how_to_read_describes_only_what_is_drawn():
    rows = [_row(i + 1, {"a": i / 10}, {"y": 1.0 + i / 10}) for i in range(10)]
    q = insights.quality(Ctx(_snap([("a", 0, 1)], rows), True))
    assert "빨갛게 표시합니다" in q["how_to_read"] and "빨간 점은" not in q["how_to_read"]
    pts = [(1.0, 1.0), (2.0, 0.9), (3.0, 0.8), (4.0, 0.7), (5.0, 0.6), (6.0, 0.5)]  # 직선이라 균형 설계가 없다
    sec = insights.pareto(Ctx(_snap([("x", 0, 1)], [_row(i + 1, {"x": i / 10}, {"a": u, "b": v}) for i, (u, v) in enumerate(pts)], objectives=TWO_GOALS), True))
    assert not any("균형 설계는 제안" in n for n in sec["notes"]), sec["notes"]
    assert "뚜렷한 균형 설계는 없습니다" in sec["sentence"]


def test_optional_rows_do_not_look_like_a_problem():
    rows = [_row(i + 1, {"a": i / 10}, {"y": float(i), "z": float(i)}) for i in range(10)]
    out = insights.compute(_snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], best_id=1), True)
    assert "active" not in [x["key"] for x in out["sections"]]
    assert _section_of(out, "pareto")["state_label"] == "선택 사항"


# ---------- 문장 규칙 ----------
FORBIDDEN = ["R²", "RMSE", "Sobol", "EI", "GP", "획득함수", "하이퍼파라미터", "교차검증", "σ", "초부피", "kernel", "DOE", "후보 범위", "표준 배치", "치우침 값", "AI가 고른 {"]
SAMPLE = dict(goals="두 목표", side="큰", n=60, d=4, need=20, rec=32, var="스프링 강성", range="-10~2.5%", p="31", a="스프링 강성", b="댐퍼 감쇠력", r="0.62", cost="(지금 속도로 약 3시간)", ratio="4", levels="3", total=14, hit=12, e="0.3 mm", why="이유입니다", design="진동 2.85 mm, 바닥 전달력 240 N", to="2.75 mm", verb="낮출", names="처짐·최대 응력",
              i=12, j=34, f=3, m=5, t="2.5시간", id=17, out="통 진동 변위", val="2.61 mm", q1="2.9", q3="4.1", k=8, g=3, var1="스프링 강성", var2="댐퍼 감쇠력", p1=69, p2=29,
              x="스프링 강성 12.5, 댐퍼 감쇠력 25.0", partner="댐퍼 감쇠력", p80="0.31 mm", rmse="0.3 mm", where="스프링 강성 위", W=5, start="4.20 mm", now="2.61 mm", gain="37.8%",
              pred="2.5 mm", con="최대 응력", tol="2", diff="0.1 mm", verb_a="줄이", verb_b="늘어납니다", need2=3)


def _rendered():
    for key in insights.T:
        yield key, insights.say(key, **SAMPLE)


def test_all_sentences_are_plain_and_short():
    for key, text in _rendered():
        first = re.split(r"(?<=[다요)])\.\s", text)[0]
        for word in FORBIDDEN:
            assert word not in text, (key, text)
        assert len(first) <= 100, (key, len(first), first)
    for key, (text, _) in insights.NEXT.items():
        assert "DOE" not in text or "어떤 값들로" in text, key  # 약어는 처음 나올 때 풀어 쓴다


def test_model_word_is_never_ambiguous():
    """엔지니어에게 '모델'은 FE 모델이다. 예측 모델이면 그렇게 쓴다."""
    texts = [t for t in insights.T.values()] + [t for t in insights.HOW_TO_READ.values()] + [t for t, _ in insights.NEXT.values()] + [t for _, t in insights.META.values()] + list(insights.BASIS_LABEL.values())
    for text in texts:
        for m in re.finditer("모델", text):
            before = text[max(0, m.start() - 3):m.start()]
            assert before.endswith("예측 ") or before.endswith("해석 "), text


def test_state_word_answers_the_question():
    """'더 돌리면 좋아질까요? 좋음'은 거꾸로 읽힌다. 질문마다 답이 되는 말을 쓰고, 네 가지 상태는 그대로다."""
    assert insights.state_label("convergence", "good") == "거의 다 찾음" and insights.state_label("convergence", "fair") == "더 돌릴 만함"
    assert insights.state_label("optimum", "caution") == "아직 쓰지 마세요" and insights.state_label("active", "unknown") == "아직 판단 못 함"
    for words in insights.STATE_WORDS.values():
        assert set(words) == {"good", "fair", "caution"} and "좋음" not in words.values()


def test_titles_are_questions_with_term_once():
    for key, (title, term) in insights.META.items():
        assert title.endswith("?"), key
        assert term and term not in title, key


def test_josa_chooses_by_last_syllable():
    assert insights.josa("강성", "을를") == "을" and insights.josa("댐퍼", "을를") == "를"
    assert insights.josa("진동", "이가") == "이" and insights.josa("무게", "이가") == "가"
    assert insights.josa("x1", "을를") == "을" and insights.josa("x2", "을를") == "를"


def test_thresholds_written_in_spec():
    spec = (Path(__file__).resolve().parents[2] / "docs" / "product-spec.md").read_text(encoding="utf-8")
    assert "4-6-1" in spec
    for key, value in insights.THRESHOLDS.items():
        assert re.search(rf"`{key}`\s*\|\s*{re.escape(str(value))}\s*\|", spec), f"{key}={value}가 product-spec.md 4-6-1절 표에 없습니다"
