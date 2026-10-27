# -*- coding: utf-8 -*-
"""
CST Studio Suite 케이스 템플릿 (CST 파이썬 라이브러리, DDS 규약 준수)

흐름: params.json -> CST 연결 -> 프로젝트 열기 -> 파라미터 저장 -> 히스토리 재생성 -> 솔버
      -> 결과 항목(복소 y(f)) -> |y| dB 지표(min_db, fmin, bw10) -> results.json (+ images/)
실행 (tool 키 'cst_python'):
  python cst_case.py project=patch.cst inspect=1
  python cst_case.py project=patch.cst results="1D Results\\S-Parameters\\S1,1" image=1
콘솔 출력은 ASCII.
"""
import glob
import json
import math
import os
import re
import sys

CASE = os.getcwd()


def log(msg):
    sys.stdout.write(str(msg).encode("ascii", "replace").decode("ascii") + "\n")
    sys.stdout.flush()


def fail(msg):
    log("FAIL: " + msg)
    sys.exit(1)


def args():
    a = {}
    for x in sys.argv[1:]:
        k, _, v = x.partition("=")
        a[k.strip()] = v.strip() if _ else "1"
    return a


def flag(a, k, default="0"):
    return a.get(k, default) not in ("0", "", "false", "no")


def read_json(name, default=None):
    p = os.path.join(CASE, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(name, data):
    with open(os.path.join(CASE, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_cst_path():
    """CST 설치 폴더의 python_cst_libraries 를 import 경로에 넣는다(DDS_CST = CST DESIGN ENVIRONMENT.exe)."""
    exe = os.environ.get("DDS_CST") or os.environ.get("DDS_TOOL_CST") or ""
    roots = [os.path.dirname(exe)] if exe else []
    for r in roots:
        lib = os.path.join(r, "AMD64", "python_cst_libraries")
        if os.path.isdir(lib) and lib not in sys.path:
            sys.path.insert(0, lib)


def slug(item):
    last = re.split(r"[\\/]", item)[-1]
    s = re.sub(r"[^0-9A-Za-z]+", "", last).lower()
    return s or "result"


def metrics(freq, y):
    """복소(또는 실수) 응답 -> |y| dB 지표: 최소 dB, 그 주파수, 최소점을 포함하는 -10 dB 이하 연속 대역폭."""
    db = []
    for v in y:
        mag = abs(complex(v)) if not isinstance(v, (int, float)) else abs(v)
        db.append(20.0 * math.log10(max(mag, 1e-30)))
    if not db:
        raise RuntimeError("empty result data")
    i = min(range(len(db)), key=lambda k: db[k])
    lo = hi = i
    if db[i] <= -10.0:
        while lo > 0 and db[lo - 1] <= -10.0:
            lo -= 1
        while hi < len(db) - 1 and db[hi + 1] <= -10.0:
            hi += 1
        bw = float(freq[hi]) - float(freq[lo])
    else:
        bw = 0.0
    return {"min_db": db[i], "fmin": float(freq[i]), "bw10": bw}, db


def save_plot(name, freq, db):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        os.makedirs(os.path.join(CASE, "images"), exist_ok=True)
        fig, ax = plt.subplots(figsize=(5.4, 3.6), dpi=120)
        ax.plot(freq, db, lw=1.6)
        ax.axhline(-10, color="#999", lw=0.8, ls="--")
        ax.set_xlabel("Frequency")
        ax.set_ylabel(name + " (dB)")
        ax.grid(alpha=0.3)
        fig.tight_layout()
        fig.savefig(os.path.join(CASE, "images", name + ".png"))
        plt.close(fig)
    except Exception as e:  # noqa: BLE001 — 그림이 없어도 케이스는 성공
        log("warn: plot failed: %s" % e)


def find_project(a):
    if a.get("project"):
        return os.path.abspath(a["project"])
    inputs = (read_json("case.json", {}) or {}).get("inputs") or []
    if isinstance(inputs, dict):
        inputs = list(inputs.values())
    for x in inputs:
        if str(x).lower().endswith(".cst"):
            return os.path.join(CASE, os.path.basename(str(x)))
    hits = glob.glob(os.path.join(CASE, "*.cst"))
    if len(hits) == 1:
        return hits[0]
    fail("project .cst not found (give project=<file>)")


def set_param(prj, name, value):
    """파라미터 저장. TODO(사내검증): 버전별 호출 형태 — 먼저 VBA 메서드, 안 되면 매크로 실행."""
    m3d = prj.model3d
    try:
        m3d.StoreDoubleParameter(name, float(value))
        return
    except Exception:  # noqa: BLE001
        pass
    code = 'Sub Main\n StoreDoubleParameter "%s", %r\nEnd Sub' % (name, float(value))
    for fn in ("execute_vba_code", "_execute_vba_code"):
        if hasattr(m3d, fn):
            getattr(m3d, fn)(code)
            return
    raise RuntimeError("cannot set CST parameter (check API for this CST version): " + name)


def main():
    a = args()
    if os.path.exists(os.path.join(CASE, "results.json")):
        os.remove(os.path.join(CASE, "results.json"))
    add_cst_path()
    try:
        import cst.interface
        import cst.results
    except ImportError as e:
        fail("CST python libraries not importable (%s). Set agent.toml [tools] cst = '.../CST DESIGN ENVIRONMENT.exe'" % e)
    path = find_project(a)
    if flag(a, "attach"):
        de = cst.interface.DesignEnvironment.connect_to_any()  # TODO(사내검증)
    else:
        de = cst.interface.DesignEnvironment()
    prj = de.open_project(path)
    log("opened: " + path)

    if flag(a, "inspect"):
        info = {"project": path}
        try:
            r3d = cst.results.ProjectFile(path, allow_interactive=True).get_3d()
            info["result_tree"] = list(r3d.get_tree_items())[:400]
            ids = r3d.get_all_run_ids()
            info["parameters"] = r3d.get_parameter_combination(ids[-1]) if ids else {}
        except Exception as e:  # noqa: BLE001
            info["note"] = "no results yet or API differs: %s" % e
        write_json("model_info.json", info)
        log("model_info.json written")
        return

    params = read_json("params.json", {}) or {}
    for k, v in params.items():
        set_param(prj, k, v)
        log("param %s = %s" % (k, v))
    if params:
        prj.model3d.full_history_rebuild()
    if flag(a, "solve", "1"):
        prj.model3d.run_solver()
        log("solver done")
    prj.save()

    items = [x.strip() for x in a.get("results", "1D Results\\S-Parameters\\S1,1").split(",") if x.strip()]
    # 쉼표가 결과 이름 안에 들어 있는 S1,1 같은 경우를 다시 붙인다
    merged = []
    for x in items:
        if merged and re.fullmatch(r"\d+", x) and re.search(r"S\d+$", merged[-1]):
            merged[-1] += "," + x
        else:
            merged.append(x)
    r3d = cst.results.ProjectFile(path, allow_interactive=True).get_3d()
    run = r3d.get_all_run_ids()[-1]
    results = {}
    for item in merged:
        it = r3d.get_result_item(item, run_id=run)
        freq, y = list(it.get_xdata()), list(it.get_ydata())
        m, db = metrics(freq, y)
        key = slug(item)
        for k, v in m.items():
            results[key + "_" + k] = v
        if flag(a, "image", "1"):
            save_plot(key, freq, db)
    write_json("results.json", results)
    log("results.json: " + ", ".join(sorted(results)))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        fail(str(e))
