"""Live Link 연습용 '가짜 CAD 툴' (CAD/CAE 라이선스 없이 Live Link 전체 흐름을 검증).
작은 Tk 창에 박스 형상(폭·높이·두께)을 그리고, Live Link 프로토콜(PROTOCOL.md)로 받은 Python 코드를 GUI 스레드에서 실행한다.
코드 안에서 쓸 수 있는 객체: model (dict: width, height, thickness, color, note), redraw()
실행: python practice_tool.py [--port 47800] [--headless]
"""
import argparse
import io
import json
import os
import queue
import socket
import socketserver
import sys
import threading
import traceback
from contextlib import redirect_stdout
from pathlib import Path

PORT = 47800


def load_token() -> str:
    t = os.environ.get("DDS_LIVE_TOKEN")
    if t:
        return t
    f = Path.home() / ".dds-agent" / "live.token"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


class Tool:
    def __init__(self, headless: bool):
        self.model = {"width": 120.0, "height": 60.0, "thickness": 4.0, "color": "#2563eb", "note": ""}
        self.jobs: queue.Queue = queue.Queue()
        self.headless = headless
        self.root = self.canvas = None
        if not headless:
            import tkinter as tk
            self.root = tk.Tk()
            self.root.title("DDS 연습용 CAD — Live Link :%d" % PORT)
            self.canvas = tk.Canvas(self.root, width=520, height=360, bg="white")
            self.canvas.pack()
            self.root.after(50, self.pump)

    # ---------- GUI 스레드 ----------
    def redraw(self):
        if self.headless:
            return
        c, m = self.canvas, self.model
        c.delete("all")
        s = 2.0
        x0, y0 = 260 - m["width"] * s / 2, 180 - m["height"] * s / 2
        c.create_rectangle(x0, y0, x0 + m["width"] * s, y0 + m["height"] * s, fill=m["color"], outline="#18181b", width=max(1, m["thickness"]))
        c.create_text(260, 330, text="폭 %.1f · 높이 %.1f · 두께 %.1f  %s" % (m["width"], m["height"], m["thickness"], m["note"]), fill="#52525b")

    def pump(self):
        while not self.jobs.empty():
            fn, done = self.jobs.get()
            try:
                done["value"] = fn()
            except Exception as e:  # noqa: BLE001
                done["error"] = "%s\n%s" % (e, traceback.format_exc()[-1500:])
            done["event"].set()
        self.root.after(50, self.pump)

    def on_gui(self, fn, timeout=60):
        """요청 스레드 → GUI 스레드에서 fn 실행 후 결과를 기다린다."""
        if self.headless:
            return fn()
        done = {"event": threading.Event()}
        self.jobs.put((fn, done))
        if not done["event"].wait(timeout):
            raise TimeoutError("GUI 응답 시간 초과")
        if "error" in done:
            raise RuntimeError(done["error"])
        return done.get("value")

    # ---------- 요청 처리 ----------
    def handle(self, req: dict) -> dict:
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "tool": "practice", "version": "1.0", "pid": os.getpid()}
        if op == "info":
            return {"ok": True, "model": "box", "summary": dict(self.model)}
        if op == "exec":
            def run():
                env = {"model": self.model, "redraw": self.redraw, "result": None}
                buf = io.StringIO()
                with redirect_stdout(buf):
                    exec(req.get("code", ""), env)  # noqa: S102 — 토큰을 확인한 로컬 요청만 실행
                self.redraw()
                return {"stdout": buf.getvalue()[-8000:], "result": None if env.get("result") is None else str(env["result"])}
            return {"ok": True, **self.on_gui(run)}
        if op == "screenshot":
            path = req.get("path") or str(Path.cwd() / "practice.png")
            def snap():
                self.redraw()
                from PIL import ImageGrab
                if self.headless:
                    from PIL import Image, ImageDraw
                    img = Image.new("RGB", (520, 360), "white")
                    d = ImageDraw.Draw(img)
                    m = self.model
                    d.rectangle((260 - m["width"], 180 - m["height"], 260 + m["width"], 180 + m["height"]), fill=m["color"], outline="black")
                    img.save(path)
                    return path
                self.root.update()
                x, y = self.canvas.winfo_rootx(), self.canvas.winfo_rooty()
                ImageGrab.grab((x, y, x + self.canvas.winfo_width(), y + self.canvas.winfo_height())).save(path)
                return path
            return {"ok": True, "path": self.on_gui(snap)}
        return {"ok": False, "error": "알 수 없는 op: %s" % op}


def serve(tool: Tool, port: int, token: str):
    class H(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                req: dict = {}
                try:
                    req = json.loads(line.decode("utf-8"))
                    if token and req.get("token") != token:
                        resp = {"ok": False, "error": "토큰이 맞지 않습니다"}
                    else:
                        resp = tool.handle(req)
                except Exception as e:  # noqa: BLE001
                    resp = {"ok": False, "error": str(e)}
                resp["id"] = req.get("id")
                self.wfile.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()

    class S(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    srv = S(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main():
    global PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args()
    PORT = a.port
    tool = Tool(a.headless)
    serve(tool, a.port, load_token())
    print("practice tool listening on 127.0.0.1:%d" % a.port, flush=True)
    if a.headless:
        threading.Event().wait()
    else:
        tool.redraw()
        tool.root.mainloop()


if __name__ == "__main__":
    sys.exit(main())
