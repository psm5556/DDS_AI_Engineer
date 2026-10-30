"""낙하해석: 스마트폰 모서리 낙하. 명시적 동해석(중심차분), 탄소성(J2) + 바닥 접촉(벌점), 사면체 요소.

    python scripts/hero/drop.py --check   # 막대를 지나는 탄성파 속도와 탄성 충돌 안정성 확인
    python scripts/hero/drop.py           # 해석 후 frontend/public/hero/drop.* 저장
단위: mm · t · s · N · MPa. 알루미늄 프레임 + 등가 판(유리·기판·배터리를 합친 재료)으로 단순화한 예시 모델이며 실제 제품 데이터가 아니다.
얇은 유리층을 그대로 메쉬하면 안정 시간 간격이 너무 작아져, 질량을 크게 키워야 해서 결과가 틀려진다. 그래서 등가 판을 쓴다.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import feature_edges, mesh_edges, quantize_field, quantize_positions, reindex, save, surface_of  # noqa: E402

G = 9810.0
# 재료: E, nu, 밀도, 항복응력(None이면 탄성)
AL = (71000.0, 0.33, 2.8e-9, 280.0)
PLATE = (20000.0, 0.25, 1.95e-9, None)  # 유리·기판·배터리를 합친 등가 판


def build_phone(size: float):
    """둥근 사각 프레임 + 등가 판 + 카메라 섬."""
    import gmsh

    W, H, T, R = 72.0, 150.0, 8.4, 10.0
    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("phone")
    occ = gmsh.model.occ

    def rounded(x0, y0, z0, w, h, t, r):
        tag = occ.addBox(x0, y0, z0, w, h, t)
        occ.synchronize()
        edges = []
        for _, s in gmsh.model.getBoundary([(3, tag)], oriented=False, combined=False):
            for _, c in gmsh.model.getBoundary([(2, s)], oriented=False, combined=False):
                a = gmsh.model.getBoundingBox(1, abs(c))
                if abs(a[0] - a[3]) < 1e-6 and abs(a[1] - a[4]) < 1e-6:
                    edges.append(abs(c))
        return occ.fillet([tag], sorted(set(edges)), [r], removeVolume=True)[0][1]

    outer = rounded(0, 0, 0, W, H, T, R)
    wall = 2.6
    hole = rounded(wall, wall, -1, W - 2 * wall, H - 2 * wall, T + 2, R - wall)
    frame, _ = occ.cut([(3, outer)], [(3, hole)])
    plate = rounded(wall, wall, 0, W - 2 * wall, H - 2 * wall, T, R - wall)
    bump = rounded(7, H - 48, -2.0, 28, 40, 2.0, 6.0)
    cams = [occ.addCylinder(21, H - 17 - i * 12.0, -3.2, 0, 0, 1.2, 4.6) for i in range(3)]
    all_parts = frame + [(3, plate), (3, bump), *[(3, c) for c in cams]]
    out, _ = occ.fragment(all_parts[:1], all_parts[1:])
    occ.synchronize()
    is_al = {}
    for _, tag in out:
        x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(3, tag)
        # 프레임은 몸체 전체 윤곽, 등가 판은 그 안쪽, 나머지(카메라 섬)는 알루미늄
        inside = x0 >= wall - 0.01 and x1 <= W - wall + 0.01 and z0 >= -0.01 and z1 <= T + 0.01
        is_al[tag] = not inside
    gmsh.option.setNumber("Mesh.MeshSizeMin", size * 0.6)
    gmsh.option.setNumber("Mesh.MeshSizeMax", size * 1.5)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 14)
    gmsh.option.setNumber("Mesh.Algorithm3D", 1)
    gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)
    gmsh.model.mesh.generate(3)
    tags, coords, _ = gmsh.model.mesh.getNodes()
    index = np.zeros(int(tags.max()) + 1, dtype=np.int64)
    index[tags.astype(np.int64)] = np.arange(len(tags))
    pts = coords.reshape(-1, 3)
    tets, mats = [], []
    for _, tag in out:
        _, enodes = gmsh.model.mesh.getElementsByType(4, tag)
        t = index[enodes.astype(np.int64).reshape(-1, 4)]
        tets.append(t)
        mats.append(np.full(len(t), 0 if is_al[tag] else 1, dtype=np.int64))
    gmsh.finalize()
    tets, mats = np.vstack(tets), np.concatenate(mats)
    used = np.unique(tets)
    remap = np.full(len(pts), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return pts[used], remap[tets], mats


class Explicit:
    """사면체 명시적 동해석. 큰 회전은 요소마다 회전을 빼고 작은 변형률로 다룬다(극분해)."""

    def __init__(self, pts, tets, mats, table, added_mass_limit=0.02, dt=None):
        E = np.array([m[0] for m in table])[mats]
        nu = np.array([m[1] for m in table])[mats]
        rho = np.array([m[2] for m in table])[mats]
        self.yld = np.array([m[3] if m[3] else 1e12 for m in table])[mats]
        d = pts[tets][:, 1:] - pts[tets][:, :1]
        flip = np.linalg.det(d) < 0
        tets = tets.copy()
        tets[flip, 2], tets[flip, 3] = tets[flip, 3], tets[flip, 2].copy()
        d = pts[tets][:, 1:] - pts[tets][:, :1]
        self.tets, self.vol0 = tets, np.linalg.det(d) / 6
        dm_inv = np.linalg.inv(d.transpose(0, 2, 1))  # 기준 모서리를 열로 둔 행렬의 역
        self.Dm_inv = dm_inv
        self.n = len(pts)
        self.x, self.v = pts.copy(), np.zeros_like(pts)
        self.mu = E / (2 * (1 + nu))
        self.lam = E * nu / ((1 + nu) * (1 - 2 * nu))
        self.vm = np.zeros(len(tets))
        self.peeq = np.zeros(len(tets))
        self.eps_p = np.zeros((len(tets), 3, 3))
        c = np.sqrt((self.lam + 2 * self.mu) / rho)
        faces = np.stack([np.linalg.norm(np.cross(pts[tets[:, b]] - pts[tets[:, a]], pts[tets[:, e]] - pts[tets[:, a]]), axis=1) / 2
                          for a, b, e in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))], axis=1)
        h = 3 * self.vol0 / faces.max(axis=1)
        dt_el = 0.7 * h / c
        m_el = rho * self.vol0
        if dt is None:  # 작은 요소 몇 개 때문에 전체가 느려지지 않게, 질량 보정이 한도 안에 드는 가장 큰 시간 간격을 고른다
            for dt in np.sort(dt_el)[::-1]:
                extra = (np.maximum(1.0, (dt / dt_el) ** 2) - 1) * m_el
                if extra.sum() / m_el.sum() <= added_mass_limit:
                    break
        self.dt = float(dt)
        scale = np.maximum(1.0, (self.dt / dt_el) ** 2)
        self.added_mass = float(((scale - 1) * m_el).sum() / m_el.sum())
        self.true_mass = float(m_el.sum())
        self.mass = np.zeros(self.n)
        for a in range(4):
            np.add.at(self.mass, tets[:, a], scale * m_el / 4)
        self.grad = np.zeros((len(tets), 4, 3))  # 기준 형상함수 기울기
        self.grad[:, 1:] = dm_inv
        self.grad[:, 0] = -dm_inv.sum(axis=1)

    def internal_force(self) -> np.ndarray:
        t = self.tets
        xe = self.x[t]
        F = (xe[:, 1:] - xe[:, :1]).transpose(0, 2, 1) @ self.Dm_inv
        U, _, Vt = np.linalg.svd(F)
        R = U @ Vt
        neg = np.linalg.det(R) < 0
        if neg.any():
            U[neg, :, 2] *= -1
            R[neg] = U[neg] @ Vt[neg]
        Fc = R.transpose(0, 2, 1) @ F  # 회전을 뺀 변형
        eps = 0.5 * (Fc + Fc.transpose(0, 2, 1)) - np.eye(3)
        tr = np.trace(eps, axis1=1, axis2=2)
        dev = eps - tr[:, None, None] * np.eye(3) / 3
        bulk = self.lam + 2 * self.mu / 3
        s_tr = (2 * self.mu)[:, None, None] * (dev - self.eps_p)
        q = np.sqrt(1.5 * np.sum(s_tr * s_tr, axis=(1, 2)))
        over = q > self.yld
        s_dev = s_tr
        if over.any():  # J2 완전 탄소성: 반경 복귀
            ratio = np.ones_like(q)
            ratio[over] = self.yld[over] / q[over]
            s_dev = s_tr * ratio[:, None, None]
            self.eps_p[over] += (s_tr[over] - s_dev[over]) / (2 * self.mu[over])[:, None, None]
            self.peeq[over] += (q[over] - self.yld[over]) / (3 * self.mu[over])
        sig_c = s_dev + (bulk * tr)[:, None, None] * np.eye(3)
        self.vm = np.sqrt(1.5 * np.sum(s_dev * s_dev, axis=(1, 2)))
        sig = R @ sig_c @ R.transpose(0, 2, 1)
        grad_cur = self.grad @ np.linalg.inv(F)
        vol = self.vol0 * np.linalg.det(F)
        fe = -(grad_cur @ sig) * vol[:, None, None]
        f = np.zeros((self.n, 3))
        for a in range(4):
            np.add.at(f, t[:, a], fe[:, a])
        return f


def rotation(axis, deg):
    a = np.radians(deg)
    k = np.asarray(axis, float) / np.linalg.norm(axis)
    K_ = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(a) * K_ + (1 - np.cos(a)) * K_ @ K_


def nodal(n, tets, vol, values):
    total, weight = np.zeros(n), np.zeros(n)
    for a in range(4):
        np.add.at(total, tets[:, a], values * vol)
        np.add.at(weight, tets[:, a], vol)
    return total / np.maximum(weight, 1e-30)


def bar_mesh():
    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.occ.addBox(0, 0, 0, 100, 10, 10)
    gmsh.model.occ.synchronize()
    gmsh.option.setNumber("Mesh.MeshSizeMax", 3.0)
    gmsh.model.mesh.generate(3)
    tags, coords, _ = gmsh.model.mesh.getNodes()
    index = np.zeros(int(tags.max()) + 1, dtype=np.int64)
    index[tags.astype(np.int64)] = np.arange(len(tags))
    _, nodes = gmsh.model.mesh.getElementsByType(4)
    tets = index[nodes.astype(np.int64).reshape(-1, 4)]
    pts = coords.reshape(-1, 3)
    gmsh.finalize()
    return pts, tets


def check_wave():
    """막대 끝을 민 탄성파 속도 c=√(E/ρ)와, 탄성 충돌에서 운동 에너지가 늘지 않는지(안정성)."""
    pts, tets = bar_mesh()
    table = [(70000.0, 0.0, 2.7e-9, None)]
    mats = np.zeros(len(tets), dtype=np.int64)
    s = Explicit(pts, tets, mats, table, dt=1.2e-7)
    left = pts[:, 0] < 1e-6
    far = np.flatnonzero(pts[:, 0] > 95)
    arrive = None
    for step in range(int(40e-6 / s.dt)):
        f = s.internal_force()
        s.v += s.dt * f / s.mass[:, None]
        s.v[left] = [1000.0, 0, 0]
        s.x += s.dt * s.v
        if arrive is None and s.v[far, 0].mean() > 500.0:
            arrive = step * s.dt
    c = np.sqrt(70000.0 / 2.7e-9)
    print(f"탄성파: 측정 {97.5 / arrive:.0f} mm/s · 이론 {c:.0f} mm/s · 비 {(97.5 / arrive) / c:.3f}")
    s = Explicit(pts, tets, mats, table, dt=1.2e-7)
    s.v[:, 0] = -4000.0
    s.x[:, 0] += 0.2
    ke0 = 0.5 * (s.mass * (s.v ** 2).sum(axis=1)).sum()
    for _ in range(int(80e-6 / s.dt)):
        f = s.internal_force()
        contact = s.x[:, 0] < 0
        f[contact, 0] += -2e5 * s.x[contact, 0]
        s.v += s.dt * f / s.mass[:, None]
        s.x += s.dt * s.v
    ke1 = 0.5 * (s.mass * (s.v ** 2).sum(axis=1)).sum()
    print(f"탄성 충돌 운동 에너지: 전 {ke0:.1f} · 후 {ke1:.1f} (늘어나면 불안정) · 평균 속도 {s.v[:, 0].mean():.0f} mm/s(튀어 오름 +)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--size", type=float, default=3.0)
    ap.add_argument("--ms", type=float, default=2.8, help="해석 시간(ms)")
    a = ap.parse_args()
    if a.check:
        check_wave()
        return
    t0 = time.time()
    pts, tets, mats = build_phone(a.size)
    print(f"메쉬: 절점 {len(pts)} · 사면체 {len(tets)} · 재료별 {np.bincount(mats)} ({time.time() - t0:.0f}s)", flush=True)
    center = pts.mean(axis=0)
    R = rotation([1, 0, 0], 62) @ rotation([0, 1, 0], 24)  # 모서리가 먼저 닿도록 기울인다
    x = (pts - center) @ R.T
    x[:, 2] -= x[:, 2].min() - 0.01
    sim = Explicit(x, tets, mats, [AL, PLATE])
    drop_height = 1000.0  # mm
    v0 = np.sqrt(2 * G * drop_height)
    sim.v[:, 2] = -v0
    print(f"질량 {sim.true_mass * 1e6:.1f} g · 시간 간격 {sim.dt * 1e9:.0f} ns · 질량 보정 {sim.added_mass * 100:.2f}% · 충돌 속도 {v0 / 1000:.2f} m/s", flush=True)
    keep, tris = reindex(surface_of(sim.tets))
    steps = int(a.ms * 1e-3 / sim.dt)
    n_frames = 40
    every = max(1, steps // (n_frames - 1))
    frames_x, frames_vm, times, floor_force = [], [], [], []
    penalty = 3e5  # N/mm
    t1 = time.time()
    ke0 = 0.5 * (sim.mass * (sim.v ** 2).sum(axis=1)).sum()
    max_force, impulse = 0.0, 0.0
    for step in range(steps + 1):
        f = sim.internal_force()
        contact = sim.x[:, 2] < 0
        fc = -penalty * sim.x[contact, 2]
        f[contact, 2] += fc
        total_fc = float(fc.sum()) if contact.any() else 0.0
        max_force = max(max_force, total_fc)
        impulse += total_fc * sim.dt
        f[:, 2] -= sim.mass * G
        sim.v += sim.dt * f / sim.mass[:, None]
        sim.x += sim.dt * sim.v
        if step % every == 0:
            frames_x.append(sim.x[keep].copy())
            frames_vm.append(nodal(sim.n, sim.tets, sim.vol0, sim.vm)[keep])
            times.append(step * sim.dt * 1e3)
            floor_force.append(total_fc)
        if step % 2000 == 0:
            ke = 0.5 * (sim.mass * (sim.v ** 2).sum(axis=1)).sum()
            print(f"  {step}/{steps} t={step * sim.dt * 1e3:.2f}ms KE/KE0={ke / ke0:.3f} σmax={sim.vm.max():.0f} 소성 요소 {(sim.peeq > 1e-5).sum()} F={total_fc:.0f}N ({time.time() - t1:.0f}s)", flush=True)
            if not np.isfinite(ke) or ke > ke0 * 1.5:
                raise RuntimeError("해석이 발산했습니다")
    vcm = (sim.mass[:, None] * sim.v).sum(axis=0) / sim.mass.sum()
    dv = sim.mass.sum() * (vcm[2] + v0)
    print(f"끝: 무게중심 속도 {np.round(vcm / 1000, 3)} m/s · 최대 접촉력 {max_force:.0f} N · 충격량 {impulse:.3f} N·s · 운동량 변화 {dv:.3f} N·s · 비 {dv / impulse:.3f}", flush=True)
    frames = np.stack(frames_x)
    vm = np.stack(frames_vm)
    # 범례 상한: 모서리의 소성 응력에 맞추면 몸체에 퍼지는 응력파가 보이지 않는다. 상위 0.5%는 끝 색으로 둔다(최대값은 따로 표시)
    vmax = float(np.ceil(np.percentile(vm, 99.5) / 5) * 5)
    meta_steps = []
    for i in range(len(frames)):
        meta_steps.append({"t": round(times[i], 2), "max": round(float(vm[i].max()), 1), "force": round(floor_force[i], 0), "maxNode": int(np.argmax(vm[i]))})
    q, lo, span = quantize_positions(frames)
    meta = {
        "id": "drop", "kind": "낙하해석", "name": "스마트폰 모서리 낙하", "method": "명시적 동해석 · 탄소성 + 접촉", "element": "사면체", "elements": int(len(tets)),
        "nodes": int(len(pts)), "field": "von Mises", "unit": "MPa", "vmin": 0.0, "vmax": round(vmax, 0), "lo": lo, "span": span, "mode": "frames",
        "seconds": 13, "fall": 1.0, "yaw": [2.3, 0.02], "pitch": 0.2, "steps": meta_steps, "fit": 138, "digits": 2, "dy": 0.06,
        "center": [float(lo[0] + span[0] / 2), float(lo[1] + span[1] / 2), float(span[2] * 0.5)], "floor": True,
        "note": f"낙하 높이 1 m · 충돌 {v0 / 1000:.1f} m/s · 내부는 등가 판 · 범례 상한 초과는 빨강",
        "readout": [["시각", "t", "ms"], ["최대 응력", "max", "MPa"], ["바닥 접촉력", "force", "N"]],
    }
    save("drop", meta, {
        "tris": tris.astype(np.uint32), "feature": feature_edges(frames[0], tris).astype(np.uint32), "mesh": mesh_edges(tris).astype(np.uint32),
        "pos": q, "field": quantize_field(vm, 0.0, vmax),
    })
    print(f"완료 {time.time() - t0:.0f}s · 표면 절점 {len(keep)} · 프레임 {len(frames)}", flush=True)


if __name__ == "__main__":
    main()
