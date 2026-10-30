"""case.h5 → train.h5(학습 샘플): 변형 전 좌표, 노드 기준 출력, 단위 환산, 노드별 입력, 추출 못 한 입력 제외."""
import json

import h5py
import numpy as np
import pytest

from agent.tests.test_deck_abaqus import FLAT
from cae_adapters.h5store import CaseH5, pack_case
from cae_adapters.train_h5 import build_sample, spec_hash

XYZ = np.array([[x, y, 0] for y in (0, 1) for x in (0, 1, 2)] + [[x, y, 1] for y in (0, 1) for x in (0, 1, 2)], dtype=np.float64)


def _case(tmp_path, units=None, deck=FLAT):
    """육면체 2개(FLAT 덱) + ODB에서 뽑은 것과 같은 h5in: 변위(3프레임, 번호 역순), 응력(적분점 1개), 변형 좌표."""
    (tmp_path / "h5in").mkdir(exist_ok=True)
    if deck:
        (tmp_path / "model.inp").write_text(deck, encoding="utf-8")
    (tmp_path / "params.json").write_text(json.dumps({"t": 2.0}), encoding="utf-8")
    (tmp_path / "results.json").write_text(json.dumps({"mass": 3.0}), encoding="utf-8")
    ids = np.arange(1, 13)
    conn = np.array([[0, 1, 4, 3, 6, 7, 10, 9], [1, 2, 5, 4, 7, 8, 11, 10]])
    np.savez_compressed(tmp_path / "h5in" / "mesh__PART-1-1.npz", points=XYZ, node_ids=ids, elements__C3D8R=conn,
                        element_ids__C3D8R=np.array([1, 2]), meta_json=json.dumps({"part": "PART-1-1", "name": "mesh"}))
    t = np.array([0.0, 0.5, 1.0])
    u = np.stack([np.tile([0.0, 0.0, k], (12, 1)) * ids[::-1, None] for k in (1.0, 2.0, 3.0)])
    np.savez_compressed(tmp_path / "h5in" / "Load__U__PART-1-1__nodal.npz", frames=u, time=t, labels=ids[::-1],
                        meta_json=json.dumps({"name": "U", "step": "Load", "part": "PART-1-1", "location": "nodal",
                                              "components": ["U1", "U2", "U3"]}))
    s = np.zeros((3, 2, 6))
    s[:, 0, 0], s[:, 1, 0] = 100.0, 200.0  # 요소 1은 S11=100, 요소 2는 S11=200
    np.savez_compressed(tmp_path / "h5in" / "Load__S__PART-1-1__integration_point.npz", frames=s, time=t, labels=np.array([1, 2]),
                        meta_json=json.dumps({"name": "S", "step": "Load", "part": "PART-1-1", "location": "integration_point",
                                              "components": ["S11", "S22", "S33", "S12", "S13", "S23"]}))
    np.savez_compressed(tmp_path / "h5in" / "Load__COORD.npz", frames=np.stack([XYZ + 9.0] * 3), time=t, labels=ids,
                        meta_json=json.dumps({"name": "coordinates", "step": "Load", "part": "PART-1-1", "location": "node"}))
    pack_case(tmp_path, units=units)
    return tmp_path / "case.h5"


def test_geometry_is_undeformed_and_targets_on_nodes(tmp_path):
    info = build_sample(_case(tmp_path), tmp_path / "t.h5", {"targets": ["displacement", "von_mises"]})
    assert info["format"] == "dds-train-h5" and info["nodes"] == 12 and info["frames"] == 1
    assert info["targets"] == {"displacement": [1, 12, 3], "von_mises": [1, 12, 1]}
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert np.allclose(f["geometry/nodes"][()], XYZ)  # 변형 좌표(+9)가 아니라 변형 전 좌표
        assert f["geometry/nodes"].dtype == np.float32 and f["targets/displacement"].compression == "gzip"
        assert f["targets/time"][()].tolist() == [1.0]  # 기본은 마지막 프레임
        # 노드 번호 k의 변위: 3프레임의 U3 = 3 × (13 - 역순 번호 위치). 라벨을 따라 제자리에 들어갔는지 본다
        assert np.allclose(f["targets/displacement"][0, :, 2], 3.0 * np.arange(1, 13))
        vm = f["targets/von_mises"][0, :, 0]
    assert np.allclose(vm[[0, 3, 6, 9]], 100.0)  # 요소 1에만 속한 노드
    assert np.allclose(vm[[2, 5, 8, 11]], 200.0)  # 요소 2에만 속한 노드
    assert np.allclose(vm[[1, 4, 7, 10]], 150.0)  # 두 요소가 만나는 노드: 성분 평균 후 Mises


