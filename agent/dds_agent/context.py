"""프로젝트 작업 폴더에 에이전트 CLI별 컨텍스트(작업 규칙 + 스킬)를 동기화한다.
- 작업 규칙: GEMINI.md(Gemini), CLAUDE.md(Claude Code), AGENTS.md(Codex) — 내용은 agent-context/INSTRUCTIONS.md
- 스킬: .gemini/skills, .claude/skills, .agents/skills 에 복사. 규칙 파일 끝에 스킬 목록(경로)도 붙여서
  스킬 자동 탐색을 지원하지 않는 CLI도 SKILL.md를 직접 읽을 수 있게 한다.
- 새 Agent 버전에서 빠진 스킬은 프로젝트 폴더에서도 지운다. 지난번에 복사한 목록(.dds-skills.json)에 있던 것만 지우므로
  사용자가 직접 만든 스킬은 남는다.
- 사용자의 규칙 파일은 지우지 않는다. 표시한 구간에만 DDS 규칙을 넣고 그 밖의 글은 한 글자도 바꾸지 않는다.
- 규칙·스킬은 Project 폴더에 1벌이다. Task 폴더에는 AGENTS.md·GEMINI.md·.gemini/settings.json과 기록 파일만 둔다(sync_work_context).
  CLAUDE.md는 Task 폴더에 두지 않는다. Claude는 부모의 CLAUDE.md를 읽으므로 두면 규칙을 두 번 읽는다.
- DDS만 만든 항목은 Hidden 속성을 붙여 탐색기와 웹 파일 탭에서 뺀다. 폴더를 깔끔하게 하려는 것이지 보호 수단이 아니다.
  CLI가 찾는 데는 지장이 없다(docs/local-data.md에 CLI별 확인 결과).
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

from cae_adapters.hidden import hidden_kwargs

from . import market as market_store
from .config import REPO_ROOT

log = logging.getLogger("dds_agent.context")

CONTEXT_DIR = REPO_ROOT / "agent-context"
RULE_FILES = ("GEMINI.md", "CLAUDE.md", "AGENTS.md")
SKILL_DIRS = (".gemini/skills", ".claude/skills", ".agents/skills")
MANAGED = ".dds-skills.json"  # 이 Agent가 복사한 스킬 이름 목록
STAMP = ".dds-context-stamp"  # 1행: 마지막으로 복사한 규칙·스킬의 지문, 2행: 규칙 파일별 크기·DDS 전용 여부, 숨긴 항목, git 상태(JSON)
GEMINI_IGNORE = ".geminiignore"
MARKET_FILE = ".dds-market.json"  # 마켓 스킬의 지문과 목록. 기본 스킬의 지문과 따로 둔다: 설치 한 번에 기본 스킬 수백 개를 다시 복사하지 않게
MARKET_TMP = ".dds-market-tmp"
FORMAT = "4"  # 규칙 파일 형식이 바뀌면 올린다. 지문이 달라져 기존 프로젝트도 한 번 다시 맞춘다
WORK_RULES = {"AGENTS.md": ".agents/skills", "GEMINI.md": ".gemini/skills"}  # Task 폴더의 규칙 파일 → 그 CLI의 스킬 폴더
WORK_STAMP = ".dds-work-stamp"  # 1행: 지문, 2행: 규칙 파일별 크기·DDS 전용 여부, 숨긴 항목(JSON)
GEMINI_SETTINGS = ".gemini/settings.json"
WORK_FORMAT = "1"
TASK_GIT_PATTERNS = ("T*/AGENTS.md", "T*/GEMINI.md", "T*/.gemini/", f"T*/{WORK_STAMP}")

# 표시 구간 밖은 사용자 글이다. 파일 앞뒤 어디에 있어도 이 구간만 바꾼다
RULES_START = "<!-- DDS Conversa 규칙 시작: 이 구간은 자동으로 바뀝니다. 직접 쓴 글은 구간 밖에 두세요 -->"
RULES_END = "<!-- DDS Conversa 규칙 끝 -->"
IGNORE_START = "# DDS Conversa 시작: 이 구간은 자동으로 바뀝니다. 직접 쓴 줄은 구간 밖에 두세요"
IGNORE_END = "# DDS Conversa 끝"

# 지난 버전은 규칙 파일을 통째로 썼다: 제목 + 규칙 + 스킬 목록
_OLD_TITLE = re.compile(r"^# DDS (?:Conversa|AI Engineer) — 에이전트 작업 규칙[ \t]*\r?$", re.M)
_OLD_INDEX = re.compile(r"\r?\n## 부록: 사용 가능한 스킬\r?\n")
_OLD_LIST = ("- **", "  `", "필요할 때 아래 SKILL.md를 읽고 따르세요.")

_FILES = {MANAGED, STAMP, GEMINI_IGNORE, MARKET_FILE, *RULE_FILES}  # 나머지 항목은 폴더다
_RETRY = (0.1, 0.3, 0.6, None)  # 보안 프로그램이 방금 쓴 파일을 잠깐 잡고 있을 수 있다
_folder_locks: dict[str, threading.Lock] = {}
_git_lock = threading.Lock()  # 같은 저장소의 프로젝트들이 제외 목록 하나를 함께 고친다

_HIDDEN, _READONLY = 0x2, 0x1
_INVALID = 0xFFFFFFFF
_k32 = None


# ---------- Windows 속성. 다른 OS에서는 아무 일도 하지 않는다 ----------
def _kernel32():
    global _k32
    if os.name != "nt":
        return None
    if _k32 is None:
        k = ctypes.WinDLL("kernel32", use_last_error=True)  # 자기 사본: 공용 windll의 함수 선언을 바꾸지 않는다
        k.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
        k.GetFileAttributesW.restype = ctypes.c_uint32
        k.SetFileAttributesW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        k.SetFileAttributesW.restype = ctypes.c_int
        _k32 = k
    return _k32


def _attrs(path: Path) -> int | None:
    k = _kernel32()
    if k is None:
        return None
    a = k.GetFileAttributesW(str(path))
    return None if a == _INVALID else a


def _set_attrs(path: Path, attrs: int):
    k = _kernel32()
    if k is not None:
        k.SetFileAttributesW(str(path), attrs)


def _set_hidden(path: Path, hidden: bool):
    """다른 속성은 그대로 두고 Hidden만 바꾼다. 실패해도 작업을 막지 않는다(겉모습 문제일 뿐이다)."""
    a = _attrs(path)
    if a is not None and bool(a & _HIDDEN) != hidden:
        _set_attrs(path, a | _HIDDEN if hidden else a & ~_HIDDEN)


# ---------- 쓰기. Hidden 파일은 open('w')·CopyFile이, 읽기 전용은 바꿔치기도 거부한다 ----------
def _retry(action):
    for wait in _RETRY:
        try:
            return action()
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)


def _write_new(target: Path, data: bytes):
    """새 파일은 임시 파일에 다 쓴 뒤 옮겨, 도중에 끊겨도 반쪽 파일이 남지 않게 한다."""
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        _retry(lambda: os.replace(tmp, target))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _write_in_place(target: Path, data: bytes):
    """같은 앞부분은 다시 쓰지 않는다. 표시 구간 앞의 사용자 글은 디스크에서 한 번도 건드리지 않는다."""
    with open(target, "r+b") as f:  # 숨김 파일도 된다. 'w'와 달리 새로 만들지 않는다
        same = len(os.path.commonprefix([f.read(), data]))
        f.seek(same)
        f.write(data[same:])
        f.truncate()


def _write_bytes(path: Path, data: bytes):
    """기존 파일은 제자리에서 고친다. 바꿔 넣으면 새 파일이 되어 ACL·하드 링크·이름의 대소문자를 잃는다."""
    target = path.resolve() if path.is_symlink() else path  # 링크를 일반 파일로 바꾸지 않는다
    if not target.exists():
        return _write_new(target, data)
    attrs, mode = _attrs(target), None
    if attrs is not None and attrs & _READONLY:
        _set_attrs(target, attrs & ~_READONLY)
    if os.name != "nt" and not target.stat().st_mode & stat.S_IWUSR:
        mode = target.stat().st_mode
        target.chmod(mode | stat.S_IWUSR)
    try:
        _retry(lambda: _write_in_place(target, data))
    finally:
        if attrs is not None and attrs & _READONLY:
            _set_attrs(target, attrs)
        if mode is not None:
            target.chmod(mode)


def _copy_file(src, dst):
    """copytree용 복사. 대상이 숨김·읽기 전용이면 속성을 잠깐 풀고, 다른 프로그램이 잡고 있으면 기다렸다 다시 시도한다."""
    hide = False
    try:
        for wait in _RETRY:
            try:
                return shutil.copy2(src, dst)
            except PermissionError as e:
                if wait is None:
                    raise
                a = _attrs(Path(dst))
                if a is not None and a & (_HIDDEN | _READONLY):
                    hide = hide or bool(a & _HIDDEN)
                    _set_attrs(Path(dst), a & ~(_HIDDEN | _READONLY))
                elif getattr(e, "winerror", None) == 5:
                    raise  # 정말 권한이 없다. 파일마다 기다리면 몇 분씩 멈춘다
                else:
                    time.sleep(wait)
    finally:
        if hide:
            _set_hidden(Path(dst), True)


# ---------- 표시 구간 ----------
def _codec(raw: bytes) -> str:
    # UTF-16은 같은 인코딩으로 넣어야 파일이 깨지지 않는다(PowerShell 5.1의 > 가 만든다). 그 밖에는 바이트를 그대로 두고 UTF-8로 덧붙인다
    return "utf-16-le" if raw[:2] == b"\xff\xfe" else "utf-16-be" if raw[:2] == b"\xfe\xff" else "utf-8"


def _blank(raw: bytes, enc: str) -> bool:
    return not raw.decode(enc, "replace").strip(" \t\r\n﻿")


def _merge(raw: bytes | None, body: str, start: str, end: str) -> tuple[bytes, bool]:
    """raw에 표시 구간을 넣거나 바꾼다. (새 내용, 구간 밖에 글이 없음)을 돌려준다."""
    raw = raw or b""
    enc = _codec(raw)
    # 내용 안의 표시 글자에서 구간이 끝난 것으로 읽히면 뒤의 글이 사용자 글이 되어 지워지지 않는다(스킬 설명으로 끼워 넣는 공격)
    while start in body or end in body:
        body = body.replace(start, "").replace(end, "")
    block = f"{start}\n{body.strip()}\n{end}".encode(enc)
    s, e = re.escape(start.encode(enc)), re.escape(end.encode(enc))
    # 끝 표시가 지워진 시작 표시를 건너뛴다: 그 뒤의 사용자 글을 구간으로 삼키지 않게 한다
    m = re.search(s + b"(?:(?!" + s + b").)*?" + e, raw, re.S)
    if m:
        return raw[:m.start()] + block + raw[m.end():], _blank(raw[:m.start()] + raw[m.end():], enc)
    nl = "\n".encode(enc)
    if _blank(raw, enc):
        return raw + block + nl, True
    return raw + (nl if raw.endswith(nl) else nl + nl) + block + nl, False


def _strip_old_copy(raw: bytes, folder: str, synced_before: bool) -> bytes:
    """지난 버전이 통째로 쓴 부분을 빼고 사용자 글만 돌려준다. 그 앞뒤에 덧붙인 글은 바이트 그대로 남긴다.
    지난 버전은 동기화할 때마다 파일을 덮어썼으니, 동기화 흔적이 있거나 목록이 이 폴더를 가리키면 DDS가 쓴 것이다."""
    if RULES_START.encode() in raw:
        return raw  # 이미 새 형식이다. 구간 안의 제목과 목록을 옛 사본으로 보지 않는다
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    title, index = _OLD_TITLE.search(text), None
    for index in _OLD_INDEX.finditer(text):
        pass
    if not title or not index or index.start() < title.start():
        return raw
    lines = text[index.end():].splitlines(keepends=True)
    n = 0
    while n < len(lines) and (not lines[n].strip() or lines[n].startswith(_OLD_LIST)):
        n += 1
    if not synced_before and f"`{folder}/.claude/skills/" not in "".join(lines[:n]):
        return raw
    return (text[:title.start()] + "".join(lines[n:])).encode("utf-8")


def _put_block(path: Path, body: str, start: str, end: str, legacy: tuple[str, bool] | None = None) -> list:
    """표시 구간을 쓴다. [파일 크기, DDS 전용 여부]를 돌려준다. legacy는 지난 형식에서 넘어올 때만 준다: (이 폴더, 동기화 흔적 유무)."""
    try:
        old = path.read_bytes()
    except FileNotFoundError:
        old = None
    new, own = _merge(_strip_old_copy(old, *legacy) if old and legacy else old, body, start, end)
    if new != old:
        _write_bytes(path, new)
    return [len(new), own and not path.is_symlink()]


# ---------- 숨길 항목 ----------
def _names(d: Path) -> set[str]:
    with os.scandir(d) as it:
        return {e.name for e in it}


def _skill_names() -> set[str]:
    with os.scandir(CONTEXT_DIR / "skills") as it:
        return {e.name for e in it if e.is_dir()}


def _own_items(project_dir: Path, rules: dict, skills: set[str]) -> list[str]:
    """DDS만 만든 항목. 이름만 보고 내용은 읽지 않으며 스킬 폴더 안으로 내려가지 않는다.
    .claude 등의 위쪽 폴더에 다른 것이 있으면 사용자 폴더다. 그때는 그 안의 DDS 스킬 폴더만 고른다."""
    items = [MANAGED, STAMP, *(n for n, (_, own) in rules.items() if own)]
    if (project_dir / MARKET_FILE).exists():
        items.append(MARKET_FILE)
    for rel in SKILL_DIRS:
        top, sub = rel.split("/")
        try:
            at_top, in_skills = _names(project_dir / top), _names(project_dir / rel)
        except OSError:
            continue
        if at_top <= {sub} and in_skills <= skills:
            items.append(top)
        else:
            items += [f"{rel}/{n}" for n in sorted(in_skills & skills)]
    return items


# ---------- 저장해 둔 상태 ----------
def _read_state(project_dir: Path) -> dict | None:
    try:
        head, _, body = (project_dir / STAMP).read_text(encoding="utf-8").partition("\n")
        st = json.loads(body)
        rules = {k: [int(v[0]), bool(v[1])] for k, v in st["rules"].items() if k in RULE_FILES}
        hidden = list(st["hidden"])
        git = st.get("git")
        # 망가진 기록으로 폴더 밖을 건드리지 않는다
        if not all(isinstance(r, str) and ".." not in Path(r).parts and not Path(r).is_absolute() for r in hidden):
            return None
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None  # 처음이거나 지난 버전의 형식이면 다시 맞춘다
    return {"stamp": head.strip(), "rules": rules, "hidden": hidden, "git": git}


def dds_items(project_dir: Path) -> set[str]:
    """DDS가 숨긴 항목(프로젝트 폴더 기준 상대 경로). 웹 파일 탭이 탐색기와 같은 목록을 보여 주는 데 쓴다."""
    state = _read_state(project_dir)
    return set(state["hidden"]) if state else set()


def dds_owned_files(project_dir: Path) -> set[str]:
    """내용을 보고 DDS 것임이 확실한 파일(프로젝트 폴더 기준 상대 경로). 지워도 사용자 글을 잃지 않는 것만 고른다.
    dds_items는 마지막 동기화 때의 기록이라 그 뒤에 넣은 메모·명령·개인 스킬까지 DDS 것으로 본다. 지우는 판단에는 쓰지 않는다.
    - 동기화 기록 파일
    - 표시 구간 밖에 글이 없는 규칙 파일·.geminiignore (옛 형식은 고르지 않는다: 본문을 고쳤는지 알 수 없다)
    - DDS 스킬 폴더 안에서 배포본에 같은 이름이 있는 파일 (동기화가 어차피 덮어쓴다. docs/local-data.md)"""
    owned = {n for n in (MANAGED, STAMP) if (project_dir / n).is_file()}
    marks = [(n, RULES_START, RULES_END) for n in RULE_FILES] + [(GEMINI_IGNORE, IGNORE_START, IGNORE_END)]
    for name, start, end in marks:
        path = project_dir / name
        try:
            if not path.is_symlink() and _merge(path.read_bytes(), "", start, end)[1]:
                owned.add(name)
        except OSError:
            pass
    for rel in SKILL_DIRS:
        for skill in _skill_names():
            src, dst = CONTEXT_DIR / "skills" / skill, project_dir / rel / skill
            for sub_dir, _, files in os.walk(dst):
                for f in files:
                    path = Path(sub_dir) / f
                    if (src / path.relative_to(dst)).is_file():
                        owned.add(path.relative_to(project_dir).as_posix())
    return owned


# ---------- git: 커밋에 섞이지 않게 .git/info/exclude에만 적는다. 실패해도 조용히 넘어간다 ----------
def _find_repo(project_dir: Path) -> tuple[Path, Path] | None:
    """(저장소 최상위, 제외 목록이 있는 git 폴더). 작업 폴더가 저장소 안쪽 폴더여도 찾는다."""
    here = Path(os.path.abspath(project_dir))
    for root in (here, *here.parents):
        dot = root / ".git"
        try:
            if dot.is_dir():
                git_dir = dot
            elif dot.is_file():  # 워크트리·서브모듈
                m = re.match(r"gitdir:\s*(.+)", dot.read_text(encoding="utf-8", errors="replace").strip())
                if not m:
                    return None
                git_dir = (root / m.group(1)).resolve()
                if (git_dir / "commondir").is_file():
                    git_dir = (git_dir / (git_dir / "commondir").read_text(encoding="utf-8").strip()).resolve()
            else:
                continue
            return (root, git_dir) if (git_dir / "HEAD").exists() else None
        except OSError:
            return None
    return None


def _tracked(root: Path, paths: list[str]) -> set[str] | None:
    """이미 추적 중인 항목. git이 없거나 실패하면 None(모름): '추적하지 않음'으로 보면 팀이 올린 파일을 고치게 된다."""
    exe = shutil.which("git")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--literal-pathspecs", "ls-files", "-z", "--", *paths], cwd=root, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=10, **hidden_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode:
        return None
    files = r.stdout.decode("utf-8", "replace").split("\0")
    return {p for p in paths if any(f == p or f.startswith(p + "/") for f in files)}


def _pattern(path: str) -> str:
    return "/" + re.sub(r"([\\*?\[])", r"\\\1", path)


def _reopen_for_gemini(path: Path, folders: list[str], locked: bool) -> tuple[bool, bool]:
    """.geminiignore에서 스킬 폴더를 다시 연다. (다시 열렸음, DDS 전용 파일임)을 돌려준다.
    locked면 쓰지 않는다: 팀이 올린 파일이거나 올렸는지 알 수 없는 파일이다."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = None
    if not folders and (raw is None or IGNORE_START.encode(_codec(raw)) not in raw):
        return True, False  # 다시 열 것도, 지울 구간도 없다
    body = "\n".join(f"!{_pattern(i)}/\n!{_pattern(i)}/**" for i in folders)
    new, own = _merge(raw, body, IGNORE_START, IGNORE_END)
    if new != raw:
        if locked:
            return False, False
        _write_bytes(path, new)
    return True, own and not locked


