# -*- coding: utf-8 -*-
"""
CATIA V5 파라메트릭 케이스 템플릿 (COM 자동화, DDS 규약 준수)

흐름: params.json 읽기 -> CATIA 연결(실행 중이면 attach, 없으면 새로 시작) -> CATPart 열기
      -> Parameters 변경 -> Part.Update(실패 감지) -> 질량특성 측정
      -> (선택) STEP/STL 내보내기, fields npz, Save As, 이미지 -> results.json

실행 (tool 키 'catia_com', 플랫폼 venv 파이썬 + pywin32):
  python catia_param_update.py part=bracket.CATPart export_stl=1 fields=1 image=1

인자(모두 key=value, 전부 선택):
  part=<파일>        열 CATPart. 없으면 case.json inputs -> 케이스 폴더의 *.CATPart 1개 순으로 찾는다
  inspect=1          파라미터 목록만 model_info.json 에 쓰고 끝낸다(results.json 미작성, 종료코드 0)
  attach=1           사용자가 열어 둔 CATIA 의 '활성 문서'에 작업한다(Live Link). 파일을 열지 않고 닫지도 않는다
  strict=1|0         params 키가 파트에 없으면 실패(1, 기본) / 경고만(0)
  visible=0|1        새로 시작한 CATIA 창 표시 여부(기본 0). image=1 이면 자동으로 1
  export_step=0|1    export/<이름>.stp
  export_stl=0|1     export/<이름>.stl
  fields=0|1         STL 을 fields/shape.npz 로 변환(stl_to_fields.py, export_stl 자동 활성)
  save_as=0|1        <case>/<이름>_case.CATPart 로 Save As (원본 보존)
  image=0|1          images/iso.png (실패해도 케이스는 성공)

params.json 의 키 = CATIA 파라미터 이름(트리에 보이는 이름 또는 전체 경로 'PartBody\\Pad.1\\FirstLimit\\Length').
값: 길이 mm, 각도 deg (CATIA Dimension.Value 규약), 그 외 실수/정수는 그대로.
콘솔 출력은 ASCII. f-string 미사용.
"""
import json
import os
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
CATIA_EXE = "CNEXT.exe"


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
    """argv -> case.json inputs(dict 의 part/model 키 또는 list) -> 케이스 폴더 glob."""
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
             if f.lower().endswith(exts) and "_case." not in f.lower()]
    if len(cands) != 1:
        raise RuntimeError("cannot pick one model (candidates: %s). pass part=..." % ", ".join(cands))
    return os.path.join(cdir, cands[0])


def list_pids(image):
    """tasklist 로 실행 중인 PID 집합. 좀비 정리용."""
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


# ===========================================================================
# CATIA 연결
# ===========================================================================
def connect_catia(attach, visible):
    """(catia, started_by_me, new_pids). 실행 중 세션이 있으면 그것을 쓴다."""
    import win32com.client
    try:
        app = win32com.client.GetActiveObject("CATIA.Application")
        log("attached to running CATIA")
        return app, False, set()
    except Exception:
        if attach:
            raise RuntimeError("attach=1 but no running CATIA session (GetActiveObject failed)")
    before = list_pids(CATIA_EXE)
    app = win32com.client.Dispatch("CATIA.Application")     # 없으면 CNEXT 를 새로 띄운다
    new_pids = list_pids(CATIA_EXE) - before
    app.Visible = bool(visible)
    log("started CATIA (new pids: %s)" % sorted(new_pids))
    return app, True, new_pids


# ===========================================================================
# 파라미터
# ===========================================================================
def list_parameters(part):
    out = []
    params = part.Parameters
    for i in range(1, params.Count + 1):            # COM 컬렉션은 1-based
        p = params.Item(i)
        row = {"name": p.Name}
        for attr in ("Value", "ReadOnly", "Hidden", "UserAccessMode"):
            try:
                v = getattr(p, attr)
                row[attr.lower()] = v if isinstance(v, (int, float, str, bool)) else str(v)
            except Exception:
                pass
        try:
            row["value_str"] = p.ValueAsString()
        except Exception:
            pass
        out.append(row)
    return out


