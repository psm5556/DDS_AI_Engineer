"""Task 폴더: P0012_<Project>\\T01_<Task>. 조회로는 만들지 않고, 이름을 따라가고, 잠겨 있으면 접합으로 보여 준다.
번호 규칙과 이름 절차는 Project 폴더와 같다. 잠금은 실제 자식 프로세스로 시험한다(test_folder_names.Holder)."""
import json
import os
from pathlib import Path

import pytest

from agent.dds_agent import workspace as W
from agent.dds_agent.workspace import Workspace
from agent.tests.test_folder_names import Holder, _hidden, _is_alias, windows_only


def _work(seq=1, name="1차 경량화", uid=None, **kw):
    return {"id": seq + 100, "uid": uid or f"work{seq:04d}aaaaaaaaaaaaaaaaaaaaaa", "seq": seq, "name": name, "legacy": False, **kw}


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path / "ws", "http://s")


def _project(ws, pid=12, name="스파이더", uid="u12"):
    return ws.project_dir(pid, name, uid)


def _tasks(pdir: Path) -> list[str]:
    return sorted(p.name for p in pdir.glob("T*") if not _hidden(p))


# ---------- 조회로는 만들지 않는다
def test_reads_make_no_folders(ws):
    w = _work()
    assert ws.find_work(12, w, "u12") is None
    assert ws.tree(12, "", "스파이더", project_uid="u12", work=w)["missing"] is True
    assert ws.folder_info(12, "스파이더", "u12", w)["missing"] is True
    with pytest.raises(FileNotFoundError):
        ws.resolve(12, "", "스파이더", "u12", w)
    assert ws.sync_work_name(12, "u12", w, may_move=True) == {"found": False}
    assert not list(ws.root.glob("P*")), "Project 폴더도 만들지 않는다"


def test_reads_do_not_make_task_inside_existing_project(ws):
    pdir = _project(ws)
    assert ws.tree(12, "", "스파이더", project_uid="u12", work=_work())["entries"] == []
    assert not list(pdir.glob("T*"))


# ---------- 만들기와 찾기
def test_work_dir_is_number_and_name(ws):
    w = _work()
    d = ws.work_dir(12, w, "스파이더", "u12")
    assert d.parent.name == "P0012_스파이더" and d.name == "T01_1차-경량화"
    for sub in W.SUBDIRS:
        assert (d / sub).is_dir()
    info = json.loads((d / "work.json").read_text(encoding="utf-8"))
    assert info["uid"] == w["uid"] and info["seq"] == 1 and info["work_id"] == w["id"] and info["project_id"] == 12
    assert ws.work_dir(12, {**w, "name": "다른 이름"}, "스파이더", "u12") == d, "찾기는 이름을 보지 않는다"


@pytest.mark.parametrize("name", ["", "...", None])
def test_no_bare_number_folder(ws, name):
    d = ws.work_dir(12, _work(name=name), "스파이더", "u12")
    assert d.name == "T01_untitled" and not (d.parent / "T01").exists()


def test_seq_over_99(ws):
    a, b = _work(seq=10, name="열"), _work(seq=100, name="백")
    da, db = ws.work_dir(12, a, "스파이더", "u12"), ws.work_dir(12, b, "스파이더", "u12")
    assert da.name == "T10_열" and db.name == "T100_백" and ws.find_work(12, a, "u12") == da


@pytest.mark.parametrize("work", [None, {"id": 1, "uid": "x", "seq": 0, "name": "이전 기록", "legacy": True}])
def test_legacy_and_unknown_use_project_folder(ws, work):
    pdir = _project(ws)
    assert ws.work_dir(12, work, "스파이더", "u12") == pdir
    assert ws.study_dir(12, 3, "스터디", "스파이더", "u12", work).parent == pdir / "studies"
    assert not list(pdir.glob("T*"))


def test_study_lives_in_task_folder(ws):
    w = _work()
    d = ws.study_dir(12, 3, "스터디", "스파이더", "u12", w)
    assert d.parent.parent.name == "T01_1차-경량화" and (d / "cases").is_dir()


