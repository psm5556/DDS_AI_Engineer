"""구조해석: 드럼세탁기 스파이더(뒷면 알루미늄 주조품). CAD → 2차 사면체 메쉬(C3D10) → 선형 정적 FEM.

치수를 바꾼 설계 여러 개를 실제로 풀어 저장한다(같은 메쉬를 모핑하므로 절점 수가 같고, 화면은 설계 사이를 잇는다).
    python scripts/hero/structural.py [--check] [--size 5]
--check: 외팔보를 풀어 보 이론과 비교한다.
형상·하중은 일반적인 구조의 예시이며 실제 제품 데이터가 아니다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from scipy.sparse.linalg import cg

sys.path.insert(0, str(Path(__file__).parent))
from common import feature_edges, quantize_field, quantize_positions, save  # noqa: E402

E, NU, RHO = 71000.0, 0.33, 2.7e-9  # ADC12 근사: MPa, -, t/mm³
RH, RB, HH = 42.0, 19.0, 44.0  # 허브 반지름, 축 구멍, 허브 높이
RA, RBOSS, HBOSS, RBOLT = 172.0, 15.0, 26.0, 5.5  # 볼트 위치 반지름, 보스, 볼트 구멍
T0 = 6.0  # 판 두께
W0, W1 = 40.0, 15.0  # 팔 반폭: 허브 쪽, 보스 쪽

# C3D10: gmsh 절점 순서는 꼭짓점 0~3, 그다음 모서리 0-1, 1-2, 2-0, 3-0, 3-2, 3-1
TET10_EDGES = [(0, 1), (1, 2), (2, 0), (3, 0), (3, 2), (3, 1)]
A_, B_ = 0.13819660112501051, 0.58541019662496845
GAUSS = np.array([[A_, A_, A_], [B_, A_, A_], [A_, B_, A_], [A_, A_, B_]])  # ξ, η, ζ. 가중치는 각 1/24


def dN_ref(xi: float, eta: float, zeta: float) -> np.ndarray:
    """(10,3) 기준 좌표에 대한 형상함수 기울기."""
    L = np.array([1 - xi - eta - zeta, xi, eta, zeta])
    dL = np.array([[-1, -1, -1], [1, 0, 0], [0, 1, 0], [0, 0, 1]], dtype=float)
    out = np.zeros((10, 3))
    for a in range(4):
        out[a] = (4 * L[a] - 1) * dL[a]
    for m, (i, j) in enumerate(TET10_EDGES):
        out[4 + m] = 4 * (L[i] * dL[j] + L[j] * dL[i])
    return out


DN = np.stack([dN_ref(*g) for g in GAUSS])  # (4,10,3)
# 적분점 값 → 꼭짓점 값(1차 외삽): 적분점의 부피 좌표로 만든 행렬의 역 (꼭짓점, 적분점)
EXTRAP = np.linalg.inv(np.column_stack([1 - GAUSS.sum(axis=1), GAUSS]))


def build_cad(size: float):
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("spider")
    occ = gmsh.model.occ

    def prism(points, z0, z1):
        """XY 다각형을 z 방향으로 민 기둥."""
        ids = [occ.addPoint(x, y, z0) for x, y in points]
        lines = [occ.addLine(ids[i], ids[(i + 1) % len(ids)]) for i in range(len(ids))]
        surface = occ.addPlaneSurface([occ.addCurveLoop(lines)])
        out = occ.extrude([(2, surface)], 0, 0, z1 - z0)
        return next(tag for dim, tag in out if dim == 3)

    def rib_xz(points, half):
        """XZ 윤곽을 y 방향으로 민 리브."""
        ids = [occ.addPoint(x, -half, z) for x, z in points]
        lines = [occ.addLine(ids[i], ids[(i + 1) % len(ids)]) for i in range(len(ids))]
        surface = occ.addPlaneSurface([occ.addCurveLoop(lines)])
        out = occ.extrude([(2, surface)], 0, 2 * half, 0)
        return next(tag for dim, tag in out if dim == 3)

    solids = [occ.addCylinder(0, 0, 0, 0, 0, HH, RH), occ.addCylinder(0, 0, 0, 0, 0, T0 + 3, 74.0)]
    for k in range(3):
        ang = 2 * np.pi * k / 3
        parts = [prism([(RH * 0.6, -W0), (RA, -W1), (RA, W1), (RH * 0.6, W0)], 0, T0)]
        # 팔 가운데 리브와 양쪽 테두리 리브(ㅠ 단면). 보스 쪽으로 낮아진다
        parts.append(rib_xz([(RH * 0.7, 0), (RA - 4, 0), (RA - 4, HBOSS * 0.7), (RH * 0.9, HH * 0.82), (RH * 0.7, HH * 0.82)], 3.2))
        for sign in (-1, 1):
            a, b = (RH * 0.8, sign * (W0 - 1.5)), (RA - 2, sign * (W1 - 1.5))
            c, d = (RA - 2, sign * (W1 + 1.5)), (RH * 0.8, sign * (W0 + 1.5))
            parts.append(prism([a, b, c, d] if sign < 0 else [d, c, b, a], 0, T0 + 8))
        parts.append(occ.addCylinder(RA, 0, 0, 0, 0, HBOSS, RBOSS))
        for p in parts:
            occ.rotate([(3, p)], 0, 0, 0, 0, 0, 1, ang)
        solids += parts
    fused, _ = occ.fuse([(3, solids[0])], [(3, s) for s in solids[1:]])
    tools = [(3, occ.addCylinder(0, 0, -1, 0, 0, HH + 2, RB))]
    for k in range(3):
        ang = 2 * np.pi * k / 3
        tools.append((3, occ.addCylinder(RA * np.cos(ang), RA * np.sin(ang), -1, 0, 0, HBOSS + 2, RBOLT)))
    body, _ = occ.cut(fused, tools)
    occ.synchronize()
    vol = body[0][1]
    # 주조 필렛: 판 윗면과 리브·허브가 만나는 모서리. OCC가 거절하면 필렛 없이 간다
    edges = []
    for dim, tag in gmsh.model.getBoundary([(3, vol)], oriented=False, combined=False):
        for _, c in gmsh.model.getBoundary([(dim, tag)], oriented=False, combined=False):
            x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(1, abs(c))
            if abs(z0 - T0) < 0.01 and abs(z1 - T0) < 0.01 and np.hypot(x1 - x0, y1 - y0) > 6:
                edges.append(abs(c))
    try:
        out = occ.fillet([vol], sorted(set(edges)), [2.2], removeVolume=True)
        occ.synchronize()
        vol = out[0][1]
        print("필렛", len(set(edges)), "모서리")
    except Exception as e:  # noqa: BLE001
        print("필렛 생략:", str(e)[:80])
    gmsh.option.setNumber("Mesh.MeshSizeMin", size * 0.5)
    gmsh.option.setNumber("Mesh.MeshSizeMax", size * 1.5)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 16)
    gmsh.option.setNumber("Mesh.Algorithm3D", 1)
    gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)
    gmsh.option.setNumber("Mesh.ElementOrder", 2)
    gmsh.option.setNumber("Mesh.SecondOrderLinear", 0)
    gmsh.model.mesh.generate(3)
    return _take_mesh(gmsh)


def _take_mesh(gmsh):
    tags, coords, _ = gmsh.model.mesh.getNodes()
    index = np.zeros(int(tags.max()) + 1, dtype=np.int64)
    index[tags.astype(np.int64)] = np.arange(len(tags))
    _, nodes = gmsh.model.mesh.getElementsByType(11)  # 11 = 10절점 사면체
    tets = index[nodes.astype(np.int64).reshape(-1, 10)]
    pts = coords.reshape(-1, 3)
    gmsh.finalize()
    used = np.unique(tets)
    remap = np.full(len(pts), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    pts, tets = pts[used], remap[tets]
    # 절점 순서 확인: 모서리 절점이 정말 그 모서리의 가운데 근처에 있어야 한다(순서가 틀리면 조용히 틀린 답이 나온다)
    for m, (i, j) in enumerate(TET10_EDGES):
        mid = 0.5 * (pts[tets[:, i]] + pts[tets[:, j]])
        edge = np.linalg.norm(pts[tets[:, i]] - pts[tets[:, j]], axis=1)
        assert (np.linalg.norm(pts[tets[:, 4 + m]] - mid, axis=1) < 0.35 * edge).all(), f"절점 순서가 다릅니다: 모서리 {i}-{j}"
    return pts, tets


def element_b(pts: np.ndarray, tets: np.ndarray):
    """적분점별 B 행렬 (4,M,6,30)과 detJ (4,M). 뒤집힌 요소가 있으면 멈춘다(모핑이 형상을 찢었다는 뜻)."""
    xe = pts[tets]  # (M,10,3)
    Bs, dets = [], []
    for g in range(4):
        J = np.einsum("ab,mac->mbc", DN[g], xe)  # (M,3,3): J[b,c] = ∂x_c/∂ξ_b
        det = np.linalg.det(J)
        if det.min() <= 0:
            raise ValueError(f"뒤집힌 요소 {(det <= 0).sum()}개: 메쉬나 모핑이 잘못됐습니다")
        grad = np.einsum("mbc,ac->mab", np.linalg.inv(J), DN[g])  # (M,10,3) ∂N/∂x
        B = np.zeros((len(tets), 6, 30))
        gx, gy, gz = grad[:, :, 0], grad[:, :, 1], grad[:, :, 2]
        B[:, 0, 0::3], B[:, 1, 1::3], B[:, 2, 2::3] = gx, gy, gz
        B[:, 3, 0::3], B[:, 3, 1::3] = gy, gx
        B[:, 4, 1::3], B[:, 4, 2::3] = gz, gy
        B[:, 5, 0::3], B[:, 5, 2::3] = gz, gx
        Bs.append(B)
        dets.append(det)
    return np.stack(Bs), np.stack(dets)


def d_matrix(e: float, nu: float) -> np.ndarray:
    c = e / ((1 + nu) * (1 - 2 * nu))
    D = np.zeros((6, 6))
    D[:3, :3] = c * nu
    D[np.arange(3), np.arange(3)] = c * (1 - nu)
    D[np.arange(3, 6), np.arange(3, 6)] = c * (1 - 2 * nu) / 2
    return D


def solve_static(pts, tets, fixed_nodes, forces, e=E, nu=NU, x0=None):
    """선형 정적 해석. 돌려주는 값: 변위 (N,3), 적분점 응력 (4,M,6), 부피 합."""
    Bs, dets = element_b(pts, tets)
    D = d_matrix(e, nu)
    ke = np.zeros((len(tets), 30, 30))
    for g in range(4):
        ke += np.einsum("m,mji,jk,mkl->mil", dets[g] / 24.0, Bs[g], D, Bs[g], optimize=True)
    dof = (tets[:, :, None] * 3 + np.arange(3)).reshape(len(tets), 30)
    rows = np.repeat(dof, 30, axis=1).ravel()
    cols = np.tile(dof, (1, 30)).ravel()
    n = pts.shape[0] * 3
    K = sp.coo_matrix((ke.ravel(), (rows, cols)), shape=(n, n)).tocsr()
    del ke
    free = np.ones(n, dtype=bool)
    free[(fixed_nodes[:, None] * 3 + np.arange(3)).ravel()] = False
    Kff = K[free][:, free]
    precond = sp.diags(1.0 / Kff.diagonal())
    x, info = cg(Kff, forces.ravel()[free], x0=None if x0 is None else x0.ravel()[free], M=precond, rtol=1e-7, maxiter=60000)
    if info != 0:
        raise RuntimeError(f"CG 수렴 실패: {info}")
    u = np.zeros(n)
    u[free] = x
    u = u.reshape(-1, 3)
    ue = u[tets].reshape(len(tets), 30)
    stress = np.stack([np.einsum("mij,mj->mi", Bs[g], ue) @ D.T for g in range(4)])
    return u, stress, float(dets.sum() / 24.0)


def von_mises(s: np.ndarray) -> np.ndarray:
    return np.sqrt(0.5 * ((s[..., 0] - s[..., 1]) ** 2 + (s[..., 1] - s[..., 2]) ** 2 + (s[..., 2] - s[..., 0]) ** 2)
                   + 3 * (s[..., 3] ** 2 + s[..., 4] ** 2 + s[..., 5] ** 2))


def nodal_von_mises(n: int, tets: np.ndarray, stress: np.ndarray) -> np.ndarray:
    """적분점 응력 → 요소 꼭짓점으로 외삽 → 절점 평균 → von Mises. 후처리기의 기본 표시와 같은 순서다(성분을 먼저 평균)."""
    # 적분점 값 σ_g = Σ_c L_c(g) σ_c 이므로 꼭짓점 값은 역행렬로 구한다
    corner = np.einsum("cg,gmk->cmk", EXTRAP, stress)  # (4,M,6)
    total, count = np.zeros((n, 6)), np.zeros(n)
    for c in range(4):
        np.add.at(total, tets[:, c], corner[c])
        np.add.at(count, tets[:, c], 1.0)
    for m, (i, j) in enumerate(TET10_EDGES):
        np.add.at(total, tets[:, 4 + m], 0.5 * (corner[i] + corner[j]))
        np.add.at(count, tets[:, 4 + m], 1.0)
    return von_mises(total / np.maximum(count, 1.0)[:, None])


def surface_quadratic(tets: np.ndarray):
    """표면: 10절점 사면체의 바깥 면 → 삼각형 4개로 나눈 표시 삼각형, 요소 경계선(꼭짓점-모서리 절점 연결)."""
    # 면: 꼭짓점 3개(바깥 방향 순서)와 그 사이 모서리 절점 3개
    table = [((0, 2, 1), (6, 5, 4)), ((0, 1, 3), (4, 9, 7)), ((1, 2, 3), (5, 8, 9)), ((0, 3, 2), (7, 8, 6))]
    corners = np.vstack([tets[:, list(c)] for c, _ in table])
    mids = np.vstack([tets[:, list(m)] for _, m in table])
    _, first, cnt = np.unique(np.sort(corners, axis=1), axis=0, return_index=True, return_counts=True)
    pick = first[cnt == 1]
    c, m = corners[pick], mids[pick]
    tris = np.vstack([
        np.column_stack([c[:, 0], m[:, 0], m[:, 2]]), np.column_stack([m[:, 0], c[:, 1], m[:, 1]]),
        np.column_stack([m[:, 2], m[:, 1], c[:, 2]]), np.column_stack([m[:, 0], m[:, 1], m[:, 2]]),
    ])
    lines = np.vstack([
        np.column_stack([c[:, 0], m[:, 0]]), np.column_stack([m[:, 0], c[:, 1]]),
        np.column_stack([c[:, 1], m[:, 1]]), np.column_stack([m[:, 1], c[:, 2]]),
        np.column_stack([c[:, 2], m[:, 2]]), np.column_stack([m[:, 2], c[:, 0]]),
    ])
    return tris, np.unique(np.sort(lines, axis=1), axis=0)


def check_cantilever():
    """외팔보 끝단 처짐을 보 이론(전단 포함)과 비교한다(L=100, 10×10, P=100N)."""
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.occ.addBox(0, 0, 0, 100, 10, 10)
    gmsh.model.occ.synchronize()
    gmsh.option.setNumber("Mesh.MeshSizeMax", 5.0)
    gmsh.option.setNumber("Mesh.ElementOrder", 2)
    gmsh.model.mesh.generate(3)
    pts, tets = _take_mesh(gmsh)
    fixed = np.flatnonzero(pts[:, 0] < 1e-6)
    tip = np.flatnonzero(pts[:, 0] > 100 - 1e-6)
    f = np.zeros_like(pts)
    f[tip, 2] = -100.0 / len(tip)
    u, s, v = solve_static(pts, tets, fixed, f, e=70000.0, nu=0.0)
    inertia = 10 ** 4 / 12
    theory = 100 * 100 ** 3 / (3 * 70000 * inertia) + 100 * 100 / ((5 / 6) * 100 * 35000)  # 굽힘 + 전단
    got = -u[tip, 2].mean()
    # 가운데 단면의 위쪽 굽힘 응력: M c / I
    vm = nodal_von_mises(len(pts), tets, s)
    near = np.flatnonzero((np.abs(pts[:, 0] - 50) < 3) & (pts[:, 2] > 10 - 1e-6) & (np.abs(pts[:, 1] - 5) < 3))
    sig_theory = 100 * 50 * 5 / inertia
    print(f"외팔보 처짐: FEM {got:.4f} · 이론 {theory:.4f} · 비 {got / theory:.3f} · 부피 {v:.1f}(10000) · 요소 {len(tets)} · "
          f"가운데 응력 FEM {vm[near].mean():.2f} · 이론 {sig_theory:.2f} MPa")


def smooth(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def morph(pts: np.ndarray, sz: float, sw: float, dr: float) -> np.ndarray:
    """메쉬 모핑: 두께(sz), 팔 폭(sw), 볼트 위치(dr). 축 구멍·볼트 구멍은 둥글게 둔다.
    이동량은 팔 바깥으로 갈수록 0이 되어, 팔과 팔 사이 경계에서 끊기지 않는다(끊기면 메쉬가 찢어진다)."""
    out = pts.copy()
    out[:, 2] *= sz
    r = np.hypot(pts[:, 0], pts[:, 1])
    th = np.arctan2(pts[:, 1], pts[:, 0])
    arm = np.round(th / (2 * np.pi / 3)) * 2 * np.pi / 3
    u = r * np.cos(th - arm)
    v = r * np.sin(th - arm)
    half = np.interp(u, [RH * 0.6, RA], [W0, W1]) + 2.5  # 팔 반폭(테두리 리브 포함)
    inside = 1 - smooth((np.abs(v) - half) / 24.0)  # 팔 안 1 → 팔에서 24mm 떨어지면 0
    along = smooth((r - RH * 1.05) / (RH * 0.8)) * (1 - smooth((u - (RA - RBOSS * 2.4)) / (RBOSS * 1.1)))
    v2 = v + (sw - 1) * v * along * inside
    u2 = u + dr * smooth((r - RH * 1.05) / (RA - RBOSS - RH * 1.05)) * inside
    out[:, 0] = u2 * np.cos(arm) - v2 * np.sin(arm)
    out[:, 1] = u2 * np.sin(arm) + v2 * np.cos(arm)
    return out


def morph_mesh(pts0: np.ndarray, tets: np.ndarray, sz: float, sw: float, dr: float) -> np.ndarray:
    """꼭짓점만 모핑하고, 모서리 절점은 새 꼭짓점의 가운데 + 원래의 휨으로 다시 놓는다.
    곡선 모핑을 모서리 절점에 그대로 쓰면 꼭짓점 사이에서 벗어나 2차 요소가 뒤집힌다."""
    out = morph(pts0, sz, sw, dr)
    for m, (i, j) in enumerate(TET10_EDGES):
        mid = tets[:, 4 + m]
        bow0 = pts0[mid] - 0.5 * (pts0[tets[:, i]] + pts0[tets[:, j]])
        out[mid] = 0.5 * (out[tets[:, i]] + out[tets[:, j]]) + bow0
    return out


DESIGNS = [  # 두께 비, 팔 폭 비, 볼트 위치 이동(mm)
    (1.00, 1.00, 0.0), (0.82, 1.18, 6.0), (1.20, 0.85, -6.0), (0.90, 0.90, 8.0), (1.12, 1.20, -4.0),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--size", type=float, default=5.0)
    a = ap.parse_args()
    if a.check:
        check_cantilever()
        return
    t0 = time.time()
    pts0, tets = build_cad(a.size)
    print(f"메쉬: 절점 {len(pts0)} · C3D10 {len(tets)} ({time.time() - t0:.0f}s)", flush=True)
    R = np.hypot(pts0[:, 0], pts0[:, 1])
    bolt_centers = np.array([[RA * np.cos(2 * np.pi * k / 3), RA * np.sin(2 * np.pi * k / 3)] for k in range(3)])
    bolt_d = np.min(np.linalg.norm(pts0[:, None, :2] - bolt_centers[None], axis=2), axis=1)
    fixed = np.flatnonzero(bolt_d < RBOLT + 0.05)  # 볼트 구멍 면 고정
    bore = np.flatnonzero(R < RB + 0.05)  # 축 구멍에 토크 + 반경 하중(탈수 불평형 근사)
    # 응력 특이점 제외: 고정한 구멍의 가장자리는 메쉬를 줄일수록 값이 끝없이 커지므로 Max에서 뺀다(실무 관행)
    away = bolt_d > RBOLT + 7.0
    print("고정 절점", len(fixed), "축 구멍 절점", len(bore), flush=True)

    tris_full, lines_full = surface_quadratic(tets)
    keep, inv = np.unique(np.vstack([tris_full.ravel()[:, None], lines_full.ravel()[:, None]]), return_inverse=True)
    inv = inv.ravel()
    tris = inv[:tris_full.size].reshape(tris_full.shape)
    lines = inv[tris_full.size:].reshape(lines_full.shape)
    results, rows, prev = [], [], None
    for sz, sw, dr in DESIGNS:
        pts = morph_mesh(pts0, tets, sz, sw, dr)
        f = np.zeros_like(pts)
        torque, radial = 300e3, 10000.0  # N·mm, N
        rb = np.hypot(pts[bore, 0], pts[bore, 1])
        f[bore, 0] = -torque * pts[bore, 1] / rb ** 2 / len(bore) + radial / len(bore)
        f[bore, 1] = torque * pts[bore, 0] / rb ** 2 / len(bore)
        f[bore, 2] = 2500.0 / len(bore)
        t1 = time.time()
        u, stress, volume = solve_static(pts, tets, fixed, f, x0=prev)
        prev = u
        vm = nodal_von_mises(len(pts), tets, stress)
        vis = np.zeros(len(pts), dtype=bool)
        vis[keep] = True
        cand = np.flatnonzero(vis & away)
        max_i = int(cand[np.argmax(vm[cand])])
        rows.append({"t": round(T0 * sz, 2), "w": round(2 * W1 * sw, 1), "ra": round(2 * (RA + dr), 0), "sigma": round(float(vm[max_i]), 1),
                     "disp": round(float(np.linalg.norm(u, axis=1).max()), 3), "mass": round(float(volume * RHO * 1e6), 0),
                     "peak": round(float(vm[keep].max()), 1)})
        print(sz, sw, dr, rows[-1], f"({time.time() - t1:.0f}s)", flush=True)
        results.append((pts, u, vm, max_i))
    all_vm = np.concatenate([r[2][np.flatnonzero(vis & away)] for r in results])
    vmax = float(np.percentile(all_vm, 99.8))
    scale = round(0.045 * 300 / max(r["disp"] for r in rows) / 5) * 5  # 변형이 눈에 보이되 과하지 않게
    positions = [(pts + u * scale)[keep] for pts, u, _, _ in results]
    fields = [vm[keep] for _, _, vm, _ in results]
    q, lo, span = quantize_positions(np.stack(positions))
    keep_index = {int(n): i for i, n in enumerate(keep)}
    meta = {
        "id": "structural", "kind": "구조해석", "name": "드럼 스파이더", "method": "선형 정적 FEM", "element": "2차 사면체(C3D10)",
        "elements": int(len(tets)), "nodes": int(len(pts0)), "field": "von Mises", "unit": "MPa", "vmin": 0.0, "vmax": round(vmax, 1),
        "scale": scale, "lo": lo, "span": span, "mode": "designs", "seconds": 15, "yaw": [-1.05, 0.03], "pitch": 0.72,
        "steps": [{**r, "maxNode": keep_index[res[3]]} for r, res in zip(rows, results)], "note": "구속부 특이점 제외",
        "readout": [["판 두께", "t", "mm"], ["팔 끝 폭", "w", "mm"], ["최대 응력", "sigma", "MPa"], ["질량", "mass", "g"]],
    }
    save("structural", meta, {
        "tris": tris.astype(np.uint32), "feature": feature_edges(positions[0], tris).astype(np.uint32), "mesh": lines.astype(np.uint32),
        "pos": q, "field": quantize_field(np.stack(fields), 0.0, vmax),
    })
    print(f"완료 {time.time() - t0:.0f}s · 표면 절점 {len(keep)} · 표시 삼각형 {len(tris)} · 변형 배율 {scale}", flush=True)


if __name__ == "__main__":
    main()
