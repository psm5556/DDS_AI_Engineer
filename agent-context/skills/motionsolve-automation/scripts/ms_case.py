# -*- coding: utf-8 -*-
"""
Altair MotionSolve 케이스 템플릿 (XML 모델, DDS 규약 준수)

흐름: params.json -> 모델 값 바꾸기({{자리표시자}} 또는 ms_map.json 의 요소 id·속성) -> case_model.xml
      -> motionsolve.bat 배치 해석 -> 결과 CSV(.csv, 없으면 .plt 텍스트) -> 열마다 max/min/final -> results.json
실행 (tool 키 'motionsolve_python'):
  python ms_case.py model=door.xml inspect=1
  python ms_case.py model=door.xml map=ms_map.json image=1
솔버 경로: 환경변수 DDS_MOTIONSOLVE (PC Agent가 자동으로 찾은 경로를 넣는다)
"""
import csv
import glob
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

CASE = os.getcwd()


def log(msg):
    sys.stdout.write(str(msg).encode("ascii", "replace").decode("ascii") + "\n")
    sys.stdout.flush()


def fail(msg):
    log("FAIL: " + msg)
    sys.exit(1)


def args():
    a = {}
    for x in sys.argv[1:]:
        k, _, v = x.partition("=")
        a[k.strip()] = v.strip() if _ else "1"
    return a


def flag(a, k, default="0"):
    return a.get(k, default) not in ("0", "", "false", "no")


def read_json(name, default=None):
    p = os.path.join(CASE, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(name, data):
    with open(os.path.join(CASE, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def render(text, params):
    """{{이름}} 자리표시자를 채운다. 못 채운 자리표시자가 남으면 실패."""
    out = re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", lambda m: str(params[m.group(1)]) if m.group(1) in params else m.group(0), text)
    left = sorted(set(re.findall(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}", out)))
    if left:
        raise RuntimeError("unfilled placeholders: " + ", ".join(left))
    return out


def apply_map(xml_text, params, mp, strict):
    root = ET.fromstring(xml_text)
    specs = mp.get("params") or {}
    for k, v in params.items():
        s = specs.get(k)
        if not s:
            if strict:
                raise RuntimeError("param '%s' not in ms_map.json" % k)
            continue
        hits = [e for e in root.iter(s["tag"]) if e.get("id") == str(s["id"])]
        if not hits:
            raise RuntimeError("element %s id=%s not found" % (s["tag"], s["id"]))
        hits[0].set(s["attr"], repr(float(v) * float(s.get("scale", 1.0))))
    return ET.tostring(root, encoding="unicode")


def read_table(path):
    """결과 표(CSV 또는 공백 구분 텍스트) -> (열 이름들, 행들). 첫 숫자 행 앞의 줄을 머리글로 본다."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    rows, header = [], None
    for ln in lines:
        parts = next(csv.reader([ln])) if "," in ln else ln.split()
        try:
            rows.append([float(p) for p in parts])
        except ValueError:
            if not rows:
                header = parts
    if not rows:
        raise RuntimeError("no numeric rows in " + os.path.basename(path))
    n = len(rows[0])
    if not header or len(header) != n:
        header = ["time"] + ["col%d" % i for i in range(1, n)]
    return header, [r for r in rows if len(r) == n]


def main():
    a = args()
    if os.path.exists(os.path.join(CASE, "results.json")):
        os.remove(os.path.join(CASE, "results.json"))
    model = a.get("model") or (glob.glob(os.path.join(CASE, "*.xml.tmpl")) + glob.glob(os.path.join(CASE, "*.xml")) or [None])[0]
    if not model or not os.path.exists(model):
        fail("model .xml not found (give model=<file>)")
    text = open(model, encoding="utf-8").read()

    if flag(a, "inspect"):
        root = ET.fromstring(re.sub(r"\{\{\s*\w+\s*\}\}", "0", text))
        info = [{"tag": e.tag, "id": e.get("id"), "label": e.get("label"), "attrs": sorted(k for k in e.attrib if k not in ("id", "label"))}
                for e in root.iter() if e.get("id")]
        write_json("model_info.json", {"elements": info[:2000]})
        log("model_info.json: %d elements" % len(info))
        return

    params = read_json("params.json", {}) or {}
    mp = read_json(a.get("map", "ms_map.json"), {}) or {}
    try:
        if "{{" in text:
            text = render(text, params)
        elif params:
            text = apply_map(text, params, mp, flag(a, "strict", "1"))
    except Exception as e:  # noqa: BLE001
        fail(str(e))
    deck = os.path.join(CASE, "case_model.xml")
    with open(deck, "w", encoding="utf-8") as f:
        f.write(text)

    solver = os.environ.get("DDS_MOTIONSOLVE") or os.environ.get("DDS_TOOL_MOTIONSOLVE")
    if not solver:
        fail("MotionSolve not found on this PC. Ask the user to pick motionsolve.bat in the web app (CAD/CAE tools > Altair MotionSolve)")
    log("run: %s %s" % (solver, deck))
    with open(os.path.join(CASE, "solver.log"), "w", encoding="utf-8", errors="replace") as lf:
        rc = subprocess.call([solver, deck], cwd=CASE, stdout=lf, stderr=subprocess.STDOUT,
                             timeout=int(a.get("timeout", "3600")))
    logtext = open(os.path.join(CASE, "solver.log"), encoding="utf-8", errors="replace").read()
    if rc != 0 or re.search(r"\bERROR\b", logtext):
        fail("solver failed (exit %s) - see solver.log" % rc)

    files = glob.glob(os.path.join(CASE, a.get("results", "*.csv"))) or glob.glob(os.path.join(CASE, "*.plt"))
    if not files:
        fail("no result table (.csv/.plt) - add a CSV output request to the model")
    header, rows = read_table(sorted(files, key=os.path.getmtime)[-1])
    cols = {name: i for i, name in enumerate(header)}
    wanted = {k: s.get("column", "") for k, s in (mp.get("outputs") or {}).items()}
    if not wanted:
        wanted = {re.sub(r"\W+", "_", h).strip("_").lower() or "c%d" % i: h for i, h in enumerate(header) if i > 0}
    results = {}
    for key, col in wanted.items():
        idx = next((i for name, i in cols.items() if col and col.lower() in name.lower()), None)
        if idx is None:
            fail("result column not found: " + col)
        ys = [r[idx] for r in rows]
        results[key + "_max"], results[key + "_min"], results[key + "_final"] = max(ys), min(ys), ys[-1]
        if flag(a, "image", "1"):
            try:
                import matplotlib
                matplotlib.use("Agg")
                import matplotlib.pyplot as plt
                os.makedirs(os.path.join(CASE, "images"), exist_ok=True)
                fig, ax = plt.subplots(figsize=(5.4, 3.4), dpi=120)
                ax.plot([r[0] for r in rows], ys, lw=1.5)
                ax.set_xlabel(header[0])
                ax.set_ylabel(key)
                ax.grid(alpha=0.3)
                fig.tight_layout()
                fig.savefig(os.path.join(CASE, "images", key + ".png"))
                plt.close(fig)
            except Exception as e:  # noqa: BLE001
                log("warn: plot failed: %s" % e)
    write_json("results.json", results)
    log("results.json: " + ", ".join(sorted(results)))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        fail(str(e))
