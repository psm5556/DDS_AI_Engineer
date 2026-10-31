"""작업 폴더 정리: DDS가 만든 규칙·스킬은 탐색기와 웹 파일 탭에서 숨기고, 사용자 파일은 내용도 보임 상태도 그대로 둔다.
숨김은 폴더를 깔끔하게 하려는 것이지 보호 수단이 아니다."""
import ctypes
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest

from agent.dds_agent import context
from agent.dds_agent.workspace import Workspace
from cae_adapters.hidden import hidden_kwargs

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows Hidden 속성 시험")
HIDDEN, READONLY = 0x2, 0x1
RULES = ("GEMINI.md", "CLAUDE.md", "AGENTS.md")
TOPS = (".gemini", ".claude", ".agents")


def _attrs(p) -> int:
    get = ctypes.WinDLL("kernel32").GetFileAttributesW
    get.argtypes, get.restype = [ctypes.c_wchar_p], ctypes.c_uint32
    a = get(str(p))
    assert a != 0xFFFFFFFF, f"없는 경로: {p}"
    return a


def _set(p, a):
    put = ctypes.WinDLL("kernel32").SetFileAttributesW
    put.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    assert put(str(p), a)


def is_hidden(p) -> bool:
    return bool(_attrs(p) & HIDDEN)


def _skill(src, name):
    d = src / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name} 설명\n---\n본문\n", encoding="utf-8")
    return d


@pytest.fixture
def src(tmp_path, monkeypatch):
    s = tmp_path / "agent-context"
    _skill(s, "nx-cad")
    _skill(s, "abaqus")
    (s / "INSTRUCTIONS.md").write_text("# DDS Conversa — 에이전트 작업 규칙\n\n첫 번째 규칙\n", encoding="utf-8")
    monkeypatch.setattr(context, "CONTEXT_DIR", s)
    return s


