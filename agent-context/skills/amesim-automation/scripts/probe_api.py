# -*- coding: utf-8 -*-
"""설치된 Amesim Python API 조사 (py2/py3 겸용). 라이선스를 체크아웃하지 않는다.

실행:  AMEPython probe_api.py            (tool: amesim, run_script 로 1회 실행)
출력:  api_probe.txt  -- ame_apy / amesim 모듈의 함수 목록과 주요 함수 help 텍스트
       results.json   -- 함수 개수 (규약상 성공 표시용)

이 출력으로 SKILL.md 의 TODO(사내검증) 항목(AMESetRunParameter 이름, AMEGetVariableValues
반환 형식, amesim 모듈 함수 시그니처 등)을 확인한다.
"""
from __future__ import print_function

import json
import os
import pydoc
import sys
import traceback

KEY_FUNCS = [
    "AMEInitAPI", "AMEOpenAmeFile", "AMESetParameterValue", "AMEGetParameterValue",
    "AMEGetParameterInfos", "AMESetRunParameter", "AMEGenerateCode", "AMERunSimulation",
    "AMEGetVariableValues", "AMEGetVariableInfos", "AMECloseCircuit", "AMECloseAPI",
    # Simulation Scripting (amesim 모듈)
    "ameputp", "amegetsimopt", "amerunsingle", "ameloadvarst", "ameputbatch", "amerunbatch",
    "amereadgp",
]


def describe(mod_name, lines):
    try:
        mod = __import__(mod_name)
    except Exception as e:
        lines.append("## %s : import 실패 (%s)\n" % (mod_name, e))
        return 0
    names = sorted(n for n in dir(mod) if not n.startswith("_"))
    lines.append("## %s (%d names)\n" % (mod_name, len(names)))
    lines.append(", ".join(names) + "\n")
    for fn in KEY_FUNCS:
        if hasattr(mod, fn):
            try:
                text = pydoc.render_doc(getattr(mod, fn), renderer=pydoc.plaintext)
            except Exception as e:
                text = "help 실패: %s" % e
            lines.append("\n### %s.%s\n%s\n" % (mod_name, fn, text))
    return len(names)


def main():
    lines = ["# python %s\n" % sys.version, "# AME=%s\n\n" % os.environ.get("AME", "(unset)")]
    n_apy = describe("ame_apy", lines)
    n_ame = describe("amesim", lines)
    with open("api_probe.txt", "w") as f:
        f.write("\n".join(lines))
    with open("results.json", "w") as f:
        json.dump({"n_ame_apy_names": n_apy, "n_amesim_names": n_ame}, f, indent=2)
    print("\n".join(lines))
    if n_apy == 0 and n_ame == 0:
        raise RuntimeError("ame_apy / amesim 모두 import 실패 -- AMEPython 으로 실행했는지 확인")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        if os.path.exists("results.json"):
            os.remove("results.json")
        sys.exit(1)
