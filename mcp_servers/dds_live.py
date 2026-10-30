"""dds-live MCP: 사용자가 열어 둔 CAD/CAE GUI 세션에 붙어 '사람과 같은 화면'에서 함께 모델링한다 (Live Link).
프로토콜·플러그인: agent-context/live/PROTOCOL.md
- 소켓 플러그인(practice, abaqus, hypermesh): 코드를 툴의 GUI 스레드에서 실행 → 화면이 바로 바뀐다
- NX: 대기열 방식 — 코드를 큐에 넣으면 세션 안 실행기(dds_live_nx.py)가 실행한다. 실행기는 이 MCP가 열린 NX에서 스스로 띄운다
- 사용자에게 버튼·저널·플러그인 실행을 부탁하지 않는다. 붙을 수 없으면 원인을 에이전트에게 돌려주고 배치로 진행하게 한다
- 빠른 툴(live_set_params·live_view·live_undo): AI가 코드를 쓰지 않고 값만 넘긴다. NX는 세션 안 도우미(nx/ddslive_nx_ops.py)를 live_exec로 부른다
- 캡처: 플러그인의 뷰포트 이미지, 안 되면 툴 창을 화면 캡처 → 웹앱 결과(이미지)로 게시
"""
import base64
import ctypes
import json
import os
import secrets
import shutil
import socket
import sys
import time
from pathlib import Path

from _common import ROOT, api, resolve_pid, settings
from cae_adapters.hidden import spawn_gui_detached
from mcp.server import MCPServer

LIVE_DIR = ROOT / "agent-context" / "live"
NX_OPS_DIR = LIVE_DIR / "nx"
MAX_SIDE = 1600  # 게시할 캡처 이미지의 긴 변(px) — 원본(4K 창 등)을 그대로 올리면 느리다
TOKEN_FILE = Path.home() / ".dds-agent" / "live.token"

if os.name == "nt":  # 화면 배율(125%·150%)에서도 창 좌표와 실제 픽셀이 맞도록
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        ctypes.windll.user32.SetProcessDPIAware()
NX_QUEUE = Path.home() / ".dds-agent" / "live" / "nx" / "queue"
NX_ALIVE_SEC = 3  # 실행기가 이 시간 안에 표시를 남겼으면 붙어 있는 것이다
NX_WINDOW_WAIT = 240  # NX를 새로 띄웠을 때 창이 뜨기를 기다리는 시간(초). 사내 라이선스 확인에 오래 걸린다
NO_ASK = "사용자에게 버튼 클릭, 저널·스크립트 실행, 플러그인 시작을 부탁하지 마세요."

TOOLS = {
    "practice": {"port": 47800, "lang": "python", "window": "DDS 연습용 CAD", "label": "연습용 CAD (라이선스 불필요)"},
    "abaqus": {"port": 47801, "lang": "python (Abaqus 커널: mdb, session)", "window": "Abaqus/CAE", "label": "Abaqus/CAE"},
    "hypermesh": {"port": 47802, "lang": "tcl (HyperMesh *명령, hm_ 함수)", "window": "HyperMesh", "label": "HyperMesh"},
    "nx": {"port": None, "lang": "python (NXOpen: session, work_part)", "window": "NX", "label": "NX / Simcenter 3D (대기열 방식)"},
}

mcp = MCPServer(
    "dds-live",
    instructions=(
        "사용자가 열어 둔 CAD/CAE 화면에서 함께 모델링할 때 쓴다(형상 만들기, 재료·하중·경계조건·메쉬 설정, 해석 모델 준비). "
        "치수·설계변수는 live_set_params, 화면은 live_view, 되돌리기는 live_undo로 한다(코드를 쓰지 않아 빠르다). "
        "live_exec는 이것들로 안 되는 일만, 하나의 명령 묶음으로 실행한다. "
        "사용자가 툴 화면을 보고 있으므로 캡처는 큰 단계가 끝났을 때와 요청할 때만 한다(capture=true로 같은 호출에서). "
        "연결은 이 도구들이 스스로 한다. 사용자에게 리본 버튼을 누르거나 저널·스크립트를 실행하거나 플러그인을 켜 달라고 절대 부탁하지 않는다. "
        "해석 제출처럼 오래 걸리는 일은 Live Link가 아니라 dds-cae(배치)로 한다. 툴별 방법은 live-modeling 스킬을 읽는다."
    ),
)


