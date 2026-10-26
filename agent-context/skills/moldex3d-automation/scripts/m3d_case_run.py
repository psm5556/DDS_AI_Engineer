# -*- coding: utf-8 -*-
"""Moldex3D 케이스 실행 드라이버 템플릿 (tool: moldex3d, py2/py3 겸용)

Moldex3D 의 API/배치 호출은 버전마다 다르고 이 템플릿 작성 시 확인하지 못했다. 그래서 확인된 부분만 실제로 수행하고,
툴 호출은 사내에서 확인한 방법을 꽂아 쓰는 구조다.

  1) params.json 검증 (매핑 안 된 숫자 키 -> 실패)
  2) 텍스트 입력 템플릿 렌더 (--template src.tpl=dst, {{key}} 치환, 남은 자리표시자 -> 실패)
  3) (선택) apply_params_api(): Moldex3D Python API 로 해석조건 변경  <- TODO(사내검증) 훅
  4) (선택) 배치 명령 실행: '--' 뒤 명령을 그대로 실행. {project} {project_dir} {case_dir} {params키} 치환
  5) 완료 판정: 종료코드 + 로그 파일(LOG_GLOBS)의 실패/성공 문구
results.json 은 쓰지 않는다. post 단계 m3d_extract.py(python) 가 내보낸 결과 파일로 쓴다.

실행 예:
  <moldex3d_python> m3d_case_run.py --project cover/cover.<ext> --template cond.tpl=cond.txt -- <사내확인 배치exe> {project}
"""
from __future__ import print_function

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
import traceback

# ===================== 사용자 설정 =====================
# params.json 키 -> API 로 바꿀 해석조건 이름(훅에 그대로 전달). 템플릿 치환만 쓰면 비워 둔다.
PARAM_MAP = {
    # "melt_temp": "<api 의 용융온도 항목>",   # TODO(사내검증)
}
# 프로젝트 파일 확장자 후보. TODO(사내검증): 사용하는 Moldex3D Studio 버전의 프로젝트/런 파일 확장자
PROJECT_EXTS = []
# 완료 판정용 로그 파일(케이스 폴더 기준 glob)과 문구(정규식). TODO(사내검증): 실제 솔버 로그 이름/문구
LOG_GLOBS = ["m3d_run.log", "*.log", "*/*.log"]   # 실행 이후 수정된 파일만 검사
FAIL_PATTERNS = [r"\berror\b", r"\bfailed\b", r"\bdiverge"]   # 오탐이 있으면 사내 로그에 맞게 좁힌다
SUCCESS_PATTERNS = []          # 예: [r"analysis\s+completed"]  비우면 검사하지 않음
RUN_TIMEOUT_SEC = 8 * 3600
STRICT_PARAMS = True
# =======================================================

try:
    STRING_TYPES = (basestring,)  # noqa: F821  (py2)
except NameError:
    STRING_TYPES = (str,)


def log(msg):
    line = "[m3d_case_run] " + msg
    try:
        print(line)
    except UnicodeError:
        print(line.encode("ascii", "backslashreplace").decode("ascii"))
    sys.stdout.flush()


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def to_float(v):
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fmt_value(v):
    f = to_float(v)
    if f is not None and abs(f - round(f)) < 1e-12 and abs(f) < 1e15:
        return str(int(round(f)))
    return repr(f) if f is not None else str(v)


def find_project(case_dir, given):
    if given:
        return os.path.abspath(given)
    if not PROJECT_EXTS:
        return ""
    found = []
    for ext in PROJECT_EXTS:
        found += glob.glob(os.path.join(case_dir, "*" + ext)) + glob.glob(os.path.join(case_dir, "*", "*" + ext))
    if len(found) == 1:
        return os.path.abspath(found[0])
    raise RuntimeError("cannot decide project file; pass --project. candidates=%s" % found)


