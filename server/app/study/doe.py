"""실험계획(DOE) 샘플링. 단위 초입방체에서 뽑아 변수 범위로 스케일한다."""
import numpy as np
from scipy.stats import qmc


def bounds_of(variables: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    lo = np.array([float(v["lower"]) for v in variables])
    hi = np.array([float(v["upper"]) for v in variables])
    return lo, hi


def sample(variables: list[dict], n: int, method: str = "lhs", seed: int | None = None) -> list[dict[str, float]]:
    d = len(variables)
    if method == "lhs":
        unit = qmc.LatinHypercube(d=d, seed=seed).random(n)
    elif method == "sobol":
        unit = qmc.Sobol(d=d, scramble=True, seed=seed).random(n)
    elif method == "random":
        unit = np.random.default_rng(seed).random((n, d))
    else:
        raise ValueError(f"지원하지 않는 DOE 방법: {method}")
    lo, hi = bounds_of(variables)
    pts = qmc.scale(unit, lo, hi)
    names = [v["name"] for v in variables]
    return [dict(zip(names, map(float, row))) for row in pts]
