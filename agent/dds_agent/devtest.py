"""개발 AI의 원격 검증: Linux 서버에서 개발하는 AI가 이 PC의 CAD/CAE 연동을 스스로 시험한다(docs/pc-verification.md).
서버에는 툴이 없으므로, 스크립트를 이 PC에서 돌리고 출력·파일을 돌려준다. 이 PC에서 허용했을 때만 동작한다(main.py에서 확인).
작업마다 ~/.dds-agent/devtest/<시각>/ 에서 돌고, 그 안의 out/ 폴더 파일을 돌려준다. 기록은 devtest/history.jsonl에 남는다."""
from __future__ import annotations

import base64
import json
import os
import platform
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from cae_adapters.tool_commands import build_command, tool_env

from . import __version__, updater
from .config import REPO_ROOT, AgentSettings

TAIL = 200_000  # 돌려주는 출력의 끝부분(글자 수)
MAX_FILES = 8 * 1024 * 1024  # 한 번에 돌려줄 파일 합계. WebSocket 메시지 한도(16MB) 안에서 base64가 들어가게
KEEP_RUNS = 30


def root() -> Path:
    return Path.home() / ".dds-agent" / "devtest"


def _workdir(label: str) -> Path:
    base = root()
    base.mkdir(parents=True, exist_ok=True)
    d = base / f"{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}_{label}"  # 같은 초에 여러 시험이 돌아도 겹치지 않게
    (d / "out").mkdir(parents=True)
    runs = sorted(p for p in base.iterdir() if p.is_dir())
    for old in runs[:-KEEP_RUNS]:  # 오래된 시험 폴더는 지운다
        shutil.rmtree(old, ignore_errors=True)
    return d


def _write_inputs(d: Path, files: dict | None) -> None:
    for name, b64 in (files or {}).items():
        target = (d / name).resolve()
        if d.resolve() not in target.parents:
            raise ValueError(f"시험 폴더 밖의 경로입니다: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(base64.b64decode(b64))


def _collect(d: Path) -> tuple[dict, list[str]]:
    """out/의 파일을 돌려준다. 한도를 넘는 파일은 경로만 알려 주고 fetch로 나눠 받게 한다."""
    out, skipped, total = {}, [], 0
    for p in sorted((d / "out").rglob("*")):
        if not p.is_file():
            continue
        size = p.stat().st_size
        rel = p.relative_to(d / "out").as_posix()
        if total + size > MAX_FILES:
            skipped.append(f"{rel} ({size} 바이트): {p}")
            continue
        out[rel] = base64.b64encode(p.read_bytes()).decode("ascii")
        total += size
    return out, skipped


def _run(cmd: list[str], d: Path, env: dict, timeout: int) -> dict:
    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=d, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        code, stdout, stderr, timed_out = p.returncode, p.stdout, p.stderr, False
    except subprocess.TimeoutExpired as e:
        decode = lambda b: b.decode("utf-8", "replace") if isinstance(b, bytes) else (b or "")  # noqa: E731
        code, stdout, stderr, timed_out = None, decode(e.stdout), decode(e.stderr), True
    except OSError as e:  # 실행 파일이 없는 경우 등
        code, stdout, stderr, timed_out = None, "", f"실행하지 못했습니다: {e}", False
    files, skipped = _collect(d)
    return {"ok": code == 0, "returncode": code, "timed_out": timed_out, "seconds": round(time.time() - t0, 2), "command": cmd,
            "dir": str(d), "stdout": stdout[-TAIL:], "stderr": stderr[-TAIL:], "files": files, "skipped_files": skipped}


def _python_env(base: dict, d: Path) -> dict:
    extra = [str(REPO_ROOT), str(REPO_ROOT / "mcp_servers")]  # 시험 스크립트가 cae_adapters·dds_live 등을 그대로 불러 쓴다
    old = base.get("PYTHONPATH")
    return {**base, "PYTHONPATH": os.pathsep.join(extra + ([old] if old else [])), "PYTHONIOENCODING": "utf-8",
            "DDS_TEST_OUT": str(d / "out")}


def op_python(settings: AgentSettings, env: dict, req: dict) -> dict:
    d = _workdir("python")
    _write_inputs(d, req.get("files"))
    name = req.get("filename") or "check.py"
    (d / name).write_text(req["script"], encoding="utf-8")
    return _run([sys.executable, name, *[str(a) for a in req.get("args") or []]], d, _python_env(env, d), int(req.get("timeout_sec") or 600))


def op_tool(settings: AgentSettings, env: dict, req: dict) -> dict:
    """대화에서 쓰는 것과 같은 명령 키(tool_commands)로 툴을 실행한다"""
    d = _workdir(str(req["tool"]))
    _write_inputs(d, req.get("files"))
    name = req.get("filename") or "script.py"
    (d / name).write_text(req["script"], encoding="utf-8")
    cmd = build_command(req["tool"], name, [str(a) for a in req.get("args") or []], settings.tools, settings.tool_commands)
    run_env = {**_python_env(env, d), **tool_env(req["tool"], settings.tools, settings.tool_env, str(d))}
    return _run(cmd, d, run_env, int(req.get("timeout_sec") or 1800))


def op_info(settings: AgentSettings, env: dict, req: dict) -> dict:
    home = updater.install_home()
    found = {k: v for k, v in settings.tools.items() if k in settings.tool_source}
    return {"ok": True, "version": __version__, "hostname": socket.gethostname(), "platform": platform.platform(),
            "python": sys.version.split()[0], "install_home": str(home) if home else None, "code": str(REPO_ROOT),
            "workspace": str(settings.workspace_dir), "tools": found, "tool_source": settings.tool_source,
            "agents": settings.available_agents()}


def op_logs(settings: AgentSettings, env: dict, req: dict) -> dict:
    home = updater.install_home()
    if home is None:
        return {"ok": False, "error": "개발용 실행이라 로그 파일이 없습니다."}
    lines = int(req.get("lines") or 300)
    out = {}
    for name in req.get("names") or ("agent.log", "launcher.log"):
        p = home / "logs" / Path(name).name
        out[p.name] = "\n".join(p.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]) if p.exists() else None
    return {"ok": True, "logs": out}