def render_template(src, dst, params):
    with open(src, "r") as f:
        text = f.read()
    for k, v in params.items():
        text = text.replace("{{" + k + "}}", fmt_value(v))
    left = re.findall(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", text)
    if left:
        raise RuntimeError("unresolved placeholders in %s: %s" % (src, sorted(set(left))))
    with open(dst, "w") as f:
        f.write(text)
    log("rendered %s -> %s" % (src, dst))


def apply_params_api(project, edits):
    """Moldex3D Python API 로 해석조건을 바꾸는 훅.
    TODO(사내검증): m3d_probe_api.py 의 api_probe.txt 와 Moldex3D API 문서로 확인한 호출만 여기에 쓴다.
      - 프로젝트/런 열기, 해석조건(용융온도, 금형온도, 충전시간, 보압, 냉각시간) 변경, 저장, 닫기
    확인 전에는 실행되지 않도록 예외를 낸다."""
    raise NotImplementedError("apply_params_api is not implemented; fill it with verified Moldex3D API calls")


def scan_logs(case_dir, t0):
    fails, oks = [], []
    seen = set()
    for g in LOG_GLOBS:
        for p in glob.glob(os.path.join(case_dir, g)):
            if p in seen or os.path.getmtime(p) < t0 or os.path.basename(p) == "run.log":
                continue
            seen.add(p)
            try:
                with open(p, "rb") as f:
                    text = f.read().decode("utf-8", "replace")
            except Exception:
                continue
            for line in text.splitlines():
                if any(re.search(r, line, re.IGNORECASE) for r in FAIL_PATTERNS):
                    fails.append("%s: %s" % (os.path.basename(p), line.strip()[:200]))
                if any(re.search(r, line, re.IGNORECASE) for r in SUCCESS_PATTERNS):
                    oks.append(p)
    return fails, oks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project")
    ap.add_argument("--template", action="append", default=[], help="src.tpl=dst (repeatable)")
    ap.add_argument("cmd", nargs=argparse.REMAINDER, help="-- <batch command with {placeholders}>")
    a = ap.parse_args()

    case_dir = os.getcwd()
    params = load_json("params.json") if os.path.isfile("params.json") else {}
    log("params: %s" % params)

    templated = set()
    for spec in a.template:
        if "=" not in spec:
            raise RuntimeError("--template needs src=dst, got %s" % spec)
        src, dst = spec.split("=", 1)
        with open(src, "r") as f:
            templated.update(re.findall(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", f.read()))
        render_template(src, dst, params)

    cmd = [c for c in a.cmd if c != "--"]
    in_cmd = set()
    for c in cmd:
        in_cmd.update(re.findall(r"\{([A-Za-z0-9_]+)\}", c))

    edits = []
    for k, raw in params.items():
        val = to_float(raw)
        if k in PARAM_MAP:
            edits.append((k, PARAM_MAP[k], val))
        elif val is not None and k not in templated and k not in in_cmd:
            if STRICT_PARAMS:
                raise RuntimeError("params key not used anywhere (PARAM_MAP/template/cmd): %s" % k)
            log("WARNING: params key ignored: %s" % k)

    project = find_project(case_dir, a.project)
    if project:
        log("project: " + project)
    if edits:
        apply_params_api(project, edits)

    if not cmd:
        log("no batch command given; finished after parameter step")
        return 0
    values = dict((k, fmt_value(v)) for k, v in params.items())
    values.update({"project": project, "project_dir": os.path.dirname(project) if project else case_dir,
                   "case_dir": case_dir})
    full = [c.format(**values) for c in cmd]
    t0 = time.time() - 2
    log("run: %s" % " ".join(full))
    with open("m3d_run.log", "wb") as lf:
        p = subprocess.Popen(full, cwd=case_dir, stdout=lf, stderr=subprocess.STDOUT)
        while p.poll() is None:
            if time.time() - t0 > RUN_TIMEOUT_SEC:
                p.kill()
                raise RuntimeError("timeout after %d s" % RUN_TIMEOUT_SEC)
            time.sleep(2)
    log("exit code %s (%.0f s)" % (p.returncode, time.time() - t0))
    if p.returncode != 0:
        raise RuntimeError("batch command failed (exit %s), see m3d_run.log" % p.returncode)

    fails, oks = scan_logs(case_dir, t0)
    for line in fails[:20]:
        log("LOG FAIL: " + line)
    if fails:
        raise RuntimeError("failure messages in logs (%d)" % len(fails))
    if SUCCESS_PATTERNS and not oks:
        raise RuntimeError("no success message found in logs")
    log("run finished OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
