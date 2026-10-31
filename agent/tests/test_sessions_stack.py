"""세션 유지 전체 시험: 실제 uvicorn + 실제 PC Agent + 실제 stdio ACP 가짜 CLI. 횟수는 가짜 CLI의 기록으로 잰다.
핵심 원칙: 느린 CLI를 실패시키지 않는다. 세션은 죽었거나 사용자가 끊었을 때만 버린다."""
import json
import os
import re
import time

import pytest

from agent.tests.fake_acp import count, read_log
from agent.tests.stack import Stack, wait_for


@pytest.fixture
def stack(tmp_path):
    s = Stack(tmp_path)
    try:
        yield s.up()
    except Exception:
        print(s.log_tail())
        raise
    finally:
        s.down()


def _counts(s: Stack) -> dict:
    return {ev: count(s.state, ev) for ev in ("process_start", "session_new", "session_load", "prompt")}


def _alive(pid: int) -> bool:
    import ctypes

    handle = ctypes.WinDLL("kernel32").OpenProcess(0x1000, False, pid) if os.name == "nt" else None
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    if not handle:
        return False
    code = ctypes.c_ulong()
    k = ctypes.WinDLL("kernel32")
    k.GetExitCodeProcess(handle, ctypes.byref(code))
    k.CloseHandle(handle)
    return code.value == 259  # STILL_ACTIVE


def test_slow_start_answers_at_once_and_shows_stages(tmp_path):
    """시작이 오래 걸려도 POST는 바로 돌아오고, 메시지·busy·단계가 서버에 먼저 잡힌다(새로고침해도 복원된다)."""
    s = Stack(tmp_path, start_delay=4)
    try:
        s.up()
        chat = s.new_chat()
        t0 = time.time()
        r = s.post_prompt(chat, "느린 시작")
        assert r.status_code == 200 and time.time() - t0 < 2
        st = s.status(chat)
        assert st["busy"] and st["stage"] == "agent_starting" and st["link"] == "ok" and st["started_at"]
        assert [m["content"] for m in s.messages(chat) if m["role"] == "user"] == ["느린 시작"]
        assert s.post_prompt(chat, "또 보냄").status_code == 409  # 시작 중에도 중복 전송은 막는다
        time.sleep(1.5)
        st = s.status(chat)
        assert st["elapsed_sec"] >= 1 and st["stage_detail"] in ("cli_start", "initialize", "context_sync"), st
        s.wait_idle(chat, 60)
        assert "답: " in s.agent_text(chat)
        assert not s.system_notes(chat)
        data = json.loads((s.tmp / "agent" / "state.json").read_text(encoding="utf-8"))
        assert data["last_agent"] == "fake" and data["start_sec"]["fake"] >= 3  # 다음에 "지난번에는 ~" 으로 보여 줄 값
    finally:
        s.down()


def test_follow_up_turns_do_not_restart_or_reload(stack):
    chat = stack.new_chat()
    for i in range(4):
        stack.turn(chat, f"요청 {i}")
    assert _counts(stack) == {"process_start": 1, "session_new": 1, "session_load": 0, "prompt": 4}
    # 첫 요청에만 컨텍스트가 붙는다
    prompts = [e["text"] for e in read_log(stack.state) if e["ev"] == "prompt"]
    assert "[DDS 컨텍스트]" in prompts[0] and all("[DDS 컨텍스트]" not in p for p in prompts[1:])


def test_link_cut_keeps_turn_events_and_session(stack):
    """연결만 끊겨도 CLI는 계속 돌고, 끊긴 동안의 출력은 다시 온다. 재연결에 session/load는 0번이다."""
    chat = stack.new_chat()
    stack.turn(chat, "처음")
    stack.post_prompt(chat, "[slow 4]").raise_for_status()
    wait_for(lambda: "조각3" in stack.agent_text(chat), what="출력 시작")
    stack.proxy.cut()
    st = wait_for(lambda: (lambda x: x if x["link"] == "lost" else None)(stack.status(chat)), what="끊김 표시", timeout=30)
    assert st["busy"] and st["stage"] == "reconnecting"
    time.sleep(2)
    stack.proxy.resume()
    stack.wait_idle(chat, 60)
    text = stack.agent_text(chat)
    pieces = [int(n) for n in re.findall(r"조각(\d+)", text)]
    assert pieces == list(range(1, len(pieces) + 1)), "끊긴 동안의 출력이 빠지거나 겹쳤다"
    assert "답: " in text and not stack.system_notes(chat)
    stack.turn(chat, "이어서")
    assert _counts(stack) == {"process_start": 1, "session_new": 1, "session_load": 0, "prompt": 3}


