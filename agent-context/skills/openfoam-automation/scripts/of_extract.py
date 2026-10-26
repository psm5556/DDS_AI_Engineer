# -*- coding: utf-8 -*-
"""
OpenFOAM 결과 파서 -> results.json, images/*.png, fields/*.npz (tool 키: python, Windows 플랫폼 venv)
  실행: python of_extract.py [--avg-last 100] [--map forces_total_x=drag] [--only-mapped]
          [--log log.simpleFoam] [--surface-raw "postProcessing/surfaces/*/p_wall.raw" --field-name pressure
           [--triangulate xy]]

읽는 파일
- postProcessing/<함수>/<시작시간>/<이름>.dat : forces, forceCoeffs(coefficient.dat), surfaceFieldValue,
  volFieldValue, fieldMinMax 등 함수 객체 출력. '#' 머리말의 마지막 'Time ...' 줄이 열 이름.
  재시작으로 시작시간 폴더가 여러 개면 시간 순으로 이어 붙인다.
  열 값 '(a b c)' 는 <열>_x/_y/_z 로 펼친다. 구형 forces 형식 'forces(pressure viscous porous)' 도 펼친다.
  숫자가 아닌 열(fieldMinMax 의 필드 이름)은 키 접두어로 쓴다.
  키 = <함수>_<열> (같은 폴더에 .dat 가 여러 개면 <함수>_<파일>_<열>), 소문자 snake.
- 솔버 로그(log.<solver>): 'Time = ' 별 초기 잔차 -> images/residuals.png, iterations, final_residual_<field>.
  'converged in' 문구 -> converged(1/0).
- surfaces 함수 객체(surfaceFormat raw) 출력 -> fields/<name>.npz (points, values[, triangles]).
값을 하나도 못 찾으면 results.json 을 쓰지 않고 exit 1.
"""
import argparse
import glob
import json
import math
import os
import re
import sys

CASE_DIR = os.getcwd()          # results.json, images/, fields/ 위치
FOAM_DIR = CASE_DIR             # postProcessing/, log.* 위치 (system/ 을 가진 하위 폴더 하나면 그 폴더)
if not os.path.isdir(os.path.join(CASE_DIR, "system")):
    _subs = [d for d in os.listdir(CASE_DIR) if os.path.isdir(os.path.join(CASE_DIR, d, "system"))]
    if len(_subs) == 1:
        FOAM_DIR = os.path.join(CASE_DIR, _subs[0])
SOLVER_LOG_EXCLUDE = ("blockMesh", "snappyHexMesh", "checkMesh", "decomposePar", "reconstructPar",
                      "foamToVTK", "surfaceFeature", "post_", "setFields", "topoSet")


def log(msg):
    print("[of_extract] " + str(msg), flush=True)


def snake(s):
    return re.sub(r"[^0-9a-z]+", "_", str(s).lower()).strip("_")


def to_float(t):
    try:
        return float(t)
    except ValueError:
        return None


def top_tokens(s):
    """괄호 깊이 0 의 공백으로 나눈다: 'a (1 2 3) ((1 2) (3 4))' -> ['a', '(1 2 3)', '((1 2) (3 4))']."""
    out, cur, depth = [], "", 0
    for ch in s:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch.isspace() and depth == 0:
            if cur:
                out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def flat_numbers(tok):
    return [to_float(x) for x in tok.replace("(", " ").replace(")", " ").split()]


def expand_names(hname, n):
    """헤더 토큰 하나 -> n 개 열 이름."""
    if n == 1:
        return [hname]
    m = re.match(r"^([^(]*)\((.*)\)$", hname)
    if m:
        base, inner = m.group(1), m.group(2).split()
        if len(inner) * 3 == n:
            return ["%s_%s_%s" % (base, i, c) for i in inner for c in "xyz"]
        if len(inner) == n:
            return ["%s_%s" % (base, i) for i in inner]
    if n == 3:
        return [hname + "_" + c for c in "xyz"]
    return ["%s_%d" % (hname, i) for i in range(n)]


