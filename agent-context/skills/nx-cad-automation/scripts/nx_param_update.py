# -*- coding: utf-8 -*-
"""
NX CAD 파라메트릭 케이스 템플릿 (DDS 규약 준수)

흐름: params.json 읽기 -> 파트 열기 -> expression 변경 -> 모델 업데이트(실패 감지)
      -> 질량특성 측정 -> (선택) STEP/STL 내보내기, Save As, 이미지 -> results.json

실행 (tool 키 'nx'):
  run_journal.exe nx_param_update.py -args part=bracket.prt export_step=1 save_as=1 image=1

인자(-args, 모두 key=value, 전부 선택):
  part=<파일>        열 파트(.prt). 없으면 케이스 폴더에서 *.prt 하나를 자동 선택
  strict=1|0         params 키가 expression 에 없으면 실패(1, 기본) / 경고만(0)
  export_step=0|1    STEP 내보내기 -> <case>/export/<이름>.stp
  export_stl=0|1     STL 내보내기  -> <case>/export/<이름>.stl
  save_as=0|1        변경된 파트를 <case>/<이름>_case.prt 로 Save As
  image=0|1          images/iso.png 저장 시도(배치에서 실패해도 케이스는 성공 처리)
  case_dir=<폴더>    케이스 폴더(기본: 현재 작업 폴더)

params.json 의 키 = NX expression 이름(대소문자 정확히), 값 = 해당 expression 단위 기준 숫자.
NX 내장 Python 3 용. f-string 미사용.
"""
import json
import os
import sys
import traceback

import NXOpen
import NXOpen.UF


# ===========================================================================
# 공통 유틸
# ===========================================================================
def log(msg):
    print("[DDS] " + str(msg))
    sys.stdout.flush()


