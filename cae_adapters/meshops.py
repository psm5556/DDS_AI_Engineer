"""메쉬 계산: 요소 형식별 면, 외곽 표면, 노드 법선, 면 압력 → 절점 하중. numpy만 쓴다(PC Agent·서버 공통)."""
from __future__ import annotations

import re

import numpy as np

# 면 번호(Abaqus S1, S2, …)의 꼭짓점. 2차 요소도 꼭짓점만 쓴다
FACES = {
    "tet": [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)],
    "hex": [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1), (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)],
    "wedge": [(0, 1, 2), (3, 5, 4), (0, 3, 4, 1), (1, 4, 5, 2), (2, 5, 3, 0)],
    "tri": [(0, 1, 2)],
    "quad": [(0, 1, 2, 3)],
}
_CORNERS = {"tet": 4, "hex": 8, "wedge": 6}
_FAMILY = [(r"^(C3D4|C3D10|CTETRA)", "tet"), (r"^(C3D8|C3D20|CHEXA|SOLID)", "hex"), (r"^(C3D6|C3D15|CPENTA)", "wedge"),
           (r"^(S3|STRI|M3D3|M3D6|CTRIA)", "tri"), (r"^(S4|S8|M3D4|M3D8|CQUAD|SHELL)", "quad")]


def element_family(etype: str) -> str | None:
    """요소 형식 이름 → 모양. 모르는 형식이면 None(추측하지 않는다)."""
    name = str(etype).upper()
    for pattern, family in _FAMILY:
        if re.match(pattern, name):
            return family
    return None


def _triangulate(faces: np.ndarray) -> np.ndarray:
    return faces if faces.shape[1] == 3 else np.vstack([faces[:, [0, 1, 2]], faces[:, [0, 2, 3]]])


def boundary_triangles(elements: dict) -> np.ndarray:
    """요소 연결(형식 → (M,k) 노드 인덱스) → 외곽 표면 삼각형. 쉘은 요소 자체, 솔리드는 한 요소에만 속한 면."""
    by_size: dict[int, list] = {3: [], 4: []}
    shells: list = []
    for etype, conn in elements.items():
        family, conn = element_family(etype), np.asarray(conn)
        if family is None or not conn.size:
            continue
        for face in FACES[family]:
            rows = conn[:, list(face)]
            (shells if family in ("tri", "quad") else by_size[len(face)]).append(rows)
    tris = [_triangulate(r) for r in shells]
    for rows in by_size.values():
        if not rows:
            continue
        faces = np.vstack(rows)
        _, first, counts = np.unique(np.sort(faces, axis=1), axis=0, return_index=True, return_counts=True)
        tris.append(_triangulate(faces[first[counts == 1]]))
    return np.vstack(tris).astype(np.int32) if tris else np.zeros((0, 3), np.int32)


def vertex_normals(nodes: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """면적 가중 노드 법선(단위 벡터). 표면에 속하지 않은 노드는 0."""
    out = np.zeros_like(nodes, dtype=np.float64)
    if len(triangles):
        a, b, c = (nodes[triangles[:, i]] for i in range(3))
        n = np.cross(b - a, c - a)
        for i in range(3):
            np.add.at(out, triangles[:, i], n)
    length = np.linalg.norm(out, axis=1, keepdims=True)
    return np.divide(out, length, out=np.zeros_like(out), where=length > 0)


def _vector_area(p: np.ndarray) -> np.ndarray:
    """면 꼭짓점 (k,3) → 넓이 벡터."""
    return 0.5 * np.cross(p[2] - p[0], p[-1] - p[1]) if len(p) == 4 else 0.5 * np.cross(p[1] - p[0], p[2] - p[0])


def pressure_to_nodal(nodes: np.ndarray, etype: str, conn: np.ndarray, rows: np.ndarray) -> np.ndarray:
    """면 압력 rows (P,3: 요소 행, 면 번호, 값) → 절점 하중 (N,3). 합력을 꼭짓점에 똑같이 나눈다.
    양의 압력은 면을 미는 방향이다: 솔리드는 요소 안쪽, 쉘(면 번호 0)은 법선 반대쪽."""
    family = element_family(etype)
    if family is None:
        raise ValueError(f"요소 형식 {etype}의 면을 모릅니다")
    out = np.zeros((len(nodes), 3), dtype=np.float64)
    for row, face_no, value in np.asarray(rows, dtype=np.float64):
        element = conn[int(row)]
        face = FACES[family][int(face_no) - 1 if family not in ("tri", "quad") else 0]
        ids = element[list(face)]
        area = _vector_area(nodes[ids])
        if family in ("tri", "quad"):
            force = -value * area
        else:  # 면 번호의 꼭짓점 순서에 기대지 않고, 요소 중심 쪽을 안쪽으로 본다
            inward = nodes[element[:_CORNERS[family]]].mean(axis=0) - nodes[ids].mean(axis=0)
            force = value * (area if np.dot(area, inward) >= 0 else -area)
        np.add.at(out, ids, force / len(ids))
    return out