def test_frames_are_evenly_spaced_with_last_included(tmp_path):
    info = build_sample(_case(tmp_path), tmp_path / "t.h5", {"targets": ["displacement"], "frames": 2})
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert f["targets/time"][()].tolist() == [0.0, 1.0]
    assert info["frames"] == 2


def test_units_are_converted_to_mm_mpa_n(tmp_path):
    h5 = _case(tmp_path, units={"length": "m", "stress": "Pa", "force": "kN"})
    info = build_sample(h5, tmp_path / "t.h5", {"targets": ["displacement", "von_mises"]})
    assert info["units_known"] is True
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert f.attrs["length_unit"] == "mm" and f.attrs["stress_unit"] == "MPa" and f.attrs["force_unit"] == "N"
        assert np.allclose(f["geometry/nodes"][()], XYZ * 1000)
        assert np.allclose(f["targets/displacement"][0, 0, 2], 3000.0)
        assert np.allclose(f["targets/von_mises"][0, 0, 0], 100e-6)
        assert f["targets/von_mises"].attrs["unit"] == "MPa"
        assert np.allclose(f["inputs/nodal_load"][()][:, 2].sum(), -1000.0 * 1000 - 10.0 * 1000)  # kN → N, 압력 합력도 환산


def test_unknown_units_are_flagged_and_values_kept(tmp_path):
    info = build_sample(_case(tmp_path), tmp_path / "t.h5", {"targets": ["displacement"]})
    assert info["units_known"] is False
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert f.attrs["length_unit"] == "unknown"
        assert np.allclose(f["geometry/nodes"][()], XYZ)


def test_inputs_come_from_deck(tmp_path):
    info = build_sample(_case(tmp_path), tmp_path / "t.h5", {"targets": ["von_mises"]})
    assert sorted(info["inputs"]) == ["fixed_mask", "nodal_load", "prescribed_u"]
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert f["inputs/fixed_mask"].shape == (12, 6) and f["inputs/fixed_mask"][()].sum() == 24
        load = f["inputs/nodal_load"][()]
        assert np.allclose(load[:, 2].sum(), -1000.0 - 10.0)  # 절점 하중 + 압력(윗면 1×1, 안쪽 방향) 합력
        assert np.allclose(load[[1, 2, 4, 5], 2], [0.0, -250.0, 0.0, -250.0])  # 아랫면 노드에는 압력이 없다
        assert np.allclose(load[[7, 8, 10, 11], 2], [-2.5, -252.5, -2.5, -252.5])
        c = f["conditions"].attrs
        assert c["params.t"] == 2.0 and c["results.mass"] == 3.0
        assert json.loads(c["global.gravity"]) == [0.0, 0.0, -9810.0] and json.loads(c["global.rotation"])["omega2"] == 1600.0
        assert json.loads(c["extraction"])["boundary"] == "ok"
        attrs = f["geometry/node_attrs"]
        assert attrs["youngs_modulus"][0] == 210000.0 and attrs["part_id"][()].tolist() == [0] * 12


def test_not_extracted_inputs_are_left_out(tmp_path):
    info = build_sample(_case(tmp_path, deck=None), tmp_path / "t.h5", {"targets": ["von_mises"]})
    assert info["inputs"] == [] and info["extraction"]["boundary"] == "not_extracted"
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert "inputs" not in f


def test_none_status_writes_zeros(tmp_path):
    deck = FLAT.split("*BOUNDARY")[0]  # 경계조건·하중·스텝이 없는 덱
    info = build_sample(_case(tmp_path, deck=deck), tmp_path / "t.h5", {"targets": ["von_mises"]})
    assert info["extraction"]["boundary"] == "none" and info["extraction"]["loads"] == "none"
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert f["inputs/fixed_mask"][()].sum() == 0 and f["inputs/nodal_load"][()].sum() == 0


