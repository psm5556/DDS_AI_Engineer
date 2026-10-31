"""PC Agent 쪽 세션 유지: 실제 ACP 프로세스(가짜 CLI)와 AcpRunner·Agent를 직접 시험한다."""
import asyncio
import json
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.dds_agent import config, context, outbox
from agent.dds_agent import main as M
from agent.dds_agent.acp_runner import AcpRunner
from agent.dds_agent.runner import AgentError, LoginRequired, SessionGone
from agent.tests.fake_acp import count, read_log

FAKE = str(Path(__file__).with_name("fake_acp.py"))


def _runner(tmp_path, *args, events=None, perms=None, **kw) -> AcpRunner:
    cmd = [sys.executable, FAKE, "--state", str(tmp_path / "cli"), *args]
    return AcpRunner(cmd, (lambda sid, u: events.append((sid, u))) if events is not None else (lambda *a: None),
                     (lambda sid, rid, p: perms.append((sid, rid, p))) if perms is not None else (lambda *a: None), **kw)


@pytest.fixture
def agent(monkeypatch, tmp_path):
    state = tmp_path / "cli"
    cfg = tmp_path / "agent.toml"
    cfg.write_text("\n".join([
        f"workspace_dir = {json.dumps(str(tmp_path / 'ws'))}", "tray = false", 'default_agent = "fake"', "[agents.fake]",
        'label = "가짜"', f"command = {json.dumps([sys.executable, FAKE, '--state', str(state)])}", "packages = []", ""]), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    monkeypatch.setenv("DDS_AGENT_CONFIG", str(cfg))
    monkeypatch.setenv("DDS_TOKEN", "t")
    monkeypatch.setenv("DDS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(context, "sync_project_context", lambda d: False)
    monkeypatch.setattr(M, "sync_project_context", lambda d: False)
    a = M.Agent()
    yield a
    a.shutdown()


# ---------- 느린 시작 ----------
def test_slow_start_past_limit_keeps_process_and_fills_late_initialize(tmp_path):
    """예전 60초 제한을 줄여서 재현: 시간을 넘겨도 프로세스를 죽이지 않고, 늦게 온 initialize로 능력을 채워 session/load를 쓸 수 있다."""
    r = _runner(tmp_path, "--start-delay", "2", start_timeout=0.3)
    try:
        with pytest.raises(AgentError) as e:
            r.new_session(str(tmp_path))
        assert str(e.value), "빈 오류"
        assert "그대로" in str(e.value)  # 끝내지 않았다고 알려 준다
        assert r.alive()
        for _ in range(100):
            if r.agent_info:
                break
            time.sleep(0.05)
        assert r.supports_load()  # 예전에는 agent_info가 빈 채로 남아 프로세스가 사는 동안 재개가 안 됐다
        r.start_timeout = 30
        sid = r.new_session(str(tmp_path))
        assert sid in r.live_sessions()
        assert count(tmp_path / "cli", "process_start") == 1 and count(tmp_path / "cli", "initialize") == 1
    finally:
        r.shutdown()


def test_default_start_limit_is_long():
    assert AcpRunner(["x"], lambda *a: None, lambda *a: None).start_timeout >= 1800
    assert config.AgentSettings().cli_start_timeout_sec == 1800


def test_stage_reports_and_timing(tmp_path):
    stages, times = [], {}
    r = _runner(tmp_path, "--start-delay", "0.3")
    r.on_timing = lambda kind, sec: times.setdefault(kind, sec)
    try:
        r.new_session(str(tmp_path), None, stages.append)
        assert stages == ["cli_start", "initialize", "session_new"]
        for _ in range(50):
            if "start" in times:
                break
            time.sleep(0.02)
        assert times["start"] >= 0.2 and "session" in times
    finally:
        r.shutdown()


def test_login_error_names_the_agent(tmp_path):
    r = _runner(tmp_path, "--auth-error", label="Claude Code", login="터미널에서 `claude` 실행 → /login")
    try:
        with pytest.raises(LoginRequired) as e:
            r.new_session(str(tmp_path))
        assert "Claude Code 로그인이 필요합니다" in str(e.value) and "/login" in str(e.value) and "Gemini" not in str(e.value)
    finally:
        r.shutdown()


# ---------- 세션 ----------
def test_load_results(tmp_path):
    r = _runner(tmp_path)
    try:
        sid = r.new_session(str(tmp_path))
        assert r.load_session(sid, str(tmp_path)) is True  # 살아 있으면 CLI에 보내지 않는다(Gemini는 dispose한다)
        assert count(tmp_path / "cli", "session_load") == 0
        assert r.load_session("없는세션", str(tmp_path)) is False  # 없음 → 새 세션으로 넘어가도 된다
    finally:
        r.shutdown()
    r2 = _runner(tmp_path, "--no-load")
    try:
        assert r2.load_session(sid, str(tmp_path)) is False  # 지원 안 함
    finally:
        r2.shutdown()


def test_generic_load_error_raises_once_then_gives_up(tmp_path, monkeypatch):
    """"지원 안 함/없음"이 아닌 오류로 멀쩡한 세션을 덮지 않는다. 그래도 망가진 세션이 대화를 영원히 막지는 않는다."""
    r = _runner(tmp_path)
    try:
        r.warm_up()

        def boom(cli, method, params, timeout=None):
            raise AgentError("내부 오류 (-32603)")
        monkeypatch.setattr(r, "_request", boom)
        with pytest.raises(AgentError):
            r.load_session("s1", str(tmp_path))
        assert r.load_session("s1", str(tmp_path)) is False
    finally:
        r.shutdown()


def test_load_replay_is_not_forwarded(tmp_path):
    events = []
    r = _runner(tmp_path, events=events)
    try:
        sid = r.new_session(str(tmp_path))
        r.prompt(sid, "첫 말")
        events.clear()
    finally:
        r.shutdown()
    r2 = _runner(tmp_path, events=events)
    try:
        assert r2.load_session(sid, str(tmp_path)) is True
        assert events == []
        r2.prompt(sid, "다음 말")
        assert any("다음 말" in u.get("content", {}).get("text", "") for _, u in events)
    finally:
        r2.shutdown()


def test_prompt_on_unknown_session_is_session_gone(tmp_path):
    r = _runner(tmp_path)
    try:
        r.warm_up()
        with pytest.raises(SessionGone):
            r.prompt("모르는세션", "x")
        assert count(tmp_path / "cli", "prompt") == 0
    finally:
        r.shutdown()


# ---------- 프로세스 감시 ----------
def test_watcher_reports_exit_before_failing_pending(tmp_path):
    order, sessions = [], []
    r = _runner(tmp_path)
    r.on_exit = lambda code, tail, sids: (order.append("exit"), sessions.append((code, tail, sids)))
    sid = r.new_session(str(tmp_path))
    with pytest.raises(AgentError) as e:
        r.prompt(sid, "[die]")
    order.append("prompt failed")
    assert order == ["exit", "prompt failed"]
    assert sessions[0][0] == 7 and "도중에 죽습니다" in sessions[0][1] and sessions[0][2] == {sid}
    assert "종료" in str(e.value) and r.live_sessions() == set() and not r.alive()
    # 다음 호출 때 다시 뜬다
    sid2 = r.new_session(str(tmp_path))
    assert r.alive() and sid2 in r.live_sessions()
    assert count(tmp_path / "cli", "process_start") == 2
    r.shutdown()


def test_stop_during_turn_on_dead_pipe_does_not_raise_oserror(tmp_path):
    """죽어 가는 CLI에 쓴 오류가 AgentError로 나와야 서버 연결까지 끊기지 않는다."""
    r = _runner(tmp_path)
    r.warm_up()
    cli = r._cli
    cli.proc.stdin.close()
    with pytest.raises(AgentError):
        r.cancel("s")
    r.shutdown()


def test_cancel_only_touches_that_sessions_permissions(tmp_path):
    perms = []
    r = _runner(tmp_path, perms=perms)
    try:
        a, b = r.new_session(str(tmp_path)), r.new_session(str(tmp_path))
        results = {}
        ta = threading.Thread(target=lambda: results.setdefault("a", r.prompt(a, "[perm]")))
        tb = threading.Thread(target=lambda: results.setdefault("b", r.prompt(b, "[perm]")))
        ta.start()
        tb.start()
        for _ in range(100):
            if len(perms) == 2:
                break
            time.sleep(0.05)
        r.cancel(a)
        ta.join(timeout=10)
        assert "a" in results and "b" not in results and len(r._perm) == 1  # B의 권한 요청은 남는다
        rb = next(rid for sid, rid, _ in perms if sid == b)
        r.respond_permission(rb, "allow")
        tb.join(timeout=10)
        assert "b" in results
    finally:
        r.shutdown()


# ---------- Agent ----------
def _run_turn(agent, sid, text, turn_id="t1", chat_id=1):
    agent._start_turn({"turn_id": turn_id, "chat_id": chat_id, "session_id": sid, "agent": "fake", "text": text, "started_at": 123.0})


def _outbox(agent) -> list[dict]:
    return [json.loads(t) for _, t in agent.outbox.after(0)]


def _wait(cond, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    raise TimeoutError


def test_events_and_turn_end_kept_while_disconnected(agent):
    sid = agent._new_session(1, "fake", "p", "u", "t0")["session_id"]
    assert agent.ws is None  # 연결이 없다
    _run_turn(agent, sid, "끊긴 동안")
    _wait(lambda: not agent.active_turns)
    msgs = _outbox(agent)
    kinds = [m["type"] for m in msgs]
    assert "agent.event" in kinds and kinds[-1] == "agent.turn_end"
    assert [m["seq"] for m in msgs] == sorted(m["seq"] for m in msgs)  # 순서대로
    assert all(m["boot"] == agent.boot for m in msgs)
    # 끝났어도 서버가 모르면 hello에 함께 알린다
    assert [t["turn_id"] for t in agent.hello()["active_turns"]] == ["t1"]
    agent.outbox.ack(msgs[-1]["seq"])
    assert len(agent.outbox) == 0 and agent.hello()["active_turns"] == []


def test_hello_reports_live_sessions_and_active_turns(agent):
    sid = agent._new_session(1, "fake", "p", "u")["session_id"]
    _run_turn(agent, sid, "[wait]")
    _wait(lambda: agent.active_prompts == 1)
    h = agent.hello()
    assert h["live_sessions"] == [sid] and h["running_agents"] == ["fake"] and h["boot"] == agent.boot
    t = h["active_turns"][0]
    assert (t["turn_id"], t["chat_id"], t["session_id"], t["started_at"]) == ("t1", 1, sid, 123.0)


def test_same_turn_twice_runs_once(agent):
    sid = agent._new_session(1, "fake", "p", "u")["session_id"]
    _run_turn(agent, sid, "한 번만")
    _run_turn(agent, sid, "한 번만")
    _wait(lambda: not agent.active_turns)
    time.sleep(0.2)
    assert count(Path(agent.s.agents["fake"]["command"][3]), "prompt") == 1


def test_session_new_with_same_turn_is_one_session(agent):
    state = Path(agent.s.agents["fake"]["command"][3])
    a = agent._new_session(1, "fake", "p", "u", "same")
    b = agent._new_session(1, "fake", "p", "u", "same")
    assert a == b and count(state, "session_new") == 1


def test_load_of_live_session_skips_cli(agent):
    state = Path(agent.s.agents["fake"]["command"][3])
    sid = agent._new_session(1, "fake", "p", "u")["session_id"]
    assert agent._load_session(1, sid, "fake", "p", "u") == {"ok": True, "live": True}
    assert count(state, "session_load") == 0


def test_runner_exit_notice_and_turn_end(agent):
    sid = agent._new_session(1, "fake", "p", "u")["session_id"]
    _run_turn(agent, sid, "[die]")
    _wait(lambda: not agent.active_turns)
    msgs = _outbox(agent)
    exit_i = next(i for i, m in enumerate(msgs) if m["type"] == "agent.runner_exit")
    end_i = next(i for i, m in enumerate(msgs) if m["type"] == "agent.turn_end")
    assert exit_i < end_i  # 서버가 CLI 사망을 먼저 안다
    assert msgs[exit_i]["session_ids"] == [sid] and msgs[exit_i]["reason"] == "exit" and msgs[exit_i]["code"] == 7
    assert agent.live_sessions() == [] and sid not in agent.session_runner


def test_force_restart_kills_hung_turn_and_warms_up(agent):
    state = Path(agent.s.agents["fake"]["command"][3])
    sid = agent._new_session(1, "fake", "p", "u")["session_id"]
    _run_turn(agent, sid, "[hang]")
    _wait(lambda: count(state, "prompt") == 1)
    first_pid = agent.runners["fake"].proc.pid
    agent.state.remember_session("fake", sid, (1, "p", "u"))
    agent._force_restart("fake")
    _wait(lambda: not agent.active_turns)
    msgs = _outbox(agent)
    ex = next(m for m in msgs if m["type"] == "agent.runner_exit")
    assert ex["reason"] == "force_restart"
    assert agent.runners["fake"].proc.pid != first_pid and agent.runners["fake"].alive()  # 다음 요청을 위해 다시 떠 있다
    assert count(state, "process_start") == 2 and count(state, "session_load") == 1


def test_handle_survives_bad_messages_and_cancel_does_not_block(agent):
    async def run():
        agent.loop = asyncio.get_running_loop()
        # 모르는 세션·없는 러너·잘못된 형식: 수신 루프를 끊지 않는다
        await agent.handle({"type": "session.cancel", "session_id": "없음"})
        await agent.handle({"type": "permission.reply", "request_id": "no-colon", "option_id": None})
        await agent.handle({"type": "session.force_restart", "agent": "없음"})
        await asyncio.sleep(0.1)
    asyncio.run(run())


def test_respond_uses_repr_for_empty_error(agent):
    sent = []

    class Ws:
        async def send(self, text):
            sent.append(json.loads(text))

    async def run():
        agent.ws = Ws()

        def boom():
            raise TimeoutError()  # str(e) == ""

        await agent._respond(5, boom)
    asyncio.run(run())
    assert sent[0]["rid"] == 5 and sent[0]["error"]


def test_big_events_are_cut_but_text_is_kept():
    big = "가" * (5 * 1024 * 1024)
    chunks = outbox.shrink_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": big}})
    assert len(chunks) > 1 and "".join(c["content"]["text"] for c in chunks) == big  # 답변 글자는 잃지 않는다
    assert all(len(json.dumps(c, ensure_ascii=False)) < 16 * 1024 * 1024 for c in chunks)
    tool = outbox.shrink_update({"sessionUpdate": "tool_call", "toolCallId": "x", "title": "t", "rawInput": {"body": big},
                                 "content": [{"type": "content", "content": {"type": "text", "text": big}}]})[0]
    assert len(json.dumps(tool, ensure_ascii=False)) < outbox.MAX_EVENT_CHARS
    assert tool["toolCallId"] == "x" and tool["rawInput"]["잘림"]
    small = {"sessionUpdate": "tool_call", "rawInput": {"a": 1}}
    assert outbox.shrink_update(small) == [small]


def test_big_permission_and_image_fit_the_websocket_limit():
    """답을 받기 전에는 지우지 않으므로 한도를 넘는 메시지 하나가 재연결마다 연결을 끊어 대화 전체를 막는다."""
    big = "가" * (10 * 1024 * 1024)
    params = {"sessionId": "s", "options": [{"optionId": "allow"}],
              "toolCall": {"toolCallId": "t", "title": "입력 덱 수정", "kind": "edit", "rawInput": {"a": 1},
                           "content": [{"type": "diff", "path": "deck.key", "oldText": big, "newText": big}]}}
    small = outbox.shrink_permission(params)
    assert len(json.dumps(small, ensure_ascii=False)) < outbox.MAX_EVENT_CHARS
    assert small["toolCall"]["title"] == "입력 덱 수정" and small["toolCall"]["rawInput"] == {"a": 1} and small["options"] == params["options"]
    img = {"sessionUpdate": "agent_message_chunk", "content": {"type": "image", "data": "A" * (20 * 1024 * 1024)}}
    out = outbox.shrink_update(img)
    assert len(json.dumps(out, ensure_ascii=False)) < outbox.MAX_EVENT_CHARS and "이미지" in out[0]["content"]["text"]
    # 작은 것은 그대로
    assert outbox.shrink_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "image", "data": "A"}})[0]["content"]["type"] == "image"
    p = {"toolCall": {"title": "t", "rawInput": {"a": 1}}}
    assert outbox.shrink_permission(p) == p


