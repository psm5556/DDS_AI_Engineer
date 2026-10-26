# -*- coding: utf-8 -*-
"""DDS Live Link — Abaqus/CAE GUI 플러그인 (Python 2.7 / 3 겸용)

설치: 이 폴더(ddsLive)를 %USERPROFILE%\\abaqus_plugins\\ 아래에 복사 → Abaqus/CAE 재시작
     → 메뉴 Plug-ins > DDS Live Link 시작  (또는 `abaqus cae startup=start_ddslive.py`)
동작: 127.0.0.1:47801 소켓으로 받은 요청을 큐에 넣고, GUI 타이머(메인 스레드)가 꺼내서
     sendCommand()로 커널에서 실행한다 → 사용자 화면(뷰포트·모델 트리)이 바로 바뀐다.
     실행 결과(stdout/result/오류)는 커널 도우미(ddslive_kernel.py)가 파일로 남기고 GUI가 읽어서 응답한다.
TODO(사내검증):
  - FXApp.addTimeout 시그니처 및 FXMAPFUNC/SEL_TIMEOUT 사용법(Abaqus GUI Toolkit 버전별)
  - sendCommand 가 여러 줄 명령을 받는지 → 여기서는 한 줄 호출만 보낸다
  - 대안: 커널 쪽 스레드에서 직접 실행(빠르지만 GUI 갱신·스레드 안전성 확인 필요)
"""
from abaqusGui import *  # noqa: F401,F403 — Abaqus GUI Toolkit
from abaqusConstants import ALL  # noqa: F401
import json
import os
import socket
import tempfile
import threading
import time

try:
    import Queue as queue  # py2
except ImportError:  # py3
    import queue

PORT = 47801
WORK = os.path.join(tempfile.gettempdir(), "ddslive_abaqus")
_requests = queue.Queue()


def _token():
    t = os.environ.get("DDS_LIVE_TOKEN", "")
    if t:
        return t
    f = os.path.join(os.path.expanduser("~"), ".dds-agent", "live.token")
    if os.path.exists(f):
        return open(f).read().strip()
    return ""


def _serve(token):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(2)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_client, args=(conn, token)).start()


def _client(conn, token):
    f = conn.makefile("rb")
    for raw in f:
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            _send(conn, {"ok": False, "error": str(e)})
            continue
        if token and req.get("token") != token:
            _send(conn, {"id": req.get("id"), "ok": False, "error": "token mismatch"})
            continue
        box = {"event": threading.Event()}
        _requests.put((req, box))  # GUI 타이머가 꺼내 처리
        box["event"].wait(600)
        resp = box.get("resp", {"ok": False, "error": "timeout"})
        resp["id"] = req.get("id")
        _send(conn, resp)
    conn.close()


def _send(conn, obj):
    conn.sendall((json.dumps(obj) + "\n").encode("utf-8"))


def _handle_in_gui(req):
    """GUI 스레드에서 호출됨."""
    op = req.get("op")
    if op == "ping":
        try:
            ver = str(getAFXApp().getVersionNumbers())  # TODO(사내검증): 버전 조회 API
        except Exception:  # noqa: BLE001
            ver = "unknown"
        return {"ok": True, "tool": "abaqus", "version": ver, "pid": os.getpid()}
    if not os.path.isdir(WORK):
        os.makedirs(WORK)
    stamp = str(int(time.time() * 1000))
    code_file = os.path.join(WORK, "req_%s.py" % stamp)
    out_file = os.path.join(WORK, "res_%s.json" % stamp)
    if op == "exec":
        code = req.get("code", "")
    elif op == "info":
        code = "result = ddslive_kernel.summary()"
    elif op == "screenshot":
        code = "result = ddslive_kernel.screenshot(%r)" % req.get("path", os.path.join(WORK, "shot.png"))
    else:
        return {"ok": False, "error": "unknown op"}
    with open(code_file, "wb") as fh:
        fh.write(code.encode("utf-8"))
    here = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
    sendCommand("import sys; sys.path.insert(0, %r) if %r not in sys.path else None; import ddslive_kernel" % (here, here))
    sendCommand("ddslive_kernel.run(%r, %r)" % (code_file.replace("\\", "/"), out_file.replace("\\", "/")))
    try:
        with open(out_file, "rb") as fh:
            res = json.loads(fh.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "error": "no result file: %s" % e}
    if op == "screenshot" and res.get("ok"):
        res["path"] = res.get("result")
    return res


class DDSLiveTimer(FXObject):
    """GUI 메인 스레드에서 0.2초마다 요청 큐를 확인한다."""
    [ID_TICK] = range(1)

    def __init__(self):
        FXObject.__init__(self)
        FXMAPFUNC(self, SEL_TIMEOUT, self.ID_TICK, DDSLiveTimer.onTick)
        getAFXApp().addTimeout(self, self.ID_TICK, 200)  # TODO(사내검증)

    def onTick(self, sender, sel, ptr):
        try:
            while not _requests.empty():
                req, box = _requests.get_nowait()
                try:
                    box["resp"] = _handle_in_gui(req)
                except Exception as e:  # noqa: BLE001
                    box["resp"] = {"ok": False, "error": str(e)}
                box["event"].set()
        finally:
            getAFXApp().addTimeout(self, self.ID_TICK, 200)
        return 1


_started = []


def start_ddslive():
    if _started:
        getAFXApp().getAFXMainWindow().writeToMessageArea("DDS Live Link 이미 실행 중 (127.0.0.1:%d)" % PORT)
        return
    t = threading.Thread(target=_serve, args=(_token(),))
    t.daemon = True
    t.start()
    _started.append(DDSLiveTimer())
    getAFXApp().getAFXMainWindow().writeToMessageArea("DDS Live Link 시작: 127.0.0.1:%d — 웹앱 에이전트가 이 세션에서 함께 작업합니다" % PORT)


# Plug-ins 메뉴 등록
toolset = getAFXApp().getAFXMainWindow().getPluginToolset()
toolset.registerGuiMenuButton(
    buttonText="DDS Live Link 시작",
    object=None,  # 다이얼로그 없이 함수만 실행 — TODO(사내검증): object=None 허용 여부, 안 되면 AFXProcedure로 감싼다
    kernelInitString="",
    applicableModules=ALL,
    version="1.0",
    author="DDS AI Engineer",
    description="웹앱 에이전트가 열려 있는 Abaqus/CAE 세션에서 함께 모델링합니다",
)
start_ddslive()  # 플러그인 로드 시 바로 시작