def token() -> str:
    if os.environ.get("DDS_LIVE_TOKEN"):
        return os.environ["DDS_LIVE_TOKEN"]
    if not TOKEN_FILE.exists():
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(secrets.token_urlsafe(24), encoding="utf-8")
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def _call(tool: str, op: str, timeout: float = 120, **fields) -> dict:
    port = TOOLS[tool]["port"]
    req = {"id": int(time.time() * 1000) % 1_000_000, "op": op, "token": token(), **fields}
    # Tcl 플러그인(JSON 파서 없음)을 위해 문자열 필드는 base64로도 보낸다
    for k in ("code", "path"):
        if k in fields:
            req[f"{k}_b64"] = base64.b64encode(fields[k].encode("utf-8")).decode("ascii")
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.settimeout(timeout)
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode("utf-8"))


def _nx_listening() -> bool:
    """세션 안 실행기가 대기열을 보고 있는가"""
    try:
        return time.time() - (NX_QUEUE / "listener.alive").stat().st_mtime < NX_ALIVE_SEC
    except OSError:
        return False


def _windows_of(*exe_names: str) -> list[int]:
    """이 실행 파일들의 보이는 최상위 창"""
    if os.name != "nt":
        return []
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    names = {n.lower() for n in exe_names}
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
            if h:
                buf, n = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) and Path(buf.value).name.lower() in names:
                    found.append(hwnd)
                kernel32.CloseHandle(h)
        return True

    user32.EnumWindows(cb, 0)
    return found


def _press(*vks: int) -> None:
    """키를 차례로 누르고 거꾸로 뗀다"""
    user32 = ctypes.windll.user32
    for vk in vks:
        user32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        user32.keybd_event(vk, 0, 2, 0)


def _focus(hwnd: int) -> None:
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    _press(0x12)  # Alt를 한 번 눌러야 다른 프로그램이 창을 앞으로 가져올 수 있다
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)


def _process_dialogs(hwnd: int) -> set[int]:
    """같은 프로세스의 보이는 대화상자(#32770) 창"""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    found: set[int] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        other, cls = wintypes.DWORD(), ctypes.create_unicode_buffer(64)
        user32.GetWindowThreadProcessId(h, ctypes.byref(other))
        user32.GetClassNameW(h, cls, 64)
        if other.value == pid.value and cls.value == "#32770" and user32.IsWindowVisible(h):
            found.add(h)
        return True

    user32.EnumWindows(cb, 0)
    return found


def _file_box(dlg: int) -> int | None:
    """파일 대화상자의 파일 이름 칸"""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        cls = ctypes.create_unicode_buffer(32)
        user32.GetClassNameW(h, cls, 32)
        if cls.value == "Edit" and user32.IsWindowVisible(h):
            found.append(h)
        return True

    user32.EnumChildWindows(dlg, cb, 0)
    return found[0] if found else None


