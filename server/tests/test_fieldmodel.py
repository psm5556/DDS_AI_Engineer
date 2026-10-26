"""형상·필드 모델 단위 테스트: 리메쉬(케이스마다 노드 수가 다름)에서도 학습·예측이 되는지."""
import numpy as np

from cae_adapters.mock import beam_mesh
from server.app.study.fieldmodel import train_field_rom


def _snap(b, h, nx):
    pts, tris = beam_mesh(b, h, nx=nx)
    inertia = b * h ** 3 / 12
    stress = np.abs(1000.0 * (1000.0 - pts[:, 0]) * pts[:, 2] / inertia)
    return {"points": pts, "values": stress, "triangles": tris}


def test_remeshed_cases_are_aligned():
    rng = np.random.default_rng(0)
    X = np.column_stack([rng.uniform(10, 40, 18), rng.uniform(20, 80, 18)])
    nxs = [30, 36, 40, 44] * 5
    snaps = [_snap(b, h, nxs[i]) for i, (b, h) in enumerate(X)]
    assert len({s["points"].shape[0] for s in snaps}) > 1  # 정말로 노드 수가 다르다
    rom = train_field_rom(["width", "height"], "stress", X, snaps)
    assert rom.info["remeshed"] is True
    pts, vals, _ = rom.predict({"width": 20, "height": 50})
    assert pts.shape[0] == rom.n_nodes == max(s["points"].shape[0] for s in snaps)
    assert abs(vals.max() - 120.0) / 120.0 < 0.2, vals.max()  # 해석해 최대 120 MPa
    assert abs(np.ptp(pts[:, 1]) - 20) < 2.5  # 폭 20 mm
