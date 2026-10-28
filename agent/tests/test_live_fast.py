"""Live Link 빠른 툴(live_set_params·live_view·live_undo·live_info) 테스트.
NX가 없으므로 '가짜 NX'가 대기열의 작업을 dds_live_nx.py처럼 exec로 실행한다. 도우미(ddslive_nx_ops)는 호출만 기록하는 가짜로 바꾼다.
→ MCP가 만든 코드가 실제로 실행되는 Python인지, 도우미에 값이 그대로 전달되는지, 결과·걸린 시간을 돌려주는지 확인한다."""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp_servers"))
import dds_live  # noqa: E402


@pytest.fixture()
def fake_nx(tmp_path, monkeypatch):
    queue = tmp_path / "queue"
    queue.mkdir()
    monkeypatch.setattr(dds_live, "NX_QUEUE", queue)
    calls = []
    ops = types.ModuleType("ddslive_nx_ops")

    def helper(name, **extra):
        def fn(*args, **kw):
            calls.append((name, args, kw))
            out = {"ok": True, "ms": {"total": 5}, **extra}
            shot = kw.get("capture") or (args[0] if name == "capture" else None)
            if shot:
                Image.new("RGB", (3200, 1800), "white").save(shot)
                out["image"] = shot
            return json.dumps(out)
        return fn

    ops.fresh = lambda: ops
    ops.summary = helper("summary", expressions=[{"name": "thickness", "value": 3.0, "unit": "MilliMeter"}])
    ops.set_params = helper("set_params", changed=[{"name": "thickness", "old": 3.0, "new": 4.0}])
    ops.view = helper("view")
    ops.undo = helper("undo", undone=1)
    ops.capture = helper("capture")
    monkeypatch.setitem(sys.modules, "ddslive_nx_ops", ops)

    stop = threading.Event()

    def nx_loop():  # dds_live_nx.py와 같은 방식: 작업 코드를 exec하고 result를 문자열로 남긴다
        while not stop.is_set():
            for job in sorted(queue.glob("job_*.py")):
                res = queue / (job.stem + ".result.json")
                if res.exists():
                    continue
                env = {"result": None}
                try:
                    exec(compile(job.read_text(encoding="utf-8"), str(job), "exec"), env)
                    out = {"ok": True, "stdout": "", "result": None if env["result"] is None else str(env["result"])}
                except Exception as e:  # noqa: BLE001
                    out = {"ok": False, "error": repr(e)}
                res.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            time.sleep(0.02)

    t = threading.Thread(target=nx_loop, daemon=True)
    t.start()
    yield calls
    stop.set()
    t.join(2)
    while str(dds_live.NX_OPS_DIR) in sys.path:
        sys.path.remove(str(dds_live.NX_OPS_DIR))


def test_set_params_calls_helper_once(fake_nx):
    r = dds_live.live_set_params("nx", {"thickness": 4.0, "rib_count": 6}, fem=True)
    assert r["ok"] and r["changed"][0]["new"] == 4.0
    assert r["ms"]["total"] == 5 and "wait" in r["ms"] and "round_trip" in r["ms"]
    assert r["ms"]["wait"] < 2000  # 1초 간격 폴링이 아니다
    name, args, kw = fake_nx[-1]
    assert name == "set_params" and args[0] == {"thickness": 4.0, "rib_count": 6}
    assert kw == {"fem": True, "measure": False, "capture": None}


def test_set_params_rejects_non_numbers(fake_nx):
    r = dds_live.live_set_params("nx", {"thickness": "4mm"})
    assert not r["ok"] and "thickness" in r["error"]
    assert not list(dds_live.NX_QUEUE.glob("job_*.py")) and not fake_nx


def test_view_undo_info(fake_nx):
    assert dds_live.live_view("nx", orient="front", hide=["EXTRUDE(3)"], style="edges")["ok"]
    assert fake_nx[-1] == ("view", (), {"orient": "front", "fit": True, "style": "edges", "show": None,
                                         "hide": ["EXTRUDE(3)"], "only": None, "capture": None})
    u = dds_live.live_undo("nx", steps=2)
    assert u.get("undone") == 1, u
    assert fake_nx[-1] == ("undo", (2,), {})
    info = dds_live.live_info("nx")
    assert info["expressions"][0]["name"] == "thickness"
    assert fake_nx[-1] == ("summary", (), {"all_expressions": False})


def test_capture_same_call_and_shrink(fake_nx, tmp_path, monkeypatch):
    published = []
    shot = tmp_path / "live" / "nx_shot.png"
    shot.parent.mkdir()
    monkeypatch.setattr(dds_live, "resolve_pid", lambda pid=None: 7)
    monkeypatch.setattr(dds_live, "_shot_path", lambda pid, tool: shot)
    monkeypatch.setattr(dds_live.api, "upload_image", lambda pid, path, sub: published.append(path) or "live/x.png")
    monkeypatch.setattr(dds_live.api, "post", lambda path, body: {"id": 42})
    r = dds_live.live_set_params("nx", {"thickness": 4.0}, capture=True, title="두께 4")
    assert r["artifact_id"] == 42 and published == [shot]
    assert fake_nx[-1][2]["capture"] == str(shot)  # 화면 저장도 같은 NX 실행에서
    with Image.open(shot) as img:
        assert max(img.size) == dds_live.MAX_SIDE


def test_pending_when_nx_does_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(dds_live, "NX_QUEUE", tmp_path)
    r = dds_live.live_set_params("nx", {"thickness": 4.0}, timeout_sec=1)
    assert r["pending"] and (tmp_path / f"{r['job']}.py").exists()


def test_other_tools_point_to_live_exec():
    assert "live_exec" in dds_live.live_set_params("abaqus", {"t": 1.0})["error"]
    assert "live_exec" in dds_live.live_view("hypermesh", orient="front")["error"]


def test_shrink_keeps_small_images(tmp_path):
    p = tmp_path / "small.png"
    Image.new("RGB", (800, 600), "white").save(p)
    dds_live._shrink(p)
    with Image.open(p) as img:
        assert img.size == (800, 600)


def test_practice_set_params(monkeypatch):
    """연습용 CAD(라이선스 불필요)로 빠른 툴의 실제 소켓 흐름까지 확인한다."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("DDS_LIVE_TOKEN", "test-token")
    monkeypatch.setitem(dds_live.TOOLS["practice"], "port", port)
    proc = subprocess.Popen([sys.executable, str(dds_live.LIVE_DIR / "practice_tool.py"), "--headless", "--port", str(port)],
                            env={**os.environ, "DDS_LIVE_TOKEN": "test-token"}, stdout=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                if dds_live._call("practice", "ping", timeout=1).get("ok"):
                    break
            except OSError:
                time.sleep(0.1)
        r = dds_live.live_set_params("practice", {"width": 150, "thickness": 6.5})
        assert r["ok"] and r["changed"] == {"thickness": 6.5, "width": 150}
        assert dds_live._call("practice", "info")["summary"]["width"] == 150
    finally:
        proc.kill()
