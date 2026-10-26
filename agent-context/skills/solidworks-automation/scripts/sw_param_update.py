# -*- coding: utf-8 -*-
"""
SOLIDWORKS 파라메트릭 케이스 템플릿 (COM 자동화, DDS 규약 준수)

흐름: params.json 읽기 -> SolidWorks 연결(실행 중이면 attach, 없으면 새로 시작) -> OpenDoc6
      -> 치수(D1@Sketch1)/전역변수(EquationMgr) 변경 -> ForceRebuild3(실패 감지) -> 질량특성
      -> (선택) STEP/STL/Parasolid, fields npz, Save As, 이미지 -> results.json

실행 (tool 키 'solidworks_com', 플랫폼 venv 파이썬 + pywin32):
  python sw_param_update.py part=bracket.SLDPRT export_stl=1 fields=1 image=1

인자(모두 key=value, 전부 선택):
  part=<파일>        .SLDPRT/.SLDASM. 없으면 case.json inputs -> 케이스 폴더의 파일 1개
  inspect=1          치수·전역변수 목록만 model_info.json 에 쓰고 끝낸다(results.json 미작성)
  attach=1           열려 있는 SolidWorks 의 활성 문서에 작업(Live Link). 열기/닫기/Save As 안 함
  strict=1|0         없는 params 키를 실패(1, 기본) / 경고(0)
  length_unit=mm|m   치수 값 단위(기본 mm). SystemValue 는 m 이므로 mm 이면 /1000
  angle_params=a,b   도(deg)로 준 각도 치수 이름 목록(rad 로 변환)
  visible=0|1        새로 시작한 SolidWorks 표시(기본 0, image=1 이면 1)
  export_step/export_stl/export_x_t=0|1   export/<이름>.step|.stl|.x_t
  fields=0|1         STL -> fields/shape.npz (export_stl 자동 활성)
  stl_scale=<수>     STL 좌표 배율(STL 이 m 단위로 나오면 1000)
  save_as=0|1        <case>/<이름>_case.<확장자> (원본 보존)
  image=0|1          images/iso.png (실패해도 케이스는 성공)

params 키 규칙: '@' 가 있으면 치수 전체 이름(D1@Sketch1, D1@Boss-Extrude1),
                없으면 Equations 의 전역변수 이름("thk").
콘솔 출력은 ASCII. f-string 미사용.
"""
import json
import os
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
SW_EXE = "SLDWORKS.exe"
DOC_TYPES = {".sldprt": 1, ".sldasm": 2}      # swDocPART, swDocASSEMBLY


# ===========================================================================
# 공통 유틸
# ===========================================================================
def log(msg):
    print("[DDS] " + str(msg))
    sys.stdout.flush()


def parse_args(argv):
    out = {}
    for tok in argv:
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


def ensure_dir(p):
    if not os.path.isdir(p):
        os.makedirs(p)
    return p


def find_model(cdir, explicit, exts):
    if not explicit:
        case = load_json(os.path.join(cdir, "case.json"), {}) or {}
        inp = case.get("inputs")
        if isinstance(inp, dict):
            explicit = inp.get("part") or inp.get("model")
        elif isinstance(inp, list):
            c = [x for x in inp if str(x).lower().endswith(exts)]
            if len(c) == 1:
                explicit = os.path.basename(c[0])
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(cdir, explicit)
        if not os.path.isfile(p):
            raise RuntimeError("model file not found: " + p)
        return os.path.abspath(p)
    cands = [f for f in os.listdir(cdir)
             if f.lower().endswith(exts) and "_case." not in f.lower() and not f.startswith("~$")]
    if len(cands) != 1:
        raise RuntimeError("cannot pick one model (candidates: %s). pass part=..." % ", ".join(cands))
    return os.path.join(cdir, cands[0])


def list_pids(image):
    try:
        out = subprocess.check_output(["tasklist", "/FI", "IMAGENAME eq " + image, "/FO", "CSV", "/NH"],
                                      universal_newlines=True, errors="replace")
    except Exception:
        return set()
    pids = set()
    for line in out.splitlines():
        parts = [x.strip('"') for x in line.split('","')]
        if len(parts) > 1 and parts[1].isdigit():
            pids.add(int(parts[1]))
    return pids


