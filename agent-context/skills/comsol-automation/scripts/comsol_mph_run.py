# -*- coding: utf-8 -*-
"""
COMSOL 케이스 실행 템플릿 (tool 키: comsol_python = 플랫폼 python + MPh 패키지)
  실행: python comsol_mph_run.py [--model m.mph] [--study "Study 1"]
          --eval "max_temp=comp1.maxop1(T)|degC" --eval "p_drop=comp1.aveop_in(p)-comp1.aveop_out(p)|Pa"
          [--punit width=mm] [--export "Image 1=images/temperature.png"]
          [--field "temperature=T|degC"] [--field-dataset "Surface 1"] [--save out.mph] [--cores 4]

순서: params.json 읽기 -> mph.start() -> 모델 로드 -> 파라미터 대입 -> solve -> evaluate -> 이미지 export
      -> (선택) fields/<name>.npz -> results.json (마지막에만 쓴다)
- params.json 키 중 '_' 로 시작하는 키는 제어용이라 파라미터로 넣지 않는다.
- 모델에 없는 파라미터 이름이면 오타로 보고 실패한다(--allow-new 로 해제).
- MPh 설치 필요: <platform python> -m pip install mph  (JPype 포함, COMSOL 설치 경로 자동 탐색)
"""
import argparse
import glob
import json
import math
import os
import re
import sys

CASE_DIR = os.getcwd()


def log(msg):
    print("[comsol_mph_run] " + str(msg), flush=True)


def snake(s):
    return re.sub(r"[^0-9a-z]+", "_", str(s).lower()).strip("_")


def read_json(name, default=None):
    path = os.path.join(CASE_DIR, name)
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def split_kv(text, sep="="):
    if sep not in text:
        raise ValueError("expected key%svalue: %s" % (sep, text))
    k, v = text.split(sep, 1)
    return k.strip(), v.strip()


def split_unit(text):
    """'expr|unit' -> (expr, unit or None)"""
    if "|" in text:
        e, u = text.rsplit("|", 1)
        return e.strip(), (u.strip() or None)
    return text.strip(), None


def find_model(arg):
    if arg:
        return os.path.abspath(arg)
    case = read_json("case.json", {}) or {}
    for name in case.get("inputs", []):
        if str(name).lower().endswith(".mph"):
            return os.path.join(CASE_DIR, name)
    cands = [c for c in glob.glob(os.path.join(CASE_DIR, "*.mph")) if not os.path.basename(c).startswith("out")]
    if len(cands) == 1:
        return cands[0]
    raise RuntimeError("model .mph not found or ambiguous (use --model)")


def last_scalar(value):
    """evaluate() 결과(스칼라/배열)를 마지막 해(파라메트릭·시간의 마지막)의 스칼라로 만든다."""
    import numpy as np
    a = np.asarray(value, dtype=float)
    if a.ndim == 0:
        return float(a)
    a = a.reshape(-1)
    return float(a[-1])


