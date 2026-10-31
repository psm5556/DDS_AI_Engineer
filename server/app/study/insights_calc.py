"""스터디 판단 도우미의 순수 계산. DB와 문장을 모르고 숫자만 다룬다(정답을 아는 시험이 이 파일을 직접 부른다).
판정과 쉬운 문장은 insights.py에 있다."""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from scipy.spatial.distance import cdist, pdist, squareform
from scipy.stats import hypergeom, qmc, spearmanr
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.model_selection import KFold


def sobol_unit(d: int, n: int, seed: int = 0) -> np.ndarray:
    """[0,1]^d의 고른 점 n개. 2의 거듭제곱으로 뽑아 균형을 지키고, 넘치는 만큼은 뒤에서 버린다."""
    return qmc.Sobol(d=d, scramble=True, seed=seed).random_base2(max(1, math.ceil(math.log2(n))))[:n]


# ---------- DOE: 고르게 퍼진 정도 ----------
def max_abs_corr(U: np.ndarray) -> tuple[float, int, int]:
    """변수쌍 중 가장 큰 |피어슨 상관|. 변수가 하나거나 값이 안 변하면 0."""
    d = U.shape[1]
    if d < 2 or len(U) < 3:
        return 0.0, 0, 0
    best = (0.0, 0, 0)
    sd = U.std(axis=0)
    for i in range(d):
        for j in range(i + 1, d):
            if sd[i] <= 1e-12 or sd[j] <= 1e-12:
                continue
            r = float(np.corrcoef(U[:, i], U[:, j])[0, 1])
            if abs(r) > abs(best[0]):
                best = (r, i, j)
    return best


@lru_cache(maxsize=256)
def doe_baseline(n: int, d: int) -> dict:
    """같은 크기의 표준 배치(LHS)와 무작위 배치를 30번씩 뽑아 잰 기준값. 같은 (n, d)면 언제나 같은 값이 나오게 씨앗을 고정한다."""
    seed = n * 1009 + d
    rng = np.random.default_rng(seed)
    lhs = [qmc.LatinHypercube(d=d, seed=seed + i).random(n) for i in range(30)]
    rnd = [rng.random((n, d)) for _ in range(30)]
    cd = lambda s: [float(qmc.discrepancy(u, method="CD")) for u in s]  # noqa: E731
    cd_lhs, cd_rnd = cd(lhs), cd(rnd)
    return {
        "cd_lhs_p90": float(np.percentile(cd_lhs, 90)),
        "cd_lhs_median": float(np.median(cd_lhs)),
        "cd_random_median": float(np.median(cd_rnd)),
        "cd_random_p90": float(np.percentile(cd_rnd, 90)),  # 무작위 배치도 절반은 중앙값을 넘는다. '무작위보다 나쁘다'는 말은 이 값을 넘을 때만 한다
        "dist_lhs_median": float(np.median([pdist(u).min() for u in lhs])) if n >= 2 else 0.0,
        "corr_lhs_p90": float(np.percentile([abs(max_abs_corr(u)[0]) for u in lhs], 90)),
    }


def largest_gap(column: np.ndarray, ends_only: bool = False) -> dict:
    """한 변수에서 가장 빈 구간. 0과 1도 끝점으로 본다. 정렬만 하므로 점이 많아도 빠르다.
    ends_only: 수준으로 나눈 변수는 수준 사이가 비는 것이 설계이므로 양 끝만 본다."""
    s = np.concatenate([[0.0], np.sort(column), [1.0]])
    gaps = np.diff(s)
    k = int(np.argmax(gaps)) if not ends_only else (0 if gaps[0] >= gaps[-1] else len(gaps) - 1)
    return {"gap": float(s[k + 1] - s[k]), "lo": float(s[k]), "hi": float(s[k + 1])}


def level_counts(U: np.ndarray, max_levels: int = 7) -> list[int | None]:
    """변수마다 정해진 값(수준)으로 나뉘었으면 수준 수, 아니면 None. 격자·요인 배치는 공간 충진 지표로 판단하면 안 된다."""
    out: list[int | None] = []
    for i in range(U.shape[1]):
        k = len(np.unique(np.round(U[:, i], 6)))
        out.append(k if 2 <= k <= max_levels and len(U) >= 2 * k else None)
    return out


