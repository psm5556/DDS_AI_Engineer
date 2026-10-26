"""지원하는 CAD/CAE 툴 목록 — 웹앱 'CAD·CAE 툴' 화면, PC Agent 진단, 에이전트의 dds-cae.list_tools가 모두 이 표를 쓴다.

각 툴: id, 이름, 회사, 분야, 스킬 폴더, 필요한 실행 파일 키(paths), 실행 명령 키(run), 에이전트가 할 수 있는 일(can),
Live Link 지원 여부, 이 PC에서 자동으로 찾을 기본 설치 경로(guess, glob — 여러 버전이면 이름순 마지막=최신).
"""
from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path

PARAM, COND, RUN, RESULT, IMAGE, FIELD = "파라미터 변경", "해석 조건 변경", "배치 실행", "결과 추출", "이미지 저장", "3D 필드"
EXPORT, MASS, LIVE, MESH = "형상 내보내기", "질량특성", "Live Link", "메쉬·전처리"

CATEGORIES = ["CAD", "전처리·후처리", "구조 해석", "충돌·비선형", "유동·열", "다중물리", "다물체 동역학", "사출 성형", "1D 시스템", "피로·내구"]

PF = os.environ.get("ProgramFiles", r"C:\Program Files")

TOOLS: list[dict] = [
    # ---------- CAD ----------
    {"id": "nx", "name": "Siemens NX", "vendor": "Siemens", "category": "CAD", "skill": "nx-cad-automation",
     "paths": ["nx_run_journal"], "run": ["nx"], "can": [PARAM, EXPORT, MASS, IMAGE, LIVE],
     "guess": {"nx_run_journal": [PF + r"\Siemens\*\NXBIN\run_journal.exe"]}},
    {"id": "catia", "name": "CATIA V5", "vendor": "Dassault Systèmes", "category": "CAD", "skill": "catia-automation",
     "paths": ["catia"], "run": ["catia_com", "catia_batch"], "can": [PARAM, EXPORT, MASS, IMAGE],
     "guess": {"catia": [PF + r"\Dassault Systemes\B*\win_b64\code\bin\CNEXT.exe"]}},
    {"id": "solidworks", "name": "SOLIDWORKS", "vendor": "Dassault Systèmes", "category": "CAD", "skill": "solidworks-automation",
     "paths": ["solidworks"], "run": ["solidworks_com"], "can": [PARAM, EXPORT, MASS, IMAGE],
     "guess": {"solidworks": [PF + r"\SOLIDWORKS Corp\SOLIDWORKS\SLDWORKS.exe"]}},
    {"id": "creo", "name": "Creo Parametric", "vendor": "PTC", "category": "CAD", "skill": "creo-automation",
     "paths": ["creo"], "run": ["creo_trail", "creo_python"], "can": [PARAM, EXPORT, MASS, IMAGE],
     "guess": {"creo": [PF + r"\PTC\Creo *\Parametric\bin\parametric.exe"]}},
    # ---------- 전처리·후처리 ----------
    {"id": "ansa", "name": "ANSA · META", "vendor": "BETA CAE Systems", "category": "전처리·후처리", "skill": "ansa-automation",
     "paths": ["ansa", "meta"], "run": ["ansa", "meta"], "can": [MESH, PARAM, COND, IMAGE, FIELD],
     "guess": {"ansa": [PF + r"\BETA_CAE_Systems\ansa_v*\ansa64.bat"], "meta": [PF + r"\BETA_CAE_Systems\meta_post_v*\meta_post64.bat"]}},
    {"id": "altair", "name": "HyperMesh · OptiStruct · HyperView", "vendor": "Altair", "category": "전처리·후처리",
     "skill": "altair-automation", "paths": ["altair_hmbatch", "altair_hw", "altair_optistruct"],
     "run": ["altair_hm", "altair_hv", "optistruct"], "can": [MESH, PARAM, COND, RUN, RESULT, IMAGE, FIELD, LIVE],
     "guess": {"altair_hmbatch": [PF + r"\Altair\*\hwdesktop\hm\bin\win64\hmbatch.exe"],
               "altair_hw": [PF + r"\Altair\*\hwdesktop\hw\bin\win64\hw.exe"],
               "altair_optistruct": [PF + r"\Altair\*\hwsolvers\scripts\optistruct.bat"]}},
    # ---------- 구조 해석 ----------
    {"id": "simcenter3d", "name": "Simcenter 3D · Nastran", "vendor": "Siemens", "category": "구조 해석",
     "skill": "simcenter3d-automation", "paths": ["nx_run_journal"], "run": ["simcenter", "simcenter_chain"],
     "can": [PARAM, COND, RUN, RESULT, IMAGE, FIELD, LIVE],
     "guess": {"nx_run_journal": [PF + r"\Siemens\*\NXBIN\run_journal.exe"]}},
    {"id": "abaqus", "name": "Abaqus", "vendor": "Dassault Systèmes SIMULIA", "category": "구조 해석", "skill": "abaqus-automation",
     "paths": ["abaqus"], "run": ["abaqus_cae", "abaqus_python", "abaqus_solver"],
     "can": [PARAM, COND, RUN, RESULT, IMAGE, FIELD, LIVE], "guess": {"abaqus": [r"C:\SIMULIA\Commands\abaqus.bat"]}},
    {"id": "ansys", "name": "Ansys Mechanical · MAPDL · Workbench", "vendor": "Ansys", "category": "구조 해석",
     "skill": "ansys-automation", "paths": ["ansys_runwb2", "ansys_mapdl", "ansys_python"], "optional": ["ansys_python"],
     "run": ["ansys_wb", "mapdl", "ansys_python"], "can": [PARAM, COND, RUN, RESULT, IMAGE, FIELD],
     "guess": {"ansys_runwb2": [PF + r"\ANSYS Inc\v*\Framework\bin\Win64\RunWB2.exe"],
               "ansys_mapdl": [PF + r"\ANSYS Inc\v*\ansys\bin\winx64\ANSYS*.exe"]}},
    {"id": "msc_nastran", "name": "MSC Nastran", "vendor": "Hexagon", "category": "구조 해석", "skill": "msc-nastran-automation",
     "paths": ["msc_nastran"], "run": ["msc_nastran"], "can": [PARAM, COND, RUN, RESULT, IMAGE, FIELD],
     "guess": {"msc_nastran": [PF + r"\MSC.Software\MSC_Nastran\*\bin\nastran.exe"]}},
    # ---------- 충돌·비선형 ----------
    {"id": "lsdyna", "name": "LS-DYNA · LS-PrePost", "vendor": "Ansys(LST)", "category": "충돌·비선형", "skill": "lsdyna-automation",
     "paths": ["lsdyna", "lsprepost"], "optional": ["lsprepost"], "run": ["lsdyna", "lsprepost"],
     "can": [PARAM, COND, RUN, RESULT, IMAGE, FIELD],
     "guess": {"lsdyna": [r"C:\LSDYNA\program\ls-dyna*.exe", PF + r"\ANSYS Inc\v*\ansys\bin\winx64\lsdyna*.exe"],
               "lsprepost": [r"C:\LSDYNA\program\lsprepost*\lsprepost*.exe"]}},
    # ---------- 유동·열 ----------
    {"id": "starccm", "name": "STAR-CCM+", "vendor": "Siemens", "category": "유동·열", "skill": "starccm-automation",
     "paths": ["starccm"], "run": ["starccm", "starccm_run"], "can": [PARAM, COND, RUN, RESULT, IMAGE],
     "guess": {"starccm": [PF + r"\Siemens\*\STAR-CCM+*\star\bin\starccm+.bat"]}},
    {"id": "fluent", "name": "Ansys Fluent", "vendor": "Ansys", "category": "유동·열", "skill": "fluent-automation",
     "paths": ["fluent"], "run": ["fluent", "fluent_run", "fluent_python"], "can": [PARAM, COND, RUN, RESULT, IMAGE, FIELD],
     "guess": {"fluent": [PF + r"\ANSYS Inc\v*\fluent\ntbin\win64\fluent.exe"]}},
    {"id": "openfoam", "name": "OpenFOAM", "vendor": "오픈소스(WSL)", "category": "유동·열", "skill": "openfoam-automation",
     "paths": ["openfoam_shell"], "run": ["openfoam"], "can": [PARAM, COND, RUN, RESULT, IMAGE, FIELD],
     "guess": {"openfoam_shell": [r"C:\Windows\System32\wsl.exe"]},
     # WSL만 있고 OpenFOAM이 없을 수 있다 — 실제 솔버가 있는지 한 번 더 확인
     "verify": {"cmd": ["{openfoam_shell}", "bash", "-lc", "command -v simpleFoam"], "hint": "WSL 안에 OpenFOAM을 설치하세요"}},
    {"id": "flotherm", "name": "Simcenter Flotherm", "vendor": "Siemens", "category": "유동·열", "skill": "flotherm-automation",
     "paths": ["flotherm"], "run": ["flotherm"], "can": [PARAM, COND, RUN, RESULT, IMAGE],
     "guess": {"flotherm": [PF + r"\Siemens\SimcenterFlotherm\*\WinXP\bin\flotherm.bat"]}},
    # ---------- 다중물리 ----------
    {"id": "comsol", "name": "COMSOL Multiphysics", "vendor": "COMSOL", "category": "다중물리", "skill": "comsol-automation",
     "paths": ["comsol"], "run": ["comsol_batch", "comsol_python"], "can": [PARAM, COND, RUN, RESULT, IMAGE, FIELD],
     "guess": {"comsol": [PF + r"\COMSOL\COMSOL*\Multiphysics\bin\win64\comsol.exe"]}},
    # ---------- 다물체 동역학 ----------
    {"id": "adams", "name": "Adams", "vendor": "Hexagon(MSC)", "category": "다물체 동역학", "skill": "adams-automation",
     "paths": ["adams"], "run": ["adams_view", "adams_solver", "adams_python", "adams_car"], "can": [PARAM, COND, RUN, RESULT, IMAGE],
     "guess": {"adams": [PF + r"\MSC.Software\Adams\*\common\mdi.bat"]}},
    {"id": "recurdyn", "name": "RecurDyn", "vendor": "FunctionBay", "category": "다물체 동역학", "skill": "recurdyn-automation",
     "paths": ["recurdyn", "recurdyn_solver"], "optional": ["recurdyn_solver"], "run": ["recurdyn", "recurdyn_solver"],
     "can": [PARAM, COND, RUN, RESULT, IMAGE], "guess": {"recurdyn": [PF + r"\FunctionBay, Inc\RecurDyn*\Bin\RecurDyn.exe"]}},
    # ---------- 사출 성형 ----------
    {"id": "moldflow", "name": "Moldflow Insight · Synergy", "vendor": "Autodesk", "category": "사출 성형",
     "skill": "moldflow-automation", "paths": ["moldflow_runstudy", "moldflow_studyrlt"], "run": ["moldflow_run", "moldflow_result", "moldflow_synergy"],
     "can": [PARAM, COND, RUN, RESULT, IMAGE],
     "guess": {"moldflow_runstudy": [PF + r"\Autodesk\Moldflow Insight *\bin\runstudy.exe"],
               "moldflow_studyrlt": [PF + r"\Autodesk\Moldflow Insight *\bin\studyrlt.exe"]}},
    {"id": "moldex3d", "name": "Moldex3D", "vendor": "CoreTech System", "category": "사출 성형", "skill": "moldex3d-automation",
     "paths": ["moldex3d_python"], "run": ["moldex3d"], "can": [PARAM, COND, RUN, RESULT, IMAGE], "guess": {}},
    # ---------- 1D 시스템 ----------
    {"id": "amesim", "name": "Simcenter Amesim", "vendor": "Siemens", "category": "1D 시스템", "skill": "amesim-automation",
     "paths": ["amesim_python"], "run": ["amesim"], "can": [PARAM, COND, RUN, RESULT, IMAGE], "guess": {}},
    # ---------- 피로·내구 ----------
    {"id": "ncode", "name": "nCode DesignLife", "vendor": "HBK", "category": "피로·내구", "skill": "ncode-automation",
     "paths": ["ncode"], "run": ["ncode"], "can": [PARAM, COND, RUN, RESULT, IMAGE], "guess": {}},
]


