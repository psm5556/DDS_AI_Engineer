"""E2E 검증: 실제 서버 + 실제 PC Agent 두 대 + 데모 AI로 마켓 한 바퀴를 돈다.
  김 엔지니어가 스킬을 올린다 → 나만 쓴다 → 검토를 요청한다 → 관리자가 PIN으로 승인한다
  → 이 대리가 대화 중에 설치한다 → 같은 대화의 다음 요청에서 AI가 스킬을 읽는다 → 관리자가 회수한다
  → 같은 대화의 다음 요청에서 AI가 쓰지 않는다 → 세션 번호가 끝까지 같다.
실제 AI CLI와 CAD는 쓰지 않는다. 진짜 AI가 안내를 보고 스킬을 쓰는지는 이것으로 알 수 없다(현장 시험).

실행: .venv\\Scripts\\python scripts\\e2e_market.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cae_adapters.hidden import hidden_kwargs, kill_tree  # noqa: E402

PIN = "123456"
SKILL = "weld-fatigue-e2e"
SKILL_MD = f"---\nname: {SKILL}\ndescription: 용접부 피로 수명을 평가할 때 쓴다.\n---\n\n# 용접부 피로\n\n응력 범위를 S-N 곡선과 비교한다.\n"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for(cond, timeout=120, interval=0.3, what=""):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(interval)
    raise TimeoutError(f"시간 초과: {what}")


def ok(text: str):
    print(f"[통과] {text}", flush=True)


def check(cond, text: str):
    if not cond:
        raise AssertionError(text)
    ok(text)


class Person:
    def __init__(self, base: str, name: str, tmp: Path, env: dict, admin_client: httpx.Client | None):
        self.name, self.tmp, self.env = name, tmp, env
        self.base = base
        self.c = httpx.Client(base_url=base, timeout=60)
        self.c.post("/api/auth/register", json={"username": name, "pin": PIN, "display_name": name})
        if admin_client is not None:
            uid = next(u["id"] for u in admin_client.get("/api/users").json() if u["username"] == name)
            admin_client.put(f"/api/users/{uid}", json={"is_active": True}).raise_for_status()
        r = self.c.post("/api/auth/login", json={"username": name, "pin": PIN})
        r.raise_for_status()
        self.user = r.json()["user"]
        self.c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
        self.home = tmp / f"pc_{name}"
        self.home.mkdir()
        self.proc: subprocess.Popen | None = None
        self.log = None

    def start_agent(self):
        token = self.c.post("/api/tokens", json={"name": "e2e"}).json()["token"]
        cfg = self.home / "agent.toml"
        cfg.write_text("\n".join([f"server_url = {json.dumps(self.base)}", f"token = {json.dumps(token)}", f"workspace_dir = {json.dumps(str(self.home / 'ws'))}",
                                  'default_agent = "demo"', "tray = false", "warm_up = false", ""]), encoding="utf-8")
        env = {k: v for k, v in self.env.items() if k not in ("DDS_DATA_DIR", "DDS_MARKET_DIR", "DDS_SERVER_URL", "DDS_TOKEN")}
        env.update({"DDS_AGENT_CONFIG": str(cfg), "DDS_WORKSPACE": str(self.home / "ws"), "DDS_DEFAULT_AGENT": "demo"})
        self.log = open(self.home / "agent.log", "w", encoding="utf-8")
        self.proc = subprocess.Popen([sys.executable, "-m", "agent.dds_agent.main"], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT, **hidden_kwargs())
        # 연결 직후에는 hello가 아직 오지 않았을 수 있다
        wait_for(lambda: (self.c.get("/api/agent/status").json().get("info") or {}).get("market_api"), what=f"{self.name} PC Agent 연결(market_api)")

    def stop_agent(self):
        if self.proc:
            kill_tree(self.proc)
            self.proc = None
        if self.log:
            self.log.close()
            self.log = None

    def package(self, pid: int) -> dict:
        return self.c.get(f"/api/market/packages/{pid}").json()

    def wait_state(self, pid: int, state: str, what: str) -> dict:
        return wait_for(lambda: (lambda p: p if p["install"]["state"] == state else None)(self.package(pid)), timeout=60, what=what)


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p, b in files.items():
            z.writestr(p, b)
    return buf.getvalue()


def _tree_mtimes(root: Path, skip: str) -> dict[str, int]:
    out = {}
    for rel in (".claude/skills", ".gemini/skills", ".agents/skills"):
        for f in (root / rel).rglob("*"):
            if f.is_file() and skip not in f.parts:
                out[f.as_posix()] = f.stat().st_mtime_ns
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    tmp = Path(tempfile.mkdtemp(prefix="dds_e2e_market_"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "DDS_DATA_DIR": str(tmp / "server"), "PYTHONIOENCODING": "utf-8", "PYSTRAY_BACKEND": "dummy"}
    env.pop("DDS_SERVER_CONFIG", None)
    server_log = open(tmp / "server.log", "w", encoding="utf-8")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app.main:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env,
                              stdin=subprocess.DEVNULL, stdout=server_log, stderr=subprocess.STDOUT, **hidden_kwargs())
    people: list[Person] = []
    try:
        probe = httpx.Client(base_url=base, timeout=10)

        def up():
            try:
                return probe.get("/api/auth/me").status_code in (200, 401)
            except httpx.HTTPError:
                return False

        wait_for(up, what="서버 시작")
        admin = Person(base, "admin", tmp, env, None)
        kim = Person(base, "kim", tmp, env, admin.c)
        lee = Person(base, "lee", tmp, env, admin.c)
        people += [admin, kim, lee]
        check(admin.user["is_admin"] and not kim.user["is_admin"], "관리자와 일반 사용자 준비")
        kim.start_agent()
        lee.start_agent()
        ok("김·이 PC Agent 두 대 연결")

        # ---------- 빈 마켓 ----------
        chat_pid = lee.c.post("/api/projects", json={"name": "마켓 E2E"}).json()["id"]
        chat = lee.c.post(f"/api/projects/{chat_pid}/sessions", json={"agent": "demo"}).json()["id"]
        lee.c.post(f"/api/sessions/{chat}/prompt", json={"text": "Branin 최적화 흐름을 제안해 줘"}).raise_for_status()
        wait_for(lambda: not lee.c.get(f"/api/sessions/{chat}/status").json()["busy"], what="첫 턴")
        sid_first = next(c for c in lee.c.get(f"/api/projects/{chat_pid}/sessions").json() if c["id"] == chat)["agent_session_id"]
        check(sid_first and sid_first.startswith("demo-"), f"이 대리의 대화가 시작됨 (세션 {sid_first})")
        project = next((lee.home / "ws").glob(f"P{chat_pid:04d}_*"))
        check(not (project / ".dds-market.json").exists() and not (lee.home / "market" / "lock.json").exists(), "빈 마켓: 프로젝트에도 PC에도 마켓 파일이 없음")
        rules_before = (project / "CLAUDE.md").read_bytes()
        base_before = _tree_mtimes(project, SKILL)
        check(len(base_before) > 100, f"기본 스킬 복사본 {len(base_before)}개 확인")

        # ---------- 등록 ----------
        r = kim.c.post("/api/market/submissions", files={"file": ("x.zip", _zip({f"{SKILL}/SKILL.md": SKILL_MD}))}, data={"source": "cli"})
        r.raise_for_status()
        sub = r.json()
        pid, ver = sub["package"]["id"], sub["version"]
        vbase = f"/api/market/packages/{pid}/versions/{ver}"
        check(sub["package"]["status"] == "draft", "등록 결과는 초안")
        check(lee.c.get(f"{vbase}/download").status_code == 404, "초안은 다른 사람이 받지 못함")

        swap = kim.c.post("/api/market/submissions", files={"file": ("x.zip", _zip({f"{SKILL}/SKILL.md": SKILL_MD, f"{SKILL}/r.md": "바꿔치기"}))}, data={"source": "cli"})
        swap.raise_for_status()
        check(kim.c.post(f"{vbase}/private", json={"fingerprint": sub["fingerprint"]}).status_code == 409, "사람이 본 뒤 초안이 바뀌면 결정이 적용되지 않음")
        r = kim.c.post("/api/market/submissions", files={"file": ("x.zip", _zip({f"{SKILL}/SKILL.md": SKILL_MD}))}, data={"source": "cli"})
        r.raise_for_status()
        sub = r.json()
        r = kim.c.post(f"{vbase}/private", json={"meta": {"display_name": "용접부 피로 평가", "examples": ["용접부 피로 수명을 계산해 줘"]}, "fingerprint": sub["fingerprint"]})
        r.raise_for_status()
        seen = r.json()["fingerprint"]
        kim.wait_state(pid, "installed", "김의 PC에 설치")
        kim_lock = json.loads((kim.home / "market" / "lock.json").read_text(encoding="utf-8"))
        key = kim_lock["items"][0]["key"]
        check(key == f"u{kim.user['id']}.{SKILL}", f"나만 쓰기: 김의 PC에 바로 설치됨 ({key})")
        check(lee.c.get(f"{vbase}/download").status_code == 404, "나만 쓰기는 다른 사람이 받지 못함")

        # ---------- 검토·승인 ----------
        kim.c.post(f"{vbase}/request-review", json={"fingerprint": seen}).raise_for_status()
        queue = admin.c.get("/api/market/review").json()["queue"]
        check(any(q["package_id"] == pid for q in queue), "검토 대기열에 올라옴")
        check(admin.c.get("/api/market/notices").json()["review_waiting"] >= 1, "관리부서에게 검토 대기 수가 보임")
        token = kim.c.post("/api/tokens", json={"name": "ai"}).json()["token"]
        check(httpx.post(f"{base}{vbase}/approve", json={"pin": PIN}, headers={"Authorization": f"Bearer {token}"}).status_code == 403, "토큰(AI)으로는 승인하지 못함")
        check(admin.c.post(f"{vbase}/approve", json={"pin": "000000"}).status_code == 403, "PIN이 틀리면 승인하지 못함")

        # 반려돼도 만든 사람은 계속 쓴다
        admin.c.post(f"{vbase}/reject", json={"pin": PIN, "note": "설명이 부족합니다"}).raise_for_status()
        time.sleep(1.5)
        check((kim.home / "market" / "items" / f"u{kim.user['id']}.{SKILL}").exists() and kim.package(pid)["status"] == "rejected", "반려돼도 김의 PC에서 지워지지 않음")
        check(any("설명이 부족합니다" in n["text"] for n in kim.c.get("/api/market/notices").json()["items"]), "반려 사유가 김에게 알림으로 감")
        r = kim.c.post(f"{vbase}/request-review", json={"fingerprint": seen})
        r.raise_for_status()

        # 로그인 없는 사람이 틀린 PIN을 보내도 관리부서의 결정을 막지 못한다
        for _ in range(5):
            httpx.post(f"{base}/api/auth/login", json={"username": "admin", "pin": "000000"})
        admin.c.post(f"{vbase}/approve", json={"pin": PIN, "official": True, "fingerprint": seen}).raise_for_status()
        ok("공개 로그인이 잠겨도 관리자의 승인은 지나감")
        pkg = lee.package(pid)
        check(pkg["grade"] == "official" and pkg["install"]["state"] == "none", "승인: 다른 사용자에게 보임(관리부서 제공), 아직 설치 안 됨")
        kim.wait_state(pid, "installed", "공개 뒤 김의 PC")
        wait_for(lambda: json.loads((kim.home / "market" / "lock.json").read_text(encoding="utf-8"))["items"][0]["key"] == SKILL, what="공개 이름으로 바뀜")
        ok("김의 PC도 공개 이름으로 옮겨 감")

        # ---------- 대화 중 설치 ----------
        check(httpx.put(f"{base}/api/market/installs/{pid}", json={}, headers={"Authorization": f"Bearer {token}"}).status_code == 403, "토큰(AI)으로는 설치하지 못함")
        lee.c.put(f"/api/market/installs/{pid}", json={}).raise_for_status()
        lee.wait_state(pid, "installed", "이의 PC에 설치")
        store = lee.home / "market" / "items" / SKILL / ver
        zip_sha = kim.c.get(f"{vbase}/download").headers["x-market-sha256"]
        check((store / ".installed").read_text(encoding="utf-8") == zip_sha, "이 대리의 PC에 설치: sha256 일치")
        check((store / "skills" / SKILL / "SKILL.md").exists() and not (project / ".claude" / "skills" / SKILL).exists(), "받아 두었고, 프로젝트 복사는 다음 요청 때 한다")

        before = len(lee.c.get(f"/api/sessions/{chat}/messages").json())
        lee.c.post(f"/api/sessions/{chat}/prompt", json={"text": "계속 진행해 줘"}).raise_for_status()
        wait_for(lambda: not lee.c.get(f"/api/sessions/{chat}/status").json()["busy"], what="설치 뒤 턴")
        msgs = lee.c.get(f"/api/sessions/{chat}/messages").json()
        new = msgs[before:]
        text = "\n".join(m["content"] for m in new)
        check(f"설치된 스킬을 읽었습니다: {SKILL}" in text, "같은 대화의 다음 요청에서 데모 AI가 안내만 보고 스킬을 읽음")
        check(not any("[마켓 안내]" in m["content"] for m in msgs), "안내는 프롬프트에만 있고 대화 기록에는 없음")
        for rel in (".claude/skills", ".gemini/skills", ".agents/skills"):
            check((project / rel / SKILL / "SKILL.md").exists(), f"프로젝트 {rel}에 복사됨")
        check(_tree_mtimes(project, SKILL) == base_before, "기본 스킬 복사본의 수정 시각이 그대로(전체 재복사 없음)")
        sid = next(c for c in lee.c.get(f"/api/projects/{chat_pid}/sessions").json() if c["id"] == chat)["agent_session_id"]
        check(sid == sid_first, "설치 뒤에도 세션 번호가 같음(CLI 재시작 없음)")
        ctx = lee.c.get(f"/api/projects/{chat_pid}/report-context").json()
        check(ctx.get("market_items_read") == [{"name": SKILL, "display_name": "용접부 피로 평가", "version": ver}], "읽음 기록이 보고서 자료에 실림")

        before = len(msgs)
        lee.c.post(f"/api/sessions/{chat}/prompt", json={"text": "계속 진행해 줘"}).raise_for_status()
        wait_for(lambda: not lee.c.get(f"/api/sessions/{chat}/status").json()["busy"], what="셋째 턴")
        msgs = lee.c.get(f"/api/sessions/{chat}/messages").json()
        check("설치된 스킬을 읽었습니다" not in "\n".join(m["content"] for m in msgs[before:]), "안내는 한 번만")

        # ---------- 회수 ----------
        admin.c.post(f"{vbase}/withdraw", json={"pin": PIN, "reason": "S-N 곡선 계수 오류"}).raise_for_status()
        wait_for(lambda: not (project / ".claude" / "skills" / SKILL).exists(), timeout=30, what="모든 프로젝트에서 삭제")
        ok("회수: 대화를 기다리지 않고 프로젝트 복사본 삭제")
        wait_for(lambda: not store.exists(), timeout=30, what="보관소에서 삭제")
        ok("회수: PC 보관소에서도 삭제")
        coverage = wait_for(lambda: (lambda c: c if c["applied"] >= 2 else None)(admin.c.get(f"/api/market/packages/{pid}/coverage").json()), timeout=30, what="회수 현황")
        check(coverage["pending"] == 0, f"회수 현황: PC별 적용 {coverage['applied']}대")
        pkg = lee.package(pid)
        check(pkg["install"]["state"] == "recalled" and pkg["recalled"]["reason"] == "S-N 곡선 계수 오류", "이 대리의 카드에 회수 사유가 보임")
        check(any("S-N 곡선 계수 오류" in n["text"] for n in lee.c.get("/api/market/notices").json()["items"]), "이 대리에게 회수 알림이 감")
        check(lee.c.get(f"{vbase}/download").status_code == 403, "회수한 버전은 받지 못함")
        rules_after = (project / "CLAUDE.md").read_bytes()
        check(rules_after == rules_before, "회수 뒤 규칙 파일이 설치 전과 바이트까지 같음")

        before = len(msgs)
        lee.c.post(f"/api/sessions/{chat}/prompt", json={"text": "계속 진행해 줘"}).raise_for_status()
        wait_for(lambda: not lee.c.get(f"/api/sessions/{chat}/status").json()["busy"], what="회수 뒤 턴")
        msgs = lee.c.get(f"/api/sessions/{chat}/messages").json()
        text = "\n".join(m["content"] for m in msgs[before:])
        check(f"회수된 스킬은 쓰지 않겠습니다: {SKILL}" in text, "같은 대화의 다음 요청에 회수 안내가 닿음")
        notes = [m["content"] for m in msgs[before:] if m["role"] == "system"]
        check(any("S-N 곡선 계수 오류" in n for n in notes), "대화에 회수 사유가 사람에게 보이게 남음")
        agent_text = "\n".join(m["content"] for m in msgs[before:] if m["role"] != "system")
        check("S-N 곡선" not in agent_text, "회수 안내에 사유 글을 AI에게 넣지 않음")
        ctx = lee.c.get(f"/api/projects/{chat_pid}/report-context").json()
        check(ctx["market_items_read"][0].get("recalled") is True, "보고서 자료에 회수된 항목이라고 표시됨")
        sid = next(c for c in lee.c.get(f"/api/projects/{chat_pid}/sessions").json() if c["id"] == chat)["agent_session_id"]
        check(sid == sid_first, f"세션 번호가 끝까지 같음 ({sid})")

        # ---------- 꺼진 PC ----------
        r = kim.c.post("/api/market/submissions", files={"file": ("x.zip", _zip({"later-skill-e2e/SKILL.md": SKILL_MD.replace(SKILL, "later-skill-e2e")}))})
        r.raise_for_status()
        later = r.json()["package"]["id"]
        admin_base = f"/api/market/packages/{later}/versions/1.0.0"
        kim.c.post(f"{admin_base}/request-review", json={"fingerprint": r.json()["fingerprint"]}).raise_for_status()
        admin.c.post(f"{admin_base}/approve", json={"pin": PIN, "fingerprint": r.json()["fingerprint"]}).raise_for_status()
        lee.stop_agent()
        wait_for(lambda: not lee.c.get("/api/agent/status").json()["connected"], what="이의 Agent 끊김")
        lee.c.put(f"/api/market/installs/{later}", json={}).raise_for_status()
        check(lee.package(later)["install"]["state"] == "reserved", "PC가 꺼져 있으면 설치 예약")
        lee.start_agent()
        lee.wait_state(later, "installed", "켜지면 설치")
        ok("PC를 켜자 예약한 항목이 설치됨")
        check(not (lee.home / "market" / "items" / SKILL).exists(), "다시 연결해도 회수한 항목이 돌아오지 않음")

        # 프로젝트에 같은 이름의 내 스킬이 있으면 덮어쓰지 않고, 그 대화와 카드에 어느 프로젝트인지 알린다
        mine_dir = project / ".claude" / "skills" / "later-skill-e2e"
        mine_dir.mkdir(parents=True)
        (mine_dir / "SKILL.md").write_text("내 스킬", encoding="utf-8")
        before = len(lee.c.get(f"/api/sessions/{chat}/messages").json())
        lee.c.post(f"/api/sessions/{chat}/prompt", json={"text": "계속 진행해 줘"}).raise_for_status()
        wait_for(lambda: not lee.c.get(f"/api/sessions/{chat}/status").json()["busy"], what="겹침 턴")
        check((mine_dir / "SKILL.md").read_text(encoding="utf-8") == "내 스킬", "내 스킬을 덮어쓰지 않음")
        pkg = lee.wait_state(later, "conflict", "겹침이 카드에 보임")
        where = lee.c.get(f"/api/projects/{chat_pid}").json()["name"]  # 데모 AI가 프로젝트 이름을 바꾼다
        check([c["project"] for c in pkg["install"]["conflicts"]] == [where], f"카드에 겹친 프로젝트 이름이 보임 ({where})")
        wait_for(lambda: any("쓰지 못합니다" in m["content"] and m["role"] == "system" for m in lee.c.get(f"/api/sessions/{chat}/messages").json()[before:]), timeout=15, what="겹침 안내")
        ok("겹침이 그 대화에 안내로 남음")
        shutil.rmtree(mine_dir)
        lee.c.post(f"/api/sessions/{chat}/prompt", json={"text": "계속 진행해 줘"}).raise_for_status()
        wait_for(lambda: not lee.c.get(f"/api/sessions/{chat}/status").json()["busy"], what="겹침 푼 턴")
        lee.wait_state(later, "installed", "겹침이 풀림")
        check((mine_dir / "SKILL.md").exists(), "내 스킬을 치우면 그 프로젝트에 설치됨")
        lee.stop_agent()
        wait_for(lambda: not lee.c.get("/api/agent/status").json()["connected"], what="이의 Agent 다시 끊김")
        off = lee.package(later)["install"]
        check(off["state"] == "installed" and off.get("offline") is True, "PC Agent를 꺼도 설치한 것이 '예약'으로 되돌아가지 않음")

        # PC의 토큰으로 망가진 상태를 보내도 관리부서의 회수가 멈추지 않는다
        import websockets.sync.client as ws_client  # noqa: PLC0415 — 이 시험에서만 쓴다

        kim_token = kim.c.post("/api/tokens", json={"name": "bad"}).json()["token"]
        with ws_client.connect(f"ws://127.0.0.1:{port}/ws/agent?token={kim_token}") as bad:
            bad.send(json.dumps({"type": "hello", "hostname": "ATTACK-HOST", "version": "0.0.0"}))
            bad.send(json.dumps({"type": "market.state", "items": ["boom"]}))
            time.sleep(1)
        admin.c.post(f"{admin_base}/withdraw", json={"pin": PIN, "reason": "망가진 상태 시험"}).raise_for_status()
        check(admin.c.get(f"/api/market/packages/{later}/coverage").status_code == 200, "망가진 market.state가 있어도 회수와 현황이 됨")

        # 서버 로그에 예상 못 한 오류가 없어야 한다
        server_log.flush()
        log = (tmp / "server.log").read_text(encoding="utf-8", errors="replace")
        check("Traceback" not in log, "서버 로그에 예외 없음")
        for p in (kim, lee):
            text = (p.home / "agent.log").read_text(encoding="utf-8", errors="replace")
            check("마켓 동기화 실패" not in text and "마켓 관문 실패" not in text, f"{p.name} Agent 로그에 마켓 예외 없음")
        print("\n마켓 E2E 통과", flush=True)
        return 0
    except Exception:
        print("\n--- 서버 로그 끝 ---", flush=True)
        server_log.flush()
        print((tmp / "server.log").read_text(encoding="utf-8", errors="replace")[-3000:], flush=True)
        for p in people:
            f = p.home / "agent.log"
            if f.exists():
                print(f"--- {p.name} Agent 로그 끝 ---", flush=True)
                print(f.read_text(encoding="utf-8", errors="replace")[-2000:], flush=True)
        raise
    finally:
        for p in people:
            p.stop_agent()
        kill_tree(server)
        server_log.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