@pytest.fixture
def proj(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    return p


def _new_version(s, text="두 번째 규칙"):
    (s / "INSTRUCTIONS.md").write_text(f"# DDS Conversa — 에이전트 작업 규칙\n\n{text}\n", encoding="utf-8")


# ---------- 숨기기 ----------
@windows_only
def test_fresh_folder_hides_everything_dds_made(src, proj):
    assert context.sync_project_context(proj) is True
    for name in (*RULES, *TOPS, context.MANAGED, context.STAMP):
        assert is_hidden(proj / name), name
    # 안쪽은 건드리지 않는다: 위쪽 폴더만 숨겨도 탐색기에 보이지 않고, 복사가 안쪽 파일에서 막히지 않는다
    assert not is_hidden(proj / ".claude" / "skills" / "nx-cad" / "SKILL.md")


@windows_only
def test_other_attributes_are_kept(src, proj):
    context.sync_project_context(proj)
    f = proj / context.STAMP
    _set(f, HIDDEN | READONLY)
    _set(f, _attrs(f) & ~HIDDEN)
    assert context.sync_project_context(proj) is False
    assert is_hidden(f) and _attrs(f) & READONLY


@windows_only
def test_users_own_dot_folder_stays_visible(src, proj):
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    own = proj / ".claude" / "skills" / "my-own"
    own.mkdir(parents=True)
    context.sync_project_context(proj)
    assert not is_hidden(proj / ".claude")
    assert not is_hidden(proj / ".claude" / "skills")
    assert not is_hidden(own)
    assert is_hidden(proj / ".claude" / "skills" / "nx-cad")  # 사용자의 폴더 안에서는 DDS 스킬만 숨긴다
    assert is_hidden(proj / ".gemini") and is_hidden(proj / ".agents")
    assert (proj / ".claude" / "settings.json").read_text(encoding="utf-8") == "{}"


@windows_only
def test_skipped_sync_still_hides_without_reading_or_walking(src, proj, monkeypatch):
    context.sync_project_context(proj)
    for name in (*RULES, *TOPS, context.MANAGED, context.STAMP):
        _set(proj / name, _attrs(proj / name) & ~HIDDEN)

    def no_read(self, *a, **k):
        raise AssertionError(f"파일 내용을 읽으면 안 된다: {self}")

    monkeypatch.setattr(Path, "read_bytes", no_read)
    monkeypatch.setattr(context.shutil, "copytree", no_read)
    calls = []
    real = context._attrs
    monkeypatch.setattr(context, "_attrs", lambda p: (calls.append(p), real(p))[1])
    assert context.sync_project_context(proj) is False
    for name in (*RULES, *TOPS, context.MANAGED, context.STAMP):
        assert is_hidden(proj / name), name
    assert len(calls) <= 2 * 8  # 항목마다 한두 번. 스킬 폴더 안을 훑지 않는다


@windows_only
def test_user_content_added_later_makes_folder_visible_again(src, proj):
    context.sync_project_context(proj)
    assert is_hidden(proj / ".claude")
    (proj / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    assert context.sync_project_context(proj) is False
    assert not is_hidden(proj / ".claude")
    assert is_hidden(proj / ".claude" / "skills" / "abaqus")
    (proj / ".claude" / "settings.json").unlink()  # 다시 DDS 것만 남았다
    context.sync_project_context(proj)
    assert is_hidden(proj / ".claude") and not is_hidden(proj / ".claude" / "skills" / "abaqus")


@windows_only
def test_never_unhides_what_user_hid(src, proj):
    mine = proj / "CLAUDE.md"
    mine.write_text("# 내 규칙\n", encoding="utf-8")
    _set(mine, _attrs(mine) | HIDDEN)
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    _set(proj / ".claude", _attrs(proj / ".claude") | HIDDEN)
    context.sync_project_context(proj)
    context.sync_project_context(proj)
    assert is_hidden(mine) and is_hidden(proj / ".claude")


# ---------- 사용자 규칙 파일 보존 ----------
def _strip_block(raw: bytes) -> bytes:
    return re.sub(re.escape(context.RULES_START.encode()) + rb".*?" + re.escape(context.RULES_END.encode()), b"", raw, flags=re.S)


@pytest.mark.parametrize("name", RULES)
def test_users_rule_file_is_kept_and_gets_dds_rules(src, proj, name):
    mine = "# 우리 팀 규칙\r\n\r\n- 단위는 mm\r\n".encode("utf-8")
    (proj / name).write_bytes(mine)
    context.sync_project_context(proj)
    raw = (proj / name).read_bytes()
    assert raw.startswith(mine)
    text = raw.decode("utf-8")
    assert "첫 번째 규칙" in text and "abaqus" in text  # DDS 규칙과 스킬 경로도 CLI에 닿는다

    _new_version(src)
    context.sync_project_context(proj)
    raw = (proj / name).read_bytes()
    assert raw.startswith(mine) and raw.count(context.RULES_START.encode()) == 1
    text = raw.decode("utf-8")
    assert "두 번째 규칙" in text and "첫 번째 규칙" not in text


@windows_only
def test_users_rule_file_is_not_hidden(src, proj):
    (proj / "CLAUDE.md").write_text("# 내 규칙\n", encoding="utf-8")
    context.sync_project_context(proj)
    assert not is_hidden(proj / "CLAUDE.md")
    assert is_hidden(proj / "AGENTS.md")


def test_text_around_block_survives_update_unchanged(src, proj):
    context.sync_project_context(proj)
    f = proj / "AGENTS.md"
    before, after = "# 앞에 쓴 글\n\n".encode(), "\n\n# 뒤에 쓴 글\r\n끝".encode()
    if os.name == "nt":
        _set(f, _attrs(f) & ~HIDDEN)
    f.write_bytes(before + f.read_bytes() + after)
    _new_version(src)
    assert context.sync_project_context(proj) is True
    raw = f.read_bytes()
    assert raw.startswith(before) and raw.endswith(after) and "두 번째 규칙".encode() in raw
    assert raw.count(context.RULES_START.encode()) == 1


@windows_only
def test_writing_into_dds_file_shows_it_again(src, proj):
    context.sync_project_context(proj)
    f = proj / "CLAUDE.md"
    assert is_hidden(f)
    with open(f, "ab") as h:  # 숨김 파일에 덧붙이기는 된다
        h.write("\n내가 덧붙인 글\n".encode())
    assert context.sync_project_context(proj) is True  # 크기가 달라 다시 맞춘다
    assert not is_hidden(f) and "내가 덧붙인 글" in f.read_text(encoding="utf-8")


def test_broken_marker_does_not_swallow_user_text(src, proj):
    f = proj / "CLAUDE.md"
    head = f"{context.RULES_START}\n지워진 끝 표시\n\n내 글은 남아야 한다\n".encode()
    f.write_bytes(head)
    context.sync_project_context(proj)
    assert f.read_bytes().startswith(head)
    _new_version(src)
    context.sync_project_context(proj)
    raw = f.read_bytes()
    assert raw.startswith(head) and "두 번째 규칙".encode() in raw and "첫 번째 규칙".encode() not in raw


def test_non_utf8_user_file_bytes_unchanged(src, proj):
    f = proj / "CLAUDE.md"
    mine = "# 사내 규칙\r\n".encode("cp949")
    f.write_bytes(mine)
    context.sync_project_context(proj)
    _new_version(src)
    context.sync_project_context(proj)
    raw = f.read_bytes()
    assert raw.startswith(mine) and "두 번째 규칙".encode() in raw and raw.count(context.RULES_START.encode()) == 1


def test_utf16_user_file_stays_readable(src, proj):
    f = proj / "CLAUDE.md"
    f.write_bytes("# 내 규칙\r\n".encode("utf-16"))  # PowerShell 5.1의 > 가 만드는 형식
    context.sync_project_context(proj)
    _new_version(src)
    context.sync_project_context(proj)
    text = f.read_bytes().decode("utf-16")
    assert text.startswith("# 내 규칙\r\n") and "두 번째 규칙" in text and text.count(context.RULES_START) == 1


def test_old_full_copy_is_replaced_not_kept_as_user_text(src, proj):
    """지난 버전이 통째로 쓴 파일(CRLF)은 사용자 글이 아니다. 규칙이 두 벌 쌓이면 안 된다."""
    old = ("# DDS AI Engineer — 에이전트 작업 규칙\n\n옛 규칙\n\n## 부록: 사용 가능한 스킬\n필요할 때 아래 SKILL.md를 읽고 따르세요.\n\n"
           "- **nx-cad** — 설명  \n  `C:/x/.claude/skills/nx-cad/SKILL.md`\n").replace("\n", "\r\n").encode()
    for n in RULES:
        (proj / n).write_bytes(old)
    (proj / context.MANAGED).write_text('["nx-cad"]', encoding="utf-8")
    (proj / context.STAMP).write_text("옛날지문", encoding="utf-8")
    assert context.sync_project_context(proj) is True
    for n in RULES:
        raw = (proj / n).read_bytes()
        assert "옛 규칙".encode() not in raw and "첫 번째 규칙".encode() in raw and raw.startswith(context.RULES_START.encode())
        if os.name == "nt":
            assert is_hidden(proj / n)


def test_old_format_lookalike_without_dds_trace_is_user_file(src, proj):
    """DDS가 동기화한 흔적이 없으면 비슷하게 생겨도 사용자 파일이다."""
    mine = "# DDS Conversa 메모\n\n## 부록: 사용 가능한 스킬\n\n- **x** — y  \n  `z`\n".encode()
    (proj / "CLAUDE.md").write_bytes(mine)
    context.sync_project_context(proj)
    assert (proj / "CLAUDE.md").read_bytes().startswith(mine)


def test_empty_user_file_just_gets_rules(src, proj):
    (proj / "GEMINI.md").write_bytes(b"")
    context.sync_project_context(proj)
    assert "첫 번째 규칙" in (proj / "GEMINI.md").read_text(encoding="utf-8")


# ---------- 숨김·읽기 전용 파일에 다시 쓰기 ----------
@windows_only
def test_plain_open_w_really_fails_on_hidden_file(tmp_path):
    """이 시험이 깨지면 아래 대비가 필요 없어진 것이다. 일반 open('w')가 숨김 파일에서 막히는 것을 기록해 둔다."""
    f = tmp_path / "x.md"
    f.write_text("a", encoding="utf-8")
    _set(f, _attrs(f) | HIDDEN)
    with pytest.raises(PermissionError):
        f.write_text("b", encoding="utf-8")


@windows_only
def test_resync_over_hidden_and_readonly_targets(src, proj):
    """Agent 업데이트 뒤의 경로: 숨김 규칙·표시 파일과 숨김·읽기 전용 스킬 파일 위에 다시 복사한다."""
    context.sync_project_context(proj)
    skill_file = proj / ".claude" / "skills" / "nx-cad" / "SKILL.md"
    _set(skill_file, _attrs(skill_file) | HIDDEN)
    other = proj / ".gemini" / "skills" / "abaqus" / "SKILL.md"
    _set(other, _attrs(other) | READONLY)
    _set(proj / ".claude" / "skills", _attrs(proj / ".claude" / "skills") | HIDDEN)
    (src / "skills" / "nx-cad" / "SKILL.md").write_text("---\nname: nx-cad\ndescription: 새 설명\n---\n새 본문\n", encoding="utf-8")
    (src / "skills" / "abaqus" / "SKILL.md").write_text("---\nname: abaqus\ndescription: 새 설명2\n---\n새 본문2\n", encoding="utf-8")
    _new_version(src)
    assert context.sync_project_context(proj) is True
    assert "새 본문" in skill_file.read_text(encoding="utf-8") and is_hidden(skill_file)
    assert "새 본문2" in other.read_text(encoding="utf-8")
    assert "두 번째 규칙" in (proj / "CLAUDE.md").read_text(encoding="utf-8")
    for name in (*RULES, *TOPS, context.MANAGED, context.STAMP):
        assert is_hidden(proj / name), name
    assert not list(proj.glob("*.tmp")) and not list(proj.glob(".*.tmp"))


@windows_only
def test_readonly_source_can_be_copied_twice(src, proj):
    """설치본의 스킬 파일이 읽기 전용이면 copy2가 속성까지 옮겨, 다음 복사가 막힌다."""
    f = src / "skills" / "nx-cad" / "SKILL.md"
    _set(f, _attrs(f) | READONLY)
    try:
        context.sync_project_context(proj)
        _new_version(src)
        assert context.sync_project_context(proj) is True
    finally:
        _set(f, _attrs(f) & ~READONLY)
        for p in proj.rglob("SKILL.md"):
            _set(p, _attrs(p) & ~READONLY)


@windows_only
def test_readonly_user_rule_file_gets_rules_and_stays_readonly(src, proj):
    f = proj / "CLAUDE.md"
    f.write_text("# 내 규칙\n", encoding="utf-8")
    _set(f, _attrs(f) | READONLY)
    try:
        context.sync_project_context(proj)
        _new_version(src)
        context.sync_project_context(proj)
        text = f.read_text(encoding="utf-8")
        assert text.startswith("# 내 규칙\n") and "두 번째 규칙" in text and _attrs(f) & READONLY
    finally:
        _set(f, _attrs(f) & ~READONLY)


@windows_only
def test_removed_skill_inside_hidden_folder_is_cleaned(src, proj):
    context.sync_project_context(proj)
    shutil.rmtree(src / "skills" / "abaqus")
    assert context.sync_project_context(proj) is True
    for top in TOPS:
        assert not (proj / top / "skills" / "abaqus").exists()
    assert is_hidden(proj / ".claude")


def test_failed_write_leaves_stamp_so_next_run_retries_and_keeps_user_file(src, proj, monkeypatch):
    f = proj / "CLAUDE.md"
    f.write_text("# 내 규칙\n", encoding="utf-8")
    real = context._write_in_place

    def boom(target, data):
        if Path(target).name == "CLAUDE.md":
            raise OSError("잠겨 있음")
        return real(target, data)

    monkeypatch.setattr(context, "_write_in_place", boom, raising=False)
    monkeypatch.setattr(context.time, "sleep", lambda s: None)
    with pytest.raises(OSError, match="CLAUDE.md"):
        context.sync_project_context(proj)
    assert f.read_text(encoding="utf-8") == "# 내 규칙\n"
    assert not (proj / context.STAMP).exists()
    assert not [p.name for p in proj.iterdir() if p.name.endswith(".tmp")]
    monkeypatch.setattr(context, "_write_in_place", real)
    assert context.sync_project_context(proj) is True and "첫 번째 규칙" in f.read_text(encoding="utf-8")


def test_off_windows_attribute_calls_are_noops(src, proj, monkeypatch):
    monkeypatch.setattr(context, "_kernel32", lambda: None)
    assert context.sync_project_context(proj) is True
    assert context.sync_project_context(proj) is False
    assert context.dds_items(proj) >= {"CLAUDE.md", ".claude"}


# ---------- 웹 파일 탭 ----------
def _ws_project(tmp_path, src, folder=None):
    ws = Workspace(tmp_path / "ws", "http://srv")
    d = ws.project_dir(1, "브래킷", "uid1")
    if folder:
        ws.set_folder(1, "브래킷", "uid1", str(folder))
        d = ws.project_dir(1, "브래킷", "uid1")
    return ws, d


def test_file_tab_lists_user_files_only(tmp_path, src):
    ws, d = _ws_project(tmp_path, src)
    (d / "내 메모.txt").write_text("x", encoding="utf-8")
    context.sync_project_context(d)
    names = {e["name"] for e in ws.tree(1, "", "브래킷", project_uid="uid1")["entries"]}
    assert not names & {*RULES, *TOPS, context.MANAGED, context.STAMP}
    assert {"내 메모.txt", "project.json", "inputs", "runs", "studies"} <= names


def test_file_tab_keeps_users_rule_file_and_dot_folder(tmp_path, src):
    ws, d = _ws_project(tmp_path, src)
    (d / "CLAUDE.md").write_text("# 내 규칙\n", encoding="utf-8")
    (d / ".claude" / "skills" / "my-own").mkdir(parents=True)
    context.sync_project_context(d)
    top = {e["name"] for e in ws.tree(1, "", "브래킷", project_uid="uid1")["entries"]}
    assert {"CLAUDE.md", ".claude"} <= top and not top & {"AGENTS.md", "GEMINI.md", ".gemini", ".agents"}
    inside = {e["name"] for e in ws.tree(1, ".claude/skills", "브래킷", project_uid="uid1")["entries"]}
    assert inside == {"my-own"}


def test_file_tab_limit_counts_after_filtering(tmp_path, src):
    ws, d = _ws_project(tmp_path, src)
    context.sync_project_context(d)
    (d / "zz.txt").write_text("x", encoding="utf-8")
    visible = {p.name for p in d.iterdir()} - {*RULES, *TOPS, context.MANAGED, context.STAMP}
    entries = ws.tree(1, "", "브래킷", limit=len(visible), project_uid="uid1")["entries"]
    assert {e["name"] for e in entries} == visible  # 숨긴 항목이 한도를 차지해 사용자 파일이 잘리면 안 된다


def test_file_tab_folder_without_sync_lists_everything(tmp_path, src):
    ws, d = _ws_project(tmp_path, src)
    (d / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert "CLAUDE.md" in {e["name"] for e in ws.tree(1, "", "브래킷", project_uid="uid1")["entries"]}


# ---------- git ----------
_real_which = shutil.which
git_exe = shutil.which("git")
git_only = pytest.mark.skipif(not git_exe, reason="git 없음")


def _git(cwd, *args) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    r = subprocess.run([git_exe, *args], cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30, **hidden_kwargs())
    assert r.returncode == 0, r.stderr
    return r.stdout


def _exclude(repo) -> str:
    return (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")


def test_not_a_repo_gets_no_git_files(src, proj):
    context.sync_project_context(proj)
    assert not (proj / ".git").exists() and not (proj / ".gitignore").exists() and not (proj / ".geminiignore").exists()


@git_only
def test_repo_excludes_dds_items_and_leaves_user_files_alone(src, proj):
    _git(proj, "init", "-q")
    (proj / ".gitignore").write_text("*.odb\n", encoding="utf-8")
    (proj / "model.py").write_text("x", encoding="utf-8")
    exclude = proj / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text("# 내 제외\n/my-secret.txt\n", encoding="utf-8")
    context.sync_project_context(proj)
    assert (proj / ".gitignore").read_text(encoding="utf-8") == "*.odb\n"
    text = _exclude(proj)
    assert text.startswith("# 내 제외\n/my-secret.txt\n")
    status = _git(proj, "status", "--porcelain", "--untracked-files=all", "--ignored=no")
    assert ".gitignore" in status and "model.py" in status
    for name in (*RULES, *TOPS, context.MANAGED, context.STAMP, ".geminiignore"):
        assert name not in status, name
    _new_version(src)
    context.sync_project_context(proj)
    assert _exclude(proj) == text  # 몇 번을 돌아도 한 벌만


@git_only
def test_tracked_rule_file_is_not_excluded_and_commit_history_untouched(src, proj):
    _git(proj, "init", "-q")
    (proj / "CLAUDE.md").write_text("# 팀 규칙\n", encoding="utf-8")
    _git(proj, "add", "CLAUDE.md")
    _git(proj, "commit", "-qm", "규칙")
    context.sync_project_context(proj)
    text = _exclude(proj)
    assert "/CLAUDE.md" not in text and "/AGENTS.md" in text
    if os.name == "nt":
        assert not is_hidden(proj / "CLAUDE.md")


@git_only
def test_tracked_dds_only_file_is_skipped_silently(src, proj):
    _git(proj, "init", "-q")
    context.sync_project_context(proj)
    _git(proj, "add", "-f", "AGENTS.md", ".agents")
    _git(proj, "commit", "-qm", "잘못 올림")
    _new_version(src)
    context.sync_project_context(proj)
    text = _exclude(proj)
    assert "/AGENTS.md" not in text and "/.agents/" not in text and "/CLAUDE.md" in text


@git_only
def test_user_dot_folder_excludes_only_dds_skills(src, proj):
    _git(proj, "init", "-q")
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    context.sync_project_context(proj)
    text = _exclude(proj)
    assert "/.claude/skills/nx-cad/" in text and "/.claude/\n" not in text
    assert ".claude/settings.json" in _git(proj, "status", "--porcelain", "--untracked-files=all")


@git_only
def test_user_content_later_drops_folder_exclude(src, proj):
    """통째로 제외했던 .claude에 사용자가 파일을 넣으면 git이 그 파일을 몰래 무시하면 안 된다."""
    _git(proj, "init", "-q")
    context.sync_project_context(proj)
    assert "/.claude/\n" in _exclude(proj)
    (proj / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    context.sync_project_context(proj)
    assert "/.claude/\n" not in _exclude(proj)
    assert ".claude/settings.json" in _git(proj, "status", "--porcelain", "--untracked-files=all")


@git_only
def test_folder_inside_repo_uses_repo_relative_paths(src, tmp_path):
    repo = tmp_path / "repo"
    sub = repo / "해석" / "브래킷[1]"
    sub.mkdir(parents=True)
    _git(repo, "init", "-q")
    context.sync_project_context(sub)
    status = _git(repo, "status", "--porcelain", "--untracked-files=all")
    assert status == ""
    assert not (sub / ".geminiignore").exists()  # Gemini는 작업 폴더 바로 아래 .git/info/exclude만 읽어 이 경우 막히지 않는다


@git_only
def test_git_worktree_writes_to_common_exclude(src, tmp_path):
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    (main / "a.txt").write_text("x", encoding="utf-8")
    _git(main, "add", "a.txt")
    _git(main, "commit", "-qm", "첫")
    wt = tmp_path / "wt"
    _git(main, "worktree", "add", "-q", str(wt))
    assert (wt / ".git").is_file()
    context.sync_project_context(wt)
    assert "/CLAUDE.md" in _exclude(main)
    assert _git(wt, "status", "--porcelain", "--untracked-files=all") == ""


@git_only
def test_repo_created_after_sync_is_picked_up_on_skip_path(src, proj):
    context.sync_project_context(proj)
    _git(proj, "init", "-q")
    assert context.sync_project_context(proj) is False
    assert "/CLAUDE.md" in _exclude(proj)
    assert _git(proj, "status", "--porcelain", "--untracked-files=all") == ""


@windows_only
@git_only
def test_git_failure_never_stops_sync(src, proj, monkeypatch):
    _git(proj, "init", "-q")

    def broken(*a, **k):
        raise OSError("git 없음")

    monkeypatch.setattr(context.subprocess, "run", broken)
    assert context.sync_project_context(proj) is True
    assert "/CLAUDE.md" in _exclude(proj)  # 추적 여부를 몰라도 제외 목록은 무해하다: 이미 추적 중인 파일에는 효과가 없다
    assert is_hidden(proj / "CLAUDE.md")


# ---------- Gemini: .git/info/exclude에 든 경로는 read_file이 거부한다 ----------
@git_only
def test_geminiignore_reopens_excluded_skills_for_gemini(src, proj):
    _git(proj, "init", "-q")
    context.sync_project_context(proj)
    text = (proj / ".geminiignore").read_text(encoding="utf-8")
    for top in TOPS:
        assert f"!/{top}/\n" in text and f"!/{top}/**\n" in text
    assert "/CLAUDE.md" not in text
    if os.name == "nt":
        assert is_hidden(proj / ".geminiignore")
    assert "/.geminiignore" in _exclude(proj)


@git_only
def test_users_geminiignore_is_kept_visible(src, proj):
    _git(proj, "init", "-q")
    mine = "*.odb\n"
    (proj / ".geminiignore").write_text(mine, encoding="utf-8")
    context.sync_project_context(proj)
    f = proj / ".geminiignore"
    text = f.read_text(encoding="utf-8")
    assert text.startswith(mine) and "!/.claude/" in text
    assert "/.geminiignore" not in _exclude(proj)
    if os.name == "nt":
        assert not is_hidden(f)


@git_only
def test_tracked_geminiignore_is_not_touched(src, proj):
    _git(proj, "init", "-q")
    (proj / ".geminiignore").write_text("*.odb\n", encoding="utf-8")
    _git(proj, "add", ".geminiignore")
    _git(proj, "commit", "-qm", "무시 목록")
    context.sync_project_context(proj)
    assert (proj / ".geminiignore").read_text(encoding="utf-8") == "*.odb\n"
    assert _git(proj, "status", "--porcelain", ".geminiignore") == ""


# ---------- 한 저장소에 여러 프로젝트·추적 중인 .geminiignore·다시 만든 저장소 ----------
def _status(repo) -> str:
    return _git(repo, "status", "--porcelain", "--untracked-files=all")


def _rm_git(repo):
    def force(fn, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    shutil.rmtree(repo / ".git", onexc=force)


def _commit_geminiignore(repo, text="*.odb\n"):
    (repo / ".geminiignore").write_text(text, encoding="utf-8")
    _git(repo, "add", ".geminiignore")
    _git(repo, "commit", "-qm", "무시 목록")


@git_only
def test_two_projects_in_one_repo_both_stay_excluded(src, tmp_path):
    repo = tmp_path / "repo"
    a, b = repo / "A", repo / "B"
    a.mkdir(parents=True)
    b.mkdir()
    _git(repo, "init", "-q")
    context.sync_project_context(a)
    assert _status(repo) == ""
    context.sync_project_context(b)
    assert _status(repo) == ""  # B의 동기화가 A의 항목을 지우면 A가 커밋에 섞인다
    assert context.sync_project_context(a) is False
    assert _status(repo) == ""
    text = _exclude(repo)
    assert "/A/CLAUDE.md" in text and "/B/CLAUDE.md" in text
    _new_version(src)
    context.sync_project_context(a)
    context.sync_project_context(b)
    assert _exclude(repo) == text  # 프로젝트마다 한 벌만


@git_only
def test_geminiignore_committed_by_team_is_left_alone_and_no_repeat_copy(src, tmp_path):
    """팀이 .geminiignore를 올려 pull이 DDS 사본을 덮어쓴 뒤: 다시 복사하지 않고, 숨기지 않고, 고치지 않는다."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q")
    (origin / "a.txt").write_text("x", encoding="utf-8")
    _git(origin, "add", "a.txt")
    _git(origin, "commit", "-qm", "첫")
    proj = tmp_path / "proj"
    _git(tmp_path, "clone", "-q", str(origin), str(proj))
    assert context.sync_project_context(proj) is True
    assert (proj / ".geminiignore").exists()
    _commit_geminiignore(origin)
    _git(proj, "pull", "-q")
    for _ in range(3):
        assert context.sync_project_context(proj) is False
    f = proj / ".geminiignore"
    assert f.read_text(encoding="utf-8") == "*.odb\n"
    assert ".geminiignore" not in context.dds_items(proj)
    if os.name == "nt":
        assert not is_hidden(f)
    assert _status(proj).count(".geminiignore") == 0


@git_only
def test_deleting_repo_and_geminiignore_does_not_recopy(src, proj):
    _git(proj, "init", "-q")
    context.sync_project_context(proj)
    (proj / ".geminiignore").unlink()
    _rm_git(proj)
    for _ in range(4):
        assert context.sync_project_context(proj) is False
    assert ".geminiignore" not in context.dds_items(proj)


@git_only
def test_tracked_geminiignore_keeps_skill_folders_out_of_exclude(src, proj):
    """다시 열 수 없으면 스킬 폴더를 제외 목록에 넣지 않는다. 넣으면 Gemini가 스킬을 읽지 못한다(실제 코드로 확인)."""
    _git(proj, "init", "-q")
    _commit_geminiignore(proj)
    context.sync_project_context(proj)
    text = _exclude(proj)
    assert "/CLAUDE.md" in text and context.STAMP in text
    for top in TOPS:
        assert f"/{top}/" not in text
    assert (proj / ".geminiignore").read_text(encoding="utf-8") == "*.odb\n"
    assert ".claude/skills/" in _status(proj)  # 대신 git에는 새 파일로 보인다


@git_only
@pytest.mark.parametrize("failure", ["no_git", "git_error"])
def test_git_failure_never_writes_existing_geminiignore(src, proj, monkeypatch, failure):
    _git(proj, "init", "-q")
    _commit_geminiignore(proj)
    cfg = proj / ".git" / "config"
    good = cfg.read_text(encoding="utf-8")
    if failure == "no_git":
        monkeypatch.setattr(context.shutil, "which", lambda *_a, **_k: None)
    else:
        cfg.write_text(good.replace("repositoryformatversion = 0", "repositoryformatversion = 99"), encoding="utf-8")
    assert context.sync_project_context(proj) is True
    assert (proj / ".geminiignore").read_text(encoding="utf-8") == "*.odb\n"
    text = (proj / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert "/.claude/" not in text  # 추적 여부를 모르니 다시 열지 못했다. 스킬 폴더를 막지 않는다
    # git이 돌아오면 추적 중임을 알고 여전히 건드리지 않는다
    if failure == "no_git":
        monkeypatch.setattr(context.shutil, "which", _real_which)
    else:
        cfg.write_text(good, encoding="utf-8")
    assert context.sync_project_context(proj) is False
    assert (proj / ".geminiignore").read_text(encoding="utf-8") == "*.odb\n"
    assert _status(proj).count(".geminiignore") == 0


@git_only
def test_untracked_user_geminiignore_written_when_git_works(src, proj):
    _git(proj, "init", "-q")
    (proj / ".geminiignore").write_text("*.odb\n", encoding="utf-8")
    context.sync_project_context(proj)
    assert "!/.claude/" in (proj / ".geminiignore").read_text(encoding="utf-8")
    assert "/.claude/" in _exclude(proj)


@git_only
def test_no_block_in_user_geminiignore_when_nothing_to_reopen(src, proj):
    _git(proj, "init", "-q")
    context.sync_project_context(proj)
    _git(proj, "add", "-f", *TOPS)
    _git(proj, "commit", "-qm", "스킬까지 올림")
    f = proj / ".geminiignore"
    f.unlink()
    f.write_text("*.odb\n", encoding="utf-8")
    context.sync_project_context(proj)
    assert f.read_text(encoding="utf-8") == "*.odb\n"


@git_only
def test_recreated_repo_gets_exclude_back_on_skip_path(src, proj):
    _git(proj, "init", "-q")
    context.sync_project_context(proj)
    _rm_git(proj)
    _git(proj, "init", "-q")
    assert context.sync_project_context(proj) is False
    assert _status(proj) == ""
    assert context.sync_project_context(proj) is False


@git_only
def test_exclude_changed_from_outside_is_restored(src, proj):
    _git(proj, "init", "-q")
    context.sync_project_context(proj)
    (proj / ".git" / "info" / "exclude").write_text("# 사용자가 비움\n", encoding="utf-8")
    assert context.sync_project_context(proj) is False
    assert _status(proj) == "" and _exclude(proj).startswith("# 사용자가 비움\n")


@git_only
def test_failed_exclude_write_keeps_geminiignore_record_and_retries(src, proj):
    _git(proj, "init", "-q")
    info = proj / ".git" / "info"
    shutil.rmtree(info, ignore_errors=True)
    info.write_text("막힘", encoding="utf-8")  # 일부러 쓰기 실패를 만든다
    assert context.sync_project_context(proj) is True
    assert ".geminiignore" in context.dds_items(proj)
    if os.name == "nt":
        assert is_hidden(proj / ".geminiignore")
    info.unlink()
    assert context.sync_project_context(proj) is False  # 다음 세션에 다시 시도한다
    assert _status(proj) == ""


@git_only
def test_skip_path_in_repo_runs_no_git_and_reads_no_files(src, proj, monkeypatch):
    _git(proj, "init", "-q")
    context.sync_project_context(proj)

    def no_call(*a, **k):
        raise AssertionError("건너뛰는 때에는 git을 부르거나 파일을 읽거나 쓰지 않는다")

    monkeypatch.setattr(context.subprocess, "run", no_call)
    monkeypatch.setattr(Path, "read_bytes", no_call)
    monkeypatch.setattr(context, "_write_bytes", no_call)
    assert context.sync_project_context(proj) is False


@windows_only
def test_skipped_sync_with_user_dot_folder_does_not_walk_skill_files(src, proj, monkeypatch):
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    context.sync_project_context(proj)
    calls = []
    real = context._attrs
    monkeypatch.setattr(context, "_attrs", lambda p: (calls.append(p), real(p))[1])
    assert context.sync_project_context(proj) is False
    assert all("SKILL.md" not in str(p) for p in calls)
    assert len(calls) <= 2 * 8 + 2  # 사용자 폴더 안에서는 DDS 스킬 수만큼 는다(여기서는 2개)


# ---------- 사용자 파일은 같은 파일로 남는다 ----------
@windows_only
def test_rewriting_keeps_same_file_identity_and_name_case(src, proj):
    """바꿔 넣으면 새 파일이 되어 ACL·하드 링크·이름 대소문자를 잃는다. 같은 파일이면 ACL도 그대로다."""
    f = proj / "claude.md"
    f.write_text("# 내 규칙\n", encoding="utf-8")
    ino = f.stat().st_ino
    context.sync_project_context(proj)
    assert "claude.md" in os.listdir(proj) and "CLAUDE.md" not in os.listdir(proj)
    assert f.stat().st_ino == ino
    _new_version(src)
    context.sync_project_context(proj)
    assert f.stat().st_ino == ino


def test_hard_linked_rule_files_stay_linked_with_one_block(src, proj):
    a, b = proj / "CLAUDE.md", proj / "AGENTS.md"
    a.write_text("# 내 규칙\n", encoding="utf-8")
    os.link(a, b)
    context.sync_project_context(proj)
    assert a.stat().st_ino == b.stat().st_ino
    assert a.read_bytes().count(context.RULES_START.encode()) == 1
    _new_version(src)
    context.sync_project_context(proj)
    assert a.stat().st_ino == b.stat().st_ino and "두 번째 규칙".encode() in b.read_bytes()


def test_new_file_write_failure_leaves_no_partial_file(src, proj, monkeypatch):
    real = os.replace

    def boom(a, b):
        if Path(b).name == "AGENTS.md":
            raise OSError("막힘")
        return real(a, b)

    monkeypatch.setattr(context.os, "replace", boom)
    with pytest.raises(OSError, match="AGENTS.md"):
        context.sync_project_context(proj)
    assert not (proj / "AGENTS.md").exists()
    assert not [p.name for p in proj.iterdir() if p.name.endswith(".tmp")]


# ---------- 지난 버전이 통째로 쓴 파일 ----------
def _old_copy(path_prefix="C:/x", crlf=True):
    text = ("# DDS AI Engineer — 에이전트 작업 규칙\n\n옛 규칙\n\n## 부록: 사용 가능한 스킬\n필요할 때 아래 SKILL.md를 읽고 따르세요.\n\n"
            f"- **nx-cad** — 설명  \n  `{path_prefix}/.claude/skills/nx-cad/SKILL.md`\n")
    return (text.replace("\n", "\r\n") if crlf else text).encode()


def test_old_copy_plus_memo_keeps_only_memo_and_one_rule_set(src, proj):
    memo = "내가 덧붙인 메모\r\n".encode()
    (proj / "CLAUDE.md").write_bytes(_old_copy() + memo)
    (proj / context.STAMP).write_text("옛날지문", encoding="utf-8")
    context.sync_project_context(proj)
    raw = (proj / "CLAUDE.md").read_bytes()
    assert raw.startswith(memo) and "옛 규칙".encode() not in raw and "첫 번째 규칙".encode() in raw
    assert raw.count(context.RULES_START.encode()) == 1 and raw.count("에이전트 작업 규칙".encode()) == 1
    if os.name == "nt":
        assert not is_hidden(proj / "CLAUDE.md")  # 사용자 글이 있으니 보인다


def test_old_copy_with_text_before_title_keeps_that_text(src, proj):
    head = "# 우리 팀 메모\r\n\r\n".encode()
    (proj / "CLAUDE.md").write_bytes(head + _old_copy())
    (proj / context.STAMP).write_text("옛날지문", encoding="utf-8")
    context.sync_project_context(proj)
    raw = (proj / "CLAUDE.md").read_bytes()
    assert raw.startswith(head) and "옛 규칙".encode() not in raw


def test_very_old_copy_without_markers_is_recognized_by_its_own_path(src, proj):
    """0.2.7 이전에는 동기화 기록을 남기지 않았다. 목록이 이 폴더의 경로를 가리키면 DDS가 쓴 것이다."""
    for n in RULES:
        (proj / n).write_bytes(_old_copy(proj.as_posix()))
    context.sync_project_context(proj)
    for n in RULES:
        raw = (proj / n).read_bytes()
        assert "옛 규칙".encode() not in raw and raw.startswith(context.RULES_START.encode())
        if os.name == "nt":
            assert is_hidden(proj / n)


def test_old_copy_of_other_folder_without_trace_stays_user_text(src, proj):
    mine = _old_copy("D:/other")
    (proj / "CLAUDE.md").write_bytes(mine)
    context.sync_project_context(proj)
    assert (proj / "CLAUDE.md").read_bytes().startswith(mine)


def test_old_copy_rule_never_applies_after_new_format(src, proj):
    """새 형식이 된 뒤에 사용자가 옛 글을 일부러 넣었다면 지우지 않는다. 구간 안의 제목·목록도 옛 사본으로 보지 않는다."""
    context.sync_project_context(proj)
    f = proj / "CLAUDE.md"
    mine = _old_copy()
    with open(f, "r+b") as h:
        body = h.read()
        h.seek(0)
        h.write(mine + body)
    _new_version(src)
    context.sync_project_context(proj)
    assert f.read_bytes().startswith(mine)
    g, st = proj / "AGENTS.md", proj / context.STAMP
    with open(st, "r+b") as h:  # 기록이 깨져도 구간이 든 파일은 옛 사본이 아니다
        h.truncate(5)
    before = g.read_bytes()
    context.sync_project_context(proj)
    assert g.read_bytes() == before


# ---------- 사용자 스킬·동시 동기화 ----------
def test_non_utf8_user_skill_does_not_stop_sync(src, proj):
    d = proj / ".claude" / "skills" / "mine"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_bytes("---\nname: mine\ndescription: 내 스킬\n---\n".encode("cp949"))
    assert context.sync_project_context(proj) is True
    text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "**mine**" in text and "첫 번째 규칙" in text


def test_concurrent_syncs_of_one_folder_do_not_overlap(src, proj, monkeypatch):
    """세션 시작이 스레드에서 돌아 같은 폴더를 동시에 맞출 수 있다. 두 복사가 겹치면 파일 공유 위반(WinError 32)이 난다."""
    real = shutil.copytree
    live, peak = [0], [0]
    gate = threading.Lock()

    def slow(*a, **k):
        with gate:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.01)
        try:
            return real(*a, **k)
        finally:
            with gate:
                live[0] -= 1

    monkeypatch.setattr(context.shutil, "copytree", slow)
    errors = []

    def run():
        try:
            context.sync_project_context(proj)
        except Exception as e:  # noqa: BLE001 — 어떤 실패든 기록해 아래에서 검사한다
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and peak[0] == 1


@git_only
def test_projects_syncing_together_keep_both_exclude_blocks(src, tmp_path):
    repo = tmp_path / "repo"
    dirs = [repo / n for n in ("A", "B", "C", "D")]
    for d in dirs:
        d.mkdir(parents=True)
    _git(repo, "init", "-q")
    threads = [threading.Thread(target=context.sync_project_context, args=(d,)) for d in dirs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _status(repo) == ""


def test_copy_retries_when_scanner_holds_target(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_text("x", encoding="utf-8")
    real = shutil.copy2
    tries = []

    def flaky(s, d):
        tries.append(d)
        if len(tries) < 3:
            raise PermissionError(13, "다른 프로세스가 사용 중")
        return real(s, d)

    monkeypatch.setattr(context.shutil, "copy2", flaky)
    monkeypatch.setattr(context.time, "sleep", lambda s: None)
    context._copy_file(a, b)
    assert len(tries) == 3 and b.read_text(encoding="utf-8") == "x"


@windows_only
def test_rule_file_tells_ai_how_to_list_hidden_skills(src, proj):
    """PowerShell의 Get-ChildItem은 -Force 없이 Hidden 항목을 보여 주지 않는다. AI가 스킬이 없다고 오해하지 않게 적어 둔다."""
    context.sync_project_context(proj)
    assert "Get-ChildItem -Force" in (proj / "CLAUDE.md").read_text(encoding="utf-8")


def test_state_file_broken_means_resync_not_crash(src, proj):
    context.sync_project_context(proj)
    f = proj / context.STAMP
    if os.name == "nt":
        _set(f, _attrs(f) & ~HIDDEN)
    f.write_text("지문\n{깨진", encoding="utf-8")
    assert context.sync_project_context(proj) is True
    assert json.loads(f.read_text(encoding="utf-8").partition("\n")[2])["hidden"]
    assert context.dds_items(proj)


def test_state_file_paths_cannot_escape(src, proj):
    context.sync_project_context(proj)
    f = proj / context.STAMP
    head, _, body = f.read_text(encoding="utf-8").partition("\n")
    st = json.loads(body)
    st["hidden"].append("../밖")
    if os.name == "nt":
        _set(f, _attrs(f) & ~HIDDEN)
    f.write_text(head + "\n" + json.dumps(st), encoding="utf-8")
    assert "../밖" not in context.dds_items(proj)
    assert context.sync_project_context(proj) is True  # 망가진 기록은 버리고 다시 맞춘다


def test_rule_file_mode_kept_off_windows(src, proj):
    if os.name == "nt":
        pytest.skip("POSIX 권한 시험")
    f = proj / "CLAUDE.md"
    f.write_text("# 내 규칙\n", encoding="utf-8")
    f.chmod(0o640)
    context.sync_project_context(proj)
    assert stat.S_IMODE(f.stat().st_mode) == 0o640


def test_module_runs_without_visible_git_console():
    """git 호출은 다른 곳과 같이 hidden_kwargs를 쓴다(콘솔 창이 보이면 안 된다)."""
    source = Path(context.__file__).read_text(encoding="utf-8")
    assert "**hidden_kwargs()" in source and "shell=True" not in source and "attrib.exe" not in source
