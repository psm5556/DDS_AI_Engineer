"""형상·필드 학습 (Reduced-Order Model): 케이스마다 남긴 메쉬 필드 스냅샷으로 '설계변수 → 3D 형상 + 필드'를 예측한다.

입력: 케이스별 fields/<field>.npz
  points    (N, 3)  float  — 노드 좌표 (형상이 바뀌면 케이스마다 다름, 노드 수·순서는 같아야 함)
  values    (N,) 또는 (N, 3) — 노드 값 (응력, 변위 벡터 등)
  triangles (M, 3)  int    — 표면 삼각형 (가시화용, 모든 케이스 동일 토폴로지)
학습: 필드 값 스냅샷과 형상(좌표) 스냅샷을 각각 PCA로 압축하고(에너지 99.9% 또는 최대 20모드)
      모드 계수를 설계변수에 대해 GP(Surrogate)로 학습한다.
예측: 설계변수 → 모드 계수 → 필드(+형상) 복원.
리메쉬: 노드 수가 케이스마다 다르면 가장 촘촘한 케이스의 메쉬로 자동 보간한다(align_to_reference). 위상이 바뀌는 형상 변화에는 맞지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
from sklearn.decomposition import PCA

from .surrogate import Surrogate


def load_snapshot(path: Path) -> dict:
    z = np.load(path)
    out = {"points": np.asarray(z["points"], dtype=np.float64), "values": np.asarray(z["values"], dtype=np.float64)}
    if "triangles" in z:
        out["triangles"] = np.asarray(z["triangles"], dtype=np.int32)
    return out


@dataclass
class Basis:
    """스냅샷 행렬의 PCA 기저(평균 + 모드). 필드 값과 형상(좌표)에 따로 둔다 — 크기가 다른 두 신호를 한 PCA에 넣으면
    작은 쪽(예: 폭 변화)이 절단되어 사라지기 때문."""
    mean: np.ndarray
    pca: PCA
    energy: float

    @staticmethod
    def fit(S: np.ndarray, energy: float, max_modes: int) -> tuple["Basis", np.ndarray]:
        mean = S.mean(axis=0)
        n_max = int(max(1, min(max_modes, S.shape[0] - 1, S.shape[1])))
        full = PCA(n_components=n_max).fit(S - mean)
        cum = np.cumsum(full.explained_variance_ratio_)
        k = int(min(n_max, np.searchsorted(cum, energy) + 1))
        pca = PCA(n_components=k).fit(S - mean)
        return Basis(mean, pca, float(cum[k - 1])), pca.transform(S - mean)

    def inverse(self, c: np.ndarray) -> np.ndarray:
        return self.pca.inverse_transform(c) + self.mean

    def error(self, S: np.ndarray) -> float:
        rec = self.inverse(self.pca.transform(S - self.mean))
        return float(np.linalg.norm(rec - S) / (np.linalg.norm(S - self.mean) or 1))


@dataclass
class FieldROM:
    variables: list[str]
    field_name: str
    n_nodes: int
    value_dim: int  # 1(스칼라) 또는 3(벡터)
    shape_varies: bool
    triangles: np.ndarray | None = None
    ref_points: np.ndarray | None = None  # 형상이 고정일 때 쓰는 좌표
    val_basis: Basis | None = None
    geo_basis: Basis | None = None
    sur: Surrogate | None = None
    info: dict = field(default_factory=dict)

    def fit(self, X: np.ndarray, snaps: list[dict], energy: float = 0.999, max_modes: int = 20) -> dict:
        V = np.vstack([s["values"].ravel() for s in snaps])
        self.val_basis, cv = Basis.fit(V, energy, max_modes)
        outs = {f"v{i + 1}": cv[:, i] for i in range(cv.shape[1])}
        info = {"value_modes": cv.shape[1], "value_energy": self.val_basis.energy, "value_error": self.val_basis.error(V)}
        if self.shape_varies:
            G = np.vstack([s["points"].ravel() for s in snaps])
            self.geo_basis, cg = Basis.fit(G, energy, max_modes)
            outs.update({f"g{i + 1}": cg[:, i] for i in range(cg.shape[1])})
            info.update(geometry_modes=cg.shape[1], geometry_energy=self.geo_basis.energy, geometry_error=self.geo_basis.error(G))
        self.sur = Surrogate(variables=self.variables, outputs=list(outs))
        metrics = self.sur.fit(X, outs)
        self.info = {**info, "modes": len(outs), "energy": info["value_energy"], "truncation_error": info["value_error"],
                     "n_train": len(snaps), "mode_r2": {n: round(m["r2"], 4) for n, m in metrics.items()}}
        return self.info

    def predict(self, x: dict[str, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """→ (points (N,3), values (N,) 또는 (N,3), 불확실도(N,) — 모드 표준편차를 노드로 전파한 근사)"""
        X = np.array([[x[v] for v in self.variables]])
        mean, std = self.sur.predict(X)
        kv = self.val_basis.pca.n_components_
        cv = np.array([[mean[f"v{i + 1}"][0] for i in range(kv)]])
        sv = np.array([std[f"v{i + 1}"][0] for i in range(kv)])
        values = self.val_basis.inverse(cv)[0].reshape(self.n_nodes, self.value_dim)
        unc = np.sqrt(((self.val_basis.pca.components_ * sv[:, None]) ** 2).sum(axis=0)).reshape(self.n_nodes, self.value_dim)
        unc = np.linalg.norm(unc, axis=1)
        if self.shape_varies:
            kg = self.geo_basis.pca.n_components_
            cg = np.array([[mean[f"g{i + 1}"][0] for i in range(kg)]])
            points = self.geo_basis.inverse(cg)[0].reshape(self.n_nodes, 3)
        else:
            points = self.ref_points
        return points, (values[:, 0] if self.value_dim == 1 else values), unc

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: str | Path) -> "FieldROM":
        return joblib.load(path)


def _normalized(points: np.ndarray) -> np.ndarray:
    """각 형상을 자기 경계상자 [0,1]^3로 정규화 — 형상이 달라도 '같은 상대 위치'끼리 대응시키기 위함."""
    lo, hi = points.min(axis=0), points.max(axis=0)
    span = np.where(hi - lo > 1e-12, hi - lo, 1.0)
    return (points - lo) / span


def align_to_reference(snaps: list[dict], k: int = 4) -> tuple[list[dict], int]:
    """리메쉬로 노드 수·순서가 케이스마다 다를 때, 기준 메쉬(삼각형이 있는 가장 촘촘한 케이스)의 노드 위치로
    좌표와 값을 보간한다(경계상자 정규화 좌표에서 k-최근접 역거리 가중). 반환: (정렬된 스냅샷, 기준 순번)."""
    from scipy.spatial import cKDTree

    cand = [i for i, s in enumerate(snaps) if s.get("triangles") is not None] or list(range(len(snaps)))
    ref_i = max(cand, key=lambda i: snaps[i]["points"].shape[0])
    ref = snaps[ref_i]
    u_ref = _normalized(ref["points"])
    out = []
    for i, s in enumerate(snaps):
        if i == ref_i or (s["points"].shape == ref["points"].shape and np.allclose(_normalized(s["points"]), u_ref, atol=1e-6)):
            out.append({**s, "triangles": ref.get("triangles")})
            continue
        d, idx = cKDTree(_normalized(s["points"])).query(u_ref, k=min(k, s["points"].shape[0]))
        d, idx = d.reshape(len(u_ref), -1), idx.reshape(len(u_ref), -1)  # k=1이면 1차원으로 오므로 맞춘다
        w = 1.0 / np.maximum(d, 1e-12) ** 2
        w /= w.sum(axis=1, keepdims=True)
        vals = s["values"].reshape(s["points"].shape[0], -1)
        out.append({
            "points": np.einsum("nk,nkd->nd", w, s["points"][idx]),
            "values": np.einsum("nk,nkd->nd", w, vals[idx]).reshape((-1,) + s["values"].shape[1:]),
            "triangles": ref.get("triangles"),
        })
    return out, ref_i


def train_field_rom(variables: list[str], field_name: str, X: np.ndarray, snaps: list[dict]) -> FieldROM:
    n = snaps[0]["points"].shape[0]
    remeshed = any(s["points"].shape[0] != n for s in snaps)
    if any(s["values"].shape[0] != s["points"].shape[0] for s in snaps):
        raise ValueError("values의 개수가 points(노드 수)와 다른 케이스가 있습니다. 요소 값은 노드로 평균해서 저장하세요")
    if remeshed:  # 리메쉬 → 기준 메쉬로 보간해서 같은 토폴로지로 맞춘다
        snaps, _ = align_to_reference(snaps)
        n = snaps[0]["points"].shape[0]
    vdim = 1 if snaps[0]["values"].ndim == 1 else snaps[0]["values"].shape[1]
    pts = np.stack([s["points"] for s in snaps])
    shape_varies = bool(np.max(np.abs(pts - pts[0])) > 1e-9 * (np.max(np.abs(pts)) or 1))
    rom = FieldROM(variables=variables, field_name=field_name, n_nodes=n, value_dim=vdim, shape_varies=shape_varies,
                   triangles=snaps[0].get("triangles"))
    rom.ref_points = pts[0]
    rom.fit(X, snaps)
    rom.info["remeshed"] = remeshed  # True면 기준 메쉬로 보간해서 학습함
    return rom
