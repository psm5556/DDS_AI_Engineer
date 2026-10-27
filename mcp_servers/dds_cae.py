"""dds-cae MCP: 이 PC의 CAD/CAE 툴을 배치 모드로 실행한다(스크립트 1회 실행, 툴 목록·경로 확인, 규약 조회).
반복 실행(DOE/최적화)은 dds-study의 스터디 + script 어댑터를 쓴다."""
import shutil
import time
from pathlib import Path

from _common import ROOT, api, fresh_tools, resolve_pid, settings
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
               timeout_sec: int = 3600, publish_images: bool = True, flow_id: int | None = None,
               node_id: str | None = None, project_id: int | None = None) -> dict:
    """스크립트를 지정한 툴로 1회 실행한다(탐색·디버깅·단발 작업).

    - tool: list_tools의 키 (nx, simcenter, abaqus_cae, abaqus_python, lsdyna, ansys_wb, starccm, amesim, python ...)
    - script: 스크립트 본문. 실행 폴더(runs/<시각>_<파일명>/)에 filename으로 저장된다.
    - params: 주면 실행 폴더에 params.json으로 써 준다. inputs: 프로젝트 폴더 기준 복사할 파일(모델 등).
    - 실행 후 results.json(있으면)과 stdout/stderr 끝부분을 돌려주고, images/의 그림을 아티팩트로 게시한다.
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


if __name__ == "__main__":
    mcp.run()
