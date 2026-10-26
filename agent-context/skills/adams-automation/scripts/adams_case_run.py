# -*- coding: utf-8 -*-
"""
Adams 케이스 실행 템플릿 (tool 키: python, 플랫폼 venv / Python 3)

  python adams_case_run.py [--adm model.adm] [--acf template.acf] [--adams <mdi.bat 경로>]
                           [--solver-args "ru-standard i {acf} exit"] [--timeout 3600]
                           [--view-model model.bin --view-model-name .MODEL]

처리 순서
  1) params.json 읽기. '_' 로 시작하는 키는 제어용
       _end_time, _steps, _sim_type(dynamic|kinematic|static), _static_first(0/1),
       _integrator(GSTIFF|WSTIFF|HHT ...), _integrator_error, _timeout
  2) (선택) --view-model 이 있으면 Adams View 배치(.cmd 생성 → aview ru-standard b)로
     설계변수(variable) 값을 바꾸고 ADM 을 내보낸다(case.adm).
  3) ADM 편집 (케이스 폴더의 복사본을 제자리 수정)
       a) {{name}} 플레이스홀더 치환
       b) STATEMENT_MAP 에 적은 statement 인자(PART/2 MASS=, MARKER/10 QP= 의 성분 등) 교체
     params 키가 어디에도 반영되지 않으면 실패로 처리한다(오타 방지).
  4) RESULTS/XRF 가 없으면 END 앞에 추가, ACF 작성(또는 --acf 템플릿 치환)
  5) Adams Solver 실행 → .msg 오류 검사 → .res 존재 확인 → adams_run_info.json 기록
     결과 추출(results.json)은 post 단계 adams_extract.py 가 한다.
실패하면 0 이 아닌 코드로 종료한다(results.json 을 쓰지 않는다).
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

CASE_DIR = os.getcwd()
CASE_NAME = "case"          # case.adm / case.acf / case.res / case.msg

# params.json 키 -> (STATEMENT, id 또는 None, ARGUMENT[, 벡터 성분 인덱스])
STATEMENT_MAP = {
    # "arm_mass": ("PART", 2, "MASS"),
    # "spring_k": ("SPRINGDAMPER", 1, "K"),
    # "spring_c": ("SPRINGDAMPER", 1, "C"),
    # "pivot_x": ("MARKER", 10, "QP", 0),     # QP = x, y, z 의 0번 성분
    # "gravity_y": ("ACCGRAV", None, "JGRAV"),
}

# Adams View 설계변수 이름 매핑: params 키 -> View variable 전체 이름. 없으면 <model>.<key>
VIEW_DV_MAP = {
    # "arm_length": ".MODEL.DV_arm_length",
}

# TODO(사내검증): .msg 의 오류/성공 문구는 솔버 버전에 따라 다르다. 실제 실패 케이스의 .msg 로 확인한다.
ERROR_PATTERNS = [
    r"-{2,}\s*START:\s*ERROR",          # C++ 솔버 오류 블록 머리
    r"\bERROR\s*-{2,}",                 # ---- ERROR ---- 형식
    r"(?i)\bsimulation\s+(has\s+)?(failed|aborted)",
    r"(?i)\bfatal\b",
]
ADM_LINE_LIMIT = 80   # TODO(사내검증): 데이터셋 한 줄 길이 제한(구 FORTRAN 솔버 80자). 넘으면 경고만 한다.


def log(msg):
    print("[adams_case_run] " + str(msg))
    sys.stdout.flush()


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def fmt_num(v):
    if isinstance(v, bool):
        v = int(v)
    if isinstance(v, int):
        return str(v)
    s = "%.10g" % float(v)
    if "e" not in s and "." not in s:
        s += ".0"
    return s.upper()


# ------------------------------------------------------------------ ADM 텍스트 편집
_HEAD_RE = re.compile(r"^\s*([A-Za-z_]+)\s*/\s*(\d+)?")
_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eEdD][-+]?\d+)?"


def statement_blocks(lines):
    """[(NAME, id 또는 None, 시작 줄, 끝 줄(포함))]. 0번 줄(제목)은 건너뛴다. 이어지는 줄은 ',' 로 시작."""
    blocks = []
    cur = None
    for i, line in enumerate(lines):
        if i == 0:
            continue
        s = line.strip()
        if not s or s.startswith("!"):
            continue
        if s.startswith(","):
            if cur is not None:
                cur[3] = i
            continue
        m = _HEAD_RE.match(line)
        if cur is not None:
            blocks.append(tuple(cur))
            cur = None
        if m:
            sid = int(m.group(2)) if m.group(2) else None
            cur = [m.group(1).upper(), sid, i, i]
        elif s.upper().startswith("END"):
            blocks.append(("END", None, i, i))
    if cur is not None:
        blocks.append(tuple(cur))
    return blocks


def find_block(lines, name, sid):
    for b in statement_blocks(lines):
        if b[0] == name.upper() and (sid is None or b[1] == sid):
            return b
    raise KeyError("statement %s/%s not found in ADM" % (name, "" if sid is None else sid))


def _split_comment(line):
    k = line.find("!")
    return (line, "") if k < 0 else (line[:k], line[k:])


def set_arg(lines, name, sid, arg, value, comp=None):
    """statement 의 숫자 인자 1개(또는 벡터 인자의 comp 번째 성분)를 바꾼다. 없으면 KeyError."""
    _, _, i0, i1 = find_block(lines, name, sid)
    if comp is None:
        rx = re.compile(r"(?i)(?<![A-Za-z_])(" + re.escape(arg) + r"\s*=\s*)(" + _NUM + ")")
    else:
        rx = re.compile(r"(?i)(?<![A-Za-z_])(" + re.escape(arg) + r"\s*=\s*)(" + _NUM + r"(?:\s*,\s*" + _NUM + ")*)")
    for i in range(i0, i1 + 1):
        code, cmt = _split_comment(lines[i])
        m = rx.search(code)
        if not m:
            continue
        if comp is None:
            new = fmt_num(value)
        else:
            parts = [p.strip() for p in m.group(2).split(",")]
            if comp >= len(parts):
                raise KeyError("%s/%s %s has only %d components on one line" % (name, sid, arg, len(parts)))
            parts[comp] = fmt_num(value)
            new = ", ".join(parts)
        lines[i] = code[:m.start(2)] + new + code[m.end(2):] + cmt
        if len(lines[i].rstrip("\r\n")) > ADM_LINE_LIMIT:
            log("WARNING: line %d longer than %d chars: %s" % (i + 1, ADM_LINE_LIMIT, lines[i].strip()))
        return
    raise KeyError("argument %s= not found in %s/%s" % (arg, name, "" if sid is None else sid))


def apply_placeholders(text, values, used):
    def rep(mo):
        key = mo.group(1)
        if key not in values:
            raise KeyError("placeholder {{%s}} has no value in params.json" % key)
        used.add(key)
        return fmt_num(values[key])
    return re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", rep, text)


def ensure_results_xrf(lines):
    names = [b[0] for b in statement_blocks(lines)]
    if "RESULTS" in names:
        txt = "".join(lines).upper()
        if "XRF" not in txt:
            log("WARNING: RESULTS statement exists without XRF; .res may not be XML. Check the ADM.")
        return
    for b in statement_blocks(lines):
        if b[0] == "END":
            lines.insert(b[2], "RESULTS/XRF\n")
            log("added RESULTS/XRF before END")
            return
    lines.append("RESULTS/XRF\n")
    lines.append("END\n")


# ------------------------------------------------------------------ ACF
def build_acf(params, adm_name):
    sim = str(params.get("_sim_type", "dynamic")).upper()
    out = [adm_name, CASE_NAME]
    if "_integrator" in params or "_integrator_error" in params:
        s = "INTEGRATOR/" + str(params.get("_integrator", "GSTIFF")).upper()
        if "_integrator_error" in params:
            s += ", ERROR=" + fmt_num(params["_integrator_error"])
        out.append(s)
    if int(params.get("_static_first", 0)) and sim != "STATIC":
        out.append("SIMULATE/STATIC")
    if sim == "STATIC":
        out.append("SIMULATE/STATIC")
    else:
        out.append("SIMULATE/%s, END=%s, STEPS=%d" % (sim, fmt_num(params.get("_end_time", 1.0)),
                                                     int(params.get("_steps", 500))))
    out.append("STOP")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ 실행
def adams_exe(arg):
    return arg or os.environ.get("DDS_TOOL_ADAMS") or os.environ.get("DDS_ADAMS") or "mdi"


def run(cmd, stdout_name, timeout):
    log("run: " + " ".join(cmd))
    with open(os.path.join(CASE_DIR, stdout_name), "w") as fo:
        try:
            rc = subprocess.call(cmd, cwd=CASE_DIR, stdout=fo, stderr=subprocess.STDOUT,
                                 shell=(os.name == "nt"), timeout=timeout)
        except subprocess.TimeoutExpired:
            log("timeout after %s s" % timeout)
            return -999
    log("return code %s (informational; status is judged from files)" % rc)
    return rc


def view_export(params, exe, model_path, model_name, timeout):
    """Adams View 배치로 설계변수 수정 후 ADM 내보내기. TODO(사내검증): 명령 철자와 quit 동작."""
    ext = os.path.splitext(model_path)[1].lower()
    lines = []
    if ext == ".bin":
        lines.append('file binary read file_name = "%s"' % model_path.replace("\\", "/"))
    else:
        lines.append('file command read file_name = "%s"' % model_path.replace("\\", "/"))
    used = set()
    for k, v in params.items():
        if k.startswith("_") or k in STATEMENT_MAP:
            continue
        full = VIEW_DV_MAP.get(k, model_name.rstrip(".") + "." + k)
        lines.append("variable modify variable_name = %s real_value = %s" % (full, fmt_num(v)))
        used.add(k)
    lines.append('file adams_data_set write model_name = %s file_name = "%s.adm"' % (model_name, CASE_NAME))
    lines.append("quit")
    cmd_file = "dds_view_export.cmd"
    with open(os.path.join(CASE_DIR, cmd_file), "w") as f:
        f.write("\n".join(lines) + "\n")
    run([exe, "aview", "ru-standard", "b", cmd_file], "adams_view_stdout.txt", timeout)
    if not os.path.isfile(os.path.join(CASE_DIR, CASE_NAME + ".adm")):
        raise RuntimeError("Adams View did not write %s.adm (see adams_view_stdout.txt, aview.log)" % CASE_NAME)
    return used


def check_msg(msg_path):
    if not os.path.isfile(msg_path):
        return ["message file not found: " + os.path.basename(msg_path)]
    errs = []
    with open(msg_path, "r", errors="replace") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        for p in ERROR_PATTERNS:
            if re.search(p, line):
                errs.append(" | ".join(x.strip() for x in lines[i:i + 4] if x.strip()))
                break
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adm", help="ADM template (default: the single *.adm in case dir)")
    ap.add_argument("--acf", help="ACF template with {{name}} (default: generated)")
    ap.add_argument("--adams", help="mdi.bat path (default: env DDS_TOOL_ADAMS)")
    ap.add_argument("--solver-args", default="ru-standard i {acf} exit",
                    help="solver args after exe; {acf} is replaced. Confirm syntax per Adams version")
    ap.add_argument("--timeout", type=float, default=None)
    ap.add_argument("--view-model", help="Adams View .bin or .cmd model (design variables)")
    ap.add_argument("--view-model-name", default=".MODEL")
    a = ap.parse_args()

    params = read_json(os.path.join(CASE_DIR, "params.json"), {}) or {}
    timeout = a.timeout or params.get("_timeout") or 7200
    exe = adams_exe(a.adams)
    used = set()

    if a.view_model:
        used |= view_export(params, exe, a.view_model, a.view_model_name, timeout)
        adm = CASE_NAME + ".adm"
    else:
        adm = a.adm
        if not adm:
            cands = [p for p in glob.glob(os.path.join(CASE_DIR, "*.adm"))]
            if len(cands) != 1:
                raise RuntimeError("give --adm (found %d *.adm files)" % len(cands))
            adm = os.path.basename(cands[0])

    with open(os.path.join(CASE_DIR, adm), "r") as f:
        text = f.read()
    text = apply_placeholders(text, params, used)
    lines = text.splitlines(True)
    for key, spec in STATEMENT_MAP.items():
        if key not in params:
            continue
        name, sid, arg = spec[0], spec[1], spec[2]
        comp = spec[3] if len(spec) > 3 else None
        set_arg(lines, name, sid, arg, params[key], comp)
        used.add(key)
        log("set %s/%s %s%s = %s" % (name, sid, arg, "" if comp is None else "[%d]" % comp, params[key]))
    ensure_results_xrf(lines)
    with open(os.path.join(CASE_DIR, CASE_NAME + ".adm"), "w") as f:
        f.write("".join(lines))

    missing = [k for k in params if not k.startswith("_") and k not in used]
    if missing:
        raise RuntimeError("params not applied anywhere (typo?): " + ", ".join(sorted(missing)))

    if a.acf:
        with open(os.path.join(CASE_DIR, a.acf), "r") as f:
            acf_text = apply_placeholders(f.read(), params, set())
    else:
        acf_text = build_acf(params, CASE_NAME + ".adm")
    acf = CASE_NAME + ".acf"
    with open(os.path.join(CASE_DIR, acf), "w") as f:
        f.write(acf_text)
    log("ACF:\n" + acf_text)

    for ext in (".res", ".msg", ".req", ".gra", ".out"):
        p = os.path.join(CASE_DIR, CASE_NAME + ext)
        if os.path.exists(p):
            os.remove(p)
    cmd = [exe] + [x.replace("{acf}", acf) for x in a.solver_args.split()]
    run(cmd, "adams_solver_stdout.txt", timeout)

    errs = check_msg(os.path.join(CASE_DIR, CASE_NAME + ".msg"))
    res = os.path.join(CASE_DIR, CASE_NAME + ".res")
    if not os.path.isfile(res) or os.path.getsize(res) == 0:
        errs.append("results file %s.res missing or empty" % CASE_NAME)
    if errs:
        for e in errs[:20]:
            log("ERROR: " + e)
        sys.exit(1)

    end_time = None if str(params.get("_sim_type", "dynamic")).lower() == "static" else float(params.get("_end_time", 1.0))
    info = {"res": CASE_NAME + ".res", "msg": CASE_NAME + ".msg", "end_time": end_time}
    with open(os.path.join(CASE_DIR, "adams_run_info.json"), "w") as f:
        json.dump(info, f, indent=2)
    log("solver finished without error markers; run adams_extract.py as post step")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        log("FAILED: " + str(e))
        sys.exit(1)
