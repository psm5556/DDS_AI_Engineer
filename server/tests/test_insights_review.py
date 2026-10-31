"""판단 도우미 검토에서 나온 문제들. 문장이 같은 화면의 다른 사실과 어긋나거나, 보이지 않는 것을 믿게 하면 안 된다."""
import re
import time
from pathlib import Path

import numpy as np
import pytest
from scipy.stats import qmc

from server.tests.test_api import _auth, client  # noqa: F401  서버 코드보다 먼저 불러 임시 폴더를 쓴다

from server.app import models, report  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.study import insights, insights_calc as calc, service  # noqa: E402
from server.app.study.insights import Ctx, ModelRow  # noqa: E402
from server.tests.test_insights_api import _ready, _run, _washer_study  # noqa: E402
from server.tests.test_insights_calc import _fit, _row, _section_of, _snap  # noqa: E402

insights.BENCH_ENABLED = False
V2 = [("a", 0, 1), ("b", 0, 1)]


def _P(u):
    return {"a": float(u[0]), "b": float(u[1])}


def _variant(sec, name):
    return next(q for q in sec["variants"] if q["name"] == name)


def _make_study(client, admin, variables, objectives=(), constraints=(), outputs=(), name="검토"):
    pid = client.post("/api/projects", json={"name": name}, headers=admin).json()["id"]
    r = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": name, "adapter": "mock", "adapter_config": {"function": "branin"}, "variables": variables,
        "objectives": list(objectives), "constraints": list(constraints), "outputs": list(outputs)})
    assert r.status_code == 200, r.text
    return pid, r.json()["id"]


def _add_cases(db, sid, rows):
    for params, results, status, source in rows:
        db.add(models.Case(study_id=sid, params=params, results=results, status=status, source=source))
    db.commit()


# ---------- 본 적 없는 구간 ----------
def _half_seen(client, admin):
    """a가 0.5보다 큰 케이스가 모두 실패했다. 본 구간에서는 잘 맞으니 교차검증만으로는 문제가 보이지 않는다."""
    f = lambda a, b: float(np.sin(6 * a) + 0.5 * b + 4 * max(0, a - 0.5) ** 2)  # noqa: E731
    _, sid = _make_study(client, admin, [{"name": "a", "lower": 0, "upper": 1}, {"name": "b", "lower": 0, "upper": 1}],
                         [{"name": "y", "goal": "min"}], outputs=[{"name": "y", "note": "처짐 [mm]"}], name="반만 본 스터디")
    rows = []
    for u in qmc.LatinHypercube(d=2, seed=1).random(40):
        rows.append((_P(u), {"y": f(*u)}, "complete", "doe") if u[0] < 0.5 else (_P(u), {}, "failed", "doe"))
    return sid, rows


def test_unseen_region_lowers_trust_and_is_named_on_the_simulator_line(client):
    admin, _ = _auth(client, "admin")
    sid, rows = _half_seen(client, admin)
    with SessionLocal() as db:
        _add_cases(db, sid, rows)
        s = db.get(models.Study, sid)
        m = service.train(db, s, None)
        line = insights.trust_line(db, m)
        sec = _section_of(insights.compute(insights.snapshot(db, s), True), "trust")
        # 같은 화면의 정확도 칩도 같은 답을 한다
        assert service.quality_level(m) == "fair" and any("학습 데이터가 없어" in w for w in m.quality["warnings"]), m.quality
    assert line["state"] != "good", line
    assert "구간" in line["sentence"] and "믿기 어렵" in line["sentence"], line["sentence"]
    assert line["gaps"][0]["variable"] == "a" and line["gaps"][0]["lo"] < 0.55 and line["gaps"][0]["hi"] == 1, line["gaps"]
    assert sec["state"] != "good" and "구간" in sec["sentence"], sec["sentence"]


def test_regular_design_has_no_holes(client):
    admin, _ = _auth(client, "admin")
    _, sid = _make_study(client, admin, [{"name": "a", "lower": 0, "upper": 1}, {"name": "b", "lower": 0, "upper": 1}], [{"name": "y", "goal": "min"}], name="고른 스터디")
    rows = [(_P(u), {"y": float(u[0] + 2 * u[1] ** 2)}, "complete", "doe") for u in qmc.LatinHypercube(d=2, seed=1).random(40)]
    with SessionLocal() as db:
        _add_cases(db, sid, rows)
        m = service.train(db, db.get(models.Study, sid), None)
        line = insights.trust_line(db, m)
    assert line["gaps"] == [] and line["state"] == "good", line


