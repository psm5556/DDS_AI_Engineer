# -*- coding: utf-8 -*-
"""
LS-DYNA 케이스 실행 템플릿 (tool 키: python, 플랫폼 venv / Python 3)

  python dyna_case_run.py --lsdyna "C:/LSDYNA/lsdyna_smp_d_R13.exe" --ncpu 4 --memory 200m \
                          [--main main.k] [--lsprepost "C:/LSDYNA/lsprepost4.10/lsprepost.exe"]

처리 순서
  1) params.json 읽기 ('_' 로 시작하는 키는 제어용: _ncpu, _memory, _endtim 등)
  2) 케이스 폴더의 키워드 파일(.k/.key/.dyn/.inc)에 파라미터 반영 (케이스 폴더의 복사본이므로 제자리 수정)
       a) {{name}} 플레이스홀더 치환 (값은 FIELD_WIDTH 폭에 맞춰 오른쪽 정렬)
       b) *PARAMETER 카드의 같은 이름 값 교체 (고정폭 10자 / 콤마 자유형식 모두 지원)
     params 키가 어디에도 반영되지 않으면 실패로 처리한다(오타 방지).
  3) LS-DYNA 실행:  <lsdyna> i=main.k ncpu=N memory=M
  4) d3hsp / messag / mes0000 에서 Normal/Error termination 판정
  5) 결과 추출: glstat, rcforc ASCII (없으면 lasso-python 으로 binout 시도)
  6) (선택) LS-PrePost cfile 로 images/*.png 저장
  7) results.json 작성. 실패 시 작성하지 않고 종료코드 1.

파라미터 이름 매핑: params.json 키(소문자_스네이크, 길 수 있음)와 *PARAMETER 이름(최대 약 9자)이
다르면 NAME_MAP 에 적는다.
"""
import argparse
import glob
import json
import math
import os
import re
import subprocess
import sys

CASE_DIR = os.getcwd()
KEYWORD_EXT = ('.k', '.key', '.dyn', '.inc')
FIELD_WIDTH = 10

# params.json 키 -> *PARAMETER 이름 (대소문자 무시). 없으면 같은 이름으로 찾는다.
NAME_MAP = {
    # "thickness": "thk",
    # "impact_velocity": "vel",
}

# 이미지: LS-PrePost cfile 템플릿. TODO(사내검증): 명령 문법은 사내 LS-PrePost 버전의
# lspost.cfile(GUI 조작 기록)로 반드시 확인한다. {d3plot}, {img_dir} 는 치환된다.
CFILE_TEMPLATE = """open d3plot "{d3plot}"
ac
anim last
fringe 9
pfringe
print png "{img_dir}/vonmises_last.png" LANDSCAPE nocompress gamma 1.000 opaque enlisted "OGL1x1"
fringe 7
pfringe
print png "{img_dir}/eps_last.png" LANDSCAPE nocompress gamma 1.000 opaque enlisted "OGL1x1"
quit
"""
# 위 템플릿에서 확인된 것: 'open d3plot <경로>', 'fringe 7'(유효소성변형률), 'quit'(LS-OPT 예제).
# TODO(사내검증): 'anim last'(마지막 state 이동), 'fringe 9'(von Mises), 'print png ...' 옵션 문자열,
#                 -nographics 모드에서 print 렌더링 가능 여부.


def log(msg):
    print("[dyna_case_run] " + str(msg), flush=True)


