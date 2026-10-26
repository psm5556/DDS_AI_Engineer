# -*- coding: utf-8 -*-
"""
Simcenter 3D 케이스 드라이버 + Nastran 결과 추출 (플랫폼 파이썬, tool 키 'python')

두 가지 모드:
  (A) 전체 케이스:  run_journal 로 sc_update_solve.py 실행 -> op2/f06 결과 추출 -> results.json
      python sc_run_case.py --run-journal "C:/.../NXBIN/run_journal.exe" --sim bracket_sim1.sim
  (B) 추출만:       이미 해석된 결과에서 results.json 만 생성
      python sc_run_case.py --extract-only [--op2 x.op2] [--f06 x.f06]

필요 패키지(플랫폼 venv): numpy, pyNastran  (pip install pyNastran)
run_journal 경로 우선순위: --run-journal > 환경변수 NX_RUN_JOURNAL > %UGII_BASE_DIR%/NXBIN/run_journal.exe
알 수 없는 추가 인자(key=value)는 그대로 저널 -args 로 전달된다 (예: solution="Solution 1" save=1).

results.json 키(있는 결과만):
  max_disp_mm          최대 변위 크기(모델 단위계 기준, mm-N 모델이면 mm)
  max_von_mises        요소 von Mises 최대값(요소 절점/중심 '비평균' 값, 모델 응력 단위)
  max_von_mises_<etype>  요소 타입별 최대값 (ctetra, chexa, cquad4 ...)
  reaction_fx/fy/fz    SPC 반력 합계
  freq_1..freq_n       고유진동수[Hz] (모달 해석)
"""
import argparse
import glob
import json
import os
import subprocess
import sys

STRESS_TABLES = ["ctetra_stress", "chexa_stress", "cpenta_stress", "cpyram_stress",
                 "cquad4_stress", "ctria3_stress", "cquad8_stress", "ctria6_stress",
                 "cquadr_stress", "ctriar_stress"]


def log(msg):
    print("[DDS] " + str(msg))
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# f06 / op2
# ---------------------------------------------------------------------------
def check_f06(path):
    with open(path, "r", errors="replace") as f:
        txt = f.read()
    fatals = [ln.strip() for ln in txt.splitlines() if "FATAL" in ln.upper()]
    if fatals:
        return False, "FATAL: " + " | ".join(fatals[:5])
    if "END OF JOB" not in txt:
        return False, "END OF JOB 없음(중단/미완료)"
    return True, "ok"


def get_table(model, name):
    """pyNastran 1.4+: model.op2_results.stress.<name>, 1.3: model.<name>"""
    try:
        d = getattr(model.op2_results.stress, name)
        if d:
            return d
    except Exception:
        pass
    return getattr(model, name, None) or {}


def headers_of(obj):
    try:
        return list(obj.get_headers())
    except Exception:
        return list(getattr(obj, "headers", []))


def extract_op2(op2_path, n_modes=6):
    import numpy as np
    from pyNastran.op2.op2 import read_op2

    try:
        model = read_op2(op2_path, build_dataframe=False, debug=False, mode="nx")
    except TypeError:
        model = read_op2(op2_path, build_dataframe=False, debug=False)

    res = {}
    # 변위
    dmax = None
    for key, obj in model.displacements.items():
        mag = np.sqrt((obj.data[:, :, :3] ** 2).sum(axis=2))
        v = float(mag.max())
        dmax = v if dmax is None else max(dmax, v)
    if dmax is not None:
        res["max_disp_mm"] = dmax

    # 응력 (von Mises)
    vm_all = None
    for tname in STRESS_TABLES:
        table = get_table(model, tname)
        vmax = None
        for key, obj in table.items():
            h = headers_of(obj)
            if "von_mises" not in h:
                log("경고: %s 에 von_mises 없음(headers=%s) - 출력 설정이 max shear 일 수 있음" % (tname, h))
                continue
            col = h.index("von_mises")
            v = float(np.abs(obj.data[:, :, col]).max())
            vmax = v if vmax is None else max(vmax, v)
        if vmax is not None:
            res["max_von_mises_" + tname.replace("_stress", "")] = vmax
            vm_all = vmax if vm_all is None else max(vm_all, vmax)
    if vm_all is not None:
        res["max_von_mises"] = vm_all

    # SPC 반력 합계 (마지막 시간/하중스텝, 첫 subcase)
    for key, obj in model.spc_forces.items():
        s = obj.data[-1, :, :3].sum(axis=0)
        res["reaction_fx"], res["reaction_fy"], res["reaction_fz"] = [float(x) for x in s]
        break

    # 고유진동수
    for title, eig in model.eigenvalues.items():
        cyc = list(eig.cycles)[:n_modes]
        for i, f in enumerate(cyc):
            res["freq_%d" % (i + 1)] = float(f)
        break

    subcases = sorted(set(str(k) for k in model.displacements.keys()))
    if len(subcases) > 1:
        log("주의: subcase 가 여러 개(%s) - 위 값은 전체 최대값/첫 subcase 반력" % ", ".join(subcases))
    return res


