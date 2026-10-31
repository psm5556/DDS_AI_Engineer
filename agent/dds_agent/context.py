"""프로젝트 작업 폴더에 에이전트 CLI별 컨텍스트(작업 규칙 + 스킬)를 동기화한다.
- 작업 규칙: GEMINI.md(Gemini), CLAUDE.md(Claude Code), AGENTS.md(Codex) — 내용은 agent-context/INSTRUCTIONS.md
- 스킬: .gemini/skills, .claude/skills, .agents/skills 에 복사. 규칙 파일 끝에 스킬 목록(경로)도 붙여서
  스킬 자동 탐색을 지원하지 않는 CLI도 SKILL.md를 직접 읽을 수 있게 한다.
- 새 Agent 버전에서 빠진 스킬은 프로젝트 폴더에서도 지운다. 지난번에 복사한 목록(.dds-skills.json)에 있던 것만 지우므로
  사용자가 직접 만든 스킬은 남는다.
- 사용자의 규칙 파일은 지우지 않는다. 표시한 구간에만 DDS 규칙을 넣고 그 밖의 글은 한 글자도 바꾸지 않는다.
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

from .config import REPO_ROOT

log = logging.getLogger("dds_agent.context")

CONTEXT_DIR = REPO_ROOT / "agent-context"
RULE_FILES = ("GEMINI.md", "CLAUDE.md", "AGENTS.md")
SKILL_DIRS = (".gemini/skills", ".claude/skills", ".agents/skills")
MANAGED = ".dds-skills.json"  # 이 Agent가 복사한 스킬 이름 목록
STAMP = ".dds-context-stamp"  # 1행: 마지막으로 복사한 규칙·스킬의 지문, 2행: 규칙 파일별 크기·DDS 전용 여부, 숨긴 항목, git 상태(JSON)
GEMINI_IGNORE = ".geminiignore"
FORMAT = "2"  # 규칙 파일 형식이 바뀌면 올린다. 지문이 달라져 기존 프로젝트도 한 번 다시 맞춘다

# 표시 구간 밖은 사용자 글이다. 파일 앞뒤 어디에 있어도 이 구간만 바꾼다
RULES_START = "<!-- DDS Conversa 규칙 시작: 이 구간은 자동으로 바뀝니다. 직접 쓴 글은 구간 밖에 두세요 -->"
RULES_END = "<!-- DDS Conversa 규칙 끝 -->"
IGNORE_START = "# DDS Conversa 시작: 이 구간은 자동으로 바뀝니다. 직접 쓴 줄은 구간 밖에 두세요"
IGNORE_END = "# DDS Conversa 끝"

# 지난 버전은 규칙 파일을 통째로 썼다: 제목 + 규칙 + 스킬 목록
_OLD_TITLE = re.compile(r"^# DDS (?:Conversa|AI Engineer) — 에이전트 작업 규칙[ \t]*\r?$", re.M)
_OLD_INDEX = re.compile(r"\r?\n## 부록: 사용 가능한 스킬\r?\n")
_OLD_LIST = ("- **", "  `", "필요할 때 아래 SKILL.md를 읽고 따르세요.")

_FILES = {MANAGED, STAMP, GEMINI_IGNORE, *RULE_FILES}  # 나머지 항목은 폴더다
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
    lines = "\n".join(_pattern(prefix + i) + ("" if i in _FILES else "/") for i in sorted(listed))
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
    items = _own_items(project_dir, rules, _skill_names())
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
def _skill_index(skills_root: Path) -> str:
    lines = ["", "## 부록: 사용 가능한 스킬", "필요할 때 아래 SKILL.md를 읽고 따르세요."]
    if os.name == "nt":  # PowerShell·ripgrep이 Hidden 항목을 건너뛰어 스킬이 없다고 오해할 수 있다
        lines.append("사용자 폴더를 깔끔하게 두려고 이 스킬 폴더에 Hidden 속성을 붙여 두었습니다. 아래 경로는 그대로 읽으면 됩니다. "
                     "목록을 볼 때 PowerShell은 `Get-ChildItem -Force`, ripgrep은 `--hidden`을 쓰세요.")
    lines.append("")
    for d in sorted(p for p in skills_root.iterdir() if (p / "SKILL.md").exists()):
        try:  # 사용자 스킬은 cp949일 수도 있다. 설명 하나 때문에 동기화 전체가 멈추면 안 된다
            text = (d / "SKILL.md").read_bytes().decode("utf-8", "replace")
        except OSError:
            text = ""
        m = re.search(r"^description:\s*(.+)$", text, re.M)
        lines.append(f"- **{d.name}** — {m.group(1).strip() if m else ''}  \n  `{(d / 'SKILL.md').as_posix()}`")
    return "\n".join(lines) + "\n"


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


def sync_project_context(project_dir: Path) -> bool:
    """복사했으면 True, 내용이 그대로라 건너뛰었으면 False. 세션마다 파일 수백 개를 복사하면 보안 검사에 걸려 대화 시작이 늦어진다."""
    # 세션 시작이 스레드에서 돌아 같은 폴더를 동시에 맞출 수 있다. 복사가 겹치면 파일 공유 위반이 난다
    with _folder_locks.setdefault(os.path.normcase(os.path.abspath(project_dir)), threading.Lock()):
        return _sync(project_dir)


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
            shutil.copytree(skills_src / name, dst / name, dirs_exist_ok=True, copy_function=_copy_file,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    _write_bytes(marker, json.dumps(names, ensure_ascii=False).encode("utf-8"))
    body = (CONTEXT_DIR / "INSTRUCTIONS.md").read_text(encoding="utf-8")
    body += _skill_index(project_dir / ".claude" / "skills")
    rules = {}
    for name in RULE_FILES:
        try:
            rules[name] = _put_block(project_dir / name, body, RULES_START, RULES_END, legacy)
        except OSError as e:
            raise OSError(f"작업 폴더의 {name}에 DDS 규칙을 넣지 못했습니다({e}). "
                          "다른 프로그램이 파일을 잠그고 있거나 쓰기 권한이 없는지 확인한 뒤 다시 시도하세요") from e
    _tidy(project_dir, stamp, state, rules, dirty=True)
    return True