def test_outbox_never_holds_an_item_over_the_limit():
    box = outbox.Outbox("b")
    big = "가" * (10 * 1024 * 1024)
    box.add({"type": "agent.turn_end", "turn_id": "t", "chat_id": 1, "session_id": "s", "error": big, "stop_reason": "error"})
    box.add({"type": "모르는것", "x": [{"y": big}] * 3, "session_id": "s"})
    box.add({"type": "큰 목록", "x": list(range(6_000_000))})
    for _, text in box.after(0):
        assert len(text.encode("utf-8")) <= outbox.MAX_ITEM_BYTES + 200
    end = json.loads(box.after(0)[0][1])
    assert end["turn_id"] == "t" and end["stop_reason"] == "error" and end["error"].startswith("가")  # 턴 끝의 뜻은 남는다


def test_force_restart_blocks_session_requests_until_cli_is_gone(agent):
    """다시 시작 바로 뒤의 session/load가 죽어 가는 CLI를 보고 "살아 있다"고 답하면 다음 프롬프트가 그 CLI로 간다."""
    state = Path(agent.s.agents["fake"]["command"][3])
    sid = agent._new_session(1, "fake", "p", "u")["session_id"]
    first_pid = agent.runners["fake"].proc.pid
    agent._begin_force("fake")
    results = []
    t = threading.Thread(target=lambda: results.append(agent._load_session(1, sid, "fake", "p", "u")))
    t.start()
    time.sleep(0.3)
    assert not results  # 다시 시작이 끝날 때까지 기다린다
    agent._force_restart("fake")
    t.join(timeout=30)
    assert results and results[0].get("ok") and not results[0].get("live")  # 살아 있다고 답하지 않고 새 CLI에 불러왔다
    assert agent.runners["fake"].proc.pid != first_pid and count(state, "session_load") >= 1
    assert sid in agent.live_sessions()


