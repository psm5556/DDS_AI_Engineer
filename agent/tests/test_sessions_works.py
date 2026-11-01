"""Task마다 폴더 하나: 실제 서버 + 실제 PC Agent + 가짜 CLI로 잰다. 횟수는 가짜 CLI의 기록으로 본다.
복합 상황을 막는다: Task를 옮겨 다녀도 CLI를 다시 띄우지 않는다, 세션 중 이름 변경에 끊기지 않는다, 옛 Agent는 Project 폴더를 쓴다."""
import json
import os
from pathlib import Path

import pytest

from agent.tests.fake_acp import count, read_log
from agent.tests.stack import Stack, wait_for
from agent.tests.test_folder_names import _hidden, _is_alias, _holders_dead, windows_only

_n = [0]


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    s = Stack(tmp_path_factory.mktemp("works"))
    try:
        yield s.up()
    finally:
        s.down()


@pytest.fixture
def s(shared):
    """Project는 시험마다 새로 만든다. CLI는 그대로이므로 시작 횟수는 차이로 잰다."""
    _n[0] += 1
    shared.pid = shared.c.post("/api/projects", json={"name": f"부품{_n[0]}"}).json()["id"]
    return shared


def _task(s: Stack, name: str | None = None) -> dict:
    r = s.c.post(f"/api/projects/{s.pid}/works", json={"name": name, "name_auto": name is None, "agent": s.cli_name})
    r.raise_for_status()
    return r.json()


def _chat(work: dict) -> int:
    return work["active_chat_id"]


def _new_cwds(s: Stack, since: int = 0) -> list[str]:
    return [e["cwd"] for e in read_log(s.state)[since:] if e["ev"] == "session_new"]


def _project_folder(s: Stack) -> Path:
    return next((s.tmp / "ws").glob(f"P{s.pid:04d}_*"))


def test_new_task_runs_in_its_own_folder(s):
    w = _task(s, "1차 경량화")
    mark = len(read_log(s.state))
    s.turn(_chat(w), "시작")
    cwd = Path(_new_cwds(s, mark)[0])
    assert cwd.name == "T01_1차-경량화" and cwd.parent == _project_folder(s)
    assert json.loads((cwd / "work.json").read_text(encoding="utf-8"))["uid"] == w["uid"]
    assert (cwd.parent / ".claude" / "skills").is_dir() and not (cwd / ".claude").exists(), "스킬은 Project 폴더에 1벌이다"
    assert (cwd.parent / "CLAUDE.md").is_file() and not (cwd / "CLAUDE.md").exists()
    assert (cwd / "AGENTS.md").is_file() and (cwd / "GEMINI.md").is_file()
    dirs = [e for e in read_log(s.state) if e["ev"] == "session_dirs"][-1]
    assert dirs["meta"] == [str(cwd.parent)], "부모 폴더(스킬 1벌)를 세션에 함께 넘긴다"


def test_switching_tasks_never_restarts_cli(s):
    starts = count(s.state, "process_start")
    a, b = _task(s, "하나"), _task(s, "둘")
    mark = len(read_log(s.state))
    s.turn(_chat(a), "첫째")
    s.turn(_chat(b), "둘째")
    cwds = [Path(c).name for c in _new_cwds(s, mark)]
    assert cwds == ["T01_하나", "T02_둘"]
    s.turn(_chat(a), "다시 첫째")
    s.turn(_chat(b), "다시 둘째")
    log = read_log(s.state)[mark:]
    assert count(s.state, "process_start") == starts, "Task를 옮겨 다녀도 CLI를 다시 띄우지 않는다"
    assert sum(e["ev"] == "session_new" for e in log) == 2 and sum(e["ev"] == "session_load" for e in log) == 0


def test_legacy_task_runs_in_project_folder(s):
    chat = s.new_chat()
    s.make_legacy(chat)
    mark = len(read_log(s.state))
    s.turn(chat, "이어서")
    cwd = Path(_new_cwds(s, mark)[0])
    assert cwd == _project_folder(s) and not list(cwd.glob("T*")), "옛 폴더를 건드리지 않고 새 폴더도 만들지 않는다"


def test_mcp_sees_the_task(s):
    """실제 MCP 코드를 세션 환경으로 돌린다: 폴더와 Task가 서버까지 이어진다."""
    w = _task(s)
    s.turn(_chat(w), "[where] 시작")
    cwd = _new_cwds(s)[-1]
    assert cwd.replace("\\", "\\\\") in s.agent_text(_chat(w))
    # 헤더가 이어지면 AI의 쓰기가 이 Task로 들어간다
    s.turn(_chat(w), "[name 첫 경량화] 이름 정해")
    assert s.c.get(f"/api/works/{w['id']}").json()["name"] == "첫 경량화"
    wait_for(lambda: (Path(cwd).parent / "T01_첫-경량화").is_dir() or (Path(cwd).parent / "T02_첫-경량화").is_dir(), what="폴더가 따라옴")


