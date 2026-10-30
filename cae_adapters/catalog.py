"""지원하는 CAD/CAE 툴 목록 — 웹앱 'CAD·CAE 툴' 화면, PC Agent 진단, 에이전트의 dds-cae.list_tools가 모두 이 표를 쓴다.

각 툴: id, 이름, 회사, 분야, 스킬 폴더, 필요한 실행 파일 키(paths), 실행 명령 키(run), 에이전트가 할 수 있는 일(can),
Live Link 지원 여부, 이 PC에서 자동으로 찾을 기본 설치 경로(guess, glob).
PC Agent는 guess를 기본 위치뿐 아니라 다른 드라이브·설치된 앱 목록·시작 메뉴·환경변수에서 찾은 설치 폴더에도 적용한다
(agent/dds_agent/toolscan.py). 새 툴을 넣을 때는 TOOLS에 guess를, FIND에 설치 흔적을 알아볼 단어를 넣는다.
"""
from __future__ import annotations

import glob
import os
import shutil
from pathlib import Path

PARAM, COND, RUN, RESULT, IMAGE, FIELD = "파라미터 변경", "해석 조건 변경", "배치 실행", "결과 추출", "이미지 저장", "3D 필드"
EXPORT, MASS, LIVE, MESH = "형상 내보내기", "질량특성", "Live Link", "메쉬·전처리"
TOPO, DATA, ML = "위상 최적화", "데이터셋 변환", "학습·예측"

CATEGORIES = ["CAD", "전처리·후처리", "구조 해석", "충돌·비선형", "유동·열", "다중물리", "전자기", "다물체 동역학", "사출 성형",
              "1D 시스템", "피로·내구", "광학", "설계 최적화", "AI 예측"]

PF = os.environ.get("ProgramFiles", r"C:\Program Files")
PFX86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")

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
    {"id": "simlab", "name": "Altair SimLab", "vendor": "Altair", "category": "전처리·후처리", "skill": "simlab-automation",
     "paths": ["simlab"], "run": ["simlab"], "can": [MESH, PARAM, COND, RUN, RESULT, IMAGE],
     "guess": {"simlab": [PF + r"\Altair\*\SimLab*\bin\win64\SimLab.bat", PF + r"\Altair\*\hwdesktop\SimLab*\bin\win64\SimLab.bat"]}},
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
    {"id": "celsius", "name": "Cadence Celsius", "vendor": "Cadence", "category": "유동·열", "skill": "celsius-automation",
     "paths": ["celsius"], "run": ["celsius", "celsius_python"], "can": [PARAM, COND, RUN, RESULT, IMAGE],
     # TODO(사내검증): 설치 폴더·실행 파일 이름(Celsius Thermal Solver / Celsius EC Solver)
     "guess": {"celsius": [r"C:\Cadence\Celsius*\tools\bin\celsius*.exe", PF + r"\Cadence\Celsius*\bin\celsius*.exe"]}},
    # ---------- 다중물리 ----------
    {"id": "comsol", "name": "COMSOL Multiphysics", "vendor": "COMSOL", "category": "다중물리", "skill": "comsol-automation",
     "paths": ["comsol"], "run": ["comsol_batch", "comsol_python"], "can": [PARAM, COND, RUN, RESULT, IMAGE, FIELD],
     "guess": {"comsol": [PF + r"\COMSOL\COMSOL*\Multiphysics\bin\win64\comsol.exe"]}},
    # ---------- 전자기 ----------
    {"id": "cst", "name": "CST Studio Suite", "vendor": "Dassault Systèmes", "category": "전자기", "skill": "cst-automation",
     "paths": ["cst"], "run": ["cst_python", "cst_batch"], "can": [PARAM, COND, RUN, RESULT, IMAGE],
     "guess": {"cst": [PF + r"\CST Studio Suite *\CST DESIGN ENVIRONMENT.exe", PFX86 + r"\CST Studio Suite *\CST DESIGN ENVIRONMENT.exe",
                       PF + r"\Dassault Systemes\CST Studio Suite *\CST DESIGN ENVIRONMENT.exe"]}},
    # ---------- 다물체 동역학 ----------
    {"id": "adams", "name": "Adams", "vendor": "Hexagon(MSC)", "category": "다물체 동역학", "skill": "adams-automation",
     "paths": ["adams"], "run": ["adams_view", "adams_solver", "adams_python", "adams_car"], "can": [PARAM, COND, RUN, RESULT, IMAGE],
     "guess": {"adams": [PF + r"\MSC.Software\Adams\*\common\mdi.bat"]}},
    {"id": "recurdyn", "name": "RecurDyn", "vendor": "FunctionBay", "category": "다물체 동역학", "skill": "recurdyn-automation",
     "paths": ["recurdyn", "recurdyn_solver"], "optional": ["recurdyn_solver"], "run": ["recurdyn", "recurdyn_solver"],
     "can": [PARAM, COND, RUN, RESULT, IMAGE], "guess": {"recurdyn": [PF + r"\FunctionBay, Inc\RecurDyn*\Bin\RecurDyn.exe"]}},
    {"id": "motionsolve", "name": "Altair MotionSolve", "vendor": "Altair", "category": "다물체 동역학", "skill": "motionsolve-automation",
     "paths": ["motionsolve"], "run": ["motionsolve", "motionsolve_python"], "can": [PARAM, COND, RUN, RESULT, IMAGE],
     "guess": {"motionsolve": [PF + r"\Altair\*\hwsolvers\scripts\motionsolve.bat"]}},
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
     "paths": ["amesim_python"], "run": ["amesim"], "can": [PARAM, COND, RUN, RESULT, IMAGE],
     "guess": {"amesim_python": [PF + r"\Simcenter\*\Amesim\win64\AMEPython.exe"]}},
    # ---------- 피로·내구 ----------
    {"id": "ncode", "name": "nCode DesignLife", "vendor": "HBK", "category": "피로·내구", "skill": "ncode-automation",
     "paths": ["ncode"], "run": ["ncode"], "can": [PARAM, COND, RUN, RESULT, IMAGE], "guess": {}},
    # ---------- 광학 ----------
    {"id": "lighttools", "name": "LightTools", "vendor": "Synopsys", "category": "광학", "skill": "lighttools-automation",
     "paths": ["lighttools"], "run": ["lighttools_com"], "can": [PARAM, COND, RUN, RESULT, IMAGE, FIELD, LIVE],
     # 버전마다 설치 폴더 이름이 다르다(예전 ORA, 현재 Synopsys) — TODO(사내검증) 실행 파일 이름
     "guess": {"lighttools": [PF + r"\Synopsys\LightTools*\bin\lt.exe", PF + r"\Optical Research Associates\LightTools*\bin\lt.exe"]}},
    # ---------- 설계 최적화 ----------
    {"id": "inspire", "name": "Altair Inspire", "vendor": "Altair", "category": "설계 최적화", "skill": "inspire-automation",
     "paths": ["altair_inspire"], "run": ["inspire_script"], "can": [PARAM, COND, TOPO, RUN, RESULT, EXPORT, IMAGE],
     "guess": {"altair_inspire": [PF + r"\Altair\*\Inspire*\Inspire.exe", PF + r"\Altair\*\Inspire*\bin\win64\Inspire.exe"]}},
    # ---------- AI 예측 ----------
    # HyperWorks(2023+)에 들어 있는 기하 딥러닝 예측. DDS 자체 예측 모델(GP·형상·필드)과 함께 쓰거나 비교한다
    {"id": "physicsai", "name": "Altair PhysicsAI", "vendor": "Altair", "category": "AI 예측", "skill": "physicsai-automation",
     "paths": ["altair_hw"], "run": ["physicsai_export", "physicsai_hw"], "can": [DATA, ML, FIELD],
     "guess": {"altair_hw": [PF + r"\Altair\*\hwdesktop\hw\bin\win64\hw.exe"]}},
]


