"""Nastran OP2 (Simcenter Nastran · MSC · OptiStruct) → fields/<이름>.npz (3D 형상·필드 학습용)

실행(플랫폼 python, pyNastran 필요):
  python op2_fields.py <result.op2> [--bdf model.bdf] [--field disp|vm] [--name 이름] [--subcase 1] [--deformed 1] [--scale 1.0]
  --field disp : 변위 크기 (노드 값 — 가장 안정적)
          vm   : 고체(CTETRA/CHEXA/CPENTA)·쉘(CQUAD4/CTRIA3) von Mises를 노드로 평균  — TODO(사내검증): 버전별 결과 테이블 이름
  --bdf        : 형상(GRID·요소)을 OP2에 쓰지 않은 경우(PARAM,POST,-1 없음) 모델 파일을 함께 준다
출력: fields/<name>.npz  points(N,3) · values(N,) · triangles(M,3) — 외곽 표면 노드만
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np

SOLID_FACES = {
    "CTETRA": (4, [(0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3)]),
    "CPENTA": (6, [(0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (0, 2, 5, 3)]),
    "CHEXA": (8, [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]),
}
SHELLS = {"CTRIA3": 3, "CTRIA6": 3, "CQUAD4": 4, "CQUAD8": 4}


def load(op2: str, bdf: str | None):
    """→ (결과 모델, 형상 모델). 형상이 OP2에 있으면 둘은 같은 객체."""
    if bdf:
        from pyNastran.bdf.bdf import read_bdf
        from pyNastran.op2.op2 import read_op2
        return read_op2(op2, debug=False), read_bdf(bdf, debug=False)
    from pyNastran.op2.op2_geom import read_op2_geom
    model = read_op2_geom(op2, debug=False)
    return model, model


def surface(geom) -> tuple[list[tuple[int, ...]], dict[int, list[int]]]:
    count, shells, elem_nodes = {}, [], {}
    for eid, el in geom.elements.items():
        nids = [n for n in el.node_ids if n]
        elem_nodes[eid] = nids
        if el.type in SOLID_FACES:
            nc, faces = SOLID_FACES[el.type]
            corners = nids[:nc]
            for f in faces:
                face = tuple(corners[j] for j in f)
                key = tuple(sorted(face))
                c, _ = count.get(key, (0, face))
                count[key] = (c + 1, face)
        elif el.type in SHELLS:
            shells.append(tuple(nids[:SHELLS[el.type]]))
    faces = [face for c, face in count.values() if c == 1] + shells
    tris = []
    for f in faces:
        tris.append((f[0], f[1], f[2]))
        if len(f) == 4:
            tris.append((f[0], f[2], f[3]))
    return tris, elem_nodes


def nodal_vm(model, subcase: int, elem_nodes: dict[int, list[int]]) -> dict[int, float]:
    """요소 von Mises를 노드로 평균 (요소 중심값을 그 요소의 모든 노드에 나눠 줌)."""
    acc, cnt = defaultdict(float), defaultdict(int)
    stress = model.op2_results.stress
    for name in ("ctetra_stress", "chexa_stress", "cpenta_stress", "cquad4_stress", "ctria3_stress"):
        res = getattr(stress, name, {}).get(subcase)
        if res is None:
            continue
        data = res.data[-1]  # 마지막 시간/하중 단계
        headers = list(res.get_headers() if hasattr(res, "get_headers") else getattr(res, "headers", []))
        col = headers.index("von_mises") if "von_mises" in headers else len(headers) - 1
        eids = res.element_node[:, 0] if hasattr(res, "element_node") else res.element
        for row, eid in enumerate(eids):
            vm = float(data[row, col])
            for nid in elem_nodes.get(int(eid), []):
                acc[nid] += vm
                cnt[nid] += 1
    return {n: acc[n] / cnt[n] for n in acc}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("op2")
    ap.add_argument("--bdf")
    ap.add_argument("--field", default="disp", choices=["disp", "vm"])
    ap.add_argument("--name")
    ap.add_argument("--subcase", type=int, default=1)
    ap.add_argument("--deformed", default="0")
    ap.add_argument("--scale", type=float, default=1.0)
    a = ap.parse_args()
    model, geom = load(a.op2, a.bdf)
    tris, elem_nodes = surface(geom)
    used = sorted({n for t in tris for n in t})
    index = {n: i for i, n in enumerate(used)}
    points = np.array([geom.nodes[n].get_position() for n in used], dtype=float)

    disp = model.displacements.get(a.subcase)
    dmap = {}
    if disp is not None:
        d = disp.data[-1, :, :3]
        dmap = {int(n): d[i] for i, n in enumerate(disp.node_gridtype[:, 0])}
    if a.field == "disp":
        if not dmap:
            raise SystemExit("OP2에 변위 결과가 없습니다 (DISPLACEMENT 출력 요청 확인)")
        values = np.array([np.linalg.norm(dmap.get(n, np.zeros(3))) for n in used])
    else:
        vm = nodal_vm(model, a.subcase, elem_nodes)
        if not vm:
            raise SystemExit("OP2에 응력 결과가 없습니다 (STRESS 출력 요청 확인)")
        values = np.array([vm.get(n, 0.0) for n in used])
    if a.deformed == "1" and dmap:
        points += a.scale * np.array([dmap.get(n, np.zeros(3)) for n in used])

    triangles = np.array([[index[n] for n in t] for t in tris], dtype=np.int32)
    name = a.name or ("stress" if a.field == "vm" else "disp")
    Path("fields").mkdir(exist_ok=True)
    np.savez_compressed(Path("fields") / f"{name}.npz", points=points, values=values, triangles=triangles)
    print(f"saved fields/{name}.npz: nodes={len(points)} triangles={len(triangles)} max={values.max():.6g}")


if __name__ == "__main__":
    main()