def find_latest(cdir, ext):
    c = glob.glob(os.path.join(cdir, "*" + ext))
    return max(c, key=os.path.getmtime) if c else None


def do_extract(cdir, op2, f06, n_modes):
    st_path = os.path.join(cdir, "solve_status.json")
    if (not op2 or not f06) and os.path.isfile(st_path):
        with open(st_path, "r", encoding="utf-8") as f:
            st = json.load(f)
        op2 = op2 or st.get("op2")
        f06 = f06 or st.get("f06")
    op2 = op2 or find_latest(cdir, ".op2")
    f06 = f06 or (os.path.splitext(op2)[0] + ".f06" if op2 else None)
    if not op2 or not os.path.isfile(op2):
        raise RuntimeError("op2 파일 없음: %s" % op2)
    if f06 and os.path.isfile(f06):
        ok, msg = check_f06(f06)
        if not ok:
            raise RuntimeError("f06 판정 실패: " + msg)
    else:
        log("경고: f06 없음 - 완료 판정 생략")
    log("op2=" + op2)
    res = extract_op2(op2, n_modes)
    if not res:
        raise RuntimeError("op2 에서 추출된 결과가 없음(출력 요청 확인)")
    return res


# ---------------------------------------------------------------------------
# run_journal 실행
# ---------------------------------------------------------------------------
def resolve_run_journal(arg):
    if arg:
        return arg
    if os.environ.get("NX_RUN_JOURNAL"):
        return os.environ["NX_RUN_JOURNAL"]
    base = os.environ.get("UGII_BASE_DIR")
    if base:
        p = os.path.join(base, "NXBIN", "run_journal.exe")
        if os.path.isfile(p):
            return p
    raise RuntimeError("run_journal.exe 경로를 알 수 없음: --run-journal 또는 NX_RUN_JOURNAL 지정")


def run_journal(exe, journal, jargs, cdir, timeout):
    cmd = [exe, journal, "-args"] + jargs
    log("run: " + " ".join(cmd))
    p = subprocess.Popen(cmd, cwd=cdir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True, errors="replace")
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        p.kill()
        out, _ = p.communicate()
        print(out)
        raise RuntimeError("run_journal 타임아웃(%ss)" % timeout)
    print(out)
    return p.returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract-only", action="store_true")
    ap.add_argument("--op2")
    ap.add_argument("--f06")
    ap.add_argument("--n-modes", type=int, default=6)
    ap.add_argument("--run-journal")
    ap.add_argument("--journal", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                      "sc_update_solve.py"))
    ap.add_argument("--sim")
    ap.add_argument("--timeout", type=int, default=6 * 3600)
    ap.add_argument("--case-dir", default=os.getcwd())
    a, extra = ap.parse_known_args()
    cdir = os.path.abspath(a.case_dir)

    res_path = os.path.join(cdir, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)

    if not a.extract_only:
        exe = resolve_run_journal(a.run_journal)
        if not os.path.isfile(a.journal):
            raise RuntimeError("저널 없음: " + a.journal)
        jargs = ["case_dir=" + cdir] + (["sim=" + a.sim] if a.sim else []) + extra
        rc = run_journal(exe, a.journal, jargs, cdir, a.timeout)
        st_path = os.path.join(cdir, "solve_status.json")
        if not os.path.isfile(st_path):
            raise RuntimeError("solve_status.json 없음 - 저널 실패(rc=%s), 위 로그 확인" % rc)
        with open(st_path, "r", encoding="utf-8") as f:
            st = json.load(f)
        if not st.get("ok"):
            raise RuntimeError("해석 실패: " + str(st.get("message")))

    res = do_extract(cdir, a.op2, a.f06, a.n_modes)
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    log("results.json: " + json.dumps(res))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