def _nx_play_journal(hwnd: int, journal: Path) -> str | None:
    """열린 NX에서 저널을 실행한다: Ctrl+U(NX Open 실행) 파일 창에 경로를 넣고 확인. 끝나면 사용자가 보던 창으로 돌려놓는다.
    TODO(사내검증): 버전별 Ctrl+U 단축키와 파일 창. 명령 창이 열려 있으면 NX가 단축키를 받지 않는다."""
    user32 = ctypes.windll.user32
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_wchar_p]
    prev, before = user32.GetForegroundWindow(), _process_dialogs(hwnd)
    _focus(hwnd)
    _press(0x11, 0x55)  # Ctrl+U
    dlg = None
    for _ in range(60):
        time.sleep(0.1)
        fresh = _process_dialogs(hwnd) - before
        if fresh:
            dlg = fresh.pop()
            break
    if not dlg:
        return "NX가 실행 창을 열지 않았습니다. 진행 중인 명령 창이 열려 있을 수 있습니다."
    box = _file_box(dlg)
    if not box:
        _press(0x1B)  # 모르는 창이면 닫는다
        return "NX 실행 창의 파일 이름 칸을 찾지 못했습니다."
    user32.SendMessageW(box, 0x000C, 0, str(journal))  # WM_SETTEXT
    time.sleep(0.1)
    user32.PostMessageW(dlg, 0x0111, 1, 0)  # WM_COMMAND IDOK
    time.sleep(0.2)
    if prev and prev != hwnd:
        _press(0x12)
        user32.SetForegroundWindow(prev)
    return None


def _nx_launch(model_path: str | None = None) -> str | None:
    """NX를 띄운다. run_journal.exe와 같은 폴더의 ugraf.exe를 쓴다."""
    run_journal = settings.tools.get("nx_run_journal") or ""
    exe = Path(run_journal).with_name("ugraf.exe") if run_journal else None
    if not exe or not exe.exists():
        return "이 PC에서 NX(ugraf.exe)를 찾지 못했습니다. 웹앱 CAD·CAE 툴 화면에서 경로를 확인하세요."
    try:
        spawn_gui_detached([str(exe)] + ([model_path] if model_path else []), cwd=str(exe.parent))
    except (OSError, ValueError) as e:
        return f"NX를 띄우지 못했습니다: {e}"
    return None


def _nx_start(model_path: str | None = None) -> str | None:
    """세션 안 실행기를 띄운다. NX가 없으면 NX부터 띄운다. 실패하면 원인을 돌려준다."""
    if os.name != "nt":
        return "NX Live Link는 Windows PC에서만 됩니다."
    wins = _windows_of("ugraf.exe")
    if not wins:
        err = _nx_launch(model_path)
        if err:
            return err
        t0 = time.time()
        while not wins and time.time() - t0 < NX_WINDOW_WAIT:
            time.sleep(1)
            wins = _windows_of("ugraf.exe")
        if not wins:
            return f"NX를 띄웠지만 {NX_WINDOW_WAIT}초 안에 창이 뜨지 않았습니다."
        time.sleep(5)  # 창이 뜬 직후에는 단축키를 받지 않는다
    return _nx_play_journal(wins[0], NX_OPS_DIR / "dds_live_nx.py")


def _nx_exec(code: str, wait_sec: int) -> dict:
    NX_QUEUE.mkdir(parents=True, exist_ok=True)
    name = f"job_{int(time.time() * 1000)}"
    # 다 쓴 뒤 이름을 바꾼다 — 대기열을 자동으로 읽는 NX가 반쯤 쓴 코드를 실행하지 않도록
    tmp = NX_QUEUE / f"{name}.tmp"
    tmp.write_text(code, encoding="utf-8")
    os.replace(tmp, NX_QUEUE / f"{name}.py")
    res_file = NX_QUEUE / f"{name}.result.json"
    start_err = None if _nx_listening() else _nx_start()
    if start_err:
        (NX_QUEUE / f"{name}.py").unlink(missing_ok=True)  # 나중에 뜻밖의 때 실행되지 않게
        return {"ok": False, "error": f"NX 세션에 붙지 못했습니다: {start_err} "
                                      f"잠시 뒤 다시 시도하거나 dds-cae 배치로 진행하세요. {NO_ASK}"}
    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if res_file.exists():
            try:
                res = json.loads(res_file.read_text(encoding="utf-8"))
            except ValueError:  # NX가 아직 쓰는 중
                time.sleep(0.05)
                continue
            res["wait_ms"] = int((time.time() - t0) * 1000)
            return res
        time.sleep(0.1)  # 1초 간격이면 단계마다 평균 0.5초씩 더 기다린다
    return {"ok": False, "pending": True, "job": name,
            "error": f"NX가 {wait_sec}초 안에 끝내지 않았습니다. 오래 걸리는 작업이거나 NX에 진행 중인 명령이 있을 수 있습니다. "
                     f"live_result로 다시 확인하세요. {NO_ASK}"}


