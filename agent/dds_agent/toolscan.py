"""이 PC에 설치된 CAD/CAE 툴의 실행 파일을 찾는다 — 사용자가 경로를 적지 않아도 되게.

찾는 곳 (찾은 것을 모두 모아 키마다 버전이 가장 높은 것을 고른다):
  1. 기본 설치 경로(catalog의 guess)와, 같은 구조를 다른 드라이브·폴더에 설치한 경우
     (예: D:\\Program Files\\Altair\\…, D:\\Altair\\…, D:\\CAE\\Altair\\…)
  2. Windows '설치된 앱' 목록(레지스트리 Uninstall의 설치 위치·아이콘·제거 프로그램)과 App Paths
  3. 시작 메뉴·바탕 화면 바로가기(.lnk)의 대상·시작 위치
  4. 환경변수(AWP_ROOT241, UGII_BASE_DIR, AME …)와 PATH — 레지스트리에서 다시 읽어 로그인 후 설치한 것도 잡는다
2~4는 '설치 폴더 후보'다. 툴 회사·제품 이름(catalog.FIND)이 보이는 후보만 골라 그 폴더(와 한 단계 위) 아래에서
guess의 끝부분(예: hwdesktop\\hw\\bin\\win64\\hw.exe)을 찾는다.

결과는 agent.toml 옆 tools.json에 저장한다:
  {"found": {키: 경로}, "picked": {키: 경로}, "scanned_at": "...", "ready": [툴 이름…], "fingerprint": "..."}
  - found: 자동으로 찾은 것(다시 찾을 때마다 덮어씀) / picked: 사용자가 웹에서 직접 고른 것(자동보다 우선)
설치 프로그램이 설치 직후 실행하고(python -m agent.dds_agent.toolscan), PC Agent는 실행 중에 설치 상태가 바뀌면
다시 찾는다(main.py _watch_tools: fingerprint()가 바뀌거나 쓰던 실행 파일이 없어지면).
"""
from __future__ import annotations

import fnmatch
import glob
import hashlib
import json
import logging
import os
import re
import struct
import threading
import time
from pathlib import Path

from cae_adapters.catalog import FIND, PF, PFX86, TOOLS, path_keys, resolve, tool_status

from . import config

log = logging.getLogger("dds_agent.toolscan")

WALK_DEPTH = 4  # 후보 폴더 아래로 내려가 볼 깊이
WALK_LIMIT = 20000  # 후보 폴더 하나에서 살펴볼 최대 항목 수(큰 폴더에서 오래 걸리지 않게)
TIME_BUDGET = 90  # 한 번 찾기에 쓸 최대 시간(초) — 넘으면 그때까지 찾은 것으로 끝낸다
SKIP_DIRS = {"__pycache__", "site-packages", "node_modules", ".git", "help", "doc", "docs", "examples", "tutorials",
             "samples", "locale", "$recycle.bin", "system volume information"}
_WILD = re.compile(r"[*?\[]")
_ABS = re.compile(r'[A-Za-z]:\\[^"<>|*?\r\n]*')
_WORDS = re.compile(r"(?<![a-z0-9])(" + "|".join(sorted({re.escape(w) for ws in FIND.values() for w in ws}, key=len, reverse=True))
                    + ")")
_lock = threading.Lock()


def state_path() -> Path:
    return config.CONFIG_PATH.parent / "tools.json"


# ---------- catalog guess → 찾을 모양 ----------
def _rel(pattern: str) -> list[str] | None:
    """기본 경로 패턴에서 드라이브·Program Files를 뗀 나머지(폴더 단위). Windows 폴더 아래(wsl.exe 등)는 None."""
    low = pattern.lower()
    if low.startswith(os.environ.get("SystemRoot", r"C:\Windows").lower()):
        return None
    for base in (PFX86, PF):  # PFX86이 PF로 시작하므로 먼저 본다
        if low.startswith(base.lower() + "\\"):
            return pattern[len(base) + 1:].split("\\")
    if re.match(r"^[A-Za-z]:\\", pattern):
        return pattern[3:].split("\\")
    return None


def _suffix(rel: list[str]) -> list[str]:
    """설치 폴더 후보 아래에서 찾을 끝부분: 마지막 와일드카드 폴더 다음부터(없으면 회사 폴더 다음부터)."""
    wild = [i for i, p in enumerate(rel[:-1]) if _WILD.search(p)]
    return rel[wild[-1] + 1:] if wild else rel[min(1, len(rel) - 1):]