def test_late_exit_notice_does_not_forget_session_reloaded_in_new_cli(agent):
    """옛 프로세스의 뒤늦은 종료 알림이 새 프로세스에 불러온 세션을 지우면 바로 다음 프롬프트가 실패한다."""
    sid = agent._new_session(1, "fake", "p", "u")["session_id"]
    agent._on_runner_exit("fake", 1, "", {sid})  # 세션이 살아 있는 채로 알림이 왔다
    assert agent.session_runner.get(sid) == "fake" and sid in agent.live_sessions()
    ex = next(m for m in _outbox(agent) if m["type"] == "agent.runner_exit")
    assert ex["session_ids"] == []


def test_auto_restart_waits_for_undelivered_output(updating, monkeypatch):
    """서버가 안 되는 동안 끝난 답을 들고 있으면 다시 시작하지 않는다. outbox는 메모리에만 있어 함께 사라진다."""
    agent = updating
    agent._reliable({"type": "agent.turn_end", "turn_id": "t", "chat_id": 1, "session_id": "s", "stop_reason": "end_turn"})

    async def run():
        agent.schedule_update("99.0.0", "x")
        await asyncio.sleep(0.5)
        assert agent.steps == ["prepare"] and not agent.restart
        agent.outbox.ack(10 ** 6)
        await asyncio.sleep(0.5)
        assert agent.steps == ["prepare", "activate"] and agent.restart
    asyncio.run(run())