# ---------- 창 캡처 (플러그인이 캡처를 못 할 때) ----------
def _find_window(keyword: str):
    if os.name != "nt":
        return None
    from ctypes import wintypes  # Windows 전용 — 맨 위에서 불러오면 Linux 서버의 테스트가 import부터 실패한다
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, b, n + 1)
                if keyword.lower() in b.value.lower():
                    found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def _capture_window(keyword: str, path: Path) -> str | None:
    """창 내용을 PrintWindow로 직접 그려 받는다(다른 창에 가려져도 됨). 실패하면 화면 영역 캡처."""
    hwnd = _find_window(keyword)
    if not hwnd:
        return None
    from ctypes import wintypes

    from PIL import Image, ImageGrab
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    hdc_win = user32.GetWindowDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    bmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    gdi32.SelectObject(hdc_mem, bmp)
    ok = user32.PrintWindow(hwnd, hdc_mem, 2)  # PW_RENDERFULLCONTENT: GPU로 그리는 창(OpenGL 뷰포트)도 포함
    img = None
    if ok:
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bi), 0):
            img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
            box = img.getbbox()  # DPI 배율 차이로 생기는 검은 여백 제거
            if box:
                img = img.crop(box)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)
    if img is None or img.getextrema() == ((0, 0), (0, 0), (0, 0)):  # 검은 화면이면 화면 영역 캡처로 대신
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.4)
        img = ImageGrab.grab((r.left, r.top, r.right, r.bottom), all_screens=True)
    img.save(path)
    return str(path)


def _shrink(path: Path, max_side: int = MAX_SIDE) -> None:
    """게시 전에 긴 변을 max_side로 줄인다(업로드·화면 표시가 빨라진다)."""
    from PIL import Image
    with Image.open(path) as img:
        if max(img.size) <= max_side:
            return
        small = img.copy()  # 파일을 닫은 뒤 같은 경로에 저장한다(Windows 파일 잠금)
    small.thumbnail((max_side, max_side), Image.LANCZOS)
    small.save(path)


def _grab(tool: str, path: Path) -> str | None:
    """툴 화면을 path에 저장한다. 플러그인 뷰포트 → (NX) 도우미 뷰포트 → 창 캡처 순서."""
    if TOOLS.get(tool, {}).get("port"):
        try:
            r = _call(tool, "screenshot", path=str(path))
            if r.get("ok") and r.get("path") and Path(r["path"]).exists():
                return r["path"]
        except OSError:
            pass
    elif tool == "nx":
        r = _nx_ops(f"ops.capture({str(path)!r})", 10)
        if r.get("pending") and r.get("job"):  # NX가 바로 실행하지 않으면 창 캡처로 — 늦게 돌 작업은 지운다
            (NX_QUEUE / f"{r['job']}.py").unlink(missing_ok=True)
        if r.get("ok") and path.exists():
            return str(path)
    return _capture_window(TOOLS.get(tool, {}).get("window", tool), path)


def _shot_path(pid: int, tool: str) -> Path:
    shots = settings.project_dir(pid) / "live"
    shots.mkdir(parents=True, exist_ok=True)
    return shots / f"{tool}_{time.strftime('%Y%m%d_%H%M%S')}.png"


def _publish_image(pid: int, tool: str, path: Path, title: str = "", caption: str = "",
                   flow_id: int | None = None, node_id: str | None = None) -> int:
    _shrink(path)
    rel = api.upload_image(pid, path, "live")
    a = api.post(f"/api/projects/{pid}/artifacts", {
        "kind": "image", "title": title or f"{TOOLS.get(tool, {}).get('label', tool)} 화면 {time.strftime('%H:%M:%S')}",
        "payload": {"path": rel, "caption": caption}, "flow_id": flow_id, "node_id": node_id})
    return a["id"]


