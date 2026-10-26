"""획득함수 기반 다음 후보점 제안.
- bo: Expected Improvement (단일 목적함수) × 제약 만족 확률(제약 출력도 학습돼 있으면)
- al: 능동학습 — 예측 표준편차(불확실도)가 큰 지점
여러 점이 필요하면 이미 고른 점 근처를 거리 페널티로 피하는 단순 greedy 배치를 쓴다.
"""
import numpy as np
from scipy.stats import norm, qmc

from .surrogate import Surrogate


def expected_improvement(mu: np.ndarray, sigma: np.ndarray, best: float, xi: float = 0.01) -> np.ndarray:
    """최소화 기준 EI."""
    sigma = np.maximum(sigma, 1e-12)
    imp = best - mu - xi
    z = imp / sigma
    return imp * norm.cdf(z) + sigma * norm.pdf(z)


def suggest(
    surrogate: Surrogate,
    lo: np.ndarray,
    hi: np.ndarray,
    mode: str,
    n: int,
    objective: str | None = None,
    goal: str = "min",
    y_best: float | None = None,
    existing: np.ndarray | None = None,
    n_candidates: int = 4096,
    seed: int = 0,
    constraints: list[dict] | None = None,
) -> np.ndarray:
    d = len(lo)
    cand = qmc.scale(qmc.Sobol(d=d, scramble=True, seed=seed).random(n_candidates), lo, hi)
    mean, std = surrogate.predict(cand)

    if mode == "bo":
        key = objective or surrogate.outputs[0]
        sign = 1.0 if goal == "min" else -1.0
        score = expected_improvement(sign * mean[key], std[key], sign * y_best)
        for c in constraints or []:  # P(제약 만족)을 곱한다 (Gardner et al. 2014)
            mu, sd = mean[c["name"]], np.maximum(std[c["name"]], 1e-12)
            if c.get("upper") is not None:
                score = score * norm.cdf((c["upper"] - mu) / sd)
            if c.get("lower") is not None:
                score = score * norm.cdf((mu - c["lower"]) / sd)
    elif mode == "al":
        # 출력별 표준편차를 스케일 무관하게 합산
        score = sum(std[k] / (np.std(mean[k]) + 1e-12) for k in surrogate.outputs)
    else:
        raise ValueError(f"지원하지 않는 모드: {mode}")

    span = hi - lo
    picked: list[np.ndarray] = []
    taken = [] if existing is None else list(existing)
    score = np.nan_to_num(score.astype(float), nan=0.0)
    used = np.zeros(len(cand), dtype=bool)
    for _ in range(n):
        if taken:
            dist = np.min(np.linalg.norm((cand[:, None, :] - np.array(taken)[None]) / span, axis=2), axis=1)
            penalized = score * (1 - np.exp(-(dist / 0.05) ** 2))
        else:
            penalized = score.copy()
        penalized[used] = -np.inf
        i = int(np.argmax(penalized))
        picked.append(cand[i])
        taken.append(cand[i])
        used[i] = True
    return np.array(picked)
