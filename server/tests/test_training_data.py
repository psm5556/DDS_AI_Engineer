"""학습 데이터: 케이스마다 train.h5 → 데이터셋(manifest). 빠진 케이스와 이유, 케이스 단위 분할, 학습 분할 통계."""
import json
import time

import h5py
import numpy as np

from cae_adapters.train_h5 import spec_hash
from server.app.config import settings
from server.app.study import service
from server.tests.test_api import _auth, _wait_idle, client  # noqa: F401 — 같은 서버 fixture 재사용

UNITS = {"length": "mm", "force": "N", "stress": "MPa", "time": "s"}


def _study(client, admin, n=12, function="bracket_fe", name="브래킷"):  # noqa: F811
    pid = client.post("/api/projects", json={"name": f"학습 데이터 {name}"}, headers=admin).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": name, "adapter": "mock", "adapter_config": {"function": function, "units": UNITS},
        "variables": [{"name": "thickness", "lower": 4, "upper": 12}, {"name": "rib_height", "lower": 5, "upper": 30}],
        "objectives": [{"name": "max_stress", "goal": "min"}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": n, "seed": 3}, headers=admin)
    client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    _wait_idle(client, sid, admin)
    return pid, sid


def _ready(client, ds_id, admin, timeout=60):  # noqa: F811
    t0 = time.time()
    while time.time() - t0 < timeout:
        d = client.get(f"/api/datasets/{ds_id}", headers=admin).json()
        if d["status"] != "building":
            return d
        time.sleep(0.1)
    raise TimeoutError


def test_dataset_from_mock_cases(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid = _study(client, admin)
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert cases[0]["archive"]["extraction"] == {"materials": "ok", "boundary": "ok", "loads": "ok"}

    r = client.post(f"/api/studies/{sid}/datasets", json={"name": "응력", "targets": ["von_mises", "displacement"]}, headers=admin)
    assert r.status_code == 200, r.text
    d = _ready(client, r.json()["id"], admin)
    assert d["status"] == "ready", d
    assert d["n_samples"] == 12 and d["excluded"] == []
    assert sum(d["splits"].values()) == 12 and d["splits"]["test"] >= 1 and d["splits"]["val"] >= 1
    assert sorted(d["inputs"]) == ["fixed_mask", "nodal_load", "prescribed_u"]
    assert d["units_known"] is True and d["same_mesh"] is True

    m = client.get(f"/api/datasets/{d['id']}/manifest", headers=admin).json()
    assert m["format"] == "dds-dataset" and m["variables"] == ["thickness", "rib_height"]
    assert m["targets"] == ["displacement", "von_mises"]
    assert {s["case_id"] for s in m["samples"]} == {c["id"] for c in cases}
    assert {s["split"] for s in m["samples"]} == {"train", "val", "test"}

    # 통계는 학습 분할에서만 계산한다(시험 데이터가 새지 않게)
    vals = []
    for s in m["samples"]:
        path = settings.data_dir / "training" / s["path"]
        assert path.is_file() and len(s["sha256"]) == 64
        if s["split"] == "train":
            with h5py.File(path, "r") as f:
                vals.append(f["targets/von_mises"][()].ravel())
    vals = np.concatenate(vals)
    assert np.isclose(m["stats"]["von_mises"]["mean"][0], vals.mean(), rtol=1e-5)
    assert np.isclose(m["stats"]["von_mises"]["std"][0], vals.std(), rtol=1e-5)
    assert np.isclose(m["stats"]["von_mises"]["max"][0], vals.max(), rtol=1e-5)


def test_excluded_cases_have_reasons(client):  # noqa: F811
    """PC에만 있고 PC가 꺼진 케이스, 실패한 케이스, case.h5가 없는 케이스는 이유와 함께 빠진다."""
    admin, _ = _auth(client, "admin")
    pid, sid = _study(client, admin, name="제외")
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    pc_only, no_h5, failed = cases[0]["id"], cases[1]["id"], cases[2]["id"]
    from server.app import models
    from server.app.database import SessionLocal
    with SessionLocal() as db:
        c = db.get(models.Case, pc_only)
        service.archive_path(pid, sid, pc_only).unlink()
        c.archive = {**c.archive, "stored": "pc", "pc_path": "D:/work/case.h5"}
        c2 = db.get(models.Case, no_h5)
        service.archive_path(pid, sid, no_h5).unlink()
        c2.archive = None
        db.get(models.Case, failed).status = "failed"
        db.commit()
    d = _ready(client, client.post(f"/api/studies/{sid}/datasets", json={"targets": ["von_mises"]}, headers=admin).json()["id"], admin)
    assert d["status"] == "ready" and d["n_samples"] == 9
    why = {x["case_id"]: x["reason"] for x in d["excluded"]}
    assert "PC" in why[pc_only] and "연결" in why[pc_only]
    assert "case.h5" in why[no_h5]
    assert "완료" in why[failed]


def test_too_few_samples_fails_with_reason(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid = _study(client, admin, n=5, name="적음")
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    from server.app import models
    from server.app.database import SessionLocal
    with SessionLocal() as db:
        for c in cases[:2]:
            service.archive_path(pid, sid, c["id"]).unlink()
            db.get(models.Case, c["id"]).archive = None
        db.commit()
    d = _ready(client, client.post(f"/api/studies/{sid}/datasets", json={"targets": ["von_mises"]}, headers=admin).json()["id"], admin)
    assert d["status"] == "failed" and "4개" in d["error"] and len(d["excluded"]) == 2


def test_missing_target_excludes_all_and_fails(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    _, sid = _study(client, admin, n=5, name="없는 출력")
    d = _ready(client, client.post(f"/api/studies/{sid}/datasets", json={"targets": ["temperature"]}, headers=admin).json()["id"], admin)
    assert d["status"] == "failed" and all("temperature" in x["reason"] for x in d["excluded"])


def test_same_spec_reuses_samples_and_new_spec_keeps_old_manifest(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid = _study(client, admin, n=5, name="재사용")
    a = _ready(client, client.post(f"/api/studies/{sid}/datasets", json={"targets": ["von_mises"]}, headers=admin).json()["id"], admin)
    ma = client.get(f"/api/datasets/{a['id']}/manifest", headers=admin).json()
    p0 = settings.data_dir / "training" / ma["samples"][0]["path"]
    t0 = p0.stat().st_mtime_ns
    b = _ready(client, client.post(f"/api/studies/{sid}/datasets", json={"targets": ["von_mises"]}, headers=admin).json()["id"], admin)
    assert b["status"] == "ready" and p0.stat().st_mtime_ns == t0  # 같은 조건이면 다시 만들지 않는다
    c = _ready(client, client.post(f"/api/studies/{sid}/datasets", json={"targets": ["displacement"]}, headers=admin).json()["id"], admin)
    assert c["status"] == "ready" and p0.is_file() and p0.stat().st_mtime_ns == t0  # 다른 조건은 다른 파일, 이전 데이터셋은 그대로
    assert client.get(f"/api/studies/{sid}/datasets", headers=admin).json()[0]["id"] == c["id"]


def test_upload_train_sample_validates(client, tmp_path):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid, sid = _study(client, admin, n=4, name="올리기")
    cid = client.get(f"/api/studies/{sid}/cases", headers=admin).json()[0]["id"]
    url = f"/api/cases/{cid}/train_sample?spec_hash={spec_hash({'targets': ['von_mises']})}"
    assert client.post(url, files={"file": ("t.h5", b"not hdf5")}, headers=admin).status_code == 400
    assert client.post(f"/api/cases/{cid}/train_sample?spec_hash=../../evil", files={"file": ("t.h5", b"x")},
                       headers=admin).status_code == 400
    bad = tmp_path / "other.h5"
    with h5py.File(bad, "w") as f:
        f.attrs["format"] = "something"
    with open(bad, "rb") as fh:
        assert client.post(url, files={"file": ("t.h5", fh)}, headers=admin).status_code == 400


def test_pc_sample_reply_completes_dataset(client, tmp_path):  # noqa: F811
    """PC Agent로 보낸 케이스: 샘플을 올리고 case.train_sample로 답하면 데이터셋이 끝난다."""
    from cae_adapters.train_h5 import build_sample
    from server.app import models
    from server.app.agent.link import registry
    from server.app.database import SessionLocal
    from server.app.study import dataset as ds

    admin, me = _auth(client, "admin")
    pid, sid = _study(client, admin, n=5, name="PC")
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    cid = cases[0]["id"]
    src = tmp_path / "case.h5"
    src.write_bytes(service.archive_path(pid, sid, cid).read_bytes())
    with SessionLocal() as db:
        c = db.get(models.Case, cid)
        service.archive_path(pid, sid, cid).unlink()
        c.archive, c.run_by = {**c.archive, "stored": "pc"}, me["id"]
        db.commit()

    sent = []
    class _Conn:
        def send(self, m):
            sent.append(m)
    registry.conns[me["id"]] = _Conn()
    try:
        spec = {"targets": ["von_mises"], "frames": "last", "step": None}
        d = client.post(f"/api/studies/{sid}/datasets", json={"targets": ["von_mises"]}, headers=admin).json()
        assert d["status"] == "building"
        t0 = time.time()
        while not sent and time.time() - t0 < 10:
            time.sleep(0.05)
        job = sent[0]
        assert job["type"] == "case.train_sample" and job["case_id"] == cid and job["dataset_id"] == d["id"]
        assert job["spec"] == spec and job["spec_hash"] == spec_hash(spec)
        out = tmp_path / "t.h5"
        build_sample(src, out, job["spec"])
        with open(out, "rb") as fh:
            r = client.post(f"/api/cases/{cid}/train_sample?spec_hash={job['spec_hash']}", files={"file": ("t.h5", fh)}, headers=admin)
        assert r.status_code == 200, r.text
        ds.on_sample_status({"dataset_id": d["id"], "case_id": cid, "ok": True, "error": None})
        d = _ready(client, d["id"], admin)
        assert d["status"] == "ready" and d["n_samples"] == 5
    finally:
        registry.conns.pop(me["id"], None)


def test_field_training_reports_excluded_cases(client):  # noqa: F811
    """큰 case.h5가 PC에만 있으면 형상·필드 학습에서 빠지는데, 이제 몇 건이 왜 빠졌는지 남긴다."""
    admin, _ = _auth(client, "admin")
    pid, sid = _study(client, admin, n=6, function="bracket3d", name="필드")
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    from server.app import models
    from server.app.database import SessionLocal
    with SessionLocal() as db:
        c = db.get(models.Case, cases[0]["id"])
        c.fields = []
        c.archive = {**c.archive, "stored": "pc"}
        service.archive_path(pid, sid, c.id).unlink()
        db.commit()
    fm = client.post(f"/api/studies/{sid}/train_field", json={"field": "stress"}, headers=admin).json()
    assert fm["n_train"] == 5
    assert fm["info"]["excluded"] == [{"case_id": cases[0]["id"], "reason": "case.h5가 PC에만 있습니다"}]
    arts = client.get(f"/api/projects/{pid}/artifacts", headers=admin).json()
    text = next(a for a in arts if "형상·필드 모델" in a["title"])["payload"]["text"]
    assert "제외 1개" in text
    json.dumps(fm)  # 응답이 직렬화되는지
