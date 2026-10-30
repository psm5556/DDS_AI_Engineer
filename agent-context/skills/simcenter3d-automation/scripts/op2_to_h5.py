"""Nastran OP2 결과 전체 → case.h5(HDF5 압축) — Simcenter 3D(NX Nastran)·MSC Nastran·OptiStruct 공통.
OP2에 든 모든 결과 표(변위·고유벡터·응력·변형률·힘·SPC/MPC 반력·온도 등)를 서브케이스·모드·시간마다 빠짐없이 담는다.

실행(플랫폼 python, 케이스 폴더에서 — 스터디 adapter_config.post에 붙이거나 dds-cae.run_script로):
  python op2_to_h5.py [model.op2] [--bdf model.bdf] [--mode nx|msc|optistruct] [--out case.h5]
  --bdf : 메쉬(노드 좌표·요소 연결)를 입력 덱에서 읽는다. 없으면 OP2에 기하(PARAM,POST,-1 / OP2GEOM)가 있을 때 그것을 쓴다
담는 것(docs/cae-data-hdf5.md 스키마):
  /mesh/model          노드 좌표·id(GRID), 요소 형식별 연결·id(CQUAD4, CTETRA …), 외곽 표면 삼각형(쉘·솔리드 면)
  /steps/subcase_<n>/fields/<표 이름>   (F, N, C) — F는 시간·모드·하중 단계, N은 노드 또는 요소(·절점), C는 성분(headers)
                     labels/<표 이름>: 행마다 노드 id(node_gridtype) 또는 요소 id·절점(element_node)
                     time: 시간·주파수·고유값(모드) — OP2의 _times
  고유치 해석은 eigenvalues 표(모드별 고유값·진동수)도 /history/eigenvalues_<이름>에 담는다
TODO(사내검증): 사내 Nastran 버전 OP2를 pyNastran 1.4.1이 읽는지(mode 지정), 복소수 결과(주파수 응답)의 크기·위상 저장, 큰 모델 시간
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

SHELL = ("CQUAD4", "CTRIA3", "CQUAD8", "CTRIA6", "CQUADR", "CTRIAR")


def _platform_path():
    here = Path(__file__).resolve()
    for p in [*here.parents, Path.cwd(), *Path.cwd().parents]:
        if (p / "cae_adapters" / "h5store.py").is_file():
            sys.path.insert(0, str(p))
            return


def mesh_from(model) -> tuple | None:
    """BDF/OP2GEOM 모델 → (좌표, 노드 id, {형식: 연결(인덱스)}, {형식: 요소 id}, 표면 삼각형)."""
    nodes = getattr(model, "nodes", None) or {}
    if not nodes:
        return None
    nids = np.array(sorted(nodes), dtype=np.int64)
    try:
        xyz = np.array([model.nodes[n].get_position() for n in nids], dtype=np.float64)  # 전역 좌표계
    except Exception:  # noqa: BLE001 — 좌표계 해석이 안 되면 입력 좌표 그대로
        xyz = np.array([model.nodes[n].xyz for n in nids], dtype=np.float64)
    index = {n: i for i, n in enumerate(nids)}
    by_type: dict[str, list] = {}
    for eid, e in sorted((getattr(model, "elements", None) or {}).items()):
        conn = [index.get(n, -1) for n in (e.node_ids or []) if n is not None]
        by_type.setdefault(e.type, []).append((eid, conn))
    elements, element_ids, tris = {}, {}, []
    for et, rows in by_type.items():
        width = max(len(c) for _, c in rows)
        elements[et] = np.array([c + [-1] * (width - len(c)) for _, c in rows], dtype=np.int64)  # -1 = 빈 자리
        element_ids[et] = np.array([eid for eid, _ in rows], dtype=np.int64)
        if et in SHELL:
            for _, c in rows:
                tris.append((c[0], c[1], c[2]))
                if len(c) >= 4 and et.startswith("CQUAD"):
                    tris.append((c[0], c[2], c[3]))
    return xyz, nids, elements, element_ids, np.array(tris, dtype=np.int64).reshape(-1, 3)


def pack_op2(h, op2_path: str, bdf: str | None, mode: str | None) -> dict:
    from pyNastran.op2.op2_geom import read_op2_geom

    kw = {"debug": False, "build_dataframe": False}
    if mode:
        kw["mode"] = mode
    try:
        model = read_op2_geom(op2_path, **kw)
    except Exception:  # noqa: BLE001 — 기하가 없는 OP2도 결과는 읽는다
        from pyNastran.op2.op2 import read_op2
        model = read_op2(op2_path, **kw)
    geo = model
    if bdf:
        from pyNastran.bdf.bdf import read_bdf
        geo = read_bdf(bdf, xref=True, debug=False)
    mesh = mesh_from(geo)
    if mesh:
        xyz, nids, elements, element_ids, tris = mesh
        h.add_mesh("model", xyz, node_ids=nids, elements=elements, element_ids=element_ids, surface=tris if len(tris) else None)
    stored = []
    for table in model.get_table_types():
        try:
            res = model.get_result(table)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(res, dict):
            continue
        for key, obj in res.items():
            data = getattr(obj, "data", None)
            if data is None or not hasattr(data, "shape") or data.ndim < 2:
                continue
            arr = np.asarray(data)
            if np.iscomplexobj(arr):  # 주파수 응답 등: 실수부·허수부를 성분으로 나란히
                arr = np.concatenate([arr.real, arr.imag], axis=-1)
            frames = arr if arr.ndim == 3 else arr[None]
            sub = key if isinstance(key, (int, np.integer)) else "_".join(str(k) for k in (key if isinstance(key, tuple) else (key,)))
            if getattr(obj, "node_gridtype", None) is not None:
                labels, loc = np.asarray(obj.node_gridtype)[:, 0], "node"
            elif getattr(obj, "element_node", None) is not None:
                labels, loc = np.asarray(obj.element_node), "element_node"
            elif getattr(obj, "element", None) is not None:
                labels, loc = np.asarray(obj.element), "element"
            else:
                labels, loc = None, "global"
            times = getattr(obj, "_times", None)
            heads = [str(x) for x in (getattr(obj, "headers", None) or [])]
            h.add_field(table.replace(".", "__"), frames, step=f"subcase_{sub}", time=times, location=loc, part="model",
                        components=heads + ([f"{x}_imag" for x in heads] if np.iscomplexobj(data) else []), labels=labels,
                        table=type(obj).__name__)
            stored.append(f"subcase_{sub}/{table}")
    for name, eig in (getattr(model, "eigenvalues", None) or {}).items():  # 고유치 표(모드·고유값·진동수)
        cols = [c for c in ("mode", "eigenvalues", "radians", "cycles", "generalized_mass", "generalized_stiffness") if hasattr(eig, c)]
        if cols:
            h.add_history(f"eigenvalues_{name}", np.column_stack([np.asarray(getattr(eig, c), dtype=np.float64) for c in cols]), columns=cols)
    return {"tables": stored, "mesh": bool(mesh)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("op2", nargs="?")
    ap.add_argument("--bdf")
    ap.add_argument("--mode", choices=["nx", "msc", "optistruct", "autodesk"])
    ap.add_argument("--out", default="case.h5")
    a = ap.parse_args()
    op2 = a.op2 or next(iter(sorted(Path(".").glob("*.op2"))), None)
    if not op2:
        raise SystemExit("OP2 파일이 없습니다 (PARAM,POST,-1 로 OP2를 쓰게 하세요)")
    _platform_path()
    from cae_adapters.h5store import CaseH5

    with CaseH5(a.out, "a") as h:
        h.set_meta(tool="nastran", source=str(op2))
        info = pack_op2(h, str(op2), a.bdf, a.mode)
    print(f"case.h5: 결과 표 {len(info['tables'])}개 · 메쉬 {'있음' if info['mesh'] else '없음(--bdf로 지정)'}")


if __name__ == "__main__":
    main()
