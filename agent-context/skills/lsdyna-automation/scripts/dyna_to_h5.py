"""LS-DYNA 결과 전체 → case.h5(HDF5 압축) — d3plot의 모든 상태(state)·모든 배열과 binout 시간 이력을 빠짐없이 담는다.

실행(플랫폼 python, 케이스 폴더에서 — 스터디 adapter_config.post에 붙이거나 dds-cae.run_script로):
  python dyna_to_h5.py [d3plot] [--binout "binout*"] [--every 1] [--out case.h5]
  --every k : k개 상태마다 하나(1이면 전부). 상태가 수백 개인 충돌 해석에서 크기를 줄일 때만 쓴다
담는 것(docs/cae-data-hdf5.md 스키마):
  /mesh/model        노드 좌표·id, 요소 형식별 연결(element_<형식>_node_indexes)·id, 외곽 표면 삼각형, 그 밖의 정적 배열(extra/)
  /steps/d3plot      time(상태 시각) + 시간에 따라 바뀌는 모든 배열(F, N, C): 변위·속도·가속도·응력·변형률·소성변형률·살아 있는 요소 등
                     node_displacement(lasso: 현재 좌표)는 'coordinates'로도 넣어 변형 형상 학습에 쓴다
  /history/binout_<분류>_<변수>  glstat·matsum·rcforc·nodout 등 binout의 모든 숫자 이력
원본(d3plot·binout)은 PC 케이스 폴더에 그대로 두고, PC Agent가 케이스가 끝나면 case.h5를 서버로 올린다.
TODO(사내검증): 사내 LS-DYNA 버전의 d3plot·binout을 lasso-python 2.0.3이 모두 읽는지, 큰 모델(수백만 요소·수백 상태)의 시간·크기
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import numpy as np

HEX_FACES = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def _platform_path():
    """케이스 폴더에서 돌 때 cae_adapters(저장소 또는 설치된 Agent)를 찾는다."""
    here = Path(__file__).resolve()
    for p in [*here.parents, Path.cwd(), *Path.cwd().parents]:
        if (p / "cae_adapters" / "h5store.py").is_file():
            sys.path.insert(0, str(p))
            return
    try:  # PC Agent가 실행할 때는 패키지 경로가 이미 잡혀 있다
        import cae_adapters  # noqa: F401
    except ImportError:
        raise SystemExit("cae_adapters를 찾지 못했습니다 — PC Agent의 python(tool 'python')으로 실행하세요")


def surface_triangles(A, ArrayType) -> np.ndarray:
    """쉘은 그 자체, 솔리드는 한 요소에만 속한 면(외곽)을 삼각형으로."""
    faces = {}
    shells = A.get(ArrayType.element_shell_node_indexes)
    if shells is not None:
        for q in shells:
            faces[tuple(sorted(set(q)))] = (1, tuple(dict.fromkeys(q)))
    solids = A.get(ArrayType.element_solid_node_indexes)
    if solids is not None:
        cnt = {}
        for hx in solids:
            for f in HEX_FACES:
                face = tuple(hx[j] for j in f)
                key = tuple(sorted(set(face)))
                c, _ = cnt.get(key, (0, face))
                cnt[key] = (c + 1, face)
        faces.update({k: v for k, v in cnt.items() if v[0] == 1})
    tris = []
    for _, f in faces.values():
        f = list(f)
        if len(f) >= 3:
            tris.append((f[0], f[1], f[2]))
        if len(f) == 4:
            tris.append((f[0], f[2], f[3]))
    return np.array(tris, dtype=np.int64).reshape(-1, 3)


def _frames(arr: np.ndarray) -> np.ndarray:
    """(F, ...) → (F, N, C): 적분점·층·성분 축은 C로 펼친다(원래 모양은 attrs.shape에 남긴다)."""
    if arr.ndim == 1:
        return arr[:, None, None]
    if arr.ndim == 2:
        return arr[:, :, None]
    return arr.reshape(arr.shape[0], arr.shape[1], -1)


def pack_d3plot(h, path: str, every: int = 1) -> dict:
    from lasso.dyna import ArrayType, D3plot

    d = D3plot(path)
    A = d.arrays
    t = np.asarray(A.get(ArrayType.global_timesteps, []), dtype=np.float64)
    n_states = len(t)
    pick = slice(None, None, max(1, every))
    elements = {k[len("element_"):-len("_node_indexes")]: np.asarray(v) for k, v in A.items()
                if k.startswith("element_") and k.endswith("_node_indexes") and np.asarray(v).ndim == 2}
    element_ids = {k[len("element_"):-len("_ids")]: np.asarray(v) for k, v in A.items()
                   if k.startswith("element_") and k.endswith("_ids") and np.asarray(v).ndim == 1}
    h.add_mesh("model", A[ArrayType.node_coordinates], node_ids=A.get(ArrayType.node_ids), elements=elements,
               element_ids=element_ids, surface=surface_triangles(A, ArrayType))
    stored, static = [], []
    for key, val in A.items():
        arr = np.asarray(val)
        if key == ArrayType.global_timesteps or arr.dtype.kind not in "fiub":
            continue
        if n_states and arr.ndim >= 1 and arr.shape[0] == n_states and key != ArrayType.node_coordinates:
            loc = "node" if key.startswith("node_") else "element" if key.startswith("element_") else "global"
            h.add_field(key, _frames(arr[pick]), step="d3plot", time=t[pick], location=loc, part="model",
                        shape=list(arr.shape[1:]))
            stored.append(key)
        elif key not in (ArrayType.node_coordinates, ArrayType.node_ids) and not key.endswith(("_node_indexes", "_ids")):
            h._put(f"mesh/model/extra/{key}", arr)
            static.append(key)
    if ArrayType.node_displacement in A:  # lasso의 node_displacement는 상태별 현재 좌표다
        h.add_field("coordinates", np.asarray(A[ArrayType.node_displacement])[pick], step="d3plot", time=t[pick],
                    location="node", part="model")
    return {"states": n_states, "stored_states": len(t[pick]), "fields": stored, "static": static}


def pack_binout(h, pattern: str) -> list[str]:
    from lasso.dyna import Binout

    if not glob.glob(pattern):
        return []
    b = Binout(pattern)
    names = []
    for cat in b.read():
        try:
            variables = b.read(cat)
        except Exception:  # noqa: BLE001 — 읽지 못한 분류는 건너뛰고 나머지를 담는다
            continue
        time = None
        try:
            time = np.asarray(b.read(cat, "time"), dtype=np.float64)
        except Exception:  # noqa: BLE001
            pass
        for var in variables:
            if var == "time":
                continue
            try:
                arr = np.asarray(b.read(cat, var))
            except Exception:  # noqa: BLE001
                continue
            if arr.dtype.kind not in "fiu" or not arr.size:
                continue
            arr = arr.reshape(arr.shape[0], -1) if arr.ndim > 1 else arr[:, None]
            if time is not None and len(time) == arr.shape[0]:
                data, cols = np.column_stack([time, arr]), ["time"] + [f"{var}[{i}]" for i in range(arr.shape[1])]
            else:
                data, cols = arr, [f"{var}[{i}]" for i in range(arr.shape[1])]
            h.add_history(f"binout_{cat}_{var}", data, columns=cols)
            names.append(f"{cat}/{var}")
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("d3plot", nargs="?", default="d3plot")
    ap.add_argument("--binout", default="binout*")
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--out", default="case.h5")
    a = ap.parse_args()
    _platform_path()
    from cae_adapters.h5store import CaseH5

    with CaseH5(a.out, "a") as h:
        h.set_meta(tool="lsdyna", source="d3plot+binout")
        info = pack_d3plot(h, a.d3plot, a.every) if Path(a.d3plot).exists() else {}
        hist = pack_binout(h, a.binout)
    print(f"case.h5: d3plot 상태 {info.get('stored_states', 0)}/{info.get('states', 0)} · 배열 {len(info.get('fields', []))}개 · "
          f"binout 이력 {len(hist)}개")


if __name__ == "__main__":
    main()
