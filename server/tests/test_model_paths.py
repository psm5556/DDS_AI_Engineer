"""모델 파일 경로와 예측 실패, 없는 API, 판단 도우미의 '모델이 없다'는 잘못된 결론.
- 상대 경로를 저장해 서버를 다른 폴더에서 띄우면 /predict가 맨 500을 냈다.
- 그때 판단 도우미는 '예측 모델이 아직 없습니다'라고 저장했고, 파일을 되살려도 상태 키가 같아 그대로였다(스터디 23)."""
import os
import shutil
import time
from pathlib import Path

import pytest

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402

from server.app import config, models  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.main import spa_file  # noqa: E402
from server.app.study import insights, service  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def trained(client):
    """연습용 계산 12건으로 학습하고 배포한 모델."""
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "경로"}, headers=admin).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", json={
        "name": "경로 스터디", "adapter": "mock", "adapter_config": {"function": "branin", "delay_sec": 0},
        "variables": [{"name": "x1", "lower": -5, "upper": 10}, {"name": "x2", "lower": 0, "upper": 15}],
        "objectives": [{"name": "f", "goal": "min"}]}, headers=admin).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": 12, "seed": 1}, headers=admin).raise_for_status()
    client.post(f"/api/studies/{sid}/run", json={}, headers=admin).raise_for_status()
    end = time.time() + 60
    while client.get(f"/api/studies/{sid}", headers=admin).json()["counts"].get("complete", 0) < 12:
        assert time.time() < end
        time.sleep(0.1)
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    client.post(f"/api/models/{m['id']}/deploy", headers=admin).raise_for_status()
    return {"h": admin, "pid": pid, "sid": sid, "mid": m["id"]}


PARAMS = {"params": {"x1": 1.0, "x2": 2.0}}


def _set_path(mid: int, path: str) -> str:
    with SessionLocal() as db:
        m = db.get(models.SurrogateModel, mid)
        old, m.path = m.path, path
        db.commit()
    return old


def test_new_models_store_a_path_relative_to_the_data_folder(trained):
    with SessionLocal() as db:
        stored = db.get(models.SurrogateModel, trained["mid"]).path
    assert stored == f"models/model_{trained['mid']}.joblib"
    assert not Path(stored).is_absolute()


def test_prediction_survives_running_the_server_from_another_folder(client, trained, tmp_path, monkeypatch):
    before = client.post(f"/api/models/{trained['mid']}/predict", json=PARAMS, headers=trained["h"]).json()
    monkeypatch.chdir(tmp_path)
    r = client.post(f"/api/models/{trained['mid']}/predict", json=PARAMS, headers=trained["h"])
    assert r.status_code == 200 and r.json()["mean"] == before["mean"]


@pytest.mark.parametrize("old", ["data\\dev\\models\\model_{id}.joblib", "data/dev/models/model_{id}.joblib", "C:\\옛 서버\\data\\models\\model_{id}.joblib", "/srv/old/data/models/model_{id}.joblib"])
def test_old_rows_keep_working(client, trained, old, tmp_path, monkeypatch):
    """옛 상대 경로(서버를 띄운 폴더 기준)와 옮기기 전의 절대 경로."""
    keep = _set_path(trained["mid"], old.format(id=trained["mid"]))
    monkeypatch.chdir(tmp_path)
    try:
        r = client.post(f"/api/models/{trained['mid']}/predict", json=PARAMS, headers=trained["h"])
        assert r.status_code == 200, r.text
    finally:
        _set_path(trained["mid"], keep)


