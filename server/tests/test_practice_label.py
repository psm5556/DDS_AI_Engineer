"""연습용 계산(mock)의 결과는 어디서 보든 연습용이라고 적혀 있다. 사용자와 AI 모두 실제 해석으로 읽었다('질량 880 g').
서버가 정한다: 화면·보고서·AI 자료가 같은 값을 받는다."""
import io
import time

import pytest
from docx import Document

from server.tests.test_api import _auth, app  # 임시 data 폴더를 먼저 정한다  # noqa: F401
from fastapi.testclient import TestClient  # noqa: E402

from server.app.database import SessionLocal  # noqa: E402
from server.app import models  # noqa: E402
from server.app.study import insights, service  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def world(client):
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "연습"}, headers=admin).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", json={
        "name": "연습 스터디", "adapter": "mock", "adapter_config": {"function": "branin", "delay_sec": 0},
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


def test_study_says_practice_not_mock(client, world):
    st = client.get(f"/api/studies/{world['sid']}", headers=world["h"]).json()
    assert st["tool_label"] == "연습용 계산" and st["study"]["practice"] is True
    assert st["data_source"]["kind"] == "practice" and st["data_source"]["label"] == "연습용 계산"
    assert "실제 해석이 아니라" in st["data_source"]["notice"] and "→" in st["data_source"]["notice"]
    assert "mock" not in st["tool_label"]


def test_real_solver_and_table_are_not_marked(client, world):
    sid = client.post(f"/api/projects/{world['pid']}/studies", json={
        "name": "실제", "adapter": "script", "adapter_config": {"tool": "abaqus", "script": "scripts/run.py"},
        "variables": [{"name": "x", "lower": 0, "upper": 1}]}, headers=world["h"]).json()["id"]
    st = client.get(f"/api/studies/{sid}", headers=world["h"]).json()
    assert st["tool_label"] == "abaqus" and st["study"]["practice"] is False
    assert st["data_source"]["kind"] == "solver" and "notice" not in st["data_source"]


def test_simulator_spec_list_and_share_carry_the_notice(client, world):
    h = world["h"]
    spec = client.get(f"/api/models/{world['mid']}/spec", headers=h).json()
    assert spec["data_source"]["label"] == "연습용 계산" and spec["data_source"]["notice"]
    item = next(x for x in client.get("/api/simulators", headers=h).json()["mine"] if x["model_id"] == world["mid"])
    assert item["data_source"]["label"] == "연습용 계산"
    token = client.post(f"/api/models/{world['mid']}/shares", json={}, headers=h).json()["token"]
    shared = client.get(f"/api/shares/{token}", headers=h).json()
    assert shared["data_source"]["label"] == "연습용 계산" and shared["data_source"]["notice"]  # 받는 사람이 가장 모른다


def _ready(db, study):
    end = time.time() + 120
    v = insights.get_insights(db, study)
    while v["status"] != "ready":
        assert time.time() < end
        time.sleep(0.2)
        v = insights.get_insights(db, study)
    return v


def test_every_judgement_sentence_is_marked(world):
    with SessionLocal() as db:
        study = db.get(models.Study, world["sid"])
        insights.forget([study.id])
        v = _ready(db, study)
        assert v["overview"]["practice"] == service.PRACTICE_NOTE
        for x in v["sections"]:
            assert x["sentence"].startswith("연습용 계산 기준: "), x["key"]
            assert "해석으로 확인된" not in x["sentence"], x["key"]
            for q in x.get("variants") or []:
                assert q["sentence"].startswith("연습용 계산 기준: "), x["key"]
        m = db.get(models.SurrogateModel, world["mid"])
        assert insights.trust_line(db, m)["sentence"].startswith("연습용 계산 기준: ")


def test_report_marks_practice_even_if_the_ai_does_not(client, world):
    h, pid = world["h"], world["pid"]
    ctx = client.get(f"/api/projects/{pid}/report-context", headers=h).json()
    assert ctx["practice_note"] == service.PRACTICE_NOTE
    st = next(x for x in ctx["studies"] if x["study_id"] == world["sid"])
    assert st["practice"] is True and st["solver"] == "연습용 계산"
    rep = client.post(f"/api/projects/{pid}/reports", json={"title": "결과", "body": "## 요약\n질량 880 g입니다."}, headers=h).json()
    assert "연습용 계산" in rep["provenance"] and "실제 해석" in rep["provenance"]
    assert service.PRACTICE_NOTE in rep["colophon"]
    html = client.get(f"/api/projects/{pid}/report", params={"format": "html"}, headers=h).text
    assert "연습용 계산 포함(실제 해석 아님)" in html and service.PRACTICE_NOTE in html
    doc = Document(io.BytesIO(client.get(f"/api/projects/{pid}/report", params={"format": "docx"}, headers=h).content))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "연습용 계산 포함(실제 해석 아님)" in text and service.PRACTICE_NOTE in text


def test_report_of_a_real_project_is_not_marked(client, world):
    h = world["h"]
    pid = client.post("/api/projects", json={"name": "실제 과제"}, headers=h).json()["id"]
    rep = client.post(f"/api/projects/{pid}/reports", json={"title": "결과", "body": "## 요약\n값"}, headers=h).json()
    assert "연습용" not in rep["provenance"] and "연습용" not in rep["colophon"]
    assert client.get(f"/api/projects/{pid}/report-context", headers=h).json()["practice_note"] is None


def test_adapter_list_tells_the_ai_the_screen_name(client, world):
    mock = next(a for a in client.get("/api/adapters", headers=world["h"]).json() if a["name"] == "mock")
    assert mock["label"] == "연습용 계산" and "실제 해석이 아니라" in mock["description"]