# ---------- 빠른 툴: 값 → 도우미 호출 코드 ----------
def _nx_code(call: str) -> str:
    """NX 세션 안 도우미(ddslive_nx_ops)를 불러 call을 실행하는 짧은 코드. 결과(JSON 문자열)는 result에."""
    return (f"import sys\n_d = {str(NX_OPS_DIR)!r}\n"
            "if _d not in sys.path:\n    sys.path.insert(0, _d)\n"
            "import json, ddslive_nx_ops as ops\nops = ops.fresh()\n"
            f"result = {call}\n")


def _parse(res: dict, t0: float | None = None) -> dict:
    """live_exec 응답의 result(JSON 문자열)를 풀어 한 dict로. 대기 중·실행 오류는 그대로 돌려준다."""
    if not res.get("ok") or res.get("pending"):
        return res
    if not res.get("result"):
        return {**res, "ok": False, "error": "도우미 결과가 비어 있습니다. stdout을 확인하세요."}
    try:
        data = json.loads(res["result"])
    except ValueError:
        return res
    if not isinstance(data, dict):
        return res
    out = {**data}
    ms = dict(out.get("ms") or {})
    if res.get("wait_ms") is not None:
        ms["wait"] = res["wait_ms"]
    if t0 is not None:
        ms["round_trip"] = int((time.time() - t0) * 1000)
    out["ms"] = ms
    if res.get("stdout"):
        out["stdout"] = res["stdout"][-2000:]
    return out


def _nx_ops(call: str, timeout_sec: int = 120) -> dict:
    t0 = time.time()
    return _parse(live_exec("nx", _nx_code(call), timeout_sec), t0)


def _check_values(values: dict) -> str | None:
    if not isinstance(values, dict) or not values:
        return "values는 {\"이름\": 숫자} 형식이어야 합니다"
    bad = [k for k, v in values.items() if not isinstance(v, (int, float)) or isinstance(v, bool)]
    return f"숫자가 아닌 값: {', '.join(bad)}" if bad else None


def _with_capture(out: dict, tool: str, shot: Path | None, pid: int | None, title: str, caption: str,
                  flow_id: int | None, node_id: str | None) -> dict:
    """같은 실행에서 남긴 화면(없으면 창 캡처)을 게시한다."""
    if shot is None or not out.get("ok"):
        return out
    got = out.get("image") if out.get("image") and Path(out["image"]).exists() else _grab(tool, shot)
    if got:
        out["image"] = got
        out["artifact_id"] = _publish_image(pid, tool, Path(got), title, caption, flow_id, node_id)
    else:
        out.setdefault("warnings", []).append("화면을 캡처하지 못했습니다")
    return out


# ---------- 툴 ----------
@mcp.tool()
def live_status() -> list[dict]:
    """Live Link로 붙을 수 있는 툴과 지금 연결 가능 여부(플러그인 응답)를 확인한다."""
    out = []
    for name, t in TOOLS.items():
        row = {"tool": name, "label": t["label"], "language": t["lang"]}
        if t["port"]:
            try:
                r = _call(name, "ping", timeout=3)
                row.update(connected=bool(r.get("ok")), version=r.get("version"))
            except OSError:
                row.update(connected=False, how=f"플러그인이 꺼져 있습니다. live_launch(tool='{name}')로 스스로 띄웁니다")
        else:
            row.update(connected=_nx_listening(), how="대기열 방식: 첫 live_* 호출 때 열린 NX에 스스로 붙습니다(NX가 없으면 띄웁니다)")
        out.append(row)
    return out


