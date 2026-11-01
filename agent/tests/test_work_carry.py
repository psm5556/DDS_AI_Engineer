"""새 Task의 첫 AI 시작 전 복사(agent/dds_agent/carry.py). 복사이며, 여러 번 해도 같고, 덮어쓰지 않고, 실패해도 AI는 시작한다."""
import hashlib
import io
import zipfile

from agent.dds_agent.carry import MAX_COPY, apply_carry
from agent.dds_agent.workspace import Workspace

BODY = b"print('run')\n"
SHA = hashlib.sha256(BODY).hexdigest()


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


class Api:
    def __init__(self, blobs: dict[str, bytes]):
        self.blobs, self.calls = blobs, []

    def download(self, path, dest):
        self.calls.append(path)
        if path not in self.blobs:
            raise RuntimeError("서버 오류 404")
        dest.write_bytes(self.blobs[path])
        return hashlib.sha256(self.blobs[path]).hexdigest()


def _world(tmp_path):
    ws = Workspace(tmp_path / "ws")
    work = {"id": 9, "uid": "u" * 32, "seq": 2, "name": "2차", "legacy": False}
    d = ws.work_dir(1, work, "스파이더", "p" * 32)
    return ws, work, d


def _carry(study=5, blob=None):
    blob = blob if blob is not None else _zip({"scripts/run.py": BODY})
    return {"recipes": [{"study_id": study, "name": "S", "sha256": hashlib.sha256(blob).hexdigest(), "files": [{"path": "scripts/run.py", "sha256": SHA}]}], "attachments": [], "local_source": None}, blob


def test_nothing_to_carry_returns_none(tmp_path):
    ws, work, d = _world(tmp_path)
    assert apply_carry(Api({}), ws, d, work) is None
    assert apply_carry(Api({}), ws, d, {**work, "carry": None}) is None


def test_recipe_is_restored_and_second_time_is_the_same(tmp_path):
    ws, work, d = _world(tmp_path)
    carry, blob = _carry()
    api = Api({"/api/works/9/carry/5": blob})
    one = apply_carry(api, ws, d, {**work, "carry": carry})
    assert one["scripts"] == ["scripts/run.py"] and not one["errors"] and (d / "scripts" / "run.py").read_bytes() == BODY
    two = apply_carry(api, ws, d, {**work, "carry": carry})
    assert two["scripts"] == [] and two["unchanged"] == ["scripts/run.py"] and not two["conflicts"]


def test_existing_different_file_is_kept_and_reported(tmp_path):
    ws, work, d = _world(tmp_path)
    (d / "scripts").mkdir(exist_ok=True)
    (d / "scripts" / "run.py").write_text("사람이 고친 것", encoding="utf-8")
    carry, blob = _carry()
    r = apply_carry(Api({"/api/works/9/carry/5": blob}), ws, d, {**work, "carry": carry})
    assert r["conflicts"] == ["scripts/run.py"] and (d / "scripts" / "run.py").read_text(encoding="utf-8") == "사람이 고친 것"


def test_server_failure_is_reported_not_raised(tmp_path):
    """스크립트를 못 받아도 AI는 시작한다. 새로 만들 수 있고, 서버가 다음 세션에 다시 보낸다."""
    ws, work, d = _world(tmp_path)
    carry, _ = _carry()
    r = apply_carry(Api({}), ws, d, {**work, "carry": carry})
    assert r["errors"] and "서버 오류" in r["errors"][0] and r["scripts"] == []


def test_tampered_bundle_is_refused(tmp_path):
    ws, work, d = _world(tmp_path)
    carry, blob = _carry()
    carry["recipes"][0]["sha256"] = "0" * 64
    r = apply_carry(Api({"/api/works/9/carry/5": blob}), ws, d, {**work, "carry": carry})
    assert r["errors"] and not (d / "scripts" / "run.py").exists()


def test_unsafe_paths_never_land(tmp_path):
    """다른 사람이 만든 묶음이다. 지침 파일이나 폴더 밖으로 쓰지 못한다."""
    ws, work, d = _world(tmp_path)
    for name in ("AGENTS.md", ".claude/settings.json", "../evil.py"):
        blob = _zip({name: b"x"})
        carry = {"recipes": [{"study_id": 5, "name": "S", "sha256": hashlib.sha256(blob).hexdigest(), "files": [{"path": name, "sha256": hashlib.sha256(b"x").hexdigest()}]}]}
        r = apply_carry(Api({"/api/works/9/carry/5": blob}), ws, d, {**work, "carry": carry})
        assert r["errors"], name
    assert not (d / "AGENTS.md").read_text(encoding="utf-8", errors="ignore").startswith("x") if (d / "AGENTS.md").exists() else True
    assert not (d.parent / "evil.py").exists()