def doe_stats(U: np.ndarray, levels: list[int | None] | None = None) -> dict:
    """U: [0,1]로 맞춘 점들 (n, d). 변수별 가장 빈 구간, 전체 고른 정도(CD), 최소 거리, 상관.
    거리 계산이 n²이라 많은 점은 부르는 쪽에서 줄여서 넘긴다."""
    n, d = U.shape
    gaps = [largest_gap(U[:, i], bool(levels and levels[i])) for i in range(d)]
    out = {"n": n, "d": d, "gaps": gaps, "cd": float(qmc.discrepancy(U, method="CD")) if n >= 2 else None,
           "min_dist": None, "min_pair": None}
    if n >= 2:
        dist = squareform(pdist(U))
        np.fill_diagonal(dist, np.inf)
        i, j = np.unravel_index(int(np.argmin(dist)), dist.shape)
        out["min_dist"], out["min_pair"] = float(dist[i, j]), [int(min(i, j)), int(max(i, j))]
    r, a, b = max_abs_corr(U)
    out["corr"] = {"r": float(r), "a": int(a), "b": int(b)}
    return out


# ---------- 분산 기반 영향도(Sobol) ----------
def saltelli(mean_fn, d: int, n: int = 1024, seed: int = 0) -> dict:
    """1차 지수 S와 전체 지수 T(Saltelli 2010, Jansen). mean_fn은 [0,1]^d 점을 받아 예측 평균을 돌려준다. 비용은 n(d+2)회 예측."""
    base = sobol_unit(2 * d, n, seed)
    A, B = base[:, :d], base[:, d:]
    fA, fB = mean_fn(A), mean_fn(B)
    var = float(np.var(np.concatenate([fA, fB])))
    S, T, fAB = np.zeros(d), np.zeros(d), []
    for i in range(d):
        ABi = A.copy()
        ABi[:, i] = B[:, i]
        f = mean_fn(ABi)
        fAB.append(f)
        if var > 0:
            S[i] = float(np.mean(fB * (f - fA)) / var)
            T[i] = float(0.5 * np.mean((fA - f) ** 2) / var)
    return {"S": S, "T": T, "var": var, "A": A, "B": B, "fA": fA, "fB": fB, "fAB": fAB}


def second_order(mean_fn, res: dict, i: int, j: int) -> float:
    """변수 i, j가 함께 만드는 몫. AB_j(j만 B)와 BA_i(i만 A)가 i(A값)·j(B값)을 함께 가지므로 두 예측의 곱이 {i,j}의 닫힌 지수를 준다."""
    if res["var"] <= 0:
        return 0.0
    BAi = res["B"].copy()
    BAi[:, i] = res["A"][:, i]
    v = float(np.mean(mean_fn(BAi) * res["fAB"][j]) - np.mean(res["fA"]) * np.mean(res["fB"]))
    return v / res["var"] - float(res["S"][i]) - float(res["S"][j])


def main_effect(predict_cov, d: int, i: int, grid: int = 21, others: int = 64, seed: int = 0) -> dict:
    """주효과 곡선: 변수 i를 움직이고 나머지는 범위 전체로 평균한 값과, 그 평균의 모델 불확실도(±1σ).
    predict_cov(X) → (평균, 공분산). 표본 평균의 표준편차라 sqrt(cov 합)/표본 수다."""
    rest = sobol_unit(d, others, seed)
    xs, mean, sd = np.linspace(0.0, 1.0, grid), [], []
    for g in xs:
        X = rest.copy()
        X[:, i] = g
        m, c = predict_cov(X)
        mean.append(float(np.mean(m)))
        sd.append(float(math.sqrt(max(float(np.sum(c)), 0.0)) / len(X)))
    return {"x": xs.tolist(), "mean": mean, "sd": sd}


def direction(x: list[float], m: list[float], mono: float = 0.8) -> dict:
    """곡선의 모양: 쭉 늘어나거나 줄어드는지, 가운데에 최소·최대가 있는지."""
    arr = np.asarray(m, dtype=float)
    span = float(arr.max() - arr.min())
    if span <= 0:
        return {"kind": "flat", "x": None}
    delta = float(arr[-1] - arr[0])
    if abs(delta) >= mono * span:
        return {"kind": "up" if delta > 0 else "down", "x": None}
    lo, hi = int(np.argmin(arr)), int(np.argmax(arr))
    interior_lo, interior_hi = 0 < lo < len(arr) - 1, 0 < hi < len(arr) - 1
    # 양쪽 끝보다 더 깊이 들어간 쪽을 고른다
    depth_lo = min(arr[0], arr[-1]) - arr[lo] if interior_lo else -1.0
    depth_hi = arr[hi] - max(arr[0], arr[-1]) if interior_hi else -1.0
    if depth_lo >= depth_hi:
        return {"kind": "min", "x": float(x[lo])}
    return {"kind": "max", "x": float(x[hi])}


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return 0.0
    r = spearmanr(a, b).statistic
    return 0.0 if r is None or not np.isfinite(r) else float(r)