def apply_params(part, params, strict):
    applied, missing = [], []
    coll = part.Parameters
    for name, value in params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            log("skip non-numeric param: %s" % name)
            continue
        try:
            p = coll.Item(name)                        # 없으면 COM 예외
        except Exception:
            missing.append(name)
            continue
        try:
            # TODO(사내검증): OptionalRelation 은 수식/룰로 구동되는 파라미터면 Relation 을 돌려준다.
            if p.OptionalRelation is not None:
                log("WARN: '%s' is driven by a relation - value may be overwritten on update" % name)
        except Exception:
            pass
        try:
            if p.ReadOnly:
                raise RuntimeError("parameter is read-only: " + name)
        except AttributeError:
            pass
        p.Value = value      # Length=mm, Angle=deg. 대안: p.ValuateFromString("12.5mm")
        applied.append(name)
        log("param %s = %r" % (name, value))
    if missing:
        msg = "parameters not found in part: " + ", ".join(missing)
        if strict:
            raise RuntimeError(msg + " (strict=0 to relax)")
        log("WARN: " + msg)
    return applied


def update_part(part):
    """Part.Update(). 업데이트 오류는 COM 예외로 올라온다."""
    try:
        part.Update()
    except Exception as e:
        raise RuntimeError("Part.Update failed - parameter combination may be infeasible: %s" % e)
    # TODO(사내검증): IsUpToDate 인자 형식(Part 자신을 넘겨도 되는지) 확인. 실패해도 판정은 위 예외로 된다.
    try:
        if not part.IsUpToDate(part):
            raise RuntimeError("part is not up to date after Update")
    except RuntimeError:
        raise
    except Exception:
        pass
    try:
        if part.MainBody.Shapes.Count == 0:
            raise RuntimeError("MainBody has no shapes")
    except RuntimeError:
        raise
    except Exception:
        pass


# ===========================================================================
# 측정
# ===========================================================================
_VB_COG = """
Public Function dds_cog(analyze)
    Dim c(2)
    analyze.GetGravityCenter c
    dds_cog = c
End Function
"""

_VB_INERTIA = """
Public Function dds_inertia(analyze)
    Dim m(8)
    analyze.GetInertia m
    dds_inertia = m
End Function
"""


def _evaluate_vb(catia, code, func, obj):
    """ByRef 배열을 받는 메서드는 pywin32 late binding 으로 직접 호출이 어렵다.
    CATIA SystemService.Evaluate 로 VBScript 함수를 실행해 배열을 돌려받는다(pycatia 와 같은 방식).
    TODO(사내검증): 언어 enum 0 = CATVBScriptLanguage 인지, 반환 배열이 tuple 로 오는지 확인."""
    return catia.SystemService.Evaluate(code, 0, func, [obj])


def measure(catia, doc):
    """Product.Analyze 기반 질량특성.
    TODO(사내검증): Analyze 단위 - Mass[kg], Volume[m^3], WetArea[m^2], GravityCenter[mm],
    Inertia[kg*m^2] 로 가정한다. 단순 블록 모델로 한 번 대조하고, stl_volume(fields=1)과도 비교한다."""
    analyze = doc.Product.Analyze
    res = {
        "mass": float(analyze.Mass),
        "volume_mm3": float(analyze.Volume) * 1.0e9,
        "area_mm2": float(analyze.WetArea) * 1.0e6,
    }
    try:
        c = _evaluate_vb(catia, _VB_COG, "dds_cog", analyze)
        res["cog_x"], res["cog_y"], res["cog_z"] = float(c[0]), float(c[1]), float(c[2])
    except Exception as e:
        log("WARN: gravity center failed: %s" % e)
    try:
        m = _evaluate_vb(catia, _VB_INERTIA, "dds_inertia", analyze)
        res["ixx"], res["iyy"], res["izz"] = float(m[0]), float(m[4]), float(m[8])
    except Exception as e:
        log("WARN: inertia failed: %s" % e)
    # 대안(부피/면적만): spa = doc.GetWorkbench("SPAWorkbench");
    #   ref = part.CreateReferenceFromObject(part.MainBody); meas = spa.GetMeasurable(ref); meas.Volume, meas.Area
    return res


# ===========================================================================
# 내보내기 / 이미지
# ===========================================================================
def export(doc, path, fmt):
    if os.path.exists(path):
        os.remove(path)
    doc.ExportData(path, fmt)          # fmt: "stp", "stl", "igs", "model" ...
    if not os.path.isfile(path):
        raise RuntimeError("export file not created: " + path)


