"""획득함수 기반 다음 후보점 제안.
- bo: Expected Improvement (단일 목적함수) × 제약 만족 확률(제약 출력도 학습돼 있으면)
- mo: 목적 2개 이상. 후보마다 다른 가중치로 묶은 값의 기대 개선(몬테카를로 Chebyshev EI) × 제약 만족 확률
- al: 능동학습 — 예측 표준편차(불확실도)가 큰 지점
여러 점이 필요하면 이미 고른 점 근처를 거리 페널티로 피하는 단순 greedy 배치를 쓴다.
"""
import numpy as np
from scipy.stats import norm, qmc

from .insights_calc import non_dominated
from .surrogate import Surrogate


def expected_improvement(mu: np.ndarray, sigma: np.ndarray, best: float, xi: float = 0.01) -> np.ndarray:
    """최소화 기준 EI."""
    sigma = np.maximum(sigma, 1e-12)
    imp = best - mu - xi
    z = imp / sigma
    return imp * norm.cdf(z) + sigma * norm.pdf(z)


def constraint_probability(mean: dict, std: dict, constraints: list[dict] | None, size: int) -> np.ndarray:
    """제약을 모두 만족할 확률(Gardner et al. 2014). 판단 도우미도 같은 값을 쓴다."""
    p = np.ones(size)
    for c in constraints or []:
        mu, sd = mean[c["name"]], np.maximum(std[c["name"]], 1e-12)
        if c.get("upper") is not None:
            p = p * norm.cdf((c["upper"] - mu) / sd)
        if c.get("lower") is not None:
            p = p * norm.cdf((mu - c["lower"]) / sd)
    return p


def chebyshev_ei(mean: dict, std: dict, objectives: list[dict], observed: np.ndarray, seed: int = 0, samples: int = 64) -> np.ndarray:
    """두 목표 이상의 기대 개선. observed: 이미 해석한 값 (n, m), 원래 부호.
    후보마다 가중치를 Dirichlet로 뽑아 앞면의 여러 곳을 살핀다. 단위가 달라도 되도록 해석값 범위로 맞춘다."""
    signs = np.array([1.0 if o.get("goal", "min") == "min" else -1.0 for o in objectives])
    mu = np.column_stack([signs[i] * mean[o["name"]] for i, o in enumerate(objectives)])
    sd = np.column_stack([np.maximum(std[o["name"]], 1e-12) for o in objectives])
    seen = observed * signs
    seen = seen[non_dominated(seen)]  # 가중합의 최솟값은 언제나 앞면 위에 있으므로 앞면만 보면 된다
    low = (observed * signs).min(axis=0)
    span = np.maximum(np.ptp(observed * signs, axis=0), 1e-12)
    rng = np.random.default_rng(seed)
    w = rng.dirichlet(np.ones(mu.shape[1]), size=len(mu))
    scalar = lambda v: np.max(w * v, axis=-1) + 0.05 * np.sum(w * v, axis=-1)  # noqa: E731
    best = np.min(np.max(w[:, None, :] * ((seen - low) / span)[None], axis=2) + 0.05 * np.sum(w[:, None, :] * ((seen - low) / span)[None], axis=2), axis=1)
    gain = np.zeros(len(mu))
    for z in rng.standard_normal((samples, mu.shape[1])):
        gain += np.maximum(best - scalar((mu + sd * z - low) / span), 0.0)
    return gain / samples


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
    objectives: list[dict] | None = None,
    observed: np.ndarray | None = None,
) -> np.ndarray:
    d = len(lo)
    cand = qmc.scale(qmc.Sobol(d=d, scramble=True, seed=seed).random(n_candidates), lo, hi)
    mean, std = surrogate.predict(cand)

    if mode == "bo":
        key = objective or surrogate.outputs[0]
        sign = 1.0 if goal == "min" else -1.0
        # xi를 출력 크기에 맞춘다. 절댓값으로 두면 m·변형률처럼 값이 작은 출력에서 EI가 모두 0이 되어 임의의 점을 고른다
        score = expected_improvement(sign * mean[key], std[key], sign * y_best, xi=0.01 * float(np.std(mean[key])))
        score = score * constraint_probability(mean, std, constraints, len(cand))
    elif mode == "mo":
        score = chebyshev_ei(mean, std, objectives or [], observed, seed=seed)
        score = score * constraint_probability(mean, std, constraints, len(cand))
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