def top_related(X: np.ndarray, y: np.ndarray, p_limit: float = 0.05) -> tuple[int | None, float]:
    """결과와 가장 함께 움직이는 변수와 그 순위 상관. 변수 수만큼 비교하므로 유의수준을 나눈다.
    잡음뿐이어도 '가장 큰 값'은 늘 있어서, 우연으로 설명되면 어떤 변수도 고르지 않는다."""
    rows = []
    for i in range(X.shape[1]):
        if len(y) < 4 or np.ptp(X[:, i]) == 0 or np.ptp(y) == 0:
            rows.append((0.0, 1.0))
            continue
        res = spearmanr(X[:, i], y)
        ok = np.isfinite(res.statistic) and np.isfinite(res.pvalue)
        rows.append((float(res.statistic), float(res.pvalue)) if ok else (0.0, 1.0))
    i = int(np.argmax([abs(r) for r, _ in rows]))
    return (i, rows[i][0]) if rows[i][1] * X.shape[1] < p_limit else (None, rows[i][0])


# ---------- 다목적 ----------
def non_dominated(S: np.ndarray) -> np.ndarray:
    """S: 작을수록 좋게 맞춘 값 (n, m). 돌려주는 값: 비지배 여부. 같은 값끼리는 서로 지배하지 않는다."""
    S = np.asarray(S, dtype=float)
    mask = np.zeros(len(S), dtype=bool)
    if not len(S):
        return mask
    # 사전순으로 보면 뒤의 점이 앞의 점을 지배할 수 없다. 앞쪽 비지배 점과만 비교하면 된다
    front: list[int] = []
    for i in np.lexsort(S.T[::-1]):
        if front:
            F = S[front]
            if np.any(np.all(F <= S[i], axis=1) & np.any(F < S[i], axis=1)):
                continue
        front.append(int(i))
    mask[front] = True
    return mask


def knee(F: np.ndarray, min_dist: float = 0.05) -> tuple[int | None, float]:
    """균형 후보의 위치와 거리. 앞면을 [0,1]로 맞춘 뒤 2목표면 양 끝을 잇는 선에서 이상점 쪽으로 가장 먼 점, 3목표 이상이면 이상점에 가장 가까운 점.
    2목표에서 뚜렷하게 튀어나온 점이 없으면(직선·오목) None."""
    F = np.asarray(F, dtype=float)
    if len(F) < 3:
        return None, 0.0
    span = np.ptp(F, axis=0)
    U = (F - F.min(axis=0)) / np.where(span > 0, span, 1.0)
    if F.shape[1] > 2:
        dist = np.linalg.norm(U, axis=1)
        k = int(np.argmin(dist))
        return k, float(dist[k])
    order = np.argsort(U[:, 0])
    p0, p1 = U[order[0]], U[order[-1]]
    line = p1 - p0
    norm = float(np.linalg.norm(line))
    if norm <= 0:
        return None, 0.0
    # 2차원 외적의 부호로 이상점(원점) 쪽만 잰다
    signed = ((U[:, 0] - p0[0]) * line[1] - (U[:, 1] - p0[1]) * line[0]) / norm
    toward = -float(((0 - p0[0]) * line[1] - (0 - p0[1]) * line[0]) / norm)
    signed = -signed if toward > 0 else signed
    k = int(np.argmax(signed))
    return (k, float(signed[k])) if signed[k] >= min_dist else (None, float(signed[k]))


def hypervolume(F: np.ndarray, ref: np.ndarray, samples: int = 2 ** 14, seed: int = 0) -> float:
    """기준점 ref까지 앞면이 덮는 부피(작을수록 좋은 값 기준). 2목표는 정확 계산, 3목표 이상은 몬테카를로."""
    F = np.asarray(F, dtype=float)
    ref = np.asarray(ref, dtype=float)
    F = F[np.all(F < ref, axis=1)] if len(F) else F
    if not len(F):
        return 0.0
    F = F[non_dominated(F)]
    if F.shape[1] == 2:
        F = F[np.argsort(F[:, 0])]
        area, prev = 0.0, float(ref[1])
        for a, b in F:
            if b < prev:
                area += (float(ref[0]) - float(a)) * (prev - float(b))
                prev = float(b)
        return area
    lo = F.min(axis=0)
    box = float(np.prod(ref - lo))
    pts = lo + qmc.Sobol(d=F.shape[1], scramble=True, seed=seed).random_base2(int(math.log2(samples))) * (ref - lo)
    hit = np.zeros(len(pts), dtype=bool)
    for f in F:
        hit |= np.all(pts >= f, axis=1)
    return box * float(hit.mean())