def _git_hide(project_dir: Path, repo: tuple[Path, Path], items: list[str]) -> tuple[list[str], bool]:
    """DDS 항목을 제외 목록에 적는다. (숨길 항목, 끝까지 맞췄음)을 돌려주며, 못 맞췄으면 다음 세션에 다시 시도한다.
    Gemini의 read_file은 작업 폴더 바로 아래 .git/info/exclude에 든 경로를 거부한다(실제 코드로 확인).
    작업 폴더가 저장소 최상위이면 .geminiignore에서 스킬 폴더를 다시 열고, 다시 열 수 없으면 스킬 폴더는 제외하지 않는다:
    git에 새 파일로 보이는 쪽이 Gemini가 스킬을 못 읽는 쪽보다 낫다."""
    root, git_dir = repo
    here = Path(os.path.abspath(project_dir))
    prefix = "" if here == root else here.relative_to(root).as_posix() + "/"
    tracked = _tracked(root, [prefix + i for i in {*items, GEMINI_IGNORE}])
    known = tracked or set()
    ok = tracked is not None
    keep = [i for i in items if prefix + i not in known]
    folders = [i for i in keep if i not in _FILES]
    items, reopened = list(items), True
    if here == root:
        f = here / GEMINI_IGNORE
        try:
            reopened, own = _reopen_for_gemini(f, folders, GEMINI_IGNORE in known or (tracked is None and f.exists()))
        except OSError as e:
            log.info(".geminiignore를 고치지 못했습니다: %s", e)
            reopened, own, ok = False, False, False
        if own:
            items.append(GEMINI_IGNORE)
            keep.append(GEMINI_IGNORE)
    listed = keep if reopened else [i for i in keep if i in _FILES]
    lines = "\n".join([_pattern(prefix + i) + ("" if i in _FILES else "/") for i in sorted(listed)]
                      + [_pattern(prefix) + pat for pat in TASK_GIT_PATTERNS])  # Task 폴더마다 적지 않는다. 글자 그대로 넣어야 *가 패턴이 된다
    # 한 저장소에 프로젝트가 여럿일 수 있다. 구간에 프로젝트 경로를 적어 서로의 항목을 지우지 않는다
    tag = "/" + prefix
    try:
        (git_dir / "info").mkdir(parents=True, exist_ok=True)
        _put_block(git_dir / "info" / "exclude", lines, f"# DDS Conversa 시작 [{tag}]: 이 구간은 자동으로 바뀝니다. 직접 쓴 줄은 구간 밖에 두세요",
                   f"# DDS Conversa 끝 [{tag}]")
    except OSError as e:
        log.info("git 제외 목록을 고치지 못했습니다: %s", e)
        ok = False
    return items, ok