def test_my_own_folder_wins_over_the_server_bundle(tmp_path):
    """아직 올라가지 않은 스크립트도 내 PC 폴더에는 있다. 숨김·지침 파일은 옮기지 않는다."""
    ws, work, d = _world(tmp_path)
    old = {"id": 8, "uid": "v" * 32, "seq": 1, "name": "1차", "legacy": False}
    src = ws.work_dir(1, old, "스파이더", "p" * 32)
    (src / "scripts" / "sub").mkdir(parents=True, exist_ok=True)
    (src / "scripts" / "run.py").write_bytes(BODY)
    (src / "scripts" / "sub" / "new.py").write_bytes(b"only here\n")
    (src / "scripts" / "AGENTS.md").write_bytes(b"x")
    carry, blob = _carry()
    carry["local_source"] = {"project_id": 1, "project_uid": "p" * 32, "work": old}
    api = Api({"/api/works/9/carry/5": blob})
    r = apply_carry(api, ws, d, {**work, "carry": carry})
    assert sorted(r["scripts"]) == ["scripts/run.py", "scripts/sub/new.py"]
    assert not (d / "scripts" / "AGENTS.md").exists() and api.calls == [], "내 폴더가 있으면 내려받지 않는다"
    (src / "scripts" / "sub" / "new.py").write_bytes(b"changed later\n")
    assert (d / "scripts" / "sub" / "new.py").read_bytes() == b"only here\n", "복사이며 연결이 아니다"


def test_my_folder_gone_falls_back_to_the_bundle(tmp_path):
    ws, work, d = _world(tmp_path)
    carry, blob = _carry()
    carry["local_source"] = {"project_id": 1, "project_uid": "p" * 32, "work": {"id": 8, "uid": "z" * 32, "seq": 7, "name": "없음", "legacy": False}}
    r = apply_carry(Api({"/api/works/9/carry/5": blob}), ws, d, {**work, "carry": carry})
    assert r["scripts"] == ["scripts/run.py"]


def test_big_folder_stops_at_the_cap(tmp_path):
    ws, work, d = _world(tmp_path)
    old = {"id": 8, "uid": "v" * 32, "seq": 1, "name": "1차", "legacy": False}
    src = ws.work_dir(1, old, "스파이더", "p" * 32)
    (src / "scripts").mkdir(exist_ok=True)
    with open(src / "scripts" / "huge.bin", "wb") as f:
        f.truncate(MAX_COPY + 1)
    (src / "scripts" / "run.py").write_bytes(BODY)
    r = apply_carry(Api({}), ws, d, {**work, "carry": {"recipes": [], "local_source": {"project_id": 1, "project_uid": "p" * 32, "work": old}}})
    assert r["skipped"] == ["scripts/huge.bin"] and r["scripts"] == ["scripts/run.py"]


def test_input_attachment_lands_in_inputs_and_is_not_overwritten(tmp_path):
    ws, work, d = _world(tmp_path)
    (d / "inputs").mkdir(exist_ok=True)
    (d / "inputs" / "other.dat").write_bytes(b"mine")
    body = b"model"
    att = [{"id": 3, "filename": "m.dat", "size": 5, "sha256": hashlib.sha256(body).hexdigest()},
           {"id": 4, "filename": "other.dat", "size": 5, "sha256": hashlib.sha256(body).hexdigest()}]
    api = Api({"/api/attachments/3/download": body, "/api/attachments/4/download": body})
    r = apply_carry(api, ws, d, {**work, "carry": {"attachments": att}})
    assert r["inputs"] == ["inputs/m.dat", "inputs/other_4.dat"]
    assert (d / "inputs" / "other.dat").read_bytes() == b"mine" and (d / "inputs" / "m.dat").read_bytes() == body
    again = apply_carry(api, ws, d, {**work, "carry": {"attachments": att[:1]}})
    assert again["inputs"] == ["inputs/m.dat"]


# ---------- 되돌린 Task의 폴더 ----------
def test_discard_removes_only_when_just_what_we_brought(tmp_path):
    ws, work, d = _world(tmp_path)
    (d / "scripts" / "run.py").write_bytes(BODY)
    (d / "AGENTS.md").write_text("규칙", encoding="utf-8")
    assert ws.discard_work(1, "p" * 32, work, ["scripts/run.py"]) == "gone"
    assert not d.exists()
    assert ws.discard_work(1, "p" * 32, work, []) == "gone", "두 번 해도 같다"


def test_discard_keeps_folder_with_anything_else(tmp_path):
    ws, work, d = _world(tmp_path)
    (d / "scripts" / "run.py").write_bytes(BODY)
    (d / "scripts" / "mine.py").write_text("사람이 만든 것", encoding="utf-8")
    assert ws.discard_work(1, "p" * 32, work, ["scripts/run.py"]) == "kept"
    assert (d / "scripts" / "mine.py").exists()


def test_discard_held_by_cli_waits_for_next_start(tmp_path, monkeypatch):
    import shutil as sh

    ws, work, d = _world(tmp_path)
    real = sh.rmtree
    monkeypatch.setattr("agent.dds_agent.workspace.shutil.rmtree", lambda p, **k: (_ for _ in ()).throw(PermissionError("잠김")))
    assert ws.discard_work(1, "p" * 32, work, []) == "later"
    assert d.exists()
    monkeypatch.setattr("agent.dds_agent.workspace.shutil.rmtree", real)
    # 그사이 사람이 파일을 넣었으면 지우지 않는다
    (d / "scripts" / "mine.py").write_text("x", encoding="utf-8")
    ws.retry_discards()
    assert d.exists() and not ws._discard_file().exists()
    # 비어 있으면 다음 시작 때 지운다
    (d / "scripts" / "mine.py").unlink()
    monkeypatch.setattr("agent.dds_agent.workspace.shutil.rmtree", lambda p, **k: (_ for _ in ()).throw(PermissionError("잠김")))
    assert ws.discard_work(1, "p" * 32, work, []) == "later"
    monkeypatch.setattr("agent.dds_agent.workspace.shutil.rmtree", real)
    ws.retry_discards()
    assert not d.exists() and not ws._discard_file().exists()
