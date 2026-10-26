"""라이선스 없이 전체 흐름을 검증하기 위한 가짜 솔버(해석함수)."""
import math
import time

import numpy as np

from .base import CaeAdapter, CaseOutcome, write_json


def _branin(p):
    x1, x2 = p["x1"], p["x2"]
    a, b, c, r, s, t = 1, 5.1 / (4 * math.pi ** 2), 5 / math.pi, 6, 10, 1 / (8 * math.pi)
    return {"f": a * (x2 - b * x1 ** 2 + c * x1 - r) ** 2 + s * (1 - t) * math.cos(x1) + s}


def _rosenbrock(p):
    xs = [p[k] for k in sorted(p)]
    return {"f": sum(100 * (xs[i + 1] - xs[i] ** 2) ** 2 + (1 - xs[i]) ** 2 for i in range(len(xs) - 1))}


def _beam(p):
    """외팔보: 폭 width, 높이 height [mm], 길이 1000mm, 끝단 하중 1000N, 강재."""
    b, h, L, P, E, rho = p["width"], p["height"], 1000.0, 1000.0, 210e3, 7.85e-6
    inertia = b * h ** 3 / 12
    return {
        "mass": rho * b * h * L,  # kg
        "deflection": P * L ** 3 / (3 * E * inertia),  # mm
        "max_stress": 6 * P * L / (b * h ** 2),  # MPa
    }


def beam_mesh(b: float, h: float, L: float = 1000.0, nx: int = 40, ny: int = 6, nz: int = 8):
    """외팔보 표면 삼각형 메쉬(직육면체 6면, 모든 케이스 같은 토폴로지). x=0 고정, x=L 끝단 하중."""
    pts, tris, index = [], [], {}

    def node(i, j, k):
        key = (i, j, k)
        if key not in index:
            index[key] = len(pts)
            pts.append((L * i / nx, b * (j / ny - 0.5), h * (k / nz - 0.5)))
        return index[key]

    def face(fixed, a_n, b_n, mk):
        for a in range(a_n):
            for c in range(b_n):
                p00, p10, p01, p11 = mk(a, c), mk(a + 1, c), mk(a, c + 1), mk(a + 1, c + 1)
                tris.extend([(p00, p10, p11), (p00, p11, p01)])

    face("z0", nx, ny, lambda a, c: node(a, c, 0))
    face("z1", nx, ny, lambda a, c: node(a, c, nz))
    face("y0", nx, nz, lambda a, c: node(a, 0, c))
    face("y1", nx, nz, lambda a, c: node(a, ny, c))
    face("x0", ny, nz, lambda a, c: node(0, a, c))
    face("x1", ny, nz, lambda a, c: node(nx, a, c))
    return np.array(pts, dtype=np.float64), np.array(tris, dtype=np.int32)


def _beam3d(p):
    """3D 외팔보: 스칼라 결과 + 표면 응력 필드(fields/stress.npz) — 형상·필드 학습 연습용."""
    b, h, L, P, E = p["width"], p["height"], 1000.0, 1000.0, 210e3
    pts, tris = beam_mesh(b, h, L)
    inertia = b * h ** 3 / 12
    x, z = pts[:, 0], pts[:, 2]
    stress = np.abs(P * (L - x) * z / inertia)  # 굽힘 응력 |M z / I|
    defl = P * x ** 2 * (3 * L - x) / (6 * E * inertia)  # 처짐 w(x)
    shown = pts.copy()
    shown[:, 2] -= defl  # 변형 형상(처짐)을 좌표에 반영
    res = _beam(p)
    return res, {"stress": {"points": shown, "values": stress, "triangles": tris}}


FUNCTIONS = {
    "branin": (_branin, "2변수 x1∈[-5,10], x2∈[0,15], 출력 f (최소 0.398)"),
    "rosenbrock": (_rosenbrock, "n변수, 출력 f (최소 0 @ 모두 1)"),
    "beam": (_beam, "변수 width, height [mm], 출력 mass, deflection, max_stress"),
    "beam3d": (_beam3d, "beam과 같고 + 3D 표면 메쉬 응력 필드(fields/stress.npz, 처짐 형상 포함) — 형상·필드 학습 연습"),
}


class MockAdapter(CaeAdapter):
    name = "mock"
    label = "Mock 솔버"
    description = "해석함수 기반 가짜 솔버. " + " / ".join(f"{k}: {v[1]}" for k, v in FUNCTIONS.items())
    config_example = {"function": "branin", "delay_sec": 0.2, "noise": 0.0}

    def run_case(self, params, run_dir, config, runner, tools, dry_run=False, **_):
        fn_name = config.get("function", "branin")
        if fn_name not in FUNCTIONS:
            return CaseOutcome("failed", error=f"알 수 없는 함수: {fn_name}")
        run_dir.mkdir(parents=True, exist_ok=True)
        write_json(run_dir / "params.json", params)
        if dry_run:
            return CaseOutcome("dry_run", files=["params.json"])
        time.sleep(float(config.get("delay_sec", 0)))
        try:
            results = FUNCTIONS[fn_name][0](params)
        except KeyError as e:
            return CaseOutcome("failed", error=f"설계변수 {e} 가 필요합니다")
        if isinstance(results, tuple):  # (스칼라 결과, 필드) — 필드는 fields/<이름>.npz 규약으로 저장
            results, fields = results
            (run_dir / "fields").mkdir(exist_ok=True)
            for name, f in fields.items():
                np.savez_compressed(run_dir / "fields" / f"{name}.npz", **f)
        noise = float(config.get("noise", 0))
        if noise:
            rng = np.random.default_rng()
            results = {k: v * (1 + noise * rng.standard_normal()) for k, v in results.items()}
        write_json(run_dir / "results.json", results)
        return CaseOutcome("complete", results=results, files=["params.json", "results.json"])
