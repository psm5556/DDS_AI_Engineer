# -*- coding: utf-8 -*-
"""DDS Live Link — Abaqus 커널 쪽 도우미 (Python 2.7 / 3 겸용).
GUI 플러그인이 sendCommand('ddslive_kernel.run(code_file, out_file)')로 호출한다.
코드에서 쓸 수 있는 것: mdb, session, (abaqus 모듈 전체), result 변수에 값을 넣으면 응답에 담긴다."""
import json
import sys
import traceback

try:
    from StringIO import StringIO  # py2
except ImportError:
    from io import StringIO


def run(code_file, out_file):
    from abaqus import mdb, session  # noqa: F401
    import abaqus
    import abaqusConstants
    env = {"__name__": "__ddslive__", "mdb": mdb, "session": session, "result": None}
    env.update({k: getattr(abaqus, k) for k in dir(abaqus) if not k.startswith("_")})
    env.update({k: getattr(abaqusConstants, k) for k in dir(abaqusConstants) if not k.startswith("_")})
    code = open(code_file, "rb").read().decode("utf-8")
    buf, old = StringIO(), sys.stdout
    sys.stdout = buf
    try:
        exec(compile(code, code_file, "exec"), env)
        res = {"ok": True, "stdout": buf.getvalue()[-8000:], "result": None if env.get("result") is None else str(env["result"])}
    except Exception as e:  # noqa: BLE001
        res = {"ok": False, "stdout": buf.getvalue()[-4000:], "error": "%s\n%s" % (e, traceback.format_exc()[-2000:])}
    finally:
        sys.stdout = old
    with open(out_file, "wb") as fh:
        fh.write(json.dumps(res).encode("utf-8"))


def summary():
    from abaqus import mdb, session
    out = {}
    for name, m in mdb.models.items():
        out[name] = {
            "parts": list(m.parts.keys()), "materials": list(m.materials.keys()), "steps": list(m.steps.keys()),
            "loads": list(m.loads.keys()), "bcs": list(m.boundaryConditions.keys()),
            "sets": list(m.rootAssembly.sets.keys()),
        }
    vp = session.viewports[session.currentViewportName]
    return json.dumps({"models": out, "viewport": session.currentViewportName,
                       "displayed": str(vp.displayedObject)})


def screenshot(path):
    """현재 뷰포트를 PNG로 저장."""
    from abaqus import session
    from abaqusConstants import PNG, OFF
    session.printOptions.setValues(vpBackground=OFF, reduceColors=False)
    base = path[:-4] if path.lower().endswith(".png") else path
    session.printToFile(fileName=base, format=PNG, canvasObjects=(session.viewports[session.currentViewportName],))
    return base + ".png"
