"""사용자 PC에 콘솔 창이 보이지 않는지, 숨긴 프로세스가 고아로 남지 않는지 검사한다.

설치본은 pythonw에서 돌고 개발 환경(dev_live·pytest)은 콘솔 하나를 공유해, 평소 시험으로는 창 문제가 드러나지 않는다.
그래서 실제 체인을 pythonw 아래에서 돌려 창을 직접 세고, 실행 지점은 AST로 막는다.
시험 중 포커스를 뺏지 않는다: 보이는 것은 화면 밖 1픽셀 비활성 창 하나뿐이다.
"""
import ast
import ctypes
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections import Counter
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.dds_agent import acp_runner
from agent.dds_agent.acp_runner import AcpRunner
from agent.dds_agent.config import AgentSettings, load_settings
from agent.dds_agent.executor import CaseExecutor
from agent.dds_agent.job_runner import STOPPED_MESSAGE, LocalRunner
from agent.dds_agent.main import Agent
from cae_adapters import hidden
from cae_adapters.base import resolve_cmd
from cae_adapters.hidden import KillOnExitJob, console_python, hidden_kwargs, kill_tree, run_hidden, spawn_gui_detached
from cae_adapters.tool_commands import build_command

ROOT = Path(__file__).resolve().parents[2]
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows 콘솔·Job Object 동작 시험")
PYTHONW = Path(sys.executable).with_name("pythonw.exe")

# ---------- 1. AST 린트 ----------
SCAN_DIRS = ("agent", "mcp_servers", "cae_adapters")
SPAWN_ATTRS = {"Popen", "run", "call", "check_call", "check_output", "getoutput", "getstatusoutput"}
OTHER_SPAWNS = {("os", "system"), ("os", "popen"), ("asyncio", "create_subprocess_exec"), ("asyncio", "create_subprocess_shell")}
OS_SPAWN_PREFIXES = ("spawn", "exec")  # os.spawnv·os.execv 등
LOOP_SPAWNS = {"subprocess_exec", "subprocess_shell"}  # loop.subprocess_exec

# hidden_kwargs 없이 프로세스를 띄워도 되는 곳. (파일, 함수) → (지점 수, 이유). 수를 세어 허용된 함수에 지점이 늘어나면 잡는다.
# 새 항목을 넣기 전에 정말 창이 필요한지 따진다
SPAWN_ALLOWED = {
    ("agent/launcher.py", "main"): (1, "버전 폴더 밖에 복사되어 hidden.py를 불러올 수 없다. CREATE_NO_WINDOW를 직접 준다"),
    ("agent/dds_agent/main.py", "_local_open"): (1, "탐색기: 사용자가 볼 GUI다(의도적으로 보임)"),
    ("agent/dds_agent/tray.py", "_open"): (1, "Linux 전용 xdg-open이다"),
    ("cae_adapters/hidden.py", "spawn_gui_detached"): (3, "CREATE_NO_WINDOW와 CREATE_BREAKAWAY_FROM_JOB을 직접 준다"),
}
# sys.executable로 자식을 띄우면 pythonw가 체인에 끼어 숨은 콘솔 상속이 끊긴다. 나머지는 console_python을 쓴다
SYS_EXECUTABLE_ALLOWED = {
    ("agent/launcher.py", "agent_python"): "hidden.py를 불러올 수 없어 같은 일을 직접 한다",
    ("agent/dds_agent/main.py", "_dialog"): "Tk 선택 창 프로세스. 그 아래에 콘솔 프로그램이 없다",
    ("mcp_servers/dds_live.py", "live_launch"): "연습 툴은 GUI라 일부러 pythonw로 띄운다",
    ("cae_adapters/hidden.py", "console_python"): "pythonw를 바꾸는 함수 자신이다",
}


def _lint(source: str, rel: str) -> tuple[list[tuple[str, str, int, str]], list[tuple[str, str, int]]]:
    """(hidden_kwargs가 없는 실행 지점, sys.executable 사용 지점)을 찾는다. 위치는 (파일, 함수, 줄).
    직접 부르는 곳뿐 아니라 함수로 넘기는 곳(to_thread(subprocess.run, ...), partial, 별칭)도 플래그를 확인할 수 없으니 잡는다."""
    tree = ast.parse(source)
    modules, names = {}, {}
    spawns, executables = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name in ("subprocess", "os", "asyncio", "sys"):
                    modules[a.asname or a.name] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module in ("subprocess", "os", "asyncio"):
            for a in node.names:
                if a.name == "*":
                    spawns.append((rel, "<module>", node.lineno, f"from {node.module} import *"))
                names[a.asname or a.name] = (node.module, a.name)

    def resolve(f) -> tuple[str, str] | None:
        if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.value.id in modules:
            return modules[f.value.id], f.attr
        if isinstance(f, ast.Name) and f.id in names:
            return names[f.id]
        return None

    def spawns_process(t: tuple[str, str] | None) -> bool:
        if not t:
            return False
        return (t[0] == "subprocess" and t[1] in SPAWN_ATTRS) or t in OTHER_SPAWNS or (t[0] == "os" and t[1].startswith(OS_SPAWN_PREFIXES))

    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    # 타입 표기(proc: subprocess.Popen)는 프로세스를 띄우지 않는다
    for n in ast.walk(tree):
        note = getattr(n, "annotation", None) or getattr(n, "returns", None)
        if note is not None:
            called |= {id(x) for x in ast.walk(note)}

    def walk(node, func: str):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
        if isinstance(node, ast.Call):
            t = resolve(node.func)
            loop_spawn = isinstance(node.func, ast.Attribute) and node.func.attr in LOOP_SPAWNS
            if spawns_process(t) or loop_spawn:
                hidden_arg = any(k.arg is None and isinstance(k.value, ast.Call) and getattr(k.value.func, "id", "") == "hidden_kwargs"
                                 for k in node.keywords)
                if not hidden_arg:
                    spawns.append((rel, func, node.lineno, f"{t[0]}.{t[1]}" if t else node.func.attr))
        elif isinstance(node, (ast.Attribute, ast.Name)) and id(node) not in called and spawns_process(resolve(node)):
            t = resolve(node)
            spawns.append((rel, func, node.lineno, f"{t[0]}.{t[1]} (함수로 넘김)"))
        if (isinstance(node, ast.Attribute) and node.attr == "executable" and isinstance(node.value, ast.Name)
                and modules.get(node.value.id) == "sys"):
            executables.append((rel, func, node.lineno))
        for child in ast.iter_child_nodes(node):
            walk(child, func)

    walk(tree, "<module>")
    return spawns, executables


def _scan() -> tuple[list, list, list[str]]:
    spawns, executables, files = [], [], []
    for folder in SCAN_DIRS:
        for path in (ROOT / folder).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("agent/tests/") or "__pycache__" in rel:
                continue
            files.append(rel)
            s, e = _lint(path.read_text(encoding="utf-8"), rel)
            spawns += s
            executables += e
    return spawns, executables, files


def test_lint_finds_a_missing_flag():
    """린트 자체가 잡아내는지: 이게 깨지면 아래 린트 통과는 아무 뜻이 없다"""
    bad = textwrap.dedent("""
        import subprocess as sp, sys
        from subprocess import Popen as P
        import os, asyncio
        def a(): sp.run(["x"])
        def b(): P(["x"], creationflags=0)
        def c(): sp.check_output(["x"], **{})
        def d(): os.system("x")
        async def e(): await asyncio.create_subprocess_exec("x")
        def f(): return sys.executable
        def ok(): sp.run(["x"], **hidden_kwargs())
    """)
    spawns, executables = _lint(bad, "bad.py")
    assert [s[1] for s in spawns] == ["a", "b", "c", "d", "e"]
    assert [e[1] for e in executables] == ["f"]


