"""PC의 마켓 항목 보관소. ~/.dds-agent/market/ (agent.toml과 같은 폴더 아래라 Agent를 업데이트해도 남는다)
  lock.json                    지금 이 PC가 쓸 항목. 쓰는 쪽은 worker 하나이고 바꿔치기로만 쓴다
  items/<key>/<version>/       받아 둔 항목. .installed가 있어야 다 받은 것이다
  downloads/ tmp/              받는 중

서버는 market.sync로 전체 목록을 보낸다. 잃어도 다음 연결에서 복구된다. 일을 시킨 메시지가 아니므로 자동 업데이트의 유휴 판단을 깨지 않는다.
- 회수를 가장 먼저 처리하고 바로 답한다. 목록을 바꾸면 그 뒤의 어떤 턴도 그 항목을 쓰지 않으므로, 폴더를 다 지우기 전에 답한다.
  큰 파일을 받느라 작업자가 바쁘거나 프로젝트가 많아도 서버의 15초 기다림을 넘기지 않게 한다.
- 작업자 하나가 가장 새 메시지만 처리한다. 늦게 끝난 옛 동기화가 회수를 되돌리지 않게 lock.json을 쓰기 직전에 다시 확인한다.
- 받은 것은 sha256과 파일 목록을 정확히 비교한다. 다르면 설치하지 않고 쓰던 버전을 그대로 둔다.
PC에서 도는 항목은 내 권한을 그대로 갖는다. 이 파일의 검사는 전송 오류·교체·실수를 잡는 것이지 격리가 아니다.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import sys
import threading
import time
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

log = logging.getLogger("dds_agent.market")

LOCK = "lock.json"
INSTALLED = ".installed"
REPLACE_RETRY_SEC = 3.0  # 입구·보안 프로그램이 파일을 읽는 동안 바꿔치기가 잠깐 실패한다(WinError 5)
RETRY_FAILED_SEC = 300
MAX_MEMBER = 64 * 1024 * 1024
KEEP_OLD = 1  # 대화가 고정해 둔 코드 항목의 이전 버전을 이만큼 남긴다
# 코드 항목의 버전 고정은 DOE처럼 이어지는 작업을 위한 것이다. CLI는 끊을 때까지 살아 있으므로, 쉬는 대화까지 붙들면 새 버전이 영영 오지 않는다
PIN_IDLE_SEC = 3600
_WIN_RESERVED = re.compile(r"^(con|prn|aux|nul|conin\$|conout\$|com[0-9¹²³]|lpt[0-9¹²³])\Z", re.I)
_WIN_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]*\Z")
_VER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+\Z")
_SKILL_RE = re.compile(r"^[a-z][a-z0-9-]{0,39}\Z")
_FRIENDLY = re.compile(r"[가-힣].*→")
SKILL_DIR_OF = {"gemini": ".gemini/skills", "codex": ".agents/skills"}  # 나머지는 .claude/skills

# 대화가 쓰던 코드 항목의 버전을 고정한다. Agent가 프로젝트 폴더별로 답한다(D9: DOE 도중 버전이 바뀌면 학습 데이터가 섞인다)
PIN_PROVIDER = None


class MarketError(RuntimeError):
    """사용자에게 보일 문장을 담는다."""


def market_root() -> Path:
    env = os.environ.get("DDS_MARKET_DIR")
    if env:
        return Path(env)
    cfg = os.environ.get("DDS_AGENT_CONFIG")
    return (Path(cfg).parent if cfg else Path.home() / ".dds-agent") / "market"


def read_lock() -> dict:
    """없으면 빈 값. 내용이 망가졌으면 빈 값으로 보고 다음 동기화가 다시 쓴다(바꿔치기로만 쓰므로 일부러 고치지 않으면 생기지 않는다).
    바꿔치기하는 순간에는 Windows가 읽기를 잠깐 거부한다. 이것을 빈 목록으로 읽으면 설치된 스킬을 모두 지우게 되므로 기다렸다 다시 읽고,
    끝내 못 읽으면 예외를 내어 지금 상태를 그대로 둔다."""
    end = time.monotonic() + REPLACE_RETRY_SEC
    while True:
        try:
            data = json.loads((market_root() / LOCK).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except ValueError as e:
            log.warning("마켓 목록이 망가졌습니다. 다음 동기화가 다시 씁니다: %s", e)
            return {}
        except OSError as e:
            if time.monotonic() >= end:
                raise MarketError(f"마켓 목록을 읽지 못했습니다({e}) → 다음 요청 때 다시 시도합니다") from e
            time.sleep(0.05)


def write_lock(data: dict):
    root = market_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / LOCK
    tmp = root / f".{LOCK}.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    end = time.monotonic() + REPLACE_RETRY_SEC
    while True:
        try:
            os.replace(tmp, target)
            return
        except PermissionError:
            if time.monotonic() >= end:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)


def item_dir(key: str, version: str) -> Path:
    if not _KEY_RE.match(key or "") or not _VER_RE.match(version or ""):
        raise MarketError(f"항목 이름이나 버전을 쓸 수 없습니다: {key} {version} → 관리부서에 알려 주세요")
    return market_root() / "items" / key / version


def is_installed(key: str, version: str, sha256: str | None = None) -> bool:
    try:
        got = (item_dir(key, version) / INSTALLED).read_text(encoding="utf-8").strip()
    except (OSError, MarketError):
        return False
    return not sha256 or got == sha256


def recalled_versions(lock: dict) -> dict[str, set[str]]:
    return {r["key"]: set(r.get("versions") or []) for r in lock.get("recalled") or [] if r.get("key")}


def wanted_items(project_dir: Path | None = None, lock: dict | None = None) -> list[dict]:
    """이 프로젝트 폴더에 있어야 할 항목. 살아 있는 대화가 쓰던 코드 항목은 그 버전을 유지한다.
    회수된 버전은 고정보다 우선한다."""
    lock = read_lock() if lock is None else lock
    gone = recalled_versions(lock)
    pins = {}
    if project_dir is not None and PIN_PROVIDER is not None:
        try:
            pins = PIN_PROVIDER(project_dir) or {}
        except Exception:
            log.exception("마켓 버전 고정을 읽지 못했습니다")
    out = []
    for it in lock.get("items") or []:
        key, version = it.get("key"), it.get("version")
        if not all(_SKILL_RE.match(str(s)) for s in it.get("skills") or []):
            log.warning("쓸 수 없는 스킬 이름이 있어 건너뜁니다: %s", key)  # 이름이 프로젝트 폴더의 경로가 된다. 서버를 믿지 않는 마지막 관문이다
            continue
        pin = pins.get(key)
        floor = it.get("pin_floor")  # 사용자가 "지금 새 버전으로"를 누른 버전. 그 아래로는 붙들지 않는다
        if pin and pin != version and it.get("code") and pin not in gone.get(key, ()) and is_installed(key, pin) and not (floor and _vkey(pin) < _vkey(floor)):
            version = pin
        if version in gone.get(key, ()) or not is_installed(key, version):
            continue
        out.append({**it, "version": version, "root": item_dir(key, version)})
    return out


def _clear_attrs(func, path, _exc):
    """읽기 전용·숨김 파일은 rmtree가 지우지 못한다."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def remove_tree(path: Path) -> bool:
    """지운 뒤 정말 없어졌는지 본다. ignore_errors는 열린 파일을 남기고도 성공처럼 끝나 회수가 조용히 실패한다."""
    # onexc는 3.12부터다. PC 런타임은 서버의 Python을 묶어 가므로 3.11일 수 있고, 거기서 TypeError가 나면 회수 보고까지 끊긴다
    handler = {"onexc" if sys.version_info >= (3, 12) else "onerror": _clear_attrs}
    for wait in (0.1, 0.5, 1.0, None):
        if path.exists() or path.is_symlink():
            shutil.rmtree(path, **handler) if path.is_dir() and not path.is_symlink() else _unlink(path)
        if not path.exists() and not path.is_symlink():
            return True
        if wait is None:
            return False
        time.sleep(wait)
    return False


