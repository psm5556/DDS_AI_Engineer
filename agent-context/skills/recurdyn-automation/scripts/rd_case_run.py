# -*- coding: utf-8 -*-
"""
RecurDyn 케이스 실행 템플릿 (tool 키: python, 플랫폼 venv / Python 3)

  python rd_case_run.py [--template model.rmd ...] [--solver <솔버 exe>]
                        [--solver-args "{rmd}"] [--expect "*.rad"] [--timeout 7200]
                        [--post-exe <RecurDyn.exe> --post-args "..."]

처리 순서
  1) params.json 읽기. '_' 로 시작하는 키는 제어용(_end_time, _timeout ...).
  2) 템플릿 텍스트 파일(기본: 케이스 폴더의 유일한 *.rmd)에서 {{name}} 플레이스홀더를 값으로 치환
     (케이스 폴더의 복사본을 제자리 수정). params 키가 어디에도 쓰이지 않으면 실패(오타 방지).
     제어 키도 템플릿에 {{_end_time}} 처럼 넣으면 치환된다.
  3) 솔버 실행: <solver> + solver-args({rmd} 치환). 종료코드는 참고만 한다.
  4) 판정: 솔버 표준출력과 새로 생긴 *.msg/*.log 에서 ERROR_PATTERNS 검색, --expect 패턴 파일 존재 확인
  5) (선택) --post-exe: 결과를 CSV 로 내보내는 후처리(ProcessNet 매크로 등)를 실행
  결과 추출(results.json)은 post 단계 rd_extract.py 가 한다. 실패하면 종료코드 1.

왜 플레이스홀더인가: .rmd(솔버 입력) 문법은 공개 문서로 확인하지 못했다. 사용자가 GUI 에서 내보낸 .rmd 를 열어
설계변수 값이 있는 자리를 직접 {{name}} 으로 바꾸는 방식이 가장 안전하다. TODO(사내검증): .rmd 구조.
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

# TODO(사내검증): RecurDyn 솔버 메시지의 오류 문구. 실제 실패 케이스 로그로 보정한다.
ERROR_PATTERNS = [
    r"(?i)\berror\s*[:\]]",
    r"(?i)\bfatal\b",
    r"(?i)\b(analysis|simulation|solver)\s+(is\s+)?(failed|aborted|terminated abnormally)",
    r"(?i)\bfail(ed)?\s+to\s+converge",
]
# 성공 문구가 확인되면 여기에 넣는다(비어 있으면 검사하지 않음). TODO(사내검증)
SUCCESS_PATTERNS = []


def log(msg):
    print("[rd_case_run] " + str(msg))
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


def apply_placeholders(text, values, used):
    def rep(mo):
        key = mo.group(1)
        if key not in values:
            raise KeyError("placeholder {{%s}} has no value in params.json" % key)
        used.add(key)
        return fmt_num(values[key])
    return re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", rep, text)


def render_templates(paths, params):
    used = set()
    for p in paths:
        full = os.path.join(CASE_DIR, p)
        with open(full, "r", errors="strict") as f:
            text = f.read()
        new = apply_placeholders(text, params, used)
        with open(full, "w") as f:
            f.write(new)
        log("rendered " + p)
    missing = [k for k in params if not k.startswith("_") and k not in used]
    if missing:
        raise RuntimeError("params not used in any template (typo?): " + ", ".join(sorted(missing)))


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
                lines = f.readlines()
        except IOError:
            continue
        for line in lines:
            for pat in ERROR_PATTERNS:
                if re.search(pat, line):
                    errs.append(os.path.basename(p) + ": " + line.strip())
                    break
    return errs


def has_success(paths):
    if not SUCCESS_PATTERNS:
        return True
    for p in paths:
        try:
            with open(p, "r", errors="replace") as f:
                text = f.read()
        except IOError:
            continue
        if any(re.search(s, text) for s in SUCCESS_PATTERNS):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", action="append", default=[],
                    help="text file(s) with {{name}} placeholders (default: the single *.rmd)")
    ap.add_argument("--solver", help="solver exe (default: env DDS_TOOL_RECURDYN_SOLVER)")
    ap.add_argument("--solver-args", default="{rmd}", help="args after exe; {rmd} is replaced")
    ap.add_argument("--expect", action="append", default=[], help="glob(s) that must exist after the run")
    ap.add_argument("--timeout", type=float)
    ap.add_argument("--post-exe", help="optional exporter exe (e.g. RecurDyn.exe for a ProcessNet macro)")
    ap.add_argument("--post-args", default="", help="args for --post-exe; {rmd} is replaced")
    a = ap.parse_args()

    params = read_json(os.path.join(CASE_DIR, "params.json"), {}) or {}
    timeout = a.timeout or params.get("_timeout") or 7200
    templates = a.template
    if not templates:
        cands = glob.glob(os.path.join(CASE_DIR, "*.rmd"))
        if len(cands) != 1:
            raise RuntimeError("give --template (found %d *.rmd files)" % len(cands))
        templates = [os.path.basename(cands[0])]
    render_templates(templates, params)
    rmd = [t for t in templates if t.lower().endswith(".rmd")]
    rmd = rmd[0] if rmd else templates[0]

    solver = a.solver or os.environ.get("DDS_TOOL_RECURDYN_SOLVER") or os.environ.get("DDS_RECURDYN_SOLVER")
    if not solver:
        raise RuntimeError("solver path not set (--solver or agent.toml [tools] recurdyn_solver)")
    t0 = time.time()
    cmd = [solver] + [x.replace("{rmd}", rmd) for x in a.solver_args.split()]
    run(cmd, "recurdyn_solver_stdout.txt", timeout)

    logs = [os.path.join(CASE_DIR, "recurdyn_solver_stdout.txt")]
    for pat in ("*.msg", "*.log"):
        for p in glob.glob(os.path.join(CASE_DIR, pat)):
            if os.path.getmtime(p) >= t0 - 1 and p not in logs and not p.endswith("run.log"):
                logs.append(p)
    errs = scan_errors(logs)
    for pat in (a.expect or ["*.rad"]):
        found = [p for p in glob.glob(os.path.join(CASE_DIR, pat)) if os.path.getmtime(p) >= t0 - 1]
        if not found:
            errs.append("expected output not produced: " + pat)
    if not has_success(logs):
        errs.append("no success message found in logs")
    if errs:
        for e in errs[:20]:
            log("ERROR: " + e)
        sys.exit(1)

    if a.post_exe:
        args = [x.replace("{rmd}", rmd) for x in a.post_args.split()]
        run([a.post_exe] + args, "recurdyn_post_stdout.txt", timeout)
    log("solver finished; run rd_extract.py as post step")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log("FAILED: " + str(e))
        sys.exit(1)