def test_relative_data_dir_is_made_absolute(tmp_path, monkeypatch):
    """DDS_DATA_DIR=data\\dev: 설정이 상대 경로로 남으면 그 뒤로 만드는 모든 경로가 폴더에 묶인다."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DDS_DATA_DIR", "rel_data")
    monkeypatch.setenv("DDS_SERVER_CONFIG", str(tmp_path / "none.toml"))
    s = config.load_settings()
    assert s.data_dir.is_absolute() and s.data_dir == (tmp_path / "rel_data").resolve()


def _detail_is_helpful(r, status):
    assert r.status_code == status and r.headers["content-type"].startswith("application/json")
    assert "→" in r.json()["detail"], r.json()


def test_missing_model_file_says_why_and_what_to_do_everywhere(client, trained):
    f = config.settings.data_dir / "models" / f"model_{trained['mid']}.joblib"
    aside = f.with_suffix(".aside")
    token = client.post(f"/api/models/{trained['mid']}/shares", json={}, headers=trained["h"]).json()["token"]
    f.rename(aside)
    try:
        h = trained["h"]
        _detail_is_helpful(client.post(f"/api/models/{trained['mid']}/predict", json=PARAMS, headers=h), 404)
        _detail_is_helpful(client.post(f"/api/models/{trained['mid']}/predict_batch", json={"points": [PARAMS["params"]]}, headers=h), 404)
        _detail_is_helpful(client.post(f"/api/shares/{token}/predict", json=PARAMS, headers=h), 404)
        _detail_is_helpful(client.post(f"/api/shares/{token}/predict_batch", json={"points": [PARAMS["params"]]}, headers=h), 404)
    finally:
        aside.rename(f)
    assert client.post(f"/api/models/{trained['mid']}/predict", json=PARAMS, headers=trained["h"]).status_code == 200


def test_broken_model_file_says_why_and_what_to_do(client, trained):
    f = config.settings.data_dir / "models" / f"model_{trained['mid']}.joblib"
    saved = f.read_bytes()
    f.write_bytes(b"not a model")
    try:
        _detail_is_helpful(client.post(f"/api/models/{trained['mid']}/predict", json=PARAMS, headers=trained["h"]), 500)
    finally:
        f.write_bytes(saved)


def test_unexpected_server_error_is_json_with_an_action(trained, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("뜻밖의 실패")
    monkeypatch.setattr(service, "load_surrogate", boom)
    with TestClient(app, raise_server_exceptions=False) as c:
        _detail_is_helpful(c.post(f"/api/models/{trained['mid']}/predict", json=PARAMS, headers=trained["h"]), 500)


def _sections(v):
    return {x["key"]: x["sentence"] for x in v["sections"]}


def _ready(db, study):
    end = time.time() + 120
    v = insights.get_insights(db, study)
    while v["status"] != "ready":
        assert time.time() < end, "판단 계산이 끝나지 않음"
        time.sleep(0.2)
        v = insights.get_insights(db, study)
    return v


def test_insights_never_say_no_model_when_the_file_cannot_be_read(trained):
    """스터디 23: 파일을 못 읽었는데 '예측 모델이 아직 없습니다'라고 하고, 그 결론을 저장해 파일을 되살려도 그대로였다."""
    f = config.settings.data_dir / "models" / f"model_{trained['mid']}.joblib"
    aside = f.with_suffix(".aside")
    with SessionLocal() as db:
        study = db.get(models.Study, trained["sid"])
        insights.forget([study.id])
        f.rename(aside)
        try:
            v = _ready(db, study)
            s = _sections(v)
            assert v["had_error"] is True
            assert "아직 없습니다" not in s["trust"] and "아직 없습니다" not in s["influence"]
            assert "파일을 읽지 못해" in s["trust"] and "→" in s["trust"]
            assert not insights._cache_path(study.id).exists()  # 잘못된 결론을 파일로 남기지 않는다
        finally:
            aside.rename(f)
        # 파일이 돌아오면 서버를 다시 시작하지 않아도 제대로 판단한다. 상태 키는 그대로다
        insights._memory[study.id]["retry_at"] = 0
        end = time.time() + 120
        s = _sections(insights.get_insights(db, study))
        while "파일을 읽지 못해" in s["trust"]:
            assert time.time() < end, "다시 재지 않음"
            time.sleep(0.2)
            s = _sections(insights.get_insights(db, study))
        assert "아직 없습니다" not in s["trust"]


def test_old_cached_verdicts_are_thrown_away(trained):
    """SCHEMA를 올려 옛 서버가 저장한 '모델이 없다'를 읽지 않는다."""
    import json
    with SessionLocal() as db:
        study = db.get(models.Study, trained["sid"])
        insights.forget([study.id])
        p = insights._cache_path(study.id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"schema": 3, "state_key": insights.state_key(db, study), "complete_layer": True, "owner": insights._owner(study), "sections": []}), encoding="utf-8")
        assert insights._read(study.id) is None
        insights.forget([study.id])


def test_field_model_path_is_relative_and_old_paths_resolve(tmp_path):
    name = "field_99.joblib"
    f = config.settings.data_dir / "models" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"x")
    try:
        assert service.stored_path(f) == f"models/{name}"
        for old in (f"models/{name}", f"data\\dev\\models\\{name}", f"D:\\옛\\models\\{name}"):
            assert service.model_file(old) == f
    finally:
        f.unlink()


def test_model_file_prefers_the_file_under_data_and_reports_the_standard_place_when_missing():
    p = service.model_file("data\\dev\\models\\model_987654.joblib")
    assert p == config.settings.data_dir / "models" / "model_987654.joblib" and not p.exists()


# ---------- 없는 API ----------
def test_unknown_api_is_a_json_404_with_a_reason(client):
    for method, path in (("GET", "/api/projects/1/flows/없는주소"), ("GET", "/api/없는주소"), ("POST", "/api/없는주소"), ("PUT", "/api/없는/주소"), ("DELETE", "/api/없는주소"), ("GET", "/api")):
        r = client.request(method, path)
        assert r.status_code == 404, path
        assert r.headers["content-type"].startswith("application/json"), path
        assert "없는 API" in r.json()["detail"] and "→" in r.json()["detail"], path


def test_real_routes_are_not_shadowed(client, trained):
    assert client.get(f"/api/projects/{trained['pid']}/flows/summary", headers=trained["h"]).status_code == 200
    assert client.get("/api/agent/status", headers=trained["h"]).status_code == 200


def test_screen_paths_still_serve_the_app(tmp_path):
    (tmp_path / "index.html").write_text("앱", encoding="utf-8")
    assert spa_file(tmp_path, "projects/1") == tmp_path / "index.html"


def test_data_dir_can_move(client, trained, tmp_path):
    """data 폴더 안의 경로는 상대이므로 복사해 옮겨도 같은 값으로 읽는다."""
    with SessionLocal() as db:
        stored = db.get(models.SurrogateModel, trained["mid"]).path
    moved = tmp_path / "moved"
    shutil.copytree(config.settings.data_dir / "models", moved / "models")
    keep = config.settings.data_dir
    try:
        config.settings.data_dir = moved
        assert service.model_file(stored) == moved / "models" / f"model_{trained['mid']}.joblib"
    finally:
        config.settings.data_dir = keep
    assert os.path.exists(config.settings.data_dir / "models")