def forget_git_block(old_dir: Path, new_dir: Path):
    """프로젝트 폴더 이름이 바뀌었다. 옛 경로의 제외 구간을 지운다. 크기가 달라져 다음 동기화가 새 경로의 구간을 다시 쓴다."""
    repo = _find_repo(new_dir)
    if not repo:
        return
    root, git_dir = repo
    try:
        prefix = Path(os.path.abspath(old_dir)).relative_to(root).as_posix()
    except ValueError:
        return
    exclude = git_dir / "info" / "exclude"
    if prefix == "." or not exclude.is_file():  # 폴더 자체가 저장소이면 구간 표시에 경로가 없다
        return
    with _git_lock:
        raw = exclude.read_bytes()
        enc, tag = _codec(raw), f"[/{prefix}/]"
        start, end = re.escape(f"# DDS Conversa 시작 {tag}".encode(enc)), re.escape(f"# DDS Conversa 끝 {tag}".encode(enc))
        new = re.sub(start + b".*?" + end + b"(?:\r?\n)?", b"", raw, flags=re.S)
        if new != raw:
            _write_bytes(exclude, new)


def git_hide_aliases(root: Path, project_id: int, names: list[str], label: str | None = None):
    """Project·Task 폴더의 다른 이름(접합)을 제외 목록에 적는다. git은 접합을 보통 폴더로 보아 규칙·스킬·파일 전체를 새 파일로 잡는다.
    `git add -A`면 같은 파일이 이름마다 또 올라간다. 접합이 파일로 보이는 OS도 있어 경로 끝에 /를 붙이지 않는다.
    Project 폴더 자체가 저장소이면 Project의 접합은 저장소 밖이라 할 일이 없다(root에서 저장소를 찾지 못한다).
    label: Task의 접합이면 그 번호(T01). root는 그때 Project 폴더다."""
    repo = _find_repo(root)
    if not repo:
        return
    top, git_dir = repo
    here = Path(os.path.abspath(root))
    prefix = "" if here == top else here.relative_to(top).as_posix() + "/"
    tag = f"[/{prefix}{label or f'P{project_id:04d}'} 접합]"
    start, end = f"# DDS Conversa 시작 {tag}: 이 구간은 자동으로 바뀝니다. 직접 쓴 줄은 구간 밖에 두세요", f"# DDS Conversa 끝 {tag}"
    exclude = git_dir / "info" / "exclude"
    with _git_lock:
        try:
            raw = exclude.read_bytes()
        except FileNotFoundError:
            raw = b""
        if not names and start.encode(_codec(raw)) not in raw:
            return
        exclude.parent.mkdir(parents=True, exist_ok=True)
        _put_block(exclude, "\n".join(_pattern(prefix + n) for n in sorted(names)), start, end)


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _git_seen(project_dir: Path, repo: tuple[Path, Path]) -> list:
    """저장소 위치와 제외 목록·.geminiignore의 크기. 크기만 보면 저장소를 다시 만들었거나 팀이 파일을 올린 것을 읽지 않고 알아챈다."""
    root, git_dir = repo
    at_root = Path(os.path.abspath(project_dir)) == root
    return [str(root), _size(git_dir / "info" / "exclude"), _size(project_dir / GEMINI_IGNORE) if at_root else None]


