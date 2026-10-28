"""Linux 서버 준비: Windows PC에서 만든 설치 묶음(--pack)을 서버 데이터 폴더에 넣는다(--import-pack).
실제 묶음 만들기(csc·Python·Node)는 Windows가 필요하므로, 같은 모양의 가짜 묶음으로 넣기·검사·정리만 확인한다."""
import hashlib
import importlib.util
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("prepare_onsite", ROOT / "scripts" / "prepare_onsite.py")
po = importlib.util.module_from_spec(spec)
spec.loader.exec_module(po)


def _pack(tmp: Path, files: dict[str, bytes], manifest: dict | None = None, extra: dict[str, bytes] | None = None) -> Path:
    if manifest is None:
        manifest = {k.split("-")[0]: {"file": k, "sha256": hashlib.sha256(v).hexdigest()} for k, v in files.items()}
    z = tmp / "agent_runtime_pack.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr(po.SETUP_EXE, b"MZ-setup")
        for k, v in {**files, **(extra or {})}.items():
            zf.writestr(k, v)
    return z


def test_import_pack_installs_and_cleans_old(tmp_path):
    rt = tmp_path / "data" / "agent_runtime"
    rt.mkdir(parents=True)
    (rt / "python-old.tar.gz").write_bytes(b"old")
    (rt / "manifest.json").write_text(json.dumps({"python": {"file": "python-old.tar.gz"}}), encoding="utf-8")
    z = _pack(tmp_path, {"python-3.12.zip": b"py", "node-v22.zip": b"node", "wheels-agent.zip": b"whl"})
    assert po.import_pack(z, rt)
    assert po.runtime_ready(rt) and (rt / po.SETUP_EXE).read_bytes() == b"MZ-setup"
    assert not (rt / "python-old.tar.gz").exists()  # 예전 런타임 파일은 정리


def test_import_pack_rejects_damaged_or_unsafe(tmp_path):
    rt = tmp_path / "data" / "agent_runtime"
    bad_sha = {"python": {"file": "python.zip", "sha256": "0" * 64}}
    assert not po.import_pack(_pack(tmp_path, {"python.zip": b"py"}, bad_sha), rt)
    assert not (rt / "manifest.json").exists()  # 실패하면 목록을 바꾸지 않는다
    assert not po.import_pack(_pack(tmp_path, {"python.zip": b"py"}, extra={"../evil.txt": b"x"}), rt)
    assert not (tmp_path / "data" / "evil.txt").exists()
    assert not po.import_pack(tmp_path / "없음.zip", rt)


def test_linux_server_points_to_windows_pack(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(po, "WINDOWS", False)
    rt = tmp_path / "agent_runtime"
    assert not po.build_setup_exe(rt) and not po.build_runtime(rt, False, True)
    assert "--pack" in capsys.readouterr().out
    assert not po.make_pack(tmp_path / "pack.zip", True)  # 묶음은 Windows PC에서만 만든다
    assert po.import_pack(_pack(tmp_path, {"python.zip": b"py"}), rt)  # 넣기는 Linux에서도 된다
    assert po.build_setup_exe(rt) and po.build_runtime(rt, False, True)  # 넣은 뒤에는 '이미 있습니다'
