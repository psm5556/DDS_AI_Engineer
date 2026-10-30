"""Abaqus 입력 덱(.inp) → case.h5의 재료·경계조건·하중. 고정 노드 수와 하중 합계를 덱에서 손으로 센 값과 맞춘다."""
import json

import numpy as np

from cae_adapters.h5store import CaseH5, pack_case

# 격자: x=0,1,2 · y=0,1 · z=0,1 / 노드 번호 z=0: 1~6, z=1: 7~12 / 육면체 2개
GRID = """*NODE, NSET=ALL
1,0,0,0
2,1,0,0
3,2,0,0
4,0,1,0
5,1,1,0
6,2,1,0
7,0,0,1
8,1,0,1
9,2,0,1
10,0,1,1
11,1,1,1
12,2,1,1
*ELEMENT, TYPE=C3D8R, ELSET=BODY
1, 1,2,5,4,7,8,11,10
2, 2,3,6,5,8,9,12,11
"""
FLAT = """*HEADING
** 주석은 건너뛴다
""" + GRID + """*NSET, NSET=FIX
1,4,7,10
*NSET, NSET=TIP
3,6,9,12
*MATERIAL, NAME=Steel
*ELASTIC
210000., 0.3
*DENSITY
7.85e-9
*PLASTIC
250., 0.
400., 0.2
*SOLID SECTION, ELSET=BODY, MATERIAL=Steel
*BOUNDARY
FIX, ENCASTRE
*STEP, NAME=Load
*STATIC
*CLOAD
TIP, 3, -250.
*DLOAD
BODY, GRAV, 9810., 0., 0., -1.
BODY, CENTRIF, 1600., 0., 0., 0., 0., 0., 1.
2, P2, 10.
*END STEP
*STEP, NAME=Release
*STATIC
*BOUNDARY, OP=NEW
FIX, 1, 3
*CLOAD, OP=NEW
TIP, 3, -100.
*END STEP
"""


