"""Task 폴더의 규칙 파일. 스킬은 Project 폴더에 1벌이고 Task마다 파일 4개만 더한다."""
import json
import shutil
import subprocess

import pytest

from agent.dds_agent import context
from agent.tests.test_context_hidden import git_only
from agent.tests.test_folder_names import _hidden, windows_only

EXPECTED = {"AGENTS.md", "GEMINI.md", ".gemini/settings.json", context.WORK_STAMP}


@pytest.fixture
def dirs(tmp_path):
    p = tmp_path / "P0012_스파이더"
    w = p / "T01_일"
    w.mkdir(parents=True)
    context.sync_project_context(p)
    return p, w


def _files(d) -> set[str]:
    return {f.relative_to(d).as_posix() for f in d.rglob("*") if f.is_file()}


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def test_four_files_only(dirs):
    p, w = dirs
    assert context.sync_work_context(w, p) is True
    assert _files(w) == EXPECTED
    assert not (w / "CLAUDE.md").exists(), "두면 Claude가 규칙을 두 번 읽는다"
    assert not (w / ".claude").exists() and not (w / ".agents").exists()


def test_second_sync_skips(dirs):
    p, w = dirs
    context.sync_work_context(w, p)
    assert context.sync_work_context(w, p) is False


@windows_only
def test_rule_files_are_hidden(dirs):
    p, w = dirs
    context.sync_work_context(w, p)
    for name in ("AGENTS.md", "GEMINI.md", ".gemini", context.WORK_STAMP):
        assert _hidden(w / name), name
    assert context.dds_work_items(w) == {"AGENTS.md", "GEMINI.md", ".gemini", context.WORK_STAMP}


def test_skill_paths_point_to_the_parent_copy(dirs):
    p, w = dirs
    context.sync_work_context(w, p)
    for rule, skills in (("AGENTS.md", ".agents/skills"), ("GEMINI.md", ".gemini/skills")):
        text = (w / rule).read_text(encoding="utf-8")
        assert "에이전트 작업 규칙" in text and f"`../{skills}/abaqus-automation/SKILL.md`" in text
        assert (w / f"../{skills}/abaqus-automation/SKILL.md").is_file()
        assert ".claude/skills" not in text


def test_project_rule_file_tells_task_folder_to_go_up(dirs):
    p, _ = dirs
    text = (p / "CLAUDE.md").read_text(encoding="utf-8")
    assert "`../`를 붙이" in text and "`.claude/skills/abaqus-automation/SKILL.md`" in text


def test_user_rule_file_survives_and_stays_visible(dirs):
    p, w = dirs
    (w / "AGENTS.md").write_bytes(b"# my notes\n")
    context.sync_work_context(w, p)
    data = (w / "AGENTS.md").read_bytes()
    assert data.startswith(b"# my notes\n") and context.RULES_START.encode() in data
    assert "AGENTS.md" not in context.dds_work_items(w)
    assert not _hidden(w / "AGENTS.md")