def test_ai_does_not_rename_a_named_task(s):
    w = _task(s, "사람이 지은 이름")
    s.turn(_chat(w), "[name 다른 이름] 시작")
    assert s.c.get(f"/api/works/{w['id']}").json()["name"] == "사람이 지은 이름"
    assert '"changed": false' in s.agent_text(_chat(w))


def test_attachment_goes_to_task_inputs(s):
    w = _task(s, "첨부")
    up = s.c.post(f"/api/projects/{s.pid}/attachments", files={"file": ("m.prt", b"model")})
    up.raise_for_status()
    s.c.post(f"/api/sessions/{_chat(w)}/prompt", json={"text": "봐 줘", "attachment_ids": [up.json()["id"]]}).raise_for_status()
    s.wait_idle(_chat(w))
    cwd = Path(_new_cwds(s)[-1])
    assert (cwd / "inputs" / "m.prt").read_bytes() == b"model"
    assert not (cwd.parent / "inputs" / "m.prt").exists()


def test_case_runs_in_task_folder(s):
    w = _task(s, "해석")
    h = {"work_id": w["id"]}
    st = s.c.post(f"/api/projects/{s.pid}/studies", params=h, json={"name": "S", "adapter": "mock", "adapter_config": {"function": "beam"},
                                                                     "variables": [{"name": "x", "lower": 0, "upper": 1}], "objectives": []})
    st.raise_for_status()
    sid = st.json()["id"]
    s.c.post(f"/api/studies/{sid}/doe", json={"method": "lhs", "n": 1}).raise_for_status()
    r = s.c.post(f"/api/studies/{sid}/run", json={})
    r.raise_for_status()

    def done():
        cases = s.c.get(f"/api/studies/{sid}/cases").json()
        return cases if cases and all(c["status"] in ("complete", "failed") for c in cases) else None

    cases = wait_for(done, what="케이스 끝", timeout=120)
    run_dir = Path(cases[0]["run_dir"])
    assert run_dir.parents[3].name == "T01_해석" and run_dir.parents[1].name.startswith(f"S{sid:03d}")


def test_file_views_make_no_folder(s):
    w = _task(s, "구경")
    before = list((s.tmp / "ws").glob(f"P{s.pid:04d}*"))
    t = s.c.get(f"/api/projects/{s.pid}/local/tree", params={"work_id": w["id"]}).json()
    assert t["missing"] is True and t["entries"] == []
    assert s.c.get(f"/api/projects/{s.pid}/local/folder", params={"work_id": w["id"]}).json()["missing"] is True
    assert list((s.tmp / "ws").glob(f"P{s.pid:04d}*")) == before, "구경으로 폴더가 생기면 안 된다"
    s.turn(_chat(w), "시작")
    t = s.c.get(f"/api/projects/{s.pid}/local/tree", params={"work_id": w["id"]}).json()
    assert Path(t["root"]).name == "T01_구경" and "inputs" in {e["name"] for e in t["entries"]} and "work.json" in {e["name"] for e in t["entries"]}
    assert "AGENTS.md" not in {e["name"] for e in t["entries"]}, "숨긴 규칙 파일은 파일 탭에도 없다"
    assert s.c.get(f"/api/projects/{s.pid}/local/tree", params={"work_id": 999999}).status_code == 404