def test_lint_finds_indirect_spawns():
    """플래그를 확인할 수 없는 모양: 함수로 넘기기, 별칭, os.spawn·exec, 이벤트 루프, *"""
    bad = textwrap.dedent("""
        import asyncio, functools, os, subprocess
        from subprocess import Popen
        async def a(): await asyncio.to_thread(subprocess.run, ["x"])
        def b(pool): pool.submit(subprocess.run, ["x"])
        def c():
            run = subprocess.run
            run(["x"])
        def d(): functools.partial(Popen, ["x"])
        def e(): os.spawnv(os.P_NOWAIT, "x", ["x"])
        def f(): os.execv("x", ["x"])
        async def g(loop): await loop.subprocess_exec(object, "x")
        def ok(): raise subprocess.TimeoutExpired("x", 1)
    """)
    spawns, _ = _lint(bad, "bad.py")
    assert sorted({s[1] for s in spawns}) == list("abcdefg")
    star, _ = _lint("from subprocess import *\n", "bad.py")
    assert star and star[0][3] == "from subprocess import *"


def test_every_spawn_is_hidden_or_allowed():
    spawns, _, files = _scan()
    assert "agent/dds_agent/acp_runner.py" in files and "mcp_servers/dds_live.py" in files and "cae_adapters/catalog.py" in files
    counts = Counter((rel, func) for rel, func, _, _ in spawns)
    missing = [s for s in spawns if (s[0], s[1]) not in SPAWN_ALLOWED]
    assert not missing, f"창이 뜰 수 있는 실행 지점(**hidden_kwargs()를 붙이세요): {missing}"
    assert counts == Counter({key: n for key, (n, _) in SPAWN_ALLOWED.items()}), \
        f"허용 목록과 실행 지점 수가 다릅니다. 허용된 함수에 지점이 늘었다면 정말 창이 필요한지 따지세요: {counts}"


def test_every_sys_executable_is_console_python_or_allowed():
    _, executables, _ = _scan()
    found = {(rel, func) for rel, func, _ in executables}
    missing = [e for e in executables if (e[0], e[1]) not in SYS_EXECUTABLE_ALLOWED]
    assert not missing, f"pythonw가 체인에 낄 수 있습니다(console_python을 쓰세요): {missing}"
    assert found == set(SYS_EXECUTABLE_ALLOWED), f"허용 목록에 쓰이지 않는 항목이 있습니다: {set(SYS_EXECUTABLE_ALLOWED) - found}"


