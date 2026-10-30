"""로그인 화면의 해석 결과 파일 검사. 파일이 깨지면 화면이 그 해석을 조용히 건너뛰므로 여기서 잡는다.
결과를 만드는 해법 자체의 검증은 scripts/hero/*.py --check 이다."""
import json
from pathlib import Path

import numpy as np
import pytest

HERO = Path(__file__).resolve().parents[2] / "frontend" / "public" / "hero"
IDS = ["structural", "thermal", "drop"]
TYPES = {"uint8": np.uint8, "int16": np.int16, "uint32": np.uint32}


def _load(name):
    meta = json.loads((HERO / f"{name}.json").read_text(encoding="utf-8"))
    raw = (HERO / f"{name}.bin").read_bytes()
    arrays = {}
    for key, info in meta["arrays"].items():
        count = int(np.prod(info["shape"]))
        arrays[key] = np.frombuffer(raw, dtype=TYPES[info["type"]], count=count, offset=info["offset"]).reshape(info["shape"])
    return meta, arrays


@pytest.mark.parametrize("name", IDS)
def test_result_is_consistent(name):
    meta, a = _load(name)
    frames, nodes, _ = a["pos"].shape
    assert a["field"].shape == (frames, nodes)
    assert len(meta["steps"]) == frames, "단계 정보와 프레임 수가 다릅니다"
    for key in ("tris", "feature", "mesh"):
        assert a[key].max() < nodes, f"{key}가 없는 절점을 가리킵니다"
    assert all(0 <= s["maxNode"] < nodes for s in meta["steps"])
    assert np.isfinite(meta["lo"]).all() and np.isfinite(meta["span"]).all() and min(meta["span"]) > 0
    assert meta["vmax"] > meta["vmin"]
    for row in meta["readout"]:  # 화면이 읽는 값이 단계마다 있어야 한다
        assert all(row[1] in s for s in meta["steps"]), row


@pytest.mark.parametrize("name", IDS)
def test_says_it_is_an_example_with_method(name):
    meta, _ = _load(name)
    assert meta["method"] and meta["element"] and meta["elements"] > 1000  # 무엇으로 계산했는지 화면에 보인다


def test_thermal_is_physically_bounded():
    """열원만 있으므로 입구 온도보다 차가운 곳이 없고, 핀이 높을수록 최고 온도는 낮고 압력 손실은 크다."""
    meta, a = _load("thermal")
    assert meta["vmin"] == 25.0
    steps = sorted(meta["steps"], key=lambda s: s["H"])
    assert [s["max"] for s in steps] == sorted((s["max"] for s in steps), reverse=True)
    assert [s["dp"] for s in steps] == sorted(s["dp"] for s in steps)
    assert all(s["max"] > s["outlet"] > 25 for s in steps)
    assert all(0.85 < s["balance"] < 1.15 for s in steps), "에너지 수지가 크게 어긋납니다"
    # 유선은 같은 수의 점으로 이어진다
    assert a["lineIndex"].max() < a["linePos"].shape[1]


def test_structural_thin_design_has_higher_stress():
    meta, _ = _load("structural")
    steps = sorted(meta["steps"], key=lambda s: s["t"])
    assert steps[0]["sigma"] > steps[-1]["sigma"]
    assert steps[0]["mass"] < steps[-1]["mass"]


def test_drop_contact_happens_and_stress_stays_near_yield():
    meta, _ = _load("drop")
    steps = meta["steps"]
    assert steps[0]["t"] == 0 and max(s["force"] for s in steps) > 100
    assert max(s["max"] for s in steps) < 400, "완전 탄소성 재료의 항복응력(280MPa)을 크게 넘으면 안 됩니다"
