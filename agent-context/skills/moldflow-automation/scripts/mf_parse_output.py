"""Moldflow 해석 로그(screen output) 텍스트 -> results.json  (tool: python, 플랫폼 venv)

실행:  python mf_parse_output.py [--text mf_screen_output.txt] [--require fill_time_s,clamp_force_max_tonne]
                                 [--merge] [--generic] [--ignore-errors]
입력:  mf_case_run.py 가 남긴 mf_screen_output.txt (studyrlt <study>.sdy -exportoutput 결과 텍스트)
       --text 가 없으면 케이스 폴더에서 mf_screen_output.txt -> 가장 최근 *.txt 순으로 찾는다.
출력:  results.json            KEY_PATTERNS 로 뽑은 핵심 값 (단위 환산 후)
       mf_output_all.json     로그의 모든 "라벨 = 숫자 단위" 줄 (키 발굴용, 항상 저장)
실패:  필수 키 누락, 추출 값 0개, 로그에 '** ERROR' 줄 -> results.json 미작성 + exit 1

TODO(사내검증): 라벨 문자열은 Moldflow 버전/해석 종류/언어 설정에 따라 다르다.
  먼저 한 케이스를 돌려 mf_output_all.json 을 보고 KEY_PATTERNS 의 정규식을 맞춘다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"

# results 키 -> (라벨 정규식 후보들, 목표 단위, 여러 번 나오면 선택: first|last|max|min)
# 라벨은 '=' 앞부분에 대해 대소문자 무시로 검색한다. TODO(사내검증): 실제 로그 문구로 보정.
KEY_PATTERNS = {
    "max_injection_pressure_mpa": ([r"maximum injection pressure"], "MPa", "max"),
    "vp_switch_time_s": ([r"velocity/pressure switch-?over at time", r"v/p switch-?over.*time"], "s", "first"),
    "fill_time_s": ([r"time at the end of filling", r"actual filling time", r"filling time"], "s", "first"),
    "clamp_force_max_tonne": ([r"clamp force\s*-\s*maximum", r"maximum clamp force", r"peak clamp force"], "tonne", "max"),
    "total_weight_g": ([r"total weight", r"total part weight"], "g", "last"),
    "shot_volume_cm3": ([r"total volume", r"total part volume"], "cm^3", "first"),
    "cycle_time_s": ([r"cycle time"], "s", "last"),
    "max_deflection_mm": ([r"maximum deflection", r"deflection.*maximum"], "mm", "max"),
}

# 단위 환산: 목표 단위 -> {원래 단위(소문자): 배율}
UNIT_FACTORS = {
    "MPa": {"mpa": 1.0, "kpa": 1e-3, "pa": 1e-6, "bar": 0.1, "psi": 6.894757e-3},
    "tonne": {"tonne": 1.0, "tonnes": 1.0, "t": 1.0, "kn": 0.1019716,
              "ton": 0.9071847, "tons": 0.9071847},  # ton = US short ton 가정. TODO(사내검증)
    "s": {"s": 1.0, "sec": 1.0, "ms": 1e-3},
    "mm": {"mm": 1.0, "m": 1000.0, "in": 25.4},
    "g": {"g": 1.0, "kg": 1000.0, "lb": 453.59237},
    "cm^3": {"cm^3": 1.0, "cm3": 1.0, "cc": 1.0, "mm^3": 1e-3, "in^3": 16.387064},
}

LINE_RE = re.compile(
    r"^\s*(?P<label>[A-Za-z][^=]*?)\s*(?:\(\s*at\s+(?P<at>" + NUM + r")\s*\w*\s*\))?\s*=\s*"
    r"(?P<val>" + NUM + r")\s*(?P<unit>[A-Za-z%^0-9/]+)?\s*$")
ERROR_RE = re.compile(r"\*\*\s*ERROR", re.IGNORECASE)  # TODO(사내검증): Moldflow 오류 줄 형식


def log(msg):
    print("[mf_parse] " + msg)
    sys.stdout.flush()


def snake(text):
    s = re.sub(r"[^0-9a-zA-Z]+", "_", text.strip().lower()).strip("_")
    return re.sub(r"_+", "_", s)


def find_text(case_dir, given):
    if given:
        return given
    p = os.path.join(case_dir, "mf_screen_output.txt")
    if os.path.isfile(p):
        return p
    cands = [f for f in glob.glob(os.path.join(case_dir, "*.txt"))]
    if not cands:
        raise RuntimeError("no screen output text (*.txt) in case folder")
    return max(cands, key=os.path.getmtime)


def read_text(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp949", "latin-1"):
        try:
            text = raw.decode(enc)
            if "\x00" not in text:
                return text
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", "replace")


def parse_lines(text):
    """모든 '라벨 = 값 단위' 줄 -> [(label, value, unit, at)]"""
    rows = []
    for line in text.splitlines():
        m = LINE_RE.match(line.replace("|", " "))
        if m:
            rows.append((m.group("label").strip(), float(m.group("val")),
                         (m.group("unit") or "").strip(), m.group("at")))
    return rows


def convert(value, unit, target):
    if not target or not unit:
        return value, True
    table = UNIT_FACTORS.get(target, {})
    f = table.get(unit.lower())
    if f is None:
        return value, False
    return value * f, True


def extract(rows, patterns=KEY_PATTERNS):
    out = {}
    for key, (label_res, target, pick) in patterns.items():
        hits = []
        for label, val, unit, _ in rows:
            if any(re.search(r, label, re.IGNORECASE) for r in label_res):
                v, ok = convert(val, unit, target)
                if not ok:
                    log("WARNING unit '%s' for %s not convertible to %s, skipped" % (unit, key, target))
                    continue
                hits.append(v)
        if not hits:
            continue
        out[key] = {"first": hits[0], "last": hits[-1], "max": max(hits), "min": min(hits)}[pick]
    return out


def generic(rows):
    allv = {}
    for label, val, unit, _ in rows:
        base = snake(label) + ("_" + snake(unit) if unit else "")
        key, n = base, 2
        while key in allv:
            key = "%s_%d" % (base, n)
            n += 1
        allv[key] = val
    return allv


def write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text")
    ap.add_argument("--require", default="", help="comma separated result keys that must exist")
    ap.add_argument("--merge", action="store_true", help="merge into existing results.json")
    ap.add_argument("--generic", action="store_true", help="also put all parsed lines into results.json")
    ap.add_argument("--ignore-errors", action="store_true")
    a = ap.parse_args()

    case_dir = os.getcwd()
    path = find_text(case_dir, a.text)
    log("text: " + path)
    text = read_text(path)

    errors = [ln.strip() for ln in text.splitlines() if ERROR_RE.search(ln)]
    for ln in errors[:20]:
        log("LOG ERROR: " + ln)
    if errors and not a.ignore_errors:
        raise RuntimeError("analysis log contains %d error line(s)" % len(errors))

    rows = parse_lines(text)
    allv = generic(rows)
    write_json(os.path.join(case_dir, "mf_output_all.json"), allv)
    log("parsed %d 'label = value' lines -> mf_output_all.json" % len(rows))

    results = extract(rows)
    if a.generic:
        for k, v in allv.items():
            results.setdefault("out_" + k, v)
    required = [k.strip() for k in a.require.split(",") if k.strip()]
    missing = [k for k in required if k not in results]
    if missing:
        raise RuntimeError("required keys missing: %s (see mf_output_all.json)" % ", ".join(missing))
    if not results:
        raise RuntimeError("no result value extracted (see mf_output_all.json)")

    res_path = os.path.join(case_dir, "results.json")
    if a.merge and os.path.isfile(res_path):
        with open(res_path, encoding="utf-8") as f:
            old = json.load(f)
        old.update(results)
        results = old
    write_json(res_path, results)
    log("results.json: %s" % json.dumps(results))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # 실패 -> results.json 미작성
        log("FAILED: %s" % e)
        sys.exit(1)