def byref_long():
    import pythoncom
    import win32com.client
    return win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_I4, 0)


def null_dispatch():
    import pythoncom
    import win32com.client
    return win32com.client.VARIANT(pythoncom.VT_DISPATCH, None)


def com_put(obj, name, *args):
    """인덱스 있는 속성 쓰기(예: EquationMgr.Equation(i) = text).
    pywin32 late binding 에서 obj.Equation(i) = x 문법이 없으므로 PROPERTYPUT 으로 직접 호출한다.
    TODO(사내검증): 인자 순서(index, value)와 동작을 실제 SolidWorks 에서 확인."""
    import pythoncom
    ole = obj._oleobj_
    dispid = ole.GetIDsOfNames(name)
    ole.Invoke(dispid, 0, pythoncom.DISPATCH_PROPERTYPUT, False, *args)


# ===========================================================================
# 연결 / 열기
# ===========================================================================
def connect_sw(attach, visible):
    import win32com.client
    try:
        app = win32com.client.GetActiveObject("SldWorks.Application")
        log("attached to running SolidWorks")
        return app, False, set()
    except Exception:
        if attach:
            raise RuntimeError("attach=1 but no running SolidWorks (GetActiveObject failed)")
    before = list_pids(SW_EXE)
    app = win32com.client.Dispatch("SldWorks.Application")
    new_pids = list_pids(SW_EXE) - before
    app.Visible = bool(visible)
    log("started SolidWorks (new pids: %s)" % sorted(new_pids))
    return app, True, new_pids


def open_doc(sw, path):
    ext = os.path.splitext(path)[1].lower()
    err, warn = byref_long(), byref_long()
    # options 1 = swOpenDocOptions_Silent
    model = sw.OpenDoc6(path, DOC_TYPES[ext], 1, "", err, warn)
    if model is None:
        raise RuntimeError("OpenDoc6 failed: errors=%s warnings=%s" % (err.value, warn.value))
    if warn.value:
        log("OpenDoc6 warnings=%s (swFileLoadWarning_e)" % warn.value)
    return model


# ===========================================================================
# 치수 / 전역변수
# ===========================================================================
def list_dimensions(model):
    out = []
    feat = model.FirstFeature()
    while feat is not None:
        try:
            dd = feat.GetFirstDisplayDimension()
            while dd is not None:
                d = dd.GetDimension2(0)
                out.append({"name": d.FullName, "system_value": d.SystemValue})
                dd = feat.GetNextDisplayDimension(dd)
        except Exception:
            pass
        feat = feat.GetNextFeature()
    return out


def list_equations(model):
    eqm = model.GetEquationMgr()
    out = []
    for i in range(eqm.GetCount()):
        row = {"index": i, "equation": eqm.Equation(i)}
        try:
            row["global_variable"] = bool(eqm.GlobalVariable(i))
            row["value"] = eqm.Value(i)
        except Exception:
            pass
        out.append(row)
    return out


def set_global_variable(model, name, value):
    eqm = model.GetEquationMgr()
    key = '"%s"' % name
    for i in range(eqm.GetCount()):
        text = eqm.Equation(i)
        if text.replace(" ", "").startswith(key + "="):
            # 단위 없는 값은 문서 단위로 해석된다. TODO(사내검증): 문서 단위계 확인
            com_put(eqm, "Equation", i, '%s = %r' % (key, float(value)))
            return True
    return False


