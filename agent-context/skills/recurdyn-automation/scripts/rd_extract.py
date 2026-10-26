# -*- coding: utf-8 -*-
"""
RecurDyn 곡선 내보내기(CSV/TXT) -> results.json  (tool 키: python, 플랫폼 venv / Python 3, 표준 라이브러리만)

  python rd_extract.py [--csv "plots/*.csv" ...] [--list]
                       [--metric max_lift=max:Body1_Pos_Z --metric f_end=final:Spring1_Force ...]
                       [--plot Body1_Pos_Z ...] [--end-time 2.0]

- 입력: RecurDyn Plot 의 곡선 Export 나 ProcessNet 매크로로 만든 표 파일(시간 열 + 값 열).
  구분자(콤마, 탭, 세미콜론, 공백)와 머리글 줄을 자동 인식한다. 머리글 앞의 메타 줄은 건너뛴다.
  "Time" 이 여러 번 나오면(곡선마다 x 열이 있는 형식) 각 값 열은 바로 앞의 시간 열을 쓴다.
  TODO(사내검증): 사내 RecurDyn 버전의 Export 형식(머리글, 단위 줄, 구분자)을 샘플로 확인.
- 시계열 키: 파일이 하나면 "<열 이름>", 여러 개면 "<파일 stem>.<열 이름>". 열 이름만 줘도 유일하면 찾는다.
- stat: max, min, absmax, mean, rms, final, initial, range, at<t>
- --metric 이 없으면 모든 시계열의 absmax 를 쓴다.
- --end-time(또는 params.json 의 _end_time)이 있으면 마지막 시간이 도달했는지 확인한다. 미달이면 실패.
- 기존 results.json 이 있으면 합친다. 실패 시 results.json 을 쓰지 않고 종료코드 1.
"""
import argparse
import glob
import json
import math
import os
import re
import sys

CASE_DIR = os.getcwd()


def log(msg):
    print("[rd_extract] " + str(msg))
    sys.stdout.flush()


def _num(tok):
    try:
        return float(tok)
    except ValueError:
        return None


def _split(line, delim):
    if delim is None:
        return line.split()
    return [c.strip().strip('"') for c in line.rstrip("\r\n").split(delim)]


def read_table(path):
    """-> [(col_name, [t], [y])] (시간 열 제외)"""
    with open(path, "r", errors="replace") as f:
        raw = [l for l in f.read().splitlines() if l.strip()]
    if not raw:
        raise RuntimeError("empty file: " + path)
    sample = "\n".join(raw[-5:])
    delim = None
    for d in (",", "\t", ";"):
        if d in sample:
            delim = d
            break
    rows = [_split(l, delim) for l in raw]
    first_data = None
    for i, r in enumerate(rows):
        vals = [_num(c) for c in r if c != ""]
        if vals and all(v is not None for v in vals) and len(vals) >= 2:
            first_data = i
            break
    if first_data is None:
        raise RuntimeError("no numeric rows in " + path)
    ncol = len(rows[first_data])
    header = None
    for r in rows[:first_data]:
        if len(r) == ncol:
            header = r
            break
    if header is None:
        header = ["col%d" % j for j in range(ncol)]
        header[0] = "time"
    header = [h if h else "col%d" % j for j, h in enumerate(header)]
    data = []
    for r in rows[first_data:]:
        data.append([_num(c) if c != "" else None for c in r] + [None] * (ncol - len(r)))
    is_time = [bool(re.match(r"(?i)^\s*time\b", h)) for h in header]
    if not any(is_time):
        is_time[0] = True
    out = []
    xcol = 0
    for j in range(ncol):
        if is_time[j]:
            xcol = j
            continue
        t, y = [], []
        for r in data:
            if r[xcol] is not None and r[j] is not None:
                t.append(r[xcol])
                y.append(r[j])
        if t:
            out.append((header[j], t, y))
    return out