# 설치 흔적(설치된 앱 이름·회사, 바로가기 이름, 환경변수 이름, 경로)에서 이 툴로 볼 단어 — 소문자, 단어 앞부분 일치
FIND: dict[str, list[str]] = {
    "nx": ["nx", "siemens", "ugii", "splm"], "catia": ["catia", "dassault"], "solidworks": ["solidworks"],
    "creo": ["creo", "ptc"], "ansa": ["ansa", "meta_post", "beta cae", "beta_cae"],
    "altair": ["altair", "hyperworks", "hypermesh", "optistruct", "hw_rootdir"], "simlab": ["simlab"],
    "simcenter3d": ["simcenter", "nastran"], "abaqus": ["abaqus", "simulia"], "ansys": ["ansys", "awp_root"],
    "msc_nastran": ["msc.software", "msc software", "mscsoftware", "msc_nastran", "nastran", "hexagon"], "lsdyna": ["ls-dyna", "lsdyna", "lstc", "lsprepost", "ls-prepost"],
    "starccm": ["star-ccm", "starccm"], "fluent": ["fluent"], "openfoam": [], "flotherm": ["flotherm"],
    "celsius": ["celsius", "cadence"], "comsol": ["comsol"], "cst": ["cst"], "adams": ["adams"],
    "recurdyn": ["recurdyn", "functionbay"], "motionsolve": ["motionsolve"], "moldflow": ["moldflow"],
    "moldex3d": ["moldex3d", "coretech"], "amesim": ["amesim"], "ncode": ["ncode", "designlife", "hbk"],
    "lighttools": ["lighttools", "synopsys", "optical research"], "inspire": ["inspire"], "physicsai": ["physicsai"],
}


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

    from .hidden import hidden_kwargs

    try:
        args = [paths.get(c[1:-1], c) if c.startswith("{") and c.endswith("}") else c for c in cmd]
        # 입력을 막는다: WSL이 없으면 wsl.exe가 콘솔에서 '아무 키나 누르면 설치'를 기다려 시간 초과까지 멈춘다
        return subprocess.run(args, capture_output=True, stdin=subprocess.DEVNULL, timeout=20, **hidden_kwargs()).returncode == 0
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
    """웹앱에 보낼 정적 목록(기본 경로 패턴 대신 실행 파일 이름만 — 직접 고를 때 안내용)."""
    return [{**{k: v for k, v in t.items() if k not in ("guess", "verify")},
             "files": {k: list(dict.fromkeys(p.split("\\")[-1] for p in t.get("guess", {}).get(k, []))) for k in t["paths"]}}
            for t in TOOLS]
