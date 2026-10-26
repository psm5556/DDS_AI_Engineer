# -*- coding: utf-8 -*-
"""
Creo trail 파일 케이스 드라이버 (라이선스 1개로 GUI 없이 배치, DDS 규약 준수)

흐름: 사용자가 녹화한 trail 템플릿의 @@이름@@ 자리표시자를 params.json 값으로 치환
      -> trail_case.txt 작성 -> parametric -g:no_graphics -i:rpc_input trail_case.txt 실행
      -> trail 이 내보낸 STL 확인 -> STL 형상량(부피/면적/중심/경계상자) + fields npz -> results.json

실행 (tool 키 'creo_python'):
  python creo_trail_case.py trail=bracket_trail.txt stl=export/bracket.stl fields=1

인자(모두 key=value):
  trail=<파일>      trail 템플릿(필수). 케이스 폴더 기준 상대경로 또는 절대경로
  creo=<실행파일>   기본: 환경변수 DDS_CREO -> 'parametric'
  stl=<파일>        trail 이 만드는 STL 경로(필수, 케이스 폴더 기준). 결과 계산의 근거
  timeout=<초>      기본 600
  fields=0|1        STL -> fields/shape.npz
  scale=<수>        STL 좌표 배율(기본 1)

trail 템플릿 작성법은 SKILL.md 2-2절. trail 은 Creo 버전·UI 언어·설정에 민감하므로
사내 Creo 로 직접 녹화한 것만 쓴다. TODO(사내검증): -g:no_graphics 에서 녹화 trail 재생 가능 여부.
콘솔 출력은 ASCII. f-string 미사용.
"""
import json
import os
import re
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print("[DDS] " + str(msg))
    sys.stdout.flush()


def parse_args(argv):
    out = {}
    for tok in argv:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def flag(args, key, default="0"):
    return str(args.get(key, default)).strip().lower() in ("1", "true", "yes", "y")


def fill_template(text, params):
    """@@name@@ 를 params 값으로 치환. 남은 자리표시자가 있으면 실패."""
    def rep(m):
        k = m.group(1)
        if k not in params:
            raise RuntimeError("placeholder @@%s@@ has no value in params.json" % k)
        v = params[k]
        return repr(float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)
    return re.sub(r"@@([A-Za-z0-9_]+)@@", rep, text)


def main(argv):
    args = parse_args(argv)
    cdir = os.path.abspath(args.get("case_dir") or os.getcwd())
    res_path = os.path.join(cdir, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)
    with open(os.path.join(cdir, "params.json"), "r", encoding="utf-8") as f:
        params = json.load(f)

    if "trail" not in args or "stl" not in args:
        raise RuntimeError("trail=<template> and stl=<output stl> are required")
    tpath = args["trail"] if os.path.isabs(args["trail"]) else os.path.join(cdir, args["trail"])
    with open(tpath, "r", encoding="utf-8", errors="replace") as f:
        trail = fill_template(f.read(), params)
    case_trail = os.path.join(cdir, "trail_case.txt")
    with open(case_trail, "w", encoding="utf-8") as f:
        f.write(trail)

    stl = args["stl"] if os.path.isabs(args["stl"]) else os.path.join(cdir, args["stl"])
    if os.path.exists(stl):
        os.remove(stl)                                 # 이전 산출물 오인 방지
    if not os.path.isdir(os.path.dirname(stl)):
        os.makedirs(os.path.dirname(stl))

    creo = args.get("creo") or os.environ.get("DDS_CREO") or "parametric"
    cmd = [creo, "-g:no_graphics", "-i:rpc_input", case_trail]
    log("run: " + " ".join(cmd))
    try:
        rc = subprocess.call(cmd, cwd=cdir, timeout=float(args.get("timeout", "600")),
                             shell=creo.lower().endswith((".bat", ".cmd")))
    except subprocess.TimeoutExpired:
        # TODO(사내검증): 타임아웃 시 xtop.exe 가 남을 수 있다. 이 케이스 cwd 의 xtop 만 골라 정리하는 방법 확인.
        raise RuntimeError("Creo trail timed out - a dialog may be waiting; check std.out / trail")
    log("creo exit code %s" % rc)
    if not os.path.isfile(stl):
        raise RuntimeError("STL not produced by trail (regeneration failure or trail mismatch): " + stl)

    sys.path.insert(0, HERE)
    import stl_to_fields as s2f
    scale = float(args.get("scale", "1"))
    if flag(args, "fields"):
        pts, faces = s2f.convert(stl, os.path.join(cdir, "fields", "shape.npz"), "zeros", scale)
    else:
        pts, faces = s2f.index_mesh(s2f.read_stl(stl), scale=scale)
    metrics = s2f.geometry_metrics(pts, faces)
    if metrics.get("stl_volume", 0.0) <= 0:
        raise RuntimeError("STL has zero volume")
    results = {"volume": metrics["stl_volume"], "area": metrics["stl_area"]}
    results.update(metrics)
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    log("results.json: " + json.dumps(results))
    return 0


if __name__ == "__main__":
    try:
        rc = main(sys.argv[1:])
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        rc = 1
    sys.exit(rc)
