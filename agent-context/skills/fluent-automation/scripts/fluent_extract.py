# -*- coding: utf-8 -*-
"""
Fluent 결과 파일 파서 -> results.json, images/*.png, fields/*.npz (tool 키: python, 플랫폼 venv)
  실행: python fluent_extract.py [--out "*.out"] [--srp "*.srp"] [--avg-last 50]
          [--map outlet-temp=outlet_temp] [--transcript fluent.trn]
          [--ascii heater_surface.csv --field-name temperature --value-col temperature [--triangulate xy]]

읽는 파일
- 리포트 파일(*.out): 리포트 정의의 Report File. 머리말("..." 따옴표 열 이름) + 숫자 행.
  열마다 마지막 값(또는 --avg-last N 평균)을 쓴다. 키 = 열 이름 snake (Iteration/flow-time 등은 제외).
- 표면 적분 파일(*.srp): /report/surface-integrals ... yes <file>.srp 로 쓴 파일.
  'Net' 값이 있으면 키 = 파일 이름, 표면별 값은 <파일>_<표면>.
- 트랜스크립트(fluent.trn): 잔차 표 -> iterations, images/residuals.png. 'solution is converged' -> converged.
- ASCII 내보내기(.csv, /file/export/ascii): x/y/z-coordinate 열 + 값 열 -> fields/<name>.npz.
값을 하나도 못 찾으면 results.json 을 쓰지 않고 exit 1.
"""
import argparse
import csv
import glob
import json
import math
import os
import re
import sys

CASE_DIR = os.getcwd()
SKIP_COLS = {"iteration", "time_step", "flow_time", "time", "physical_time"}


def log(msg):
    print("[fluent_extract] " + str(msg), flush=True)


def snake(s):
    return re.sub(r"[^0-9a-z]+", "_", str(s).lower()).strip("_")


def is_float(t):
    try:
        float(t)
        return True
    except ValueError:
        return False


def parse_out(path):
    """리포트 파일 -> (열 이름, 행 목록)."""
    names, rows = None, []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            toks = s.split()
            if all(is_float(t) for t in toks):
                rows.append([float(t) for t in toks])
            elif '"' in s:
                q = re.findall(r'"([^"]*)"', s)
                if len(q) >= 2:            # 제목 줄("xxx-rfile")은 1개라 무시
                    names = q
    if rows and (not names or len(names) != len(rows[-1])):
        names = ["col%d" % i for i in range(len(rows[-1]))]
    return names, rows


def parse_srp(path):
    """표면 적분 리포트 -> {surface: value} ('net' 포함 가능)."""
    vals, after_dash = {}, False
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("---"):
                after_dash = True
                continue
            if not after_dash or not s:
                continue
            toks = s.split()
            if len(toks) >= 2 and is_float(toks[-1]):
                vals[snake(" ".join(toks[:-1]))] = float(toks[-1])
    return vals


def parse_transcript(path):
    """잔차 표: 'iter continuity x-velocity ... time/iter' 머리말 + 숫자 행."""
    names, hist, converged = None, [], 0
    if not os.path.exists(path):
        return names, hist, None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if "solution is converged" in s.lower():
                converged = 1
            toks = s.split()
            if toks and toks[0] == "iter" and len(toks) > 2:
                names = [t for t in toks[1:] if t != "time/iter"]
                continue
            if names and toks and toks[0].isdigit() and len(toks) >= len(names) + 1:
                vals = toks[1:1 + len(names)]
                if all(is_float(v) for v in vals):
                    hist.append([int(toks[0])] + [float(v) for v in vals])
    return names, hist, converged


def save_plot(fname, x, ys, labels, title, logy=False):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
        for y, lb in zip(ys, labels):
            ax.plot(x, y, label=lb, lw=1.2)
        if logy:
            ax.set_yscale("log")
        ax.set_xlabel("iteration")
        ax.set_title(title)
        ax.grid(alpha=0.3)
        if len(labels) > 1:
            ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(os.path.join(CASE_DIR, "images", fname))
        plt.close(fig)
    except Exception as e:  # 이미지 실패는 케이스 실패가 아니다
        log("WARN: plot %s failed: %s" % (fname, e))


