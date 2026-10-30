"""PC에서 도는 Live Link 호출 하나: 에이전트가 쓰는 것과 같은 함수를 부른다. 고칠 때 재현용으로 쓴다.
사용: live_call.py <tool> status | launch [모델] | info | capture | exec <코드 파일 또는 코드> | set 이름=값 … | view [방향] | undo"""
import json
import os
import sys
from pathlib import Path

import dds_live


def main() -> int:
    tool, action, rest = sys.argv[1], sys.argv[2], sys.argv[3:]
    if action == "status":
        res = dds_live.live_status()
    elif action == "launch":
        res = dds_live.live_launch(tool, rest[0] if rest else None)
    elif action == "info":
        res = dds_live.live_info(tool)
    elif action == "capture":
        path = Path(os.environ["DDS_TEST_OUT"]) / f"{tool}.png"
        got = dds_live._grab(tool, path)
        res = {"ok": bool(got), "path": got}
    elif action == "exec":
        code = Path(rest[0]).read_text(encoding="utf-8") if Path(rest[0]).exists() else " ".join(rest)
        res = dds_live.live_exec(tool, code)
    elif action == "set":
        res = dds_live.live_set_params(tool, {k: float(v) for k, v in (r.split("=", 1) for r in rest)})
    elif action == "view":
        res = dds_live.live_view(tool, orient=rest[0] if rest else None)
    elif action == "undo":
        res = dds_live.live_undo(tool)
    else:
        print(f"알 수 없는 동작: {action}")
        return 2
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return 1 if isinstance(res, dict) and res.get("ok") is False else 0


if __name__ == "__main__":
    sys.exit(main())