def apply_params(model, params, strict, length_scale, angle_names):
    applied, missing = [], []
    used_eq = False
    for name, value in params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            log("skip non-numeric param: %s" % name)
            continue
        if "@" in name:
            dim = model.Parameter(name)          # 없으면 None
            if dim is None:
                missing.append(name)
                continue
            if name in angle_names:
                sv = float(value) * 3.141592653589793 / 180.0
            else:
                sv = float(value) * length_scale
            dim.SystemValue = sv                  # SI(m, rad). 활성 구성에만 적용될 수 있음
            # 대안: dim.SetSystemValue3(sv, 2, None)  # 2 = swSetValue_InAllConfigurations  TODO(사내검증)
        else:
            if not set_global_variable(model, name, value):
                missing.append(name)
                continue
            used_eq = True
        applied.append(name)
        log("param %s = %r" % (name, value))
    if used_eq:
        model.GetEquationMgr().EvaluateAll()
    if missing:
        msg = "parameters not found: " + ", ".join(missing)
        if strict:
            raise RuntimeError(msg + " (strict=0 to relax)")
        log("WARN: " + msg)
    return applied


def rebuild(model):
    ok = model.ForceRebuild3(False)               # False = 모든 구성 아님(활성만)
    if not ok:
        raise RuntimeError("ForceRebuild3 returned False - geometry may be infeasible")
    # TODO(사내검증): GetWhatsWrongCount 가 사내 버전에 있는지. 없으면 이 검사만 건너뛴다.
    try:
        n = model.Extension.GetWhatsWrongCount()
        if n:
            raise RuntimeError("rebuild errors in What's Wrong list: %d" % n)
    except RuntimeError:
        raise
    except Exception as e:
        log("WhatsWrong check skipped: %s" % e)


# ===========================================================================
# 측정 / 내보내기 / 이미지
# ===========================================================================
def measure(model):
    """IMassProperty: SI 단위(kg, m^3, m^2, m). 아래에서 mm 로 바꾼다."""
    mp = model.Extension.CreateMassProperty()
    if mp is None:
        raise RuntimeError("CreateMassProperty returned None (no solid body?)")
    vol = float(mp.Volume)
    if vol <= 0:
        raise RuntimeError("volume is zero - no solid body")
    c = mp.CenterOfMass
    res = {"mass": float(mp.Mass), "volume_mm3": vol * 1e9, "area_mm2": float(mp.SurfaceArea) * 1e6,
           "cog_x": c[0] * 1000.0, "cog_y": c[1] * 1000.0, "cog_z": c[2] * 1000.0}
    try:
        # TODO(사내검증): 0 = swMassPropertyMomentAboutCenterOfMass, 반환 9개 [Lxx..Lzz] kg*m^2
        m = mp.GetMomentOfInertia(0)
        res["ixx"], res["iyy"], res["izz"] = float(m[0]), float(m[4]), float(m[8])
    except Exception as e:
        log("WARN: inertia failed: %s" % e)
    return res


def save_as(model, path, copy=True):
    """Extension.SaveAs 는 확장자로 포맷을 고른다(.step/.stl/.x_t/.png/.SLDPRT).
    options: 1 = Silent, 2 = Copy(활성 문서 이름 유지)."""
    if os.path.exists(path):
        os.remove(path)
    err, warn = byref_long(), byref_long()
    opts = 1 | (2 if copy else 0)
    ok = model.Extension.SaveAs(path, 0, opts, null_dispatch(), err, warn)
    if not ok or not os.path.isfile(path):
        # 대안(구형 API): model.SaveAs3(path, 0, opts)
        raise RuntimeError("SaveAs failed: %s errors=%s warnings=%s" % (path, err.value, warn.value))


def save_image(model, png_path):
    try:
        model.ShowNamedView2("*Isometric", 7)    # 7 = swIsometricView
        model.ViewZoomtofit2()
        try:
            save_as(model, png_path)                 # TODO(사내검증): 버전별 PNG Save As 지원
            return True
        except Exception as e:
            log("png save failed, try jpg: %s" % e)
        jpg = os.path.splitext(png_path)[0] + ".jpg"
        save_as(model, jpg)
        from PIL import Image
        Image.open(jpg).save(png_path)
        os.remove(jpg)
        return os.path.isfile(png_path)
    except Exception as e:
        log("image failed (ignored): %s" % e)
        return False


