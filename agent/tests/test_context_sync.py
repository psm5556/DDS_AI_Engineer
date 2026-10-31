"""프로젝트 폴더 스킬 동기화: 새 Agent 버전의 스킬은 복사되고, 빠진 스킬은 지워지고, 사용자가 만든 스킬은 남는다."""
import json

import pytest

from agent.dds_agent import context


def _skill(root, name, body="x"):
    d = root / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {body}\n---\n", encoding="utf-8")
    return d


def test_removed_skill_is_cleaned_but_user_skill_stays(tmp_path, monkeypatch):
    src, proj = tmp_path / "agent-context", tmp_path / "proj"
    _skill(src, "nx-cad-automation")
    old = _skill(src, "old-skill")
    (src / "INSTRUCTIONS.md").write_text("# 규칙\n", encoding="utf-8")
    monkeypatch.setattr(context, "CONTEXT_DIR", src)

    context.sync_project_context(proj)
    for rel in context.SKILL_DIRS:
        assert (proj / rel / "old-skill" / "SKILL.md").exists()
    (proj / ".claude" / "skills" / "my-own").mkdir()  # 사용자가 직접 만든 스킬
    assert json.loads((proj / context.MANAGED).read_text(encoding="utf-8")) == ["nx-cad-automation", "old-skill"]

    # 새 버전: old-skill이 빠지고 new-skill이 생김
    for p in sorted(old.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    old.rmdir()
    _skill(src, "new-skill")
    context.sync_project_context(proj)
    for rel in context.SKILL_DIRS:
        assert not (proj / rel / "old-skill").exists()
        assert (proj / rel / "new-skill" / "SKILL.md").exists()
    assert (proj / ".claude" / "skills" / "my-own").exists()
    assert "new-skill" in (proj / "CLAUDE.md").read_text(encoding="utf-8")


def test_broken_marker_never_deletes_outside(tmp_path, monkeypatch):
    src, proj = tmp_path / "agent-context", tmp_path / "proj"
    _skill(src, "nx-cad-automation")
    (src / "INSTRUCTIONS.md").write_text("# 규칙\n", encoding="utf-8")
    monkeypatch.setattr(context, "CONTEXT_DIR", src)
    proj.mkdir()
    keep = tmp_path / "keep"
    keep.mkdir()
    (proj / context.MANAGED).write_text(json.dumps(["../../keep", "..", ".claude"]), encoding="utf-8")
    context.sync_project_context(proj)
    assert keep.exists() and (proj / ".claude").exists()


def test_copy_failure_says_why_in_one_line(tmp_path, monkeypatch):
    """복사 실패의 원문은 경로 수백 개다. 대화에 "에이전트 오류"로 쏟아지지 않고 이유와 할 일 한 줄이 된다."""
    src = tmp_path / "agent-context"
    deep = _skill(src, "nx-cad-automation")
    (src / "INSTRUCTIONS.md").write_text("# 규칙\n", encoding="utf-8")
    monkeypatch.setattr(context, "CONTEXT_DIR", src)
    failure = context.shutil.Error([("a", "b", "[WinError 5] 권한")] * 300)

    def fail(*a, **kw):
        raise failure

    monkeypatch.setattr(context.shutil, "copytree", fail)
    (tmp_path / "proj").mkdir()
    with pytest.raises(RuntimeError, match="쓰기 권한") as e:
        context.sync_project_context(tmp_path / "proj")
    assert "→" in str(e.value) and str(e.value).count("WinError") == 1 and "\n" not in str(e.value)
    # 경로가 한도를 넘는 경우는 무엇을 줄여야 하는지 말한다. 복사하기 전 폴더가 없어도 길이로 판단한다
    long_dst = tmp_path / ("p" * 120) / ("q" * 120)
    text = context._copy_failed(tmp_path / "w", deep, long_dst, failure)
    assert "경로가 너무 길어" in text and "260" in text and "→" in text
