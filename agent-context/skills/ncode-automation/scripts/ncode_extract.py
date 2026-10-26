# -*- coding: utf-8 -*-
"""
nCode DesignLife 손상/수명 표(CSV) -> results.json  (tool 키: python, 플랫폼 venv / Python 3, 표준 라이브러리만)

  python ncode_extract.py --csv "results/*.csv" [--id-col NodeID] [--damage-col Damage] [--life-col Life]
                          [--target-life 1.0] [--per-file] [--plot] [--list]

- 입력: 플로의 출력 글리프로 쓴 표(한 줄 = 한 엔티티(노드/요소), 열 = ID, 손상, 수명 …).
  머리글은 'damage' 또는 'life' 가 들어간 첫 줄로 찾는다(그 앞의 메타 줄은 건너뜀). 구분자 자동 인식.
  TODO(사내검증): 사내 플로가 쓰는 표의 실제 열 이름(예: 'Damage', 'Life (Repeats)')과 엔티티 ID 열.
- 손상 열만 있으면 수명 = 1/손상, 수명 열만 있으면 손상 = 1/수명 으로 계산한다.
- 'inf', 'Infinity', '1.#INF' 같은 무한 수명은 LIFE_CAP 으로 바꿔 JSON 에 쓴다.
- 출력 키: max_damage, min_life, log10_min_life, n_entities, n_damage_ge_1, critical_id
           (--target-life 가 있으면 life_margin = min_life / target_life)
           (--per-file 이면 <파일 stem>_max_damage, <파일 stem>_min_life 추가)
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
LIFE_CAP = 1e30


def log(msg):
    print("[ncode_extract] " + str(msg))
    sys.stdout.flush()


def to_float(s):
    s = (s or "").strip().strip('"')
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        if "inf" in s.lower():
            return float("inf")
        return None


def _pick(header, rx, override):
    if override:
        for j, h in enumerate(header):
            if h.strip().lower() == override.lower():
                return j
        raise KeyError("column %s not found in header %s" % (override, header))
    for j, h in enumerate(header):
        if re.search(rx, h, re.I):
            return j
    return None


def read_damage_table(path, id_col=None, damage_col=None, life_col=None):
    """-> [(entity_id, damage, life)]"""
    with open(path, "r", errors="replace") as f:
        lines = [l.rstrip("\r\n") for l in f if l.strip()]
    delim = None
    for d in (",", "\t", ";"):
        if any(d in l for l in lines[:20]):
            delim = d
            break
    rows = [[c.strip().strip('"') for c in (l.split(delim) if delim else l.split())] for l in lines]
    hi = None
    for i, r in enumerate(rows):
        if any(re.search(r"damage|life", c, re.I) for c in r) and not all(to_float(c) is not None for c in r):
            hi = i
            break
    if hi is None:
        raise RuntimeError("no header with 'damage' or 'life' in " + path)
    header = rows[hi]
    jd = _pick(header, r"damage", damage_col)
    jl = _pick(header, r"life", life_col)
    ji = _pick(header, r"^\s*(node|element|elem|entity)?\s*_?\s*id\s*$|^\s*(node|element|entity)\s*$", id_col)
    if ji is None:
        ji = 0
    if jd is None and jl is None:
        raise RuntimeError("no damage/life columns in " + path)
    out = []
    for r in rows[hi + 1:]:
        if len(r) < len(header):
            continue
        eid = to_float(r[ji])
        d = to_float(r[jd]) if jd is not None else None
        life = to_float(r[jl]) if jl is not None else None
        if d is None and life is None:
            continue
        if d is None:
            d = 0.0 if life == float("inf") or life == 0 else 1.0 / life
        if life is None:
            life = float("inf") if d <= 0 else 1.0 / d
        out.append((eid, d, life))
    if not out:
        raise RuntimeError("no data rows in " + path)
    return out


def summarize(rows, prefix=""):
    crit = max(rows, key=lambda r: r[1])
    min_life = min(r[2] for r in rows)
    min_life = LIFE_CAP if (min_life == float("inf") or min_life > LIFE_CAP) else min_life
    res = {
        prefix + "max_damage": crit[1],
        prefix + "min_life": min_life,
        prefix + "log10_min_life": math.log10(min_life) if min_life > 0 else -99.0,
    }
    if not prefix:
        res["n_entities"] = len(rows)
        res["n_damage_ge_1"] = sum(1 for r in rows if r[1] >= 1.0)
        res["critical_id"] = crit[0] if crit[0] is not None else -1
    return res


def snake(s):
    return re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower() or "file"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="append", required=True)
    ap.add_argument("--id-col")
    ap.add_argument("--damage-col")
    ap.add_argument("--life-col")
    ap.add_argument("--target-life", type=float)
    ap.add_argument("--per-file", action="store_true")
    ap.add_argument("--plot", action="store_true")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    files = []
    for p in a.csv:
        files.extend(sorted(glob.glob(os.path.join(CASE_DIR, p), recursive=True)))
    if not files:
        raise RuntimeError("no files match: " + ", ".join(a.csv))
    all_rows = []
    results = {}
    for f in files:
        rows = read_damage_table(f, a.id_col, a.damage_col, a.life_col)
        log("%s: %d entities, max damage %.4g" % (os.path.basename(f), len(rows), max(r[1] for r in rows)))
        all_rows.extend(rows)
        if a.per_file:
            results.update(summarize(rows, snake(os.path.splitext(os.path.basename(f))[0]) + "_"))
    if a.list:
        return 0
    results.update(summarize(all_rows))
    if a.target_life:
        results["life_margin"] = results["min_life"] / a.target_life
    for k, v in results.items():
        if v != v or v in (float("inf"), float("-inf")):
            log("ERROR: non-finite result %s" % k)
            return 1

    if a.plot:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import ncode_plots
            results["n_plots"] = ncode_plots.make_plots(all_rows, os.path.join(CASE_DIR, "images"))
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
