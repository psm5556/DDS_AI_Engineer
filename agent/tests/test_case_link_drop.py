"""연결이 잠깐 끊겨도 해석은 계속된다: 실제 서버 + 실제 PC Agent + 실제 스크립트 솔버 + 연결만 끊는 중계.
끊긴 동안 다른 멤버가 멈출 수 없고, 다시 연결돼도 솔버가 끝나지 않으며, 끊긴 사이에 끝난 결과도 잃지 않는다."""
import json
from pathlib import Path

import httpx
import pytest

from agent.dds_agent.outbox import Outbox
from agent.tests.stack import Stack, wait_for
from agent.tests.test_sessions_stack import _alive

SOLVER = """import json, os, pathlib, time
base = pathlib.Path({base!r})
(base / "pid").write_text(str(os.getpid()))
end = time.time() + 180
while not (base / "go").exists() and time.time() < end:
    time.sleep(0.1)
p = json.load(open("params.json"))
json.dump({{"y": p["x"] + 1}}, open("results.json", "w"))
(base / "done").write_text("1")
"""


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


def _member(s: Stack) -> httpx.Client:
    me = s.c.post("/api/auth/register", json={"username": "kim", "pin": "123456", "display_name": "김멤버"}).json()
    assert s.c.put(f"/api/users/{me['id']}", json={"is_active": True}).status_code == 200
    c = httpx.Client(base_url=s.base, timeout=30)
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": "kim", "pin": "123456"}).json()["access_token"]
    assert s.c.put(f"/api/projects/{s.pid}/members/{me['id']}", json={"role": "member"}).status_code == 200
    return c


def _started(s: Stack, name: str) -> tuple[int, int, Path]:
    """케이스 하나를 돌려 솔버가 뜰 때까지 기다린다. 'go' 파일을 만들 때까지 끝나지 않는다."""
    base = s.tmp / name
    base.mkdir()
    script = base / "solver.py"
    script.write_text(SOLVER.format(base=str(base)), encoding="utf-8")
    sid = s.c.post(f"/api/projects/{s.pid}/studies", json={
        "name": name, "adapter": "script", "adapter_config": {"tool": "python", "script": str(script)},
        "variables": [{"name": "x", "lower": 0, "upper": 1}], "objectives": [{"name": "y", "goal": "min"}]}).json()["id"]
    assert s.c.post(f"/api/studies/{sid}/doe", json={"n": 1, "method": "lhs", "seed": 1}).status_code == 200
    assert s.c.post(f"/api/studies/{sid}/run", json={}).json() == {"queued": 1}
    case_id = s.c.get(f"/api/studies/{sid}/cases").json()[0]["id"]
    wait_for(lambda: (base / "pid").exists(), what="솔버 시작")
    wait_for(lambda: _case(s, sid)["status"] == "running", what="실행 중 표시")
    return sid, case_id, base


def _case(s: Stack, sid: int) -> dict:
    return s.c.get(f"/api/studies/{sid}/cases").json()[0]


def _drop(s: Stack, sid: int):
    s.proxy.cut()
    wait_for(lambda: s.c.get(f"/api/studies/{sid}").json()["stalled"] == 1, what="끊김 표시")


def _back(s: Stack):
    s.proxy.resume()
    wait_for(s.agent_connected, what="다시 연결")


