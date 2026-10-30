"""케이스 해석 데이터(case.h5, HDF5): 케이스마다 묶어 저장 → 요약·내려받기 → 원본 npz가 없어도 형상·필드 학습."""
import shutil

from server.app.config import settings
from server.tests.test_api import _auth, _wait_idle, client  # noqa: F401 — 같은 서버 fixture 재사용


def test_case_archive_saved_and_used(client, tmp_path):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "HDF5"}, headers=admin).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "Beam3D", "adapter": "mock", "adapter_config": {"function": "beam3d"},
        "variables": [{"name": "width", "lower": 10, "upper": 40}, {"name": "height", "lower": 20, "upper": 80}],
        "objectives": [{"name": "max_stress", "goal": "min"}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": 6, "seed": 4}, headers=admin)
    client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    _wait_idle(client, sid, admin)
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert all(c["archive"]["stored"] == "server" and c["archive"]["ok"] for c in cases)
    assert cases[0]["archive"]["field_names"] == ["stress"]

    s = client.get(f"/api/cases/{cases[0]['id']}/archive", headers=admin).json()
    assert s["format"] == "dds-cae-h5" and s["checks"]["ok"]
    assert set(s["params"]) == {"width", "height"} and "max_stress" in s["results"]
    assert s["fields"][0]["name"] == "stress" and s["mesh"][0]["nodes"] > 100
    r = client.get(f"/api/cases/{cases[0]['id']}/archive/file", headers=admin)
    assert r.status_code == 200 and r.content[:8] == b"\x89HDF\r\n\x1a\n"

    # 다시 올리기: DDS case.h5만 받는다
    h5 = tmp_path / "case.h5"
    h5.write_bytes(r.content)
    with open(h5, "rb") as f:
        assert client.post(f"/api/cases/{cases[1]['id']}/archive", files={"file": ("case.h5", f)}, headers=admin).status_code == 200
    bad = client.post(f"/api/cases/{cases[1]['id']}/archive", files={"file": ("case.h5", b"not hdf5")}, headers=admin)
    assert bad.status_code == 400

    # 올린 필드(npz)를 지워도 case.h5에서 읽어 형상·필드 학습이 된다
    shutil.rmtree(settings.files_dir / f"project_{pid}" / "fields")
    fm = client.post(f"/api/studies/{sid}/train_field", json={"field": "stress"}, headers=admin)
    assert fm.status_code == 200, fm.text
    assert fm.json()["n_train"] == 6
