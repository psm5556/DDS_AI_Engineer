"""시험용 가짜 AI CLI: 실제 CLI처럼 stdio로 ACP(JSON-RPC 줄 단위)를 말한다. 실제 Claude·Gemini·Codex를 띄우지 않고 세션 수명을 잰다.
실행: python fake_acp.py --state <폴더> [--start-delay 초] [--session-delay 초] [--no-load] [--auth-error] [--no-window-check]

- 프로세스 시작·session/new·session/load·prompt·cancel을 <폴더>/log.jsonl에 남긴다. 시험은 read_log·count로 횟수를 잰다.
- 세션 기록을 <폴더>/sessions/에 남겨, 다시 시작한 프로세스도 실제 CLI처럼 session/load로 불러온다(기록을 다시 들려준다).
- 프로세스가 뜰 때 <폴더>/control.json이 있으면 위 옵션을 덮어쓴다(시험 도중 다음 시작만 느리게 만들 때).
프롬프트 글자로 동작을 고른다:
  [die]      답을 조금 쓰다가 프로세스가 죽는다      [die-silent] 바로 죽는다
  [slow N]   N초 동안 글자를 조금씩 쓴다             [quiet N]    N초 동안 아무것도 쓰지 않고 답한다
  [wait]     session/cancel이 올 때까지 기다린다      [hang]       cancel도 듣지 않고 영원히 멈춘다(멈춘 도구)
  [perm]     권한을 묻고 답을 글자로 쓴다             [big]        20MB짜리 도구 입출력을 보낸다
  [gone]     세션을 잊고 "Session not found" 오류를 낸다
  [bigperm]  10MB+10MB diff가 든 권한 요청을 보낸다(Gemini가 큰 파일 수정을 묻는 모양)   [bigimg] 20MB 이미지 조각을 보낸다
  [modelerr] 세션은 살아 있는데 "model gemini-x not found" 오류를 낸다
  [ratelimit] 사용 한도 오류    [emptyerr] 메시지 없는 오류
  [longtool] 명령줄처럼 아주 긴 제목의 도구를 6초 돌린다
  [rename 이름]  실제 dds_flow.set_project_info를 세션의 작업 폴더·MCP 환경으로 돌려 프로젝트 이름을 바꾼다
  [where]    실제 dds_cae.project_folder를 같은 환경으로 돌려 작업 폴더를 글자로 쓴다
control.json의 hold_cwd=true면 세션마다 cwd가 그 작업 폴더인 자식 프로세스를 띄운다.
실제 Claude(세션마다 claude.exe·MCP가 cwd를 그 폴더에 둔다)처럼 Windows가 폴더 이름 바꾸기를 막는다.
control.json의 history_by는 session/load가 기록을 찾는 규칙이다: "realpath"는 Claude($CLAUDE_CONFIG_DIR/projects/<실제 경로 인코딩>/<세션>.jsonl 한 곳),
"cwd"는 Gemini(경로 문자열). 실제 CLI로 확인한 규칙이다. 없으면 세션 id로만 찾는다.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

_write_lock = threading.Lock()
_log_lock = threading.Lock()


def read_log(state: str | Path) -> list[dict]:
    p = Path(state) / "log.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def count(state: str | Path, event: str) -> int:
    return sum(1 for e in read_log(state) if e["ev"] == event)


class FakeCli:
    def __init__(self, a):
        self.state = Path(a.state)
        (self.state / "sessions").mkdir(parents=True, exist_ok=True)
        self.start_delay, self.session_delay = a.start_delay, a.session_delay
        self.load_ok, self.auth_error = not a.no_load, a.auth_error
        self.hold_cwd = False
        self.history_by = None
        ctl = self.state / "control.json"
        if ctl.exists():
            c = json.loads(ctl.read_text(encoding="utf-8"))
            self.start_delay = c.get("start_delay", self.start_delay)
            self.session_delay = c.get("session_delay", self.session_delay)
            self.load_ok = c.get("load", self.load_ok)
            self.auth_error = c.get("auth_error", self.auth_error)
            self.hold_cwd = c.get("hold_cwd", False)
            self.history_by = c.get("history_by")
        self.live: set[str] = set()
        self.session_env: dict[str, tuple[str, dict]] = {}  # 세션 id → (cwd, MCP 환경)
        self.holders: list[subprocess.Popen] = []
        self.cancelled: dict[str, threading.Event] = {}
        self.waiters: dict[int, threading.Event] = {}
        self.replies: dict[int, dict] = {}
        self.next_id = 90000

    def log(self, ev: str, **kw):
        line = json.dumps({"ev": ev, "pid": os.getpid(), "t": time.time(), **kw}, ensure_ascii=False)
        with _log_lock:
            with open(self.state / "log.jsonl", "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def send(self, msg: dict):
        with _write_lock:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    def update(self, sid: str, upd: dict):
        self.send({"method": "session/update", "params": {"sessionId": sid, "update": upd}})

    def say(self, sid: str, text: str):
        self.update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})

    # ---------- 세션 기록 ----------
    def _session_file(self, sid: str) -> Path:
        return self.state / "sessions" / f"{sid}.json"

    def _history(self, sid: str) -> list[str]:
        try:
            return json.loads(self._session_file(sid).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _remember(self, sid: str, text: str):
        self._session_file(sid).write_text(json.dumps(self._history(sid) + [text], ensure_ascii=False), encoding="utf-8")

    # ---------- 실제 CLI가 지난 대화를 찾는 방법 (control.json의 history_by) ----------
    def _claude_transcript(self, sid: str, cwd: str) -> Path:
        name = re.sub(r"[^a-zA-Z0-9]", "-", os.path.realpath(cwd))
        return Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects" / name / f"{sid}.jsonl"

    def _record_home(self, sid: str, cwd: str):
        if self.history_by == "realpath":
            path = self._claude_transcript(sid, cwd)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        elif self.history_by == "cwd":
            (self.state / "sessions" / f"{sid}.cwd").write_text(os.path.normcase(os.path.abspath(cwd)), encoding="utf-8")

    def _history_found(self, sid: str, cwd: str) -> bool:
        if self.history_by == "realpath":  # Claude: 접합을 풀어 실제 경로의 폴더 한 곳만 본다
            return self._claude_transcript(sid, cwd).is_file()
        if self.history_by == "cwd":  # Gemini: 경로 문자열 그대로 찾는다
            home = self.state / "sessions" / f"{sid}.cwd"
            return home.is_file() and home.read_text(encoding="utf-8") == os.path.normcase(os.path.abspath(cwd))
        return True

    # ---------- 작업 폴더 ----------
    def _open_session(self, sid: str, params: dict):
        cwd = params.get("cwd") or os.getcwd()
        servers = params.get("mcpServers") or []
        env = {e["name"]: e["value"] for e in servers[0].get("env") or []} if servers else {}
        self.session_env[sid] = (cwd, env)
        if self.hold_cwd:  # stdin이 닫히면(이 프로세스가 끝나면) 함께 끝난다
            self.holders.append(subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], cwd=cwd, stdin=subprocess.PIPE,
                                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
            self.log("holder", sid=sid, holder=self.holders[-1].pid, cwd=cwd)

    def _run_mcp(self, sid: str, code: str, *args: str) -> str:
        """MCP 서버를 실제 CLI처럼 세션 cwd에서 세션 환경으로 띄워 도구 함수를 부른다."""
        cwd, env = self.session_env[sid]
        root = Path(__file__).resolve().parents[2]
        full = {**os.environ, **env, "PYTHONIOENCODING": "utf-8"}
        boot = f"import sys, json; sys.path.insert(0, {str(root / 'mcp_servers')!r}); " + code
        r = subprocess.run([sys.executable, "-c", boot, *args], cwd=cwd, env=full, capture_output=True, text=True, encoding="utf-8", timeout=120,
                           stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.stdout.strip() or r.stderr.strip()[-500:]

    # ---------- 요청 ----------
    def handle(self, msg: dict):
        if "method" not in msg:  # 권한 요청의 답
            ev = self.waiters.pop(msg.get("id"), None)
            if ev:
                self.replies[msg["id"]] = msg.get("result") or {}
                ev.set()
            return
        method, params, rid = msg["method"], msg.get("params") or {}, msg.get("id")
        if method == "session/cancel":
            self.log("cancel", sid=params.get("sessionId"))
            ev = self.cancelled.get(params.get("sessionId"))
            if ev:
                ev.set()
            return
        if method == "session/prompt":  # 턴 중에도 cancel·다른 세션을 받아야 한다
            threading.Thread(target=self._prompt, args=(rid, params), daemon=True).start()
            return
        try:
            self.send({"id": rid, "result": self._call(method, params)})
        except RuntimeError as e:
            code, _, text = str(e).partition(":")
            self.send({"id": rid, "error": {"code": int(code), "message": text}})

    def _call(self, method: str, params: dict) -> dict:
        if method == "initialize":
            self.log("initialize")
            return {"protocolVersion": 1, "agentCapabilities": {"loadSession": self.load_ok}, "authMethods": [],
                    "agentInfo": {"name": "fake-acp", "version": "1"}}
        if method == "session/new":
            if self.auth_error:
                raise RuntimeError("-32000:Authentication required")
            time.sleep(self.session_delay)
            sid = "fake-" + uuid.uuid4().hex[:10]
            self.live.add(sid)
            self._remember(sid, "(세션 시작)")
            self._record_home(sid, params.get("cwd") or os.getcwd())
            self._open_session(sid, params)
            self.log("session_new", sid=sid, cwd=params.get("cwd"), mcp=len(params.get("mcpServers") or []))
            return {"sessionId": sid}
        if method == "session/load":
            sid = params.get("sessionId")
            self.log("session_load", sid=sid, was_live=sid in self.live, cwd=params.get("cwd"))
            time.sleep(self.session_delay)
            if not self._session_file(sid).exists() or not self._history_found(sid, params.get("cwd") or os.getcwd()):
                raise RuntimeError("-32002:Session not found")
            for text in self._history(sid):  # 실제 CLI처럼 지난 대화를 다시 들려준다. 화면에 또 쌓이면 시험이 잡는다
                self.say(sid, "[다시 들려줌] " + text)
            self.live.add(sid)
            self._open_session(sid, params)
            return {}
        raise RuntimeError(f"-32601:지원하지 않는 메서드: {method}")

    def _prompt(self, rid, params: dict):
        sid = params.get("sessionId")
        text = "".join(p.get("text", "") for p in params.get("prompt") or [])
        self.log("prompt", sid=sid, text=text, known=sid in self.live)
        if sid not in self.live:
            self.send({"id": rid, "error": {"code": -32002, "message": "Session not found"}})
            return
        self._remember(sid, text)
        cancelled = self.cancelled[sid] = threading.Event()
        stop = "end_turn"
        m = re.search(r"\[rename ([^\]]+)\]", text)
        if m:
            out = self._run_mcp(sid, "import dds_flow; print(json.dumps(dds_flow.set_project_info(name=sys.argv[1]), ensure_ascii=False))", m.group(1))
            self.log("mcp_rename", sid=sid, out=out)
            self.say(sid, out + " ")
        if "[where]" in text:
            out = self._run_mcp(sid, "import dds_cae; print(json.dumps(dds_cae.project_folder()['folder'], ensure_ascii=False))")
            self.log("mcp_where", sid=sid, out=out)
            self.say(sid, "폴더:" + out + " ")
        if "[die-silent]" in text:
            os._exit(3)
        if "[die]" in text:
            self.say(sid, "하던 중에 ")
            time.sleep(0.1)
            sys.stderr.write("가짜 CLI: 도중에 죽습니다\n")
            sys.stderr.flush()
            os._exit(7)
        if "[hang]" in text:
            # 끝나지 않는 솔버처럼 자식을 남긴다. 다시 시작이 그 아래까지 끝내는지 시험이 pid로 본다
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.log("child", child=child.pid)
            self.say(sid, "멈춘 도구를 실행합니다")
            self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": "hang", "title": "dds-cae.run_script", "kind": "execute", "status": "in_progress"})
            threading.Event().wait()  # cancel을 받아도 답하지 않는다
        if "[gone]" in text:
            self.live.discard(sid)
            self.send({"id": rid, "error": {"code": -32002, "message": "Session not found"}})
            return
        for mark, error in (("[modelerr]", {"code": -32603, "message": "Internal error: model gemini-x not found"}),
                            ("[ratelimit]", {"code": -32603, "message": "Internal error: rate limit exceeded", "data": {"retry": 30}}),
                            ("[emptyerr]", {"code": -32603, "message": ""})):
            if mark in text:
                self.send({"id": rid, "error": error})
                return
        if "[longtool]" in text:
            title = "Run command: " + r"C:/very/long/path/to/solver/" * 40
            self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": "long", "title": title, "kind": "execute", "status": "in_progress"})
            time.sleep(6)
            self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "long", "status": "completed"})
        if "[bigperm]" in text:
            self.next_id += 1
            pid, ev = self.next_id, threading.Event()
            self.waiters[pid] = ev
            big = "가" * (10 * 1024 * 1024)
            self.send({"id": pid, "method": "session/request_permission", "params": {
                "sessionId": sid, "toolCall": {"toolCallId": "t" + str(pid), "title": "입력 덱 수정", "kind": "edit",
                                               "content": [{"type": "diff", "path": "deck.key", "oldText": big, "newText": big}]},
                "options": [{"optionId": "allow", "name": "허용", "kind": "allow_once"}]}})
            ev.wait()
            self.say(sid, "수정 허용됨")
        if "[bigimg]" in text:
            self.update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "image", "mimeType": "image/png", "data": "A" * (20 * 1024 * 1024)}})
        if "[wait]" in text:
            self.say(sid, "기다립니다")
            cancelled.wait()
            stop = "cancelled"
        m = re.search(r"\[slow (\d+(?:\.\d+)?)\]", text)
        if m:
            end = time.time() + float(m.group(1))
            n = 0
            while time.time() < end and not cancelled.is_set():
                n += 1
                self.say(sid, f"조각{n} ")
                time.sleep(0.05)
            stop = "cancelled" if cancelled.is_set() else stop
        m = re.search(r"\[quiet (\d+(?:\.\d+)?)\]", text)
        if m:
            cancelled.wait(float(m.group(1)))
        if "[perm]" in text:
            self.next_id += 1
            pid, ev = self.next_id, threading.Event()
            self.waiters[pid] = ev
            self.send({"id": pid, "method": "session/request_permission", "params": {
                "sessionId": sid, "toolCall": {"toolCallId": "t" + str(pid), "title": "스크립트 실행", "kind": "execute", "rawInput": {"a": 1}},
                "options": [{"optionId": "allow", "name": "허용", "kind": "allow_once"}, {"optionId": "reject", "name": "거부", "kind": "reject_once"}]}})
            ev.wait()
            self.say(sid, "권한:" + json.dumps(self.replies.pop(pid, {}), ensure_ascii=False))
        if "[big]" in text:
            big = "가" * (20 * 1024 * 1024)
            self.update(sid, {"sessionUpdate": "tool_call", "toolCallId": "big", "title": "큰 도구", "kind": "execute", "status": "in_progress", "rawInput": {"body": big}})
            self.update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "big", "status": "completed",
                              "content": [{"type": "content", "content": {"type": "text", "text": big}}]})
        self.say(sid, "답: " + text[-200:])
        self.send({"id": rid, "result": {"stopReason": stop}})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", required=True)
    ap.add_argument("--start-delay", type=float, default=0.0)
    ap.add_argument("--session-delay", type=float, default=0.0)
    ap.add_argument("--no-load", action="store_true")
    ap.add_argument("--auth-error", action="store_true")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", newline="\n")
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    cli = FakeCli(a)
    cli.log("process_start")
    time.sleep(cli.start_delay)  # 보안 검사로 프로그램 시작이 늦는 것을 흉내 낸다: 이 동안 stdin을 읽지 않는다
    for line in sys.stdin:
        line = line.strip()
        if line:
            cli.handle(json.loads(line))
    cli.log("process_exit")


if __name__ == "__main__":
    main()
