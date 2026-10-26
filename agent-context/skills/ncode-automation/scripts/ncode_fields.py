# -*- coding: utf-8 -*-
"""
nCode 손상 표(CSV) + Nastran 메쉬(BDF) -> fields/<이름>.npz  (tool 키: python, 플랫폼 venv / Python 3 + numpy + pyNastran)

  python ncode_fields.py --bdf model.bdf --csv "results/damage.csv" [--location node|element]
                         [--value damage|log10_damage|life] [--name damage] [--id-col NodeID]

- 메쉬: pyNastran 으로 GRID 와 요소를 읽고 외곽 표면 삼각형을 만든다(쉘 CTRIA3/6, CQUAD4/8 + 솔리드 CTETRA/CPENTA/CHEXA 외곽면).
- 값: --location node 이면 표의 ID 를 노드 ID 로, element 이면 요소 ID 로 보고 요소 값을 노드로 평균한다.
  표에 없는 노드는 손상 0(수명 LIFE_CAP)으로 둔다.
- --value log10_damage 는 손상 0 을 FLOOR(1e-12) 로 바꿔 로그를 취한다(학습용으로 범위를 줄일 때 권장).
- FE 입력이 Abaqus/Ansys 결과라면 그 툴 스킬의 필드 스크립트로 좌표, 표면을 만들고 같은 ID 매핑을 하면 된다.
출력: fields/<name>.npz  points(N,3) · values(N,) · triangles(M,3)
"""
import argparse
import glob
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SOLID_FACES = {
    "CTETRA": (4, [(0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3)]),
    "CPENTA": (6, [(0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (0, 2, 5, 3)]),
    "CHEXA": (8, [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]),
}
SHELLS = {"CTRIA3": 3, "CTRIA6": 3, "CQUAD4": 4, "CQUAD8": 4}
FLOOR = 1e-12


def surface(model):
    count, shells, elem_nodes = {}, [], {}
    for eid, el in model.elements.items():
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


def node_positions(model, nids):
    if all((getattr(model.nodes[n], "cp", 0) or 0) == 0 for n in nids):
        return np.array([model.nodes[n].xyz for n in nids], dtype=float)
    model.cross_reference()       # 국부 좌표계 노드가 있으면 기본 좌표로 변환
    return np.array([model.nodes[n].get_position() for n in nids], dtype=float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bdf", required=True)
    ap.add_argument("--csv", action="append", required=True)
    ap.add_argument("--location", choices=["node", "element"], default="node")
    ap.add_argument("--value", choices=["damage", "log10_damage", "life"], default="damage")
    ap.add_argument("--name", default="damage")
    ap.add_argument("--id-col")
    ap.add_argument("--damage-col")
    ap.add_argument("--life-col")
    a = ap.parse_args()

    from ncode_extract import read_damage_table, LIFE_CAP
    from pyNastran.bdf.bdf import read_bdf

    rows = []
    for p in a.csv:
        for f in sorted(glob.glob(p, recursive=True)):
            rows.extend(read_damage_table(f, a.id_col, a.damage_col, a.life_col))
    if not rows:
        raise RuntimeError("no damage rows")
    dmg = {}
    for eid, d, _ in rows:           # 여러 표(하중 그룹)에 같은 ID 가 있으면 최대 손상
        if eid is None:
            continue
        k = int(eid)
        dmg[k] = max(d, dmg.get(k, 0.0))

    model = read_bdf(a.bdf, xref=False, debug=None)
    tris, elem_nodes = surface(model)
    if not tris:
        raise RuntimeError("no surface faces (supported: CTRIA3/6, CQUAD4/8, CTETRA, CPENTA, CHEXA)")
    used = sorted({n for t in tris for n in t})
    index = dict((n, i) for i, n in enumerate(used))

    if a.location == "node":
        nd = dmg
    else:
        acc, cnt = defaultdict(float), defaultdict(int)
        for eid, d in dmg.items():
            for n in elem_nodes.get(eid, []):
                acc[n] += d
                cnt[n] += 1
        nd = dict((n, acc[n] / cnt[n]) for n in acc)
    hit = sum(1 for n in used if n in nd)
    if hit == 0:
        raise RuntimeError("no table IDs match surface %ss (check --location / --id-col)" % a.location)
    print("[ncode_fields] matched %d / %d surface nodes" % (hit, len(used)))

    d = np.array([nd.get(n, 0.0) for n in used], dtype=float)
    if a.value == "damage":
        values = d
    elif a.value == "log10_damage":
        values = np.log10(np.maximum(d, FLOOR))
    else:
        values = np.where(d > 1.0 / LIFE_CAP, 1.0 / np.maximum(d, 1.0 / LIFE_CAP), LIFE_CAP)

    points = node_positions(model, used)
    triangles = np.array([[index[n] for n in t] for t in tris], dtype=np.int32)
    if not os.path.isdir("fields"):
        os.makedirs("fields")
    out = os.path.join("fields", a.name + ".npz")
    np.savez_compressed(out, points=points, values=values, triangles=triangles)
    print("[ncode_fields] saved %s: nodes=%d triangles=%d max=%.6g" % (out, len(points), len(triangles),
                                                                      float(values.max())))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("[ncode_fields] FAILED: " + str(e))
        sys.exit(1)
