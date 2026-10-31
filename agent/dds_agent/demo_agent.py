"""데모 에이전트: Gemini CLI 없이 전체 경로(ACP → MCP 서버 → DDS 서버 → 웹앱)를 검증하기 위한 가짜 ACP 에이전트.
Gemini처럼 session/new로 받은 MCP 서버를 실제로 띄워서 툴을 호출하며, 정해진 시나리오
(Flow 제안 → 승인 대기 → 권한 요청 → mock DOE → GP 학습 → BO → Simulator 배포)를 수행한다.
시나리오는 요청에 따라 고른다(pick_mode): 세탁기 탈수 진동 · 브래킷 E2E · 3D 외팔보 · Branin.
표·해석 폴더·단순 질문은 Flow 없이 바로 처리한다(pick_entry). 그래야 "표로 Simulator"를 시험할 때 엉뚱하게 Branin을 돌려 거짓 통과하지 않는다.
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


TABLE_EXT = (".csv", ".tsv", ".xlsx", ".xlsm")
_QUOTED = re.compile(r'"([^"\n]+)"|“([^”\n]+)”')
_ABS_PATH = re.compile(r'(?:[A-Za-z]:[\\/]|\\\\)[^\s"\'<>|]*')
_TABLE_WORD = re.compile(r"(?i)(?<![a-z])csv(?![a-z])|xlsx|엑셀|(?:^|[\s,])표(?:로| 데이터| 파일|를|에)")  # 한글 조사가 붙으므로 \b를 쓰지 않는다. "목표로"는 표가 아니다
_FOLDER_WORD = ("해석 폴더", "결과 폴더", "케이스 폴더", "이미 해석", "해석 결과가 있", "해석해 둔", "돌려 둔")
_DOING_WORD = ("돌려", "돌리", "해석해", "실행", "최적화", "만들", "배포", "학습", "생성", "구성", "작성", "찾아", "추가", "DOE", "다시")
_ASKING_WORD = ("?", "？", "알려", "뭐", "무엇", "몇 ", "몇개", "어떻게", "어디", "얼마", "있나", "있어", "보여")
_TAKEOVER_WORD = ("이어받", "이어 받")
_RERUN_WORD = ("신규 모델", "새 모델", "지난번처럼", "지난 흐름")
_PLAN_WORD = ("DOE 계획", "계획만")
_MODEL_WORD = ("배포한 모델", "되돌려", "공유 링크", "이 스터디로", "예측해 줘", "예측해줘")  # 이미 있는 모델로 하는 일. 새 대화에서 말해도 해석을 돌리지 않는다
_RANGE = re.compile(r"([A-Za-z_]\w*)\s*(-?\d+(?:\.\d+)?)\s*~\s*(-?\d+(?:\.\d+)?)")
_OBJECTIVE = re.compile(r"([A-Za-z_]\w*)\s*(?:을|를|이|가)?\s*(최소|최대)")
PLANNED_MODES = ("connect", "rerun", "takeover")  # 승인을 받은 뒤 _run_planned로 도는 일


def request_text(text: str) -> str:
    """프롬프트에서 이번 요청만 남긴다. [DDS 컨텍스트]는 세션을 새로 연 첫 턴에만 붙으므로,
    그때만 앞 문단을 뗀다. 무조건 떼면 후속 턴의 첫 문단이 사라진다."""
    body = text
    if body.startswith("[DDS 컨텍스트]"):
        body = body.split("\n\n", 1)[-1]
    if "[이전 대화 끝]" in body:  # 새 세션에 넘긴 지난 대화 요약도 제외(이번 요청만)
        body = body.split("[이전 대화 끝]", 1)[1]
    return body.strip()


def attached_files(body: str) -> list[str]:
    return [ln.strip("- ").split(" (")[0] for ln in body.splitlines() if ln.startswith("- inputs/")]


def _named_paths(body: str) -> list[str]:
    """요청에 적힌 경로. 한글 조사가 붙어 있을 수 있어("D:\\runs에") 없는 경로면 뒤의 한글을 뗀다."""
    found = [a or b for a, b in _QUOTED.findall(body) if re.match(r"([A-Za-z]:[\\/]|\\\\|/)", a or b)]
    for token in _ABS_PATH.findall(_QUOTED.sub(" ", body)):
        token = token.rstrip(".,;:)")
        if not Path(token).exists():
            token = re.sub(r"[가-힣]+$", "", token)
        found.append(token)
    return found


def pick_entry(body: str) -> tuple[str, str | None]:
    """사용자가 무엇을 가지고 왔는가: table·folder(가진 데이터로 바로) · question(바로 답) · flow(해석부터, Flow 승인).
    경로를 함께 돌려준다. 데이터를 말했는데 경로가 없으면 None이다."""
    table = next((f for f in attached_files(body) if f.lower().endswith(TABLE_EXT)), None)
    if table:
        return "table", table
    for p in _named_paths(body):
        if p.lower().endswith(TABLE_EXT):
            return "table", p
        if Path(p).is_dir() or not Path(p).suffix:  # bracket.prt 같은 모델 파일은 가진 데이터가 아니라 해석할 대상이다
            return "folder", p
    if any(k in body for k in _TAKEOVER_WORD):
        return "takeover", None
    if any(k in body for k in _RERUN_WORD):
        return "rerun", None
    if any(k in body for k in _PLAN_WORD):
        return "plan", None
    if "해석 방법" in body and "연결" in body:  # DOE 계획만 뽑아 둔 스터디에 나중에 해석 방법을 붙인다
        return "connect", None
    if "묶음" in body and "올려" in body:
        return "recipe", None
    asking = any(k in body for k in _ASKING_WORD) and not any(k in body for k in _DOING_WORD)
    if any(k in body for k in _MODEL_WORD) and not asking and ("만들" not in body or "공유" in body):
        return "model", None
    if _TABLE_WORD.search(body):
        return "table", None
    if any(k in body for k in _FOLDER_WORD):
        return "folder", None
    if any(k in body for k in _ASKING_WORD) and not any(k in body for k in _DOING_WORD) and pick_mode(body) == "branin":
        return "question", None
    return "flow", None


def tool_error_text(e: Exception) -> str:
    """MCP 도구 오류에서 사용자에게 보여 줄 문구만 남긴다."""
    return re.sub(r"서버 오류 \d+: ", "", re.sub(r"^Error executing tool \w+:\s*", "", str(e))).strip()


def send(msg: dict):
    with _write_lock:
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False) + "\n")
        sys.stdout.flush()


class DemoSession:
    def __init__(self, sid: str, mcp_defs: list[dict]):
        self.sid, self.mcp_defs = sid, mcp_defs
        self.stack = AsyncExitStack()
        self.clients: dict[str, ClientSession] = {}
        self.pending_table: dict[str, Any] | None = None  # 붙여 넣은 표: 출력이 무엇인지 물어 둔 상태
        self.flow_id: int | None = None
        self.study_id: int | None = None
        self.model_id: int | None = None
        self.from_data = False  # 이 대화의 스터디가 표·해석 폴더에서 온 것이다(돌릴 해석이 없다)
        self.mode = "branin"
        self.plan: dict[str, Any] = {}  # 승인 뒤에 할 일(PLANNED_MODES)
        self.revising = False  # 반려된 흐름을 고쳐 다시 제안하는 중(같은 시나리오를 유지한다)
        self.points: int | None = None  # 사용자가 고친 DOE 점 수
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
        """가진 데이터·단순 질문은 Flow 없이 바로 처리한다. 해석을 새로 돌리는 요청만 Flow 시나리오 4가지로 간다:
        '세탁기'/'탈수' 요청(탈수 진동 최적화~보고서) · CAD~보고서 전체(브래킷 E2E) ·
        '3D'/'형상' 요청(Beam3D 형상·필드 학습) · 기본(Branin 최적화)."""
        body = request_text(text)
        entry, target = pick_entry(body)
        approved = "승인했습니다" in body or "승인되었습니다" in body
        if not approved and s.pending_table and await self._outputs_answer(s, body):
            return
        if not approved and entry == "table":
            await self._from_table(s, target, body)
            return
        if not approved and entry == "folder":
            await self._from_folder(s, target)
            return
        if not approved and entry == "question":  # Flow를 검토하는 중이어도 답한다. 승인을 기다리느라 대화가 막히지 않는다
            await self._answer(s, body)
            return
        if not approved and entry == "plan":
            await self._plan_only(s, body)
            return
        if not approved and entry == "connect":
            await self._connect_later(s)
            return
        if not approved and entry == "recipe":
            await self._save_recipe(s, body)
            return
        if not approved and entry == "rerun":
            await self._same_flow_again(s, body)
            return
        if not approved and entry == "takeover":
            await self._takeover(s, body)
            return
        if not approved and entry == "model" and await self._model_work(s, body):
            return
        if not approved and s.from_data:
            await self._data_follow_up(s, body)
            return
        if not approved and s.flow_id is None and "보고서" in body and pick_mode(body) == "branin" and not any(k in body for k in ("돌려", "해석해", "DOE")):
            await self._report_without_flow(s)  # 있는 결과로 쓰는 보고서: 해석이 없으니 승인이 필요 없다
            return
        if s.flow_id is None:
            s.mode = s.mode if s.revising else pick_mode(body)
            s.revising = False
            await s.say("요청을 분석하고 있습니다. 전체 작업 흐름을 먼저 제안할게요.", thought=True)
            if "[첨부 파일" in body:
                await s.say(f"첨부하신 파일을 작업 폴더에서 확인했습니다: {', '.join(attached_files(body))}\n\n")
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
                    {"id": "doe", "label": f"DOE {s.points or 20}점 (LHS)", "type": "doe", "tool": "mock", "detail": "x1∈[-5,10], x2∈[0,15]"},
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
        if flow["status"] == "rejected":  # 반려는 끝이 아니다. 고칠 점을 받아 다시 제안한다(FlowCard가 그렇게 안내한다)
            n = re.search(r"(\d+)\s*점", body)
            if "반려했습니다" in body or not any(k in body for k in _DOING_WORD):
                await s.say("작업 흐름을 반려하셔서 아무것도 돌리지 않았습니다. 무엇을 바꿀까요? 예: 'DOE를 10점으로 줄여서 다시 제안해 줘'")
                return
            s.flow_id = None
            if s.mode in PLANNED_MODES:  # 묶어 둔 계획은 처음 요청에 달려 있다. 고치려면 그 요청부터 다시 받는다
                s.plan, s.mode = {}, "branin"
                await s.say("반려한 작업은 돌리지 않았습니다. 바꾼 내용으로 처음 요청을 다시 말씀해 주시면 새로 제안합니다.")
                return
            s.points, s.revising = int(n.group(1)) if n else s.points, True
            await self.scenario(s, text)
            return
        if flow["status"] not in ("approved", "in_progress"):
            await s.say("작업 흐름이 아직 승인 전입니다. 카드에서 **승인**을 누르면 시작합니다. 고칠 점이 있으면 반려한 뒤 입력창에 적어 주세요.")
            return
        n_cases = {"beam3d": 16, "washer": 60}.get(s.mode, 20)
        title = s.plan.get("permission") if s.mode in PLANNED_MODES else None
        title = title or {"bracket": "CAD·해석 모델 생성 후 케이스 해석 실행",
                          "washer": "RecurDyn 탈수 해석 60케이스 실행"}.get(s.mode, "Mock 솔버로 케이스 실행")
        if not await self.ask_permission(s, title, {"study": s.mode, "cases": n_cases}):
            await s.say("실행 권한이 거부되어 중단했습니다.")
            return
        fid = s.flow_id
        node = lambda nid, st, d=None: s.tool("dds-flow", "update_node", {"flow_id": fid, "node_id": nid, "status": st, "detail": d})
        if s.mode in PLANNED_MODES:
            await self._run_planned(s, fid, node)
        elif s.mode == "washer":
            await self._washer(s, fid, node)
        elif s.mode == "bracket":
            await self._bracket(s, fid, node)
        elif s.mode == "beam3d":
            await self._beam3d(s, fid, node)
        else:
            await self._branin(s, fid, node)

    # ---------- 가진 데이터로 바로: Flow 없음, 해석 없음 ----------
    async def _from_table(self, s, path: str | None, body: str):
        if not path:
            await s.say("표가 아직 없습니다. 엑셀에서 표를 복사해 입력창에 붙여 넣어 주시면 바로 예측 모델과 Simulator를 만듭니다.")
            return
        adding = s.study_id is not None and s.from_data and any(k in body for k in ("추가", "더 넣", "더해", "덧붙"))
        # 입력·출력이 파일 둘로 나뉜 표: 둘째 파일을 출력으로 맞춰 붙인다
        tables = [p for p in [*attached_files(body), *_named_paths(body)] if p.lower().endswith(TABLE_EXT)]
        extra: dict[str, Any] = {"outputs_path": tables[1]} if len(tables) > 1 else {}
        await s.say("표의 열을 확인하고 입력·출력을 나눕니다. 해석을 돌리지 않으므로 바로 진행합니다.", thought=True)
        try:
            info = await self._finished(s, "dds-study", "inspect_table", {"path": path, **extra})
            if "붙여넣은_표" in Path(path).name and not adding:
                # 붙여 넣은 표에서 묻는 것은 어느 열이 출력인가 하나뿐이다. 요청에 열 이름이 있으면 묻지 않는다(INSTRUCTIONS 표 규칙)
                named = [c["name"] for c in info["columns"] if c["name"] in body.split("[첨부 파일")[0]]
                if not named:
                    s.pending_table = {"path": path, "columns": [c["name"] for c in info["columns"]], "guess": info["outputs"]}
                    guess = ", ".join(info["outputs"])
                    await s.say(f"표 {info['n_rows']}행을 읽었습니다. 결과(출력) 열이 {guess} 맞나요? 나머지 숫자 열은 입력으로 씁니다." if guess
                                else f"표 {info['n_rows']}행을 읽었습니다. 어느 열이 결과(출력)인가요? 나머지 숫자 열은 입력으로 씁니다.")
                    return
                extra["outputs"] = named
            if "계획" in body:  # 밖에서 해석해 온 DOE 계획표: 대기 중인 점을 채운다
                planned = await self._pending_study(s)
                if planned:
                    s.study_id, adding = planned["study_id"], True
            if adding:
                rep = await self._finished(s, "dds-study", "import_table", {"path": path, "study_id": s.study_id, **extra})
            else:
                await s.tool("dds-flow", "set_project_info", {"name": f"{Path(path).stem} 예측 모델"[:60], "description": "표에서 바로 만든 예측 모델과 Simulator"})
                rep = await self._finished(s, "dds-study", "import_table", {"path": path, **extra})
        except RuntimeError as e:
            await s.say(f"표를 불러오지 못했습니다. {tool_error_text(e)}")
            return
        s.from_data, s.study_id = True, rep["study_id"]
        what = f"표 {rep['registered'] + rep.get('filled_pending', 0)}행을 더해" if adding else f"표 {info['n_rows']}행으로"
        await self._train_and_deploy(s, rep, what)

    async def _outputs_answer(self, s, body: str) -> bool:
        """물어 둔 출력의 답이면 그대로 진행한다. 다른 요청이면 묻지 않은 것으로 하고 그 요청을 처리한다."""
        p, s.pending_table = s.pending_table, None
        named = [n for n in p["columns"] if n in body]
        if not named and not any(k in body for k in ("맞", "네", "응", "예", "그래")):
            return False
        try:
            await s.tool("dds-flow", "set_project_info", {"name": "붙여 넣은 표 예측 모델", "description": "표에서 바로 만든 예측 모델과 Simulator"})
            rep = await self._finished(s, "dds-study", "import_table", {"path": p["path"], "outputs": named or p["guess"]})
        except RuntimeError as e:
            await s.say(f"표를 불러오지 못했습니다. {tool_error_text(e)}")
            return True
        s.from_data, s.study_id = True, rep["study_id"]
        await self._train_and_deploy(s, rep, f"표 {rep['registered']}행으로")
        return True

    async def _pending_study(self, s) -> dict | None:
        """해석을 기다리는 점이 남은 스터디(DOE 계획만 뽑아 둔 것). 2주 뒤 새 대화에서 말해도 찾을 수 있어야 한다."""
        studies = (await s.tool("dds-study", "list_studies", {}))["studies"]
        return next((x for x in studies if x["counts"].get("pending")), None)

    # ---------- DOE 계획만 뽑고 나중에 해석 연결 ----------
    async def _plan_only(self, s, body: str):
        found = _RANGE.findall(body)
        n = re.search(r"(\d+)\s*(?:점|개|건)", body)
        if not found:
            await s.say("어떤 변수를 어떤 범위로 뽑을까요? 예: x1 -5~10, x2 0~15")
            return
        variables = [{"name": a, "lower": float(lo), "upper": float(hi)} for a, lo, hi in found]
        # 해석 방법을 정하지 않은 스터디다. 돌리면 이유와 할 일을 듣는다
        st = await s.tool("dds-study", "create_study", {"name": "DOE 계획", "variables": variables, "objectives": [{"name": "f", "goal": "min"}]})
        s.study_id = st["study_id"]
        r = await s.tool("dds-study", "generate_doe", {"study_id": s.study_id, "n": int(n.group(1)) if n else 12, "seed": 3})
        out = await s.tool("dds-study", "export_doe", {"study_id": s.study_id})
        await s.say(f"DOE 계획 {r['created']}점을 뽑아 CSV로 저장했습니다: {out['path']}\n해석은 돌리지 않았습니다. 밖에서 해석한 값을 표로 주시면 채우고, 이 PC에서 돌리려면 해석 방법을 연결해 달라고 말씀해 주세요.")

    async def _connect_later(self, s):
        target = await self._pending_study(s)
        if not target:
            await s.say("해석을 기다리는 스터디가 없습니다 → DOE 계획을 먼저 뽑거나 어느 스터디인지 알려 주세요.")
            return
        await s.tool("dds-study", "set_study_adapter", {"study_id": target["study_id"], "adapter": "mock", "adapter_config": {"function": "branin", "delay_sec": 0.02}})
        s.study_id, s.mode = target["study_id"], "connect"
        n = target["counts"]["pending"]
        s.plan = {"permission": f"대기 중인 {n}점 해석 실행"}
        r = await s.tool("dds-flow", "propose_flow", {"title": f"{target['name']} 이어서 해석", "goal": f"대기 중인 {n}점을 이 PC에서 해석합니다", "nodes": self._solve_nodes(f"대기 {n}점"),
                                                     "edges": [{"source": "solve", "target": "train"}, {"source": "train", "target": "deploy"}]})
        s.flow_id = r["flow_id"]
        await s.say(f"해석 방법을 연결했습니다. 대기 중인 {n}점을 돌리는 작업 흐름을 올렸습니다. **승인**을 눌러 주시면 시작합니다.")

    @staticmethod
    def _solve_nodes(detail: str) -> list[dict]:
        return [{"id": "solve", "label": "케이스 해석", "type": "solve", "detail": detail},
                {"id": "train", "label": "예측 모델 학습", "type": "train"}, {"id": "deploy", "label": "Simulator 배포", "type": "deploy"}]

    async def _run_planned(self, s, fid, node):
        """승인받은 뒤: (있으면 DOE) → 해석 → 학습 → 배포."""
        sid = s.study_id
        if s.plan.get("doe"):
            await node("doe", "in_progress")
            await s.tool("dds-study", "generate_doe", {"study_id": sid, "n": s.plan["doe"], "seed": s.plan.get("seed", 21), "flow_id": fid, "node_id": "doe"})
            await node("doe", "complete")
        await node("solve", "in_progress")
        try:
            r = await s.tool("dds-study", "run_cases", {"study_id": sid, "flow_id": fid})
        except RuntimeError as e:  # 승인 확인에 걸리면 돌리지 않은 것이다. 이유와 할 일을 말한다
            await node("solve", "failed")
            await s.say(f"해석을 시작하지 못했습니다. {tool_error_text(e)}")
            return
        counts = r["counts"]
        if counts.get("failed") or not counts.get("complete"):
            cases = (await s.tool("dds-study", "get_study", {"study_id": sid, "include_cases": True}))["cases"]
            await node("solve", "failed", f"실패 {counts.get('failed', 0)}건")
            await s.say(f"해석이 끝나지 않았습니다(완료 {counts.get('complete', 0)}건, 실패 {counts.get('failed', 0)}건). 상태: {sorted({c['status'] for c in cases})}. 케이스 오류를 확인해 주세요.")
            return
        await node("solve", "complete", f"완료 {counts['complete']}건")
        await node("train", "in_progress")
        try:
            m = await self._finished(s, "dds-study", "train_surrogate", {"study_id": sid, "flow_id": fid, "node_id": "train"})
        except RuntimeError as e:  # 해석은 끝났다. 점이 모자라 학습이 안 돼도 턴을 오류로 끝내지 않고 이유와 할 일을 말한다
            await node("train", "failed")
            await s.say(f"해석 {counts['complete']}건은 끝났지만 예측 모델을 만들지 못했습니다. {tool_error_text(e)}")
            return
        await node("train", "complete", " · ".join(f"R² {k} {v['r2']:.3f}" for k, v in m["metrics"].items()))
        await node("deploy", "in_progress")
        d = await s.tool("dds-study", "deploy_model", {"model_id": m["model_id"]})
        await node("deploy", "complete", f"모델 #{d['model_id']}")
        s.model_id = d["model_id"]
        lines = [f"해석 {counts['complete']}건을 마치고 Simulator(모델 #{d['model_id']})를 배포했습니다."]
        if s.plan.get("old_study"):  # 신규 모델: 옛 결과와 나란히 보여 준다. 옛 스터디는 건드리지 않았다
            old, new = (await s.tool("dds-study", "get_study", {"study_id": s.plan["old_study"]})), (await s.tool("dds-study", "get_study", {"study_id": sid}))
            brief = lambda st: ", ".join(f"{k} {v:.4g}" for k, v in ((st.get("best") or {}).get("results") or {}).items()) or "없음"
            lines.append(f"- 지난 모델 최적: {brief(old)} / 신규 모델 최적: {brief(new)}. 지난 스터디·모델·Simulator는 그대로 있습니다.")
        await s.say("\n".join(lines))

    # ---------- 신규 모델로 지난번처럼 ----------
    async def _same_flow_again(self, s, body: str):
        flows = (await s.tool("dds-flow", "list_flows", {}))["flows"]
        last = next((f for f in flows if f["studies"]), None)
        if not last:
            await s.say("다시 돌릴 지난 작업 흐름이 이 프로젝트에 없습니다 → 다른 프로젝트라면 어느 프로젝트인지 알려 주시거나, 무엇을 돌릴지 말씀해 주세요.")
            return
        ranges = {a: (float(lo), float(hi)) for a, lo, hi in _RANGE.findall(body)}
        new_files = attached_files(body) + [p for p in _named_paths(body) if Path(p).suffix]
        if not new_files and not ranges:  # 옛 모델로 또 돌리면 옛 결과와 같다. 없으면 부탁한다
            await s.say(f"지난 흐름 '{last['title']}'을 찾았습니다. 새 모델 파일을 첨부해 주시면 옛 모델과 달라진 점을 먼저 알려 드리고 작업 흐름을 올리겠습니다.")
            return
        old = await s.tool("dds-study", "get_study", {"study_id": last["studies"][0]["id"]})
        variables, changes = [], []
        for v in old["variables"]:
            lo, hi = ranges.get(v["name"], (v["lower"], v["upper"]))
            if (lo, hi) != (v["lower"], v["upper"]):
                changes.append(f"{v['name']} 범위 {v['lower']:g}~{v['upper']:g} → {lo:g}~{hi:g}")
            variables.append({**v, "lower": lo, "upper": hi})
        changes += [f"새 모델 {Path(p).name}" for p in new_files]
        attached = [f for f in attached_files(body)]
        # 새 모델을 설정에 넣지 않으면 새 모델이라고 말하면서 옛 모델로 돈다
        models = {"model_files": attached} if old["adapter"] == "script" and attached else {}
        if new_files and old["adapter"] != "script":
            changes.append("연습용 솔버는 모델 파일을 읽지 않아 해석 값은 범위로만 달라집니다")
        new = await s.tool("dds-study", "clone_study", {"study_id": old["study_id"], "name": f"{old['name']} 신규 모델"[:60], "variables": variables, **models})
        if new.get("warning"):
            changes.append(new["warning"])
        s.study_id, s.mode = new["study_id"], "rerun"
        s.plan = {"doe": sum(old["counts"].values()), "old_study": old["study_id"], "permission": f"신규 모델로 {sum(old['counts'].values())}케이스 해석"}
        detail = "달라진 점: " + " · ".join(changes)
        nodes = [{"id": "doe", "label": "DOE 다시 뽑기", "type": "doe", "detail": detail}, *self._solve_nodes("지난 흐름과 같은 방법")]
        r = await s.tool("dds-flow", "propose_flow", {"title": f"{last['title']} (신규 모델)", "goal": body[:200], "nodes": nodes,
                                                     "edges": [{"source": "doe", "target": "solve"}, {"source": "solve", "target": "train"}, {"source": "train", "target": "deploy"}]})
        s.flow_id = r["flow_id"]
        await s.say(f"지난 흐름 '{last['title']}'을 찾았습니다. {detail}.\n새 스터디로 진행하고 지난 스터디·모델·Simulator는 건드리지 않습니다. **승인**을 눌러 주시면 시작합니다.")

    # ---------- 다른 멤버가 이어받기 ----------
    async def _save_recipe(self, s, body: str):
        studies = (await s.tool("dds-study", "list_studies", {}))["studies"]
        st = next((x for x in studies if x["name"] in body), None) or next((x for x in studies if x["adapter"] == "script"), None)
        if not st:
            await s.say("묶을 스크립트 스터디가 이 프로젝트에 없습니다 → 스크립트로 해석하는 스터디를 먼저 만들어 주세요.")
            return
        try:
            r = await s.tool("dds-study", "save_recipe", {"study_id": st["study_id"], "files": re.findall(r"scripts/[\w./-]+", body)})
        except RuntimeError as e:
            await s.say(f"묶음을 올리지 못했습니다. {tool_error_text(e)}")
            return
        await s.say(f"'{st['name']}' 스크립트 {len(r['files'])}개를 서버에 올렸습니다({r['version']}판). 다른 멤버 PC에서도 받아 쓸 수 있습니다.")

    async def _takeover(self, s, body: str):
        studies = (await s.tool("dds-study", "list_studies", {}))["studies"]
        st = next((x for x in studies if x["name"] in body), None) or next((x for x in studies if x["has_recipe"]), None) or next(iter(studies), None)
        if not st:
            await s.say("이어받을 스터디가 이 프로젝트에 없습니다 → 프로젝트 멤버로 추가됐는지 확인해 주세요.")
            return
        s.study_id = st["study_id"]
        try:
            if any(k in body for k in ("Output", "output", "출력")):
                await self._takeover_output(s, st, body)
            elif any(k in body for k in ("해석", "돌려", "점 더")):
                await self._takeover_run(s, st, body)
            else:  # 서버에 있는 데이터로 충분하다. 내 PC·만든 사람 PC 어느 쪽도 해석하지 않는다
                m = await self._finished(s, "dds-study", "train_surrogate", {"study_id": st["study_id"]})
                d = await s.tool("dds-study", "deploy_model", {"model_id": m["model_id"]})
                await s.say(f"'{st['name']}'의 서버 데이터로 다시 학습해 Simulator(모델 #{d['model_id']}, {m['version']}판)를 배포했습니다. 해석은 돌리지 않았습니다.")
        except RuntimeError as e:
            await s.say(f"이어받지 못했습니다. {tool_error_text(e)}")

    async def _takeover_output(self, s, st: dict, body: str):
        name = re.search(r"(?:Output|output|출력)\s+([A-Za-z_]\w*)", body)
        script = re.search(r"scripts/[\w./-]+", body)
        post = None
        if script:
            await s.tool("dds-study", "restore_recipe", {"study_id": st["study_id"]})  # 추출 스크립트도 묶음에 있다. 받기만 한다
            post = {"tool": "python", "script": script.group(0)}
        r = await s.tool("dds-study", "add_outputs", {"study_id": st["study_id"], "outputs": [{"name": name.group(1) if name else "extra"}], "post": post})
        got = next(iter(r["extracted"].values()), 0)
        await s.say(f"Output을 추가했습니다. 완료 케이스 {r['complete_cases']}건 중 {got}건에서 뽑았고, {r['skipped_no_agent']}건은 뽑지 못했습니다. {r.get('note', '')}".strip())

    async def _takeover_run(self, s, st: dict, body: str):
        rec = await s.tool("dds-study", "restore_recipe", {"study_id": st["study_id"]})
        if rec["input_files_missing"]:
            await s.say(f"스크립트는 받았지만 입력 모델이 이 PC에 없습니다: {', '.join(rec['input_files_missing'])} → 그 파일을 대화에 첨부해 주세요. 묶음에는 입력 모델이 들어가지 않습니다.")
            return
        if rec["conflicts"]:
            await s.say(f"이 PC에 내용이 다른 같은 파일이 있습니다: {', '.join(rec['conflicts'])} → 덮어써도 되는지 알려 주세요. 묻지 않고 덮어쓰지 않습니다.")
            return
        author = rec.get("created_by_name") or "다른 멤버"
        n = re.search(r"(\d+)\s*점", body)
        n = int(n.group(1)) if n else 4
        s.mode = "takeover"
        s.plan = {"doe": n, "permission": f"{author}님이 만든 스크립트로 {n}케이스 해석"}
        nodes = [{"id": "doe", "label": f"DOE {n}점 추가", "type": "doe", "detail": f"{author}님이 만든 스크립트: {', '.join(rec['restored'] + rec['unchanged'])}"},
                 *self._solve_nodes(f"이 PC에서 {author}님이 만든 스크립트 실행")]
        r = await s.tool("dds-flow", "propose_flow", {"title": f"{st['name']} 이어받아 해석", "goal": body[:200], "nodes": nodes,
                                                     "edges": [{"source": "doe", "target": "solve"}, {"source": "solve", "target": "train"}, {"source": "train", "target": "deploy"}]})
        s.flow_id = r["flow_id"]
        await s.say(f"**{author}님이 만든 스크립트**를 이 PC로 받았습니다(아직 돌리지 않았습니다). 내용을 확인하시고 **승인**을 눌러 주시면 이 PC에서 해석을 시작합니다.")

    # ---------- 이미 있는 모델로 하는 일 ----------
    async def _model_work(self, s, body: str) -> bool:
        """예측·되돌리기·공유·최적 후보. 새 대화에서 말해도 해석을 돌리지 않는다. 모델이 없으면 False(처음부터 하는 요청으로 넘긴다)."""
        models = (await s.tool("dds-study", "list_models", {}))["models"]
        if not models:
            return False
        live = next((m for m in models if m["deployed"]), models[0])
        studies = (await s.tool("dds-study", "list_studies", {}))["studies"]
        study = next((x for x in studies if x["study_id"] == live["study_id"]), None)
        if "되돌려" in body:
            older = next((m for m in models if m["study_id"] == live["study_id"] and (m.get("version") or 0) < (live.get("version") or 0)), None)
            if not older:
                await s.say(f"되돌릴 옛 버전이 없습니다. 지금 v{live.get('version')}이 첫 버전입니다.")
                return True
            await s.tool("dds-study", "deploy_model", {"model_id": older["model_id"]})
            await s.say(f"Simulator를 v{older.get('version')}으로 되돌렸습니다. 공유 링크도 이 버전을 보여 줍니다. 해석은 돌리지 않았습니다.")
            return True
        if "공유" in body:
            await s.say("오른쪽 **Simulator** 탭의 **공유** 버튼으로 링크를 만듭니다. 링크는 항상 현재 버전을 보여 주고, 받는 사람은 로그인만 하면 됩니다.")
            return True
        if "이 스터디로" in body:
            if study and study["adapter"] != "data":
                await s.say("이 스터디는 해석을 새로 돌려 최적 설계를 찾습니다. 몇 점을 더 돌릴지 알려 주시면 작업 흐름을 올리겠습니다.")
                return True
            goal = _OBJECTIVE.search(body)
            # 줄일지 키울지 지어내지 않는다. 수명을 최소로 찾으면 가장 나쁜 설계를 최적이라고 답한다
            extra = {"objective": goal.group(1), "goal": "min" if goal.group(2) == "최소" else "max"} if goal else {"objective": live["outputs"][0]}
            try:
                r = await s.tool("dds-study", "optimize", {"study_id": live["study_id"], **extra})
            except RuntimeError as e:
                await s.say(tool_error_text(e))
                return True
            top = next(iter(r["candidates"]), None)
            shown = ", ".join(f"{k}={v:.3g}" for k, v in top["params"].items()) if top else "없음"
            await s.say(f"모델로만 찾은 후보입니다. {extra['objective']} 기준 가장 나은 후보: {shown}. {r['note']}")
            return True
        names = [v["name"] for v in (study or {}).get("inputs") or []]
        params = {}
        for n in names:
            m = re.search(rf"{re.escape(n)}\s*(?:은|는|이|가|=|:)?\s*(-?\d+(?:\.\d+)?)", body)
            if m:
                params[n] = float(m.group(1))
        if len(params) < len(names) or not names:
            await s.say(f"어떤 값으로 예측할까요? 입력: {', '.join(names)}. 예측은 해석을 돌리지 않고 바로 답합니다.")
            return True
        r = await s.tool("dds-study", "predict", {"model_id": live["model_id"], "params": params})
        shown = ", ".join(f"{k} {v:.4g}" for k, v in r["mean"].items())
        await s.say(f"예측값: {shown} (모델 v{live.get('version')}, 해석으로 확인된 값은 아닙니다).")
        return True

    async def _from_folder(self, s, folder: str | None):
        if not folder:
            await s.say("해석 결과 폴더 경로를 알려 주세요. 폴더를 읽어 케이스를 찾고, 해석을 다시 돌리지 않고 예측 모델과 Simulator를 만듭니다.")
            return
        await s.say("폴더에서 케이스를 찾습니다. 원본은 읽기만 합니다.", thought=True)
        try:
            scan = await s.tool("dds-study", "scan_results", {"folder": folder})
            if not scan["ready"]:
                await s.say(self._not_ready_text(scan))
                return
            await s.tool("dds-flow", "set_project_info", {"name": f"{Path(scan['folder']).name} 예측 모델"[:60], "description": "기존 해석 결과로 만든 예측 모델과 Simulator"})
            rep = await self._finished(s, "dds-study", "import_cases", {"folder": folder})
        except RuntimeError as e:
            await s.say(f"해석 폴더를 불러오지 못했습니다. {tool_error_text(e)}")
            return
        s.from_data, s.study_id = True, rep["study_id"]
        await self._train_and_deploy(s, rep, f"해석 폴더 {rep['registered'] + rep.get('duplicates', 0)}건으로", fields=scan.get("fields") or [])

    @staticmethod
    def _not_ready_text(scan: dict) -> str:
        if scan.get("encrypted"):
            return (f"폴더 {scan['cases_total']}개 중 {scan['encrypted']}개의 요약 파일이 보안 프로그램으로 암호화되어 값을 읽지 못했습니다. 값이 없는 것이 아닙니다. "
                    "입력·출력을 엑셀에서 복사해 표로 붙여 넣어 주시면 바로 진행합니다.")
        kinds = ", ".join(f"{e['kind']} {e['cases']}건" for e in scan.get("extract") or [])
        if kinds:
            return (f"폴더 {scan['cases_total']}개를 찾았지만 값이 뽑혀 있지 않습니다({kinds}). 이 데모에는 실제 툴이 없어 결과 파일에서 값을 뽑을 수 없습니다. "
                    "케이스마다 params.json과 results.json을 두거나, 입력·출력 표를 주시면 바로 진행합니다.")
        return "이 폴더에서 해석 케이스를 찾지 못했습니다. 케이스 폴더가 더 아래에 있으면 그 경로를 알려 주시거나, 입력·출력 표를 주세요."

    async def _finished(self, s, server: str, name: str, args: dict) -> dict:
        """'아직 진행 중'이면 같은 인자로 다시 부른다. 실제 AI에게 도구 설명으로 주문한 그대로다."""
        r = await s.tool(server, name, args)
        while isinstance(r, dict) and r.get("status") == "running":
            await asyncio.sleep(1)
            r = await s.tool(server, name, args)
        return r

    async def _train_and_deploy(self, s, rep: dict, what: str, fields: list[str] | None = None):
        try:
            m = await self._finished(s, "dds-study", "train_surrogate", {"study_id": rep["study_id"]})
            field_note = ""
            for f in fields or []:  # 3D 필드는 있으면 학습한다. 실패해도 스칼라 Simulator는 살린다
                try:
                    fm = await s.tool("dds-study", "train_field_model", {"study_id": rep["study_id"], "field": f})
                    field_note = f"\n- 3D 필드 '{f}'도 학습했습니다(노드 {fm['nodes']:,}개)."
                except RuntimeError as e:
                    field_note = f"\n- 3D 필드 '{f}'는 학습하지 못했습니다. {tool_error_text(e)}"
            d = await s.tool("dds-study", "deploy_model", {"model_id": m["model_id"]})
        except RuntimeError as e:
            await s.say(f"데이터는 등록했지만 예측 모델을 만들지 못했습니다. {tool_error_text(e)}")
            return
        s.model_id = d["model_id"]
        r2 = ", ".join(f"{k} {v['r2']:.3f}" for k, v in m["metrics"].items())
        lines = [f"{what} 예측 모델을 만들어 Simulator에 배포했습니다. 해석은 돌리지 않았습니다.",
                 f"- 정확도(R², 예측이 실제를 얼마나 잘 따르는지): {r2} · {m.get('quality_label') or m.get('quality') or ''}".rstrip(" ·")]
        if rep.get("excluded_count"):
            by = ", ".join(f"{k} {v}개" for k, v in (rep.get("excluded_by_reason") or {}).items())
            lines.append(f"- 뺀 행 {rep['excluded_count']}개: {by}")
        if rep.get("fixed"):
            lines.append("- 값이 하나뿐이라 고정값으로 기록한 열: " + ", ".join(rep["fixed"]))
        for c in (rep.get("removed_columns") or [])[:3]:
            lines.append(f"- 뺀 열 '{c['name']}': {c['reason']}")
        for w in [*(rep.get("warnings") or []), *(m.get("warnings") or [])][:3]:
            lines.append(f"- {w}")
        await s.say("\n".join(lines) + field_note + "\n\n오른쪽 **Simulator** 탭에서 바로 값을 바꿔 볼 수 있습니다.")

    async def _answer(self, s, body: str):
        """조회는 Flow 없이 바로 답한다."""
        if "툴" in body:  # 없는 툴이 있어도 표·기존 결과 작업은 그대로 된다
            tools = (await s.tool("dds-cae", "list_tools", {}))["tools"]
            ready = [t["name"] for t in tools if t["status"] == "ready"]
            await s.say(f"지원 툴 {len(tools)}종 중 이 PC에서 바로 쓸 수 있는 것은 {len(ready)}종입니다{': ' + ', '.join(ready[:8]) if ready else ''}. "
                        "없는 툴은 해석을 돌릴 때만 필요합니다. 표나 기존 해석 결과로 예측 모델을 만드는 일은 툴 없이 됩니다.")
            return
        if any(k in body for k in ("다른 프로젝트", "모든 프로젝트", "전체 프로젝트")):
            found = (await s.tool("dds-study", "list_studies", {"all_projects": True}))["studies"]
            names = ", ".join(f"{x['project_name']}: {x['name']}" for x in found[:10]) or "없음"
            await s.say(f"내가 멤버인 프로젝트의 스터디 {len(found)}개입니다. {names}. 같은 설정으로 쓰려면 복제해 드릴 수 있습니다.")
            return
        if not any(k in body for k in ("스터디", "모델", "Simulator", "시뮬레이터", "케이스", "결과", "프로젝트", "정확도", "R²")):
            await s.say("표(CSV·XLSX)나 해석 결과 폴더가 있으면 바로 예측 모델과 Simulator를 만들고, 없으면 작업 흐름을 제안해 해석부터 합니다. 무엇을 가지고 계신지 알려 주세요.")
            return
        studies = (await s.tool("dds-study", "list_studies", {}))["studies"]
        models = (await s.tool("dds-study", "list_models", {}))["models"]
        if not studies:
            await s.say("이 프로젝트에는 아직 스터디가 없습니다. 표나 해석 결과 폴더를 주시면 바로 만들 수 있습니다.")
            return
        names = ", ".join(f"{x['name']}({sum(x['counts'].values())}건)" for x in studies[:5])
        live = [m for m in models if m["deployed"]]
        best = next(iter(live or models), None)
        tail = f" 가장 최근 모델의 R²는 {', '.join(f'{k} {v:.3f}' for k, v in best['r2'].items())}입니다." if best else ""
        await s.say(f"이 프로젝트에는 스터디 {len(studies)}개({names})와 예측 모델 {len(models)}개가 있고, 그중 {len(live)}개가 Simulator로 배포돼 있습니다.{tail}")

    async def _data_follow_up(self, s, body: str):
        """표·해석 폴더로 만든 스터디의 이어지는 요청. 돌릴 해석이 없으니 Branin을 새로 돌리지 않는다."""
        if "최적" in body or "후보" in body:
            goal = _OBJECTIVE.search(body)  # "stress 최소": 목적을 정하지 않은 표에서도 무엇을 줄일지 말하면 바로 된다
            extra = {"objective": goal.group(1), "goal": "min" if goal.group(2) == "최소" else "max"} if goal else {}
            try:
                r = await s.tool("dds-study", "optimize", {"study_id": s.study_id, **extra})
            except RuntimeError as e:
                await s.say(f"후보를 찾지 못했습니다. {tool_error_text(e)}")
                return
            top = next(iter(r["candidates"]), None)
            shown = ", ".join(f"{k}={v:.3g}" for k, v in top["params"].items()) if top else "없음"
            await s.say(f"모델로만 찾은 후보입니다. 가장 나은 후보: {shown}. {r['note']}")
            return
        if "보고서" in body:
            await self._report_without_flow(s)
            return
        await s.say("이 스터디는 가진 데이터로 만들어 돌릴 해석이 없습니다. 행 추가, 최적 후보 찾기, 보고서를 바로 할 수 있고, 해석을 돌리려면 해석 방법을 연결해야 합니다.")

    async def _report_without_flow(self, s):
        ctx = await s.tool("dds-flow", "get_report_context", {})
        if not ctx["studies"] and not ctx["results"]:
            await s.say("보고서에 넣을 결과가 아직 없습니다. 표나 해석 결과 폴더를 주시면 예측 모델을 만든 뒤 보고서를 씁니다.")
            return
        await self._generic_report(s)
        await s.say("있는 결과로 보고서를 썼습니다. 오른쪽 **보고서** 탭에서 볼 수 있습니다.")

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
        # 표·해석 폴더로 시작한 대화에는 Flow가 없다. 보고서는 Flow 없이도 쓴다
        flow = next((f for f in ctx["flows"] if f["flow_id"] == s.flow_id), {"title": "기존 데이터 예측 모델", "steps": []})
        lines = ["## 요약"]
        for st in ctx["studies"]:
            best = st["best"]
            r2 = ", ".join(f"{k} {v:.3f}" for k, v in (st["models"][0]["r2"] if st["models"] else {}).items())
            if not best:  # 목적을 정하지 않은 표: 최적 케이스는 없지만 모델 정확도는 알린다
                if r2:
                    lines.append(f"- {st['name']}: 데이터 {st['cases'].get('complete', 0)}건, 예측 모델 R² {r2}")
                continue
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
        await s.tool("dds-study", "generate_doe", {"study_id": sid, "n": s.points or 20, "seed": 7, "flow_id": fid, "node_id": "doe"})
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
