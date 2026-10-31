"""PC Agent 설정. 기본 위치: %USERPROFILE%\\.dds-agent\\agent.toml (DDS_AGENT_CONFIG로 변경 가능).
MCP 서버들도 이 파일에서 서버 주소·토큰·툴 경로를 읽는다."""
from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from cae_adapters.hidden import console_python
from cae_adapters.tool_commands import DEFAULT_TOOL_PATHS

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("DDS_AGENT_CONFIG", Path.home() / ".dds-agent" / "agent.toml"))


@dataclass
class AgentSettings:
    server_url: str = "http://127.0.0.1:7014"
    token: str = ""  # 웹앱 > 설정 > PC Agent 토큰에서 발급
    workspace_dir: Path = Path.home() / "DDS_Workspace"
    # 사용할 수 있는 에이전트 CLI(모두 ACP로 구동). 대화를 만들 때 사용자가 고른다.
    # [agents.<이름>] label, command(목록) 로 추가/변경. 구버전 Gemini CLI는 "--experimental-acp".
    agents: dict[str, dict] = field(default_factory=lambda: {
        "gemini": {"label": "Gemini CLI", "command": ["gemini", "--acp"],
                   "packages": ["@google/gemini-cli"], "login": "터미널에서 `gemini` 실행 → Google 계정으로 로그인"},
        "claude": {"label": "Claude Code", "command": ["claude-agent-acp"],
                   "packages": ["@anthropic-ai/claude-code", "@agentclientprotocol/claude-agent-acp"],
                   "login": "터미널에서 `claude` 실행 → /login 으로 구독 계정 로그인"},
        "codex": {"label": "Codex CLI", "command": ["codex-acp"],
                  "packages": ["@openai/codex", "@agentclientprotocol/codex-acp"],
                  "login": "터미널에서 `codex login` 실행 → ChatGPT 계정 로그인"},
        "demo": {"label": "데모(시나리오)", "command": ["{python}", "{demo_agent}"], "packages": [],
                 "login": "로그인 불필요 (Gemini 없이 흐름 검증용)"},
    })
    default_agent: str = "gemini"
    # AI CLI 시작(initialize·session/new·session/load) 한계. 사내 보안 검사로 몇 분씩 걸려 넉넉히 둔다. 넘겨도 프로세스는 끝내지 않는다
    cli_start_timeout_sec: int = 1800
    warm_up: bool = True  # Agent가 뜨고 연결되면 마지막에 쓴 CLI와 대화를 미리 띄운다
    # gemini를 ACP 대신 헤드리스(-p --output-format stream-json)로 쓸 때 true
    gemini_headless: bool = False
    headless_extra_args: list[str] = field(default_factory=lambda: ["--approval-mode", "auto_edit"])
    runner: str = "local"  # local | hpc  (스터디별로 adapter_config.runner 로도 지정)
    hpc: dict = field(default_factory=dict)  # [hpc] scheduler·ssh·path_map·exe_map·ncpu·header·poll_sec — job_runner.HpcRunner 참고
    auto_update: bool = True  # 연결 전 기본값. 연결되면 웹앱 > 설정의 값(agent.settings)을 따른다
    tray: bool = True  # Windows 트레이 아이콘으로 연결 상태 표시
    max_parallel_cases: int = 1
    # 케이스 해석 데이터 저장(case.h5, HDF5 압축) — executor.ARCHIVE_DEFAULTS를 덮어쓴다. 예) [archive] upload_max_mb = 1000
    archive: dict = field(default_factory=dict)
    tools: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOOL_PATHS))
    tool_commands: dict[str, list[str]] = field(default_factory=dict)
    tool_env: dict[str, dict[str, str]] = field(default_factory=dict)  # 툴별 추가 환경변수 (예: amesim의 AME)
    # 결과 파일을 여는 CAE 뷰어: 확장자 → 명령(목록, {file} 치환). 없으면 Windows 기본 연결 프로그램으로 연다.
    # 예) [viewers]  ".odb" = ["C:/DDS/Viewer/ddsview.exe", "--open", "{file}"]   "*" = [...]  (모든 결과 파일)
    viewers: dict[str, list[str]] = field(default_factory=dict)
    # 툴 경로는 사용자가 적지 않는다: PC Agent가 찾아 tools.json에 두고(toolscan.py) 아래 값은 apply_tools가 채운다
    tool_source: dict[str, str] = field(default_factory=dict)  # 키 → picked(웹에서 직접 고름) | config(agent.toml) | path(PATH) | auto(자동으로 찾음)
    tools_rev: str = ""  # 툴 경로가 바뀌면 달라지는 값 — 웹이 '내 PC 툴' 화면을 새로 고칠 때 쓴다
    base_tools: dict[str, str] = field(default_factory=dict)  # 기본값 + agent.toml [tools]
    base_tool_env: dict[str, dict[str, str]] = field(default_factory=dict)
    config_tools: set[str] = field(default_factory=set)  # agent.toml [tools]에 적힌 키

    @property
    def ws_url(self) -> str:
        return self.server_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/") + "/ws/agent"

    @property
    def workspace(self):
        from .workspace import Workspace
        return Workspace(self.workspace_dir, self.server_url)

    def project_dir(self, project_id: int, name: str | None = None, uid: str | None = None) -> Path:
        """로컬 데이터 규칙(P0012_<이름>)에 따른 프로젝트 작업 폴더 — workspace.py 참고"""
        return self.workspace.project_dir(project_id, name, uid)

    def agent_command(self, name: str) -> list[str]:
        if name not in self.agents:
            raise KeyError(f"설정에 없는 에이전트입니다: {name}")
        subst = {"{python}": console_python(), "{demo_agent}": str(Path(__file__).parent / "demo_agent.py")}
        cmd = [subst.get(c, c) for c in self.agents[name]["command"]]
        cmd[0] = shutil.which(cmd[0]) or cmd[0]
        return cmd

    def available_agents(self) -> list[dict]:
        out = []
        for name, a in self.agents.items():
            exe = self.agent_command(name)[0]
            out.append({"name": name, "label": a.get("label", name), "login": a.get("login", ""),
                        "installable": bool(a.get("packages")),
                        "installed": bool(Path(exe).exists() or shutil.which(exe))})
        return out


