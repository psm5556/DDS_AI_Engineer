"""표로 Simulator 서버 작업과 파일 판별. 화면을 닫아도 끝까지 가고, 돌아오면 DB의 상태를 읽는다."""
import json
import os
import time

import numpy as np
import pytest

from server.tests.test_api import _auth, client  # noqa: F401 — 같은 서버 fixture 재사용, 서버 코드보다 먼저 불러온다

from cae_adapters import result_scan  # noqa: E402
from cae_adapters.tabular import TableEncrypted, TableError, TableNotATable, read_table, sniff_table_file  # noqa: E402
from server.app import models  # noqa: E402
from server.app.database import SessionLocal  # noqa: E402
from server.app.study import table_job  # noqa: E402


def _text(n=20, seed=3, head="두께\t폭\t응력"):
    rng = np.random.default_rng(seed)
    lines = [head]
    for t, w in zip(rng.uniform(1, 5, n), rng.uniform(10, 40, n)):
        lines.append(f"{t:.5f}\t{w:.5f}\t{1000 / (t * w) + 0.5 * t:.6f}")
    return "\n".join(lines) + "\n"


def _wait(client, job_id, h, timeout=90):  # noqa: F811
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/table-jobs/{job_id}", headers=h).json()
        if j["status"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError(f"작업이 끝나지 않음: {j}")


def _start(client, h, text=None, **kw):  # noqa: F811
    body = {"text": text or _text(), "name": "응력 예측", "outputs": [{"name": "응력"}], **kw}
    r = client.post("/api/table-jobs", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_job_registers_trains_and_deploys_on_the_server(client):  # noqa: F811
    h, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "표 작업"}, headers=h).json()["id"]
    job = _start(client, h, project_id=pid)
    assert job["status"] == "running" and job["project_id"] == pid  # 바로 돌아온다
    assert any(j["id"] == job["id"] for j in client.get("/api/table-jobs", headers=h).json())
    # 브라우저가 더 부르지 않아도 끝까지 간다
    done = _wait(client, job["id"], h)
    assert done["status"] == "done" and done["phase"] == "done" and done["rows"] == 20
    m = next(x for x in client.get(f"/api/projects/{pid}/models", headers=h).json() if x["id"] == done["model_id"])
    assert m["deployed"] and m["outputs"] == ["응력"]
    # 돌아왔을 때 읽을 수 있고, 확인하면 목록에서 빠진다
    assert any(j["id"] == job["id"] and j["status"] == "done" for j in client.get("/api/table-jobs", headers=h).json())
    client.post(f"/api/table-jobs/{job['id']}/seen", headers=h)
    assert all(j["id"] != job["id"] for j in client.get("/api/table-jobs", headers=h).json())


def test_job_from_first_screen_makes_a_project_only_if_the_table_is_usable(client):  # noqa: F811
    h, _ = _auth(client, "admin")
    good = _wait(client, _start(client, h, text=_text(seed=5), name="첫 화면 표")["id"], h)
    assert good["status"] == "done" and good["project_id"]
    assert client.get(f"/api/projects/{good['project_id']}", headers=h).json()["name"] == "첫 화면 표"
    before = len(client.get("/api/projects", headers=h).json())
    bad = client.post("/api/table-jobs", json={"text": "두께\t폭\t응력\n1\t2\t3\n", "name": "나쁜 표", "outputs": [{"name": "응력"}]}, headers=h).json()
    failed = _wait(client, bad["id"], h)
    assert failed["status"] == "failed" and "3행" in failed["error"] and "→" in failed["error"] and failed["project_id"] is None
    assert len(client.get("/api/projects", headers=h).json()) == before  # 빈 프로젝트가 남지 않는다


