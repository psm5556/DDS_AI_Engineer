"""마켓 패키지 검사기(규격: docs/market-package-spec.md).

올린 코드를 실행하지 않고 디스크에 풀지도 않는다. 메모리에서 읽고 AST로만 본다. 거절한 묶음은 어디에도 남지 않는다.
- 거절: 받으면 다른 PC에서 망가지거나 위험이 큰 것. 문구에 이유와 할 일을 함께 쓴다.
- 표시: 정상일 수도 있는 것. 검토자가 항목마다 확인해야 승인할 수 있다.
- 고침: 서버가 안전하게 고칠 수 있는 것(글자 인코딩, 여러 줄 설명, 이름 불일치, 권한 설정). 초안 화면에 무엇을 고쳤는지 보여 준다.
자동 검사는 탐지일 뿐이다. 특정 조건에서만 나쁘게 동작하는 코드와 틀린 공학 판단은 지나간다. 그래서 화면에 "자동 검사 통과"까지만 쓴다.

이 파일의 정규식은 올린 사람의 글자에 돌므로 모두 선형이어야 한다. re는 도는 동안 GIL을 쥐고 있어, 제곱 시간 정규식 하나면
일반 계정의 업로드 한 번이 서버 전체를 멈춘다. 그래서 `.*`로 두 단어 사이를 찾지 않고 함수로 나눠 찾는다. test_market_check의 시간 시험이 막는다.
"""
from __future__ import annotations

import ast
import codecs
import hashlib
import io
import json
import re
import struct
import sys
import unicodedata
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath

from cae_adapters.catalog import TOOLS

from .config import ROOT
from .study.recipe import _WIN_RESERVED, MAX_BUNDLE, RecipeRejected, check_bundle

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}\Z")
VERSION_RE = re.compile(r"^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}\Z")  # \d는 다른 문자의 숫자도 받아 1.0.0과 같은 값의 다른 버전이 생긴다
MAX_DESCRIPTION = 1024
SUMMARY_LEN = 100
MAX_PLUGIN_JSON = 64 * 1024
MAX_AST_FILE = 512 * 1024  # 기본 스킬의 가장 큰 스크립트가 18 KB다. 문법 분석은 도는 동안 GIL을 쥐므로(1 MB에 1.3초) 크기로 묶는다
MAX_AST_TOTAL = 2 * 1024 * 1024
MAX_HITS = 100
MAX_ITEM_PATH = 150  # PC에서 앞에 붙는 작업 폴더·보관소 경로를 100자 안팎으로 잡았다. Windows 한도는 260자다

# 걸러서 받지 않는 폴더. 브라우저와 scripts/market_publish.py도 보내기 전에 같은 목록으로 뺀다(Starlette는 파일 1000개에서 막힌다)
JUNK_PARTS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode"}
JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini", ".gitattributes", ".gitignore"}

PY_EXT = {".py", ".pyw"}
# 구조까지 확인하는 그림. 확장자만 보면 실행되는 파일이 "지침뿐인 스킬"로 들어온다
CHECKED_IMAGE_EXT = {".png", ".jpg", ".jpeg"}
IMAGE_EXT = CHECKED_IMAGE_EXT | {".gif", ".svg", ".webp", ".bmp"}
TEXT_ONLY_EXT = {".md", ".svg"} | CHECKED_IMAGE_EXT  # 이것만 있어야 "지침뿐인 스킬"이다. 나머지는 모두 코드 있는 항목으로 본다
# 받기는 하되 AST로 볼 수 없는 스크립트. 사람이 읽을 수밖에 없다는 것을 숨기지 않는다
OTHER_SCRIPT_EXT = {".ps1", ".psm1", ".psd1", ".bat", ".cmd", ".js", ".mjs", ".cjs", ".ts", ".vbs", ".vb", ".sh", ".bash", ".zsh", ".tcl", ".java", ".catscript", ".catvbs",
                    ".jou", ".lua", ".pl", ".rb", ".mac", ".cs", ".scr", ".cfile", ".proc", ".m", ".r", ".swb", ".swp", ".wbjn", ".ses", ".html", ".htm"}
# 글자로 읽어 검사하는 자료. 이 목록에 없는 확장자는 "자동 검사가 모르는 파일"로 표시한다: 아는 스크립트만 표시하면 모르는 실행 형식이 조용히 지나간다
DATA_EXT = {".txt", ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".rst", ".xml", ".log", ".tmpl",
            ".key", ".k", ".dyn", ".inp", ".dat", ".nas", ".bdf", ".fem", ".cdb", ".pc", ".mapdl", ".sif", ".gmsh", ".geo"}