def _allowed_roots(settings: AgentSettings) -> list[Path]:
    """받아 갈 수 있는 곳: 시험 폴더, 작업 폴더, Agent 설치·설정 폴더. 사용자의 다른 파일은 안 된다."""
    roots = [root(), settings.workspace_dir, Path.home() / ".dds-agent"]
    if updater.install_home():
        roots.append(updater.install_home())
    return [r.resolve() for r in roots]


def op_fetch(settings: AgentSettings, env: dict, req: dict) -> dict:
    p = Path(req["path"]).expanduser().resolve()
    if not any(r == p or r in p.parents for r in _allowed_roots(settings)):
        return {"ok": False, "error": f"시험 폴더·작업 폴더·Agent 폴더 밖의 파일은 받을 수 없습니다: {p}"}
    if p.name in ("agent.toml", "live.token"):  # 토큰이 들어 있다
        return {"ok": False, "error": "토큰이 들어 있는 파일은 받을 수 없습니다."}
    if not p.is_file():
        return {"ok": False, "error": f"파일이 없습니다: {p}"}
    size = p.stat().st_size
    if size > MAX_FILES:
        return {"ok": False, "error": f"파일이 너무 큽니다({size} 바이트). 스크립트에서 줄여 out/에 넣으세요."}
    return {"ok": True, "path": str(p), "size": size, "data": base64.b64encode(p.read_bytes()).decode("ascii")}


OPS = {"info": op_info, "python": op_python, "tool": op_tool, "logs": op_logs, "fetch": op_fetch}


def run(settings: AgentSettings, env: dict, req: dict) -> dict:
    op = req.get("op")
    if op not in OPS:
        raise ValueError(f"알 수 없는 작업: {op}")
    res = OPS[op](settings, env, req)
    try:  # 이 PC에서 무엇이 실행됐는지 사용자가 볼 수 있게 남긴다
        root().mkdir(parents=True, exist_ok=True)
        rec = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "op": op, "by": req.get("by"), "tool": req.get("tool"), "ok": res.get("ok"),
               "returncode": res.get("returncode"), "dir": res.get("dir")}
        with (root() / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return res