def test_turn_finished_while_disconnected_is_delivered_once(stack):
    chat = stack.new_chat()
    stack.turn(chat, "처음")
    stack.post_prompt(chat, "[slow 1]").raise_for_status()
    stack.proxy.cut()
    wait_for(lambda: stack.status(chat)["link"] == "lost", what="끊김", timeout=30)
    time.sleep(2.5)  # 끊긴 동안 턴이 끝난다
    stack.proxy.resume()
    stack.wait_idle(chat, 60)
    assert "답: " in stack.agent_text(chat) and not stack.system_notes(chat)


def test_server_restart_restores_running_turn(stack):
    chat = stack.new_chat()
    stack.turn(chat, "처음")
    stack.post_prompt(chat, "[slow 6]").raise_for_status()
    wait_for(lambda: "조각3" in stack.agent_text(chat), what="출력 시작")
    stack.stop_server()
    time.sleep(1)
    stack.start_server()
    stack.login()
    st = wait_for(lambda: (lambda x: x if x["busy"] else None)(stack.status(chat)) or "답: [slow 6]" in stack.agent_text(chat),
                  what="턴 복원", timeout=30)
    if isinstance(st, dict):
        assert st["elapsed_sec"] >= 1  # 시작 시각이 이어진다
    stack.wait_idle(chat, 60)
    assert "답: " in stack.agent_text(chat)
    stack.turn(chat, "이어서")
    c = _counts(stack)
    assert c == {"process_start": 1, "session_new": 1, "session_load": 0, "prompt": 3}, c
    assert not stack.system_notes(chat)


def test_cli_death_then_load_without_replay_in_chat(stack):
    chat = stack.new_chat()
    stack.turn(chat, "처음")
    stack.turn(chat, "[die]")
    notes = stack.system_notes(chat)
    assert len(notes) == 1 and "종료" in notes[0], notes
    stack.turn(chat, "살아났니")  # 죽은 세션으로 대화가 막히지 않는다
    c = _counts(stack)
    assert c["process_start"] == 2 and c["session_load"] == 1 and c["session_new"] == 1, c
    text = stack.agent_text(chat)
    assert "다시 들려줌" not in text and "답: " in text  # session/load가 다시 들려준 기록이 화면에 또 쌓이지 않는다
    last = [e for e in read_log(stack.state) if e["ev"] == "prompt"][-1]
    assert "[이전 대화" not in last["text"]  # load로 이어 붙였으니 요약은 필요 없다


def test_cli_death_without_load_support_new_session_with_recap(stack):
    chat = stack.new_chat()
    stack.turn(chat, "브래킷 두께를 바꿔 줘")
    stack.control(load=False)
    stack.turn(chat, "[die]")
    stack.turn(chat, "다음 요청")
    c = _counts(stack)
    assert c["process_start"] == 2 and c["session_new"] == 2 and c["session_load"] == 0, c
    last = [e for e in read_log(stack.state) if e["ev"] == "prompt"][-1]["text"]
    assert "[DDS 컨텍스트]" in last and "[이전 대화" in last and "브래킷 두께를 바꿔 줘" in last and last.endswith("다음 요청")
    assert sum("새 세션" in n for n in stack.system_notes(chat)) == 1  # 조용히 맥락을 잃지 않게 한 번 안내한다


def test_session_forgotten_by_cli_does_not_block_chat(stack):
    chat = stack.new_chat()
    stack.turn(chat, "처음")
    stack.turn(chat, "[gone]")
    assert any("같은 요청을 다시" in n for n in stack.system_notes(chat))
    stack.turn(chat, "다시")
    assert "답: 다시" in stack.agent_text(chat)
    assert count(stack.state, "session_load") == 1