def _tidy(project_dir: Path, stamp: str, state: dict | None, rules: dict, dirty: bool):
    """숨김·git 제외를 맞추고 상태를 기록한다. 동기화를 건너뛰는 때도 부른다. 내용을 읽거나 스킬 폴더 안으로 내려가지 않는다
    (보안 프로그램이 읽을 때마다 검사해 대화 시작이 늦어진다). 호출 수는 항목 수만큼이다: DDS만 있으면 약 8개,
    사용자의 .claude 등에서는 그 안의 DDS 스킬 폴더마다 하나씩 는다."""
    before = set(state["hidden"]) if state else set()
    # 마켓 스킬도 DDS가 만든 것이다. 빼면 .claude가 사용자 폴더로 보여 기본 스킬 폴더마다 따로 숨기게 된다
    items = _own_items(project_dir, rules, _skill_names() | _market_skills(project_dir))
    if GEMINI_IGNORE in before and (project_dir / GEMINI_IGNORE).exists():
        items.append(GEMINI_IGNORE)  # 저장소가 있으면 아래에서 다시 판단한다
    repo = _find_repo(project_dir)
    seen = _git_seen(project_dir, repo) if repo else None
    if repo and (dirty or seen != (state or {}).get("git") or set(items) != before):
        try:
            with _git_lock:
                items, ok = _git_hide(project_dir, repo, [i for i in items if i != GEMINI_IGNORE])
            seen = _git_seen(project_dir, repo) if ok else None  # 기록을 비워 다음 세션에 다시 시도한다
        except (OSError, ValueError) as e:
            log.info("git 제외 목록을 고치지 못했습니다: %s", e)
            seen = None
    if dirty or not state or set(items) != before or rules != state["rules"] or seen != state["git"]:
        # 다 쓴 뒤에 남긴다. 중간에 끊기면 다음에 다시 복사한다
        body = json.dumps({"rules": rules, "hidden": items, "git": seen}, ensure_ascii=False)
        try:
            _write_bytes(project_dir / STAMP, f"{stamp}\n{body}".encode())
        except OSError as e:  # 기록을 못 남겨도 규칙은 이미 들어갔다. 다음에 다시 맞춘다
            log.info("동기화 기록을 남기지 못했습니다: %s", e)
    for rel in before - set(items):  # DDS가 숨겼던 것만 되돌린다. 사용자가 직접 숨긴 것은 건드리지 않는다
        _set_hidden(project_dir / rel, False)
    for rel in items:
        _set_hidden(project_dir / rel, True)


