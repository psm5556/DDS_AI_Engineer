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
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

_write_lock = threading.Lock()


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
        if s.flow_id is None:
            await s.say("요청을 분석하고 있습니다. 전체 작업 흐름을 먼저 제안할게요.", thought=True)
            nodes = [
                {"id": "doe", "label": "DOE 20점 (LHS)", "type": "doe", "tool": "mock", "detail": "x1∈[-5,10], x2∈[0,15]"},
                {"id": "solve", "label": "Mock 솔버 실행", "type": "solve", "tool": "mock"},
                {"id": "train", "label": "GP Surrogate 학습", "type": "train"},
                {"id": "opt", "label": "베이지안 최적화 6회", "type": "optimize"},
                {"id": "deploy", "label": "Simulator 배포", "type": "deploy"},
            ]
            edges = [{"source": a, "target": b} for a, b in
                     [("doe", "solve"), ("solve", "train"), ("train", "opt"), ("opt", "deploy")]]
            r = await s.tool("dds-flow", "propose_flow", {"title": "Branin 최적화 데모", "goal": text[:200],
                                                         "nodes": nodes, "edges": edges})
            s.flow_id = r["flow_id"]
            await s.say("작업 흐름을 캔버스에 올렸습니다. 확인 후 **승인**을 눌러 주시면 실행을 시작하겠습니다.")
            return

        flow = await s.tool("dds-flow", "get_flow", {"flow_id": s.flow_id})
        if flow["status"] not in ("approved", "in_progress"):
            await s.say(f"현재 플로우 상태가 '{flow['status']}'입니다. 승인 후 다시 요청해 주세요.")
            return
        if not await self.ask_permission(s, "Mock 솔버로 케이스 실행", {"study": "Branin", "cases": 20}):
            await s.say("실행 권한이 거부되어 중단했습니다.")
            return

        fid = s.flow_id
        node = lambda nid, st, d=None: s.tool("dds-flow", "update_node", {"flow_id": fid, "node_id": nid, "status": st, "detail": d})
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