def last_solution_array(value):
    """필드 evaluate 결과: 정상해는 (N,), 시간/파라메트릭은 (n_sol, N). 마지막 해를 쓴다."""
    import numpy as np
    a = np.asarray(value, dtype=float)
    if a.ndim >= 2:
        a = a[-1]
    return a.reshape(-1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--study", default=None, help="study name (default: solve all studies)")
    ap.add_argument("--eval", action="append", default=[], help="key=expression|unit (global/derived value)")
    ap.add_argument("--eval-dataset", default=None)
    ap.add_argument("--punit", action="append", default=[], help="param=unit, value is written as '<v>[unit]'")
    ap.add_argument("--export", action="append", default=[], help="'Export node name=images/file.png'")
    ap.add_argument("--field", default=None, help="name=expression|unit -> fields/<name>.npz")
    ap.add_argument("--field-dataset", default=None, help="dataset to evaluate the field on (surface dataset recommended)")
    ap.add_argument("--save", default=None, help="save solved model (e.g. out.mph)")
    ap.add_argument("--cores", type=int, default=None)
    ap.add_argument("--allow-new", action="store_true", help="allow creating parameters missing in the model")
    a = ap.parse_args()

    params = read_json("params.json", {}) or {}
    model_path = find_model(a.model)
    punits = dict(split_kv(x) for x in a.punit)
    evals = [split_kv(x) for x in a.eval]
    if not evals:
        log("ERROR: no --eval given; nothing to write to results.json")
        return 2

    import mph  # 플랫폼 파이썬에 mph 설치 필요
    log("starting COMSOL client (cores=%s)" % a.cores)
    client = mph.start(cores=a.cores) if a.cores else mph.start()
    log("loading " + model_path)
    model = client.load(model_path)

    # 1) 파라미터
    existing = model.parameters()
    for k, v in params.items():
        if str(k).startswith("_"):
            continue
        if k not in existing and not a.allow_new:
            raise KeyError("parameter '%s' not in model (existing: %s)" % (k, ", ".join(existing)))
        value = "%r[%s]" % (v, punits[k]) if k in punits else repr(v) if isinstance(v, float) else str(v)
        model.parameter(k, value)
        log("param %s = %s" % (k, value))

    # 2) 해석 (geometry/mesh 는 study 실행 시 필요하면 다시 만들어진다. 명시적으로 하려면 model.build(); model.mesh())
    log("solving " + (a.study or "all studies"))
    if a.study:
        model.solve(a.study)
    else:
        model.solve()

    # 3) 결과
    results = {}
    for key, spec in evals:
        expr, unit = split_unit(spec)
        val = model.evaluate(expr, unit, dataset=a.eval_dataset) if a.eval_dataset else model.evaluate(expr, unit)
        x = last_scalar(val)
        if math.isnan(x) or math.isinf(x):
            raise ValueError("result %s is not finite (%s)" % (key, x))
        results[snake(key)] = x
        log("%s = %.6g %s  (%s)" % (snake(key), x, unit or "", expr))

    # 4) 이미지: 모델에 미리 만들어 둔 Image export 노드를 실행한다. 실패해도 케이스는 실패로 보지 않는다.
    os.makedirs(os.path.join(CASE_DIR, "images"), exist_ok=True)
    for item in a.export:
        node, path = split_kv(item)
        try:
            model.export(node, os.path.join(CASE_DIR, path))
            log("exported %s -> %s" % (node, path))
        except Exception as e:  # noqa
            log("WARN: export %s failed: %s" % (node, e))

    # 5) 필드 (선택): 좌표와 값을 같은 데이터셋에서 평가한다. 삼각형은 MPh 로 얻기 어려워 생략한다(서버가 points 만으로도 학습).
    if a.field:
        import numpy as np
        name, spec = split_kv(a.field)
        expr, unit = split_unit(spec)
        kw = {"dataset": a.field_dataset} if a.field_dataset else {}
        xyz = [last_solution_array(model.evaluate(c, None, **kw)) for c in ("x", "y", "z")]
        vals = last_solution_array(model.evaluate(expr, unit, **kw))
        pts = np.column_stack(xyz)
        if len(pts) != len(vals):
            raise ValueError("field size mismatch: points %d values %d" % (len(pts), len(vals)))
        os.makedirs(os.path.join(CASE_DIR, "fields"), exist_ok=True)
        np.savez_compressed(os.path.join(CASE_DIR, "fields", snake(name) + ".npz"), points=pts, values=vals)
        log("saved fields/%s.npz: nodes=%d max=%.6g" % (snake(name), len(pts), float(vals.max())))

    if a.save:
        model.save(os.path.join(CASE_DIR, a.save))
        log("saved model " + a.save)

    with open(os.path.join(CASE_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    log("results.json written: " + ", ".join(sorted(results)))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # results.json 을 쓰지 않고 실패
        log("FAILED: %s: %s" % (type(e).__name__, e))
        sys.exit(1)
