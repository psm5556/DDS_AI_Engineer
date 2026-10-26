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
    # 입력은 MinMaxScaler로 [0,1]이므로 length scale을 그 척도에 맞게 제한해 과적합(길이척도 붕괴)을 막는다
    kernel = (ConstantKernel(1.0, (1e-3, 1e3)) * Matern(length_scale=np.full(d, 0.3), length_scale_bounds=(1e-2, 1e2), nu=2.5)
              + WhiteKernel(1e-6, (1e-10, 1e-1)))
    gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, n_restarts_optimizer=3, random_state=0)
    return make_pipeline(MinMaxScaler(), gp)


@dataclass
class Surrogate:
    variables: list[str]
    outputs: list[str]
    models: dict = field(default_factory=dict)
    bounds: tuple[list[float], list[float]] | None = None

    def fit(self, X: np.ndarray, Y: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
        """학습 후 교차검증 지표(R², RMSE)를 돌려준다."""
        metrics = {}
        k = min(5, len(X))
        for name in self.outputs:
            y = Y[name]
            if k >= 3:
                pred = cross_val_predict(_make_gp(X.shape[1]), X, y, cv=KFold(k, shuffle=True, random_state=0))
                metrics[name] = {
                    "r2": float(r2_score(y, pred)),
                    "rmse": float(np.sqrt(mean_squared_error(y, pred))),
                    "cv_pred": pred.tolist(),
                    "actual": y.tolist(),
                }
            self.models[name] = _make_gp(X.shape[1]).fit(X, y)
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
