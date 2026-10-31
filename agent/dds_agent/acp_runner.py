"""ACP(Agent Client Protocol) 러너: AI CLI(`gemini --acp` 등)를 상주 프로세스로 띄우고 JSON-RPC(줄 단위, stdio)로 통신한다.
이벤트 루프 종류와 무관하게 동작하도록 subprocess + 리더 스레드로 구현했다.

느린 CLI를 죽이지 않는다: 시작이 시간 제한을 넘겨도 프로세스는 두고, initialize가 늦게 오면 그때 능력 정보를 채운다.
프로세스가 죽으면 감시 스레드가 대기 중인 요청을 실패시키고 on_exit으로 알린다. 다음 호출 때 다시 띄운다.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cae_adapters.hidden import KillOnExitJob, hidden_kwargs, kill_tree

from .runner import AgentError, AgentRunner, CliDied, LoginRequired, SessionGone, StageReport

log = logging.getLogger(__name__)
PROTOCOL_VERSION = 1
# 이 시간 동안 평균으로 코어 하나의 이만큼을 쓰면 계산 중으로 본다. CLI의 짧은 주기 작업은 평균에 묻힌다
CPU_WINDOW_SEC = 30
CPU_BUSY_CORES = 0.25
DEFAULT_START_TIMEOUT_SEC = 1800.0
AUTH_MARKS = ("auth", "login", "credential", "unauthorized", "로그인", "(-32000)")
LOAD_GONE_CODES = ("(-32002)", "(-32602)", "(-32601)")  # session/load에서는 이 코드도 "지원 안 함/없음"이다
RATE_MARKS = ("rate limit", "rate_limit", "quota", "too many requests", "(429)", "overloaded")
_NOT_FOUND = r"(not found|no such|does not exist|doesn't exist)"
_SESSION_MISSING = re.compile(rf"(unknown session|invalid session|no such session|session\b.{{0,120}}?\b{_NOT_FOUND})", re.S)


def _looks_like(error: Exception, marks: tuple[str, ...]) -> bool:
    s = str(error).lower()
    return any(m in s for m in marks)


def _session_missing(error: Exception, session_id: str) -> bool:
    """CLI가 정말 이 세션을 모른다고 할 때만 True. "model … not found"나 없는 파일 오류는 세션 문제가 아니다.
    살아 있는 세션을 잘못 버리면 다음 요청이 session/load를 보내고, Gemini는 그 세션을 dispose하고 Claude는 기록을 다시 들려준다."""
    s = str(error).lower()
    if _SESSION_MISSING.search(s) or (session_id.lower() in s and re.search(_NOT_FOUND, s)):
        return True
    return getattr(error, "code", None) == -32002 and not getattr(error, "data", None)  # ACP "없는 자원"인데 다른 자원을 말하지 않았다


class _RpcError(AgentError):
    """CLI가 돌려준 JSON-RPC 오류. str()은 원문 그대로라 표시를 가리는 데 쓰고, 화면에 보일 말은 _friendly가 만든다."""

    def __init__(self, error: dict):
        self.message = str(error.get("message") or "").strip()
        self.code = error.get("code")
        data = error.get("data")
        self.data = (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, default=str)) if data else ""
        super().__init__(f"{self.message} ({self.code}) {self.data}".strip())


@dataclass
class _Cli:
    """프로세스 하나의 상태. 재시작 뒤 옛 프로세스의 정리가 새 프로세스의 세션·요청을 지우지 않게 묶어 둔다."""
    proc: subprocess.Popen
    started_at: float
    pending: dict[int, Future] = field(default_factory=dict)
    sessions: set[str] = field(default_factory=set)
    loading: set[str] = field(default_factory=set)
    init: Future | None = None
    stdout_thread: threading.Thread | None = None


class AcpRunner(AgentRunner):
    def __init__(self, command: list[str], on_event, on_permission, env: dict | None = None,
                 start_timeout: float = DEFAULT_START_TIMEOUT_SEC, label: str | None = None, login: str | None = None):
        super().__init__(on_event, on_permission)
        self.command = command
        self.env = env
        self.start_timeout = start_timeout
        self.label = label or Path(command[0]).stem
        self.login = login or ""
        self.proc: subprocess.Popen | None = None
        self._cli: _Cli | None = None
        self._job: KillOnExitJob | None = None
        self._cpu_mark: tuple[float, float] | None = None  # (시각, Job의 CPU 시간)
        self._cpu_busy = False
        self._ids = itertools.count(1)
        self._perm: dict[str, tuple[int | str, str | None]] = {}  # request_id → (JSON-RPC id, 세션)
        self._load_errors: dict[str, int] = {}
        self._write_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self.agent_info: dict[str, Any] = {}
        self.stderr_tail: list[str] = []

    # ---------- 프로세스 ----------
    def _ensure_started(self, report: StageReport | None = None) -> _Cli:
        with self._start_lock:  # initialize를 기다리는 동안에는 잡지 않는다. 다른 대화의 중지·권한 응답이 막히면 안 된다
            cli = self._cli
            if not (cli and cli.proc.poll() is None):
                if report:
                    report("cli_start")
                cli = self._spawn()
            init = cli.init
            if init is None or (init.done() and init.exception() is not None):
                # 빠진 경우만 다시 묻는다. 늦게 오는 답을 버리지 않으려고 시간이 지나도 Future를 지우지 않는다
                init = cli.init = self._send_initialize(cli)
        if init.done():
            if init.exception() is not None:
                raise self._friendly(init.exception())
            return cli
        if report:
            report("initialize")
        try:
            init.result(timeout=self.start_timeout)
        except TimeoutError:
            raise AgentError(f"{self.label}이(가) {self.start_timeout:.0f}초 안에 시작되지 않았습니다 → 프로그램은 끝내지 않고 그대로 두었습니다. "
                             "다시 보내면 이어서 기다립니다. 계속되면 사내 보안 프로그램이나 설치 상태를 확인하세요.") from None
        except AgentError as e:
            raise self._friendly(e)
        return cli

    def _spawn(self) -> _Cli:
        log.info("에이전트 시작: %s", self.command)
        try:
            proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=self.env, bufsize=1, text=True, encoding="utf-8", errors="replace", **hidden_kwargs(),
            )
        except FileNotFoundError as e:
            raise CliDied(f"{self.label} 실행 파일을 찾을 수 없습니다({self.command[0]}) → 설정에서 AI 도구 설치 상태를 확인하세요.") from e
        if self._job:  # CLI만 죽었다. 사용자가 쓰는 GUI까지 끝내지 않게 정리한다
            self._job.stop()
        # 창이 없으니 Agent가 비정상 종료해도 사용자가 알아채지 못한다. 고아로 쌓이지 않게 Agent 수명에 묶는다
        self._job = KillOnExitJob()
        self._job.assign(proc)
        self._cpu_mark = None
        self.proc = proc
        self.agent_info = {}
        self._perm.clear()
        self.stderr_tail = []
        cli = self._cli = _Cli(proc=proc, started_at=time.monotonic())
        cli.stdout_thread = threading.Thread(target=self._read_stdout, args=(cli,), daemon=True)
        cli.stdout_thread.start()
        threading.Thread(target=self._read_stderr, args=(cli,), daemon=True).start()
        threading.Thread(target=self._watch, args=(cli,), daemon=True).start()
        return cli

    def _send_initialize(self, cli: _Cli) -> Future:
        rid = next(self._ids)
        fut: Future = Future()
        cli.pending[rid] = fut
        fut.add_done_callback(lambda f, c=cli: self._initialized(c, f))
        self._send(cli, {"id": rid, "method": "initialize", "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}, "terminal": False},
        }})
        return fut

    def _initialized(self, cli: _Cli, fut: Future):
        if fut.exception() is not None:
            return
        if self._cli is cli:  # 시간 제한 뒤에 와도 채운다. 비어 있으면 그 프로세스가 사는 동안 session/load를 못 쓴다
            self.agent_info = fut.result() or {}
        self._timing("start", time.monotonic() - cli.started_at)

    def _timing(self, kind: str, seconds: float):
        if self.on_timing:
            try:
                self.on_timing(kind, seconds)
            except Exception:
                log.exception("시작 시간 기록 실패")

    def _watch(self, cli: _Cli):
        """프로세스를 감시한다. 기다리던 호출이 stdout을 쥔 손자 때문에 영원히 멈추지 않게, 끝나면 stdout 끝을 조금만 기다린다."""
        code = cli.proc.wait()
        if cli.stdout_thread:
            cli.stdout_thread.join(timeout=2)
        tail = "\n".join(self.stderr_tail[-20:])
        sessions = set(cli.sessions)
        cli.sessions.clear()
        if self._cli is cli:
            self._perm.clear()
        log.info("에이전트 종료: 코드 %s, 세션 %d개", code, len(sessions))
        if self.on_exit:  # 진행 중 호출을 실패시키기 전에 알린다. 서버가 "CLI가 죽었다"를 먼저 알아야 안내가 하나로 끝난다
            try:
                self.on_exit(code, tail, sessions)
            except Exception:
                log.exception("CLI 종료 알림 실패")
        err = self._died(cli, code, tail)
        for fut in list(cli.pending.values()):
            if not fut.done():
                fut.set_exception(err)
        cli.pending.clear()

    def _died(self, cli: _Cli, code, tail: str) -> CliDied:
        """CLI가 끝났을 때 사용자에게 보일 말. 시작하다 끝났으면 보안 프로그램이 막았을 수 있다. 원인 없는 문장으로 끝내지 않는다."""
        started = bool(cli.init and cli.init.done() and cli.init.exception() is None)
        if started:
            text = f"{self.label}이(가) 갑자기 종료되었습니다(코드 {code}) → 다시 보내면 AI 도구를 다시 시작해 이어 갑니다."
        else:
            text = (f"{self.label}이(가) 시작하다가 종료되었습니다(코드 {code}) → 사내 보안 프로그램이 막았거나 설치가 깨졌을 수 있습니다. "
                    "설정에서 AI 도구 설치 상태를 확인한 뒤 다시 보내 주세요.")
        if tail.strip():
            text += f"\n\n종료 전 기록:\n{tail.strip()[-800:]}"
        return CliDied(text)

    def alive(self) -> bool:
        cli = self._cli
        return bool(cli and cli.proc.poll() is None)

    def live_sessions(self) -> set[str]:
        cli = self._cli
        return set(cli.sessions) if cli and cli.proc.poll() is None else set()

    def warm_up(self, report: StageReport | None = None) -> None:
        self._ensure_started(report)

    def supports_load(self) -> bool:
        return bool((self.agent_info.get("agentCapabilities") or {}).get("loadSession"))

    def working_in_background(self) -> bool:
        """CLI 아래 프로세스들이 CPU를 쓰는 중인지. 프로세스 이름으로는 CLI 도우미와 솔버를 가릴 수 없어 쓰는 양으로 본다.
        한가한 CLI·MCP는 거의 쓰지 않는다. 라이선스를 기다리는 솔버처럼 CPU를 안 쓰는 것은 잡지 못한다."""
        cpu = self._job.cpu_seconds() if self._job else None
        if cpu is None:
            return False
        now = time.monotonic()
        mark = self._cpu_mark
        if mark is None:
            self._cpu_mark = (now, cpu)
        elif now - mark[0] >= CPU_WINDOW_SEC:
            self._cpu_busy = (cpu - mark[1]) / (now - mark[0]) >= CPU_BUSY_CORES
            self._cpu_mark = (now, cpu)
        return self._cpu_busy

    def shutdown(self):
        job, self._job = self._job, None
        # Job을 먼저 쓴다. 부모가 끝나 트리에서 빠진 자손까지 찾고, 창이 보이는 사용자 GUI는 남긴다
        stopped = job.stop() if job else False
        if not (self.proc and self.proc.poll() is None):
            return
        if stopped:
            try:
                self.proc.wait(timeout=3)
                return
            except subprocess.TimeoutExpired:
                pass
        kill_tree(self.proc)  # .cmd 셈은 terminate로 cmd.exe만 끝나 node가 숨은 채 남는다

    # ---------- JSON-RPC ----------
    def _send(self, cli: _Cli, msg: dict):
        if cli.proc.poll() is not None:
            raise self._died(cli, cli.proc.poll(), "\n".join(self.stderr_tail[-20:]))
        line = json.dumps({"jsonrpc": "2.0", **msg}, ensure_ascii=False)
        try:
            with self._write_lock:
                cli.proc.stdin.write(line + "\n")
                cli.proc.stdin.flush()
        except (OSError, ValueError) as e:  # 죽어 가는 CLI에 쓴 것이다. PC Agent의 서버 연결과 상관없다
            raise CliDied(f"{self.label}에 보내지 못했습니다({e}) → 다시 보내면 AI 도구를 다시 시작해 이어 갑니다.") from e

    def _request(self, cli: _Cli, method: str, params: dict, timeout: float | None = None) -> Any:
        rid = next(self._ids)
        fut: Future = Future()
        cli.pending[rid] = fut
        try:
            self._send(cli, {"id": rid, "method": method, "params": params})
            return fut.result(timeout=timeout)
        except TimeoutError:
            raise AgentError(f"{self.label}이(가) {timeout:.0f}초 안에 {method}에 답하지 않았습니다 → 프로그램은 그대로 두었습니다. "
                             "다시 보내면 이어서 기다립니다.") from None
        finally:
            cli.pending.pop(rid, None)

    def _read_stderr(self, cli: _Cli):
        for line in cli.proc.stderr:
            self.stderr_tail = (self.stderr_tail + [line.rstrip()])[-200:]
            log.debug("[agent stderr] %s", line.rstrip())

    def _read_stdout(self, cli: _Cli):
        for line in cli.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.debug("[agent stdout] %s", line)
                continue
            try:
                self._dispatch(cli, msg)
            except Exception:
                log.exception("ACP 메시지 처리 실패: %s", line[:500])

    def _dispatch(self, cli: _Cli, msg: dict):
        if "method" not in msg:  # 응답
            fut = cli.pending.get(msg.get("id"))
            if fut and not fut.done():
                if "error" in msg:
                    fut.set_exception(_RpcError(msg["error"] if isinstance(msg["error"], dict) else {"message": str(msg["error"])}))
                else:
                    fut.set_result(msg.get("result"))
            return

        method, params = msg["method"], msg.get("params") or {}
        if method == "session/update":
            if params.get("sessionId") in cli.loading:  # session/load가 다시 들려주는 지난 대화. 화면에 또 쌓이면 안 된다
                return
            self.on_event(params.get("sessionId"), params.get("update") or {})
        elif method == "session/request_permission":
            request_id = str(msg["id"])
            self._perm[request_id] = (msg["id"], params.get("sessionId"))
            self.on_permission(params.get("sessionId"), request_id, params)
        elif "id" in msg:  # fs/terminal 등 미지원 요청
            self._send(cli, {"id": msg["id"], "error": {"code": -32601, "message": f"클라이언트 미지원: {method}"}})

    def _friendly(self, e: BaseException) -> BaseException:
        """CLI가 준 오류를 사용자의 말로. 로그인 안 됨은 느린 것과 다르다: 기다리게 두지 않고 어느 CLI를 어떻게 로그인할지 알려 준다.
        그 밖의 오류도 원인과 할 일을 함께 보인다(영어 원문이나 코드 번호만 남기지 않는다)."""
        if isinstance(e, (CliDied, LoginRequired, SessionGone)) or not isinstance(e, AgentError):
            return e
        if _looks_like(e, AUTH_MARKS):
            how = self.login or "터미널에서 그 도구를 실행해 로그인"
            return LoginRequired(f"{self.label} 로그인이 필요합니다 → {how}. 로그인한 뒤 다시 보내 주세요.")
        if not isinstance(e, _RpcError):  # 이미 이 파일에서 안내를 담아 만든 오류
            return e
        if _looks_like(e, RATE_MARKS):
            return AgentError(f"{self.label} 사용 한도에 걸렸습니다 → 잠시 뒤 다시 보내 주세요. 계속되면 계정의 사용량을 확인하세요.\n\n원문: {str(e)[:500]}")
        if not e.message and not e.data:
            return AgentError(f"{self.label}이(가) 원인을 알리지 않고 실패했습니다(코드 {e.code}) → 다시 보내 주세요. "
                              f"계속되면 터미널에서 {self.label}을(를) 직접 실행해 상태를 확인하세요.")
        return AgentError(f"{self.label}이(가) 요청을 처리하지 못했습니다 → 다시 보내 주세요. 계속되면 아래 원문을 확인하세요.\n\n원문: {str(e)[:500]}")

    # ---------- AgentRunner ----------
    def new_session(self, cwd: str, mcp_servers: list[dict] | None = None, report: StageReport | None = None) -> str:
        cli = self._ensure_started(report)
        if report:
            report("session_new")
        t0 = time.monotonic()
        try:
            res = self._request(cli, "session/new", {"cwd": os.path.abspath(cwd), "mcpServers": mcp_servers or []},
                                timeout=self.start_timeout)
        except AgentError as e:
            raise self._friendly(e)
        self._timing("session", time.monotonic() - t0)
        cli.sessions.add(res["sessionId"])
        return res["sessionId"]

    def load_session(self, session_id: str, cwd: str, mcp_servers: list[dict] | None = None, report: StageReport | None = None) -> bool:
        """False는 지원하지 않거나 세션이 없을 때뿐이다. 멈췄거나 죽은 경우는 예외로 올려 멀쩡한 세션을 새 세션으로 덮지 않는다."""
        cli = self._ensure_started(report)
        if not self.supports_load():
            return False
        if session_id in cli.sessions:  # 살아 있는 세션에 load하면 Gemini는 세션을 dispose하고 Claude는 기록을 전부 다시 들려준다
            return True
        if report:
            report("session_load")
        t0 = time.monotonic()
        cli.loading.add(session_id)
        try:
            self._request(cli, "session/load", {"sessionId": session_id, "cwd": os.path.abspath(cwd), "mcpServers": mcp_servers or []},
                          timeout=self.start_timeout)
        except AgentError as e:
            if isinstance(self._friendly(e), LoginRequired):
                raise self._friendly(e)
            if isinstance(e, CliDied) or cli.proc.poll() is not None or "초 안에" in str(e):
                raise
            if _session_missing(e, session_id) or _looks_like(e, LOAD_GONE_CODES):
                return False
            # 그 밖의 CLI 오류: 한 번은 그대로 알린다. 기록이 망가진 세션이면 계속 실패해 대화가 막히므로 두 번째에는 새 세션으로 넘어간다
            self._load_errors[session_id] = self._load_errors.get(session_id, 0) + 1
            if self._load_errors[session_id] >= 2:
                log.warning("세션 %s을 두 번 불러오지 못해 새 세션으로 넘어갑니다: %s", session_id, e)
                return False
            raise self._friendly(e)
        finally:
            cli.loading.discard(session_id)
        self._timing("session", time.monotonic() - t0)
        cli.sessions.add(session_id)
        return True

    def prompt(self, session_id: str, text: str) -> str:
        cli = self._ensure_started()
        if session_id not in cli.sessions:
            raise SessionGone("AI 도구가 다시 시작되어 이 세션을 다시 불러와야 합니다 → 같은 요청을 다시 보내 주세요.")
        try:
            res = self._request(cli, "session/prompt", {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]})
        except AgentError as e:
            if cli.proc.poll() is None and _session_missing(e, session_id):
                cli.sessions.discard(session_id)  # CLI 쪽에서 세션이 사라졌다. 같은 sid로 계속 실패해 대화가 막히지 않게 잊는다
                raise SessionGone(f"AI 도구가 이 세션을 찾지 못했습니다({e}) → 같은 요청을 다시 보내면 이전 대화를 이어 붙입니다.") from e
            raise self._friendly(e)
        return (res or {}).get("stopReason", "end_turn")

    def cancel(self, session_id: str) -> None:
        cli = self._cli
        if not cli:
            return
        self._send(cli, {"method": "session/cancel", "params": {"sessionId": session_id}})
        for request_id, (_, sid) in list(self._perm.items()):  # 다른 대화의 권한 요청은 건드리지 않는다
            if sid == session_id:
                self.respond_permission(request_id, None)

    def respond_permission(self, request_id: str, option_id: str | None) -> None:
        entry = self._perm.pop(request_id, None)
        if entry is None:
            raise AgentError("이미 처리되었거나 존재하지 않는 권한 요청입니다")
        if not self._cli:
            return
        outcome = {"outcome": "selected", "optionId": option_id} if option_id else {"outcome": "cancelled"}
        self._send(self._cli, {"id": entry[0], "result": {"outcome": outcome}})
