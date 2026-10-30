"""케이스 폴더 → case.h5(HDF5, 압축): 빠짐없이 담기(메타·필드 전 프레임·이력·작은 파일·원본 목록)와 다시 읽기."""
import json

import numpy as np

from cae_adapters.h5store import CaseH5, pack_case, read_snapshot, summary


def _case(tmp_path):
    d = tmp_path / "C0001_doe"
    (d / "fields").mkdir(parents=True)
    (d / "history").mkdir()
    (d / "params.json").write_text(json.dumps({"t": 2.5, "rib": 12.0}), encoding="utf-8")
    (d / "results.json").write_text(json.dumps({"mass": 1.23, "max_mises": 210.5}), encoding="utf-8")
    (d / "case.json").write_text(json.dumps({"case_id": 1, "study_id": 3}), encoding="utf-8")
    (d / "run.log").write_text("$ solver\nok\n", encoding="utf-8")
    (d / "model.inp").write_text("*NODE\n1, 0, 0, 0\n", encoding="utf-8")
    (d / "model.odb").write_bytes(b"\x00" * 4096)  # 큰 바이너리 결과(원본은 PC에 남는다)
    pts = np.random.default_rng(0).random((50, 3))
    tris = np.array([[0, 1, 2], [2, 3, 4]], dtype=np.int32)
    np.savez_compressed(d / "fields" / "stress.npz", points=pts, values=np.arange(50.0), triangles=tris)
    # 확장 규약: 전 프레임(F, N, C) + 시각 + 단위
    frames = np.stack([np.full((50, 3), k, dtype=np.float64) for k in range(4)])
    np.savez_compressed(d / "fields" / "disp.npz", points=pts, frames=frames, time=np.linspace(0, 0.3, 4),
                        step="Step-1", unit="mm", components=np.array(["U1", "U2", "U3"]))
    (d / "history" / "energy.csv").write_text("time,ke,ie\n0,0,0\n0.1,1.5,0.2\n0.2,1.1,0.6\n", encoding="utf-8")
    return d


def test_pack_case_keeps_everything(tmp_path):
    d = _case(tmp_path)
    s = pack_case(d, meta={"case_id": 1, "tool": "abaqus"})
    assert s["format"] == "dds-cae-h5" and s["checks"]["ok"]
    assert s["params"] == {"t": 2.5, "rib": 12.0} and s["results"]["max_mises"] == 210.5
    f = {(x["step"], x["name"]): x for x in s["fields"]}
    assert f[("final", "stress")]["shape"] == [1, 50, 1]
    assert f[("Step-1", "disp")]["frames"] == 4 and f[("Step-1", "disp")]["unit"] == "mm"
    assert s["history"][0]["columns"] == ["time", "ke", "ie"]
    assert {"run.log", "model.inp", "params.json"} <= {p.split("/")[-1] for p in s["files"]}
    raw = {x["path"]: x for x in s["raw"]}
    assert raw["model.odb"]["size"] == 4096 and len(raw["model.odb"]["sha256"]) == 64

    with CaseH5(d / "case.h5", "r") as h:
        man = h.manifest()
        assert {x["path"] for x in man["files"]} >= {"model.odb", "fields/disp.npz", "history/energy.csv", "case.h5"}
        assert np.allclose(h.f["steps/Step-1/fields/disp"][2], 2.0)  # 프레임 그대로(손실 없음)
        assert h.f["steps/Step-1/fields/disp"].compression == "gzip"
        assert bytes(h.f["files/run.log"][()]) == (d / "run.log").read_bytes()  # 원문 바이트 그대로

    snap = read_snapshot(d / "case.h5", "stress")
    assert snap["points"].shape == (50, 3) and snap["values"][-1] == 49 and snap["triangles"].shape == (2, 3)


def test_repack_updates_results_and_keeps_written_data(tmp_path):
    d = _case(tmp_path)
    pack_case(d)
    # 플랫폼 파이썬의 추출 스크립트가 case.h5에 직접 쓴 필드는 다시 묶어도 남는다
    with CaseH5(d / "case.h5", "a") as h:
        h.add_field("eps", np.ones((3, 10)), step="Step-2", part="main", time=[0.1, 0.2, 0.3])
    (d / "results.json").write_text(json.dumps({"mass": 1.23, "max_mises": 210.5, "disp_max": 3.0}), encoding="utf-8")
    s = pack_case(d)
    assert s["results"]["disp_max"] == 3.0
    assert any(x["name"] == "eps" and x["frames"] == 1 for x in s["fields"])  # (N,C)=(3,10) → 1프레임
    assert summary(d / "case.h5")["checks"]["ok"]
