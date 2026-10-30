"""PC에서 도는 Live Link 점검: 붙기 → 모델 읽기 → 화면 캡처까지 한 번에 하고 걸린 시간을 남긴다. 모델을 바꾸지 않는다.
사용: live.py <tool>  (nx·abaqus·hypermesh·practice). 화면은 out/으로 받아 간다."""
import json
import os
import shutil
import sys
import time
from pathlib import Path

import dds_live


def step(name, fn, log):
    t0 = time.time()
    try:
        res = fn()
        ok = not (isinstance(res, dict) and res.get("ok") is False)
    except Exception as e:  # noqa: BLE001
        res, ok = {"error": repr(e)}, False
    log.append({"step": name, "ok": ok, "seconds": round(time.time() - t0, 2), "result": res})
    return ok


def main() -> int:
    tool = sys.argv[1]
    log = []
    good = step("상태", dds_live.live_status, log)
    good = step("붙기", lambda: dds_live.live_launch(tool), log) and good
    good = step("모델 읽기", lambda: dds_live.live_info(tool), log) and good

    def shot():
        path = Path(os.environ["DDS_TEST_OUT"]) / f"{tool}.png"
        got = dds_live._grab(tool, path)
        if got and Path(got) != path:
            shutil.copy(got, path)
        return {"ok": bool(got), "path": got}

    good = step("화면 캡처", shot, log) and good
    print(json.dumps({"tool": tool, "ok": good, "steps": log}, ensure_ascii=False, indent=1, default=str))
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