def test_brief_drop_never_ends_the_solver(stack):
    s = stack
    member = _member(s)
    try:
        # 1) 끊긴 동안 멤버는 멈출 수 없고, 다시 연결돼도 솔버가 계속 돌아 결과가 들어온다
        sid, _, base = _started(s, "s1")
        pid = int((base / "pid").read_text())
        _drop(s, sid)
        r = member.post(f"/api/studies/{sid}/stop", json={})
        assert r.status_code == 403 and "이 작업을 내 PC에서 이어서 하기" in r.json()["detail"]
        _back(s)
        wait_for(lambda: s.c.get(f"/api/studies/{sid}").json()["stalled"] == 0, what="끊김 표시 해제")
        assert _alive(pid) and _case(s, sid)["status"] == "running", s.log_tail()
        (base / "go").write_text("1")
        wait_for(lambda: _case(s, sid)["status"] == "complete", what="결과 도착")
        got = _case(s, sid)
        assert "y" in got["results"] and not got["error"]

        # 2) 끊긴 사이에 끝난 해석의 결과도 다시 연결되면 들어온다. 전에는 사라지고 '끝나지 못했습니다'로 남았다
        sid, _, base = _started(s, "s2")
        _drop(s, sid)
        (base / "go").write_text("1")
        wait_for(lambda: (base / "done").exists(), what="끊긴 사이에 해석 끝")
        _back(s)
        wait_for(lambda: _case(s, sid)["status"] == "complete", timeout=30, what="끊긴 사이의 결과 도착")
        got = _case(s, sid)
        assert "y" in got["results"] and not got["error"], got

        # 3) 실행한 사람이 끊긴 동안 중지하면 다시 연결될 때 솔버가 실제로 끝나고, 누가 멈췄는지 남는다
        sid, _, base = _started(s, "s3")
        pid = int((base / "pid").read_text())
        _drop(s, sid)
        r = s.c.post(f"/api/studies/{sid}/stop", json={})
        assert r.status_code == 200 and r.json()["released"] == 1
        assert _alive(pid)
        _back(s)
        wait_for(lambda: not _alive(pid), timeout=30, what="주인이 중지한 솔버 종료")
        got = _case(s, sid)
        assert got["status"] == "cancelled" and "관리자" in got["error"] and not got["results"]

        # 4) 끝낸 결과를 전하기 전에 Agent가 다시 시작했다. PC의 case.json이 끝났다고 하면 중지로 바꾸지 않는다
        sid, _, base = _started(s, "s4")
        _drop(s, sid)
        (base / "go").write_text("1")

        def recorded():
            for p in (s.tmp / "ws").glob("P*/studies/S*_s4/cases/*/case.json"):
                try:
                    return json.loads(p.read_text(encoding="utf-8")).get("status") == "complete"
                except (OSError, ValueError):
                    return False
            return False
        wait_for(recorded, what="PC에 끝난 기록")
        s.kill_agent()
        s.proxy.resume()
        s.start_agent()
        seen = []

        def settled():
            seen.append(_case(s, sid)["status"])
            return seen[-1] not in ("queued", "running")
        wait_for(settled, timeout=30, what="다시 시작 뒤 결과")
        got = _case(s, sid)
        assert "cancelled" not in seen and got["status"] == "complete" and "y" in got["results"] and not got["error"], (seen, got)
    finally:
        member.close()


def test_status_waits_in_the_outbox_and_counts_as_active_until_delivered():
    """결과를 전하기 전에는 '이 PC가 아는 케이스'다. hello에서 빠지면 서버가 끝나지 못한 것으로 친다."""
    box = Outbox("boot")
    seq = box.add({"type": "case.status", "case_id": 7, "status": "complete", "results": {"y": 1}})
    box.add({"type": "agent.event", "session_id": "s", "update": {}})
    assert box.pending_cases() == [7]
    assert json.loads(box.after(0)[0][1])["case_id"] == 7
    box.ack(seq)
    assert box.pending_cases() == []


def test_oversized_case_status_still_says_which_case_failed():
    """결과가 한도를 넘어도 케이스 번호를 잃으면 서버가 어느 케이스인지 몰라 '실행 중'으로 남는다."""
    box = Outbox("boot")
    box.add({"type": "case.status", "case_id": 9, "status": "complete", "results": {f"k{i}": float(i) for i in range(1_500_000)}})
    sent = json.loads(box.after(0)[0][1])
    assert sent["case_id"] == 9 and sent["status"] == "failed" and "→" in sent["error"] and "results" not in sent
