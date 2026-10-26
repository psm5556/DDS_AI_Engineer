"""PC Agent 설정. 기본 위치: %USERPROFILE%\\.dds-agent\\agent.toml (DDS_AGENT_CONFIG로 변경 가능).
MCP 서버들도 이 파일에서 서버 주소·토큰·툴 경로를 읽는다."""
from __future__ import annotations

import os
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from cae_adapters.tool_commands import DEFAULT_TOOL_PATHS

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("DDS_AGENT_CONFIG", Path.home() / ".dds-agent" / "agent.toml"))


@dataclass
class AgentSettings:
    server_url: str = "http://127.0.0.1:8765"
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
    # gemini를 ACP 대신 헤드리스(-p --output-format stream-json)로 쓸 때 true
    gemini_headless: bool = False
    headless_extra_args: list[str] = field(default_factory=lambda: ["--approval-mode", "auto_edit"])
    runner: str = "local"  # local | hpc  (스터디별로 adapter_config.runner 로도 지정)
    hpc: dict = field(default_factory=dict)  # [hpc] scheduler·ssh·path_map·exe_map·ncpu·header·poll_sec — job_runner.HpcRunner 참고
    auto_update: bool = True  # 서버에 새 버전이 배포되면 유휴 시 자동 업데이트
    tray: bool = True  # Windows 트레이 아이콘으로 연결 상태 표시
    max_parallel_cases: int = 1
    tools: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOOL_PATHS))
    tool_commands: dict[str, list[str]] = field(default_factory=dict)
    tool_env: dict[str, dict[str, str]] = field(default_factory=dict)  # 툴별 추가 환경변수 (예: amesim의 AME)
    # 결과 파일을 여는 CAE 뷰어: 확장자 → 명령(목록, {file} 치환). 없으면 Windows 기본 연결 프로그램으로 연다.
    # 예) [viewers]  ".odb" = ["C:/DDS/Viewer/ddsview.exe", "--open", "{file}"]   "*" = [...]  (모든 결과 파일)
    viewers: dict[str, list[str]] = field(default_factory=dict)

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
        subst = {"{python}": sys.executable, "{demo_agent}": str(Path(__file__).parent / "demo_agent.py")}
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
        for key, value in tomllib.loads(path.read_text(encoding="utf-8")).items():
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
    return s
