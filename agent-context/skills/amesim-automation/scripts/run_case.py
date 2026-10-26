# -*- coding: utf-8 -*-
"""Simcenter Amesim 케이스 실행 템플릿 (DDS 플랫폼 규약 준수, py2/py3 겸용).

실행:  AMEPython run_case.py [model.ame]          (tool: amesim)
입력:  params.json  (설계변수), case.json(선택, inputs 에서 .ame 탐색)
출력:  results.json (변수 통계), ts_<key>.csv (시계열), images/<key>.png (matplotlib 가능 시)

흐름: AMEInitAPI -> AMEOpenAmeFile -> AMESetParameterValue -> AMESetRunParameter
      -> AMEGenerateCode -> AMERunSimulation -> AMEGetVariableValues -> 통계 -> results.json
      -> AMECloseCircuit -> AMECloseAPI (finally)
"""
from __future__ import print_function, division

import glob
import json
import math
import os
import sys
import traceback

# ===================== 사용자 설정 =====================
# params.json 키 -> 파라미터 데이터패스 (param@alias)
PARAM_MAP = {
    # "spring_k": "k@spring01",
    # "orifice_d": "diam@or01",
}

# params.json 키 -> AMESetRunParameter 이름. TODO(사내검증): 이름은 help(ame_apy.AMESetRunParameter) 로 확인
RUN_PARAM_KEYS = {
    "final_time": "stop_time_s",
    "print_interval": "interval_s",
    "tolerance": "tolerance",
}
# params.json 에 없어도 항상 적용할 실행 조건 (비우면 모델 저장값 사용)
RUN_PARAMS_DEFAULT = {
    # "stop_time_s": 10.0,
    # "interval_s": 0.001,
}

# results 키 접두어 -> 변수 데이터패스 (var@alias)
OUTPUT_VARS = {
    # "p_out": "p2@pipe01",
    # "x_spool": "x@spool01",
}
# 사용할 통계: final, max, min, mean, rms, settling_time, overshoot_pct
STATS = ["final", "max", "min", "rms"]
SETTLING_BAND = 0.02      # 정착 밴드: 최종값의 ±2%
MIN_END_RATIO = 0.99      # 마지막 시각 < 종료시간*비율 이면 적분 중단으로 보고 실패

STRICT_PARAMS = True      # 매핑 안 된 숫자 params 키가 있으면 실패
COMPILE = True            # 실행 전 AMEGenerateCode() 호출
SAVE_MODEL = False        # 닫을 때 저장 여부
MAKE_PLOTS = True
# =======================================================

try:
    STRING_TYPES = (basestring,)  # noqa: F821  (py2)
except NameError:
    STRING_TYPES = (str,)         # py3


def log(msg):
    line = "[run_case] " + msg
    try:
        print(line)
    except UnicodeError:  # 콘솔 인코딩이 한글을 못 쓰는 경우에도 죽지 않도록
        print(line.encode("ascii", "backslashreplace").decode("ascii"))
    sys.stdout.flush()


def load_json(path):
    with open(path, "r") as f:
        return json.load(f)


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    if os.path.exists(path):
        os.remove(path)
    os.rename(tmp, path)


def find_model(case_dir):
    """우선순위: argv[1] -> case.json inputs 의 .ame -> 케이스 폴더의 유일한 .ame"""
    if len(sys.argv) > 1 and sys.argv[1].lower().endswith(".ame"):
        return os.path.abspath(sys.argv[1])
    case_json = os.path.join(case_dir, "case.json")
    if os.path.isfile(case_json):
        inputs = load_json(case_json).get("inputs", {})
        values = inputs.values() if isinstance(inputs, dict) else inputs
        for v in values:
            if isinstance(v, STRING_TYPES) and v.lower().endswith(".ame"):
                local = os.path.join(case_dir, os.path.basename(v))
                return local if os.path.isfile(local) else os.path.abspath(v)
    found = glob.glob(os.path.join(case_dir, "*.ame"))
    if len(found) == 1:
        return os.path.abspath(found[0])
    raise RuntimeError("모델(.ame) 경로를 정할 수 없음: argv 로 지정하세요. 후보=%s" % found)


def to_float(v):
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def num_str(v):
    """API 에 넘길 숫자 문자열 (repr 로 정밀도 유지)."""
    return repr(float(v))


def to_series(raw):
    """AMEGetVariableValues 반환값 -> (times, values).
    TODO(사내검증): 반환 형식. [[t, v], ...] 와 ([t...], [v...]) 둘 다 처리."""
    raw = list(raw)
    if (len(raw) == 2 and hasattr(raw[0], "__len__") and hasattr(raw[1], "__len__")
            and len(raw[0]) == len(raw[1]) and len(raw[0]) > 2):
        return [float(t) for t in raw[0]], [float(v) for v in raw[1]]
    times, values = [], []
    for pair in raw:
        times.append(float(pair[0]))
        values.append(float(pair[1]))
    return times, values


def trapz(y, t):
    s = 0.0
    for i in range(1, len(t)):
        s += 0.5 * (y[i] + y[i - 1]) * (t[i] - t[i - 1])
    return s