def tradeoff(Fa: np.ndarray, Fb: np.ndarray, at_a: float, delta: float) -> dict:
    """앞면 위 at_a에서 첫 값을 delta만큼 더 좋게(작게) 할 때 둘째 값이 얼마나 바뀌는가. 점 사이는 선형 보간이다.
    앞면 끝을 넘으면 끝점까지만 계산하고 within=False로 알린다."""
    order = np.argsort(Fa)
    xs, ys = np.asarray(Fa, dtype=float)[order], np.asarray(Fb, dtype=float)[order]
    target = at_a - delta
    within = bool(target >= xs[0])
    target = max(target, float(xs[0]))
    b_from, b_to = float(np.interp(at_a, xs, ys)), float(np.interp(target, xs, ys))
    return {"a_from": float(at_a), "a_to": float(target), "b_from": b_from, "b_to": b_to, "within": within}


# ---------- 능동학습 ----------
def fixed_gp(kernel) -> GaussianProcessRegressor:
    """학습 곡선은 최종 모델의 kernel을 고정해 다시 맞춘다. 점이 적을 때 다시 최적화하면 값이 크게 흔들린다(설계 측정 F5)."""
    return GaussianProcessRegressor(kernel=kernel, optimizer=None, normalize_y=True)


def _fold_error(X_tr, y_tr, X_te, y_te, kernel) -> np.ndarray:
    return fixed_gp(kernel).fit(X_tr, y_tr).predict(X_te) - y_te


def learning_curve(X_base, y_base, X_add, y_add, kernel, points: int = 6, folds: int = 5, seed: int = 0, tail: int = 0, same_tol: float = 0.01) -> list[dict]:
    """처음 점을 folds겹으로 나눠 가리고, 나머지 + 추가 점 앞 m건으로 학습해 가린 점을 채점한다. 같은 가린 점으로 재므로 m에 따른 변화를 비교할 수 있다.
    가린 점과 거의 같은 추가 점은 그 겹의 학습에서 뺀다. 복사본이 가린 점을 맞혀 정보 없는 추가가 큰 개선으로 보이는 일을 막는다.
    tail: 멈춤 조언이 '최근 tail건'을 말할 수 있게 그 지점도 잰다."""
    X_base, y_base, X_add, y_add = map(lambda a: np.asarray(a, dtype=float), (X_base, y_base, X_add, y_add))
    n_add = len(X_add)
    ms = {int(round(v)) for v in np.linspace(0, n_add, min(points, n_add + 1))}
    if tail and n_add > tail:
        ms.add(n_add - tail)
    split = list(KFold(min(folds, len(X_base)), shuffle=True, random_state=seed).split(X_base))
    keep = [cdist(X_add, X_base[te]).min(axis=1) > same_tol if n_add else np.zeros(0, dtype=bool) for _, te in split]
    out = []
    tot = float(np.sum((y_base - y_base.mean()) ** 2))
    for m in sorted(ms):
        err = np.zeros(len(y_base))
        for (tr, te), ok in zip(split, keep):
            use = np.where(ok[:m])[0]
            err[te] = _fold_error(np.vstack([X_base[tr], X_add[use]]), np.concatenate([y_base[tr], y_add[use]]), X_base[te], y_base[te], kernel)
        sse = float(np.sum(err ** 2))
        out.append({"m": m, "rmse": math.sqrt(sse / len(err)), "r2": 1 - sse / tot if tot > 0 else None, "err": err.tolist()})
    return out