@mcp.tool()
def live_launch(tool: str, model_path: str | None = None) -> dict:
    """툴 GUI를 Live Link와 함께 띄운다. NX는 이미 열려 있으면 그 세션에 붙는다. 사용자가 할 일은 없다."""
    env = {**os.environ, "DDS_LIVE_TOKEN": token()}
    if tool == "nx":
        if _nx_listening():
            return {"ok": True, "tool": tool, "already": True}
        err = _nx_start(model_path)
        if err:
            return {"ok": False, "error": f"{err} {NO_ASK}"}
        for _ in range(60):
            time.sleep(0.5)
            if _nx_listening():
                return {"ok": True, "tool": tool}
        return {"ok": True, "tool": tool, "note": "실행기를 띄웠습니다. 대기 표시는 아직 없습니다. live_exec를 작업마다 실행합니다."}
    # GUI는 CLI 트리와 끊어 띄운다: Agent가 다시 시작하거나 CLI를 끝내도 사용자의 저장하지 않은 모델이 죽지 않는다
    try:
        if tool == "practice":
            exe = Path(sys.executable)
            pyw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
            spawn_gui_detached([str(pyw), str(LIVE_DIR / "practice_tool.py")], env=env, cwd=str(LIVE_DIR))
        elif tool == "abaqus":
            _register_abaqus()
            plugin_dir = LIVE_DIR / "abaqus" / "abaqus_plugins"
            env["ABAQUS_PLUGIN_DIRS"] = str(plugin_dir)  # TODO(사내검증): 플러그인 경로 지정 방법(abaqus_v6.env의 plugin_central_dir 등)
            cmd = [settings.tools.get("abaqus", "abaqus"), "cae"] + ([f"database={model_path}"] if model_path else [])
            spawn_gui_detached(cmd, env=env)
        elif tool == "hypermesh":
            script = LIVE_DIR / "hypermesh" / "dds_live_hm.tcl"
            _register_hypermesh(script)
            hmbatch = settings.tools.get("altair_hmbatch") or ""
            hm = Path(hmbatch).with_name("hmopengl.exe") if hmbatch else None
            exe = str(hm) if hm and hm.exists() else settings.tools.get("altair_hw", "hw")
            # TODO(사내검증): 버전별 GUI 시작 인자(-tcl)
            spawn_gui_detached([exe, "-tcl", str(script)] + ([model_path] if model_path else []), env=env)
        else:
            return {"ok": False, "error": f"{tool}은(는) Live Link를 지원하지 않습니다. dds-cae 배치로 진행하세요. {NO_ASK}"}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": f"{tool}을(를) 띄우지 못했습니다: {e}. 웹앱 CAD·CAE 툴 화면에서 경로를 확인하거나 dds-cae 배치로 진행하세요. {NO_ASK}"}
    for _ in range(60):
        time.sleep(1)
        try:
            if _call(tool, "ping", timeout=2).get("ok"):
                return {"ok": True, "tool": tool}
        except OSError:
            pass
    return {"ok": False, "error": f"60초 안에 플러그인이 응답하지 않았습니다. 라이선스 대기일 수 있습니다. dds-cae 배치로 진행하거나 다시 시도하세요. {NO_ASK}"}


