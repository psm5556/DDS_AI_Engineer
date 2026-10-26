# -*- coding: utf-8 -*-
"""
COMSOL batch 드라이버 (tool 키: python, 플랫폼 venv) — MPh 를 못 쓸 때의 대안
  실행: python comsol_batch_run.py [--comsol <exe>] [--model m.mph] [--study std1]
          [--methodcall exportResults] --table results_table.csv [--map "Max T (K)=max_temp"]
          [--punit width=mm] [--np 4] [--extra "-nosave"]

순서: params.json -> comsol batch -inputfile m.mph -outputfile out.mph -pname ... -plist ... -batchlog batch.log
      -> 모델 안의 Table export(또는 -methodcall 메서드)가 쓴 표 파일 파싱 -> results.json
- 실행 파일: --comsol, 없으면 환경변수 DDS_COMSOL(플랫폼이 agent.toml [tools].comsol 로 설정), 없으면 'comsol'.
  Windows 에서 'comsol' 이 batch 하위명령을 못 받으면 comsolbatch.exe 를 지정한다. TODO(사내검증)
- 여러 파라미터를 한 번에 넘기는 -pname/-plist 형식은 버전 문서를 확인한다. TODO(사내검증)
  (불확실하면 MPh 템플릿 comsol_mph_run.py 를 쓴다.)
- 표 파일 형식: '%' 로 시작하는 줄은 머리말, 마지막 '%' 줄이 열 이름, 숫자 행 중 마지막 행을 쓴다.
  열 구분자는 콤마(.csv) 또는 공백/탭(.txt). 열 이름은 --map 으로 results 키에 연결한다(없으면 자동 snake).
"""
import argparse
import glob
import json
import math
import os
import re
import shutil
import subprocess
import sys

CASE_DIR = os.getcwd()


def log(msg):
    print("[comsol_batch_run] " + str(msg), flush=True)


def snake(s):
    s = re.sub(r"\(.*?\)|\[.*?\]", "", str(s))   # 단위 표기 제거
    return re.sub(r"[^0-9a-z]+", "_", s.lower()).strip("_")


def read_json(name, default=None):
    path = os.path.join(CASE_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt(v):
    if isinstance(v, bool):
        return "1" if v else "0"
    return repr(v) if isinstance(v, float) else str(v)


def find_model(arg):
    if arg:
        return arg
    case = read_json("case.json", {}) or {}
    for name in case.get("inputs", []):
        if str(name).lower().endswith(".mph"):
            return name
    cands = [os.path.basename(c) for c in glob.glob(os.path.join(CASE_DIR, "*.mph")) if not os.path.basename(c).startswith("out")]
    if len(cands) == 1:
        return cands[0]
    raise RuntimeError("model .mph not found or ambiguous (use --model)")


def parse_table(path):
    """COMSOL 표 내보내기(.csv/.txt) -> (열 이름 목록, 마지막 숫자 행)."""
    header, last = None, None
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("%"):
                header = s.lstrip("%").strip()
                continue
            toks = [t for t in re.split(r"[,;\t]|\s{1,}", s) if t] if "," not in s else [t.strip() for t in s.split(",")]
            try:
                last = [float(t) for t in toks]
            except ValueError:
                continue
    if last is None:
        raise ValueError("no numeric rows in " + path)
    names = None
    if header:
        if "," in header:
            names = [h.strip().strip('"') for h in header.split(",")]
        else:
            names = [h.strip() for h in re.split(r"\t|\s{2,}", header) if h.strip()]
    if not names or len(names) != len(last):
        names = ["col%d" % i for i in range(len(last))]
    return names, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--comsol", default=os.environ.get("DDS_COMSOL") or "comsol")
    ap.add_argument("--model")
    ap.add_argument("--output", default="out.mph")
    ap.add_argument("--study", default=None, help="study tag, e.g. std1")
    ap.add_argument("--methodcall", default=None, help="model method name to run (Application Builder method)")
    ap.add_argument("--table", action="append", default=[], help="table file written by the model (csv/txt)")
    ap.add_argument("--map", action="append", default=[], help="'column name=result_key'")
    ap.add_argument("--punit", action="append", default=[], help="param=unit -> -punit")
    ap.add_argument("--np", type=int, default=None)
    ap.add_argument("--extra", action="append", default=[], help="extra raw batch option tokens")
    ap.add_argument("--timeout", type=float, default=None)
    a = ap.parse_args()

    params = dict((k, v) for k, v in (read_json("params.json", {}) or {}).items() if not str(k).startswith("_"))
    model = find_model(a.model)
    if not a.table:
        log("ERROR: --table is required (file exported by the model)")
        return 2

    exe = shutil.which(a.comsol) or a.comsol
    cmd = [exe]
    if os.path.basename(exe).lower().startswith("comsolbatch"):
        pass                      # comsolbatch.exe 는 'batch' 하위명령 없이 옵션만 받는다
    else:
        cmd.append("batch")
    if exe.lower().endswith((".bat", ".cmd")):
        cmd = ["cmd", "/c"] + cmd
    cmd += ["-inputfile", model, "-outputfile", a.output, "-batchlog", "batch.log"]
    if a.np:
        cmd += ["-np", str(a.np)]
    if a.study:
        cmd += ["-study", a.study]
    if params:
        names = list(params)
        cmd += ["-pname", ",".join(names), "-plist", ",".join(fmt(params[k]) for k in names)]  # TODO(사내검증): 다중 파라미터 형식
        if a.punit:
            units = dict(x.split("=", 1) for x in a.punit)
            cmd += ["-punit", ",".join(units.get(k, "") for k in names)]                       # TODO(사내검증): 빈 단위 허용 여부
    if a.methodcall:
        cmd += ["-methodcall", a.methodcall]
    for x in a.extra:
        cmd += x.split()

    for t in a.table:                       # 이전 결과가 남아 있으면 오판하므로 지운다
        p = os.path.join(CASE_DIR, t)
        if os.path.exists(p):
            os.remove(p)

    log("run: " + " ".join(cmd))
    rc = subprocess.call(cmd, cwd=CASE_DIR, timeout=a.timeout)
    blog = os.path.join(CASE_DIR, "batch.log")
    text = open(blog, "r", encoding="utf-8", errors="replace").read() if os.path.exists(blog) else ""
    for line in text.splitlines():
        if re.search(r"error|exception", line, re.I):
            log("LOG: " + line.strip()[:300])
    if rc != 0:
        log("ERROR: comsol exited with code %d" % rc)
        return 1

    mapping = dict(x.split("=", 1) for x in a.map)
    results = {}
    for t in a.table:
        path = os.path.join(CASE_DIR, t)
        if not os.path.exists(path):
            log("ERROR: table not written: %s (export node / method not executed?)" % t)
            return 1
        names, row = parse_table(path)
        for n, v in zip(names, row):
            key = mapping.get(n) if mapping else snake(n)
            if not key:
                continue
            if math.isnan(v) or math.isinf(v):
                log("ERROR: %s is not finite" % n)
                return 1
            results[key] = v
    if not results:
        log("ERROR: no values mapped (check --map column names)")
        return 1
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
