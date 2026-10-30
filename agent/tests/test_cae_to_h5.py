"""툴 결과 전체 → case.h5: LS-DYNA(d3plot, lasso로 만든 작은 파일) · Nastran(OP2 결과 표 형태) · Abaqus(h5in npz 규약)."""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np

from cae_adapters.h5store import CaseH5, pack_case, read_snapshot

SKILLS = Path(__file__).resolve().parents[2] / "agent-context" / "skills"


def _load(rel: str):
    p = SKILLS / rel
    spec = importlib.util.spec_from_file_location(p.stem, p)
    m = importlib.util.module_from_spec(spec)
    sys.modules[p.stem] = m
    spec.loader.exec_module(m)
    return m


def test_dyna_all_states(tmp_path):
    from lasso.dyna import ArrayType, D3plot

    x0 = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=np.float32)
    d = D3plot()
    A = d.arrays
    A[ArrayType.node_coordinates], A[ArrayType.node_ids] = x0, np.arange(1, 9)
    A[ArrayType.element_solid_node_indexes], A[ArrayType.element_solid_ids] = np.array([[0, 1, 2, 3, 4, 5, 6, 7]]), np.array([1])
    A[ArrayType.element_solid_part_indexes], A[ArrayType.part_ids] = np.array([0]), np.array([1])
    A[ArrayType.global_timesteps] = np.array([0.0, 0.001, 0.002], dtype=np.float32)
    A[ArrayType.node_displacement] = np.stack([x0 + 0.01 * k for k in range(3)])
    A[ArrayType.element_solid_stress] = np.random.default_rng(0).random((3, 1, 1, 6)).astype(np.float32)
    d.write_d3plot(str(tmp_path / "d3plot"))

    m = _load("lsdyna-automation/scripts/dyna_to_h5.py")
    with CaseH5(tmp_path / "case.h5", "a") as h:
        info = m.pack_d3plot(h, str(tmp_path / "d3plot"))
    assert info["states"] == 3 and "element_solid_stress" in info["fields"]
    s = pack_case(tmp_path)  # PC Agent가 이어서 묶어도 d3plot 내용은 남고, 원본은 raw로 적힌다
    f = {x["name"]: x for x in s["fields"]}
    assert f["element_solid_stress"]["shape"] == [3, 1, 6] and f["coordinates"]["frames"] == 3
    assert s["mesh"][0]["elements"] == 1 and "d3plot" in {r["path"] for r in s["raw"]}
    snap = read_snapshot(tmp_path / "case.h5", "node_displacement")
    assert np.allclose(snap["points"][0], x0[0] + 0.02)  # 마지막 상태의 변형 좌표


class _Disp:
    def __init__(self):
        self.data = np.arange(2 * 4 * 6, dtype=float).reshape(2, 4, 6)
        self.node_gridtype = np.array([[1, 1], [2, 1], [3, 1], [4, 1]])
        self._times = np.array([1.0, 2.0])
        self.headers = ["t1", "t2", "t3", "r1", "r2", "r3"]


class _FakeOp2:
    def __init__(self, bdf):
        self.nodes, self.elements = bdf.nodes, bdf.elements
        self.eigenvalues = {}

    def get_table_types(self):
        return ["displacements", "stress.cquad4_stress"]

    def get_result(self, name):
        return {1: _Disp()} if name == "displacements" else {}


def test_nastran_tables_and_mesh(tmp_path, monkeypatch):
    from pyNastran.bdf.bdf import BDF

    bdf = BDF(debug=False)
    for i, xyz in enumerate([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], 1):
        bdf.add_grid(i, xyz)
    bdf.add_cquad4(10, 1, [1, 2, 3, 4])
    bdf.add_pshell(1, mid1=1, t=1.0)
    bdf.add_mat1(1, 2.1e5, None, 0.3)
    bdf.cross_reference()
    m = _load("simcenter3d-automation/scripts/op2_to_h5.py")
    xyz, nids, elements, element_ids, tris = m.mesh_from(bdf)
    assert xyz.shape == (4, 3) and elements["CQUAD4"].tolist() == [[0, 1, 2, 3]] and tris.shape == (2, 3)

    monkeypatch.setitem(sys.modules, "pyNastran.op2.op2_geom",
                        type(sys)("fake_geom"))
    sys.modules["pyNastran.op2.op2_geom"].read_op2_geom = lambda *a, **k: _FakeOp2(bdf)
    with CaseH5(tmp_path / "case.h5", "a") as h:
        info = m.pack_op2(h, "model.op2", None, None)
    assert info["tables"] == ["subcase_1/displacements"] and info["mesh"]
    with CaseH5(tmp_path / "case.h5", "r") as h:
        ds = h.f["steps/subcase_1/fields/displacements"]
        assert ds.shape == (2, 4, 6) and json.loads(ds.attrs["components"])[0] == "t1"
        assert h.f["steps/subcase_1/labels/displacements"][()].tolist() == [1, 2, 3, 4]
        assert h.f["steps/subcase_1/time"][()].tolist() == [1.0, 2.0]


def test_abaqus_h5in_convention(tmp_path):
    """abq_odb_to_npz.py가 남기는 h5in/*.npz(메쉬·전 프레임 필드)를 pack_case가 그대로 담는다."""
    (tmp_path / "h5in").mkdir()
    np.savez_compressed(tmp_path / "h5in" / "mesh__PART-1-1.npz", points=np.random.default_rng(1).random((5, 3)),
                        node_ids=np.arange(1, 6), elements__C3D4=np.array([[0, 1, 2, 3], [1, 2, 3, 4]]),
                        element_ids__C3D4=np.array([1, 2]), meta_json=json.dumps({"part": "PART-1-1", "name": "mesh"}))
    np.savez_compressed(tmp_path / "h5in" / "Step-1__S__PART-1-1__integration_point.npz", frames=np.ones((4, 2, 6)),
                        time=np.linspace(0, 1, 4), labels=np.array([1, 2]),
                        meta_json=json.dumps({"name": "S", "step": "Step-1", "part": "PART-1-1", "location": "integration_point",
                                              "components": ["S11", "S22", "S33", "S12", "S13", "S23"]}))
    s = pack_case(tmp_path)
    f = s["fields"][0]
    assert (f["step"], f["name"], f["shape"], f["location"]) == ("Step-1", "S", [4, 2, 6], "integration_point")
    assert s["mesh"] == [{"part": "PART-1-1", "nodes": 5, "elements": 2}]
