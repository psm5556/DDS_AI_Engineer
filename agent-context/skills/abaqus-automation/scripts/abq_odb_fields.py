# -*- coding: utf-8 -*-
"""Abaqus ODB → fields/<이름>.npz (3D 형상·필드 학습용, docs/skill-contract.md 1-1절)

실행(케이스 폴더에서):  abaqus python abq_odb_fields.py <job.odb> [--field S] [--invariant mises] [--name stress]
                                       [--step 이름] [--frame -1] [--deformed 1] [--scale 1.0]
  --field      S(응력) | U(변위) | LE(변형률) | PEEQ 등 fieldOutputs 키
  --invariant  mises | magnitude | maxPrincipal | tresca | (빈칸: 스칼라 필드 그대로) | 성분 인덱스(0,1,2…)
  --deformed   1이면 변형 후 좌표(원좌표 + U*scale)를 points로 저장 → 형상 변화까지 학습
출력: fields/<name>.npz  points(N,3) · values(N,) · triangles(M,3)  — 외곽 표면 노드만
Abaqus 내장 파이썬(2.7 / 3) 겸용. numpy는 Abaqus에 포함돼 있다.
TODO(사내검증): 다중 인스턴스 모델(어셈블리)은 인스턴스별 노드 라벨이 겹친다 — 여기서는 (인스턴스, 라벨)로 구분한다.
"""
import os
import sys

import numpy as np
from odbAccess import openOdb
from abaqusConstants import ELEMENT_NODAL, NODAL

# 요소 종류별 면(모서리 노드 인덱스) — 2차 요소도 모서리 노드만 쓴다
FACES = {
    4: [(0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3)],                                   # C3D4, C3D10
    6: [(0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (0, 2, 5, 3)],               # C3D6, C3D15
    8: [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],  # C3D8, C3D20
}
CORNERS = {"C3D4": 4, "C3D10": 4, "C3D6": 6, "C3D15": 6, "C3D8": 8, "C3D8R": 8, "C3D8I": 8, "C3D20": 8, "C3D20R": 8}
SHELL_CORNERS = {"S3": 3, "S3R": 3, "STRI3": 3, "S4": 4, "S4R": 4, "S8R": 4, "M3D3": 3, "M3D4": 4, "M3D4R": 4, "CPS3": 3, "CPS4": 4}


def args():
    a = {"field": "S", "invariant": "mises", "name": "", "step": "", "frame": "-1", "deformed": "0", "scale": "1.0"}
    argv = sys.argv[1:]
    odb = argv[0]
    i = 1
    while i < len(argv):
        if argv[i].startswith("--") and i + 1 < len(argv):
            a[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            i += 1
    a["name"] = a["name"] or ("stress" if a["field"] == "S" else a["field"].lower())
    return odb, a


def surface(odb):
    """외곽 표면 삼각형: 고체 요소 면 중 한 번만 나오는 면 + 쉘 요소 자체."""
    count, shells = {}, []
    for inst_name, inst in odb.rootAssembly.instances.items():
        for el in inst.elements:
            t = el.type.upper()
            conn = [(inst_name, n) for n in el.connectivity]
            if t in CORNERS:
                corners = conn[:CORNERS[t]]
                for f in FACES[CORNERS[t]]:
                    face = tuple(corners[j] for j in f)
                    key = tuple(sorted(face))
                    count[key] = (count.get(key, (0, face))[0] + 1, face)
            elif t in SHELL_CORNERS:
                shells.append(tuple(conn[:SHELL_CORNERS[t]]))
    faces = [face for (c, face) in count.values() if c == 1] + shells
    tris = []
    for f in faces:
        tris.append((f[0], f[1], f[2]))
        if len(f) == 4:
            tris.append((f[0], f[2], f[3]))
    return tris


def scalar(v, inv):
    if not inv:
        return float(v.data) if np.ndim(v.data) == 0 else float(np.linalg.norm(v.data))
    if inv.isdigit():
        return float(v.data[int(inv)])
    return float(getattr(v, inv))


def main():
    odb_path, a = args()
    odb = openOdb(odb_path, readOnly=True)
    step = odb.steps[a["step"]] if a["step"] else odb.steps[list(odb.steps.keys())[-1]]
    frame = step.frames[int(a["frame"])]
    tris = surface(odb)
    used = sorted(set(n for t in tris for n in t))
    index = dict((k, i) for i, k in enumerate(used))

    coords = {}
    for inst_name, inst in odb.rootAssembly.instances.items():
        for nd in inst.nodes:
            key = (inst_name, nd.label)
            if key in index:
                coords[key] = np.array(nd.coordinates[:3], dtype=float)

    # 노드 값: 노드 위치 출력이 있으면 그대로, 없으면 ELEMENT_NODAL을 노드 평균
    fo = frame.fieldOutputs[a["field"]]
    acc, cnt = np.zeros(len(used)), np.zeros(len(used))
    try:
        sub = fo.getSubset(position=NODAL) if fo.locations[0].position == NODAL else fo.getSubset(position=ELEMENT_NODAL)
    except Exception:  # noqa: BLE001
        sub = fo.getSubset(position=ELEMENT_NODAL)
    for v in sub.values:
        key = (v.instance.name if v.instance else "", v.nodeLabel)
        if key in index:
            i = index[key]
            acc[i] += scalar(v, a["invariant"])
            cnt[i] += 1
    values = acc / np.maximum(cnt, 1)

    points = np.array([coords[k] for k in used])
    if a["deformed"] == "1" and "U" in frame.fieldOutputs.keys():
        for v in frame.fieldOutputs["U"].values:
            key = (v.instance.name if v.instance else "", v.nodeLabel)
            if key in index:
                points[index[key]] += float(a["scale"]) * np.array(v.data[:3], dtype=float)

    triangles = np.array([[index[n] for n in t] for t in tris], dtype=np.int32)
    if not os.path.isdir("fields"):
        os.makedirs("fields")
    out = os.path.join("fields", a["name"] + ".npz")
    np.savez_compressed(out, points=points, values=values, triangles=triangles)
    print("saved %s: nodes=%d triangles=%d max=%.6g" % (out, len(points), len(triangles), values.max()))
    odb.close()


if __name__ == "__main__":
    main()