def _specs() -> dict[str, list[tuple[list[str], list[str]]]]:
    """키 → [(드라이브 뺀 경로, 끝부분)]"""
    out: dict[str, list] = {}
    for t in TOOLS:
        for key, pats in t.get("guess", {}).items():
            for p in pats:
                rel = _rel(p)
                if rel and (rel, _suffix(rel)) not in out.setdefault(key, []):
                    out[key].append((rel, _suffix(rel)))
    return out


def file_names(key: str) -> list[str]:
    """이 키의 실행 파일 이름(웹 화면 안내용, 예: hw.exe)."""
    names = [p.split("\\")[-1] for t in TOOLS for p in t.get("guess", {}).get(key, [])]
    return list(dict.fromkeys(names))


def _version_key(path: str):
    """여러 버전 중 최신 고르기: 경로 폴더마다 숫자들을 묶어 큰 것부터 비교(NX2306 > NX 12.0, 2024.1 > 2024, v241 > v232)."""
    nums = [tuple(int(n) for n in re.findall(r"\d+", c)) for c in Path(path).parts if re.search(r"\d", c)]
    return sorted(nums, reverse=True), path.lower()


def _tail_match(parts: list[str], suffix: list[str]) -> bool:
    """경로(소문자 폴더 목록)가 끝부분 모양과 맞는지"""
    return len(parts) > len(suffix) and all(fnmatch.fnmatch(parts[-len(suffix) + i], s.lower()) for i, s in enumerate(suffix))


# ---------- 설치 폴더 후보 ----------
def fixed_drives() -> list[str]:
    """고정 드라이브만(네트워크·USB는 느리거나 바뀌므로 뺀다)."""
    if os.name != "nt":
        return []
    import ctypes

    k = ctypes.windll.kernel32
    mask = k.GetLogicalDrives()
    return [f"{chr(65 + i)}:\\" for i in range(26) if mask >> i & 1 and k.GetDriveTypeW(f"{chr(65 + i)}:\\") == 3]


def _paths_in(text: str | None, base: Path | None = None) -> list[str]:
    """문자열(설치 위치·명령줄·아이콘 "…\\hw.exe,0")에서 실제로 있는 절대 경로를 뽑는다."""
    out = []
    text = os.path.expandvars(text or "")
    if base and text and not re.match(r"^[A-Za-z]:\\|^\\\\", text) and text.startswith("."):  # .lnk의 상대 경로
        text = os.path.normpath(str(base / text))
    for m in _ABS.finditer(text):
        s = re.sub(r",\s*-?\d+\s*$", "", m.group(0).strip())
        while s and not os.path.exists(s):  # 따옴표 없는 '경로 인자' — 있는 가장 긴 앞부분
            cut = s.rfind(" ")
            s = s[:cut].rstrip() if cut > 3 else ""
        if s:
            out.append(s)
    return out


def _reg_values(hive, sub: str) -> list[tuple[str, dict[str, str]]]:
    """레지스트리 키 아래 하위 키들의 (이름, 문자열 값들)."""
    import winreg

    out = []
    try:
        key = winreg.OpenKey(hive, sub)
    except OSError:
        return out
    with key:
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                name = winreg.EnumKey(key, i)
                with winreg.OpenKey(key, name) as k:
                    vals = {}
                    for j in range(winreg.QueryInfoKey(k)[1]):
                        n, v, _ = winreg.EnumValue(k, j)
                        if isinstance(v, str):
                            vals[n] = v
                out.append((name, vals))
            except OSError:
                continue
    return out


UNINSTALL = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")
APP_PATHS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
ENV_KEYS = (("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"), ("HKCU", "Environment"))


def _hives():
    import winreg

    return {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}


def _from_registry() -> list[tuple[str, str]]:
    if os.name != "nt":
        return []
    out = []
    h = _hives()
    for hive in (h["HKLM"], h["HKCU"]):
        for sub in UNINSTALL:
            for name, v in _reg_values(hive, sub):
                label = " ".join(filter(None, (name, v.get("DisplayName"), v.get("Publisher"))))
                for field in ("InstallLocation", "DisplayIcon", "UninstallString", "InstallSource"):
                    if "msiexec" not in (v.get(field) or "").lower():
                        out += [(p, label) for p in _paths_in(v.get(field))]
        for name, v in _reg_values(hive, APP_PATHS):
            out += [(p, name) for f in ("", "Path") for p in _paths_in(v.get(f))]
    return out