def parse_args():
    # '=' 없는 토큰은 무시 -> sys.argv[0] 에 저널 경로가 있든 없든 안전
    out = {}
    for tok in sys.argv[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def flag(args, key, default="0"):
    return str(args.get(key, default)).strip().lower() in ("1", "true", "yes", "y")


def load_json(path, default=None):
    if not os.path.isfile(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def find_part_file(cdir, explicit):
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(cdir, explicit)
        if not os.path.isfile(p):
            raise RuntimeError("파트 파일이 없습니다: " + p)
        return os.path.abspath(p)
    cands = [f for f in os.listdir(cdir)
             if f.lower().endswith(".prt") and not f.lower().endswith("_i.prt")
             and not f.lower().endswith("_case.prt")]
    if len(cands) != 1:
        raise RuntimeError("파트를 하나로 특정할 수 없습니다(후보: %s). -args part=... 로 지정하세요."
                           % ", ".join(cands))
    return os.path.join(cdir, cands[0])


# ===========================================================================
# NX 기능
# ===========================================================================
def is_plain_number(text):
    try:
        float(text)
        return True
    except Exception:
        return False


def apply_params(part, params, strict):
    """params 키와 이름이 같은 expression 을 수정. 적용된 이름 목록 반환."""
    applied, missing = [], []
    for name, value in params.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            log("숫자가 아닌 파라미터는 건너뜀: %s=%r" % (name, value))
            continue
        try:
            expr = part.Expressions.FindObject(name)   # 없으면 예외
        except Exception:
            expr = None
        if expr is None:
            missing.append(name)
            continue
        if not is_plain_number(expr.RightHandSide):
            log("경고: '%s' 는 수식(%s)으로 구동 중. 상수로 덮어씀." % (name, expr.RightHandSide))
        if expr.Type == "Integer":
            rhs = str(int(round(value)))
        else:
            rhs = repr(float(value))
        unit = expr.Units          # 무단위 expression 이면 None
        part.Expressions.EditWithUnits(expr, unit, rhs)
        # 대안: expr.RightHandSide = rhs
        applied.append(name)
        log("expression %s = %s" % (name, rhs))
    if missing:
        msg = "파트에 없는 expression: " + ", ".join(missing)
        if strict:
            raise RuntimeError(msg + "  (strict=0 으로 완화 가능)")
        log("경고: " + msg)
    return applied


def do_update(session):
    """모델 업데이트. 오류 수와 메시지 목록 반환."""
    mark = session.SetUndoMark(NXOpen.Session.MarkVisibility.Invisible, "DDS update")
    try:
        n_err = session.UpdateManager.DoUpdate(mark)
    except Exception as e:
        return 1, ["DoUpdate 예외: " + str(e)]
    msgs = []
    if n_err:
        # TODO(사내검증): ErrorList/GetErrorInfo 속성명 확인. 실패해도 n_err 로 판정은 된다.
        try:
            el = session.UpdateManager.ErrorList
            for i in range(el.Length):
                info = el.GetErrorInfo(i)
                msgs.append(str(getattr(info, "Description", info)))
        except Exception as e:
            msgs.append("오류 상세 조회 실패: " + str(e))
    return n_err, msgs


def solid_bodies(part):
    return [b for b in part.Bodies if b.IsSolidBody]


def mass_properties(part, bodies, accuracy=0.99):
    """질량특성. 단위를 명시하므로 파트 단위계(mm/inch)와 무관하게 아래 단위로 나온다."""
    uc = part.UnitCollection
    units = [uc.FindObject("SquareMilliMeter"),   # 면적
             uc.FindObject("CubicMilliMeter"),    # 부피
             uc.FindObject("Kilogram"),           # 질량
             uc.FindObject("MilliMeter"),         # 길이
             uc.FindObject("Newton")]             # 무게
    mb = part.MeasureManager.NewMassProperties(units, accuracy, bodies)
    c = mb.Centroid
    res = {
        "mass_kg": mb.Mass,
        "volume_mm3": mb.Volume,
        "area_mm2": mb.Area,
        "cog_x_mm": c.X,
        "cog_y_mm": c.Y,
        "cog_z_mm": c.Z,
    }
    # 재료 밀도 미지정 시 NX 기본밀도(7.83e-6 kg/mm^3)로 계산됨 -> SKILL.md 참고
    return res


def export_step(session, part, out_path):
    """STEP 내보내기. TODO(사내검증): ExportAs 옵션(Ap214/Ap242)과 SettingsFile 기본값은
    NX 버전마다 다르다. File > Export > STEP 을 Journal 로 기록해 맞출 것."""
    sc = session.DexManager.CreateStepCreator()
    try:
        sc.ExportAs = NXOpen.StepCreator.ExportAsOption.Ap214
        sc.ExportFrom = NXOpen.StepCreator.ExportFromOption.ExistingPart
        sc.InputFile = part.FullPath
        sc.OutputFile = out_path
        sc.FileSaveFlag = False
        sc.ExportSelectionBlock.SelectionScope = NXOpen.ObjectSelector.Scope.EntirePart
        sc.Commit()
    finally:
        sc.Destroy()
    if not os.path.isfile(out_path):
        raise RuntimeError("STEP 파일이 생성되지 않음: " + out_path)


def export_stl(session, bodies, out_path):
    """STL 내보내기. TODO(사내검증): CreateStlCreator 속성명/Add 시그니처는
    File > Export > STL 을 Journal 로 기록해 확인. 대안: UF_STD STL 함수."""
    stl = session.DexManager.CreateStlCreator()
    try:
        stl.AutoNormalGen = True
        stl.ChordalTol = 0.05
        stl.AdjacencyTol = 0.05
        stl.OutputFile = out_path
        for b in bodies:
            stl.ExportSelectionBlock.Add(b)
        stl.Commit()
    finally:
        stl.Destroy()


def try_save_image(part, png_path):
    """이미지 저장 '시도'. run_journal 배치에는 그래픽 창이 없어 실패할 수 있다.
    실패해도 False 만 반환하고 케이스는 계속 진행한다.
    TODO(사내검증): 배치에서 이미지가 꼭 필요하면 대화형 NX(ugraf) 에서 저널을 재생하거나
    File > Export > Image 를 Journal 로 기록해 아래 builder 코드를 맞출 것."""
    try:
        part.ModelingViews.WorkView.Orient(NXOpen.View.Canned.Isometric,
                                           NXOpen.View.ScaleAdjustment.Fit)
    except Exception as e:
        log("뷰 방향 설정 실패: " + str(e))
    # 1) UI ImageExportBuilder (대화형 세션에서 기록되는 방식)
    try:
        ui = NXOpen.UI.GetUI()
        b = ui.CreateImageExportBuilder()
        try:
            b.RegionMode = False
            b.FileFormat = NXOpen.Gateway.ImageExportBuilder.FileFormats.Png
            b.FileName = png_path
            b.BackgroundOption = NXOpen.Gateway.ImageExportBuilder.BackgroundOptions.Original
            b.EnhanceEdges = True
            b.Commit()
        finally:
            b.Destroy()
        if os.path.isfile(png_path):
            return True
    except Exception as e:
        log("ImageExportBuilder 실패(배치에서는 정상일 수 있음): " + str(e))
    # 2) UF_DISP_create_image
    try:
        ufs = NXOpen.UF.UFSession.GetUFSession()
        # TODO(사내검증): enum 경로/이름(NXOpen.UF.Disp.ImageFormat.PNG 등) 확인
        fmt = NXOpen.UF.Disp.ImageFormat.PNG
        bg = NXOpen.UF.Disp.BackgroundColor.WHITE
        ufs.Disp.CreateImage(png_path, fmt, bg)
        if os.path.isfile(png_path):
            return True
    except Exception as e:
        log("UF Disp.CreateImage 실패: " + str(e))
    return False


# ===========================================================================
# main
# ===========================================================================
def main():
    args = parse_args()
    cdir = os.path.abspath(args.get("case_dir") or os.getcwd())
    res_path = os.path.join(cdir, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)                       # 이전 결과 오인 방지

    params = load_json(os.path.join(cdir, "params.json"), {}) or {}
    part_file = find_part_file(cdir, args.get("part"))
    strict = flag(args, "strict", "1")
    log("case_dir=" + cdir)
    log("part=" + part_file)
    log("params=" + json.dumps(params))

    session = NXOpen.Session.GetSession()
    try:
        session.Parts.LoadOptions.ComponentLoadMethod = NXOpen.LoadOptions.LoadMethod.FromDirectory
    except Exception as e:
        # TODO(사내검증): 어셈블리면 컴포넌트가 원본 폴더에서 열리지 않는지 반드시 확인
        log("LoadOptions 설정 실패: " + str(e))

    part, load_status = session.Parts.OpenBaseDisplay(part_file)
    try:
        load_status.Dispose()
    except Exception:
        pass
    work = session.Parts.Work

    # 1) 파라미터 변경 + 업데이트
    applied = apply_params(work, params, strict)
    n_err, msgs = do_update(session)
    if n_err:
        for m in msgs:
            log("UPDATE ERROR: " + m)
        raise RuntimeError("모델 업데이트 실패(오류 %d건) - 형상이 성립하지 않는 파라미터 조합일 수 있음" % n_err)

    bodies = solid_bodies(work)
    if not bodies:
        raise RuntimeError("솔리드 바디가 없습니다(시트 바디만 있거나 업데이트 실패).")

    # 2) 결과
    results = mass_properties(work, bodies)
    results["n_solid_bodies"] = len(bodies)
    results["n_params_applied"] = len(applied)

    base = os.path.splitext(os.path.basename(part_file))[0]
    if flag(args, "export_step") or flag(args, "export_stl"):
        exp_dir = os.path.join(cdir, "export")
        if not os.path.isdir(exp_dir):
            os.makedirs(exp_dir)
        if flag(args, "export_step"):
            export_step(session, work, os.path.join(exp_dir, base + ".stp"))
            log("STEP 저장 완료")
        if flag(args, "export_stl"):
            export_stl(session, bodies, os.path.join(exp_dir, base + ".stl"))
            log("STL 저장 완료")

    if flag(args, "image"):
        img_dir = os.path.join(cdir, "images")
        if not os.path.isdir(img_dir):
            os.makedirs(img_dir)
        ok = try_save_image(work, os.path.join(img_dir, "iso.png"))
        log("이미지 저장 " + ("성공" if ok else "실패(결과에는 영향 없음)"))

    if flag(args, "save_as"):
        # 원본 이름 그대로 Save 하지 않는다(입력 파일 보존). 새 이름으로 저장.
        work.SaveAs(os.path.join(cdir, base + "_case.prt"))
        log("Save As 완료")

    write_json(res_path, results)
    log("results.json: " + json.dumps(results))


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
    # TODO(사내검증): run_journal 이 sys.exit 코드를 프로세스 종료코드로 전달하는지 확인.
    # 전달하지 않아도 results.json 미생성으로 플랫폼은 failed 로 판정한다.
    if rc:
        sys.exit(rc)