def _detached_popens(source: str) -> list[int]:
    """기다리지 않고 띄워 두는 Popen(스크립트가 끝나도 남는 창일 수 있다) 중 CLI의 Job에서 떼어 내지 않은 줄"""
    tree = ast.parse(source)
    bad = []
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] + [tree]:
        calls = [n for n in ast.walk(func) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "Popen" and getattr(n.func.value, "id", "") == "subprocess"]
        waited = {n.value.id for n in ast.walk(func) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                  and n.attr in ("wait", "communicate", "poll")}
        assigned = {id(n.value): n.targets[0].id for n in ast.walk(func) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
        breakaway = any(isinstance(n, ast.Constant) and n.value == "CREATE_BREAKAWAY_FROM_JOB" for n in ast.walk(func)) or \
            any(isinstance(n, ast.Attribute) and n.attr == "CREATE_BREAKAWAY_FROM_JOB" for n in ast.walk(func))
        for call in calls:
            if assigned.get(id(call)) not in waited and not breakaway:
                bad.append(call.lineno)
    return sorted(set(bad))


def test_skill_lint_finds_gui_left_in_job():
    assert _detached_popens("import subprocess\ndef a(exe):\n    subprocess.Popen([exe])\n") == [3]
    assert _detached_popens("import subprocess\ndef a(exe):\n    p = subprocess.Popen([exe])\n    return p.wait()\n") == []
    assert _detached_popens("import subprocess\ndef a(exe):\n    subprocess.Popen([exe], creationflags=subprocess.CREATE_BREAKAWAY_FROM_JOB)\n") == []


def test_skill_scripts_do_not_leave_gui_in_cli_job():
    """스킬 스크립트는 AI CLI의 Job 안에서 돈다. 그대로 띄워 둔 GUI는 Agent가 죽을 때 저장하지 않은 모델과 함께 끝난다"""
    found = {}
    for path in (ROOT / "agent-context" / "skills").rglob("*.py"):
        bad = _detached_popens(path.read_text(encoding="utf-8"))
        if bad:
            found[path.relative_to(ROOT).as_posix()] = bad
    assert not found, f"띄워 두는 프로그램은 CREATE_BREAKAWAY_FROM_JOB으로 떼어 내세요(lt_case.py의 start_gui 참고): {found}"


# ---------- 2. 단위 ----------
@pytest.fixture
def installed_python(tmp_path, monkeypatch):
    """설치본처럼 pythonw에서 도는 상태를 흉내 낸다"""
    (tmp_path / "python.exe").write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    return tmp_path


def test_console_python_replaces_pythonw(installed_python):
    assert console_python() == str(installed_python / "python.exe")
    assert console_python(str(installed_python / "PythonW.EXE")).lower() == str(installed_python / "python.exe").lower()


def test_console_python_keeps_others(tmp_path):
    assert console_python(str(tmp_path / "python.exe")) == str(tmp_path / "python.exe")
    only_w = tmp_path / "only" / "pythonw.exe"  # python.exe가 없으면 그대로 둔다
    only_w.parent.mkdir()
    only_w.write_bytes(b"")
    assert console_python(str(only_w)) == str(only_w)


def test_mcp_command_is_console_python(installed_python):
    fake = SimpleNamespace(s=SimpleNamespace(server_url="http://x", token="t", workspace_dir=Path("C:/ws")))
    servers = Agent.mcp_servers(fake, 1)
    assert servers and {s["command"] for s in servers} == {str(installed_python / "python.exe")}


def test_python_tool_command_is_console_python(installed_python):
    assert build_command("python", "a.py", [], {})[0] == str(installed_python / "python.exe")


def test_demo_agent_command_is_console_python(installed_python):
    assert AgentSettings().agent_command("demo")[0] == str(installed_python / "python.exe")


def test_launcher_runs_agent_with_console_python(installed_python):
    src = (ROOT / "agent" / "launcher.py").read_text(encoding="utf-8")
    assert "CREATE_NO_WINDOW" in src and "stdin=subprocess.DEVNULL" in src
    ns = {"__name__": "launcher_test", "__file__": str(ROOT / "agent" / "launcher.py")}
    exec(compile(src, "launcher.py", "exec"), ns)
    assert ns["agent_python"]() == str(installed_python / "python.exe")


def test_hidden_kwargs_only_on_windows():
    expected = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    assert hidden_kwargs() == expected


def test_instructions_forbid_shell_gui():
    text = (ROOT / "agent-context" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "GUI는 `dds-live.live_launch`로만" in text and "Start-Process" in text


def test_ai_is_told_timeout_kills_the_solver():
    """시간 초과가 솔버까지 끝내므로, 긴 해석을 기본값으로 잘라 라이선스 시간을 버리지 않게 한다"""
    text = (ROOT / "agent-context" / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    assert "`timeout_sec`" in text and "솔버까지 모두 끝내는 한계" in text and "dds-study.stop_cases" in text
    tree = ast.parse((ROOT / "mcp_servers" / "dds_cae.py").read_text(encoding="utf-8"))
    run_script = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_script")
    assert "솔버까지 모두 끝내고" in ast.get_docstring(run_script)
    limit = next(n for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "RUN_SCRIPT_TIMEOUT_SEC")
    assert eval(compile(ast.Expression(limit.value), "x", "eval")) >= 24 * 3600


def test_decode_output_handles_mixed_encodings(monkeypatch):
    """파이썬 드라이버의 UTF-8과 cmd의 코드 페이지가 한 출력에 섞여도 두 줄 모두 읽힌다"""
    monkeypatch.setattr(hidden, "_console_encoding", lambda: "cp949")
    mixed = "명령을 찾을 수 없습니다".encode("cp949") + b"\r\n" + "한글 UTF-8".encode("utf-8") + b"\n"
    assert hidden.decode_output(mixed) == "명령을 찾을 수 없습니다\n한글 UTF-8\n"
    assert hidden.decode_output(None) == "" and hidden.decode_output("글자") == "글자"


def test_kill_tree_escalates_on_posix(monkeypatch):
    """SIGTERM을 무시하는 솔버가 라이선스를 쥔 채 남지 않게 강제 종료까지 간다(Windows에서도 도는 모의 시험)"""
    sent = []

    class Stubborn:
        pid = 4321

        def poll(self):
            return None

        def send_signal(self, sig):
            sent.append(sig)

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("x", timeout)

    monkeypatch.setattr(hidden, "_WINDOWS", False)
    kill_tree(Stubborn())
    assert len(sent) == 2 and sent[0] == signal.SIGTERM and sent[1] == getattr(signal, "SIGKILL", signal.SIGTERM)


def test_timeout_kills_process_group_on_posix(monkeypatch):
    """직계 자식이 끝나도 그룹에 남은 자손까지 강제 종료한다(Windows에서도 도는 모의 시험)"""
    groups = []
    monkeypatch.setattr(hidden.os, "killpg", lambda pid, sig: groups.append((pid, sig)), raising=False)

    class Finished:
        pid = 4321

        def wait(self, timeout=None):
            return 0

    hidden._kill_posix(Finished(), group=True)
    assert [g[0] for g in groups] == [4321, 4321] and groups[0][1] == signal.SIGTERM


def test_working_in_background_uses_cpu_share(monkeypatch):
    """프로세스 이름으로는 CLI 도우미와 솔버를 못 가린다. 한가하면 False, 계산하면 True"""
    monkeypatch.setattr(acp_runner, "CPU_WINDOW_SEC", 0)
    runner = AcpRunner(["x"], lambda *a: None, lambda *a: None)
    assert runner.working_in_background() is False  # Job이 없으면 일도 없다

    used = {"cpu": 100.0}
    runner._job = SimpleNamespace(cpu_seconds=lambda: used["cpu"])
    now = {"t": 1000.0}
    monkeypatch.setattr(acp_runner.time, "monotonic", lambda: now["t"])
    assert runner.working_in_background() is False  # 첫 측정은 기준점만 잡는다
    now["t"] += 30
    used["cpu"] += 0.3  # CLI의 짧은 주기 작업
    assert runner.working_in_background() is False
    now["t"] += 30
    used["cpu"] += 29.0  # 솔버가 코어 하나를 쓴다
    assert runner.working_in_background() is True
    now["t"] += 30
    assert runner.working_in_background() is False  # 끝나면 곧 풀린다


def test_window_watch_catches_terminal_window_by_class(monkeypatch):
    """Windows Terminal이 기본 터미널이면 콘솔 프로세스의 자기 보고는 '안 보임'이고 창 주인도 트리 밖이다.
    창 종류로 잡는 이 경로가 살아 있어야 체인 시험의 '창 0개'가 뜻이 있다. 실제 창을 띄우지 않고 감지 논리만 확인한다."""
    windows = []
    monkeypatch.setattr(sys.modules[__name__], "_visible_windows", lambda: list(windows))
    monkeypatch.setattr(sys.modules[__name__], "_descendants", lambda roots: set(roots))
    watch = WindowWatch()
    windows.append({"hwnd": 11, "pid": 999, "class": "Notepad", "title": "사용자 창"})
    windows.append({"hwnd": 12, "pid": 998, "class": "CASCADIA_HOSTING_WINDOW_CLASS", "title": "터미널"})
    watch.poll()
    assert [h["hwnd"] for h in watch.hits] == [12]


@windows_only
def test_cmd_arg_quotes_shell_characters():
    assert hidden._cmd_arg("cae") == "cae"
    assert hidden._cmd_arg("database=D:\\a.cae") == "database=D:\\a.cae"
    assert hidden._cmd_arg("D:\\R&D\\a b.prt") == '"D:\\R&D\\a b.prt"'
    assert hidden._cmd_arg("D:\\a b\\") == '"D:\\a b\\\\"'
    assert hidden._cmd_arg("") == '""'
    for bad in ('a" & calc & "', "a\nb"):
        with pytest.raises(ValueError):
            hidden._cmd_arg(bad)


def test_spawn_gui_missing_exe_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        spawn_gui_detached([str(tmp_path / "없는파일.exe")])
    with pytest.raises(FileNotFoundError):
        spawn_gui_detached([""])


def _load_dds_live():
    sys.path.insert(0, str(ROOT / "mcp_servers"))
    import dds_live
    return dds_live


def test_live_launch_uses_detached_for_every_gui(monkeypatch, tmp_path):
    dds_live = _load_dds_live()
    calls = []
    monkeypatch.setattr(dds_live, "spawn_gui_detached", lambda cmd, env=None, cwd=None: calls.append((cmd, env, cwd)))
    monkeypatch.setattr(dds_live, "_call", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(dds_live, "_register_abaqus", lambda: None)
    monkeypatch.setattr(dds_live, "_register_hypermesh", lambda script: None)
    monkeypatch.setattr(dds_live.time, "sleep", lambda s: None)
    monkeypatch.setitem(dds_live.settings.tools, "abaqus", "abaqus.bat")
    monkeypatch.setitem(dds_live.settings.tools, "altair_hw", "hw.exe")
    monkeypatch.setitem(dds_live.settings.tools, "altair_hmbatch", "")
    monkeypatch.setattr(dds_live, "token", lambda: "tok")

    assert dds_live.live_launch("abaqus", "D:/a b.cae")["ok"]
    assert calls[-1][0] == ["abaqus.bat", "cae", "database=D:/a b.cae"]
    assert calls[-1][1]["DDS_LIVE_TOKEN"] == "tok" and "ABAQUS_PLUGIN_DIRS" in calls[-1][1]
    assert dds_live.live_launch("hypermesh")["ok"]
    assert calls[-1][0][:2] == ["hw.exe", "-tcl"]
    assert dds_live.live_launch("practice")["ok"]
    assert calls[-1][0][-1].endswith("practice_tool.py")

    exe = tmp_path / "run_journal.exe"
    (tmp_path / "ugraf.exe").write_bytes(b"")
    monkeypatch.setitem(dds_live.settings.tools, "nx_run_journal", str(exe))
    assert dds_live._nx_launch("D:/m.prt") is None
    assert calls[-1][0] == [str(tmp_path / "ugraf.exe"), "D:/m.prt"] and calls[-1][2] == str(tmp_path)


def test_live_launch_reports_missing_tool_without_waiting(monkeypatch):
    dds_live = _load_dds_live()

    def missing(*a, **k):
        raise FileNotFoundError("실행 파일을 찾을 수 없습니다: abaqus")

    monkeypatch.setattr(dds_live, "spawn_gui_detached", missing)
    monkeypatch.setattr(dds_live, "_register_abaqus", lambda: None)
    monkeypatch.setattr(dds_live, "token", lambda: "tok")
    res = dds_live.live_launch("abaqus")
    assert res["ok"] is False and "찾을 수 없습니다" in res["error"] and "dds-cae" in res["error"]


# ---------- 3. 실제 Windows 프로세스 ----------
def _kernel32():
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    return k


def _alive(pid: int) -> bool:
    k = _kernel32()
    synchronize = 0x00100000
    h = k.OpenProcess(synchronize, False, pid)
    if not h:
        return False
    try:
        return k.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: 아직 끝나지 않음
    finally:
        k.CloseHandle(h)


def _wait_dead(pids, timeout: float = 15) -> list[int]:
    end = time.time() + timeout
    while time.time() < end:
        alive = [p for p in pids if _alive(p)]
        if not alive:
            return []
        time.sleep(0.1)
    return [p for p in pids if _alive(p)]


def _wait_for(check, what: str, timeout: float = 60):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError(f"시간 초과: {what}")


def _kill_pid(pid: int):
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, stdin=subprocess.DEVNULL, **hidden_kwargs())


def _visible_windows() -> list[dict]:
    """지금 보이는 최상위 창. 다른 시험이 쓰는 ctypes 설정에 번지지 않게 WinDLL을 따로 만든다"""
    u = ctypes.WinDLL("user32", use_last_error=True)
    proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u.EnumWindows.argtypes = [proc_type, wintypes.LPARAM]
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    out = []

    def on_window(hwnd, _):
        if u.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cls, title = ctypes.create_unicode_buffer(128), ctypes.create_unicode_buffer(128)
            u.GetClassNameW(hwnd, cls, 128)
            u.GetWindowTextW(hwnd, title, 128)
            out.append({"hwnd": int(hwnd), "pid": pid.value, "class": cls.value, "title": title.value})
        return True

    u.EnumWindows(proc_type(on_window), 0)
    return out


def _descendants(roots: set[int]) -> set[int]:
    """Toolhelp로 자손 pid를 모은다(conhost도 부모 아래에 있다)"""

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    snap = k.CreateToolhelp32Snapshot(0x2, 0)
    entry, pairs = Entry(), []
    entry.dwSize = ctypes.sizeof(Entry)
    ok = k.Process32FirstW(snap, ctypes.byref(entry))
    while ok:
        pairs.append((entry.th32ProcessID, entry.th32ParentProcessID))
        ok = k.Process32NextW(snap, ctypes.byref(entry))
    k.CloseHandle(snap)
    found, grew = set(roots), True
    while grew:
        grew = False
        for pid, parent in pairs:
            if parent in found and pid not in found:
                found.add(pid)
                grew = True
    return found


class WindowWatch:
    """프로세스 트리의 보이는 창과 새로 뜬 콘솔·터미널 창을 시험 내내 잡아 둔다.
    기본 터미널이 Windows Terminal이면 창 주인이 트리 밖이라, 그 창 종류는 pid와 상관없이 잡는다."""
    TERMINAL_CLASSES = {"CASCADIA_HOSTING_WINDOW_CLASS"}

    def __init__(self, root_pid_files=lambda: set()):
        self.root_pid_files = root_pid_files
        self.baseline = {w["hwnd"] for w in _visible_windows()}
        self.roots: set[int] = set()
        self.tree: set[int] = set()
        self.hits: list[dict] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.poll()
            except OSError:
                pass
            self._stop.wait(0.05)

    def poll(self):
        self.tree |= _descendants(self.roots | self.tree | self.root_pid_files())
        for w in _visible_windows():
            if w["hwnd"] in self.baseline:
                continue
            if w["pid"] in self.tree or w["class"] in self.TERMINAL_CLASSES:
                if not any(h["hwnd"] == w["hwnd"] for h in self.hits):
                    self.hits.append(w)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)


REPORT = textwrap.dedent("""
    import ctypes, json, os, sys
    from pathlib import Path
    def report(role):
        k = ctypes.WinDLL("kernel32")
        k.GetConsoleWindow.restype = ctypes.c_void_p
        u = ctypes.WinDLL("user32")
        u.IsWindowVisible.argtypes = [ctypes.c_void_p]
        hwnd = k.GetConsoleWindow()
        rec = {"role": role, "pid": os.getpid(), "exe": Path(sys.executable).name, "visible": bool(hwnd and u.IsWindowVisible(hwnd))}
        d = Path(os.environ["CHAIN_DIR"]) / "reports"
        d.mkdir(exist_ok=True)
        (d / f"{role}.{os.getpid()}.json").write_text(json.dumps(rec), encoding="utf-8")
""")

# Codex처럼 자식에게 창 숨김을 주지 않는 CLI를 흉내 낸다
FAKE_CLI = REPORT + textwrap.dedent("""
    import subprocess
    report("cli")
    servers = []
    def reply(id_, result):
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": id_, "result": result}) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize":
            reply(msg["id"], {"protocolVersion": 1, "agentCapabilities": {"loadSession": False}})
        elif msg.get("method") == "session/new":
            for s in msg["params"]["mcpServers"]:
                env = {**os.environ, **{e["name"]: e["value"] for e in s["env"]}}
                servers.append(subprocess.Popen([s["command"], *s["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env))
            reply(msg["id"], {"sessionId": "s1"})
        elif msg.get("method") == "session/prompt":
            for p in servers:
                p.stdin.write("run\\n"); p.stdin.flush(); p.stdout.readline()
            reply(msg["id"], {"stopReason": "end_turn"})
""")

# 스킬 스크립트처럼 창 숨김 없이 솔버·배치 파일을 띄우는 MCP 서버를 흉내 낸다
FAKE_MCP = REPORT + textwrap.dedent("""
    import subprocess
    sys.path.insert(0, os.environ["CHAIN_ROOT"])
    from agent.dds_agent.job_runner import LocalRunner
    from cae_adapters.tool_commands import build_command
    report("mcp")
    d = Path(os.environ["CHAIN_DIR"])
    solver, shim = str(d / "solver.py"), str(d / "solver.cmd")
    for line in sys.stdin:
        plain = subprocess.Popen([sys.executable, solver, "flagless"])
        via_cmd = subprocess.Popen([shim, "flagless_cmd"])
        LocalRunner().run(["cmd", "/c", shim, "local_runner"], d / "run1", timeout=60)
        LocalRunner().run(build_command("python", solver, ["python_tool"], {}), d / "run2", timeout=60)
        plain.wait(); via_cmd.wait()
        sys.stdout.write("done\\n"); sys.stdout.flush()
""")

SOLVER = REPORT + textwrap.dedent("""
    import time
    report("solver_" + sys.argv[1])
    time.sleep(1.5)  # 창이 뜬다면 감시가 잡을 만큼 머문다
""")

# 설치본의 Agent처럼 pythonw에서 AcpRunner를 돌린다. pythonw에는 stderr가 없으니 결과를 파일에 남긴다
CHAIN_PARENT = REPORT + textwrap.dedent("""
    import traceback
    d = Path(os.environ["CHAIN_DIR"])
    sys.path.insert(0, os.environ["CHAIN_ROOT"])
    result = {}
    try:
        from agent.dds_agent.acp_runner import AcpRunner
        from cae_adapters.hidden import console_python
        report("agent")
        env_list = [{"name": k, "value": os.environ[k]} for k in ("CHAIN_DIR", "CHAIN_ROOT")]
        servers = [{"name": "mcp", "command": console_python(), "args": [str(d / "fake_mcp.py")], "env": env_list}]
        r = AcpRunner([str(d / "fake_cli.cmd")], lambda *a: None, lambda *a: None, dict(os.environ))
        sid = r.new_session(str(d), servers)
        result["stop"] = r.prompt(sid, "go")
        result["cli_root"] = r.proc.pid
        result["job"] = bool(r._job and r._job._handle)
        r.shutdown()
        result["ok"] = True
    except Exception:
        result["error"] = traceback.format_exc()
    (d / "result.json").write_text(json.dumps(result), encoding="utf-8")
""")


def _reports(d: Path) -> list[dict]:
    out = []
    for f in (d / "reports").glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass  # 쓰는 중이면 다음 번에 읽는다
    return out


@windows_only
@pytest.mark.skipif(not PYTHONW.exists(), reason="pythonw.exe가 없다")
def test_real_chain_under_pythonw_shows_no_window(tmp_path):
    """pythonw → AcpRunner → .cmd CLI → MCP(console_python) → 플래그 없는 솔버·.cmd, LocalRunner: 창 0개.
    끝나면 CLI 트리가 모두 사라진다. 사용자 화면을 차지하지 않으려면 이 시험은 통과가 정상이다."""
    d = tmp_path
    (d / "fake_cli.py").write_text(FAKE_CLI, encoding="utf-8")
    (d / "fake_mcp.py").write_text(FAKE_MCP, encoding="utf-8")
    (d / "solver.py").write_text(SOLVER, encoding="utf-8")
    (d / "parent.py").write_text(CHAIN_PARENT, encoding="utf-8")
    python = console_python()
    (d / "fake_cli.cmd").write_text(f'@echo off\r\n"{python}" "{d / "fake_cli.py"}" %*\r\n', encoding="utf-8")
    (d / "solver.cmd").write_text(f'@echo off\r\n"{python}" "{d / "solver.py"}" %*\r\n', encoding="utf-8")
    env = {**os.environ, "CHAIN_DIR": str(d), "CHAIN_ROOT": str(ROOT), "PYTHONIOENCODING": "utf-8"}

    with WindowWatch(lambda: {r["pid"] for r in _reports(d)}) as watch:
        parent = subprocess.Popen([str(PYTHONW), str(d / "parent.py")], env=env, cwd=str(d))
        watch.roots.add(parent.pid)
        try:
            parent.wait(timeout=180)
        finally:
            if parent.poll() is None:
                parent.kill()
        watch.poll()  # 끝난 뒤 한 번 더 본다

    result = json.loads((d / "result.json").read_text(encoding="utf-8"))
    assert result.get("ok"), result.get("error")
    assert result["stop"] == "end_turn" and result["job"] is True

    reports = _reports(d)
    roles = {r["role"] for r in reports}
    assert {"agent", "cli", "mcp", "solver_flagless", "solver_flagless_cmd", "solver_local_runner", "solver_python_tool"} <= roles, roles
    # Windows Terminal이 새 콘솔을 맡는 PC에서는 자기 보고가 창이 떠도 '안 보임'이다. 그때 잡아내는 것은 아래 WindowWatch다
    assert [r for r in reports if r["visible"]] == [], "콘솔 창이 보입니다"
    assert watch.hits == [], f"보이는 창이 있습니다: {watch.hits}"
    agent = next(r for r in reports if r["role"] == "agent")
    assert agent["exe"].lower() == "pythonw.exe"  # 설치본과 같은 조건이었는지
    assert {r["exe"].lower() for r in reports if r["role"] != "agent"} == {"python.exe"}  # 체인 중간에 pythonw가 없는지
    assert _wait_dead([r["pid"] for r in reports]) == []  # shutdown 뒤 고아가 없는지


def _write_tree_scripts(d: Path) -> Path:
    """.cmd → python → python 트리. 각 단계가 pid를 남긴다. 돌려주는 값은 .cmd 경로"""
    (d / "leaf.py").write_text(textwrap.dedent("""
        import os, sys, time
        from pathlib import Path
        Path(sys.argv[1], f"leaf.{os.getpid()}").write_text("x")
        time.sleep(120)
    """), encoding="utf-8")
    (d / "mid.py").write_text(textwrap.dedent("""
        import os, subprocess, sys, time
        from pathlib import Path
        d = Path(sys.argv[1])
        (d / f"mid.{os.getpid()}").write_text("x")
        subprocess.run([sys.executable, str(d / "leaf.py"), str(d)])
    """), encoding="utf-8")
    shim = d / "tree.cmd"
    shim.write_text(f'@echo off\r\n"{console_python()}" "{d / "mid.py"}" "{d}"\r\n', encoding="utf-8")
    return shim


def _tree_pids(d: Path) -> list[int]:
    return _wait_for(lambda: (lambda m, l: [int(p.suffix[1:]) for p in m + l] if m and l else None)(list(d.glob("mid.*[0-9]")), list(d.glob("leaf.*[0-9]"))),
                     "트리의 pid 기록")


@windows_only
def test_kill_tree_reaches_grandchildren_of_cmd(tmp_path):
    shim = _write_tree_scripts(tmp_path)
    proc = subprocess.Popen([str(shim)], stdin=subprocess.DEVNULL, **hidden_kwargs())
    pids = _tree_pids(tmp_path)
    assert all(_alive(p) for p in pids)
    try:
        kill_tree(proc)
        assert proc.poll() is not None
        assert _wait_dead(pids) == []
    finally:
        for p in pids:
            _kill_pid(p)


@windows_only
def test_terminate_alone_leaves_grandchildren(tmp_path):
    """kill_tree가 필요한 이유의 대조군: terminate는 .cmd의 cmd.exe만 끝낸다"""
    shim = _write_tree_scripts(tmp_path)
    proc = subprocess.Popen([str(shim)], stdin=subprocess.DEVNULL, **hidden_kwargs())
    pids = _tree_pids(tmp_path)
    try:
        proc.terminate()
        proc.wait(timeout=10)
        time.sleep(0.5)
        assert any(_alive(p) for p in pids)
    finally:
        for p in pids:
            _kill_pid(p)


@windows_only
def test_kill_tree_ignores_finished_process(tmp_path):
    proc = subprocess.Popen(["cmd", "/c", "exit 3"], stdin=subprocess.DEVNULL, **hidden_kwargs())
    proc.wait(timeout=10)
    kill_tree(proc)  # 끝난 프로세스의 pid를 다른 프로세스가 쓸 수 있으니 건드리지 않는다
    assert proc.returncode == 3


@windows_only
def test_job_close_kills_tree(tmp_path):
    shim = _write_tree_scripts(tmp_path)
    job = KillOnExitJob()
    proc = subprocess.Popen([str(shim)], stdin=subprocess.DEVNULL, **hidden_kwargs())
    assert job.assign(proc) is True
    pids = _tree_pids(tmp_path)
    try:
        job.close()
        assert _wait_dead(pids) == []
        assert proc.wait(timeout=10) is not None
    finally:
        for p in pids:
            _kill_pid(p)


@windows_only
def test_job_kills_cli_tree_when_agent_dies_abnormally(tmp_path):
    """Agent가 강제 종료돼도 숨은 CLI가 남지 않는다"""
    shim = _write_tree_scripts(tmp_path)
    (tmp_path / "holder.py").write_text(textwrap.dedent(f"""
        import os, subprocess, sys, time
        from pathlib import Path
        sys.path.insert(0, {str(ROOT)!r})
        from cae_adapters.hidden import KillOnExitJob, hidden_kwargs
        job = KillOnExitJob()
        p = subprocess.Popen([{str(shim)!r}], stdin=subprocess.DEVNULL, **hidden_kwargs())
        assert job.assign(p)
        Path({str(tmp_path)!r}, "holder.pid").write_text(str(os.getpid()))
        time.sleep(120)
    """), encoding="utf-8")
    holder = subprocess.Popen([console_python(), str(tmp_path / "holder.py")], stdin=subprocess.DEVNULL, **hidden_kwargs())
    pids = _tree_pids(tmp_path)
    holder_pid = int(_wait_for(lambda: (tmp_path / "holder.pid").exists() and (tmp_path / "holder.pid").read_text(), "holder pid"))
    try:
        # 트리가 아니라 Agent 역할 프로세스 하나만 끝낸다. 나머지를 정리하는 것은 Job이다
        subprocess.run(["taskkill", "/PID", str(holder_pid), "/F"], capture_output=True, stdin=subprocess.DEVNULL, **hidden_kwargs())
        assert _wait_dead(pids) == [], "Agent가 죽었는데 CLI 트리가 남았습니다"
    finally:
        holder.kill()
        for p in pids:
            _kill_pid(p)


@windows_only
def test_job_failure_does_not_block(monkeypatch):
    def boom():
        raise OSError("job 금지")

    monkeypatch.setattr(hidden, "_create_kill_on_close_job", boom)
    job = KillOnExitJob()
    assert job.assign(SimpleNamespace(pid=1)) is False
    job.close()


@windows_only
def test_local_runner_timeout_kills_solver_tree(tmp_path):
    """시간 초과 때 숨은 솔버가 라이선스를 쥔 채 남지 않고, 자손이 쥔 파이프 때문에 멈추지도 않는다"""
    shim = _write_tree_scripts(tmp_path)
    t0 = time.time()
    res = LocalRunner().run(["cmd", "/c", str(shim)], tmp_path / "run", timeout=8)
    assert res.returncode is None and "시간 초과" in res.stderr
    assert time.time() - t0 < 40
    pids = _tree_pids(tmp_path)
    assert _wait_dead(pids) == []


@windows_only
def test_run_hidden_returns_output(tmp_path):
    p = run_hidden(["cmd", "/c", "echo hello"], text=True, encoding="utf-8")
    assert p.returncode == 0 and p.stdout.strip() == "hello"
    with pytest.raises(FileNotFoundError):
        run_hidden([str(tmp_path / "없는파일.exe")])


LEAF = textwrap.dedent("""
    import os, sys, time
    from pathlib import Path
    Path(sys.argv[1], f"leaf.{os.getpid()}").write_text("x")
    time.sleep(90)
""")


def _write_leaf(d: Path) -> str:
    (d / "leaf.py").write_text(LEAF, encoding="utf-8")
    return str(d / "leaf.py")


def _leaf_pid(d: Path, timeout: float = 60) -> int:
    return _wait_for(lambda: next((int(p.suffix[1:]) for p in d.glob("leaf.*[0-9]")), None), "솔버 시작", timeout)


@windows_only
def test_timeout_kills_solver_when_wrapper_already_exited(tmp_path):
    """배치 파일이 솔버를 백그라운드로 띄우고 먼저 끝난 경우: 부모가 없어 taskkill로는 찾을 수 없다"""
    leaf = _write_leaf(tmp_path)
    bat = tmp_path / "bg.bat"
    bat.write_text(f'@echo off\r\nstart "" /b "{console_python()}" "{leaf}" "{tmp_path}"\r\nexit /b 0\r\n', encoding="utf-8")
    res = LocalRunner().run(["cmd", "/c", str(bat)], tmp_path / "run", timeout=6)
    assert res.returncode is None and "시간 초과" in res.stderr and "timeout_sec" in res.stderr
    pid = _leaf_pid(tmp_path, timeout=1)
    try:
        assert _wait_dead([pid]) == []
    finally:
        _kill_pid(pid)


@windows_only
def test_timeout_kills_solver_when_middle_process_exited(tmp_path):
    """드라이버 → (먼저 끝난 중간 프로세스) → 솔버: 트리가 끊겨 있어도 끝낸다"""
    leaf = _write_leaf(tmp_path)
    (tmp_path / "mid.py").write_text(textwrap.dedent(f"""
        import subprocess, sys
        d = subprocess.DEVNULL
        subprocess.Popen([sys.executable, {leaf!r}, {str(tmp_path)!r}], stdin=d, stdout=d, stderr=d)
    """), encoding="utf-8")
    (tmp_path / "driver.py").write_text(textwrap.dedent(f"""
        import subprocess, sys, time
        subprocess.run([sys.executable, {str(tmp_path / "mid.py")!r}])
        time.sleep(90)
    """), encoding="utf-8")
    res = LocalRunner().run([console_python(), str(tmp_path / "driver.py")], tmp_path / "run", timeout=6)
    assert res.returncode is None
    pid = _leaf_pid(tmp_path, timeout=1)
    try:
        assert _wait_dead([pid]) == []
    finally:
        _kill_pid(pid)


@windows_only
def test_normal_return_leaves_intentional_background_run(tmp_path):
    """abaqus의 백그라운드 실행처럼 일부러 뒤에 남긴 해석은 정상 종료 때 끝내지 않는다"""
    leaf = _write_leaf(tmp_path)
    (tmp_path / "driver.py").write_text(textwrap.dedent(f"""
        import subprocess, sys
        d = subprocess.DEVNULL
        subprocess.Popen([sys.executable, {leaf!r}, {str(tmp_path)!r}], stdin=d, stdout=d, stderr=d)
    """), encoding="utf-8")
    res = LocalRunner().run([console_python(), str(tmp_path / "driver.py")], tmp_path / "run", timeout=60)
    assert res.ok, res.stderr
    pid = _leaf_pid(tmp_path)
    try:
        time.sleep(1.0)
        assert _alive(pid), "정상으로 끝난 실행의 백그라운드 해석까지 끝냈습니다"
    finally:
        _kill_pid(pid)


@windows_only
def test_case_solver_dies_with_agent(tmp_path):
    """Agent가 강제 종료되면 창도 주인도 없는 솔버가 라이선스를 쥔 채 남지 않는다"""
    shim = _write_tree_scripts(tmp_path)
    (tmp_path / "agent_like.py").write_text(textwrap.dedent(f"""
        import os, sys, threading, time
        from pathlib import Path
        sys.path.insert(0, {str(ROOT)!r})
        from agent.dds_agent.job_runner import LocalRunner
        threading.Thread(target=lambda: LocalRunner().run(["cmd", "/c", {str(shim)!r}], Path({str(tmp_path / "run")!r})), daemon=True).start()
        while not list(Path({str(tmp_path)!r}).glob("leaf.*[0-9]")):
            time.sleep(0.1)
        os._exit(9)
    """), encoding="utf-8")
    agent = subprocess.Popen([console_python(), str(tmp_path / "agent_like.py")], stdin=subprocess.DEVNULL, **hidden_kwargs())
    pids = _tree_pids(tmp_path)
    try:
        assert agent.wait(timeout=60) == 9
        assert _wait_dead(pids) == [], "Agent가 죽었는데 솔버가 남았습니다"
    finally:
        for p in pids:
            _kill_pid(p)


@windows_only
def test_local_runner_stop_kills_solver_tree(tmp_path):
    """사용자가 웹앱에서 중지: 시간 제한이 없어도 솔버 트리가 끝난다"""
    shim = _write_tree_scripts(tmp_path)
    runner = LocalRunner()
    box = {}
    thread = threading.Thread(target=lambda: box.update(res=runner.run(["cmd", "/c", str(shim)], tmp_path / "run")), daemon=True)
    thread.start()
    pids = _tree_pids(tmp_path)
    runner.stop.set()
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert box["res"].returncode is None and box["res"].stderr == STOPPED_MESSAGE
    assert _wait_dead(pids) == []
    # 여러 단계를 도는 어댑터의 다음 단계는 시작하지 않는다
    again = runner.run([console_python(), "-c", "open('ran.txt','w').close()"], tmp_path / "run2")
    assert again.stderr == STOPPED_MESSAGE and not (tmp_path / "run2" / "ran.txt").exists()


def _case_job(case_id: int, script: str = "scripts/hang.py") -> dict:
    return {"type": "case.run", "case_id": case_id, "study_id": 1, "project_id": 1, "project_name": "p", "project_uid": "u", "study_name": "s",
            "source": "doe", "params": {"x": 1.0}, "variables": [], "objectives": [], "adapter": "script",
            "adapter_config": {"tool": "python", "script": script}}


@pytest.fixture
def executor(monkeypatch, tmp_path):
    monkeypatch.setenv("DDS_AGENT_CONFIG", str(tmp_path / "none.toml"))
    monkeypatch.setenv("DDS_TOKEN", "t")
    monkeypatch.setenv("DDS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("DDS_AGENT_HOME", str(tmp_path / "home"))
    settings = load_settings()
    settings.max_parallel_cases = 1
    reports: list[dict] = []
    ex = CaseExecutor(settings, reports.append)
    scripts = settings.workspace.project_dir(1, "p", "u") / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "hang.py").write_text(LEAF.replace("sys.argv[1]", repr(str(tmp_path))), encoding="utf-8")
    yield ex, reports, tmp_path
    ex.cancel(list(ex._stops))
    ex.pool.shutdown(wait=True)


@windows_only
def test_executor_cancel_stops_running_and_queued_case(executor):
    ex, reports, d = executor
    ex.submit(_case_job(1))
    ex.submit(_case_job(2))  # 동시 실행 1건이라 대기한다
    pid = _leaf_pid(d)
    assert ex.running == 1 and ex.queued == 1
    ex.cancel([1, 2])
    assert _wait_dead([pid]) == []
    _wait_for(lambda: ex.running == 0 and ex.queued == 0 and not ex._stops, "케이스 정리")
    final = {r["case_id"]: r for r in reports if r["status"] != "running"}
    assert final[1]["status"] == final[2]["status"] == "cancelled"  # 해석 실패로 보이지 않는다
    assert final[1]["error"] == final[2]["error"] == STOPPED_MESSAGE
    assert not [r for r in reports if r["case_id"] == 2 and r["status"] == "running"]  # 대기 케이스는 시작하지 않았다
    assert len(list(d.glob("leaf.*[0-9]"))) == 1


def test_executor_cancel_reports_case_it_does_not_know(executor):
    """Agent가 다시 시작해 잊은 케이스가 화면에 '실행 중'으로 영영 남지 않게 한다"""
    ex, reports, _ = executor
    ex.cancel([77], [88])
    assert reports[0] == {"type": "case.status", "case_id": 77, "status": "cancelled", "error": STOPPED_MESSAGE}
    assert reports[1]["extract"] is True and reports[1]["status"] == "complete" and STOPPED_MESSAGE in reports[1]["error"]


# 배치 파일로 받은 인자를 그대로 남긴다
SHOW_ARGS = 'import json, sys\nfrom pathlib import Path\nPath(sys.argv[1], "argv.json").write_text(json.dumps(sys.argv[2:]), encoding="utf-8")\n'


@windows_only
@pytest.mark.parametrize("folder", ["with space", "Program Files (x86)", "한글 폴더", "plain"])
def test_batch_path_with_space_and_spaced_argument(tmp_path, folder):
    """cmd /c "경로 공백" "인자 공백"은 cmd가 처음·끝 따옴표를 벗겨 공백에서 끊긴 경로를 실행하던 문제"""
    (tmp_path / "show.py").write_text(SHOW_ARGS, encoding="utf-8")
    d = tmp_path / folder
    d.mkdir()
    bat = d / "tool.bat"
    bat.write_text(f'@echo off\r\n"{console_python()}" "{tmp_path / "show.py"}" "{tmp_path}" %*\r\n', encoding="utf-8")
    command = resolve_cmd(str(bat)) + ["drv.py", "input=D:\\my dir\\a.inp", "plain", "A&B"]
    assert command[:2] == ["cmd", "/c"]  # HPC 실행기가 벗겨 내는 모양은 그대로다
    res = LocalRunner().run(command, tmp_path / "run")
    assert res.ok, res.stderr
    assert json.loads((tmp_path / "argv.json").read_text(encoding="utf-8")) == ["drv.py", "input=D:\\my dir\\a.inp", "plain", "A&B"]


@windows_only
def test_batch_called_directly_and_quote_in_argument(tmp_path):
    (tmp_path / "show.py").write_text(SHOW_ARGS, encoding="utf-8")
    d = tmp_path / "with space"
    d.mkdir()
    bat = d / "tool.cmd"
    bat.write_text(f'@echo off\r\n"{console_python()}" "{tmp_path / "show.py"}" "{tmp_path}" %*\r\n', encoding="utf-8")
    assert LocalRunner().run([str(bat), "a b"], tmp_path / "run").ok
    assert json.loads((tmp_path / "argv.json").read_text(encoding="utf-8")) == ["a b"]
    # 따옴표가 든 인자는 /s로 옮길 수 없다. 공백 없는 경로라면 전처럼 실행된다
    plain = tmp_path / "plain"
    plain.mkdir()
    bat2 = plain / "tool.bat"
    bat2.write_text(f'@echo off\r\n"{console_python()}" "{tmp_path / "show.py"}" "{tmp_path}" %*\r\n', encoding="utf-8")
    assert LocalRunner().run(["cmd", "/c", str(bat2), 'x"y'], tmp_path / "run").returncode is not None


@windows_only
def test_console_error_message_is_readable(tmp_path):
    """창이 없어 이 글자가 실패 이유를 알 유일한 곳이다. 한글 Windows의 cmd 오류가 깨지지 않는다"""
    bat = tmp_path / "bad.bat"
    bat.write_text("@echo off\r\nno_such_command_xyz\r\n", encoding="utf-8")
    res = LocalRunner().run(["cmd", "/c", str(bat)], tmp_path / "run")
    assert not res.ok and "no_such_command_xyz" in res.stderr
    assert "\ufffd" not in res.stderr and "\ufffd" not in res.stdout


# GUI 프로그램처럼 창을 띄운다. 화면 밖 1픽셀 비활성 창이라 사용자에게 보이지 않고 포커스도 뺏지 않는다.
# 첫 ShowWindow는 STARTUPINFO의 값을 따르므로, 실행 경로가 GUI를 숨기면 여기서 드러난다
FAKE_GUI = REPORT + textwrap.dedent("""
    import time
    from ctypes import wintypes
    report("gui")
    d = Path(os.environ["CHAIN_DIR"])
    (d / "argv.json").write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
    u = ctypes.WinDLL("user32")
    u.CreateWindowExW.restype = wintypes.HWND
    u.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD] + [ctypes.c_int] * 4 + [wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    ws_ex_toolwindow, ws_ex_noactivate, ws_popup, sw_shownoactivate = 0x80, 0x08000000, 0x80000000, 4
    hwnd = u.CreateWindowExW(ws_ex_toolwindow | ws_ex_noactivate, "STATIC", "dds-hidden-test", ws_popup, -32000, -32000, 1, 1, None, None, None, None)
    u.ShowWindow(hwnd, sw_shownoactivate)
    (d / "hwnd.txt").write_text(str(int(hwnd or 0)), encoding="utf-8")
    time.sleep(90)
""")


def _write_fake_gui(d: Path) -> dict:
    (d / "fake_gui.py").write_text(FAKE_GUI, encoding="utf-8")
    return {**os.environ, "CHAIN_DIR": str(d), "PYTHONIOENCODING": "utf-8"}


# venv의 python.exe는 실제 파이썬을 Job에 넣어 띄우는 대리 프로세스라, 그것을 끝내면 자식도 함께 끝난다.
# 실제 GUI 프로그램에 없는 이 동작이 시험에 끼지 않게, 남아야 하는 가짜 GUI는 설치본처럼 진짜 파이썬으로 띄운다
BASE_PYTHON = str(Path(sys.base_prefix) / "python.exe")


def _gui_report(d: Path) -> dict:
    gui = _wait_for(lambda: next((r for r in _reports(d) if r["role"] == "gui"), None), "GUI 시작")
    hwnd = int(_wait_for(lambda: (d / "hwnd.txt").exists() and (d / "hwnd.txt").read_text(), "GUI 창"))
    assert hwnd, "GUI 창을 만들지 못했습니다"
    _wait_for(lambda: [w for w in _visible_windows() if w["hwnd"] == hwnd], "GUI 창이 보임", timeout=10)
    return gui


@windows_only
def test_job_stop_keeps_user_gui_and_ends_hidden_rest(tmp_path):
    """스킬이나 셸이 그대로 띄운 GUI는 Job 안에 있다. 정상 종료(자동 업데이트 등)에서는 창이 보이는 것만 살리고, 숨은 프로세스는 끝낸다"""
    env = _write_fake_gui(tmp_path)
    leaf = _write_leaf(tmp_path)
    python = console_python()
    # 드라이버는 GUI와 숨은 솔버를 띄우고 먼저 끝난다. 부모가 없으니 트리로는 찾을 수 없다
    (tmp_path / "driver.py").write_text(textwrap.dedent(f"""
        import subprocess, sys
        subprocess.Popen([{BASE_PYTHON!r}, {str(tmp_path / "fake_gui.py")!r}])
        subprocess.Popen([{python!r}, {leaf!r}, {str(tmp_path)!r}], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    """), encoding="utf-8")
    job = KillOnExitJob()
    driver = subprocess.Popen([python, str(tmp_path / "driver.py")], env=env, stdin=subprocess.DEVNULL, **hidden_kwargs())
    assert job.assign(driver) is True
    gui = None
    pid = None
    try:
        gui = _gui_report(tmp_path)
        pid = _leaf_pid(tmp_path)
        assert job.stop() is True
        assert _wait_dead([pid]) == [], "숨은 솔버가 남았습니다"
        time.sleep(1.0)
        assert _alive(gui["pid"]), "사용자 GUI가 함께 죽었습니다"
    finally:
        job.close()
        if gui:
            _kill_pid(gui["pid"])
        if pid:
            _kill_pid(pid)


@windows_only
def test_job_stop_without_window_ends_everything(tmp_path):
    shim = _write_tree_scripts(tmp_path)
    job = KillOnExitJob()
    proc = subprocess.Popen([str(shim)], stdin=subprocess.DEVNULL, **hidden_kwargs())
    assert job.assign(proc) is True
    pids = _tree_pids(tmp_path)
    try:
        assert job.stop() is True
        assert _wait_dead(pids) == []
    finally:
        for p in pids:
            _kill_pid(p)


@windows_only
def test_job_stop_reports_when_nothing_was_assigned():
    job = KillOnExitJob()
    assert job.stop() is False  # 호출한 쪽이 kill_tree로 넘어가야 한다


@windows_only
def test_job_cpu_seconds_counts_children():
    job = KillOnExitJob()
    burner = subprocess.Popen([console_python(), "-c", "import time\nend=time.time()+1.2\nwhile time.time()<end: pass"],
                              stdin=subprocess.DEVNULL, **hidden_kwargs())
    assert job.assign(burner) is True
    try:
        burner.wait(timeout=60)
        assert job.cpu_seconds() >= 0.5  # 끝난 프로세스의 사용량도 남는다
    finally:
        job.close()
    assert job.cpu_seconds() is None


@windows_only
def test_acp_shutdown_keeps_gui_started_by_cli_shell(tmp_path):
    """AI가 셸 도구로 띄운 GUI(지침을 어겨도): 자동 업데이트로 CLI를 끝낼 때 저장하지 않은 작업이 함께 죽지 않는다"""
    env = _write_fake_gui(tmp_path)
    python = console_python()
    (tmp_path / "cli.py").write_text(textwrap.dedent(f"""
        import json, os, subprocess, sys
        for line in sys.stdin:
            msg = json.loads(line)
            if msg.get("method") == "initialize":
                print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": {{"protocolVersion": 1, "agentCapabilities": {{}}}}}}), flush=True)
            elif msg.get("method") == "session/new":
                # 입력을 동기로 기다리는 파이프를 물려주면 자식이 시작하다 멈춘다. 실제 CLI의 셸 도구도 자기 입출력을 준다
                d = subprocess.DEVNULL
                subprocess.Popen([{BASE_PYTHON!r}, {str(tmp_path / "fake_gui.py")!r}], stdin=d, stdout=d, stderr=d)
                print(json.dumps({{"jsonrpc": "2.0", "id": msg["id"], "result": {{"sessionId": "s1"}}}}), flush=True)
    """), encoding="utf-8")
    shim = tmp_path / "cli.cmd"
    shim.write_text(f'@echo off\r\n"{python}" "{tmp_path / "cli.py"}" %*\r\n', encoding="utf-8")
    runner = AcpRunner([str(shim)], lambda *a: None, lambda *a: None, env)
    gui = None
    try:
        assert runner.new_session(str(tmp_path), []) == "s1"
        root = runner.proc.pid
        gui = _gui_report(tmp_path)
        runner.shutdown()
        assert _wait_dead([root]) == [], "CLI가 남았습니다"
        time.sleep(1.0)
        assert _alive(gui["pid"]), "사용자 GUI가 CLI와 함께 죽었습니다"
    finally:
        runner.shutdown()
        if gui:
            _kill_pid(gui["pid"])


@windows_only
@pytest.mark.parametrize("breakaway", [True, False], ids=["lt_case", "control_no_breakaway"])
def test_lighttools_gui_survives_agent_death(tmp_path, breakaway):
    """lt_case.py가 띄운 LightTools: CLI Job > LocalRunner Job > 드라이버 체인에서 떼어져 Agent가 죽어도 산다.
    대조군은 같은 체인에서 그대로 띄운 것으로, Job이 그런 프로그램을 끝낸다는 것(이 시험이 실패할 수 있음)을 보인다."""
    env = _write_fake_gui(tmp_path)
    python = console_python()
    launcher = tmp_path / "lt.bat"  # lt.exe 대역
    launcher.write_text(f'@echo off\r\n"{python}" "{tmp_path / "fake_gui.py"}"\r\n', encoding="utf-8")
    d = "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL"  # 출력 파이프를 쥐면 드라이버가 끝나도 실행이 돌아오지 않는다
    start = f"lt_case.start_gui({str(launcher)!r})" if breakaway else f"subprocess.Popen([{str(launcher)!r}], {d})"
    (tmp_path / "driver.py").write_text(textwrap.dedent(f"""
        import subprocess, sys
        sys.path.insert(0, {str(ROOT / "agent-context" / "skills" / "lighttools-automation" / "scripts")!r})
        import lt_case
        {start}
    """), encoding="utf-8")
    (tmp_path / "mcp_like.py").write_text(textwrap.dedent(f"""
        import sys, time
        from pathlib import Path
        sys.path.insert(0, {str(ROOT)!r})
        from agent.dds_agent.job_runner import LocalRunner
        res = LocalRunner().run([{python!r}, {str(tmp_path / "driver.py")!r}], Path({str(tmp_path / "run")!r}), env=None)
        Path({str(tmp_path / "driver_done.txt")!r}).write_text(str(res.returncode) + res.stderr, encoding="utf-8")
        time.sleep(120)
    """), encoding="utf-8")
    job = KillOnExitJob()
    with WindowWatch() as watch:
        mcp = subprocess.Popen([python, str(tmp_path / "mcp_like.py")], env=env, stdin=subprocess.DEVNULL, **hidden_kwargs())
        watch.roots.add(mcp.pid)
        assert job.assign(mcp) is True
        gui = None
        try:
            gui = _gui_report(tmp_path)
            done = _wait_for(lambda: (tmp_path / "driver_done.txt").exists() and (tmp_path / "driver_done.txt").read_text(encoding="utf-8"), "드라이버 종료")
            assert done.startswith("0"), done
            job.close()  # Agent가 죽으면 OS가 Job을 닫는다
            assert _wait_dead([mcp.pid]) == []
            if breakaway:
                time.sleep(1.5)
                assert _alive(gui["pid"]), "LightTools가 Agent와 함께 죽었습니다"
                hwnd = int((tmp_path / "hwnd.txt").read_text())
                others = [h for h in watch.hits if h["hwnd"] != hwnd]
                assert others == [], f"콘솔 창이 보입니다: {others}"
            else:
                assert _wait_dead([gui["pid"]]) == [], "대조군: Job을 빠져나가지 않은 GUI는 Job과 함께 끝나야 합니다"
        finally:
            job.close()
            mcp.kill()
            if gui:
                _kill_pid(gui["pid"])


@windows_only
@pytest.mark.parametrize("via_bat", [False, True], ids=["exe", "bat"])
def test_gui_detached_shows_gui_hides_console_and_survives(tmp_path, via_bat):
    """MCP 역할 프로세스가 CLI Job 안에서 GUI를 띄운다 → 콘솔은 안 보이고 GUI는 보인다 → kill_tree·Job 닫기 뒤에도 산다.
    bat은 abaqus.bat처럼 배치 파일로 시작하는 툴이다. 경로의 & 와 공백이 명령으로 새지 않는지도 본다."""
    env = _write_fake_gui(tmp_path)
    python = console_python()
    target = [python, str(tmp_path / "fake_gui.py")]
    if via_bat:
        bat = tmp_path / "tool launcher.bat"
        bat.write_text(f'@echo off\r\n"{python}" "{tmp_path / "fake_gui.py"}" %*\r\n', encoding="utf-8")
        target = [str(bat)]
    marker = tmp_path / "injected.txt"
    arg = f"D:\\R&D\\a b.prt & echo x > {marker}"
    (tmp_path / "mcp_like.py").write_text(textwrap.dedent(f"""
        import json, os, sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from cae_adapters.hidden import spawn_gui_detached
        spawn_gui_detached({target!r} + [{arg!r}], env=dict(os.environ), cwd={str(tmp_path)!r})
        time.sleep(120)
    """), encoding="utf-8")
    job = KillOnExitJob()
    with WindowWatch() as watch:
        mcp = subprocess.Popen([python, str(tmp_path / "mcp_like.py")], env=env, stdin=subprocess.DEVNULL, **hidden_kwargs())
        watch.roots.add(mcp.pid)
        assert job.assign(mcp) is True
        gui = None
        try:
            gui = _wait_for(lambda: next((r for r in _reports(tmp_path) if r["role"] == "gui"), None), "GUI 시작")
            hwnd = int(_wait_for(lambda: (tmp_path / "hwnd.txt").exists() and (tmp_path / "hwnd.txt").read_text(), "GUI 창"))
            assert hwnd, "GUI 창을 만들지 못했습니다"
            mine = _wait_for(lambda: [w for w in _visible_windows() if w["hwnd"] == hwnd], "GUI 창이 보임", timeout=10)
            assert mine[0]["pid"] == gui["pid"]
            assert gui["visible"] is False, "GUI를 띄운 콘솔 창이 보입니다"

            kill_tree(mcp)  # CLI 트리를 끝내도
            job.close()  # Agent가 죽어도
            time.sleep(1.5)
            assert not _alive(mcp.pid)
            assert _alive(gui["pid"]), "사용자 GUI가 CLI와 함께 죽었습니다"
            assert json.loads((tmp_path / "argv.json").read_text(encoding="utf-8")) == [arg]
            assert not marker.exists(), "인자가 명령으로 실행됐습니다"
            console_hits = [h for h in watch.hits if h["hwnd"] != hwnd]
            assert console_hits == [], f"콘솔 창이 보입니다: {console_hits}"
        finally:
            job.close()
            mcp.kill()
            if gui:
                _kill_pid(gui["pid"])
