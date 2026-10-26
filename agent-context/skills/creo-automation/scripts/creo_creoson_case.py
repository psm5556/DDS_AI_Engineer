# -*- coding: utf-8 -*-
"""
Creo Parametric 파라메트릭 케이스 템플릿 (Creoson + creopyson, DDS 규약 준수)

흐름: params.json 읽기 -> CreosonServer 접속 -> (선택) Creo 시작 -> 모델 열기
      -> 파라미터/치수 변경 -> 재생성(실패 감지) -> 질량특성
      -> (선택) STEP/STL 내보내기, fields npz, 저장, 이미지 -> results.json

전제: CreosonServer(JSON over HTTP, 기본 localhost:9056)가 실행 중이고, 파이썬에 creopyson 설치.
      Creo 는 Creoson 이 붙을 수 있게 시작돼 있거나(start_creo=<nitro_proe_remote.bat>) 이미 실행 중.

실행 (tool 키 'creo_python'):
  python creo_creoson_case.py model=bracket.prt export_stl=1 fields=1

인자(모두 key=value, 전부 선택):
  model=<파일>        .prt/.asm (버전 접미어 .1 허용). 없으면 case.json inputs -> 케이스 폴더 1개
  inspect=1           파라미터·치수 목록만 model_info.json 에 쓰고 끝냄
  attach=1            Creo 에 이미 열린 활성 모델에 작업(Live Link). 열기/지우기/저장 안 함
  host=localhost port=9056
  start_creo=<bat>    Creo 시작 배치(nitro_proe_remote.bat). 주면 스크립트가 띄운 Creo 를 끝에서 종료
  creo_version=7      Creo 7 이상이면 지정(creo_set_creo_version)  TODO(사내검증)
  strict=1|0
  export_step=0|1 export_stl=0|1 fields=0|1 save=0|1 image=0|1

params 키 규칙: 'd:' 접두어면 치수(d:d12 -> 치수 d12), 그 외는 모델 파라미터 이름.
콘솔 출력은 ASCII. f-string 미사용.
"""
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))


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
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


def ensure_dir(p):
    if not os.path.isdir(p):
        os.makedirs(p)
    return p


def _is_model(name):
    low = name.lower()
    parts = low.split(".")
    # bracket.prt, bracket.prt.3
    if len(parts) >= 2 and parts[1] in ("prt", "asm") and (len(parts) == 2 or parts[-1].isdigit()):
        return True
    return False


def find_model(cdir, explicit):
    if not explicit:
        case = load_json(os.path.join(cdir, "case.json"), {}) or {}
        inp = case.get("inputs")
        if isinstance(inp, dict):
            explicit = inp.get("model") or inp.get("part")
        elif isinstance(inp, list):
            c = [os.path.basename(x) for x in inp if _is_model(os.path.basename(str(x)))]
            if len(c) == 1:
                explicit = c[0]
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(cdir, explicit)
        if not os.path.isfile(p):
            raise RuntimeError("model file not found: " + p)
        return os.path.abspath(p)
    cands = sorted(set(".".join(f.split(".")[:2]) for f in os.listdir(cdir) if _is_model(f)))
    if len(cands) != 1:
        raise RuntimeError("cannot pick one model (candidates: %s). pass model=..." % ", ".join(cands))
    return os.path.join(cdir, cands[0])


def model_name(path):
    """Creo 는 버전 접미어 없이 이름으로 연다: bracket.prt.3 -> bracket.prt"""
    return ".".join(os.path.basename(path).split(".")[:2])


# ---------------------------------------------------------------------------
def apply_params(c, mdl, params, strict):
    applied, missing = [], []
    names = set()
    try:
        names = set(p.get("name", "").upper() for p in c.parameter_list(file_=mdl))
    except Exception as e:
        log("parameter_list failed: %s" % e)
    for key, value in params.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            log("skip non-numeric param: %s" % key)
            continue
        if key.lower().startswith("d:"):
            dim = key[2:]
            try:
                c.dimension_set(dim, value, file_=mdl)
            except Exception as e:
                log("dimension_set failed for %s: %s" % (dim, e))
                missing.append(key)
                continue
        else:
            if names and key.upper() not in names:
                missing.append(key)        # parameter_set 은 없으면 새로 만들 수 있으므로 미리 막는다
                continue
            # TODO(사내검증): no_create 인자 이름과 type_ 생략 시 기존 타입 유지 여부
            c.parameter_set(key, value, file_=mdl, no_create=True)
        applied.append(key)
        log("param %s = %r" % (key, value))
    if missing:
        msg = "parameters/dimensions not found: " + ", ".join(missing)
        if strict:
            raise RuntimeError(msg + " (strict=0 to relax)")
        log("WARN: " + msg)
    return applied


def regenerate(c, mdl):
    try:
        c.file_regenerate(file_=mdl)
    except Exception as e:
        # TODO(사내검증): 재생성 실패 시 creopyson 이 예외를 던지는지, Creo 가 실패 피처를 남긴 채
        # 성공을 돌려주는지 확인. 후자면 regen 후 질량특성/feature 상태(feature_list status)로 판정한다.
        raise RuntimeError("regenerate failed - parameter combination may be infeasible: %s" % e)


def _num(d, *keys):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return float(d[k])
    return None


