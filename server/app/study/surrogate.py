"""Gaussian Process 기반 Surrogate. 출력별로 GP 하나씩 학습하고 joblib으로 저장한다."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel
from sklearn.metrics import mean_squared_error, r2_score
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import MinMaxScaler


def _make_gp(d: int):
    # 입력은 MinMaxScaler로 [0,1]이므로 length scale을 그 척도에 맞게 제한해 과적합(길이척도 붕괴)을 막는다.
    # 점 사이 거리는 √d로 커진다. 0.3으로 시작하면 약 48차원부터 모든 점이 서로 무관해져 최적화가 움직이지 못하고 평균만 내는 모델이 된다(측정: 48입력 R² -0.01 → 1.00)
    start = max(0.3, 0.3 * np.sqrt(d / 2))
    kernel = (ConstantKernel(1.0, (1e-3, 1e3)) * Matern(length_scale=np.full(d, start), length_scale_bounds=(1e-2, 1e2 if d <= 20 else 1e3), nu=2.5)
              + WhiteKernel(1e-6, (1e-10, 1e-1)))
    gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, n_restarts_optimizer=3 if d <= 20 else 1, random_state=0)
    return make_pipeline(MinMaxScaler(), gp)


def space_filling_subset(X: np.ndarray, k: int) -> np.ndarray:
    """입력 공간을 고르게 덮는 행 k개의 위치(먼 점부터 고르는 방식). 무작위가 아니라 같은 표면 같은 모델이 나온다.
    변수마다 최소·최대 행을 먼저 넣어 슬라이더 양 끝이 외삽이 되지 않게 한다."""
    n = len(X)
    if k >= n:
        return np.arange(n)
    lo, hi = X.min(axis=0), X.max(axis=0)
    Z = (X - lo) / np.where(hi > lo, hi - lo, 1.0)
    chosen = list(dict.fromkeys(int(i) for i in np.concatenate([Z.argmin(axis=0), Z.argmax(axis=0)])))[:k]
    dist = np.min(np.linalg.norm(Z[:, None, :] - Z[chosen][None, :, :], axis=2), axis=1)
    while len(chosen) < k:
        i = int(np.argmax(dist))
        chosen.append(i)
        dist = np.minimum(dist, np.linalg.norm(Z - Z[i], axis=1))
    return np.array(sorted(chosen))


@dataclass
class Surrogate:
    variables: list[str]
    outputs: list[str]
    models: dict = field(default_factory=dict)
    bounds: tuple[list[float], list[float]] | None = None

    def fit(self, X: np.ndarray, Y: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
        """학습 후 교차검증 지표(R², RMSE)를 돌려준다."""
        metrics = {}
        for name in self.outputs:
            # 이 출력의 값이 있는 행만 쓴다. 값이 빈 행이 다른 출력의 학습까지 줄이면 안 된다
            keep = np.isfinite(Y[name])
            Xo, y = X[keep], Y[name][keep]
            k = min(5, len(Xo))
            if k >= 3:
                pred = cross_val_predict(_make_gp(X.shape[1]), Xo, y, cv=KFold(k, shuffle=True, random_state=0))
                metrics[name] = {
                    "r2": float(r2_score(y, pred)),
                    "rmse": float(np.sqrt(mean_squared_error(y, pred))),
                    "cv_pred": pred.tolist(),
                    "actual": y.tolist(),
                }
            self.models[name] = _make_gp(X.shape[1]).fit(Xo, y)
        return metrics

    def predict(self, X: np.ndarray) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        mean, std = {}, {}
        for name, pipe in self.models.items():
            scaler, gp = pipe[0], pipe[-1]
            m, s = gp.predict(scaler.transform(X), return_std=True)
            mean[name], std[name] = m, s
        return mean, std

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: str | Path) -> "Surrogate":
        return joblib.load(path)
