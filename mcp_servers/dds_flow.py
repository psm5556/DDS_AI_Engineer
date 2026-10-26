"""dds-flow MCP: 작업 흐름(Flow) 시각화와 결과 아티팩트 게시."""
from pathlib import Path
from typing import Any, Literal

from _common import api, resolve_pid, settings
from mcp.server import MCPServer

mcp = MCPServer(
    "dds-flow",
    instructions=(
        "사용자의 자동화 요청을 실행하기 전에 반드시 propose_flow로 작업 흐름을 제안하고, "
        "사용자가 웹앱에서 승인할 때까지 실행하지 마세요. 실행 중에는 update_node로 각 단계의 상태를 갱신하고, "
        "결과는 publish_artifact / publish_image로 게시하세요."
    ),
)

NodeType = Literal["cad", "pre", "solve", "post", "doe", "train", "optimize", "deploy", "task"]


@mcp.tool()
def propose_flow(title: str, nodes: list[dict[str, Any]], edges: list[dict[str, str]], goal: str = "",
                 project_id: int | None = None) -> dict:
    """작업 흐름 그래프를 제안한다(상태: 검토). 웹앱 캔버스에 표시되고 사용자가 승인해야 실행할 수 있다.

    nodes: [{"id": "cad", "label": "NX 형상 변경", "type": "cad|pre|solve|post|doe|train|optimize|deploy|task",
             "tool": "nx|simcenter|abaqus|lsdyna|ansys|altair|starccm|amesim|mock|python", "detail": "설명"}]
    edges: [{"source": "cad", "target": "solve", "label": "STEP"}]
    반환된 flow_id를 기억했다가 update_node / publish_artifact에 사용한다.
    제안 후에는 턴을 끝내고 사용자에게 웹앱에서 '승인'을 눌러 달라고 안내한다.
    """
    pid = resolve_pid(project_id)
    f = api.post(f"/api/projects/{pid}/flows", {"title": title, "goal": goal, "nodes": nodes, "edges": edges})
    return {"flow_id": f["id"], "status": f["status"], "message": "사용자 승인을 기다리세요. 승인되면 실행을 이어가라는 메시지가 옵니다."}


@mcp.tool()
def get_flow(flow_id: int) -> dict:
    """Flow의 상태(review/approved/in_progress/complete/failed/rejected)와 노드 상태를 조회한다."""
    f = api.get(f"/api/flows/{flow_id}")
    return {"flow_id": f["id"], "title": f["title"], "status": f["status"],
            "nodes": [{k: n.get(k) for k in ("id", "label", "status", "detail")} for n in f["nodes"]]}


@mcp.tool()
def update_node(flow_id: int, node_id: str, status: Literal["pending", "in_progress", "complete", "failed", "skipped"],
                detail: str | None = None) -> dict:
    """Flow 노드의 진행 상태를 갱신한다. 단계를 시작할 때 in_progress, 끝나면 complete(실패 시 failed + detail에 원인)."""
    f = api.put(f"/api/flows/{flow_id}/nodes/{node_id}", {"status": status, "detail": detail})
    return {"flow_status": f["status"]}


@mcp.tool()
def publish_artifact(kind: Literal["table", "scatter", "line", "bar", "parallel", "markdown"], title: str,
                     payload: dict[str, Any], flow_id: int | None = None, node_id: str | None = None,
                     project_id: int | None = None) -> dict:
    """결과를 웹앱 아티팩트 패널에 게시한다. 같은 제목이면 갱신된다.

    payload 형식:
      table:    {"columns": ["이름", ...], "rows": [[...], ...]}
      scatter:  {"x": "x축 이름", "y": "y축 이름", "series": [{"name": "...", "points": [{"x": 1, "y": 2}]}], "diagonal": false}
      line:     {"x": "x축 이름", "series": [{"name": "...", "points": [{"x": 1, "y": 2}]}]}
      bar:      {"categories": ["A", "B"], "series": [{"name": "...", "values": [1, 2]}]}
      parallel: {"dimensions": ["x1", "x2", "f"], "rows": [[...], ...]}
      markdown: {"text": "## 제목\\n본문"}
    """
    pid = resolve_pid(project_id)
    a = api.post(f"/api/projects/{pid}/artifacts",
                 {"kind": kind, "title": title, "payload": payload, "flow_id": flow_id, "node_id": node_id})
    return {"artifact_id": a["id"]}


@mcp.tool()
def publish_image(path: str, title: str, caption: str = "", flow_id: int | None = None, node_id: str | None = None,
                  project_id: int | None = None) -> dict:
    """이 PC의 이미지 파일(컨투어·형상 캡처 등)을 서버에 올려 아티팩트로 게시한다. path는 절대경로 또는 프로젝트 작업폴더 기준."""
    pid = resolve_pid(project_id)
    p = Path(path)
    if not p.is_absolute():
        p = settings.project_dir(pid) / p
    if not p.is_file():
        return {"error": f"파일이 없습니다: {p}"}
    rel = api.upload_image(pid, p)
    a = api.post(f"/api/projects/{pid}/artifacts", {"kind": "image", "title": title,
                                                    "payload": {"path": rel, "caption": caption},
                                                    "flow_id": flow_id, "node_id": node_id})
    return {"artifact_id": a["id"], "path": rel}


if __name__ == "__main__":
    mcp.run()
