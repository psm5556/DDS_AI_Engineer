# -*- coding: utf-8 -*-
"""
LightTools 케이스 템플릿 (COM API · LTAPI4, DDS 규약 준수)

흐름: params.json 읽기 -> LightTools 연결(실행 중이면 attach, 없으면 lt.exe 시작) -> 모델 열기
      -> lt_map.json 의 params 를 DbSet -> 광선 추적 -> outputs DbGet, 수신기 메시 -> results.json
      (+ images/<메시>.png, fields/<메시>.npz)

실행 (tool 키 'lighttools_com', 플랫폼 파이썬 + pywin32):
  python lt_case.py model=headlamp.lts inspect=1          # 먼저: 목록·키를 model_info.json, lt_map.json 초안
  python lt_case.py model=headlamp.lts map=lt_map.json image=1 fields=1

인자(모두 key=value, 전부 선택):
  model=<파일>      .lts. 없으면 case.json inputs -> 케이스 폴더의 *.lts 1개 (attach=1 이면 열지 않음)
  map=<파일>        설계변수·출력·메시 키 표(기본 lt_map.json)
  inspect=1         광원·수신기·부품 목록과 키를 model_info.json 에, lt_map.json 초안(없을 때만)을 쓰고 끝낸다
  probe_mesh=<키>   그 메시를 읽어 mesh_probe.json 에 크기·값 일부를 쓰고 끝낸다(CellValue 호출 형태 확인용)
  attach=1          열려 있는 LightTools 의 현재 모델에 작업(Live Link). 열기/닫기/저장 안 함
  sim=0|1           광선 추적 실행(기본 1)
  strict=1|0        lt_map 에 없는 params 키를 실패(1, 기본) / 경고(0)
  image=0|1         images/<메시이름>.png (기본 1)
  fields=0|1        fields/<메시이름>.npz 평면 메시(기본 1)
  save_as=0|1       케이스 폴더에 <이름>_case.lts 로 저장(원본 보존)
  open_cmd=...      모델 열기 명령 형식(기본 'Open "{path}"')      TODO(사내검증)
  sim_cmd=...       추적 명령(기본 BeginAllSimulations)           TODO(사내검증)
  save_cmd=...      저장 명령 형식(기본 'SaveAs "{path}"')         TODO(사내검증)
콘솔 출력은 ASCII. f-string 미사용(오래된 파이썬에서도 읽히게).
"""
import glob
import json
import math
import os
import subprocess
import sys
import time

PROGIDS = ("LightTools.LTAPI4", "LightTools.LTAPI5")  # TODO(사내검증): 사내 버전의 ProgID
CASE = os.getcwd()


def log(msg):
    sys.stdout.write(str(msg).encode("ascii", "replace").decode("ascii") + "\n")
    sys.stdout.flush()


def fail(msg):
    log("FAIL: " + msg)
    sys.exit(1)


def args():
    a = {}
    for x in sys.argv[1:]:
        if "=" in x:
            k, v = x.split("=", 1)
            a[k.strip()] = v.strip()
        else:
            a[x.strip()] = "1"
    return a


def flag(a, k, default="0"):
    return a.get(k, default) not in ("0", "", "false", "False", "no")