@windows_only
def test_rename_during_session_shows_new_name_and_keeps_cli(tmp_path):
    """세션 중 Task 이름 변경: 실제 cwd가 잠겨 있다. 탐색기에는 새 이름, 실제 이름은 CLI가 끝난 뒤. 그 뒤에도 세션을 잇는다."""
    s = Stack(tmp_path, cli_name="claude")
    s.agent_env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-home")}
    s.control(hold_cwd=True, history_by="realpath")
    try:
        s.up()
        w = _task(s, "처음")
        chat = _chat(w)
        s.turn(chat, "시작")
        real = Path(_new_cwds(s)[-1])
        assert real.name == "T01_처음"
        p = s.c.get(f"/api/works/{w['id']}").json()
        s.c.put(f"/api/works/{w['id']}", json={"name": "새 이름", "version": p["version"]}).raise_for_status()
        shown = real.parent / "T01_새-이름"
        wait_for(lambda: _is_alias(shown), what="새 이름 접합")
        assert real.is_dir() and not _is_alias(real) and _hidden(real)
        tree = s.c.get(f"/api/projects/{s.pid}/local/tree", params={"work_id": w["id"]}).json()
        assert tree["root"] == str(shown)
        s.turn(chat, "[where] 이어서")
        assert count(s.state, "process_start") == 1 and count(s.state, "session_new") == 1 and count(s.state, "session_load") == 0
        (shown / "scripts" / "via.txt").write_text("v")
        assert (real / "scripts" / "via.txt").read_text() == "v"
        # Agent를 다시 시작하면 미리 띄우기 전에 실제 이름이 바뀌고, 미리 띄우기가 Task 폴더로 세션을 불러온다
        s.stop_agent()
        wait_for(lambda: not s.agent_connected(), what="Agent 끊김", timeout=30)
        _holders_dead(s)
        s.start_agent()
        wait_for(lambda: shown.is_dir() and not _is_alias(shown), what="실제 이름 바뀜", timeout=60)
        assert _is_alias(real) and _hidden(real) and (shown / "scripts" / "via.txt").read_text() == "v"
        wait_for(lambda: count(s.state, "session_load") == 1, what="미리 띄우기", timeout=60)
        load = next(e for e in read_log(s.state) if e["ev"] == "session_load")
        assert Path(load["cwd"]) == shown, "Task를 기억하지 못하면 Project 폴더로 불러온다"
        assert count(s.state, "session_new") == 1
        s.turn(chat, "다시 시작한 뒤")
        assert count(s.state, "session_new") == 1
    except Exception:
        print(s.log_tail())
        raise
    finally:
        s.down()


@windows_only
def test_project_rename_with_live_task_session_and_inner_alias(tmp_path):
    s = Stack(tmp_path)
    s.control(hold_cwd=True)
    try:
        s.up()
        w = _task(s, "처음")
        chat = _chat(w)
        s.turn(chat, "시작")
        real_t = Path(_new_cwds(s)[-1])
        real_p = real_t.parent
        p = s.c.get(f"/api/projects/{s.pid}").json()
        s.c.put(f"/api/projects/{s.pid}", json={"name": "새 부품", "version": p["version"]}).raise_for_status()
        q = s.c.get(f"/api/works/{w['id']}").json()
        s.c.put(f"/api/works/{w['id']}", json={"name": "새 일", "version": q["version"]}).raise_for_status()
        shown_p = real_p.parent / f"P{s.pid:04d}_새-부품"
        wait_for(lambda: _is_alias(shown_p) and _is_alias(real_p / "T01_새-일"), what="두 접합")
        assert (shown_p / "T01_새-일" / "work.json").is_file()
        s.turn(chat, "이어서")
        assert count(s.state, "process_start") == 1 and count(s.state, "session_new") == 1
        s.stop_agent()
        wait_for(lambda: not s.agent_connected(), what="Agent 끊김", timeout=30)
        _holders_dead(s)
        s.start_agent()
        wait_for(lambda: shown_p.is_dir() and not _is_alias(shown_p), what="Project 실제 이름", timeout=60)
        wait_for(lambda: (shown_p / "T01_새-일").is_dir() and not _is_alias(shown_p / "T01_새-일"), what="Task 실제 이름", timeout=60)
        assert (shown_p / "T01_새-일" / "work.json").is_file() and (shown_p / "T01_처음" / "work.json").is_file(), "옛 이름이 그대로 돈다"
        assert os.path.exists(real_p / "T01_처음" / "work.json"), "옛 Project 이름 + 옛 Task 이름도 돈다"
    except Exception:
        print(s.log_tail())
        raise
    finally:
        s.down()


def test_gemini_session_copies_skills_only_for_gemini(tmp_path):
    """부모의 스킬을 읽지 못하는 CLI만 Task 폴더에 복사한다(관문 G3의 대안). Claude·Codex의 Task는 파일 4개다."""
    s = Stack(tmp_path, cli_name="gemini")
    try:
        s.up()
        w = _task(s, "제미나이")
        s.turn(_chat(w), "시작")
        cwd = Path(_new_cwds(s)[-1])
        assert (cwd / ".gemini" / "skills" / "abaqus-automation" / "SKILL.md").is_file()
        assert "`.gemini/skills/abaqus-automation/SKILL.md`" in (cwd / "GEMINI.md").read_text(encoding="utf-8")
        assert not (cwd / ".agents").exists() and "`../.agents/skills/abaqus-automation/SKILL.md`" in (cwd / "AGENTS.md").read_text(encoding="utf-8")
    except Exception:
        print(s.log_tail())
        raise
    finally:
        s.down()
