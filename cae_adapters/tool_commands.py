"""tool 키 → 배치 실행 명령 템플릿 (docs/skill-contract.md 2절).
{script}=스크립트 파일, {args}=추가 인자(펼쳐짐), 그 외 {키}는 config.toml [tools]의 실행파일 경로.
config.toml의 [tool_commands]로 덮어쓸 수 있다.
"""
from __future__ import annotations

import os
import sys

from .base import resolve_cmd

DEFAULT_TOOL_COMMANDS: dict[str, list[str]] = {
    "nx": ["{nx_run_journal}", "{script}", "-args", "{args}"],
    "simcenter": ["{nx_run_journal}", "{script}", "-args", "{args}"],
    "abaqus_cae": ["{abaqus}", "cae", "noGUI={script}", "--", "{args}"],
    "abaqus_python": ["{abaqus}", "python", "{script}", "{args}"],
    "abaqus_solver": ["{abaqus}", "{args}"],
    "lsdyna": ["{lsdyna}", "{args}"],
    "lsprepost": ["{lsprepost}", "-nographics", "c={script}"],
    "ansys_python": ["{ansys_python}", "{script}", "{args}"],
    "ansys_wb": ["{ansys_runwb2}", "-B", "-R", "{script}"],
    "mapdl": ["{ansys_mapdl}", "-b", "-i", "{script}", "-o", "solve.out"],
    "altair_hm": ["{altair_hmbatch}", "-tcl", "{script}"],
    "altair_hv": ["{altair_hw}", "-b", "-tcl", "{script}"],
    "optistruct": ["{altair_optistruct}", "{args}"],
    "simcenter_chain": ["{python}", "{script}", "--run-journal", "{nx_run_journal}", "{args}"],
    "starccm": ["{starccm}", "-batch", "{script}", "{args}"],
    "starccm_run": ["{starccm}", "-batch", "run", "{args}"],
    "amesim": ["{amesim_python}", "{script}", "{args}"],
    # 다물체 동역학
    "adams_view": ["{adams}", "aview", "ru-standard", "b", "{script}"],  # .cmd 배치(모델 수정·시뮬레이션)
    "adams_solver": ["{adams}", "ru-standard", "{args}"],  # .acf 솔버 단독
    "adams_python": ["{adams}", "python", "{script}", "{args}"],  # Adams View Python(Adams 2017.2+)
    "adams_car": ["{adams}", "acar", "ru-solver", "{args}"],  # Adams Car files_only 이벤트 솔버 실행 — TODO(사내검증)
    "recurdyn": ["{recurdyn}", "{args}"],  # RecurDyn GUI/ProcessNet 매크로 실행
    "recurdyn_solver": ["{recurdyn_solver}", "{args}"],  # 솔버 배치(.rmd)
    # 사출 성형
    "moldflow_run": ["{moldflow_runstudy}", "{args}"],  # runstudy.exe study.sdy
    "moldflow_result": ["{moldflow_studyrlt}", "{args}"],  # studyrlt.exe study.sdy -exportoutput ...
    "moldflow_synergy": ["{python}", "{script}", "{args}"],  # Synergy COM API(pywin32)
    "moldex3d": ["{moldex3d_python}", "{script}", "{args}"],  # Moldex3D API(Python)
    # 다중물리·유동·열
    "comsol_batch": ["{comsol}", "batch", "{args}"],  # -inputfile m.mph -outputfile out.mph -pname/-plist -methodcall
    "comsol_python": ["{python}", "{script}", "{args}"],  # MPh 패키지(comsol mphserver 경유)
    "fluent": ["{fluent}", "3ddp", "-g", "-t4", "-i", "{script}"],  # TUI 저널(.jou)
    "fluent_run": ["{fluent}", "{args}"],  # 차원·코어·-wait를 args로 지정 (예: 3ddp -g -t8 -i run.jou -wait)
    "fluent_python":["{ansys_python}", "{script}", "{args}"],  # PyFluent
    "openfoam": ["{openfoam_shell}", "bash", "{script}", "{args}"],  # WSL/리눅스 bash 스크립트
    "flotherm": ["{flotherm}", "-b", "{args}"],  # FloSCRIPT 배치
    # 전처리·후처리·피로
    "ansa": ["{ansa}", "-nogui", "-exec", "load_script: '{script}'", "-exec", "main()"],
    "meta": ["{meta}", "-b", "-s", "{script}"],  # META 세션 파일 배치
    "msc_nastran": ["{msc_nastran}", "{args}"],  # nastran job.bdf scr=yes
    "ncode": ["{ncode}", "{args}"],  # DesignLife 배치(.flo)
    # CAD (COM 자동화는 pywin32 파이썬, 배치 매크로는 툴 실행파일)
    "catia_com": ["{python}", "{script}", "{args}"],
    "catia_batch": ["{catia}", "-batch", "-macro", "{script}"],
    "solidworks_com": ["{python}", "{script}", "{args}"],
    "creo_trail": ["{creo}", "-g:no_graphics", "-i:rpc_input", "{script}"],
    "creo_python": ["{python}", "{script}", "{args}"],  # Creoson(JSON) 또는 Creo Toolkit 래퍼
    # 광학
    "lighttools_com": ["{python}", "{script}", "{args}"],  # LightTools COM API(LTAPI4, pywin32)
    "python": ["{python}", "{script}", "{args}"],
}

