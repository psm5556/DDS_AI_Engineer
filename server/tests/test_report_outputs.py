"""AI 보고서(새 판 저장·자료·Word/HTML)와 Output 추가 추출(이미 해석한 케이스 + 이후 케이스) 테스트."""
import time

from server.app.report import order_nodes, split_body
from server.tests.test_api import _auth, _wait_idle, client  # noqa: F401 — 같은 서버 fixture 재사용


def _wait_extracted(client, sid, headers, timeout=60):  # noqa: F811
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = client.get(f"/api/studies/{sid}", headers=headers).json()
        if not st["running"] and not st["extracting"]:
            return st
        time.sleep(0.2)
    raise TimeoutError


def test_step_order_and_body_split():
    nodes = [{"id": "b"}, {"id": "a"}, {"id": "c"}]
    edges = [{"source": "a", "target": "b"}, {"source": "b", "target": "c"}]
    assert [n["id"] for n in order_nodes(nodes, edges)] == ["a", "b", "c"]
    parts = split_body("## 요약\n본문\n![진동 수렴](artifact:7)\n끝")
    assert parts == [("md", "## 요약\n본문", None), ("art", "진동 수렴", 7), ("md", "끝", None)]


def test_ai_report(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "보고서"}, headers=admin).json()["id"]
    f = client.post(f"/api/projects/{pid}/flows", headers=admin, json={
        "title": "흐름", "nodes": [{"id": "solve", "label": "해석", "type": "solve"}, {"id": "doe", "label": "DOE", "type": "doe"}],
        "edges": [{"source": "doe", "target": "solve"}]}).json()
    tbl = client.post(f"/api/projects/{pid}/artifacts", headers=admin, json={
        "kind": "table", "title": "케이스", "flow_id": f["id"], "node_id": "solve",
        "payload": {"columns": ["ID", "f"], "rows": [[1, 0.5], [2, 0.4]]}}).json()
    line = client.post(f"/api/projects/{pid}/artifacts", headers=admin, json={
        "kind": "line", "title": "수렴", "payload": {"x": "회", "series": [{"name": "f", "points": [{"x": 1, "y": 2}, {"x": 2, "y": 1}]}]}}).json()
    assert client.get(f"/api/projects/{pid}/reports/latest", headers=admin).json() is None

    ctx = client.get(f"/api/projects/{pid}/report-context", headers=admin).json()
    assert [s["no"] for s in ctx["flows"][0]["steps"]] == [1, 2] and ctx["flows"][0]["steps"][0]["id"] == "doe"
    step = {r["artifact_id"]: r["step"] for r in ctx["results"]}
    assert step[tbl["id"]] == "2단계 · 해석" and step[line["id"]] == "단계 밖"

    body = f"## 요약\n- f가 **0.4**까지 줄었습니다\n![케이스 결과](artifact:{tbl['id']})\n![수렴 이력](artifact:{line['id']})\n## 결론\n1. 채택"
    assert client.post(f"/api/projects/{pid}/reports", headers=admin, json={"title": "t", "body": "![x](artifact:999999)"}).status_code == 400
    rep = client.post(f"/api/projects/{pid}/reports", headers=admin, json={"title": "종합 보고서", "body": body}).json()
    latest = client.get(f"/api/projects/{pid}/reports/latest", headers=admin).json()
    assert latest["id"] == rep["id"] and latest["new_results"] == 0

    html = client.get(f"/api/projects/{pid}/report?format=html", headers=admin).text
    assert "종합 보고서" in html and "표 1. 케이스 결과" in html and "그림 1. 수렴 이력" in html and "<strong>0.4</strong>" in html
    assert client.get(f"/api/projects/{pid}/report?format=docx", headers=admin).content[:2] == b"PK"

    time.sleep(0.05)  # 결과를 다시 게시하면 '새 결과'로 센다
    client.post(f"/api/projects/{pid}/artifacts", headers=admin, json={"kind": "table", "title": "케이스", "payload": {"columns": ["ID"], "rows": [[3]]}})
    assert client.get(f"/api/projects/{pid}/reports/latest", headers=admin).json()["new_results"] == 1
    assert len(client.get(f"/api/projects/{pid}/reports", headers=admin).json()) == 1


def test_add_output_extracts_existing_and_later_cases(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = client.post("/api/projects", json={"name": "Output 추가"}, headers=admin).json()["id"]
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "탈수", "adapter": "mock", "adapter_config": {"function": "washer", "outputs": ["vibration"]},
        "variables": [{"name": "spring_k", "lower": -10, "upper": 30}, {"name": "damper_c", "lower": -10, "upper": 50}],
        "objectives": [{"name": "vibration", "goal": "min"}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": 6, "seed": 2}, headers=admin)
    client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    _wait_idle(client, sid, admin)
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert all(set(c["results"]) == {"vibration"} for c in cases)

    # post(추출 단계)는 script 어댑터에만
    bad = client.post(f"/api/studies/{sid}/outputs", headers=admin,
                      json={"outputs": [{"name": "x"}], "post": {"tool": "python", "script": "a.py"}})
    assert bad.status_code == 400
    r = client.post(f"/api/studies/{sid}/outputs", headers=admin,
                    json={"outputs": [{"name": "floor_force", "note": "바닥 전달력 [N]"}]}).json()
    assert r["queued"] == 6
    st = _wait_extracted(client, sid, admin)
    assert st["study"]["outputs"][0]["name"] == "floor_force"
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert all("floor_force" in c["results"] and "vibration" in c["results"] for c in cases)

    # 이후 케이스는 해석할 때 함께 뽑는다
    client.post(f"/api/studies/{sid}/doe", json={"n": 2, "seed": 9}, headers=admin)
    client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    _wait_extracted(client, sid, admin)
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert len(cases) == 8 and all("floor_force" in c["results"] for c in cases)
    table = next(a for a in client.get(f"/api/projects/{pid}/artifacts", headers=admin).json() if a["title"] == "[탈수] 케이스 결과")
    assert table["payload"]["columns"][-2:] == ["vibration", "floor_force"]
