"""참고 초안: COM으로 붙는 툴(CATIA V5·SOLIDWORKS·LightTools)의 연결을 dds-live 프로세스에 계속 들고 있는 작업 스레드.
- 지금은 단계마다 run_script로 파이썬을 새로 띄우고 GetActiveObject로 다시 붙는다(한 번에 수 초).
- COM 객체는 만든 스레드에서만 써야 한다. MCP 툴 함수는 다른 스레드에서 불릴 수 있으므로, 전용 스레드 하나가 연결을 들고 작업을 차례로 실행한다.
- 실행되는 코드가 아니다. 사내 dds_live.py 구조에 맞춰 옮기고, 툴 스킬의 배치 스크립트 함수(apply_params 등)를 fn 안에서 부른다.

사용 예 (CATIA):
    catia = ComWorker("CATIA.Application")
    def _set(app):
        part = app.ActiveDocument.Part
        old = catia_param_update.apply_params(part, values, strict=True)   # 사내 스크립트의 실제 반환값에 맞춘다
        catia_param_update.update_part(part)
        return old
    old = catia.call(_set)
"""
import queue
import threading
import time


class ComWorker:
    def __init__(self, progid: str):
        self.progid = progid
        self.app = None
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._loop, name=f"com-{progid}", daemon=True).start()

    def _loop(self):
        import pythoncom
        import pywintypes
        import win32com.client
        pythoncom.CoInitialize()
        while True:
            fn, box = self.q.get()
            t0 = time.time()
            try:
                if self.app is None:  # 처음이거나 끊긴 뒤에만 붙는다
                    self.app = win32com.client.GetActiveObject(self.progid)
                    box["connect_ms"] = int((time.time() - t0) * 1000)
                box["value"] = fn(self.app)
            except pywintypes.com_error as e:
                # 툴을 껐다 켰으면 다음 호출에서 다시 붙는다. 잘못된 인자 오류여도 다시 붙는 비용은 작다
                self.app = None
                box["error"] = e
            except Exception as e:  # noqa: BLE001
                box["error"] = e
            box["ms"] = int((time.time() - t0) * 1000)
            box["done"].set()

    def call(self, fn, timeout: float = 120):
        """fn(app)을 COM 스레드에서 실행하고 결과를 돌려준다. 툴이 대화상자에 막혀 있으면 timeout으로 알린다."""
        box = {"done": threading.Event()}
        self.q.put((fn, box))
        if not box["done"].wait(timeout):
            raise TimeoutError(f"{self.progid} 응답 없음 — 툴에 열린 대화상자나 편집 중인 명령이 있는지 확인하세요")
        if "error" in box:
            raise box["error"]
        return box.get("value")