def test_hole_uses_levels_only_at_the_ends(tmp_path):
    """격자 배치는 수준 사이가 비는 것이 설계다."""
    grid = np.array([[a, b] for a in (0.0, 0.5, 1.0) for b in (0.0, 0.25, 0.5, 0.75, 1.0)] * 2)
    _, m = _fit(grid, {"y": grid[:, 0] + grid[:, 1]}, tmp_path, "grid")
    rows = [_row(i + 1, _P(u), {"y": float(u[0] + u[1])}) for i, u in enumerate(grid)]
    ctx = Ctx(_snap(V2, rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True)
    assert ctx.holes(m, "y") == []


# ---------- 거의 일정한 출력 ----------
def _offset_snap(tmp_path, objective=True):
    """101325 Pa 위에 ±50 Pa가 흔들린다. 절대 크기로 보면 작지만 최적화하려는 신호 전부다."""
    U = qmc.LatinHypercube(d=2, seed=1).random(30)
    p = 101325 + np.random.default_rng(3).uniform(-50, 50, 30)
    z = U[:, 0] + U[:, 1]
    _, m = _fit(U, {"p": p, "z": z}, tmp_path, f"offset{objective}")
    rows = [_row(i + 1, _P(u), {"p": float(p[i]), "z": float(z[i])}) for i, u in enumerate(U)]
    return _snap(V2, rows, objectives=[{"name": "p" if objective else "z", "goal": "min"}], models=[m], outputs=[{"name": "p", "note": "압력 [Pa]"}], best_id=1)


def test_big_offset_objective_with_no_skill_is_not_called_flat(tmp_path):
    out = insights.compute(_offset_snap(tmp_path), True)
    tr, inf = _section_of(out, "trust"), _section_of(out, "influence")
    assert tr["state"] == "caution" and "거의 일정" not in tr["sentence"], tr["sentence"]
    assert "변하지 않아" not in inf["sentence"], inf["sentence"]
    assert out["overview"]["next"]["key"] == "poor"


def test_numeric_noise_on_an_objective_is_still_flat(tmp_path):
    U = qmc.LatinHypercube(d=2, seed=1).random(30)
    y = 7 + 1e-8 * np.random.default_rng(3).standard_normal(30)
    _, m = _fit(U, {"y": y}, tmp_path, "noise-goal")
    rows = [_row(i + 1, _P(u), {"y": float(y[i])}) for i, u in enumerate(U)]
    tr = insights.trust(Ctx(_snap(V2, rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True))
    assert "거의 일정" in tr["sentence"], tr["sentence"]


def test_spread_must_be_small_too_for_a_monitored_output(tmp_path):
    """오차가 값에 비해 작아도 값 자체가 눈에 띄게 움직이면 일정하다고 하지 않는다."""
    U = qmc.LatinHypercube(d=2, seed=1).random(30)
    y = 1000 + 20 * np.random.default_rng(1).standard_normal(30)  # 흩어짐 약 ±4%, 오차는 평균의 약 2%
    _, m = _fit(U, {"y": y, "z": U[:, 0]}, tmp_path, "spread")
    rows = [_row(i + 1, _P(u), {"y": float(y[i]), "z": float(U[i, 0])}) for i, u in enumerate(U)]
    ctx = Ctx(_snap(V2, rows, objectives=[{"name": "z", "goal": "min"}], models=[m], best_id=1), True)
    assert not ctx.near_flat(m, "y")


# ---------- 옛 모델 ----------
def _old_model(db, sid, r2=0.95):
    """이 기능 전에 학습한 모델: 모델 파일에 교차검증 예측이 없고, 품질은 모든 점으로 잰 값이다."""
    m = service.train(db, db.get(models.Study, sid), None)
    sur = service.load_surrogate(m)
    sur.cv = {}
    sur.save(Path(m.path))
    m.metrics = {o: {**v, "r2": r2} for o, v in m.metrics.items()}
    m.quality = {"level": "good", "label": "좋음", "by_output": {o: {"level": "good", "r2": r2} for o in m.metrics}, "warnings": [], "method": "K겹 교차검증 R²"}
    db.commit()
    return m


def test_old_model_is_measured_once_not_judged_from_stored_score_and_chip_follows(client):
    """공유받은 사람은 데이터 탭을 열 수 없다. 저장된 R²로 '믿을 만함'이라고 했다가 나중에 뒤집히면 안 된다."""
    admin, _ = _auth(client, "admin")
    kim, _ = _auth(client, "kim_rev", admin)
    U = qmc.LatinHypercube(d=2, seed=2).random(30)
    noise = np.random.default_rng(4).standard_normal(30)
    _, sid = _make_study(client, admin, [{"name": "a", "lower": 0, "upper": 1}, {"name": "b", "lower": 0, "upper": 1}], [{"name": "y", "goal": "min"}], name="옛 모델")
    with SessionLocal() as db:
        _add_cases(db, sid, [(_P(u), {"y": float(noise[i])}, "complete", "doe") for i, u in enumerate(U)])
        mid = _old_model(db, sid).id
    client.post(f"/api/models/{mid}/deploy", headers=admin)
    token = client.post(f"/api/models/{mid}/shares", json={"note": "", "expires_days": 7}, headers=admin).json()["token"]
    first = client.get(f"/api/shares/{token}/trust-line", headers=kim).json()
    assert first["state"] == "unknown" and first["computing"] is True and first["state_label"] == "계산 중", first
    t0, last = time.time(), first
    while last.get("computing") and time.time() - t0 < 90:
        time.sleep(0.2)
        last = client.get(f"/api/shares/{token}/trust-line", headers=kim).json()
    assert not last["computing"] and last["state"] == "caution", last
    assert last["outputs"]["y"]["p80"] > 0
    with SessionLocal() as db:
        m = db.get(models.SurrogateModel, mid)
        assert service.quality_level(m) == "poor" and m.quality["reassessed"] is True
        assert m.quality["by_output"]["y"]["r2"] < 0.7
    # 다시 재지 않는다
    assert client.get(f"/api/shares/{token}/trust-line", headers=kim).json()["state"] == "caution"


def test_trust_line_of_a_constant_output_says_it_cannot_be_measured(client):
    admin, _ = _auth(client, "admin")
    _, sid = _make_study(client, admin, [{"name": "a", "lower": 0, "upper": 1}, {"name": "b", "lower": 0, "upper": 1}], [{"name": "y", "goal": "min"}], name="일정한 출력")
    rows = [(_P(u), {"y": 7.0}, "complete", "doe") for u in qmc.LatinHypercube(d=2, seed=1).random(30)]
    with SessionLocal() as db:
        _add_cases(db, sid, rows)
        m = service.train(db, db.get(models.Study, sid), None)
        line = insights.trust_line(db, m)
    assert line["state"] == "unknown" and "오차가 큽니다" not in line["sentence"], line


def test_failed_measurement_stops_asking_and_falls_back(client, monkeypatch):
    admin, _ = _auth(client, "admin")
    U = qmc.LatinHypercube(d=2, seed=2).random(30)
    _, sid = _make_study(client, admin, [{"name": "a", "lower": 0, "upper": 1}, {"name": "b", "lower": 0, "upper": 1}], [{"name": "y", "goal": "min"}], name="재기 실패")
    with SessionLocal() as db:
        _add_cases(db, sid, [(_P(u), {"y": float(u[0] + u[1])}, "complete", "doe") for u in U])
        m = _old_model(db, sid)
        mid = m.id
        monkeypatch.setattr(insights, "_reassess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("시험")))
        insights._backfill(mid)
        line = insights.trust_line(db, db.get(models.SurrogateModel, mid))
    assert not line["computing"] and line["state"] != "unknown", line


# ---------- 저장한 보고서 ----------
def _table(rows):
    return {"columns": ["질문", "결론"], "rows": rows}


def _html(client, pid, rid, admin):
    r = client.get(f"/api/projects/{pid}/report?format=html&report_id={rid}", headers=admin)
    assert r.status_code == 200
    return r.text


def test_saved_edition_keeps_the_values_it_was_written_from(client):
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "판 고정"}, headers=admin).json()["id"]
    with SessionLocal() as db:
        aid = service.publish_artifact(db, pid, "table", "[스터디] 판단 요약", _table([["몇 건?", "처음 값 32건"]])).id
    rep = client.post(f"/api/projects/{pid}/reports", headers=admin, json={"title": "1판", "body": f"## 결과\n\n![요약](artifact:{aid})\n"}).json()
    with SessionLocal() as db:
        again = service.publish_artifact(db, pid, "table", "[스터디] 판단 요약", _table([["몇 건?", "새 값 50건"]]))
        assert again.id == aid  # 같은 제목이면 카드는 하나로 갱신된다
    html = _html(client, pid, rep["id"], admin)
    assert "처음 값 32건" in html and "새 값 50건" not in html
    got = client.get(f"/api/reports/{rep['id']}", headers=admin).json()
    assert got["frozen"][str(aid)]["payload"]["rows"] == [["몇 건?", "처음 값 32건"]]
    assert got["new_results"] >= 1  # 새 값이 있다는 사실은 여전히 알린다
    # Word도 같은 틀을 쓴다
    with SessionLocal() as db:
        r, arts = db.get(models.Report, rep["id"]), db.query(models.Artifact).filter_by(project_id=pid).all()
        block = next(b for b in report.compose(db.get(models.Project, pid), arts, None, r).blocks if b.kind == "figure")
        assert block.art.payload["rows"][0][1] == "처음 값 32건"
        assert not db.new and not db.dirty  # 고정한 값이 카드에 덮어 쓰이면 안 된다
        assert db.get(models.Artifact, aid).payload["rows"][0][1] == "새 값 50건"
    # 새 판은 새 값을 쓴다
    rep2 = client.post(f"/api/projects/{pid}/reports", headers=admin, json={"title": "2판", "body": f"![요약](artifact:{aid})\n"}).json()
    assert "새 값 50건" in _html(client, pid, rep2["id"], admin)


def test_deleted_result_still_leaves_a_saved_edition(client):
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "판 지움"}, headers=admin).json()["id"]
    with SessionLocal() as db:
        aid = service.publish_artifact(db, pid, "table", "지울 결과", _table([["a", "지울 값"]])).id
    rep = client.post(f"/api/projects/{pid}/reports", headers=admin, json={"title": "1판", "body": f"![결과](artifact:{aid})\n"}).json()
    with SessionLocal() as db:
        db.delete(db.get(models.Artifact, aid))
        db.commit()
    assert "지울 값" not in _html(client, pid, rep["id"], admin)


