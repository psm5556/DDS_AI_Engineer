# -*- coding: utf-8 -*-
"""
Altair Inspire 케이스 템플릿 — Inspire 안의 파이썬으로 실행한다 (tool 키 'inspire_script').

케이스 폴더(DDS_CASE_DIR 또는 현재 폴더)의 inspire_job.json 을 읽는다:
  mode: inspect -> api_probe.json (inspire 모듈·모델 API, 변수·파트 목록)
  mode: case    -> params.json 을 변수에 넣고 analyze/optimize -> results.json, 형상 내보내기, images/view.png
API 이름은 버전마다 다르다(TODO(사내검증)). inspect 결과를 보고 아래 API 표만 고치면 된다.
f-string 미사용, 콘솔 출력은 ASCII.
"""
import json
import os
import sys
import traceback

CASE = os.environ.get("DDS_CASE_DIR") or os.getcwd()

# Inspire 파이썬 API 이름 표 — inspect 결과에 맞춰 고친다 (TODO(사내검증))
API = {
    "open": ["openFile", "importFile", "open"],            # inspire.<이름>(path)
    "model": ["getModel"],                                  # inspire.<이름>() -> 모델
    "set_variable": ["setVariable"],                        # model.<이름>(name, value)
    "analyze": ["runAnalysis", "analyze"],                  # model.<이름>() 또는 inspire.<이름>()
    "optimize": ["runOptimization", "optimize"],
    "result": ["getResult", "getResults"],                  # model.<이름>(kind)
    "export": ["exportFile", "export"],                     # inspire.<이름>(path)
    "screenshot": ["captureImage", "saveImage"],            # inspire.<이름>(path)
    "quit": ["exit", "quit"],
}


def p(name):
    return os.path.join(CASE, name)


def first(obj, names):
    for n in names:
        f = getattr(obj, n, None)
        if callable(f):
            return f
    return None


def call(objs, key, *a):
    for o in objs:
        f = first(o, API[key])
        if f:
            return f(*a)
    raise RuntimeError("Inspire API not found for '%s' (tried %s) - run mode=inspect and fix API table" % (key, API[key]))


def probe(inspire, model):
    out = {"inspire": sorted(n for n in dir(inspire) if not n.startswith("_"))}
    out["submodules"] = sorted(n for n in out["inspire"] if type(getattr(inspire, n, None)).__name__ == "module")
    if model is not None:
        out["model"] = sorted(n for n in dir(model) if not n.startswith("_"))
        for attr in ("variables", "parts", "loads", "supports"):
            try:
                items = getattr(model, attr)
                items = items() if callable(items) else items
                out[attr] = [str(getattr(i, "name", i)) for i in list(items)[:200]]
            except Exception as e:  # noqa: BLE001
                out[attr] = "n/a: " + str(e)
    return out


def main():
    job = json.load(open(p("inspire_job.json"), encoding="utf-8"))
    import inspire  # Inspire 안에서만 있다
    if job.get("model"):
        call([inspire], "open", p(job["model"]))
    model = None
    try:
        model = call([inspire], "model")
    except Exception:  # noqa: BLE001
        pass
    if job.get("mode", "case") == "inspect":
        json.dump(probe(inspire, model), open(p("api_probe.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        return
    if os.path.exists(p("results.json")):
        os.remove(p("results.json"))
    params = json.load(open(p("params.json"), encoding="utf-8")) if os.path.exists(p("params.json")) else {}
    names = job.get("variables") or {}
    for k, v in params.items():
        call([model, inspire], "set_variable", names.get(k, k), v)
    run = job.get("run", "analyze")
    if run == "optimize":
        if job.get("mass_target") is not None:
            try:
                call([model, inspire], "set_variable", "massTarget", job["mass_target"])  # TODO(사내검증): 최적화 설정 방법
            except RuntimeError:
                pass
        call([model, inspire], "optimize")
    else:
        call([model, inspire], "analyze")
    results = {}
    for kind in job.get("results", ["disp_max", "stress_max", "mass"]):
        try:
            val = call([model, inspire], "result", kind)
            results[kind] = float(val)
        except Exception as e:  # noqa: BLE001
            results.setdefault("_missing", []).append(kind + ": " + str(e)[:80])
    for f in job.get("export", []):
        call([inspire], "export", p(f))
    if job.get("image"):
        try:
            os.makedirs(p("images"), exist_ok=True)
            call([inspire], "screenshot", p(os.path.join("images", "view.png")))
        except Exception:  # noqa: BLE001 — 그림이 없어도 케이스는 성공
            pass
    numeric = {k: v for k, v in results.items() if not k.startswith("_")}
    if not numeric:
        raise RuntimeError("no results read: %s" % results.get("_missing"))
    json.dump(numeric, open(p("results.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 — Inspire 안에서는 종료 코드가 안 보일 수 있어 파일로 남긴다
        open(p("inspire_error.txt"), "w", encoding="utf-8").write(traceback.format_exc())
        try:
            import inspire
            call([inspire], "quit")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)
    try:
        import inspire
        if (json.load(open(p("inspire_job.json"), encoding="utf-8")).get("quit", True)):
            call([inspire], "quit")
    except Exception:  # noqa: BLE001
        pass
