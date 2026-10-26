"""dds-live MCP: 사용자가 열어 둔 CAD/CAE GUI 세션에 붙어 '사람과 같은 화면'에서 함께 모델링한다 (Live Link).
프로토콜·플러그인: agent-context/live/PROTOCOL.md
- 소켓 플러그인(practice, abaqus, hypermesh): 코드를 툴의 GUI 스레드에서 실행 → 화면이 바로 바뀐다
- NX: 대기열 방식 — 저널을 큐에 넣고 사용자가 NX에서 'DDS 실행'(dds_live_nx.py)을 누르면 열린 세션에서 실행
- 캡처: 플러그인의 뷰포트 이미지, 안 되면 툴 창을 화면 캡처 → 웹앱 결과(이미지)로 게시
"""
import base64
import ctypes
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from _common import ROOT, api, resolve_pid, settings
from mcp.server import MCPServer

LIVE_DIR = ROOT / "agent-context" / "live"
TOKEN_FILE = Path.home() / ".dds-agent" / "live.token"

if os.name == "nt":  # 화면 배율(125%·150%)에서도 창 좌표와 실제 픽셀이 맞도록
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        ctypes.windll.user32.SetProcessDPIAware()
NX_QUEUE = Path.home() / ".dds-agent" / "live" / "nx" / "queue"

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
        "한 번에 작은 단계만 실행하고, 단계마다 live_capture로 화면을 게시해 사용자가 확인하게 한다. "
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


def _nx_exec(code: str, wait_sec: int) -> dict:
    NX_QUEUE.mkdir(parents=True, exist_ok=True)
    name = f"job_{int(time.time() * 1000)}"
    (NX_QUEUE / f"{name}.py").write_text(code, encoding="utf-8")
    res_file = NX_QUEUE / f"{name}.result.json"
    t0 = time.time()
    while time.time() - t0 < wait_sec:
        if res_file.exists():
            return json.loads(res_file.read_text(encoding="utf-8"))
        time.sleep(1)
    return {"ok": False, "pending": True, "job": name,
            "error": "NX에서 아직 실행하지 않았습니다. 사용자에게 NX의 'DDS 실행' 버튼(dds_live_nx.py)을 눌러 달라고 안내한 뒤 live_result로 확인하세요."}


# ---------- 창 캡처 (플러그인이 캡처를 못 할 때) ----------
def _find_window(keyword: str):
    if os.name != "nt":
        return None
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
                row.update(connected=False, how=f"플러그인 미실행 — live_launch(tool='{name}') 또는 live-modeling 스킬의 시작 방법 참고")
        else:
            row.update(connected=None, how="대기열 방식: live_exec 후 사용자가 NX에서 'DDS 실행'을 누름")
        out.append(row)
    return out


@mcp.tool()
def live_launch(tool: str, model_path: str | None = None) -> dict:
    """툴 GUI를 Live Link 플러그인과 함께 띄운다(가능한 툴만). 이미 열려 있으면 그 화면에서 플러그인만 시작하도록 사용자에게 안내한다."""
    env = {**os.environ, "DDS_LIVE_TOKEN": token()}
    if tool == "practice":
        exe = Path(sys.executable)
        pyw = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").exists() else exe
        subprocess.Popen([str(pyw), str(LIVE_DIR / "practice_tool.py")], env=env, cwd=str(LIVE_DIR))
    elif tool == "abaqus":
        plugin_dir = LIVE_DIR / "abaqus" / "abaqus_plugins"
        env["ABAQUS_PLUGIN_DIRS"] = str(plugin_dir)  # TODO(사내검증): 플러그인 경로 지정 방법(abaqus_v6.env의 plugin_central_dir 등)
        cmd = [settings.tools.get("abaqus", "abaqus"), "cae"] + ([f"database={model_path}"] if model_path else [])
        subprocess.Popen(cmd, env=env, shell=os.name == "nt")
    else:
        return {"ok": False, "how": f"{tool}은(는) 사용자가 직접 열고 플러그인을 시작해야 합니다. live-modeling 스킬의 '{tool}' 절을 안내하세요."}
    for _ in range(60):
        time.sleep(1)
        try:
            if _call(tool, "ping", timeout=2).get("ok"):
                return {"ok": True, "tool": tool}
        except OSError:
            pass
    return {"ok": False, "error": "플러그인 응답이 없습니다. 툴 창이 열렸는지 확인하세요."}


@mcp.tool()
def live_info(tool: str) -> dict:
    """열려 있는 모델의 요약(파트·재료·스텝·하중·세트 등)."""
    if tool == "nx":
        return _nx_exec("p = work_part\nresult = {'part': p.Leaf if p else None, 'bodies': [b.JournalIdentifier for b in p.Bodies] if p else []}", 60)
    return _call(tool, "info")


@mcp.tool()
def live_exec(tool: str, code: str, timeout_sec: int = 120) -> dict:
    """열려 있는 툴 세션에서 코드를 실행한다(사용자 화면이 바로 바뀐다). 결과로 돌려받을 값은 result 변수에 넣는다.
    tool: practice(python: model, redraw) · abaqus(python: mdb, session) · hypermesh(tcl) · nx(python: session, work_part — 대기열)
    한 번에 작은 단계만 실행하고 결과·화면을 확인하며 진행한다."""
    if tool not in TOOLS:
        return {"ok": False, "error": f"지원하지 않는 툴: {tool}"}
    if tool == "nx":
        return _nx_exec(code, timeout_sec)
    try:
        return _call(tool, "exec", timeout=timeout_sec, code=code)
    except OSError as e:
        return {"ok": False, "error": f"{tool} Live Link에 연결할 수 없습니다({e}). live_status로 확인하세요."}


@mcp.tool()
def live_result(job: str) -> dict:
    """NX 대기열 작업 결과 확인(live_exec가 pending을 돌려준 경우)."""
    f = NX_QUEUE / f"{job}.result.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"ok": False, "pending": True}


@mcp.tool()
def live_capture(tool: str, title: str = "", caption: str = "", publish: bool = True, flow_id: int | None = None,
                 node_id: str | None = None, project_id: int | None = None) -> dict:
    """툴 화면(뷰포트)을 캡처해 웹앱 결과로 게시한다. 사용자가 바뀐 모델을 바로 확인하게 단계마다 쓴다."""
    pid = resolve_pid(project_id)
    shots = settings.project_dir(pid) / "live"
    shots.mkdir(parents=True, exist_ok=True)
    path = shots / f"{tool}_{time.strftime('%Y%m%d_%H%M%S')}.png"
    got = None
    if TOOLS.get(tool, {}).get("port"):
        try:
            r = _call(tool, "screenshot", path=str(path))
            if r.get("ok") and r.get("path") and Path(r["path"]).exists():
                got = r["path"]
        except OSError:
            pass
    if not got:
        got = _capture_window(TOOLS.get(tool, {}).get("window", tool), path)
    if not got:
        return {"ok": False, "error": "화면을 캡처하지 못했습니다(툴 창을 찾지 못함)."}
    out = {"ok": True, "path": got}
    if publish:
        rel = api.upload_image(pid, Path(got), "live")
        a = api.post(f"/api/projects/{pid}/artifacts", {
            "kind": "image", "title": title or f"{TOOLS.get(tool, {}).get('label', tool)} 화면 {time.strftime('%H:%M:%S')}",
            "payload": {"path": rel, "caption": caption}, "flow_id": flow_id, "node_id": node_id})
        out["artifact_id"] = a["id"]
    return out


if __name__ == "__main__":
    mcp.run()