def test_prompt_from_rolled_back_server_answers_by_response(agent):
    """서버를 옛 버전으로 되돌리면 session.prompt는 rid만 있고 turn_id가 없다. Agent는 되돌릴 수 없으니 여기서 맞춘다."""
    sent = []
    agent.send_threadsafe = sent.append
    sid = agent._new_session(1, "fake", "p", "u")["session_id"]
    agent._start_turn({"type": "session.prompt", "rid": 7, "session_id": sid, "text": "옛 서버 요청", "attachments": []})
    _wait(lambda: sent)
    assert sent[0] == {"type": "response", "rid": 7, "result": {"stop_reason": "end_turn"}}
    assert not any(m["type"] == "agent.turn_end" for m in _outbox(agent))


def test_old_server_without_acks_does_not_pile_up_or_replay(agent):
    agent.outbox.add({"type": "agent.event", "session_id": "s", "update": {}})
    agent._sent_seq = 1

    async def settings():
        await agent.handle({"type": "agent.settings", "auto_update": False})
    asyncio.run(settings())
    assert agent.server_acks is False and len(agent.outbox) == 0

    async def pump():
        class Ws:
            def __init__(self):
                self.got = []

            async def send(self, text):
                self.got.append(text)
        ws = Ws()
        agent._out_wake = asyncio.Event()
        agent.outbox.add({"type": "agent.event", "session_id": "s", "update": {}})
        task = asyncio.create_task(agent._pump(ws))
        await asyncio.sleep(0.1)
        task.cancel()
        return ws.got
    assert len(asyncio.run(pump())) == 1 and len(agent.outbox) == 0