def read_json(name, default=None):
    p = os.path.join(CASE, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(name, data):
    with open(os.path.join(CASE, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------- COM ----------
def _dispatch():
    import win32com.client
    last = None
    for pid in PROGIDS:
        try:
            return win32com.client.Dispatch(pid)
        except Exception as e:  # noqa: BLE001 — ProgID가 없으면 다음 후보
            last = e
    raise RuntimeError("LightTools COM connect failed (%s)" % last)


def start_gui(exe):
    """스크립트가 끝나도 사용자가 이어서 쓰는 창이므로 AI CLI의 Job에서 떼어 띄운다.
    Job 안에 두면 PC Agent가 다시 시작하거나 죽을 때 저장하지 않은 모델과 함께 끝난다."""
    d = subprocess.DEVNULL
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # 배치 파일로 띄워도 콘솔은 보이지 않게
    try:
        subprocess.Popen([exe], stdin=d, stdout=d, stderr=d, creationflags=flags | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0))
    except PermissionError:  # 바깥 Job이 빠져나가기를 금지한 PC: 안 띄우는 것보다 낫다
        subprocess.Popen([exe], stdin=d, stdout=d, stderr=d, creationflags=flags)


def connect(attach):
    """실행 중인 LightTools에 붙는다. 없고 attach가 아니면 lt.exe를 띄우고 붙을 때까지 기다린다."""
    try:
        return _dispatch()
    except Exception:  # noqa: BLE001
        if attach:
            fail("attach=1 but no running LightTools. Open LightTools first.")
    exe = os.environ.get("DDS_LIGHTTOOLS") or os.environ.get("DDS_TOOL_LIGHTTOOLS")
    if not exe or not os.path.exists(exe):
        fail("LightTools executable not found on this PC. Ask the user to pick lt.exe in the web app (CAD/CAE tools > LightTools)")
    log("starting LightTools: " + exe)
    start_gui(exe)
    t0 = time.time()
    while time.time() - t0 < 180:
        time.sleep(5)
        try:
            return _dispatch()
        except Exception:  # noqa: BLE001
            continue
    fail("LightTools did not accept API connection in 180 s (check license dialog)")


def status_ok(ret):
    """API 반환값의 상태코드가 0(ltStatusSuccess)인지. 반환 형태가 버전마다 달라 너그럽게 본다. TODO(사내검증)"""
    if isinstance(ret, tuple):
        ret = ret[0] if ret else 0
    if ret is None:
        return True
    try:
        return int(ret) == 0
    except (TypeError, ValueError):
        return True


def cmd(lt, text):
    ret = lt.Cmd(text)
    if not status_ok(ret):
        raise RuntimeError("Cmd failed (%s): %s" % (ret, text))


def db_get(lt, key, prop, *index):
    """속성 값. pywin32의 [out] 튜플에서 상태코드(정수 0)가 아닌 값을 꺼낸다. TODO(사내검증): 튜플 순서"""
    ret = lt.DbGet(key, prop, None, *index) if index else lt.DbGet(key, prop)
    if isinstance(ret, tuple):
        vals = [r for r in ret if not (isinstance(r, int) and not isinstance(r, bool) and r == 0)]
        return vals[0] if vals else 0
    return ret


def db_set(lt, key, prop, value):
    ret = lt.DbSet(key, prop, value)
    if not status_ok(ret):
        raise RuntimeError("DbSet failed (%s): %s . %s = %s" % (ret, key, prop, value))


def db_list(lt, key, kind):
    """key 아래 kind(SOLID, SURFACE_RECEIVER, SURFACE_SOURCE ...) 항목들의 (이름, 키). TODO(사내검증): DbList/ListNext 형태"""
    out = []
    try:
        lst = lt.DbList(key, kind)
        if isinstance(lst, tuple):
            lst = lst[0]
        n = int(lt.ListSize(lst)) if hasattr(lt, "ListSize") else 0
        for _ in range(n):
            k = lt.ListNext(lst)
            if isinstance(k, tuple):
                k = k[0]
            try:
                name = db_get(lt, k, "Name")
            except Exception:  # noqa: BLE001
                name = ""
            out.append({"name": str(name), "key": str(k)})
    except Exception as e:  # noqa: BLE001
        out.append({"error": str(e)})
    return out


def read_mesh(lt, key):
    """수신기 메시 -> (xs, ys, grid[ny][nx]). TODO(사내검증): 속성 이름과 CellValue 인덱스 순서(열, 행)"""
    nx = int(float(db_get(lt, key, "X_Dimension")))
    ny = int(float(db_get(lt, key, "Y_Dimension")))
    x0, x1 = float(db_get(lt, key, "Min_X_Bound")), float(db_get(lt, key, "Max_X_Bound"))
    y0, y1 = float(db_get(lt, key, "Min_Y_Bound")), float(db_get(lt, key, "Max_Y_Bound"))
    grid = [[float(db_get(lt, key, "CellValue", i, j) or 0.0) for i in range(1, nx + 1)] for j in range(1, ny + 1)]
    xs = [x0 + (x1 - x0) * (i + 0.5) / nx for i in range(nx)]
    ys = [y0 + (y1 - y0) * (j + 0.5) / ny for j in range(ny)]
    return xs, ys, grid


# ---------- 결과 저장 ----------
def save_mesh_outputs(name, xs, ys, grid, results, image, fields):
    vals = [v for row in grid for v in row]
    vmax = max(vals) if vals else 0.0
    vmean = sum(vals) / len(vals) if vals else 0.0
    if vmax <= 0:
        raise RuntimeError("receiver mesh '%s' is all zero (no rays reached it)" % name)
    results[name + "_max"] = vmax
    results[name + "_mean"] = vmean
    results[name + "_uniformity"] = vmean / vmax
    if image:
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            os.makedirs(os.path.join(CASE, "images"), exist_ok=True)
            fig, ax = plt.subplots(figsize=(5.2, 4.2), dpi=120)
            im = ax.imshow(grid, origin="lower", extent=[xs[0], xs[-1], ys[0], ys[-1]], cmap="inferno", aspect="auto")
            fig.colorbar(im, ax=ax, label=name)
            ax.set_title("%s  max %.4g  uniformity %.3f" % (name, vmax, vmean / vmax))
            fig.tight_layout()
            fig.savefig(os.path.join(CASE, "images", name + ".png"))
            plt.close(fig)
        except Exception as e:  # noqa: BLE001 — 이미지가 없어도 케이스는 성공
            log("warn: image failed: %s" % e)
    if fields:
        import numpy as np
        nx, ny = len(xs), len(ys)
        pts = np.array([[x, y, 0.0] for y in ys for x in xs], dtype=np.float32)
        tri = []
        for j in range(ny - 1):
            for i in range(nx - 1):
                a = j * nx + i
                tri += [[a, a + 1, a + nx], [a + 1, a + nx + 1, a + nx]]
        os.makedirs(os.path.join(CASE, "fields"), exist_ok=True)
        np.savez(os.path.join(CASE, "fields", name + ".npz"), points=pts, values=np.array(vals, dtype=np.float32),
                 triangles=np.array(tri, dtype=np.int32))


def find_model(a):
    if a.get("model"):
        return os.path.abspath(a["model"])
    case = read_json("case.json", {}) or {}
    inputs = case.get("inputs") or []
    if isinstance(inputs, dict):
        inputs = list(inputs.values())
    for x in inputs:
        if str(x).lower().endswith(".lts"):
            return os.path.join(CASE, os.path.basename(str(x)))
    hits = glob.glob(os.path.join(CASE, "*.lts"))
    if len(hits) == 1:
        return hits[0]
    fail("model .lts not found (give model=<file>)")


def main():
    a = args()
    attach = flag(a, "attach")
    if os.path.exists(os.path.join(CASE, "results.json")):
        os.remove(os.path.join(CASE, "results.json"))
    lt = connect(attach)
    if not attach and not a.get("probe_mesh"):
        path = find_model(a)
        cmd(lt, a.get("open_cmd", 'Open "{path}"').replace("{path}", path))
        log("opened: " + path)

    if a.get("probe_mesh"):  # CellValue 호출 형태 확인용
        xs, ys, grid = read_mesh(lt, a["probe_mesh"])
        write_json("mesh_probe.json", {"nx": len(xs), "ny": len(ys), "x": [xs[0], xs[-1]], "y": [ys[0], ys[-1]],
                                       "corner_values": [grid[0][:5], grid[-1][-5:]]})
        log("mesh_probe.json written")
        return

    if flag(a, "inspect"):
        root = "LENS_MANAGER[1]"  # TODO(사내검증): 최상위 키
        info = {
            "solids": db_list(lt, root + ".COMPONENTS[Components]", "SOLID"),
            "sources": db_list(lt, root + ".ILLUM_MANAGER[Illumination_Manager].SOURCES[Source_List]", "SURFACE_SOURCE"),
            "receivers": db_list(lt, root + ".ILLUM_MANAGER[Illumination_Manager].RECEIVERS[Receiver_List]", "SURFACE_RECEIVER"),
        }
        write_json("model_info.json", info)
        if not os.path.exists(os.path.join(CASE, "lt_map.json")):
            write_json("lt_map.json", {
                "params": {"<변수이름>": {"key": "<solids[].key>", "prop": "X_Position", "scale": 1.0}},
                "outputs": {"<출력이름>": {"key": "<receivers[].key>.FORWARD_SIM_FUNCTION[Forward_Simulation]", "prop": "TotalPower"}},
                "meshes": {"illum": {"key": "<receivers[].key>.FORWARD_SIM_FUNCTION[Forward_Simulation].ILLUMINANCE_MESH[Illuminance_Mesh]"}},
                "source_power": {"key": "<sources[].key>", "prop": "Power"},
            })
        log("model_info.json written (solids %d, sources %d, receivers %d)"
            % (len(info["solids"]), len(info["sources"]), len(info["receivers"])))
        return

    m = read_json(a.get("map", "lt_map.json"))
    if not m:
        fail("lt_map.json not found - run inspect=1 first and fill the map")
    params = read_json("params.json", {}) or {}
    strict = flag(a, "strict", "1")
    for k, v in params.items():
        spec = (m.get("params") or {}).get(k)
        if not spec:
            if strict:
                fail("param '%s' not in lt_map.json params" % k)
            log("warn: skip param " + k)
            continue
        db_set(lt, spec["key"], spec["prop"], float(v) * float(spec.get("scale", 1.0)))
        log("set %s = %s" % (k, v))

    if flag(a, "sim", "1"):
        cmd(lt, a.get("sim_cmd", "BeginAllSimulations"))  # TODO(사내검증): 동기 여부
        log("simulation done")

    results = {}
    for name, spec in (m.get("outputs") or {}).items():
        val = float(db_get(lt, spec["key"], spec["prop"]))
        if math.isnan(val) or math.isinf(val):
            fail("output '%s' is not a number" % name)
        results[name] = val
    sp = m.get("source_power") or {}
    if sp.get("key") and results:
        power = float(db_get(lt, sp["key"], sp.get("prop", "Power")))
        if power > 0:
            results["efficiency"] = list(results.values())[0] / power
    for name, spec in (m.get("meshes") or {}).items():
        xs, ys, grid = read_mesh(lt, spec["key"])
        save_mesh_outputs(name, xs, ys, grid, results, flag(a, "image", "1"), flag(a, "fields", "1"))

    if flag(a, "save_as") and not attach:
        out = os.path.join(CASE, os.path.splitext(os.path.basename(find_model(a)))[0] + "_case.lts")
        cmd(lt, a.get("save_cmd", 'SaveAs "{path}"').replace("{path}", out))
    if not results:
        fail("no outputs - add outputs or meshes to lt_map.json")
    write_json("results.json", results)
    log("results.json: " + ", ".join(sorted(results)))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — 실패 시 results.json 을 쓰지 않는다
        fail(str(e))
