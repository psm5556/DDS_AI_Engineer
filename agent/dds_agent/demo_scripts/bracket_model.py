"""[데모] 브래킷 해석 모델 구성 — Abaqus/CAE 전처리 대신 쓰는 연습용 스크립트.
params.json(thickness, rib_height, mesh_size) → 프로젝트 model/bracket.inp(Abaqus 입력), images/model_bc.png, results.json
재료 강(E 210 GPa, ν 0.3), 경계조건 x=0 고정(ENCASTRE), 하중 x=L 끝단 -z 방향 P.
사용: python bracket_model.py <저장소 루트>
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, sys.argv[1])
from cae_adapters.mock import BRACKET, bracket_mesh  # noqa: E402

run = Path.cwd()
p = json.loads((run / "params.json").read_text(encoding="utf-8"))
t, r = float(p["thickness"]), float(p["rib_height"])
pts, tris = bracket_mesh(t, r)
L, P = BRACKET["L"], BRACKET["P"]
fixed = np.where(pts[:, 0] < 1e-6)[0]
loaded = np.where(pts[:, 0] > L - 1e-6)[0]

model = run.parent.parent / "model"  # 프로젝트 폴더/model
model.mkdir(exist_ok=True)
lines = ["*HEADING", f"DDS demo bracket t={t:g} rib={r:g}", "*NODE"]
lines += [f"{i + 1}, {x:.4f}, {y:.4f}, {z:.4f}" for i, (x, y, z) in enumerate(pts)]
lines += ["*ELEMENT, TYPE=S3, ELSET=BRACKET"]
lines += [f"{i + 1}, {a + 1}, {b + 1}, {c + 1}" for i, (a, b, c) in enumerate(tris)]
lines += ["*NSET, NSET=FIXED"] + [", ".join(str(n + 1) for n in fixed[i:i + 16]) for i in range(0, len(fixed), 16)]
lines += ["*NSET, NSET=TIP"] + [", ".join(str(n + 1) for n in loaded[i:i + 16]) for i in range(0, len(loaded), 16)]
lines += ["*SHELL SECTION, ELSET=BRACKET, MATERIAL=STEEL", f"{min(t, BRACKET['RIB_T']):g}",
          "*MATERIAL, NAME=STEEL", "*ELASTIC", "210000., 0.3", "*DENSITY", "7.85e-09",
          "*BOUNDARY", "FIXED, ENCASTRE",
          "*STEP, NAME=LOAD", "*STATIC",
          "*CLOAD", f"TIP, 3, {-P / len(loaded):.4f}",
          "*OUTPUT, FIELD", "*NODE OUTPUT", "U", "*ELEMENT OUTPUT", "S, MISES",
          "*END STEP"]
(model / "bracket.inp").write_text("\n".join(lines) + "\n", encoding="utf-8")

import matplotlib  # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Line3DCollection  # noqa: E402

edges = np.vstack([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
fig = plt.figure(figsize=(7, 4.2), dpi=130)
ax = fig.add_subplot(111, projection="3d")
ax.add_collection3d(Line3DCollection(pts[edges], colors=(0.2, 0.3, 0.5, 0.35), linewidths=0.3))
ax.scatter(*pts[fixed].T, s=6, c="#d33", label=f"Fixed (ENCASTRE) {len(fixed)} nodes")
tip = pts[loaded]
ax.quiver(tip[:, 0], tip[:, 1], tip[:, 2] + 18, 0, 0, -14, color="#e80", linewidth=0.8, arrow_length_ratio=0.35)
ax.plot([], [], color="#e80", label=f"Load P={P:g} N (-z)")
ax.set_xlim(0, L); ax.set_ylim(-60, 60); ax.set_zlim(-40, 80)
ax.set_box_aspect((L, 120, 120))
ax.view_init(elev=24, azim=-58)
ax.set_title(f"Analysis model: S3 shell {len(tris)} elem / {len(pts)} nodes, steel", fontsize=10)
ax.legend(loc="upper left", fontsize=8)
(run / "images").mkdir(exist_ok=True)
fig.tight_layout()
fig.savefig(run / "images" / "model_bc.png")

(run / "results.json").write_text(json.dumps({
    "nodes": int(len(pts)), "elements": int(len(tris)), "fixed_nodes": int(len(fixed)), "load_nodes": int(len(loaded)),
    "inp": str(model / "bracket.inp")}, indent=2), encoding="utf-8")
print("해석 모델 생성 완료:", model / "bracket.inp")