def test_same_number_other_uid_is_not_mine(ws):
    a = _work(uid="a" * 32)
    da = ws.work_dir(12, a, "스파이더", "u12")
    b = _work(uid="b" * 32)  # 서버 DB를 새로 만들었다
    assert ws.find_work(12, b, "u12") is None
    db = ws.work_dir(12, b, "스파이더", "u12")
    assert da != db and db.name == "T01_1차-경량화_bbbbbb"
    assert ws.find_work(12, a, "u12") == da and ws.find_work(12, b, "u12") == db


def test_folder_without_record_is_left_alone(ws):
    pdir = _project(ws)
    (pdir / "T01_사용자폴더").mkdir()
    (pdir / "T01_사용자폴더" / "메모.txt").write_text("내 것", encoding="utf-8")
    d = ws.work_dir(12, _work(), "스파이더", "u12")
    assert d.name == "T01_1차-경량화" and not (pdir / "T01_사용자폴더" / "work.json").exists()


def test_explorer_copy_is_not_the_task(ws):
    w = _work()
    d = ws.work_dir(12, w, "스파이더", "u12")
    copy = d.parent / "T01_1차-경량화 - 복사본"
    copy.mkdir()
    (copy / "work.json").write_text((d / "work.json").read_text(encoding="utf-8"), encoding="utf-8")
    ws.sync_work_name(12, "u12", {**w, "name": "새 이름"}, may_move=True)
    assert ws.find_work(12, w, "u12").name == "T01_새-이름" and copy.is_dir()


def test_long_workspace_shortens_task_name(tmp_path):
    root = tmp_path / ("긴" * max(1, 110 - len(str(tmp_path))))
    ws = Workspace(root, "http://s")
    d = ws.work_dir(1, _work(name="가" * 60), "나" * 60, "u1")
    deepest = d / "studies" / f"S003_{'x' * 24}" / "cases" / f"C0001_{'y' * 12}" / "fields" / ("z" * 20 + ".npz")
    assert len(d.name) < len("T01_") + W.SLUG_MAX and len(d.parent.name) < len("P0001_") + W.SLUG_MAX
    assert len(str(deepest)) < 260


def test_short_workspace_keeps_full_names():
    """기본 위치에서 Project·Task 이름을 모두 24자까지 쓴다."""
    project = W._Slot(Path(r"C:\Users\psm55\DDS_Workspace"), "P0012", W.PROJECT_FILE, W.PROJECT_RESERVE)
    assert Workspace._fit(project) == W.SLUG_MAX
    task = W._Slot(Path(r"C:\Users\psm55\DDS_Workspace") / ("P0012_" + "가" * W.SLUG_MAX), "T01", W.WORK_FILE, W.PATH_RESERVE)
    assert Workspace._fit(task) == W.SLUG_MAX


# ---------- 경로
def test_resolve_stays_inside_the_task(ws):
    a, b = _work(1, "하나"), _work(2, "둘")
    da, db = ws.work_dir(12, a, "스파이더", "u12"), ws.work_dir(12, b, "스파이더", "u12")
    other = ws.work_dir(13, _work(1, "남의 것", uid="c" * 32), "다른", "u13")
    assert ws.resolve(12, "inputs", "스파이더", "u12", a) == (da / "inputs").resolve()
    for bad in ("..", f"../{db.name}", str(other), "../../P0013_다른"):
        with pytest.raises(ValueError):
            ws.resolve(12, bad, "스파이더", "u12", a)


def test_legacy_view_hides_task_folders(ws):
    pdir = _project(ws)
    ws.work_dir(12, _work(), "스파이더", "u12")
    legacy = {"id": 1, "uid": "x", "seq": 0, "name": "이전 기록", "legacy": True}
    names = {e["name"] for e in ws.tree(12, "", "스파이더", project_uid="u12", work=legacy)["entries"]}
    assert "inputs" in names and not any(n.startswith("T01") for n in names)
    assert any(e["name"].startswith("T01") for e in ws.tree(12, "", "스파이더", project_uid="u12")["entries"]), "Task를 모르는 호출은 그대로 본다"
    assert pdir.is_dir()


def test_task_tree_is_relative_to_task(ws):
    w = _work()
    d = ws.work_dir(12, w, "스파이더", "u12")
    (d / "inputs" / "m.prt").write_text("x")
    t = ws.tree(12, "inputs", "스파이더", project_uid="u12", work=w)
    assert t["root"] == str(d) and t["path"] == "inputs" and [e["path"] for e in t["entries"]] == ["inputs/m.prt"]