def test_stop_during_start_sends_no_prompt_and_keeps_cli(tmp_path):
    s = Stack(tmp_path, start_delay=4)
    try:
        s.up()
        chat = s.new_chat()
        s.post_prompt(chat, "시작 중에 멈출 요청").raise_for_status()
        wait_for(lambda: count(s.state, "process_start") == 1, what="CLI 시작")
        t0 = time.time()
        s.c.post(f"/api/sessions/{chat}/cancel").raise_for_status()
        assert not s.status(chat)["busy"] and time.time() - t0 < 2  # 몇 분을 기다리지 않고 바로 멈춘다
        wait_for(lambda: count(s.state, "session_new") == 1, what="세션 준비", timeout=60)
        time.sleep(0.5)
        assert count(s.state, "prompt") == 0  # 세션 준비가 끝나도 프롬프트를 보내지 않는다
        s.turn(chat, "다시 요청")
        c = _counts(s)
        assert c == {"process_start": 1, "session_new": 1, "session_load": 0, "prompt": 1}, c  # CLI를 죽이지 않아 다시 시작 0번
        assert "[DDS 컨텍스트]" in [e for e in read_log(s.state) if e["ev"] == "prompt"][-1]["text"]
    finally:
        s.down()


def test_resend_during_start_after_stop_waits_for_same_session(tmp_path):
    s = Stack(tmp_path, start_delay=3)
    try:
        s.up()
        chat = s.new_chat()
        s.post_prompt(chat, "첫 요청").raise_for_status()
        s.c.post(f"/api/sessions/{chat}/cancel").raise_for_status()
        s.post_prompt(chat, "바로 다시").raise_for_status()
        s.wait_idle(chat, 60)
        c = _counts(s)
        assert c == {"process_start": 1, "session_new": 1, "session_load": 0, "prompt": 1}, c  # 세션이 둘 열리지 않는다
    finally:
        s.down()


def test_stop_ends_only_that_chat(stack):
    a, b = stack.new_chat(), stack.new_chat()
    stack.turn(a, "처음")
    stack.turn(b, "처음")
    stack.post_prompt(a, "[wait]").raise_for_status()
    stack.post_prompt(b, "[perm]").raise_for_status()
    perm = wait_for(lambda: stack.status(b)["pending_permissions"], what="권한 요청")[0]
    stack.c.post(f"/api/sessions/{a}/cancel").raise_for_status()
    stack.wait_idle(a, 30)
    assert stack.status(b)["pending_permissions"] and stack.status(b)["busy"]  # A를 멈춰도 B의 권한 요청은 그대로다
    stack.c.post(f"/api/sessions/{b}/permission", json={"request_id": perm["request_id"], "option_id": "allow"}).raise_for_status()
    stack.wait_idle(b, 30)
    assert "allow" in stack.agent_text(b)


def test_hung_tool_force_restart_ends_cli_and_chat_recovers(stack):
    """멈춘 도구: 중지가 듣지 않아도 '다시 시작'으로 그 CLI가 끝난다. 사용자가 끊는 유일한 길이고, 다음 요청은 이어진다."""
    chat, other = stack.new_chat(), stack.new_chat()
    stack.turn(other, "다른 대화")
    stack.post_prompt(chat, "[hang]").raise_for_status()
    wait_for(lambda: stack.status(chat)["stage"] == "tool", what="도구 실행")
    pid = next(e["pid"] for e in read_log(stack.state) if e["ev"] == "process_start")
    solver = next(e["child"] for e in read_log(stack.state) if e["ev"] == "child")
    assert _alive(solver)
    stack.c.post(f"/api/sessions/{chat}/cancel").raise_for_status()
    st = stack.status(chat)
    assert st["busy"] and st["cancelling"] and st["cancel_elapsed_sec"] is not None
    time.sleep(1)
    assert stack.status(chat)["busy"]  # CLI가 답하지 않으면 끝나지 않는다(그래서 화면에 버튼이 필요하다)
    stack.c.post(f"/api/sessions/{chat}/force-restart").raise_for_status()
    assert not stack.status(chat)["busy"]
    wait_for(lambda: not _alive(pid), what="멈춘 CLI 끝남", timeout=20)
    wait_for(lambda: not _alive(solver), what="그 아래 멈춘 프로세스도 끝남", timeout=20)
    wait_for(lambda: count(stack.state, "process_start") == 2, what="미리 다시 띄움", timeout=30)
    tool = next(m for m in stack.messages(chat) if m["role"] == "tool")
    assert tool["meta"]["status"] == "failed"  # 기록에 '실행 중'이 영원히 남지 않는다
    notes = stack.system_notes(chat)
    assert len(notes) == 1 and "다시 시작" in notes[0], notes  # CLI가 바로 죽어도 안내는 하나다
    assert stack.status(other)["busy"] is False and not stack.system_notes(other)  # 일하지 않던 대화에는 안내가 없다
    stack.turn(chat, "다시 시작 뒤")
    assert "답: 다시 시작 뒤" in stack.agent_text(chat)
    c = _counts(stack)
    assert c["process_start"] == 2 and c["session_new"] == 2 and c["session_load"] >= 1, c
    assert stack.c.post(f"/api/sessions/{chat}/force-restart").status_code == 409  # 진행 중이 아니면 끊을 것이 없다