def compute_stats(t, y):
    out = {}
    duration = t[-1] - t[0]
    final = y[-1]
    out["final"] = final
    out["max"] = max(y)
    out["min"] = min(y)
    if duration > 0:
        out["mean"] = trapz(y, t) / duration
        out["rms"] = math.sqrt(trapz([v * v for v in y], t) / duration)
    else:
        out["mean"] = final
        out["rms"] = abs(final)
    band = SETTLING_BAND * max(abs(final), 1e-12)
    settle = t[0]
    for i in range(len(y) - 1, -1, -1):
        if abs(y[i] - final) > band:
            settle = t[i + 1] if i + 1 < len(t) else t[-1]
            break
    out["settling_time"] = settle
    step = final - y[0]
    if abs(step) > 1e-12:
        peak = max(y) if step > 0 else min(y)
        out["overshoot_pct"] = max(0.0, (peak - final) / step * 100.0)
    else:
        out["overshoot_pct"] = 0.0
    return out


def write_csv(path, t, y):
    with open(path, "w") as f:
        f.write("time,value\n")
        for a, b in zip(t, y):
            f.write("%r,%r\n" % (a, b))


def try_plot(images_dir, key, datapath, t, y):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:  # 번들에 없으면 plot_timeseries.py 를 venv 에서 실행
        log("matplotlib 없음 -> plot_timeseries.py 사용 권장 (%s)" % e)
        return False
    if not os.path.isdir(images_dir):
        os.makedirs(images_dir)
    fig = plt.figure(figsize=(8, 4.5), dpi=120)
    ax = fig.add_subplot(111)
    ax.plot(t, y, linewidth=1.5)
    ax.set_xlabel("time [s]")
    ax.set_ylabel(datapath)
    ax.set_title(key)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(os.path.join(images_dir, key + ".png"))
    plt.close(fig)
    return True


def main():
    case_dir = os.getcwd()
    params_path = os.path.join(case_dir, "params.json")
    params = load_json(params_path) if os.path.isfile(params_path) else {}
    log("params: %s" % params)

    # 파라미터/실행조건 분류 (API 열기 전에 검증해 라이선스 낭비 방지)
    set_params = []
    run_params = dict(RUN_PARAMS_DEFAULT)
    for key, raw in params.items():
        val = to_float(raw)
        if key in RUN_PARAM_KEYS:
            if val is None:
                raise RuntimeError("실행조건 값이 숫자가 아님: %s=%r" % (key, raw))
            run_params[RUN_PARAM_KEYS[key]] = val
        elif key in PARAM_MAP:
            if val is None:
                raise RuntimeError("파라미터 값이 숫자가 아님: %s=%r" % (key, raw))
            set_params.append((PARAM_MAP[key], val))
        elif val is not None:
            msg = "PARAM_MAP 에 없는 숫자 파라미터: %s" % key
            if STRICT_PARAMS:
                raise RuntimeError(msg)
            log("WARNING: " + msg)
    if not OUTPUT_VARS:
        raise RuntimeError("OUTPUT_VARS 가 비어 있음")

    model = find_model(case_dir)
    log("model: " + model)

    import ame_apy as api  # AMEPython 에서만 import 가능

    api_open = False
    circuit_open = False
    try:
        api.AMEInitAPI(False)          # 라이선스 체크아웃. TODO(사내검증): 인자 의미
        api_open = True
        api.AMEOpenAmeFile(model)
        circuit_open = True

        for datapath, val in set_params:
            api.AMESetParameterValue(datapath, num_str(val))
            try:
                got = api.AMEGetParameterValue(datapath)   # (value, unit)
                log("set %s = %r -> readback %r" % (datapath, val, got))
            except Exception as e:
                log("readback 실패 %s: %s" % (datapath, e))

        for name, val in run_params.items():
            api.AMESetRunParameter(name, num_str(val))   # TODO(사내검증): 이름/값 형식
            log("run param %s = %r" % (name, val))

        if COMPILE:
            api.AMEGenerateCode()
        log("running simulation...")
        api.AMERunSimulation()
        log("simulation finished")

        results = {}
        series_all = {}
        stop_time = run_params.get("stop_time_s")
        for key, datapath in OUTPUT_VARS.items():
            t, y = to_series(api.AMEGetVariableValues(datapath))
            if len(t) < 2:
                raise RuntimeError("결과 시계열이 비어 있음: %s" % datapath)
            if any(math.isnan(v) or math.isinf(v) for v in y):
                raise RuntimeError("결과에 NaN/Inf: %s" % datapath)
            if stop_time and t[-1] < MIN_END_RATIO * float(stop_time):
                raise RuntimeError("적분 중단 의심: %s 마지막 시각 %r < 종료시간 %r"
                                   % (datapath, t[-1], stop_time))
            stats = compute_stats(t, y)
            for s in STATS:
                results["%s_%s" % (key, s)] = stats[s]
            series_all[key] = (datapath, t, y)
            write_csv(os.path.join(case_dir, "ts_%s.csv" % key), t, y)
    finally:
        if circuit_open:
            try:
                api.AMECloseCircuit(SAVE_MODEL)
            except Exception as e:
                log("AMECloseCircuit 실패: %s" % e)
        if api_open:
            try:
                api.AMECloseAPI()      # 라이선스 반납
            except Exception as e:
                log("AMECloseAPI 실패: %s" % e)

    # 이미지 (실패해도 케이스 실패 아님)
    if MAKE_PLOTS:
        images_dir = os.path.join(case_dir, "images")
        for key, (datapath, t, y) in series_all.items():
            try:
                if not try_plot(images_dir, key, datapath, t, y):
                    break
            except Exception as e:
                log("plot 실패 %s: %s" % (key, e))

    # 마지막에 results.json -> 존재하면 성공
    write_json(os.path.join(case_dir, "results.json"), results)
    log("results.json: %s" % results)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
