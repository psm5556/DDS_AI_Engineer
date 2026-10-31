"""사용자 PC 로컬 데이터 규칙 (docs/local-data.md).

%USERPROFILE%\\DDS_Workspace\\
  index.json                          내 PC의 전체 프로젝트 ↔ 서버 연결 색인
  .dds-lock                           작업 공간을 고치는 동안 잡는 잠금(Agent·MCP 프로세스 사이)
  P0012_<프로젝트이름>\\
    project.json                      프로젝트 ID·이름·서버 주소 (서버와 연결 고리)
    inputs\\ scripts\\ runs\\ live\\ reports\\
    studies\\S003_<스터디이름>\\
      study.json  cases.csv
      cases\\C0001_doe\\ C0021_bo\\ C0031_al\\   ← 케이스마다 case.json·params.json·results.json·run.log·images\\·fields\\·툴 산출물

- 폴더 이름의 번호(P/S/C + 서버 ID)가 연결의 기준이다. 이름 부분은 사람이 알아보기 위한 것이라 바뀌어도 된다(번호로 찾는다).
- 프로젝트 폴더는 항상 P0012_<이름>이고 프로젝트 이름을 따라간다. 번호만 있는 폴더는 만들지 않는다.
  Windows는 프로세스가 cwd를 둔 폴더의 이름을 바꾸지 못한다(WinError 5·32). CLI를 끄지 않으므로 그동안은 새 이름을 접합으로 보여 주고
  실제 이름은 안전할 때 바꾼다. 절차는 sync_name, 기록은 docs/local-data.md '폴더 이름'.
- 사용자가 프로젝트마다 작업 폴더를 직접 정할 수 있다(D:\\Work\\... 등). index.json의 links(uid → 절대 경로)에
  이 PC 기준으로 기록하고, 그 폴더 안에도 같은 구조(project.json·inputs·studies…)를 만든다. 이 폴더의 이름은 바꾸지 않는다.
- 원본(inputs)은 읽기 전용으로 다루고, 케이스는 복사본으로 실행한다.
"""
from __future__ import annotations

import csv
import errno
import filecmp
import json
import logging
import os
import re
import stat
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .context import _set_hidden, dds_items, dds_owned_files, forget_git_block, git_hide_aliases

log = logging.getLogger("dds_agent.workspace")

SUBDIRS = ("inputs", "scripts", "runs", "studies", "live", "reports")
MOVING = "dds-moving"  # 이름을 바꾸는 중의 임시 이름. P0012_로 시작하게 두어 도중에 끊겨도 번호로 다시 찾는다
PROBE = "dds-probe"
ALIAS_KEEP = 8  # 옛 이름 접합을 남기는 수. CAD 파일·스크립트에 옛 경로가 적혀 있을 수 있어 넉넉히 둔다
PATH_RESERVE = 110  # 프로젝트 폴더 아래 studies\S003_…\cases\C0001_…\fields\파일 이 쓰는 길이
SLUG_MAX, SLUG_MIN = 24, 8
RENAME_RETRY = (0.05, 0.15, 0.3, None)  # 보안 프로그램이 잠깐 잡을 수 있다(측정 400ms). 더 잡고 있으면 다음 기회에 한다
MERGE_NOTE_DAYS = 14
LOCK_FILE = ".dds-lock"
LOCK_WAIT_SEC = 10
_lock = threading.Lock()
_NOT_NAME = re.compile(r'[\\/:*?"<>|\s…&%^!\x00-\x1f\ud800-\udfff]+')  # 금지 문자 + 배치 파일 명령줄을 깨는 문자


def slug(name: str | None, limit: int = SLUG_MAX) -> str:
    """폴더 이름용: 금지 문자·공백·말줄임표 제거, 짧게(Windows 경로 길이 260자 제한 대비).
    끝의 점·공백은 Windows가 지워 다른 폴더로 착각하므로 뺀다. CON 같은 예약어는 P0012_ 뒤에 붙어 문제가 없다."""
    s = _NOT_NAME.sub("-", (name or "").strip())
    return s[:limit].strip("-.") or "untitled"


def _usable(name: str | None) -> bool:
    return bool(_NOT_NAME.sub("-", (name or "").strip()).strip("-."))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json(path: Path, data: dict):
    # MCP가 index.json을 읽는 순간에는 바꿔 넣기가 거부될 수 있다(5·32). 임시 파일은 프로세스마다 따로 둔다
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for wait in RENAME_RETRY:
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if wait is None:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            if os.name == "nt":  # 폴더 속성의 '읽기 전용'을 파일에 적용하면 바꿔 넣기가 거부된다. 우리 기록 파일이라 풀어도 된다
                try:
                    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
            time.sleep(wait)


# ---------- 접합(junction): 잠긴 폴더에 다른 이름을 주는 유일한 방법이다. POSIX에서는 심볼릭 링크로 같은 일을 한다 ----------
def _is_alias(path: Path) -> bool:
    return os.path.isjunction(path) or os.path.islink(path)


def _make_alias(target: Path, alias: Path):
    if os.name == "nt":
        import _winapi

        existed = os.path.lexists(alias)
        try:
            _winapi.CreateJunction(str(target), str(alias))
        except OSError:
            # CreateJunction은 폴더를 먼저 만들고, 접합 설정이 거부되면(네트워크 드라이브·보안 프로그램) 빈 폴더를 남긴다.
            # 남겨 두면 다음 시도가 그 이름을 남의 폴더로 보고 다른 이름을 고른다
            if not existed:
                try:
                    os.rmdir(alias)
                except OSError:
                    pass
            raise
    else:
        os.symlink(target, alias, target_is_directory=True)


def _drop_alias(alias: Path):
    """접합만 지운다. 대상 폴더는 그대로이고, 그 안에 프로세스가 있어도 된다(측정)."""
    if os.path.isjunction(alias):
        os.rmdir(alias)
    elif os.path.islink(alias):
        os.unlink(alias)


def _empty_dir(path: Path) -> bool:
    try:
        return not _is_alias(path) and path.is_dir() and not any(os.scandir(path))
    except OSError:
        return False


def _number(name: str) -> int | None:
    m = re.match(r"(?:P|project_)0*(\d+)", name)
    return int(m.group(1)) if m else None


def _plain_names(names) -> list[str]:
    """project.json에 적힌 이름 중 폴더 이름 하나인 것만. 사람이 고칠 수 있는 파일이라 경로가 들어와도 밖으로 나가지 않게 한다."""
    return [n for n in names or [] if isinstance(n, str) and n and n == Path(n).name and n not in (".", "..")]


