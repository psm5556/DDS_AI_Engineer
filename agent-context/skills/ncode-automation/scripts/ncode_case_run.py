# -*- coding: utf-8 -*-
"""
nCode DesignLife 케이스 실행 템플릿 (tool 키: python, 플랫폼 venv / Python 3)

  python ncode_case_run.py [--template case.flo ...] [--scale-csv loads/lc1.csv:load_scale ...]
                           [--ncode <배치 실행파일>] [--ncode-args "{flo}"]
                           [--expect "results/*.csv"] [--timeout 7200]

처리 순서
  1) params.json 읽기. '_' 로 시작하는 키는 제어용(_timeout 등).
  2) 텍스트 템플릿(플로 .flo 가 텍스트일 때, ASCII 재료/하중 정의 파일 등)의 {{name}} 치환.
     {{__case_dir__}} 는 케이스 폴더 절대경로(슬래시 구분)로 바뀐다.
     바이너리 파일(NUL 바이트 포함)은 치환하지 않고 오류로 처리한다.
  3) --scale-csv FILE:PARAM : ASCII 시계열(첫 열=시간)의 나머지 숫자 열에 params[PARAM] 배율을 곱한다.
  4) params 키가 템플릿/배율 어디에도 쓰이지 않으면 실패(오타 방지).
  5) DesignLife 배치 실행: <ncode> + ncode-args({flo} 치환). 종료코드는 참고만 한다.
  6) 판정: 표준출력과 새로 생긴 *.log 에서 ERROR_PATTERNS, --expect 파일이 새로 생겼는지 확인.
  결과 추출(results.json)은 post 단계 ncode_extract.py 가 한다. 실패하면 종료코드 1.

TODO(사내검증): DesignLife 배치 실행 명령행(실행파일 이름과 플로 실행 옵션), .flo 가 텍스트(XML)인지.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time

CASE_DIR = os.getcwd()

# TODO(사내검증): 실패한 배치 실행의 로그 문구로 보정한다.
ERROR_PATTERNS = [
    r"(?i)\berror\s*[:\]]",
    r"(?i)\bfatal\b",
    r"(?i)\bexception\b",
    r"(?i)\b(flow|run|analysis)\s+(has\s+)?(failed|aborted)",
    r"(?i)\blicen[cs]e\b.*\b(fail|denied|not available|unavailable)",
]


def log(msg):
    print("[ncode_case_run] " + str(msg))
    sys.stdout.flush()


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def fmt_num(v):
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, int):
        return str(v)
    s = "%.10g" % float(v)
    if "e" not in s and "." not in s:
        s += ".0"
    return s


def render_template(path, params, used):
    with open(path, "rb") as f:
        raw = f.read()
    if b"\x00" in raw:
        raise RuntimeError("binary file, cannot template: " + os.path.basename(path))
    enc = "utf-8"
    try:
        text = raw.decode(enc)
    except UnicodeDecodeError:
        enc = "latin-1"
        text = raw.decode(enc)

    def rep(mo):
        key = mo.group(1)
        if key == "__case_dir__":          # 플로 안의 입력/출력 경로를 케이스 폴더로 고정할 때
            return CASE_DIR.replace("\\", "/")
        if key not in params:
            raise KeyError("placeholder {{%s}} has no value in params.json" % key)
        used.add(key)
        return fmt_num(params[key])
    new = re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", rep, text)
    with open(path, "wb") as f:
        f.write(new.encode(enc))
    log("rendered " + os.path.basename(path))


def scale_csv(path, factor):
    """첫 열(시간)은 두고 나머지 숫자 열에 factor 를 곱한다. 숫자가 아닌 줄(머리글)은 그대로 둔다."""
    with open(path, "r", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        delim = "," if "," in sample else ("\t" if "\t" in sample else None)
        lines = f.read().splitlines()
    out = []
    n = 0
    for line in lines:
        cells = line.split(delim) if delim else line.split()
        try:
            vals = [float(c) for c in cells]
        except ValueError:
            out.append(line)
            continue
        vals = [vals[0]] + [v * factor for v in vals[1:]]
        sep = delim if delim else " "
        out.append(sep.join(fmt_num(v) for v in vals))
        n += 1
    if n == 0:
        raise RuntimeError("no numeric rows to scale in " + path)
    with open(path, "w") as f:
        f.write("\n".join(out) + "\n")
    log("scaled %d rows of %s by %g" % (n, os.path.basename(path), factor))


def run(cmd, stdout_name, timeout):
    log("run: " + " ".join(cmd))
    with open(os.path.join(CASE_DIR, stdout_name), "w") as fo:
        try:
            rc = subprocess.call(cmd, cwd=CASE_DIR, stdout=fo, stderr=subprocess.STDOUT, timeout=timeout,
                                 shell=(os.name == "nt" and cmd[0].lower().endswith((".bat", ".cmd"))))
        except subprocess.TimeoutExpired:
            log("timeout after %s s" % timeout)
            return -999
    log("return code %s (informational)" % rc)
    return rc


def scan_errors(paths):
    errs = []
    for p in paths:
        try:
            with open(p, "r", errors="replace") as f:
                for line in f:
                    if any(re.search(pat, line) for pat in ERROR_PATTERNS):
                        errs.append(os.path.basename(p) + ": " + line.strip())
        except IOError:
            pass
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", action="append", default=[], help="text file(s) with {{name}}")
    ap.add_argument("--scale-csv", action="append", default=[], help="FILE:PARAM")
    ap.add_argument("--ncode", help="DesignLife batch exe (default: env DDS_TOOL_NCODE)")
    ap.add_argument("--ncode-args", default="{flo}", help="args after exe; {flo} is replaced")
    ap.add_argument("--flo", help="flow file (default: the single *.flo)")
    ap.add_argument("--expect", action="append", default=[], help="glob(s) that must be newly written")
    ap.add_argument("--timeout", type=float)
    a = ap.parse_args()

    params = read_json(os.path.join(CASE_DIR, "params.json"), {}) or {}
    timeout = a.timeout or params.get("_timeout") or 7200
    flo = a.flo
    if not flo:
        cands = glob.glob(os.path.join(CASE_DIR, "*.flo"))
        if len(cands) != 1:
            raise RuntimeError("give --flo (found %d *.flo files)" % len(cands))
        flo = os.path.basename(cands[0])

    used = set()
    for t in a.template:
        render_template(os.path.join(CASE_DIR, t), params, used)
    for spec in a.scale_csv:
        path, key = spec.rsplit(":", 1)
        if key not in params:
            raise KeyError("scale param %s not in params.json" % key)
        scale_csv(os.path.join(CASE_DIR, path), float(params[key]))
        used.add(key)
    missing = [k for k in params if not k.startswith("_") and k not in used]
    if missing:
        raise RuntimeError("params not applied anywhere (typo?): " + ", ".join(sorted(missing)))

    exe = a.ncode or os.environ.get("DDS_TOOL_NCODE") or os.environ.get("DDS_NCODE")
    if not exe:
        raise RuntimeError("nCode path not set (--ncode, or ask the user to pick the nCode executable in the web app CAD/CAE tools page)")
    t0 = time.time()
    run([exe] + [x.replace("{flo}", flo) for x in a.ncode_args.split()], "ncode_stdout.txt", timeout)

    logs = [os.path.join(CASE_DIR, "ncode_stdout.txt")]
    for p in glob.glob(os.path.join(CASE_DIR, "**", "*.log"), recursive=True):
        if os.path.getmtime(p) >= t0 - 1 and not p.endswith("run.log"):
            logs.append(p)
    errs = scan_errors(logs)
    for pat in (a.expect or ["*.csv"]):
        found = [p for p in glob.glob(os.path.join(CASE_DIR, pat), recursive=True)
                 if os.path.getmtime(p) >= t0 - 1]
        if not found:
            errs.append("expected output not written: " + pat)
    if errs:
        for e in errs[:20]:
            log("ERROR: " + e)
        sys.exit(1)
    log("flow finished; run ncode_extract.py as post step")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log("FAILED: " + str(e))
        sys.exit(1)
