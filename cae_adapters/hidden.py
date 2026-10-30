"""PC Agent가 띄우는 프로세스의 콘솔 창을 숨기는 공용 도구.

사용자 PC에는 AI CLI·MCP 서버·솔버의 검은 창이 보이면 안 된다. 사용자가 그 창을 닫으면 해석과 세션이 함께 죽기 때문이다.
창을 숨기면 남은 프로세스도 보이지 않게 되므로, 프로세스 트리를 끝내는 도구를 같은 곳에 둔다.
agent·mcp_servers·cae_adapters가 함께 쓰고, Linux 서버에서 불러도 깨지지 않아야 한다.
"""
from __future__ import annotations

import codecs
import locale
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

_WINDOWS = os.name == "nt"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0)
ERROR_ACCESS_DENIED = 5
# 이 글자가 있으면 cmd가 명령 구분·리디렉션으로 읽으므로 따옴표로 막는다
_CMD_SPECIAL = set(" \t&|<>()^,;%!")


def hidden_kwargs() -> dict:
    """콘솔 프로그램을 창 없이 띄우는 Popen 인자. GUI 프로그램에는 영향이 없다."""
    return {"creationflags": CREATE_NO_WINDOW} if _WINDOWS else {}


def console_python(executable: str | None = None) -> str:
    """자식 프로세스를 띄울 파이썬 경로. pythonw면 같은 폴더의 python.exe로 바꾼다.
    pythonw가 체인에 끼면 숨은 콘솔 상속이 끊겨 그 아래 솔버·배치 파일이 창을 연다."""
    exe = executable or sys.executable
    path = Path(exe)
    if path.name.lower() == "pythonw.exe":
        python = path.with_name("python.exe")
        if python.exists():
            return str(python)
    return exe


def _system32(name: str) -> str:
    # 전체 경로로 부른다: 이름만 쓰면 작업 폴더에 같은 이름의 파일이 있을 때 그것이 실행된다
    return str(Path(os.environ.get("SystemRoot") or r"C:\Windows") / "System32" / name)


