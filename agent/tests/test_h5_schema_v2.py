"""case.h5 스키마 v2: 재료·경계조건·하중·단위와 추출 상태. v1 파일 읽기, 같은 이름 필드가 덮어써지지 않는지."""
import json

import numpy as np

from cae_adapters.h5store import CaseH5, archive_info, pack_case, summary


def test_same_field_on_two_parts_is_not_overwritten(tmp_path):
    """인스턴스가 둘인 Abaqus 모델: S가 파트마다 따로 저장되어야 한다(전에는 나중 파트가 앞을 덮어썼다)."""
    (tmp_path / "h5in").mkdir()
    for part, value in (("A-1", 1.0), ("B-1", 2.0)):
        np.savez_compressed(tmp_path / "h5in" / f"Step-1__S__{part}__integration_point.npz", frames=np.full((2, 3, 6), value),
                            time=[0.0, 1.0], labels=np.array([1, 2, 3]),
                            meta_json=json.dumps({"name": "S", "step": "Step-1", "part": part, "location": "integration_point"}))
    s = pack_case(tmp_path)
    got = {x["part"]: x for x in s["fields"]}
    assert set(got) == {"A-1", "B-1"}
    with CaseH5(tmp_path / "case.h5", "r") as h:
        for part, value in (("A-1", 1.0), ("B-1", 2.0)):
            path = f"steps/Step-1/fields/{got[part]['name']}"
            assert np.allclose(h.f[path][()], value)
    assert archive_info(s)["field_names"] == ["S"]  # 화면에는 같은 이름 하나로 보인다
    assert pack_case(tmp_path)["fields"] == s["fields"]  # 다시 묶어도 같은 자리에


def test_same_field_at_two_locations_is_not_overwritten(tmp_path):
    with CaseH5(tmp_path / "case.h5", "a") as h:
        h.add_field("S", np.ones((1, 4, 6)), part="A", location="integration_point")
        h.add_field("S", np.full((1, 8, 6), 2.0), part="A", location="element_nodal")
        assert {x["location"] for x in h.fields()} == {"integration_point", "element_nodal"}


def test_v1_file_reads_as_not_extracted(tmp_path):
    """스키마 v1 파일(재료·경계조건·하중 없음)은 그대로 열리고, 세 항목은 '추출 못 함'으로 본다."""
    with CaseH5(tmp_path / "case.h5", "a") as h:
        h.f.attrs["schema"] = 1
    s = summary(tmp_path / "case.h5")
    assert s["schema"] == 1
    assert {k: v["status"] for k, v in s["extraction"].items()} == {"materials": "not_extracted", "boundary": "not_extracted",
                                                                    "loads": "not_extracted"}


def test_no_deck_means_not_extracted_with_reason(tmp_path):
    (tmp_path / "params.json").write_text("{}", encoding="utf-8")
    s = pack_case(tmp_path)
    assert s["schema"] == 2
    for item in ("materials", "boundary", "loads"):
        assert s["extraction"][item]["status"] == "not_extracted" and "입력 덱" in s["extraction"][item]["reason"]
    assert archive_info(s)["extraction"] == {"materials": "not_extracted", "boundary": "not_extracted", "loads": "not_extracted"}


def test_other_tool_deck_says_extractor_is_missing(tmp_path):
    (tmp_path / "model.k").write_text("*KEYWORD\n*END\n", encoding="utf-8")
    s = pack_case(tmp_path)
    assert "추출기" in s["extraction"]["boundary"]["reason"] and ".k" in s["extraction"]["boundary"]["reason"]


def test_units_and_setup_written_by_script_survive_repack(tmp_path):
    """플랫폼 파이썬 스크립트가 직접 쓴 입력 정보와 상태는 다시 묶어도 남는다."""
    (tmp_path / "params.json").write_text("{}", encoding="utf-8")
    with CaseH5(tmp_path / "case.h5", "a") as h:
        h.add_material("STEEL", model="elastic", props={"E": 210e3, "nu": 0.3}, tables={"plastic": np.array([[250.0, 0.0]])})
        h.add_boundary("Load", "P", fixed_mask=np.ones((2, 6), np.uint8), prescribed_u=np.zeros((2, 6)))
        h.add_nodal_force("Load", "P", np.ones((2, 6)))
        h.set_body_load("Load", gravity=[0.0, 0.0, -9810.0], rotation={"omega2": 4.0, "point": [0, 0, 0], "axis": [0, 0, 1]})
        h.set_extraction("materials", "ok", source="script")
        h.set_extraction("boundary", "ok", source="script")
        h.set_extraction("loads", "none", source="script")
    s = pack_case(tmp_path, units={"length": "mm", "stress": "MPa"})
    assert s["units"] == {"length": "mm", "stress": "MPa"}
    assert s["extraction"]["boundary"] == {"status": "ok", "source": "script", "reason": "", "warnings": []}
    assert s["extraction"]["loads"]["status"] == "none"
    assert s["materials"] == [{"name": "STEEL", "model": "elastic", "E": 210e3, "nu": 0.3}]
    with CaseH5(tmp_path / "case.h5", "r") as h:
        assert h.f["boundary/Load/P/fixed_mask"].shape == (2, 6)
        assert h.f["materials/STEEL/plastic"].shape == (1, 2)
        assert json.loads(h.f["loads/Load/body"].attrs["gravity"]) == [0.0, 0.0, -9810.0]