def _unlink(path: Path):
    try:
        path.unlink()
    except OSError:
        pass


def _safe_member(name: str) -> str:
    """풀기 전에 경로를 다시 본다. 서버의 검사와 같은 규칙을 PC에서도 적용한다: 서버를 믿지 않는 PC가 마지막 관문이다."""
    n = name.replace("\\", "/")
    parts = n.split("/")
    if n.startswith("/") or any(not p.strip(" .") for p in parts):
        raise MarketError(f"안전하지 않은 경로가 있어 설치하지 않았습니다: {name[:80]} → 관리부서에 알려 주세요")
    for part in parts:
        if part != part.rstrip(" .") or _WIN_BAD.search(part) or _WIN_RESERVED.match(part.split(".")[0].rstrip(" ")):
            raise MarketError(f"안전하지 않은 파일 이름이 있어 설치하지 않았습니다: {name[:80]} → 관리부서에 알려 주세요")
    return n


def extract_checked(zip_path: Path, dest: Path, files: list[dict]):
    """임시 폴더에 풀고 등록본의 파일 목록과 정확히 비교한다. 이름 끝의 점으로 컴파일 코드를 넣는 길을 여기서도 막는다."""
    want = {f["path"]: f["sha256"] for f in files}
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = _safe_member(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise MarketError(f"링크가 있어 설치하지 않았습니다: {rel} → 관리부서에 알려 주세요")
            target = dest.joinpath(*PurePosixPath(rel).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src:
                body = src.read(MAX_MEMBER + 1)
            if len(body) > MAX_MEMBER:
                raise MarketError(f"파일이 너무 커서 설치하지 않았습니다: {rel} → 관리부서에 알려 주세요")
            target.write_bytes(body)
    got = {}
    for p in dest.rglob("*"):
        if p.is_file():
            got[p.relative_to(dest).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    if got != want:
        extra = sorted(set(got) - set(want))[:3]
        missing = sorted(set(want) - set(got))[:3]
        changed = sorted(k for k in set(got) & set(want) if got[k] != want[k])[:3]
        raise MarketError(f"풀린 파일이 등록본과 다릅니다(더 있음 {extra}, 없음 {missing}, 내용 다름 {changed}) → 다시 시도하고, 반복되면 관리부서에 알려 주세요")


class MarketStore:
    def __init__(self, api, send_state, workspace_root, version: str, on_busy=None, project_dirs=None):
        self.api = api
        self.send_state = send_state  # market.state를 끊겨도 잃지 않는 길로 보낸다
        self.workspace_root = Path(workspace_root)
        self.version = version
        self.on_busy = on_busy  # 받는 동안 Agent 자동 업데이트를 미룬다. (+1 / -1)
        self.project_dirs = project_dirs
        self.first_sync = threading.Event()
        self.seen: dict[str, dict[str, str]] = {}  # 세션 → 그 대화가 아는 항목 {key: version}
        self.holds: dict[str, dict[str, str]] = {}  # 세션 → 그 대화가 붙들고 있는 코드 항목 버전. 다른 대화의 고정 때문에 옛 버전을 받은 새 대화는 붙들지 않는다
        self.last_turn: dict[str, float] = {}  # 세션 → 마지막 요청 시각
        self.conflicts: dict[str, dict] = {}  # key → {프로젝트: {project_id, project, message}}. 겹침은 프로젝트마다 다르다
        self.pins: dict[str, dict] = {}  # key → {프로젝트: {project_id, project, version}}. 그 프로젝트가 옛 버전을 쓰는 중이다
        self.told: dict[str, set[str]] = {}  # 세션 → 대화에 이미 알린 겹침
        self.send_note = None  # 그 대화의 사용자에게 보일 안내를 보낸다. (세션, 글)
        self._acked_recall = None
        self._lock_write = threading.Lock()  # 작업자와 회수 답장이 lock.json을 함께 쓴다
        self._cv = threading.Condition()
        self._latest: dict | None = None
        self._applied: dict | None = None
        self._states: dict[str, dict] = {}
        self._incomplete: list[str] = []
        self._thread: threading.Thread | None = None
        self._seen_lock = threading.Lock()

    # ---------- 서버 메시지 ----------
    def on_sync(self, msg: dict):
        """수신 루프에서 부른다. 막지 않는다."""
        with self._cv:
            self._latest = msg
            self._cv.notify_all()
        self.first_sync.set()
        # 작업자가 큰 파일을 받는 중이면 이 메시지를 받기가 끝난 뒤에야 본다. 회수 답장은 그것을 기다리지 않는다
        if msg.get("recalled") and msg.get("recall_rev") != self._acked_recall:
            threading.Thread(target=self._ack_recall, args=(msg,), daemon=True, name="market-recall").start()
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="market-sync")
            self._thread.start()

    def _stale(self, msg: dict) -> bool:
        with self._cv:
            return self._latest is not msg

    def _loop(self):
        while True:
            with self._cv:
                while self._latest is self._applied:
                    # 받기에 실패한 항목이 있으면 조금 뒤 다시 시도한다
                    if not self._cv.wait(RETRY_FAILED_SEC if self._has_failed() else None) and self._has_failed():
                        self._applied = None
                msg = self._latest
            try:
                if self.process(msg):
                    with self._cv:
                        self._applied = msg
            except Exception:
                log.exception("마켓 동기화 실패")
                with self._cv:
                    self._applied = msg  # 다음 메시지나 다음 연결에서 다시 맞춘다

    def _has_failed(self) -> bool:
        """조금 뒤 다시 할 일이 있는가. 지우지 못한 복사본도 넣는다: 대화가 없는 프로젝트는 턴을 기다려도 지워지지 않는다."""
        return bool(self._incomplete) or any(s.get("state") == "failed" for s in self._states.values())

    # ---------- 처리 ----------
    def process(self, msg: dict) -> bool:
        """msg에 맞춘다. 처리 도중 더 새 메시지가 오면 False를 돌려주고 그것을 처리하게 한다."""
        recalled = msg.get("recalled") or []
        self._apply_recall(msg)
        if self._stale(msg):
            return False

        lock = read_lock()
        gone = recalled_versions({"recalled": recalled})
        old_items = {i["key"]: i for i in lock.get("items") or []}
        states: dict[str, dict] = {}
        keep: list[dict] = []
        need = [it for it in msg.get("items") or [] if not is_installed(it["key"], it["version"], it.get("sha256"))]
        if need and self.on_busy:
            self.on_busy(1)
        try:
            for it in msg.get("items") or []:
                key, version = it["key"], it["version"]
                if self._stale(msg):
                    return False
                row = {"key": key, "name": it.get("name"), "version": version, "state": "installed", "error": None}
                if it.get("min_agent") and _vkey(it["min_agent"]) > _vkey(self.version):
                    row.update(state="agent_too_old", error=f"PC Agent를 {it['min_agent']} 이상으로 업데이트하면 설치됩니다 → 트레이에서 업데이트를 확인하세요")
                    self._keep_old(old_items.get(key), gone, keep, row, version)
                else:
                    try:
                        if not is_installed(key, version, it.get("sha256")):
                            self._install(it)
                        keep.append({k: v for k, v in it.items() if k != "root"})
                    except Exception as e:  # 항목 하나 때문에 나머지가 멈추면 안 된다
                        log.warning("마켓 항목 설치 실패(%s %s): %s", key, version, e)
                        row.update(state="failed", error=_friendly_error(e))
                        self._keep_old(old_items.get(key), gone, keep, row, version)
                self._decorate(row)
                states[key] = row
        finally:
            if need and self.on_busy:
                self.on_busy(-1)
        with self._lock_write:
            with self._cv:  # 바꾸기 직전에 다시 본다: 늦게 끝난 동기화가 새 회수를 덮으면 안 된다
                if self._latest is not msg:
                    return False
            new_lock = {"rev": msg.get("rev"), "recall_rev": msg.get("recall_rev"), "enabled": msg.get("enabled", True), "gateway_on": False, "items": keep, "recalled": recalled}
            if lock.get("items") or keep or recalled or (market_root() / LOCK).exists():
                write_lock(new_lock)
        self._states = states
        removed = sorted(set(old_items) - {i["key"] for i in keep})
        left = self.sweep_projects() if removed else []
        incomplete = [*left, *self._collect_garbage(keep, gone)]
        self._incomplete = sorted({*self._incomplete, *incomplete})
        self.report(msg)
        return True

    def _keep_old(self, old: dict | None, gone: dict, keep: list, row: dict, wanted: str):
        """새 버전을 받지 못했으면 쓰던 버전을 그대로 둔다. 회수된 버전은 예외다."""
        if old and old.get("version") not in gone.get(old["key"], ()) and is_installed(old["key"], old["version"]):
            keep.append(old)
            row.update(version=old["version"], target=wanted)
        else:
            row.update(version=None, target=wanted)

    def _write_recall(self, msg: dict) -> tuple[bool, bool]:
        """lock.json에서 회수된 버전을 뺀다. (목록이 바뀜, 쓰던 항목이 빠짐). 이 뒤의 턴은 그 버전을 쓰지 않는다."""
        recalled = msg.get("recalled") or []
        gone = recalled_versions({"recalled": recalled})
        with self._lock_write:
            lock = read_lock()
            items = lock.get("items") or []
            kept = [i for i in items if i.get("version") not in gone.get(i.get("key"), ())]
            dropped = len(kept) != len(items)
            changed = dropped or (lock.get("recalled") or []) != recalled
            if changed and (items or recalled or lock):
                write_lock({**lock, "items": kept, "recalled": recalled, "recall_rev": msg.get("recall_rev")})
        return changed, dropped

    def _ack_recall(self, msg: dict):
        """받기를 기다리지 않고 회수를 적용했다고 답한다. 폴더는 작업자가 지우고 다 지운 뒤 다시 알린다. 그때까지 현황에는 "일부 남음"으로 보인다."""
        try:
            self._write_recall(msg)
            self._acked_recall = msg.get("recall_rev")
            gone = recalled_versions({"recalled": msg.get("recalled") or []})
            self._incomplete = sorted({*self._incomplete, *(k for k, vs in gone.items() if self._on_disk(k, vs))})
            self.report(msg, recall_only=True)
        except Exception:
            log.exception("마켓 회수 답장 실패")

    def _apply_recall(self, msg: dict):
        """회수를 먼저 적용하고 바로 알린다. 폴더를 지우기 전에 답한다: 프로젝트가 많거나 다른 대화가 폴더를 잡고 있으면 오래 걸린다."""
        recalled = msg.get("recalled") or []
        gone = recalled_versions({"recalled": recalled})
        changed, dropped = self._write_recall(msg)
        on_disk = [k for k, vs in gone.items() if self._on_disk(k, vs)]
        # 재시작하면 지우지 못한 목록을 잊는다. 프로젝트의 기록을 함께 봐야 남은 것을 '적용됨'으로 보고하지 않는다
        must_sweep = dropped or bool(on_disk) or bool(self._incomplete) or self._residue_left()
        if recalled or changed:
            self._acked_recall = msg.get("recall_rev")
            if must_sweep:
                self._incomplete = sorted({*self._incomplete, *on_disk})
            self.report(msg, recall_only=True)
        if not must_sweep:
            self._incomplete = []
            return
        left = self.sweep_projects()
        for key, versions in gone.items():
            for v in versions:
                try:
                    if not remove_tree(item_dir(key, v)):
                        left.append(key)
                except MarketError:
                    pass
        self._incomplete = sorted(set(left))
        self.report(msg, recall_only=True)

    def _on_disk(self, key: str, versions) -> bool:
        try:
            return any(item_dir(key, v).exists() for v in versions)
        except MarketError:
            return False

    def _install(self, it: dict):
        """받기 → sha256 → 임시 폴더에 풀며 경로 재검사 → 파일 목록 비교 → 이름 바꾸기 → .installed. 중간에 실패하면 흔적을 남기지 않는다."""
        key, version, pid = it["key"], it["version"], it["package_id"]
        target = item_dir(key, version)
        if not all(_SKILL_RE.match(str(s)) for s in it.get("skills") or []):
            raise MarketError(f"쓸 수 없는 스킬 이름이 있어 설치하지 않았습니다: {key} → 관리부서에 알려 주세요")
        root = market_root()
        (root / "downloads").mkdir(parents=True, exist_ok=True)
        tmp = root / "tmp" / f"{key}-{version}-{os.getpid()}"
        dl = root / "downloads" / f"{key}-{version}.zip"
        base = f"/api/market/packages/{pid}/versions/{version}"
        try:
            files = self.api.get(f"{base}/files")["files"]
            got = self.api.download(f"{base}/download", dl)
            if got != it.get("sha256"):
                raise MarketError("내려받은 파일이 등록본과 다릅니다 → 5분마다 저절로 다시 받습니다. 지금 받으려면 다시 시도를 누르고, 반복되면 관리부서에 알려 주세요")
            remove_tree(tmp)
            tmp.mkdir(parents=True, exist_ok=True)
            extract_checked(dl, tmp, files)
            target.parent.mkdir(parents=True, exist_ok=True)
            remove_tree(target)
            end = time.monotonic() + REPLACE_RETRY_SEC
            while True:
                try:
                    tmp.rename(target)
                    break
                except PermissionError:
                    if time.monotonic() >= end:
                        raise
                    time.sleep(0.1)
            (target / INSTALLED).write_text(it.get("sha256") or "", encoding="utf-8")
        finally:
            remove_tree(tmp)
            dl.unlink(missing_ok=True)

    def _collect_garbage(self, keep: list[dict], gone: dict) -> list[str]:
        """lock.json에 없는 항목을 지운다. 대화가 고정한 코드 항목을 위해 이전 버전은 조금 남긴다. 회수된 버전은 남기지 않는다."""
        items = market_root() / "items"
        left = []
        if not items.is_dir():
            return left
        active = {i["key"]: i["version"] for i in keep}
        with self._seen_lock:  # 살아 있는 대화가 쓰는 버전은 더 새 버전이 여럿 와도 남긴다
            held = {(k, ver) for row in self.holds.values() for k, ver in row.items()}
        for d in items.iterdir():
            if not d.is_dir():
                continue
            if d.name not in active:
                if not remove_tree(d):
                    left.append(d.name)
                continue
            old = sorted((v for v in d.iterdir() if v.is_dir() and v.name != active[d.name]), key=lambda v: _vkey(v.name), reverse=True)
            for i, v in enumerate(old):
                if v.name in gone.get(d.name, ()) or (i >= KEEP_OLD and (d.name, v.name) not in held):
                    if not remove_tree(v):
                        left.append(d.name)
        return left

    def report(self, msg: dict, recall_only: bool = False):
        rows = [] if recall_only else list(self._states.values())
        body = {"type": "market.state", "recall_rev": msg.get("recall_rev"), "incomplete": self._incomplete}
        if recall_only:
            lock = read_lock()  # 아직 전체를 맞추기 전이다. 서버의 목록을 빈 값으로 덮지 않게 지금 있는 것을 알린다
            rows = [self._decorate({"key": i["key"], "name": i.get("name"), "version": i["version"], "state": "installed", "error": None}) for i in lock.get("items") or []]
            body["rev"] = lock.get("rev")
        else:
            body["rev"] = msg.get("rev")
        body["items"] = rows
        self.send_state(body)

    # ---------- 프로젝트 폴더 ----------
    def known_projects(self) -> list[Path]:
        """이 PC의 프로젝트 폴더 전부: index.json의 projects·links와 작업 공간 안의 폴더."""
        found: dict[str, Path] = {}
        try:
            idx = json.loads((self.workspace_root / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            idx = {}
        for row in (idx.get("projects") or {}).values():
            folder = (row or {}).get("folder")
            if folder:
                p = self.workspace_root / folder
                found[os.path.normcase(str(p))] = p
        for folder in (idx.get("links") or {}).values():
            p = Path(folder)
            found[os.path.normcase(str(p))] = p
        try:
            for p in self.workspace_root.iterdir():
                if p.is_dir():
                    found[os.path.normcase(str(p))] = p
        except OSError:
            pass
        return [p for p in found.values() if p.is_dir()]

    def _residue_left(self) -> bool:
        from .context import has_market_residue

        return any(has_market_residue(d) for d in self.known_projects())

    def sweep_projects(self) -> list[str]:
        """제거·회수한 항목의 복사본을 모든 프로젝트에서 지운다. 지우지 못한 항목 이름을 돌려준다."""
        from .context import MARKET_FILE, sync_market

        left: list[str] = []
        for d in self.known_projects():
            if not (d / MARKET_FILE).exists():
                continue
            try:
                left += sync_market(d).get("incomplete") or []
            except Exception as e:
                log.warning("프로젝트의 마켓 스킬을 맞추지 못했습니다(%s): %s", d, e)
                left.append(d.name)
        return left

    # ---------- 턴 ----------
    def turn_notice(self, sid: str, project_dir_of, agent: str | None, project: tuple | None = None) -> str:
        """턴 시작 때 이 프로젝트의 스킬을 맞추고 AI에게 줄 안내를 만든다. 안내는 프롬프트에만 붙고 대화 기록에는 남지 않는다.
        마켓이 비어 있으면 폴더를 찾지도 파일을 건드리지도 않고 빈 글자를 돌려준다. project_dir_of: 폴더를 돌려주는 함수. project: (번호, 이름)."""
        lock = read_lock()
        with self._seen_lock:
            known = self.seen.get(sid)
            first = known is None
            known = dict(known or {})
            self.last_turn[sid] = time.monotonic()
        if not lock.get("items") and not lock.get("recalled") and not known:
            with self._seen_lock:
                self.seen[sid] = {}  # 이 대화가 있는 동안 설치하면 "새로 설치됨"으로 알리려고 기억한다
            return ""
        from .context import sync_market

        project_dir = project_dir_of()
        project_id, project_name = project or (None, "")
        project_name = project_name or project_dir.name  # 다시 이어 붙인 대화는 이름을 모를 수 있다. 폴더 이름이라도 알린다
        try:
            res = sync_market(project_dir)
        except Exception as e:  # 복사에 실패해도 턴은 진행하고 다음 턴에 다시 시도한다. 회수 안내는 아래에서 그대로 붙는다
            log.warning("마켓 스킬을 맞추지 못했습니다(%s): %s", project_dir, e)
            res = {"active": wanted_items(project_dir, lock)}
        where = os.path.normcase(str(project_dir))
        self._note_conflicts(where, project_id, project_name, res.get("conflicts") or {}, sid)
        active = {w["key"]: w for w in res.get("active") or []}
        newest = {i.get("key"): i.get("version") for i in lock.get("items") or []}
        self._note_pins(where, project_id, project_name, active, newest)
        with self._seen_lock:
            holds = self.holds.setdefault(sid, {})
            for key in list(holds):
                if key not in active:
                    del holds[key]
            for key, w in active.items():
                # 처음부터 그 버전을 쓰던 대화만 붙든다. 다른 대화 때문에 옛 버전을 받은 새 대화까지 붙들면 프로젝트의 CLI가 다 죽기 전에는 새 버전이 오지 않는다
                if w.get("code") and (w["version"] == newest.get(key) or holds.get(key) == w["version"]):
                    holds[key] = w["version"]
                else:
                    holds.pop(key, None)
        rel = SKILL_DIR_OF.get(agent or "", ".claude/skills")
        lines = []
        for key, w in active.items():
            paths = "\n".join(f"  스킬: {(project_dir / rel / s / 'SKILL.md').as_posix()}" for s in w.get("skills") or [])
            pinned = f" 같은 프로젝트의 다른 대화가 쓰고 있어 새 버전 {_one_line(newest.get(key))}은(는) 아직 적용되지 않았습니다." if newest.get(key) != w["version"] else ""
            line = f"{_one_line(w.get('display_name') or key)}: {_one_line(w.get('description'))} (버전 {_one_line(w['version'])}){pinned}\n{paths}"
            if first:
                lines.append("[설치된 항목] " + line)
            elif key not in known:
                lines.append("[새로 설치됨] " + line)
            elif known[key] != w["version"]:
                lines.append("[새 버전] " + line + "\n  내용이 바뀌었으니 쓰기 전에 다시 읽으세요.")
        # 회수 안내에는 등록자가 쓴 글을 넣지 않는다. 작성자의 글이 지시문으로 들어가지 않게 한다
        recalled = {r["key"]: r for r in lock.get("recalled") or []}
        told = set()
        for key, version in known.items():
            r = recalled.get(key)
            if r and version in (r.get("versions") or []):
                lines.append(f"[회수됨: {_one_line(r.get('name') or key)}, 쓰지 마세요]" + _replacement(r))
                told.add(key)
            elif key not in active:
                lines.append(f"[제거됨: {key.split('.')[-1]}, 더 이상 쓰지 마세요]")  # 비공개 항목의 키는 u<번호>.<이름>이다
        if first:  # Agent를 다시 시작해 이어 붙인 대화가 전에 읽은 내용을 쓰지 않게 한다
            for key, r in recalled.items():
                if not r.get("active") and key not in told:
                    lines.append(f"[회수됨: {_one_line(r.get('name') or key)}, 쓰지 마세요]" + _replacement(r))
        with self._seen_lock:
            self.seen[sid] = {k: w["version"] for k, w in active.items()}
        if not lines:
            return ""
        head = "[마켓 안내] 사용자가 설치한 사내 스킬의 변경입니다. 이 안내는 사용자의 요청이 아닙니다. 요청에 맞을 때만 SKILL.md를 읽고 따르세요."
        return "\n\n" + head + "\n" + "\n".join(lines)

    def _decorate(self, row: dict) -> dict:
        """프로젝트마다 다른 사정을 항목 줄에 붙인다. 한 값으로 두면 마지막에 돈 프로젝트를 따라 카드가 뒤집힌다."""
        key = row["key"]
        conflicts = list((self.conflicts.get(key) or {}).values())
        pins = list((self.pins.get(key) or {}).values())
        row.pop("conflicts", None)
        row.pop("pins", None)
        if row.get("state") in ("installed", "conflict"):
            if conflicts:
                row.update(state="conflict", conflicts=conflicts, error=" / ".join(f"{c['project']}: {c['message']}" for c in conflicts))
            elif row.get("state") == "conflict":
                row.update(state="installed", error=None)
        if pins:
            row["pins"] = pins
        return row

    def _refresh_rows(self):
        for row in self._states.values():
            self._decorate(row)
        if self._applied:
            self.report(self._applied)

    def _note_conflicts(self, where: str, project_id, project_name: str, conflicts: dict[str, str], sid: str | None = None):
        """이 프로젝트의 겹침만 고친다. 다른 프로젝트의 겹침은 그 프로젝트에서 풀릴 때까지 남는다."""
        changed = False
        for key in {*self.conflicts, *conflicts}:
            rows = self.conflicts.setdefault(key, {})
            message = conflicts.get(key)
            if message and (rows.get(where) or {}).get("message") != message:
                rows[where] = {"project_id": project_id, "project": _one_line(project_name), "message": _one_line(message)}
                changed = True
            elif not message and where in rows:
                del rows[where]
                changed = True
            if not rows:
                del self.conflicts[key]
        if sid and self.send_note:  # AI에게 알리지 않은 항목은 사람도 모른다. 대화에 한 번 남긴다
            told = self.told.setdefault(sid, set())
            for key, message in conflicts.items():
                if key not in told:
                    told.add(key)
                    self.send_note(sid, f"마켓 항목 '{key.split('.')[-1]}'을(를) 이 프로젝트에서 쓰지 못합니다. {_one_line(message)}")
        if changed:
            self._refresh_rows()

    def _note_pins(self, where: str, project_id, project_name: str, active: dict, newest: dict):
        """이 프로젝트가 옛 버전을 쓰는지 알린다. 카드에 새 버전이 보이는데 AI가 옛 파일을 읽으면 고친 계산이 적용된 줄 알게 된다."""
        changed = False
        for key in {*self.pins, *active}:
            rows = self.pins.setdefault(key, {})
            w = active.get(key)
            if w and newest.get(key) and w["version"] != newest[key]:
                if (rows.get(where) or {}).get("version") != w["version"]:
                    rows[where] = {"project_id": project_id, "project": _one_line(project_name), "version": w["version"]}
                    changed = True
            elif where in rows:
                del rows[where]
                changed = True
            if not rows:
                del self.pins[key]
        if changed:
            self._refresh_rows()

    def touch(self, sid: str):
        with self._seen_lock:
            self.last_turn[sid] = time.monotonic()

    def forget_session(self, sid: str):
        with self._seen_lock:
            self.seen.pop(sid, None)
            self.holds.pop(sid, None)
            self.last_turn.pop(sid, None)
            self.told.pop(sid, None)
            had_pins = bool(self.pins)
            self.pins = {}  # 붙들던 대화가 끝났을 수 있다. 아직 붙든 대화가 있으면 그 프로젝트의 다음 턴이 다시 알린다
        if had_pins:
            self._refresh_rows()

    def code_versions(self, sids, active=()) -> dict[str, str]:
        """대화들이 붙들고 있는 코드 항목의 버전. 같은 폴더를 함께 쓰므로 가장 낮은 버전으로 맞춘다.
        한 시간 넘게 요청이 없는 대화는 붙들지 않는다. active: 지금 일하는 대화(요청 중이거나 케이스가 도는 중)."""
        lock = read_lock()
        code = {i["key"] for i in lock.get("items") or [] if i.get("code")}
        out: dict[str, str] = {}
        now = time.monotonic()
        with self._seen_lock:
            for sid in sids:
                if sid not in active and now - self.last_turn.get(sid, now) > PIN_IDLE_SEC:
                    continue
                for key, version in (self.holds.get(sid) or {}).items():
                    if key in code and (key not in out or _vkey(version) < _vkey(out[key])):
                        out[key] = version
        return out

    def rev(self) -> tuple[str | None, str | None]:
        try:
            lock = read_lock()
        except MarketError as e:  # hello를 막으면 연결이 끊긴다. 모른다고 알리면 서버가 목록을 다시 보낸다
            log.warning("%s", e)
            return None, None
        return lock.get("rev"), lock.get("recall_rev")


def _one_line(text) -> str:
    """이름표에 줄바꿈이 남으면 프롬프트 안내 줄을 흉내 낼 수 있고, 보이지 않는 글자는 사람이 못 보는 지시가 된다. 표시 구간 글자는 규칙 파일의 범위를 바꾼다.
    서버가 이미 지우지만 PC가 마지막 관문이다."""
    s = "".join(c for c in str(text or "") if c in "\t\n\r" or unicodedata.category(c) not in ("Cf", "Cc", "Co", "Cs"))
    while "<!--" in s or "-->" in s:
        s = s.replace("<!--", "").replace("-->", "")
    return " ".join(s.split())


def _friendly_error(e: Exception) -> str:
    """화면에 그대로 보인다. 원인과 할 일이 적힌 말은 그대로 쓰고, 그 밖의 오류는 할 일을 붙인다."""
    text = str(e)
    if isinstance(e, MarketError) or _FRIENDLY.search(text):
        return re.sub(r"^서버 오류 \d+: ", "", text)
    return f"받지 못했습니다({e.__class__.__name__}) → 5분마다 저절로 다시 받습니다. 지금 받으려면 다시 시도를 누르고, 반복되면 관리부서에 알려 주세요"


def _replacement(r: dict) -> str:
    return f" 대신 {_one_line(r['replacement'])}을(를) 쓸 수 있습니다." if r.get("replacement") else ""


def _vkey(v: str | None) -> tuple:
    try:
        return tuple(int(x) for x in (v or "0").split("."))
    except ValueError:
        return (0,)


def gateway_on() -> bool:
    """2단계(기능의 입구) 자리. 실제 CLI 시험을 통과하기 전까지 언제나 꺼져 있어, MCP 목록은 오늘과 같은 4개다."""
    return False
