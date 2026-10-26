# -*- coding: utf-8 -*-
"""DDS Live Link — NX / Simcenter 3D (사람-에이전트 협업 방식: '대기 중인 작업 실행')

NX Open은 GUI 스레드 밖에서 호출하면 안전하지 않으므로, NX에서는 다음처럼 함께 작업한다.
 1) 에이전트(dds-live.live_exec tool="nx")가 실행할 저널을 %USERPROFILE%\\.dds-agent\\live\\nx\\queue\\ 에 넣는다.
 2) 사용자가 NX에서 이 저널(dds_live_nx.py)을 실행하면(리본 버튼으로 등록 권장, Alt+F8 > 재생),
    대기 중인 작업을 순서대로 '열려 있는 세션'에서 실행하고 결과를 queue\\*.result.json 으로 남긴다.
 3) 에이전트가 결과를 읽어 다음 단계를 진행한다. 화면에는 바뀐 모델이 그대로 보인다.
코드에서 쓸 수 있는 것: NXOpen, session(=NXOpen.Session.GetSession()), work_part, display_part, result(응답에 담을 값)

리본 버튼 등록: Customize > Commands > New Item > Action: 이 파일 경로  (TODO(사내검증): NX 버전별 메뉴 위치)
TODO(사내검증): 리스너(백그라운드 스레드 + GetUnloadOption)로 완전 자동 실행 — NX Open 스레드 안전성 확인 후 적용
"""
import io
import json
import os
import sys
import traceback

import NXOpen

QUEUE = os.path.join(os.path.expanduser("~"), ".dds-agent", "live", "nx", "queue")


def main():
    session = NXOpen.Session.GetSession()
    lw = session.ListingWindow
    lw.Open()
    if not os.path.isdir(QUEUE):
        os.makedirs(QUEUE)
    jobs = sorted(f for f in os.listdir(QUEUE) if f.endswith(".py") and not os.path.exists(os.path.join(QUEUE, f[:-3] + ".result.json")))
    if not jobs:
        lw.WriteLine("DDS Live Link: 대기 중인 작업이 없습니다.")
        return
    for name in jobs:
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
            lw.WriteLine("DDS Live Link: 실행 완료 " + name)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "stdout": buf.getvalue()[-4000:], "error": "%s\n%s" % (e, traceback.format_exc()[-2000:])}
            lw.WriteLine("DDS Live Link: 실패 %s — %s (Edit > Undo 로 되돌릴 수 있습니다)" % (name, e))
        finally:
            sys.stdout = old
        with io.open(os.path.join(QUEUE, name[:-3] + ".result.json"), "w", encoding="utf-8") as fh:
            fh.write(json.dumps(res, ensure_ascii=False))
        try:
            session.UpdateManager.DoUpdate(mark)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
