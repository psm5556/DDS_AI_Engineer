"""데모 에이전트: Gemini CLI 없이 전체 경로(ACP → MCP 서버 → DDS 서버 → 웹앱)를 검증하기 위한 가짜 ACP 에이전트.
Gemini처럼 session/new로 받은 MCP 서버를 실제로 띄워서 툴을 호출하며, 정해진 시나리오
(Flow 제안 → 승인 대기 → 권한 요청 → mock DOE → GP 학습 → BO → Simulator 배포)를 수행한다.
시나리오는 요청에 따라 고른다(pick_mode): 세탁기 탈수 진동 · 브래킷 E2E · 3D 외팔보 · Branin.
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


def pick_mode(body: str) -> str:
    """요청에 맞는 시나리오: 세탁기 탈수 진동 · CAD~보고서 전체(브래킷) · 3D 형상(외팔보) · 기본(Branin)."""
    if "세탁기" in body or "탈수" in body:
        return "washer"
    if wants_e2e(body):
        return "bracket"
    return "beam3d" if ("3D" in body or "형상" in body) else "branin"


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
        self.study_id: int | None = None
        self.model_id: int | None = None
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
        """시나리오 4가지: '세탁기'/'탈수' 요청(탈수 진동 최적화~보고서) · CAD~보고서 전체(브래킷 E2E) ·
        '3D'/'형상' 요청(Beam3D 형상·필드 학습) · 기본(Branin 최적화)."""
        body = text.split("\n\n", 1)[-1]  # 앞의 [DDS 컨텍스트] 줄 제외
        if "[이전 대화 끝]" in body:  # 새 세션에 넘긴 지난 대화 요약도 제외(이번 요청만)
            body = body.split("[이전 대화 끝]", 1)[1].strip()
        if s.flow_id is None:
            s.mode = pick_mode(body)
            await s.say("요청을 분석하고 있습니다. 전체 작업 흐름을 먼저 제안할게요.", thought=True)
            if "[첨부 파일" in body:
                files = [ln.strip("- ").split(" (")[0] for ln in body.splitlines() if ln.startswith("- inputs/")]
                await s.say(f"첨부하신 파일을 작업 폴더에서 확인했습니다: {', '.join(files)}\n\n")
            if s.mode == "washer":
                title, desc = "드럼세탁기 탈수 진동 저감", "RecurDyn 탈수 모델 → DOE 60케이스 → 진동 예측 모델 → 최적화 → Simulator → 보고서"
                nodes = [
                    {"id": "model", "label": "탈수 해석 모델 불러오기", "type": "pre", "tool": "recurdyn",
                     "detail": "드럼·통 · 스프링 2 · 댐퍼 2 · 1,200 rpm"},
                    {"id": "doe", "label": "DOE 60케이스 (LHS)", "type": "doe", "detail": "스프링 강성·댐퍼 감쇠력·밸런서·스프링 위치"},
                    {"id": "solve", "label": "케이스 해석 60건", "type": "solve", "tool": "recurdyn", "detail": "통 진동 변위 추출"},
                    {"id": "train", "label": "진동 예측 모델", "type": "train", "detail": "GP"},
                    {"id": "opt", "label": "최적화 8회", "type": "optimize", "detail": "진동 최소 · 베이지안"},
                    {"id": "deploy", "label": "Simulator 배포", "type": "deploy"},
                    {"id": "report", "label": "보고서 작성", "type": "post", "detail": "Word · HTML"},
                ]
                pairs = [("model", "doe"), ("doe", "solve"), ("solve", "train"), ("train", "opt"), ("opt", "deploy"), ("deploy", "report")]
            elif s.mode == "bracket":
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
        if flow["status"] == "complete":  # 끝난 뒤의 이어지는 요청: 보고서 다시 쓰기 · Output 추가
            await self._follow_up(s, body)
            return
        if flow["status"] not in ("approved", "in_progress"):
            await s.say(f"현재 플로우 상태가 '{flow['status']}'입니다. 승인 후 다시 요청해 주세요.")
            return
        n_cases = {"beam3d": 16, "washer": 60}.get(s.mode, 20)
        title = {"bracket": "CAD·해석 모델 생성 후 케이스 해석 실행",
                 "washer": "RecurDyn 탈수 해석 60케이스 실행"}.get(s.mode, "Mock 솔버로 케이스 실행")
        if not await self.ask_permission(s, title, {"study": s.mode, "cases": n_cases}):
            await s.say("실행 권한이 거부되어 중단했습니다.")
            return
        fid = s.flow_id
        node = lambda nid, st, d=None: s.tool("dds-flow", "update_node", {"flow_id": fid, "node_id": nid, "status": st, "detail": d})
        if s.mode == "washer":
            await self._washer(s, fid, node)
        elif s.mode == "bracket":
            await self._bracket(s, fid, node)
        elif s.mode == "beam3d":
            await self._beam3d(s, fid, node)
        else:
            await self._branin(s, fid, node)

    async def _washer(self, s, fid, node):
        """드럼세탁기 탈수 진동: 해석 모델 → DOE 60·해석 → 진동 예측 모델 → 베이지안 최적화 → Simulator → 보고서."""
        variables = [{"name": "spring_k", "lower": -10, "upper": 30}, {"name": "damper_c", "lower": -10, "upper": 50},
                     {"name": "balancer_m", "lower": -20, "upper": 20}, {"name": "spring_pos", "lower": -20, "upper": 20}]
        await node("model", "in_progress")
        await s.say("RecurDyn 탈수 모델에서 바꿀 수 있는 부품 값을 설계 변수로 잡습니다.\n\n", thought=True)
        await s.tool("dds-flow", "publish_artifact", {
            "kind": "table", "title": "탈수 해석 모델과 설계 변수", "flow_id": fid, "node_id": "model",
            "payload": {"columns": ["이름", "범위·값", "설명"], "rows": [
                ["rpm", "1,200 rpm", "탈수 회전수 — 해석 조건"], ["unbalance", "0.5 kg", "세탁물 편심 — 해석 조건"],
                ["spring_k", "-10 ~ +30 %", "스프링 강성 변화 — 설계 변수"], ["damper_c", "-10 ~ +50 %", "댐퍼 감쇠력 변화 — 설계 변수"],
                ["balancer_m", "-20 ~ +20 %", "밸런서 질량 변화 — 설계 변수"], ["spring_pos", "-20 ~ +20 mm", "스프링 위치 — 설계 변수"],
                ["vibration", "mm", "통 진동 변위 — 줄일 값"]]}})
        await node("model", "complete", "설계 변수 4개 · 해석 조건 1,200 rpm")

        await node("doe", "in_progress")
        study = await s.tool("dds-study", "create_study", {
            "name": "탈수 진동", "adapter": "mock", "adapter_config": {"function": "washer", "delay_sec": 0.05, "outputs": ["vibration"]},
            "variables": variables, "objectives": [{"name": "vibration", "goal": "min"}],
            "outputs": [{"name": "vibration", "note": "통 진동 변위 [mm]"}]})
        sid = s.study_id = study["study_id"]
        await s.tool("dds-study", "generate_doe", {"study_id": sid, "n": 60, "seed": 12, "flow_id": fid, "node_id": "doe"})
        await node("doe", "complete")
        await node("solve", "in_progress")
        r = await s.tool("dds-study", "run_cases", {"study_id": sid})
        await node("solve", "complete", f"완료 {r['counts'].get('complete', 0)}건")
        await node("train", "in_progress")
        m = await s.tool("dds-study", "train_surrogate", {"study_id": sid, "flow_id": fid, "node_id": "train"})
        await node("train", "complete", f"R²={m['metrics']['vibration']['r2']:.3f}")
        await node("opt", "in_progress")
        o = await s.tool("dds-study", "optimize", {"study_id": sid, "iterations": 8, "flow_id": fid, "node_id": "opt"})
        bp, br = o["best"]["params"], o["best"]["results"]
        await node("opt", "complete", f"진동 {br['vibration']:.2f} mm")
        await node("deploy", "in_progress")
        d = await s.tool("dds-study", "deploy_model", {"model_id": o["model_id"]})
        await node("deploy", "complete", f"모델 #{d['model_id']}")

        s.model_id = o["model_id"]

        # 보고서: 작업 흐름·결과를 읽고 AI가 새로 쓴다(보고서 탭) → Word·HTML 파일도 저장
        await node("report", "in_progress")
        rep = await self._washer_report(s)
        files = await s.tool("dds-flow", "make_report", {"formats": ["docx", "html"]})
        await node("report", "complete", f"보고서 탭 · Word·HTML {len(files['files'])}개 저장")
        await s.say(
            "탈수 진동 최적화를 마쳤습니다.\n\n"
            f"- 진동 {rep['now']:.1f} mm → {rep['best']:.1f} mm (**{rep['cut']:.0f}% 감소**), "
            f"스프링 강성 {bp['spring_k']:+.0f}% · 댐퍼 감쇠력 {bp['damper_c']:+.0f}%\n"
            "- 보고서는 오른쪽 **보고서** 탭에 있습니다. 다음으로 최적 설계 검증 해석을 권합니다.")

    async def _washer_report(self, s) -> dict:
        """세탁기 보고서: 자료를 읽고 결론·해석을 쓰며, 결과(표·그래프)를 본문에 넣는다."""
        ctx = await s.tool("dds-flow", "get_report_context", {})
        st = next(x for x in ctx["studies"] if x["study_id"] == s.study_id)

        def art(part: str, kind: str | None = None) -> str:
            return next((r["embed"] for r in reversed(ctx["results"]) if part in r["title"] and (kind is None or r["kind"] == kind)), "")

        bp, br = st["best"]["params"], st["best"]["results"]
        pred = await s.tool("dds-study", "predict", {"model_id": s.model_id, "params": {v["name"]: 0.0 for v in st["inputs"]}})
        now = pred.get("mean", pred).get("vibration", 0.0)
        cut = (1 - br["vibration"] / now) * 100 if now else 0.0
        r2 = (st["models"][0]["r2"] if st["models"] else {}).get("vibration", 0.0)
        n = sum(st["cases"].values())
        flow = next(f for f in ctx["flows"] if f["flow_id"] == s.flow_id)
        steps = "\n".join(f"{x['no']}. {x['label']}{' — ' + x['detail'] if x['detail'] else ''}" for x in flow["steps"])
        trade = ""
        if any(o["name"] == "floor_force" and o["extracted"] for o in st["outputs"]):
            cs = await s.tool("dds-study", "get_study", {"study_id": s.study_id, "include_cases": True})
            vals = [c["results"]["floor_force"] for c in cs["cases"] if "floor_force" in (c.get("results") or {})]
            fb = br.get("floor_force")
            if fb and vals:
                trade = (f"- 바닥 전달력은 최적 설계에서 **{fb:.0f} N**입니다(전체 케이스 {min(vals):.0f}~{max(vals):.0f} N). "
                         "스프링·댐퍼를 단단하게 할수록 진동은 줄고 전달력은 커지므로 함께 보고 정해야 합니다.\n")
        body = (
            "## 요약\n"
            f"- 탈수 1,200 rpm·편심 0.5 kg 조건에서 통 진동이 현재 설계 **{now:.1f} mm** → 최적 설계 **{br['vibration']:.1f} mm**로 "
            f"**{cut:.0f}% 줄었습니다**.\n"
            f"- 최적 설계는 스프링 강성 **{bp['spring_k']:+.0f}%**, 댐퍼 감쇠력 **{bp['damper_c']:+.0f}%** 조합입니다. "
            f"밸런서({bp['balancer_m']:+.0f}%)·스프링 위치({bp['spring_pos']:+.0f} mm)의 영향은 작습니다.\n"
            f"- 진동 예측 모델의 정확도는 R² {r2:.3f}입니다(1에 가까울수록 해석과 잘 맞음). Simulator에서 바로 쓸 수 있습니다.\n"
            + trade
            + "\n## 목적과 조건\n"
            "- 목적: 탈수 중 통 진동을 줄이는 스프링·댐퍼 설계\n"
            "- 조건: 탈수 1,200 rpm, 세탁물 편심 0.5 kg, RecurDyn 탈수 해석 모델\n"
            f"{art('설계 변수', 'table')}\n"
            "\n## 수행 내용\n" + steps + "\n"
            f"\n## 결과\n### 설계 공간 탐색\n{art('DOE 샘플 분포')}\n"
            f"해석 {n}건 중 처음 60건은 LHS로 설계 범위를 고르게 채웠고, 나머지는 최적화가 고른 점입니다.\n"
            f"\n### 진동 예측 모델\n{art('예측 정확도')}\n"
            f"교차검증에서 예측값이 해석값과 거의 한 줄 위에 놓여(R² {r2:.3f}) 설계 범위 안에서는 해석 대신 예측 모델을 써도 됩니다.\n"
            f"\n### 최적화\n{art('수렴 이력')}\n"
            f"베이지안 최적화 8회 동안 진동이 {br['vibration']:.2f} mm까지 내려갔고, 마지막 몇 회는 거의 바뀌지 않아 수렴했다고 봅니다.\n"
            "\n## 결론과 권고\n"
            f"1. 스프링 강성 {bp['spring_k']:+.0f}%, 댐퍼 감쇠력 {bp['damper_c']:+.0f}%를 설계안으로 제안합니다.\n"
            "2. 최적 설계로 실제 해석을 1건 더 돌려 예측을 확인합니다.\n"
            "3. Simulator로 회전수·부품 값을 바꿔 보며 설계팀과 함께 검토합니다.\n"
            "\n## 한계\n- 이 데모의 해석 값은 예시 식으로 만든 값입니다. 실제 과제에서는 RecurDyn 해석 결과로 바뀝니다.\n")
        await s.tool("dds-flow", "write_report", {"title": "드럼세탁기 탈수 진동 저감 보고서", "body": body, "flow_id": s.flow_id})
        return {"now": now, "best": br["vibration"], "cut": cut}

    async def _generic_report(self, s):
        """다른 시나리오의 보고서: 스터디 요약 + 단계 순서대로 결과를 넣는다."""
        ctx = await s.tool("dds-flow", "get_report_context", {})
        flow = next(f for f in ctx["flows"] if f["flow_id"] == s.flow_id)
        lines = ["## 요약"]
        for st in ctx["studies"]:
            best = st["best"]
            if not best:
                continue
            r2 = ", ".join(f"{k} {v:.3f}" for k, v in (st["models"][0]["r2"] if st["models"] else {}).items())
            obj = next((o["name"] for o in st["outputs"] if o["role"].startswith("목적")), next(iter(best["results"]), ""))
            lines.append(f"- {st['name']}: 해석 {st['cases'].get('complete', 0)}건, 최적 {obj} **{best['results'].get(obj, 0):.4g}** "
                         f"({', '.join(f'{k}={v:.3g}' for k, v in best['params'].items())})" + (f", 예측 모델 R² {r2}" if r2 else ""))
        lines += ["", "## 수행 내용", *[f"{x['no']}. {x['label']}{' — ' + x['detail'] if x['detail'] else ''}" for x in flow["steps"]],
                  "", "## 결과"]
        for x in flow["steps"]:
            embeds = [r["embed"] for r in ctx["results"] if r["step"] == f"{x['no']}단계 · {x['label']}"]
            if embeds:
                lines += [f"### {x['no']}단계 · {x['label']}", *embeds, ""]
        lines += ["## 결론과 권고", "1. 최적 설계점에서 실제 해석으로 검증 해석을 1건 더 수행합니다.",
                  "2. Simulator로 설계 변수를 바꿔 보며 설계팀과 함께 검토합니다."]
        await s.tool("dds-flow", "write_report", {"title": f"{flow['title']} 보고서", "body": "\n".join(lines), "flow_id": s.flow_id})

    async def _follow_up(self, s, body: str):
        """작업 흐름을 마친 뒤의 요청: Output 추가(세탁기: 바닥 전달력) · 보고서 다시 쓰기."""
        if any(k in body for k in ("Output", "output", "출력", "전달력")):
            if s.mode != "washer" or not s.study_id:
                await s.say("이 데모에서는 세탁기 시나리오에서 Output 추가(바닥 전달력)를 해 볼 수 있습니다.")
                return
            r = await s.tool("dds-study", "add_outputs", {"study_id": s.study_id,
                                                          "outputs": [{"name": "floor_force", "note": "바닥 전달력 [N]"}]})
            await s.say(f"Output에 바닥 전달력(floor_force)을 추가했습니다. 이미 해석한 {r['extracted']['floor_force']}건에서 뽑았고, "
                        "이후 케이스도 해석할 때 함께 뽑습니다. '데이터' 탭에서 볼 수 있습니다.")
            return
        if "보고서" in body:
            await (self._washer_report(s) if s.mode == "washer" else self._generic_report(s))
            await s.say("최신 결과로 보고서를 다시 썼습니다. 오른쪽 **보고서** 탭에서 볼 수 있습니다.")
            return
        await s.say("이 작업 흐름은 끝났습니다. '보고서 다시 써 줘'나 'Output 추가'를 요청할 수 있습니다.")

    async def _branin(self, s, fid, node):
        await node("doe", "in_progress")
        study = await s.tool("dds-study", "create_study", {
            "name": "Branin", "adapter": "mock", "adapter_config": {"function": "branin", "delay_sec": 0.05},
            "variables": [{"name": "x1", "lower": -5, "upper": 10}, {"name": "x2", "lower": 0, "upper": 15}],
            "objectives": [{"name": "f", "goal": "min"}]})
        sid = s.study_id = study["study_id"]
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
        sid = s.study_id = study["study_id"]
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
        sid = s.study_id = study["study_id"]
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
        await self._generic_report(s)  # 결론 요약(위 markdown)과 단계별 결과를 넣어 보고서 탭에 쓴다
        rep = await s.tool("dds-flow", "make_report", {"formats": ["docx", "html"], "flow_id": fid})
        names = [Path(f).name for f in rep["files"]]
        await node("report", "complete", f"보고서 탭 · Word·HTML {len(names)}개 저장")
        # 대화에는 핵심만: 파일 경로·단계별 내용은 작업 흐름·보고서 탭에 있다
        await s.say(
            "전체 과정을 마쳤습니다.\n\n"
            f"- 응력 {STRESS_LIMIT:.0f} MPa 이하 최경량 설계: 두께 {bp['thickness']:.1f} mm, 리브 {bp['rib_height']:.1f} mm "
            f"(질량 {br['mass']:.0f} g)\n"
            f"- 예측 모델 R² 질량 {r2['mass']:.3f} · 응력 {r2['max_stress']:.3f}, 3D Simulator로 응력 분포를 바로 봅니다\n"
            "- 보고서는 오른쪽 **보고서** 탭에 있습니다.")


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
