"""세션 유지·대기 표시 E2E: 실제 uvicorn + 실제 PC Agent + 가짜 ACP CLI(agent/tests/fake_acp.py) + 창 없는 브라우저.
실제 Claude·Gemini·Codex와 data/를 건드리지 않고, 7014·8000 포트를 쓰지 않는다. 화면 점검에는 npm run build 결과가 필요하다.

실행: .venv\\Scripts\\python scripts\\e2e_sessions.py [--shots 폴더] [--no-browser] [--start-delay 12]
재는 것(가짜 CLI 기록): CLI 프로세스 시작·session/new·session/load 횟수. 후속 요청·재연결·서버 재시작에서 늘지 않아야 한다.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.tests.fake_acp import count, read_log  # noqa: E402
from agent.tests.stack import Stack, wait_for  # noqa: E402
from cae_adapters.hidden import hidden_kwargs  # noqa: E402


def counts(s: Stack) -> dict:
    return {ev: count(s.state, ev) for ev in ("process_start", "session_new", "session_load", "prompt")}


def check(cond, what: str):
    if not cond:
        raise AssertionError(what)
    print(f"  OK {what}")


def scenario_api(s: Stack, report: dict, start_delay: float):
    print("1) 느린 시작: POST는 바로 돌아오고 단계가 보인다")
    base = counts(s)
    s.control(start_delay=start_delay)
    s.stop_agent()
    wait_for(lambda: not s.agent_connected(), what="Agent 끊김", timeout=30)
    (s.tmp / "agent" / "state.json").unlink(missing_ok=True)  # 미리 띄우기 없이 첫 요청이 시작 비용을 지는 경우
    s.start_agent()
    chat = s.new_chat()
    t0 = time.time()
    s.post_prompt(chat, "느린 시작").raise_for_status()
    report["post_sec_during_slow_start"] = round(time.time() - t0, 3)
    st = s.status(chat)
    check(report["post_sec_during_slow_start"] < 2, f"POST가 CLI를 기다리지 않음 ({report['post_sec_during_slow_start']}초, CLI 시작 {start_delay}초)")
    check(st["busy"] and st["stage"] == "agent_starting", f"시작 전에 busy·단계가 잡힘 (단계 {st['stage']})")
    check(s.post_prompt(chat, "중복").status_code == 409, "시작 중 중복 전송은 409")
    s.wait_idle(chat, start_delay + 60)
    check(not s.system_notes(chat), "오류 없이 끝남")
    data = json.loads((s.tmp / "agent" / "state.json").read_text(encoding="utf-8"))
    report["measured_start_sec"] = data["start_sec"]["fake"]
    check(report["measured_start_sec"] >= start_delay - 1, f"시작 시간을 잼 ({report['measured_start_sec']}초)")
    s.control(start_delay=0)

    print("2) 후속 요청: CLI 재시작 0회, load 0회")
    before = counts(s)
    for i in range(3):
        s.turn(chat, f"후속 {i}")
    after = counts(s)
    report["follow_up"] = {"turns": 3, "process_starts": after["process_start"] - before["process_start"],
                           "session_new": after["session_new"] - before["session_new"], "session_load": after["session_load"] - before["session_load"]}
    check(report["follow_up"] == {"turns": 3, "process_starts": 0, "session_new": 0, "session_load": 0}, f"후속 3턴 {report['follow_up']}")

    print("3) 재연결: 턴·이벤트 유지, load 0회")
    before = counts(s)
    s.post_prompt(chat, "[slow 4] 끊김").raise_for_status()
    wait_for(lambda: "조각2" in s.agent_text(chat), what="출력 시작")
    s.proxy.cut()
    wait_for(lambda: s.status(chat)["link"] == "lost", what="끊김 표시", timeout=30)
    check(s.status(chat)["busy"] and s.status(chat)["stage"] == "reconnecting", "끊겨도 턴을 버리지 않음")
    time.sleep(2)
    s.proxy.resume()
    s.wait_idle(chat, 60)
    check("답: [slow 4] 끊김" in s.agent_text(chat), "끊긴 동안의 출력까지 모두 도착")
    s.turn(chat, "재연결 뒤")
    after = counts(s)
    report["reconnect"] = {"process_starts": after["process_start"] - before["process_start"], "session_load": after["session_load"] - before["session_load"]}
    check(report["reconnect"] == {"process_starts": 0, "session_load": 0}, f"재연결 {report['reconnect']}")

    print("4) 서버 재시작: 턴 복원, load 0회")
    before = counts(s)
    # 다시 시작과 재연결 대기보다 길게 돈다. 그 안에 끝나면 복원할 턴이 없어 이 시험이 볼 것이 없다
    s.post_prompt(chat, "[slow 25] 서버 재시작").raise_for_status()
    wait_for(lambda: s.status(chat)["stage"] == "thinking" and "서버 재시작" not in s.agent_text(chat), what="출력 시작")
    time.sleep(1)
    s.stop_server()
    time.sleep(1)
    s.start_server()
    s.login()
    st = wait_for(lambda: (lambda x: x if x["busy"] else None)(s.status(chat)), what="턴 복원", timeout=30)
    report["restored_elapsed_sec"] = st["elapsed_sec"]
    check(st["elapsed_sec"] >= 1, f"경과 시간이 이어짐 ({st['elapsed_sec']}초)")
    s.wait_idle(chat, 60)
    check("답: [slow 25] 서버 재시작" in s.agent_text(chat), "서버 재시작 뒤 턴이 끝까지 도착")
    s.turn(chat, "서버 재시작 뒤")
    after = counts(s)
    report["server_restart"] = {"process_starts": after["process_start"] - before["process_start"], "session_load": after["session_load"] - before["session_load"]}
    check(report["server_restart"] == {"process_starts": 0, "session_load": 0}, f"서버 재시작 {report['server_restart']}")

    print("5) CLI 사망: 다음 요청은 재시작 + load, 대화가 막히지 않음")
    before = counts(s)
    s.turn(chat, "[die]")
    check(sum("종료" in n for n in s.system_notes(chat)) == 1, "안내는 한 번")
    s.turn(chat, "살아났니")
    after = counts(s)
    report["cli_death"] = {"process_starts": after["process_start"] - before["process_start"], "session_load": after["session_load"] - before["session_load"],
                           "session_new": after["session_new"] - before["session_new"]}
    check(report["cli_death"] == {"process_starts": 1, "session_load": 1, "session_new": 0}, f"CLI 사망 {report['cli_death']}")
    check("답: 살아났니" in s.agent_text(chat) and "다시 들려줌" not in s.agent_text(chat), "load가 다시 들려준 기록이 쌓이지 않음")

    print("6) load를 지원하지 않는 CLI: 새 세션 + 지난 대화 요약 + 안내 한 번")
    s.control(load=False)
    before = counts(s)
    s.turn(chat, "[die]")
    s.turn(chat, "요약 받았니")
    after = counts(s)
    s.control(load=True)
    last = [e for e in read_log(s.state) if e["ev"] == "prompt"][-1]["text"]
    report["no_load_support"] = {"session_new": after["session_new"] - before["session_new"], "recap": "[이전 대화" in last}
    check(report["no_load_support"] == {"session_new": 1, "recap": True}, f"새 세션 {report['no_load_support']}")
    check(sum("새 세션" in n for n in s.system_notes(chat)) == 1, "새 세션 안내는 한 번")

    print("7) Agent 재시작: 마지막 CLI·대화를 미리 띄움")
    chat2 = s.new_chat()
    s.turn(chat2, "미리 띄울 대화")
    s.stop_agent()
    wait_for(lambda: not s.agent_connected(), what="Agent 끊김", timeout=30)
    before = counts(s)
    s.start_agent()
    wait_for(lambda: count(s.state, "session_load") > before["session_load"], what="미리 불러오기", timeout=30)
    warmed = counts(s)
    s.turn(chat2, "재시작 뒤")
    after = counts(s)
    report["warm_up"] = {"started_before_prompt": warmed["process_start"] - before["process_start"], "loaded_before_prompt": warmed["session_load"] - before["session_load"],
                         "extra_starts_at_prompt": after["process_start"] - warmed["process_start"], "extra_loads_at_prompt": after["session_load"] - warmed["session_load"]}
    check(report["warm_up"] == {"started_before_prompt": 1, "loaded_before_prompt": 1, "extra_starts_at_prompt": 0, "extra_loads_at_prompt": 0}, f"미리 띄우기 {report['warm_up']}")

    print("8) 시작 중 중지: 프롬프트 0개, CLI 유지")
    s.control(start_delay=4)
    s.turn(chat2, "[die]")
    chat3 = s.new_chat()
    before = counts(s)
    s.post_prompt(chat3, "멈출 요청").raise_for_status()
    wait_for(lambda: count(s.state, "process_start") > before["process_start"], what="CLI 시작")
    s.c.post(f"/api/sessions/{chat3}/cancel").raise_for_status()
    check(not s.status(chat3)["busy"], "중지가 바로 끝남")
    wait_for(lambda: count(s.state, "session_new") > before["session_new"], what="세션 준비", timeout=60)
    time.sleep(0.5)
    check(count(s.state, "prompt") == before["prompt"], "준비가 끝나도 프롬프트를 보내지 않음")
    s.control(start_delay=0)
    s.turn(chat3, "다시 요청")
    after = counts(s)
    report["stop_during_start"] = {"process_starts": after["process_start"] - before["process_start"], "session_new": after["session_new"] - before["session_new"], "prompts": after["prompt"] - before["prompt"]}
    check(report["stop_during_start"] == {"process_starts": 1, "session_new": 1, "prompts": 1}, f"시작 중 중지 {report['stop_during_start']}")


def scenario_browser(s: Stack, shots: Path, start_delay: float, report: dict):
    print("9) 화면: 대기 말풍선·새로고침·재연결·다시 시작 (창 없는 브라우저)")
    s.control(start_delay=start_delay)
    s.stop_agent()
    wait_for(lambda: not s.agent_connected(), what="Agent 끊김", timeout=30)
    (s.tmp / "agent" / "state.json").write_text(json.dumps({"start_sec": {"fake": 192.0}}), encoding="utf-8")  # "지난번에는" 문구 확인
    s.start_agent()
    env = {**s.env, "DDS_URL": s.base, "DDS_PID": str(s.pid), "DDS_SHOTS": str(shots)}
    node = subprocess.Popen(["node", "e2e/waiting.mjs"], cwd=ROOT / "frontend", env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, **hidden_kwargs())
    ok = False
    report["shots"] = []
    for line in node.stdout:
        line = line.rstrip()
        print("   ", line)
        if line.startswith("STEP cut"):
            s.control(start_delay=0)
            s.proxy.cut()
            node.stdin.write("ok\n")
            node.stdin.flush()
        elif line.startswith("STEP resume"):
            time.sleep(1)
            s.proxy.resume()
            node.stdin.write("ok\n")
            node.stdin.flush()
        elif line.startswith("STEP server_down"):
            s.stop_server()
            node.stdin.write("ok\n")
            node.stdin.flush()
        elif line.startswith("STEP server_up"):
            s.start_server()
            s.login()
            node.stdin.write("ok\n")
            node.stdin.flush()
        elif line.startswith("SHOT "):
            report["shots"].append(line[5:])
        elif line.startswith("PASS"):
            ok = True
    node.wait(timeout=30)
    check(ok, "화면 점검 통과")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--shots", default=str(ROOT / "frontend" / "e2e" / "shots" / "sessions"))
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--start-delay", type=float, default=8.0)
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="dds_e2e_sessions_"))
    s = Stack(tmp)
    report: dict = {}
    t0 = time.time()
    try:
        s.up()
        print(f"서버 {s.base} · 임시 폴더 {tmp}")
        scenario_api(s, report, a.start_delay)
        if not a.no_browser:
            scenario_browser(s, Path(a.shots), a.start_delay, report)
        report["total"] = counts(s)
        report["seconds"] = round(time.time() - t0)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        print("E2E 통과")
    except Exception:
        print(s.log_tail(6000))
        raise
    finally:
        s.down()


if __name__ == "__main__":
    main()