def test_old_edition_without_saved_values_says_it_shows_current_values(client):
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "옛 판"}, headers=admin).json()["id"]
    with SessionLocal() as db:
        aid = service.publish_artifact(db, pid, "table", "옛 결과", _table([["a", "옛 값"]])).id
    rep = client.post(f"/api/projects/{pid}/reports", headers=admin, json={"title": "1판", "body": f"![결과](artifact:{aid})\n"}).json()
    with SessionLocal() as db:
        db.get(models.Report, rep["id"]).frozen = None  # 이 기능 전에 저장한 판
        db.commit()
    assert "지금 값으로 보입니다" not in _html(client, pid, rep["id"], admin)  # 바뀐 것이 없으면 말하지 않는다
    time.sleep(0.01)
    with SessionLocal() as db:
        a = db.get(models.Artifact, aid)
        a.payload = _table([["a", "바뀐 값"]])
        db.commit()
    assert client.get(f"/api/reports/{rep['id']}", headers=admin).json()["frozen"] is None
    assert "지금 값으로 보입니다" in _html(client, pid, rep["id"], admin)


# ---------- 제약 여유 ----------
def _margin_section(rows, upper=0.0, unit="-"):
    return insights.optimum(Ctx(_snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}], constraints=[{"name": "s", "upper": upper}], outputs=[{"name": "s", "note": f"여유 [{unit}]"}], best_id=1), True))


