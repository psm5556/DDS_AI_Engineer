"""빈 마켓이면 오늘과 같다: MCP 4개, 규칙 파일 바이트 동일, 지문 동일, 재복사 없음, 프롬프트 글자 동일."""
import hashlib
import json
import sys
from pathlib import Path

import pytest

from agent.dds_agent import config, context, market
from agent.dds_agent import main as M


def _tree(d: Path) -> dict[str, str]:
    return {p.relative_to(d).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in d.rglob("*") if p.is_file()}


def _mtimes(d: Path) -> dict[str, int]:
    return {p.relative_to(d).as_posix(): p.stat().st_mtime_ns for p in d.rglob("*") if p.is_file()}


@pytest.fixture
def empty_market(tmp_path, monkeypatch):
    monkeypatch.setenv("DDS_MARKET_DIR", str(tmp_path / "market"))
    return tmp_path / "market"


@pytest.fixture
def agent(monkeypatch, tmp_path, empty_market):
    cfg = tmp_path / "agent.toml"
    cfg.write_text("\n".join([f"workspace_dir = {json.dumps(str(tmp_path / 'ws'))}", "tray = false"]) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    monkeypatch.setenv("DDS_AGENT_CONFIG", str(cfg))
    monkeypatch.setenv("DDS_TOKEN", "t")
    monkeypatch.setenv("DDS_WORKSPACE", str(tmp_path / "ws"))
    a = M.Agent()
    yield a
    a.shutdown()


def _expected_rules(project: Path) -> bytes:
    """마켓 코드를 지나지 않고 만든 규칙 파일. 표시 구간 안은 지침 + 스킬 목록뿐이다."""
    body = (context.CONTEXT_DIR / "INSTRUCTIONS.md").read_text(encoding="utf-8") + context._skill_index(project / ".claude" / "skills")
    return f"{context.RULES_START}\n{body.strip()}\n{context.RULES_END}\n".encode("utf-8")


def test_project_folder_is_identical_with_empty_market(tmp_path, empty_market):
    project = tmp_path / "p"
    project.mkdir()
    assert context.sync_project_context(project) is True
    for name in context.RULE_FILES:
        assert (project / name).read_bytes() == _expected_rules(project), name
    assert not (project / context.MARKET_FILE).exists()
    assert not (project / context.MARKET_TMP).exists()
    assert not empty_market.exists()  # 보관소 폴더도 만들지 않는다
    # 지문 형식이 그대로다: 첫 줄이 기본 스킬 지문이고 기록에 마켓 항목이 없다
    head, _, body = (project / context.STAMP).read_text(encoding="utf-8").partition("\n")
    assert head == context._source_stamp()
    assert context.MARKET_FILE not in json.loads(body)["hidden"]


def test_second_sync_copies_nothing(tmp_path, empty_market):
    project = tmp_path / "p"
    project.mkdir()
    context.sync_project_context(project)
    before = _mtimes(project)
    assert context.sync_project_context(project) is False
    assert _mtimes(project) == before


def test_market_sync_with_nothing_touches_nothing(tmp_path, empty_market):
    project = tmp_path / "p"
    project.mkdir()
    context.sync_project_context(project)
    before = _tree(project), _mtimes(project)
    res = context.sync_market(project)
    assert res == {"changed": False, "active": [], "conflicts": {}, "incomplete": []}
    assert (_tree(project), _mtimes(project)) == before
    # 기본 스킬을 복사하지 않은 폴더에도 아무것도 만들지 않는다
    other = tmp_path / "untouched"
    other.mkdir()
    context.sync_market(other)
    assert list(other.iterdir()) == []


def test_skill_index_same_without_summaries(tmp_path):
    root = tmp_path / "skills"
    (root / "a").mkdir(parents=True)
    (root / "a" / "SKILL.md").write_text("---\ndescription: 설명\n---\n", encoding="utf-8")
    assert context._skill_index(root) == context._skill_index(root, {}) == context._skill_index(root, None)


def test_mcp_servers_are_the_same_four(agent):
    defs = agent.mcp_servers(1, "claude")
    assert [d["name"] for d in defs] == ["dds-flow", "dds-cae", "dds-study", "dds-live"]
    assert market.gateway_on() is False  # 2단계 전에는 입구를 켜는 길이 없다
    assert "dds-market" not in json.dumps(agent._runner_env("codex").get("CODEX_CONFIG"))
    assert not list((Path(M.__file__).parents[2] / "mcp_servers").glob("dds_market*"))


def test_settings_message_shape_is_untouched():
    """market.sync는 별도 메시지다. agent.settings에 값을 더하지 않는다"""
    src = (Path(M.__file__).parents[2] / "server" / "app" / "routers" / "agents.py").read_text(encoding="utf-8")
    body = src[src.index("def settings_message"):src.index('@router.get("/settings"')]
    assert "market" not in body


def test_prompt_is_unchanged_with_empty_market(agent, tmp_path):
    """안내가 없으면 프롬프트 글자가 그대로이고 폴더도 찾지 않는다."""
    agent.session_project["s1"] = (1, "p", "u")
    seen = []
    agent.s.project_dir = lambda *a: seen.append(a) or pytest.fail("빈 마켓에서는 폴더를 찾지 않는다")
    assert agent._market_notice("s1", "claude") == ""
    assert agent._market_notice("s1", "claude") == ""
    assert seen == []


def test_empty_sync_writes_no_lock_and_reports_state(tmp_path, empty_market):
    sent = []
    store = market.MarketStore(None, sent.append, tmp_path, "9.9.9")
    msg = {"type": "market.sync", "rev": "r", "recall_rev": "c", "enabled": True, "items": [], "recalled": []}
    with store._cv:
        store._latest = msg
    assert store.process(msg) is True
    assert not empty_market.exists()
    assert sent == [{"type": "market.state", "recall_rev": "c", "incomplete": [], "rev": "r", "items": []}]


def test_hello_carries_market_fields_without_changing_old_ones(agent):
    h = agent.hello()
    assert h["market_api"] == 1 and h["market_rev"] is None and h["market_recall_rev"] is None and isinstance(h["tools_ready"], list)
    assert {"hostname", "version", "agents", "boot", "live_sessions", "active_turns"} <= set(h)


def test_market_messages_do_not_count_as_work(agent):
    """동기화가 자동 업데이트의 유휴 판단을 깨지 않는다"""
    before = agent._last_work
    agent.touch("market.sync")
    assert agent._last_work == before
    assert not any("market".startswith(p) for p in M.WORK_MSGS)


def test_import_has_no_new_dependency():
    """Agent의 마켓 코드는 표준 모듈만 쓴다"""
    import ast

    tree = ast.parse(Path(market.__file__).read_text(encoding="utf-8"))
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module and not n.level}
    assert mods <= set(sys.stdlib_module_names)