def test_same_table_again_keeps_the_version_the_user_rolled_back_to(client):  # noqa: F811
    h, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "되돌림 작업"}, headers=h).json()["id"]
    text = _text(seed=7)
    first = _wait(client, _start(client, h, text=text, project_id=pid)["id"], h)
    sid = first["study_id"]
    client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"두께": 2.2, "폭": 21.0, "응력": 9999.0}]}, headers=h)
    v2 = client.post(f"/api/studies/{sid}/train", json={"outputs": ["응력"]}, headers=h).json()
    client.post(f"/api/models/{v2['id']}/deploy", headers=h)
    client.post(f"/api/models/{first['model_id']}/deploy", headers=h)
    again = _wait(client, _start(client, h, text=text, project_id=pid)["id"], h)
    assert again["status"] == "done" and again["reused"] and again["model_id"] == first["model_id"]
    live = next(m for m in client.get(f"/api/projects/{pid}/models", headers=h).json() if m["deployed"])
    assert live["id"] == first["model_id"]


def test_job_failure_in_training_keeps_table_and_says_so(client, monkeypatch):  # noqa: F811
    h, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "학습 실패"}, headers=h).json()["id"]
    monkeypatch.setattr(table_job.service, "start_train", lambda *a, **k: (_ for _ in ()).throw(ValueError("값이 극단적입니다 → 확인하세요")))
    failed = _wait(client, _start(client, h, text=_text(seed=8), project_id=pid)["id"], h)
    assert failed["status"] == "failed" and "표는 등록했지만" in failed["error"] and "값이 극단적" in failed["error"] and failed["study_id"]


