"""[데모] 파라메트릭 브래킷 CAD 생성 — NX 저널 대신 쓰는 연습용 스크립트.
params.json(thickness, rib_height) → 프로젝트 cad/bracket.stl, cad/expressions.json, images/bracket_cad.png, results.json
사용: python bracket_cad.py <저장소 루트>
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, sys.argv[1])
from cae_adapters.mock import BRACKET, bracket_mesh, bracket_section  # noqa: E402

run = Path.cwd()
p = json.loads((run / "params.json").read_text(encoding="utf-8"))
t, r = float(p["thickness"]), float(p["rib_height"])
pts, tris = bracket_mesh(t, r)

cad = run.parent.parent / "cad"  # 프로젝트 폴더/cad
cad.mkdir(exist_ok=True)

# 바이너리 STL
tri = pts[tris]
nrm = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
rec = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
rec["n"], rec["v"] = nrm, tri
with open(cad / "bracket.stl", "wb") as f:
    f.write(b"DDS bracket".ljust(80, b" "))
    f.write(np.uint32(len(tris)).tobytes())
    f.write(rec.tobytes())

# NX expression 처럼 파라미터 목록
expr = {"L": BRACKET["L"], "W": BRACKET["W"], "rib_t": BRACKET["RIB_T"], "thickness": t, "rib_height": r}
(cad / "expressions.json").write_text(json.dumps(expr, indent=2), encoding="utf-8")

area, zc, inertia = bracket_section(t, r)
volume = area * BRACKET["L"]

# 형상 그림
import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

light = np.clip(0.35 + 0.65 * np.abs(nrm @ np.array([0.4, -0.5, 0.75])), 0, 1)
fig = plt.figure(figsize=(7, 4.2), dpi=130)
ax = fig.add_subplot(111, projection="3d")
ax.add_collection3d(Poly3DCollection(tri, facecolors=plt.cm.Blues(0.35 + 0.55 * light), edgecolor=(0, 0, 0, 0.08), linewidths=0.2))
ax.set_xlim(0, BRACKET["L"]); ax.set_ylim(-60, 60); ax.set_zlim(-40, 80)
ax.set_box_aspect((BRACKET["L"], 120, 120))
ax.view_init(elev=24, azim=-58)
ax.set_xlabel("x [mm]"); ax.set_ylabel("y"); ax.set_zlabel("z")
ax.set_title(f"Bracket  t={t:g} mm, rib={r:g} mm  (fixed at x=0)", fontsize=10)
(run / "images").mkdir(exist_ok=True)
fig.tight_layout()
fig.savefig(run / "images" / "bracket_cad.png")

(run / "results.json").write_text(json.dumps({
    "volume_mm3": round(volume, 1), "mass_g": round(volume * BRACKET["rho"] * 1000, 1),
    "faces": int(len(tris)), "stl": str(cad / "bracket.stl")}, indent=2), encoding="utf-8")
print("CAD 생성 완료:", cad / "bracket.stl")
