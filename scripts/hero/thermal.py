"""열유동해석: 전자 보드의 히트싱크 냉각. 유동은 격자 볼츠만(D3Q19), 온도는 고체·공기를 함께 푸는 정상 유한체적법.

핀 높이를 바꾼 설계마다 실제로 풀어 저장한다.
    python scripts/hero/thermal.py --check            # 채널 유동을 이론값과 비교
    python scripts/hero/thermal.py --solve 14         # 핀 높이 14mm 풀기(오래 걸린다. 설계마다 따로)
    python scripts/hero/thermal.py --export 14 20 26  # 풀어 둔 결과를 frontend/public/hero/로
저속 층류 조건의 예시 모델이며 실제 제품 데이터가 아니다.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import scipy.sparse as sp
from scipy.ndimage import distance_transform_edt, map_coordinates
from scipy.sparse.linalg import bicgstab, spilu, LinearOperator

sys.path.insert(0, str(Path(__file__).parent))
from common import feature_edges, mesh_edges, quantize_field, quantize_positions, save  # noqa: E402

CACHE = Path(os.environ.get("HERO_CACHE", Path(tempfile.gettempdir()) / "dds_hero"))
NX, NY, NZ = 120, 70, 36  # 격자(1칸 = 1mm)
POWER, T_IN = 8.0, 25.0  # 칩 발열 W, 입구 공기 ℃
POWER_SHOW = 4.0  # 화면에 보일 발열. 에너지 식이 발열에 선형이라 온도 상승을 그대로 비례시키면 그 발열의 정확한 해다
TAU, U_LB = 0.53, 0.08
NU_AIR, RHO_AIR, CP_AIR, K_AIR = 1.5e-5, 1.2, 1005.0, 0.026
K = {"air": K_AIR, "pcb": 4.0, "chip": 120.0, "sink": 200.0, "part": 1.0}
PCB_T, CHIP_T, BASE_T = 2, 2, 3  # 칸 수
SINK_X, SINK_Y = (38, 82), (13, 57)  # 히트싱크 범위(칸)
CHIP_X, CHIP_Y = (48, 72), (23, 47)
FIN_PITCH, FIN_THICK = 5, 2
# 보드 위 다른 부품: (종류, x, y, 크기1, 크기2, 높이)
PARTS = [("cyl", 16, 18, 6, 0, 14), ("cyl", 16, 52, 6, 0, 14), ("cyl", 29, 35, 4, 0, 10),
         ("box", 92, 10, 14, 12, 7), ("box", 92, 48, 14, 12, 7), ("box", 22, 4, 10, 6, 4), ("cyl", 104, 35, 5, 0, 11)]

# D3Q19
C = np.array([[0, 0, 0], [1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1], [1, 1, 0], [-1, -1, 0], [1, -1, 0], [-1, 1, 0],
              [1, 0, 1], [-1, 0, -1], [1, 0, -1], [-1, 0, 1], [0, 1, 1], [0, -1, -1], [0, 1, -1], [0, -1, 1]])
W = np.array([1 / 3] + [1 / 18] * 6 + [1 / 36] * 12, dtype=np.float32)
OPP = np.array([0, 2, 1, 4, 3, 6, 5, 8, 7, 10, 9, 12, 11, 14, 13, 16, 15, 18, 17])


def feq(rho, ux, uy, uz):
    out = np.empty((19,) + rho.shape, dtype=np.float32)
    u2 = 1.5 * (ux * ux + uy * uy + uz * uz)
    for i in range(19):
        cu = 3.0 * (C[i, 0] * ux + C[i, 1] * uy + C[i, 2] * uz)
        out[i] = W[i] * rho * (1.0 + cu + 0.5 * cu * cu - u2)
    return out


def lbm(solid: np.ndarray, u_in: float, tau: float, steps: int, average_from: int, inlet_mask=None, periodic_x=False, force=0.0, log=True):
    """solid: (nx,ny,nz) 불리언. 돌려주는 값: 시간 평균 (u,v,w,rho)."""
    nx, ny, nz = solid.shape
    rho0 = np.ones(solid.shape, dtype=np.float32)
    zero = np.zeros(solid.shape, dtype=np.float32)
    ux0 = np.full(solid.shape, u_in, dtype=np.float32)
    ux0[solid] = 0
    f = feq(rho0, ux0, zero, zero)
    omega = 1.0 / tau
    avg = [np.zeros(solid.shape, dtype=np.float64) for _ in range(4)]
    n_avg = 0
    moving = [i for i in range(19)]
    t0 = time.time()
    in_rho = np.ones((ny, nz), dtype=np.float32)
    for step in range(steps):
        rho = f.sum(axis=0)
        ux = (f[1] - f[2] + f[7] - f[8] + f[9] - f[10] + f[11] - f[12] + f[13] - f[14]) / rho
        uy = (f[3] - f[4] + f[7] - f[8] - f[9] + f[10] + f[15] - f[16] + f[17] - f[18]) / rho
        uz = (f[5] - f[6] + f[11] - f[12] - f[13] + f[14] + f[15] - f[16] - f[17] + f[18]) / rho
        if force:
            ux = ux + force * tau / rho
        if step >= average_from:
            for a, v in zip(avg, (ux, uy, uz, rho)):
                a += v
            n_avg += 1
        fe = feq(rho, ux, uy, uz)
        post = f + omega * (fe - f)
        post[:, solid] = f[OPP][:, solid]  # 벽: 되튕김
        for i in moving:
            cx, cy, cz = C[i]
            f[i] = np.roll(np.roll(np.roll(post[i], cx, axis=0), cy, axis=1), cz, axis=2)
        if not periodic_x:
            # 입구: 일정한 속도 · 출구: 기울기 0
            in_ux = np.full((ny, nz), u_in, dtype=np.float32)
            in_ux[solid[0]] = 0
            f[:, 0] = feq(in_rho, in_ux, zero[0], zero[0])
            f[:, -1] = f[:, -2]
        if log and step % 500 == 0:
            speed = float(np.abs(ux).max())
            print(f"  step {step}/{steps} |u|max {speed:.3f} ({time.time() - t0:.0f}s)", flush=True)
            if not np.isfinite(speed) or speed > 0.5:
                raise RuntimeError("격자 볼츠만이 발산했습니다")
    return [a / max(n_avg, 1) for a in avg]


def check_channel():
    """평행 평판 사이 유동: 체적력으로 민 정상 속도 분포를 포물선과 비교한다."""
    ny, nz = 6, 41
    solid = np.zeros((4, ny, nz), dtype=bool)
    solid[:, :, 0] = solid[:, :, -1] = True
    tau, g = 0.8, 1e-5
    u, *_ = lbm(solid, 0.0, tau, 14000, 13000, periodic_x=True, force=g, log=False)
    nu = (tau - 0.5) / 3
    H = nz - 2  # 되튕김 벽이 놓이는 곳 기준 유효 간격
    theory = g * H * H / (8 * nu)
    got = float(u[:, :, 1:-1].mean(axis=(0, 1)).max())
    print(f"채널 유동 최대 속도: LBM {got:.5f} · 이론 {theory:.5f} · 비 {got / theory:.3f}")


def geometry(height: int):
    """격자 재료 표시와 고체 여부."""
    mat = np.zeros((NX, NY, NZ), dtype=np.uint8)  # 0 공기, 1 PCB, 2 칩, 3 싱크, 4 부품
    mat[:, :, :PCB_T] = 1
    z = PCB_T
    x0, x1, y0, y1 = CHIP_X[0], CHIP_X[1], CHIP_Y[0], CHIP_Y[1]
    mat[x0:x1, y0:y1, z:z + CHIP_T] = 2
    z += CHIP_T
    sx0, sx1, sy0, sy1 = SINK_X[0], SINK_X[1], SINK_Y[0], SINK_Y[1]
    mat[sx0:sx1, sy0:sy1, z:z + BASE_T] = 3
    z += BASE_T
    y = sy0
    while y + FIN_THICK <= sy1:
        mat[sx0:sx1, y:y + FIN_THICK, z:z + height] = 3
        y += FIN_PITCH
    xx, yy = np.meshgrid(np.arange(NX) + 0.5, np.arange(NY) + 0.5, indexing="ij")
    for kind, px, py, a, b, h in PARTS:
        if kind == "cyl":
            mask = (xx - px) ** 2 + (yy - py) ** 2 <= a * a
        else:
            mask = (xx >= px) & (xx < px + a) & (yy >= py) & (yy < py + b)
        mat[:, :, PCB_T:PCB_T + h][mask] = 4
    solid = mat > 0
    walls = solid.copy()
    walls[:, 0, :] = walls[:, -1, :] = True  # 덕트 옆벽
    walls[:, :, -1] = True  # 덕트 윗벽
    return mat, walls


def energy(mat, u, v, w, height):
    """정상 에너지 식 ρcp u·∇T = ∇·(k∇T) + q. 면 유속은 평균 속도에서 얻고 풍상 차분을 쓴다. 단위: 1칸 = 1mm = 1e-3 m."""
    dx = 1e-3
    dt_phys = ((TAU - 0.5) / 3) * dx * dx / NU_AIR
    vel = dx / dt_phys  # 격자 속도 → m/s
    nx, ny, nz = mat.shape
    k = np.full(mat.shape, K["air"])
    k[mat == 1], k[mat == 2], k[mat == 3], k[mat == 4] = K["pcb"], K["chip"], K["sink"], K["part"]
    air = mat == 0
    rc = RHO_AIR * CP_AIR
    idx = np.arange(mat.size).reshape(mat.shape)
    rows, cols, vals = [], [], []
    b = np.zeros(mat.size)
    diag = np.zeros(mat.size)
    area = dx * dx

    def face(a0, a1, ua0, ua1, axis_flow_ok0, axis_flow_ok1):
        """a0(작은 쪽)·a1(큰 쪽) 칸 사이 면의 전도·대류. 유속은 공기-공기 면에만."""
        k_face = 2 * k[a0] * k[a1] / (k[a0] + k[a1])
        g = k_face * area / dx
        i0, i1 = idx[a0].ravel(), idx[a1].ravel()
        gv = g.ravel()
        flow = 0.5 * (ua0 + ua1) * vel * area * rc * (axis_flow_ok0 & axis_flow_ok1)
        fl = flow.ravel()
        out0 = np.maximum(fl, 0)  # i0 → i1
        out1 = np.maximum(-fl, 0)  # i1 → i0
        # 평균 속도장은 질량을 완벽히 보존하지 않는다. 대각을 '들어오는 쪽 계수의 합'으로 두면 일정한 온도가 항상 해가 되어,
        # 입구보다 차가운 공기 같은 불가능한 값이 생기지 않는다(최대값 원리). 대신 에너지 수지에 작은 오차가 남아 따로 확인한다
        np.add.at(diag, i0, gv + out1)
        np.add.at(diag, i1, gv + out0)
        rows.extend([i0, i1])
        cols.extend([i1, i0])
        vals.extend([-(gv + out1), -(gv + out0)])

    face((slice(0, -1), slice(None), slice(None)), (slice(1, None), slice(None), slice(None)), u[:-1], u[1:], air[:-1], air[1:])
    face((slice(None), slice(0, -1), slice(None)), (slice(None), slice(1, None), slice(None)), v[:, :-1], v[:, 1:], air[:, :-1], air[:, 1:])
    face((slice(None), slice(None), slice(0, -1)), (slice(None), slice(None), slice(1, None)), w[:, :, :-1], w[:, :, 1:], air[:, :, :-1], air[:, :, 1:])
    # 입구 칸: 온도 고정 · 출구: 흘러 나감(기울기 0) · 나머지 벽은 단열
    inlet = idx[0].ravel()
    out_flux = np.maximum(u[-1][air[-1]].ravel(), 0) * vel * area * rc
    chip = mat == 2
    b[idx[chip]] = POWER / chip.sum()
    A = sp.coo_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(mat.size, mat.size)).tolil()
    A.setdiag(diag)
    A = A.tocsr()
    # 입구 고정(행을 항등식으로)
    keep = np.ones(mat.size)
    keep[inlet] = 0
    D = sp.diags(keep)
    A = (D @ A + sp.diags(1 - keep)).tocsc()
    b = b * keep + (1 - keep) * T_IN
    print("  에너지 식 풀기", A.shape, flush=True)
    ilu = spilu(A, drop_tol=1e-4, fill_factor=8)
    x, info = bicgstab(A, b, x0=np.full(mat.size, T_IN + 5.0), M=LinearOperator(A.shape, ilu.solve), rtol=1e-8, maxiter=800)
    if info != 0:
        print("  경고: 반복 해법 미수렴", info)
    T = x.reshape(mat.shape)
    # 에너지 수지: 출구로 나간 열 = 칩 발열
    out_heat = float((out_flux * (T[-1][air[-1]] - T_IN)).sum())
    bulk = T_IN + out_heat / max(out_flux.sum(), 1e-12)  # 유량 가중 출구 온도
    print(f"  에너지 수지: 출구 {out_heat:.2f} W · 발열 {POWER:.2f} W · 비 {out_heat / POWER:.3f} · 온도 범위 {T.min():.2f}~{T.max():.1f}℃ · 출구 공기(유량 가중) {bulk:.1f}℃", flush=True)
    return T, vel, out_heat, bulk


def resolve_energy(height: int):
    """저장해 둔 속도장으로 온도만 다시 푼다."""
    p = CACHE / f"thermal_{height}.npz"
    d = dict(np.load(p))
    T, vel, heat, bulk = energy(d["mat"], d["u"].astype(np.float64), d["v"].astype(np.float64), d["w"].astype(np.float64), height)
    d.update(T=T.astype(np.float32), heat=heat, bulk=bulk)
    np.savez_compressed(p, **d)


def solve(height: int):
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    mat, walls = geometry(height)
    u, v, w, rho = lbm(walls, U_LB, TAU, 5000, 3500)
    u[walls] = v[walls] = w[walls] = 0
    T, vel, heat, bulk = energy(mat, u, v, w, height)
    # 압력 손실: 입구·출구 단면 평균 밀도 차 × 격자 음속²
    air = ~walls
    dp_lb = (rho[4][air[4]].mean() - rho[-5][air[-5]].mean()) / 3
    dp = float(dp_lb * RHO_AIR * vel * vel)
    np.savez_compressed(CACHE / f"thermal_{height}.npz", mat=mat, u=u.astype(np.float32), v=v.astype(np.float32), w=w.astype(np.float32),
                        T=T.astype(np.float32), vel=vel, dp=dp, heat=heat, bulk=bulk)
    print(f"핀 {height}mm 완료: T최대 {T.max():.1f}℃ · Δp {dp:.3f} Pa · 입구 {U_LB * vel:.3f} m/s · {time.time() - t0:.0f}s", flush=True)


def cad_surface(height: int, size: float = 2.2):
    """표시용 CAD 표면 메쉬. 돌려주는 값: 절점, 삼각형, 핀 윗부분 여부(높이 모핑용), 재료 번호."""
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("board")
    occ = gmsh.model.occ
    z = PCB_T + CHIP_T + BASE_T
    pcb = occ.addBox(0, 0, 0, NX, NY, PCB_T)
    chip = occ.addBox(CHIP_X[0], CHIP_Y[0], PCB_T, CHIP_X[1] - CHIP_X[0], CHIP_Y[1] - CHIP_Y[0], CHIP_T)
    sink = [occ.addBox(SINK_X[0], SINK_Y[0], PCB_T + CHIP_T, SINK_X[1] - SINK_X[0], SINK_Y[1] - SINK_Y[0], BASE_T)]
    y = SINK_Y[0]
    while y + FIN_THICK <= SINK_Y[1]:
        sink.append(occ.addBox(SINK_X[0], y, z, SINK_X[1] - SINK_X[0], FIN_THICK, height))
        y += FIN_PITCH
    fused, _ = occ.fuse([(3, sink[0])], [(3, s) for s in sink[1:]])
    sink_tags = [t for _, t in fused]
    parts = []
    for kind, px, py, a, b, h in PARTS:
        parts.append(occ.addCylinder(px, py, PCB_T, 0, 0, h, a) if kind == "cyl" else occ.addBox(px, py, PCB_T, a, b, h))
    occ.synchronize()
    gmsh.option.setNumber("Mesh.MeshSizeMin", size * 0.5)
    gmsh.option.setNumber("Mesh.MeshSizeMax", size * 2)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 18)
    gmsh.model.mesh.generate(2)
    pts_all, tris_all, tag_all, offset = [], [], [], 0
    groups = [([pcb], 1), ([chip], 2), (sink_tags, 3), (parts, 4)]
    for vols, tag in groups:
        for vol in vols:
            for dim, surf in gmsh.model.getBoundary([(3, vol)], oriented=False):
                node_tags, coords, _ = gmsh.model.mesh.getNodes(2, abs(surf), includeBoundary=True)
                if not len(node_tags):
                    continue
                index = {int(t): i for i, t in enumerate(node_tags)}
                _, enodes = gmsh.model.mesh.getElementsByType(2, abs(surf))
                tri = np.array([index[int(t)] for t in enodes], dtype=np.int64).reshape(-1, 3)
                pts_all.append(coords.reshape(-1, 3))
                tris_all.append(tri + offset)
                tag_all.append(np.full(len(node_tags), tag, dtype=np.uint8))
                offset += len(node_tags)
    gmsh.finalize()
    return np.vstack(pts_all), np.vstack(tris_all), np.concatenate(tag_all)


def streamlines(u, v, w, T, vel, n_y=9, n_z=4, n_pts=56):
    """입구에서 시작하는 유선(시간 평균 속도장 적분). 같은 시작점을 설계마다 쓴다."""
    lines, temps = [], []
    shape = np.array(u.shape, dtype=np.float64)
    for y0 in np.linspace(6, NY - 6, n_y):
        for z0 in np.linspace(PCB_T + 2.5, NZ - 5, n_z):
            p = np.array([1.0, y0, z0])
            path = [p.copy()]
            for _ in range(1400):
                q = p[:, None] - 0.5
                vel_here = np.array([map_coordinates(c, q, order=1, mode="nearest")[0] for c in (u, v, w)])
                speed = np.linalg.norm(vel_here)
                if speed < 1e-5:
                    break
                mid = p + 0.35 * vel_here / speed
                q = mid[:, None] - 0.5
                vel_mid = np.array([map_coordinates(c, q, order=1, mode="nearest")[0] for c in (u, v, w)])
                p = p + 0.7 * vel_mid / max(np.linalg.norm(vel_mid), 1e-6)
                path.append(p.copy())
                if p[0] >= shape[0] - 1.5 or p[1] < 1 or p[1] > shape[1] - 1 or p[2] < 1 or p[2] > shape[2] - 1:
                    break
            path = np.array(path)
            if len(path) < 8:
                path = np.vstack([path, np.repeat(path[-1:], 8, axis=0)])
            s = np.r_[0, np.cumsum(np.linalg.norm(np.diff(path, axis=0), axis=1))]
            if s[-1] < 1e-6:
                s = np.linspace(0, 1, len(path))
            t = np.linspace(0, s[-1], n_pts)
            res = np.column_stack([np.interp(t, s, path[:, c]) for c in range(3)])
            lines.append(res)
            temps.append(map_coordinates(T, res.T - 0.5, order=1, mode="nearest"))
    return np.array(lines), np.array(temps)


def export(heights: list[int]):
    t0 = time.time()
    base_h = heights[len(heights) // 2]
    pts0, tris, tags = cad_surface(base_h)
    top = PCB_T + CHIP_T + BASE_T
    positions, fields, steps, line_pos, line_field, planes = [], [], [], [], [], []
    for h in heights:
        d = np.load(CACHE / f"thermal_{h}.npz")
        mat, T, u, v, w = d["mat"], d["T"], d["u"], d["v"], d["w"]
        T = T_IN + (T - T_IN) * POWER_SHOW / POWER
        pts = pts0.copy()
        fins = (tags == 3) & (pts0[:, 2] > top + 1e-6)
        pts[fins, 2] = top + (pts0[fins, 2] - top) * h / base_h  # 핀 높이 모핑
        # 표면 온도: 가까운 고체 칸의 값(공기 칸 값이 섞이지 않게)
        solid = mat > 0
        _, near = distance_transform_edt(~solid, return_indices=True)
        T_solid = T[tuple(near)]
        vals = map_coordinates(T_solid, (pts.T - 0.5), order=1, mode="nearest")
        positions.append(pts)
        fields.append(vals)
        lp, lt = streamlines(u, v, w, T, float(d["vel"]))
        line_pos.append(lp)
        line_field.append(lt)
        iy = NY // 2 + 1  # 핀 사이 공기 칸을 지나는 단면
        plane = T[:, iy, :].copy()
        plane[mat[:, iy, :] > 0] = np.nan
        planes.append(plane)
        i_max = int(np.argmax(vals))
        steps.append({"H": h, "max": round(float(vals.max()), 1), "dp": round(float(d["dp"]) * 1000, 0), "balance": round(float(d["heat"]) / POWER, 3),
                      "outlet": round(float(T_IN + (float(d["bulk"]) - T_IN) * POWER_SHOW / POWER), 1), "maxNode": i_max})
        print(steps[-1], flush=True)
    vmin, vmax = T_IN, float(np.ceil(max(s["max"] for s in steps)))
    allp = np.concatenate([np.stack(positions), np.stack(line_pos).reshape(len(heights), -1, 3)], axis=1)
    q, lo, span = quantize_positions(allp)
    n = pts0.shape[0]
    L, K_PTS = line_pos[0].shape[:2]
    segs = np.array([[l * K_PTS + i, l * K_PTS + i + 1] for l in range(L) for i in range(K_PTS - 1)], dtype=np.uint32)
    plane_q = np.stack([np.where(np.isnan(p), 0, np.round(np.clip((np.nan_to_num(p, nan=vmin) - vmin) / (vmax - vmin), 0, 1) * 254) + 1) for p in planes]).astype(np.uint8)
    meta = {
        "id": "thermal", "kind": "열유동해석", "name": "보드 히트싱크", "method": "격자 볼츠만 + 유한체적(고체·공기)", "element": "격자",
        "elements": int(NX * NY * NZ), "nodes": n, "field": "온도", "unit": "℃", "vmin": vmin, "vmax": vmax, "lo": lo, "span": span,
        "mode": "designs", "seconds": 15, "yaw": [-0.95, 0.03], "pitch": 0.6, "steps": steps, "fit": 100, "digits": 1, "dy": 0.12,
        "readout": [["핀 높이", "H", "mm"], ["최고 온도", "max", "℃"], ["출구 공기", "outlet", "℃"], ["압력 손실", "dp", "mPa"]],
        "note": f"칩 {POWER_SHOW:.0f} W · 입구 {U_LB * float(np.load(CACHE / f'thermal_{heights[0]}.npz')['vel']):.2f} m/s(저속 층류)",
        "plane": {"x": [0, NX], "z": [0, NZ], "y": NY // 2 + 1.5},
    }
    save("thermal", meta, {
        "tris": tris.astype(np.uint32), "feature": feature_edges(positions[0], tris).astype(np.uint32), "mesh": mesh_edges(tris).astype(np.uint32),
        "pos": q[:, :n], "field": quantize_field(np.stack(fields), vmin, vmax),
        "linePos": q[:, n:], "lineField": quantize_field(np.stack(line_field), vmin, vmax), "lineIndex": segs, "plane": plane_q,
    })
    print(f"내보내기 {time.time() - t0:.0f}s · 표면 절점 {n} · 유선 {L}개", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--solve", type=int)
    ap.add_argument("--export", type=int, nargs="+")
    ap.add_argument("--energy", type=int, nargs="+", help="저장해 둔 속도장으로 온도만 다시 푼다")
    a = ap.parse_args()
    for h in a.energy or []:
        resolve_energy(h)
    if a.check:
        check_channel()
    if a.solve:
        solve(a.solve)
    if a.export:
        export(a.export)
