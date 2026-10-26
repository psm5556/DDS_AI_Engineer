# -*- coding: utf-8 -*-
"""
STL -> fields/<name>.npz 변환기 (DDS 규약 1-1, 3D 형상 학습/가시화용)

CAD 툴(CATIA/SolidWorks/Creo)이 내보낸 STL(ASCII 또는 binary)을 읽어
  points    (N,3) float  : 중복 제거한 정점 좌표
  triangles (M,3) int    : 삼각형 정점 인덱스
  values    (N,)  float  : 노드 값 (zeros | dist_centroid | x | y | z)
를 numpy savez_compressed 로 저장한다. CAD 툴 없이 순수 Python(+numpy)으로 동작한다.

옵션으로 STL에서 계산한 형상량(stl_volume, stl_area, stl_cx.., bbox)을 results.json 에 병합한다.
(닫힌 솔리드 메쉬일 때만 부피가 의미 있다. CAD 측정값이 정답이고 이것은 교차검증용이다.)

사용:
  python stl_to_fields.py export/part.stl
  python stl_to_fields.py export/part.stl --out fields/shape.npz --values dist_centroid --scale 1.0 --merge-results

종료코드: 성공 0, 실패 1. 콘솔 출력은 ASCII.
"""
import argparse
import json
import math
import os
import struct
import sys


def log(msg):
    print("[DDS] " + str(msg))
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# STL 읽기
# ---------------------------------------------------------------------------
def _is_binary_stl(data):
    """binary 판정: 헤더 80B + 개수 4B + 50B*n 길이가 정확히 맞으면 binary.
    ('solid' 로 시작하는 binary 파일도 있으므로 길이로 판정한다.)"""
    if len(data) < 84:
        return False
    n = struct.unpack("<I", data[80:84])[0]
    return len(data) == 84 + 50 * n


def read_stl(path):
    """삼각형 리스트 [(v1, v2, v3), ...] 반환. 각 v 는 (x, y, z) float 튜플."""
    with open(path, "rb") as f:
        data = f.read()
    tris = []
    if _is_binary_stl(data):
        n = struct.unpack("<I", data[80:84])[0]
        off = 84
        for _ in range(n):
            vals = struct.unpack("<12f", data[off:off + 48])
            tris.append((vals[3:6], vals[6:9], vals[9:12]))
            off += 50
        return tris
    text = data.decode("ascii", errors="replace")
    verts = []
    for line in text.splitlines():
        tok = line.strip().split()
        if len(tok) == 4 and tok[0].lower() == "vertex":
            verts.append((float(tok[1]), float(tok[2]), float(tok[3])))
    if len(verts) % 3 != 0:
        raise ValueError("ASCII STL vertex count is not a multiple of 3: %d" % len(verts))
    for i in range(0, len(verts), 3):
        tris.append((verts[i], verts[i + 1], verts[i + 2]))
    return tris


def index_mesh(tris, scale=1.0, tol=1e-9):
    """triangle soup -> (points, triangles). tol 격자로 반올림해 중복 정점을 합친다."""
    key_to_idx = {}
    points = []
    faces = []
    inv = 1.0 / tol if tol > 0 else None
    for tri in tris:
        face = []
        for v in tri:
            p = (v[0] * scale, v[1] * scale, v[2] * scale)
            key = (round(p[0] * inv), round(p[1] * inv), round(p[2] * inv)) if inv else p
            idx = key_to_idx.get(key)
            if idx is None:
                idx = len(points)
                key_to_idx[key] = idx
                points.append(p)
            face.append(idx)
        if face[0] != face[1] and face[1] != face[2] and face[0] != face[2]:
            faces.append(tuple(face))     # 퇴화 삼각형 제거
    return points, faces