def _pack(tmp_path, text, name="model.inp"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return pack_case(tmp_path)


def test_flat_deck_mesh_and_material(tmp_path):
    s = _pack(tmp_path, FLAT)
    assert s["extraction"]["materials"]["status"] == "ok" and s["extraction"]["materials"]["source"] == "model.inp"
    assert s["mesh"] == [{"part": "PART-1-1", "nodes": 12, "elements": 2}]
    assert s["materials"] == [{"name": "STEEL", "model": "elastic", "E": 210000.0, "nu": 0.3, "density": 7.85e-9}]
    with CaseH5(tmp_path / "case.h5", "r") as h:
        m = h.f["mesh/PART-1-1"]
        assert m["node_ids"][()].tolist() == list(range(1, 13))
        assert m["elements/C3D8R"][0].tolist() == [0, 1, 4, 3, 6, 7, 10, 9]  # 번호가 아니라 인덱스
        assert m["element_material/C3D8R"][()].tolist() == [0, 0]
        assert m["surface"].shape[0] == 20  # 바깥면 10개 × 삼각형 2개(맞닿은 면 1개는 제외)
        assert h.f["materials/STEEL/plastic"][()].tolist() == [[250.0, 0.0], [400.0, 0.2]]


def test_flat_deck_boundary_counts_match_deck(tmp_path):
    s = _pack(tmp_path, FLAT)
    assert s["extraction"]["boundary"]["status"] == "ok"
    with CaseH5(tmp_path / "case.h5", "r") as h:
        load = h.f["boundary/Load/PART-1-1/fixed_mask"][()]
        release = h.f["boundary/Release/PART-1-1/fixed_mask"][()]
    assert load.shape == (12, 6)
    assert np.flatnonzero(load.all(axis=1)).tolist() == [0, 3, 6, 9]  # ENCASTRE: 6자유도 전부
    assert load.sum() == 4 * 6
    assert release.sum() == 4 * 3 and not release[:, 3:].any()  # OP=NEW로 다시 정한 병진 3자유도만


def test_flat_deck_load_totals_match_deck(tmp_path):
    s = _pack(tmp_path, FLAT)
    assert s["extraction"]["loads"]["status"] == "ok"
    with CaseH5(tmp_path / "case.h5", "r") as h:
        assert h.f["loads/Load/PART-1-1/nodal_force"][()][:, 2].sum() == -1000.0  # 4노드 × -250
        assert h.f["loads/Release/PART-1-1/nodal_force"][()][:, 2].sum() == -400.0  # OP=NEW: 앞 스텝 하중을 지우고 4 × -100
        rows = h.f["loads/Load/PART-1-1/pressure/C3D8R"][()]
        assert rows.tolist() == [[1.0, 2.0, 10.0]]  # 요소 행 · 면 번호 · 압력
        assert "loads/Release/PART-1-1/pressure/C3D8R" in h.f  # 압력은 OP=NEW가 없으니 다음 스텝으로 이어진다
        body = h.f["loads/Load/body"].attrs
        assert json.loads(body["gravity"]) == [0.0, 0.0, -9810.0]
        assert json.loads(body["rotation"]) == {"omega2": 1600.0, "point": [0.0, 0.0, 0.0], "axis": [0.0, 0.0, 1.0]}


PARTS = """*PART, NAME=Plate
*NODE
1,0,0,0
2,1,0,0
3,1,1,0
4,0,1,0
*ELEMENT, TYPE=S4R, ELSET=Sk
1, 1,2,3,4
*NSET, NSET=Edge
1,4
*SHELL SECTION, ELSET=Sk, MATERIAL=Al
2.5, 5
*END PART
*ASSEMBLY, NAME=A
*INSTANCE, NAME=Plate-1, PART=Plate
10., 0., 0.
*END INSTANCE
*INSTANCE, NAME=Plate-2, PART=Plate
*END INSTANCE
*NODE
100, 5., 5., 5.
*NSET, NSET=Tip, INSTANCE=Plate-1
2,3
*COUPLING, CONSTRAINT NAME=C1, REF NODE=100, SURFACE=Sf
*END ASSEMBLY
*MATERIAL, NAME=Al
*ELASTIC
70000., 0.33
*STEP, NAME=S1
*STATIC
*BOUNDARY
Plate-2.Edge, 1, 3
*CLOAD
Tip, 2, 10.
100, 1, 7.
Plate-1.1, 3, 1.5
*END STEP
"""


def test_part_instance_deck_resolves_sets_and_instances(tmp_path):
    s = _pack(tmp_path, PARTS)
    assert {x["part"]: x["nodes"] for x in s["mesh"]} == {"PLATE-1": 4, "PLATE-2": 4, "ASSEMBLY": 1}
    assert s["extraction"]["boundary"]["status"] == "ok" and s["extraction"]["loads"]["status"] == "ok"
    with CaseH5(tmp_path / "case.h5", "r") as h:
        assert h.f["mesh/PLATE-1/nodes"][()][0].tolist() == [10.0, 0.0, 0.0]  # 인스턴스 이동 반영
        assert h.f["mesh/PLATE-2/nodes"][()][0].tolist() == [0.0, 0.0, 0.0]
        assert h.f["mesh/PLATE-1/element_thickness/S4R"][()].tolist() == [2.5]
        assert h.f["boundary/S1/PLATE-2/fixed_mask"][()].sum() == 2 * 3
        f1 = h.f["loads/S1/PLATE-1/nodal_force"][()]
        assert f1[:, 1].sum() == 20.0 and f1[0, 2] == 1.5
        assert h.f["loads/S1/ASSEMBLY/nodal_force"][()][0, 0] == 7.0
    assert any("COUPLING" in w for w in s["extraction"]["boundary"]["warnings"])  # 입력에 담지 못한 구속은 알린다


def test_existing_mesh_order_is_kept(tmp_path):
    """ODB에서 나온 메쉬(노드 순서가 다름)가 이미 있으면 그 순서에 맞춘다."""
    (tmp_path / "h5in").mkdir()
    ids = np.arange(12, 0, -1)
    np.savez_compressed(tmp_path / "h5in" / "mesh__PART-1-1.npz", points=np.zeros((12, 3)), node_ids=ids,
                        elements__C3D8R=np.array([[11, 10, 7, 8, 5, 4, 1, 2], [10, 9, 6, 7, 4, 3, 0, 1]]),
                        element_ids__C3D8R=np.array([1, 2]), meta_json=json.dumps({"part": "PART-1-1", "name": "mesh"}))
    _pack(tmp_path, FLAT)
    with CaseH5(tmp_path / "case.h5", "r") as h:
        assert np.allclose(h.f["mesh/PART-1-1/nodes"][()], 0)  # 덱이 메쉬를 덮어쓰지 않는다
        mask = h.f["boundary/Load/PART-1-1/fixed_mask"][()]
    assert sorted(ids[mask.all(axis=1)].tolist()) == [1, 4, 7, 10]


def test_deck_without_boundary_is_none_not_missing(tmp_path):
    s = _pack(tmp_path, GRID + "*STEP\n*FREQUENCY\n5\n*END STEP\n")
    assert s["extraction"]["boundary"]["status"] == "none"
    assert s["extraction"]["loads"]["status"] == "none"
    assert s["extraction"]["materials"]["status"] == "none"


def test_unresolved_parameter_is_not_guessed(tmp_path):
    s = _pack(tmp_path, FLAT.replace("TIP, 3, -250.", "TIP, 3, <LOAD>"))
    for item in ("materials", "boundary", "loads"):
        assert s["extraction"][item]["status"] == "not_extracted"
    assert "<LOAD>" in s["extraction"]["loads"]["reason"]
    assert s["checks"]["ok"]  # 추출을 못 해도 case.h5 저장은 성공한다


def test_unknown_set_is_not_guessed(tmp_path):
    s = _pack(tmp_path, FLAT.replace("FIX, ENCASTRE", "NOPE, ENCASTRE"))
    assert s["extraction"]["boundary"]["status"] == "not_extracted" and "NOPE" in s["extraction"]["boundary"]["reason"]


def test_missing_include_is_reported(tmp_path):
    s = _pack(tmp_path, "*INCLUDE, INPUT=mesh.inp\n" + FLAT)
    assert s["extraction"]["boundary"]["status"] == "not_extracted" and "mesh.inp" in s["extraction"]["boundary"]["reason"]


def test_include_and_continuation_lines(tmp_path):
    (tmp_path / "mesh.inc").write_text(GRID.replace("1, 1,2,5,4,7,8,11,10", "1, 1,2,5,4,\n7,8,11,10"), encoding="utf-8")
    s = _pack(tmp_path, "*INCLUDE, INPUT=mesh.inc\n*NSET, NSET=F, GENERATE\n1, 7, 3\n*BOUNDARY\nF, 2\n")
    assert s["mesh"][0]["elements"] == 2
    assert s["extraction"]["boundary"]["status"] == "ok"
    with CaseH5(tmp_path / "case.h5", "r") as h:
        assert h.f["mesh/PART-1-1/elements/C3D8R"][0].tolist() == [0, 1, 4, 3, 6, 7, 10, 9]
        mask = h.f["boundary/Step-1/PART-1-1/fixed_mask"][()]  # 스텝이 없으면 모델 전체 조건을 기본 이름으로 둔다
    assert np.flatnonzero(mask[:, 1]).tolist() == [0, 3, 6]  # 1, 4, 7


def test_amplitude_is_warned(tmp_path):
    s = _pack(tmp_path, FLAT.replace("*CLOAD\nTIP, 3, -250.", "*CLOAD, AMPLITUDE=Ramp\nTIP, 3, -250."))
    assert s["extraction"]["loads"]["status"] == "ok"
    assert any("AMPLITUDE" in w for w in s["extraction"]["loads"]["warnings"])


def test_largest_deck_is_chosen(tmp_path):
    (tmp_path / "small.inp").write_text("*NODE\n1,0,0,0\n", encoding="utf-8")
    s = _pack(tmp_path, FLAT)
    assert s["extraction"]["boundary"]["source"] == "model.inp"