def test_force_restart_on_half_open_link_reaches_the_agent_after_reconnect(stack):
    """'중지'에 답이 없던 이유가 반쯤 끊긴 연결일 수 있다. 보내기는 성공하고 메시지는 사라지므로, 받았다는 답이 없으면 다시 연결될 때 보낸다."""
    chat = stack.new_chat()
    stack.post_prompt(chat, "[hang]").raise_for_status()
    wait_for(lambda: stack.status(chat)["stage"] == "tool", what="도구 실행")
    pid = next(e["pid"] for e in read_log(stack.state) if e["ev"] == "process_start")
    solver = next(e["child"] for e in read_log(stack.state) if e["ev"] == "child")
    stack.proxy.freeze()
    stack.c.post(f"/api/sessions/{chat}/cancel").raise_for_status()
    stack.c.post(f"/api/sessions/{chat}/force-restart").raise_for_status()
    assert not stack.status(chat)["busy"]  # 대화는 바로 풀린다
    assert _alive(pid)  # 아직 전달되지 않았다
    wait_for(lambda: not _alive(pid), what="다시 연결된 뒤 멈춘 CLI 끝남", timeout=120)
    wait_for(lambda: not _alive(solver), what="그 아래 멈춘 프로세스도 끝남", timeout=20)
    stack.turn(chat, "이어서")
    last = [e for e in read_log(stack.state) if e["ev"] == "prompt"][-1]
    assert last["pid"] != pid and "이어서" in last["text"]


def test_force_restart_then_immediate_prompt_goes_to_new_cli(stack):
    """다시 시작을 누르고 곧바로 보내도 죽어 가는 CLI로 가지 않는다."""
    chat = stack.new_chat()
    stack.post_prompt(chat, "[hang]").raise_for_status()
    wait_for(lambda: stack.status(chat)["stage"] == "tool", what="도구 실행")
    pid = next(e["pid"] for e in read_log(stack.state) if e["ev"] == "process_start")
    stack.c.post(f"/api/sessions/{chat}/cancel").raise_for_status()
    stack.c.post(f"/api/sessions/{chat}/force-restart").raise_for_status()
    stack.turn(chat, "바로 보낸 요청")
    assert "답: 바로 보낸 요청" in stack.agent_text(chat)
    last = [e for e in read_log(stack.state) if e["ev"] == "prompt"][-1]
    assert last["pid"] != pid and last["known"]
    assert len(stack.system_notes(chat)) == 1, stack.system_notes(chat)
    wait_for(lambda: not _alive(pid), what="옛 CLI 끝남", timeout=20)


@pytest.mark.parametrize("mark", ["[bigperm]", "[bigimg]"])
def test_oversized_message_does_not_loop_the_link(stack, mark):
    """한도를 넘는 메시지 하나가 재연결마다 다시 나가 연결이 영원히 끊기던 문제. 큰 입력 덱을 고치는 권한 요청에서 실제로 난다."""
    chat, other = stack.new_chat(), stack.new_chat()
    stack.post_prompt(chat, mark).raise_for_status()
    if mark == "[bigperm]":
        wait_for(lambda: stack.status(chat)["pending_permissions"], what="권한 카드", timeout=60)
        perm = stack.status(chat)["pending_permissions"][0]
        assert perm["title"] == "입력 덱 수정"
        stack.c.post(f"/api/sessions/{chat}/permission", json={"request_id": perm["request_id"], "option_id": "allow"}).raise_for_status()
    stack.wait_idle(chat, 60)
    stack.turn(other, "다른 대화")
    assert "다른 대화" in stack.agent_text(other)
    agent_log = (stack.tmp / "agent.log").read_text(encoding="utf-8", errors="replace")
    assert agent_log.count("서버에 연결됨") == 1, agent_log[-2000:]


