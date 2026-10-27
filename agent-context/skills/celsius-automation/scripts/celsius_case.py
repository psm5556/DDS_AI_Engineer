# -*- coding: utf-8 -*-
"""
Cadence Celsius 케이스 실행 (템플릿 채우기 -> 배치 솔브 -> 결과 표, DDS 규약 준수)

  python celsius_case.py --template board.tmpl=board.xml --project board.xml \
      --cmd "{exe} -batch {project}" --results temps.csv --limits celsius_limits.json
- {{이름}} 을 params.json 값으로 채운다(남으면 실패). --cmd 의 {exe}=DDS_CELSIUS, {project}, {이름}=params 값.
- 결과 표(CSV)에서 부품별 최고 온도 -> tmax_<부품>, tmax_overall, margin_min(limits 있을 때) -> results.json
"""
import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys

CASE = os.getcwd()
NAME_COLS = ("name", "component", "part", "부품", "이름")
TEMP_COLS = ("tmax", "max", "temperature", "temp", "온도")


def log(msg):
    sys.stdout.write(str(msg).encode("ascii", "replace").decode("ascii") + "\n")
    sys.stdout.flush()


def fail(msg):
    log("FAIL: " + msg)
    sys.exit(1)


def render(text, values):
    pat = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
    out = pat.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)
    left = sorted(set(pat.findall(out)))
    if left:
        raise RuntimeError("unfilled placeholders: " + ", ".join(left))
    return out


def safe_key(s):
    return re.sub(r"[^0-9A-Za-z가-힣]+", "_", s).strip("_") or "part"


def read_temps(path):
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as f:
        rows = list(csv.reader(f))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise RuntimeError("result table is empty: " + os.path.basename(path))
    head = [h.strip().lower() for h in rows[0]]
    ni = next((i for i, h in enumerate(head) if any(k in h for k in NAME_COLS)), 0)
    ti = next((i for i, h in enumerate(head) if any(k in h for k in TEMP_COLS)), None)
    if ti is None:
        raise RuntimeError("temperature column not found in " + ", ".join(rows[0]))
    temps = {}
    for r in rows[1:]:
        try:
            temps[r[ni].strip()] = float(r[ti])
        except (ValueError, IndexError):
            continue
    if not temps:
        raise RuntimeError("no numeric temperatures")
    return temps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", action="append", default=[], help="src=dst")
    ap.add_argument("--cmd", default="")
    ap.add_argument("--project", default="")
    ap.add_argument("--results", required=True)
    ap.add_argument("--limits", default="")
    ap.add_argument("--timeout", type=int, default=7200)
    a = ap.parse_args()
    if os.path.exists(os.path.join(CASE, "results.json")):
        os.remove(os.path.join(CASE, "results.json"))
    params = json.load(open("params.json", encoding="utf-8")) if os.path.exists("params.json") else {}
    try:
        for t in a.template:
            src, _, dst = t.partition("=")
            with open(dst or src.replace(".tmpl", ""), "w", encoding="utf-8") as f:
                f.write(render(open(src, encoding="utf-8").read(), params))
            log("rendered: " + (dst or src))
    except Exception as e:  # noqa: BLE001
        fail(str(e))

    if a.cmd:
        exe = os.environ.get("DDS_CELSIUS") or os.environ.get("DDS_TOOL_CELSIUS") or ""
        if "{exe}" in a.cmd and not exe:
            fail("Celsius executable not found on this PC. Ask the user to pick it in the web app (CAD/CAE tools > Cadence Celsius)")
        line = a.cmd.replace("{exe}", '"%s"' % exe).replace("{project}", a.project)
        for k, v in params.items():
            line = line.replace("{%s}" % k, str(v))
        log("run: " + line)
        with open(os.path.join(CASE, "celsius.log"), "w", encoding="utf-8", errors="replace") as lf:
            rc = subprocess.call(shlex.split(line, posix=False), cwd=CASE, stdout=lf, stderr=subprocess.STDOUT, timeout=a.timeout)
        if rc != 0:
            fail("Celsius batch failed (exit %s) - see celsius.log" % rc)  # TODO(사내검증): 종료 코드 의미

    if not os.path.exists(a.results):
        fail("result table not found: " + a.results)
    try:
        temps = read_temps(a.results)
    except Exception as e:  # noqa: BLE001
        fail(str(e))
    res = {"tmax_" + safe_key(k): v for k, v in temps.items()}
    res["tmax_overall"] = max(temps.values())
    if a.limits and os.path.exists(a.limits):
        lim = json.load(open(a.limits, encoding="utf-8"))
        margins = [lim.get(k, lim.get("*")) - v for k, v in temps.items() if lim.get(k, lim.get("*")) is not None]
        if margins:
            res["margin_min"] = min(margins)
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        os.makedirs("images", exist_ok=True)
        top = sorted(temps.items(), key=lambda x: -x[1])[:15]
        fig, ax = plt.subplots(figsize=(6, 0.35 * len(top) + 1.2), dpi=120)
        ax.barh([k for k, _ in top][::-1], [v for _, v in top][::-1], color="#ef6c00")
        ax.set_xlabel("Tmax")
        fig.tight_layout()
        fig.savefig(os.path.join("images", "component_tmax.png"))
        plt.close(fig)
    except Exception as e:  # noqa: BLE001
        log("warn: plot failed: %s" % e)
    json.dump(res, open("results.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log("results.json: %d values, tmax_overall=%.3f" % (len(res), res["tmax_overall"]))


if __name__ == "__main__":
    main()