def export_field(path, name, value_col, tri_plane):
    import numpy as np
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        rd = csv.reader(f)
        header = [snake(h) for h in next(rd)]
        rows = [r for r in rd if r and all(is_float(t) for t in r[:len(header)])]
    cols = {h: i for i, h in enumerate(header)}
    need = ["x_coordinate", "y_coordinate"]
    if any(c not in cols for c in need):
        raise ValueError("ascii export needs x/y-coordinate columns, got %s" % header)
    vkey = snake(value_col)
    if vkey not in cols:
        raise ValueError("value column %s not in %s" % (vkey, header))
    a = np.array([[float(t) for t in r[:len(header)]] for r in rows])
    z = a[:, cols["z_coordinate"]] if "z_coordinate" in cols else np.zeros(len(a))
    pts = np.column_stack([a[:, cols["x_coordinate"]], a[:, cols["y_coordinate"]], z])
    vals = a[:, cols[vkey]]
    out = {"points": pts, "values": vals}
    if tri_plane:                      # 평평한 면이면 투영 평면에서 Delaunay 삼각형을 만든다
        from scipy.spatial import Delaunay
        ax = {"xy": (0, 1), "xz": (0, 2), "yz": (1, 2)}[tri_plane]
        out["triangles"] = Delaunay(pts[:, ax]).simplices.astype(np.int32)
    os.makedirs(os.path.join(CASE_DIR, "fields"), exist_ok=True)
    np.savez_compressed(os.path.join(CASE_DIR, "fields", snake(name) + ".npz"), **out)
    log("saved fields/%s.npz: nodes=%d max=%.6g" % (snake(name), len(pts), float(vals.max())))
    return pts, vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", action="append", default=[], help="report file glob (default *.out)")
    ap.add_argument("--srp", action="append", default=[], help="surface integral file glob (default *.srp)")
    ap.add_argument("--avg-last", type=int, default=1, help="average of last N rows of report files")
    ap.add_argument("--map", action="append", default=[], help="source_key=result_key (applied after snake)")
    ap.add_argument("--only-mapped", action="store_true", help="write only keys listed in --map")
    ap.add_argument("--transcript", default="fluent.trn")
    ap.add_argument("--ascii", default=None, help="CSV from /file/export/ascii")
    ap.add_argument("--field-name", default="field")
    ap.add_argument("--value-col", default="temperature")
    ap.add_argument("--triangulate", choices=["xy", "xz", "yz"], default=None)
    a = ap.parse_args()

    os.makedirs(os.path.join(CASE_DIR, "images"), exist_ok=True)
    raw = {}

    for pat in a.out or ["*.out"]:
        for path in sorted(glob.glob(os.path.join(CASE_DIR, pat))):
            names, rows = parse_out(path)
            if not rows:
                log("WARN: no data rows in " + os.path.basename(path))
                continue
            n = max(1, min(a.avg_last, len(rows)))
            x = [r[0] for r in rows]
            for j, nm in enumerate(names):
                key = snake(nm)
                if key in SKIP_COLS:
                    continue
                raw[key] = sum(r[j] for r in rows[-n:]) / n
                save_plot("report_%s.png" % key, x, [[r[j] for r in rows]], [nm], nm)
            log("%s: %d rows, cols=%s" % (os.path.basename(path), len(rows), names))

    for pat in a.srp or ["*.srp"]:
        for path in sorted(glob.glob(os.path.join(CASE_DIR, pat))):
            stem = snake(os.path.basename(path).rsplit(".", 1)[0])
            vals = parse_srp(path)
            if not vals:
                log("WARN: no values in " + os.path.basename(path))
                continue
            if stem in raw:                # 리포트 파일 열 이름과 겹치면 덮어쓰지 않는다
                log("WARN: key %s already from report file; srp keys use %s_srp" % (stem, stem))
                stem += "_srp"
            if "net" in vals:
                raw[stem] = vals["net"]
            elif len(vals) == 1:
                raw[stem] = list(vals.values())[0]
            for surf, v in vals.items():
                if surf != "net":
                    raw["%s_%s" % (stem, surf)] = v

    names, hist, converged = parse_transcript(os.path.join(CASE_DIR, a.transcript))
    if hist:
        raw["iterations"] = float(hist[-1][0])
        save_plot("residuals.png", [h[0] for h in hist],
                  [[h[j + 1] for h in hist] for j in range(len(names))], names, "residuals", logy=True)
    if converged is not None:
        raw["converged"] = float(converged)

    mapping = dict(m.split("=", 1) for m in a.map)
    results = {}
    for k, v in raw.items():
        if a.only_mapped and k not in mapping:
            continue
        results[mapping.get(k, k)] = v
    bad = [k for k, v in results.items() if math.isnan(v) or math.isinf(v)]
    if bad:
        log("ERROR: non-finite values: %s" % bad)
        return 1
    data_keys = [k for k in results if k not in ("iterations", "converged")]
    if not data_keys:
        log("ERROR: no report values found (check report files / srp names)")
        return 1

    if a.ascii:
        pts, vals = export_field(os.path.join(CASE_DIR, a.ascii), a.field_name, a.value_col, a.triangulate)
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig = plt.figure(figsize=(7, 5), dpi=150)
            ax = fig.add_subplot(111, projection="3d")
            sc = ax.scatter(pts[:, 0], pts[:, 1], pts[:, 2], c=vals, s=2, cmap="jet")
            fig.colorbar(sc, ax=ax, shrink=0.7, label=a.value_col)
            ax.set_title(a.field_name)
            fig.savefig(os.path.join(CASE_DIR, "images", "field_%s.png" % snake(a.field_name)))
            plt.close(fig)
        except Exception as e:
            log("WARN: field plot failed: %s" % e)

    with open(os.path.join(CASE_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    log("results.json: " + json.dumps(results))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("FAILED: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
