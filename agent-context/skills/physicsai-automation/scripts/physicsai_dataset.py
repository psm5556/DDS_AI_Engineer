# -*- coding: utf-8 -*-
"""
DDS 스터디 케이스 -> Altair PhysicsAI 학습용 데이터셋 (VTK legacy + dataset.csv)

  python physicsai_dataset.py --study ../../studies/S003_bracket --field stress [--out physicsai_dataset] [--split 0.8]
  python physicsai_dataset.py --probe        # (HyperWorks 파이썬에서) PhysicsAI 관련 모듈을 찾아 physicsai_probe.json 에
케이스 폴더: <study>/cases/C0001_doe/ 의 case.json(params·results·status), fields/<field>.npz(points, triangles, values)
"""
import argparse
import csv
import json
import os
import random
import sys
import time


def log(msg):
    sys.stdout.write(str(msg).encode("ascii", "replace").decode("ascii") + "\n")
    sys.stdout.flush()


def write_vtk(path, points, triangles, values, field):
    with open(path, "w", encoding="ascii") as f:
        f.write("# vtk DataFile Version 3.0\nDDS case %s\nASCII\nDATASET POLYDATA\n" % field)
        f.write("POINTS %d float\n" % len(points))
        for x, y, z in points:
            f.write("%.6g %.6g %.6g\n" % (x, y, z))
        f.write("POLYGONS %d %d\n" % (len(triangles), 4 * len(triangles)))
        for a, b, c in triangles:
            f.write("3 %d %d %d\n" % (a, b, c))
        f.write("POINT_DATA %d\nSCALARS %s float 1\nLOOKUP_TABLE default\n" % (len(values), field))
        for v in values:
            f.write("%.6g\n" % v)


def probe():
    import importlib
    import pkgutil
    found = {}
    for name in ("physicsai", "hw", "hwx", "hm", "hwx.physicsai", "hw.physicsai"):
        try:
            m = importlib.import_module(name)
            found[name] = sorted(n for n in dir(m) if not n.startswith("_"))[:300]
        except Exception as e:  # noqa: BLE001
            found[name] = "not importable: %s" % e
    found["candidates"] = sorted({m.name for m in pkgutil.iter_modules() if "phys" in m.name.lower() or "ai" == m.name.lower()})
    json.dump(found, open("physicsai_probe.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log("physicsai_probe.json written")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--study")
    ap.add_argument("--field", default="stress")
    ap.add_argument("--out", default="physicsai_dataset")
    ap.add_argument("--split", type=float, default=0.8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--probe", action="store_true")
    a = ap.parse_args()
    if a.probe:
        return probe()
    if not a.study:
        sys.exit("--study is required")
    import numpy as np
    cases_dir = os.path.join(a.study, "cases")
    rows, skipped = [], []
    os.makedirs(os.path.join(a.out, "cases"), exist_ok=True)
    for name in sorted(os.listdir(cases_dir)) if os.path.isdir(cases_dir) else []:
        cdir = os.path.join(cases_dir, name)
        npz = os.path.join(cdir, "fields", a.field + ".npz")
        meta_p = os.path.join(cdir, "case.json")
        if not (os.path.exists(npz) and os.path.exists(meta_p)):
            continue
        meta = json.load(open(meta_p, encoding="utf-8"))
        if meta.get("status") not in (None, "complete"):
            continue
        z = np.load(npz)
        if "triangles" not in z.files:
            skipped.append(name + ": no triangles")
            continue
        cid = name.split("_")[0]
        rel = os.path.join("cases", cid + ".vtk")
        write_vtk(os.path.join(a.out, rel), z["points"].tolist(), z["triangles"].tolist(), z["values"].ravel().tolist(), a.field)
        rows.append({"case_id": cid, "file": rel.replace("\\", "/"), **{"in:" + k: v for k, v in (meta.get("params") or {}).items()},
                     **{"out:" + k: v for k, v in (meta.get("results") or {}).items()}})
    if len(rows) < 2:
        sys.exit("FAIL: need at least 2 completed cases with fields/%s.npz under %s" % (a.field, cases_dir))
    random.Random(a.seed).shuffle(rows)
    n_train = max(1, min(len(rows) - 1, int(round(len(rows) * a.split))))
    for i, r in enumerate(rows):
        r["split"] = "train" if i < n_train else "test"
    rows.sort(key=lambda r: r["case_id"])
    cols = ["case_id", "file", "split"] + sorted({k for r in rows for k in r if k.startswith("in:")}) + \
        sorted({k for r in rows for k in r if k.startswith("out:")})
    with open(os.path.join(a.out, "dataset.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(a.out, "README.txt"), "w", encoding="utf-8") as f:
        f.write("DDS -> PhysicsAI dataset\nfield: %s\ncases: %d (train %d, test %d)\nstudy: %s\ncreated: %s\nskipped: %s\n"
                % (a.field, len(rows), n_train, len(rows) - n_train, os.path.abspath(a.study), time.strftime("%Y-%m-%d %H:%M"), skipped))
    log("dataset: %d cases (train %d) -> %s" % (len(rows), n_train, a.out))


if __name__ == "__main__":
    main()
