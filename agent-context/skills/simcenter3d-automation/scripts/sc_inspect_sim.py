# -*- coding: utf-8 -*-
"""
Simcenter 3D 모델 탐색 저널 - 자동화 스크립트를 쓰기 '전에' 먼저 실행한다.

출력:
  model_info.json : 로드된 파트(경로), 각 파트 expression, 솔루션/하중/구속 이름,
                    물리속성 테이블 이름, 재료 이름
  results.json    : n_solutions, n_loads, n_constraints (규약상 숫자만)

실행 (tool 키 'simcenter', run_script 로 1회):
  run_journal.exe sc_inspect_sim.py -args sim=bracket_sim1.sim
"""
import glob
import json
import os
import sys
import traceback

import NXOpen
import NXOpen.CAE


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


def names(collection):
    out = []
    try:
        for o in collection:
            try:
                out.append(o.Name)
            except Exception:
                out.append(str(o))
    except Exception as e:
        out.append("<조회 실패: %s>" % e)
    return out


def expressions(part):
    out = []
    for e in part.Expressions:
        item = {"name": e.Name, "rhs": e.RightHandSide, "type": e.Type}
        try:
            item["unit"] = e.Units.Name if e.Units is not None else None
        except Exception:
            item["unit"] = None
        out.append(item)
    return out


def main():
    args = parse_args()
    cdir = os.path.abspath(args.get("case_dir") or os.getcwd())
    res_path = os.path.join(cdir, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)
    sim_path = args.get("sim")
    if sim_path:
        sim_path = sim_path if os.path.isabs(sim_path) else os.path.join(cdir, sim_path)
    else:
        c = glob.glob(os.path.join(cdir, "*.sim"))
        if len(c) != 1:
            raise RuntimeError("sim=... 로 지정하세요. 후보: %s" % c)
        sim_path = c[0]

    session = NXOpen.Session.GetSession()
    try:
        session.Parts.LoadOptions.ComponentLoadMethod = NXOpen.LoadOptions.LoadMethod.FromDirectory
    except Exception as e:
        log("LoadOptions 설정 실패: " + str(e))
    session.Parts.OpenBaseDisplay(sim_path)
    sim_part = session.Parts.BaseWork
    simulation = sim_part.Simulation

    info = {"sim": sim_path, "loaded_parts": [], "expressions": {},
            "solutions": names(simulation.Solutions),
            "loads": names(simulation.Loads),
            "constraints": names(simulation.Constraints)}

    fem_part = None
    try:
        fem_part = sim_part.FemPart
        info["fem"] = fem_part.FullPath
    except Exception as e:
        info["fem"] = "<조회 실패: %s>" % e

    if fem_part is not None:
        # TODO(사내검증): 컬렉션 이름은 버전별로 Journal 기록/레퍼런스로 확인
        try:
            info["physical_property_tables"] = names(fem_part.PhysicalPropertyTables)
        except Exception as e:
            info["physical_property_tables"] = "<조회 실패: %s>" % e
        try:
            info["materials"] = names(fem_part.MaterialManager.PhysicalMaterials)
        except Exception as e:
            info["materials"] = "<조회 실패: %s>" % e

    for p in session.Parts:
        try:
            fp = p.FullPath
        except Exception:
            continue
        info["loaded_parts"].append(fp)
        try:
            info["expressions"][os.path.basename(fp)] = expressions(p)
        except Exception as e:
            info["expressions"][os.path.basename(fp)] = "<조회 실패: %s>" % e

    outside = [fp for fp in info["loaded_parts"]
               if not os.path.normcase(fp).startswith(os.path.normcase(cdir))]
    info["loaded_outside_case_dir"] = outside
    if outside:
        log("경고: 케이스 폴더 밖 파일이 로드됨 -> 원본 수정 위험: %s" % outside)

    with open(os.path.join(cdir, "model_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
    results = {"n_solutions": len(info["solutions"]), "n_loads": len(info["loads"]),
               "n_constraints": len(info["constraints"]),
               "n_loaded_outside_case_dir": len(outside)}
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    log("model_info.json 작성: " + json.dumps(results))


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
    if rc:
        sys.exit(rc)