def test_two_parts_are_merged_and_missing_values_are_nan(tmp_path):
    (tmp_path / "h5in").mkdir()
    for part, offs, n in (("A", 0.0, 2), ("B", 5.0, 3)):
        np.savez_compressed(tmp_path / "h5in" / f"mesh__{part}.npz", points=np.full((n, 3), offs), node_ids=np.arange(1, n + 1),
                            meta_json=json.dumps({"part": part, "name": "mesh"}))
    np.savez_compressed(tmp_path / "h5in" / "S1__U__A.npz", frames=np.ones((1, 2, 3)), labels=np.array([1, 2]),
                        meta_json=json.dumps({"name": "U", "step": "S1", "part": "A", "location": "nodal"}))
    pack_case(tmp_path)
    info = build_sample(tmp_path / "case.h5", tmp_path / "t.h5", {"targets": ["displacement"]})
    assert info["nodes"] == 5 and info["qc"]["nan_nodes"]["displacement"] == 3
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert f["geometry/node_attrs/part_id"][()].tolist() == [0, 0, 1, 1, 1]
        assert np.isnan(f["targets/displacement"][0, 2:, :]).all() and (f["targets/displacement"][0, :2, :] == 1).all()


def test_legacy_field_npz_is_flagged(tmp_path):
    """기존 fields/*.npz(노드 번호 없음, 좌표가 변형 형상일 수 있음)도 쓰되 표시해 둔다."""
    (tmp_path / "fields").mkdir()
    pts = np.random.default_rng(0).random((10, 3))
    np.savez_compressed(tmp_path / "fields" / "stress.npz", points=pts, values=np.arange(10.0), triangles=np.array([[0, 1, 2]]))
    pack_case(tmp_path)
    info = build_sample(tmp_path / "case.h5", tmp_path / "t.h5", {})
    assert info["targets"] == {"von_mises": [1, 10, 1]} and info["geometry_source"] == "field_points"
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert f.attrs["geometry_source"] == "field_points"
        assert f["targets/von_mises"].attrs["source_field"] == "stress"
        assert f["geometry/normals"].shape == (10, 3)


def test_lsdyna_positions_become_displacement(tmp_path):
    """lasso의 node_displacement는 변위가 아니라 현재 좌표다. coordinates - 초기 좌표로 변위를 만든다."""
    x0 = np.array([[0.0, 0, 0], [1, 0, 0]])
    with CaseH5(tmp_path / "case.h5", "a") as h:
        h.add_mesh("model", x0, node_ids=np.array([1, 2]))
        pos = np.stack([x0 + 0.5, x0 + 1.5])
        h.add_field("node_displacement", pos, step="d3plot", time=[0.0, 1.0], location="node", part="model")
        h.add_field("coordinates", pos, step="d3plot", time=[0.0, 1.0], location="node", part="model")
    info = build_sample(tmp_path / "case.h5", tmp_path / "t.h5", {})
    assert list(info["targets"]) == ["displacement"]
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert np.allclose(f["targets/displacement"][0], 1.5)


def test_lsdyna_element_stress_without_labels(tmp_path):
    x0 = np.array([[0.0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]])
    with CaseH5(tmp_path / "case.h5", "a") as h:
        h.add_mesh("model", x0, node_ids=np.arange(1, 9), elements={"solid": np.arange(8)[None]}, element_ids={"solid": np.array([1])})
        s = np.zeros((1, 1, 12))  # 층 2개 × 6성분을 펼친 모양
        s[0, 0, 0], s[0, 0, 6] = 100.0, 300.0
        h.add_field("element_solid_stress", s, step="d3plot", time=[1.0], location="element", part="model", shape=[1, 2, 6])
    build_sample(tmp_path / "case.h5", tmp_path / "t.h5", {"targets": ["von_mises"]})
    with h5py.File(tmp_path / "t.h5", "r") as f:
        assert np.allclose(f["targets/von_mises"][0, :, 0], 200.0)  # 층 평균 S11 = 200


def test_missing_requested_target_is_reported(tmp_path):
    with pytest.raises(ValueError, match="temperature"):
        build_sample(_case(tmp_path), tmp_path / "t.h5", {"targets": ["temperature"]})


def test_requested_source_name_passes_through(tmp_path):
    h5 = _case(tmp_path)
    with CaseH5(h5, "a") as h:
        h.add_field("MYVAR", np.full((3, 12, 1), 7.0), step="Load", time=[0.0, 0.5, 1.0], location="nodal", part="PART-1-1",
                    labels=np.arange(1, 13))
    info = build_sample(h5, tmp_path / "t.h5", {"targets": ["MYVAR"]})
    assert info["targets"] == {"myvar": [1, 12, 1]}


def test_spec_hash_is_stable_and_order_free():
    a = spec_hash({"targets": ["von_mises", "displacement"], "frames": "last"})
    assert a == spec_hash({"frames": "last", "targets": ["displacement", "von_mises"]}) and len(a) == 12
    assert a != spec_hash({"targets": ["displacement"], "frames": "last"})