# 사람이 읽을 수 없거나, 더블클릭·압축 풀기만으로 코드가 도는 형식. 표시로 넘기면 검토자가 확인할 방법이 없다
_MARKET_BLOCKED_EXT = {".pyz", ".pyzw", ".egg", ".whl", ".pth", ".reg", ".msc", ".url", ".inf", ".application", ".appref-ms", ".xll", ".xla", ".xlam", ".xlsm", ".xlsb", ".docm", ".dotm",
                       ".pptm", ".potm", ".ppam", ".chm", ".gadget", ".appx", ".msix", ".msixbundle", ".cab", ".iso", ".img", ".vhd", ".vhdx", ".ws", ".sct", ".wsc", ".ppkg", ".diagcab",
                       ".library-ms", ".settingcontent-ms", ".jnlp", ".deb", ".rpm", ".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
RULE_FILES = {"claude.md", "agents.md", "gemini.md"}
# 2단계 전에는 받지 않는 것. 표시 없이 받으면 설치 뒤에 아무 일도 하지 않아 사용자를 속인다
TOOL_ONLY = ("server", "bin", "hooks")
TOOL_ONLY_FILES = {"hooks.json", "mcp.json", ".mcp.json"}
# SKILL.md 머리말에서 남기는 값. allowed-tools·hooks 등은 AI 도구의 승인 창을 건너뛰거나 명령을 돌린다. 머리말을 서버가 새로 쓰므로 YAML 변형으로도 남지 않는다
KEEP_FRONT_KEYS = {"name", "description", "license"}
# PEP 263 선언으로 허용하는 인코딩. utf-7·unicode_escape는 사람과 검사기에는 주석으로 보이는 글자를 Python이 코드로 읽게 만든다
SAFE_CODECS = {"utf-8", "ascii", "cp949", "euc_kr", "iso8859-1", "cp1252"}

# requirements-agent.txt의 직접 의존성. pandas는 간접 의존성이라 PC마다 달라 뺐다
ALLOWED_THIRD_PARTY = {"httpx", "websockets", "mcp", "numpy", "scipy", "pyNastran", "lasso", "h5py", "matplotlib", "win32com", "win32api", "win32con",
                       "pythoncom", "pywintypes", "pystray", "PIL", "cae_adapters"}
# 툴 안의 파이썬에서만 있는 모듈 → 필요한 툴
MODULE_TOOL = {"NXOpen": "nx", "NXOpenUF": "nx", "abaqus": "abaqus", "abaqusConstants": "abaqus", "odbAccess": "abaqus", "part": "abaqus", "mesh": "abaqus",
               "ansa": "ansa", "ansys": "ansys", "inspire": "inspire", "cst": "cst"}
NETWORK_MODULES = {"socket", "ssl", "http", "urllib", "urllib2", "urllib3", "requests", "httpx", "ftplib", "smtplib", "telnetlib", "websockets", "aiohttp", "paramiko"}
SYSTEM_MODULES = {"ctypes", "winreg", "_winreg", "msvcrt"}
DYNAMIC_MODULES = {"importlib", "marshal", "pickle", "cPickle", "shelve", "dill", "imp", "runpy"}
FILE_MODULES = {"shutil", "pathlib", "glob", "tempfile", "zipfile", "tarfile"}
PROGRAM_MODULES = {"subprocess", "multiprocessing", "pty", "commands"}
DYNAMIC_CALLS = {"exec", "eval", "compile", "__import__", "execfile"}
PLATFORM_MODULES = {"_common", "agent", "dds_agent", "mcp_servers"}
_FILE_CALLS = {"remove", "unlink", "rename", "replace", "makedirs", "rmdir", "open"}
_RUN_OS_CALLS = {"system", "popen", "startfile", "posix_spawn", "posix_spawnp", "fork", "forkpty"}
_RUN_CALLS = {"pty.spawn", "commands.getoutput", "commands.getstatusoutput", "commands.getstatus", "webbrowser.open", "win32api.ShellExecute",
              "win32process.CreateProcess", "asyncio.create_subprocess_exec", "asyncio.create_subprocess_shell"}
_GETATTR_MODULES = {"os", "posix", "nt", "subprocess", "builtins", "__builtins__", "importlib", "ctypes"}

CAP_LABEL = {"runs_code": "내 PC에서 코드 실행", "files": "파일 읽기·쓰기", "starts_programs": "다른 프로그램 실행", "network": "네트워크 사용",
             "system": "Windows 시스템 기능 사용", "dynamic_code": "실행 중에 코드를 만들어 실행"}

RULE_TITLE = {
    "runs_programs": ("다른 프로그램을 실행합니다", "무엇을 실행하는지 읽어 보세요. 라이선스를 잡는 툴이면 CPU 수와 동시 실행 수도 확인하세요."),
    "network": ("네트워크를 씁니다", "어디로 무엇을 보내는지 읽어 보세요."),
    "system": ("Windows 시스템 기능을 씁니다", "ctypes·winreg로 무엇을 하는지 읽어 보세요."),
    "dynamic_code": ("실행 중에 코드를 만들어 실행합니다", "exec·eval·동적 import·pickle은 읽어서는 무엇이 도는지 알 수 없습니다. 꼭 필요한지 확인하세요."),
    "platform_internal": ("플랫폼 내부 코드를 불러옵니다", "Agent가 바뀌면 깨지고 토큰에 닿을 수 있습니다."),
    "tool_module": ("필요한 툴 내부 모듈이 있습니다", "그 툴이 있는 PC에서만 돕니다. 필요한 툴 칸과 맞는지 확인하세요."),
    "tool_python2": ("Python 2 문법입니다", "Abaqus 2023 이하처럼 툴 안에서 도는 스크립트면 정상입니다. 자동 검사가 문법 분석을 하지 못해 글자로만 찾았습니다."),
    "not_python": ("자동 검사가 읽지 못하는 파일이 있습니다", "자동 검사는 Python만 봅니다. Python 외 스크립트와 내용을 알 수 없는 파일은 직접 읽어야 합니다."),
    "runs_document": ("문서나 그림을 프로그램으로 실행하라는 문구가 있습니다", "확장자만 문서인 코드일 수 있습니다. 그 파일을 어떻게 쓰는지 읽어 보세요."),
    "shell_inject": ("AI가 읽는 순간 명령을 돌리는 문법이 있습니다", "!`명령` 문법은 승인 창 없이 돕니다. 설명으로 적은 것인지 확인하세요."),
    "detached_gui": ("띄워 두고 끝내는 프로그램이 있습니다", "AI 도구가 끝날 때 저장하지 않은 모델과 함께 꺼질 수 있습니다. CREATE_BREAKAWAY_FROM_JOB으로 떼어 내야 합니다."),
    "shadow_module": ("표준 모듈과 같은 이름의 파일이 있습니다", "다른 스크립트가 이 파일을 대신 불러올 수 있습니다."),
    "external_url": ("사내가 아닌 주소가 있습니다", "사내에서 닿지 않거나 외부로 내용이 나갈 수 있습니다."),
    "remote_fetch": ("원격에서 스크립트를 받아 실행하라는 문구가 있습니다", "검토한 내용과 다른 코드가 돌 수 있습니다."),
    "invisible_text": ("보이지 않는 글자가 있습니다", "사람은 못 보고 AI만 읽는 지시일 수 있습니다."),
    "big_base64": ("긴 인코딩 문자열이 있습니다", "읽을 수 없는 내용이 숨어 있을 수 있습니다."),
    "platform_secret": ("토큰·설정 파일을 언급합니다", ".dds-agent·agent.toml·DDS_TOKEN·~/.claude를 다루는 이유를 확인하세요."),
    "prompt_injection": ("AI의 지시를 바꾸려는 문구가 있습니다", "\"이전 지시 무시\" 같은 문구입니다. 설명인지 지시인지 확인하세요."),
    "similar_name": ("비슷한 이름의 항목이 있습니다", "기존 항목으로 착각하게 하려는 것은 아닌지 확인하세요."),
    "unknown_mcp_tool": ("없는 dds 도구를 언급합니다", "AI가 없는 도구를 찾다 멈춥니다."),
    "asks_user_click": ("사용자에게 조작을 부탁하는 문구가 있습니다", "Live Link는 스스로 연결합니다. 사용자에게 버튼·저널 실행을 부탁하면 안 됩니다."),
    "project_value": ("이 프로젝트 값이 남아 있습니다", "절대 경로·프로젝트 번호·케이스 번호·프로젝트 이름은 다른 PC에서 맞지 않고 기밀일 수 있습니다."),
}
LABEL_PATH = "(이름표)"  # 파일이 아니라 등록 양식의 값에서 찾은 표시

_URL = re.compile(r"\b(?:https?|ftps?|sftp|ssh|wss?|smb)://(?:[^\s/@]+@)?([A-Za-z0-9.\-]+)", re.I)  # user@ 뒤가 진짜 주소다
_IPV4 = r"(?:\.\d{1,3})"
# 앞만 맞춰 보면 10.evil.example.com이 사내로 지나간다. 주소 전체가 맞아야 한다
_INTERNAL_HOST = re.compile(rf"^(localhost|127{_IPV4}{{3}}|10{_IPV4}{{3}}|192\.168{_IPV4}{{2}}|172\.(1[6-9]|2\d|3[01]){_IPV4}{{2}}|[^.]+)\Z|\.(corp|local|internal|lan|intranet)\Z", re.I)
# 공개 문서 주소는 내용이 밖으로 나가지 않는다. 규격 문서에 적은 대로 몇 곳만 뺀다
_DOC_HOST = re.compile(r"(^|\.)((python|numpy|scipy)\.org|w3\.org|microsoft\.com|openfoam\.(com|org))\Z", re.I)
_FETCH_WORD = re.compile(r"\b(curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b", re.I)
_PIPE_RUN = re.compile(r"\s*(sh|bash|iex|invoke-expression|python)\b", re.I)
_INSTALL = re.compile(r"\b(pip3?|npm|uv)\s+install\b|\b(npx|uvx)\s+\S|@latest\b", re.I)
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]+")
_PLATFORM_SECRET = re.compile(r"\.dds-agent|agent\.toml|DDS_TOKEN|~[/\\]\.(claude|codex|gemini)\b|\.claude[/\\](credentials|\.credentials|settings)", re.I)
_INJECTION = re.compile(r"(이전|앞의|위의|기존)\s*(모든\s*)?(지시|지침|규칙|명령)[을를은는]?\s*(모두\s*)?(무시|따르지|잊)|ignore\s+(all\s+)?(the\s+)?(previous|prior|above)\s+(instructions|prompts?)|disregard\s+(all\s+)?(previous|prior)|시스템\s*프롬프트[를을]?\s*(출력|보여|알려)", re.I)
_ABS_PATH = re.compile(r"\b[A-Za-z]:[\\/][^\s\"'`<>|]{2,}|(?<![\w.])/(home|Users|mnt|data)/[A-Za-z0-9_.\-]+/")
_PROJECT_ID = re.compile(r"\bP\d{4}_|\bS\d{3}_[^\s]|\bC\d{4}_(doe|bo|al)\b")
# 사람이 눌러야 하는 조작을 안내하는 문구(agent/tests/test_live_autoconnect.py의 규칙). "사용자에게 …안내하세요"는 _asks_user가 따로 찾는다
_ASK_USER = re.compile(r"(눌러\s*(주세요|달라|주시)|클릭해\s*(주세요|달라|주시)|실행해\s*(주세요|달라|주시)|'DDS 실행'|리본에 등록|사용자에게 안내|사용자가 직접 열)")
_DDS_TOOL = re.compile(r"\bdds-(flow|cae|study|live)(?:\.|__)([a-z][a-z0-9_]+)")  # dds_live_nx.py 같은 파일 이름은 도구가 아니다
_PY2 = re.compile(r"^[ \t]*(print[ \t]+[\"'\w%(]|exec[ \t]+[\"'\w]|raise[ \t]+\w+[ \t]*,)|^[ \t]*except[ \t]+[\w.]+[ \t]*,[ \t]*\w+[ \t]*:|\b0[0-7]{2,}\b|`[^`\n]+`|\bur?[\"']", re.M)
_CODING = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")
_FRONT = re.compile(r"\A---[ \t]*\r?\n(.*?\r?\n)?---[ \t]*(?:\r?\n|\Z)", re.S)
_KEY_LINE = re.compile(r"^[\"']?([A-Za-z][\w-]*)[\"']?[ \t]*:(.*)$")  # "name": x와 name : x도 키다. 못 읽으면 같은 키가 둘 남는다
# CLI의 문법에 맞춘다. 공백이나 줄 처음 뒤의 !`는 명령이 여러 줄이어도 돌고, 울타리는 줄 처음이 아니어도 돈다(claude.exe의 정규식과 대조).
# 코드의 x!=`y`처럼 줄을 건너 우연히 맞는 것은 잡지 않는다. 한 줄 안의 !`명령`은 CLI가 달라져도 위험하므로 전처럼 어디서나 잡는다
_SHELL_INJECT = re.compile(r"!`[^`\n]+`|(?<!\S)!`[^`]+`|(?:```|~~~)!")
_INTERPRETER = re.compile(r"(?<![\w.-])(python[0-9.]*|pythonw|py|bash|sh|zsh|pwsh|powershell|cmd|node|ruby|perl|wscript|cscript|mshta|rundll32|regsvr32|certutil|"
                          r"copy|cp|mv|move|ren|rename|copy-item|move-item|rename-item)(?:\.exe)?(?![\w-])", re.I)