def resolve(exe: str | None) -> str | None:
    """실행 파일 경로를 실제 파일로 확인(절대경로 존재 또는 PATH에서 찾기)."""
    if not exe:
        return None
    if Path(exe).exists():
        return str(Path(exe))
    return shutil.which(exe)


def discover(key: str) -> str | None:
    """기본 설치 경로에서 실행 파일을 찾는다(버전이 여러 개면 이름순 마지막 = 대개 최신)."""
    for t in TOOLS:
        for pattern in t.get("guess", {}).get(key, []):
            hits = sorted(glob.glob(pattern))
            if hits:
                return hits[-1]
    return None


def path_keys() -> set[str]:
    return {k for t in TOOLS for k in t["paths"]}


def _verify(cmd: list[str], paths: dict[str, str]) -> bool:
    import subprocess

    try:
        args = [paths.get(c[1:-1], c) if c.startswith("{") and c.endswith("}") else c for c in cmd]
        return subprocess.run(args, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def tool_status(paths: dict[str, str], verify: bool = True) -> list[dict]:
    """툴별 이 PC의 상태: ready(필수 실행 파일 모두 찾음) | partial | missing, 실행 파일별 경로."""
    out = []
    for t in TOOLS:
        exes = []
        for key in t["paths"]:
            configured = paths.get(key) or ""
            found = resolve(configured)
            exes.append({"key": key, "configured": configured, "found": found, "optional": key in t.get("optional", [])})
        required = [e for e in exes if not e["optional"]]
        ok = sum(1 for e in required if e["found"])
        status = "ready" if required and ok == len(required) else "partial" if ok or any(e["found"] for e in exes) else "missing"
        note = None
        if status == "ready" and t.get("verify") and verify:
            if not _verify(t["verify"]["cmd"], paths):
                status, note = "partial", t["verify"]["hint"]
        out.append({"id": t["id"], "status": status, "executables": exes, "note": note})
    return out


def public_catalog() -> list[dict]:
    """웹앱에 보낼 정적 목록(기본 경로 패턴 제외)."""
    return [{k: v for k, v in t.items() if k not in ("guess", "verify")} for t in TOOLS]