def test_cli_exit_during_start_explains_cause_and_next_step(tmp_path):
    """stderr 없이 시작하다 끝나면(보안 프로그램이 죽인 경우) 콜론으로 끝나는 빈 문장이 아니라 원인과 할 일을 보인다."""
    r = _runner(tmp_path, label="Claude Code")
    r.command = [sys.executable, "-c", "import sys; sys.exit(3)"]
    try:
        with pytest.raises(AgentError) as e:
            r.new_session(str(tmp_path))
        s = str(e.value)
        assert "Claude Code" in s and "시작하다가 종료" in s and "→" in s and "코드 3" in s and "기록" not in s
    finally:
        r.shutdown()


@pytest.mark.parametrize("mark, want", [("[ratelimit]", "사용 한도"), ("[emptyerr]", "원인을 알리지 않고")])
def test_cli_errors_are_explained(tmp_path, mark, want):
    r = _runner(tmp_path, label="Gemini CLI")
    try:
        sid = r.new_session(str(tmp_path))
        with pytest.raises(AgentError) as e:
            r.prompt(sid, mark)
        s = str(e.value)
        assert "Gemini CLI" in s and want in s and "→" in s and "{'retry'" not in s
    finally:
        r.shutdown()


def test_model_not_found_does_not_drop_live_session(tmp_path):
    """"model … not found"는 세션 문제가 아니다. 살아 있는 세션을 버리면 다음 요청이 session/load를 보낸다(Gemini는 dispose, Claude는 재생)."""
    r = _runner(tmp_path, label="Gemini CLI")
    try:
        sid = r.new_session(str(tmp_path))
        with pytest.raises(AgentError) as e:
            r.prompt(sid, "[modelerr]")
        assert not isinstance(e.value, SessionGone) and sid in r.live_sessions()
        assert "이 세션을 찾지 못했습니다" not in str(e.value) and "gemini-x" in str(e.value)
        assert r.prompt(sid, "다음 요청") == "end_turn"
        assert count(tmp_path / "cli", "session_load") == 0 and count(tmp_path / "cli", "process_start") == 1
    finally:
        r.shutdown()