def _env_values() -> dict[str, str]:
    """현재 프로세스 환경 + 레지스트리의 시스템·사용자 환경변수(Agent 시작 후 설치한 툴이 추가한 값까지)."""
    vals = dict(os.environ)
    if os.name == "nt":
        import winreg

        h = _hives()
        for hive, sub in ENV_KEYS:
            try:
                with winreg.OpenKey(h[hive], sub) as k:
                    for j in range(winreg.QueryInfoKey(k)[1]):
                        n, v, _ = winreg.EnumValue(k, j)
                        if isinstance(v, str):
                            if n.upper() == "PATH":
                                vals["PATH"] = vals.get("PATH", "") + ";" + v
                            else:
                                vals.setdefault(n, v)
            except OSError:
                continue
    return vals


def _from_environment() -> list[tuple[str, str]]:
    out = []
    for name, val in _env_values().items():
        if name.upper() in ("PSMODULEPATH", "PATHEXT"):
            continue
        for part in os.path.expandvars(val).split(";"):
            part = part.strip().strip('"')
            if re.match(r"^[A-Za-z]:\\", part) and os.path.exists(part):
                out.append((part, part if name.upper() == "PATH" else f"{name} {part}"))
    return out


def shortcut_dirs() -> list[Path]:
    env = os.environ.get
    return [p for p in (Path(env("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
                        Path(env("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
                        Path(env("PUBLIC", r"C:\Users\Public")) / "Desktop", Path.home() / "Desktop") if p.is_dir()]


def _from_shortcuts() -> list[tuple[str, str]]:
    out = []
    for d in shortcut_dirs():
        for f in d.rglob("*.lnk"):
            label = str(f.relative_to(d))
            for s in read_lnk(f):
                out += [(p, f"{label} {p}") for p in _paths_in(s, f.parent)]
    return out


def read_lnk(path) -> list[str]:
    """바로가기(.lnk)의 대상 경로·상대 경로·시작 위치·인자·아이콘 위치 (MS-SHLLINK 형식, COM 없이 읽는다)."""
    try:
        b = Path(path).read_bytes()
        if len(b) < 0x4C or struct.unpack_from("<I", b, 0)[0] != 0x4C:
            return []
        flags = struct.unpack_from("<I", b, 0x14)[0]
        pos, out = 0x4C, []
        if flags & 0x01:  # LinkTargetIDList
            pos += 2 + struct.unpack_from("<H", b, pos)[0]
        if flags & 0x02:  # LinkInfo
            size, hsize, liflags, _vol, base_off, _net, suf_off = struct.unpack_from("<7I", b, pos)
            if liflags & 1:  # VolumeIDAndLocalBasePath
                if hsize >= 0x24:
                    ubase, usuf = struct.unpack_from("<2I", b, pos + 0x1C)
                    out.append(_wstr(b, pos + ubase) + (_wstr(b, pos + usuf) if usuf else ""))
                else:
                    out.append(_astr(b, pos + base_off) + _astr(b, pos + suf_off))
            pos += size
        wide = flags & 0x80
        for bit in (0x04, 0x08, 0x10, 0x20, 0x40):  # 이름, 상대 경로, 시작 위치, 인자, 아이콘
            if flags & bit:
                n = struct.unpack_from("<H", b, pos)[0]
                ln = n * 2 if wide else n
                s = b[pos + 2:pos + 2 + ln].decode("utf-16-le" if wide else "mbcs" if os.name == "nt" else "latin-1", "replace")
                pos += 2 + ln
                if bit != 0x04:
                    out.append(s)
        return [s for s in out if s]
    except (OSError, struct.error):
        return []


def _wstr(b: bytes, i: int) -> str:
    j = i
    while j + 1 < len(b) and b[j:j + 2] != b"\0\0":
        j += 2
    return b[i:j].decode("utf-16-le", "replace")


def _astr(b: bytes, i: int) -> str:
    j = b.find(b"\0", i)
    return b[i:j if j >= 0 else len(b)].decode("mbcs" if os.name == "nt" else "latin-1", "replace")


def _generic(p: Path) -> bool:
    """찾기 시작점으로 쓰면 너무 넓은 폴더(드라이브 루트, Program Files, Windows, 사용자 폴더 등)."""
    s = os.path.normcase(str(p)).rstrip("\\/")
    if len(s) <= 3:
        return True
    env = os.environ.get
    broad = {os.path.normcase(x).rstrip("\\") for x in (
        PF, PFX86, env("ProgramData", r"C:\ProgramData"), env("SystemRoot", r"C:\Windows"), str(Path.home()), str(Path.home().parent),
        env("APPDATA", ""), env("LOCALAPPDATA", ""), os.path.join(env("LOCALAPPDATA", ""), "Programs"),
        os.path.join(PF, "Common Files"), os.path.join(PFX86, "Common Files")) if x}
    if s in broad or s.startswith(os.path.normcase(env("SystemRoot", r"C:\Windows")) + "\\"):
        return True
    return p.parent == Path(p.anchor) and p.name.lower() in ("program files", "program files (x86)", "programdata", "users", "windows")


# ---------- 찾기 ----------
class _Index:
    """후보 폴더들을 얕게 훑어 폴더·파일 이름 → 경로 목록을 만든다(같은 폴더를 두 번 훑지 않는다)."""

    def __init__(self, deadline: float):
        self.dirs: dict[str, list[str]] = {}
        self.files: dict[str, list[str]] = {}
        self.seen: dict[str, int] = {}
        self.deadline = deadline

    def walk(self, root: Path):
        stack, n = [(str(root), WALK_DEPTH)], 0
        self.dirs.setdefault(root.name.lower(), []).append(str(root))
        while stack and n < WALK_LIMIT and time.monotonic() < self.deadline:
            d, left = stack.pop()
            key = os.path.normcase(d)
            if self.seen.get(key, -1) >= left:
                continue
            self.seen[key] = left
            try:
                with os.scandir(d) as it:
                    for e in it:
                        n += 1
                        name = e.name.lower()
                        try:
                            if e.is_dir(follow_symlinks=False):
                                if name in SKIP_DIRS:
                                    continue
                                if e.path not in self.dirs.setdefault(name, []):
                                    self.dirs[name].append(e.path)
                                if left > 1:
                                    stack.append((e.path, left - 1))
                            elif name.endswith((".exe", ".bat", ".cmd")):
                                self.files.setdefault(name, []).append(e.path)
                        except OSError:
                            continue
            except OSError:
                continue

    def find(self, suffix: list[str]) -> list[str]:
        first = suffix[0].lower()
        if len(suffix) == 1:
            return [p for name, ps in self.files.items() if fnmatch.fnmatch(name, first) for p in ps]
        dirs = self.dirs.get(first, []) if not _WILD.search(first) else \
            [p for name, ps in self.dirs.items() if fnmatch.fnmatch(name, first) for p in ps]
        return [h for d in dirs for h in glob.glob(os.path.join(glob.escape(d), *suffix[1:]))]


def candidates() -> list[tuple[str, str]]:
    """(경로, 설명) — 설치된 앱 목록·바로가기·환경변수·PATH에서 모은 설치 흔적"""
    out = []
    for src in (_from_registry, _from_shortcuts, _from_environment):
        try:
            out += src()
        except Exception:  # 한 군데 실패해도 나머지로 찾는다
            log.exception("툴 찾기: %s 실패", src.__name__)
    return out


def scan() -> dict[str, str]:
    """이 PC에서 툴 실행 파일을 찾는다. 키 → 경로(버전이 여러 개면 최신)."""
    t0 = time.monotonic()
    specs = _specs()
    hits: dict[str, set[str]] = {k: set() for k in specs}

    # 1) 기본 설치 경로 + 같은 구조를 다른 드라이브·폴더에(D:\Program Files\…, D:\Altair\…, D:\CAE\Altair\…)
    drives = fixed_drives() or ["C:\\"]
    for key, lst in specs.items():
        for rel, _ in lst:
            rest = os.path.join(*rel)
            for d in drives:
                for base in ("Program Files", "Program Files (x86)", "", "*"):
                    hits[key].update(glob.glob(os.path.join(d, base, rest) if base else d + rest))

    # 2~4) 설치 흔적에서 후보 폴더를 모아 그 아래에서 끝부분을 찾는다
    roots: dict[str, Path] = {}
    for path, label in candidates():
        p = Path(path)
        parts = [x.lower() for x in p.parts] if path.lower().endswith((".exe", ".bat", ".cmd")) else []
        for key, lst in specs.items():  # 실행 파일을 바로 가리키면(바로가기·아이콘) 그대로 쓰고, 제품 폴더도 후보로
            for _, suf in lst:
                if parts and _tail_match(parts, suf):
                    hits[key].add(path)
                    top = p.parents[len(suf) - 1] if len(p.parents) >= len(suf) else None
                    if top and not _generic(top):
                        roots.setdefault(os.path.normcase(str(top)), top)
        if not _WORDS.search((label + " " + path).lower()):
            continue
        d = p if p.is_dir() else p.parent
        for q in (d, d.parent):  # 설치 위치가 제품 폴더 안쪽을 가리킬 때를 위해 한 단계 위까지
            if not _generic(q):
                roots.setdefault(os.path.normcase(str(q)), q)
    idx = _Index(t0 + TIME_BUDGET)
    for r in sorted(roots.values(), key=lambda q: len(q.parts)):
        idx.walk(r)
    for key, lst in specs.items():
        for _, suf in lst:
            hits[key].update(idx.find(suf))

    found = {}
    for key, ps in hits.items():
        ps = {str(Path(p)) for p in ps if os.path.isfile(p)}
        if ps:
            found[key] = max(ps, key=_version_key)
    log.info("툴 찾기: 후보 폴더 %d곳, %d개 키 찾음 (%.1f초)", len(roots), len(found), time.monotonic() - t0)
    return found


def fingerprint() -> str:
    """설치 상태가 바뀌었는지 가볍게 확인할 값: 설치된 앱 목록(키 이름·수정 시각), 바로가기, 환경변수, 드라이브."""
    h = hashlib.sha1()
    if os.name == "nt":
        import winreg

        hv = _hives()
        for hive in (hv["HKLM"], hv["HKCU"]):
            for sub in UNINSTALL + (APP_PATHS,):
                try:
                    with winreg.OpenKey(hive, sub) as key:
                        for i in range(winreg.QueryInfoKey(key)[0]):
                            try:
                                name = winreg.EnumKey(key, i)
                                with winreg.OpenKey(key, name) as k:
                                    h.update(f"{name}:{winreg.QueryInfoKey(k)[2]};".encode())
                            except OSError:
                                continue
                except OSError:
                    continue
        for k, v in sorted(_env_values().items()):
            h.update(f"{k}={v};".encode("utf-8", "replace"))
    for d in shortcut_dirs():
        for f in sorted(d.rglob("*.lnk")):
            try:
                h.update(f"{f}:{f.stat().st_mtime_ns};".encode("utf-8", "replace"))
            except OSError:
                continue
    h.update(",".join(fixed_drives()).encode())
    return h.hexdigest()[:16]


# ---------- 저장·반영 ----------
def load_state() -> dict:
    try:
        st = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    st.setdefault("found", {})
    st.setdefault("picked", {})
    return st


def save_state(st: dict):
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def ready_names(paths: dict[str, str], verify: bool = False) -> list[str]:
    names = {t["id"]: t["name"] for t in TOOLS}
    return [names[x["id"]] for x in tool_status(paths, verify=verify) if x["status"] == "ready"]


def refresh(s=None, max_age: float = 0) -> dict:
    """다시 찾아 tools.json에 저장하고(사용자가 고른 것은 그대로) 설정 s에 반영한다.
    max_age초 안에 이미 찾았으면 다시 찾지 않고 저장된 결과만 반영한다(동시에 여러 곳에서 부를 때)."""
    with _lock:
        st = load_state()
        if not (max_age and time.time() - st.get("scanned_ts", 0) < max_age):
            t0 = time.time()
            fp = fingerprint()
            st["found"] = scan()
            st.update(fingerprint=fp, scanned_ts=t0, scanned_at=time.strftime("%Y-%m-%d %H:%M"), seconds=round(time.time() - t0, 1))
        if s is not None:
            config.apply_tools(s, st)
            paths = dict(s.tools)
        else:
            paths = {**st["found"], **{k: v for k, v in st["picked"].items() if resolve(v)}}
        st["ready"] = ready_names(paths)
        save_state(st)
        return st


def set_picked(key: str, path: str | None) -> dict:
    """사용자가 웹에서 고른 실행 파일을 저장한다(빈 값이면 지우고 자동으로 찾은 것을 쓴다)."""
    if key not in path_keys():
        raise ValueError(f"알 수 없는 툴 키입니다: {key}")
    with _lock:
        st = load_state()
        warning = None
        if path and path.strip():
            p = Path(path.strip().strip('"'))
            if not p.is_file():
                raise ValueError(f"파일이 없습니다: {p}")
            names = file_names(key)
            if names and not any(fnmatch.fnmatch(p.name.lower(), n.lower()) for n in names):
                warning = f"보통 {', '.join(names)} 파일입니다. 맞게 골랐는지 확인하세요"
            st["picked"][key] = str(p)
        else:
            st["picked"].pop(key, None)
            p = None
        save_state(st)
    return {"key": key, "path": str(p) if p else "", "warning": warning}


def main():
    """설치 프로그램용: 찾아서 저장하고 결과를 tools.json의 ready에 남긴다(설치 스크립트가 읽어 보여 준다)."""
    logging.basicConfig(level=logging.CRITICAL)  # 설치 스크립트는 마지막 줄(JSON)만 읽는다
    s = config.load_settings()
    st = refresh(s)
    st["ready"] = ready_names(dict(s.tools), verify=True)
    save_state(st)
    print(json.dumps({"ready": st["ready"], "seconds": st.get("seconds")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
