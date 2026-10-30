"""로그인 화면의 해석 결과 생성기 공통: 메쉬에서 표면 뽑기, 특징선, 결과 파일 쓰기.

이 폴더의 스크립트는 개발자가 한 번 돌려 frontend/public/hero/ 에 결과를 남기는 도구다. 웹앱·PC Agent 실행에는 쓰이지 않는다.
필요한 것(저장소 환경과 분리): python -m venv .hero-env; .hero-env\\Scripts\\pip install gmsh numpy scipy
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "hero"


def tet_faces(tets: np.ndarray) -> np.ndarray:
    """바깥을 향하는 면 순서로 네 면을 돌려준다."""
    return np.vstack([tets[:, [0, 2, 1]], tets[:, [0, 1, 3]], tets[:, [1, 2, 3]], tets[:, [0, 3, 2]]])


def surface_of(tets: np.ndarray) -> np.ndarray:
    """한 요소에만 속한 면 = 바깥 표면 삼각형."""
    faces = tet_faces(tets)
    _, first, count = np.unique(np.sort(faces, axis=1), axis=0, return_index=True, return_counts=True)
    return faces[first[count == 1]]


def reindex(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """표면 절점만 남긴다. 돌려주는 값: (원래 절점 번호, 새 번호의 삼각형)."""
    keep, inv = np.unique(tris, return_inverse=True)
    return keep, inv.reshape(tris.shape)


def feature_edges(pts: np.ndarray, tris: np.ndarray, angle_deg: float = 32.0) -> np.ndarray:
    """꺾인 모서리(CAD 윤곽선): 맞닿은 두 면의 법선이 angle_deg 넘게 다른 선 + 한 면에만 속한 선."""
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    n = np.cross(b - a, c - a)
    n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)
    edges = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    owner = np.tile(np.arange(len(tris)), 3)
    key = np.sort(edges, axis=1)
    order = np.lexsort((key[:, 1], key[:, 0]))
    key, owner = key[order], owner[order]
    same = np.all(key[1:] == key[:-1], axis=1)
    cos_limit = np.cos(np.radians(angle_deg))
    out = []
    i = 0
    while i < len(key):
        if i + 1 < len(key) and same[i]:
            if np.dot(n[owner[i]], n[owner[i + 1]]) < cos_limit:
                out.append(key[i])
            i += 2
        else:
            out.append(key[i])
            i += 1
    return np.array(out, dtype=np.int64).reshape(-1, 2)


def mesh_edges(tris: np.ndarray) -> np.ndarray:
    edges = np.sort(np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    return np.unique(edges, axis=0)


def quantize_positions(frames: np.ndarray) -> tuple[np.ndarray, list[float], list[float]]:
    """(F,N,3) 실수 좌표 → int16. 파일을 줄이면서 0.01mm 안쪽의 정밀도를 지킨다."""
    lo, hi = frames.min(axis=(0, 1)), frames.max(axis=(0, 1))
    span = np.maximum(hi - lo, 1e-9)
    q = np.round((frames - lo) / span * 65535 - 32768).astype(np.int16)
    return q, lo.tolist(), span.tolist()


def quantize_field(frames: np.ndarray, vmin: float, vmax: float) -> np.ndarray:
    """(F,N) 값 → uint8(0~255). 범례 범위를 넘는 값은 끝 색으로 둔다."""
    return np.round(np.clip((frames - vmin) / (vmax - vmin), 0, 1) * 255).astype(np.uint8)


def save(name: str, meta: dict, arrays: dict[str, np.ndarray]) -> Path:
    """<name>.json(설명·배열 위치) + <name>.bin(배열). 화면은 필요할 때만 받는다."""
    OUT.mkdir(parents=True, exist_ok=True)
    table = {}
    offset = 0
    with open(OUT / f"{name}.bin", "wb") as f:
        for key, arr in arrays.items():
            arr = np.asarray(arr)
            pad = -offset % 4  # Int16·Uint32·Float32 배열로 바로 읽을 수 있게 맞춘다
            f.write(b"\0" * pad)
            offset += pad
            table[key] = {"offset": offset, "type": arr.dtype.name, "shape": list(arr.shape)}
            data = arr.tobytes()
            f.write(data)
            offset += len(data)
    (OUT / f"{name}.json").write_text(json.dumps({**meta, "arrays": table}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}: {offset / 1e6:.2f} MB")
    return OUT / f"{name}.bin"