def test_zero_limit_margin_is_judged_against_the_output_range_small_units(monkeypatch):
    """한계가 0이면 %의 기준이 없다. 1.0으로 나누면 작은 단위의 넉넉한 여유가 '아직 쓰지 마세요'가 된다."""
    monkeypatch.setattr(insights, "_optimum_caps", lambda ctx, o: [])
    rows = [_row(1, {"a": 0.5}, {"y": 1.0, "s": -9.7e-5})] + [_row(i, {"a": 0.09 * i}, {"y": 2.0 + i, "s": -5e-5 - 1e-5 * i}) for i in range(2, 12)]
    sec = _margin_section(rows)
    assert sec["state"] == "good" and "여유" not in sec["sentence"], sec["sentence"]
    assert not sec["facts"]["tight_constraint"]


def test_zero_limit_margin_small_against_a_wide_range_warns(monkeypatch):
    monkeypatch.setattr(insights, "_optimum_caps", lambda ctx, o: [])
    rows = [_row(1, {"a": 0.5}, {"y": 1.0, "s": -0.5})] + [_row(i, {"a": 0.09 * i}, {"y": 2.0 + i, "s": -25.0 * i}) for i in range(2, 12)]
    sec = _margin_section(rows, unit="MPa")
    assert sec["state"] == "caution" and "값 폭의" in sec["sentence"] and "MPa" in sec["sentence"], sec["sentence"]


def test_dimensionless_unit_mark_is_not_printed():
    assert insights.with_unit(0.5, "-") == "0.5"


# ---------- 목표 값이 없는 케이스 ----------
def _missing_goal_snap(key="other", every=2, n=20):
    rows = []
    for i, u in enumerate(qmc.LatinHypercube(d=2, seed=1).random(n)):
        rows.append(_row(i + 1, _P(u), {"y": float(u[0] + u[1])} if i % every else {key: 1.0 + float(u[0])}))
    return _snap(V2, rows, objectives=[{"name": "y", "goal": "min"}])


def test_quality_reports_cases_without_the_objective():
    sec = insights.quality(Ctx(_missing_goal_snap(), True))
    assert sec["state"] == "caution" and "10건" in sec["sentence"] and "값이 없습니다" in sec["sentence"], sec["sentence"]
    assert sec["facts"]["objective_missing"] == {"y": 10}


def test_few_missing_objective_values_stay_a_note():
    rows = [_row(i + 1, {"a": i / 40}, {"y": float(i)} if i else {"other": 1.0}) for i in range(40)]
    sec = insights.quality(Ctx(_snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}]), True))
    assert any("값이 없습니다" in n for n in sec["notes"]), sec


def test_objective_never_produced_is_said_everywhere_it_matters():
    rows = [_row(i + 1, _P(u), {"other": float(u[0])}) for i, u in enumerate(qmc.LatinHypercube(d=2, seed=1).random(20))]
    out = insights.compute(_snap(V2, rows, objectives=[{"name": "typo", "goal": "min"}]), True)
    q, opt, inf = (_section_of(out, k) for k in ("quality", "optimum", "influence"))
    assert q["state"] == "caution" and "어디에도 없습니다" in q["sentence"], q["sentence"]
    assert "완료된 해석이 없어" not in opt["sentence"] and "값이 없어" in opt["sentence"], opt["sentence"]
    assert "값이 있는 해석이 0건" in _variant(inf, "typo")["sentence"], inf["sentence"]
    nx = out["overview"]["next"]
    assert nx["key"] == "no_value" and "typo" in nx["text"] and nx["label"], nx


def test_convergence_without_objective_values_does_not_blame_constraints():
    rows = [_row(i + 1, {"a": i / 10}, {"other": 1.0}, source="doe" if i < 10 else "bo") for i in range(16)]
    sec = insights.convergence(Ctx(_snap([("a", 0, 1)], rows, objectives=[{"name": "typo", "goal": "min"}]), True))
    assert "제약" not in sec["sentence"] and "값이" in sec["sentence"], sec["sentence"]


# ---------- 두 목표 수렴: 처음 점에 제약 만족이 없을 때 ----------
def test_multi_goal_with_no_feasible_start_reports_what_was_found():
    rows = [_row(i + 1, _P(u), {"f1": float(u[0]), "f2": float(1 - u[0]), "g": float(2 + u[1])}) for i, u in enumerate(qmc.LatinHypercube(d=2, seed=1).random(12))]
    rows += [_row(100 + j, {"a": j / 9, "b": 0.5}, {"f1": j / 9 + 0.5 - 0.04 * j, "f2": 1.5 - j / 9 - 0.04 * j, "g": 0.0}, source="bo") for j in range(10)]
    sec = insights.convergence(Ctx(_snap(V2, rows, objectives=[{"name": "f1", "goal": "min"}, {"name": "f2", "goal": "min"}], constraints=[{"name": "g", "upper": 0.5}]), True))
    assert sec["state"] == "fair" and "없었고" in sec["sentence"] and "나아지지 않았습니다" not in sec["sentence"], sec["sentence"]
    assert sec["facts"]["no_start"] is True and sec["facts"]["more_worth"] is True


