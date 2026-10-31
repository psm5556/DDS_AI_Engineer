"""프로젝트 폴더 이름: 항상 P0025_<이름>이고 프로젝트 이름을 따라간다.
Windows는 CLI·MCP·솔버가 cwd를 둔 폴더의 이름을 바꾸지 못한다(WinError 5·32). 그래서 실제 자식 프로세스로 잠그고 시험한다.
잠겨 있으면 새 이름을 접합(junction)으로 보여 주고, 풀리면 실제 이름을 바꾼 뒤 옛 이름을 숨긴 접합으로 남긴다."""
import asyncio
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.dds_agent import config, context
from agent.dds_agent import main as M
from agent.dds_agent import workspace as W
from agent.dds_agent.config import AgentSettings
from agent.dds_agent.executor import CaseExecutor
from agent.dds_agent.session_history import encode_claude_path, move_claude_session
from agent.dds_agent.workspace import Workspace, slug
from agent.tests.fake_acp import count, read_log
from agent.tests.stack import Stack, wait_for

windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows의 폴더 잠금·접합을 시험한다")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Holder:
    """실제 자식 프로세스. cwd를 폴더에 두거나 파일을 열어 둔다. 솔버·CLI가 Windows에서 하는 일과 같다."""

    def __init__(self, cwd: Path, open_file: Path | None = None):
        code = "import sys\n"
        if open_file:
            code += f"f = open({str(open_file)!r}, 'rb')\n"
        code += "print('ready', flush=True)\nsys.stdin.read()\n"
        self.p = subprocess.Popen([sys.executable, "-c", code], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
        assert self.p.stdout.readline().strip() == b"ready"

    def alive(self) -> bool:
        return self.p.poll() is None

    def stop(self):
        if self.alive():
            self.p.stdin.close()
            self.p.wait(timeout=30)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.stop()


def _hidden(p: Path) -> bool:
    return bool((context._attrs(p) or 0) & context._HIDDEN)


def _shown(root: Path, prefix: str = "P") -> list[str]:
    """탐색기 기본 설정(숨긴 항목 빼기)으로 보이는 이름. Linux에는 Hidden 속성이 없어 옛 이름 링크가 보이므로, 그곳에서는 실제 폴더만 센다."""
    hide = _hidden if context._kernel32() else _is_alias
    return sorted(e.name for e in os.scandir(root) if e.name.startswith(prefix) and not hide(Path(e.path)))


def _info(d: Path) -> dict:
    return json.loads((d / "project.json").read_text(encoding="utf-8"))


def _index(root: Path) -> dict:
    return json.loads((root / "index.json").read_text(encoding="utf-8"))


def _is_alias(p: Path) -> bool:
    return os.path.isjunction(p) or p.is_symlink()


# ---------- 이름 규칙 ----------
@pytest.mark.parametrize("name,want", [
    ("브래킷 경량화", "브래킷-경량화"),
    ("a/b:c*d?e\"f<g>h|i", "a-b-c-d-e-f-g-h-i"),
    ("끝에 점...", "끝에-점"),
    ("  앞뒤 공백  ", "앞뒤-공백"),
    ("R&D 100%", "R-D-100"),
    ("줄\n바꿈\t탭", "줄-바꿈-탭"),
    ("CON", "CON"),
    ("가" * 60, "가" * 24),
])
def test_slug_is_safe_for_windows(name, want):
    assert slug(name) == want


@pytest.mark.parametrize("name", ["", "   ", "...", "---", "///", None])
def test_slug_of_nothing_is_untitled(name):
    assert slug(name) == "untitled"


def test_new_folder_is_always_id_and_name(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    assert ws.project_dir(25, "브래킷 설계", "uid25").name == "P0025_브래킷-설계"
    # 이름을 모르는 호출도 번호만 있는 폴더를 만들지 않는다
    assert ws.project_dir(26).name == "P0026_untitled"
    assert ws.project_dir(27, "", "uid27").name == "P0027_untitled"
    assert not [p.name for p in tmp_path.iterdir() if p.name in ("P0025", "P0026", "P0027")]


def test_reserved_names_make_real_folders(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    for i, name in enumerate(("CON", "NUL", "COM1", "LPT1", "AUX", "PRN"), start=1):
        d = ws.project_dir(i, name, f"uid{i}")
        assert d.is_dir() and d.name == f"P{i:04d}_{name}" and (d / "inputs").is_dir()


def test_long_workspace_path_shortens_name(tmp_path):
    root = tmp_path / ("w" * (200 - len(str(tmp_path))))
    ws = Workspace(root, "http://srv")
    d = ws.project_dir(1, "가" * 60, "uid1")
    assert d.name == "P0001_" + "가" * W.SLUG_MIN  # 짧은 작업 공간에서는 24자까지 쓴다


# ---------- 잠기지 않은 폴더: 바로 실제 이름을 바꾼다 ----------
def test_free_folder_is_really_renamed(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음 이름", "u25")
    (d / "inputs" / "a.stp").write_text("A")
    res = ws.sync_name(25, "u25", "새 이름", may_move=True)
    new = tmp_path / "P0025_새-이름"
    assert res["moved"] and new.is_dir() and not _is_alias(new)
    assert (new / "inputs" / "a.stp").read_text() == "A"
    assert ws.project_dir(25, "새 이름", "u25") == new
    assert _info(new)["name"] == "새 이름" and _info(new)["folder"] == new.name
    assert _index(tmp_path)["projects"]["25"]["folder"] == new.name
    assert not _index(tmp_path)["projects"]["25"]["pending"]


@windows_only
def test_old_name_keeps_working_after_real_rename(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음 이름", "u25")
    (d / "scripts").mkdir(exist_ok=True)
    (d / "scripts" / "run.py").write_text("x")
    old = str(d / "scripts" / "run.py")
    ws.sync_name(25, "u25", "새 이름", may_move=True)
    # AI 스크립트·CAD 파일에 적힌 옛 절대 경로
    assert Path(old).read_text() == "x"
    assert _is_alias(d) and _hidden(d)
    assert _shown(tmp_path) == ["P0025_새-이름"]
    Path(old).with_name("new.txt").write_text("y")
    assert (tmp_path / "P0025_새-이름" / "scripts" / "new.txt").read_text() == "y"


@windows_only
def test_second_rename_keeps_every_old_name_working(tmp_path):
    """두 번째 실제 이름 바꾸기는 옛 접합을 끊는다(측정값). 다시 연결해 둔다."""
    ws = Workspace(tmp_path, "http://srv")
    first = ws.project_dir(25, "하나", "u25")
    ws.sync_name(25, "u25", "둘", may_move=True)
    ws.sync_name(25, "u25", "셋", may_move=True)
    real = tmp_path / "P0025_셋"
    assert real.is_dir() and not _is_alias(real)
    for old in (first, tmp_path / "P0025_둘"):
        assert _is_alias(old) and _hidden(old) and (old / "project.json").is_file()
    assert _shown(tmp_path) == ["P0025_셋"]


@windows_only
def test_rename_back_to_old_name(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    a = ws.project_dir(25, "가", "u25")
    ws.sync_name(25, "u25", "나", may_move=True)
    assert _is_alias(a)
    ws.sync_name(25, "u25", "가", may_move=True)
    assert a.is_dir() and not _is_alias(a)
    b = tmp_path / "P0025_나"
    assert _is_alias(b) and _hidden(b) and _shown(tmp_path) == ["P0025_가"]


def test_same_slug_does_not_touch_folder(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "a b", "u25")
    res = ws.sync_name(25, "u25", "a-b", may_move=True)
    assert not res.get("moved") and d.is_dir() and not _is_alias(d)
    assert _info(d)["name"] == "a-b" and d.name == "P0025_a-b"


def test_blank_name_keeps_folder(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    for bad in ("", "   ", "...", None):
        ws.sync_name(25, "u25", bad, may_move=True)
        assert d.is_dir() and d.name == "P0025_브래킷" and _info(d)["name"] == "브래킷"


def test_sync_twice_is_idempotent(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    ws.project_dir(25, "처음", "u25")
    ws.sync_name(25, "u25", "다음", may_move=True)
    before = sorted(p.name for p in tmp_path.iterdir())
    stamp = (tmp_path / "P0025_다음" / "project.json").read_bytes()
    assert not ws.sync_name(25, "u25", "다음", may_move=True).get("moved")
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert (tmp_path / "P0025_다음" / "project.json").read_bytes() == stamp


def test_quick_renames_land_on_the_last_name(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    ws.project_dir(25, "시작", "u25")
    for n in ("하나", "둘", "셋", "넷"):
        ws.sync_name(25, "u25", n, may_move=True)
    assert ws.project_dir(25, "넷", "u25").name == "P0025_넷"
    assert _shown(tmp_path) == ["P0025_넷"]


def test_case_only_rename(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    ws.project_dir(25, "abc", "u25")
    assert ws.sync_name(25, "u25", "ABC", may_move=True)["moved"]
    assert [e.name for e in os.scandir(tmp_path) if e.is_dir() and not _is_alias(Path(e.path))] == ["P0025_ABC"]
    assert _shown(tmp_path) == ["P0025_ABC"]


def test_name_collision_with_another_project_folder(tmp_path):
    """서버 DB를 새로 만들어 번호가 겹치면 같은 이름의 폴더가 이미 있을 수 있다. 덮어쓰지 않고 uid를 붙인다."""
    ws = Workspace(tmp_path, "http://srv")
    mine = ws.project_dir(25, "내 것", "aaaaaaaa")
    theirs = ws.project_dir(25, "다른 것", "bbbbbbbb")
    (theirs / "inputs" / "t.txt").write_text("T")
    ws.sync_name(25, "aaaaaaaa", "다른 것", may_move=True)
    new = tmp_path / "P0025_다른-것_aaaaaa"
    assert new.is_dir() and _info(new)["uid"] == "aaaaaaaa"
    assert (theirs / "inputs" / "t.txt").read_text() == "T" and _info(theirs)["uid"] == "bbbbbbbb"
    assert mine.name != new.name


def test_project_not_on_this_pc_makes_no_folder(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    assert ws.sync_name(99, "u99", "다른 PC의 프로젝트", may_move=True) == {"found": False}
    assert not list(tmp_path.glob("P0099*"))


def test_user_chosen_folder_is_never_renamed(tmp_path):
    ws = Workspace(tmp_path / "ws", "http://srv")
    mine = tmp_path / "Work" / "내폴더"
    ws.project_dir(25, "처음", "u25")
    ws.set_folder(25, "처음", "u25", str(mine))
    res = ws.sync_name(25, "u25", "새 이름", may_move=True)
    assert res.get("custom") and mine.is_dir() and not _is_alias(mine)
    assert not (tmp_path / "Work" / "P0025_새-이름").exists()
    assert _info(mine)["name"] == "새 이름"
    # 기본 위치 폴더는 되돌렸을 때 따라온다
    assert (tmp_path / "ws" / "P0025_처음").is_dir()
    ws.set_folder(25, "새 이름", "u25", None)
    ws.sync_name(25, "u25", "새 이름", may_move=True)
    assert ws.project_dir(25, "새 이름", "u25").name == "P0025_새-이름"


def test_stale_name_does_not_rename_or_overwrite(tmp_path):
    """미리 띄우기·대기 중인 케이스·session_project는 옛 이름을 들고 있다. 찾기만 하고 되돌리지 않는다."""
    ws = Workspace(tmp_path, "http://srv")
    ws.project_dir(25, "옛 이름", "u25")
    ws.sync_name(25, "u25", "새 이름", may_move=True)
    d = tmp_path / "P0025_새-이름"
    assert ws.project_dir(25, "옛 이름", "u25") == d
    assert ws.study_dir(25, 3, "DOE", "옛 이름", "u25").parent.parent == d
    assert _info(d)["name"] == "새 이름"
    assert _index(tmp_path)["projects"]["25"]["name"] == "새 이름"


def test_hand_renamed_folder_is_respected(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "이름", "u25")
    hand = d.with_name("P0025_내가-지은-이름")
    d.rename(hand)
    assert not ws.sync_name(25, "u25", "이름", may_move=True).get("moved")
    assert hand.is_dir() and not _is_alias(hand) and _info(hand)["folder"] == hand.name
    # 프로젝트 이름을 다시 바꾸면 그때는 따라간다
    assert ws.sync_name(25, "u25", "새 이름", may_move=True)["moved"]
    assert (tmp_path / "P0025_새-이름").is_dir()


def test_old_folder_without_record_is_renamed_once(tmp_path):
    """이 기능 전에 만든 폴더: 이름이 맞지 않으면 한 번 맞춘다."""
    d = tmp_path / "P0025_브래킷-CAD를-만들고-해석-모델을-구성한"
    d.mkdir()
    (d / "project.json").write_text(json.dumps({"project_id": 25, "uid": "u25", "name": "브래킷-CAD를 만들고", "folder": d.name}), encoding="utf-8")
    ws = Workspace(tmp_path, "http://srv")
    assert ws.sync_name(25, "u25", "브래킷 E2E 설계", may_move=True)["moved"]
    assert (tmp_path / "P0025_브래킷-E2E-설계").is_dir()


# ---------- 번호만 있는 폴더 옮겨 오기 ----------
def _bare(root: Path, pid=25, **info) -> Path:
    d = root / f"P{pid:04d}"
    (d / "runs" / "20261030_trial").mkdir(parents=True)
    (d / "runs" / "20261030_trial" / "log.txt").write_text("run")
    (d / "project.json").write_text(json.dumps({"project_id": pid, "uid": None, "name": "", "server": "http://srv", **info}), encoding="utf-8")
    return d


def test_lone_bare_folder_gets_the_name(tmp_path):
    bare = _bare(tmp_path)
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    assert d == bare  # 새 폴더를 또 만들지 않는다
    assert ws.sync_name(25, "u25", "브래킷", may_move=True)["moved"]
    new = tmp_path / "P0025_브래킷"
    assert (new / "runs" / "20261030_trial" / "log.txt").read_text() == "run"
    assert _info(new)["uid"] == "u25" and _shown(tmp_path) == ["P0025_브래킷"]


def test_bare_next_to_named_moves_files_without_overwriting(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    (d / "scripts" / "same.py").write_text("같음")
    (d / "scripts" / "clash.py").write_text("이름 있는 폴더의 내용")
    bare = _bare(tmp_path)
    (bare / "scripts").mkdir()
    (bare / "scripts" / "same.py").write_text("같음")
    (bare / "scripts" / "clash.py").write_text("번호 폴더의 내용")
    (bare / "scripts" / "only.py").write_text("여기만")

    ws.sync_name(25, "u25", "브래킷", may_move=True)

    assert (d / "runs" / "20261030_trial" / "log.txt").read_text() == "run"
    assert (d / "scripts" / "only.py").read_text() == "여기만"
    assert (d / "scripts" / "clash.py").read_text() == "이름 있는 폴더의 내용"  # 덮어쓰지 않는다
    assert (bare / "scripts" / "clash.py").read_text() == "번호 폴더의 내용"  # 잃지 않는다
    assert not (bare / "scripts" / "same.py").exists()
    notice = ws.tree(25, "", "브래킷", project_uid="u25")["notice"]
    assert "P0025" in notice and "1개" in notice and "덮어쓰지" in notice and "정리" in notice
    assert ws.project_dir(25, "브래킷", "u25") == d
    # 다시 돌려도 되풀이하지 않는다
    before = _info(d)["merged"]
    ws.sync_name(25, "u25", "브래킷", may_move=True)
    assert _info(d)["merged"] == before


def test_bare_with_nothing_left_is_removed(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    bare = _bare(tmp_path)
    ws.sync_name(25, "u25", "브래킷", may_move=True)
    assert (d / "runs" / "20261030_trial" / "log.txt").read_text() == "run"
    assert not bare.is_dir() or _is_alias(bare)
    assert "옮겼습니다" in ws.tree(25, "", "브래킷", project_uid="u25")["notice"]


def test_bare_of_another_project_is_left_alone(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    bare = _bare(tmp_path, uid="someone-else")
    ws.sync_name(25, "u25", "브래킷", may_move=True)
    assert (bare / "runs" / "20261030_trial" / "log.txt").read_text() == "run"
    assert not (d / "runs" / "20261030_trial").exists()
    other_server = _bare(tmp_path, pid=26, server="http://other")
    assert ws.project_dir(26, "딴 서버", "u26") != other_server
    assert (other_server / "runs" / "20261030_trial" / "log.txt").read_text() == "run"


@windows_only
def test_bare_in_use_waits(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    bare = _bare(tmp_path)
    with Holder(bare / "runs") as h:
        ws.sync_name(25, "u25", "브래킷", may_move=True)
        assert h.alive() and (bare / "runs" / "20261030_trial" / "log.txt").exists()
        assert not (d / "runs" / "20261030_trial").exists()
        assert _index(tmp_path)["projects"]["25"]["pending"]
    ws.sync_name(25, "u25", None, may_move=True)
    assert (d / "runs" / "20261030_trial" / "log.txt").read_text() == "run"
    assert not _index(tmp_path)["projects"]["25"]["pending"]


# ---------- 잠긴 폴더: 실제 프로세스가 cwd를 둔다 ----------
@windows_only
@pytest.mark.parametrize("where", ["root", "sub", "file"])
def test_locked_folder_shows_new_name_and_keeps_holder(tmp_path, where):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷-CAD를 만들고", "u25")
    (d / "inputs" / "model.stp").write_text("M")
    cwd = {"root": d, "sub": d / "runs", "file": tmp_path}[where]
    with Holder(cwd, d / "inputs" / "model.stp" if where == "file" else None) as h:
        with pytest.raises(PermissionError):  # 정말 잠겼는지 먼저 확인한다
            os.rename(d, d.with_name("probe"))
        started = time.perf_counter()
        res = ws.sync_name(25, "u25", "브래킷 E2E 설계", may_move=True)
        assert time.perf_counter() - started < 2  # 턴을 막지 않는다
        new = tmp_path / "P0025_브래킷-E2E-설계"
        assert h.alive() and res["pending"] and not res.get("moved")
        assert d.is_dir() and not _is_alias(d) and _is_alias(new)
        assert _hidden(d) and not _hidden(new)
        assert _shown(tmp_path) == [new.name]  # 탐색기에는 새 이름 하나만 보인다
        assert (new / "inputs" / "model.stp").read_text() == "M"
        (new / "scripts" / "via.txt").write_text("v")
        assert (d / "scripts" / "via.txt").read_text() == "v"
        assert ws.project_dir(25, "브래킷 E2E 설계", "u25") == d  # 살아 있는 세션의 경로는 그대로다
        assert _index(tmp_path)["projects"]["25"]["pending"]
        tree = ws.tree(25, "", "브래킷 E2E 설계", project_uid="u25")
        assert tree["root"] == str(new) and "뒤 바뀝니다" in tree["notice"]
        assert ws.folder_info(25, "브래킷 E2E 설계", "u25")["path"] == str(new)
        assert h.alive()
    # 잠금이 풀린 뒤 안전한 때: 실제 이름이 바뀐다
    res = ws.sync_name(25, "u25", None, may_move=True)
    assert res["moved"] and new.is_dir() and not _is_alias(new) and not _hidden(new)
    assert _is_alias(d) and _hidden(d) and (d / "scripts" / "via.txt").read_text() == "v"
    assert not _index(tmp_path)["projects"]["25"]["pending"]
    tree = ws.tree(25, "", "브래킷 E2E 설계", project_uid="u25")
    assert tree["root"] == str(new) and not tree["notice"]


@windows_only
def test_locked_rename_twice_and_back(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    with Holder(d):
        ws.sync_name(25, "u25", "둘째", may_move=False)
        ws.sync_name(25, "u25", "셋째", may_move=False)
        two, three = tmp_path / "P0025_둘째", tmp_path / "P0025_셋째"
        assert _is_alias(two) and _hidden(two) and _is_alias(three) and not _hidden(three)
        assert (two / "project.json").is_file()  # 탐색기 창이 열려 있어도 끊기지 않는다
        assert _shown(tmp_path) == [three.name]
        ws.sync_name(25, "u25", "둘째", may_move=False)  # 쓰던 이름으로 되돌림
        assert not _hidden(two) and _hidden(three) and _shown(tmp_path) == [two.name]
        ws.sync_name(25, "u25", "처음", may_move=False)  # 실제 이름으로 되돌림
        assert d.is_dir() and not _hidden(d) and _shown(tmp_path) == [d.name]
        assert not _index(tmp_path)["projects"]["25"]["pending"]
    assert ws.sync_name(25, "u25", None, may_move=True) and d.is_dir() and not _is_alias(d)


@windows_only
def test_may_move_false_never_tries_real_rename(tmp_path):
    """세션이 살아 있으면 rename이 성공해도 안 된다. Codex·Gemini는 cwd를 메모리에 둔다."""
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    res = ws.sync_name(25, "u25", "다음", may_move=False)
    assert res["pending"] and d.is_dir() and not _is_alias(d)
    assert _is_alias(tmp_path / "P0025_다음")
    # 판단은 바꾸기 바로 전에 다시 한다(그 사이 케이스가 들어올 수 있다)
    asked = []
    ws.sync_name(25, "u25", None, may_move=lambda: asked.append(1) or False)
    assert asked and d.is_dir() and not _is_alias(d)


def test_alias_refused_keeps_old_name_and_says_when(tmp_path, monkeypatch):
    """네트워크 드라이브·FAT·회사 정책: 접합을 못 만들면 옛 이름으로 일하고 나중에 바꾼다."""
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")

    def refuse(*a):
        raise OSError(1, "지원하지 않는 드라이브")

    monkeypatch.setattr(W, "_make_alias", refuse)
    res = ws.sync_name(25, "u25", "다음", may_move=False)
    assert res["pending"] and d.is_dir() and not _hidden(d)
    tree = ws.tree(25, "", "다음", project_uid="u25")
    assert tree["root"] == str(d) and "폴더 이름은" in tree["notice"] and "P0025_다음" in tree["notice"] and "뒤" in tree["notice"]
    assert _index(tmp_path)["projects"]["25"]["pending"]
    monkeypatch.undo()
    assert ws.sync_name(25, "u25", None, may_move=True)["moved"]
    assert (tmp_path / "P0025_다음").is_dir()
    assert not ws.tree(25, "", "다음", project_uid="u25")["notice"]


@windows_only
def test_failed_rename_leaves_no_gap(tmp_path):
    """시도가 실패해도 보이는 이름은 한 순간도 사라지지 않는다: 접합을 지우기 전에 잠금부터 확인한다."""
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    with Holder(d):
        ws.sync_name(25, "u25", "다음", may_move=False)
        new = tmp_path / "P0025_다음"
        ino = os.lstat(new).st_ino
        ws.sync_name(25, "u25", None, may_move=True)
        assert _is_alias(new) and os.lstat(new).st_ino == ino and d.is_dir()
        assert not list(tmp_path.glob("*dds-moving*"))


def test_crash_between_renames_recovers(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    (d / "inputs" / "a.txt").write_text("A")
    ws.sync_name(25, "u25", "다음", may_move=False)
    d.rename(tmp_path / f"P0025_{W.MOVING}")  # 첫 단계 뒤에 Agent가 죽었다
    res = ws.sync_name(25, "u25", None, may_move=True)
    real = tmp_path / "P0025_다음"
    assert res["moved"] and real.is_dir() and not _is_alias(real) and (real / "inputs" / "a.txt").read_text() == "A"
    assert not (tmp_path / f"P0025_{W.MOVING}").exists()


def test_crash_found_again_without_index_mark(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    d.rename(tmp_path / f"P0025_{W.MOVING}")
    assert (25, "u25") in ws.pending_projects()
    ws.sync_name(25, "u25", None, may_move=True)
    assert (tmp_path / "P0025_처음").is_dir() and not list(tmp_path.glob(f"*{W.MOVING}*"))


@windows_only
def test_deleted_real_folder_does_not_block_its_name(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    ws.sync_name(25, "u25", "다음", may_move=False)
    shutil.rmtree(d)
    assert ws.project_dir(25, "다음", "u25").name == "P0025_다음"
    assert not _is_alias(tmp_path / "P0025_다음")


@windows_only
def test_old_aliases_are_capped(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    ws.project_dir(25, "n0", "u25")
    for i in range(1, W.ALIAS_KEEP + 5):
        ws.sync_name(25, "u25", f"n{i}", may_move=True)
    names = [p.name for p in tmp_path.glob("P0025_*")]
    assert len(names) == W.ALIAS_KEEP + 1
    assert "P0025_n0" not in names  # 가장 오래된 것부터
    assert all((tmp_path / n / "project.json").is_file() for n in names)


def test_project_dir_skips_alias(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    ws.sync_name(25, "u25", "다음", may_move=False)
    assert ws.project_dir(25) == d
    assert ws.project_dir(25, "다음", "u25") == d
    idx = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    idx["projects"]["25"]["folder"] = "P0025_다음"  # 색인이 접합을 가리켜도 실제 폴더를 쓴다
    (tmp_path / "index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    assert ws.project_dir(25).resolve() == d.resolve() and not _is_alias(ws.project_dir(25))


def test_custom_folder_inside_workspace_not_renamed(tmp_path):
    ws = Workspace(tmp_path / "ws", "http://srv")
    inside = tmp_path / "ws" / "내폴더"
    ws.set_folder(25, "처음", "u25", str(inside))
    assert ws.sync_name(25, "u25", "다음", may_move=True).get("custom")
    assert inside.is_dir() and not list((tmp_path / "ws").glob("P0025*"))


# ---------- 경로를 쓰는 곳 ----------
# ---------- 번호 폴더를 합칠 때 사용자 글을 지우지 않는다 ----------
def test_stale_stamp_does_not_delete_user_content(tmp_path):
    """동기화 기록은 그 뒤에 넣은 글을 모른다. 기록에 있다는 이유로 지우면 메모·명령·개인 스킬을 영영 잃는다."""
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "이름", "u25")
    ws.sync_name(25, "u25", "이름", may_move=True)
    s = tmp_path / "P0025"
    s.mkdir()
    (s / "project.json").write_text(json.dumps({"project_id": 25, "uid": None, "name": "", "server": "http://srv"}), encoding="utf-8")
    context.sync_project_context(s)
    assert "CLAUDE.md" in context.dds_items(s) and ".claude" in context.dds_items(s)
    rule = s / "CLAUDE.md"
    with open(rule, "r+", encoding="utf-8") as f:  # Hidden 파일은 'w'가 거부된다
        body = f.read()
        f.seek(0)
        f.write("# 내 메모: 해석 조건 절대 바꾸지 말 것\n" + body)
    (s / ".claude" / "commands").mkdir()
    (s / ".claude" / "commands" / "내명령.md").write_text("MY COMMAND", encoding="utf-8")
    (s / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")
    (s / ".claude" / "skills" / "my-own-skill").mkdir()
    (s / ".claude" / "skills" / "my-own-skill" / "SKILL.md").write_text("MY SKILL", encoding="utf-8")
    (s / "inputs").mkdir()
    (s / "inputs" / "a.txt").write_text("a")

    ws.sync_name(25, "u25", "이름", may_move=True)

    assert (d / ".claude" / "commands" / "내명령.md").read_text(encoding="utf-8") == "MY COMMAND"
    assert (d / ".claude" / "settings.local.json").is_file()
    assert (d / ".claude" / "skills" / "my-own-skill" / "SKILL.md").read_text(encoding="utf-8") == "MY SKILL"
    assert (d / "inputs" / "a.txt").read_text() == "a"
    # DDS 스킬·기록은 옮기지 않는다. 다음 동기화가 만든다
    assert not (d / ".claude" / "skills" / "abaqus-automation").exists() and not (d / context.STAMP).exists()
    assert "내 메모" in (d / "CLAUDE.md").read_text(encoding="utf-8")  # 이름 있는 폴더에 없어 옮겨졌다
    # 그 뒤 동기화해도 사용자 글이 남고 숨겨지지 않는다
    context.sync_project_context(d)
    assert "내 메모" in (d / "CLAUDE.md").read_text(encoding="utf-8")
    assert (d / ".claude" / "commands" / "내명령.md").is_file() and "CLAUDE.md" not in context.dds_items(d)


def test_user_rule_file_in_conflict_stays_and_is_reported(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "이름", "u25")
    context.sync_project_context(d)
    s = tmp_path / "P0025"
    s.mkdir()
    (s / "project.json").write_text(json.dumps({"project_id": 25, "uid": None, "name": "", "server": "http://srv"}), encoding="utf-8")
    context.sync_project_context(s)
    with open(s / "CLAUDE.md", "r+", encoding="utf-8") as f:
        body = f.read()
        f.seek(0)
        f.write("# 내 메모\n" + body)
    ws.sync_name(25, "u25", "이름", may_move=True)
    assert "내 메모" in (s / "CLAUDE.md").read_text(encoding="utf-8")
    assert not _hidden(s / "CLAUDE.md")  # 사람이 비교해야 하는 파일이다
    assert "내 메모" not in (d / "CLAUDE.md").read_text(encoding="utf-8")
    notice = ws.tree(25, "", "이름", project_uid="u25")["notice"]
    assert "P0025" in notice and "덮어쓰지" in notice
    assert not (s / ".claude" / "skills" / "abaqus-automation").exists() or (d / ".claude" / "skills" / "abaqus-automation").is_dir()


def test_dds_only_bare_folder_goes_away_quietly(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "이름", "u25")
    context.sync_project_context(d)
    s = tmp_path / "P0025"
    s.mkdir()
    (s / "project.json").write_text(json.dumps({"project_id": 25, "uid": None, "name": "", "server": "http://srv"}), encoding="utf-8")
    context.sync_project_context(s)
    ws.sync_name(25, "u25", "이름", may_move=True)
    assert not s.is_dir() or _is_alias(s)
    assert not ws.tree(25, "", "이름", project_uid="u25")["notice"]


# ---------- 읽기 실패를 빈 기록으로 보지 않는다 ----------
class _Excl:
    """공유 없이 파일을 잡는다. 보안·백업 프로그램이 하는 일이다."""

    def __init__(self, path: Path):
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.CreateFileW.restype = ctypes.c_void_p
        self.k.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        self.k.CloseHandle.argtypes = [ctypes.c_void_p]
        self.h = self.k.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)
        assert self.h not in (None, ctypes.c_void_p(-1).value)

    def close(self):
        if self.h:
            self.k.CloseHandle(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _hold_for(path: Path, seconds: float):
    x = _Excl(path)
    threading.Timer(seconds, x.close).start()


@windows_only
def test_index_survives_short_read_lock(tmp_path):
    ws = Workspace(tmp_path / "ws", "http://srv")
    ws.set_folder(7, "직접", "uid7", str(tmp_path / "내폴더"))
    ws.project_dir(8, "다른", "uid8")
    ws.project_dir(25, "옛", "u25")
    _hold_for(tmp_path / "ws" / "index.json", 0.2)
    ws.sync_name(25, "u25", "새", may_move=False)
    time.sleep(0.3)
    idx = _index(tmp_path / "ws")
    assert idx["links"] == {"uid7": str(tmp_path / "내폴더")} and {"8", "25"} <= set(idx["projects"])
    assert ws.project_dir(7, "직접", "uid7") == tmp_path / "내폴더"
    assert not list((tmp_path / "ws").glob("P0007*"))


@windows_only
def test_index_long_lock_fails_loudly_and_keeps_file(tmp_path):
    ws = Workspace(tmp_path / "ws", "http://srv")
    ws.set_folder(7, "직접", "uid7", str(tmp_path / "내폴더"))
    ws.project_dir(25, "옛", "u25")
    before = (tmp_path / "ws" / "index.json").read_bytes()
    with _Excl(tmp_path / "ws" / "index.json"):
        with pytest.raises(PermissionError):
            ws.project_dir(7, "직접", "uid7")  # 기본 폴더를 새로 만들지 않는다
    assert (tmp_path / "ws" / "index.json").read_bytes() == before
    assert not list((tmp_path / "ws").glob("P0007*"))


@windows_only
def test_short_lock_does_not_take_over_another_project(tmp_path):
    """서버 DB를 새로 만들어 같은 번호에 다른 uid가 왔다. 읽는 순간 잠겼다고 빈 기록으로 보면 남의 폴더를 차지한다."""
    ws = Workspace(tmp_path, "http://srv")
    a = ws.project_dir(3, "프로젝트A", "uidA")
    (a / "A의자료.txt").write_text("A")
    _hold_for(a / "project.json", 0.2)
    assert ws.sync_name(3, "uidB", "프로젝트B", may_move=True) == {"found": False}
    time.sleep(0.3)
    assert _info(a)["uid"] == "uidA" and a.name == "P0003_프로젝트A"
    assert ws.project_dir(3, "프로젝트A", "uidA") == a


@windows_only
def test_long_lock_on_record_does_not_take_over_either(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    a = ws.project_dir(3, "프로젝트A", "uidA")
    with _Excl(a / "project.json"):
        with pytest.raises(PermissionError):
            ws.sync_name(3, "uidB", "프로젝트B", may_move=True)
    assert _info(a)["uid"] == "uidA"


# ---------- 탐색기로 복사한 백업 ----------
def test_explorer_copy_never_becomes_the_project(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    ws.sync_name(25, "u25", "브래킷", may_move=True)
    (d / "inputs" / "원본.txt").write_text("옛 내용")
    copy = tmp_path / "P0025_브래킷 - 복사본"
    shutil.copytree(d, copy)
    (d / "inputs" / "원본.txt").write_text("최신 내용")
    ws.sync_name(25, "u25", "후속설계", may_move=True)  # 'P0025_후속설계'는 복사본보다 뒤에 정렬된다
    real = tmp_path / "P0025_후속설계"
    got = ws.project_dir(25, "후속설계", "u25")
    assert got == real and (got / "inputs" / "원본.txt").read_text() == "최신 내용"
    ws.sync_name(25, "u25", "후속설계", may_move=True)
    ws.sync_name(25, "u25", "또 다른 이름", may_move=True)
    assert copy.is_dir() and not _is_alias(copy) and (copy / "inputs" / "원본.txt").read_text() == "옛 내용"  # 백업은 그대로다
    assert ws.project_dir(25, "또 다른 이름", "u25").name == "P0025_또-다른-이름"


def test_explorer_copy_without_index_is_still_told_apart(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "하이브리드", "u25")
    ws.sync_name(25, "u25", "하이브리드", may_move=True)
    copy = tmp_path / "P0025_가나다 복사본"  # 정렬이 앞선다
    shutil.copytree(d, copy)
    (tmp_path / "index.json").unlink()
    assert ws.project_dir(25, "하이브리드", "u25") == d
    assert ws.project_dir(25) == d


# ---------- 숨김 기록 ----------
@windows_only
def test_hidden_real_folder_is_recorded_before_hiding(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "옛", "u25")
    with _Excl(d / "project.json"):  # 기록을 못 남기면 숨기지 않는다
        with pytest.raises(PermissionError):
            ws.sync_name(25, "u25", "새", may_move=False)
    assert not _hidden(d)
    ws.sync_name(25, "u25", "새", may_move=True)
    new = tmp_path / "P0025_새"
    assert new.is_dir() and not _is_alias(new) and not _hidden(new) and _shown(tmp_path) == [new.name]


@windows_only
def test_read_only_record_does_not_lose_the_project_from_explorer(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "옛", "u25")
    os.chmod(d / "project.json", 0o444)  # 폴더 속성의 '읽기 전용'을 파일까지 적용했다
    ws.sync_name(25, "u25", "새", may_move=False)
    assert _info(d)["hid_real"] and _hidden(d)
    ws.sync_name(25, "u25", None, may_move=True)
    new = tmp_path / "P0025_새"
    assert new.is_dir() and not _hidden(new) and _shown(tmp_path) == [new.name]


@windows_only
def test_short_lock_at_read_keeps_records_and_visibility(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "옛", "u25")
    ws.sync_name(25, "u25", "새", may_move=False)
    created = _info(d)["created"]
    _hold_for(d / "project.json", 0.2)
    ws.sync_name(25, "u25", None, may_move=True)
    time.sleep(0.3)
    new = tmp_path / "P0025_새"
    assert new.is_dir() and not _hidden(new) and _shown(tmp_path) == [new.name]
    assert _info(new)["created"] == created and (d / "project.json").is_file()


# ---------- 확인 이름 ----------
def _bare_with_file(root: Path, uid=None) -> Path:
    s = root / "P0025"
    (s / "inputs").mkdir(parents=True)
    (s / "inputs" / "번호폴더결과.csv").write_text("x")
    (s / "project.json").write_text(json.dumps({"project_id": 25, "uid": uid, "name": "", "server": "http://srv"}), encoding="utf-8")
    return s


def test_crash_between_probe_renames_recovers(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    ws.sync_name(25, "u25", "브래킷", may_move=True)
    s = _bare_with_file(tmp_path)
    os.rename(s, tmp_path / f"P0025_{W.PROBE}")  # 두 이름 바꾸기 사이에 Agent가 죽었다
    assert (25, "u25") in ws.pending_projects()
    ws.sync_name(25, "u25", None, may_move=True)
    assert (d / "inputs" / "번호폴더결과.csv").read_text() == "x"
    assert not (tmp_path / f"P0025_{W.PROBE}").exists() and "옮겼습니다" in ws.tree(25, "", "브래킷", project_uid="u25")["notice"]


def test_probe_folder_is_never_the_project(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    ws.sync_name(25, "u25", "브래킷", may_move=True)
    (d / "inputs" / "최신.step").write_text("L")
    s = _bare_with_file(tmp_path, "u25")
    os.rename(s, tmp_path / f"P0025_{W.PROBE}")
    assert ws.project_dir(25, "브래킷", "u25") == d  # 이름 순서가 앞서도 쓰지 않는다
    ws.sync_name(25, "u25", None, may_move=True)
    assert not list(tmp_path.glob("P0025_브래킷_*"))
    assert (d / "inputs" / "번호폴더결과.csv").is_file() and (d / "inputs" / "최신.step").is_file()


@windows_only
def test_lock_taken_between_probe_renames_retries(tmp_path, monkeypatch):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    ws.sync_name(25, "u25", "브래킷", may_move=True)
    _bare_with_file(tmp_path)
    probe = tmp_path / f"P0025_{W.PROBE}"
    real_rename = W._rename_retry
    holders = []

    def rename(src, dst):
        ok = real_rename(src, dst)
        if ok and Path(dst) == probe and not holders:  # 확인 이름으로 옮긴 직후 누가 안의 파일을 잡았다
            holders.append(Holder(tmp_path, probe / "inputs" / "번호폴더결과.csv"))
        return ok

    monkeypatch.setattr(W, "_rename_retry", rename)
    ws.sync_name(25, "u25", None, may_move=True)
    assert probe.is_dir() and _index(tmp_path)["projects"]["25"]["pending"]
    ws.sync_name(25, "u25", None, may_move=False)  # 세션이 있어 못 옮기는 동안에도 잊지 않는다
    assert _index(tmp_path)["projects"]["25"]["pending"]
    holders[0].stop()
    ws.sync_name(25, "u25", None, may_move=True)
    assert (d / "inputs" / "번호폴더결과.csv").read_text() == "x" and not probe.exists()
    assert not _index(tmp_path)["projects"]["25"]["pending"]


def test_project_named_like_the_temporary_names(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    ws.project_dir(25, "시작", "u25")
    for n in (W.MOVING, W.PROBE):
        assert ws.sync_name(25, "u25", n, may_move=True)["moved"]
        got = ws.project_dir(25, n, "u25")
        assert got.is_dir() and got.name not in (f"P0025_{W.MOVING}", f"P0025_{W.PROBE}")


# ---------- 사용자가 이어 둔 링크 ----------
@windows_only
def test_user_link_to_other_drive_is_the_project(tmp_path):
    root, far = tmp_path / "ws", tmp_path / "D드라이브" / "브래킷"
    ws = Workspace(root, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    (d / "inputs" / "모델.step").write_text("M")
    shutil.move(d, far)
    W._make_alias(far, d)
    assert ws.project_dir(25, "브래킷", "u25") == d  # 빈 폴더를 새로 만들지 않는다
    assert (ws.project_dir(25, "브래킷", "u25") / "inputs" / "모델.step").read_text() == "M"
    assert ws.sync_name(25, "u25", "새 이름", may_move=True).get("custom")  # 이름은 사용자 것이다
    assert _is_alias(d) and (far / "inputs" / "모델.step").is_file()
    assert _info(far)["name"] == "새 이름"
    assert ws.find(25) == d
    assert not list(root.glob("P0025_새*"))


@windows_only
def test_user_link_offline_says_so_and_keeps_link(tmp_path):
    root, far = tmp_path / "ws", tmp_path / "D드라이브" / "브래킷"
    ws = Workspace(root, "http://srv")
    d = ws.project_dir(25, "브래킷", "u25")
    shutil.move(d, far)
    W._make_alias(far, d)
    far.rename(far.with_name("빠짐"))  # 외장 드라이브를 뺐다
    with pytest.raises(FileNotFoundError, match="외장 드라이브"):
        ws.project_dir(25, "브래킷", "u25")
    assert _is_alias(d) and not list(root.glob("P0025_브래킷_*"))
    far.with_name("빠짐").rename(far)  # 돌아왔다
    assert ws.project_dir(25, "브래킷", "u25") == d and (d / "project.json").is_file()


# ---------- 이름을 바꾸다 끊기거나 끼어들었을 때 ----------
@windows_only
def test_crash_after_real_rename_still_leaves_old_name(tmp_path, monkeypatch):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "옛", "u25")
    ws.sync_name(25, "u25", "옛", may_move=True)
    (d / "scripts" / "run.py").write_text("x")

    def die(*a, **k):
        raise SystemExit("죽음")

    with monkeypatch.context() as m:
        m.setattr(Workspace, "_finish_move", die)
        with pytest.raises(SystemExit):
            ws.sync_name(25, "u25", "새", may_move=True)
    new = tmp_path / "P0025_새"
    assert new.is_dir() and not d.exists()
    ws.sync_name(25, "u25", None, may_move=True)  # 다시 시작
    assert _is_alias(d) and _hidden(d) and (d / "scripts" / "run.py").read_text() == "x"
    assert _info(new)["aliases"] == ["P0025_옛"] and _info(new)["folder"] == new.name and "moving" not in _info(new)
    assert _info(new)["folder_for"] == "새"


def test_failed_rename_clears_its_plan(tmp_path, monkeypatch):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "옛", "u25")
    real = os.rename

    def refuse_second(src, dst):
        if Path(dst).name == "P0025_새":
            raise OSError(1, "거부")
        return real(src, dst)

    monkeypatch.setattr(W.os, "rename", refuse_second)
    ws.sync_name(25, "u25", "새", may_move=True)
    monkeypatch.setattr(W.os, "rename", real)
    assert d.is_dir() and not _is_alias(d) and "moving" not in _info(d)
    assert ws.sync_name(25, "u25", None, may_move=True)["moved"]
    assert (tmp_path / "P0025_새").is_dir() and "moving" not in _info(tmp_path / "P0025_새")


@windows_only
def test_writer_recreating_old_name_during_rename_is_merged(tmp_path, monkeypatch):
    """옛 이름이 잠깐 비는 사이에 Agent 밖의 스크립트가 makedirs로 진짜 폴더를 만들 수 있다. 조용히 갈라지지 않게 합친다."""
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "옛", "u25")
    ws.sync_name(25, "u25", "옛", may_move=True)
    real_finish = Workspace._finish_move

    def race(self, *a, **k):
        (d / "runs" / "out").mkdir(parents=True)
        (d / "runs" / "out" / "결과.csv").write_text("r")
        return real_finish(self, *a, **k)

    with monkeypatch.context() as m:
        m.setattr(Workspace, "_finish_move", race)
        ws.sync_name(25, "u25", "새", may_move=True)
    new = tmp_path / "P0025_새"
    assert (new / "runs" / "out" / "결과.csv").read_text() == "r"
    assert _is_alias(d) and _hidden(d)  # 옛 경로는 다시 같은 폴더로 이어진다
    (d / "runs" / "out" / "다음.csv").write_text("n")
    assert (new / "runs" / "out" / "다음.csv").read_text() == "n"
    assert not _index(tmp_path)["projects"]["25"]["pending"]


@windows_only
def test_busy_writer_state_merges_once_it_stops(tmp_path):
    """옛 경로에 쉬지 않고 쓰는 스크립트가 있으면 확인 이름으로 옮긴 사이에도 옛 이름을 다시 만든다. 멈춘 뒤에는 모두 합쳐진다."""
    ws = Workspace(tmp_path, "http://srv")
    ws.project_dir(25, "옛", "u25")
    ws.sync_name(25, "u25", "옛", may_move=True)
    ws.sync_name(25, "u25", "새", may_move=True)
    new, old = tmp_path / "P0025_새", tmp_path / "P0025_옛"
    old.rmdir()  # 접합을 지우고 스크립트가 다시 만든 상태
    (old / "out").mkdir(parents=True)
    (old / "out" / "b.csv").write_text("b")
    probe = tmp_path / f"P0025_{W.PROBE}"
    (probe / "out").mkdir(parents=True)
    (probe / "out" / "a.csv").write_text("a")
    info = _info(new)
    info["stray_names"] = ["P0025_옛"]
    info["stray_wait"] = True
    (new / "project.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    ws.sync_name(25, "u25", None, may_move=True)
    assert (new / "out" / "a.csv").read_text() == "a" and (new / "out" / "b.csv").read_text() == "b"
    assert not probe.exists() and _is_alias(old) and _hidden(old)
    assert not _index(tmp_path)["projects"]["25"]["pending"] and "stray_names" not in _info(new)


_LOOP_CODE = """
import sys, time
sys.path.insert(0, sys.argv[1])
from agent.dds_agent.workspace import Workspace
ws = Workspace(sys.argv[2], "http://srv")
end = time.time() + float(sys.argv[3])
print("ready", flush=True)
while time.time() < end:
    ws.project_dir(25, sys.argv[4] if len(sys.argv) > 4 else None, sys.argv[5] if len(sys.argv) > 5 else None)
"""


def _looper(root: Path, seconds: float, *args: str) -> subprocess.Popen:
    repo = str(Path(__file__).resolve().parents[2])
    p = subprocess.Popen([sys.executable, "-c", _LOOP_CODE, repo, str(root), str(seconds), *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, creationflags=NO_WINDOW, env={**os.environ, "PYTHONPATH": repo})
    assert p.stdout.readline().strip() == b"ready"
    return p


def test_other_process_cannot_recreate_temporary_name(tmp_path):
    """MCP의 project_dir은 다른 프로세스다. 이름을 바꾸는 중에 끼어들어 임시 이름으로 진짜 폴더를 만들면 이름 바꾸기가 영영 막힌다."""
    ws = Workspace(tmp_path, "http://srv")
    ws.project_dir(25, "시작", "u25")
    ws.sync_name(25, "u25", "시작", may_move=True)
    other = _looper(tmp_path, 4, "시작", "u25")
    n = 0
    end = time.time() + 3
    while time.time() < end:
        n += 1
        ws.sync_name(25, "u25", f"새{n}", may_move=True)
    other.wait(timeout=30)
    assert other.returncode == 0, other.stderr.read().decode("utf-8", "replace")[-800:]
    assert n > 1 and not (tmp_path / f"P0025_{W.MOVING}").exists()
    real = [p.name for p in tmp_path.glob("P0025*") if p.is_dir() and not _is_alias(p)]
    assert real == [f"P0025_새{n}"] and not _index(tmp_path)["projects"]["25"]["pending"]


def test_two_processes_do_not_lose_links(tmp_path):
    ws = Workspace(tmp_path / "ws", "http://srv")
    ws.set_folder(7, "직접", "uid7", str(tmp_path / "내폴더"))
    ws.project_dir(25, "시작", "u25")
    other = _looper(tmp_path / "ws", 3, "시작", "u25")
    end = time.time() + 2.5
    n = 0
    while time.time() < end:
        n += 1
        ws.project_dir(100 + n % 5, f"이름{n}", f"uid{100 + n % 5}")
        ws.sync_name(100 + n % 5, f"uid{100 + n % 5}", f"이름{n}", may_move=False)
    other.wait(timeout=30)
    assert other.returncode == 0, other.stderr.read().decode("utf-8", "replace")[-800:]
    idx = _index(tmp_path / "ws")
    assert idx["links"] == {"uid7": str(tmp_path / "내폴더")} and "25" in idx["projects"]


def test_lock_is_released_when_a_process_dies(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    code = ("import os, sys, time, msvcrt\nfd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\nmsvcrt.locking(fd, msvcrt.LK_NBLCK, 1)\nprint('ready', flush=True)\ntime.sleep(60)\n"
            if os.name == "nt" else
            "import os, sys, time, fcntl\nfd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT)\nfcntl.flock(fd, fcntl.LOCK_EX)\nprint('ready', flush=True)\ntime.sleep(60)\n")
    p = subprocess.Popen([sys.executable, "-c", code, str(root / W.LOCK_FILE)], stdout=subprocess.PIPE, creationflags=NO_WINDOW)
    assert p.stdout.readline().strip() == b"ready"
    threading.Timer(0.3, p.kill).start()
    started = time.perf_counter()
    assert Workspace(root, "http://srv").project_dir(1, "가", "u1").is_dir()
    assert 0.2 < time.perf_counter() - started < W.LOCK_WAIT_SEC
    p.wait(timeout=10)


# ---------- 접합을 거부하는 드라이브 ----------
@windows_only
def test_refused_junction_leaves_no_folders_and_keeps_the_name(tmp_path, monkeypatch):
    """_winapi.CreateJunction은 폴더를 만든 뒤 거부되면 그 폴더를 남긴다. 실제 동작을 그대로 흉내 낸다."""
    import _winapi

    def refuse(target, alias):
        os.mkdir(alias)
        raise OSError(13, "거부")

    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    (d / "inputs" / "a.txt").write_text("A")
    with monkeypatch.context() as m:
        m.setattr(_winapi, "CreateJunction", refuse)
        for _ in range(4):  # 세션이 사는 동안의 재시도
            res = ws.sync_name(25, "u25", "다음", may_move=False)
            assert res["pending"]
            assert sorted(p.name for p in tmp_path.glob("P0025*")) == ["P0025_처음"]  # 빈 폴더가 쌓이지 않는다
            assert not _hidden(d)
        assert "P0025_다음'" in ws.tree(25, "", "다음", project_uid="u25")["notice"]
        # 세션이 끝난 뒤의 실제 이름 바꾸기. 옛 이름 접합도 거부된다
        res = ws.sync_name(25, "u25", None, may_move=True)
        assert res["moved"]
        assert sorted(p.name for p in tmp_path.glob("P0025*")) == ["P0025_다음"]
        assert (tmp_path / "P0025_다음" / "inputs" / "a.txt").read_text() == "A"
        # 되돌려도 옛 이름이 막히지 않는다
        assert ws.sync_name(25, "u25", "처음", may_move=True)["moved"]
        assert sorted(p.name for p in tmp_path.glob("P0025*")) == ["P0025_처음"]


@windows_only
def test_leftover_empty_folder_of_our_name_is_reused(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    info = _info(d)
    info["want"] = "P0025_다음"
    (d / "project.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "P0025_다음").mkdir()  # 지난 버전이 남긴 빈 폴더
    ws.sync_name(25, "u25", "다음", may_move=True)
    assert [p.name for p in tmp_path.glob("P0025*") if not _is_alias(p)] == ["P0025_다음"]


def _settings(root: Path) -> AgentSettings:
    return AgentSettings(server_url="http://srv", workspace_dir=root)


def test_case_folder_found_after_rename(tmp_path):
    s = _settings(tmp_path)
    ws = s.workspace
    sdir = ws.study_dir(25, 3, "DOE", "처음", "u25")
    case = ws.case_dir(sdir, 7, "doe")
    (case / "results.json").write_text("{}")
    server_run_dir = str(case)  # 서버 DB에 남은 절대 경로
    ws.sync_name(25, "u25", "다음", may_move=True)
    ex = CaseExecutor(s, lambda m: None)
    job = {"run_dir": server_run_dir, "project_id": 25, "study_id": 3, "case_id": 7, "study_name": "DOE", "project_name": "다음", "project_uid": "u25"}
    found = ex._case_dir(job)
    assert (found / "results.json").is_file()
    assert "P0025_다음" in str(found.resolve()) and not any(_is_alias(p) for p in (found, *found.parents) if p.name.startswith("P0025"))
    # 옛 이름 접합까지 없어져도 번호로 찾는다
    alias = tmp_path / "P0025_처음"
    if _is_alias(alias):
        os.rmdir(alias) if os.path.isjunction(alias) else alias.unlink()
    assert (ex._case_dir(job) / "results.json").is_file()
    # 다시 실행(스터디 번호·케이스 번호가 같으면 같은 폴더)
    again = ws.case_dir(ws.study_dir(25, 3, "DOE", "다음", "u25"), 7, "doe")
    assert (again / "results.json").is_file()
    ws.record_case(ws.study_dir(25, 3, "DOE", "다음", "u25"), again, {"case_id": 7, "status": "complete"})
    assert "cases" in (tmp_path / "P0025_다음" / "studies" / "S003_DOE" / "cases.csv").read_text(encoding="utf-8-sig")


@windows_only
def test_local_open_uses_the_name_people_see(tmp_path, monkeypatch):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    (d / "inputs" / "a.stp").write_text("A")
    ws.sync_name(25, "u25", "다음", may_move=False)
    opened = []
    fake = SimpleNamespace(s=SimpleNamespace(workspace=ws, viewers={}), _settle_names=lambda *a, **k: None)
    monkeypatch.setattr(M.os, "startfile", lambda p: opened.append(p), raising=False)
    res = M.Agent._local_open(fake, 25, "다음", "inputs", False, "u25")
    assert res["opened"] == str(tmp_path / "P0025_다음" / "inputs") == opened[0]
    assert Path(opened[0]).is_dir()


def test_tree_lists_real_contents_through_alias(tmp_path):
    ws = Workspace(tmp_path, "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    (d / "inputs" / "a.stp").write_text("A")
    ws.sync_name(25, "u25", "다음", may_move=False)
    t = ws.tree(25, "inputs", "다음", project_uid="u25")
    assert [e["path"] for e in t["entries"]] == ["inputs/a.stp"]


def test_rules_use_relative_paths_so_rename_does_not_stale(tmp_path):
    proj = tmp_path / "P0025_처음"
    proj.mkdir()
    context.sync_project_context(proj)
    text = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "`.claude/skills/" in text and tmp_path.as_posix() not in text
    assert context.FORMAT != "2"


def _git(root: Path, *args) -> str:
    exe = shutil.which("git")
    if not exe:
        pytest.skip("git 없음")
    return subprocess.run([exe, *args], cwd=root, capture_output=True, text=True, encoding="utf-8", check=True, creationflags=NO_WINDOW).stdout


def test_git_exclude_block_follows_rename(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    ws = Workspace(repo / "ws", "http://srv")
    d = ws.project_dir(25, "처음", "u25")
    context.sync_project_context(d)
    exclude = repo / ".git" / "info" / "exclude"
    (exclude.parent).mkdir(exist_ok=True)
    assert "P0025_처음" in exclude.read_text(encoding="utf-8")
    exclude.write_text("# 사용자 줄\n" + exclude.read_text(encoding="utf-8"), encoding="utf-8")
    ws.sync_name(25, "u25", "다음", may_move=True)
    new = repo / "ws" / "P0025_다음"
    context.sync_project_context(new)
    text = exclude.read_text(encoding="utf-8")
    assert "P0025_다음" in text and "[/ws/P0025_처음/]" not in text and "# 사용자 줄" in text
    assert ".claude" not in _git(repo, "status", "--porcelain", "--untracked-files=all", "--", "ws/P0025_다음")


def _git_status(repo: Path) -> str:
    return _git(repo, "-c", "core.quotepath=false", "status", "--porcelain", "--untracked-files=all")


def test_git_does_not_see_project_through_aliases(tmp_path):
    """git은 접합을 보통 폴더로 본다. 경로를 좁히지 않고 저장소 전체로 본다: git add -A가 하는 일이다."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    ws = Workspace(repo / "ws", "http://srv")
    d = ws.project_dir(1, "처음", "u1")
    (d / "inputs" / "a.txt").write_text("A")
    context.sync_project_context(d)
    base = _git_status(repo)
    assert "처음/inputs/a.txt" in base and ".claude" not in base

    ws.sync_name(1, "u1", "둘째", may_move=False)  # 세션이 살아 있어 접합만 있다
    now = _git_status(repo)
    assert "둘째" not in now and now == base  # 같은 파일이 새 이름으로 또 보이지 않는다

    ws.sync_name(1, "u1", None, may_move=True)  # 실제 이름이 바뀌고 옛 이름이 숨긴 접합으로 남았다
    new = repo / "ws" / "P0001_둘째"
    context.sync_project_context(new)
    assert new.is_dir() and not _is_alias(new)
    now = _git_status(repo)
    assert "처음" not in now and "둘째/inputs/a.txt" in now and ".claude" not in now
    added = _git(repo, "-c", "core.quotepath=false", "add", "-A", "--dry-run")
    assert "처음" not in added and ".claude" not in added

    # 옛 이름으로 되돌아가면 그 이름은 다시 실제 폴더이므로 제외하지 않는다
    ws.sync_name(1, "u1", "처음", may_move=True)
    context.sync_project_context(d)
    now = _git_status(repo)
    assert "처음/inputs/a.txt" in now and "둘째" not in now


# ---------- MCP: Agent와 같은 작업 공간을 본다 ----------
def test_mcp_gets_absolute_config_workspace_and_session_folder(tmp_path, monkeypatch):
    fake = SimpleNamespace(s=SimpleNamespace(server_url="http://x", token="t", workspace_dir=tmp_path / "ws"))
    env = {e["name"]: e["value"] for e in M.Agent.mcp_servers(fake, 25, None, cwd=str(tmp_path / "ws" / "P0025_a"), uid="u25")[0]["env"]}
    assert Path(env["DDS_AGENT_CONFIG"]).is_absolute()
    assert env["DDS_WORKSPACE"] == str(tmp_path / "ws")
    assert env["DDS_PROJECT_DIR"] == str(tmp_path / "ws" / "P0025_a") and env["DDS_PROJECT_UID"] == "u25"


def test_relative_config_env_is_made_absolute_at_import():
    code = ("import os; from agent.dds_agent import config; "
            "print(config.CONFIG_PATH.is_absolute(), os.path.isabs(os.environ['DDS_AGENT_CONFIG']))")
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, "-c", code], cwd=root, env={**os.environ, "DDS_AGENT_CONFIG": "data\\x\\agent.toml", "PYTHONPATH": str(root)},
                         capture_output=True, text=True, creationflags=NO_WINDOW)
    assert out.stdout.split() == ["True", "True"], out.stderr


def test_settings_project_dir_prefers_session_folder(tmp_path, monkeypatch):
    s = _settings(tmp_path / "ws")
    d = s.workspace.project_dir(25, "처음", "u25")
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.setenv("DDS_PROJECT_ID", "25")
    monkeypatch.setenv("DDS_PROJECT_DIR", str(other))
    assert s.project_dir(25) == other  # 세션의 cwd가 곧 이 프로젝트의 폴더다
    assert s.project_dir(25, "처음", "u25") == d  # 이름·uid를 준 Agent 쪽 호출은 그대로
    monkeypatch.setenv("DDS_PROJECT_DIR", str(tmp_path / "없는 폴더"))
    monkeypatch.setenv("DDS_PROJECT_UID", "u25")
    assert s.project_dir(25) == d


def test_settings_learns_name_of_unknown_project_from_server(tmp_path, monkeypatch):
    monkeypatch.delenv("DDS_PROJECT_ID", raising=False)
    monkeypatch.delenv("DDS_PROJECT_DIR", raising=False)
    s = _settings(tmp_path / "ws")
    asked = []
    s.project_lookup = lambda pid: asked.append(pid) or {"name": "서버의 이름", "uid": "u31"}
    assert s.project_dir(31).name == "P0031_서버의-이름"
    assert s.project_dir(31).name == "P0031_서버의-이름" and asked == [31]  # 찾은 뒤에는 묻지 않는다

    def down(pid):
        raise OSError("서버 없음")

    s.project_lookup = down
    assert s.project_dir(32).name == "P0032_untitled"  # 서버가 안 되어도 번호만 있는 폴더는 만들지 않는다


# ---------- Agent ----------
@pytest.fixture
def agent(monkeypatch, tmp_path):
    cfg = tmp_path / "agent.toml"
    cfg.write_text("\n".join([f"workspace_dir = {json.dumps(str(tmp_path / 'ws'))}", "tray = false", "warm_up = false", ""]), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    monkeypatch.setenv("DDS_AGENT_CONFIG", str(cfg))
    monkeypatch.setenv("DDS_TOKEN", "t")
    monkeypatch.setenv("DDS_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr(M, "sync_project_context", lambda d: False)
    a = M.Agent()
    yield a
    a.closing = True


def _live_runner(*sids):
    return SimpleNamespace(live_sessions=lambda: set(sids), shutdown=lambda: None)


def test_project_free_rules(agent):
    assert agent._project_free(25)
    agent.runners["x"] = _live_runner("s1")
    agent.session_project["s1"] = (25, "a", "u")
    agent.session_owner["s1"] = 25
    assert not agent._project_free(25)  # 살아 있는 세션
    assert agent._project_free(26)  # 다른 프로젝트의 세션은 상관없다
    agent.runners["x"] = _live_runner("모르는 세션")
    assert not agent._project_free(26)  # 모르는 세션은 조심한다
    agent.runners.clear()
    agent.executor.running = 1
    assert not agent._project_free(25)  # 해석 중: HPC는 PC에서 잠그지 않으니 잠금으로 알 수 없다
    agent.executor.running = 0
    agent.executor.queued = 1
    assert not agent._project_free(25)
    agent.executor.queued = 0
    agent.devtests = 1
    assert not agent._project_free(25)
    agent.devtests = 0
    agent._busy_projects[25] = 1
    assert not agent._project_free(25)  # 세션을 여는 중
    assert agent._project_free(25, mine=1)


def test_rename_message_order_last_wins(agent, tmp_path):
    agent.s.workspace.project_dir(25, "시작", "u25")
    for n in ("하나", "둘", "셋"):  # 수신 루프는 순서대로 받는다. 처리는 스레드에서 해도 마지막 이름이 이긴다
        agent._note_name({"project_id": 25, "project_uid": "u25", "project_name": n})
    agent._settle_names(25)
    assert (tmp_path / "ws" / "P0025_셋").is_dir() and not _is_alias(tmp_path / "ws" / "P0025_셋")
    assert not (tmp_path / "ws" / "P0025_하나").exists()


def test_rule_copy_failure_after_rename_waits_for_next_session(agent, tmp_path, monkeypatch):
    # Agent 업데이트 직후 규칙 사본이 낡았고, 이름이 길어져 260자를 넘거나 파일이 잠겨 복사가 실패하는 경우
    monkeypatch.setattr(M, "sync_project_context", context.sync_project_context)
    dirs = [agent.s.workspace.project_dir(p, "시작", f"u{p}") for p in (25, 26)]
    for d in dirs:
        context.sync_project_context(d)
        (d / context.STAMP).unlink()
    real_copy = context.shutil.copytree

    def boom(src, dst, *a, **k):
        if "P0025" in str(dst):
            raise shutil.Error([("a", "b", "경로가 너무 깁니다")])
        return real_copy(src, dst, *a, **k)

    monkeypatch.setattr(context.shutil, "copytree", boom)
    agent._note_name({"project_id": 25, "project_uid": "u25", "project_name": "새 이름"})
    agent._note_name({"project_id": 26, "project_uid": "u26", "project_name": "다른 이름"})
    # 파일 탭이 부르는 곳. 규칙 복사 실패로 파일 목록까지 실패하면 안 된다
    assert agent._local_tree(25, "새 이름", "", "u25")["entries"] is not None
    root = tmp_path / "ws"
    assert any(x.name.startswith("P0025_새") for x in root.iterdir())
    # 앞 폴더가 실패해도 뒤 폴더는 맞춘다. 전체를 한 번에 맞추는 호출로 잰다
    agent._note_name({"project_id": 25, "project_uid": "u25", "project_name": "또 새 이름"})
    agent._settle_names()
    moved = next(x for x in root.iterdir() if x.name.startswith("P0026_다른"))
    assert (moved / context.STAMP).exists()


def test_live_session_gets_alias_then_real_after_exit(agent, tmp_path):
    root = tmp_path / "ws"
    d = agent.s.workspace.project_dir(25, "시작", "u25")
    agent.runners["x"] = _live_runner("s1")
    agent.session_owner["s1"] = 25
    agent.session_project["s1"] = (25, "시작", "u25")
    agent._note_name({"project_id": 25, "project_uid": "u25", "project_name": "새 이름"})
    agent._settle_names(25)
    assert d.is_dir() and not _is_alias(d) and _is_alias(root / "P0025_새-이름")
    agent.runners.clear()  # CLI가 끝났다
    agent.session_owner.clear()
    agent.session_project.clear()
    agent._settle_names()  # 저장된 '기다리는 이름'을 스스로 찾는다
    assert (root / "P0025_새-이름").is_dir() and not _is_alias(root / "P0025_새-이름")


def test_pending_survives_agent_restart(agent, tmp_path, monkeypatch):
    root = tmp_path / "ws"
    agent.s.workspace.project_dir(25, "시작", "u25")
    agent.s.workspace.sync_name(25, "u25", "새 이름", may_move=False)
    b = M.Agent()  # 다시 시작: 이름 정보는 디스크에만 있다
    b._settle_names()
    assert (root / "P0025_새-이름").is_dir() and not _is_alias(root / "P0025_새-이름")


def test_stale_warm_up_name_does_not_rename_back(agent, tmp_path):
    root = tmp_path / "ws"
    agent.s.workspace.project_dir(25, "시작", "u25")
    agent.s.workspace.sync_name(25, "u25", "새 이름", may_move=True)
    agent._prepare(25, "시작", "u25")  # state.json의 옛 이름
    assert (root / "P0025_새-이름").is_dir() and not _is_alias(root / "P0025_새-이름")
    assert _info(root / "P0025_새-이름")["name"] == "새 이름"


def test_rename_messages_through_handle(agent, tmp_path):
    root = tmp_path / "ws"
    agent.s.workspace.project_dir(25, "시작", "u25")
    agent.s.workspace.project_dir(26, "둘째", "u26")

    async def go():
        await agent.handle({"type": "project.renamed", "project_id": 25, "project_uid": "u25", "project_name": "알림으로"})
        await agent.handle({"type": "project.names", "projects": [
            {"project_id": 26, "project_uid": "u26", "project_name": "연결하며"},
            {"project_id": 99, "project_uid": "u99", "project_name": "이 PC에 없음"}]})
        await agent.handle({"type": "project.새로운-메시지", "project_id": 25})  # 새 서버가 보낸 모르는 메시지는 로그만 남긴다
        for _ in range(100):
            if (root / "P0025_알림으로").is_dir() and (root / "P0026_연결하며").is_dir():
                break
            await asyncio.sleep(0.05)

    asyncio.run(go())
    assert not _is_alias(root / "P0025_알림으로") and not _is_alias(root / "P0026_연결하며")
    assert not list(root.glob("P0099*"))


def test_going_back_to_default_folder_catches_up(agent, tmp_path, monkeypatch):
    root = tmp_path / "ws"
    agent.s.workspace.project_dir(25, "시작", "u25")
    agent._set_folder(25, "시작", "u25", str(tmp_path / "내폴더"))
    agent._note_name({"project_id": 25, "project_uid": "u25", "project_name": "새 이름"})
    agent._settle_names(25)
    assert (root / "P0025_시작").is_dir() and not (tmp_path / "P0025_새-이름").exists()
    info = agent._set_folder(25, "새 이름", "u25", None)
    assert info["path"] == str(root / "P0025_새-이름")
    assert not _is_alias(root / "P0025_새-이름")


def test_case_message_name_is_not_trusted(agent):
    agent._note_name({"type": "local.tree", "project_id": 1, "project_uid": "u", "project_name": "n"})
    assert 1 in agent._wanted_names
    assert "case.run" not in M.NAME_MSGS and "session.prompt" not in M.NAME_MSGS


def test_session_load_prefers_the_cwd_it_started_with(agent, tmp_path):
    d = agent.s.workspace.project_dir(25, "시작", "u25")
    agent.state.remember_cwd("s1", str(d))
    agent.s.workspace.sync_name(25, "u25", "새 이름", may_move=True)
    new = tmp_path / "ws" / "P0025_새-이름"
    if os.name == "nt":  # 옛 이름 접합으로 부르면 CLI가 지난 대화를 그대로 찾는다
        assert agent._session_cwd("s1", new) == str(d)
    assert agent._session_cwd("모르는 세션", new) == str(new)
    agent.state.remember_cwd("s2", str(tmp_path / "지워진 폴더"))
    assert agent._session_cwd("s2", new) == str(new)
    other = agent.s.workspace.project_dir(26, "딴", "u26")
    agent.state.remember_cwd("s3", str(other))
    assert agent._session_cwd("s3", new) == str(new)  # 다른 프로젝트의 폴더로 불러오지 않는다


class _LoadRunner:
    """CLI가 지난 대화를 찾는 규칙만 흉내 낸다. 규칙은 실제 CLI로 확인했다(docs/local-data.md)."""

    def __init__(self, found):
        self.found, self.tried, self.env = found, [], {}

    def live_sessions(self):
        return set()

    def load_session(self, sid, cwd, mcp, report=None):
        self.tried.append(cwd)
        return self.found(cwd)

    def shutdown(self):
        pass


def _wire_load(agent, name: str, runner: _LoadRunner):
    agent.runners[name] = runner
    agent.runner = lambda n, report=None: agent.runners[n]
    agent._wait_force = lambda n: None


def test_old_gemini_session_without_memory_finds_history_through_old_name(agent, tmp_path):
    """이 버전 전에 만든 세션은 시작 경로를 기억하지 못한다. 연결하자마자 폴더 이름이 바뀌므로 옛 이름 접합을 차례로 본다."""
    root = tmp_path / "ws"
    old = agent.s.workspace.project_dir(25, "브래킷-CAD를 만들고", "u25")
    agent.s.workspace.sync_name(25, "u25", "브래킷 E2E 설계", may_move=True)
    new = root / "P0025_브래킷-E2E-설계"
    if not _is_alias(old):
        pytest.skip("옛 이름 링크를 만들 수 없는 환경")
    r = _LoadRunner(lambda cwd: cwd == str(old))
    _wire_load(agent, "gemini", r)
    assert agent._load_session(25, "gem-1", "gemini", "브래킷 E2E 설계", "u25") == {"ok": True}
    assert r.tried == [str(new), str(old)]
    assert agent.state.session_cwd("gem-1") == str(old)  # 다음부터는 바로 찾는다
    assert agent.session_owner["gem-1"] == 25
    r2 = _LoadRunner(lambda cwd: cwd == str(old))
    _wire_load(agent, "gemini", r2)
    assert agent._load_session(25, "gem-1", "gemini", "브래킷 E2E 설계", "u25") == {"ok": True}
    assert r2.tried == [str(old)]


def test_gemini_session_with_no_history_anywhere_falls_back(agent):
    agent.s.workspace.project_dir(25, "시작", "u25")
    agent.s.workspace.sync_name(25, "u25", "새 이름", may_move=True)
    r = _LoadRunner(lambda cwd: False)
    _wire_load(agent, "gemini", r)
    assert agent._load_session(25, "gem-2", "gemini", "새 이름", "u25") == {"ok": False}
    assert agent.state.session_cwd("gem-2") is None


def test_remembered_gemini_session_tries_only_its_path(agent, tmp_path):
    d = agent.s.workspace.project_dir(25, "시작", "u25")
    agent.state.remember_cwd("gem-3", str(d))
    agent.s.workspace.sync_name(25, "u25", "새 이름", may_move=True)
    r = _LoadRunner(lambda cwd: False)
    _wire_load(agent, "gemini", r)
    assert agent._load_session(25, "gem-3", "gemini", "새 이름", "u25") == {"ok": False}
    assert len(r.tried) == 1


def test_codex_session_uses_the_current_path_once(agent):
    """Codex는 세션 id로 찾는다(실제 codex-acp로 확인). 옛 이름 접합을 cwd로 잡아 폴더를 다시 잠글 이유가 없다."""
    d = agent.s.workspace.project_dir(25, "시작", "u25")
    agent.state.remember_cwd("cdx-1", str(d))
    r = _LoadRunner(lambda cwd: False)
    _wire_load(agent, "codex", r)
    agent.s.workspace.sync_name(25, "u25", "새 이름", may_move=True)
    assert agent._load_session(25, "cdx-1", "codex", "새 이름", "u25") == {"ok": False}
    assert r.tried == [str(d.with_name("P0025_새-이름"))]


def test_unknown_cli_keeps_the_path_it_started_with(agent):
    d = agent.s.workspace.project_dir(25, "시작", "u25")
    agent.state.remember_cwd("x-1", str(d))
    r = _LoadRunner(lambda cwd: True)
    _wire_load(agent, "other", r)
    agent.s.workspace.sync_name(25, "u25", "새 이름", may_move=True)
    assert agent._load_session(25, "x-1", "other", "새 이름", "u25") == {"ok": True}
    assert r.tried == [str(d)] if os.name == "nt" else len(r.tried) == 1


def test_claude_session_history_moves_with_the_folder(agent, tmp_path, monkeypatch, request):
    """Claude는 실제 경로를 인코딩한 폴더 한 곳만 본다. 옛 이름 접합을 넘겨도 못 찾으므로 기록을 옮긴다."""
    cfg = Path(tempfile.mkdtemp(prefix="cc"))  # 인코딩한 경로가 길어 pytest 임시 폴더 안에서는 260자를 넘는다
    request.addfinalizer(lambda: shutil.rmtree(cfg, ignore_errors=True))
    monkeypatch.setitem(agent.env, "CLAUDE_CONFIG_DIR", str(cfg))
    d = agent.s.workspace.project_dir(25, "시작", "u25")
    sid = "11111111-2222-3333-4444-555555555555"
    home = cfg / "projects" / encode_claude_path(os.path.realpath(d))
    (home / sid).mkdir(parents=True)
    (home / f"{sid}.jsonl").write_text("{}\n")
    (home / sid / "tool.txt").write_text("t")
    (home / "다른세션.jsonl").write_text("{}\n")
    agent.state.remember_cwd(sid, str(d))
    agent.s.workspace.sync_name(25, "u25", "E2E 설계", may_move=True)
    new = tmp_path / "ws" / "P0025_E2E-설계"

    def found(cwd):
        return (cfg / "projects" / encode_claude_path(os.path.realpath(cwd)) / f"{sid}.jsonl").is_file()

    r = _LoadRunner(found)
    _wire_load(agent, "claude", r)
    assert agent._load_session(25, sid, "claude", "E2E 설계", "u25") == {"ok": True}
    assert r.tried == [str(new)]  # 옛 이름으로 다시 잠그지 않는다
    target = cfg / "projects" / encode_claude_path(os.path.realpath(new))
    assert (target / sid / "tool.txt").read_text() == "t"
    assert (home / "다른세션.jsonl").is_file()  # 이 세션 것만 옮긴다


def test_move_claude_session_rules(tmp_path):
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "c")}
    proj = tmp_path / "P0001_새"
    proj.mkdir()
    assert not move_claude_session("없는-세션", proj, env)
    assert not move_claude_session("../밖", proj, env) and not move_claude_session("*", proj, env)
    other = tmp_path / "c" / "projects" / "옛"
    other.mkdir(parents=True)
    (other / "s1.jsonl").write_text("old")
    assert move_claude_session("s1", proj, env)
    target = tmp_path / "c" / "projects" / encode_claude_path(str(proj.resolve())) / "s1.jsonl"
    assert target.read_text() == "old" and not (other / "s1.jsonl").exists()
    assert not move_claude_session("s1", proj, env)  # 이미 제자리다
    (other / "s1.jsonl").write_text("다른 복사본")
    assert not move_claude_session("s1", proj, env) and target.read_text() == "old"  # 덮어쓰지 않는다


def test_encode_claude_path_matches_the_cli():
    assert encode_claude_path(r"C:\Users\me\P0025_브래킷-E2E") == "C--Users-me-P0025-----E2E"
    assert encode_claude_path("a😀b") == "a--b"  # JS 문자열 두 칸


# ---------- 실제 uvicorn + 실제 Agent + 가짜 CLI ----------
@pytest.fixture
def stack(tmp_path):
    s = Stack(tmp_path)
    s.control(hold_cwd=True)
    try:
        yield s.up()
    except Exception:
        print(s.log_tail())
        raise
    finally:
        s.down()


def _rename(s: Stack, name: str):
    p = s.c.get(f"/api/projects/{s.pid}").json()
    s.c.put(f"/api/projects/{s.pid}", json={"name": name, "version": p["version"]}).raise_for_status()


def _dir(s: Stack, suffix: str) -> Path:
    return s.tmp / "ws" / f"P{s.pid:04d}_{suffix}"


def _holders_dead(s: Stack):
    def dead(pid: int) -> bool:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, creationflags=NO_WINDOW)
        return str(pid) not in r.stdout

    for e in read_log(s.state):
        if e["ev"] == "holder":
            wait_for(lambda pid=e["holder"]: dead(pid), what="자식 끝남", timeout=30)


@windows_only
def test_first_turn_rename_with_live_session_does_not_restart_cli(stack):
    """AI가 첫 응답에서 실제 set_project_info로 이름을 바꾼다. 세션은 이미 그 폴더에서 돌고 있다: 흔한 경우다."""
    s = stack
    chat = s.new_chat()
    s.turn(chat, "[rename 브래킷 E2E 설계] 시작합니다")
    assert "브래킷 E2E 설계" in s.agent_text(chat)
    real, shown = _dir(s, "세션-시험"), _dir(s, "브래킷-E2E-설계")
    wait_for(lambda: _is_alias(shown), what="새 이름 접합")
    assert real.is_dir() and not _is_alias(real) and _hidden(real) and _shown(s.tmp / "ws") == [shown.name]
    # 살아 있는 세션을 그대로 쓴다
    s.turn(chat, "[where] 이어서")
    assert count(s.state, "process_start") == 1 and count(s.state, "session_new") == 1 and count(s.state, "session_load") == 0
    assert str(real).replace("\\", "\\\\") in s.agent_text(chat)
    tree = s.c.get(f"/api/projects/{s.pid}/local/tree").json()
    assert tree["root"] == str(shown) and "뒤 바뀝니다" in tree["notice"]
    assert s.c.get(f"/api/projects/{s.pid}/local/folder").json()["path"] == str(shown)
    # 세션이 살아 있는 동안 웹에서 다시 바꿔도 끊기지 않는다
    _rename(s, "최종 이름")
    final = _dir(s, "최종-이름")
    wait_for(lambda: _is_alias(final), what="다시 바꾼 이름")
    wait_for(lambda: _hidden(shown), what="앞선 이름 숨김")
    s.turn(chat, "또 이어서")
    assert count(s.state, "process_start") == 1 and count(s.state, "session_new") == 1
    (final / "scripts" / "via.txt").write_text("v")
    assert (real / "scripts" / "via.txt").read_text() == "v"
    # Agent를 다시 시작하면(CLI가 끝나면) 미리 띄우기 전에 실제 이름이 바뀌고, 지난 경로 그대로 세션을 불러온다
    s.stop_agent()
    wait_for(lambda: not s.agent_connected(), what="Agent 끊김", timeout=30)
    _holders_dead(s)
    s.start_agent()
    wait_for(lambda: final.is_dir() and not _is_alias(final), what="실제 이름 바뀜", timeout=60)
    assert not _hidden(final) and _is_alias(real) and _hidden(real) and _shown(s.tmp / "ws") == [final.name]
    assert (final / "scripts" / "via.txt").read_text() == "v"
    wait_for(lambda: count(s.state, "session_load") == 1, what="미리 띄우기", timeout=60)
    load = next(e for e in read_log(s.state) if e["ev"] == "session_load")
    assert load["cwd"] == str(real)  # 세션을 시작한 경로 문자열 그대로다(Gemini는 이 문자열로 기록을 찾는다)
    s.turn(chat, "다시 시작 뒤에도 이어서")
    assert "다시 시작 뒤에도 이어서" in s.agent_text(chat)
    assert not s.c.get(f"/api/projects/{s.pid}/local/tree").json()["notice"]


@windows_only
def test_rename_without_session_is_immediate(stack):
    s = stack
    assert s.c.get(f"/api/projects/{s.pid}/local/folder").json()["path"] == str(_dir(s, "세션-시험"))
    _rename(s, "바로 바뀜")
    new = _dir(s, "바로-바뀜")
    wait_for(lambda: new.is_dir() and not _is_alias(new), what="실제 이름 바뀜", timeout=30)
    assert s.c.get(f"/api/projects/{s.pid}/local/tree").json()["root"] == str(new)


@windows_only
def test_offline_pc_catches_up_at_connect(stack):
    s = stack
    s.c.get(f"/api/projects/{s.pid}/local/folder").raise_for_status()
    s.stop_agent()
    wait_for(lambda: not s.agent_connected(), what="Agent 끊김", timeout=30)
    _rename(s, "꺼진 동안 바꿈")
    assert _dir(s, "세션-시험").is_dir()
    s.start_agent()
    new = _dir(s, "꺼진-동안-바꿈")
    wait_for(lambda: new.is_dir() and not _is_alias(new), what="연결하자 따라옴", timeout=30)


@windows_only
def test_two_sessions_then_exit_renames_and_new_session_uses_it(stack):
    s = stack
    a, b = s.new_chat(), s.new_chat()
    s.turn(a, "하나")
    s.turn(b, "둘")
    assert count(s.state, "session_new") == 2
    _rename(s, "둘이 함께")
    shown, real = _dir(s, "둘이-함께"), _dir(s, "세션-시험")
    wait_for(lambda: _is_alias(shown), what="접합")
    s.turn(a, "하나 계속")
    s.turn(b, "둘 계속")
    assert count(s.state, "process_start") == 1
    assert real.is_dir() and not _is_alias(real)
    s.post_prompt(a, "[die-silent]")  # CLI가 죽었다: 세션이 모두 없어져 이제 안전하다
    wait_for(lambda: shown.is_dir() and not _is_alias(shown), what="CLI가 끝난 뒤 실제 이름 바뀜", timeout=90)
    s.wait_idle(a)
    s.turn(b, "둘 다시")
    assert "둘 다시" in s.agent_text(b)


@windows_only
def test_turn_keeps_running_while_rename_is_pending(stack):
    s = stack
    chat = s.new_chat()
    s.post_prompt(chat, "[quiet 4] 긴 일")
    wait_for(lambda: count(s.state, "prompt") >= 1, what="턴 시작")
    _rename(s, "도는 중에 바꿈")
    wait_for(lambda: _is_alias(_dir(s, "도는-중에-바꿈")), what="턴 중에 접합", timeout=20)
    assert s.status(chat)["busy"]
    s.wait_idle(chat, 30)
    assert "긴 일" in s.agent_text(chat) and count(s.state, "process_start") == 1


def _rename_offline(s: Stack, name: str):
    s.stop_agent()
    wait_for(lambda: not s.agent_connected(), what="Agent 끊김", timeout=30)
    _holders_dead(s)
    _rename(s, name)
    s.start_agent()


@windows_only
def test_claude_session_survives_real_rename(tmp_path):
    """첫 턴에 이름을 바꾸고 CLI가 끝나 실제 이름이 바뀐 뒤에도 같은 세션을 불러온다. 가짜 CLI는 Claude의 규칙(실제 경로의 폴더 한 곳)을 따른다."""
    s = Stack(tmp_path, cli_name="claude")
    s.agent_env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-home")}
    s.control(hold_cwd=True, history_by="realpath")
    try:
        s.up()
        chat = s.new_chat()
        s.turn(chat, "[rename 브래킷 E2E 설계] 시작합니다")
        real, shown = _dir(s, "세션-시험"), _dir(s, "브래킷-E2E-설계")
        wait_for(lambda: _is_alias(shown), what="새 이름 접합")
        assert count(s.state, "session_new") == 1
        s.stop_agent()
        wait_for(lambda: not s.agent_connected(), what="Agent 끊김", timeout=30)
        _holders_dead(s)
        s.start_agent()
        wait_for(lambda: shown.is_dir() and not _is_alias(shown), what="실제 이름 바뀜", timeout=60)
        s.turn(chat, "이어서")
        assert "이어서" in s.agent_text(chat)
        assert count(s.state, "session_new") == 1  # 새 세션을 열지 않았다
        assert not [n for n in s.system_notes(chat) if "이어 붙일 수 없어" in n]
        load = [e for e in read_log(s.state) if e["ev"] == "session_load"][-1]
        assert load["cwd"] == str(shown)
    except Exception:
        print(s.log_tail())
        raise
    finally:
        s.down()


@windows_only
def test_gemini_session_from_before_this_version_survives_real_rename(tmp_path):
    """옛 Agent가 만든 세션: state.json에 시작 경로가 없다. 연결하자마자 폴더 이름이 바뀌어도 옛 이름 접합으로 기록을 찾는다."""
    s = Stack(tmp_path, cli_name="gemini")
    s.control(hold_cwd=True, history_by="cwd")
    try:
        s.up()
        chat = s.new_chat()
        s.turn(chat, "첫 요청")
        s.stop_agent()
        wait_for(lambda: not s.agent_connected(), what="Agent 끊김", timeout=30)
        _holders_dead(s)
        state_file = tmp_path / "agent" / "state.json"
        st = json.loads(state_file.read_text(encoding="utf-8"))
        st.pop("session_cwd", None)
        state_file.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
        old, new = _dir(s, "세션-시험"), _dir(s, "새-이름")
        _rename(s, "새 이름")
        s.start_agent()
        wait_for(lambda: new.is_dir() and not _is_alias(new), what="실제 이름 바뀜", timeout=60)
        s.turn(chat, "다음 요청")
        assert "다음 요청" in s.agent_text(chat)
        assert count(s.state, "session_new") == 1
        assert not [n for n in s.system_notes(chat) if "이어 붙일 수 없어" in n]
        loads = [e["cwd"] for e in read_log(s.state) if e["ev"] == "session_load"]
        assert loads[-1] == str(old) and _is_alias(old)
    except Exception:
        print(s.log_tail())
        raise
    finally:
        s.down()


def test_relative_config_mcp_stays_in_agent_workspace(tmp_path):
    """dev_live.cmd --data data\\dev: 설정 경로가 상대 경로면 MCP(cwd=프로젝트 폴더)가 설정을 못 찾아 ~/DDS_Workspace에 번호 폴더를 만들었다."""
    s = Stack(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    s.relative_config = True
    s.agent_env = {"USERPROFILE": str(home), "HOME": str(home)}  # 고치지 못했으면 실제 집 폴더가 아니라 여기에 생긴다
    try:
        s.up()
        chat = s.new_chat()
        s.turn(chat, "[where] 폴더")
        text = s.agent_text(chat)
        assert "세션-시험" in text and str(tmp_path / "ws").replace("\\", "\\\\") in text, text
        assert not (tmp_path / "ws" / f"P{s.pid:04d}").exists()
        assert not list(home.glob("DDS_Workspace/P*"))
    except Exception:
        print(s.log_tail())
        raise
    finally:
        s.down()


def test_project_response_has_uid_for_mcp(stack):
    assert stack.c.get(f"/api/projects/{stack.pid}").json()["uid"]
