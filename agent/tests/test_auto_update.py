"""자동 업데이트: 켜 두면 일이 없을 때 뒤에서 하고, 하는 일을 끊지 않으며, 끄면 멈춘다."""
import asyncio
import zipfile
from types import SimpleNamespace

import pytest

from agent.dds_agent import main as M
from agent.dds_agent import updater


@pytest.fixture
def agent(monkeypatch, tmp_path):
    monkeypatch.setenv("DDS_AGENT_CONFIG", str(tmp_path / "none.toml"))
    monkeypatch.setenv("DDS_TOKEN", "t")
    monkeypatch.setenv("DDS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("DDS_AGENT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(M, "IDLE_SEC", 0.2)
    monkeypatch.setattr(M, "IDLE_POLL_SEC", 0.02)
    a = M.Agent()
    a.steps = []
    monkeypatch.setattr(M.updater, "prepare_update", lambda api, v, sha=None: a.steps.append(f"prepare {v}"))
    monkeypatch.setattr(M.updater, "activate", lambda v: a.steps.append(f"activate {v}"))
    return a


async def _settle(a, seconds=0.5):
    await asyncio.sleep(seconds)
    return a.steps


def test_updates_in_background_once_idle(agent):
    async def run():
        agent.schedule_update("99.0.0", "x")
        assert agent.update_state == "pending:99.0.0"
        await _settle(agent)
        assert agent.steps == ["prepare 99.0.0", "activate 99.0.0"]
        assert agent.restart and agent.update_state == "restarting:99.0.0"
    asyncio.run(run())


def test_waits_while_background_solver_computes(agent):
    """턴이 끝나도 AI가 남긴 솔버가 돌고 있으면 다시 시작해 끝내지 않는다"""
    state = {"working": True}
    agent.runners["claude"] = SimpleNamespace(working_in_background=lambda: state["working"], shutdown=lambda: None)

    async def run():
        agent.schedule_update("99.0.0", "x")
        await _settle(agent, 0.4)
        assert agent.steps == [] and not agent.restart
        state["working"] = False
        await _settle(agent)
        assert agent.steps == ["prepare 99.0.0", "activate 99.0.0"]
    asyncio.run(run())


def test_waits_for_running_work_and_quiet_time(agent):
    async def run():
        agent.active_prompts = 1
        agent.schedule_update("99.0.0", "x")
        await _settle(agent, 0.4)
        assert agent.steps == [] and not agent.restart  # 대화가 진행 중이면 받지도 않는다
        agent.active_prompts = 0
        await asyncio.sleep(0.05)
        assert agent.steps == []  # 끝나자마자가 아니라 잠시 조용해진 뒤에
        await _settle(agent)
        assert agent.restart
    asyncio.run(run())


def test_recent_request_counts_as_working(agent):
    async def run():
        agent.schedule_update("99.0.0", "x")
        for _ in range(10):  # 짧은 요청이 이어지는 동안에는 유휴가 아니다
            await asyncio.sleep(0.05)
            agent.touch("session.load")
        assert agent.steps == []
        await _settle(agent)
        assert agent.restart
    asyncio.run(run())


def test_screen_refresh_is_not_work(agent):
    """화면을 열어 두면 주기적으로 오는 조회 때문에 업데이트가 영영 밀리면 안 된다."""
    before = agent._last_work
    agent.touch("local.tree")
    agent.touch("local.diagnose")
    assert agent._last_work == before


def test_work_started_during_install_delays_restart(agent, monkeypatch):
    """설치는 뒤에서 하되, 그사이 시작한 일이 끝날 때까지 다시 시작하지 않는다."""
    def prepare(api, v, sha=None):
        agent.steps.append(f"prepare {v}")
        agent.active_prompts = 1

    monkeypatch.setattr(M.updater, "prepare_update", prepare)

    async def run():
        agent.schedule_update("99.0.0", "x")
        await _settle(agent, 0.5)
        assert agent.steps == ["prepare 99.0.0"] and not agent.restart
        agent.active_prompts = 0
        await _settle(agent)
        assert agent.steps[-1] == "activate 99.0.0" and agent.restart
    asyncio.run(run())


def test_turning_off_cancels_waiting_update(agent):
    async def run():
        agent.active_prompts = 1
        await agent.handle({"type": "agent.release", "version": "99.0.0", "sha256": "x"})
        assert agent.update_state == "pending:99.0.0"
        await agent.handle({"type": "agent.settings", "auto_update": False})
        await asyncio.sleep(0.01)
        assert agent.update_state == "idle"
        agent.active_prompts = 0
        await _settle(agent)
        assert agent.steps == [] and not agent.restart
    asyncio.run(run())


def test_off_ignores_new_release_but_update_now_still_works(agent):
    async def run():
        await agent.handle({"type": "agent.settings", "auto_update": False})
        await agent.handle({"type": "agent.release", "version": "99.0.0", "sha256": "x"})
        await _settle(agent, 0.3)
        assert agent.steps == []
        agent.active_prompts = 1  # '지금 업데이트'는 사람이 직접 고른 것이라 기다리지 않는다
        await agent.handle({"type": "agent.update", "version": "99.0.0", "sha256": "x", "force": True})
        await _settle(agent, 0.2)
        assert agent.steps == ["prepare 99.0.0", "activate 99.0.0"]
    asyncio.run(run())


def test_update_now_replaces_waiting_update(agent):
    async def run():
        agent.active_prompts = 1
        agent.schedule_update("99.0.0", "x")
        await asyncio.sleep(0.01)
        agent.schedule_update("99.0.0", "x", force=True)
        await _settle(agent, 0.2)
        assert agent.restart and agent.steps == ["prepare 99.0.0", "activate 99.0.0"]
    asyncio.run(run())


def test_turning_on_checks_right_away(agent, monkeypatch):
    monkeypatch.setattr(agent.api, "get", lambda path: {"version": "99.0.0", "sha256": "x"})

    async def run():
        await agent.handle({"type": "agent.settings", "auto_update": True})
        await _settle(agent)
        assert agent.restart
    asyncio.run(run())


def test_off_setting_arriving_during_check_wins(agent, monkeypatch):
    """연결 직후 최신 버전을 묻는 사이에 '끔'이 도착해도 업데이트하지 않는다."""
    def get(path):
        agent.auto_update = False
        return {"version": "99.0.0", "sha256": "x"}

    monkeypatch.setattr(agent.api, "get", get)

    async def run():
        await agent._check_latest()
        await _settle(agent, 0.3)
        assert agent.steps == []
    asyncio.run(run())


def test_failed_update_reports_and_keeps_running(agent, monkeypatch):
    def fail(api, v, sha=None):
        raise RuntimeError("의존성 설치 실패")

    monkeypatch.setattr(M.updater, "prepare_update", fail)

    async def run():
        agent.schedule_update("99.0.0", "x")
        await _settle(agent)
        assert agent.update_state.startswith("failed:") and "의존성" in agent.update_state
        assert not agent.restart
    asyncio.run(run())


def test_prepare_does_not_switch_version_until_activate(monkeypatch, tmp_path):
    """받아 두는 단계에서는 current.txt를 바꾸지 않는다. 작업 중 PC가 꺼져도 예전 버전으로 뜬다."""
    monkeypatch.setenv("DDS_AGENT_HOME", str(tmp_path))
    (tmp_path / "current.txt").write_text("1.0.0", encoding="utf-8")

    class Api:
        def download(self, path, dest):
            with zipfile.ZipFile(dest, "w") as z:
                z.writestr("a.txt", "x")
            return "sha"

    updater.prepare_update(Api(), "2.0.0", "sha")
    assert (tmp_path / "versions" / "2.0.0" / ".installed").exists()
    assert (tmp_path / "current.txt").read_text(encoding="utf-8") == "1.0.0"
    updater.activate("2.0.0")
    assert (tmp_path / "current.txt").read_text(encoding="utf-8") == "2.0.0"
    assert (tmp_path / "previous.txt").read_text(encoding="utf-8") == "1.0.0"


def test_prepare_rejects_bad_download(monkeypatch, tmp_path):
    monkeypatch.setenv("DDS_AGENT_HOME", str(tmp_path))

    class Api:
        def download(self, path, dest):
            dest.write_bytes(b"x")
            return "other"

    with pytest.raises(RuntimeError, match="검증 실패"):
        updater.prepare_update(Api(), "2.0.0", "sha")
    assert not (tmp_path / "versions" / "2.0.0").exists()