# ---------- 모델이 배우지 않은 제약 ----------
def _unlearned_snap(tmp_path):
    U = qmc.LatinHypercube(d=2, seed=1).random(30)
    _, m = _fit(U, {"y": 1 - U[:, 0]}, tmp_path, "unlearned")
    rows = [_row(i + 1, _P(u), {"y": float(1 - u[0]), "g": float(u[0])}) for i, u in enumerate(U)]
    return _snap(V2, rows, objectives=[{"name": "y", "goal": "min"}], constraints=[{"name": "g", "upper": 0.5}], models=[m], best_id=None), rows


def test_predicted_best_is_not_recommended_when_a_constraint_was_not_learned(tmp_path):
    snap, rows = _unlearned_snap(tmp_path)
    good = [c for c in rows if c.results["g"] <= 0.5]
    snap.best_id = min(good, key=lambda c: c.results["y"]).id
    out = insights.compute(snap, True)
    opt = _section_of(out, "optimum")
    joined = " ".join([opt["sentence"], *opt["notes"]])
    assert "predicted_best" not in opt["facts"], opt["facts"]
    assert "배우지 않아" in joined and "g" in joined, joined
    assert opt["next"]["action"] == "train"
    assert out["overview"]["next"]["key"] == "retrain_con", out["overview"]["next"]


def test_pareto_drops_predicted_points_when_a_constraint_was_not_learned(tmp_path):
    U = qmc.LatinHypercube(d=2, seed=1).random(30)
    _, m = _fit(U, {"f1": U[:, 0], "f2": 1 - U[:, 0] + U[:, 1]}, tmp_path, "par-unlearned")
    rows = [_row(i + 1, _P(u), {"f1": float(u[0]), "f2": float(1 - u[0] + u[1]), "g": 0.1}) for i, u in enumerate(U)]
    sec = insights.pareto(Ctx(_snap(V2, rows, objectives=[{"name": "f1", "goal": "min"}, {"name": "f2", "goal": "min"}], constraints=[{"name": "g", "upper": 0.5}], models=[m]), True))
    chart = next(c for c in sec["charts"] if c["kind"] == "pareto")
    assert chart["predicted"] == [] and any("배우지 않아" in n for n in sec["notes"]), sec["notes"]


# ---------- 능동학습 판정 ----------
def test_drop_interval_separates_luck_from_a_real_drop():
    rng = np.random.default_rng(0)
    base = rng.normal(0, 1, 40)
    lo, hi = calc.drop_interval(base, base * 0.5)
    assert 0.45 < lo <= hi < 0.55  # 모든 점이 고르게 절반
    # 한 점의 운으로 줄어든 평균 오차
    first = np.full(14, 0.1)
    first[0] = 3.0
    last = np.full(14, 0.1)
    assert calc.drop_interval(first, last)[0] <= 0 < (1 - np.sqrt(np.mean(last ** 2) / np.mean(first ** 2)))
    assert calc.drop_interval(np.zeros(5), np.zeros(5)) == (0.0, 0.0)


def _al_rows(n_base=14, n_add=4, d=2):
    U = qmc.LatinHypercube(d=d, seed=1).random(n_base + n_add)
    names = [f"x{i}" for i in range(d)]
    return U, [_row(i + 1, {n: float(u[j]) for j, n in enumerate(names)}, {"y": float(u.sum())}, source="doe" if i < n_base else "al") for i, u in enumerate(U)], [(n, 0, 1) for n in names]


def _fake_curve(monkeypatch, first, last):
    monkeypatch.setattr(insights.calc, "learning_curve", lambda *a, **k: [
        {"m": 0, "rmse": float(np.sqrt(np.mean(np.square(first)))), "r2": 0.5, "err": list(first)}, {"m": 4, "rmse": float(np.sqrt(np.mean(np.square(last)))), "r2": 0.6, "err": list(last)}])


def test_active_does_not_state_a_drop_it_cannot_tell_from_luck(tmp_path, monkeypatch):
    U, rows, variables = _al_rows()
    _, m = _fit(U, {"y": U.sum(axis=1)}, tmp_path, "al-luck")
    first = [3.0] + [0.1] * 13
    _fake_curve(monkeypatch, first, [0.1] * 14)
    snap = _snap(variables, rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1)
    sec = insights.active(Ctx(snap, True))
    assert sec["state"] == "unknown" and "구분되지 않" in sec["sentence"] and "줄었습니다" not in sec["sentence"], sec["sentence"]
    # 몇 점의 운으로 늘어난 것도 '오차 늘어남'이라고 말하지 않는다
    _fake_curve(monkeypatch, [0.1] * 14, first)
    sec = insights.active(Ctx(snap, True))
    assert sec["state"] == "unknown" and sec["state_label"] != "오차 늘어남", sec