@pytest.mark.parametrize("inside", [False, True])
def test_task_folder_cannot_become_a_project_folder(ws, inside):
    d = ws.work_dir(12, _work(), "스파이더", "u12")
    target = d / "inputs" if inside else d
    with pytest.raises(ValueError, match="Task 폴더"):
        ws.check_folder(str(target), "u99")
    with pytest.raises(ValueError, match="Task 폴더"):
        ws.check_folder(str(target), "u12")


# ---------- 이름
@windows_only
def test_free_task_is_really_renamed_and_old_name_works(ws):
    w = _work(name="처음")
    d = ws.work_dir(12, w, "스파이더", "u12")
    (d / "scripts" / "a.py").write_text("x")
    res = ws.sync_work_name(12, "u12", {**w, "name": "새 이름"}, may_move=True)
    new = d.parent / "T01_새-이름"
    assert res["moved"] and new.is_dir() and not _is_alias(new) and not _hidden(new)
    assert _is_alias(d) and _hidden(d) and (d / "scripts" / "a.py").read_text() == "x", "옛 절대 경로가 그대로 돈다"
    assert ws.find_work(12, w, "u12") == new and _tasks(d.parent) == ["T01_새-이름"]
    assert json.loads((new / "work.json").read_text(encoding="utf-8"))["folder"] == "T01_새-이름"


def test_sync_twice_is_idempotent(ws):
    w = _work(name="처음")
    d = ws.work_dir(12, w, "스파이더", "u12")
    before = (d / "work.json").read_text(encoding="utf-8")
    assert ws.sync_work_name(12, "u12", w, may_move=True)["pending"] is False
    assert json.loads(before)["uid"] == w["uid"]
    once = (d / "work.json").read_text(encoding="utf-8")
    ws.sync_work_name(12, "u12", w, may_move=True)
    assert ws.find_work(12, w, "u12") == d and (d / "work.json").read_text(encoding="utf-8") == once


@windows_only
def test_locked_task_shows_new_name_and_keeps_cwd(ws):
    """세션 중 이름 변경: 실제 폴더는 CLI가 잡고 있어 옛 이름 그대로 숨기고, 새 이름을 접합으로 보인다. 프로세스를 끝내지 않는다."""
    w = _work(name="처음")
    d = ws.work_dir(12, w, "스파이더", "u12")
    pdir = d.parent
    with Holder(d) as h:
        res = ws.sync_work_name(12, "u12", {**w, "name": "새 이름"}, may_move=True)  # rename이 실패해도 끊기지 않는다
        shown = pdir / "T01_새-이름"
        assert res["pending"] and _is_alias(shown) and not _hidden(shown) and _hidden(d) and h.alive()
        assert _tasks(pdir) == ["T01_새-이름"] and d.is_dir() and not _is_alias(d)
        (shown / "scripts" / "via.txt").write_text("v")
        assert (d / "scripts" / "via.txt").read_text() == "v"
        assert ws.find_work(12, w, "u12") == d, "접합을 후보로 잡지 않는다"
        assert ws.visible_work_dir(12, w, "스파이더", "u12") == shown
        assert [p[2]["uid"] for p in Workspace(ws.root, "http://s").pending_works()] == [w["uid"]], "Agent를 다시 시작해도 이어 간다"
    res = ws.sync_work_name(12, "u12", {"id": w["id"], "uid": w["uid"], "seq": 1}, may_move=True)  # 이름은 work.json에 있다
    assert res["moved"] and shown.is_dir() and not _is_alias(shown) and _is_alias(d) and _hidden(d) and not _hidden(shown)
    assert ws.pending_works() == []
    assert (d / "scripts" / "via.txt").read_text() == "v"


@windows_only
def test_may_move_false_never_tries(ws):
    w = _work(name="처음")
    d = ws.work_dir(12, w, "스파이더", "u12")
    res = ws.sync_work_name(12, "u12", {**w, "name": "다음"}, may_move=False)
    assert res["pending"] and d.is_dir() and not _is_alias(d) and _is_alias(d.parent / "T01_다음")


