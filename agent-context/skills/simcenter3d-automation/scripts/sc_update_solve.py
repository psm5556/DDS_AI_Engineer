# -*- coding: utf-8 -*-
"""
Simcenter 3D 케이스 저널: CAD 파라미터 변경 -> Idealized/FEM 업데이트 -> (선택) 하중 크기 변경
-> Simcenter Nastran 해석 -> 완료 판정 -> solve_status.json

실행 (tool 키 'simcenter'):
  run_journal.exe sc_update_solve.py -args sim=bracket_sim1.sim solution="Solution 1"

인자(-args, key=value, 전부 선택):
  sim=<.sim>            없으면 케이스 폴더의 *.sim 하나를 자동 선택
  cad=<.prt>            마스터 CAD 파트. 없으면 *.prt 중 _i.prt 가 아닌 것 하나
  idealized=<_i.prt>    없으면 *_i.prt 하나(없어도 됨)
  solution=<이름>       해석할 솔루션 이름. 없으면 첫 번째 솔루션
  strict=1|0            params 키가 어느 파트 expression 에도 없으면 실패(기본 1)
  save=0|1              해석 전에 변경된 sim/fem/prt 를 저장(케이스 폴더 사본이므로 안전)
  extract_python=<exe>  지정 시 해석 후 '<exe> <extract_script> --extract-only' 실행 -> results.json
  extract_script=<py>   sc_run_case.py 경로(extract_python 과 함께)
  case_dir=<폴더>       기본: 현재 작업 폴더

params.json 키 처리 순서:
  1) BC_PARAM_MAP 에 있으면 하중/구속 크기로 반영(아래 표, 기본 비어 있음)
  2) 마스터 CAD 파트 expression -> 3) sim 파트 expression -> 4) fem 파트 expression
결과 추출은 이 저널이 하지 않는다(NX 내장 파이썬에 pyNastran 이 없음). sc_run_case.py 참조.
"""
import glob
import json
import os
import subprocess
import sys
import time
import traceback

import NXOpen
import NXOpen.CAE

# ---------------------------------------------------------------------------
# (선택) params 키 -> 하중/구속 스칼라 속성 매핑
#   "params키": ("BC 이름(트리에 보이는 이름)", "속성 이름", "단위 이름")
# 예: "force_n": ("Force(1)", "Magnitude", "Newton")
# TODO(사내검증): 속성 이름("Magnitude" 등)과 단위 이름은 해당 하중 편집을 Journal 로 기록해 확인.
# 권장 대안: GUI 에서 하중 크기 필드에 sim 파트 expression(예: force_n)을 입력해 두면
#           이 매핑 없이 expression 변경만으로 반영된다.
# ---------------------------------------------------------------------------
BC_PARAM_MAP = {
}


def log(msg):
    print("[DDS] " + str(msg))
    sys.stdout.flush()


