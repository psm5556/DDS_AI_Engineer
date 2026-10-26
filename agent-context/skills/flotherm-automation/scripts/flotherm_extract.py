# -*- coding: utf-8 -*-
"""
Flotherm 결과 표(CSV) 파서 -> results.json, images/component_tmax.png (tool 키: python, 플랫폼 venv)
  실행: python flotherm_extract.py [--csv component_temps.csv] [--name-col "Name"]
          [--value-col "Max.*Temp"] [--prefix tmax] [--only U1_CPU --only U2_GPU] [--limit 85]

- 입력: Flotherm 에서 내보낸 결과 표 CSV(부품별 최대/평균 온도 등). 내보내기 방법은 SKILL.md 6절.
- 헤더 행 자동 탐지: --name-col 과 --value-col(정규식, 대소문자 무시)에 맞는 열이 모두 있는 첫 행.
  --name-col 생략 시 'name' 이 들어간 첫 열, --value-col 생략 시 'max' 와 'temp' 가 모두 들어간 첫 열.
- 결과 키: <prefix>_<부품 이름 snake> 와 <prefix>_overall(최대값). --limit L 이면 margin_min = L - overall.
- 값이 하나도 없으면 results.json 을 쓰지 않고 exit 1.
"""
import argparse
import csv
import glob
import io
import json
import math
import os
import re
import sys

CASE_DIR = os.getcwd()


def log(msg):
    print("[flotherm_extract] " + str(msg), flush=True)


def snake(s):
    return re.sub(r"[^0-9a-z]+", "_", str(s).lower()).strip("_")


def to_float(t):
    try:
        return float(str(t).strip().split()[0])     # '85.3 degC' 같은 단위 붙은 값 허용
    except (ValueError, IndexError):
        return None


def read_rows(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ("utf-8-sig", "cp949", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [r for r in csv.reader(io.StringIO(text), dialect)]


def find_header(rows, name_pat, value_pat):
    for i, r in enumerate(rows):
        cells = [c.strip() for c in r]
        ni = next((j for j, c in enumerate(cells) if re.search(name_pat, c, re.I)), None)
        vi = next((j for j, c in enumerate(cells) if re.search(value_pat, c, re.I)), None)
        if ni is not None and vi is not None and ni != vi:
            return i, ni, vi
    raise ValueError("header with name /%s/ and value /%s/ columns not found" % (name_pat, value_pat))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="append", default=[], help="result table CSV (default: *.csv in cwd)")
    ap.add_argument("--name-col", default=r"name")
    ap.add_argument("--value-col", default=r"max.*temp|temp.*max")
    ap.add_argument("--prefix", default="tmax")
    ap.add_argument("--only", action="append", default=[], help="keep only these component names")
    ap.add_argument("--limit", type=float, default=None, help="allowable temperature -> margin_min")
    a = ap.parse_args()

    files = []
    for pat in a.csv or ["*.csv"]:
        files += sorted(glob.glob(os.path.join(CASE_DIR, pat)))
    if not files:
        log("ERROR: no CSV found")
        return 1

    comp = {}
    for path in files:
        rows = read_rows(path)
        try:
            h, ni, vi = find_header(rows, a.name_col, a.value_col)
        except ValueError as e:
            log("WARN: %s: %s" % (os.path.basename(path), e))
            continue
        log("%s: header row %d, name='%s', value='%s'" % (os.path.basename(path), h, rows[h][ni], rows[h][vi]))
        for r in rows[h + 1:]:
            if len(r) <= max(ni, vi):
                continue
            name, v = r[ni].strip(), to_float(r[vi])
            if not name or v is None:
                continue
            if a.only and name not in a.only:
                continue
            key = "%s_%s" % (a.prefix, snake(name))
            comp[key] = max(v, comp.get(key, -1e300))      # 같은 이름이 여러 번이면 최대값

    if not comp:
        log("ERROR: no component values parsed")
        return 1
    bad = [k for k, v in comp.items() if math.isnan(v) or math.isinf(v)]
    if bad:
        log("ERROR: non-finite values: %s" % bad)
        return 1
    missing = [n for n in a.only if "%s_%s" % (a.prefix, snake(n)) not in comp]
    if missing:
        log("ERROR: components not found in table: %s" % missing)
        return 1

    results = dict(comp)
    results["%s_overall" % a.prefix] = max(comp.values())
    if a.limit is not None:
        results["margin_min"] = a.limit - results["%s_overall" % a.prefix]

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        os.makedirs(os.path.join(CASE_DIR, "images"), exist_ok=True)
        items = sorted(comp.items(), key=lambda kv: kv[1])[-30:]
        fig, ax = plt.subplots(figsize=(8, max(3, 0.3 * len(items) + 1)), dpi=150)
        ax.barh([k[len(a.prefix) + 1:] for k, _ in items], [v for _, v in items], color="#d9534f")
        if a.limit is not None:
            ax.axvline(a.limit, color="k", ls="--", lw=1, label="limit %.1f" % a.limit)
            ax.legend(fontsize=8)
        ax.set_xlabel("temperature")
        ax.set_title("component max temperature")
        ax.grid(axis="x", alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(CASE_DIR, "images", "component_tmax.png"))
        plt.close(fig)
    except Exception as e:
        log("WARN: plot failed: %s" % e)

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