# ---------- 동기화 ----------
def _skill_index(skills_root: Path, summaries: dict[str, str] | None = None, skip: set[str] | frozenset[str] = frozenset(),
                 where: str = ".claude/skills", base: str | None = None) -> str:
    """summaries: 마켓 스킬의 서버 요약. 등록자의 설명을 모든 AI의 지침에 그대로 넣지 않는다.
    skip: 회수·제거했는데 파일이 잡혀 있어 아직 지우지 못한 스킬. 폴더가 남아 있어도 AI에게 쓰라고 알리지 않는다.
    where·base: 목록에 적을 스킬 폴더와 그 경로의 기준. Task 폴더의 규칙 파일은 ../.agents/skills처럼 부모의 1벌을 가리킨다."""
    # 경로는 규칙 파일이 있는 폴더 기준이다. 절대 경로를 적으면 이름을 바꿀 때 낡고, 규칙 파일을 커밋하면 이 PC의 경로가 함께 올라간다
    summaries = summaries or {}
    base = base or "경로는 이 규칙 파일이 있는 Project 폴더 기준입니다. Task 폴더(T01_…)에서 일할 때는 앞에 `../`를 붙이세요."
    lines = ["", "## 부록: 사용 가능한 스킬", f"필요할 때 아래 SKILL.md를 읽고 따르세요. {base}"]
    if os.name == "nt":  # PowerShell·ripgrep이 Hidden 항목을 건너뛰어 스킬이 없다고 오해할 수 있다
        lines.append("사용자 폴더를 깔끔하게 두려고 이 스킬 폴더에 Hidden 속성을 붙여 두었습니다. 아래 경로는 그대로 읽으면 됩니다. "
                     "목록을 볼 때 PowerShell은 `Get-ChildItem -Force`, ripgrep은 `--hidden`을 쓰세요.")
    lines.append("")
    for d in sorted(p for p in skills_root.iterdir() if (p / "SKILL.md").exists() and p.name not in skip):
        if d.name in summaries:
            lines.append(f"- **{d.name}** — {market_store._one_line(summaries[d.name])}  \n  `{where}/{d.name}/SKILL.md`")
            continue
        try:  # 사용자 스킬은 cp949일 수도 있다. 설명 하나 때문에 동기화 전체가 멈추면 안 된다
            text = (d / "SKILL.md").read_bytes().decode("utf-8", "replace")
        except OSError as e:  # 읽지 못한 스킬은 건너뛴다. 새 세션이 막히는 쪽이 더 나쁘다
            log.warning("스킬 설명을 읽지 못해 목록에서 뺐습니다(%s): %s", d.name, e)
            continue
        m = re.search(r"^description:\s*(.+)$", text, re.M)
        lines.append(f"- **{d.name}** — {m.group(1).strip() if m else ''}  \n  `{where}/{d.name}/SKILL.md`")
    return "\n".join(lines) + "\n"


# ---------- 마켓 스킬. 기본 스킬과 지문·목록을 따로 둔다 ----------
def _market_doc(project_dir: Path) -> dict:
    try:
        raw = json.loads((project_dir / MARKET_FILE).read_text(encoding="utf-8"))
        items = {}
        for key, row in raw["items"].items():
            # 폴더 이름만 받는다. 목록이 망가져도 스킬 폴더 밖을 지우지 않게
            skills = [s for s in row["skills"] if isinstance(s, str) and s == Path(s).name and not s.startswith(".")]
            items[str(key)] = {"version": str(row["version"]), "skills": skills, "summaries": dict(row.get("summaries") or {})}
        # 지우지 못한 스킬 → 그 항목. 기록을 잃으면 다음부터 사용자의 스킬로 보여 영영 남고, 고친 판까지 막는다
        residue = {str(s): str(k) for s, k in dict(raw.get("residue") or {}).items() if str(s) == Path(str(s)).name and not str(s).startswith(".")}
        return {"stamp": str(raw.get("stamp") or ""), "items": items, "conflicts": dict(raw.get("conflicts") or {}), "residue": residue}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"stamp": "", "items": {}, "conflicts": {}, "residue": {}}


def _market_skills(project_dir: Path) -> set[str]:
    doc = _market_doc(project_dir)
    return {s for row in doc["items"].values() for s in row["skills"]} | set(doc["residue"])


def has_market_residue(project_dir: Path) -> bool:
    return bool(_market_doc(project_dir)["residue"])


def _market_summaries(project_dir: Path) -> dict[str, str]:
    return {k: v for row in _market_doc(project_dir)["items"].values() for k, v in row["summaries"].items()}