def save_image(catia, png_path):
    """ActiveViewer.CaptureToFile 은 PNG 를 지원하지 않으므로 BMP 로 받아 Pillow 로 변환한다.
    창이 보이는 세션에서만 신뢰할 수 있다."""
    try:
        viewer = catia.ActiveWindow.ActiveViewer
        try:
            vp = viewer.Viewpoint3D
            # TODO(사내검증): 방향 벡터 설정이 버전별로 동작하는지 확인. 실패해도 Reframe 만 한다.
            vp.PutSightDirection([-1.0, -1.0, -1.0])
            vp.PutUpDirection([-0.408248, -0.408248, 0.816497])
        except Exception as e:
            log("viewpoint set failed: %s" % e)
        viewer.Reframe()
        viewer.Update()
        bmp = os.path.splitext(png_path)[0] + ".bmp"
        viewer.CaptureToFile(4, bmp)      # 4 = catCaptureFormatBMP (5 = JPEG)
        from PIL import Image
        Image.open(bmp).save(png_path)
        os.remove(bmp)
        return os.path.isfile(png_path)
    except Exception as e:
        log("image failed (ignored): %s" % e)
        return False


def stl_to_fields(stl_path, cdir):
    sys.path.insert(0, HERE)
    import stl_to_fields as s2f
    out = os.path.join(cdir, "fields", "shape.npz")
    pts, faces = s2f.convert(stl_path, out, "zeros", 1.0)
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
    state = {"catia": None, "started": False, "pids": set(), "doc": None, "opened": False}

    try:
        catia, started, pids = connect_catia(attach, flag(args, "visible") or want_image)
        state.update(catia=catia, started=started, pids=pids)
        state["alerts"] = catia.DisplayFileAlerts
        catia.DisplayFileAlerts = False            # 대화상자로 멈추지 않게(cleanup 에서 복원)
        if attach:
            doc = catia.ActiveDocument
            log("using active document: " + doc.FullName)
        else:
            model = find_model(cdir, args.get("part"), (".catpart",))
            log("open " + model)
            doc = catia.Documents.Open(model)
            state["opened"] = True
        state["doc"] = doc
        part = doc.Part

        if flag(args, "inspect"):
            info = {"document": doc.FullName, "parameters": list_parameters(part)}
            write_json(os.path.join(cdir, "model_info.json"), info)
            log("model_info.json written (%d parameters)" % len(info["parameters"]))
            return 0

        applied = apply_params(part, params, flag(args, "strict", "1"))
        update_part(part)
        results = measure(catia, doc)
        results["n_params_applied"] = len(applied)

        base = os.path.splitext(os.path.basename(doc.FullName))[0]
        if flag(args, "export_step") or want_stl:
            exp = ensure_dir(os.path.join(cdir, "export"))
            if flag(args, "export_step"):
                export(doc, os.path.join(exp, base + ".stp"), "stp")
                log("STEP exported")
            if want_stl:
                stl = os.path.join(exp, base + ".stl")
                export(doc, stl, "stl")
                log("STL exported")
                if want_fields:
                    results.update(stl_to_fields(stl, cdir))
        if want_image:
            ok = save_image(catia, os.path.join(ensure_dir(os.path.join(cdir, "images")), "iso.png"))
            log("image " + ("saved" if ok else "skipped"))
        if flag(args, "save_as") and not attach:
            doc.SaveAs(os.path.join(cdir, base + "_case.CATPart"))
            log("saved as " + base + "_case.CATPart")

        write_json(res_path, results)
        log("results.json: " + json.dumps(results))
        return 0
    finally:
        cleanup(state)


def cleanup(state):
    """스크립트가 연 문서만 닫고, 스크립트가 시작한 CATIA 만 종료한다."""
    if state["opened"] and state["doc"] is not None:
        try:
            state["doc"].Close()
        except Exception:
            pass
    if not state["started"] and state["catia"] is not None and "alerts" in state:
        try:
            state["catia"].DisplayFileAlerts = state["alerts"]   # 사용자 세션 설정 복원
        except Exception:
            pass
    if state["started"] and state["catia"] is not None:
        try:
            state["catia"].Quit()
        except Exception as e:
            log("Quit failed: %s - killing started pids" % e)
            for pid in state["pids"]:
                subprocess.call(["taskkill", "/PID", str(pid), "/F"])


if __name__ == "__main__":
    try:
        rc = main(sys.argv[1:])
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        rc = 1
    sys.exit(rc)