def test_active_clear_drop_says_where_it_was_measured(tmp_path, monkeypatch):
    U, rows, variables = _al_rows()
    _, m = _fit(U, {"y": U.sum(axis=1)}, tmp_path, "al-clear")
    base = np.random.default_rng(1).normal(0, 1, 14)
    _fake_curve(monkeypatch, base, base * 0.5)
    sec = insights.active(Ctx(_snap(variables, rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True))
    assert sec["state"] == "good" and sec["state_label"] == "효과 보임", sec
    assert "처음 계획한 점 14건" in sec["sentence"] and "50%" in sec["sentence"], sec["sentence"]
    assert insights.T["al_scope"] in sec["notes"]
    assert sec["facts"]["drop_low"] > 0.2


def test_active_base_points_grow_with_variables(tmp_path):
    U, rows, variables = _al_rows(n_base=12, n_add=4, d=3)  # 변수 3개면 15건은 있어야 한다
    _, m = _fit(U, {"y": U.sum(axis=1)}, tmp_path, "al-few")
    sec = insights.active(Ctx(_snap(variables, rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True))
    assert sec["state"] == "unknown" and "15건" in sec["sentence"], sec["sentence"]


# ---------- 다음 할 일 ----------
def _zero_pass(tmp_path, upper, base_a=lambda u: u[0], bo=0, name="z"):
    U = qmc.LatinHypercube(d=2, seed=1).random(30)
    g = np.array([base_a(u) for u in U])
    _, m = _fit(U, {"y": U[:, 0] + U[:, 1], "g": g}, tmp_path, name)
    rows = [_row(i + 1, _P(u), {"y": float(u[0] + u[1]), "g": float(g[i])}) for i, u in enumerate(U)]
    rows += [_row(100 + j, {"a": 0.5, "b": j / 10}, {"y": 1.0, "g": 5.0}, source="bo") for j in range(bo)]
    return _snap(V2, rows, objectives=[{"name": "y", "goal": "min"}], constraints=[{"name": "g", "upper": upper}], models=[m])


def test_next_does_not_spend_runs_when_nothing_can_meet_the_constraint(tmp_path):
    out = insights.compute(_zero_pass(tmp_path, -100.0), True)
    nx = out["overview"]["next"]
    assert nx["key"] == "no_feasible" and nx["action"] not in ("bo", "al"), nx


def test_next_after_optimising_still_nothing_feasible_is_not_a_report(tmp_path):
    out = insights.compute(_zero_pass(tmp_path, 0.5, base_a=lambda u: 1.0 + u[0], bo=2, name="z2"), True)
    assert out["overview"]["next"]["key"] == "no_feasible", out["overview"]["next"]


def test_next_can_optimise_when_the_model_sees_a_feasible_region(tmp_path):
    """처음 점에 제약 만족이 없어도 예측이 만족할 곳을 보면 최적화가 그곳을 찾는 길이다."""
    U = qmc.LatinHypercube(d=2, seed=1).random(30)
    U[:, 0] = 0.25 + 0.75 * U[:, 0]  # a가 0.25 아래인 곳은 아직 돌리지 않았다
    _, m = _fit(U, {"y": U[:, 0] + U[:, 1], "g": U[:, 0]}, tmp_path, "z3")
    rows = [_row(i + 1, _P(u), {"y": float(u[0] + u[1]), "g": float(u[0])}) for i, u in enumerate(U)]
    out = insights.compute(_snap(V2, rows, objectives=[{"name": "y", "goal": "min"}], constraints=[{"name": "g", "upper": 0.2}], models=[m]), True)
    assert out["overview"]["next"]["key"] == "optimize", out["overview"]["next"]


# ---------- 표로 만든 스터디 ----------
def _table_snap(tmp_path, y, name):
    U = qmc.LatinHypercube(d=3, seed=5).random(40)
    Y = y(U)
    sur, m = _fit(U, {"s": Y}, tmp_path, name)
    rows = [_row(i + 1, {f"x{j}": float(u[j]) for j in range(3)}, {"s": float(Y[i])}, source="import") for i, u in enumerate(U)]
    return _snap([(f"x{j}", 0, 1) for j in range(3)], rows, outputs=[{"name": "s", "note": "응력 [MPa]"}], models=[m], adapter="data", best_id=1)


def test_table_study_asks_for_rows_not_solver_runs(tmp_path):
    snap = _table_snap(tmp_path, lambda U: np.random.default_rng(1).normal(200, 50, len(U)), "tbl-noise")
    out = insights.compute(snap, True)
    nx = out["overview"]["next"]
    assert nx["key"] == "poor" and "행" in nx["text"] and "행" in nx["label"] and "능동학습" not in nx["request"] and "해석" not in nx["label"], nx
    for sec in out["sections"]:
        for holder in [sec, *(sec.get("variants") or [])]:
            if holder.get("next"):
                assert "능동학습" not in holder["next"]["request"], holder["next"]
    assert "처음 계획한" not in " ".join(x["sentence"] + " ".join(x["notes"]) + x["how_to_read"] for x in out["sections"])


def test_table_study_with_good_model_does_not_loop_on_runs_it_cannot_do(tmp_path):
    snap = _table_snap(tmp_path, lambda U: U[:, 0] + U[:, 1] ** 2, "tbl-good")
    snap.objectives = [{"name": "s", "goal": "min"}]
    snap.best_id = None
    nx = insights.compute(snap, True)["overview"]["next"]
    assert nx["key"] in ("report", "finish", "edge", "margin"), nx


def test_table_study_coverage_does_not_claim_a_plan(tmp_path):
    snap = _table_snap(tmp_path, lambda U: U[:, 0], "tbl-plan")
    out = insights.compute(snap, True)
    assert "처음 있던" in " ".join([_section_of(out, "coverage")["sentence"], *_section_of(out, "coverage")["notes"]])


# ---------- 카드 제목 ----------
def test_renamed_training_cards_replace_the_old_ones_and_keep_their_ids(client):
    admin, _ = _auth(client, "admin")
    pid, sid = _make_study(client, admin, [{"name": "a", "lower": 0, "upper": 1}, {"name": "b", "lower": 0, "upper": 1}], [{"name": "y", "goal": "min"}], name="옛 카드")
    rows = [(_P(u), {"y": float(u[0] + u[1])}, "complete", "doe") for u in qmc.LatinHypercube(d=2, seed=1).random(12)]
    with SessionLocal() as db:
        _add_cases(db, sid, rows)
        s = db.get(models.Study, sid)
        old_a = service.publish_artifact(db, pid, "scatter", f"[{s.name}] y 예측 정확도 (교차검증)", {"series": []}).id
        old_b = service.publish_artifact(db, pid, "table", f"[{s.name}] Surrogate 성능", {"columns": [], "rows": []}).id
        service.train(db, s, None)
        titles = {a.id: a.title for a in db.query(models.Artifact).filter_by(project_id=pid)}
        assert titles[old_a] == f"[{s.name}] y 예측과 실제 비교" and titles[old_b] == f"[{s.name}] 예측 정확도 요약", titles
        assert not any("Surrogate 성능" in t or "교차검증" in t for t in titles.values()), titles


# ---------- 해석 시간 ----------
def _timed(i, stamp=True):
    t = {"started_at": f"2026-01-01T00:{i:02d}:00", "ended_at": f"2026-01-01T00:{i:02d}:30"} if stamp else None
    return _row(i + 1, {"a": i / 12}, {"y": float(i)}, timing=t)


def test_total_time_is_not_stated_when_only_some_cases_were_timed():
    facts = insights.compute(_snap([("a", 0, 1)], [_timed(i, stamp=i < 3) for i in range(12)]), True)["overview"]["facts"]
    assert facts["run_text"] is None and facts["run_hours"] is None, facts
    facts = insights.compute(_snap([("a", 0, 1)], [_timed(i) for i in range(12)]), True)["overview"]["facts"]
    assert facts["run_text"] == "6분", facts


# ---------- 배포 상태 ----------
def test_next_step_changes_after_deploying(client):
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "배포 상태"}, headers=admin).json()["id"]
    U = qmc.LatinHypercube(d=2, seed=5).random(30)
    lines = ["두께\t폭\t질량"] + [f"{1 + 4 * u[0]:.4f}\t{10 + 20 * u[1]:.4f}\t{(1 + 4 * u[0]) * (10 + 20 * u[1]) / 10:.4f}" for u in U]
    r = client.post(f"/api/projects/{pid}/studies/import/text", headers=admin, json={"text": "\n".join(lines), "name": "배포표", "outputs": [{"name": "질량"}]})
    assert r.status_code == 200, r.text
    sid = r.json()["study_id"]
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert _ready(client, sid, admin)["overview"]["next"]["key"] == "finish"
    assert client.post(f"/api/models/{m['id']}/deploy", headers=admin).status_code == 200
    assert _ready(client, sid, admin)["overview"]["next"]["key"] == "report"


