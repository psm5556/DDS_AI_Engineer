"""어느 시점에서든 진입(docs/entry-points.md E0~E16)을 실제 프로세스로 돌린다.
실제 uvicorn + 실제 PC Agent + 데모 CLI(ACP) + 실제 MCP stdio. 실제 Claude·Gemini·Codex와 실제 CAD는 띄우지 않는다. AI의 판단은 여기서 검증되지 않는다(pc_verify chat의 몫).
data/를 건드리지 않고 임시 폴더와 빈 포트를 쓴다. 7014·8000은 건드리지 않고, 창을 띄우지 않는다.

실행: .venv\\Scripts\\python scripts\\e2e_scenarios.py [--only E1,E2] [--doc] [--out 폴더]
결과: 시나리오마다 측정값을 표로 찍고 <폴더>/results.json에 저장한다. --doc이면 docs/entry-points.md의 결과 표를 바꾼다. 하나라도 실패하면 종료 코드 1.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.tests.stack import free_port, wait_for  # noqa: E402
from cae_adapters.hidden import hidden_kwargs, kill_tree  # noqa: E402

DOC = ROOT / "docs" / "entry-points.md"
PIN = "123456"
OWNER_NAME = "박과장"
MEMBER_NAME = "김대리"
START, END = "<!-- 결과표 시작 -->", "<!-- 결과표 끝 -->"


# ---------- 시험 데이터 ----------
def f_true(t: float, w: float) -> float:
    return 1000.0 / (t * w) + 0.5 * t


def branin(x1: float, x2: float) -> float:
    a, b, c, r, s, t = 1.0, 5.1 / (4 * math.pi ** 2), 5 / math.pi, 6.0, 10.0, 1 / (8 * math.pi)
    return a * (x2 - b * x1 ** 2 + c * x1 - r) ** 2 + s * (1 - t) * math.cos(x1) + s


def design(n: int, seed: int = 3, lo=(1.0, 10.0), hi=(5.0, 40.0)) -> list[tuple[float, float]]:
    rnd = random.Random(seed)
    return [(round(rnd.uniform(lo[0], hi[0]), 4), round(rnd.uniform(lo[1], hi[1]), 3)) for _ in range(n)]


def table_text(rows, sep=",", head=("두께 [mm]", "폭 [mm]", "stress [MPa]")) -> str:
    return sep.join(head) + "\n" + "\n".join(sep.join(str(v) for v in (t, w, round(f_true(t, w), 5))) for t, w in rows) + "\n"


def xlsx_bytes(rows: list[list]) -> bytes:
    """표준 라이브러리로 만든 최소 XLSX(제목 줄·단위 머리글·한글). 제품 파서가 실제 엑셀 구조를 읽는지 본다."""
    def cell(ref: str, v) -> str:
        if isinstance(v, str):
            return f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>'
        return f'<c r="{ref}"><v>{v}</v></c>'
    body = ""
    for i, row in enumerate(rows, 1):
        body += f'<row r="{i}">' + "".join(cell(f"{chr(65 + j)}{i}", v) for j, v in enumerate(row)) + "</row>"
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr("_rels/.rels", f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><workbook xmlns="{ns}" xmlns:r="{rel}"><sheets><sheet name="결과" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{ns}"><sheetData>{body}</sheetData></worksheet>')
    return buf.getvalue()


ENCRYPTED = "보안 프로그램으로 암호화되어 읽을 수 없습니다 → 엑셀에서 표를 복사해 붙여 넣으세요"


def excel_tsv(rows, error_row=True) -> str:
    """엑셀에서 복사한 글: 탭 구분, 줄바꿈 CRLF, 따옴표 안 줄바꿈 머리글, 천 단위 쉼표, 퍼센트, 빈 열, 빈 행, 병합 셀 빈칸, 수식 오류."""
    lines = ['번호\t두께 [mm]\t폭 [mm]\t\t재질\t하중 [N]\t"최대\n응력 [MPa]"\t효율']
    for i, (t, w) in enumerate(rows, 1):
        lines.append(f'{i}\t{t}\t{w}\t\t{"SS400" if i == 1 else ""}\t1000\t"{f_true(t, w) * 1000:,.2f}"\t{50 + t * 3:.1f}%')
        if i == len(rows) // 2:
            lines.append("\t\t\t\t\t\t\t")
    if error_row:
        lines.append(f"{len(rows) + 1}\t3.3\t33\t\t\t1000\t#N/A\t60.0%")
    return "\r\n".join(lines) + "\r\n"


def paste_tsv(rows, head=("두께 [mm]", "폭 [mm]", "stress [MPa]")) -> str:
    return table_text(rows, sep="\t", head=head)


def attach_paste(who: "Who", pid: int, text: str, stamp: str) -> int:
    """입력창에 붙여 넣은 표를 AI로 보낼 때와 같게: 행은 요청 글이 아니라 첨부 파일로 간다(Composer.tsx)."""
    return who.post(f"/api/projects/{pid}/attachments", files={"file": (f"붙여넣은_표_{stamp}.tsv", text.encode("utf-8"), "text/tab-separated-values")})["id"]


def tree_hash(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def case_folders(root: Path, rows, npz=False) -> Path:
    grid = np.random.default_rng(0).uniform(0, 1, (40, 3))
    for i, (t, w) in enumerate(rows):
        d = root / f"case_{i:02d}"
        (d / "fields" if npz else d).mkdir(parents=True)
        (d / "params.json").write_text(json.dumps({"t": t, "w": w}), encoding="utf-8")
        if npz:
            np.savez_compressed(d / "fields" / "mises.npz", points=grid, values=grid[:, 0] * t + grid[:, 1] * w)
        else:
            (d / "results.json").write_text(json.dumps({"s": f_true(t, w)}), encoding="utf-8")
    return root


# ---------- 프로세스 ----------
class Who:
    def __init__(self, world: "World", username: str, display: str):
        self.world, self.username, self.display = world, username, display
        self.c = httpx.Client(base_url=world.base, timeout=240)
        self.token = ""
        self.id = 0

    def get(self, path: str, **kw):
        r = self.c.get(path, **kw)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict | None = None, **kw):
        r = self.c.post(path, json=body if body is not None else {}, **kw) if "files" not in kw and "data" not in kw else self.c.post(path, **kw)
        if r.status_code >= 400:
            raise AssertionError(f"{path} → {r.status_code}: {r.text[:400]}")
        return r.json()

    def project(self, name: str) -> int:
        return self.post("/api/projects", {"name": name})["id"]

    def new_chat(self, pid: int) -> int:
        return self.post(f"/api/projects/{pid}/sessions", {"agent": "demo"})["id"]


class Pc:
    """한 사용자의 PC: 자기 설정·작업 폴더를 가진 진짜 PC Agent. 사용자가 PC를 쓰는 중이라 트레이·창을 띄우지 않고 PC 전체 툴 검색도 건너뛴다."""

    def __init__(self, world: "World", who: Who, name: str):
        self.world, self.who, self.name = world, who, name
        self.home = world.tmp / name
        self.ws = self.home / "ws"
        self.cfg = self.home / "agent.toml"
        self.proc: subprocess.Popen | None = None

    def env(self) -> dict:
        env = {k: v for k, v in self.world.env.items() if k not in ("DDS_DATA_DIR", "DDS_SERVER_URL", "DDS_TOKEN", "DDS_AGENT_HOME")}
        env.update({"DDS_AGENT_CONFIG": str(self.cfg), "DDS_WORKSPACE": str(self.ws), "PYSTRAY_BACKEND": "dummy"})
        return env

    def start(self):
        if self.proc:
            return
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "tools.json").write_text(json.dumps({"scanned_ts": time.time() + 86400}), encoding="utf-8")
        self.cfg.write_text("\n".join([
            f"server_url = {json.dumps(self.world.base)}", f"token = {json.dumps(self.who.token)}", f"workspace_dir = {json.dumps(str(self.ws))}",
            'default_agent = "demo"', "tray = false", "warm_up = false", ""]), encoding="utf-8")
        self.proc = self.world.popen([sys.executable, "-m", "agent.dds_agent.main"], self.env(), self.name)
        wait_for(self.connected, timeout=90, what=f"{self.name} PC Agent 연결")

    def connected(self) -> bool:
        st = self.who.get("/api/agent/status")
        return bool(st.get("connected") and (st.get("info") or {}).get("boot"))

    def stop(self):
        if self.proc:
            kill_tree(self.proc)  # 데모 CLI와 MCP까지 끝낸다. 시나리오마다 MCP 프로세스가 쌓이지 않게
            self.proc = None
            wait_for(lambda: not self.who.get("/api/agent/status").get("connected"), timeout=30, what=f"{self.name} 끊김")

    def mcp(self, server: str, tool: str, args: dict, pid: int):
        """실제 AI CLI가 하는 것처럼 MCP 서버를 stdio로 띄워 도구 하나를 부른다."""
        from mcp.client.session import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        env = {**self.env(), "DDS_PROJECT_ID": str(pid), "DDS_SERVER_URL": self.world.base, "DDS_TOKEN": self.who.token, "PYTHONPATH": str(ROOT)}
        # 실제 AI의 도구 호출은 세션 안에서 일어난다. PC Agent가 세션에 주는 Task 환경값을 그대로 준다. 없으면 스크립트와 케이스의 폴더가 어긋난다
        for work in self.who.get(f"/api/projects/{pid}/works"):
            found = list(Path(self.ws).glob(f"P{pid:04d}_*/T{work['seq']:02d}_*")) if work["mine"] and not work["legacy"] else []
            if found:
                env.update({"DDS_WORK_ID": str(work["id"]), "DDS_WORK_UID": work["uid"] or "", "DDS_WORK_DIR": str(found[0])})
                break
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_servers" / f"{server.replace('-', '_')}.py")], env=env)

        async def call():
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool(tool, args)
                    text = "\n".join(getattr(c, "text", "") for c in res.content)
                    if getattr(res, "is_error", False) or getattr(res, "isError", False):  # 여기서 올리면 TaskGroup이 예외 묶음으로 감싸 문구를 볼 수 없다
                        return None, text
                    sc = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
                    return (sc.get("result", sc) if isinstance(sc, dict) else json.loads(text)), None
        value, error = asyncio.run(call())
        if error is not None:
            raise AssertionError(f"{tool}: {error[:400]}")
        return value


class World:
    def __init__(self, tmp: Path):
        self.tmp = tmp
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.env = {**os.environ, "DDS_DATA_DIR": str(tmp / "server"), "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)}
        self.logs: list = []
        self.server: subprocess.Popen | None = None
        self.admin_c = httpx.Client(base_url=self.base, timeout=240)

    def popen(self, cmd, env, name) -> subprocess.Popen:
        log = open(self.tmp / f"{name}.log", "ab")
        self.logs.append(log)
        return subprocess.Popen(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **hidden_kwargs())

    def start(self):
        self.server = self.popen([sys.executable, "-m", "uvicorn", "server.app.main:app", "--port", str(self.port)], self.env, "server")

        def up():
            try:
                return self.admin_c.post("/api/auth/login", json={}).status_code in (401, 422)
            except httpx.HTTPError:
                return False
        wait_for(up, timeout=120, what="서버 시작")
        self.owner = self.user("owner", OWNER_NAME)  # 처음 가입한 사람이 관리자다
        self.member = self.user("member", MEMBER_NAME)
        self.outsider = self.user("outsider", "외부인")
        self.owner_pc = Pc(self, self.owner, "owner_pc")
        self.member_pc = Pc(self, self.member, "member_pc")

    def user(self, username: str, display: str) -> Who:
        w = Who(self, username, display)
        self.admin_c.post("/api/auth/register", json={"username": username, "pin": PIN, "display_name": display})
        if username != "owner":
            uid = next(u["id"] for u in self.owner.get("/api/users") if u["username"] == username)
            self.owner.c.put(f"/api/users/{uid}", json={"is_active": True}).raise_for_status()
        r = self.admin_c.post("/api/auth/login", json={"username": username, "pin": PIN})
        r.raise_for_status()
        w.id = r.json()["user"]["id"]
        w.c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        w.token = w.post("/api/tokens", {"name": "시험 PC"})["token"]
        return w

    def stop(self):
        for fn in (lambda: self.owner_pc.stop(), lambda: self.member_pc.stop()):
            try:
                fn()
            except Exception:
                pass
        if self.server:
            kill_tree(self.server)
        for log in self.logs:
            try:
                log.close()
            except OSError:
                pass

    def server_log(self) -> str:
        return (self.tmp / "server.log").read_text(encoding="utf-8", errors="replace")


DEVTEST_TOKEN_CODE = ("import sys\n"
                      "from server.app import models\n"
                      "from server.app.database import SessionLocal\n"
                      "from server.app.deps import SCOPE_DEVTEST, new_api_token\n"
                      "raw, hashed = new_api_token()\n"
                      "with SessionLocal() as db:\n"
                      "    db.add(models.ApiToken(user_id=int(sys.argv[1]), name='시험', token_hash=hashed, scope=SCOPE_DEVTEST))\n"
                      "    db.commit()\n"
                      "print(raw)\n")


# ---------- 대화 ----------
@dataclass
class Turn:
    reply: str
    tools: list[str]
    errors: list[str]
    permissions: list[str]
    sec: float
    first_reply_sec: float | None


def chat(who: Who, sid: int, text: str, attach: list[int] | None = None, timeout: float = 400, allow: bool = True) -> Turn:
    wait_for(lambda: not who.get(f"/api/sessions/{sid}/status")["busy"], timeout=60, what="앞 턴 끝")
    n = len(who.get(f"/api/sessions/{sid}/messages"))
    t0 = time.time()
    who.post(f"/api/sessions/{sid}/prompt", {"text": text, "attachment_ids": attach or []})
    return follow(who, sid, n, t0, text, timeout, allow)


def follow(who: Who, sid: int, n: int, t0: float, text: str, timeout: float = 400, allow: bool = True) -> Turn:
    first, perms = None, []
    while True:
        if time.time() - t0 > timeout:
            raise TimeoutError(f"턴이 {timeout}초 안에 끝나지 않음: {text[:40]}")
        st = who.get(f"/api/sessions/{sid}/status")
        for p in st.get("pending_permissions") or []:
            perms.append(p.get("title") or "")
            if allow:
                who.post(f"/api/sessions/{sid}/permission", {"request_id": p["request_id"], "option_id": "allow"})
        if first is None and any(m["role"] == "agent" for m in who.get(f"/api/sessions/{sid}/messages")[n:]):
            first = round(time.time() - t0, 2)
        if not st["busy"]:
            break
        time.sleep(0.25)
    msgs = who.get(f"/api/sessions/{sid}/messages")[n:]
    return Turn("".join(m["content"] for m in msgs if m["role"] == "agent"), [(m.get("meta") or {}).get("title") or "" for m in msgs if m["role"] == "tool"],
                [m["content"] for m in msgs if m["role"] == "system"], perms, round(time.time() - t0, 2), first)


def approve(who: Who, sid: int, flow_id: int) -> Turn:
    wait_for(lambda: not who.get(f"/api/sessions/{sid}/status")["busy"], timeout=60, what="앞 턴 끝")
    f = who.get(f"/api/flows/{flow_id}")
    n = len(who.get(f"/api/sessions/{sid}/messages"))
    t0 = time.time()
    # 승인만 한다. 에이전트에게 전하는 일은 서버가 한다(웹앱 카드와 같다)
    r = who.c.put(f"/api/flows/{flow_id}/status", json={"status": "approved", "version": f["version"], "session_id": sid})
    r.raise_for_status()
    return follow(who, sid, n, t0, f"승인 #{flow_id}")


def flows(who: Who, pid: int) -> list[dict]:
    return who.get(f"/api/projects/{pid}/flows")


def models_of(who: Who, pid: int) -> list[dict]:
    return who.get(f"/api/projects/{pid}/models")


def study_of(who: Who, pid: int) -> dict:
    return who.get(f"/api/projects/{pid}/studies")[0]


def counts(who: Who, sid: int) -> dict:
    return who.get(f"/api/studies/{sid}")["counts"]


def cases_of(who: Who, sid: int) -> list[dict]:
    return who.get(f"/api/studies/{sid}/cases")


def train_poll(who: Who, sid: int, body: dict | None = None, timeout: float = 240) -> dict:
    job = who.post(f"/api/studies/{sid}/train/start", body or {})
    end = time.time() + timeout
    while job["status"] not in ("done", "failed") and time.time() < end:
        time.sleep(0.3)
        job = who.get(f"/api/train-jobs/{job['job_id']}")
    assert job["status"] == "done", job
    return job


def r2_of(model: dict) -> dict:
    return {k: round(v["r2"], 3) for k, v in (model.get("metrics") or {}).items()}


# ---------- 시나리오 표 ----------
class Check:
    """측정값을 모으고 단언한다. 실패해도 그때까지 잰 값을 남긴다."""

    def __init__(self):
        self.m: dict = {}
        self.passed: list[str] = []

    def __call__(self, cond, what: str):
        if not cond:
            raise AssertionError(what)
        self.passed.append(what)
        return cond

    def __setitem__(self, k, v):
        self.m[k] = v


@dataclass
class Scenario:
    sid: str
    title: str
    fn: Callable
    pc: bool = True


SCENARIOS: list[Scenario] = []


def scenario(sid: str, title: str, pc: bool = True):
    def deco(fn):
        SCENARIOS.append(Scenario(sid, title, fn, pc))
        return fn
    return deco


@scenario("E0", "처음부터 전체(대조군): Flow 승인 → DOE → 학습 → 최적화 → Simulator")
def e0(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E0")
    sid = o.new_chat(pid)
    t = chat(o, sid, "mock 솔버로 Branin 최적화하고 Simulator 배포해줘")
    fl = flows(o, pid)
    c(len(fl) == 1 and fl[0]["status"] == "review", "Flow 제안 후 검토 대기")
    c(o.get(f"/api/projects/{pid}/studies") == [], "승인 전에는 스터디·해석 없음")
    done = approve(o, sid, fl[0]["id"])
    c(len(done.permissions) == 0, "승인 뒤에는 묻지 않음")
    f = o.get(f"/api/flows/{fl[0]['id']}")
    st = study_of(o, pid)
    cnt = counts(o, st["id"])
    live = [m for m in models_of(o, pid) if m["deployed"]]
    c(f["status"] == "complete" and all(n["status"] == "complete" for n in f["nodes"]), "모든 단계 완료")
    c(cnt.get("complete", 0) >= 20 and not done.errors, "케이스 완료·오류 없음")
    c(len(live) == 1, "배포 모델 1개")
    best = o.get(f"/api/studies/{st['id']}")["best"]
    c(best["results"]["f"] < 5, "최적값이 Branin 최소 근처")
    c["cases"] = cnt.get("complete")
    c["best_f"] = round(best["results"]["f"], 3)
    c["r2"] = r2_of(live[0])
    c["propose_sec"] = t.sec
    c["run_sec"] = done.sec


@scenario("E0b", "승인: 답하는 중에 눌러도, PC Agent가 꺼졌을 때 눌러도 멈추지 않고 끝까지 간다")
def e0b(w: World, tmp: Path, c: Check):
    o = w.owner
    text = "연습용 계산으로 Branin 최적화하고 Simulator 배포해줘"

    def finished(pid, fid, sid):
        wait_for(lambda: o.get(f"/api/flows/{fid}")["status"] == "complete" and not o.get(f"/api/sessions/{sid}/status")["busy"], timeout=300, what="흐름 끝")

    # 답하는 중: 제안이 보이자마자 누른다. 예전에는 '승인했습니다'가 409로 사라져 approved로 멈췄다
    pid = o.project("E0b")
    sid = o.new_chat(pid)
    o.post(f"/api/sessions/{sid}/prompt", {"text": text})
    fl = wait_for(lambda: flows(o, pid), timeout=120, interval=0.01, what="흐름 제안")[0]
    during = o.get(f"/api/sessions/{sid}/status")["busy"]
    r = o.c.put(f"/api/flows/{fl['id']}/status", json={"status": "approved", "version": fl["version"], "session_id": sid}).json()
    c(r["status"] == "approved", "승인됨")
    finished(pid, fl["id"], sid)
    said = [m["content"] for m in o.get(f"/api/sessions/{sid}/messages") if m["role"] == "user"]
    c(sum("승인했습니다" in x for x in said) == 1, f"승인을 한 번만 전함 {said}")
    c(any(m["deployed"] for m in models_of(o, pid)), "끝까지 가서 배포됨")
    c["turn_running_when_approved"] = during
    c["queued"] = r["start_queued"]

    # PC Agent 없음: 켜지면 저절로 시작한다
    pid = o.project("E0b2")
    sid = o.new_chat(pid)
    chat(o, sid, text)
    fl = flows(o, pid)[0]
    w.owner_pc.stop()
    wait_for(lambda: not o.get("/api/agent/status").get("connected"), timeout=30, what="끊김")
    r = o.c.put(f"/api/flows/{fl['id']}/status", json={"status": "approved", "version": fl["version"], "session_id": sid}).json()
    c(r["status"] == "approved" and r["start_queued"] is True, "PC Agent가 없으면 기다린다고 표시")
    w.owner_pc.start()
    wait_for(lambda: o.get(f"/api/flows/{fl['id']}")["status"] == "complete", timeout=300, what="연결된 뒤 끝까지")
    finished(pid, fl["id"], sid)
    c(any(m["deployed"] for m in models_of(o, pid)), "연결되자 시작해 끝까지 감")

    # 중지: 멈춤으로 표시하고 다시 시키지 않는다
    pid = o.project("E0b3")
    sid = o.new_chat(pid)
    chat(o, sid, text)
    fl = flows(o, pid)[0]
    o.c.put(f"/api/flows/{fl['id']}/status", json={"status": "approved", "version": fl["version"], "session_id": sid}).raise_for_status()
    wait_for(lambda: o.get(f"/api/flows/{fl['id']}")["status"] == "in_progress", timeout=60, interval=0.05, what="진행 시작")
    o.post(f"/api/sessions/{sid}/cancel")
    wait_for(lambda: not o.get(f"/api/sessions/{sid}/status")["busy"], timeout=120, what="중지")
    got = o.get(f"/api/flows/{fl['id']}")
    c(got["status"] == "stopped", f"중지하면 멈춤 {got['status']}")
    time.sleep(1)
    c(not o.get(f"/api/sessions/{sid}/status")["busy"], "중지한 뒤 다시 시키지 않음")
    c(any("멈췄습니다" in m["content"] and "→" in m["content"] for m in o.get(f"/api/sessions/{sid}/messages") if m["role"] == "system"), "멈춘 이유와 할 일을 말함")


@scenario("E1", "표 붙여넣기 → Simulator: 해석 0건, PC Agent·AI 없이", pc=False)
def e1(w: World, tmp: Path, c: Check):
    o = w.owner
    c(not o.get("/api/agent/status").get("connected"), "PC Agent 꺼짐")
    pid = o.project("E1")
    rows = design(24)
    text = excel_tsv(rows)
    t0 = time.time()
    prev = o.post("/api/tables/preview/text", {"text": text})
    c(prev["n_rows"] == 25, "빈 행을 빼고 25행으로 읽음")
    c("최대 응력" in prev["outputs"] and "두께" in prev["inputs"] and "폭" in prev["inputs"], "출력을 알아서 추측, 나머지 숫자 열은 입력")
    c(o.get(f"/api/projects/{pid}/studies") == [], "미리보기는 저장하지 않음")
    rep = o.post(f"/api/projects/{pid}/studies/import/text", {"text": text, "name": "붙여넣기", "outputs": [{"name": "최대 응력"}, {"name": "효율"}]})
    sid = rep["study_id"]
    c(rep["registered"] == 25 and rep["partial_count"] == 1, "수식 오류 행은 그 출력에서만 빠짐")
    c(rep["fixed"].get("재질") == "SS400" and rep["fixed"].get("하중") == 1000, "병합 셀·상수 열은 고정값")
    c({v["name"] for v in rep["variables"]} == {"두께", "폭"}, "입력은 두께·폭")
    run = o.c.post(f"/api/studies/{sid}/run", json={})
    c(run.status_code == 400 and "→" in run.json()["detail"], "해석을 돌리려 하면 이유와 할 일")
    job = train_poll(o, sid)
    model = job["model"]
    o.post(f"/api/models/{model['id']}/deploy")
    pred = o.post(f"/api/models/{model['id']}/predict", {"params": {"두께": 3.0, "폭": 25.0}})["mean"]
    truth = f_true(3.0, 25.0) * 1000
    item = next(x for x in o.get("/api/simulators")["mine"] if x["study_id"] == sid)
    cs = cases_of(o, sid)
    c(all(x["source"] == "import" and x["status"] == "complete" and not x["run_dir"] for x in cs), "모든 케이스가 가져온 값, 실행 기록 없음")
    c(item["data_source"]["rows"] == 25 and item["quality"], "Simulator 목록에 데이터 출처·품질")
    c(abs(pred["최대 응력"] - truth) / truth < 0.15, "처음 보는 점의 예측 오차 15% 이내(천 단위 쉼표를 값으로 읽음)")
    c(abs(pred["효율"] - 59.0) < 2, "퍼센트 열을 숫자로 읽음")
    c(o.get(f"/api/projects/{pid}/sessions") == [], "대화·AI를 쓰지 않음")
    c(not o.get("/api/agent/status").get("connected"), "끝까지 PC Agent 없음")
    c["rows"] = len(cs)
    c["solved"] = 0
    c["chats"] = 0
    c["fixed"] = rep["fixed"]
    c["r2"] = r2_of(model)
    c["quality"] = item["quality"]
    c["pred_error_pct"] = round(abs(pred["최대 응력"] - truth) / truth * 100, 2)
    c["total_sec"] = round(time.time() - t0, 2)


@scenario("E1a", "입력창의 표를 AI로: 묻는 것은 출력 하나뿐, 행은 도구 인자로 가지 않음, Flow 0개")
def e1a(w: World, tmp: Path, c: Check):
    o = w.owner
    rows = design(16)
    pid = o.project("E1a")
    sid = o.new_chat(pid)
    t = chat(o, sid, "이 표로 Simulator 만들어 줘", [attach_paste(o, pid, excel_tsv(rows), "100001")])
    c(not t.errors and "Branin" not in t.reply, "엉뚱한 시나리오로 빠지지 않음")
    c([x.split(".")[-1] for x in t.tools] == ["inspect_table"], "먼저 표만 읽음")
    c(t.reply.count("?") == 1 and "출력" in t.reply and "최대 응력" in t.reply, "출력이 맞는지 한 번만 물음")
    c(o.get(f"/api/projects/{pid}/studies") == [], "답을 받기 전에는 등록하지 않음")
    t2 = chat(o, sid, "응, 맞아")
    c(not t2.errors and "?" not in t2.reply, "더 묻지 않음")
    c([x.split(".")[-1] for x in t2.tools] == ["set_project_info", "import_table", "train_surrogate", "deploy_model"], "도구 순서")
    st = study_of(o, pid)
    c(st["adapter"] == "data" and models_of(o, pid)[0]["deployed"] and flows(o, pid) == [], "Flow 없이 배포")
    raw = json.dumps([(m.get("meta") or {}).get("raw_input") for m in o.get(f"/api/sessions/{sid}/messages") if m["role"] == "tool"], ensure_ascii=False)
    c("SS400" not in raw and f"{f_true(*rows[0]) * 1000:,.2f}" not in raw, "행을 AI 도구 인자로 나르지 않음")
    c(set(models_of(o, pid)[0]["outputs"]) == {"최대 응력", "효율"}, "#N/A가 한 칸 있어도 고른 출력이 모델에 남음")
    # 요청에 출력을 적으면 묻지 않는다
    pid2 = o.project("E1a 출력 명시")
    t3 = chat(o, o.new_chat(pid2), "최대 응력이 결과야. 이 표로 Simulator 만들어 줘", [attach_paste(o, pid2, excel_tsv(rows), "100002")])
    c("?" not in t3.reply and models_of(o, pid2)[0]["outputs"] == ["최대 응력"], "출력을 적으면 묻지 않고 그 열만 출력")
    # 암호화되지 않은 표 경로: PC의 MCP가 직접 읽는다
    src = tmp / "두께 표.csv"
    src.write_text(table_text(design(16)), encoding="utf-8")
    pid3 = o.project("E1a 경로")
    t4 = chat(o, o.new_chat(pid3), f'이 표로 Simulator 만들어 줘: "{src}"')
    c(not t4.errors and models_of(o, pid3)[0]["deployed"] and flows(o, pid3) == [], "경로로 준 표도 Flow 없이 배포")
    c["questions"] = 1
    c["first_reply_sec"] = t.first_reply_sec
    c["answer_turn_sec"] = t2.sec
    c["flows"] = 0
    c["r2"] = r2_of(models_of(o, pid)[0])


@scenario("E1b", "엑셀 복사 형식(천 단위·퍼센트·빈 행·줄바꿈·병합 빈칸·#N/A·머리글 없음), 암호화 파일 안내")
def e1b(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E1b")
    rows = design(12, seed=5)
    base = [f"{t}\t{w_}\t{f_true(t, w_) * 1000:.2f}" for t, w_ in rows]
    head = "두께 [mm]\t폭 [mm]\t응력 [MPa]"
    shapes = {
        "천 단위 쉼표": (head + "\r\n" + "\r\n".join(f'{t}\t{w_}\t"{f_true(t, w_) * 1000:,.2f}"' for t, w_ in rows), lambda p: p["columns"][2]["kind"] == "numeric" and p["columns"][2]["max"] > 1000),
        "퍼센트": ("두께\t효율\n" + "\n".join(f"{t}\t{50 + t:.1f}%" for t, _ in rows), lambda p: p["columns"][1]["kind"] == "numeric" and p["columns"][1]["unit"] == "%"),
        "빈 행·빈 열": ("두께\t\t폭\t응력\n" + "\n".join(f"{t}\t\t{w_}\t{f_true(t, w_):.3f}\n\t\t\t" for t, w_ in rows), lambda p: p["n_rows"] == 12 and [x["name"] for x in p["columns"]] == ["두께", "폭", "응력"]),
        "따옴표 안 줄바꿈": ('두께\t폭\t"최대\n응력"\n' + "\n".join(base), lambda p: p["columns"][2]["name"] == "최대 응력" and p["n_rows"] == 12),
        "병합 셀 빈칸": ("재질\t두께\t폭\t응력\n" + "\n".join(f"{'SPCC' if i == 0 else ''}\t{x}" for i, x in enumerate(base)), lambda p: p["n_rows"] == 12),
        "수식 오류": (head + "\n" + "\n".join([base[0].rsplit("\t", 1)[0] + "\t#N/A", base[1].rsplit("\t", 1)[0] + "\t#DIV/0!", *base[2:]]), lambda p: p["columns"][2]["kind"] == "numeric" and p["columns"][2]["n_error"] == 2),
        "머리글 없음": ("\n".join(base), lambda p: p["n_rows"] == 12 and p["columns"][0]["name"] == "열1"),
        "제목 줄·단위·한글": ("2026 브래킷 결과\n\n" + head + "\n" + "\n".join(base), lambda p: p["n_rows"] == 12 and p["columns"][0]["unit"] == "mm" and p["columns"][2]["name"] == "응력"),
    }
    read = {}
    for name, (text, ok) in shapes.items():
        prev = o.post("/api/tables/preview/text", {"text": text})
        c(ok(prev), f"{name}을 알아서 읽음")
        rep = o.post(f"/api/projects/{o.project(f'E1b {name}')}/studies/import/text", {"text": text, "name": name})  # 같은 값이라 프로젝트를 나눈다. 한 프로젝트면 같은 표로 본다
        c(rep["registered"] >= 10, f"{name}: 등록 {rep['registered']}행")
        read[name] = {"행": prev["n_rows"], "등록": rep["registered"], "제외": rep["excluded_count"]}
    # 보안 프로그램으로 암호화된 파일: 깨진 표도 500도 아니고 정해진 안내
    enc = {"암호화.xlsx": b"\x00DRM\xff\xfe" * 200, "암호화.csv": bytes(np.random.default_rng(1).integers(0, 256, 4096, dtype=np.uint8))}
    answers = {}
    for name, data in enc.items():
        path = tmp / name
        path.write_bytes(data)
        for tool in ("inspect_table", "import_table"):
            try:
                w.owner_pc.mcp("dds-study", tool, {"path": str(path)}, pid)
                raise RuntimeError(f"{tool}가 암호화 파일을 읽었다고 답함")
            except AssertionError as e:
                c(ENCRYPTED in str(e), f"{tool}({name}): 정해진 안내 ({str(e)[:80]})")
        r = o.c.post(f"/api/projects/{pid}/studies/import/preview", files={"file": (name, data)})
        c(r.status_code == 400 and ENCRYPTED in r.json()["detail"], f"{name} 서버 경로 400")
        answers[name] = r.status_code
    studies_before = len(o.get(f"/api/projects/{pid}/studies"))
    t3 = chat(o, o.new_chat(pid), f'"{tmp / "암호화.xlsx"}" 표로 Simulator 만들어 줘')
    c(ENCRYPTED in t3.reply and not t3.errors and "Error executing" not in t3.reply, "대화에서도 추측 없이 붙여넣기를 안내")
    c(len(o.get(f"/api/projects/{pid}/studies")) == studies_before and models_of(o, pid) == [], "암호화 파일로 스터디·모델이 생기지 않음")
    # 암호화되지 않은 XLSX·CP949도 그대로 읽는다(사내 밖 자료)
    grid = [["2026 브래킷 결과 정리"], [], ["번호", "두께(mm)", "폭 [mm]", "재질", "응력 [MPa]"]]
    grid += [[i + 1, t, w_, "SPCC", round(f_true(t, w_), 4)] for i, (t, w_) in enumerate(rows)]
    rep = o.post(f"/api/projects/{pid}/studies/import/file", files={"file": ("결과.xlsx", xlsx_bytes(grid))}, data={"mapping": json.dumps({"outputs": [{"name": "응력"}]})})
    c(rep["registered"] == 12 and rep["fixed"].get("재질") == "SPCC", "일반 XLSX")
    rep2 = o.post(f"/api/projects/{pid}/studies/import/file", files={"file": ("엑셀저장.csv", table_text(design(10, seed=6), head=("두께 [mm]", "폭 [mm]", "응력 [MPa]")).encode("cp949"))}, data={"mapping": "{}"})
    c(rep2["registered"] == 10, "일반 CP949 CSV")
    c["read"] = read
    c["encrypted_status"] = answers
    c["encrypted_reply"] = ENCRYPTED


@scenario("E2", "해석 폴더 N개 → Simulator: 재해석 0건, 원본 변경 0")
def e2(w: World, tmp: Path, c: Check):
    o = w.owner
    runs = case_folders(tmp / "runs", design(16, seed=7))
    before = tree_hash(runs)
    pid = o.project("E2")
    t = chat(o, o.new_chat(pid), f'이미 해석한 결과가 "{runs}" 에 있어. 이걸로 Simulator 만들어 줘')
    c(not t.errors and flows(o, pid) == [], "Flow 없이 진행")
    st = study_of(o, pid)
    cs = cases_of(o, st["id"])
    c(len(cs) == 16 and all(x["status"] == "complete" for x in cs), "케이스 16건 완료")
    c(all(x["source"] == "import" and x["run_dir"] and str(runs) in x["run_dir"] for x in cs), "원본 경로를 기록만 함")
    c(tree_hash(runs) == before, "원본 파일 변경 0")
    c(o.c.post(f"/api/studies/{st['id']}/run", json={}).status_code == 400, "다시 돌리지 않음")
    c(models_of(o, pid)[0]["deployed"], "배포됨")
    c["cases"] = len(cs)
    c["solved"] = 0
    c["original_changed"] = 0
    c["r2"] = r2_of(models_of(o, pid)[0])
    c["turn_sec"] = t.sec


@scenario("E2b", "입력·출력 파일 분리: 키로 잇고 빠진 행을 알림")
def e2b(w: World, tmp: Path, c: Check):
    o = w.owner
    rows = design(14, seed=8)
    inputs = "id,두께 [mm],폭 [mm]\n" + "\n".join(f"{i},{t},{w_}" for i, (t, w_) in enumerate(rows)) + "\n"
    outputs = "id,응력 [MPa]\n" + "\n".join(f"{i},{f_true(t, w_):.5f}" for i, (t, w_) in enumerate(rows) if i < 12) + "\n"  # 2건은 해석 실패로 출력이 없다
    a, b = tmp / "입력.csv", tmp / "출력.csv"
    a.write_text(inputs, encoding="utf-8")
    b.write_text(outputs, encoding="utf-8")
    pid = o.project("E2b")
    rep = o.post(f"/api/projects/{pid}/studies/import/file", files={"file": (a.name, a.read_bytes()), "outputs_file": (b.name, b.read_bytes())},
                 data={"mapping": json.dumps({"key": "id", "outputs": [{"name": "응력"}]})})
    c(rep["registered"] == 12, "출력이 있는 12행만 등록")
    c(rep["join"]["only_left"] == ["12", "13"], "출력이 없어 뺀 행을 알림")
    pid2 = o.project("E2b 대화")
    t = chat(o, o.new_chat(pid2), f'입력 표 "{a}" 와 출력 표 "{b}" 로 Simulator 만들어 줘')
    st = study_of(o, pid2)
    c(not t.errors and counts(o, st["id"]) == {"complete": 12} and models_of(o, pid2)[0]["deployed"], "대화로도 12행 배포")
    c["registered"] = rep["registered"]
    c["join"] = rep["join"]
    c["excluded"] = rep["excluded_count"]


@scenario("E3", "붙여 넣은 표 + 행 추가 → v2, 범위 넓힘 보고, 공유 링크가 따라감")
def e3(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E3")
    sid = o.new_chat(pid)
    chat(o, sid, "stress가 결과야. 이 표로 Simulator 만들어 줘", [attach_paste(o, pid, paste_tsv(design(14, seed=9)), "300001")])
    v1 = models_of(o, pid)[0]
    token = o.post(f"/api/models/{v1['id']}/shares", {})["token"]
    # 후속 턴의 첫 문단을 잃지 않는다
    t = chat(o, sid, "행 추가해 줘\n\n어제 나온 값이야", [attach_paste(o, pid, paste_tsv(design(6, seed=10, lo=(5.5, 41.0), hi=(7.0, 60.0))), "300002")])
    ms = models_of(o, pid)
    v2 = ms[0]
    c(not t.errors and sorted(m["version"] for m in ms) == [1, 2], "새 버전 v2")
    st = o.get(f"/api/studies/{study_of(o, pid)['id']}")
    c(st["counts"] == {"complete": 20} and st["study"]["variables"][0]["upper"] >= 5.5, "20행, 범위 넓어짐")
    share = w.outsider.get(f"/api/shares/{token}")
    c(share["model"]["id"] == v2["id"], "공유 링크가 v2를 가리킴")
    pred = w.outsider.post(f"/api/shares/{token}/predict", {"params": {"두께": 6.0, "폭": 50.0}})
    c(pred["mean"]["stress"] > 0, "공유 링크로 예측")
    c["versions"] = [1, 2]
    c["rows_v2"] = v2["n_train"]
    c["share_model_version"] = share["model"]["version"]
    c["r2_v2"] = r2_of(v2)


@scenario("E3b", "같은 표 두 번 붙여넣기: 중복 0, 새 버전 0")
def e3b(w: World, tmp: Path, c: Check):
    o = w.owner
    text = paste_tsv(design(12, seed=11))
    pid = o.project("E3b")
    chat(o, o.new_chat(pid), "stress가 결과야. 이 표로 Simulator 만들어 줘", [attach_paste(o, pid, text, "310001")])
    st = study_of(o, pid)
    n_models = len(models_of(o, pid))
    t = chat(o, o.new_chat(pid), "stress가 결과야. 이 표로 Simulator 만들어 줘", [attach_paste(o, pid, text, "310002")])  # 새 대화에서 다시
    c(not t.errors, "오류 없음")
    c(len(o.get(f"/api/projects/{pid}/studies")) == 1, "대화로 다시 넣어도 스터디 1개 유지")
    c(counts(o, st["id"]) == {"complete": 12}, "행 12개 유지")
    # 화면 경로(AI 없음)로 같은 글을 다시
    pid2 = o.project("E3b 화면")
    a = o.post(f"/api/projects/{pid2}/studies/import/text", {"text": text, "outputs": [{"name": "stress"}]})
    b = o.post(f"/api/projects/{pid2}/studies/import/text", {"text": text, "outputs": [{"name": "stress"}]})
    c(b["reused"] and b["study_id"] == a["study_id"] and b["registered"] == 0, "화면 경로도 같은 스터디를 돌려줌")
    rep = o.post(f"/api/studies/{st['id']}/cases/import", {"rows": [{"두께": x["params"]["두께"], "폭": x["params"]["폭"], "stress": x["results"]["stress"]} for x in cases_of(o, st["id"])]})
    c(rep["registered"] == 0 and rep["duplicates"] == 12, "행 추가 경로로 넣어도 중복 12건")
    c(len(models_of(o, pid)) == n_models, "데이터가 같으면 새 버전을 만들지 않음")
    c["duplicates"] = rep["duplicates"]
    c["models"] = n_models


@scenario("E4", "붙여 넣은 표 모델 → 최적화: 후보만, '확인되지 않음' 표시, 케이스 증가 0")
def e4(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E4")
    sid = o.new_chat(pid)
    chat(o, sid, "stress가 결과야. 이 표로 Simulator 만들어 줘", [attach_paste(o, pid, paste_tsv(design(20, seed=12)), "320001")])
    st = study_of(o, pid)
    no_goal = chat(o, sid, "최적화해서 후보 찾아 줘")
    c("objective" in no_goal.reply and "→" in no_goal.reply, "목적이 없으면 이유와 할 일")
    t = chat(o, sid, "stress 최소가 되는 후보 찾아 줘")
    c("optimize" in t.tools[-1] and not t.errors, "optimize 호출")
    c("확인되지 않은" in t.reply, "확인되지 않은 예측이라고 말함")
    c(counts(o, st["id"]) == {"complete": 20}, "케이스 그대로")
    out = o.post(f"/api/studies/{st['id']}/candidates", {"objective": "stress", "goal": "min", "n": 3})
    c(out["verified"] is False and len(out["candidates"]) == 3, "후보 3개 모두 미확인")
    p = out["candidates"][0]
    pred, truth = p["predicted"]["stress"], f_true(p["params"]["두께"], p["params"]["폭"])
    c["candidates"] = len(out["candidates"])
    c["verified"] = False
    c["cases_added"] = 0
    c["best_pred"] = round(pred, 3)
    c["best_true"] = round(truth, 3)


@scenario("E5", "나쁜 표 붙여넣기: NaN·상수·문자·3행 미만·빈 글·이상치·엑셀 오류: 500 없음")
def e5(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E5")
    rows = design(12, seed=13)
    good = paste_tsv(rows).splitlines()
    cases = {
        "NaN 값": "\n".join([good[0], good[1].rsplit("\t", 1)[0] + "\tNaN", *good[2:]]),
        "Inf 값": "\n".join([good[0], good[1].rsplit("\t", 1)[0] + "\tinf", *good[2:]]),
        "엑셀 오류 값": "\n".join([good[0], good[1].rsplit("\t", 1)[0] + "\t#DIV/0!", good[2].rsplit("\t", 1)[0] + "\t#N/A", *good[3:]]),
        "문자 값": "\n".join([good[0], good[1].rsplit("\t", 1)[0] + "\t실패", *good[2:]]),
        "이상치": "\n".join([*good, "4.0\t20.0\t99999999"]),
        "상수 입력 열": "두께\t폭\tstress\n" + "\n".join(f"2\t{w_}\t{f_true(2, w_):.4f}" for _, w_ in rows),
        "범주 열": "재질\t두께\tstress\n" + "\n".join(f"{'AB'[i % 2]}\t{t}\t{f_true(t, 20):.4f}" for i, (t, _) in enumerate(rows)),
        "1행": "\n".join(good[:2]),
        "2행": "\n".join(good[:3]),
        "출력 없음": "\n".join(ln.rsplit("\t", 1)[0] for ln in good),
        "머리글만": good[0],
        "빈 글": "",
        "공백만": "   \n\n  ",
        "글자 묶음": "안녕하세요\n표가 아닙니다\n그냥 글입니다",
    }
    codes, reasons = {}, {}
    for name, text in cases.items():
        r = o.c.post(f"/api/projects/{pid}/studies/import/text", json={"text": text, "name": name})
        codes[name] = r.status_code
        c(r.status_code in (200, 400), f"{name}: 500 아님 ({r.status_code}: {r.text[:120]})")
        body = r.json()
        if r.status_code == 200:
            reasons[name] = {"등록": body["registered"], "제외": body["excluded_count"], "경고": len(body["warnings"])}
            sid = body["study_id"]
            c(json.dumps(o.get(f"/api/studies/{sid}/cases")).find("NaN") < 0, f"{name}: NaN이 저장되지 않음")
            m = o.c.post(f"/api/studies/{sid}/train", json={})
            c(m.status_code in (200, 400), f"{name}: 학습도 500 아님 ({m.status_code})")
        else:
            detail = body["detail"] if isinstance(body["detail"], str) else json.dumps(body["detail"], ensure_ascii=False)
            c("→" in detail, f"{name}: 이유와 할 일 ({detail[:80]})")
            reasons[name] = detail[:70]
    c(codes["NaN 값"] == 200 and reasons["NaN 값"]["제외"] == 1, "NaN 행 1개는 이유와 함께 제외")
    c(codes["엑셀 오류 값"] == 200 and reasons["엑셀 오류 값"]["제외"] == 2, "엑셀 오류 행 2개 제외")
    c(codes["이상치"] == 200 and reasons["이상치"]["경고"] >= 1, "이상치 경고")
    for name in ("1행", "2행", "빈 글", "머리글만"):
        c(codes[name] == 400, f"{name}는 거부")
    c("3행" in reasons["1행"] and "3행" in reasons["2행"], f"3행 미만 문구: {reasons['1행']}")
    # 입력창으로 받아 AI로 보내도 턴이 죽지 않고 이유와 할 일을 말한다
    t = chat(o, o.new_chat(pid), "stress가 결과야. 이 표로 만들어 줘", [attach_paste(o, pid, "\n".join(good[:2]), "330001")])
    c("→" in t.reply and not t.errors and "Error executing" not in t.reply, "대화에서도 이유와 할 일")
    c["status"] = codes
    c["reasons"] = reasons
    c["chat_reply"] = t.reply[:80]


@scenario("E6", "Simulator → Output 추가 → 재학습 → 옛 버전으로 되돌리기")
def e6(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E6")
    sid = o.new_chat(pid)
    chat(o, sid, "드럼세탁기 탈수 진동을 줄여 줘")
    fid = flows(o, pid)[0]["id"]
    run = approve(o, sid, fid)
    c(not run.errors and o.get(f"/api/flows/{fid}")["status"] == "complete", "세탁기 흐름 완료")
    st = study_of(o, pid)
    n_cases = sum(counts(o, st["id"]).values())
    live = next(m for m in models_of(o, pid) if m["deployed"])
    before = o.post(f"/api/models/{live['id']}/predict", {"params": {"spring_k": 0, "damper_c": 0, "balancer_m": 0, "spring_pos": 0}})["mean"]
    t = chat(o, sid, "바닥 전달력도 Output에 추가해 줘")
    cs = cases_of(o, st["id"])
    c("add_outputs" in t.tools[-1] and not t.errors, "add_outputs 호출")
    c(len(cs) == n_cases and all("floor_force" in x["results"] for x in cs if x["status"] == "complete"), "재해석 없이 모든 완료 케이스에서 뽑음")
    new = o.post(f"/api/studies/{st['id']}/train", {"outputs": ["vibration", "floor_force"]})
    o.post(f"/api/models/{new['id']}/deploy")
    spec = o.get(f"/api/models/{new['id']}/spec")
    names = [x["name"] if isinstance(x, dict) else x for x in spec["model"]["outputs"]]
    c("floor_force" in names, "새 버전 Simulator에 새 Output")
    o.post(f"/api/models/{live['id']}/deploy")  # 되돌리기
    deployed = [m["id"] for m in models_of(o, pid) if m["deployed"]]
    c(deployed == [live["id"]], "되돌린 버전만 배포 상태")
    after = o.post(f"/api/models/{live['id']}/predict", {"params": {"spring_k": 0, "damper_c": 0, "balancer_m": 0, "spring_pos": 0}})["mean"]
    c(after == before, "되돌린 버전의 예측이 그대로")
    c["cases"] = n_cases
    c["cases_resolved_for_output"] = 0
    c["version_with_output"] = new["version"]
    c["rolled_back_to"] = live["version"]
    c["flow_sec"] = run.sec


@scenario("E7", "결과만 있음 → 보고서: Flow 없이")
def e7(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E7 빈 프로젝트")
    t0 = chat(o, o.new_chat(pid), "보고서 써 줘")
    c("결과가 아직 없습니다" in t0.reply and flows(o, pid) == [], "결과가 없으면 무엇이 필요한지 안내")
    # 해석 결과 1건만 올린 프로젝트
    pid1 = o.project("E7 결과 1건")
    o.post(f"/api/projects/{pid1}/artifacts", {"kind": "markdown", "title": "최대 응력 1건", "payload": {"text": "최대 응력 182 MPa"}})
    t1 = chat(o, o.new_chat(pid1), "있는 결과로 보고서 작성해 줘")
    reports1 = o.get(f"/api/projects/{pid1}/reports")
    c(not t1.errors and reports1 and flows(o, pid1) == [], "결과 1건으로 보고서, Flow 0")
    # 표에서 만든 모델이 있는 프로젝트
    pid2 = o.project("E7 표")
    text = table_text(design(12, seed=14))
    sid = o.post(f"/api/projects/{pid2}/studies/import/text", {"text": text, "name": "표"})["study_id"]
    train_poll(o, sid)
    t2 = chat(o, o.new_chat(pid2), "최신 결과로 보고서를 다시 작성해 줘")
    rep = o.get(f"/api/projects/{pid2}/reports/latest")
    c(not t2.errors and rep and flows(o, pid2) == [], "표 모델로 보고서, Flow 0")
    c(o.c.get(f"/api/projects/{pid2}/report?format=docx").content[:2] == b"PK", "Word 파일을 받을 수 있음")
    c["flows"] = 0
    c["reports"] = [len(reports1), len(o.get(f"/api/projects/{pid2}/reports"))]
    c["turn_sec"] = t2.sec


@scenario("E8", "단순 조회: Flow 0개, 읽기 도구만")
def e8(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E8")
    sid = o.new_chat(pid)
    a = chat(o, sid, "이 프로젝트에 모델 몇 개 있어?")
    c("스터디가 없습니다" in a.reply, "빈 프로젝트에 맞는 답")
    text = table_text(design(10, seed=15))
    study = o.post(f"/api/projects/{pid}/studies/import/text", {"text": text, "name": "조회용"})["study_id"]
    train_poll(o, study)
    b = chat(o, sid, "지금 모델 정확도 알려 줘")
    d = chat(o, sid, "R²가 뭐야?")
    c("스터디 1개" in b.reply and "R²" in b.reply, "있는 모델의 정확도를 답함")
    c({x.split(".")[-1] for x in a.tools + b.tools} <= {"list_studies", "list_models"}, "읽기 도구만 씀")
    c(flows(o, pid) == [] and counts(o, study) == {"complete": 10}, "Flow 0, 케이스 변화 0")
    c(d.tools == [] and "결정계수" in d.reply, "일반 질문은 도구 없이")
    c["flows"] = 0
    c["tools"] = sorted({x.split(".")[-1] for x in a.tools + b.tools})
    c["first_reply_sec"] = [a.first_reply_sec, b.first_reply_sec]


@scenario("E9", "CAD/CAE 툴 없음 + 붙여 넣은 표: 거부하지 않음")
def e9(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E9")
    sid = o.new_chat(pid)
    q = chat(o, sid, "이 PC에 어떤 툴 있어?")
    tools = w.owner_pc.mcp("dds-cae", "list_tools", {}, pid)
    missing = [t["id"] for t in tools["tools"] if t["status"] == "missing"]
    c("list_tools" in q.tools[-1] and missing, "없는 툴이 있음")
    t = chat(o, sid, f"{missing[0]} 없어도 돼. stress가 결과야. 이 표로 Simulator 만들어 줘", [attach_paste(o, pid, paste_tsv(design(12, seed=16)), "390001")])
    c(not t.errors and flows(o, pid) == [], "Flow 없이 진행")
    c(models_of(o, pid)[0]["deployed"] and study_of(o, pid)["adapter"] == "data", "표로 배포")
    c(not any("list_tools" in x for x in t.tools), "데이터 작업에 툴 확인이 끼지 않음")
    # 없는 툴로 해석을 돌리면: 원인이 보인다. 실제 툴이 있는 PC에서 실행되지 않도록 없는 키만 고른다
    key = next((k["tool"] for k in tools["commands"] if not k["found"]), None)
    c(key, "없는 실행 명령 키")
    bad = o.post(f"/api/projects/{pid}/studies", {"name": "없는 툴", "adapter": "script", "adapter_config": {"tool": key}, "variables": [{"name": "x", "lower": 0, "upper": 1}]})["id"]
    o.post(f"/api/studies/{bad}/doe", {"n": 1, "seed": 1})
    o.post(f"/api/studies/{bad}/run", {})
    wait_for(lambda: not o.get(f"/api/studies/{bad}")["running"], timeout=60, what="실패 보고")
    err = cases_of(o, bad)[0]
    c(err["status"] == "failed" and "→" in err["error"], "케이스가 실패로 끝나고 이유와 할 일이 남음")
    c["missing_tools"] = len(missing)
    c["ready_tools"] = sum(t["status"] == "ready" for t in tools["tools"])
    c["r2"] = r2_of(models_of(o, pid)[-1])
    c["missing_tool_error"] = err["error"][:100]


@scenario("E10", "Flow 검토 중 다른 요청: 막히지 않고 Flow는 그대로")
def e10(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E10")
    sid = o.new_chat(pid)
    chat(o, sid, "mock 솔버로 Branin 최적화하고 Simulator 배포해줘")
    fid = flows(o, pid)[0]["id"]
    q = chat(o, sid, "이 프로젝트에 스터디 있어?")
    a = tmp / "e10.csv"
    a.write_text(table_text(design(10, seed=17)), encoding="utf-8")
    t = chat(o, sid, f'이 표로 Simulator 만들어 줘: "{a}"')
    c(not q.errors and not t.errors, "두 요청 모두 답함")
    c(o.get(f"/api/flows/{fid}")["status"] == "review", "Flow는 검토 중 그대로")
    c(models_of(o, pid)[0]["deployed"], "표 모델은 배포됨")
    done = approve(o, sid, fid)
    c(o.get(f"/api/flows/{fid}")["status"] == "complete" and not done.errors, "뒤늦게 승인해도 끝까지 진행")
    c["question_sec"] = q.sec
    c["table_sec"] = t.sec
    c["flow_status_during"] = "review"
    c["studies"] = len(o.get(f"/api/projects/{pid}/studies"))


@scenario("E11", "세션 유지: 후속 턴 재시작 0, 재연결·서버 재시작·CLI 사망 복구(e2e_sessions)")
def e11(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E11")
    sid = o.new_chat(pid)
    first = chat(o, sid, "이 프로젝트에 스터디 있어?")
    acp = next(x for x in o.get(f"/api/projects/{pid}/sessions") if x["id"] == sid)["agent_session_id"]
    later = [chat(o, sid, "이 프로젝트에 모델 있어?") for _ in range(3)]
    again = next(x for x in o.get(f"/api/projects/{pid}/sessions") if x["id"] == sid)["agent_session_id"]
    c(acp and acp == again, "세션 ID 그대로")
    c(max(t.sec for t in later) < max(first.sec, 3), "후속 턴에 세션을 다시 열지 않음")
    # 프로세스 횟수는 기록을 남기는 가짜 CLI로 잰다
    p = subprocess.run([sys.executable, str(ROOT / "scripts" / "e2e_sessions.py"), "--no-browser", "--start-delay", "3"], cwd=ROOT, env={**w.env, "DDS_DATA_DIR": ""},
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=480, stdin=subprocess.DEVNULL, **hidden_kwargs())
    c(p.returncode == 0 and "E2E 통과" in p.stdout, f"e2e_sessions 통과: {p.stdout[-300:]}{p.stderr[-300:]}")
    rep = json.loads(p.stdout[p.stdout.index("{\n"):p.stdout.rindex("}") + 1])
    c(rep["follow_up"]["process_starts"] == 0 and rep["follow_up"]["session_load"] == 0, "후속 3턴 재시작·load 0")
    c(rep["reconnect"]["process_starts"] == 0, "재연결 재시작 0")
    c(rep["server_restart"]["process_starts"] == 0, "서버 재시작 뒤에도 재시작 0")
    c["first_turn_sec"] = first.sec
    c["next_turn_sec"] = [t.sec for t in later]
    c["follow_up"] = rep["follow_up"]
    c["reconnect"] = rep["reconnect"]
    c["server_restart"] = rep["server_restart"]
    c["cli_death"] = rep["cli_death"]


@scenario("E12", "DOE 계획만 → CSV 내보내기 → 일부 값 채움 → 나중에 해석 연결")
def e12(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E12")
    t = chat(o, o.new_chat(pid), "DOE 계획만 8점 뽑아 줘. x1 -5~10, x2 0~15")
    st = study_of(o, pid)
    c(counts(o, st["id"]) == {"pending": 8} and not t.errors, "대기 8점, 해석 없음")
    c(flows(o, pid) == [], "계획만 뽑는 데 Flow 없음")
    csv_text = o.c.get(f"/api/studies/{st['id']}/doe.csv").content.decode("utf-8-sig")
    lines = csv_text.strip().splitlines()
    c(len(lines) == 9, "CSV 8행")
    run = o.c.post(f"/api/studies/{st['id']}/run", json={})
    c(run.status_code == 400 and "→" in run.json()["detail"], "해석 방법이 없으면 이유와 할 일")
    # 밖에서 4점을 해석해 왔다
    head = lines[0].split(",")
    filled = [lines[0]]
    for ln in lines[1:5]:
        v = ln.split(",")
        v[head.index("f")] = str(branin(float(v[head.index("x1")]), float(v[head.index("x2")])))
        # 엑셀을 거치면 보이는 자릿수만 남는다. 정확히 같은 값만 알아보면 이 경로는 실제로 쓸 수 없다
        for name in ("x1", "x2"):
            v[head.index(name)] = f"{float(v[head.index(name)]):.4g}"
        filled.append(",".join(v))
    back = tmp / "계획결과.csv"
    back.write_text("\n".join(filled) + "\n", encoding="utf-8")
    t2 = chat(o, o.new_chat(pid), f'DOE 계획 결과 표 "{back}" 넣어 줘')
    c(not t2.errors and counts(o, st["id"]) == {"complete": 4, "pending": 4}, "자릿수가 줄어든 값으로도 4점 채움, 스터디 그대로")
    imported = {x["id"]: x["results"] for x in cases_of(o, st["id"]) if x["status"] == "complete"}
    sid3 = o.new_chat(pid)  # 2주 뒤 새 대화
    t3 = chat(o, sid3, "이제 해석 방법 연결해서 나머지 돌려 줘")
    fl = [f for f in flows(o, pid) if f["status"] == "review"]
    c(len(fl) == 1 and counts(o, st["id"]) == {"complete": 4, "pending": 4}, "승인 전에는 돌리지 않음")
    done = approve(o, sid3, fl[0]["id"])
    c(not done.errors and counts(o, st["id"]) == {"complete": 8}, "남은 4점 해석")
    now = {x["id"]: x["results"] for x in cases_of(o, st["id"])}
    c(all(now[k]["f"] == v["f"] for k, v in imported.items()), "가져온 값은 다시 돌리지 않음")
    c(models_of(o, pid)[0]["deployed"], "배포됨")
    c["csv_rows"] = 8
    c["filled"] = 4
    c["solved_later"] = 4
    c["run_before_connect"] = run.status_code
    c["reply_after_connect"] = t3.reply[:50]


@scenario("E13", "필드 결과만 → 3D Simulator: 스칼라 없어도 성립")
def e13(w: World, tmp: Path, c: Check):
    o = w.owner
    rows = design(8, seed=18)
    root = case_folders(tmp / "fields", rows, npz=True)
    before = tree_hash(root)
    pid = o.project("E13")
    t = chat(o, o.new_chat(pid), f'이미 해석한 결과가 "{root}" 에 있어. 이걸로 Simulator 만들어 줘')
    c(not t.errors and flows(o, pid) == [], "Flow 없이 진행")
    st = study_of(o, pid)
    cs = cases_of(o, st["id"])
    c(len(cs) == 8 and all(x["fields"] == ["mises"] for x in cs), "케이스별 필드 등록")
    m = models_of(o, pid)[0]
    c("mises_max" in m["outputs"] and m["deployed"], "필드 최대값을 출력으로 만들어 배포")
    fms = o.get(f"/api/studies/{st['id']}/field-models")
    c(len(fms) == 1 and fms[0]["field"] == "mises", "3D 필드 모델")
    t_, w_ = rows[0]
    pred = o.post(f"/api/field-models/{fms[0]['id']}/predict", {"params": {"t": t_, "w": w_}})
    n = len(np.frombuffer(base64.b64decode(pred["values_b64"]), dtype=np.float32))
    c(n == 40 and pred["max"] > pred["min"], "3D 예측이 노드 40개 값을 줌")
    c(tree_hash(root) == before, "원본 변경 0")
    c["cases"] = len(cs)
    c["auto_output"] = "mises_max"
    c["r2"] = r2_of(m)
    c["field_nodes"] = n


@scenario("E14", "다른 프로젝트의 스터디 찾기·복제, 멤버 아니면 못 봄")
def e14(w: World, tmp: Path, c: Check):
    o = w.owner
    a = o.project("E14 지난 과제")
    src = o.post(f"/api/projects/{a}/studies/import/text", {"text": table_text(design(10, seed=19)), "name": "지난 브래킷"})["study_id"]
    b = o.project("E14 새 과제")
    t = chat(o, o.new_chat(b), "다른 프로젝트에 어떤 스터디 있어?")
    c("지난 브래킷" in t.reply and "E14 지난 과제" in t.reply, "다른 프로젝트 스터디를 찾음")
    c(flows(o, b) == [], "Flow 0")
    copy = o.post(f"/api/studies/{src}/clone", {"name": "복제", "project_id": b})
    c(copy["project_id"] == b and o.get(f"/api/studies/{copy['id']}")["counts"] == {}, "설정만 복제, 케이스 0")
    c(counts(o, src) == {"complete": 10}, "원본 그대로")
    denied = w.outsider.c.get(f"/api/studies/{src}")
    deny_clone = w.outsider.c.post(f"/api/studies/{src}/clone", json={"name": "훔침"})
    c(denied.status_code == 403 and deny_clone.status_code == 403, "멤버가 아니면 403")
    c["found"] = "지난 브래킷"
    c["copied_cases"] = 0
    c["outsider_status"] = denied.status_code


def new_task(who: Who, pid: int, name: str, from_work: int | None = None) -> dict:
    return who.post(f"/api/projects/{pid}/works", {"name": name, "name_auto": False, "agent": "demo", **({"from_work_id": from_work} if from_work else {})})


def task_dir(pc: "Pc", pid: int, seq: int) -> Path:
    return next(Path(pc.ws).glob(f"P{pid:04d}_*/T{seq:02d}_*"))


def in_task(who: Who, kind: str, pid: int, wid: int) -> list[dict]:
    return who.get(f"/api/projects/{pid}/{kind}?work_id={wid}")


def digest(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def task_snapshot(who: Who, pid: int, wid: int, point: dict | None = None, token: str | None = None) -> tuple:
    """Task 하나의 모든 결과. 한 글자라도 바뀌면 옛 결과가 다친 것이다."""
    studies = in_task(who, "studies", pid, wid)
    return (studies, [cases_of(who, s["id"]) for s in studies], in_task(who, "models", pid, wid), in_task(who, "flows", pid, wid), in_task(who, "artifacts", pid, wid),
            who.post(f"/api/shares/{token}/predict", point) if token else None)


@scenario("E15", "신규 모델, 같은 흐름: 새 Task에서 새 스터디로 진행, 옛 결과 해시 변화 0")
def e15(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E15")
    sid = o.new_chat(pid)
    chat(o, sid, "mock 솔버로 Branin 최적화하고 Simulator 배포해줘")
    old_work = o.get(f"/api/projects/{pid}/works")[0]
    old_flow = in_task(o, "flows", pid, old_work["id"])[0]["id"]
    approve(o, sid, old_flow)
    old = in_task(o, "studies", pid, old_work["id"])[0]["id"]
    old_model = next(m for m in in_task(o, "models", pid, old_work["id"]) if m["deployed"])
    token = o.post(f"/api/models/{old_model['id']}/shares", {})["token"]
    point = {"params": {"x1": 2.0, "x2": 3.0}}
    before = task_snapshot(o, pid, old_work["id"], point, token)
    before_hash = digest(before)

    # 입력창의 규칙: 새 모델을 말하거나 다른 내용의 모델 파일을 놓으면 카드 A다. 고르기 전에는 아무것도 생기지 않는다
    works_before = len(o.get(f"/api/projects/{pid}/works"))
    said = o.post("/api/route-check", {"work_id": old_work["id"], "text": "신규 모델 나왔어. 지난번처럼 다시 돌려 줘"})
    dropped = o.post("/api/route-check", {"work_id": old_work["id"], "text": "", "files": [{"name": "신형.prt", "sha256": "a" * 64}]})
    c(said["card"] == "A" and dropped["card"] == "A" and dropped["file"] == "신형.prt", "카드 A")
    c(o.post("/api/route-check", {"work_id": old_work["id"], "text": "새 모델 학습해 줘"})["card"] is None, "예측 모델 이야기는 묻지 않음")
    c(len(o.get(f"/api/projects/{pid}/works")) == works_before, "카드만으로는 아무것도 생기지 않음")

    # 새 모델은 새 Task에서 한다. 지난 흐름과 설정을 복사해 시작하고 지난 것은 그대로다
    task = new_task(o, pid, "신규 모델", old_work["id"])
    c(task["seq"] == old_work["seq"] + 1 and task["source_label"] == old_work["name"], "새 Task + 어디서 가져왔는지 기록")
    sid2 = task["active_chat_id"]
    ask = chat(o, sid2, "신규 모델 나왔어. 지난번처럼 다시 돌려 줘")
    c("첨부" in ask.reply and not in_task(o, "flows", pid, task["id"]), "새 모델 파일이 없으면 먼저 부탁")
    att = o.post(f"/api/projects/{pid}/attachments", files={"file": ("신형브래킷.dat", b"new model\n")})
    t = chat(o, sid2, "신규 모델 나왔어. 지난번처럼 다시 돌려 줘. x1 -5~12", [att["id"]])
    review = [f for f in in_task(o, "flows", pid, task["id"]) if f["status"] == "review"]
    c(len(review) == 1 and not t.errors, "새 Flow 제안(해석을 새로 돌리므로)")
    detail = " ".join(n.get("detail") or "" for n in review[0]["nodes"])
    c("달라진 점" in detail and "x1" in detail and "신형브래킷" in detail, "달라진 점을 승인 카드에 적음")
    new = in_task(o, "studies", pid, task["id"])[0]["id"]
    c(new != old and counts(o, new) == {}, "승인 전 해석 0")
    done = approve(o, sid2, review[0]["id"])
    c(not done.errors and counts(o, new).get("complete", 0) > 0, "새 스터디 해석 완료")
    c("지난 모델" in done.reply and "신규 모델" in done.reply, "옛 결과와 나란히 보고")
    after = task_snapshot(o, pid, old_work["id"], point, token)
    for label, x, y in zip(("스터디", "케이스", "모델", "흐름", "결과 카드", "공유 링크 예측"), before, after):
        c(x == y, f"옛 {label} 그대로")
    c(digest(after) == before_hash, "옛 결과 해시 변화 0")
    new_st = o.get(f"/api/studies/{new}")
    c(new_st["study"]["variables"][0]["upper"] == 12, "새 스터디 범위 반영")
    new_models = in_task(o, "models", pid, task["id"])
    c(new_models and new_models[0]["version"] == 1 and new_models[0]["deployed"], "새 모델 v1 배포")
    c(next(m for m in in_task(o, "models", pid, old_work["id"]) if m["id"] == old_model["id"])["deployed"], "옛 Simulator도 배포 상태")
    titles = {a["title"] for a in in_task(o, "artifacts", pid, task["id"])}
    c(titles and not (titles & {a["title"] for a in before[4]}) or all(a in before[4] for a in after[4]), "같은 제목이어도 옛 Task의 카드를 덮지 않음")
    c["old_cases"] = len(before[1][0])
    c["new_cases"] = counts(o, new)["complete"]
    c["old_hash_changed"] = 0
    c["new_model_version"] = 1
    c["old_share_pred"] = round(after[5]["mean"]["f"], 3)


@scenario("E17", "새 Task 격리: 탭은 비어 있고, 폴더가 따로 생기고, 옛 Task는 그대로")
def e17(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E17")
    first = o.new_chat(pid)
    chat(o, first, "mock 솔버로 Branin 최적화하고 Simulator 배포해줘")
    t1 = o.get(f"/api/projects/{pid}/works")[0]
    approve(o, first, in_task(o, "flows", pid, t1["id"])[0]["id"])
    before = digest(task_snapshot(o, pid, t1["id"]))
    t2 = new_task(o, pid, "조건 바꾸기")
    for kind in ("flows", "artifacts", "studies", "models"):
        c(in_task(o, kind, pid, t2["id"]) == [], f"새 Task의 {kind}가 비어 있음")
    c(t2["seq"] == t1["seq"] + 1, "번호가 이어짐")
    c(not list(Path(w.owner_pc.ws).glob(f"P{pid:04d}_*/T{t2['seq']:02d}_*")), "보낼 때까지 폴더가 생기지 않음")
    t = chat(o, t2["active_chat_id"], "mock 솔버로 Branin 최적화하고 Simulator 배포해줘")
    review = in_task(o, "flows", pid, t2["id"])
    c(len(review) == 1 and not t.errors, "Flow가 새 Task에 생김")
    d1, d2 = task_dir(w.owner_pc, pid, t1["seq"]), task_dir(w.owner_pc, pid, t2["seq"])
    c(d1 != d2 and (d2 / "work.json").exists(), "폴더가 따로 있음")
    approve(o, t2["active_chat_id"], review[0]["id"])
    new_study = in_task(o, "studies", pid, t2["id"])[0]
    c(counts(o, new_study["id"]).get("complete", 0) > 0, "새 Task에서 해석")
    c(digest(task_snapshot(o, pid, t1["id"])) == before, "옛 Task 해시 변화 0")
    c(not any(a["work_id"] == t1["id"] and a["title"].startswith(f"[{new_study['name']}]") for a in in_task(o, "artifacts", pid, t1["id"]) if a.get("work_id")), "새 결과가 옛 Task에 없음")
    # 옆 Task의 스터디에 쓰면 복제하라고 한다. 세션의 Task는 AI 세션의 값이다
    old_study = in_task(o, "studies", pid, t1["id"])[0]["id"]
    next_door = o.c.post(f"/api/studies/{old_study}/train", json={}, headers={"X-DDS-Work": str(t2["id"])})
    c(next_door.status_code == 403 and "clone_study" in next_door.json()["detail"], "옆 Task에 쓰면 복제를 안내")
    c["t1_cases"] = counts(o, in_task(o, "studies", pid, t1["id"])[0]["id"])["complete"]
    c["t2_cases"] = counts(o, new_study["id"])["complete"]
    c["t1_hash_changed"] = 0


MODEL_PY = """import glob, json
p = json.load(open("params.json"))
factor = float(open(glob.glob("*.dat")[0]).read())  # 입력 모델이 다르면 답이 달라진다. 어느 모델로 돌았는지 값으로 안다
json.dump({"y": factor * (p["x"] ** 2 + p["z"] + 1)}, open("results.json", "w"))
"""


@scenario("E15b", "신규 모델이 실제로 해석에 쓰인다: 옛 모델과 값이 달라짐")
def e15b(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("E15b")
    sid0 = o.new_chat(pid)
    chat(o, sid0, "이 프로젝트에 스터디 있어?")
    folder = Path(w.owner_pc.mcp("dds-cae", "project_folder", {}, pid)["folder"])
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "inputs").mkdir(parents=True, exist_ok=True)
    (folder / "scripts" / "run.py").write_text(MODEL_PY, encoding="utf-8")
    (folder / "inputs" / "old_model.dat").write_text("1.0", encoding="utf-8")
    flow = o.post(f"/api/projects/{pid}/flows", {"title": "모델 해석", "goal": "", "nodes": [{"id": "doe", "label": "DOE", "type": "doe"}], "edges": []})
    o.c.put(f"/api/flows/{flow['id']}/status", json={"status": "approved", "version": flow["version"]}).raise_for_status()
    variables = [{"name": "x", "lower": 0, "upper": 2}, {"name": "z", "lower": 0, "upper": 2}]
    old = w.owner_pc.mcp("dds-study", "create_study", {"name": "모델 스터디", "variables": variables, "adapter": "script", "objectives": [{"name": "y", "goal": "min"}],
                                                       "adapter_config": {"tool": "python", "script": "scripts/run.py", "inputs": ["inputs/old_model.dat"]}}, pid)["study_id"]
    w.owner_pc.mcp("dds-study", "generate_doe", {"study_id": old, "n": 4, "seed": 3, "flow_id": flow["id"]}, pid)
    first = w.owner_pc.mcp("dds-study", "run_cases", {"study_id": old, "wait_sec": 120, "flow_id": flow["id"]}, pid)
    c(first["counts"] == {"complete": 4}, f"옛 모델로 4건 {first['counts']}")
    before = cases_of(o, old)
    c(all(abs(x["results"]["y"] - (x["params"]["x"] ** 2 + x["params"]["z"] + 1)) < 1e-9 for x in before), "옛 모델 값(배수 1)")
    att = o.post(f"/api/projects/{pid}/attachments", files={"file": ("new_model.dat", b"100.0")})
    sid = o.new_chat(pid)
    t = chat(o, sid, "신규 모델 나왔어. 지난번처럼 다시 돌려 줘", [att["id"]])
    review = [f for f in flows(o, pid) if f["status"] == "review"]
    c(len(review) == 1 and not t.errors, "새 Flow 제안")
    new = next(x["id"] for x in o.get(f"/api/projects/{pid}/studies") if x["id"] != old)
    cfg = o.get(f"/api/studies/{new}")["study"]["adapter_config"]
    c(cfg["inputs"] == ["inputs/new_model.dat"], f"새 스터디가 새 모델을 가리킴 {cfg['inputs']}")
    done = approve(o, sid, review[0]["id"])
    cs = cases_of(o, new)
    c(not done.errors and cs and all(x["status"] == "complete" for x in cs), f"새 모델로 해석 {[x['status'] for x in cs][:3]}")
    ratio = [x["results"]["y"] / (x["params"]["x"] ** 2 + x["params"]["z"] + 1) for x in cs]
    c(all(abs(r - 100.0) < 1e-6 for r in ratio), f"해석 값이 새 모델에서 나옴(배수 100) {ratio[:2]}")
    c(cases_of(o, old) == before, "옛 케이스 그대로")
    c(o.get(f"/api/studies/{old}")["study"]["adapter_config"]["inputs"] == ["inputs/old_model.dat"], "옛 스터디 설정 그대로")
    c["new_cases"] = len(cs)
    c["model_factor"] = round(ratio[0], 3)


SLOW_PY = """import json, time
p = json.load(open("params.json"))
time.sleep(float(p.get("slow", 20)))
json.dump({"y": p["x"] ** 2 + p["z"]}, open("results.json", "w"))
"""


@scenario("E16b", "실행 중에 내 PC가 꺼짐: 멤버는 멈추지도 돌리지도 못하고 실행한 사람이 정리, 다시 돌리기 실패에도 값 유지", pc=False)
def e16b(w: World, tmp: Path, c: Check):
    o, m = w.owner, w.member
    w.owner_pc.start()
    pid = o.project("E16b")
    o.c.put(f"/api/projects/{pid}/members/{m.id}", json={"role": "member"}).raise_for_status()
    chat(o, o.new_chat(pid), "이 프로젝트에 스터디 있어?")
    folder = Path(w.owner_pc.mcp("dds-cae", "project_folder", {}, pid)["folder"])
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "scripts" / "fast.py").write_text(RUN_PY, encoding="utf-8")
    (folder / "scripts" / "slow.py").write_text(SLOW_PY, encoding="utf-8")
    variables = [{"name": "x", "lower": 0, "upper": 2}, {"name": "z", "lower": 0, "upper": 2}]
    fast = w.owner_pc.mcp("dds-study", "create_study", {"name": "빠른 스터디", "variables": variables, "adapter": "script", "objectives": [{"name": "y", "goal": "min"}],
                                                        "adapter_config": {"tool": "python", "script": "scripts/fast.py"}}, pid)["study_id"]
    w.owner_pc.mcp("dds-study", "generate_doe", {"study_id": fast, "n": 5, "seed": 6}, pid)
    w.owner_pc.mcp("dds-study", "run_cases", {"study_id": fast, "wait_sec": 120}, pid)
    rec = o.get(f"/api/studies/{fast}/recipe")
    c(rec["has"] and rec["created_by_name"] == OWNER_NAME and [f["path"] for f in rec["files"]] == ["scripts/fast.py"], "해석이 끝나자 스크립트가 서버에 자동으로 올라감(도구 호출 없이)")
    solved = cases_of(o, fast)
    slow = w.owner_pc.mcp("dds-study", "create_study", {"name": "느린 스터디", "variables": variables, "adapter": "script", "objectives": [{"name": "y", "goal": "min"}],
                                                        "adapter_config": {"tool": "python", "script": "scripts/slow.py"}}, pid)["study_id"]
    w.owner_pc.mcp("dds-study", "generate_doe", {"study_id": slow, "n": 3, "seed": 7}, pid)
    w.owner_pc.mcp("dds-study", "run_cases", {"study_id": slow, "wait_sec": 1}, pid)
    wait_for(lambda: counts(o, slow).get("running"), timeout=30, what="해석 시작")
    w.owner_pc.stop()  # 해석 도중 내 PC가 꺼졌다
    wait_for(lambda: m.get(f"/api/studies/{slow}")["stalled"] == 3, timeout=30, what="꺼진 PC 표시")
    st = m.get(f"/api/studies/{slow}")
    c(st["stalled_by"] == [OWNER_NAME] and not st["running"], "실행 중으로 남은 이유를 알 수 있음")
    for path in ("run", "stop"):
        denied = m.c.post(f"/api/studies/{slow}/{path}", json={})
        c(denied.status_code == 403 and "내 PC로 복사해서 하기" in denied.json()["detail"] and "→" in denied.json()["detail"], f"멤버의 {path}: 막고 할 일을 말함")
    run = o.post(f"/api/studies/{slow}/run", {})
    c(run["queued"] == 0 and "꺼져" in run["note"] and "→" in run["note"], "주인이 다시 돌려도 이유와 할 일을 들음")
    stop = o.post(f"/api/studies/{slow}/stop", {})
    c(stop["released"] == 3, "실행한 사람은 PC 없이 정리")
    c(counts(m, slow) == {"cancelled": 3} and not m.get(f"/api/studies/{slow}")["stalled"], "멤버 화면에도 정리됨")
    # 내 PC가 다시 켜지면 그 PC가 끝낸 것으로 친 케이스를 되살리지 않는다
    w.owner_pc.start()
    time.sleep(3)
    c(counts(o, slow) == {"cancelled": 3}, f"다시 켜도 정리된 상태 유지 {counts(o, slow)}")
    # 끝난 케이스를 다시 돌리다 실패해도 값은 그대로다: 스크립트를 지운 채 다시 돌린다
    (folder / "scripts" / "fast.py").unlink()
    again = o.post(f"/api/studies/{fast}/run", {"case_ids": [solved[0]["id"]]})
    c(again["queued"] == 1, "주인은 승인 없이 자기 스크립트를 다시 돌림")
    wait_for(lambda: next(x for x in cases_of(o, fast) if x["id"] == solved[0]["id"])["status"] != "queued" and not o.get(f"/api/studies/{fast}")["running"], timeout=60, what="다시 돌리기 끝")
    kept = next(x for x in cases_of(o, fast) if x["id"] == solved[0]["id"])
    c(kept["status"] == "complete" and kept["results"] == solved[0]["results"] and "이전 결과" in (kept["error"] or ""), f"스크립트가 없어 실패해도 값을 지킴: {kept['status']}")
    c(sum(x["status"] == "complete" for x in cases_of(o, fast)) == 5, "학습에 쓸 값 5건 그대로")
    w.owner_pc.stop()
    c["released"] = stop["released"]
    c["kept_results"] = kept["results"]


RUN_PY = """import json
p = json.load(open("params.json"))
json.dump({"y": p["x"] ** 2 + p["z"], "z2": p["z"] * 2}, open("results.json", "w"))
"""
EXTRACT_PY = """import json
p = json.load(open("params.json"))
json.dump({"extra_y": p["x"] + p["z"]}, open("results.json", "w"))
"""


def owner_scripts_study(w: World, c: Check, title: str) -> dict:
    """내 PC에서 스크립트 스터디를 돌리고 배포해 둔 Project. 실제 AI가 도구를 부르는 그대로 MCP stdio로 한다."""
    o, m = w.owner, w.member
    w.owner_pc.start()
    pid = o.project(title)
    o.c.put(f"/api/projects/{pid}/members/{m.id}", json={"role": "member"}).raise_for_status()
    first = o.new_chat(pid)
    chat(o, first, "이 프로젝트에 스터디 있어?")  # AI 세션이 Task 폴더를 정한다. 실제 AI의 도구 호출도 세션 안에서 일어난다
    work = o.get(f"/api/projects/{pid}/works")[0]
    folder = Path(w.owner_pc.mcp("dds-cae", "project_folder", {}, pid)["folder"])
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "scripts" / "run.py").write_text(RUN_PY, encoding="utf-8")
    (folder / "scripts" / "extract.py").write_text(EXTRACT_PY, encoding="utf-8")
    variables = [{"name": "x", "lower": 0, "upper": 2}, {"name": "z", "lower": 0, "upper": 2}]
    sid = w.owner_pc.mcp("dds-study", "create_study", {"name": "스크립트 스터디", "variables": variables, "adapter": "script", "adapter_config": {"tool": "python", "script": "scripts/run.py"},
                                                       "objectives": [{"name": "y", "goal": "min"}]}, pid)["study_id"]
    w.owner_pc.mcp("dds-study", "generate_doe", {"study_id": sid, "n": 6, "seed": 4}, pid)
    done = w.owner_pc.mcp("dds-study", "run_cases", {"study_id": sid, "wait_sec": 120}, pid)
    c(done["counts"] == {"complete": 6}, f"내 PC에서 해석 6건 ({done['counts']}: {[x['error'] for x in cases_of(o, sid)][:1]})")
    auto = o.get(f"/api/studies/{sid}/recipe")
    c(auto["has"] and auto["created_by_name"] == OWNER_NAME and any(f["path"] == "scripts/run.py" for f in auto["files"]), "AI가 올리지 않아도 해석이 끝나면 스크립트가 서버에 있음")
    v1 = w.owner_pc.mcp("dds-study", "train_surrogate", {"study_id": sid}, pid)
    w.owner_pc.mcp("dds-study", "deploy_model", {"model_id": v1["model_id"]}, pid)
    saved = w.owner_pc.mcp("dds-study", "save_recipe", {"study_id": sid, "files": ["scripts/extract.py"], "note": "기본 해석"}, pid)
    c(saved["created_by_name"] == OWNER_NAME and set(saved["files"]) == {"scripts/run.py", "scripts/extract.py"}, "묶음을 서버에 올림")
    return {"pid": pid, "sid": sid, "wid": work["id"], "seq": work["seq"], "model": v1["model_id"], "chat": first, "variables": variables, "folder": folder}


@scenario("E16", "다른 멤버는 내 PC가 꺼져도 보고·내려받고·예측한다. 재학습·배포·실행은 막고, 복사로 안내", pc=False)
def e16(w: World, tmp: Path, c: Check):
    o, m = w.owner, w.member
    s = owner_scripts_study(w, c, "E16")
    pid, sid, model = s["pid"], s["sid"], s["model"]
    w.owner_pc.stop()
    c(not o.get("/api/agent/status")["connected"], "내 PC 꺼짐")
    before = digest(task_snapshot(o, pid, s["wid"]))

    # 멤버: PC Agent도 없이 서버에 있는 것을 본다
    c(not m.get("/api/agent/status").get("connected"), "멤버 PC도 아직 없음")
    cs = cases_of(m, sid)
    c(len(cs) == 6 and all(x["status"] == "complete" for x in cs), "결과 조회")
    c(m.c.get(f"/api/studies/{sid}/recipe/file").status_code == 200, "스크립트 내려받기")
    c(m.c.get(f"/api/projects/{pid}/report?format=html&work_id={s['wid']}").status_code == 200, "보고서 내려받기")
    pred = m.post(f"/api/models/{model}/predict", {"params": {"x": 1.0, "z": 1.0}})["mean"]["y"]
    c(abs(pred - 2.0) < 1.0, f"Simulator 예측 {pred}")
    # 실행·재학습·배포·공유·게시·보고서는 서버가 막고 할 일을 말한다
    for method, path, body in (("POST", f"/api/studies/{sid}/train/start", {"force": True}), ("POST", f"/api/models/{model}/deploy", {}), ("POST", f"/api/models/{model}/shares", {}),
                               ("POST", f"/api/studies/{sid}/run", {}), ("POST", f"/api/studies/{sid}/doe", {"n": 2}),
                               ("POST", f"/api/projects/{pid}/artifacts?work_id={s['wid']}", {"kind": "markdown", "title": "x", "payload": {"text": "x"}}),
                               ("POST", f"/api/projects/{pid}/reports?work_id={s['wid']}", {"title": "x", "body": "## x"})):
        r = m.c.request(method, path, json=body)
        c(r.status_code == 403 and "내 PC로 복사해서 하기" in r.json()["detail"] and "→" in r.json()["detail"], f"{path}: {r.status_code}")
    r = m.c.post(f"/api/studies/{sid}/outputs", json={"outputs": [{"name": "extra_y"}], "post": {"tool": "python", "script": "scripts/extract.py"}})
    c(r.status_code == 403 and f"{OWNER_NAME}님만 추가할 수 있습니다" in r.json()["detail"] and "→" in r.json()["detail"], "Output은 파일이 있는 사람만")
    token = o.post(f"/api/models/{model}/shares", {})["token"]
    c(w.outsider.post(f"/api/shares/{token}/predict", {"params": {"x": 1.0, "z": 1.0}})["mean"]["y"] == o.post(f"/api/models/{model}/predict", {"params": {"x": 1.0, "z": 1.0}})["mean"]["y"], "링크로 받은 사람도 같은 값으로 예측")
    c(w.outsider.c.get(f"/api/studies/{sid}/recipe").status_code == 403 and w.outsider.c.get(f"/api/studies/{sid}").status_code == 403, "링크로 받은 사람은 예측만")

    # 멤버 PC가 켜졌다. 멤버의 AI는 내 Task에서 일하고, 남의 스터디에 쓰지 않는다
    w.member_pc.start()
    mid = m.new_chat(pid)
    mine = next(x for x in m.get(f"/api/projects/{pid}/works") if x["mine"])
    c(mine["id"] != s["wid"] and mine["owner_id"] == m.id, "멤버의 요청은 멤버의 새 Task로 간다")
    t = chat(m, mid, "스크립트 스터디 이어받아서 4점 더 해석해 줘")
    c("내 PC로 복사해서 하기" in t.reply and "→" in t.reply, f"AI가 할 일을 안내 {t.reply[:60]}")
    c("403" not in t.reply and "서버 오류" not in t.reply and "clone_study" not in t.reply and not t.errors, "원문 오류를 보이지 않음")
    c(not in_task(m, "flows", pid, mine["id"]), "남의 스터디로 Flow를 올리지 않음")
    t2 = chat(m, mid, "스크립트 스터디 이어받아서 Output extra_y 추가해 줘 scripts/extract.py")
    c("내 PC로 복사해서 하기" in t2.reply and "403" not in t2.reply and not any("extra_y" in (x["results"] or {}) for x in cases_of(m, sid)), "Output도 같은 안내, 남의 PC에서 돌지 않음")
    c(digest(task_snapshot(o, pid, s["wid"])) == before, "원본 해시 변화 0")

    # 주인이 직접 하면 된다: 파일이 있는 내 PC에서 6건을 뽑는다
    w.owner_pc.start()
    t3 = chat(o, o.new_chat(pid), "스크립트 스터디 이어받아서 Output extra_y 추가해 줘 scripts/extract.py")
    wait_for(lambda: not o.get(f"/api/studies/{sid}")["extracting"], timeout=60, what="추출 끝")
    got = [x for x in cases_of(o, sid) if "extra_y" in (x["results"] or {})]
    c(not t3.errors and len(got) == 6 and all(x["run_by"] == o.id for x in got), f"주인 PC의 6건 뽑힘 {len(got)}")
    w.owner_pc.stop()
    w.member_pc.stop()
    c["member_pred_y_at_1_1"] = round(pred, 3)
    c["reply"] = t.reply[:120]
    c["source_hash_changed"] = 0


@scenario("E22", "내 PC로 복사해서 하기(주인 PC 꺼짐): 스크립트·입력·숫자 결과 복사, 다시 돌리지 않고 재학습, 원본 그대로", pc=False)
def e22(w: World, tmp: Path, c: Check):
    o, m = w.owner, w.member
    s = owner_scripts_study(w, c, "E22")
    pid, sid = s["pid"], s["sid"]
    # 입력 모델이 필요한 스터디와 그 첨부. 첨부는 보낼 때 Task에 묶인다
    need = w.owner_pc.mcp("dds-study", "create_study", {"name": "입력 필요", "variables": s["variables"], "adapter": "script",
                                                        "adapter_config": {"tool": "python", "script": "scripts/run.py", "inputs": ["inputs/model.dat"]}, "objectives": [{"name": "y", "goal": "min"}]}, pid)["study_id"]
    w.owner_pc.mcp("dds-study", "save_recipe", {"study_id": need}, pid)
    att = o.post(f"/api/projects/{pid}/attachments", files={"file": ("model.dat", b"input model\n")})
    chat(o, s["chat"], "이 첨부 확인해 줘. 스터디 있어?", [att["id"]])
    # 승인 대기 중인 원본도 복사할 수 있다. 원본의 카드는 그대로 남는다
    waiting = o.post(f"/api/projects/{pid}/flows?work_id={s['wid']}", {"title": "주인의 다음 일", "goal": "", "nodes": [{"id": "n", "label": "n", "type": "doe"}], "edges": []})
    w.owner_pc.stop()
    before = digest(task_snapshot(o, pid, s["wid"]))

    # 카드에 알릴 것: PC Agent 없어도 답한다
    pv = m.get(f"/api/works/{s['wid']}/takeover-preview")
    c(pv["n_copied"] == 6 and pv["has_recipe"] and pv["owner_name"] == OWNER_NAME and pv["script_author"] == OWNER_NAME and pv["n_raw"] == 0, f"복사 전에 알림(이 스크립트는 원본 파일을 남기지 않는다) {pv}")
    # 멤버 PC가 꺼져 있어도 복사는 된다. 폴더는 AI를 시작할 때 생긴다
    task = m.post(f"/api/works/{s['wid']}/takeover", {"agent": "demo"})
    c(task["mine"] and task["owner_id"] == m.id and task["source_kind"] == "takeover" and task["source_label"] == next(x for x in o.get(f"/api/projects/{pid}/works") if x["id"] == s["wid"])["name"], "내 이름의 새 Task")
    c(digest(task_snapshot(o, pid, s["wid"])) == before, "복사해도 원본 해시 변화 0")
    c(o.get(f"/api/flows/{waiting['id']}")["status"] == "review", "원본의 승인 대기는 그대로")
    studies = {x["name"]: x for x in in_task(m, "studies", pid, task["id"])}
    copied = studies["스크립트 스터디 복사"]
    rows = cases_of(m, copied["id"])
    c(len(rows) == 6 and all(x["status"] == "complete" and x.get("from_case_id") for x in rows), "완료 숫자 결과와 from_case_id")
    # 다시 돌리지 않고 재학습한다. 주인 PC도 내 PC도 없이 서버에서
    job = train_poll(m, copied["id"], {})
    v1 = job["model"]
    c(v1["n_train"] == 6 and v1["version"] == 1, "재해석 없이 재학습")
    m.post(f"/api/models/{v1['id']}/deploy")
    c(m.post(f"/api/models/{v1['id']}/predict", {"params": {"x": 1.0, "z": 1.0}})["mean"]["y"] > 0, "내 Simulator로 예측")

    # 멤버 PC가 켜졌다. 첫 요청 전에 스크립트와 입력이 내 Task 폴더로 온다
    w.member_pc.start()
    sid_m = task["active_chat_id"]
    t = chat(m, sid_m, "스크립트 스터디 복사 이어받아서 4점 더 해석해 줘")
    folder = task_dir(w.member_pc, pid, task["seq"])
    c((folder / "scripts" / "run.py").read_text(encoding="utf-8") == RUN_PY and (folder / "scripts" / "extract.py").exists(), "스크립트가 내 Task 폴더로 복사됨")
    c((folder / "inputs" / "model.dat").read_bytes().replace(b"\r\n", b"\n") == b"input model\n", "입력 첨부가 내 Task 폴더로 복사됨")
    review = [x for x in in_task(m, "flows", pid, task["id"]) if x["status"] == "review"]
    c(len(review) == 1 and not t.errors, f"해석 전에 Flow 제안 {t.errors}")
    card = " ".join(n.get("detail") or "" for n in review[0]["nodes"])
    c(f"{OWNER_NAME}님이 만든 스크립트" in card, "승인 카드에 만든 사람 표시")
    c(f"{OWNER_NAME}님이 만든 스크립트" in t.reply and "돌리지 않았습니다" in t.reply, "대화에도 만든 사람 표시")
    c(len(cases_of(m, copied["id"])) == 6, "승인 전에는 돌리지 않음")
    run = approve(m, sid_m, review[0]["id"])
    c(not run.errors, f"오류 없음 {run.errors}")
    c(run.permissions and OWNER_NAME in run.permissions[0], "실행 권한에도 만든 사람 표시")
    cs = cases_of(m, copied["id"])
    fresh = [x for x in cs if x["run_by"] == m.id]
    c(len(cs) == 10 and len(fresh) == 4 and all(x["status"] == "complete" for x in cs), f"내 PC에서 4건 더 {[x['status'] for x in cs]}")
    c(all(str(w.member_pc.ws) in str((folder / x["run_dir"]).resolve()) for x in fresh), "케이스 폴더가 내 PC에 있음")
    v3 = in_task(m, "models", pid, task["id"])[0]
    c(v3["deployed"] and v3["n_train"] == 10, "10건으로 재학습·배포")
    # 입력이 필요한 스터디도 그대로 돈다. 입력을 다시 부탁하지 않는다
    t5 = chat(m, sid_m, "입력 필요 복사 이어받아서 4점 더 해석해 줘")
    review = [x for x in in_task(m, "flows", pid, task["id"]) if x["status"] == "review"]
    c(len(review) == 1 and "첨부" not in t5.reply and not t5.errors, f"입력을 다시 묻지 않음 {t5.reply[:60]}")
    run5 = approve(m, sid_m, review[0]["id"])
    c(not run5.errors and counts(m, studies["입력 필요 복사"]["id"]).get("complete") == 4, "입력 모델까지 받아 내 PC에서 4건 해석")
    # 원본을 지워도 내 Task는 그대로다
    c(digest(task_snapshot(o, pid, s["wid"])) == before, "끝까지 원본 해시 변화 0")
    w.member_pc.stop()
    c["copied"] = 6
    c["member_cases"] = len(fresh)
    c["card"] = card[:80]
    c["permission"] = run.permissions[0]
    c["source_hash_changed"] = 0


@scenario("E23", "지난 Task처럼 시작: 스크립트·설정 복사, 결과 없음, 원본을 지워도 그대로, 다른 Project면 기밀 안내", pc=False)
def e23(w: World, tmp: Path, c: Check):
    o, m = w.owner, w.member
    s = owner_scripts_study(w, c, "E23")
    pid, sid = s["pid"], s["sid"]
    before = digest(task_snapshot(o, pid, s["wid"]))
    rows = o.get(f"/api/works/sources?project_id={pid}")
    c([x["work_id"] for x in rows] == [s["wid"]] and rows[0]["has_recipe"] and rows[0]["result"], f"목록에 만든 사람·결과 한 줄 {rows}")
    c(w.outsider.get(f"/api/works/sources?project_id={pid}") == [], "멤버가 아니면 목록에 없음")

    # 같은 Project: 내 PC 폴더의 scripts를 복사한다. 묶음에 없는 파일도 온다
    (s["folder"] / "scripts" / "only_on_pc.py").write_text("print('pc')\n", encoding="utf-8")
    t2 = new_task(o, pid, "2차", s["wid"])
    for kind in ("studies", "flows", "artifacts", "models"):
        c(in_task(o, kind, pid, t2["id"]) == [], f"결과를 가져오지 않음: {kind}")
    ask = chat(o, t2["active_chat_id"], "새 모델로 지난번처럼 다시 돌려 줘")
    d2 = task_dir(w.owner_pc, pid, t2["seq"])
    c((d2 / "scripts" / "run.py").read_text(encoding="utf-8") == RUN_PY and (d2 / "scripts" / "only_on_pc.py").exists(), "내 PC 폴더의 스크립트를 복사")
    c("첨부" in ask.reply, f"새 모델 파일을 부탁 {ask.reply[:60]}")
    att = o.post(f"/api/projects/{pid}/attachments", files={"file": ("m9.dat", b"new")})
    t = chat(o, t2["active_chat_id"], "새 모델로 지난번처럼 다시 돌려 줘", [att["id"]])
    review = [x for x in in_task(o, "flows", pid, t2["id"]) if x["status"] == "review"]
    c(len(review) == 1 and not t.errors and "m9" in " ".join(n.get("detail") or "" for n in review[0]["nodes"]), f"새 Task에 Flow 제안 {t.errors}")
    clone = in_task(o, "studies", pid, t2["id"])[0]
    c(clone["adapter_config"]["inputs"] == ["inputs/m9.dat"] and counts(o, clone["id"]) == {}, "설정 복사, 새 모델을 가리킴, 승인 전 해석 0")
    c(digest(task_snapshot(o, pid, s["wid"])) == before, "원본 해시 변화 0")
    # 복사이며 연결이 아니다: 원본 스크립트를 고치고 지워도 내 것은 그대로다
    (s["folder"] / "scripts" / "run.py").write_text("raise SystemExit(1)\n", encoding="utf-8")
    c((d2 / "scripts" / "run.py").read_text(encoding="utf-8") == RUN_PY, "원본을 고쳐도 내 스크립트는 그대로")
    (s["folder"] / "scripts" / "run.py").write_text(RUN_PY, encoding="utf-8")

    # 다른 Project의 멤버가 가져온다. 주인 PC가 꺼져 있어도 서버 묶음으로 된다. 그사이 원본 Task가 지워져도 된다
    w.owner_pc.stop()
    other = m.project("E23 다른 부품")
    c(any(x["work_id"] == s["wid"] and x["other_project"] for x in m.get("/api/works/sources")), "첫 화면 목록에 다른 Project 표시")
    t3 = new_task(m, other, "내 첫 일", s["wid"])
    c(t3["project_id"] == other and t3["source_label"], "다른 Project에 시작점 기록")
    source_name = t3["source_label"]
    r = o.c.delete(f"/api/works/{s['wid']}")
    # 스터디가 있는 Task를 주인이 지워도 멤버의 사본은 남는다
    c(r.status_code == 200, f"원본 삭제 {r.text[:80]}")
    c(m.get(f"/api/works/{t3['id']}")["source_label"] == source_name and m.get(f"/api/works/{t3['id']}")["source_work_id"] is None, "원본이 지워져도 어디서 왔는지 남음")
    w.member_pc.start()
    chat(m, t3["active_chat_id"], "이 Task에 무엇이 있어?")
    d3 = task_dir(w.member_pc, other, t3["seq"])
    c((d3 / "scripts" / "run.py").read_text(encoding="utf-8") == RUN_PY, "원본이 지워져도 서버의 내 몫에서 스크립트를 받음")
    c(not (d3 / "scripts" / "only_on_pc.py").exists(), "남의 PC에만 있던 파일은 없음")
    w.member_pc.stop()
    c["source_hash_changed"] = 0
    c["script_after_source_deleted"] = True


@scenario("PV", "pc_verify chat: --attach·--session·--expect·측정값 저장·현재 대화 Flow만 승인")
def pv(w: World, tmp: Path, c: Check):
    o = w.owner
    pid = o.project("PV")
    other = o.new_chat(pid)
    chat(o, other, "mock 솔버로 Branin 최적화하고 Simulator 배포해줘")  # 다른 대화의 검토 중인 Flow: 승인하면 안 된다
    other_flow = flows(o, pid)[0]["id"]
    a = tmp / "붙여넣은_표_400001.tsv"
    a.write_text(paste_tsv(design(12, seed=21)), encoding="utf-8")
    out = tmp / "pv_out"
    # 승인·허용은 사람 전용이다. pc_verify는 원격 시험을 허용한 동안만 되는 devtest 토큰으로 한다
    made = subprocess.run([sys.executable, "-c", DEVTEST_TOKEN_CODE, str(o.id)], cwd=ROOT, env=w.env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                          stdin=subprocess.DEVNULL, **hidden_kwargs())
    c(made.returncode == 0, f"devtest 토큰 만들기: {made.stderr[-300:]}")
    devtest_token = made.stdout.strip().splitlines()[-1]
    o.c.put("/api/devtest/permission", json={"allow": True}).raise_for_status()
    env = {**w.env, "DDS_VERIFY_CHAT_TOKEN": o.token, "DDS_VERIFY_TOKEN": devtest_token, "DDS_VERIFY_OUT": str(out), "PYTHONUTF8": "1"}

    def run(*args):
        return subprocess.run([sys.executable, str(ROOT / "scripts" / "pc_verify.py"), "--server", w.base, "--timeout", "300", "chat", *args, "--agent", "demo", "--project", str(pid), "--poll", "0.5", "--settle", "6"],
                              cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=400, stdin=subprocess.DEVNULL, **hidden_kwargs())

    def saved() -> dict:
        return json.loads(sorted(out.glob("*/metrics.json"), key=lambda p: p.stat().st_mtime)[-1].read_text(encoding="utf-8"))

    p = run("stress가 결과야. 이 표로 Simulator 만들어 줘", "--attach", str(a), "--expect", "deployed,no_cases_run,flows=0,tool:import_table,no_tool:run_cases,reply~R²,questions=0", "--max-first-reply", "120")
    c(p.returncode == 0, f"합격 판정 통과: {p.stdout[-500:]}{p.stderr[-300:]}")
    c(o.get(f"/api/flows/{other_flow}")["status"] == "review", "다른 대화의 Flow는 승인하지 않음")
    m = saved()
    c(m["passed"] is True and m["first_reply_sec"] is not None and m["session"], "측정값 저장")
    p2 = run("이 프로젝트에 모델 몇 개 있어?", "--session", str(m["session"]), "--expect", "tool:list_models,flows=0")
    c(p2.returncode == 0, f"--session으로 같은 대화 이어 감: {p2.stdout[-300:]}")
    c(saved()["session"] == m["session"], "같은 대화")
    p3 = run("이 프로젝트에 모델 몇 개 있어?", "--session", str(m["session"]), "--expect", "flows=3,tool:import_table")
    c(p3.returncode == 1 and "불합격" in p3.stdout, "기대가 틀리면 종료 코드 1")
    p4 = run("mock 솔버로 Branin 최적화하고 Simulator 배포해줘", "--expect", "flows=1,flows_approved=1,deployed,permissions=0")
    c(p4.returncode == 0, f"현재 대화 Flow는 자동 승인: {p4.stdout[-500:]}")
    c(o.get(f"/api/flows/{other_flow}")["status"] == "review", "여전히 다른 대화 Flow는 그대로")
    p5 = run("mock 솔버로 Branin 최적화하고 Simulator 배포해줘", "--no-approve", "--expect", "flows=1,flows_approved=0,no_cases_run")
    c(p5.returncode == 0, f"--no-approve는 승인하지 않음: {p5.stdout[-300:]}")
    c["first_reply_sec"] = m["first_reply_sec"]
    c["saved"] = sorted(m)[:8]
    c["fail_exit_code"] = p3.returncode


@scenario("UI", "브라우저: 붙여넣기 창·입력창 붙여넣기(AI·Agent 없음)·파일 선택 없음·출력 질문 하나·프로젝트 열기로 같은 스터디 Simulator 탭", pc=False)
def ui(w: World, tmp: Path, c: Check):
    o = w.owner
    c(not o.get("/api/agent/status").get("connected"), "PC Agent 꺼짐")
    env = {**w.env, "DDS_URL": w.base, "DDS_USER": o.username, "DDS_PIN": PIN, "DDS_SHOTS": str(tmp / "shots")}
    (tmp / "shots").mkdir()
    p = subprocess.run(["node", "e2e/smoke.mjs", *os.environ.get("DDS_SMOKE_FLAGS", "").split()], cwd=ROOT / "frontend", env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900, stdin=subprocess.DEVNULL, **hidden_kwargs())
    lines = p.stdout.strip().splitlines()
    c(lines and lines[-1].startswith("PASS"), f"smoke 통과: {p.stdout[-900:]}{p.stderr[-300:]}")
    lay = subprocess.run(["node", "e2e/table_layout.mjs"], cwd=ROOT / "frontend", env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, **hidden_kwargs())
    lay_lines = [ln for ln in lay.stdout.splitlines() if ln.strip()]
    c(lay_lines and lay_lines[-1].startswith("PASS"), f"열 많은 표·긴 이름·막는 문구: {lay.stdout[-900:]}{lay.stderr[-300:]}")
    job = subprocess.run(["node", "e2e/table_job.mjs"], cwd=ROOT / "frontend", env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, **hidden_kwargs())
    job_lines = [ln for ln in job.stdout.splitlines() if ln.strip()]
    c(job_lines and job_lines[-1].startswith("PASS"), f"만드는 중 입력창·새로고침 뒤 알림: {job.stdout[-900:]}{job.stderr[-300:]}")
    shots = sorted(x.stem.split("-", 1)[1] for x in (tmp / "shots").glob("*.png"))
    for want in ("table-question", "table-simulator-tab", "composer-table-simulator", "composer-table-in-project", "simulator-back-to-tab"):
        c(want in shots, f"화면 확인: {want}")
    c(not o.get("/api/agent/status").get("connected"), "끝까지 PC Agent 없음")
    c["screens"] = len(shots)
    kept = w.tmp / "ui_shots"
    shutil.copytree(tmp / "shots", kept, dirs_exist_ok=True)
    c["shots"] = str(kept)


@scenario("UId", "브라우저 + 실제 Agent·데모 CLI: 전체 과정, 입력창 표를 AI로 보내기, 흐름 다시 입력창 채우기, DOE CSV")
def uid(w: World, tmp: Path, c: Check):
    o = w.owner
    env = {**w.env, "DDS_URL": w.base, "DDS_USER": o.username, "DDS_PIN": PIN, "DDS_SHOTS": str(tmp / "shots")}
    (tmp / "shots").mkdir()
    p = subprocess.run(["node", "e2e/smoke.mjs", "--demo"], cwd=ROOT / "frontend", env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200, stdin=subprocess.DEVNULL, **hidden_kwargs())
    lines = p.stdout.strip().splitlines()
    c(lines and lines[-1].startswith("PASS"), f"smoke --demo 통과: {p.stdout[-900:]}{p.stderr[-300:]}")
    shots = sorted(x.stem.split("-", 1)[1] for x in (tmp / "shots").glob("*.png"))
    c("demo-composer-table" in shots, "입력창의 표를 AI로 보내 Simulator까지")
    c["screens"] = len(shots)


# ---------- 실행 ----------
def short(v) -> str:
    return json.dumps(v, ensure_ascii=False)[:600]


def run_all(world: World, picks: list[Scenario]) -> list[dict]:
    results = []
    for s in picks:
        t0 = time.time()
        check = Check()
        tmp = Path(tempfile.mkdtemp(prefix=f"{s.sid}_", dir=world.tmp))
        row = {"id": s.sid, "title": s.title}
        print(f"--- {s.sid} {s.title}", flush=True)
        try:
            # 시나리오마다 PC Agent를 새로 띄운다. 데모 CLI가 대화마다 MCP 4개를 띄우므로 사용자 PC에 쌓이지 않게 한다
            world.owner_pc.stop()
            world.member_pc.stop()
            if s.pc:
                world.owner_pc.start()
            s.fn(world, tmp, check)
            row.update(ok=True)
            print(f"    통과 ({len(check.passed)}개 확인)", flush=True)
        except Exception as e:
            row.update(ok=False, error=f"{type(e).__name__}: {e}"[:1500], trace=traceback.format_exc()[-1500:])
            print(f"    실패: {row['error']}", flush=True)
            for name in ("server", "owner_pc", "member_pc"):
                p = world.tmp / f"{name}.log"
                if p.exists():
                    print(f"    ----- {name}.log 끝 -----\n" + p.read_text(encoding="utf-8", errors="replace")[-1500:], flush=True)
        row.update(sec=round(time.time() - t0, 1), checks=check.passed, measured=check.m)
        results.append(row)
    return results


def markdown(results: list[dict], extra: dict) -> str:
    lines = ["| ID | 결과 | 걸린 시간 | 확인 수 | 측정값 |", "|---|---|---:|---:|---|"]
    for r in results:
        m = "; ".join(f"{k}={short(v)}" for k, v in r["measured"].items() if k not in ("status", "reasons")).replace("|", "\\|")
        lines.append(f"| {r['id']} | {'통과' if r['ok'] else '실패: ' + r['error'][:200].replace('|', ' ')} | {r['sec']}초 | {len(r['checks'])} | {m} |")
    lines += ["", f"실행: {extra['when']}, Python {sys.version.split()[0]}, 서버 응답 500 {extra['http_500']}건, 서버 Traceback {extra['tracebacks']}건, 전체 {extra['total_sec']}초."]
    return "\n".join(lines)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="진입 시나리오를 실제 프로세스로 돌린다")
    ap.add_argument("--only", default="", help="쉼표로 구분한 시나리오 ID")
    ap.add_argument("--out", help="결과 폴더(기본: 임시 폴더)")
    ap.add_argument("--doc", action="store_true", help="docs/entry-points.md의 결과 표를 바꾼다")
    ap.add_argument("--keep", action="store_true", help="임시 폴더를 지우지 않는다")
    a = ap.parse_args()
    only = {x.strip() for x in a.only.split(",") if x.strip()}
    picks = [s for s in SCENARIOS if not only or s.sid in only]
    # 경로가 길면 스킬 복사가 Windows 260자 한도에 걸린다. 사용자의 작업 폴더가 아니라 시험 폴더 탓으로 실패하지 않게 짧게 잡는다
    base = tempfile.gettempdir()
    tmp = Path(tempfile.mkdtemp(prefix="dds_e_", dir=base if len(base) <= 60 else Path.home()))
    world = World(tmp)
    t0 = time.time()
    results: list[dict] = []
    try:
        world.start()
        print(f"서버 {world.base} · 임시 폴더 {tmp}", flush=True)
        results = run_all(world, picks)
    finally:
        world.stop()
    log = world.server_log()
    extra = {"when": time.strftime("%Y-%m-%d %H:%M"), "http_500": len(re.findall(r'" 500\b', log)), "tracebacks": log.count("Traceback (most recent call last)"), "total_sec": round(time.time() - t0)}
    table = markdown(results, extra)
    print("\n" + table)
    out = Path(a.out) if a.out else tmp / "result"
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"extra": extra, "results": results}, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.copy(tmp / "server.log", out / "server.log")
    print(f"\n저장: {out / 'results.json'}")
    if a.doc:
        text = DOC.read_text(encoding="utf-8")
        DOC.write_text(text[:text.index(START) + len(START)] + "\n" + table + "\n" + text[text.index(END):], encoding="utf-8")
    if not a.keep and not a.out:
        print(f"임시 폴더를 남깁니다(결과 포함): {tmp}")
    elif not a.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    bad = [r["id"] for r in results if not r["ok"]]
    if extra["http_500"]:
        bad.append("HTTP500")
    print(f"실패: {bad}" if bad else "전부 통과")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
