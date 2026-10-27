# -*- coding: utf-8 -*-
"""
Altair SimLab 케이스 실행 (녹화 저널 템플릿 -> 배치 실행 -> 결과 정리, DDS 규약 준수)

  python simlab_render_run.py --template bracket_journal.py.tmpl --results results.csv --require bracket.op2
- {{이름}} 을 params.json 값으로, {{case_dir}} 를 케이스 폴더 경로로 채워 case_journal.py 를 만든다.
- SimLab 실행 파일: 환경변수 DDS_SIMLAB (PC Agent가 자동으로 찾은 경로를 넣는다)
- 성공하면 results.csv(이름,값) -> results.json. 실패하면 results.json 을 쓰지 않고 종료 코드 1.
"""
import argparse
import json
import os
import re
import subprocess
import sys

CASE = os.getcwd()


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


def read_kv_csv(path):
    out = {}
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for ln in f:
            parts = [p.strip() for p in ln.replace(";", ",").split(",")]
            if len(parts) >= 2 and parts[0]:
                try:
                    out[parts[0]] = float(parts[1])
                except ValueError:
                    continue  # 머리글·문자 값은 건너뛴다
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", default="case_journal.py")
    ap.add_argument("--results", default="results.csv")
    ap.add_argument("--require", action="append", default=[])
    ap.add_argument("--timeout", type=int, default=7200)
    ap.add_argument("--dry-run", action="store_true", help="저널만 만들고 실행하지 않는다")
    a = ap.parse_args()
    if os.path.exists(os.path.join(CASE, "results.json")):
        os.remove(os.path.join(CASE, "results.json"))
    params = {}
    if os.path.exists(os.path.join(CASE, "params.json")):
        params = json.load(open(os.path.join(CASE, "params.json"), encoding="utf-8"))
    values = dict(params, case_dir=CASE.replace("\\", "/"))
    try:
        journal = render(open(a.template, encoding="utf-8").read(), values)
    except Exception as e:  # noqa: BLE001
        fail(str(e))
    with open(os.path.join(CASE, a.out), "w", encoding="utf-8") as f:
        f.write(journal)
    log("journal written: " + a.out)
    if a.dry_run:
        return
    exe = os.environ.get("DDS_SIMLAB") or os.environ.get("DDS_TOOL_SIMLAB")
    if not exe:
        fail("SimLab not found on this PC. Ask the user to pick SimLab.bat in the web app (CAD/CAE tools > Altair SimLab)")
    logp = os.path.join(CASE, "simlab.log")
    with open(logp, "w", encoding="utf-8", errors="replace") as lf:
        rc = subprocess.call([exe, "-auto", os.path.join(CASE, a.out)], cwd=CASE, stdout=lf, stderr=subprocess.STDOUT,
                             timeout=a.timeout)  # TODO(사내검증): 배치 인자
    text = open(logp, encoding="utf-8", errors="replace").read()
    if rc != 0 or re.search(r"Traceback|\bError\b", text):
        fail("SimLab failed (exit %s) - see simlab.log" % rc)
    missing = [r for r in a.require if not os.path.exists(os.path.join(CASE, r))]
    if missing:
        fail("required output missing: " + ", ".join(missing))
    rp = os.path.join(CASE, a.results)
    if not os.path.exists(rp):
        fail("results csv not found: " + a.results + " (journal must write name,value lines)")
    res = read_kv_csv(rp)
    if not res:
        fail("no numeric values in " + a.results)
    json.dump(res, open(os.path.join(CASE, "results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log("results.json: " + ", ".join(sorted(res)))


if __name__ == "__main__":
    main()