def load_settings(path: Path | None = None) -> AgentSettings:
    s = AgentSettings()
    path = path or CONFIG_PATH
    if path.exists():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        s.config_tools = set(data.get("tools") or {})
        for key, value in data.items():
            if key in ("tools", "tool_commands", "tool_env", "agents", "viewers"):
                getattr(s, key).update(value)
            elif hasattr(s, key):
                setattr(s, key, Path(value) if isinstance(getattr(s, key), Path) else value)
    for env, key in (("DDS_SERVER_URL", "server_url"), ("DDS_TOKEN", "token"), ("DDS_DEFAULT_AGENT", "default_agent")):
        if os.environ.get(env):
            setattr(s, key, os.environ[env])
    if os.environ.get("DDS_WORKSPACE"):
        s.workspace_dir = Path(os.environ["DDS_WORKSPACE"])
    s.workspace_dir = s.workspace_dir.expanduser().resolve()  # ACP session/new는 절대경로 cwd만 받는다
    s.workspace_dir.mkdir(parents=True, exist_ok=True)
    s.base_tools, s.base_tool_env = dict(s.tools), {k: dict(v) for k, v in s.tool_env.items()}
    apply_tools(s)
    return s


def apply_tools(s: AgentSettings, state: dict | None = None):
    """툴 실행 파일 경로를 정한다 — 사용자는 아무것도 적지 않아도 된다. 우선순위:
    웹에서 직접 고른 것(picked) > agent.toml [tools]·PATH에 있는 것 > 자동으로 찾은 것(tools.json found) > 기본 설치 경로.
    없어진 파일(제거·업그레이드)은 건너뛰므로 다시 부르면 지금 PC 상태를 따라간다."""
    import hashlib
    import json

    from cae_adapters.catalog import discover, path_keys, resolve

    from . import toolscan

    st = state if state is not None else toolscan.load_state()
    tools, source = dict(s.base_tools or s.tools), {}
    for key in sorted(path_keys()):
        picked = st["picked"].get(key)
        if picked and resolve(picked):
            tools[key], source[key] = picked, "picked"
        elif resolve(tools.get(key)):
            source[key] = "config" if key in s.config_tools else "path"
        else:
            found = st["found"].get(key)
            found = found if found and resolve(found) else discover(key)
            if found:
                tools[key], source[key] = found, "auto"
    for k in [k for k in s.tools if k not in tools]:
        del s.tools[k]
    s.tools.update(tools)  # 같은 dict를 고쳐 실행 중인 케이스 실행기도 새 경로를 본다
    s.tool_source = source
    # Amesim 내장 파이썬은 AME(설치 폴더)·PATH가 있어야 돈다 — agent.toml·시스템에 없으면 찾은 경로로 채운다
    s.tool_env.clear()
    s.tool_env.update({k: dict(v) for k, v in s.base_tool_env.items()})
    ame_py = resolve(tools.get("amesim_python")) if source.get("amesim_python") else None
    if ame_py and "amesim" not in s.tool_env and not os.environ.get("AME"):
        ame = str(Path(ame_py).parent.parent)
        s.tool_env["amesim"] = {"AME": ame, "PATH": ame + ";" + ame + "\\win64;{PATH}"}
    s.tools_rev = hashlib.sha1(json.dumps([sorted(tools.items()), sorted(source.items())]).encode()).hexdigest()[:10]