def test_job_is_only_visible_to_who_started_it(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    other, _ = _auth(client, "job_other", admin)
    pid = client.post("/api/projects", json={"name": "남의 작업"}, headers=admin).json()["id"]
    job = _start(client, admin, text=_text(seed=9), project_id=pid)
    assert client.get(f"/api/table-jobs/{job['id']}", headers=other).status_code == 404
    assert client.post(f"/api/table-jobs/{job['id']}/seen", headers=other).status_code == 404
    assert all(j["id"] != job["id"] for j in client.get("/api/table-jobs", headers=other).json())
    assert client.post("/api/table-jobs", json={"text": _text(), "name": "x", "project_id": pid}, headers=other).status_code == 403
    _wait(client, job["id"], admin)


def test_restart_resumes_registered_jobs_and_explains_the_others(client):  # noqa: F811
    h, me = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "재시작"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies/import/text", json={"text": _text(seed=10), "name": "재시작 표", "outputs": [{"name": "응력"}]}, headers=h).json()["study_id"]
    with SessionLocal() as db:
        done = models.TableJob(user_id=me["id"], project_id=pid, study_id=sid, name="이어 가기", outputs=["응력"], status="running", phase="train")
        lost = models.TableJob(user_id=me["id"], project_id=pid, name="글자 없음", status="running", phase="import")
        db.add_all([done, lost])
        db.commit()
        ids = done.id, lost.id
    table_job.resume_after_restart()
    resumed = _wait(client, ids[0], h)
    assert resumed["status"] == "done" and resumed["model_id"]
    gone = client.get(f"/api/table-jobs/{ids[1]}", headers=h).json()
    assert gone["status"] == "failed" and "다시 붙여 넣어" in gone["error"]


# ---------- 표인지 아닌지
ENC = os.urandom(8000).replace(b"\n", b"x")


@pytest.mark.parametrize("name,data", [
    ("m.cae", b"\x00\x01ABAQUS" + ENC),
    ("p.csv", b"\x89PNG\r\n\x1a\n" + ENC),
    ("x.pdf", b"%PDF-1.7\n" + b"1 0 obj\n<<>>\nendobj\n" * 100),
    ("r.h5", b"\x89HDF\r\n\x1a\n" + ENC),
    ("y.txt", "요구사항\n두께를 바꾼다\n폭을 바꾼다\n".encode()),
    ("z.txt", "이 문서는 설명입니다\n표가 아닙니다\n".encode()),
])
def test_non_table_files_are_not_called_encrypted(name, data):
    with pytest.raises(TableNotATable, match="표가 아닙니다"):
        read_table(data, filename=name)


@pytest.mark.parametrize("name,data", [("a.csv", ENC), ("a.xlsx", ENC), ("b.csv", b"QUJD" * 19 * 50 + b"\n" + b"QUJD" * 19 * 50 + b"\n")])
def test_encrypted_tables_stay_encrypted(name, data):
    with pytest.raises(TableEncrypted, match="암호화"):
        read_table(data, filename=name)


def test_real_tables_are_not_mistaken_for_other_files():
    # 첫 열 이름이 파일 앞머리와 같아도 표다
    assert read_table(b"MZ,FX\n1,2\n2,3\n3,4\n", filename="moment.csv").names == ["MZ", "FX"]
    assert read_table(b"BMI,y\n1,2\n2,3\n3,4\n", filename="b.csv").names == ["BMI", "y"]
    assert read_table(b"t y\n1 2\n2 3\n3 5\n", filename="solver.out").names == ["t", "y"]
    with pytest.raises(TableError):
        read_table(b"", filename="empty.csv")


def test_sniff_only_checks_table_names():
    sniff_table_file(b"\x00\x01binary model", "model.cae")  # 모델 첨부를 막지 않는다
    sniff_table_file(b"t,y\n1,2\n", "ok.csv")
    with pytest.raises(TableEncrypted):
        sniff_table_file(ENC, "enc.csv")
    with pytest.raises(TableEncrypted):
        sniff_table_file(ENC, "enc.xlsx")
    with pytest.raises(TableNotATable):
        sniff_table_file(b"\x89PNG\r\n\x1a\n" + ENC, "pic.csv")


def test_encrypted_attachment_is_told_at_upload_and_models_still_attach(client):  # noqa: F811
    h, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "첨부"}, headers=h).json()["id"]
    r = client.post(f"/api/projects/{pid}/attachments", files={"file": ("결과.xlsx", ENC)}, headers=h)
    assert r.status_code == 400 and "암호화" in r.json()["detail"] and "붙여 넣으세요" in r.json()["detail"]
    r = client.post(f"/api/projects/{pid}/attachments", files={"file": ("그림.csv", b"\x89PNG\r\n\x1a\n" + ENC)}, headers=h)
    assert r.status_code == 400 and "표가 아닙니다" in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}/attachments", headers=h).json() == []
    assert client.post(f"/api/projects/{pid}/attachments", files={"file": ("신규.cae", b"\x00\x01ABAQUS" + ENC)}, headers=h).status_code == 200
    assert client.post(f"/api/projects/{pid}/attachments", files={"file": ("붙여넣은_표_1.tsv", _text().encode())}, headers=h).status_code == 200
    # 첫 화면: 프로젝트가 없어도 고른 순간 듣는다
    r = client.post("/api/tables/check", files={"file": ("결과.csv", ENC)}, headers=h)
    assert r.status_code == 400 and "암호화" in r.json()["detail"]
    assert client.post("/api/tables/check", files={"file": ("좋은.csv", b"t,y\n1,2\n")}, headers=h).status_code == 200


def test_scan_reports_encrypted_summary_instead_of_no_values(tmp_path):
    for i in range(3):
        d = tmp_path / f"t={i + 1}.0_w=30"
        d.mkdir()
        (d / "summary.csv").write_bytes(ENC)
    plain = tmp_path / "t=9.0_w=30"
    plain.mkdir()
    (plain / "results.json").write_text(json.dumps({"s": 1.0}), encoding="utf-8")
    none = tmp_path / "t=8.0_w=30"
    none.mkdir()
    scan = result_scan.scan_folder(tmp_path, max_depth=2, time_limit=5)
    assert scan["encrypted"] == 3 and scan["ready"] == 1
    by = {c["label"]: c for c in scan["cases"]}
    assert by["t=1.0_w=30"]["encrypted"] is True and "암호화" in by["t=1.0_w=30"]["missing"] and "붙여 넣으세요" in by["t=1.0_w=30"]["missing"]
    assert not by["t=8.0_w=30"].get("encrypted") and by["t=8.0_w=30"]["missing"] == "결과값"
    assert "encrypted" in result_scan.brief(scan)["cases"][0]