_SVG_SCRIPT = re.compile(r"<script|\son\w+\s*=|javascript:|<foreignObject", re.I)
_SEP_CHARS = re.compile("[\x0b\x1c\x1d\x1e\x85  ]")  # splitlines가 줄로 나눠 한 줄 안에서는 보이지 않는 글자
_SMUGGLE_VS = re.compile("[︎️]{2,}")  # 그림 글자 뒤의 FE0E·FE0F 하나는 정상이다. 여러 개 이어지면 글자를 숨긴 것이다
MANAGED_MARK = "DDS Conversa 규칙"  # 규칙 파일의 표시 구간 글자(agent/dds_agent/context.py). 설명에 들어가면 구간이 그 자리에서 끝난 것으로 읽힌다


class PackageRejected(RecipeRejected):
    """받을 수 없는 패키지. 문구에 이유와 할 일이 들어 있다."""


@dataclass
class CheckContext:
    base_skills: set[str] = field(default_factory=set)
    public_names: set[str] = field(default_factory=set)  # 이미 공개된 이름(자기 항목 제외)
    public_skills: set[str] = field(default_factory=set)  # 공개된 항목 안의 스킬 이름(자기 항목 제외)
    project_names: set[str] = field(default_factory=set)  # 올린 사람의 프로젝트 이름
    allow_reserved: bool = False  # 기본 스킬 자체를 시험할 때만
    item_no: int = 0  # 한글 이름만 있을 때 붙일 번호


@dataclass
class CheckResult:
    name: str
    kind: str
    files: list[tuple[str, bytes]]
    skills: list[dict]
    manifest: dict
    capabilities: list[str]
    flags: list[dict]
    fixes: list[str]
    warnings: list[str]
    version: str | None = None

    @property
    def report(self) -> dict:
        return {"passed": True, "flags": self.flags, "fixes": self.fixes, "warnings": self.warnings,
                "other_scripts": [h["path"] for f in self.flags if f["rule"] == "not_python" for h in f["hits"]]}


def base_skill_names() -> set[str]:
    d = ROOT / "agent-context" / "skills"
    return {p.name for p in d.iterdir() if p.is_dir()} if d.is_dir() else set()


def is_junk(name: str) -> bool:
    parts = name.replace("\\", "/").split("/")
    return any(p in JUNK_PARTS for p in parts[:-1]) or parts[-1].lower() in JUNK_FILES


def pack_files(files: list[tuple[str, bytes]]) -> bytes:
    """폴더로 올린 파일들을 메모리에서 zip으로 묶는다. 이후 검사는 zip 업로드와 같은 길을 지난다."""
    if sum(len(b) for _, b in files) > MAX_BUNDLE * 3:
        raise PackageRejected(f"폴더가 너무 큽니다 → 스크립트와 문서만 올려 주세요. 압축한 크기가 {MAX_BUNDLE // (1024 * 1024)}MB 안이어야 합니다")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, body in files:
            zf.writestr(path.replace("\\", "/"), body)
    return buf.getvalue()