def parse_dat(path):
    """-> {tag: (names, rows)}. tag 는 숫자 아닌 열 값(없으면 '')."""
    header, series = None, {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                h = s.lstrip("#").strip()
                if h.lower().startswith("time"):
                    header = top_tokens(h)
                continue
            toks = top_tokens(s)
            tags, names, vals = [], [], []
            for i, tok in enumerate(toks):
                nums = flat_numbers(tok)
                hname = header[i] if header and len(header) == len(toks) else "col%d" % i
                if any(x is None for x in nums):
                    tags.append(tok)
                    continue
                names += expand_names(hname, len(nums))
                vals += nums
            tag = "_".join(tags)
            old = series.get(tag)
            if old is None or old[0] == names:
                series.setdefault(tag, (names, []))[1].append(vals)
    return series


def time_key(d):
    v = to_float(os.path.basename(d))
    return v if v is not None else float("inf")


def collect_dat():
    """postProcessing/<func>/<time>/<file>.dat -> {(func, stem): {tag: (names, rows)}} (시간 폴더 이어붙임)."""
    out = {}
    for fdir in sorted(glob.glob(os.path.join(FOAM_DIR, "postProcessing", "*"))):
        if not os.path.isdir(fdir):
            continue
        func = os.path.basename(fdir)
        for tdir in sorted(glob.glob(os.path.join(fdir, "*")), key=time_key):
            for path in sorted(glob.glob(os.path.join(tdir, "*.dat"))):
                stem = os.path.basename(path)[:-4]
                for tag, (names, rows) in parse_dat(path).items():
                    tgt = out.setdefault((func, stem), {})
                    if tag in tgt and tgt[tag][0] == names:
                        tgt[tag][1].extend(rows)
                    else:
                        tgt[tag] = (names, list(rows))
    return out


def parse_solver_log(path):
    res, times, converged = {}, [], 0
    cur = None
    pat_t = re.compile(r"^Time = ([0-9.eE+-]+)s?\s*$")
    pat_r = re.compile(r"Solving for (\w+), Initial residual = ([0-9.eE+-]+)")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = pat_t.match(line.strip())
            if m:
                cur = float(m.group(1))
                times.append(cur)
                seen = set()
                continue
            if cur is None:
                continue
            m = pat_r.search(line)
            if m and m.group(1) not in seen:          # 시간 스텝마다 필드별 첫 초기 잔차
                seen.add(m.group(1))
                res.setdefault(m.group(1), []).append((cur, float(m.group(2))))
            if "converged in" in line:
                converged = 1
    return times, res, converged


def find_solver_log(arg):
    if arg:
        return os.path.join(FOAM_DIR, arg)
    cands = [p for p in glob.glob(os.path.join(FOAM_DIR, "log.*"))
             if not any(os.path.basename(p)[4:].startswith(x) for x in SOLVER_LOG_EXCLUDE)]
    return max(cands, key=os.path.getmtime) if cands else None


def plot_lines(fname, series, title, logy=False, xlabel="time / iteration"):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
        for label, (x, y) in series.items():
            ax.plot(x, y, label=label, lw=1.2)
        if logy:
            ax.set_yscale("log")
        ax.set_xlabel(xlabel)
        ax.set_title(title)
        ax.grid(alpha=0.3)
        if 1 < len(series) <= 12:
            ax.legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(os.path.join(CASE_DIR, "images", fname))
        plt.close(fig)
    except Exception as e:
        log("WARN: plot %s failed: %s" % (fname, e))


def read_raw_surface(pattern, name, tri_plane):
    import numpy as np
    files = sorted(glob.glob(os.path.join(FOAM_DIR, pattern)), key=lambda p: time_key(os.path.dirname(p)))
    if not files:
        raise FileNotFoundError("no surface file: " + pattern)
    path = files[-1]                                   # 마지막 시간
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            nums = [to_float(t) for t in s.split()]
            if None not in nums and len(nums) >= 4:
                rows.append(nums)
    a = np.array(rows, dtype=float)
    pts = a[:, :3]
    vals = a[:, 3] if a.shape[1] == 4 else np.linalg.norm(a[:, 3:6], axis=1)   # 벡터는 크기
    out = {"points": pts, "values": vals}
    if tri_plane:
        from scipy.spatial import Delaunay
        ax = {"xy": (0, 1), "xz": (0, 2), "yz": (1, 2)}[tri_plane]
        out["triangles"] = Delaunay(pts[:, ax]).simplices.astype(np.int32)
    os.makedirs(os.path.join(CASE_DIR, "fields"), exist_ok=True)
    np.savez_compressed(os.path.join(CASE_DIR, "fields", snake(name) + ".npz"), **out)
    log("saved fields/%s.npz from %s: points=%d max=%.6g" % (snake(name), os.path.relpath(path, FOAM_DIR),
                                                               len(pts), float(vals.max())))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--avg-last", type=int, default=1, help="average of last N rows")
    ap.add_argument("--map", action="append", default=[], help="source_key=result_key")
    ap.add_argument("--only-mapped", action="store_true")
    ap.add_argument("--log", default=None, help="solver log (default: newest log.* that is not a utility)")
    ap.add_argument("--surface-raw", default=None, help="glob of raw surface file (relative to case)")
    ap.add_argument("--field-name", default="field")
    ap.add_argument("--triangulate", choices=["xy", "xz", "yz"], default=None)
    a = ap.parse_args()
    os.makedirs(os.path.join(CASE_DIR, "images"), exist_ok=True)

    raw = {}
    data = collect_dat()
    multi = {}
    for func, stem in data:
        multi[func] = multi.get(func, 0) + 1
    for (func, stem), tags in data.items():
        prefix = func if multi[func] == 1 else func + "_" + stem
        plot = {}
        for tag, (names, rows) in tags.items():
            if not rows:
                continue
            n = max(1, min(a.avg_last, len(rows)))
            for j, nm in enumerate(names):
                if j == 0:                 # 첫 열은 항상 Time
                    continue
                key = snake("_".join(x for x in (prefix, tag, nm) if x))
                col = [r[j] for r in rows if j < len(r)]
                raw[key] = sum(col[-n:]) / len(col[-n:])
                if len(plot) < 12:
                    plot[key] = ([r[0] for r in rows if j < len(r)], col)
        if plot:
            plot_lines("pp_%s.png" % snake(prefix), plot, prefix)
        log("%s/%s: %s" % (func, stem, ", ".join("%s(%d rows)" % (t or "-", len(v[1])) for t, v in tags.items())))

    lp = find_solver_log(a.log)
    if lp and os.path.exists(lp):
        times, res, converged = parse_solver_log(lp)
        if times:
            raw["iterations"] = float(len(times))
            raw["end_time"] = times[-1]
            raw["converged"] = float(converged)
            for fld, hist in res.items():
                raw["final_residual_" + snake(fld)] = hist[-1][1]
            plot_lines("residuals.png", dict((k, ([h[0] for h in v], [h[1] for h in v])) for k, v in res.items()),
                       "initial residuals (" + os.path.basename(lp) + ")", logy=True)
        log("solver log %s: %d time steps" % (os.path.basename(lp), len(times)))
    else:
        log("WARN: no solver log found")

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
    if not [k for k in results if not k.startswith("final_residual_") and k not in ("iterations", "end_time", "converged")]:
        log("ERROR: no function-object values found in postProcessing/ (check controlDict functions)")
        return 1

    if a.surface_raw:
        read_raw_surface(a.surface_raw, a.field_name, a.triangulate)

    with open(os.path.join(CASE_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    log("results.json: %d keys -> %s" % (len(results), ", ".join(sorted(results))))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("FAILED: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