def test_gemini_settings_merge_and_follow_project_rename(dirs):
    p, w = dirs
    (w / ".gemini").mkdir()
    (w / ".gemini" / "settings.json").write_text(json.dumps({"ui": {"theme": "x"}, "context": {"includeDirectories": ["D:/mine"]}}), encoding="utf-8")
    context.sync_work_context(w, p)
    cfg = json.loads((w / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert cfg["ui"] == {"theme": "x"} and cfg["context"]["includeDirectories"] == ["D:/mine", str(p)]
    assert set(cfg["context"]) == {"includeDirectories"}, "Gemini가 모르는 키를 넣지 않는다"
    assert not _hidden(w / ".gemini") and ".gemini" not in context.dds_work_items(w), "사용자 설정이 든 폴더는 숨기지 않는다"
    renamed = p.parent / "P0012_새이름"
    p.rename(renamed)
    w2 = renamed / w.name
    assert context.sync_work_context(w2, renamed) is True
    cfg = json.loads((w2 / ".gemini" / "settings.json").read_text(encoding="utf-8"))
    assert cfg["context"]["includeDirectories"] == ["D:/mine", str(renamed)], "옛 경로를 뺀다"


def test_broken_gemini_settings_are_not_overwritten(dirs):
    p, w = dirs
    (w / ".gemini").mkdir()
    (w / ".gemini" / "settings.json").write_text("{ 망가짐", encoding="utf-8")
    context.sync_work_context(w, p)
    assert (w / ".gemini" / "settings.json").read_text(encoding="utf-8") == "{ 망가짐"
    assert ".gemini" not in context.dds_work_items(w)


def test_market_summary_reaches_the_task(dirs):
    p, w = dirs
    context.sync_work_context(w, p)
    for rel in context.SKILL_DIRS:
        d = p / rel / "market-x"
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("---\nname: market-x\ndescription: 등록자의 긴 설명\n---\n", encoding="utf-8")
    (p / context.MARKET_FILE).write_text(json.dumps({"stamp": "s2", "items": {"a.market-x": {"version": "1", "skills": ["market-x"], "summaries": {"market-x": "서버 요약"}}}}), encoding="utf-8")
    assert context.sync_work_context(w, p) is True, "마켓이 바뀌면 다시 쓴다"
    text = (w / "AGENTS.md").read_text(encoding="utf-8")
    assert "서버 요약" in text and "등록자의 긴 설명" not in text and "`../.agents/skills/market-x/SKILL.md`" in text
    assert not (w / ".agents").exists(), "마켓 스킬도 Project 폴더에 1벌이다"


def test_fallback_copies_only_for_that_cli(dirs):
    """관문을 통과하지 못한 CLI만 스킬을 복사한다(Task마다 파일 수십 개). 경로도 그 CLI만 Task 폴더 기준이다."""
    p, w = dirs
    context.sync_work_context(w, p, ["gemini"])
    assert (w / ".gemini/skills/abaqus-automation/SKILL.md").is_file() and not (w / ".agents").exists()
    gemini, agents = (w / "GEMINI.md").read_text(encoding="utf-8"), (w / "AGENTS.md").read_text(encoding="utf-8")
    assert "`.gemini/skills/abaqus-automation/SKILL.md`" in gemini and "../.gemini" not in gemini
    assert "`../.agents/skills/abaqus-automation/SKILL.md`" in agents
    assert context.sync_work_context(w, p, ["gemini"]) is False
    assert (w / ".gemini" / "settings.json").is_file()
    assert context.sync_work_context(w, p, []) is False, "다른 CLI로 이어도 복사한 폴더를 그대로 쓴다"
    assert "`.gemini/skills/abaqus-automation/SKILL.md`" in (w / "GEMINI.md").read_text(encoding="utf-8")


@git_only
def test_git_patterns_cover_tasks_with_one_block(tmp_path):
    p = tmp_path / "repo"
    w = p / "T01_일"
    w.mkdir(parents=True)
    _git(p, "init", "-q")
    context.sync_project_context(p)
    context.sync_work_context(w, p)
    (w / "work.json").write_text("{}")
    text = (p / ".git/info/exclude").read_text(encoding="utf-8")
    assert "/T*/AGENTS.md" in text and "\\*" not in text.split("T*/AGENTS.md")[0][-5:], "글자 그대로 넣어야 *가 패턴이다"
    status = _git(p, "status", "--porcelain", "-uall")
    assert "AGENTS.md" not in status and "settings.json" not in status and context.WORK_STAMP not in status
    assert "T01_일" in status or "work.json" in status, "사용자 파일은 그대로 보인다"


@git_only
def test_repo_above_task_does_not_read_rules_twice(tmp_path):
    """Codex·Gemini는 저장소 최상위부터 cwd까지 규칙 파일을 모두 읽는다. 저장소 안이면 Task 폴더에는 스킬 경로만 둔다."""
    p = tmp_path / "repo" / "P0012"
    w = p / "T01_일"
    w.mkdir(parents=True)
    _git(tmp_path / "repo", "init", "-q")
    context.sync_project_context(p)
    context.sync_work_context(w, p)
    text = (w / "AGENTS.md").read_text(encoding="utf-8")
    assert "에이전트 작업 규칙" not in text and "`../.agents/skills/abaqus-automation/SKILL.md`" in text
    assert "에이전트 작업 규칙" in (p / "AGENTS.md").read_text(encoding="utf-8")
    shutil.rmtree(tmp_path / "repo" / ".git", ignore_errors=True)
    assert context.sync_work_context(w, p) is True
    assert "에이전트 작업 규칙" in (w / "AGENTS.md").read_text(encoding="utf-8"), "저장소가 없어지면 다시 전체 규칙을 둔다"


@git_only
def test_git_hide_task_aliases(tmp_path):
    p = tmp_path / "repo"
    p.mkdir()
    _git(p, "init", "-q")
    context.git_hide_aliases(p, 0, ["T01_옛이름"], label="T01")
    text = (p / ".git/info/exclude").read_text(encoding="utf-8")
    assert "/T01_옛이름" in text and "[/T01 접합]" in text