def build_zip(files: list[tuple[str, bytes]]) -> bytes:
    """검사를 통과한 파일로 zip을 새로 쓴다. 같은 내용이면 같은 해시가 되게 순서와 시각을 고정한다."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, body in sorted(files):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, body)
    return buf.getvalue()


def file_rows(files: list[tuple[str, bytes]]) -> list[dict]:
    return [{"path": p, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for p, b in sorted(files)]


# ---------- 글자 ----------
def _to_utf8(path: str, body: bytes, fixes: list[str]) -> str:
    """문서를 UTF-8로 맞춘다. CP949 SKILL.md 하나가 그 프로젝트의 새 세션을 막은 적이 있어 서버에서 고친다."""
    for enc in ("utf-8-sig", "utf-16") if body[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    try:
        text = body.decode("cp949")
    except UnicodeDecodeError:
        raise PackageRejected(f"글자 인코딩을 알 수 없습니다: {path} → UTF-8로 저장해서 다시 올려 주세요") from None
    fixes.append(f"{path}: 글자 인코딩을 UTF-8로 바꿨습니다")
    return text


def _decode_any(body: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", "replace")


@lru_cache(maxsize=4096)
def _is_hidden_char(c: str) -> bool:
    """사람 눈에 보이지 않지만 AI에게는 읽히는 글자. 탭·줄바꿈과 BOM은 정상이고, FE0E·FE0F는 이어질 때만 _SMUGGLE_VS가 본다."""
    if c in "\t\n\r\f﻿︎️":
        return False
    if unicodedata.category(c) in ("Cf", "Cc", "Co", "Cs"):  # 폭 없는 글자·방향 제어·제어 문자·사용자 정의 영역
        return True
    o = ord(c)
    return o in (0x034F, 0x115F, 0x1160, 0x17B4, 0x17B5, 0x3164, 0xFFA0) or 0x180B <= o <= 0x180F or 0xFE00 <= o <= 0xFE0D or 0xE0100 <= o <= 0xE01EF


def has_hidden(text: str) -> bool:
    return any(_is_hidden_char(c) for c in set(text)) or _SMUGGLE_VS.search(text) is not None


def clean_label(text) -> str:
    """이름표는 카드·AI 안내·규칙 파일로 간다. 한 줄로 만들고 보이지 않는 글자와 표시 구간 흉내를 지운다."""
    s = "".join(c for c in str(text or "") if c in "\t\n\r" or not _is_hidden_char(c))
    while "<!--" in s or "-->" in s:  # 지우고 나서 다시 표시가 만들어지지 않게 반복한다
        s = s.replace("<!--", "").replace("-->", "")
    return " ".join(s.split())


# ---------- SKILL.md 머리말 ----------
def _front_lines(text: str) -> tuple[list[tuple[str, str]] | None, str]:
    """머리말을 (키, 값)으로 읽는다. YAML 전체가 아니라 CLI가 읽는 만큼만 본다: 키 한 줄 + 들여 쓴 이어지는 줄."""
    m = _FRONT.match(text)
    if not m:
        return None, text
    out: list[tuple[str, str]] = []
    for line in (m.group(1) or "").splitlines():
        k = _KEY_LINE.match(line)
        if k and not line[0].isspace():
            out.append((k.group(1), k.group(2).strip()))
        elif out and line.strip() and line[0].isspace():
            out[-1] = (out[-1][0], (out[-1][1] + "\n" + line.strip()).strip())
        elif line.strip():  # {hooks: …}, ? hooks 같은 YAML 변형. 설명에 붙이지 않고 알 수 없는 설정으로 빼낸다
            out.append(("기타 설정", ""))
    return out, text[m.end():]


def _first_keys(front: list[tuple[str, str]] | None) -> dict[str, str]:
    """같은 키가 여럿이면 앞의 것을 쓴다. 뒤에 몰래 적은 name으로 이름을 바꾸지 못하게 한다."""
    keys: dict[str, str] = {}
    for k, v in front or []:
        keys.setdefault(k.lower(), v)
    return keys


def _one_line(value: str) -> str:
    v = re.sub(r"^[|>][+-]?\s*", "", value.strip())
    v = " ".join(p.strip() for p in v.split("\n") if p.strip())  # \s*\n\s* 정규식은 긴 공백에서 제곱 시간이 된다
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].strip()
    return v


def _first_paragraph(body: str) -> str:
    for para in re.split(r"\n\s*\n", body):
        p = para.strip()
        if p and not p.startswith(("#", "```", "|", "<!--")):
            return _one_line(p)
    return ""


def summary_of(description: str, limit: int = SUMMARY_LEN) -> str:
    """규칙 파일과 카드에 쓰는 짧은 설명. 등록자의 글이 모든 AI의 지침에 그대로 들어가지 않게 한다."""
    clean = clean_label(description)
    first = re.split(r"(?<=[.!?。])\s", clean, maxsplit=1)[0]
    return first if len(first) <= limit else first[:limit - 1].rstrip() + "…"


def _yaml_line(value: str) -> str:
    """콜론이 든 설명을 그대로 쓰면 CLI의 YAML 읽기가 실패해 스킬을 못 찾는다."""
    if re.search(r":\s|:\Z|^[\[\]{}&*!|>'\"%@`#?-]|\s#", value):
        return json.dumps(value, ensure_ascii=False)
    return value


def _build_front(skill: str, desc: str, keys: dict[str, str]) -> str:
    """머리말을 새로 쓴다. 고칠 줄만 바꾸면 "hooks": 나 {hooks: …} 같은 YAML 변형이 그대로 남아 CLI가 읽는다."""
    lines = [f"name: {skill}", f"description: {_yaml_line(desc)}"]
    lic = _one_line(keys.get("license") or "")
    if lic:
        lines.append(f"license: {_yaml_line(lic)}")
    return "\n".join(lines) + "\n"


def _fix_skill_md(path: str, body: bytes, skill: str, fixes: list[str]) -> tuple[bytes, str]:
    text = _to_utf8(path, body, fixes).replace("\r\n", "\n")
    front, rest = _front_lines(text)
    keys: dict[str, str] = {}
    if front is None:
        desc = _first_paragraph(rest)
        if not desc:
            raise PackageRejected(f"설명을 찾지 못했습니다: {path} → 파일 첫머리에 description: 한 줄 설명을 적어 주세요")
        fixes.append(f"{path}: 머리말이 없어 첫 문단을 설명으로 넣었습니다")
    else:
        keys = _first_keys(front)
        if "description" not in keys:
            desc = _first_paragraph(rest)
            if not desc:
                raise PackageRejected(f"설명을 찾지 못했습니다: {path} → 머리말에 description: 한 줄 설명을 적어 주세요")
            fixes.append(f"{path}: 설명이 없어 첫 문단을 설명으로 넣었습니다")
        else:
            desc = _one_line(keys["description"])
            if "\n" in keys["description"] or keys["description"].startswith(("|", ">")):
                fixes.append(f"{path}: 여러 줄 설명을 한 줄로 고쳤습니다")
        if keys.get("name") and _one_line(keys["name"]) != skill:
            fixes.append(f"{path}: 이름을 폴더 이름에 맞춰 '{skill}'(으)로 고쳤습니다")
        dropped = sorted({k for k, _ in front if k.lower() not in KEEP_FRONT_KEYS})
        if dropped:
            fixes.append(f"{path}: 머리말의 {', '.join(d[:40] for d in dropped[:8])}은(는) 뺐습니다. AI 도구의 승인 창을 건너뛰거나 명령을 돌리는 설정을 마켓 항목에서는 쓰지 않습니다")
    if not desc:
        raise PackageRejected(f"설명이 비어 있습니다: {path} → description: 에 한 줄 설명을 적어 주세요")
    if len(desc) > MAX_DESCRIPTION:
        raise PackageRejected(f"설명이 {MAX_DESCRIPTION}자를 넘습니다({len(desc)}자): {path} → 언제 쓰는 스킬인지 한 줄로 줄여 주세요")
    if _SHELL_INJECT.search(rest):
        raise PackageRejected(f"!`명령` 문법이 있습니다: {path} → 이 문법은 AI가 읽는 순간 승인 창 없이 명령을 돌립니다. 명령은 scripts/의 스크립트로 옮기거나, 설명이면 ! 와 ` 사이를 띄워 주세요")
    new = "---\n" + _build_front(skill, desc, keys) + "---\n" + rest.lstrip("\n")
    return new.encode("utf-8"), desc


# ---------- 코드 ----------
def _module_top(name: str) -> str:
    return name.split(".")[0]


def _detached_popens(tree: ast.AST) -> list[int]:
    """기다리지 않고 띄워 두는 Popen 중 CLI의 Job에서 떼어 내지 않은 줄. agent/tests/test_hidden_windows.py의 규칙을 서버로 옮겼다."""
    bad = []
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))] + [tree]:
        calls = [n for n in ast.walk(func) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "Popen" and getattr(n.func.value, "id", "") == "subprocess"]
        waited = {n.value.id for n in ast.walk(func) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                  and n.attr in ("wait", "communicate", "poll")}
        assigned = {id(n.value): n.targets[0].id for n in ast.walk(func) if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
        breakaway = any(isinstance(n, ast.Constant) and n.value == "CREATE_BREAKAWAY_FROM_JOB" for n in ast.walk(func)) or \
            any(isinstance(n, ast.Attribute) and n.attr == "CREATE_BREAKAWAY_FROM_JOB" for n in ast.walk(func))
        for call in calls:
            if assigned.get(id(call)) not in waited and not breakaway:
                bad.append(call.lineno)
    return sorted(set(bad))


class _Scan:
    def __init__(self):
        self.hits: dict[str, list[dict]] = {}
        self.total: dict[str, int] = {}
        self.caps: set[str] = set()
        self.tools: set[str] = set()
        self.hosts: set[str] = set()  # 같은 주소를 여러 번 적어 진짜 주소를 목록 밖으로 밀어내지 못하게 한 번만 센다
        self.doc_names: set[str] = set()
        self.ast_left = MAX_AST_TOTAL

    def flag(self, rule: str, path: str, line: int | None, text: str):
        rows = self.hits.setdefault(rule, [])
        self.total[rule] = self.total.get(rule, 0) + 1
        if len(rows) < MAX_HITS:
            rows.append({"path": path, "line": line, "text": text[:200]})

    def flags(self) -> list[dict]:
        return [{"rule": r, "title": RULE_TITLE[r][0], "why": RULE_TITLE[r][1], "hits": hits, "total": self.total[r], "truncated": self.total[r] > len(hits)}
                for r, hits in sorted(self.hits.items())]


def _python_source(path: str, body: bytes) -> str:
    """Python이 읽는 대로 글자로 바꾼다. str을 ast.parse에 넘기면 인코딩 선언을 무시하므로, 선언대로 풀어 넘겨야 검사기와 Python이 같은 글자를 본다."""
    declared = None
    for line in re.split(rb"\r\n|\r|\n", body[:4096])[:2]:
        m = _CODING.match(line)
        if m:
            declared = m.group(1).decode("ascii", "replace")
            try:
                name = codecs.lookup(declared).name
            except LookupError:
                name = ""
            if name not in SAFE_CODECS:
                raise PackageRejected(f"글자 인코딩 선언을 받을 수 없습니다: {path} ({declared[:40]}) → 사람이 읽는 글자와 Python이 읽는 글자가 달라질 수 있습니다. UTF-8로 저장하고 선언을 지워 주세요")
            declared = name
            break
    if declared in (None, "utf-8", "ascii"):
        return _decode_any(body)
    try:
        return body.decode(declared)
    except UnicodeDecodeError:
        raise PackageRejected(f"글자 인코딩 선언과 내용이 다릅니다: {path} ({declared}) → UTF-8로 저장하고 선언을 지워 주세요") from None


def _parse(text: str):
    try:
        return ast.parse(text), None
    except (SyntaxError, ValueError, RecursionError, MemoryError) as e:  # 깊은 중첩은 RecursionError·MemoryError로 끝난다
        return None, e


def _spend_ast(size: int, scan: _Scan) -> bool:
    """문법 분석 한도를 쓴다. 넘으면 분석하지 않는다."""
    if size > MAX_AST_FILE or size > scan.ast_left:
        return False
    scan.ast_left -= size
    return True


def _dotted(node) -> str | None:
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _aliases(tree: ast.AST) -> tuple[dict[str, str], set[str]]:
    """이 파일 안에서 이름이 실제로 무엇을 가리키는지. os.system만 찾으면 from os import system과 import os as o가 빠져나간다."""
    names: dict[str, str] = {}
    star: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                if a.asname:
                    names[a.asname] = a.name
                else:
                    top = _module_top(a.name)
                    names[top] = top
        elif isinstance(n, ast.ImportFrom) and not n.level and n.module:
            for a in n.names:
                if a.name == "*":
                    star.add(n.module)
                else:
                    names[a.asname or a.name] = f"{n.module}.{a.name}"
    # r = os.system 같은 한 번 건너 부르기. 두 번 돌면 충분하다: 더 꼬면 사람이 읽을 때 드러난다
    for _ in range(2):
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                got = _dotted(n.value)
                if got and got.partition(".")[0] in names:
                    names[n.targets[0].id] = _resolve(got, names)
    return names, star


def _resolve(dotted: str, names: dict[str, str]) -> str:
    head, _, rest = dotted.partition(".")
    target = names.get(head)
    if not target:
        return dotted
    return f"{target}.{rest}" if rest else target


def _runs_program(q: str) -> bool:
    mod, _, fn = q.rpartition(".")
    if mod in ("os", "posix", "nt") and (fn in _RUN_OS_CALLS or fn.startswith(("spawn", "exec"))):
        return True
    return q in _RUN_CALLS


def _scan_python(path: str, text: str, siblings: set[str], scan: _Scan):
    scan.caps.add("runs_code")
    stem = PurePosixPath(path).stem
    if stem in ("sitecustomize", "usercustomize") or stem in sys.stdlib_module_names or stem in ALLOWED_THIRD_PARTY - {"cae_adapters"}:
        scan.flag("shadow_module", path, None, f"{stem}.py")
    if not _spend_ast(len(text), scan):
        raise PackageRejected(f"Python 코드가 너무 큽니다: {path} → 자동 검사가 읽을 수 있게 파일당 {MAX_AST_FILE // 1024}KB, 항목 전체 {MAX_AST_TOTAL // (1024 * 1024)}MB 안으로 줄여 주세요")
    tree, err = _parse(text)
    if tree is None:
        if not _PY2.search(text):
            line = getattr(err, "lineno", None)
            raise PackageRejected(f"Python 구문 오류: {path}" + (f" {line}번째 줄" if line else "") + " → 실행되는 코드만 올려 주세요. "
                                  "툴 안에서 도는 Python 2 스크립트라면 print 문처럼 Python 2임을 알 수 있어야 합니다")
        scan.flag("tool_python2", path, getattr(err, "lineno", None), "Python 3 문법으로 읽지 못했습니다")
        _scan_python_text(path, text, scan)
        return
    names, star = _aliases(tree)
    for n in ast.walk(tree):
        mods: list[str] = []
        if isinstance(n, ast.Import):
            mods = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and not n.level and n.module:
            mods = [n.module]
            for a in n.names:  # 부르지 않고 넘겨주기만 해도 실행할 수 있다
                if _runs_program(f"{n.module}.{a.name}"):
                    scan.flag("runs_programs", path, n.lineno, f"from {n.module} import {a.name}")
                    scan.caps.add("starts_programs")
        for mod in mods:
            _classify_import(path, n.lineno, mod, siblings, scan)
        if isinstance(n, ast.Call):
            _scan_call(path, n, names, star, scan)
    for line in _detached_popens(tree):
        scan.flag("detached_gui", path, line, "subprocess.Popen")


def _scan_call(path: str, n: ast.Call, names: dict[str, str], star: set[str], scan: _Scan):
    raw = _dotted(n.func)
    if raw is None:
        return
    q = _resolve(raw, names)
    if "." not in q:
        for mod in star:  # from os import *
            if _runs_program(f"{mod}.{q}"):
                q = f"{mod}.{q}"
                break
    top, _, fn = q.rpartition(".")
    fn = fn or q
    if fn in DYNAMIC_CALLS and top in ("", "builtins", "__builtin__", "__builtins__"):
        scan.flag("dynamic_code", path, n.lineno, f"{raw}(…)")
        scan.caps.add("dynamic_code")
    elif _runs_program(q):
        scan.flag("runs_programs", path, n.lineno, f"{q}(…)")
        scan.caps.add("starts_programs")
    elif q == "getattr" and n.args:
        target = _dotted(n.args[0])
        if target and _module_top(_resolve(target, names)) in _GETATTR_MODULES:
            scan.flag("dynamic_code", path, n.lineno, f"getattr({target}, …)")
            scan.caps.add("dynamic_code")
    elif q in ("open", "builtins.open", "io.open", "codecs.open") or (top == "os" and fn in _FILE_CALLS):
        scan.caps.add("files")


def _classify_import(path: str, line: int, mod: str, siblings: set[str], scan: _Scan):
    top = _module_top(mod)
    if top in PROGRAM_MODULES:
        scan.flag("runs_programs", path, line, f"import {mod}")
        scan.caps.add("starts_programs")
    if top in NETWORK_MODULES:
        scan.flag("network", path, line, f"import {mod}")
        scan.caps.add("network")
    if top in SYSTEM_MODULES:
        scan.flag("system", path, line, f"import {mod}")
        scan.caps.add("system")
    if top in DYNAMIC_MODULES:
        scan.flag("dynamic_code", path, line, f"import {mod}")
        scan.caps.add("dynamic_code")
    if top in FILE_MODULES:
        scan.caps.add("files")
    if top in PLATFORM_MODULES and top != "agent" or mod.startswith("agent.dds_agent"):
        scan.flag("platform_internal", path, line, f"import {mod}")
    if top in siblings or top in sys.stdlib_module_names or top in ALLOWED_THIRD_PARTY or top in PLATFORM_MODULES:
        return
    # 같은 항목 안의 모듈이 아니고 표준·허용 모듈도 아니면 툴 내장 모듈로 본다(A1). 거절하면 기존 스킬 절반이 막힌다
    scan.flag("tool_module", path, line, f"import {mod}")
    if top in MODULE_TOOL:
        scan.tools.add(MODULE_TOOL[top])


_RE_IMPORT = re.compile(r"^[ \t]*(?:from[ \t]+([\w.]+)[ \t]+import|import[ \t]+([\w., ]+))")
_RE_PY_DYNAMIC = re.compile(r"\b(exec|eval|__import__|execfile)\s*\(")
_RE_PY_RUN = re.compile(r"\bos\.(system|popen|startfile|spawn\w*|exec\w*)\s*\(|\bfrom\s+os\s+import\b[^#\n]*\b(system|popen|startfile|spawn\w*|exec\w*)\b")
_RE_PY_OPEN = re.compile(r"\bopen\s*\(")


def _scan_python_text(path: str, text: str, scan: _Scan):
    """AST로 읽지 못하는 Python 2 스크립트는 글자로 찾는다. 놓칠 수 있으니 검토자에게 그렇게 알린다."""
    for i, row in enumerate(text.splitlines(), 1):
        m = _RE_IMPORT.match(row)
        if m:
            names = [m.group(1)] if m.group(1) else [x.strip().split(" ")[0] for x in m.group(2).split(",")]
            for mod in names:
                if mod:
                    _classify_import(path, i, mod, set(), scan)
        if _RE_PY_DYNAMIC.search(row):
            scan.flag("dynamic_code", path, i, row.strip())
            scan.caps.add("dynamic_code")
        if _RE_PY_RUN.search(row):
            scan.flag("runs_programs", path, i, row.strip())
            scan.caps.add("starts_programs")
        if _RE_PY_OPEN.search(row):
            scan.caps.add("files")


def _looks_like_code(text: str) -> bool:
    """문서로 적힌 파일이 사실 Python인가. 확장자가 .md여도 python x.md로 돈다. 설정 파일의 a = 1은 코드로 치지 않는다."""
    tree, _ = _parse(text)
    if tree is None:
        return False
    for stmt in tree.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.For, ast.While, ast.With, ast.Try)):
            return True
        if isinstance(stmt, (ast.Expr, ast.Assign)) and any(isinstance(x, ast.Call) for x in ast.walk(stmt)):
            return True
    return False


def _check_image(path: str, ext: str, body: bytes):
    """그림은 구조까지 본다. 확장자만 믿으면 assets/logo.png라는 이름의 Python zipapp이 "지침뿐인 스킬"로 들어와 확인 창 없이 설치된다."""
    def bad(why: str):
        return PackageRejected(f"그림 파일이 아닙니다: {path} ({why}) → 실제 그림을 넣어 주세요. 스크립트라면 scripts/에 .py로 넣어 주세요. 코드 있는 항목으로 검토합니다")

    if b"PK\x05\x06" in body[-65557:]:  # Python은 파일 끝쪽의 zip 표시만 보고 실행한다(zipapp). 끝쪽만 봐야 진짜 그림이 우연히 걸리지 않는다
        raise bad("압축 데이터가 붙어 있습니다")
    if ext == ".png":
        if body[:8] != b"\x89PNG\r\n\x1a\n":
            raise bad("PNG 머리글이 없습니다")
        pos, n = 8, len(body)
        while True:
            if pos + 12 > n:
                raise bad("끝 표시가 없습니다")
            size = struct.unpack(">I", body[pos:pos + 4])[0]
            kind = body[pos + 4:pos + 8]
            pos += 12 + size
            if kind == b"IEND":
                break
        if pos != n:
            raise bad("그림 뒤에 다른 데이터가 붙어 있습니다")
    elif not (body[:3] == b"\xff\xd8\xff" and body[-2:] == b"\xff\xd9"):
        raise bad("JPEG 구조가 아닙니다")


def _asks_user(line: str) -> bool:
    if _ASK_USER.search(line):
        return True
    i = line.find("사용자에게 ")
    return i >= 0 and line.find("안내하세요", i) >= 0


def _remote_fetch(line: str) -> bool:
    if _INSTALL.search(line):
        return True
    if "|" not in line or not _FETCH_WORD.search(line):
        return False
    parts = line.split("|")
    return any(_PIPE_RUN.match(b) and _FETCH_WORD.search(a) for a, b in zip(parts, parts[1:]))


def _known_mcp_tools() -> dict[str, set[str]]:
    """저장소의 dds-* MCP 도구 이름. 실행하지 않고 AST로 @mcp.tool 데코레이터만 읽는다."""
    out: dict[str, set[str]] = {}
    for f in (ROOT / "mcp_servers").glob("dds_*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        names = set()
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and any("tool" in ast.dump(d) for d in n.decorator_list):
                names.add(n.name)
        out[f.stem[4:]] = names
    return out


_mcp_cache: dict[str, set[str]] | None = None


def _mcp_tools() -> dict[str, set[str]]:
    global _mcp_cache
    if _mcp_cache is None:
        _mcp_cache = _known_mcp_tools()
    return _mcp_cache


def _distance(a: str, b: str, limit: int = 3) -> int:
    if abs(len(a) - len(b)) >= limit:
        return limit
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _scan_text(path: str, text: str, ctx: CheckContext, scan: _Scan):
    names = {n for n in ctx.project_names if len(n) >= 4}
    # 파일 전체를 보는 판단은 줄마다 하지 않는다. 줄 수 × 파일 크기가 된다
    live = "Live" in text or "live" in path.lower()
    if _SEP_CHARS.search(text):
        scan.flag("invisible_text", path, None, "줄을 나누는 보이지 않는 글자")
    for i, line in enumerate(text.splitlines(), 1):
        for m in _URL.finditer(line):
            host = m.group(1).lower()
            if host not in scan.hosts and not _INTERNAL_HOST.search(host) and not _DOC_HOST.search(host):
                scan.hosts.add(host)
                scan.flag("external_url", path, i, m.group(0))
        if _remote_fetch(line):
            scan.flag("remote_fetch", path, i, line.strip())
        if has_hidden(line):
            scan.flag("invisible_text", path, i, "보이지 않는 글자가 있는 줄")
        if any(len(m.group()) >= 400 for m in _BASE64_RUN.finditer(line)):
            scan.flag("big_base64", path, i, "긴 인코딩 문자열")
        if _PLATFORM_SECRET.search(line):
            scan.flag("platform_secret", path, i, line.strip())
        if _INJECTION.search(line):
            scan.flag("prompt_injection", path, i, line.strip())
        if live and _asks_user(line):
            scan.flag("asks_user_click", path, i, line.strip())
        if _ABS_PATH.search(line) or _PROJECT_ID.search(line):
            scan.flag("project_value", path, i, line.strip())
        elif names and any(n in line for n in names):
            scan.flag("project_value", path, i, line.strip())
        for m in _DDS_TOOL.finditer(line):
            server, tool = m.group(1), m.group(2)
            known = _mcp_tools().get(server)
            if known is not None and tool not in known:
                scan.flag("unknown_mcp_tool", path, i, m.group(0))
        if scan.doc_names:
            run = _INTERPRETER.search(line)
            if run and any(name in line[run.end():] for name in scan.doc_names):
                scan.flag("runs_document", path, i, line.strip())


def label_flags(labels: dict, ctx: CheckContext | None = None) -> list[dict]:
    """등록 양식과 plugin.json의 값도 AI 안내·카드로 가므로 파일과 똑같이 검사한다. 지우기 전의 글자로 본다."""
    ctx = ctx or CheckContext()
    scan = _Scan()
    _scan_labels(labels, ctx, scan)
    return scan.flags()


def _scan_labels(labels: dict, ctx: CheckContext, scan: _Scan):
    rows = []
    for key in ("displayName", "display_name", "description", "category"):
        if labels.get(key):
            rows.append(str(labels[key]))
    if isinstance(labels.get("examples"), list):
        rows += [str(x) for x in labels["examples"]]
    if rows:
        _scan_text(LABEL_PATH, "\n".join(rows), ctx, scan)


def merge_label_flags(report: dict, flags: list[dict]) -> dict:
    """이름표에서 찾은 표시만 새 값으로 바꾼다. 파일에서 찾은 표시는 그대로 둔다."""
    rows: dict[str, dict] = {}
    for f in report.get("flags") or []:
        hits = [h for h in f["hits"] if h.get("path") != LABEL_PATH]
        if hits:
            rows[f["rule"]] = {**f, "hits": hits, "total": len(hits) if not f.get("truncated") else f.get("total", len(hits))}
    for f in flags:
        cur = rows.get(f["rule"])
        if cur:
            rows[f["rule"]] = {**cur, "hits": [*cur["hits"], *f["hits"]], "total": cur.get("total", len(cur["hits"])) + len(f["hits"])}
        else:
            rows[f["rule"]] = f
    return {**report, "flags": [rows[r] for r in sorted(rows)]}


def _infer_tools(texts: list[str]) -> set[str]:
    """이름·설명에 나온 툴. 추천과 "이 PC에 있음" 표시에만 쓰는 어림값이고 등록 화면에서 고칠 수 있다."""
    found = set()
    joined = " ".join(texts).lower()
    for t in TOOLS:
        if t["id"] == "mock":
            continue
        words = {t["id"].lower(), t["id"].lower().replace("_", " "), t["name"].lower()}
        if any(re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", joined) for w in words):
            found.add(t["id"])
    return found


def _skill_of(tool_id: str) -> str | None:
    return next((t.get("skill") for t in TOOLS if t["id"] == tool_id), None)


# ---------- 전체 ----------
def _slug(raw: str) -> str:
    s = re.sub(r"[\s_]+", "-", raw.strip().lower()[:200])
    s = re.sub(r"[^a-z0-9-]", "", s).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:40].strip("-")
    return s


def _reserved(name: str, ctx: CheckContext) -> str | None:
    if _WIN_RESERVED.match(name):  # 이름이 PC에서 skills/<이름>/ 경로가 된다. 받아 두면 모든 설치가 영원히 실패한다
        return f"이름 '{name}'은(는) Windows에서 폴더 이름으로 쓸 수 없습니다 → CON·NUL·COM1 같은 이름을 다른 이름으로 바꿔 주세요"
    if ctx.allow_reserved:
        return None
    if name.startswith(("dds-", "dds_", "claude-", "anthropic-")) or name in ("dds", "claude", "anthropic"):
        return f"이름 '{name}'은(는) 쓸 수 없습니다 → dds-·claude-·anthropic-으로 시작하지 않는 이름으로 바꿔 주세요"
    if name in ctx.base_skills:
        return f"'{name}'은(는) 기본 스킬 이름입니다 → 다른 이름으로 바꿔 주세요. 기본 스킬을 대신하는 등록은 아직 받지 않습니다"
    return None


def _strip_top(files: list[tuple[str, bytes]]) -> tuple[list[tuple[str, bytes]], str]:
    tops = {p.split("/")[0] for p, _ in files}
    if len(tops) == 1 and all("/" in p for p, _ in files) and tops != {"skills"}:
        top = tops.pop()
        return [(p.split("/", 1)[1], b) for p, b in files], top
    return files, ""


def _read_plugin(body: bytes) -> dict:
    if len(body) > MAX_PLUGIN_JSON:
        raise PackageRejected(f"plugin.json이 {MAX_PLUGIN_JSON // 1024}KB를 넘습니다 → 이름표만 남겨 주세요. 없어도 서버가 만듭니다")
    try:
        plugin = json.loads(_decode_any(body))
    except (ValueError, RecursionError):
        plugin = None
    if not isinstance(plugin, dict):
        raise PackageRejected("plugin.json을 읽지 못했습니다 → JSON 형식을 고치거나 파일을 빼 주세요. 없어도 서버가 만듭니다")
    return plugin


def _layout(files: list[tuple[str, bytes]]) -> tuple[dict[str, list[tuple[str, bytes]]], dict, bool]:
    """파일을 스킬별로 나눈다. (스킬 폴더 → 파일, plugin.json, 최상위 SKILL.md 형식인가)."""
    plugin: dict = {}
    rest = []
    for p, b in files:
        low = p.lower()
        parts = p.split("/")
        if has_hidden(p):  # 검토 화면에서 이름이 뒤집혀 보이거나 다른 파일처럼 보인다
            raise PackageRejected(f"파일 이름에 보이지 않는 글자가 있습니다: {clean_label(p)[:80]} → 이름을 다시 입력해 주세요")
        if low == "plugin.json":
            plugin = _read_plugin(b)
            continue
        if parts[0].lower() in TOOL_ONLY or low in TOOL_ONLY_FILES or parts[0] == ".mcp":
            raise PackageRejected(f"아직 받지 않는 형식입니다: {p} → 지금은 스킬만 올릴 수 있습니다. 기능(MCP)·실행 파일·hooks는 빼고 다시 올려 주세요")
        if any(x.startswith(".") for x in parts):
            raise PackageRejected(f"점으로 시작하는 파일은 넣을 수 없습니다: {p} → 숨은 설정이 PC로 복사되지 않게 빼 주세요")
        if parts[-1].lower() in RULE_FILES:
            raise PackageRejected(f"작업 규칙 파일은 넣을 수 없습니다: {p} → 지침은 SKILL.md에 적어 주세요. 규칙 파일은 프로젝트 전체의 AI 지침을 바꿉니다")
        if PurePosixPath(low).suffix in _MARKET_BLOCKED_EXT:
            raise PackageRejected(f"받지 않는 파일 형식입니다: {p} → 사람이 읽어 확인할 수 없거나 열기만 해도 실행됩니다. 스크립트나 문서로 바꿔 넣어 주세요")
        rest.append((p, b))
    single = any(p.lower() == "skill.md" for p, _ in rest)
    multi = any(len(x) == 3 and x[0] == "skills" and x[2].lower() == "skill.md" for x in (p.split("/") for p, _ in rest))
    if single and multi:
        raise PackageRejected("최상위 SKILL.md와 skills/ 폴더가 함께 있습니다 → 한 가지 형식만 써 주세요")
    if single:
        return {"": rest}, plugin, True
    if not multi:
        raise PackageRejected("SKILL.md를 찾지 못했습니다 → SKILL.md는 AI에게 주는 작업 설명서입니다. 등록 화면의 시작 틀을 받아 폴더 맨 위에 두거나, 여러 개면 skills/<이름>/SKILL.md로 올려 주세요")
    groups: dict[str, list[tuple[str, bytes]]] = {}
    for p, b in rest:
        parts = p.split("/")
        if parts[0] != "skills" or len(parts) < 3:
            if parts[-1].lower().startswith(("readme", "license")) and len(parts) == 1:
                continue  # 설명 파일은 PC로 보내지 않는다
            raise PackageRejected(f"skills/ 밖의 파일은 받지 않습니다: {p} → skills/<이름>/ 안으로 옮기거나 빼 주세요")
        groups.setdefault(parts[1], []).append(("/".join(parts[2:]), b))
    for skill, rows in groups.items():
        if not any(p.lower() == "skill.md" for p, _ in rows):
            raise PackageRejected(f"skills/{skill}/SKILL.md가 없습니다 → 스킬마다 SKILL.md가 있어야 합니다")
    return groups, plugin, False


def _pick_name(plugin: dict, single_front: str, top: str, hint: str, ctx: CheckContext, fixes: list[str]) -> str:
    for raw, label in ((plugin.get("name"), "plugin.json"), (single_front, "SKILL.md"), (top, "폴더"), (hint, "파일")):
        if not raw or not isinstance(raw, str):
            continue
        slug = _slug(raw)
        if slug and not slug[0].isdigit():
            if slug != raw:
                fixes.append(f"{label}의 이름 '{clean_label(raw)[:60]}'을(를) 규격에 맞춰 '{slug}'(으)로 고쳤습니다")
            return slug
    # 한글 이름뿐이면 번호를 붙인다. 화면에 보이는 이름은 한글 그대로 둔다
    name = f"item-{ctx.item_no or 1}"
    fixes.append(f"영문 이름을 찾지 못해 '{name}'(으)로 정했습니다")
    return name


def _scan_files(out: list[tuple[str, bytes]], ctx: CheckContext, scan: _Scan) -> bool:
    """파일마다 검사하고 지침뿐인 항목인지 돌려준다. 확장자만으로 정하지 않는다: 코드를 문서로 이름 붙이면 확인 창 없이 설치된다."""
    siblings = {PurePosixPath(p).stem for p, _ in out if PurePosixPath(p).suffix.lower() in PY_EXT}
    scan.doc_names = {PurePosixPath(p).name for p, _ in out if PurePosixPath(p).suffix.lower() in TEXT_ONLY_EXT and PurePosixPath(p).name.lower() != "skill.md"}
    text_only = True
    for path, body in out:
        ext = PurePosixPath(path).suffix.lower()
        if ext in PY_EXT:
            text_only = False
            source = _python_source(path, body)
            _scan_python(path, source, siblings, scan)
            _scan_text(path, source, ctx, scan)
            continue
        if ext in CHECKED_IMAGE_EXT:
            _check_image(path, ext, body)
            continue
        if ext not in TEXT_ONLY_EXT:
            text_only = False
        if ext in IMAGE_EXT and ext != ".svg":  # 구조를 확인하지 않는 그림은 사람이 받아 봐야 한다
            scan.flag("not_python", path, None, f"{ext} 그림은 자동 검사가 내용을 볼 수 없습니다")
            scan.caps.add("runs_code")
            continue
        if b"\0" in body:
            if ext in TEXT_ONLY_EXT:
                raise PackageRejected(f"문서에 글자가 아닌 데이터가 있습니다: {path} → UTF-8 글자로 저장해 주세요")
            scan.flag("not_python", path, None, "사람이 읽을 수 없는 파일")
            scan.caps.add("runs_code")
            continue
        text = _decode_any(body)
        if MANAGED_MARK in text:
            raise PackageRejected(f"프로젝트 규칙 파일의 표시 글자가 있습니다: {path} → 이 글자는 AI 지침의 범위를 바꿉니다. 빼고 다시 올려 주세요")
        if ext in OTHER_SCRIPT_EXT:
            scan.flag("not_python", path, None, f"{ext} 파일")
            scan.caps.add("runs_code")
        else:
            if ext not in DATA_EXT and ext not in TEXT_ONLY_EXT:
                scan.flag("not_python", path, None, f"{ext or '확장자 없는'} 파일: 자동 검사가 모르는 형식입니다")
                scan.caps.add("runs_code")
            if text.startswith("#!"):
                text_only = False
                scan.flag("not_python", path, 1, "직접 실행할 수 있는 파일입니다")
                scan.caps.add("runs_code")
            if _spend_ast(len(text), scan):
                if _looks_like_code(text):
                    text_only = False
                    scan.flag("not_python", path, None, f"확장자는 {ext or '없음'}인데 내용이 Python 코드입니다")
                    scan.ast_left += len(text)  # 아래 분석에서 다시 쓴다
                    _scan_python(path, text, siblings, scan)
            else:  # 코드인지 볼 수 없으면 문서라고 말하지 않는다
                text_only = False
                scan.flag("not_python", path, None, "너무 커서 자동 검사가 코드인지 보지 못했습니다")
                scan.caps.add("runs_code")
        if ext == ".svg" and _SVG_SCRIPT.search(text):
            text_only = False
            scan.flag("not_python", path, None, "SVG 안에 스크립트가 있습니다")
            scan.caps.add("runs_code")
        if ext == ".md" and _SHELL_INJECT.search(text):
            scan.flag("shell_inject", path, None, "!`명령` 문법")
        _scan_text(path, text, ctx, scan)
    return text_only and "runs_document" not in scan.hits


def _check_unpacks_on_windows(files: list[tuple[str, bytes]]):
    """받아 두면 그 PC에서 영원히 설치에 실패하는 묶음. 서버는 Linux일 수 있어 풀어 보지 않고 경로로 판단한다."""
    spelled: dict[str, str] = {}
    paths = {p.lower() for p, _ in files}
    for path, _ in files:
        if len(path) > MAX_ITEM_PATH:
            raise PackageRejected(f"경로가 너무 깁니다({len(path)}자): {clean_label(path)[:80]}… → {MAX_ITEM_PATH}자 안으로 줄여 주세요. PC의 작업 폴더 경로가 앞에 붙어 Windows 한도 260자를 넘습니다")
        parts = path.split("/")
        for i in range(1, len(parts)):
            folder = "/".join(parts[:i])
            if folder.lower() in paths:
                raise PackageRejected(f"같은 이름이 파일이면서 폴더입니다: {clean_label(folder)[:80]} → 둘 중 하나의 이름을 바꿔 주세요")
            first = spelled.setdefault(folder.lower(), folder)
            if first != folder:  # 파일 목록과 풀린 결과가 달라져 설치 검사에서 실패한다
                raise PackageRejected(f"폴더 이름이 대소문자만 다릅니다: {clean_label(first)[:80]}, {clean_label(folder)[:80]} → 한 가지로 맞춰 주세요. Windows에서는 같은 폴더입니다")


def check_package(raw: bytes, ctx: CheckContext | None = None, name_hint: str = "") -> CheckResult:
    """zip 바이트를 읽어 규격에 맞추고 검사한다. 파일을 디스크에 쓰지 않는다. 받을 수 없으면 PackageRejected."""
    ctx = ctx or CheckContext(base_skills=base_skill_names())
    # 레시피와 달리 마켓은 프로젝트 맨 위로 풀지 않는다. 받을 파일은 _layout이 마켓 규칙으로 고른다
    files = check_bundle(raw, skip=is_junk, allow_key_files=True, restorable=False)
    files, top = _strip_top(files)
    groups, plugin, single = _layout(files)
    fixes: list[str] = []
    warnings: list[str] = []

    # 이름과 스킬 폴더
    single_front = ""
    if single:
        md = next(b for p, b in groups[""] if p.lower() == "skill.md")
        front, _ = _front_lines(_decode_any(md))
        single_front = _one_line(_first_keys(front).get("name", ""))
    name = _pick_name(plugin, single_front, top, Path(name_hint).stem if name_hint else "", ctx, fixes)
    if not NAME_RE.match(name):
        raise PackageRejected(f"이름 '{name}'을(를) 쓸 수 없습니다 → 영문 소문자로 시작하고 소문자·숫자·하이픈만 써서 40자 안으로 정해 주세요")
    bad = _reserved(name, ctx)
    if bad:
        raise PackageRejected(bad)

    out: list[tuple[str, bytes]] = []
    skills: list[dict] = []
    grouped = {name: groups[""]} if single else groups
    for skill in sorted(grouped):
        if not NAME_RE.match(skill):
            raise PackageRejected(f"스킬 폴더 이름 '{clean_label(skill)[:60]}'을(를) 쓸 수 없습니다 → 영문 소문자·숫자·하이픈만 써 주세요")
        bad = _reserved(skill, ctx)
        if bad:
            raise PackageRejected(bad)
        desc = ""
        for rel, body in grouped[skill]:
            path = f"skills/{skill}/{rel}"
            if rel.lower() == "skill.md":
                path = f"skills/{skill}/SKILL.md"
                body, desc = _fix_skill_md(path, body, skill, fixes)
            elif rel.lower().endswith(".md"):
                body = _to_utf8(path, body, fixes).encode("utf-8")
            out.append((path, body))
        skills.append({"name": skill, "description": desc, "summary": summary_of(desc)})
    _check_unpacks_on_windows(out)

    # 검사
    scan = _Scan()
    is_text_only = _scan_files(out, ctx, scan)
    _scan_labels(plugin, ctx, scan)
    kind = "skill_text" if is_text_only else "skill_code"
    if kind == "skill_code":
        scan.caps.add("runs_code")

    names_in = {name, *(s["name"] for s in skills)}
    for mine in sorted(names_in):
        for other in sorted(ctx.base_skills | ctx.public_names | ctx.public_skills):
            # 서버가 붙인 번호 이름(item-12)끼리는 원래 비슷하다. 사람이 고른 이름만 본다
            if other != mine and len(mine) >= 6 and not re.match(r"item-\d+$", mine) and 0 < _distance(mine, other) <= 2:
                scan.flag("similar_name", "(이름)", None, f"{mine} ↔ {other}")
        if mine in ctx.public_skills:
            scan.flag("similar_name", "(이름)", None, f"{mine}: 다른 공개 항목 안에 같은 이름의 스킬이 있습니다")
    flags = scan.flags()

    # 이름표
    desc_all = " ".join(s["description"] for s in skills)
    declared = plugin.get("requiresTools")
    known_ids = {t["id"] for t in TOOLS}
    if isinstance(declared, list):
        unknown = [x for x in declared if not isinstance(x, str) or x not in known_ids]
        if unknown:
            warnings.append(f"모르는 툴 이름은 뺐습니다: {clean_label(', '.join(map(str, unknown)))[:200]} → CAD·CAE 툴 화면의 이름을 써 주세요")
        tools = sorted(x for x in declared if isinstance(x, str) and x in known_ids)
    else:
        tools = sorted(_infer_tools([name, *[s["name"] for s in skills], desc_all]) | scan.tools)
    for t in tools:
        base = _skill_of(t)
        if base:
            warnings.append(f"기본 스킬 {base}와 쓰임이 겹칠 수 있습니다 → 설명에 언제 이 스킬을 쓰는지 적어 주세요")

    summary = str(plugin.get("description") or "").strip()
    summary = summary_of(summary if summary else (skills[0]["description"] if skills else ""))
    display = clean_label(plugin.get("displayName")) or _display_from(top, name)
    version = plugin.get("version")
    if version is not None and not (isinstance(version, str) and VERSION_RE.match(version)):
        raise PackageRejected(f"버전 '{clean_label(version)[:40]}'을(를) 쓸 수 없습니다 → 1.2.0처럼 숫자 세 개로 쓰거나 빼 주세요. 빼면 서버가 올립니다")
    examples = [clean_label(x)[:200] for x in plugin.get("examples") or [] if isinstance(x, str) and clean_label(x)][:5] if isinstance(plugin.get("examples"), list) else []
    min_agent = plugin.get("minAgentVersion")
    if min_agent is not None and not (isinstance(min_agent, str) and VERSION_RE.match(min_agent)):
        warnings.append(f"minAgentVersion '{clean_label(min_agent)[:40]}'은(는) 뺐습니다 → 0.2.20처럼 숫자 세 개로 써 주세요")
        min_agent = ""
    caps = sorted(scan.caps)
    manifest = {
        "name": name, "version": version or "", "displayName": display[:100], "description": summary, "author": "",
        "category": clean_label(plugin.get("category"))[:40],
        "extensions": {"dds.conversa": {
            "kinds": [kind], "runsOn": "pc", "skills": [s["name"] for s in skills], "requiresTools": tools, "needsTool": bool(tools),
            "examples": examples, "capabilities": caps, "toolList": [], "related": [], "overrides": None,
            "minAgentVersion": min_agent or "", "provenance": {},
            "skillSummaries": {s["name"]: s["summary"] for s in skills},
            "skillDescriptions": {s["name"]: clean_label(s["description"]) for s in skills}}},  # 찾기는 요약이 아니라 설명 전체를 본다
    }
    return CheckResult(name=name, kind=kind, files=out, skills=skills, manifest=manifest, capabilities=caps, flags=flags,
                       fixes=fixes, warnings=sorted(set(warnings)), version=version or None)


def _display_from(top: str, name: str) -> str:
    # 폴더 이름이 한글이면 그것이 사람이 지은 이름이다
    return clean_label(top) if top and re.search(r"[가-힣]", top) else name