@pytest.mark.parametrize("text, gone", [
    ("Session not found (-32002)", True),
    ("Session abc-1 not found in project directory (-32603)", True),
    ("Invalid params (-32602) Session not found: abc-1", True),
    ("unknown session (-32603)", True),
    ("resource abc-1 does not exist (-32603)", True),
    ("Internal error: model gemini-x not found (-32603)", False),
    ("ENOENT: no such file or directory, open 'deck.key' (-32603)", False),
    ("gateway 404 not found (-32603)", False),
])
def test_session_missing_is_about_the_session(text, gone):
    from agent.dds_agent.acp_runner import _session_missing

    assert _session_missing(AgentError(text), "abc-1") is gone


def test_outbox_limit_keeps_turn_end_and_reports_dropped():
    box = outbox.Outbox("b", max_bytes=2000)
    for _ in range(100):
        box.add({"type": "agent.event", "session_id": "s", "update": {"x": "y" * 50}})
    box.add({"type": "agent.turn_end", "session_id": "s", "turn_id": "t", "chat_id": 1})
    msgs = [json.loads(t) for _, t in box.after(0)]
    assert msgs[-1]["type"] == "agent.turn_end" and len(msgs) < 101
    assert box.take_dropped("s") == 101 - len(msgs)
    box.add({"type": "agent.permission", "session_id": "s", "request_id": "r", "params": {"x": "y" * 5000}})
    assert any(json.loads(t)["type"] == "agent.turn_end" for _, t in box.after(0))
    assert box.ended_turns()[0]["turn_id"] == "t"