def _replace_tree(src: Path, dst: Path, tmp_root: Path, tag: str):
    """임시 폴더에 다 복사한 뒤 이름을 바꾼다. 다른 대화가 읽는 동안 지운 폴더나 반쪽 스킬을 보지 않게 하고, 옛 파일도 남기지 않는다."""
    new, old = tmp_root / f"new-{tag}", tmp_root / f"old-{tag}"
    market_store.remove_tree(new)
    market_store.remove_tree(old)
    shutil.copytree(src, new, copy_function=_copy_file, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        _retry(lambda: os.rename(dst, old))
    try:
        _retry(lambda: os.rename(new, dst))
    except OSError:
        if old.exists():  # 새 것을 넣지 못했다. 쓰던 것을 되돌려 스킬이 사라지지 않게 한다
            os.rename(old, dst)
        raise
    market_store.remove_tree(old)


def _sync_market(project_dir: Path) -> dict:
    """lock.json에 맞춰 이 프로젝트의 마켓 스킬만 맞춘다. 기본 스킬은 건드리지 않는다.
    비어 있고 전에 복사한 것도 없으면 아무 파일도 쓰지 않는다: 빈 마켓의 프로젝트 폴더는 마켓이 없던 때와 바이트까지 같다."""
    wanted = market_store.wanted_items(project_dir)
    doc = _market_doc(project_dir)
    if not wanted and not doc["items"] and not (project_dir / MARKET_FILE).exists():
        return {"changed": False, "active": [], "conflicts": {}, "incomplete": []}
    if _read_state(project_dir) is None:  # 세션 시작을 거치지 않고 왔다. 규칙 파일 기록이 있어야 아래 _tidy가 맞는다
        _sync(project_dir)
    base, managed = _skill_names(), _market_skills(project_dir)
    active, conflicts, taken, owner = [], {}, set(), {}
    # 공개 항목을 먼저 본다. 같은 이름의 내 비공개 항목은 충돌로 알린다
    for w in sorted(wanted, key=lambda x: (bool(re.match(r"u\d+\.", x["key"])), x["key"])):
        why = None
        for s in w.get("skills") or []:
            if s in base:
                why = f"기본 스킬과 이름({s})이 겹쳐 쓰지 않습니다 → 기본 스킬을 씁니다. 이 항목은 관리부서에 알려 주세요"
            elif s in taken:  # 사용자의 스킬이 아니다. 이름을 바꾸라고 하면 엉뚱한 일을 시키게 된다
                why = f"설치한 다른 항목 {owner[s].split('.')[-1]}과(와) 스킬 이름({s})이 겹쳐 쓰지 않습니다 → 둘 중 쓰지 않을 항목을 제거하세요"
            elif s not in managed and any((project_dir / rel / s).exists() for rel in SKILL_DIRS):
                why = f"프로젝트에 같은 이름의 스킬({s})이 있어 덮어쓰지 않았습니다 → 내 스킬 이름을 바꾸세요"
            if why:
                break
        if why:
            conflicts[w["key"]] = why
            continue
        taken.update(w.get("skills") or [])
        owner.update((s, w["key"]) for s in w.get("skills") or [])
        active.append(w)
    stamp = hashlib.sha1(json.dumps([[w["key"], w["version"], w.get("skills")] for w in active] + [sorted(conflicts)], ensure_ascii=False).encode()).hexdigest()
    present = all((project_dir / rel / s).is_dir() for rel in SKILL_DIRS for s in taken)
    gone = managed - taken
    if stamp == doc["stamp"] and present and not gone and conflicts == doc["conflicts"]:
        return {"changed": False, "active": active, "conflicts": conflicts, "incomplete": []}

    tmp_root = project_dir / MARKET_TMP
    market_store.remove_tree(tmp_root)  # 지난번 복사가 도중에 끊겼으면 남아 있다
    incomplete, items, failed, residue = [], {}, False, {}
    try:
        for w in active:
            key, skills = w["key"], list(w.get("skills") or [])
            row = doc["items"].get(key)
            same = row and row["version"] == w["version"] and all((project_dir / rel / s).is_dir() for rel in SKILL_DIRS for s in skills)
            if not same:
                try:
                    for i, rel in enumerate(SKILL_DIRS):
                        for s in skills:
                            _replace_tree(w["root"] / "skills" / s, project_dir / rel / s, tmp_root, f"{i}-{s}")
                except OSError as e:  # 다음 턴에 다시 시도한다. 쓰던 버전이 있으면 그대로 둔다
                    log.warning("마켓 스킬을 복사하지 못했습니다(%s): %s", key, e)
                    conflicts[key] = f"스킬을 Project 폴더에 넣지 못했습니다({e}) → 다른 프로그램이 파일을 잡고 있지 않은지 확인하세요. 다음 요청 때 다시 시도합니다"
                    failed = True
                    if row:
                        items[key] = row
                    continue
            items[key] = {"version": w["version"], "skills": skills, "summaries": dict(w.get("summaries") or {})}
        kept = {x for r in items.values() for x in r["skills"]}
        leaving = {**{s: key for key, row in doc["items"].items() for s in row["skills"]}, **doc["residue"]}
        for s, key in leaving.items():
            if s in kept:
                continue
            if s in taken:  # 새 판 복사가 이번에 실패했다. 다음 턴에 다시 시도할 때까지 남은 것의 기록을 잃지 않는다
                if s in doc["residue"]:
                    residue[s] = key
                continue
            for i, rel in enumerate(SKILL_DIRS):
                if s in base:  # Agent 업데이트가 같은 이름의 기본 스킬을 넣었다. 지우면 기본 스킬이 사라지므로 항목이 남긴 파일 없이 기본 스킬로 되돌린다
                    try:
                        _replace_tree(CONTEXT_DIR / "skills" / s, project_dir / rel / s, tmp_root, f"base-{i}-{s}")
                    except OSError as e:
                        log.warning("기본 스킬로 되돌리지 못했습니다(%s): %s", s, e)
                        residue[s] = key
                elif not market_store.remove_tree(project_dir / rel / s):
                    residue[s] = key  # 파일이 열려 있다. 성공처럼 넘어가면 회수가 조용히 실패한다
            if s in residue:
                incomplete.append(key)
    finally:
        market_store.remove_tree(tmp_root)
    summaries = {k: v for row in items.values() for k, v in row["summaries"].items()}
    body = (CONTEXT_DIR / "INSTRUCTIONS.md").read_text(encoding="utf-8") + _skill_index(project_dir / ".claude" / "skills", summaries, skip=set(residue) - base)
    rules = {name: _put_block(project_dir / name, body, RULES_START, RULES_END) for name in RULE_FILES}
    if items or conflicts or residue:
        new_doc = {"stamp": "" if failed or incomplete else stamp, "items": items, "conflicts": conflicts}
        if residue:
            new_doc["residue"] = residue
        _write_bytes(project_dir / MARKET_FILE, json.dumps(new_doc, ensure_ascii=False, indent=1).encode("utf-8"))
    else:
        (project_dir / MARKET_FILE).unlink(missing_ok=True)
    state = _read_state(project_dir)
    # 규칙 파일 크기를 기록해 둔다. 안 그러면 다음 세션이 사용자가 고친 줄 알고 기본 스킬까지 전부 다시 복사한다
    _tidy(project_dir, state["stamp"] if state else _source_stamp(), state, rules, dirty=True)
    # 복사에 실패한 항목은 쓰던 버전을 알린다. 새 버전이라고 안내하면 AI가 옛 파일을 새것으로 읽는다
    active = [{**w, "version": items[w["key"]]["version"]} for w in active if w["key"] in items]
    return {"changed": True, "active": active, "conflicts": conflicts, "incomplete": sorted(set(incomplete))}


def sync_market(project_dir: Path) -> dict:
    """턴 시작·회수 때 부른다. 세션 시작과 같은 잠금을 쓴다: 같은 프로젝트의 두 대화가 서로의 복사를 방해하지 않게."""
    with _folder_locks.setdefault(os.path.normcase(os.path.abspath(project_dir)), threading.Lock()):
        return _sync_market(project_dir)


def _source_stamp() -> str:
    """배포된 규칙·스킬의 지문. 파일 내용을 읽지 않고 이름·크기·수정 시각만 본다(보안 프로그램이 읽을 때마다 검사한다)."""
    h = hashlib.sha1()
    h.update(f"format={FORMAT}\n".encode())
    for p in sorted(CONTEXT_DIR.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc":
            st = p.stat()
            h.update(f"{p.relative_to(CONTEXT_DIR).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()


def _up_to_date(project_dir: Path, stamp: str, state: dict | None) -> bool:
    if not state or state["stamp"] != stamp or not set(RULE_FILES) <= set(state["rules"]):
        return False
    for name, (size, _) in state["rules"].items():
        try:  # 크기만 본다. 사용자가 고쳤으면 다시 맞추며 그 글을 살린다
            if (project_dir / name).stat().st_size != size:
                return False
        except OSError:
            return False
    return all((project_dir / d).is_dir() for d in SKILL_DIRS)


def _copy_failed(project_dir: Path, src: Path, dst: Path, err: OSError) -> str:
    """Windows는 경로 260자를 넘으면 복사가 실패한다. 스킬 폴더는 작업 폴더보다 수십 자 더 깊다."""
    deepest = max((len(str(dst / p.relative_to(src))) for p in src.rglob("*")), default=len(str(dst)))
    if deepest >= 260:
        return (f"Project 폴더 경로가 너무 길어 AI 작업 규칙을 복사하지 못했습니다(가장 긴 경로 {deepest}자, Windows 한도 260자): {project_dir} "
                "→ Project 폴더를 더 짧은 경로(예: C:\\DDS)로 바꾼 뒤 다시 요청하세요")
    first = (err.args[0][0][2] if isinstance(err, shutil.Error) and err.args and err.args[0] else str(err))[:200]
    return f"AI 작업 규칙을 Project 폴더로 복사하지 못했습니다: {project_dir} ({first}) → 폴더 쓰기 권한과 남은 디스크 용량을 확인한 뒤 다시 요청하세요"


def sync_project_context(project_dir: Path) -> bool:
    """복사했으면 True, 내용이 그대로라 건너뛰었으면 False. 세션마다 파일 수백 개를 복사하면 보안 검사에 걸려 대화 시작이 늦어진다."""
    # 세션 시작이 스레드에서 돌아 같은 폴더를 동시에 맞출 수 있다. 복사가 겹치면 파일 공유 위반이 난다
    with _folder_locks.setdefault(os.path.normcase(os.path.abspath(project_dir)), threading.Lock()):
        copied = _sync(project_dir)
        try:
            _sync_market(project_dir)
        except Exception as e:  # 마켓 항목 하나 때문에 새 세션이 막히면 안 된다. 턴을 시작할 때 다시 시도한다
            log.warning("마켓 스킬을 맞추지 못했습니다(%s): %s", project_dir, e)
        return copied


def _sync(project_dir: Path) -> bool:
    stamp = _source_stamp()
    state = _read_state(project_dir)
    if _up_to_date(project_dir, stamp, state):
        _tidy(project_dir, stamp, state, state["rules"], dirty=False)
        return False
    skills_src = CONTEXT_DIR / "skills"
    names = sorted(p.name for p in skills_src.iterdir() if p.is_dir())
    marker = project_dir / MANAGED
    # 새 형식의 기록이 있으면 지난 버전 사본을 찾지 않는다. 그 뒤의 파일은 사용자가 쓴 것이다
    legacy = None if state else (project_dir.as_posix(), marker.exists() or (project_dir / STAMP).exists())
    try:
        before = json.loads(marker.read_text(encoding="utf-8"))
        # 폴더 이름만 받는다 — 목록이 망가져도 스킬 폴더 밖을 지우지 않게
        gone = {n for n in before if isinstance(n, str) and n == Path(n).name and not n.startswith(".")} - set(names)
    except (OSError, ValueError, TypeError):
        gone = set()  # 처음이거나 목록이 깨졌으면 지우지 않는다
    for rel in SKILL_DIRS:
        dst = project_dir / rel
        dst.mkdir(parents=True, exist_ok=True)
        for name in gone:
            shutil.rmtree(dst / name, ignore_errors=True)
        for name in names:
            try:
                shutil.copytree(skills_src / name, dst / name, dirs_exist_ok=True, copy_function=_copy_file,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            except OSError as e:  # shutil.Error도 OSError다. 그대로 두면 실패한 경로 수백 개가 대화에 "에이전트 오류"로 쏟아진다
                raise RuntimeError(_copy_failed(project_dir, skills_src / name, dst / name, e)) from e
    _write_bytes(marker, json.dumps(names, ensure_ascii=False).encode("utf-8"))
    body = (CONTEXT_DIR / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    body += _skill_index(project_dir / ".claude" / "skills", _market_summaries(project_dir))
    rules = {}
    for name in RULE_FILES:
        try:
            rules[name] = _put_block(project_dir / name, body, RULES_START, RULES_END, legacy)
        except OSError as e:
            raise OSError(f"Project 폴더의 {name}에 DDS 규칙을 넣지 못했습니다({e}). "
                          "다른 프로그램이 파일을 잠그고 있거나 쓰기 권한이 없는지 확인한 뒤 다시 시도하세요") from e
    _tidy(project_dir, stamp, state, rules, dirty=True)
    return True


# ---------- Task 폴더 ----------
def _read_work_state(work_dir: Path) -> dict | None:
    try:
        head, _, body = (work_dir / WORK_STAMP).read_text(encoding="utf-8").partition("\n")
        st = json.loads(body)
        rules = {k: [int(v[0]), bool(v[1])] for k, v in st["rules"].items() if k in WORK_RULES}
        hidden = [r for r in st["hidden"] if isinstance(r, str) and ".." not in Path(r).parts and not Path(r).is_absolute()]
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None
    return {"stamp": head.strip(), "rules": rules, "hidden": hidden, "include": st.get("include")}


def dds_work_items(work_dir: Path) -> set[str]:
    """DDS가 Task 폴더에서 숨긴 항목. 웹 파일 탭이 탐색기와 같은 목록을 보여 주는 데 쓴다."""
    state = _read_work_state(work_dir)
    return set(state["hidden"]) if state else set()


def _parent_rules_read(work_dir: Path) -> bool:
    """CLI가 Project 폴더의 규칙 파일도 읽는 배치인가. Codex·Gemini는 저장소 최상위부터 cwd까지의 규칙 파일을 모두 읽고(저장소가 없으면 cwd만),
    파일이 달라 같은 글을 두 번 받는다. 저장소가 Task 폴더 안에서 시작하면 부모를 읽지 않는다."""
    repo = _find_repo(work_dir)
    return bool(repo and Path(os.path.abspath(work_dir)) != repo[0])


def _work_rule_body(project_dir: Path, skills: str, inherited: bool, copied: bool) -> str:
    where = skills if copied else f"../{skills}"
    base = "경로는 이 Task 폴더 기준입니다. 위 폴더(Project 폴더)에 1벌만 있으니 고치지 말고, 쓸 때는 `scripts/`로 복사합니다."
    if copied:
        base = "경로는 이 Task 폴더 기준입니다. 쓸 때는 `scripts/`로 복사합니다."
    doc = _market_doc(project_dir)
    index = _skill_index(project_dir / ".claude" / "skills", _market_summaries(project_dir), skip=set(doc["residue"]) - _skill_names(), where=where, base=base)
    if inherited:  # 규칙 본문은 위 폴더의 파일로 이미 들어온다. 스킬 경로의 기준만 다르게 알려 준다
        return "# 이 폴더는 Task 폴더입니다\n규칙 본문은 위 폴더(Project 폴더)의 규칙 파일에 있습니다.\n" + index
    return (CONTEXT_DIR / "INSTRUCTIONS.md").read_text(encoding="utf-8") + index


def _merge_gemini_settings(path: Path, project_dir: Path, known: str | None) -> bool | None:
    """Task 폴더에서 위 폴더의 스킬과 원본을 읽게 한다. 사용자가 이 파일에 쓴 다른 설정은 그대로 둔다. 절대 경로를 쓴다(Project 이름을 바꾸면 다음 동기화가 맞춘다).
    known: 지난번에 우리가 넣은 경로. 이름이 바뀌었으면 옛 경로를 뺀다. 이 파일에는 적지 않는다: Gemini가 모르는 설정 키를 오류로 볼 수 있다.
    읽지 못한 파일은 덮지 않는다: 사용자 설정이 망가진 채 남아 있으면 Gemini가 알려 준다.
    True: DDS만 쓴 파일이다(숨긴다). False: 사용자 설정이 들었다. None: 고치지 못했다."""
    try:
        cur = json.loads(path.read_bytes().decode("utf-8-sig"))
        if not isinstance(cur, dict):
            return None
    except FileNotFoundError:
        cur = {}
    except ValueError:
        log.info("%s를 읽지 못해 고치지 않았습니다", path)
        return None
    ctx = cur.setdefault("context", {}) if isinstance(cur.get("context", {}), dict) else None
    if ctx is None:
        return None
    want = str(project_dir)
    dirs = [d for d in ctx.get("includeDirectories") or [] if isinstance(d, str)]
    new = [d for d in dirs if d != known and d != want] + [want]
    if new != dirs:
        ctx["includeDirectories"] = new
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_bytes(path, json.dumps(cur, ensure_ascii=False, indent=2).encode("utf-8"))
    return set(cur) == {"context"} and set(ctx) == {"includeDirectories"} and new == [want]


def sync_work_context(work_dir: Path, project_dir: Path, copy_skills: tuple[str, ...] | list[str] = ()) -> bool:
    """Task 폴더에 그 Task에서 CLI를 띄우는 데 필요한 것만 둔다. 복사했으면 True.
    Claude는 부모의 CLAUDE.md·.claude/skills를 읽어 여기에 아무것도 필요 없다. Codex·Gemini는 cwd의 규칙 파일이 스킬의 ../ 경로를 알려 준다.
    copy_skills: 부모의 스킬을 읽지 못하는 CLI 이름. 그 CLI만 스킬을 Task 폴더에 복사한다(Task마다 파일 수십 개). 사내 관문 G1~G6 결과로 정한다.
    한 번 복사한 폴더는 다른 CLI로 이어도 그대로 쓴다. 지우면 그 CLI의 다음 세션이 또 복사해야 한다."""
    copied_dirs = {"gemini": ".gemini/skills", "codex": ".agents/skills"}
    copies = sorted(rel for name, rel in copied_dirs.items() if name in (copy_skills or ()) or (work_dir / rel).is_dir())
    with _folder_locks.setdefault(os.path.normcase(os.path.abspath(work_dir)), threading.Lock()):
        inherited = _parent_rules_read(work_dir)
        market = _market_doc(project_dir)
        stamp = hashlib.sha1(json.dumps([WORK_FORMAT, _source_stamp(), market["stamp"], sorted(market["residue"]), inherited, copies, str(project_dir)],
                                        ensure_ascii=False).encode()).hexdigest()
        state = _read_work_state(work_dir)
        fresh = bool(state and state["stamp"] == stamp and set(WORK_RULES) <= set(state["rules"])
                     and all(_size(work_dir / n) == state["rules"][n][0] for n in WORK_RULES)
                     and all((work_dir / rel).is_dir() for rel in copies))
        if fresh:
            for rel in state["hidden"]:
                _set_hidden(work_dir / rel, True)
            return False
        rules = {name: _put_block(work_dir / name, _work_rule_body(project_dir, skills, inherited, skills in copies), RULES_START, RULES_END)
                 for name, skills in WORK_RULES.items()}
        settings_own = False
        try:
            settings_own = _merge_gemini_settings(work_dir / GEMINI_SETTINGS, project_dir, state.get("include") if state else None)
        except OSError as e:  # Gemini를 쓰지 않으면 상관없다. 쓰면 위 폴더의 스킬을 못 읽어 AI가 사용자에게 알린다
            log.info("%s를 고치지 못했습니다: %s", work_dir / GEMINI_SETTINGS, e)
        for rel in copies:
            src = project_dir / rel
            try:
                shutil.copytree(src, work_dir / rel, dirs_exist_ok=True, copy_function=_copy_file, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            except OSError as e:
                raise RuntimeError(_copy_failed(work_dir, src, work_dir / rel, e)) from e
        hidden = [WORK_STAMP, *(n for n, (_, own) in rules.items() if own)]
        gemini_top = work_dir / ".gemini"
        if gemini_top.is_dir() and settings_own and _names(gemini_top) <= {"settings.json", "skills"}:
            hidden.append(".gemini")
        if copies and (work_dir / ".agents").is_dir() and _names(work_dir / ".agents") <= {"skills"}:
            hidden.append(".agents")
        record = {"rules": rules, "hidden": hidden, "include": str(project_dir)}
        _write_bytes(work_dir / WORK_STAMP, f"{stamp}\n{json.dumps(record, ensure_ascii=False)}".encode())
        for rel in (state["hidden"] if state else []):
            if rel not in hidden:
                _set_hidden(work_dir / rel, False)
        for rel in hidden:
            _set_hidden(work_dir / rel, True)
        return True