def _register_abaqus() -> None:
    """앞으로 여는 Abaqus/CAE에서 Live Link가 저절로 켜지게 사용자 플러그인 폴더에 복사한다(불러올 때 바로 시작한다)."""
    src, dst = LIVE_DIR / "abaqus" / "abaqus_plugins" / "ddsLive", Path.home() / "abaqus_plugins" / "ddsLive"
    try:
        shutil.copytree(src, dst, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    except OSError:
        pass  # 복사하지 못해도 이번 실행은 ABAQUS_PLUGIN_DIRS로 켜진다


def _register_hypermesh(script: Path) -> None:
    """앞으로 여는 HyperMesh에서 Live Link가 저절로 켜지게 hmcustom.tcl에 한 줄 넣는다(다른 내용은 그대로 둔다)."""
    f, line = Path.home() / "hmcustom.tcl", f'source "{script.as_posix()}"'
    try:
        old = f.read_text(encoding="utf-8") if f.exists() else ""
        if line not in old:
            f.write_text(old.rstrip() + ("\n" if old.strip() else "") + line + "\n", encoding="utf-8")
    except OSError:
        pass


TOOL_EXES = {"abaqus": ("abq_cae.exe", "abaqus.exe", "ABQcaeK.exe"), "hypermesh": ("hmopengl.exe", "hw.exe", "hwx.exe")}


def _call_up(tool: str, op: str, timeout: float = 120, **fields) -> dict:
    """플러그인이 꺼져 있으면 스스로 띄운 뒤 부른다."""
    try:
        return _call(tool, op, timeout, **fields)
    except OSError as e:
        first = e
    if _windows_of(*TOOL_EXES.get(tool, ())):  # 플러그인 없이 열린 세션에는 코드를 넣을 길이 없다. 사용자에게 켜 달라고 하지 않는다
        return {"ok": False, "error": f"{tool} 세션에 Live Link가 켜져 있지 않아 붙을 수 없습니다({first}). "
                                      f"저장된 모델을 live_launch(tool='{tool}', model_path=...)로 새 세션에 열거나 dds-cae 배치로 진행하세요. {NO_ASK}"}
    up = live_launch(tool)
    if not up.get("ok"):
        return up
    return _call(tool, op, timeout, **fields)


@mcp.tool()
def live_info(tool: str, all_expressions: bool = False) -> dict:
    """열려 있는 모델의 요약(파트·재료·스텝·하중·세트 등). 시작할 때 한 번 읽고, 이름은 여기서 가져온다.
    NX: 열린 파트(cad/idealized/fem/sim), 사용자가 이름 붙인 expression(값·단위·상수 여부), 피처, 바디.
    자동 이름(p12 등) expression까지 보려면 all_expressions=true."""
    if tool == "nx":
        return _nx_ops(f"ops.summary(all_expressions={bool(all_expressions)!r})", 60)
    try:
        return _call_up(tool, "info")
    except OSError as e:
        return {"ok": False, "error": f"{tool} Live Link에 연결할 수 없습니다({e}). {NO_ASK}"}


@mcp.tool()
def live_set_params(tool: str, values: dict, fem: bool = False, measure: bool = False, capture: bool = False,
                    title: str = "", caption: str = "", flow_id: int | None = None, node_id: str | None = None,
                    project_id: int | None = None, timeout_sec: int = 120) -> dict:
    """설계변수(치수)를 한 번에 바꾼다 — 코드를 쓰지 않아 빠르다. 여러 개는 한 호출에 모은다.
    tool: nx(expression 이름, 업데이트 1번, 실패하면 자동으로 되돌림, Undo 마크 1개) · practice(model 키)
    values: {"이름": 숫자} (이름은 live_info에서) · fem: Simcenter에서 Idealized·FEM(재메쉬)까지 업데이트
    measure: 질량특성(NX) · capture: 바뀐 화면을 같은 호출에서 게시. 결과의 ms로 어디서 시간이 드는지 볼 수 있다."""
    err = _check_values(values)
    if err:
        return {"ok": False, "error": err}
    pid = resolve_pid(project_id) if capture else None
    shot = _shot_path(pid, tool) if capture else None
    if tool == "nx":
        out = _nx_ops(f"ops.set_params(json.loads({json.dumps(values)!r}), fem={bool(fem)!r}, measure={bool(measure)!r}, "
                      f"capture={str(shot) if shot else None!r})", timeout_sec)
    elif tool == "practice":
        t0 = time.time()
        code = (f"import json\nmodel.update(json.loads({json.dumps(values)!r}))\n"
                f"result = json.dumps({{'ok': True, 'changed': {{k: model[k] for k in {sorted(values)!r}}}}})\n")
        out = _parse(live_exec("practice", code, timeout_sec), t0)
    else:
        return {"ok": False, "error": f"{tool}은(는) live_exec로 값을 바꾸세요(빠른 툴은 nx·practice만)."}
    return _with_capture(out, tool, shot, pid, title, caption, flow_id, node_id)


@mcp.tool()
def live_view(tool: str, orient: str | None = None, fit: bool = True, style: str | None = None,
              show: list[str] | None = None, hide: list[str] | None = None, only: list[str] | None = None,
              capture: bool = False, title: str = "", caption: str = "", flow_id: int | None = None,
              node_id: str | None = None, project_id: int | None = None) -> dict:
    """화면을 바꾼다(NX) — 코드를 쓰지 않아 빠르다.
    orient: front·back·top·bottom·left·right·iso·trimetric · fit: 화면에 맞추기
    style: shaded(음영)·edges(음영+모서리)·wire(와이어)·hidden(숨은선) · show/hide/only: 바디 이름 또는 피처 이름(예: EXTRUDE(3))
    capture: 바뀐 화면을 같은 호출에서 게시"""
    if tool != "nx":
        return {"ok": False, "error": f"{tool}은(는) live_exec로 화면을 바꾸세요(live_view는 nx만)."}
    pid = resolve_pid(project_id) if capture else None
    shot = _shot_path(pid, tool) if capture else None
    out = _nx_ops(f"ops.view(orient={orient!r}, fit={bool(fit)!r}, style={style!r}, show={show!r}, hide={hide!r}, "
                  f"only={only!r}, capture={str(shot) if shot else None!r})", 60)
    return _with_capture(out, tool, shot, pid, title, caption, flow_id, node_id)


@mcp.tool()
def live_undo(tool: str, steps: int = 1) -> dict:
    """live_set_params로 바꾼 것을 최근 것부터 steps번 되돌린다(NX)."""
    if tool != "nx":
        return {"ok": False, "error": f"{tool}은(는) 툴의 Undo를 쓰세요(live_undo는 nx만)."}
    return _nx_ops(f"ops.undo({int(steps)!r})", 60)


@mcp.tool()
def live_exec(tool: str, code: str, timeout_sec: int = 120) -> dict:
    """열려 있는 툴 세션에서 코드를 실행한다(사용자 화면이 바로 바뀐다). 결과로 돌려받을 값은 result 변수에 넣는다.
    tool: practice(python: model, redraw) · abaqus(python: mdb, session) · hypermesh(tcl) · nx(python: session, work_part — 대기열)
    치수 바꾸기·화면 바꾸기·되돌리기는 live_set_params·live_view·live_undo가 빠르다. 그 밖의 일을 하나의 명령 묶음으로 실행한다."""
    if tool not in TOOLS:
        return {"ok": False, "error": f"지원하지 않는 툴: {tool}"}
    if tool == "nx":
        return _nx_exec(code, timeout_sec)
    try:
        return _call_up(tool, "exec", timeout=timeout_sec, code=code)
    except OSError as e:
        return {"ok": False, "error": f"{tool} Live Link에 연결할 수 없습니다({e}). {NO_ASK}"}


@mcp.tool()
def live_result(job: str) -> dict:
    """NX 대기열 작업 결과 확인(live_exec가 pending을 돌려준 경우)."""
    f = NX_QUEUE / f"{job}.result.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"ok": False, "pending": True}


@mcp.tool()
def live_capture(tool: str, title: str = "", caption: str = "", publish: bool = True, flow_id: int | None = None,
                 node_id: str | None = None, project_id: int | None = None) -> dict:
    """툴 화면(뷰포트)을 캡처해 웹앱 결과로 게시한다. 사용자가 툴 화면을 보고 있으므로 큰 단계가 끝났을 때·요청할 때·보고서용으로 쓴다."""
    pid = resolve_pid(project_id)
    got = _grab(tool, _shot_path(pid, tool))
    if not got:
        return {"ok": False, "error": "화면을 캡처하지 못했습니다(툴 창을 찾지 못함)."}
    out = {"ok": True, "path": got}
    if publish:
        out["artifact_id"] = _publish_image(pid, tool, Path(got), title, caption, flow_id, node_id)
    return out


if __name__ == "__main__":
    mcp.run()
