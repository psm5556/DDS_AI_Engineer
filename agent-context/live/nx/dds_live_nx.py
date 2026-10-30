# -*- coding: utf-8 -*-
"""DDS Live Link — NX / Simcenter 3D 세션 안 실행기.

dds-live(mcp_servers/dds_live.py)가 이 저널을 열린 NX에서 스스로 실행한다. 사용자는 아무것도 누르지 않는다.
 1) 에이전트가 실행할 코드를 %USERPROFILE%\\.dds-agent\\live\\nx\\queue\\ 에 넣는다(live_exec).
 2) 이 저널이 대기 중인 작업을 '열려 있는 세션'에서 실행하고 결과를 queue\\*.result.json 으로 남긴다.
 3) 저널은 끝나도 메모리에 남아(GetUnloadOption) Windows 타이머로 대기열을 계속 본다.
    타이머는 NX의 GUI 스레드에서 돌므로 NX Open을 안전하게 부를 수 있고, 다음 작업부터는 저널을 다시 띄우지 않아 빠르다.
코드에서 쓸 수 있는 것: NXOpen, session(=NXOpen.Session.GetSession()), work_part, display_part, result(응답에 담을 값)

queue\\..\\oneshot 파일이 있으면 타이머를 쓰지 않는다. dds-live가 작업마다 이 저널을 다시 실행한다(느리지만 메모리에 남지 않는다).
TODO(사내검증): NX 버전별로 GetUnloadOption 뒤에도 파이썬 콜백이 살아 있는지. 처음에는 저장해 둔 모델로 확인한다(docs/pc-verification.md).
"""
import io
import json
import os
import sys
import time
import traceback

import NXOpen

ROOT = os.path.join(os.path.expanduser("~"), ".dds-agent", "live", "nx")
QUEUE = os.path.join(ROOT, "queue")
ALIVE = os.path.join(QUEUE, "listener.alive")  # 살아 있다는 표시. dds-live가 수정 시각으로 판단한다
TICK_MS = 100
STATE = "_ddslive_nx_listener"  # 저널을 다시 실행해도 타이머가 겹치지 않게 sys에 둔다


def GetUnloadOption(dummy):
    """저널이 끝나도 메모리에서 내리지 않는다(타이머 콜백이 계속 불려야 한다)."""
    if os.path.exists(os.path.join(ROOT, "oneshot")):
        return int(NXOpen.Session.LibraryUnloadOption.Immediately)
    return int(NXOpen.Session.LibraryUnloadOption.AtTermination)


def pending():
    if not os.path.isdir(QUEUE):
        return []
    return sorted(f for f in os.listdir(QUEUE) if f.endswith(".py") and not os.path.exists(os.path.join(QUEUE, f[:-3] + ".result.json")))


def run_job(session, name):
    path = os.path.join(QUEUE, name)
    code = io.open(path, encoding="utf-8").read()
    env = {"NXOpen": NXOpen, "session": session, "work_part": session.Parts.Work,
           "display_part": session.Parts.Display, "result": None}
    buf, old = io.StringIO(), sys.stdout
    sys.stdout = buf
    mark = session.SetUndoMark(NXOpen.Session.MarkVisibility.Visible, "DDS 에이전트: " + name)
    try:
        exec(compile(code, path, "exec"), env)
        res = {"ok": True, "stdout": buf.getvalue()[-8000:], "result": None if env["result"] is None else str(env["result"])}
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "stdout": buf.getvalue()[-4000:], "error": "%s\n%s" % (e, traceback.format_exc()[-2000:])}
    finally:
        sys.stdout = old
    tmp = os.path.join(QUEUE, name[:-3] + ".result.tmp")
    with io.open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(res, ensure_ascii=False))
    os.replace(tmp, os.path.join(QUEUE, name[:-3] + ".result.json"))
    try:
        session.UpdateManager.DoUpdate(mark)
    except Exception:  # noqa: BLE001
        pass


def run_pending(session):
    for name in pending():
        run_job(session, name)


def touch_alive():
    with io.open(ALIVE, "w", encoding="utf-8") as fh:
        fh.write(u"%d %f" % (os.getpid(), time.time()))


class Listener(object):
    """GUI 스레드의 Windows 타이머로 대기열을 본다. 작업 중에 다시 들어오지 않게 막는다(NX 업데이트가 메시지를 돌릴 수 있다)."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.user32 = ctypes.windll.user32
        self.busy = False
        self.last_alive = 0.0
        proto = ctypes.WINFUNCTYPE(None, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, wintypes.DWORD)
        self.proc = proto(self.tick)  # 참조를 놓치면 콜백이 사라져 NX가 죽는다. 이 객체는 sys에 남는다
        self.user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, proto]
        self.user32.SetTimer.restype = ctypes.c_size_t
        self.user32.KillTimer.argtypes = [wintypes.HWND, ctypes.c_size_t]
        self.timer = self.user32.SetTimer(None, 0, TICK_MS, self.proc)
        if not self.timer:
            raise OSError("SetTimer 실패")

    def stop(self):
        self.user32.KillTimer(None, self.timer)

    def tick(self, hwnd, msg, timer, when):
        if self.busy:
            return
        self.busy = True
        try:  # 콜백 밖으로 예외가 나가면 안 된다
            now = time.time()
            if now - self.last_alive >= 0.5:
                self.last_alive = now
                touch_alive()
            if pending():
                run_pending(NXOpen.Session.GetSession())
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.busy = False


def start_listener():
    if os.path.exists(os.path.join(ROOT, "oneshot")):
        return None
    old = getattr(sys, STATE, None)
    if old is not None:
        old.stop()
    listener = Listener()
    setattr(sys, STATE, listener)
    touch_alive()
    return listener


def main():
    if not os.path.isdir(QUEUE):
        os.makedirs(QUEUE)
    run_pending(NXOpen.Session.GetSession())
    try:
        start_listener()
    except Exception:  # noqa: BLE001 — 타이머를 못 만들어도 dds-live가 작업마다 이 저널을 다시 실행한다
        pass


if __name__ == "__main__":
    main()
