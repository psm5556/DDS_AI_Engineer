# -*- coding: utf-8 -*-
"""
Adams 결과(.res, XRF XML) -> results.json  (tool 키: python, 플랫폼 venv / Python 3, 표준 라이브러리만 사용)

  python adams_extract.py [--res case.res] [--list]
                          [--metric max_arm_angle=absmax:REQ_ARM.THETA ...]
                          [--metric f_at_05=at0.5:REQ_FORCE.FMAG]
                          [--plot REQ_ARM.THETA --plot REQ_FORCE.FMAG]

- 시계열 키는 "<Entity 이름>.<Component 이름>" 이다. 먼저 --list 로 이름을 확인한다.
- stat: max, min, absmax, mean, rms, final, initial, range, at<t>(시간 t 의 선형보간 값)
- --metric 이 없으면 모든 시계열의 absmax 를 absmax_<entity>_<comp> 키로 쓴다.
- adams_run_info.json(adams_case_run.py 가 기록)의 end_time 이 있으면 마지막 시간이 end_time 에
  도달했는지 확인한다(도중 종료 감지). 미달이면 실패.
- 기존 results.json 이 있으면 합친다. 실패 시 results.json 을 쓰지 않고 종료코드 1.

XRF 구조 가정 (TODO(사내검증): 실제 .res 로 확인)
  <StepMap><Entity name=...><Component name=... id=k/></Entity>...</StepMap>
  <Data ...><Step type="dynamic|static|kinematic">공백 구분 숫자들</Step>...</Data>
  Step 숫자 열의 순서 = Component id 순서(최소 id 부터). type="input" Step 은 무시한다.
"""
import argparse
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

CASE_DIR = os.getcwd()


def log(msg):
    print("[adams_extract] " + str(msg))
    sys.stdout.flush()


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def read_xrf(path):
    """-> {"time": [..], "series": {"ENT.COMP": [..]}, "step_types": [..]}"""
    maps = []            # [(map_name, [(id, entity, comp)])]
    cur_map = None
    cur_ent = None
    rows = []            # [(type, [floats])]
    for event, el in ET.iterparse(path, events=("start", "end")):
        tag = _local(el.tag)
        if event == "start":
            if tag == "StepMap":
                cur_map = (el.get("name") or "map%d" % len(maps), [])
                maps.append(cur_map)
            elif tag == "Entity" and cur_map is not None:
                cur_ent = el.get("name") or el.get("entity") or "entity"
            elif tag == "Component" and cur_map is not None:
                cid = el.get("id")
                if cid is not None:
                    cur_map[1].append((int(cid), cur_ent, el.get("name") or "c" + cid))
        else:
            if tag == "StepMap":
                cur_map = None
            elif tag == "Entity":
                cur_ent = None
            elif tag == "Step":
                typ = (el.get("type") or "").lower()
                if typ != "input":
                    try:
                        rows.append((typ, [float(x) for x in (el.text or "").split()]))
                    except ValueError:
                        pass
                el.clear()
    if not maps:
        raise RuntimeError("no StepMap in %s (is it an XRF .res? add RESULTS/XRF to the ADM)" % path)
    best = None
    for name, comps in maps:
        n = len(comps)
        good = [r for r in rows if len(r[1]) == n]
        if good and (best is None or len(good) > len(best[2])):
            best = (name, sorted(comps), good)
    if best is None:
        raise RuntimeError("no Step rows match any StepMap component count in %s" % path)
    _, comps, good = best
    skipped = len(rows) - len(good)
    if skipped:
        log("WARNING: %d step rows skipped (length mismatch)" % skipped)
    base = comps[0][0]
    series = {}
    time_key = None
    for cid, ent, comp in comps:
        key = "%s.%s" % (ent, comp)
        series[key] = [r[1][cid - base] for r in good]
        if time_key is None and (str(ent).lower() == "time" or str(comp).upper() == "TIME"):
            time_key = key
    if time_key is None:
        raise RuntimeError("no time component in StepMap")
    t = series.pop(time_key)
    return {"time": t, "series": series, "step_types": [r[0] for r in good]}


# ------------------------------------------------------------------ 통계
def stat(name, t, y):
    if not y:
        raise ValueError("empty series")
    s = name.lower()
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
    s = re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower()
    return s or "value"


def find_series(series, key):
    if key in series:
        return series[key]
    low = dict((k.lower(), k) for k in series)
    if key.lower() in low:
        return series[low[key.lower()]]
    tails = [k for k in series if k.lower().endswith(key.lower())]   # ".model.REQ1.X" 같은 전체 이름 대응
    if len(tails) == 1:
        return series[tails[0]]
    raise KeyError("series not found: %s (use --list)" % key)


def compute_metrics(data, metric_specs):
    t, series = data["time"], data["series"]
    out = {}
    if not metric_specs:
        for k, y in series.items():
            out["absmax_" + snake(k)] = stat("absmax", t, y)
    for spec in metric_specs:
        name, rhs = spec.split("=", 1)
        st, key = rhs.split(":", 1)
        out[snake(name)] = stat(st, t, find_series(series, key))
    out["end_time_reached"] = t[-1] if t else 0.0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res")
    ap.add_argument("--metric", action="append", default=[])
    ap.add_argument("--plot", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--end-time", type=float)
    a = ap.parse_args()

    info = {}
    p = os.path.join(CASE_DIR, "adams_run_info.json")
    if os.path.isfile(p):
        with open(p) as f:
            info = json.load(f)
    res = a.res or info.get("res") or "case.res"
    data = read_xrf(os.path.join(CASE_DIR, res))
    log("read %s: %d steps, %d series" % (res, len(data["time"]), len(data["series"])))
    if a.list:
        for k in sorted(data["series"]):
            print("  " + k)
        return 0

    end_time = a.end_time if a.end_time is not None else info.get("end_time")
    if end_time:
        last = data["time"][-1] if data["time"] else 0.0
        if last < end_time - max(1e-9, 1e-6 * abs(end_time)):
            log("ERROR: simulation stopped at t=%g < end_time=%g" % (last, end_time))
            return 1

    results = compute_metrics(data, a.metric)
    for k, v in results.items():
        if not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
            log("ERROR: non-finite result %s=%r" % (k, v))
            return 1

    if a.plot:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import adams_plots
            results["n_plots"] = adams_plots.make_plots(data, a.plot, os.path.join(CASE_DIR, "images"))
        except Exception as e:   # 이미지 실패는 케이스 실패로 보지 않는다
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