@windows_only
def test_alias_refused_says_when(ws, monkeypatch):
    w = _work(name="처음")
    d = ws.work_dir(12, w, "스파이더", "u12")

    def refuse(*a):
        raise OSError("정책")

    monkeypatch.setattr(W, "_make_alias", refuse)
    res = ws.sync_work_name(12, "u12", {**w, "name": "다음"}, may_move=False)
    assert res["pending"] and "T01_다음" in ws.tree(12, "", "스파이더", project_uid="u12", work=w)["notice"]
    assert _tasks(d.parent) == ["T01_처음"]


@windows_only
def test_project_rename_relinks_inner_alias(ws):
    """순서: Task → Project → 안쪽 접합 재생성. 대상 경로가 옛 Project 이름이라 그 접합이 사라지면 끊긴다."""
    w = _work(name="처음")
    d = ws.work_dir(12, w, "스파이더", "u12")
    (d / "m.txt").write_text("m")
    with Holder(d):
        ws.sync_work_name(12, "u12", {**w, "name": "다음"}, may_move=False)
        assert _is_alias(d.parent / "T01_다음")
    ws.sync_name(12, "u12", "새 부품", may_move=True)
    old_p, new_p = d.parent, d.parent.parent / "P0012_새-부품"
    assert new_p.is_dir() and not _is_alias(new_p) and _is_alias(old_p)
    shown = new_p / "T01_다음"
    assert _is_alias(shown) and (shown / "m.txt").read_text() == "m" and not _hidden(shown)
    W._drop_alias(old_p)  # 옛 이름 접합이 지워져도 안쪽은 살아 있어야 한다
    assert (shown / "m.txt").read_text() == "m"
    assert os.path.normcase(os.path.realpath(shown)) == os.path.normcase(os.path.realpath(new_p / "T01_처음"))
    assert ws.find_work(12, w, "u12") == new_p / "T01_처음"
    assert ws.sync_work_name(12, "u12", {"id": w["id"], "uid": w["uid"], "seq": 1}, may_move=True)["moved"]
    assert (new_p / "T01_다음" / "m.txt").read_text() == "m" and not _is_alias(new_p / "T01_다음")


@windows_only
def test_project_rename_with_task_locked(ws):
    w = _work(name="처음")
    d = ws.work_dir(12, w, "스파이더", "u12")
    pdir = d.parent
    with Holder(d) as h:
        res = ws.sync_name(12, "u12", "새 부품", may_move=True)  # 안쪽을 잡고 있어 실제 이름은 못 바꾼다
        shown = pdir.parent / "P0012_새-부품"
        assert res["pending"] and _is_alias(shown) and pdir.is_dir() and h.alive()
        assert (shown / d.name / "work.json").is_file()
        assert ws.visible_work_dir(12, w, "새 부품", "u12") == shown / d.name


@windows_only
def test_crash_between_renames_recovers(ws):
    w = _work(name="처음")
    d = ws.work_dir(12, w, "스파이더", "u12")
    os.rename(d, d.parent / f"T01_{W.MOVING}")
    assert ws.find_work(12, w, "u12").name == f"T01_{W.MOVING}", "끊겨도 uid로 찾는다"
    ws.sync_work_name(12, "u12", {**w, "name": "다음"}, may_move=True)
    assert (d.parent / "T01_다음").is_dir() and not (d.parent / f"T01_{W.MOVING}").exists()


def test_custom_project_folder_is_not_renamed_but_task_follows(ws, tmp_path):
    mine = tmp_path / "내폴더"
    ws.set_folder(12, "스파이더", "u12", str(mine))
    w = _work(name="처음")
    d = ws.work_dir(12, w, "스파이더", "u12")
    assert d.parent == mine and d.name == "T01_처음"
    ws.sync_name(12, "u12", "새 부품", may_move=True)
    ws.sync_work_name(12, "u12", {**w, "name": "다음"}, may_move=True)
    assert mine.is_dir() and (mine / "T01_다음").is_dir()


def test_other_pc_has_no_folder(ws):
    """다른 PC의 내 Task: 이 PC에 Project 폴더만 있다. 조회와 이름 변경으로는 생기지 않고, AI를 시작하면 생긴다."""
    pdir = _project(ws)
    w = _work()
    assert ws.sync_work_name(12, "u12", {**w, "name": "다음"}, may_move=True)["found"] is False
    assert not list(pdir.glob("T*"))
    assert ws.work_dir(12, {**w, "name": "다음"}, "스파이더", "u12").name == "T01_다음"
