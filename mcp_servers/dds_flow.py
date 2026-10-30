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
        "결과는 publish_artifact / publish_image로 게시하세요(flow_id·node_id를 꼭 넣으면 웹앱 작업 흐름 탭에서 그 단계 아래에 보입니다). "
        "보고서는 get_report_context로 자료를 읽고 write_report로 새로 씁니다."
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
    정하지 않은 조건(DOE 점 수, 범위 등)은 합리적인 기본값을 정해 노드 detail에 적는다 — 사용자에게 따로 묻지 않는다.
    제안 후에는 턴을 끝내고 "승인하면 시작합니다" 한 줄만 안내한다.
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


@mcp.tool()
def set_project_info(name: str | None = None, description: str | None = None, project_id: int | None = None) -> dict:
    """프로젝트 이름(20자 안팎)·설명(한 줄)을 작업 내용에 맞게 정한다. 첫 화면에서 시작한 프로젝트는 요청문 앞부분이 임시 이름이므로,
    첫 응답 때 알아보기 쉬운 이름(예: "브래킷 경량화 DOE")으로 바꿔 준다."""
    pid = resolve_pid(project_id)
    p = api.get(f"/api/projects/{pid}")
    body = {"version": p["version"]}
    if name:
        body["name"] = name[:60]
    if description is not None:
        body["description"] = description[:300]
    p = api.put(f"/api/projects/{pid}", body)
    return {"project_id": pid, "name": p["name"], "description": p["description"]}


@mcp.tool()
def get_report_context(project_id: int | None = None) -> dict:
    """보고서를 쓰기 전에 읽는 자료: 작업 흐름(단계 번호·상태·detail) · 결과 목록(artifact_id, 넣는 법 embed, 단계, 데이터 요약)
    · 스터디(입력 범위, 출력과 역할, 케이스 수, 최적 케이스, 예측 모델 R²) · 이전 보고서."""
    pid = resolve_pid(project_id)
    return api.get(f"/api/projects/{pid}/report-context")


@mcp.tool()
def write_report(title: str, body: str, flow_id: int | None = None, project_id: int | None = None) -> dict:
    """종합 보고서를 새로 써서 웹앱 '보고서' 탭에 올린다(새 판으로 쌓이고 Word·HTML도 이 내용으로 만들어진다).
    먼저 get_report_context로 자료를 읽고, 숫자를 해석해 엔지니어가 읽을 문서로 쓴다. 결과 목록을 옮겨 적지 않는다.

    body: 한국어 markdown. 권장 구성
      ## 요약 — 결론 3줄 안팎(핵심 숫자: 개선 전→후, 최적 설계값, 제약 만족 여부)
      ## 목적과 조건 — 무엇을, 어떤 조건(하중·회전수 등)·설계 변수 범위로
      ## 수행 내용 — 작업 흐름 단계를 'N단계' 번호 그대로 짧게
      ## 결과 — 그림·표를 넣고 바로 아래에 해석(경향, 민감한 변수, 예측 모델 정확도 R²의 의미)
      ## 결론과 권고 — 설계 제안, 검증 해석, 다음 할 일
      ## 한계 — 가정·확인 안 된 것
    결과(그림·표)를 넣으려면 그 줄에 embed 문자열 하나만 쓴다: ![캡션](artifact:12)
    번호(그림 1, 표 1)는 자동으로 붙으므로 캡션에 번호를 쓰지 않는다. 같은 결과를 두 번 넣지 않는다.
    """
    pid = resolve_pid(project_id)
    r = api.post(f"/api/projects/{pid}/reports", {"title": title, "body": body, "flow_id": flow_id})
    return {"report_id": r["id"], "message": "웹앱 '보고서' 탭에 올렸습니다. 대화에는 한두 줄로 핵심만 알리세요."}


@mcp.tool()
def make_report(formats: list[Literal["html", "docx"]] | None = None, flow_id: int | None = None,
                project_id: int | None = None) -> dict:
    """보고서 파일(Word·HTML)을 이 PC 프로젝트 폴더 reports/에 저장한다. write_report로 쓴 최근 보고서가 있으면 그 내용으로,
    없으면 게시된 결과를 모아 만든다(flow_id를 주면 그 흐름의 결과만)."""
    import time

    pid = resolve_pid(project_id)
    out = settings.project_dir(pid) / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    saved = []
    for fmt in formats or ["docx", "html"]:
        q = f"?format={fmt}" + (f"&flow_id={flow_id}" if flow_id else "")
        dest = out / f"report_{stamp}.{fmt}"
        api.download(f"/api/projects/{pid}/report{q}", dest)
        saved.append(str(dest))
    return {"project_id": pid, "files": saved,
            "hint": "웹앱 '보고서' 탭의 Word·HTML 버튼이나 '파일' 탭 reports/에서 열 수 있습니다."}


if __name__ == "__main__":
    mcp.run()