def _same_path(a, b) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _same_name(a: str, b: str) -> bool:
    return os.path.normcase(a) == os.path.normcase(b)


def _rename_retry(src: Path, dst: Path) -> bool:
    for wait in RENAME_RETRY:
        try:
            os.rename(src, dst)
            return True
        except PermissionError as e:  # 5·32: 누가 쓰고 있다. 실패해도 잃는 것이 없다
            if wait is None:
                log.info("폴더 이름을 지금은 바꿀 수 없습니다(%s): %s", getattr(e, "winerror", e.errno), src.name)
                return False
            time.sleep(wait)
        except OSError as e:
            log.warning("폴더 이름 바꾸기 실패: %s → %s: %s", src.name, dst.name, e)
            return False
    return False


def _unlink(path: Path):
    """읽기 전용 파일도 지운다. 지워도 된다고 판단한 뒤에만 부른다."""
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink()


def _merge_tree(src: Path, dst: Path, skip: set[str], rel: str = "") -> tuple[int, int, list[str]]:
    """src의 파일을 dst로 옮긴다. 덮어쓰지 않는다. (옮긴 수, 내용이 같아 지운 수, 남긴 상대 경로)를 돌려준다.
    skip은 파일 단위다: 같은 폴더 안의 사용자 파일은 옮겨야 하므로, 건너뛸 파일이 든 폴더는 통째로 옮기지 않고 안으로 내려간다."""
    moved, same, left = 0, 0, []
    for e in list(os.scandir(src)):
        r, s, t = rel + e.name, Path(e.path), dst / e.name
        if r in skip:
            continue
        try:
            is_dir = e.is_dir(follow_symlinks=False) and not _is_alias(s)
            if is_dir and (t.is_dir() or any(x.startswith(r + "/") for x in skip)):
                m, c, l = _merge_tree(s, t, skip, r + "/")
                moved, same, left = moved + m, same + c, left + l
                try:
                    os.rmdir(s)
                except OSError:
                    pass
            elif not os.path.lexists(t):
                t.parent.mkdir(parents=True, exist_ok=True)
                os.rename(s, t)
                moved += sum(len(f) for _, _, f in os.walk(t)) if is_dir else 1
            elif not is_dir and t.is_file() and filecmp.cmp(s, t, shallow=False):
                _unlink(s)
                same += 1
            else:
                left.append(r)
        except OSError as err:
            log.info("옮기지 못했습니다: %s: %s", s, err)
            left.append(r)
    return moved, same, left


def _read_json(path: Path) -> dict:
    """없는 파일만 빈 기록이다. 못 읽은 것을 빈 기록으로 보면 index.json의 links와 행을 잃고, 남의 프로젝트 폴더를 uid 없는 폴더로 보고 차지한다.
    다른 프로세스가 파일을 바꿔 넣는 순간이나 보안 검사 중에는 읽기가 거부되므로(측정: 5·32) 잠깐 기다리고, 그래도 안 되면 오류를 올린다."""
    for wait in RENAME_RETRY:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, NotADirectoryError):
            return {}
        except ValueError as e:
            log.warning("기록 파일이 망가졌습니다: %s: %s", path, e)
            return {}
        except PermissionError:
            if wait is None:
                raise
            time.sleep(wait)
    return {}


def _os_lock(fd: int) -> bool | None:
    """True: 잡았다. False: 다른 프로세스가 잡고 있다. None: 이 드라이브는 잠금을 지원하지 않는다."""
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError as e:
        return False if e.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) else None


@contextmanager
def _guard(root: Path):
    """작업 공간을 고치는 동안 잡는다. Agent와 MCP가 서로 다른 프로세스라 스레드 잠금으로는 부족하다:
    index.json을 동시에 고치면 links를 잃고, MCP의 project_dir이 이름을 바꾸는 중인 임시 이름을 다시 만든다.
    OS 잠금이라 프로세스가 죽으면 저절로 풀린다. 오래 못 잡으면 일을 막지 않고 잠금 없이 간다(지금까지와 같다)."""
    with _lock:
        fd = None
        try:
            path = root / LOCK_FILE
            fresh = not path.exists()
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            if fresh:
                _set_hidden(path, True)
            end = time.monotonic() + LOCK_WAIT_SEC
            while (held := _os_lock(fd)) is False and time.monotonic() < end:
                time.sleep(0.02)
            if not held:
                log.warning("작업 공간 잠금을 잡지 못해 잠금 없이 진행합니다: %s", root)
        except OSError as e:
            log.info("작업 공간 잠금 파일을 쓰지 못했습니다: %s", e)
        try:
            yield
        finally:
            if fd is not None:
                os.close(fd)  # 닫으면 잠금도 풀린다