# ---------- 줄일지 키울지
def test_optimizing_a_table_without_a_goal_asks_instead_of_minimizing(client):  # noqa: F811
    h, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "수명"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies/import/text", json={"text": _text(seed=12, head="두께\t폭\t수명"), "name": "수명", "outputs": [{"name": "수명"}]}, headers=h).json()["study_id"]
    client.post(f"/api/studies/{sid}/train", json={"outputs": ["수명"]}, headers=h)
    r = client.post(f"/api/studies/{sid}/candidates", json={"objective": "수명", "n": 2}, headers=h)
    assert r.status_code == 400 and "수명" in r.json()["detail"] and "줄일까요, 키울까요" in r.json()["detail"]
    up = client.post(f"/api/studies/{sid}/candidates", json={"objective": "수명", "goal": "max", "n": 1}, headers=h).json()
    down = client.post(f"/api/studies/{sid}/candidates", json={"objective": "수명", "goal": "min", "n": 1}, headers=h).json()
    assert up["goal"] == "max" and up["candidates"][0]["predicted"]["수명"] > down["candidates"][0]["predicted"]["수명"]
    # 스터디에 정해 둔 목표는 그대로 쓴다
    sid2 = client.post(f"/api/projects/{pid}/studies/import/text", json={"text": _text(seed=13, head="두께\t폭\t수명"), "name": "목표 있음", "outputs": [{"name": "수명"}], "objectives": [{"name": "수명", "goal": "max"}]}, headers=h).json()["study_id"]
    client.post(f"/api/studies/{sid2}/train", json={"outputs": ["수명"]}, headers=h)
    assert client.post(f"/api/studies/{sid2}/candidates", json={"n": 1}, headers=h).json()["goal"] == "max"


# ---------- 잘못된 행을 지우라는 안내가 실제로 되는가
def test_filled_doe_point_can_be_cleared_as_the_warning_says(client):  # noqa: F811
    h, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "채운 점"}, headers=h).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", json={"name": "계획", "variables": [{"name": "x", "lower": 0, "upper": 10}], "objectives": [{"name": "y", "goal": "min"}], "adapter": "script", "adapter_config": {}}, headers=h).json()["id"]
    cases = client.post(f"/api/studies/{sid}/doe", json={"n": 3, "seed": 1}, headers=h).json()
    x0 = cases[0]["params"]["x"]
    client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"x": x0, "y": 5.0}]}, headers=h)
    # 같은 입력에 다른 출력: 안내가 나오는 경로
    conflict = client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"x": x0, "y": 50.0}]}, headers=h).json()
    assert any("지우세요" in w and "대기로" in w for w in conflict["warnings"])
    r = client.delete(f"/api/studies/{sid}/cases/{cases[0]['id']}", headers=h)
    assert r.status_code == 200 and r.json()["reset"] is True
    now = {c["id"]: c for c in client.get(f"/api/studies/{sid}/cases", headers=h).json()}
    assert now[cases[0]["id"]]["status"] == "pending" and now[cases[0]["id"]]["results"] == {} and now[cases[0]["id"]]["params"]["x"] == x0
    # 표에서 온 행은 지운다
    r = client.delete(f"/api/studies/{sid}/cases/{conflict['case_ids'][0]}", headers=h)
    assert r.status_code == 200 and r.json()["reset"] is False
    # 비운 뒤에는 그 점을 다시 채울 수 있다
    again = client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"x": x0, "y": 6.0}]}, headers=h).json()
    assert again["filled_pending"] == 1
