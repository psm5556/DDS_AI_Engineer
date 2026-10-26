# -*- coding: utf-8 -*-
"""
Flotherm FloSCRIPT 템플릿 렌더 (+ 선택: Flotherm 배치 실행) (tool 키: python, 플랫폼 venv)
  실행: python flotherm_prepare.py [--template case.xml.tmpl] [--out case_script.xml]
          [--set project_name=board] [--run] [--flotherm <exe>] [--require component_temps.csv]

- 템플릿(GUI 에서 녹화한 FloSCRIPT 에 {{name}} 을 넣은 파일)의 플레이스홀더를 params.json 값으로 치환한다.
  우선순위: params.json > --set 기본값. XML 주석(<!-- -->) 안의 값 없는 플레이스홀더는 그대로 둔다.
- 렌더 결과가 올바른 XML 인지 xml.etree 로 검사한다(깨진 XML 이면 실패).
- --run 이 없으면 렌더만 한다. 다음 단계에서 tool 키 'flotherm' 이 '{flotherm} -b case_script.xml' 로 실행한다.
- --run: <flotherm> -b <out> 을 직접 실행한다(실행 파일: --flotherm > 환경변수 DDS_FLOTHERM).
  TODO(사내검증): 'flotherm -b <FloSCRIPT.xml>' 배치 호출 형식, 종료코드 의미, 환경 설정 배치파일(flotherm.bat) 필요 여부.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

CASE_DIR = os.getcwd()
PH = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
COMMENT = re.compile(r"(<!--.*?-->)", re.S)


def log(msg):
    print("[flotherm_prepare] " + str(msg), flush=True)


def read_json(name, default=None):
    path = os.path.join(CASE_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def fmt(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    return repr(v) if isinstance(v, float) else str(v)


def xml_escape(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render(text, values):
    missing = set()
    parts = COMMENT.split(text)
    out = []
    for part in parts:
        is_comment = part.startswith("<!--")

        def rep(mo):
            k = mo.group(1)
            if k in values:
                return xml_escape(fmt(values[k]))
            if not is_comment:
                missing.add(k)
            return mo.group(0)
        out.append(PH.sub(rep, part))
    if missing:
        raise KeyError("no value for placeholder(s): " + ", ".join(sorted(missing)))
    return "".join(out)


def find_template(arg):
    if arg:
        return arg if os.path.isabs(arg) else os.path.join(CASE_DIR, arg)
    cands = glob.glob(os.path.join(CASE_DIR, "*.xml.tmpl"))
    if len(cands) == 1:
        return cands[0]
    raise RuntimeError("FloSCRIPT template not found or ambiguous (use --template): %s" % cands)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template")
    ap.add_argument("--out", default="case_script.xml")
    ap.add_argument("--set", action="append", default=[], help="default key=value")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--flotherm", default=os.environ.get("DDS_FLOTHERM") or "flotherm")
    ap.add_argument("--require", action="append", default=[], help="file that must exist after --run")
    ap.add_argument("--timeout", type=float, default=None)
    a = ap.parse_args()

    values = {}
    for s in a.set:
        k, v = s.split("=", 1)
        values[k.strip()] = v.strip()
    values.update(read_json("params.json", {}) or {})

    tpl = find_template(a.template)
    with open(tpl, "r", encoding="utf-8") as f:
        text = render(f.read(), values)
    ET.fromstring(text.encode("utf-8"))          # XML 문법 검사
    out = os.path.join(CASE_DIR, a.out)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    log("rendered %s -> %s" % (os.path.basename(tpl), a.out))
    os.makedirs(os.path.join(CASE_DIR, "images"), exist_ok=True)
    if not a.run:
        return 0

    for r in a.require:
        p = os.path.join(CASE_DIR, r)
        if os.path.exists(p):
            os.remove(p)
    exe = shutil.which(a.flotherm) or a.flotherm
    cmd = (["cmd", "/c"] if exe.lower().endswith((".bat", ".cmd")) else []) + [exe, "-b", a.out]
    log("run: " + " ".join(cmd))
    with open(os.path.join(CASE_DIR, "flotherm_console.log"), "w", encoding="utf-8", errors="replace") as fo:
        rc = subprocess.call(cmd, cwd=CASE_DIR, stdout=fo, stderr=subprocess.STDOUT, timeout=a.timeout)
    missing = [r for r in a.require if not os.path.exists(os.path.join(CASE_DIR, r))]
    if rc != 0 or missing:
        log("FAILED: rc=%s missing=%s (see flotherm_console.log)" % (rc, missing))
        return 1
    log("done")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("FAILED: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