def test_warm_up_starts_last_cli_and_loads_last_session(agent):
    state = Path(agent.s.agents["fake"]["command"][3])
    sid = agent._new_session(1, "fake", "p", "u")["session_id"]
    agent.runners["fake"].shutdown()
    _wait(lambda: not agent.runners["fake"].alive())
    agent.session_runner.pop(sid, None)
    agent.state.remember_session("fake", sid, (1, "p", "u"))
    agent._warm_up()
    assert count(state, "process_start") == 2 and count(state, "session_load") == 1
    assert agent.live_sessions() == [sid]
    assert any(m["type"] == "agent.live" and sid in m["live_sessions"] for m in _outbox(agent))


def test_warm_up_without_state_or_install_starts_nothing(agent):
    state = Path(agent.s.agents["fake"]["command"][3])
    agent._warm_up()
    assert count(state, "process_start") == 0
    agent.state.data["last_agent"] = "gemini"  # 이 PC에 설치됐어도 시험에서 띄우면 안 되는 CLI: 설치 안 된 것처럼 꾸민다
    agent.s.available_agents = lambda: [{"name": "gemini", "installed": False}]
    agent._warm_up()
    assert "gemini" not in agent.runners


def test_warm_up_forgets_session_that_no_longer_exists(agent):
    agent.state.remember_session("fake", "사라진세션", (1, "p", "u"))
    agent._warm_up()
    assert "last_session" not in agent.state.data


def test_state_records_start_times(agent):
    agent._new_session(1, "fake", "p", "u")
    _wait(lambda: agent.state.typical_start_sec("fake") is not None)
    assert agent.hello()["start_times"]["fake"] >= 0
    assert (Path(config.CONFIG_PATH).parent / "state.json").exists()


def test_mcp_timeouts_and_wait_limits(agent):
    assert int(agent.env["MCP_TOOL_TIMEOUT"]) >= 24 * 3600 * 1000 and int(agent.env["MCP_TIMEOUT"]) >= 60_000
    env = {e["name"]: e["value"] for e in agent.mcp_servers(1, "codex")[0]["env"]}
    assert env["DDS_MCP_WAIT_LIMIT_SEC"] == "50"
    env = {e["name"]: e["value"] for e in agent.mcp_servers(1, "gemini")[0]["env"]}
    assert env["DDS_MCP_WAIT_LIMIT_SEC"] == "540"
    env = {e["name"]: e["value"] for e in agent.mcp_servers(1, "claude")[0]["env"]}
    assert "DDS_MCP_WAIT_LIMIT_SEC" not in env
    codex = json.loads(agent._runner_env("codex")["CODEX_CONFIG"])
    assert codex["mcp_servers.dds-cae.tool_timeout_sec"] > 3600 and "CODEX_CONFIG" not in agent._runner_env("claude")


# ---------- 자동 업데이트 ----------
@pytest.fixture
def updating(monkeypatch, agent):
    monkeypatch.setattr(M, "IDLE_SEC", 0.1)
    monkeypatch.setattr(M, "IDLE_POLL_SEC", 0.02)
    monkeypatch.setenv("DDS_AGENT_HOME", "x")
    agent.steps = []
    monkeypatch.setattr(M.updater, "prepare_update", lambda api, v, sha=None: agent.steps.append("prepare"))
    monkeypatch.setattr(M.updater, "activate", lambda v: agent.steps.append("activate"))
    return agent


def test_update_prepares_but_waits_longer_while_cli_is_alive(updating, monkeypatch):
    agent = updating
    monkeypatch.setattr(M, "IDLE_RESTART_SEC", 1.0)
    agent.runners["fake"] = SimpleNamespace(working_in_background=lambda: False, alive=lambda: True, shutdown=lambda: None,
                                            live_sessions=lambda: set())

    async def run():
        agent.schedule_update("99.0.0", "x")
        await asyncio.sleep(0.5)
        assert agent.steps == ["prepare"] and not agent.restart  # 받아 두기는 유휴 5분 뒤 그대로, 다시 시작만 미룬다
        await asyncio.sleep(0.8)
        assert agent.steps == ["prepare", "activate"] and agent.restart
    asyncio.run(run())


