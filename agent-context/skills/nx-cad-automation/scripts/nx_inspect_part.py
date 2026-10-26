# -*- coding: utf-8 -*-
"""
NX 파트/어셈블리 탐색 저널 - 자동화 스크립트를 쓰기 '전에' 먼저 실행한다.

출력:
  model_info.json : expression 목록(이름/식/값/단위/종류), 바디 수, 어셈블리 트리
  results.json    : n_expressions, n_solid_bodies, n_components (규약상 숫자만)

실행 (tool 키 'nx', run_script 로 1회):
  run_journal.exe nx_inspect_part.py -args part=bracket.prt

에이전트는 model_info.json 을 읽고 사용자와 '어떤 expression 을 설계변수로 쓸지' 합의한 뒤
params.json 키를 expression 이름과 똑같이 맞춘다.
"""
import json
import os
import sys
import traceback

import NXOpen
import NXOpen.Assemblies


def log(msg):
    print("[DDS] " + str(msg))
    sys.stdout.flush()


def parse_args():
    out = {}
    for tok in sys.argv[1:]:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def find_part_file(cdir, explicit):
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(cdir, explicit)
        if not os.path.isfile(p):
            raise RuntimeError("파트 파일이 없습니다: " + p)
        return os.path.abspath(p)
    cands = [f for f in os.listdir(cdir) if f.lower().endswith(".prt")]
    if len(cands) != 1:
        raise RuntimeError("part=... 로 지정하세요. 후보: " + ", ".join(cands))
    return os.path.join(cdir, cands[0])


def expressions_info(part):
    out = []
    for e in part.Expressions:
        item = {"name": e.Name, "rhs": e.RightHandSide, "type": e.Type}
        try:
            if e.Type in ("Number", "Integer"):
                item["value"] = e.Value
        except Exception:
            pass
        try:
            item["unit"] = e.Units.Name if e.Units is not None else None
        except Exception:
            item["unit"] = None
        # 사용자 정의 이름(p12 같은 자동 이름이 아닌 것)이 보통 설계변수 후보
        nm = e.Name
        item["auto_named"] = len(nm) > 1 and nm[0] == "p" and nm[1:].isdigit()
        out.append(item)
    return out


def component_tree(comp, depth=0, max_depth=10):
    node = {"name": comp.DisplayName, "children": []}
    try:
        node["suppressed"] = comp.IsSuppressed
    except Exception:
        pass
    try:
        proto = comp.Prototype          # 로드 안 된 컴포넌트면 None 일 수 있음
        node["part_file"] = proto.FullPath if proto is not None else None
    except Exception:
        node["part_file"] = None
    if depth < max_depth:
        for ch in comp.GetChildren():
            node["children"].append(component_tree(ch, depth + 1, max_depth))
    return node


def count_nodes(tree):
    return 1 + sum(count_nodes(c) for c in tree["children"])


def main():
    args = parse_args()
    cdir = os.path.abspath(args.get("case_dir") or os.getcwd())
    res_path = os.path.join(cdir, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)

    part_file = find_part_file(cdir, args.get("part"))
    session = NXOpen.Session.GetSession()
    part, load_status = session.Parts.OpenBaseDisplay(part_file)
    work = session.Parts.Work

    info = {"part_file": part_file,
            "expressions": expressions_info(work),
            "n_solid_bodies": len([b for b in work.Bodies if b.IsSolidBody]),
            "n_sheet_bodies": len([b for b in work.Bodies if b.IsSheetBody]),
            "assembly": None,
            "loaded_parts": []}

    root = None
    try:
        root = work.ComponentAssembly.RootComponent   # 단품 파트면 None
    except Exception:
        root = None
    n_comp = 0
    if root is not None:
        info["assembly"] = component_tree(root)
        n_comp = count_nodes(info["assembly"]) - 1

    for p in session.Parts:
        try:
            info["loaded_parts"].append(p.FullPath)
        except Exception:
            pass

    with open(os.path.join(cdir, "model_info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)

    results = {"n_expressions": len(info["expressions"]),
               "n_solid_bodies": info["n_solid_bodies"],
               "n_components": n_comp}
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    log("model_info.json 작성 완료: " + json.dumps(results))


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
