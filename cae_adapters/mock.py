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


def box_surface(x: tuple, y: tuple, z: tuple, n: tuple):
    """직육면체 표면 삼각형 메쉬. n=(nx, ny, nz) 분할 수가 같으면 치수가 달라도 토폴로지가 같다."""
    (x0, x1), (y0, y1), (z0, z1), (nx, ny, nz) = x, y, z, n
    pts, b = beam_mesh(1.0, 1.0, 1.0, nx, ny, nz)  # 단위 상자(x∈[0,1], y·z∈[-.5,.5])를 늘린다
    pts = pts * [x1 - x0, y1 - y0, z1 - z0] + [x0, (y0 + y1) / 2, (z0 + z1) / 2]
    return pts, b


# 브래킷: 폭 W·길이 L 플레이트(두께 thickness) 위 가운데에 리브(두께 RIB_T, 높이 rib_height). x=0 벽 고정, x=L 끝단 하중 P.
BRACKET = {"L": 200.0, "W": 60.0, "RIB_T": 6.0, "P": 2000.0, "E": 210e3, "rho": 7.85e-6}


def bracket_section(t: float, r: float):
    """T단면 성질: 면적, 도심 높이 zc, 단면 2차 모멘트 I (z는 플레이트 바닥 기준)."""
    W, tr = BRACKET["W"], BRACKET["RIB_T"]
    a1, a2 = W * t, tr * r
    zc = (a1 * t / 2 + a2 * (t + r / 2)) / (a1 + a2)
    inertia = W * t ** 3 / 12 + a1 * (t / 2 - zc) ** 2 + tr * r ** 3 / 12 + a2 * (t + r / 2 - zc) ** 2
    return a1 + a2, zc, inertia


def bracket_mesh(t: float, r: float):
    """플레이트 + 리브 표면 메쉬(두 상자). 모든 설계에서 같은 토폴로지."""
    L, W, tr = BRACKET["L"], BRACKET["W"], BRACKET["RIB_T"]
    p1, t1 = box_surface((0, L), (-W / 2, W / 2), (0, t), (40, 12, 2))
    p2, t2 = box_surface((0, L), (-tr / 2, tr / 2), (t, t + r), (40, 2, 6))
    return np.vstack([p1, p2]), np.vstack([t1, t2 + len(p1)])


def _bracket(p):
    t, r = p["thickness"], p["rib_height"]
    L, P, E, rho = BRACKET["L"], BRACKET["P"], BRACKET["E"], BRACKET["rho"]
    area, zc, inertia = bracket_section(t, r)
    return {
        "mass": rho * area * L * 1000,  # g
        "max_stress": P * L * max(zc, t + r - zc) / inertia,  # MPa
        "deflection": P * L ** 3 / (3 * E * inertia),  # mm
    }


def _bracket3d(p):
    """브래킷 3D: 스칼라 결과 + 표면 Mises(굽힘) 응력 필드, 처짐 형상(10배 확대) 포함."""
    t, r = p["thickness"], p["rib_height"]
    L, P, E = BRACKET["L"], BRACKET["P"], BRACKET["E"]
    _, zc, inertia = bracket_section(t, r)
    pts, tris = bracket_mesh(t, r)
    x, z = pts[:, 0], pts[:, 2]
    stress = np.abs(P * (L - x) * (z - zc) / inertia)
    defl = P * x ** 2 * (3 * L - x) / (6 * E * inertia)
    shown = pts.copy()
    shown[:, 2] -= 10 * defl
    return _bracket(p), {"stress": {"points": shown, "values": stress, "triangles": tris}}


def _washer(p):
    """드럼세탁기 탈수 진동(예시 식): 스프링 강성·댐퍼 감쇠력·밸런서 질량 변화 [%], 스프링 위치 [mm], 회전수 rpm(기본 1200).
    현재 설계(모두 0)는 진동 4.2 mm, 최적(스프링 +12%, 댐퍼 +25%)은 2.6 mm. 회전수가 오르면 진동과 최적 값이 함께 오른다
    — docs/briefing 슬라이드의 데모 숫자와 같은 식이다."""
    s = (p.get("rpm", 1200.0) - 1200.0) / 200.0
    dx = (p["spring_k"] - (12 + 6 * s)) / 37.5
    dy = (p["damper_c"] - (25 + 8 * s)) / 65.8
    db = (p.get("balancer_m", 5.0) - 5.0) / 25.0
    dp = (p.get("spring_pos", 5.0) - 5.0) / 20.0
    return {"vibration": 2.6 + 0.3 * s + 3.67 * (1 + 0.25 * s) * (2 * dx * dx + 1.6 * dy * dy) + 0.3 * db * db + 0.15 * dp * dp}


FUNCTIONS = {
    "branin": (_branin, "2변수 x1∈[-5,10], x2∈[0,15], 출력 f (최소 0.398)"),
    "rosenbrock": (_rosenbrock, "n변수, 출력 f (최소 0 @ 모두 1)"),
    "beam": (_beam, "변수 width, height [mm], 출력 mass, deflection, max_stress"),
    "bracket": (_bracket, "브래킷(플레이트+리브) 변수 thickness 4~12, rib_height 5~30 [mm], 출력 mass[g], max_stress[MPa], deflection[mm]"),
    "bracket3d": (_bracket3d, "bracket과 같고 + 3D 표면 응력 필드(fields/stress.npz, 처짐 10배 확대) — 형상·필드 학습"),
    "beam3d": (_beam3d, "beam과 같고 + 3D 표면 메쉬 응력 필드(fields/stress.npz, 처짐 형상 포함) — 형상·필드 학습 연습"),
    "washer": (_washer, "드럼세탁기 탈수 진동 변수 spring_k -10~30, damper_c -10~50, balancer_m -20~20 [%], spring_pos -20~20 [mm], "
                        "rpm(선택, 기본 1200), 출력 vibration[mm]"),
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
