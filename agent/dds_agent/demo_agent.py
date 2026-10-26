"""데모 에이전트: Gemini CLI 없이 전체 경로(ACP → MCP 서버 → DDS 서버 → 웹앱)를 검증하기 위한 가짜 ACP 에이전트.
Gemini처럼 session/new로 받은 MCP 서버를 실제로 띄워서 툴을 호출하며, 정해진 시나리오
(Flow 제안 → 승인 대기 → 권한 요청 → Branin mock DOE → GP 학습 → BO → Simulator 배포)를 수행한다.
agent.toml 에 agent_mode = "demo" 로 사용한다.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

_write_lock = threading.Lock()
ROOT = Path(__file__).resolve().parents[2]  # 저장소(또는 설치된 Agent) 루트 — cae_adapters를 찾는다
SCRIPTS = Path(__file__).resolve().parent / "demo_scripts"
STRESS_LIMIT = 250.0  # MPa


def wants_e2e(body: str) -> bool:
    """CAD부터 보고서까지 전체 과정을 원하는 요청인지."""
    b = body.upper()
    cad = "CAD" in b or "캐드" in body
    tail = any(k in body for k in ("보고서", "레포트", "리포트")) or "E2E" in b or "전체 과정" in body
    return cad and tail


def send(msg: dict):
    with _write_lock:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False) + "\n")
        sys.stdout.flush()


class DemoSession:
    def __init__(self, sid: str, mcp_defs: list[dict]):
        self.sid, self.mcp_defs = sid, mcp_defs
        self.stack = AsyncExitStack()
        self.clients: dict[str, ClientSession] = {}
        self.flow_id: int | None = None
        self.mode = "branin"
        self.cancelled = False

    async def start_mcp(self):
        for d in self.mcp_defs:
            env = {e["name"]: e["value"] for e in d.get("env", [])}
            import os
            params = StdioServerParameters(command=d["command"], args=d.get("args", []), env={**os.environ, **env})
            read, write = await self.stack.enter_async_context(stdio_client(params))
            client = await self.stack.enter_async_context(ClientSession(read, write))
            await client.initialize()
            self.clients[d["name"]] = client

    def update(self, upd: dict):
        send({"method": "session/update", "params": {"sessionId": self.sid, "update": upd}})

    async def say(self, text: str, thought: bool = False):
        kind = "agent_thought_chunk" if thought else "agent_message_chunk"
        for chunk in re.findall(r".{1,12}", text, re.S):
            self.update({"sessionUpdate": kind, "content": {"type": "text", "text": chunk}})
            await asyncio.sleep(0.02)

    async def tool(self, server: str, name: str, args: dict[str, Any]) -> Any:
        if self.cancelled:
            raise asyncio.CancelledError
        tcid = uuid.uuid4().hex[:8]
        self.update({"sessionUpdate": "tool_call", "toolCallId": tcid, "title": f"{server}.{name}",
                     "kind": "execute", "status": "in_progress", "rawInput": args})
        res = await self.clients[server].call_tool(name, args, read_timeout_seconds=900)
        text = "\n".join(getattr(c, "text", "") for c in getattr(res, "content", []) or [])
        failed = bool(getattr(res, "is_error", False) or getattr(res, "isError", False))
        self.update({"sessionUpdate": "tool_call_update", "toolCallId": tcid, "status": "failed" if failed else "completed",
                     "content": [{"type": "content", "content": {"type": "text", "text": text[:3000]}}]})
        if failed:
            raise RuntimeError(text)
        sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
        if isinstance(sc, dict):
            return sc.get("result", sc)
        try:
            return json.loads(text)
        except ValueError:
            return text


class DemoAgent:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        self.sessions: dict[str, DemoSession] = {}
        self.perm_waiters: dict[int, asyncio.Future] = {}
        self.next_id = 10_000

    async def handle(self, msg: dict):
        if "method" not in msg:  # 클라이언트의 응답(권한 요청 결과)
            fut = self.perm_waiters.pop(msg.get("id"), None)
            if fut and not fut.done():
                fut.set_result(msg.get("result"))
            return
        method, params, rid = msg["method"], msg.get("params") or {}, msg.get("id")
        try:
            if method == "initialize":
                result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": False}, "authMethods": [],
                          "agentInfo": {"name": "dds-demo-agent", "version": "0.1"}}
            elif method == "session/new":
                sid = "demo-" + uuid.uuid4().hex[:8]
                s = DemoSession(sid, params.get("mcpServers") or [])
                await s.start_mcp()
                self.sessions[sid] = s
                result = {"sessionId": sid}
            elif method == "session/prompt":
                s = self.sessions[params["sessionId"]]
                s.cancelled = False
                text = "".join(p.get("text", "") for p in params.get("prompt", []))
                try:
                    await self.scenario(s, text)
                    result = {"stopReason": "end_turn"}
                except asyncio.CancelledError:
                    result = {"stopReason": "cancelled"}
            elif method == "session/cancel":
                s = self.sessions.get(params.get("sessionId"))
                if s:
                    s.cancelled = True
                return
            else:
                raise ValueError(f"지원하지 않는 메서드: {method}")
            if rid is not None:
                send({"id": rid, "result": result})
        except Exception as e:
            if rid is not None:
                send({"id": rid, "error": {"code": -32603, "message": str(e)}})

    async def ask_permission(self, s: DemoSession, title: str, raw: dict) -> bool:
        self.next_id += 1
        fut = self.loop.create_future()
        self.perm_waiters[self.next_id] = fut
        send({"id": self.next_id, "method": "session/request_permission", "params": {
            "sessionId": s.sid,
            "toolCall": {"toolCallId": uuid.uuid4().hex[:8], "title": title, "kind": "execute", "rawInput": raw},
            "options": [{"optionId": "allow", "name": "허용", "kind": "allow_once"},
                        {"optionId": "reject", "name": "거부", "kind": "reject_once"}]}})
        res = await fut
        outcome = (res or {}).get("outcome") or {}
        return outcome.get("outcome") == "selected" and outcome.get("optionId") == "allow"

    async def scenario(self, s: DemoSession, text: str):
        """시나리오 3가지: CAD~보고서 전체(브래킷 E2E) · '3D'/'형상' 요청(Beam3D 형상·필드 학습) · 기본(Branin 최적화)."""
        body = text.split("\n\n", 1)[-1]  # 앞의 [DDS 컨텍스트] 줄 제외
        if "[이전 대화 끝]" in body:  # 새 세션에 넘긴 지난 대화 요약도 제외(이번 요청만)
            body = body.split("[이전 대화 끝]", 1)[1].strip()
        if s.flow_id is None:
            s.mode = "bracket" if wants_e2e(body) else "beam3d" if ("3D" in body or "형상" in body) else "branin"
            await s.say("요청을 분석하고 있습니다. 전체 작업 흐름을 먼저 제안할게요.", thought=True)
            if "[첨부 파일" in body:
                files = [ln.strip("- ").split(" (")[0] for ln in body.splitlines() if ln.startswith("- inputs/")]
                await s.say(f"첨부하신 파일을 작업 폴더에서 확인했습니다: {', '.join(files)}\n\n")
            if s.mode == "bracket":
                title, desc = "브래킷 E2E 설계", "CAD → 해석 모델 → DOE → 형상·응력 학습 → 3D Simulator → 보고서"
                nodes = [
                    {"id": "cad", "label": "브래킷 CAD 생성", "type": "cad", "tool": "nx", "detail": "플레이트+리브, 두께·리브 높이 파라미터"},
                    {"id": "pre", "label": "해석 모델 구성", "type": "pre", "tool": "abaqus", "detail": "강재 · 벽 고정 · 끝단 2 kN"},
                    {"id": "doe", "label": "DOE 20점 (LHS)", "type": "doe", "detail": "두께 6~14, 리브 10~40 mm"},
                    {"id": "solve", "label": "케이스 해석 20건", "type": "solve", "tool": "abaqus", "detail": "응력 필드 저장"},
                    {"id": "train", "label": "질량·응력 예측 모델", "type": "train", "detail": "GP"},
                    {"id": "field", "label": "형상·응력 필드 학습", "type": "train", "detail": "PCA + GP"},
                    {"id": "deploy", "label": "3D Simulator 배포", "type": "deploy"},
                    {"id": "report", "label": "보고서 작성", "type": "post", "detail": "Word · HTML"},
                ]
                pairs = [("cad", "pre"), ("pre", "doe"), ("doe", "solve"), ("solve", "train"), ("solve", "field"),
                         ("train", "deploy"), ("field", "deploy"), ("deploy", "report")]
            elif s.mode == "beam3d":
                title, desc = "외팔보 3D 응력 예측", "mock 3D 외팔보로 DOE → 형상·필드 학습 → 3D Simulator 검증"
                nodes = [
                    {"id": "doe", "label": "DOE 16점 (LHS)", "type": "doe", "tool": "mock", "detail": "폭 10~40, 높이 20~80 mm"},
                    {"id": "solve", "label": "3D 외팔보 해석", "type": "solve", "tool": "mock", "detail": "케이스마다 응력 필드 저장"},
                    {"id": "train", "label": "스칼라 예측 모델", "type": "train"},
                    {"id": "field", "label": "형상·응력 필드 학습", "type": "train", "detail": "PCA + GP"},
                    {"id": "deploy", "label": "3D Simulator 배포", "type": "deploy"},
                ]
                pairs = [("doe", "solve"), ("solve", "train"), ("solve", "field"), ("train", "deploy"), ("field", "deploy")]
            else:
                title, desc = "Branin 최적화 데모", "mock 솔버로 DOE → GP → BO → Simulator 검증"
                nodes = [
                    {"id": "doe", "label": "DOE 20점 (LHS)", "type": "doe", "tool": "mock", "detail": "x1∈[-5,10], x2∈[0,15]"},
                    {"id": "solve", "label": "Mock 솔버 실행", "type": "solve", "tool": "mock"},
                    {"id": "train", "label": "GP Surrogate 학습", "type": "train"},
                    {"id": "opt", "label": "베이지안 최적화 6회", "type": "optimize"},
                    {"id": "deploy", "label": "Simulator 배포", "type": "deploy"},
                ]
                pairs = [("doe", "solve"), ("solve", "train"), ("train", "opt"), ("opt", "deploy")]
            await s.tool("dds-flow", "set_project_info", {"name": title, "description": desc})
            r = await s.tool("dds-flow", "propose_flow", {"title": title, "goal": body[:200], "nodes": nodes,
                                                         "edges": [{"source": x, "target": y} for x, y in pairs]})
            s.flow_id = r["flow_id"]
            await s.say("작업 흐름을 캔버스에 올렸습니다. 확인 후 **승인**을 눌러 주시면 실행을 시작하겠습니다.")
            return

        flow = await s.tool("dds-flow", "get_flow", {"flow_id": s.flow_id})
        if flow["status"] not in ("approved", "in_progress"):
            await s.say(f"현재 플로우 상태가 '{flow['status']}'입니다. 승인 후 다시 요청해 주세요.")
            return
        n_cases = 16 if s.mode == "beam3d" else 20
        title = "CAD·해석 모델 생성 후 케이스 해석 실행" if s.mode == "bracket" else "Mock 솔버로 케이스 실행"
        if not await self.ask_permission(s, title, {"study": s.mode, "cases": n_cases}):
            await s.say("실행 권한이 거부되어 중단했습니다.")
            return
        fid = s.flow_id
        node = lambda nid, st, d=None: s.tool("dds-flow", "update_node", {"flow_id": fid, "node_id": nid, "status": st, "detail": d})
        if s.mode == "bracket":
            await self._bracket(s, fid, node)
        elif s.mode == "beam3d":
            await self._beam3d(s, fid, node)
        else:
            await self._branin(s, fid, node)

    async def _branin(self, s, fid, node):
        await node("doe", "in_progress")
        study = await s.tool("dds-study", "create_study", {
            "name": "Branin", "adapter": "mock", "adapter_config": {"function": "branin", "delay_sec": 0.05},
            "variables": [{"name": "x1", "lower": -5, "upper": 10}, {"name": "x2", "lower": 0, "upper": 15}],
            "objectives": [{"name": "f", "goal": "min"}]})
        sid = study["study_id"]
        await s.tool("dds-study", "generate_doe", {"study_id": sid, "n": 20, "seed": 7, "flow_id": fid, "node_id": "doe"})
        await node("doe", "complete")
        await node("solve", "in_progress")
        r = await s.tool("dds-study", "run_cases", {"study_id": sid})
        await node("solve", "complete", f"완료 {r['counts'].get('complete', 0)}건")
        await node("train", "in_progress")
        m = await s.tool("dds-study", "train_surrogate", {"study_id": sid, "flow_id": fid, "node_id": "train"})
        await node("train", "complete", f"R²={m['metrics']['f']['r2']:.3f}")
        await node("opt", "in_progress")
        o = await s.tool("dds-study", "optimize", {"study_id": sid, "iterations": 6, "flow_id": fid, "node_id": "opt"})
        best = o["best"]
        await node("opt", "complete", f"최적 f={best['results']['f']:.3f}")
        await node("deploy", "in_progress")
        d = await s.tool("dds-study", "deploy_model", {"model_id": o["model_id"]})
        await node("deploy", "complete")
        await s.tool("dds-flow", "publish_artifact", {
            "kind": "markdown", "title": "Branin 최적화 요약", "flow_id": fid, "node_id": "deploy",
            "payload": {"text": f"### 결과\n- 최적 설계: x1={best['params']['x1']:.3f}, x2={best['params']['x2']:.3f}\n"
                                f"- 목적함수 f = **{best['results']['f']:.4f}** (이론 최소 0.398)\n"
                                f"- Simulator: 모델 #{d['model_id']}"}})
        await s.say(f"완료했습니다. 최적 f = {best['results']['f']:.4f} 이고, Simulator(모델 #{d['model_id']})를 배포했습니다.")

    async def _beam3d(self, s, fid, node):
        await node("doe", "in_progress")
        study = await s.tool("dds-study", "create_study", {
            "name": "Beam3D", "adapter": "mock", "adapter_config": {"function": "beam3d", "delay_sec": 0.05},
            "variables": [{"name": "width", "lower": 10, "upper": 40}, {"name": "height", "lower": 20, "upper": 80}],
            "objectives": [{"name": "max_stress", "goal": "min"}]})
        sid = study["study_id"]
        await s.tool("dds-study", "generate_doe", {"study_id": sid, "n": 16, "seed": 5, "flow_id": fid, "node_id": "doe"})
        await node("doe", "complete")
        await node("solve", "in_progress")
        r = await s.tool("dds-study", "run_cases", {"study_id": sid})
        await node("solve", "complete", f"완료 {r['counts'].get('complete', 0)}건 · 응력 필드 저장")
        await node("train", "in_progress")
        m = await s.tool("dds-study", "train_surrogate", {"study_id": sid, "outputs": ["max_stress", "mass", "deflection"],
                                                          "flow_id": fid, "node_id": "train"})
        await node("train", "complete", f"R²={m['metrics']['max_stress']['r2']:.3f}")
        await node("field", "in_progress")
        fm = await s.tool("dds-study", "train_field_model", {"study_id": sid, "field": "stress", "flow_id": fid, "node_id": "field"})
        await node("field", "complete", f"모드 {fm['info']['modes']}개 · 노드 {fm['nodes']:,}")
        await node("deploy", "in_progress")
        d = await s.tool("dds-study", "deploy_model", {"model_id": m["model_id"]})
        await node("deploy", "complete")
        await s.say(f"완료했습니다. 응력 필드와 처짐 형상을 학습했고, 3D Simulator(모델 #{d['model_id']})에서 폭·높이를 바꾸며 바로 볼 수 있습니다.")


    async def _bracket(self, s, fid, node):
        """브래킷 E2E: CAD → 해석 모델 → DOE·해석 → 예측 모델 + 형상·필드 학습 → 3D Simulator → 보고서."""
        base = {"thickness": 10.0, "rib_height": 25.0}

        async def script(node_id, name, title, params):
            r = await s.tool("dds-cae", "run_script", {
                "tool": "python", "script": (SCRIPTS / name).read_text(encoding="utf-8"), "filename": name,
                "args": [str(ROOT)], "params": params, "publish_images": False, "flow_id": fid, "node_id": node_id})
            if not r.get("ok"):
                raise RuntimeError(f"{name} 실패: {r.get('stderr_tail') or r.get('error')}")
            for img in sorted(Path(r["run_dir"], "images").glob("*.png")):
                await s.tool("dds-flow", "publish_image", {"path": str(img), "title": title, "caption": img.name,
                                                           "flow_id": fid, "node_id": node_id})
            return r["results"]

        # 1) CAD
        await node("cad", "in_progress")
        await s.say("브래킷 CAD를 파라메트릭으로 만듭니다(두께·리브 높이를 expression으로).\n\n", thought=True)
        cad = await script("cad", "bracket_cad.py", "브래킷 CAD 형상", base)
        await s.tool("dds-flow", "publish_artifact", {
            "kind": "table", "title": "CAD 파라미터 (expression)", "flow_id": fid, "node_id": "cad",
            "payload": {"columns": ["이름", "값", "설명"], "rows": [
                ["L", 200, "브래킷 길이 [mm]"], ["W", 60, "플레이트 폭 [mm]"], ["rib_t", 6, "리브 두께 [mm]"],
                ["thickness", base["thickness"], "플레이트 두께 [mm] — 설계변수"],
                ["rib_height", base["rib_height"], "리브 높이 [mm] — 설계변수"]]}})
        await node("cad", "complete", f"체적 {cad['volume_mm3'] / 1000:,.0f} cm³ · cad/bracket.stl")

        # 2) 해석 모델
        await node("pre", "in_progress")
        pre = await script("pre", "bracket_model.py", "해석 모델 (메쉬·경계조건·하중)", base)
        await node("pre", "complete", f"S3 {pre['elements']:,}요소 · 고정 {pre['fixed_nodes']}절점 · model/bracket.inp")

        # 3) DOE + 케이스 해석
        await node("doe", "in_progress")
        study = await s.tool("dds-study", "create_study", {
            "name": "브래킷 DOE", "adapter": "mock", "adapter_config": {"function": "bracket3d", "delay_sec": 0.05},
            "variables": [{"name": "thickness", "lower": 6, "upper": 14}, {"name": "rib_height", "lower": 10, "upper": 40}],
            "objectives": [{"name": "mass", "goal": "min"}], "constraints": [{"name": "max_stress", "upper": STRESS_LIMIT}]})
        sid = study["study_id"]
        await s.tool("dds-study", "generate_doe", {"study_id": sid, "n": 20, "seed": 11, "flow_id": fid, "node_id": "doe"})
        await node("doe", "complete")
        await node("solve", "in_progress")
        r = await s.tool("dds-study", "run_cases", {"study_id": sid})
        await node("solve", "complete", f"완료 {r['counts'].get('complete', 0)}건 · 응력 필드 저장")

        # 4) 예측 모델 + 형상·필드 학습
        await node("train", "in_progress")
        m = await s.tool("dds-study", "train_surrogate", {"study_id": sid, "outputs": ["mass", "max_stress", "deflection"],
                                                          "flow_id": fid, "node_id": "train"})
        r2 = {k: v["r2"] for k, v in m["metrics"].items()}
        await node("train", "complete", f"R² 질량 {r2['mass']:.3f} · 응력 {r2['max_stress']:.3f}")
        await node("field", "in_progress")
        fm = await s.tool("dds-study", "train_field_model", {"study_id": sid, "field": "stress", "flow_id": fid, "node_id": "field"})
        await node("field", "complete", f"모드 {fm['info']['modes']}개 · 노드 {fm['nodes']:,}")

        # 5) Simulator 배포
        await node("deploy", "in_progress")
        d = await s.tool("dds-study", "deploy_model", {"model_id": m["model_id"]})
        await node("deploy", "complete", f"모델 #{d['model_id']}")

        # 6) 보고서: 응력 제한을 만족하는 최경량 설계를 골라 결론을 쓰고, Word·HTML로 저장
        await node("report", "in_progress")
        st = await s.tool("dds-study", "get_study", {"study_id": sid, "include_cases": True})
        done = [c for c in st["cases"] if c["status"] == "complete"]
        ok = sorted((c for c in done if c["results"]["max_stress"] <= STRESS_LIMIT), key=lambda c: c["results"]["mass"])
        best = ok[0] if ok else min(done, key=lambda c: c["results"]["max_stress"])
        bp, br = best["params"], best["results"]
        pred = await s.tool("dds-study", "predict", {"model_id": m["model_id"], "params": base})
        mean = pred.get("mean", pred)
        await s.tool("dds-flow", "publish_artifact", {
            "kind": "markdown", "title": "결론 및 권고", "flow_id": fid, "node_id": "report",
            "payload": {"text": (
                "## 브래킷 설계 결론\n"
                f"- 하중 2 kN, 응력 제한 {STRESS_LIMIT:.0f} MPa 조건에서 DOE {len(done)}건 중 "
                f"**{len(ok)}건**이 제한을 만족했습니다.\n"
                f"- 최경량 설계: 두께 **{bp['thickness']:.1f} mm**, 리브 높이 **{bp['rib_height']:.1f} mm** → "
                f"질량 {br['mass']:.0f} g, 최대 응력 {br['max_stress']:.0f} MPa, 처짐 {br['deflection']:.2f} mm\n"
                f"- 기준 설계(두께 10, 리브 25) 예측: 질량 {mean.get('mass', 0):.0f} g, 응력 {mean.get('max_stress', 0):.0f} MPa\n"
                f"- 예측 모델 정확도(교차검증 R²): 질량 {r2['mass']:.3f}, 응력 {r2['max_stress']:.3f}, 처짐 {r2['deflection']:.3f}\n"
                "- 리브 높이가 응력·처짐을 가장 크게 줄이고 질량 증가는 작습니다. 두께보다 리브를 키우는 쪽이 효율적입니다.\n\n"
                "### 권고\n"
                "1. 최경량 설계점 부근에서 실제 해석으로 검증 해석을 1~2건 수행합니다.\n"
                f"2. 3D Simulator(모델 #{d['model_id']})로 설계팀이 두께·리브를 바꿔 응력 분포를 바로 확인하게 공유합니다.")}})
        rep = await s.tool("dds-flow", "make_report", {"formats": ["docx", "html"], "flow_id": fid})
        names = [Path(f).name for f in rep["files"]]
        await node("report", "complete", " · ".join(names))
        await s.say(
            "전체 과정을 마쳤습니다.\n\n"
            "- **CAD**: `cad/bracket.stl`, 파라미터 `cad/expressions.json`\n"
            "- **해석 모델**: `model/bracket.inp` (강재, 벽 고정, 끝단 2 kN)\n"
            f"- **DOE**: {len(done)}건 해석, 케이스별 응력 필드 저장\n"
            f"- **예측 모델**: 질량 R² {r2['mass']:.3f}, 응력 R² {r2['max_stress']:.3f} · 형상·응력 필드 모드 {fm['info']['modes']}개\n"
            f"- **Simulator**: 모델 #{d['model_id']} — 두께·리브를 바꾸며 3D 응력 분포를 바로 봅니다\n"
            f"- **보고서**: `reports/{names[0]}` 외 — '결과' 탭에서도 내려받을 수 있습니다\n\n"
            f"응력 {STRESS_LIMIT:.0f} MPa 이하 최경량 설계는 두께 {bp['thickness']:.1f} mm, 리브 {bp['rib_height']:.1f} mm "
            f"(질량 {br['mass']:.0f} g)입니다.")


def main():
    loop = asyncio.new_event_loop()
    agent = DemoAgent(loop)

    def reader():
        for line in sys.stdin:
            line = line.strip()
            if line:
                msg = json.loads(line)
                asyncio.run_coroutine_threadsafe(agent.handle(msg), loop)
        loop.call_soon_threadsafe(loop.stop)

    threading.Thread(target=reader, daemon=True).start()
    loop.run_forever()


if __name__ == "__main__":
    main()