def stl_to_fields(stl_path, cdir, scale):
    sys.path.insert(0, HERE)
    import stl_to_fields as s2f
    pts, faces = s2f.convert(stl_path, os.path.join(cdir, "fields", "shape.npz"), "zeros", scale)
    log("fields npz: %d points, %d triangles" % (len(pts), len(faces)))
    return s2f.geometry_metrics(pts, faces)


# ===========================================================================
# main
# ===========================================================================
def main(argv):
    args = parse_args(argv)
    cdir = os.path.abspath(args.get("case_dir") or os.getcwd())
    res_path = os.path.join(cdir, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)
    params = load_json(os.path.join(cdir, "params.json"), {}) or {}
    attach = flag(args, "attach")
    want_image = flag(args, "image")
    want_fields = flag(args, "fields")
    want_stl = flag(args, "export_stl") or want_fields
    length_scale = 0.001 if args.get("length_unit", "mm").lower() == "mm" else 1.0
    angle_names = [x.strip() for x in args.get("angle_params", "").split(",") if x.strip()]
    state = {"sw": None, "started": False, "pids": set(), "model": None, "opened": False}

    try:
        sw, started, pids = connect_sw(attach, flag(args, "visible") or want_image)
        state.update(sw=sw, started=started, pids=pids)
        if attach:
            model = sw.ActiveDoc
            if model is None:
                raise RuntimeError("no active document in SolidWorks")
            log("using active document: " + model.GetPathName())
        else:
            path = find_model(cdir, args.get("part"), (".sldprt", ".sldasm"))
            log("open " + path)
            model = open_doc(sw, path)
            state["opened"] = True
        state["model"] = model

        if flag(args, "inspect"):
            info = {"document": model.GetPathName(),
                    "dimensions": list_dimensions(model),
                    "equations": list_equations(model)}
            write_json(os.path.join(cdir, "model_info.json"), info)
            log("model_info.json written (%d dims, %d equations)"
                % (len(info["dimensions"]), len(info["equations"])))
            return 0

        applied = apply_params(model, params, flag(args, "strict", "1"), length_scale, angle_names)
        rebuild(model)
        results = measure(model)
        results["n_params_applied"] = len(applied)

        full = model.GetPathName()
        base, ext = os.path.splitext(os.path.basename(full))
        exp = os.path.join(cdir, "export")
        for key, suffix in (("export_step", ".step"), ("export_x_t", ".x_t")):
            if flag(args, key):
                save_as(model, os.path.join(ensure_dir(exp), base + suffix))
                log(suffix + " exported")
        if want_stl:
            stl = os.path.join(ensure_dir(exp), base + ".stl")
            save_as(model, stl)        # TODO(사내검증): STL 단위·해상도는 시스템 옵션(Export > STL)을 따른다
            log("STL exported")
            if want_fields:
                results.update(stl_to_fields(stl, cdir, float(args.get("stl_scale", "1"))))
        if want_image:
            ok = save_image(model, os.path.join(ensure_dir(os.path.join(cdir, "images")), "iso.png"))
            log("image " + ("saved" if ok else "skipped"))
        if flag(args, "save_as") and not attach:
            save_as(model, os.path.join(cdir, base + "_case" + ext))
            log("saved as " + base + "_case" + ext)

        write_json(res_path, results)
        log("results.json: " + json.dumps(results))
        return 0
    finally:
        cleanup(state)


def cleanup(state):
    """스크립트가 연 문서만 닫고, 스크립트가 시작한 SolidWorks 만 종료한다."""
    sw = state["sw"]
    if sw is not None and state["opened"] and state["model"] is not None:
        try:
            sw.CloseDoc(state["model"].GetTitle())
        except Exception:
            pass
    if sw is not None and state["started"]:
        try:
            sw.ExitApp()
        except Exception as e:
            log("ExitApp failed: %s - killing started pids" % e)
            for pid in state["pids"]:
                subprocess.call(["taskkill", "/PID", str(pid), "/F"])


if __name__ == "__main__":
    try:
        rc = main(sys.argv[1:])
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        rc = 1
    sys.exit(rc)