def read_json(path, default=None):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 값 서식
def fmt_field(value, width=FIELD_WIDTH):
    """숫자를 width 글자 이내 문자열로. 정밀도를 줄여가며 맞춘다. 맞출 수 없으면 오류."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        s = str(value)
        if len(s) > width:
            raise ValueError("integer %s does not fit %d chars" % (s, width))
        return s
    v = float(value)
    for prec in range(width, 0, -1):
        for s in ("%.*g" % (prec, v), "%.*E" % (max(prec - 1, 0), v)):
            s = s.replace("E+0", "E+").replace("E-0", "E-").replace("e+0", "e+").replace("e-0", "e-")
            if "." not in s and "e" not in s.lower():
                s += "."          # 실수임을 명시 (I 필드와 구분)
            if len(s) <= width:
                return s
    raise ValueError("value %r does not fit %d chars" % (value, width))


# ---------------------------------------------------------------- 키워드 반영
PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def apply_placeholders(text, values, used):
    def rep(mo):
        key = mo.group(1)
        if key not in values:
            raise KeyError("placeholder {{%s}} has no value in params.json" % key)
        used.add(key)
        # 플레이스홀더 길이와 상관없이 FIELD_WIDTH 폭으로 출력. 템플릿 작성 시
        # 플레이스홀더가 한 필드(10자)를 차지하도록 두거나, 그 줄을 콤마 자유형식으로 쓴다.
        return fmt_field(values[key], FIELD_WIDTH - 1).rjust(FIELD_WIDTH)  # 앞 공백 1자 확보
    return PLACEHOLDER.sub(rep, text)


def apply_parameter_cards(lines, values, used):
    """*PARAMETER 블록의 값 교체. values 키는 소문자 dyna 이름."""
    out, in_block = [], False
    for line in lines:
        s = line.lstrip()
        if s.startswith("*"):
            kw = s.split()[0].upper()
            in_block = kw == "*PARAMETER" or kw.startswith("*PARAMETER_DUPLICATION") or kw == "*PARAMETER_LOCAL"
            out.append(line)
            continue
        if not in_block or s.startswith("$") or not s.strip():
            out.append(line)
            continue
        body = line.rstrip("\r\n")
        nl = line[len(body):]
        if "," in body:   # 자유형식: name, value, name, value ...
            toks = body.split(",")
            for i in range(0, len(toks) - 1, 2):
                nm = toks[i].strip()
                key = nm[1:].strip().lower() if nm[:1].upper() in "RIC" else nm.lower()
                if key in values:
                    toks[i + 1] = fmt_field(values[key], 20)
                    used.add(key)
            out.append(",".join(toks) + nl)
            continue
        body = body.ljust(80)
        new = body
        for i in range(4):   # 고정폭: (이름 A10, 값 10자) x 4
            nf = body[20 * i:20 * i + 10].strip()
            if not nf:
                continue
            ptype, key = nf[0].upper(), nf[1:].strip().lower()
            if ptype not in "RIC":   # 타입 문자 없이 쓴 경우 (구버전 형식)
                ptype, key = "R", nf.lower()
            if key in values:
                val = values[key]
                if ptype == "I":
                    val = int(round(float(val)))
                field = fmt_field(val, 9).rjust(10)   # 이름 필드와 붙지 않게 9자 이내
                new = new[:20 * i + 10] + field + new[20 * i + 20:]
                used.add(key)
        out.append(new.rstrip() + nl)
    return out


def apply_params(params):
    values = {}
    for k, v in params.items():
        if str(k).startswith("_"):
            continue
        values[str(NAME_MAP.get(k, k)).lower()] = v
    # 플레이스홀더용(원래 키)과 *PARAMETER용(매핑된 소문자 이름) 둘 다 허용
    ph_values = dict((k, v) for k, v in params.items() if not str(k).startswith("_"))
    used = set()
    files = [f for f in os.listdir(CASE_DIR) if f.lower().endswith(KEYWORD_EXT)]
    for name in files:
        path = os.path.join(CASE_DIR, name)
        with open(path, "r", encoding="latin-1", newline="") as f:
            text = f.read()
        orig = text
        if "{{" in text:
            text = apply_placeholders(text, ph_values, used)
        if "*PARAMETER" in text.upper():
            text = "".join(apply_parameter_cards(text.splitlines(True), values, used))
        if text != orig:
            with open(path, "w", encoding="latin-1", newline="") as f:
                f.write(text)
            log("updated " + name)
    missing = [k for k in ph_values
               if k not in used and str(NAME_MAP.get(k, k)).lower() not in used]
    if missing:
        raise KeyError("params not applied to any keyword file: " + ", ".join(missing))


# ---------------------------------------------------------------- 실행/판정
def find_main(arg, case):
    if arg:
        return arg
    for name in (case or {}).get("inputs", []):
        if str(name).lower().endswith(KEYWORD_EXT[:3]):
            return name
    for cand in ("main.k", "main.key", "input.k"):
        if os.path.exists(os.path.join(CASE_DIR, cand)):
            return cand
    raise RuntimeError("main keyword file not found (use --main)")


def termination_status():
    """'normal' | 'error' | 'unknown'. 공백을 모두 지우고 찾는다
    (d3hsp/messag 에는 'N o r m a l    t e r m i n a t i o n' 처럼 띄어 쓰여 있다)."""
    cands = ["d3hsp", "messag"] + sorted(glob.glob(os.path.join(CASE_DIR, "mes0*")))
    status = "unknown"
    for name in cands:
        path = name if os.path.isabs(name) else os.path.join(CASE_DIR, name)
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200000))
            tail = f.read().decode("latin-1")
        squeezed = re.sub(r"\s+", "", tail)
        if "Errortermination" in squeezed:
            for line in tail.splitlines():
                if "*** Error" in line or "*** error" in line.lower():
                    log(os.path.basename(path) + ": " + line.strip())
            return "error"
        if "Normaltermination" in squeezed:
            status = "normal"
    return status


def run_solver(exe, main, ncpu, memory, timeout):
    cmd = [exe, "i=" + main, "ncpu=" + str(ncpu)]
    if memory:
        cmd.append("memory=" + str(memory))
    log("run: " + " ".join(cmd))
    with open(os.path.join(CASE_DIR, "lsdyna_stdout.txt"), "w", encoding="utf-8", errors="replace") as out:
        rc = subprocess.call(cmd, cwd=CASE_DIR, stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, timeout=timeout)
    log("lsdyna return code: %s (informational)" % rc)


# ---------------------------------------------------------------- 결과 파싱
NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[EeDd][-+]?\d+)?"


def parse_glstat(path):
    """glstat ASCII -> [{'time':..., 'kinetic_energy':...}, ...]
    'kinetic energy..........  1.2345E+03' 형식(점 + 값, '=' 선택)을 읽는다.
    같은 키가 다시 나오면 새 레코드로 본다. TODO(사내검증): 버전별 줄 형식 확인."""
    recs, cur = [], {}
    pat = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /()%_\-]*?)\s*\.{2,}\s*=?\s*(" + NUM + r")\s*$")
    with open(path, "r", encoding="latin-1") as f:
        for line in f:
            mo = pat.match(line)
            if not mo:
                continue
            key = re.sub(r"[^0-9a-z]+", "_", mo.group(1).strip().lower()).strip("_")
            if key in cur:
                recs.append(cur)
                cur = {}
            cur[key] = float(mo.group(2).replace("D", "E").replace("d", "e"))
    if cur:
        recs.append(cur)
    return recs


def parse_rcforc(path):
    """rcforc ASCII -> {(side, id): [(t, fx, fy, fz), ...]}
    TODO(사내검증): R12+ 에서 master/slave 대신 main/secondary 로 표기될 수 있어 둘 다 받는다."""
    pat = re.compile(r"^\s*(master|slave|main|secondary)\s+(\d+)\s+time\s+(" + NUM + r")\s+x\s+(" + NUM +
                     r")\s+y\s+(" + NUM + r")\s+z\s+(" + NUM + r")", re.I)
    data = {}
    with open(path, "r", encoding="latin-1") as f:
        for line in f:
            mo = pat.match(line)
            if mo:
                k = (mo.group(1).lower(), int(mo.group(2)))
                data.setdefault(k, []).append(tuple(float(mo.group(i)) for i in range(3, 7)))
    return data


def extract_results():
    res = {}
    gl = os.path.join(CASE_DIR, "glstat")
    if os.path.exists(gl):
        recs = parse_glstat(gl)
        if recs:
            last = recs[-1]
            for k in ("time", "kinetic_energy", "internal_energy", "total_energy", "hourglass_energy",
                      "energy_ratio", "added_mass"):
                if k in last:
                    res["final_" + k] = last[k]
            if any("kinetic_energy" in r for r in recs):
                res["max_kinetic_energy"] = max(r.get("kinetic_energy", 0.0) for r in recs)
            if "internal_energy" in last and any("hourglass_energy" in r for r in recs) and last["internal_energy"]:
                res["hourglass_ratio"] = last.get("hourglass_energy", 0.0) / last["internal_energy"]
    rc = os.path.join(CASE_DIR, "rcforc")
    if os.path.exists(rc):
        for (side, cid), rows in sorted(parse_rcforc(rc).items()):
            if side in ("master", "main"):   # 한쪽만 사용 (반대쪽은 부호만 반대)
                res["max_contact_force_%d" % cid] = max(math.sqrt(fx * fx + fy * fy + fz * fz)
                                                        for _, fx, fy, fz in rows)
    if not res:
        res.update(extract_binout())
    return res


def extract_binout():
    """ASCII 출력이 없을 때 binout 에서 glstat 를 읽는다. lasso-python 필요 (pip install lasso-python)."""
    if not glob.glob(os.path.join(CASE_DIR, "binout*")):
        return {}
    try:
        from lasso.dyna import Binout
    except ImportError:
        log("lasso-python not installed in platform venv; cannot read binout")
        return {}
    b = Binout(os.path.join(CASE_DIR, "binout*"))
    res = {}
    if "glstat" in b.read():
        names = b.read("glstat")
        for k in ("kinetic_energy", "internal_energy", "total_energy"):  # TODO(사내검증): b.read('glstat') 로 이름 확인
            if k in names:
                arr = b.read("glstat", k)
                res["final_" + k] = float(arr[-1])
        if "kinetic_energy" in names:
            res["max_kinetic_energy"] = float(max(b.read("glstat", "kinetic_energy")))
    return res


# ---------------------------------------------------------------- 이미지
def make_images(lsprepost, timeout):
    d3 = os.path.join(CASE_DIR, "d3plot")
    if not os.path.exists(d3):
        log("d3plot not found; skip images")
        return
    img_dir = os.path.join(CASE_DIR, "images")
    os.makedirs(img_dir, exist_ok=True)
    cfile = os.path.join(CASE_DIR, "dds_images.cfile")
    with open(cfile, "w", encoding="utf-8") as f:
        f.write(CFILE_TEMPLATE.format(d3plot=d3.replace("\\", "/"), img_dir=img_dir.replace("\\", "/")))
    cmd = [lsprepost, "-nographics", "c=" + cfile]
    log("run: " + " ".join(cmd))
    try:
        subprocess.call(cmd, cwd=CASE_DIR, stdin=subprocess.DEVNULL, timeout=timeout)
    except Exception as e:   # 이미지는 선택 출력
        log("lsprepost failed: " + str(e))
    log("images: " + ", ".join(sorted(os.listdir(img_dir))))


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lsdyna", default=os.environ.get("LSDYNA_EXE", "lsdyna"))
    ap.add_argument("--lsprepost", default=os.environ.get("LSPREPOST_EXE"))
    ap.add_argument("--main", default=None)
    ap.add_argument("--ncpu", type=int, default=None)
    ap.add_argument("--memory", default=None)
    ap.add_argument("--timeout", type=float, default=None, help="seconds")
    ap.add_argument("--skip-run", action="store_true", help="결과 추출만 (디버깅)")
    a = ap.parse_args()

    res_path = os.path.join(CASE_DIR, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)
    params = read_json(os.path.join(CASE_DIR, "params.json"), {})
    case = read_json(os.path.join(CASE_DIR, "case.json"), {})
    log("params: " + json.dumps(params))

    main_k = find_main(a.main, case)
    if not a.skip_run:
        apply_params(params)
        ncpu = a.ncpu or int(params.get("_ncpu", 1))
        memory = a.memory or params.get("_memory")
        run_solver(a.lsdyna, main_k, ncpu, memory, a.timeout)

    st = termination_status()
    log("termination: " + st)
    if st != "normal":
        sys.exit(1)

    results = extract_results()
    if a.lsprepost:
        make_images(a.lsprepost, 600)
    results = dict((k, float(v)) for k, v in results.items() if v is not None)
    if not results:
        log("no results extracted (check *DATABASE_GLSTAT / *DATABASE_RCFORC output requests)")
        sys.exit(1)
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, sort_keys=True)
    log("results: " + json.dumps(results, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        log("FAILED: " + str(e))
        sys.exit(1)
