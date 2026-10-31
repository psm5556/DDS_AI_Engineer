"""dds-cae MCP: 이 PC의 CAD/CAE 툴을 배치 모드로 실행한다(스크립트 1회 실행, 툴 목록·경로 확인, 규약 조회).
반복 실행(DOE/최적화)은 dds-study의 스터디 + script 어댑터를 쓴다."""
import shutil
import threading
import time
import uuid
from pathlib import Path

from _common import ROOT, api, fresh_tools, resolve_pid, settings, tool_wait_limit
from mcp.server import MCPServer

from agent.dds_agent.job_runner import get_runner
from cae_adapters.tool_commands import DEFAULT_TOOL_COMMANDS, DEFAULT_TOOL_PATHS, build_command, tool_env

mcp = MCPServer(
    "dds-cae",
    instructions=(
        "CAD/CAE 툴(NX, Simcenter 3D, Abaqus, LS-DYNA, Ansys, Altair, STAR-CCM+, Amesim)을 배치로 실행한다. "
        "스크립트를 쓰기 전에 해당 툴의 스킬(예: abaqus-automation)을 먼저 읽고, case_contract로 입출력 규약을 확인하라."
    ),
)
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# 넘으면 솔버까지 끝나는 한계다. 긴 해석을 잘라 라이선스 시간을 버리지 않게 넉넉히 둔다
RUN_SCRIPT_TIMEOUT_SEC = 24 * 3600
_JOBS: dict[str, dict] = {}  # 뒤에서 도는 run_script (script_status로 받는다)


@mcp.tool()
def case_contract() -> str:
    """스크립트 입출력 규약(params.json → results.json, images/)과 tool 키 표를 돌려준다."""
    return (ROOT / "docs" / "skill-contract.md").read_text(encoding="utf-8")


@mcp.tool()
def list_tools() -> dict:
    """이 PC에서 쓸 수 있는 CAD/CAE 툴과 실행 방법.
    - tools: 지원 툴별 상태(ready=바로 사용, partial=일부만 찾음, missing=설치·경로 필요), 읽을 스킬, 쓸 수 있는 tool 키
    - commands: tool 키별 명령 템플릿과 실행 파일 존재 여부(run_script·스터디 adapter_config의 tool 값)
    PC Agent가 설치된 툴을 자동으로 찾는다. missing인 툴은 사용자에게 설치가 필요하다고 알리고, 설치돼 있는데 못 찾았다면
    웹앱 'CAD·CAE 툴' 화면에서 그 툴을 눌러 실행 파일을 고르면 된다고 안내한다(설정 파일을 고치라고 하지 않는다)."""
    from cae_adapters.catalog import TOOLS, tool_status

    fresh_tools()
    paths = {**DEFAULT_TOOL_PATHS, **settings.tools}
    status = {t["id"]: t for t in tool_status(paths)}
    tools = [{"id": t["id"], "name": t["name"], "category": t["category"], "skill": t["skill"], "tool_keys": t["run"],
              "can": t["can"], "status": status[t["id"]]["status"],
              "executables": {e["key"]: e["found"] for e in status[t["id"]]["executables"]}} for t in TOOLS]
    table = {**DEFAULT_TOOL_COMMANDS, **settings.tool_commands}
    commands = []
    for key, tmpl in sorted(table.items()):
        exe_key = tmpl[0][1:-1] if tmpl[0].startswith("{") else None
        exe = paths.get(exe_key, "") if exe_key and exe_key != "python" else "(플랫폼 python)"
        found = True if exe == "(플랫폼 python)" else bool(exe and (Path(exe).exists() or shutil.which(exe)))
        commands.append({"tool": key, "command": " ".join(tmpl), "executable": exe, "found": found})
    return {"tools": tools, "commands": commands}


@mcp.tool()
def project_folder(project_id: int | None = None) -> dict:
    """이 PC에서 현재 프로젝트의 작업 폴더(모델·스크립트를 두는 곳)."""
    pid = resolve_pid(project_id)
    d = settings.project_dir(pid)
    files = sorted(str(p.relative_to(d)) for p in d.rglob("*") if p.is_file() and "studies" not in p.parts)[:200]
    return {"project_id": pid, "folder": str(d), "files": files}


