"""CAD/CAE 툴 자동 찾기 테스트: 기본 위치가 아닌 곳(다른 드라이브·폴더)에 설치된 툴을 설치 흔적으로 찾는지,
여러 버전 중 최신을 고르는지, 경로 우선순위(직접 고름 > 설정 파일 > 자동)와 제거·업그레이드를 따라가는지."""
import os
import subprocess
from pathlib import Path

import pytest

from agent.dds_agent import config, toolscan


def _touch(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("@echo off\n", encoding="utf-8")
    return p


@pytest.fixture()
def fake_pc(tmp_path, monkeypatch):
    """가짜 PC: drive = 고정 드라이브 루트, 그 밖은 설치된 앱 목록·바로가기·환경변수가 가리키는 설치 폴더."""
    drive = tmp_path / "drive"
    t = {
        # 다른 드라이브의 기본 구조 / 드라이브 바로 아래 / 임의 폴더 한 단계 아래
        "nx": _touch(drive / "Program Files" / "Siemens" / "NX2306" / "NXBIN" / "run_journal.exe"),
        "nx_old": _touch(drive / "Program Files" / "Siemens" / "NX 12.0" / "NXBIN" / "run_journal.exe"),
        "abaqus": _touch(drive / "SIMULIA" / "Commands" / "abaqus.bat"),
        "moldflow": _touch(drive / "CAE" / "Autodesk" / "Moldflow Insight 2025" / "bin" / "runstudy.exe"),
        # 설치된 앱 목록(설치 위치)이 가리키는 폴더 — 같은 회사 폴더의 새 버전(2024.1)까지 본다
        "hw": _touch(tmp_path / "CAE" / "Altair" / "2024" / "hwdesktop" / "hw" / "bin" / "win64" / "hw.exe"),
        "hw_new": _touch(tmp_path / "CAE" / "Altair" / "2024.1" / "hwdesktop" / "hw" / "bin" / "win64" / "hw.exe"),
        "hmbatch": _touch(tmp_path / "CAE" / "Altair" / "2024" / "hwdesktop" / "hm" / "bin" / "win64" / "hmbatch.exe"),
        "optistruct": _touch(tmp_path / "CAE" / "Altair" / "2024" / "hwsolvers" / "scripts" / "optistruct.bat"),
        # 환경변수(AWP_ROOT241)가 가리키는 폴더
        "mapdl_old": _touch(tmp_path / "Apps" / "ANSYS Inc" / "v232" / "ansys" / "bin" / "winx64" / "ANSYS232.exe"),
        "mapdl": _touch(tmp_path / "Apps" / "ANSYS Inc" / "v241" / "ansys" / "bin" / "winx64" / "ANSYS241.exe"),
        "runwb2": _touch(tmp_path / "Apps" / "ANSYS Inc" / "v241" / "Framework" / "bin" / "Win64" / "RunWB2.exe"),
        "amesim": _touch(tmp_path / "Sim" / "Simcenter" / "2304" / "Amesim" / "win64" / "AMEPython.exe"),
        # 바로가기가 실행 파일을 바로 가리킴
        "cst": _touch(tmp_path / "Tools" / "CST Studio Suite 2024" / "CST DESIGN ENVIRONMENT.exe"),
        # 관계없는 프로그램 폴더의 같은 이름 파일 — 찾으면 안 된다
        "decoy": _touch(tmp_path / "Other" / "bin" / "lt.exe"),
    }
    cands = [
        (str(tmp_path / "CAE" / "Altair" / "2024"), "HyperWorks 2024 Altair Engineering, Inc."),
        (str(tmp_path / "Apps" / "ANSYS Inc" / "v241"), "AWP_ROOT241 " + str(tmp_path / "Apps" / "ANSYS Inc" / "v241")),
        (str(tmp_path / "Sim" / "Simcenter" / "2304" / "Amesim"), "AME " + str(tmp_path / "Sim" / "Simcenter" / "2304" / "Amesim")),
        (str(t["cst"]), "CST Studio Suite 2024\\CST Studio Suite 2024.lnk " + str(t["cst"])),
        (str(tmp_path / "Other"), "Notepad++ Don Ho"),
    ]
    monkeypatch.setattr(toolscan, "fixed_drives", lambda: [str(drive) + "\\"])
    monkeypatch.setattr(toolscan, "candidates", lambda: cands)
    monkeypatch.setattr(toolscan, "fingerprint", lambda: "fp")
    cfg = tmp_path / "cfg" / "agent.toml"
    cfg.parent.mkdir()
    cfg.write_text(f"workspace_dir = '{(tmp_path / 'ws').as_posix()}'\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    return t


def test_scan_finds_tools_outside_default_locations(fake_pc):
    found = toolscan.scan()
    t = fake_pc
    assert found["nx_run_journal"] == str(t["nx"])  # NX2306 > NX 12.0
    assert found["abaqus"] == str(t["abaqus"])
    assert found["moldflow_runstudy"] == str(t["moldflow"])
    assert found["altair_hw"] == str(t["hw_new"])  # 2024.1 > 2024
    assert found["altair_hmbatch"] == str(t["hmbatch"])
    assert found["altair_optistruct"] == str(t["optistruct"])
    assert found["ansys_mapdl"] == str(t["mapdl"])  # v241 > v232
    assert found["ansys_runwb2"] == str(t["runwb2"])
    assert found["amesim_python"] == str(t["amesim"])
    assert found["cst"] == str(t["cst"])
    assert "lighttools" not in found  # 관계없는 폴더의 lt.exe


def test_version_order():
    k = toolscan._version_key
    assert k(r"C:\Program Files\Siemens\NX2306\NXBIN\run_journal.exe") > k(r"C:\Program Files\Siemens\NX 12.0\NXBIN\run_journal.exe")
    assert k(r"D:\Altair\2024.1\hwdesktop\hw\bin\win64\hw.exe") > k(r"C:\Program Files\Altair\2024\hwdesktop\hw\bin\win64\hw.exe")
    assert k(r"C:\Program Files\PTC\Creo 10.0.0.0\Parametric\bin\parametric.exe") > k(r"C:\Program Files\PTC\Creo 9.0.2.0\Parametric\bin\parametric.exe")


def test_paths_follow_pc_without_user_setup(fake_pc):
    """사용자는 아무것도 적지 않는다: 찾은 경로를 쓰고, 직접 고른 것이 우선이며, 지우거나 바꾸면 따라간다."""
    t = fake_pc
    s = config.load_settings()
    st = toolscan.refresh(s)
    assert s.tools["altair_hw"] == str(t["hw_new"]) and s.tool_source["altair_hw"] == "auto"
    assert "HyperMesh · OptiStruct · HyperView" in st["ready"]
    # Amesim 내장 파이썬 환경(AME·PATH)도 찾은 경로로 채운다
    if not os.environ.get("AME"):
        assert s.tool_env["amesim"]["AME"] == str(t["amesim"].parent.parent)
    rev = s.tools_rev

    # 웹에서 직접 고른 파일이 자동보다 우선, 이름이 다르면 경고
    info = toolscan.set_picked("altair_hw", str(t["hw"]))
    assert info["warning"] is None
    toolscan.refresh(s, max_age=10 ** 9)
    assert s.tools["altair_hw"] == str(t["hw"]) and s.tool_source["altair_hw"] == "picked" and s.tools_rev != rev
    assert toolscan.set_picked("altair_hw", str(t["optistruct"]))["warning"]
    with pytest.raises(ValueError):
        toolscan.set_picked("altair_hw", str(t["hw"].parent / "none.exe"))
    with pytest.raises(ValueError):
        toolscan.set_picked("no_such_key", str(t["hw"]))
    toolscan.set_picked("altair_hw", None)  # 자동으로 되돌리기
    toolscan.refresh(s, max_age=10 ** 9)
    assert s.tools["altair_hw"] == str(t["hw_new"])

    # 새 버전을 지우면(제거·업그레이드) 다시 찾을 때 남은 버전으로, 모두 지우면 없음으로
    t["hw_new"].unlink()
    config.apply_tools(s)  # 다시 찾기 전에도 없어진 파일은 쓰지 않는다
    assert s.tools["altair_hw"] != str(t["hw_new"])
    toolscan.refresh(s)
    assert s.tools["altair_hw"] == str(t["hw"])
    t["hw"].unlink()
    toolscan.refresh(s)
    assert "altair_hw" not in s.tool_source

    # agent.toml [tools]에 적힌 경로(관리자 배포 등)는 자동보다 우선
    cfg = config.CONFIG_PATH
    cfg.write_text(cfg.read_text(encoding="utf-8") + f"[tools]\nansys_mapdl = '{t['mapdl_old'].as_posix()}'\n", encoding="utf-8")
    s2 = config.load_settings()
    assert Path(s2.tools["ansys_mapdl"]) == t["mapdl_old"] and s2.tool_source["ansys_mapdl"] == "config"


def test_agent_follows_install_and_uninstall(fake_pc, monkeypatch):
    """실행 중인 PC Agent가 툴 설치(설치된 앱 목록이 바뀜)·제거(파일이 없어짐)를 알아채 경로를 바꾸고 서버에 알린다."""
    import asyncio

    from agent.dds_agent import main as M

    t = fake_pc
    cands, fp = [], {"v": "before"}
    monkeypatch.setattr(toolscan, "candidates", lambda: list(cands))
    monkeypatch.setattr(toolscan, "fingerprint", lambda: fp["v"])
    monkeypatch.setattr(M, "TOOL_WATCH_SEC", 0.05)
    agent = M.Agent()
    sent = []
    monkeypatch.setattr(agent, "send_threadsafe", lambda m: sent.append(m))

    async def run():
        task = asyncio.create_task(agent._watch_tools())

        async def until(cond):
            for _ in range(200):
                if cond():
                    return True
                await asyncio.sleep(0.02)
            return False

        try:
            # 시작: 기본 구조로 설치된 툴(다른 드라이브)을 찾고 알린다. 설치 위치가 따로인 Altair는 아직 모른다
            assert await until(lambda: sent)
            assert agent.s.tool_source.get("nx_run_journal") == "auto" and sent[-1]["type"] == "hello"
            assert "altair_hw" not in agent.s.tool_source
            # 설치: 설치된 앱 목록에 Altair가 생김(흔적 값이 바뀜) → 다시 찾아 경로를 정하고 알린다
            n = len(sent)
            cands.append((str(t["hw"].parents[4]), "HyperWorks 2024 Altair Engineering, Inc."))
            fp["v"] = "after-install"
            assert await until(lambda: len(sent) > n)
            assert agent.s.tools.get("altair_hw") == str(t["hw_new"]) and sent[-1]["tools_rev"] == agent.s.tools_rev
            # 제거: 실행 파일이 없어지면 흔적 값이 그대로여도 다시 찾는다
            t["hw_new"].unlink()
            t["hw"].unlink()
            assert await until(lambda: "altair_hw" not in agent.s.tool_source)
        finally:
            agent.quit = True
            task.cancel()

    asyncio.run(run())
    agent.executor.pool.shutdown(wait=False)


@pytest.mark.skipif(os.name != "nt", reason="Windows 바로가기")
def test_read_shortcut(tmp_path):
    target = _touch(tmp_path / "Altair" / "2024" / "hwdesktop" / "hw" / "bin" / "win64" / "hw.exe")
    lnk = tmp_path / "HyperWorks 2024.lnk"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); $s.TargetPath='{target}'; "
          f"$s.WorkingDirectory='{target.parent}'; $s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True)
    if r.returncode or not lnk.exists():
        pytest.skip("바로가기를 만들 수 없는 환경")
    got = toolscan.read_lnk(lnk)
    assert str(target) in got and str(target.parent) in got
