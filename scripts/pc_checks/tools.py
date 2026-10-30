"""PC에서 도는 기본 점검: 지원 툴을 얼마나 찾았고, 찾은 경로가 실제로 있는지, Live Link 상태는 어떤지. 결과는 표준 출력의 JSON."""
import json
import sys

from agent.dds_agent.config import load_settings
from cae_adapters.catalog import tool_status


def main() -> int:
    s = load_settings()
    rows = tool_status(s.tools)
    ready = [r for r in rows if r.get("ready") or r.get("status") == "ready"]
    out = {"found": len(ready), "total": len(rows), "tools": rows, "tool_source": s.tool_source}
    try:
        import dds_live
        out["live"] = dds_live.live_status()
    except Exception as e:  # noqa: BLE001 — Live Link 쪽 문제도 이 점검의 결과다
        out["live_error"] = repr(e)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 1 if "live_error" in out else 0


if __name__ == "__main__":
    sys.exit(main())