def measure(c, mdl):
    """file_massprops. 단위는 모델 단위계를 따른다(mm-kg-s 템플릿이면 mm/kg).
    TODO(사내검증): 반환 dict 키 이름(volume, mass, surface_area, gravity_center{x,y,z})과 단위."""
    mp = c.file_massprops(file_=mdl)
    vol = _num(mp, "volume")
    if not vol or vol <= 0:
        raise RuntimeError("volume is zero/unknown - no solid geometry: %r" % (mp,))
    res = {"volume": vol}
    for out_key, keys in (("mass", ("mass",)), ("area", ("surface_area", "area")),
                          ("density", ("density",))):
        v = _num(mp, *keys)
        if v is not None:
            res[out_key] = v
    g = mp.get("gravity_center") if isinstance(mp, dict) else None
    if isinstance(g, dict):
        for ax in ("x", "y", "z"):
            v = _num(g, ax)
            if v is not None:
                res["cog_" + ax] = v
    return res


def stl_to_fields(stl_path, cdir):
    sys.path.insert(0, HERE)
    import stl_to_fields as s2f
    pts, faces = s2f.convert(stl_path, os.path.join(cdir, "fields", "shape.npz"), "zeros", 1.0)
    log("fields npz: %d points, %d triangles" % (len(pts), len(faces)))
    return s2f.geometry_metrics(pts, faces)


def main(argv):
    args = parse_args(argv)
    cdir = os.path.abspath(args.get("case_dir") or os.getcwd())
    res_path = os.path.join(cdir, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)
    params = load_json(os.path.join(cdir, "params.json"), {}) or {}
    attach = flag(args, "attach")
    want_fields = flag(args, "fields")
    want_stl = flag(args, "export_stl") or want_fields

    import creopyson
    c = creopyson.Client(ip_adress=args.get("host", "localhost"), port=int(args.get("port", "9056")))
    c.connect()
    state = {"c": c, "started": False, "mdl": None, "opened": False}
    try:
        if args.get("start_creo") and not attach:
            c.start_creo(args["start_creo"])      # TODO(사내검증): retries/use_desktop 인자, 대기 시간
            state["started"] = True
        if args.get("creo_version"):
            c.creo_set_creo_version(int(args["creo_version"]))
        if not c.is_creo_running():
            raise RuntimeError("Creo is not running / not reachable through Creoson")

        if attach:
            mdl = c.file_get_active()["file"]       # TODO(사내검증): 반환 형식(dict 의 'file' 키)
            log("using active model: " + mdl)
        else:
            path = find_model(cdir, args.get("model"))
            mdl = model_name(path)
            c.creo_cd(cdir)                          # 케이스 폴더를 작업 폴더로
            c.file_open(mdl, dirname=cdir, display=True)
            state["opened"] = True
            log("opened " + mdl)
        state["mdl"] = mdl

        if flag(args, "inspect"):
            info = {"model": mdl, "parameters": c.parameter_list(file_=mdl)}
            try:
                info["dimensions"] = c.dimension_list(file_=mdl)
            except Exception as e:
                info["dimensions_error"] = str(e)
            write_json(os.path.join(cdir, "model_info.json"), info)
            log("model_info.json written")
            return 0

        applied = apply_params(c, mdl, params, flag(args, "strict", "1"))
        regenerate(c, mdl)
        results = measure(c, mdl)
        results["n_params_applied"] = len(applied)

        base = mdl.split(".")[0]
        exp = os.path.join(cdir, "export")
        if flag(args, "export_step"):
            c.interface_export_file("STEP", filename=base + ".stp", dirname=ensure_dir(exp), file_=mdl)
            log("STEP exported")
        if want_stl:
            # TODO(사내검증): Creoson interface.export_file 이 STL 타입을 지원하는지(버전별).
            # 미지원이면 mapkey(c.interface_mapkey) 나 trail 로 File > Save a Copy > STL 을 실행한다.
            stl = os.path.join(ensure_dir(exp), base + ".stl")
            c.interface_export_file("STL", filename=base + ".stl", dirname=exp, file_=mdl)
            if not os.path.isfile(stl):
                raise RuntimeError("STL not created: " + stl)
            log("STL exported")
            if want_fields:
                results.update(stl_to_fields(stl, cdir))
        if flag(args, "image"):
            try:
                img_dir = ensure_dir(os.path.join(cdir, "images"))
                c.interface_export_image("JPEG", filename="iso.jpg", height=6.0, width=8.0, dpi=100)
                # TODO(사내검증): 이미지가 Creo 작업 폴더(creo_cd)로 저장되는지. 그래픽 없는 세션에서는 실패한다.
                src = os.path.join(cdir, "iso.jpg")
                from PIL import Image
                Image.open(src).save(os.path.join(img_dir, "iso.png"))
                os.remove(src)
                log("image saved")
            except Exception as e:
                log("image failed (ignored): %s" % e)
        if flag(args, "save") and not attach:
            c.file_save(file_=mdl)                   # 케이스 폴더에 새 버전(.prt.N)으로 저장
            log("saved")

        write_json(res_path, results)
        log("results.json: " + json.dumps(results))
        return 0
    finally:
        cleanup(state)


def cleanup(state):
    c = state["c"]
    if state["opened"] and state["mdl"]:
        try:
            c.file_close_window(file_=state["mdl"])
            c.file_erase(file_=state["mdl"])      # 세션 메모리에서 제거(다음 케이스가 옛 모델을 쓰지 않게)
        except Exception:
            pass
    if state["started"]:
        try:
            c.kill_creo()                          # TODO(사내검증): 스크립트가 띄운 Creo 만 종료되는지
        except Exception as e:
            log("kill_creo failed: %s" % e)
    try:
        c.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        rc = main(sys.argv[1:])
    except Exception:
        log("FAILED\n" + traceback.format_exc())
        rc = 1
    sys.exit(rc)