DEFAULT_TOOL_PATHS: dict[str, str] = {
    "nx_run_journal": r"C:\Program Files\Siemens\NX\NXBIN\run_journal.exe",
    "abaqus": "abaqus",
    "lsdyna": "lsdyna",
    "lsprepost": "lsprepost",
    "ansys_python": "",  # Ansys 패키지(PyMAPDL 등)가 설치된 파이썬 — agent.toml [tools]에 지정
    "ansys_runwb2": "runwb2",
    "ansys_mapdl": "mapdl",
    "altair_hmbatch": "hmbatch",
    "altair_hw": "hw",
    "altair_optistruct": "optistruct",
    "starccm": "starccm+",
    "amesim_python": "amesim_python",
    "adams": "mdi",  # 예: C:/Program Files/MSC.Software/Adams/2023_1/common/mdi.bat
    "recurdyn": "",  # 예: C:/Program Files/FunctionBay, Inc/RecurDyn V9R5/Bin/RecurDyn.exe
    "recurdyn_solver": "",
    "moldflow_runstudy": "runstudy",  # 예: C:/Program Files/Autodesk/Moldflow Insight 2025/bin/runstudy.exe
    "moldflow_studyrlt": "studyrlt",
    "moldex3d_python": "",
    "comsol": "comsol",
    "fluent": "fluent",
    "openfoam_shell": "wsl",
    "flotherm": "",
    "ansa": "ansa64",
    "meta": "meta_post64",
    "msc_nastran": "nastran",
    "ncode": "",
    "catia": "CNEXT",
    "creo": "parametric",
    "solidworks": "",  # COM 자동화라 명령에는 안 쓰고 설치 확인용(SLDWORKS.exe)
    "lighttools": "",  # COM 자동화. 설치 확인과 LightTools를 새로 띄울 때 사용(lt.exe)
}


def build_command(tool: str, script: str | None, args: list[str], paths: dict[str, str],
                  commands: dict[str, list[str]] | None = None) -> list[str]:
    table = {**DEFAULT_TOOL_COMMANDS, **(commands or {})}
    if tool not in table:
        raise ValueError(f"알 수 없는 tool 키: {tool} (사용 가능: {', '.join(sorted(table))})")
    values = {**DEFAULT_TOOL_PATHS, **paths, "python": sys.executable}
    cmd: list[str] = []
    for i, part in enumerate(table[tool]):
        if part == "{args}":
            cmd.extend(args)
        elif part == "{script}":
            if script is None:
                raise ValueError(f"tool '{tool}' 은 스크립트가 필요합니다")
            cmd.append(script)
        elif i == 0 and part.startswith("{") and part.endswith("}"):
            exe = values.get(part[1:-1], part[1:-1])
            if not exe:
                raise ValueError(f"'{part[1:-1]}' 실행 파일 경로가 설정되지 않았습니다. agent.toml의 [tools]에 지정하세요")
            cmd.extend(resolve_cmd(exe))
        else:
            cmd.append(part.format(script=script or "", **values) if "{" in part else part)
    return cmd


def tool_env(tool: str, paths: dict[str, str], tool_envs: dict[str, dict[str, str]] | None = None,
             case_dir: str | None = None) -> dict[str, str]:
    """툴 실행 환경변수.
    - 모든 툴 경로를 DDS_TOOL_<KEY> 와 DDS_<KEY> 로 노출(예: DDS_TOOL_ANSYS_MAPDL = DDS_ANSYS_MAPDL)
      → python 드라이버 스크립트가 솔버를 직접 호출할 때 사용
    - NX_RUN_JOURNAL 별칭, DDS_CASE_DIR(케이스 실행 폴더, Workbench 저널처럼 인자를 못 받는 경우용)
    - config [tool_env.<tool>] 의 값(예: Amesim의 AME, PATH 앞에 붙일 경로)을 덮어쓴다. "{PATH}"는 기존 PATH로 치환.
    """
    values = {**DEFAULT_TOOL_PATHS, **paths}
    env = dict(os.environ)
    env.update({f"DDS_TOOL_{k.upper()}": v for k, v in values.items()})
    env.update({f"DDS_{k.upper()}": v for k, v in values.items()})
    if case_dir:
        env["DDS_CASE_DIR"] = case_dir
    env["NX_RUN_JOURNAL"] = values.get("nx_run_journal", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    for k, v in ((tool_envs or {}).get(tool) or {}).items():
        env[k] = v.replace("{PATH}", os.environ.get("PATH", ""))
    return env
