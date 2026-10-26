"""Moldflow 케이스 실행 템플릿  (tool: moldflow_synergy = 플랫폼 python 3.12 + pywin32)

흐름:  params.json -> (선택) Synergy COM 으로 공정조건/물성 필드 변경 -> StudyDoc.Save
       -> runstudy.exe <study>.sdy (배치 해석) -> studyrlt.exe <study>.sdy -exportoutput
       -> 케이스 폴더에 mf_screen_output.txt 저장
       results.json 은 쓰지 않는다. 다음 post 단계 mf_parse_output.py(python) 가 쓴다.

실행 예:
  python mf_case_run.py --project proj/proj.mpi --study-name "cover_fill_pack_warp" --sdy proj/cover_study.sdy
  python mf_case_run.py --sdy cover_study.sdy --no-com            # 변경 없이 배치 해석만
  python mf_case_run.py --sdy-pattern "cover_g{gate_id}.sdy" --no-com   # 미리 만든 게이트 후보 스터디 선택
  python mf_case_run.py --project proj/proj.mpi --study-name S1 --dump-prop 30011   # 필드 ID 조회만
실행파일 경로: 환경변수 DDS_TOOL_MOLDFLOW_RUNSTUDY / DDS_TOOL_MOLDFLOW_STUDYRLT (플랫폼이 agent.toml [tools] 에서 넣어 줌)
              또는 --runstudy / --studyrlt 인자.
실패:  0 이 아닌 종료코드 (results.json 은 원래 쓰지 않음)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import string
import subprocess
import sys
import time
import traceback

# ===================== 사용자 설정 =====================
# params.json 키 -> Moldflow 속성 필드. ID 는 Synergy 매크로 녹화(.vbs)에서 그대로 옮긴다.
#   녹화 예:  Set Prop = PropEd.FindProperty(<type>, <id>)
#             DVec.AddDouble 230
#             Prop.FieldValues <field>, DVec
# index: 필드 값 배열에서 바꿀 위치(스칼라 필드는 0). scale: params 값에 곱할 배율(단위 맞춤).
# TODO(사내검증): 아래 숫자는 자리표시자다. 반드시 녹화 매크로의 실제 type/id/field 로 바꾼다.
PROP_MAP = {
    # "melt_temp":    {"type": 0, "id": 1, "field": 0, "index": 0, "scale": 1.0},
    # "mold_temp":    {"type": 0, "id": 1, "field": 0, "index": 0, "scale": 1.0},
    # "inj_time":     {"type": 0, "id": 1, "field": 0, "index": 0, "scale": 1.0},
    # "pack_time":    {"type": 0, "id": 1, "field": 0, "index": 2, "scale": 1.0},  # 프로파일 배열 예
    # "pack_ratio":   {"type": 0, "id": 1, "field": 0, "index": 1, "scale": 1.0},
}
COMMIT_LABEL = "DDS parameter update"   # PropEd.CommitChanges 에 넘기는 설명 문자열
STRICT_PARAMS = True                    # PROP_MAP 에 없는 숫자 params 키가 있으면 실패
RUN_TIMEOUT_SEC = 6 * 3600
# =======================================================


def log(msg):
    print("[mf_case_run] " + str(msg))
    sys.stdout.flush()


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def to_float(v):
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def find_sdy(case_dir, given):
    if given:
        return os.path.abspath(given)
    cj = os.path.join(case_dir, "case.json")
    if os.path.isfile(cj):
        for v in load_json(cj).get("inputs", []):
            if str(v).lower().endswith(".sdy") and os.path.isfile(os.path.join(case_dir, v)):
                return os.path.abspath(os.path.join(case_dir, v))
    found = glob.glob(os.path.join(case_dir, "*.sdy")) + glob.glob(os.path.join(case_dir, "*", "*.sdy"))
    if len(found) == 1:
        return os.path.abspath(found[0])
    raise RuntimeError("cannot decide study file (.sdy); pass --sdy. candidates=%s" % found)


def resolve_exe(cli_value, env_key, default):
    exe = cli_value or os.environ.get(env_key) or default
    if os.path.isfile(exe):
        return exe
    w = shutil.which(exe)
    if not w:
        raise RuntimeError("executable not found: %s (set agent.toml [tools] or --%s)" % (exe, default))
    return w


# ---------------- Synergy COM ----------------
def synergy_connect():
    """실행 중인 Synergy(매크로 환경 SAInstance)가 있으면 붙고, 없으면 새로 띄운다."""
    import win32com.client  # pywin32
    inst = os.environ.get("SAInstance")
    if inst:
        # 녹화 매크로의 GetObject(%SAInstance%).GetSASynergy 패턴. TODO(사내검증)
        try:
            return win32com.client.GetObject(inst).GetSASynergy
        except Exception as e:
            log("attach to SAInstance failed, starting new Synergy: %s" % e)
    return win32com.client.Dispatch("synergy.Synergy")


def open_study(syn, project, study_name):
    syn.SetUnits("Metric")   # 이후 COM 입력/출력 값 단위계. 사내 기준이 English 면 바꾼다
    syn.OpenProject(os.path.abspath(project))
    # TODO(사내검증): 두 번째 인자 "Study" 는 항목 종류. 이름은 프로젝트 트리에 보이는 스터디 이름
    syn.Project.OpenItemByName(study_name, "Study")
    return syn.StudyDoc


def set_field(prop, field_id, dvec):
    """VBS 'Prop.FieldValues fid, DVec' 에 해당. pywin32 디스패치 방식에 따라 이름이 달라 두 가지를 시도한다.
    TODO(사내검증): 설치본에서 어느 쪽이 동작하는지 확인 후 하나만 남긴다."""
    try:
        prop.FieldValues(field_id, dvec)
        return
    except Exception as e1:
        try:
            prop.SetFieldValues(field_id, dvec)   # makepy(EnsureDispatch) 생성 시 parametrized put 이름
            return
        except Exception as e2:
            raise RuntimeError("FieldValues set failed: %s / %s" % (e1, e2))


def apply_props(syn, edits):
    ped = syn.PropertyEditor
    for key, spec, value in edits:
        prop = ped.FindProperty(spec["type"], spec["id"])
        if prop is None:
            raise RuntimeError("property not found type=%s id=%s (%s)" % (spec["type"], spec["id"], key))
        cur = prop.FieldValues(spec["field"])
        vals = [cur.Val(i) for i in range(cur.Size)] if cur is not None else []
        idx = spec.get("index", 0)
        while len(vals) <= idx:
            vals.append(0.0)
        old = vals[idx]
        vals[idx] = value * spec.get("scale", 1.0)
        dvec = syn.CreateDoubleArray()
        for v in vals:
            dvec.AddDouble(float(v))
        set_field(prop, spec["field"], dvec)
        log("set %s: type=%s id=%s field=%s[%d] %r -> %r"
            % (key, spec["type"], spec["id"], spec["field"], idx, old, vals[idx]))
    ped.CommitChanges(COMMIT_LABEL)
    # 되읽기 검증
    for key, spec, value in edits:
        cur = ped.FindProperty(spec["type"], spec["id"]).FieldValues(spec["field"])
        log("readback %s = %r" % (key, [cur.Val(i) for i in range(cur.Size)]))


def dump_props(syn, type_id):
    """속성 type 의 모든 속성/필드를 출력 (필드 ID 찾기용). TODO(사내검증): GetFirstField/GetNextField/FieldDescription 존재 여부"""
    ped = syn.PropertyEditor
    prop = ped.GetFirstProperty(type_id)
    lines = []
    while prop is not None:
        lines.append("PROPERTY type=%s id=%s name=%s" % (type_id, prop.ID, prop.Name))
        try:
            fid = prop.GetFirstField()
            while fid and fid > 0:
                vals = prop.FieldValues(fid)
                arr = [vals.Val(i) for i in range(vals.Size)] if vals is not None else []
                lines.append("  field %s  %s = %s" % (fid, prop.FieldDescription(fid), arr))
                fid = prop.GetNextField(fid)
        except Exception as e:
            lines.append("  (field listing failed: %s)" % e)
        prop = ped.GetNextProperty(prop)
    with open("mf_props_dump.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    log("wrote mf_props_dump.txt (%d lines)" % len(lines))


# ---------------- batch run ----------------
def run_logged(cmd, cwd, logfile, timeout):
    log("run: %s  (cwd=%s)" % (" ".join('"%s"' % c if " " in c else c for c in cmd), cwd))
    with open(logfile, "w", encoding="utf-8", errors="replace") as lf:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
        out = p.stdout.decode("utf-8", "replace") if p.stdout else ""
        lf.write(out)
    tail = out.strip().splitlines()[-15:]
    for ln in tail:
        log("  | " + ln)
    return p.returncode, out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sdy", help="study file to run (relative to case folder)")
    ap.add_argument("--sdy-pattern", help='e.g. "cover_g{gate_id}.sdy" (formatted with params.json)')
    ap.add_argument("--project", help=".mpi project (needed only for COM edits)")
    ap.add_argument("--study-name", help="study name in project tree (default: sdy file stem)")
    ap.add_argument("--no-com", action="store_true", help="skip Synergy; just run the study as is")
    ap.add_argument("--no-run", action="store_true", help="only edit + save (no runstudy)")
    ap.add_argument("--dump-prop", type=int, help="dump fields of property type and exit")
    ap.add_argument("--runstudy")
    ap.add_argument("--studyrlt")
    a = ap.parse_args()

    case_dir = os.getcwd()
    params = load_json("params.json") if os.path.isfile("params.json") else {}
    log("params: %s" % params)

    # --sdy-pattern "cover_g{gate_id}.sdy": 이산 설계변수(게이트 후보, 형상 변형)별로 미리 만든 스터디 중 하나를 고른다
    used_keys = set()
    if a.sdy_pattern:
        used_keys = {f for _, f, _, _ in string.Formatter().parse(a.sdy_pattern) if f}
        fmt = {}
        for k in used_keys:
            if k not in params:
                raise RuntimeError("sdy-pattern key missing in params.json: %s" % k)
            v = to_float(params[k])
            fmt[k] = int(round(v)) if v is not None and abs(v - round(v)) < 1e-9 else params[k]
        a.sdy = a.sdy_pattern.format(**fmt)

    edits = []
    for key, raw in params.items():
        val = to_float(raw)
        if key in used_keys:
            continue
        if key in PROP_MAP:
            if val is None:
                raise RuntimeError("non numeric value %s=%r" % (key, raw))
            edits.append((key, PROP_MAP[key], val))
        elif val is not None:
            if STRICT_PARAMS and not a.no_com:
                raise RuntimeError("params key not in PROP_MAP: %s" % key)
            log("WARNING: params key ignored: %s" % key)

    sdy = find_sdy(case_dir, a.sdy)
    log("study file: " + sdy)

    if (edits or a.dump_prop) and not a.no_com:
        if not a.project:
            raise RuntimeError("--project (.mpi) is required for COM edits")
        syn = synergy_connect()
        try:
            open_study(syn, a.project, a.study_name or os.path.splitext(os.path.basename(sdy))[0])
            if a.dump_prop:
                dump_props(syn, a.dump_prop)
                return 0
            apply_props(syn, edits)
            syn.StudyDoc.Save()
            log("study saved")
        finally:
            try:
                syn.Quit(False)   # TODO(사내검증): Quit 인자(저장 여부) 의미
            except Exception as e:
                log("Synergy.Quit failed (ignore): %s" % e)
        # TODO(사내검증): 프로젝트에서 연 스터디가 --sdy 파일과 같은 파일인지(프로젝트 폴더 내 실제 .sdy 이름) 확인

    if a.no_run:
        return 0

    runstudy = resolve_exe(a.runstudy, "DDS_TOOL_MOLDFLOW_RUNSTUDY", "runstudy")
    studyrlt = resolve_exe(a.studyrlt, "DDS_TOOL_MOLDFLOW_STUDYRLT", "studyrlt")
    sdy_dir = os.path.dirname(sdy)
    t0 = time.time() - 2

    rc, out = run_logged([runstudy, os.path.basename(sdy)], sdy_dir,
                         os.path.join(case_dir, "mf_runstudy.log"), RUN_TIMEOUT_SEC)
    log("runstudy exit code %s (%.0f s)" % (rc, time.time() - t0))
    if rc != 0:
        raise RuntimeError("runstudy failed (exit %s), see mf_runstudy.log" % rc)

    # 해석 로그(.out) -> 텍스트. TODO(사내검증): -exportoutput 결과 파일 이름/위치(기본 <study>.txt 로 가정)
    rc2, _ = run_logged([studyrlt, os.path.basename(sdy), "-exportoutput"], sdy_dir,
                        os.path.join(case_dir, "mf_studyrlt.log"), 600)
    if rc2 != 0:
        raise RuntimeError("studyrlt -exportoutput failed (exit %s)" % rc2)
    stem = os.path.splitext(os.path.basename(sdy))[0]
    cands = [p for p in glob.glob(os.path.join(sdy_dir, "*.txt")) if os.path.getmtime(p) >= t0]
    preferred = [p for p in cands if os.path.basename(p).lower().startswith(stem.lower())]
    pick = preferred or cands
    if not pick:
        raise RuntimeError("no exported screen output (*.txt newer than run) in %s" % sdy_dir)
    src = max(pick, key=os.path.getmtime)
    shutil.copy2(src, os.path.join(case_dir, "mf_screen_output.txt"))
    log("screen output: %s -> mf_screen_output.txt" % src)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
