"""LS-DYNA d3plot → fields/<이름>.npz (3D 형상·필드 학습용) — lasso-python 사용

실행(플랫폼 python, 케이스 폴더에서):
  python dyna_fields.py [d3plot] [--field disp|vm|eps] [--state -1] [--name 이름] [--deformed 1]
  --field disp : 초기 대비 변위 크기(노드)
          vm   : 쉘·솔리드 응력의 von Mises를 노드로 평균 (쉘은 적분점 평균)
          eps  : 유효 소성 변형률(노드 평균)
  --deformed 1 : points에 해당 시점의 변형 좌표를 저장(충돌 변형 형상까지 학습)
출력: fields/<name>.npz  points(N,3) · values(N,) · triangles(M,3)  — 외곽 표면 노드만
TODO(사내검증): d3plot에 응력/소성변형률이 저장되도록 *DATABASE_EXTENT_BINARY 설정 확인, lasso ArrayType 이름(버전별)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def vm6(s: np.ndarray) -> np.ndarray:
    """(…, 6) [xx, yy, zz, xy, yz, zx] → von Mises"""
    xx, yy, zz, xy, yz, zx = (s[..., i] for i in range(6))
    return np.sqrt(0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2) + 3 * (xy ** 2 + yz ** 2 + zx ** 2))


HEX_FACES = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def main():
    from lasso.dyna import ArrayType, D3plot

    ap = argparse.ArgumentParser()
    ap.add_argument("d3plot", nargs="?", default="d3plot")
    ap.add_argument("--field", default="vm", choices=["disp", "vm", "eps"])
    ap.add_argument("--state", type=int, default=-1)
    ap.add_argument("--name")
    ap.add_argument("--deformed", default="1")
    a = ap.parse_args()
    d = D3plot(a.d3plot)
    A = d.arrays
    x0 = A[ArrayType.node_coordinates]
    xt = A[ArrayType.node_displacement][a.state] if ArrayType.node_displacement in A else x0  # lasso: 현재 좌표
    n_nodes = len(x0)

    faces, contrib = {}, []  # contrib: (노드 인덱스 배열, 요소 값)
    shells = A.get(ArrayType.element_shell_node_indexes)
    if shells is not None:
        for q in shells:
            key = tuple(sorted(set(q)))
            faces[key] = (1, tuple(dict.fromkeys(q)))  # 쉘은 그 자체가 표면 (삼각형은 노드가 중복됨)
    solids = A.get(ArrayType.element_solid_node_indexes)
    if solids is not None:
        cnt = {}
        for h in solids:
            for f in HEX_FACES:
                face = tuple(h[j] for j in f)
                key = tuple(sorted(set(face)))
                c, _ = cnt.get(key, (0, face))
                cnt[key] = (c + 1, face)
        faces.update({k: v for k, v in cnt.items() if v[0] == 1})

    vals = np.zeros(n_nodes)
    if a.field == "disp":
        vals = np.linalg.norm(xt - x0, axis=1)
    else:
        acc, num = np.zeros(n_nodes), np.zeros(n_nodes)
        if a.field == "vm":
            pairs = [(ArrayType.element_shell_stress, shells), (ArrayType.element_solid_stress, solids)]
        else:
            pairs = [(ArrayType.element_shell_effective_plastic_strain, shells), (ArrayType.element_solid_effective_plastic_strain, solids)]
        for arr, conn in pairs:
            if conn is None or arr not in A:
                continue
            v = A[arr][a.state]
            v = vm6(v) if a.field == "vm" else v
            while v.ndim > 1:  # 적분점·층 평균
                v = v.mean(axis=-1)
            for nodes, val in zip(conn, v):
                acc[nodes] += val
                num[nodes] += 1
        if not num.any():
            raise SystemExit("d3plot에 요청한 결과가 없습니다 (*DATABASE_EXTENT_BINARY 확인)")
        vals = acc / np.maximum(num, 1)

    tris = []
    for _, f in faces.values():
        f = list(f)
        if len(f) >= 3:
            tris.append((f[0], f[1], f[2]))
        if len(f) == 4:
            tris.append((f[0], f[2], f[3]))
    used = sorted({n for t in tris for n in t})
    index = {n: i for i, n in enumerate(used)}
    pts = (xt if a.deformed == "1" else x0)[used]
    triangles = np.array([[index[n] for n in t] for t in tris], dtype=np.int32)
    name = a.name or {"vm": "stress", "disp": "disp", "eps": "eps"}[a.field]
    Path("fields").mkdir(exist_ok=True)
    np.savez_compressed(Path("fields") / f"{name}.npz", points=pts, values=vals[used], triangles=triangles)
    print(f"saved fields/{name}.npz: nodes={len(pts)} triangles={len(triangles)} max={vals[used].max():.6g}")


if __name__ == "__main__":
    main()
