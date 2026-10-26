# -*- coding: utf-8 -*-
"""
OpenFOAM 케이스 준비 (tool 키: python, Windows 플랫폼 venv) — of_run.sh(WSL) 앞 단계
  실행: python of_prepare.py [--set "0/U|boundaryField/inlet/value|uniform ({{inlet_velocity}} 0 0)"]
          [--set "system/controlDict|endTime|{{iterations}}"] [--default iterations=500]

하는 일
1) 템플릿 렌더: 케이스 안의 '*.tmpl' 파일(예: system/blockMeshDict.tmpl)의 {{name}} 을 params.json 값으로 치환해
   '.tmpl' 을 뗀 파일로 쓴다. 값이 없는 플레이스홀더가 있으면 실패.
2) foamDictionary 변경 목록: --set "파일|엔트리|값" 을 렌더해 foam_set.txt(탭 구분)로 쓴다.
   of_run.sh 가 WSL 안에서 'foamDictionary <파일> -entry <엔트리> -set <값>' 으로 적용한다.
   엔트리 경로 구분자는 '/' (foamDictionary 규칙).
3) params.sh: 'export P_<key>=<value>' (bash 에서 쓰기 위한 사본).
4) 줄바꿈 정리: *.sh 와 system/, constant/, 0/ 의 텍스트 파일 CRLF -> LF (Windows 편집기로 고친 파일 대비).
   바이너리(NUL 포함)와 constant/triSurface, constant/polyMesh 는 건드리지 않는다.
"""
import argparse
import json
import os
import re
import sys

CASE_DIR = os.getcwd()          # params.json, images/ 위치(플랫폼 케이스 폴더)
FOAM_DIR = CASE_DIR             # OpenFOAM 케이스 루트(system/ 이 있는 곳). main() 에서 정한다


def find_foam_dir():
    """cwd 에 system/ 이 없으면 system/ 을 가진 하위 폴더 하나를 쓴다(어댑터가 폴더 입력을 하위 폴더로 복사)."""
    if os.path.isdir(os.path.join(CASE_DIR, "system")):
        return CASE_DIR
    subs = [d for d in os.listdir(CASE_DIR) if os.path.isdir(os.path.join(CASE_DIR, d, "system"))]
    if len(subs) == 1:
        return os.path.join(CASE_DIR, subs[0])
    raise RuntimeError("OpenFOAM case (system/) not found in cwd or a single subfolder: %s" % subs)
PH = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
SKIP_DIRS = {"processor", "postProcessing", "VTK", "images", "fields", "triSurface", "polyMesh"}


def log(msg):
    print("[of_prepare] " + str(msg), flush=True)


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


def render(text, values, where):
    missing = set()

    def rep(mo):
        k = mo.group(1)
        if k not in values:
            missing.add(k)
            return mo.group(0)
        return fmt(values[k])
    out = PH.sub(rep, text)
    if missing:
        raise KeyError("%s: no value for %s" % (where, ", ".join(sorted(missing))))
    return out


def walk_files():
    for root, dirs, files in os.walk(FOAM_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith("processor")]
        for fn in files:
            yield os.path.join(root, fn)


def to_lf(path):
    with open(path, "rb") as f:
        data = f.read()
    if b"\0" in data or b"\r\n" not in data:
        return False
    with open(path, "wb") as f:
        f.write(data.replace(b"\r\n", b"\n"))
    return True


def wsl_path(p):
    """C:\\a\\b -> /mnt/c/a/b (로그 표시용. WSL 안에서는 'wslpath -u' 가 정답)."""
    p = os.path.abspath(p)
    if len(p) > 1 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:].replace("\\", "/")
    return p.replace("\\", "/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", default=[], help="FILE|ENTRY|VALUE (VALUE may use {{key}})")
    ap.add_argument("--default", action="append", default=[], help="key=value used when params.json lacks it")
    a = ap.parse_args()

    values = {}
    for d in a.default:
        k, v = d.split("=", 1)
        values[k.strip()] = v.strip()
    values.update(read_json("params.json", {}) or {})
    global FOAM_DIR
    FOAM_DIR = find_foam_dir()
    log("case root: " + os.path.relpath(FOAM_DIR, CASE_DIR))

    n_tmpl = 0
    for path in list(walk_files()):
        if path.endswith(".tmpl"):
            with open(path, "r", encoding="utf-8") as f:
                text = render(f.read(), values, os.path.relpath(path, FOAM_DIR))
            dst = path[:-5]
            with open(dst, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.remove(path)   # 케이스 사본이므로 지운다(0/ 의 U.tmpl 을 decomposePar 등이 읽지 않게)
            n_tmpl += 1
            log("rendered " + os.path.relpath(dst, FOAM_DIR))

    lines = []
    for s in a.set:
        parts = s.split("|", 2)
        if len(parts) != 3:
            raise ValueError("--set needs FILE|ENTRY|VALUE: " + s)
        fpath, entry, val = [x.strip() for x in parts]
        if not os.path.exists(os.path.join(FOAM_DIR, fpath)):
            raise FileNotFoundError("--set target missing: " + fpath)
        val = render(val, values, "--set " + fpath)
        if "\t" in val or "\n" in val:
            raise ValueError("value must be one line without tabs: " + val)
        lines.append("%s\t%s\t%s" % (fpath, entry, val))
        log("foamDictionary %s -entry %s -set '%s'" % (fpath, entry, val))
    with open(os.path.join(FOAM_DIR, "foam_set.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + ("\n" if lines else ""))

    with open(os.path.join(FOAM_DIR, "params.sh"), "w", encoding="utf-8", newline="\n") as f:
        for k, v in values.items():
            key = re.sub(r"[^A-Za-z0-9_]", "_", str(k))
            f.write("export P_%s='%s'\n" % (key, fmt(v).replace("'", "")))

    fixed = sum(1 for p in os.listdir(CASE_DIR) if p.endswith(".sh") and to_lf(os.path.join(CASE_DIR, p)))
    for path in walk_files():
        rel = os.path.relpath(path, FOAM_DIR).replace("\\", "/")
        top = rel.split("/", 1)[0]
        if rel.endswith(".sh") or top in ("system", "constant", "0", "0.orig"):
            fixed += 1 if to_lf(path) else 0
    os.makedirs(os.path.join(CASE_DIR, "images"), exist_ok=True)
    log("templates=%d foam_set=%d crlf_fixed=%d" % (n_tmpl, len(lines), fixed))
    log("WSL case path: " + wsl_path(FOAM_DIR))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("FAILED: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