@mcp.tool()
def run_script(tool: str, script: str, filename: str = "script.py", args: list[str] | None = None,
               params: dict[str, float] | None = None, inputs: list[str] | None = None, dry_run: bool = False,
               timeout_sec: int = RUN_SCRIPT_TIMEOUT_SEC, publish_images: bool = True, flow_id: int | None = None,
               node_id: str | None = None, project_id: int | None = None) -> dict:
    """스크립트를 지정한 툴로 1회 실행한다(탐색·디버깅·단발 작업).

    - timeout_sec: 넘으면 스크립트와 그 아래 솔버까지 모두 끝내고 결과 없이 돌아오는 한계다. 해석에 쓸 때는 예상 시간보다 넉넉히 준다.

    - tool: list_tools의 키 (nx, simcenter, abaqus_cae, abaqus_python, lsdyna, ansys_wb, starccm, amesim, python ...)
    - script: 스크립트 본문. 실행 폴더(runs/<시각>_<파일명>/)에 filename으로 저장된다.
    - params: 주면 실행 폴더에 params.json으로 써 준다. inputs: 프로젝트 폴더 기준 복사할 파일(모델 등).
    - 실행 후 results.json(있으면)과 stdout/stderr 끝부분을 돌려주고, images/의 그림을 아티팩트로 게시한다.
    - status='running'과 job_id가 오면 실패가 아니라 오래 걸리는 것이다. 다시 실행하지 말고 script_status(job_id)로 결과를 받는다.
    """
    import json

    pid = resolve_pid(project_id)
    pdir = settings.project_dir(pid)
    run_dir = pdir / "runs" / f"{time.strftime('%Y%m%d_%H%M%S')}_{Path(filename).stem}"
    (run_dir / "images").mkdir(parents=True, exist_ok=True)
    (run_dir / filename).write_text(script, encoding="utf-8")
    if params is not None:
        (run_dir / "params.json").write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    for rel in inputs or []:
        src = (pdir / rel)
        if not src.exists():
            return {"error": f"입력 파일이 없습니다: {rel}"}
        (shutil.copytree(src, run_dir / src.name, dirs_exist_ok=True) if src.is_dir() else shutil.copy2(src, run_dir))
    fresh_tools()
    try:
        cmd = build_command(tool, filename, [str(a) for a in args or []], settings.tools, settings.tool_commands)
    except ValueError as e:
        return {"error": str(e)}
    if dry_run:
        return {"dry_run": True, "command": cmd, "run_dir": str(run_dir)}

    def work() -> dict:
        res = get_runner(settings.runner, settings.hpc).run(cmd, run_dir, timeout=timeout_sec,
                                              env=tool_env(tool, settings.tools, settings.tool_env, str(run_dir)))
        out = {"returncode": res.returncode, "ok": res.ok, "command": cmd, "run_dir": str(run_dir),
               "stdout_tail": res.stdout[-3000:], "stderr_tail": res.stderr[-3000:]}
        rj = run_dir / "results.json"
        if rj.exists():
            try:
                out["results"] = json.loads(rj.read_text(encoding="utf-8"))
            except ValueError:
                out["results_error"] = "results.json 형식 오류"
        if publish_images:
            published = []
            for img in sorted((run_dir / "images").glob("*"))[:8]:
                if img.suffix.lower() in IMAGE_EXT:
                    rel = api.upload_image(pid, img)
                    api.post(f"/api/projects/{pid}/artifacts", {"kind": "image", "title": f"{run_dir.name} · {img.stem}",
                                                                "payload": {"path": rel, "caption": str(img.name)},
                                                                "flow_id": flow_id, "node_id": node_id})
                    published.append(img.name)
            out["published_images"] = published
        return out

    limit = tool_wait_limit()
    if not limit:
        return work()
    # CLI의 도구 시간 제한을 바꿀 수 없다: 넘으면 해석은 도는데 도구는 실패로 보인다. 뒤에서 돌리고 나눠서 기다린다
    return _wait_job(_start_job(work, str(run_dir)), limit)


def _start_job(work, run_dir: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    job = _JOBS[job_id] = {"done": threading.Event(), "result": None, "error": None, "run_dir": run_dir, "started": time.time()}

    def body():
        try:
            job["result"] = work()
        except Exception as e:
            job["error"] = str(e) or repr(e)
        finally:
            job["done"].set()

    threading.Thread(target=body, daemon=True).start()
    return job_id


def _wait_job(job_id: str, wait_sec: float) -> dict:
    job = _JOBS[job_id]
    if not job["done"].wait(timeout=wait_sec):
        return {"status": "running", "job_id": job_id, "run_dir": job["run_dir"], "elapsed_sec": int(time.time() - job["started"]),
                "note": "실패가 아닙니다. 스크립트가 아직 돌고 있습니다. script_status(job_id)로 결과를 받으세요."}
    _JOBS.pop(job_id, None)
    if job["error"]:
        return {"error": job["error"], "run_dir": job["run_dir"]}
    return job["result"]


@mcp.tool()
def script_status(job_id: str, wait_sec: int = 30) -> dict:
    """run_script가 status='running'과 job_id를 돌려줬을 때 결과를 받는다. 실패가 아니라 오래 걸리는 것이다.
    끝나면 run_script와 같은 결과를, 아직이면 다시 status='running'을 돌려준다. 끝날 때까지 이 도구를 반복해 부른다."""
    if job_id not in _JOBS:
        return {"error": f"모르는 job_id입니다: {job_id}. 이미 결과를 받았거나 AI 도구가 다시 시작되어 실행이 끝났습니다. 실행 폴더(runs/)를 확인하세요."}
    limit = tool_wait_limit()
    return _wait_job(job_id, min(wait_sec, limit) if limit else wait_sec)


if __name__ == "__main__":
    mcp.run()