# ---------- 영향도 문장 ----------
def test_accurate_model_with_a_true_tie_does_not_blame_accuracy(tmp_path):
    U = qmc.LatinHypercube(d=2, seed=1).random(30)
    y = U[:, 0] + U[:, 1]
    _, m = _fit(U, {"y": y}, tmp_path, "tie")
    rows = [_row(i + 1, _P(u), {"y": float(y[i])}) for i, u in enumerate(U)]
    ctx = Ctx(_snap(V2, rows, objectives=[{"name": "y", "goal": "min"}], models=[m], best_id=1), True)
    sec = insights.influence(ctx)
    assert "정확도가 보통" not in sec["sentence"] and "비슷" in sec["sentence"] and sec["state"] == "good", sec["sentence"]


# ---------- 퍼센트 ----------
def test_percent_is_not_used_when_it_has_no_meaning():
    assert insights._ref_percent(100.0, 50.0, now=80.0)
    assert not insights._ref_percent(0.0529, 1.0, now=-5.0)  # 부호가 바뀐다
    assert not insights._ref_percent(1.86, 20.0, now=-9.0, unit="degC")  # 영점이 임의인 단위
    assert not insights._ref_percent(30.0, 20.0, now=25.0, unit="℃")
    assert not insights._ref_percent(1.0, 1.0, now=50.0)  # 처음 값의 다섯 배를 넘는 변화
    assert not insights._ref_percent(0.001, 1.0)  # 기준이 0에 가깝다


def test_convergence_with_sign_crossing_uses_values(monkeypatch):
    monkeypatch.setattr(insights, "_ei_left", lambda ctx, m, o, now, sign: 0.5)
    rows = [_row(i + 1, {"a": i / 12}, {"y": 0.0529 + i}) for i in range(12)]
    rows += [_row(100 + j, {"a": 0.1 * j}, {"y": float(v)}, source="bo") for j, v in enumerate([-1, -2, -3, -4, -5])]
    sec = insights.convergence(Ctx(_snap([("a", 0, 1)], rows, objectives=[{"name": "y", "goal": "min"}]), True))
    assert "%" not in sec["sentence"] and sec["facts"]["recent_pct"] is None, sec["sentence"]


