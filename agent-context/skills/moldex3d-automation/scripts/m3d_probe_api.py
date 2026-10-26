# -*- coding: utf-8 -*-
"""Moldex3D 파이썬 환경/API 탐색 (tool: moldex3d, py2/py3 겸용)

실행:  <moldex3d_python> m3d_probe_api.py [--pattern moldex|m3d|mdx] [--import extra.module]
출력:  api_probe.txt  - 파이썬 버전, 실행파일, sys.path, 이름이 패턴과 맞는 모듈 목록과 각 모듈의 공개 이름/첫 줄 doc
       설치 폴더(실행파일 상위 2단계)에서 이름이 패턴과 맞는 .py/.pyd/.dll/.exe 파일 목록
라이선스를 잡지 않는다(모듈 import 만 한다). results.json 을 쓰지 않는다(탐색 전용, run_script 로 1회 실행).

TODO(사내검증): Moldex3D API 모듈 이름은 버전마다 다르다. 이 결과로 m3d_case_run.py 의 훅 함수를 채운다.
"""
from __future__ import print_function

import argparse
import os
import pkgutil
import re
import sys
import traceback


def first_line(doc):
    if not doc:
        return ""
    return doc.strip().splitlines()[0][:160]


def describe_module(name, out, depth=0):
    try:
        mod = __import__(name, fromlist=["*"])
    except Exception as e:
        out.append("  import %s FAILED: %s" % (name, e))
        return
    out.append("MODULE %s  file=%s" % (name, getattr(mod, "__file__", "?")))
    out.append("  doc: " + first_line(getattr(mod, "__doc__", "")))
    for attr in sorted(dir(mod)):
        if attr.startswith("_"):
            continue
        try:
            obj = getattr(mod, attr)
        except Exception as e:
            out.append("    %s  <getattr failed: %s>" % (attr, e))
            continue
        kind = type(obj).__name__
        out.append("    %-40s %-18s %s" % (attr, kind, first_line(getattr(obj, "__doc__", ""))))
        if depth == 0 and kind in ("type", "classobj"):
            members = [m for m in dir(obj) if not m.startswith("_")]
            if members:
                out.append("      members: " + ", ".join(members[:80]))


def scan_files(root, pat, limit=400):
    hits = []
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            if pat.search(fn) and fn.lower().endswith((".py", ".pyd", ".dll", ".exe", ".bat")):
                hits.append(os.path.join(dirpath, fn))
                if len(hits) >= limit:
                    return hits
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="moldex|m3d|mdx|coretech")
    ap.add_argument("--import", dest="extra", action="append", default=[])
    ap.add_argument("--no-scan", action="store_true")
    a = ap.parse_args()
    pat = re.compile(a.pattern, re.IGNORECASE)

    out = []
    out.append("python: " + sys.version.replace("\n", " "))
    out.append("executable: " + sys.executable)
    out.append("cwd: " + os.getcwd())
    out.append("sys.path:")
    for p in sys.path:
        out.append("  " + str(p))

    names = set(a.extra)
    for info in pkgutil.iter_modules():
        name = info[1]
        if pat.search(name):
            names.add(name)
    out.append("")
    out.append("matching modules: %s" % sorted(names))
    for name in sorted(names):
        out.append("")
        describe_module(name, out)

    if not a.no_scan:
        root = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
        out.append("")
        out.append("files under %s matching pattern:" % root)
        for h in scan_files(root, pat):
            out.append("  " + h)

    with open("api_probe.txt", "w") as f:
        f.write("\n".join(out) + "\n")
    print("[m3d_probe] wrote api_probe.txt (%d lines, %d modules)" % (len(out), len(names)))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