def test_update_without_cli_uses_short_wait(updating, monkeypatch):
    agent = updating
    monkeypatch.setattr(M, "IDLE_RESTART_SEC", 60.0)

    async def run():
        agent.schedule_update("99.0.0", "x")
        await asyncio.sleep(0.5)
        assert agent.steps == ["prepare", "activate"]
    asyncio.run(run())


def test_preparing_counts_as_busy(agent):
    assert not agent.busy()
    agent.preparing = 1
    assert agent.busy()
    agent.preparing = 0


# ---------- 컨텍스트 ----------
def test_context_sync_skips_when_unchanged(tmp_path, monkeypatch):
    src, proj = tmp_path / "agent-context", tmp_path / "proj"
    d = src / "skills" / "one"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: one\ndescription: x\n---\n", encoding="utf-8")
    (src / "INSTRUCTIONS.md").write_text("# 규칙\n", encoding="utf-8")
    monkeypatch.setattr(context, "CONTEXT_DIR", src)
    assert context.sync_project_context(proj) is True
    copied = proj / ".claude" / "skills" / "one" / "SKILL.md"
    mark = copied.stat().st_mtime_ns
    time.sleep(0.05)
    assert context.sync_project_context(proj) is False and copied.stat().st_mtime_ns == mark  # 파일 수백 개를 다시 복사하지 않는다
    (d / "more.md").write_text("새 파일", encoding="utf-8")  # 새 Agent 버전
    assert context.sync_project_context(proj) is True and (proj / ".claude" / "skills" / "one" / "more.md").exists()
    (proj / "CLAUDE.md").unlink()  # 사용자가 지웠으면 다시 만든다
    assert context.sync_project_context(proj) is True and (proj / "CLAUDE.md").exists()


@pytest.fixture
def dds_cae(monkeypatch, tmp_path):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "mcp_servers"))
    import dds_cae as mod

    def project_dir(pid):
        return tmp_path / "proj"

    (tmp_path / "proj").mkdir()
    monkeypatch.setattr(mod, "settings", SimpleNamespace(project_dir=project_dir, tools={}, tool_commands={}, tool_env={}, runner="local", hpc={}))
    monkeypatch.setattr(mod, "fresh_tools", lambda: None)
    monkeypatch.setattr(mod, "resolve_pid", lambda pid: 1)
    return mod


def test_long_run_script_returns_running_and_can_be_polled(dds_cae, monkeypatch):
    """CLI의 도구 시간 제한(Codex 60초·Gemini 10분)을 못 바꿔도 긴 스크립트가 실패로 보이지 않고, 두 번 돌지도 않는다."""
    monkeypatch.setenv("DDS_MCP_WAIT_LIMIT_SEC", "1")
    script = "import json, time\ntime.sleep(2.5)\njson.dump({'ok': 1}, open('results.json', 'w'))\n"
    first = dds_cae.run_script("python", script, publish_images=False)
    assert first["status"] == "running" and first["job_id"] and "실패가 아닙니다" in first["note"]
    second = dds_cae.script_status(first["job_id"], wait_sec=0)
    assert second["status"] == "running" and second["job_id"] == first["job_id"]
    for _ in range(20):
        done = dds_cae.script_status(first["job_id"], wait_sec=1)
        if done.get("status") != "running":
            break
    assert done["ok"] and done["results"] == {"ok": 1}
    assert "error" in dds_cae.script_status(first["job_id"])  # 이미 받은 결과


def test_run_script_without_limit_waits_as_before(dds_cae, monkeypatch):
    monkeypatch.delenv("DDS_MCP_WAIT_LIMIT_SEC", raising=False)
    res = dds_cae.run_script("python", "print('끝')\n", publish_images=False)
    assert res["ok"] and "status" not in res


def test_log_helper_reads_events(tmp_path):
    r = _runner(tmp_path)
    try:
        r.warm_up()
        assert [e["ev"] for e in read_log(tmp_path / "cli")][:2] == ["process_start", "initialize"]
    finally:
        r.shutdown()