# ---------------------------------------------------------------------------
# 형상량 (발산정리 기반 부피, 면적, 부피 중심, 경계상자)
# ---------------------------------------------------------------------------
def geometry_metrics(points, faces):
    vol6 = 0.0
    area2 = 0.0
    cx = cy = cz = 0.0
    for a, b, c in faces:
        p0, p1, p2 = points[a], points[b], points[c]
        # 부호 있는 사면체 부피 * 6
        d = (p0[0] * (p1[1] * p2[2] - p1[2] * p2[1])
             - p0[1] * (p1[0] * p2[2] - p1[2] * p2[0])
             + p0[2] * (p1[0] * p2[1] - p1[1] * p2[0]))
        vol6 += d
        cx += d * (p0[0] + p1[0] + p2[0])
        cy += d * (p0[1] + p1[1] + p2[1])
        cz += d * (p0[2] + p1[2] + p2[2])
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        area2 += math.sqrt(nx * nx + ny * ny + nz * nz)
    volume = vol6 / 6.0
    out = {"stl_volume": abs(volume), "stl_area": area2 / 2.0,
           "stl_n_points": float(len(points)), "stl_n_triangles": float(len(faces))}
    if abs(vol6) > 0:
        out["stl_cx"] = cx / (4.0 * vol6)
        out["stl_cy"] = cy / (4.0 * vol6)
        out["stl_cz"] = cz / (4.0 * vol6)
    if points:
        for i, ax in enumerate("xyz"):
            vals = [p[i] for p in points]
            out["stl_bbox_" + ax] = max(vals) - min(vals)
    return out


def node_values(points, mode):
    if mode == "zeros":
        return [0.0] * len(points)
    if mode in ("x", "y", "z"):
        i = "xyz".index(mode)
        return [p[i] for p in points]
    if mode == "dist_centroid":
        n = float(len(points)) or 1.0
        c = [sum(p[i] for p in points) / n for i in range(3)]
        return [math.sqrt(sum((p[i] - c[i]) ** 2 for i in range(3))) for p in points]
    raise ValueError("unknown --values mode: " + mode)


def convert(stl_path, out_path, values_mode="zeros", scale=1.0, tol=1e-9):
    import numpy as np
    tris = read_stl(stl_path)
    if not tris:
        raise RuntimeError("no triangles in STL: " + stl_path)
    points, faces = index_mesh(tris, scale=scale, tol=tol)
    vals = node_values(points, values_mode)
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    np.savez_compressed(out_path,
                        points=np.asarray(points, dtype=np.float64).reshape(-1, 3),
                        triangles=np.asarray(faces, dtype=np.int64).reshape(-1, 3),
                        values=np.asarray(vals, dtype=np.float64))
    return points, faces


def main(argv=None):
    ap = argparse.ArgumentParser(description="STL -> fields npz (points/triangles/values)")
    ap.add_argument("stl")
    ap.add_argument("--out", default=None, help="default: fields/<stl name>.npz")
    ap.add_argument("--values", default="zeros",
                    choices=["zeros", "dist_centroid", "x", "y", "z"])
    ap.add_argument("--scale", type=float, default=1.0,
                    help="coordinate scale (e.g. 1000 if STL is in meters and you want mm)")
    ap.add_argument("--tol", type=float, default=1e-9, help="vertex merge tolerance")
    ap.add_argument("--merge-results", action="store_true",
                    help="merge stl_* geometry metrics into ./results.json (only if it exists)")
    a = ap.parse_args(argv)

    if not os.path.isfile(a.stl):
        raise RuntimeError("STL not found: " + a.stl)
    out = a.out or os.path.join("fields", os.path.splitext(os.path.basename(a.stl))[0] + ".npz")
    points, faces = convert(a.stl, out, a.values, a.scale, a.tol)
    metrics = geometry_metrics(points, faces)
    log("npz written: %s (points=%d, triangles=%d)" % (out, len(points), len(faces)))
    log("metrics: " + json.dumps(metrics))
    if a.merge_results:
        # 실패 케이스(results.json 없음)를 성공으로 바꾸지 않도록, 있을 때만 병합한다.
        if os.path.isfile("results.json"):
            with open("results.json", "r") as f:
                res = json.load(f)
            res.update(metrics)
            with open("results.json", "w") as f:
                json.dump(res, f, indent=2)
            log("merged into results.json")
        else:
            log("results.json not found - metrics not merged")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("FAILED: %s" % e)
        sys.exit(1)