def parse_args():
    out = {}
    for tok in sys.argv[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def flag(args, key, default="0"):
    return str(args.get(key, default)).strip().lower() in ("1", "true", "yes", "y")


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def pick(cdir, explicit, pattern, accept=None, required=True):
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(cdir, explicit)
        if not os.path.isfile(p):
            raise RuntimeError("파일이 없습니다: " + p)
        return os.path.abspath(p)
    cands = [p for p in glob.glob(os.path.join(cdir, pattern)) if accept is None or accept(p)]
    if len(cands) == 1:
        return os.path.abspath(cands[0])
    if not cands and not required:
        return None
    raise RuntimeError("%s 를 하나로 특정할 수 없습니다(후보: %s). -args 로 지정하세요."
                       % (pattern, ", ".join(os.path.basename(c) for c in cands)))


def same_file(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def get_or_open(session, path):
    """이미 로드돼 있으면 그 파트, 아니면 OpenBase 로 연다."""
    for p in session.Parts:
        try:
            if p.FullPath and same_file(p.FullPath, path):
                return p
        except Exception:
            pass
    part, st = session.Parts.OpenBase(path)
    return part


def display(session, part):
    """디스플레이/워크 파트 전환 (CAE 에서 Journal 기록 시 나오는 형태)."""
    session.Parts.SetActiveDisplay(part, NXOpen.DisplayPartOption.AllowAdditional,
                                   NXOpen.PartDisplayPartWorkPartOption.UseLast)


def do_update(session, label):
    mark = session.SetUndoMark(NXOpen.Session.MarkVisibility.Invisible, label)
    try:
        n = session.UpdateManager.DoUpdate(mark)
    except Exception as e:
        raise RuntimeError(label + " 업데이트 예외: " + str(e))
    if n:
        raise RuntimeError(label + " 업데이트 오류 %d건 (형상 재생성 실패 가능)" % n)


def find_expr(part, name):
    if part is None:
        return None
    try:
        return part.Expressions.FindObject(name)
    except Exception:
        return None


def edit_expr(part, expr, value):
    rhs = str(int(round(value))) if expr.Type == "Integer" else repr(float(value))
    part.Expressions.EditWithUnits(expr, expr.Units, rhs)
    log("expression %s = %s  (%s)" % (expr.Name, rhs, os.path.basename(part.FullPath)))


def find_by_name(collection, name):
    for obj in collection:
        try:
            if obj.Name == name:
                return obj
        except Exception:
            pass
    return None


def set_bc_scalar(sim_part, simulation, bc_name, prop, value, unit_name):
    """하중/구속 스칼라 크기 변경. TODO(사내검증): 편집 Journal 기록으로 확인."""
    bc = find_by_name(simulation.Loads, bc_name) or find_by_name(simulation.Constraints, bc_name)
    if bc is None:
        raise RuntimeError("하중/구속을 찾을 수 없음: " + bc_name)
    builder = simulation.CreateBcBuilderForBc(bc)
    try:
        pt = builder.PropertyTable
        fe = pt.GetScalarFieldPropertyValue(prop)
        unit = sim_part.UnitCollection.FindObject(unit_name)
        fe.EditFieldExpression(repr(float(value)), unit, [], False)
        pt.SetScalarFieldPropertyValue(prop, fe)
        builder.CommitAddBc()
    finally:
        builder.Destroy()
    log("BC %s.%s = %s %s" % (bc_name, prop, value, unit_name))


def result_basename(sim_path, solution_name):
    # NX 기본 규칙: <sim 파일명>-<솔루션명 소문자, 공백->_>  예) bracket_sim1-solution_1
    # TODO(사내검증): 사내 Customer Defaults 에 따라 다를 수 있어 아래에서 최신 f06 도 함께 탐색
    base = os.path.splitext(os.path.basename(sim_path))[0]
    return base + "-" + solution_name.lower().replace(" ", "_")


def check_f06(path):
    """(ok, 메시지). FATAL 이 있거나 END OF JOB 이 없으면 실패."""
    with open(path, "r", errors="replace") as f:
        txt = f.read()
    fatals = [ln.strip() for ln in txt.splitlines() if "FATAL" in ln.upper()]
    if fatals:
        return False, "FATAL: " + " | ".join(fatals[:5])
    # 정상 종료된 f06 끝에는 '* * * END OF JOB * * *' 가 있다
    if "END OF JOB" not in txt:
        return False, "END OF JOB 없음(중단/미완료)"
    return True, "ok"


def find_result_files(sim_path, cdir, solution_name, t_start):
    rb = result_basename(sim_path, solution_name)
    dirs = [os.path.dirname(sim_path), cdir]
    for d in dirs:
        f06 = os.path.join(d, rb + ".f06")
        if os.path.isfile(f06):
            return f06, os.path.join(d, rb + ".op2")
    # 대안: 해석 시작 이후 갱신된 가장 최신 f06
    cands = []
    for d in dirs:
        for f in glob.glob(os.path.join(d, "*.f06")):
            if os.path.getmtime(f) >= t_start - 1:
                cands.append(f)
    if not cands:
        return None, None
    f06 = max(cands, key=os.path.getmtime)
    return f06, os.path.splitext(f06)[0] + ".op2"


def main():
    args = parse_args()
    cdir = os.path.abspath(args.get("case_dir") or os.getcwd())
    for stale in ("results.json", "solve_status.json"):
        p = os.path.join(cdir, stale)
        if os.path.exists(p):
            os.remove(p)
    params = {}
    pp = os.path.join(cdir, "params.json")
    if os.path.isfile(pp):
        with open(pp, "r", encoding="utf-8") as f:
            params = json.load(f) or {}
    strict = flag(args, "strict", "1")

    sim_path = pick(cdir, args.get("sim"), "*.sim")
    cad_path = pick(cdir, args.get("cad"), "*.prt",
                    accept=lambda p: not p.lower().endswith("_i.prt"), required=False)
    ideal_path = pick(cdir, args.get("idealized"), "*_i.prt", required=False)
    log("sim=%s cad=%s idealized=%s" % (sim_path, cad_path, ideal_path))

    session = NXOpen.Session.GetSession()
    try:
        # 케이스 폴더의 사본(fem/_i.prt/prt)을 열도록. TODO(사내검증) enum 이름
        session.Parts.LoadOptions.ComponentLoadMethod = NXOpen.LoadOptions.LoadMethod.FromDirectory
    except Exception as e:
        log("LoadOptions 설정 실패: " + str(e))

    sim_part, st = session.Parts.OpenBaseDisplay(sim_path)
    sim_part = session.Parts.BaseWork          # NXOpen.CAE.SimPart
    simulation = sim_part.Simulation
    fem_part = sim_part.FemPart                # TODO(사내검증) 어셈블리 FEM(.afm)이면 구조가 다름

    cad_part = get_or_open(session, cad_path) if cad_path else None
    ideal_part = get_or_open(session, ideal_path) if ideal_path else None

    # 원본 폴더 파일이 열리지 않았는지 확인
    for p in (sim_part, fem_part, cad_part, ideal_part):
        if p is not None and not os.path.normcase(p.FullPath).startswith(os.path.normcase(cdir)):
            raise RuntimeError("케이스 폴더 밖의 파일이 로드됨(원본 수정 위험): " + p.FullPath)

    # params 라우팅
    route = {"cad": [], "sim": [], "fem": [], "bc": []}
    missing = []
    for name, val in params.items():
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            continue
        if name in BC_PARAM_MAP:
            route["bc"].append(name)
        elif find_expr(cad_part, name) is not None:
            route["cad"].append(name)
        elif find_expr(sim_part, name) is not None:
            route["sim"].append(name)
        elif find_expr(fem_part, name) is not None:
            route["fem"].append(name)
        else:
            missing.append(name)
    if missing:
        msg = "어느 파트에도 없는 파라미터: " + ", ".join(missing)
        if strict:
            raise RuntimeError(msg)
        log("경고: " + msg)
    log("routing=" + json.dumps(route))

    # 1) CAD 변경 -> 업데이트
    if route["cad"]:
        display(session, cad_part)
        for n in route["cad"]:
            edit_expr(cad_part, find_expr(cad_part, n), params[n])
        do_update(session, "CAD")
    # 2) Idealized 업데이트 (WAVE 링크 바디 갱신). TODO(사내검증): FEM 업데이트만으로 충분한지
    if route["cad"] and ideal_part is not None:
        display(session, ideal_part)
        do_update(session, "Idealized")
    # 3) FEM 업데이트(형상 재연결 + 재메쉬)
    if route["cad"] or route["fem"]:
        display(session, fem_part)
        for n in route["fem"]:
            edit_expr(fem_part, find_expr(fem_part, n), params[n])
        if route["fem"]:
            do_update(session, "FEM expressions")
        fem_part.BaseFEModel.UpdateFemodel()      # TODO(사내검증) 반환값/메쉬 실패 감지
        log("FEM 업데이트 완료")
    # 4) SIM 변경
    display(session, sim_part)
    for n in route["sim"]:
        edit_expr(sim_part, find_expr(sim_part, n), params[n])
    if route["sim"]:
        do_update(session, "SIM expressions")
    for n in route["bc"]:
        bc_name, prop, unit_name = BC_PARAM_MAP[n]
        set_bc_scalar(sim_part, simulation, bc_name, prop, params[n], unit_name)

    if flag(args, "save"):
        for p in (cad_part, ideal_part, fem_part, sim_part):
            if p is not None:
                p.Save(NXOpen.BasePart.SaveComponents.TrueValue, NXOpen.BasePart.CloseAfterSave.FalseValue)
        log("저장 완료")

    # 5) 해석
    sol_name = args.get("solution")
    solution = None
    for s in simulation.Solutions:
        if sol_name is None or s.Name == sol_name:
            solution = s
            break
    if solution is None:
        raise RuntimeError("솔루션을 찾을 수 없음: " + str(sol_name))
    sol_name = solution.Name
    log("solve: " + sol_name)

    t0 = time.time()
    ssm = NXOpen.CAE.SimSolveManager.GetSimSolveManager(session)
    n_ok, n_fail, n_skip = ssm.SolveChainOfSolutions(
        [solution],
        NXOpen.CAE.SimSolution.SolveOption.Solve,
        NXOpen.CAE.SimSolution.SetupCheckOption.CompleteCheckAndOutputErrors,
        NXOpen.CAE.SimSolution.SolveMode.Foreground)   # Foreground: 끝날 때까지 대기
    elapsed = time.time() - t0
    log("solved=%d failed=%d skipped=%d (%.1fs)" % (n_ok, n_fail, n_skip, elapsed))

    f06, op2 = find_result_files(sim_path, cdir, sol_name, t0)
    status = {"solution": sol_name, "solved": n_ok, "failed": n_fail, "skipped": n_skip,
              "elapsed_s": elapsed, "f06": f06, "op2": op2, "ok": False, "message": ""}
    if n_fail or not n_ok:
        status["message"] = "SolveChainOfSolutions 실패"
    elif not f06:
        status["message"] = "f06 를 찾지 못함"
    else:
        ok, m = check_f06(f06)
        status["ok"] = ok and os.path.isfile(op2)
        status["message"] = m
        if ok and not os.path.isfile(op2):
            status["message"] = "op2 없음 - 솔루션 출력 설정 확인"
    write_json(os.path.join(cdir, "solve_status.json"), status)
    if not status["ok"]:
        raise RuntimeError("해석 실패: " + status["message"])

    # 6) (선택) 외부 파이썬으로 결과 추출
    py = args.get("extract_python")
    if py:
        script = args.get("extract_script")
        if not script or not os.path.isfile(script):
            raise RuntimeError("extract_script=<sc_run_case.py 경로> 가 필요합니다")
        cmd =[py, script, "--extract-only", "--op2", op2, "--f06", f06]
        log("extract: " + " ".join(cmd))
        rc = subprocess.call(cmd, cwd=cdir)
        if rc != 0:
            raise RuntimeError("결과 추출 실패(rc=%d)" % rc)


if __name__ == "__main__":
    rc = 0
    try:
        main()
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        rc = 1
    finally:
        try:
            NXOpen.Session.GetSession().Parts.CloseAll(NXOpen.BasePart.CloseModified.CloseModified, None)
        except Exception:
            pass
    # TODO(사내검증): run_journal 종료코드 전달 여부. 미전달이어도 results.json 부재로 failed 처리됨
    if rc:
        sys.exit(rc)