def test_stop_during_start_then_folder_change_uses_new_folder(tmp_path):
    """시작이 느릴 때 폴더를 잘못 골라 중지하고 폴더를 바꾼 경우: 다음 요청은 새 폴더에서 돈다."""
    s = Stack(tmp_path, start_delay=4)
    try:
        s.up()
        chat = s.new_chat()
        s.post_prompt(chat, "옛 폴더 요청").raise_for_status()
        s.c.post(f"/api/sessions/{chat}/cancel").raise_for_status()
        new_dir = tmp_path / "new-folder"
        s.c.put(f"/api/projects/{s.pid}/local/folder", json={"path": str(new_dir)}).raise_for_status()
        wait_for(lambda: count(s.state, "session_new") >= 1, what="옛 준비 끝남", timeout=30)
        time.sleep(0.5)
        s.turn(chat, "새 폴더 요청")
        sessions = [e for e in read_log(s.state) if e["ev"] == "session_new"]
        prompt = [e for e in read_log(s.state) if e["ev"] == "prompt"][-1]
        cwd = next(e["cwd"] for e in sessions if e["sid"] == prompt["sid"])
        assert str(new_dir) in cwd, (cwd, sessions)
    finally:
        s.down()


def test_model_error_keeps_session_without_reload(stack):
    chat = stack.new_chat()
    stack.turn(chat, "첫 요청")
    stack.turn(chat, "[modelerr]")
    stack.turn(chat, "다음")
    c = _counts(stack)
    assert c["session_load"] == 0 and c["process_start"] == 1, c
    notes = stack.system_notes(chat)
    assert len(notes) == 1 and "gemini-x" in notes[0] and "→" in notes[0]


def test_permission_resent_after_server_restart(stack):
    chat = stack.new_chat()
    stack.post_prompt(chat, "[perm]").raise_for_status()
    perm = wait_for(lambda: stack.status(chat)["pending_permissions"], what="권한 요청")[0]
    stack.stop_server()
    time.sleep(1)
    stack.start_server()
    stack.login()
    again = wait_for(lambda: stack.status(chat)["pending_permissions"], what="권한 요청 복원", timeout=30)
    assert [p["request_id"] for p in again] == [perm["request_id"]]  # 복원되고 중복은 없다
    stack.c.post(f"/api/sessions/{chat}/permission", json={"request_id": perm["request_id"], "option_id": "allow"}).raise_for_status()
    stack.wait_idle(chat, 30)
    assert "allow" in stack.agent_text(chat)


def test_agent_restart_warms_up_and_loads_last_session(stack):
    chat = stack.new_chat()
    stack.turn(chat, "처음")
    stack.stop_agent()
    wait_for(lambda: not stack.agent_connected(), what="Agent 끊김", timeout=30)
    stack.start_agent()
    wait_for(lambda: count(stack.state, "session_load") == 1, what="미리 불러오기", timeout=30)  # 요청 전에 이미 끝나 있다
    assert count(stack.state, "process_start") == 2
    stack.turn(chat, "재시작 뒤")
    c = _counts(stack)
    assert c == {"process_start": 2, "session_new": 1, "session_load": 1, "prompt": 2}, c  # 프롬프트 때 다시 뜨거나 다시 load하지 않는다


def test_agent_restart_mid_turn_ends_once(stack):
    chat = stack.new_chat()
    stack.turn(chat, "처음")
    stack.post_prompt(chat, "[wait]").raise_for_status()
    wait_for(lambda: "기다립니다" in stack.agent_text(chat), what="턴 시작")
    stack.kill_agent()  # Agent만 끝낸다. 트리째 끝내면 CLI가 먼저 죽어 runner_exit로 턴이 끝나는 경우가 섞인다
    wait_for(lambda: stack.status(chat)["link"] == "lost", what="끊김", timeout=30)
    assert stack.status(chat)["busy"]  # 끊긴 것만으로 턴을 버리지 않는다
    stack.start_agent()
    stack.wait_idle(chat, 30)
    notes = stack.system_notes(chat)
    assert len(notes) == 1 and "다시 시작" in notes[0], notes
    stack.turn(chat, "이어서")
    assert "답: 이어서" in stack.agent_text(chat)


def test_big_tool_event_is_cut_and_link_survives(stack):
    chat = stack.new_chat()
    stack.post_prompt(chat, "[big]").raise_for_status()
    stack.wait_idle(chat, 90)
    assert "답: " in stack.agent_text(chat)
    assert not stack.system_notes(chat)
    tool = next(m for m in stack.messages(chat) if m["role"] == "tool")
    assert tool["meta"]["status"] == "completed"
    assert len(json.dumps(tool["meta"], ensure_ascii=False)) < 200_000
    agent_log = (stack.tmp / "agent.log").read_text(encoding="utf-8", errors="replace")
    assert "서버 연결 끊김" not in agent_log