class Workspace:
    def __init__(self, root: Path, server_url: str = ""):
        self.root = Path(root)
        self.server_url = server_url
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- 프로젝트 ----------
    def _candidates(self, project_id: int) -> list[Path]:
        """이 번호의 프로젝트 폴더. 우리가 만든 접합은 같은 폴더의 다른 이름일 뿐이라 후보가 아니다.
        확인 이름(dds-probe)은 번호 폴더가 잠깐 쓰는 이름이지 프로젝트 폴더가 아니다."""
        prefix = f"P{project_id:04d}"
        cands = sorted(self.root.glob(f"{prefix}_*")) + sorted(self.root.glob(prefix))
        legacy = self.root / f"project_{project_id}"
        if legacy.exists():
            cands.append(legacy)
        return [c for c in cands if c.name != f"{prefix}_{PROBE}" and not self._our_alias(c) and c.is_dir()]

    def _our_alias(self, p: Path) -> bool:
        """이 코드가 만드는 접합은 작업 공간 바로 아래의 같은 번호 폴더를 가리킨다(끊겼어도 대상 경로는 남아 있다).
        사용자가 프로젝트 폴더를 다른 드라이브로 옮기고 이어 둔 링크는 프로젝트 폴더 자체다. 지우거나 건너뛰면 프로젝트가 빈 것처럼 보인다."""
        if not _is_alias(p):
            return False
        real = Path(os.path.realpath(p))
        return (os.path.normcase(str(real.parent)) == os.path.normcase(os.path.realpath(self.root))
                and _number(real.name) == _number(p.name))

    def _user_link(self, p: Path) -> bool:
        return _is_alias(p) and not self._our_alias(p)

    def _pick_own(self, project_id: int, rows: list[tuple[Path, dict]], row: dict) -> Path:
        """같은 uid의 폴더가 여럿이면(탐색기로 복사한 백업) 진짜를 고른다. 이름을 바꾸면 이름 순서가 달라지므로 순서로 고르면 복사본으로 넘어간다.
        색인이 가리키는 폴더가 진짜이고, 색인이 없으면 project.json의 folder가 자기 이름인 폴더다(복사본은 원본의 이름을 들고 있다)."""
        moving = f"P{project_id:04d}_{MOVING}"

        def rank(item: tuple[Path, dict]) -> int:
            c, i = item
            plan = i.get("moving") if isinstance(i.get("moving"), dict) else {}
            if c.name == moving:  # 이름을 바꾸다 끊긴 폴더. 이 이름은 그때만 쓴다
                return 0
            if c.name == row.get("folder"):
                return 1
            if plan.get("to") == c.name or i.get("folder") == c.name:
                return 2
            return 3

        return min(rows, key=rank)[0]

    def _adoptable(self, info: dict, name: str | None) -> bool:
        """uid 없는 폴더를 이 프로젝트의 것으로 볼까. 이름이 비었으면(MCP가 이름을 모른 채 만든 번호 폴더) 같은 서버일 때 받아들인다.
        다른 서버의 번호 폴더는 번호가 같아도 다른 프로젝트일 수 있다."""
        if info.get("uid") or info.get("server") not in (None, "", self.server_url):
            return False
        return not name or not info.get("name") or info.get("name") == name

    def _find(self, project_id: int, name: str | None = None, uid: str | None = None) -> Path | None:
        """폴더를 찾기만 한다. 사용자가 직접 정했으면 그 폴더 → uid가 같은 폴더 → 받아들일 수 있는 uid 없는 폴더.
        uid를 모르면(MCP 툴) 이 번호로 가장 최근에 쓴 폴더(index.json)를 쓴다."""
        linked = self._links().get(uid) if uid else None
        if linked:
            return Path(linked)
        infos = [(c, _read_json(c / "project.json")) for c in self._candidates(project_id)]
        row = _read_json(self.root / "index.json").get("projects", {}).get(str(project_id)) or {}
        if uid:
            mine = [(c, i) for c, i in infos if i.get("uid") == uid]
            if mine:
                return self._pick_own(project_id, mine, row if row.get("uid") == uid else {})
            return next((c for c, i in infos if self._adoptable(i, name)), None)
        cur = row.get("folder")
        cur_dir = self.root / cur if cur else None  # 직접 정한 폴더는 절대 경로로 들어 있다(root / 절대경로 = 그 경로)
        if cur_dir and cur_dir.is_dir():
            return Path(os.path.realpath(cur_dir)) if self._our_alias(cur_dir) else cur_dir
        return self._pick_own(project_id, infos, {}) if infos else None

    def find(self, project_id: int) -> Path | None:
        with _guard(self.root):
            return self._find(project_id)

    def _fit(self, prefix: str) -> int:
        """폴더 이름에 쓸 수 있는 길이. 작업 공간 경로가 길면 줄여 그 아래 파일이 260자를 넘지 않게 한다."""
        room = 259 - len(str(self.root)) - 1 - len(prefix) - 1 - PATH_RESERVE
        return max(SLUG_MIN, min(SLUG_MAX, room))

    def _pick_name(self, project_id: int, name: str | None, uid: str | None, d: Path | None = None, own: tuple = ()) -> str | None:
        """새 이름을 고른다. 다른 프로젝트의 폴더를 덮지 않게 uid를 붙인다(서버 DB를 새로 만들면 번호·이름이 겹칠 수 있다)."""
        prefix = f"P{project_id:04d}"
        tail_name = slug(name, self._fit(prefix))
        base = f"{prefix}_{tail_name}" + ("_" if tail_name in (MOVING, PROBE) else "")  # 임시 이름과 겹치면 이름을 바꾸는 중으로 착각한다
        tails = ["", f"_{uid[:6]}", *(f"_{uid[:6]}-{n}" for n in range(2, 6))] if uid else [""]
        for tail in tails:
            cand = base + tail
            if d is not None and _same_name(cand, d.name):
                return cand
            p = self.root / cand
            if (not os.path.lexists(p) or (cand in own and (_is_alias(p) or _empty_dir(p)))
                    or (d is not None and _is_alias(p) and _same_path(p, d))):
                return cand
        return None

    def project_dir(self, project_id: int, name: str | None = None, uid: str | None = None) -> Path:
        """P{id:04d}_<이름> 폴더를 찾고, 없으면 만든다. 예전 규칙(project_{id})도 그대로 쓴다.
        uid(서버 프로젝트 고유값)를 주면 project.json의 uid로 '이 프로젝트의 폴더'인지 확인한다.
        서버 DB를 새로 만들어 번호가 겹쳐도 다른 프로젝트의 폴더를 쓰지 않는다.
        name으로 이름을 바꾸지 않는다: 미리 띄우기·대기 중인 케이스·세션 기록은 옛 이름을 들고 있어 폴더를 되돌리게 된다.
        이름을 바꾸는 곳은 sync_name 하나다."""
        with _guard(self.root):
            d = self._find(project_id, name, uid)
            fresh = d is None
            if fresh:
                prefix = f"P{project_id:04d}"
                for p in [*self.root.glob(f"{prefix}_*"), self.root / prefix]:
                    if not _is_alias(p) or p.exists():
                        continue
                    if not self._our_alias(p):  # 사용자가 이어 둔 드라이브가 빠졌다. 빈 폴더로 대신하면 돌아왔을 때 링크가 없어진다
                        raise FileNotFoundError(f"프로젝트 폴더 '{p.name}'가 가리키는 '{os.path.realpath(p)}'에 연결할 수 없습니다 → "
                                                "외장 드라이브나 네트워크 연결을 확인한 뒤 다시 시도하세요.")
                    _drop_alias(p)  # 실제 폴더가 지워졌다. 끊긴 접합이 새 폴더의 이름을 막지 않게 한다
                picked = self._pick_name(project_id, name, uid) or f"{prefix}_{slug(name, self._fit(prefix))}_{uid or ''}"[:80]
                d = self.root / picked
            for sub in SUBDIRS:
                (d / sub).mkdir(parents=True, exist_ok=True)
            info = _read_json(d / "project.json")
            if (not info or (uid and info.get("uid") != uid) or (name and not info.get("name"))
                    or info.get("project_id") != project_id):
                info.update({"project_id": project_id, "uid": uid or info.get("uid"), "name": info.get("name") or name or "",
                             "server": self.server_url, "folder": d.name, "created": info.get("created", _now()),
                             "updated": _now()})
                if fresh and _usable(name):
                    info["folder_for"] = name
                _write_json(d / "project.json", info)
            if uid:
                self._index(project_id, info, d)
            return d

    def _index(self, project_id: int, info: dict, d: Path):
        folder = d.name if self._in_root(d) else str(d)  # 기본 위치면 폴더 이름, 직접 정한 폴더면 절대 경로
        row = {"name": info.get("name"), "folder": folder, "uid": info.get("uid"), "visible": info.get("visible"),
               "pending": bool(info.get("pending") or info.get("stray_wait"))}
        try:
            idx = _read_json(self.root / "index.json")
            cur = idx.get("projects", {}).get(str(project_id), {})
            if all(cur.get(k) == v for k, v in row.items()):
                return
            idx.setdefault("projects", {})[str(project_id)] = {**row, "server": self.server_url, "updated": _now()}
            idx["root"] = str(self.root)
            _write_json(self.root / "index.json", idx)
        except OSError as e:  # 색인은 다음 호출이 다시 맞춘다. 파일은 그대로이므로 links를 잃지 않고, 일을 막지도 않는다
            log.warning("index.json을 고치지 못했습니다: %s", e)

    def _in_root(self, d: Path) -> bool:
        return os.path.normcase(os.path.abspath(d.parent)) == os.path.normcase(os.path.abspath(self.root))

    # ---------- 폴더 이름 ----------
    def pending_projects(self) -> list[tuple[int, str | None]]:
        """이름 바꾸기를 기다리는 프로젝트. 디스크에 남아 Agent를 다시 시작해도 이어 간다."""
        rows = _read_json(self.root / "index.json").get("projects", {})
        out = []
        for k, r in rows.items():
            if isinstance(r, dict) and r.get("pending") and str(k).isdigit():
                out.append((int(k), r.get("uid")))
        for mark in (MOVING, PROBE):  # 이름을 바꾸다 끊긴 폴더는 색인에 표시가 없을 수 있다
            for p in self.root.glob(f"P*_{mark}"):
                m = re.fullmatch(rf"P(\d+)_{mark}", p.name)
                if m and int(m.group(1)) not in {i for i, _ in out}:
                    pid = int(m.group(1))
                    row = rows.get(str(pid)) if isinstance(rows.get(str(pid)), dict) else {}
                    out.append((pid, row.get("uid") or _read_json(p / "project.json").get("uid")))
        return out

    def sync_name(self, project_id: int, uid: str | None, name: str | None, may_move=False) -> dict:
        """폴더 이름을 프로젝트 이름에 맞춘다. 같은 값으로 몇 번을 불러도 결과가 같다. 서버가 막 알려 준 이름만 넘긴다(name=None이면 저장된 이름).
        may_move: 지금 실제 이름을 바꿔도 되는가(bool 또는 함수). 살아 있는 세션·해석이 없을 때만 참이다. rename이 성공하는지로 판단하면 안 된다:
        Codex·Gemini는 폴더를 잠그지 않아 성공하지만 cwd를 메모리에 두어 그 세션이 끊긴다.
        실제 이름을 못 바꾸면 새 이름을 접합으로 보여 주고 실제 폴더를 숨긴다. 살아 있는 세션의 경로는 바뀌지 않는다."""
        with _guard(self.root):
            linked = self._links().get(uid) if uid else None
            d = self._find(project_id, name, uid)
            if d is None or not d.is_dir():
                return {"found": False}
            info = _read_json(d / "project.json")
            before = dict(info)
            names_before = self._alias_names(before)
            if _usable(name):
                info["name"] = name
            if uid:
                info["uid"] = uid
            info.setdefault("project_id", project_id)
            info.setdefault("server", self.server_url)
            if linked or not self._in_root(d) or self._user_link(d):  # 사용자가 고른 폴더·이어 둔 링크는 이름을 바꾸지 않는다
                res = {"found": True, "custom": True, "path": str(d), "pending": False}
            else:
                d, res = self._settle(project_id, uid, d, info, may_move)
            if info != before:
                info["updated"] = _now()
                _write_json(d / "project.json", info)
            if uid:
                self._index(project_id, info, d)
            if self._alias_names(info) != names_before:
                try:
                    git_hide_aliases(self.root, project_id, self._alias_names(info))
                except (OSError, ValueError) as e:
                    log.info("git 제외 목록을 고치지 못했습니다: %s", e)
            return res

    @staticmethod
    def _alias_names(info: dict) -> list[str]:
        """접합으로 있는 이름들. 실제 폴더 이름은 뺀다."""
        names = {*_plain_names(info.get("aliases")), *_plain_names([info.get("visible")])}
        return sorted(names - {info.get("folder")})

    def _settle(self, project_id: int, uid: str | None, d: Path, info: dict, may_move) -> tuple[Path, dict]:
        prefix = f"P{project_id:04d}"
        name = info.get("name") or ""
        moving = d.name == f"{prefix}_{MOVING}"
        if not _plain_names([info.get("visible")]):  # 망가진 기록으로 작업 공간 밖을 건드리지 않는다
            info["visible"] = None
        plan = info.pop("moving", None)
        if isinstance(plan, dict) and plan.get("to") and _same_name(plan["to"], d.name) and _plain_names([plan.get("from")]) and not moving:
            # 실제 이름은 바뀌었는데 뒷정리 전에 Agent가 죽었다. 사람이 바꾼 이름으로 보면 옛 이름 접합이 끝내 생기지 않는다
            self._finish_move(d, d.name, plan["from"], info.get("visible") or plan["from"], info, plan.get("for") or name)
        visible = info.get("visible") or d.name
        # 사람이 탐색기에서 실제 폴더 이름을 바꿨다: 접합은 끊겼고, 사람이 지은 이름을 존중한다(프로젝트 이름이 다시 바뀌면 따라간다)
        if info.get("folder_for") is not None and info.get("folder") not in (None, d.name) and not moving:
            alias = self.root / visible
            if _is_alias(alias) and not alias.exists():
                _drop_alias(alias)
            if info.pop("hid_real", False):
                _set_hidden(d, False)
            info.update(folder=d.name, visible=None, pending=False)
            info.pop("want", None)
            visible = d.name
        if not moving:
            info["folder"] = d.name
        can_move = may_move() if callable(may_move) else bool(may_move)
        if can_move:
            self._absorb_strays(project_id, uid, d, info, name)
        else:
            self._note_strays(project_id, uid, d, info, name)
        if not _usable(name):  # 쓸 글자가 없는 이름: 지금 폴더를 둔다
            if moving and can_move:
                old = info.get("folder") or prefix
                if not os.path.lexists(self.root / old) and _rename_retry(d, self.root / old):
                    d = self.root / old
            return d, self._state(d, info)
        # 접합을 못 만들어 기다리는 이름은 다시 고를 때도 같은 이름이어야 한다
        own = (*_plain_names(info.get("aliases")), visible, *_plain_names([info.get("want")]))
        if moving and info.get("visible"):  # 이름을 바꾸다 끊겼다. 정해 둔 이름으로 마저 간다
            want = visible
        elif info.get("folder_for") == name and not moving:  # 이 이름으로 이미 정했다. 사람이 지은 이름·uid 접미사를 그대로 둔다
            want = visible
        else:
            want = self._pick_name(project_id, name, uid, d, own)
        if want is None:
            log.warning("폴더 이름을 고르지 못했습니다: %s", name)
            return d, self._state(d, info)
        if want == d.name:
            self._show_real(d, visible, info, name)
            return d, self._state(d, info)
        if can_move:
            new = self._move(d, want, visible, info, name, prefix)
            if new is not None:
                if info.get("stray_names"):  # 바꾸는 사이에 옛 이름으로 진짜 폴더가 생겼다
                    self._absorb_strays(project_id, uid, new, info, name)
                return new, {**self._state(new, info), "moved": True, "old": info.get("moved_from")}
        if not _same_name(want, d.name):
            self._show_as(d, want, visible, info, name)
        else:  # 대소문자만 다르면 접합을 만들 수 없다(이미 있는 이름으로 본다). 실제 이름만 바꿀 수 있다
            info.update(pending=True, want=want)
        return d, self._state(d, info)

    def _state(self, d: Path, info: dict) -> dict:
        return {"found": True, "path": str(d), "visible": info.get("visible") or d.name, "pending": bool(info.get("pending") or info.get("stray_wait"))}

    def _hide_old_visible(self, visible: str, keep: str, d: Path, info: dict):
        """앞서 보여 주던 이름을 숨긴다. 지우지 않는다: 그 이름으로 연 탐색기 창·옛 경로가 끊기지 않게."""
        if visible in (keep, d.name):
            return
        p = self.root / visible
        if _is_alias(p):
            _set_hidden(p, True)
            info["aliases"] = [a for a in info.get("aliases") or [] if a != visible] + [visible]

    def _show_real(self, d: Path, visible: str, info: dict, name: str):
        """실제 이름이 이미 맞다(처음부터 맞거나 실제 이름으로 되돌아왔다)."""
        self._hide_old_visible(visible, d.name, d, info)
        if info.pop("hid_real", False):
            _set_hidden(d, False)
        info.update(folder=d.name, visible=None, pending=False, folder_for=name)
        info.pop("want", None)

    def _show_as(self, d: Path, want: str, visible: str, info: dict, name: str):
        p = self.root / want
        try:
            if _is_alias(p) and not _same_path(p, d):
                _drop_alias(p)  # 이 프로젝트의 끊긴 접합(_pick_name이 own에서 골랐다)
            elif _empty_dir(p):
                os.rmdir(p)  # 지난번에 거부되며 남은 빈 폴더
            if not _is_alias(p):
                _make_alias(d, p)
        except OSError as e:  # 네트워크 드라이브·FAT·회사 정책. 옛 이름으로 일하고 안전할 때 바꾼다
            log.info("접합을 만들지 못했습니다(%s): %s", want, e)
            info.update(pending=True, want=want)
            return
        old = visible if visible not in (want, d.name) and _is_alias(self.root / visible) else None
        info["aliases"] = [a for a in info.get("aliases") or [] if a not in (want, old)] + ([old] if old else [])
        info.update(hid_real=True, visible=want, pending=True, folder_for=name)
        info.pop("want", None)
        # 숨기기 전에 기록한다. 기록 없이 숨기면 나중에 실제 이름을 바꿔도 숨김을 풀지 못해 프로젝트가 탐색기에서 사라진다.
        # 쓰기가 실패하면 여기서 멈춘다: 아직 아무것도 숨기지 않았고 다음 기회에 다시 한다
        _write_json(d / "project.json", info)
        _set_hidden(p, False)
        if old:
            _set_hidden(self.root / old, True)
        _set_hidden(d, True)  # 탐색기에 같은 폴더가 둘로 보이지 않게

    def _move(self, d: Path, want: str, visible: str, info: dict, name: str, prefix: str) -> Path | None:
        """실제 이름을 바꾼다. 먼저 임시 이름으로 옮겨 잠금부터 확인한다: 잠겨 있으면 접합을 건드리지 않아 보이는 이름이 끊기지 않는다."""
        tmp = self.root / f"{prefix}_{MOVING}"
        target = self.root / want
        resumed = _same_name(d.name, tmp.name)
        if not resumed:
            if os.path.lexists(tmp):
                try:
                    os.rmdir(tmp)  # 비어 있을 때만 지워진다
                except OSError as e:
                    log.warning("임시 이름이 이미 쓰이고 있어 이름을 바꾸지 못했습니다: %s: %s", tmp.name, e)
                    return None
            if not _rename_retry(d, tmp):
                return None
        old = info.get("folder") if resumed else d.name
        had_alias = False
        try:
            # 무엇을 하려는지 먼저 남긴다. 바꾼 직후 죽으면 이 기록으로 사람이 바꾼 이름과 구별해 뒷정리를 마친다
            _write_json(tmp / "project.json", {**info, "moving": {"to": want, "from": old, "for": name}})
            had_alias = _is_alias(target)
            if had_alias:
                _drop_alias(target)
            elif _empty_dir(target):
                os.rmdir(target)
            os.rename(tmp, target)
        except OSError as e:
            log.warning("폴더 이름 바꾸기 실패: %s → %s: %s", d.name, want, e)
            if not resumed:
                try:
                    os.rename(tmp, d)
                    if had_alias:
                        _make_alias(d, target)
                except OSError as e2:  # 다음 번에 임시 이름에서 이어 간다
                    log.warning("되돌리지 못했습니다: %s", e2)
            return None
        self._finish_move(target, want, old, visible, info, name)
        return target

    def _finish_move(self, target: Path, want: str, old: str | None, visible: str, info: dict, name: str):
        """실제 이름을 바꾼 뒤의 뒷정리. 옛 이름을 숨긴 접합으로 남겨 옛 절대 경로가 그대로 돌게 한다."""
        names = [*_plain_names(info.get("aliases")), *_plain_names([visible, old])]
        kept, strays = [], []
        for n in names:  # 앞선 접합은 옛 경로를 가리켜 끊겼다. 지우고 다시 만든다
            if n in kept or _same_name(n, want) or n.endswith(MOVING):
                continue
            p = self.root / n
            if _is_alias(p):
                try:
                    _drop_alias(p)
                except OSError as e:
                    log.info("옛 접합을 지우지 못했습니다(%s): %s", n, e)
                    continue
            elif os.path.lexists(p):
                # 옛 이름이 잠깐 비는 사이에 Agent 밖의 프로그램이 makedirs로 진짜 폴더를 만들었다. 그 안의 파일을 이 폴더로 합친다
                if n not in strays:
                    strays.append(n)
                continue
            kept.append(n)
        for n in kept[:-ALIAS_KEEP]:
            log.info("오래된 옛 이름을 지웠습니다: %s", n)
        kept = kept[-ALIAS_KEEP:]
        alive = []
        for n in kept:
            try:
                _make_alias(target, self.root / n)
                _set_hidden(self.root / n, True)
                alive.append(n)
            except OSError as e:
                log.info("옛 이름 접합을 만들지 못했습니다(%s): %s", n, e)
        if info.pop("hid_real", False):
            _set_hidden(target, False)
        info.pop("want", None)
        info.update(folder=want, visible=None, pending=False, folder_for=name, aliases=alive, moved_from=old)
        if strays:
            info["stray_names"] = [*(info.get("stray_names") or []), *strays]
        try:  # 실패해도 다음 동기화가 맞춘다
            if old:
                forget_git_block(self.root / old, target)
        except OSError as e:
            log.info("git 제외 목록을 고치지 못했습니다: %s", e)

    # ---------- 번호만 있는 폴더 ----------
    def _strays(self, project_id: int, uid: str | None, d: Path, name: str, info: dict) -> list[tuple[Path, dict]]:
        """이름 있는 폴더 옆에 남은 번호 폴더·예전 규칙 폴더·옛 이름 자리에 새로 생긴 폴더 중 이 프로젝트의 것."""
        out = []
        extra = _plain_names(info.get("stray_names"))
        for s in (self.root / f"P{project_id:04d}", self.root / f"project_{project_id}", *(self.root / n for n in extra)):
            if _is_alias(s) or not s.is_dir() or _same_path(s, d):
                continue
            i = _read_json(s / "project.json")
            if i.get("merged_into") or not ((uid and i.get("uid") == uid) or self._adoptable(i, name)):
                continue
            out.append((s, i))
        return out

    def _note_strays(self, project_id: int, uid: str | None, d: Path, info: dict, name: str):
        info.pop("stray_wait", None)
        if self._strays(project_id, uid, d, name, info) or (self.root / f"P{project_id:04d}_{PROBE}").is_dir():
            info["stray_wait"] = True

    def _restore_probe(self, project_id: int) -> bool:
        """번호 폴더를 확인 이름으로 옮긴 사이에 Agent가 죽었거나, 되돌리는 순간 누가 잡았다. 원래 이름으로 돌려 파일이 프로젝트에서 빠지지 않게 한다.
        아직 못 돌렸으면 False."""
        probe, bare = self.root / f"P{project_id:04d}_{PROBE}", self.root / f"P{project_id:04d}"
        if not probe.is_dir():
            return True
        if _is_alias(bare):
            _drop_alias(bare)
        elif _empty_dir(bare):
            os.rmdir(bare)
        return not os.path.lexists(bare) and _rename_retry(probe, bare)

    def _absorb_strays(self, project_id: int, uid: str | None, d: Path, info: dict, name: str):
        """파일을 잃지 않게 옮긴다. 덮어쓰지 않고, 무엇을 했는지 파일 탭에 알린다(조용히 합치지 않는다)."""
        info.pop("stray_wait", None)
        if not self._restore_probe(project_id):
            info["stray_wait"] = True
        strays = self._strays(project_id, uid, d, name, info)
        waiting = []
        for s, sinfo in strays:
            probe = self.root / f"P{project_id:04d}_{PROBE}"
            if os.path.lexists(probe) or not _rename_retry(s, probe):  # 프로세스가 안에서 돌고 있다(예전 MCP가 남긴 솔버). 다음 기회에
                info["stray_wait"] = True
                waiting.append(s.name)
                continue
            if not _rename_retry(probe, s):  # 다음 동기화의 _restore_probe가 되돌린다
                log.warning("번호 폴더 확인 이름을 되돌리지 못했습니다: %s", probe)
                info["stray_wait"] = True
                waiting.append(s.name)
                continue
            # 내용을 보고 고른다. 동기화 기록(dds_items)은 그 뒤에 사용자가 넣은 글을 모른다. 나머지는 모두 옮기거나 남긴다: 지우지 않는다
            skip = {*dds_owned_files(s), "project.json"}
            moved, same, left = _merge_tree(s, d, skip)
            note = {"from": s.name, "moved": moved, "same": same, "left": left[:20], "left_count": len(left), "at": _now()}
            if moved or same or left:
                info["merged"] = (info.get("merged") or [])[-4:] + [note]
            if left:
                for rel in left:  # 사람이 비교할 파일이다. 동기화가 숨겨 두었을 수 있다
                    for part in (s / rel, *(p for p in (s / rel).parents if s in p.parents)):
                        _set_hidden(part, False)
                sinfo["merged_into"] = d.name
                _write_json(s / "project.json", sinfo)
                continue
            for rel in skip:
                try:
                    _unlink(s / rel)
                except FileNotFoundError:
                    pass
                except OSError as e:
                    log.info("지우지 못했습니다: %s: %s", s / rel, e)
            try:
                # 비었으면 지우고, 옛 절대 경로가 이어지게 숨긴 접합을 남긴다. 무언가 남았으면 rmdir이 실패해 그대로 둔다
                for sub, _, _ in sorted(os.walk(s, topdown=False), key=lambda x: -len(x[0])):
                    os.rmdir(sub)
                _make_alias(d, s)
                _set_hidden(s, True)
                info["aliases"] = [a for a in info.get("aliases") or [] if a != s.name] + [s.name]
            except OSError as e:
                log.info("번호 폴더를 정리하지 못했습니다: %s: %s", s, e)
        names = [n for n in waiting if n not in (f"P{project_id:04d}", f"project_{project_id}")]
        if names:
            info["stray_names"] = names
        else:
            info.pop("stray_names", None)

    # ---------- 보여 줄 이름 ----------
    def visible_dir(self, project_id: int, name: str | None = None, uid: str | None = None) -> Path:
        """사람에게 보여 줄 경로. 평소에는 실제 폴더이고, 이름이 잠겨 있으면 새 이름의 접합이다."""
        d = self.project_dir(project_id, name, uid)
        v = next(iter(_plain_names([_read_json(d / "project.json").get("visible")])), None)
        if v and not (uid and self._links().get(uid)):
            p = self.root / v
            if _is_alias(p) and _same_path(p, d):
                return p
        return d

    def old_paths(self, d: Path) -> list[str]:
        """이 폴더를 아직 가리키는 옛 이름 경로. 오래된 것부터."""
        names = _plain_names(_read_json(d / "project.json").get("aliases"))
        return [str(self.root / n) for n in names if _is_alias(self.root / n) and _same_path(self.root / n, d)]

    def shown_path(self, project_id: int, target: Path, name: str | None = None, uid: str | None = None) -> Path:
        """실제 경로 → 사람에게 보여 줄 경로. 숨겨 둔 실제 폴더를 탐색기로 열지 않는다."""
        base = self.project_dir(project_id, name, uid).resolve()
        shown = self.visible_dir(project_id, name, uid)
        try:
            return shown / Path(target).resolve().relative_to(base)
        except ValueError:
            return Path(target)

    def _notice(self, d: Path, info: dict) -> str | None:
        """파일 탭에 보여 줄 말. 숨기지 않는다: 실제 이름을 지금 못 바꾸는 한계와 옮긴 파일을 알린다."""
        lines = []
        if info.get("want"):
            lines.append(f"폴더 이름은 지금 작업이 끝난 뒤 '{info['want']}'(으)로 바뀝니다 → 작업은 그대로 계속하면 됩니다.")
        elif info.get("visible") and info["visible"] != d.name:
            lines.append(f"탐색기에는 '{info['visible']}'(으)로 보입니다. AI 세션이나 해석이 이 폴더를 쓰는 동안 실제 폴더 이름('{d.name}')은 "
                         "그대로이고, 세션이 끝난 뒤 바뀝니다 → 작업은 그대로 계속하면 됩니다.")
        now = datetime.now()
        for m in info.get("merged") or []:
            frm, left, moved = m.get("from"), m.get("left_count") or 0, m.get("moved") or 0
            if left and (self.root / frm).is_dir() and not _is_alias(self.root / frm):
                lines.append(f"이전 폴더 '{frm}'의 파일 {moved}개를 이 폴더로 옮겼습니다. 이름이 같고 내용이 다른 파일 {left}개는 덮어쓰지 않고 "
                             f"'{frm}'에 남겼습니다 → 두 파일을 비교한 뒤 직접 정리하세요.")
            elif moved and not left:
                try:
                    recent = (now - datetime.fromisoformat(m.get("at"))).days < MERGE_NOTE_DAYS
                except (TypeError, ValueError):
                    recent = False
                if recent:
                    lines.append(f"이전 폴더 '{frm}'의 파일 {moved}개를 이 폴더로 옮겼습니다.")
        return " ".join(lines) or None

    # ---------- 직접 정한 작업 폴더 (이 PC에서만 유효) ----------
    def _links(self) -> dict[str, str]:
        return _read_json(self.root / "index.json").get("links", {})

    def check_folder(self, path: str, uid: str | None = None) -> Path:
        """작업 폴더로 쓸 수 있는지 확인하고 정규화한 경로를 돌려준다. 안 되면 ValueError(사용자에게 보일 문장)."""
        raw = (path or "").strip().strip('"')
        if not raw:
            raise ValueError("폴더 경로를 입력하세요")
        p = Path(os.path.expandvars(os.path.expanduser(raw)))
        if not p.is_absolute():
            raise ValueError("전체 경로를 입력하세요 (예: D:\\Work\\브래킷)")
        p = p.resolve()
        if p.parent == p:
            raise ValueError("드라이브 최상위 폴더는 쓸 수 없습니다. 그 안의 폴더를 고르세요")
        sysroots = [Path(os.environ[k]).resolve() for k in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData")
                    if os.environ.get(k)]
        if any(p == s or s in p.parents for s in sysroots):
            raise ValueError("Windows·프로그램 설치 폴더는 작업 폴더로 쓸 수 없습니다")
        if p == self.root:
            raise ValueError("작업 공간 최상위 폴더 자체는 쓸 수 없습니다. 그 안의 폴더를 고르세요")
        if p.exists() and not p.is_dir():
            raise ValueError("파일이 아니라 폴더를 고르세요")
        try:
            owner = _read_json(p / "project.json")
        except OSError as e:
            raise ValueError(f"이 폴더를 읽을 수 없습니다({e}) → 폴더 권한을 확인하거나 다른 폴더를 고르세요") from e
        if owner.get("uid") and owner.get("uid") != uid:
            raise ValueError(f"다른 프로젝트('{owner.get('name') or owner.get('project_id')}')가 쓰고 있는 폴더입니다")
        return p

    def set_folder(self, project_id: int, name: str | None, uid: str, path: str | None) -> dict:
        """이 프로젝트의 작업 폴더를 직접 정한다(path=None이면 기본 위치로 되돌린다). 기존 파일은 옮기지 않는다."""
        if not uid:
            raise ValueError("프로젝트 고유값(uid)이 없습니다")
        target = self.check_folder(path, uid) if path else None
        if target:
            target.mkdir(parents=True, exist_ok=True)
        with _guard(self.root):
            idx = _read_json(self.root / "index.json")
            links = idx.setdefault("links", {})
            if target:
                links[uid] = str(target)
            else:
                links.pop(uid, None)
            _write_json(self.root / "index.json", idx)
        return self.folder_info(project_id, name, uid)

    def folder_info(self, project_id: int, name: str | None, uid: str | None) -> dict:
        d = self.project_dir(project_id, name, uid)
        return {"path": str(self.visible_dir(project_id, name, uid)), "real": str(d), "custom": bool(uid and self._links().get(uid)),
                "root": str(self.root), "notice": self._notice(d, _read_json(d / "project.json"))}

    # ---------- 스터디·케이스 ----------
    def study_dir(self, project_id: int, study_id: int, study_name: str | None = None, project_name: str | None = None,
                  project_uid: str | None = None) -> Path:
        base = self.project_dir(project_id, project_name, project_uid) / "studies"
        found = sorted(base.glob(f"S{study_id:03d}_*")) + sorted(base.glob(f"S{study_id:03d}"))
        legacy = base / f"study_{study_id}"
        d = found[0] if found else legacy if legacy.exists() else base / (f"S{study_id:03d}_{slug(study_name)}" if study_name else f"S{study_id:03d}")
        (d / "cases").mkdir(parents=True, exist_ok=True)
        return d

    def case_dir(self, study_dir: Path, case_id: int, source: str = "doe") -> Path:
        cases = study_dir / "cases"
        found = sorted(cases.glob(f"C{case_id:04d}_*"))
        d = found[0] if found else cases / f"C{case_id:04d}_{slug(source, 12)}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_study(self, study_dir: Path, meta: dict):
        with _lock:  # 동시에 도는 케이스가 같은 임시 파일을 바꿔치기하면 뒤엣것이 실패한다
            old = _read_json(study_dir / "study.json")
            _write_json(study_dir / "study.json", {**old, **meta, "updated": _now(), "created": old.get("created", _now())})

    @staticmethod
    def is_external(study_dir: Path, case_dir: Path) -> bool:
        """스터디 폴더 밖의 케이스 폴더(사용자가 불러온 원본 해석 폴더)인가."""
        return Path(study_dir).resolve() not in Path(case_dir).resolve().parents

    @staticmethod
    def read_case(case_dir: Path) -> dict:
        """이 PC에 남은 케이스 기록. 없거나 못 읽으면 빈 값이다."""
        return _read_json(Path(case_dir) / "case.json")

    def record_case(self, study_dir: Path, case_dir: Path, case: dict) -> bool:
        """케이스 폴더의 case.json과 스터디의 cases.csv(전 케이스 한 표)를 갱신한다. 돌려주는 값: cases.csv까지 갱신했는가.
        case.json을 쓰지 못하면(폴더 없음·디스크 부족) 예외를 그대로 낸다. 케이스 기록의 원본이기 때문이다.
        케이스 폴더가 스터디 폴더 밖이면 사용자의 원본이다. 거기에는 쓰지 않고 스터디 폴더 쪽 작업 폴더에 기록한다."""
        if self.is_external(study_dir, case_dir):
            if case.get("case_id") is None:
                raise ValueError(f"원본 폴더의 케이스를 기록하려면 case_id가 필요합니다: {case_dir}")
            case = {"source_dir": str(case_dir), **case}
            case_dir = self.case_dir(study_dir, int(case["case_id"]), case.get("source") or "import")
        old = _read_json(case_dir / "case.json")
        info = {**old, **case, "folder": str(Path(case_dir).resolve().relative_to(Path(study_dir).resolve())), "updated": _now()}
        _write_json(case_dir / "case.json", info)
        return self._refresh_csv(study_dir)

    @staticmethod
    def _refresh_csv(study_dir: Path) -> bool:
        """cases.csv는 사람이 보려고 만드는 사본이다. 엑셀로 열어 잠겼거나 보안 프로그램이 암호화했어도 해석을 막지 않는다.
        그래서 읽어 오지 않고 케이스마다의 case.json에서 매번 다시 만든다. 못 쓴 줄은 다음 케이스 때 함께 맞춰진다."""
        path = study_dir / "cases.csv"
        tmp = path.with_suffix(".tmp")
        try:
            with _lock:
                rows: dict[str, dict] = {}
                for d in sorted((study_dir / "cases").glob("C*")):
                    info = _read_json(d / "case.json")
                    if info.get("case_id") is None:
                        continue
                    row = {"case_id": str(info["case_id"]), "source": info.get("source", ""), "status": info.get("status", ""),
                           "folder": info.get("folder") or str(Path("cases") / d.name), "updated": info.get("updated", "")}
                    row.update({f"in:{k}": v for k, v in (info.get("params") or {}).items()})
                    row.update({f"out:{k}": v for k, v in (info.get("results") or {}).items()})
                    if info.get("error"):
                        row["error"] = str(info["error"]).splitlines()[0][:200]
                    rows[row["case_id"]] = row
                cols = ["case_id", "source", "status"]
                for r in rows.values():
                    cols += [k for k in r if k not in cols and k.startswith("in:")]
                for r in rows.values():
                    cols += [k for k in r if k not in cols and k.startswith("out:")]
                cols += [k for k in ("error", "folder", "updated") if k not in cols]
                with open(tmp, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: 엑셀에서 한글이 깨지지 않게
                    w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                    w.writeheader()
                    for k in sorted(rows, key=lambda x: int(x) if x.isdigit() else 0):
                        w.writerow(rows[k])
                tmp.replace(path)
            return True
        except Exception as e:  # noqa: BLE001 — 어떤 이유든 사본 때문에 결과를 잃지 않는다
            log.warning("cases.csv를 갱신하지 못했습니다 %s: %s", path, e)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    # ---------- 탐색 ----------
    def resolve(self, project_id: int, rel: str, project_name: str | None = None, project_uid: str | None = None) -> Path:
        base = self.project_dir(project_id, project_name, project_uid).resolve()
        target = (base / (rel or "")).resolve()
        if base != target and base not in target.parents:
            raise ValueError("프로젝트 폴더 밖의 경로입니다")
        return target

    def tree(self, project_id: int, rel: str = "", project_name: str | None = None, limit: int = 500,
             project_uid: str | None = None) -> dict:
        d = self.resolve(project_id, rel, project_name, project_uid)
        if not d.exists():
            raise FileNotFoundError(f"없는 경로입니다: {rel}")
        base = self.project_dir(project_id, project_name, project_uid).resolve()
        entries = []
        dds = dds_items(base)  # 탐색기에서 숨긴 DDS 규칙·스킬은 웹 파일 탭에도 내보이지 않는다
        kids = [p for p in d.iterdir() if str(p.relative_to(base)).replace("\\", "/") not in dds]
        for p in sorted(kids, key=lambda x: (x.is_file(), x.name.lower()))[:limit]:
            st = p.stat()
            entries.append({"name": p.name, "path": str(p.relative_to(base)).replace("\\", "/"), "dir": p.is_dir(),
                            "size": None if p.is_dir() else st.st_size,
                            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
        shown = self.visible_dir(project_id, project_name, project_uid)
        return {"root": str(shown if _is_alias(shown) else base), "path": str(d.relative_to(base)).replace("\\", "/") if d != base else "",
                "entries": entries, "custom": bool(project_uid and self._links().get(project_uid)),
                "notice": self._notice(base, _read_json(base / "project.json"))}