def _kill_posix(proc: subprocess.Popen, group: bool, grace: float = 5) -> None:
    """SIGTERM을 무시하는 솔버가 라이선스를 쥔 채 남지 않게 SIGKILL까지 간다. group은 자기 세션을 만든 프로세스에만 쓴다."""
    import signal

    def send(sig: int) -> None:
        try:
            if group:
                os.killpg(proc.pid, sig)
            else:
                proc.send_signal(sig)
        except (ProcessLookupError, PermissionError) as e:
            log.debug("신호를 보내지 못했습니다 (pid %s): %s", proc.pid, e)

    send(signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
        if not group:
            return
    except subprocess.TimeoutExpired:
        pass
    send(getattr(signal, "SIGKILL", signal.SIGTERM))  # 그룹이면 직계 자식이 끝났어도 남은 자손을 끝낸다


def kill_tree(proc: subprocess.Popen, timeout: float = 10) -> None:
    """프로세스와 그 자손을 끝낸다. .cmd 셈은 terminate만 하면 cmd.exe만 끝나고 node가 숨은 채 남는다.
    부모가 먼저 끝나 떨어져 나간 자손은 taskkill도 못 찾는다. 그런 자손까지 끝내려면 KillOnExitJob.stop을 쓴다."""
    if proc.poll() is not None:
        return  # 이미 끝났으면 pid가 재사용됐을 수 있어 아무것도 하지 않는다
    if not _WINDOWS:
        _kill_posix(proc, group=False)
        return
    try:
        subprocess.run([_system32("taskkill.exe"), "/PID", str(proc.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, **hidden_kwargs())
        proc.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("프로세스 트리를 끝내지 못해 해당 프로세스만 끝냅니다 (pid %s): %s", proc.pid, e)
        proc.kill()


class RunStopped(subprocess.SubprocessError):
    """사용자가 실행을 중지했다(run_hidden의 stop)."""

    def __init__(self, cmd, output=None, stderr=None):
        super().__init__(f"중지됨: {cmd}")
        self.cmd, self.stdout, self.stderr = cmd, output, stderr


def _is_batch(path: str) -> bool:
    return path.lower().endswith((".bat", ".cmd"))


def _batch_spawn(cmd, env: dict | None) -> tuple[str | None, object]:
    """배치 파일을 부를 (실행 파일, 명령)을 만든다. 배치 파일이 아니면 그대로 둔다.
    cmd /c "경로 공백" "인자 공백"은 cmd가 처음과 끝 따옴표를 벗겨 공백 앞에서 끊긴 경로를 실행한다. /s로 바깥 한 쌍만 벗기게 한다.
    명령 목록은 ["cmd", "/c", 경로, ...] 그대로 두어야 HPC 실행기가 감싸기를 벗길 수 있어, 띄우는 이 자리에서만 바꾼다."""
    if not _WINDOWS or not isinstance(cmd, (list, tuple)):
        return None, cmd
    args = [str(c) for c in cmd]
    if len(args) >= 3 and args[0].lower() in ("cmd", "cmd.exe") and args[1].lower() == "/c":
        args = args[2:]
    if not args or not _is_batch(args[0]):
        return None, cmd
    try:
        line = " ".join([f'"{args[0]}"', *[_cmd_arg(a) for a in args[1:]]])
    except ValueError:  # 인자에 따옴표가 있으면 /s로 옮길 수 없다. 전과 같은 방식으로 띄운다
        return None, cmd
    return _comspec(env), f'cmd /d /s /c "{line}"'


def run_hidden(cmd, timeout: float | None = None, stop: threading.Event | None = None, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run(capture_output)과 같되 창이 없고, 시간 초과·중지 때 자손까지 끝낸다.
    subprocess.run은 시간 초과에 직계 자식만 끝내므로, 숨은 솔버가 라이선스를 쥔 채 남고 파이프 대기로 멈출 수 있다.
    timeout은 그래서 솔버까지 끝내는 한계다. stop이 켜지면 RunStopped, 시간이 다 되면 TimeoutExpired를 낸다.

    실행마다 Job에 넣는다. 부모가 먼저 끝나 떨어져 나간 자손은 taskkill로 찾을 수 없고, 실행을 맡긴 Agent가 죽어도 솔버가
    창 없이 남으면 안 되기 때문이다. 정상으로 끝나면 Job을 풀어 준다. 일부러 뒤에 남긴 실행(abaqus의 백그라운드 등)은 끝내지 않는다."""
    kwargs.setdefault("stdin", subprocess.DEVNULL)  # 숨은 콘솔에서 pause 등이 입력을 기다리면 보이지 않은 채 멈춘다
    if not _WINDOWS:
        kwargs.setdefault("start_new_session", True)  # 자손까지 한 그룹으로 끝내기 위해서다
    app, line = _batch_spawn(cmd, kwargs.get("env"))
    if app:
        kwargs["executable"] = app
    job = KillOnExitJob()
    proc = subprocess.Popen(line, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs, **hidden_kwargs())
    job.assign(proc)
    end = time.monotonic() + timeout if timeout is not None else None
    try:
        while True:
            waits = ([1.0] if stop else []) + ([max(0.0, end - time.monotonic())] if end is not None else [])
            try:
                out, err = proc.communicate(timeout=min(waits) if waits else None)
                break
            except subprocess.TimeoutExpired:
                stopped = bool(stop and stop.is_set())
                if not stopped and (end is None or time.monotonic() < end):
                    continue
                out, err = _end_run(proc, job)
                if stopped:
                    raise RunStopped(cmd, out, err) from None
                raise subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err) from None
    except BaseException:
        job.close()
        raise
    job.release()
    return subprocess.CompletedProcess(cmd, proc.returncode, out, err)


def _end_run(proc: subprocess.Popen, job: "KillOnExitJob"):
    """실행을 끝내고 그때까지의 출력을 돌려준다."""
    if not job.stop():
        if _WINDOWS:
            kill_tree(proc)
        else:
            _kill_posix(proc, group=True)
    try:
        return proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:  # 남겨 둔 GUI가 파이프를 쥐고 있으면 더 기다리지 않는다
        return None, None


def _console_encoding() -> str:
    """cmd와 배치 파일이 내는 글자의 인코딩(숨은 콘솔도 OEM 코드 페이지를 쓴다. 한글 Windows는 cp949)."""
    if _WINDOWS:
        try:
            import ctypes

            name = f"cp{ctypes.WinDLL('kernel32').GetOEMCP()}"
            codecs.lookup(name)
            return name
        except (OSError, LookupError, AttributeError) as e:
            log.debug("콘솔 코드 페이지를 알지 못했습니다: %s", e)
    return locale.getpreferredencoding(False) or "utf-8"


def decode_output(data: bytes | str | None) -> str:
    """솔버 출력을 글자로 바꾼다. 창이 없어 이 글자가 실패 이유를 알 유일한 곳이므로 깨지면 안 된다.
    파이썬 드라이버는 UTF-8, cmd·배치 파일은 콘솔 코드 페이지로 내어 한 출력에 섞이므로 줄마다 판단한다."""
    if not data:
        return ""
    if isinstance(data, str):
        return data
    fallback = _console_encoding()
    lines = []
    for raw in data.split(b"\n"):
        try:
            lines.append(raw.decode("utf-8"))
        except UnicodeDecodeError:
            lines.append(raw.decode(fallback, "replace"))
    return "\n".join(lines).replace("\r\n", "\n")


class KillOnExitJob:
    """Agent가 비정상 종료해도 숨은 프로세스가 고아로 쌓이지 않게 하는 Windows Job Object.
    핸들이 닫히면(Agent가 죽으면 OS가 닫는다) 안의 프로세스가 모두 끝난다. 사용자 GUI가 빠져나가도록 BREAKAWAY_OK를 준다.
    실패해도 실행을 막지 않고 조용히 넘어간다. 다른 OS에서는 아무 일도 하지 않는다."""

    def __init__(self):
        self._handle = None
        self._assigned = False
        if not _WINDOWS:
            return
        try:
            self._handle = _create_kill_on_close_job()
        except Exception as e:
            log.debug("Job Object를 만들지 못했습니다: %s", e)

    def assign(self, proc: subprocess.Popen) -> bool:
        if not self._handle:
            return False
        try:
            self._assigned = _assign_to_job(self._handle, proc.pid)
        except Exception as e:
            log.debug("Job Object에 넣지 못했습니다 (pid %s): %s", proc.pid, e)
        return self._assigned

    def close(self) -> None:
        """안에 남은 프로세스까지 모두 끝낸다(창이 있어도)"""
        handle, self._handle = self._handle, None
        if handle:
            try:
                _kernel32().CloseHandle(handle)
            except Exception as e:
                log.debug("Job Object를 닫지 못했습니다: %s", e)

    def release(self) -> None:
        """안의 프로세스는 그대로 두고 Job만 닫는다"""
        if self._handle:
            try:
                _set_job_limits(self._handle, JOB_BREAKAWAY_OK)
            except Exception as e:
                log.debug("Job 설정을 바꾸지 못했습니다: %s", e)
        self.close()

    def stop(self) -> bool:
        """안의 프로세스를 끝내고 Job을 닫는다. 넣어 둔 프로세스가 없어 할 일이 없었으면 False.
        창이 보이는 프로세스와 그 자손은 남긴다. 사용자가 저장하지 않은 모델일 수 있고, 보이니 사용자가 직접 닫을 수 있다."""
        if not (self._handle and self._assigned):
            self.close()
            return False
        try:
            keep: set[int] = set()
            for _ in range(3):  # 끝내는 사이에 새로 뜬 자식까지
                pids = _job_pids(self._handle)
                keep, consoles = _windowed_and_consoles(pids)
                ended = set(pids) - keep - consoles  # 콘솔 호스트를 끝내면 GUI가 쓰는 콘솔이 사라져 GUI도 끝난다. 쓰는 곳이 없으면 스스로 끝난다
                if not keep or not ended:
                    break
                for pid in ended:
                    _terminate_pid(pid)
            if keep:
                log.info("창이 보이는 프로세스는 끝내지 않습니다: %s", sorted(keep))
                self.release()
                return True
        except Exception as e:
            log.debug("Job 안의 프로세스를 가려 끝내지 못해 모두 끝냅니다: %s", e)
        self.close()  # 남길 것이 없으면 닫는 것으로 모두 끝난다
        return True

    def cpu_seconds(self) -> float | None:
        """안의 프로세스가 지금까지 쓴 CPU 시간(끝난 프로세스 포함). 알 수 없으면 None"""
        if not self._handle:
            return None
        try:
            return _job_cpu_seconds(self._handle)
        except Exception as e:
            log.debug("Job의 CPU 시간을 읽지 못했습니다: %s", e)
            return None

    def __del__(self):
        self.close()


def _kernel32():
    import ctypes
    from ctypes import wintypes

    # 전역 windll을 쓰지 않는다: 여기서 정한 함수 서명이 다른 모듈의 호출에 번지지 않게
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    k.QueryInformationJobObject.restype = wintypes.BOOL
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.AssignProcessToJobObject.restype = wintypes.BOOL
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateProcess.restype = wintypes.BOOL
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return k


JOB_BREAKAWAY_OK, JOB_KILL_ON_CLOSE = 0x800, 0x2000


def _set_job_limits(handle, flags: int) -> None:
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]

    class Extended(ctypes.Structure):
        _fields_ = [("Basic", Basic), ("Io", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    extended_limit_information = 9
    info = Extended()
    info.Basic.LimitFlags = flags
    if not _kernel32().SetInformationJobObject(handle, extended_limit_information, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())


def _create_kill_on_close_job():
    import ctypes

    k = _kernel32()
    handle = k.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        _set_job_limits(handle, JOB_BREAKAWAY_OK | JOB_KILL_ON_CLOSE)
    except Exception:
        k.CloseHandle(handle)
        raise
    return handle


def _assign_to_job(job, pid: int) -> bool:
    process_set_quota, process_terminate = 0x0100, 0x0001
    k = _kernel32()
    handle = k.OpenProcess(process_set_quota | process_terminate, False, pid)
    if not handle:
        return False
    try:
        return bool(k.AssignProcessToJobObject(job, handle))
    finally:
        k.CloseHandle(handle)


def _job_pids(job) -> list[int]:
    import ctypes
    from ctypes import wintypes

    class ProcessList(ctypes.Structure):
        _fields_ = [("Assigned", wintypes.DWORD), ("Listed", wintypes.DWORD), ("Ids", ctypes.c_size_t * 4096)]

    basic_process_id_list = 3
    info = ProcessList()
    if not _kernel32().QueryInformationJobObject(job, basic_process_id_list, ctypes.byref(info), ctypes.sizeof(info), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return [int(info.Ids[i]) for i in range(info.Listed)]


def _job_cpu_seconds(job) -> float:
    import ctypes
    from ctypes import wintypes

    class Accounting(ctypes.Structure):
        _fields_ = [("User", ctypes.c_int64), ("Kernel", ctypes.c_int64), ("PeriodUser", ctypes.c_int64), ("PeriodKernel", ctypes.c_int64),
                    ("PageFaults", wintypes.DWORD), ("Total", wintypes.DWORD), ("Active", wintypes.DWORD), ("Terminated", wintypes.DWORD)]

    basic_accounting_information = 1
    info = Accounting()
    if not _kernel32().QueryInformationJobObject(job, basic_accounting_information, ctypes.byref(info), ctypes.sizeof(info), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return (info.User + info.Kernel) / 1e7  # 100ns 단위


def _pids_with_visible_window(pids: list[int]) -> set[int]:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    wanted, found = set(pids), set()

    def on_window(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in wanted:
                found.add(pid.value)
        return True

    user32.EnumWindows(callback(on_window), 0)
    return found


def _windowed_and_consoles(pids: list[int]) -> tuple[set[int], set[int]]:
    """(창이 보이는 프로세스와 그 자손, 콘솔 호스트). 라이선스 도우미 같은 GUI 툴의 자식도 함께 남긴다."""
    table = _process_table()
    consoles = {pid for pid, _, exe in table if exe.lower() == "conhost.exe"}
    roots = _pids_with_visible_window(pids)
    if not roots:
        return set(), consoles
    found, grew = set(roots), True
    while grew:
        grew = False
        for pid, parent, _ in table:
            if parent in found and pid not in found:
                found.add(pid)
                grew = True
    return found, consoles


def _process_table() -> list[tuple[int, int, str]]:
    """(pid, 부모 pid, 실행 파일 이름)"""
    import ctypes
    from ctypes import wintypes

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
    snapshot_process = 0x2
    snap = k.CreateToolhelp32Snapshot(snapshot_process, 0)
    entry, table = Entry(), []
    entry.dwSize = ctypes.sizeof(Entry)
    more = k.Process32FirstW(snap, ctypes.byref(entry))
    while more:
        table.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
        more = k.Process32NextW(snap, ctypes.byref(entry))
    k.CloseHandle(snap)
    return table


def _terminate_pid(pid: int) -> None:
    k = _kernel32()
    process_terminate = 0x0001
    handle = k.OpenProcess(process_terminate, False, pid)
    if handle:
        try:
            k.TerminateProcess(handle, 1)
        finally:
            k.CloseHandle(handle)


def _cmd_arg(arg: str) -> str:
    """cmd와 대상 프로그램이 모두 한 인자로 읽게 따옴표를 붙인다. list2cmdline은 & 같은 글자를 그대로 두어 경로가 명령으로 읽힌다."""
    if '"' in arg or "\n" in arg or "\r" in arg:  # 파일 경로에 올 수 없는 글자다. cmd 명령으로 빠져나가지 못하게 막는다
        raise ValueError(f"실행 인자에 쓸 수 없는 글자가 있습니다: {arg!r}")
    if arg and not (_CMD_SPECIAL & set(arg)):
        return arg
    trailing = len(arg) - len(arg.rstrip("\\"))  # 닫는 따옴표 앞의 \는 대상 프로그램이 이스케이프로 읽는다
    return f'"{arg}{chr(92) * trailing}"'


def _comspec(env: dict | None) -> str:
    return (env or os.environ).get("ComSpec") or _system32("cmd.exe")


def _find_exe(exe: str, env: dict | None) -> str | None:
    if not exe:
        return None
    if Path(exe).is_file():
        return exe
    return shutil.which(exe, path=(env or os.environ).get("PATH"))


# 프로그램만 띄우고 바로 끝나는 중간 프로세스. 부모가 사라지므로 taskkill /T의 부모 추적에 걸리지 않는다
_DETACH_CODE = ("import subprocess as s,sys;d=s.DEVNULL;"
                "s.Popen(sys.argv[2],executable=sys.argv[1],stdin=d,stdout=d,stderr=d,creationflags=s.CREATE_NO_WINDOW)")


def spawn_gui_detached(cmd: list[str], env: dict | None = None, cwd: str | None = None) -> subprocess.Popen:
    """사용자가 볼 GUI 프로그램(NX·Abaqus·HyperMesh·뷰어)을 CLI 트리와 끊어 띄운다. 콘솔은 숨고 GUI만 보인다.
    Agent가 업데이트로 다시 시작해도, CLI를 끝내도 사용자의 저장하지 않은 모델이 함께 죽지 않게 한다.
    입출력을 닫아 MCP의 stdout 파이프도 물려주지 않는다. 실행 파일이 없으면 FileNotFoundError.

    cmd의 start를 쓰지 않는다: 배치 파일이면 start가 인자를 cmd /K로 한 번 더 해석해, 공백 있는 경로와 따옴표 인자가 깨진다.
    파이썬 중간 프로세스는 한 번만 해석되고 실패도 종료 코드로 돌아온다(start는 오류 대화상자를 띄운다)."""
    args = [str(c) for c in cmd]
    exe = _find_exe(args[0], env) if args else None
    if not exe:
        raise FileNotFoundError(f"실행 파일을 찾을 수 없습니다: {args[0] if args else ''}")
    streams = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if not _WINDOWS:
        return subprocess.Popen([exe, *args[1:]], env=env, cwd=cwd, start_new_session=True, **streams)

    line = " ".join([f'"{exe}"', *[_cmd_arg(a) for a in args[1:]]])
    app = exe
    if _is_batch(exe):
        # /s: 바깥 따옴표 한 쌍만 벗긴다. 안쪽 인자의 따옴표가 그대로 남아 & 같은 글자가 명령으로 새지 않는다
        app = _comspec(env)
        line = f'cmd /d /s /c "{line}"'
    # -I -S: 중간 프로세스는 표준 라이브러리만 쓴다. 작업 폴더의 같은 이름 파일을 불러오지 않게 한다
    detach = [console_python(), "-I", "-S", "-c", _DETACH_CODE, app, line]
    try:
        proc = subprocess.Popen(detach, env=env, cwd=cwd, **streams, creationflags=CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB)
    except PermissionError as e:
        if getattr(e, "winerror", None) != ERROR_ACCESS_DENIED:
            raise
        # 바깥 Job이 빠져나가기를 금지한 PC: GUI를 안 띄우는 것보다 CLI와 수명을 같이하는 편이 낫다
        log.warning("Job에서 분리하지 못해 그대로 띄웁니다: %s", e)
        proc = subprocess.Popen(detach, env=env, cwd=cwd, **streams, creationflags=CREATE_NO_WINDOW)
    try:
        code = proc.wait(timeout=30)  # 중간 프로세스는 바로 끝난다. 보안 검사로 늦으면 기다리지 않고 넘어간다
    except subprocess.TimeoutExpired:
        return proc
    if code:
        raise OSError(f"프로그램을 띄우지 못했습니다(종료 코드 {code}): {exe}")
    return proc