def load_series(patterns):
    files = []
    for p in patterns:
        files.extend(sorted(glob.glob(os.path.join(CASE_DIR, p))))
    if not files:
        raise RuntimeError("no curve files match: " + ", ".join(patterns))
    series = {}
    for f in files:
        stem = os.path.splitext(os.path.basename(f))[0]
        for name, t, y in read_table(f):
            key = name if len(files) == 1 else stem + "." + name
            if key in series:
                key = "%s_%d" % (key, len(series))
            series[key] = (t, y)
    return series


def stat(name, t, y):
    s = name.lower()
    if not y:
        raise ValueError("empty series")
    if s == "max":
        return max(y)
    if s == "min":
        return min(y)
    if s == "absmax":
        return max(abs(v) for v in y)
    if s == "mean":
        return sum(y) / len(y)
    if s == "rms":
        return math.sqrt(sum(v * v for v in y) / len(y))
    if s == "final":
        return y[-1]
    if s == "initial":
        return y[0]
    if s == "range":
        return max(y) - min(y)
    if s.startswith("at"):
        tq = float(s[2:])
        if tq <= t[0]:
            return y[0]
        for i in range(1, len(t)):
            if t[i] >= tq:
                dt = t[i] - t[i - 1]
                w = 0.0 if dt == 0 else (tq - t[i - 1]) / dt
                return y[i - 1] + w * (y[i] - y[i - 1])
        return y[-1]
    raise ValueError("unknown stat: " + name)


def snake(s):
    return re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower() or "value"


def find_series(series, key):
    if key in series:
        return series[key]
    low = dict((k.lower(), k) for k in series)
    if key.lower() in low:
        return series[low[key.lower()]]
    tails = [k for k in series if k.lower().endswith("." + key.lower())]
    if len(tails) == 1:
        return series[tails[0]]
    raise KeyError("series not found or ambiguous: %s (use --list)" % key)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="append", default=[])
    ap.add_argument("--metric", action="append", default=[])
    ap.add_argument("--plot", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--end-time", type=float)
    a = ap.parse_args()

    series = load_series(a.csv or ["*.csv"])
    log("loaded %d series" % len(series))
    if a.list:
        for k in sorted(series):
            print("  %s  (n=%d, t_end=%g)" % (k, len(series[k][0]), series[k][0][-1]))
        return 0

    params = {}
    pp = os.path.join(CASE_DIR, "params.json")
    if os.path.isfile(pp):
        with open(pp) as f:
            params = json.load(f)
    end_time = a.end_time if a.end_time is not None else params.get("_end_time")
    if end_time:
        end_time = float(end_time)
        t_last = max(v[0][-1] for v in series.values())
        if t_last < end_time - max(1e-9, 1e-6 * abs(end_time)):
            log("ERROR: curves end at t=%g < end_time=%g (solver stopped early?)" % (t_last, end_time))
            return 1

    results = {}
    if not a.metric:
        for k, (t, y) in series.items():
            results["absmax_" + snake(k)] = stat("absmax", t, y)
    for spec in a.metric:
        name, rhs = spec.split("=", 1)
        st, key = rhs.split(":", 1)
        t, y = find_series(series, key)
        results[snake(name)] = stat(st, t, y)
    for k, v in results.items():
        if v != v or v in (float("inf"), float("-inf")):
            log("ERROR: non-finite result %s" % k)
            return 1

    if a.plot:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import rd_plots
            results["n_plots"] = rd_plots.make_plots(series, a.plot, os.path.join(CASE_DIR, "images"))
        except Exception as e:
            log("WARNING: plot failed: %s" % e)

    rp = os.path.join(CASE_DIR, "results.json")
    old = {}
    if os.path.isfile(rp):
        with open(rp) as f:
            old = json.load(f)
    old.update(results)
    with open(rp, "w") as f:
        json.dump(old, f, indent=2, sort_keys=True)
    log("results.json: " + ", ".join("%s=%.6g" % (k, v) for k, v in sorted(results.items())))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("FAILED: " + str(e))
        sys.exit(1)