# ---------- 문장과 표의 숫자 ----------
def test_table_values_keep_precision_so_they_round_like_the_sentence():
    """4자리로 줄인 값을 화면이 또 3자리로 줄이면 237.46이 238이 되어 문장의 237과 어긋난다."""
    rows = [_row(i + 1, {"a": i / 10, "b": 1 - i / 10}, {"f1": float(f1), "f2": float(f2)}) for i, (f1, f2) in enumerate([(1, 300.0), (2, 237.46), (3, 200.0), (4, 150.0), (5, 120.0), (6, 400.0), (7, 500.0)])]
    sec = insights.pareto(Ctx(_snap(V2, rows, objectives=[{"name": "f1", "goal": "min"}, {"name": "f2", "goal": "min"}]), True))
    table = next(c for c in sec["charts"] if c["kind"] == "front_table")
    row = next(r for r in table["rows"] if r["case_id"] == 2)
    assert row["goals"]["f2"] == pytest.approx(237.46, abs=1e-9)
    assert insights.n3(row["goals"]["f2"]) == "237"


# ---------- 연습 함수 ----------
def test_practice_line_reports_counts_not_a_verdict_that_flips_with_seeds():
    f = {"쉬운 함수": (lambda X: X[:, 0] ** 2 + X[:, 1], 2)}
    v = insights.run_benchmark(seeds=2, n_start=8, n_add=2, functions=f)
    assert v["runs"] == 2 and 0 <= v["wins"] <= 2
    line = insights.bench_line({**v, "wins": 1, "runs": 2})
    assert "2번 중 1번" in line and "항상 낫지는 않습니다" in line and "내 결과가 아닙니다" in line
    full = insights.bench_line({**v, "wins": 2, "runs": 2})
    assert "2번 중 2번" in full and "항상 낫지는" not in full and "같다는 뜻은 아닙니다" in full
    assert "나쁘지 않았습니다" not in full


def test_practice_value_in_old_form_is_ignored(tmp_path, monkeypatch):
    p = tmp_path / "bench.json"
    p.write_text(f'{{"code": "{insights._code_hash()}", "functions": {{"a": {{}}}}, "total": 3, "worse": 0, "not_worse": 3}}', encoding="utf-8")
    monkeypatch.setattr(insights, "_bench_path", lambda: p)
    assert insights._bench_value() is None


# ---------- Simulator 입력 단위 ----------
def test_spec_gives_the_unit_written_in_note(client):
    admin, _ = _auth(client, "admin")
    pid, sid = _washer_study(client, admin, n=12, objectives=[{"name": "vibration", "goal": "min"}])
    _run(client, sid, admin)
    m = client.post(f"/api/studies/{sid}/train", json={"outputs": ["vibration"]}, headers=admin).json()
    variables = {v["name"]: v for v in client.get(f"/api/models/{m['id']}/spec", headers=admin).json()["variables"]}
    assert variables["spring_k"]["unit"] == "%" and variables["spring_k"]["note"] == "스프링 강성 [%]"
    assert not variables["balancer_m"].get("unit")


def _spread(n=30, seed=3):
    return [_row(i + 1, {"a": float(u[0]), "b": float(u[1]), "fix": 3.0}, {"y": float(u[0])}) for i, u in enumerate(qmc.LatinHypercube(d=2, seed=seed).random(n))]


def test_fixed_variable_is_not_reported_as_an_unseen_range():
    """하한과 상한이 같은 변수는 살필 범위가 없다. 'fix의 3~3 구간을 보지 않았다'고 하면 안 되고, 고른 정도 판단도 흐리면 안 된다."""
    variables = [("a", 0, 1), ("b", 0, 1), ("fix", 3, 3)]
    sec = insights.coverage(Ctx(_snap(variables, _spread(), outputs=[{"name": "y"}]), True))
    assert "fix" not in sec["sentence"]
    moved = insights.coverage(Ctx(_snap(V2, _spread(), outputs=[{"name": "y"}]), True))
    assert sec["state"] == moved["state"]
    assert sec["sentence"] == moved["sentence"] or "3~3" not in sec["sentence"]


def test_case_outside_the_bounds_does_not_break_coverage():
    """사용자가 범위를 나중에 좁혔거나 표에 범위 밖 행이 있어도 판단이 통째로 실패하면 안 된다."""
    rows = _spread() + [_row(99, {"a": 1.4, "b": -0.2, "fix": 3.0}, {"y": 1.0})]
    sec = insights.coverage(Ctx(_snap(V2, rows, outputs=[{"name": "y"}]), True))
    assert sec["state"] in ("good", "fair", "caution")
    for g in calc.doe_stats(np.array([[1.4, -0.2], [0.2, 0.3], [0.8, 0.9]]))["gaps"]:
        assert 0 <= g["lo"] <= g["hi"] <= 1


def test_all_review_templates_are_plain():
    for key in ("tr_hole", "tr_measuring", "al_unsure", "al_scope", "inf_tie", "inf_moving", "opt_unlearned", "q_goal_none", "q_goal_missing"):
        assert key in insights.T
        assert not re.search("R²|RMSE|σ|교차검증", insights.T[key]), key