def drop_interval(err_first, err_last, rounds: int = 2000, seed: int = 0, level: float = 0.9) -> tuple[float, float]:
    """오차 감소율의 구간. 같은 가린 점의 오차를 짝지어 점을 다시 뽑아 잰다. 가린 점이 적으면 몇 점의 운으로 평균 오차가 크게 움직이므로,
    이 구간이 0을 걸치면 줄었다고도 늘었다고도 말할 수 없다."""
    a, b = np.asarray(err_first, dtype=float) ** 2, np.asarray(err_last, dtype=float) ** 2
    pick = np.random.default_rng(seed).integers(0, len(a), size=(rounds, len(a)))
    first, last = a[pick].mean(axis=1), b[pick].mean(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        drops = np.where(first > 0, 1 - np.sqrt(last / first), 0.0)
    return float(np.percentile(drops, 50 * (1 - level))), float(np.percentile(drops, 50 * (1 + level)))


def uncertainty_change(X_before, y_before, X_after, y_after, kernel, Q) -> dict:
    """같은 kernel로 추가 전·후 모델의 불확실도(σ)를 같은 점들 Q에서 비교한다.
    normalize_y가 맞출 때마다 y 크기로 나누므로, 극단값이 추가되면 σ가 커져 보인다. 추가 후 y 크기 하나로 맞춰 비교한다."""
    before, after = fixed_gp(kernel).fit(X_before, y_before), fixed_gp(kernel).fit(X_after, y_after)
    scale = float(after._y_train_std)
    a = before.predict(Q, return_std=True)[1] / float(before._y_train_std) * scale
    b = after.predict(Q, return_std=True)[1]
    return {"mean_before": float(a.mean()), "mean_after": float(b.mean()),
            "p95_before": float(np.percentile(a, 95)), "p95_after": float(np.percentile(b, 95))}


# ---------- 결과 점검 ----------
def failure_cluster(U: np.ndarray, failed: np.ndarray, min_count: int = 3, share: float = 0.7, ratio: float = 2.0, p_limit: float = 0.05) -> dict | None:
    """변수를 3등분했을 때 실패가 한 구간에 몰린 곳. 조건: 실패 min_count건 이상, 그 구간에 실패의 share 이상, 그 구간 실패율이 전체의 ratio배 이상,
    우연으로 설명되지 않을 것(초기하 검정 × 비교한 구간 수). 구간을 많이 살피면 무작위 실패도 어딘가에는 몰려 보이기 때문이다."""
    total = int(failed.sum())
    if total < min_count or not len(U):
        return None
    rate_all = total / len(U)
    tests = 3 * U.shape[1]
    best = None
    for i in range(U.shape[1]):
        bins = np.minimum((U[:, i] * 3).astype(int), 2)
        for b in range(3):
            inside = bins == b
            hit = int(failed[inside].sum())
            if not inside.any() or hit < share * total:
                continue
            rate = hit / int(inside.sum())
            p = float(hypergeom.sf(hit - 1, len(U), int(inside.sum()), total)) * tests
            if p < p_limit and rate >= ratio * rate_all and (best is None or rate > best["rate"]):
                best = {"var": i, "bin": b, "lo": b / 3, "hi": (b + 1) / 3, "hit": hit, "total": total, "rate": rate, "p": min(p, 1.0)}
    return best


def outliers(y: np.ndarray, mad_limit: float = 6.0, cv_err: np.ndarray | None = None, cv_limit: float = 5.0, isolated: float = 0.5,
             eligible: np.ndarray | None = None) -> list[int]:
    """튀는 값의 위치. MAD만 보면 꼬리가 긴 정상 출력을 잘못 잡으므로 다음 값과 뚝 떨어져 있어야 하고, 모델이 있으면 교차검증 오차도 커야 한다.
    eligible이 False인 점(범위 끝)은 빼고 본다. 그 값은 다른 점으로 맞춰 볼 수 없어 오차가 커도 오류라고 할 수 없다(외팔보 측정)."""
    y = np.asarray(y, dtype=float)
    if len(y) < 5:
        return []
    med = float(np.median(y))
    mad = float(np.median(np.abs(y - med)))
    if mad <= 0:
        return []
    far = np.where(np.abs(y - med) / (1.4826 * mad) > mad_limit)[0]
    if np.all(y > 0):  # 처짐(1/h³)처럼 양수이고 꼬리가 긴 출력은 로그로 봐도 튀어야 한다. 그래야 정상 값을 오류로 의심하지 않는다
        ln = np.log(y)
        ln_mad = float(np.median(np.abs(ln - np.median(ln))))
        far = far if ln_mad <= 0 else far[np.abs(ln[far] - np.median(ln)) / (1.4826 * ln_mad) > mad_limit]
    picked = []
    for i in far:
        if eligible is not None and not eligible[i]:
            continue
        others = np.delete(y, i)
        toward = others[others < y[i]] if y[i] > med else others[others > y[i]]
        if not len(toward) or np.min(np.abs(toward - y[i])) < isolated * abs(y[i] - med):
            continue
        if cv_err is not None:
            typical = float(np.median(np.abs(cv_err)))
            if typical > 0 and abs(cv_err[i]) <= cv_limit * typical:
                continue
        picked.append(int(i))
    return picked
