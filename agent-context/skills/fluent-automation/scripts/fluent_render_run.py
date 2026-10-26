# -*- coding: utf-8 -*-
"""
Fluent 저널 템플릿 렌더 + Fluent 배치 실행 + 완료 판정 (tool 키: python, 플랫폼 venv)
  실행: python fluent_render_run.py [--template run_template.jou] [--set iterations=300]
          [--fluent <exe>] [--dim 3ddp] [--np 4] [--hidden] [--no-run] [--require outlet_temp.srp]

- {{name}} 을 params.json 값으로 치환해 run.jou 를 쓴다. 우선순위: params.json > --set 기본값 > 내장값.
  내장값: case_file = case.json inputs 또는 cwd 의 *.cas.h5 / *.cas / *.cas.gz (하나일 때).
- 주석(';') 줄의 값 없는 플레이스홀더는 그대로 둔다. 명령 줄에 값이 없으면 실패한다.
- 실행 파일: --fluent > 환경변수 DDS_FLUENT(agent.toml [tools].fluent) > 'fluent'.
- 명령: <fluent> <dim> -g -t<np> -i run.jou [-wait(Windows)] [-hidden]
  TODO(사내검증): Windows 에서 -wait 없이 런처가 바로 반환되는지, -hidden 지원 여부.
- 판정: 종료코드, 트랜스크립트(fluent.trn)/콘솔 로그의 오류 패턴, --require 파일 존재.
  결과 추출은 하지 않는다(post 단계 fluent_extract.py). 성공 시 fluent_status.json 을 남긴다.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

CASE_DIR = os.getcwd()
PH = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
FATAL = re.compile(r"(^|\s)Error:|Divergence detected|floating point exception|invalid command|"
                   r"Cannot open|No such file|license.*(fail|denied|unavailable)", re.I)


def log(msg):
    print("[fluent_render_run] " + str(msg), flush=True)


def read_json(name, default=None):
    path = os.path.join(CASE_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt(v):
    if isinstance(v, bool):
        return "yes" if v else "no"
    return repr(v) if isinstance(v, float) else str(v)


def find_case_file():
    case = read_json("case.json", {}) or {}
    names = [n for n in case.get("inputs", []) if re.search(r"\.cas(\.h5|\.gz)?$", str(n), re.I)]
    if not names:
        names = [os.path.basename(p) for p in glob.glob(os.path.join(CASE_DIR, "*.cas*"))
                 if re.search(r"\.cas(\.h5|\.gz)?$", p, re.I) and not os.path.basename(p).startswith("result")]
    return names[0] if len(names) == 1 else None


def find_template(arg):
    if arg:
        return os.path.join(CASE_DIR, arg) if not os.path.isabs(arg) else arg
    case = read_json("case.json", {}) or {}
    for n in case.get("inputs", []):
        if str(n).lower().endswith((".jou", ".jou.tmpl")) and "wrapper" not in str(n).lower():
            return os.path.join(CASE_DIR, n)
    p = os.path.join(CASE_DIR, "run_template.jou")
    if os.path.exists(p):
        return p
    raise RuntimeError("journal template not found (use --template)")


def render(text, values):
    out, missing = [], set()
    for line in text.splitlines():
        is_comment = line.lstrip().startswith(";")

        def rep(mo):
            k = mo.group(1)
            if k in values:
                return fmt(values[k])
            if not is_comment:
                missing.add(k)
            return mo.group(0)
        out.append(PH.sub(rep, line))
    if missing:
        raise KeyError("no value for placeholder(s): " + ", ".join(sorted(missing)))
    return "\n".join(out) + "\n"


def scan_errors(paths):
    bad = []
    for p in paths:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if FATAL.search(line):
                    bad.append(line.strip()[:300])
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template")
    ap.add_argument("--out", default="run.jou")
    ap.add_argument("--set", action="append", default=[], help="default value key=value (params.json wins)")
    ap.add_argument("--fluent", default=os.environ.get("DDS_FLUENT") or "fluent")
    ap.add_argument("--dim", default="3ddp", help="2d | 2ddp | 3d | 3ddp")
    ap.add_argument("--np", type=int, default=4)
    ap.add_argument("--hidden", action="store_true", help="add -hidden (Windows)")
    ap.add_argument("--no-wait", action="store_true", help="do not add -wait on Windows")
    ap.add_argument("--extra", action="append", default=[], help="extra launcher tokens")
    ap.add_argument("--no-run", action="store_true", help="render only (tool key 'fluent' runs it next)")
    ap.add_argument("--require", action="append", default=[], help="file that must exist after the run")
    ap.add_argument("--transcript", default="fluent.trn")
    ap.add_argument("--timeout", type=float, default=None)
    a = ap.parse_args()

    values = {}
    cf = find_case_file()
    if cf:
        values["case_file"] = cf
    for s in a.set:
        k, v = s.split("=", 1)
        values[k.strip()] = v.strip()
    values.update(read_json("params.json", {}) or {})

    tpl = find_template(a.template)
    with open(tpl, "r", encoding="utf-8") as f:
        text = render(f.read(), values)
    # 템플릿 주석의 한글은 '?' 로 바뀐다(Fluent 는 ASCII 저널이 안전). 명령 줄에는 ASCII 만 쓴다.
    with open(os.path.join(CASE_DIR, a.out), "w", encoding="ascii", errors="replace", newline="\n") as f:
        f.write(text)
    log("rendered %s -> %s" % (os.path.basename(tpl), a.out))
    os.makedirs(os.path.join(CASE_DIR, "images"), exist_ok=True)
    if a.no_run:
        return 0

    exe = shutil.which(a.fluent) or a.fluent
    cmd = ([ "cmd", "/c"] if exe.lower().endswith((".bat", ".cmd")) else []) + [exe, a.dim, "-g", "-t%d" % a.np, "-i", a.out]
    if os.name == "nt" and not a.no_wait:
        cmd.append("-wait")
    if a.hidden:
        cmd.append("-hidden")
    for x in a.extra:
        cmd += x.split()
    for r in a.require:               # 이전 결과 파일로 오판하지 않게 지운다
        p = os.path.join(CASE_DIR, r)
        if os.path.exists(p):
            os.remove(p)

    log("run: " + " ".join(cmd))
    console = os.path.join(CASE_DIR, "fluent_console.log")
    with open(console, "w", encoding="utf-8", errors="replace") as out:
        rc = subprocess.call(cmd, cwd=CASE_DIR, stdout=out, stderr=subprocess.STDOUT, timeout=a.timeout)
    trn = os.path.join(CASE_DIR, a.transcript)
    bad = scan_errors([trn, console])
    for b in bad[:20]:
        log("ERR: " + b)
    missing = [r for r in a.require if not os.path.exists(os.path.join(CASE_DIR, r))]
    if rc != 0 or bad or missing:
        log("FAILED: rc=%s errors=%d missing=%s" % (rc, len(bad), missing))
        return 1
    tail = open(trn, "r", encoding="utf-8", errors="replace").read() if os.path.exists(trn) else ""
    status = {"returncode": rc, "converged": 1 if "solution is converged" in tail.lower() else 0}
    with open(os.path.join(CASE_DIR, "fluent_status.json"), "w") as f:
        json.dump(status, f)
    log("done: " + json.dumps(status))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("FAILED: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
