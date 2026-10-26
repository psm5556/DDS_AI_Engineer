"""사용자 PC 로컬 데이터 규칙 (docs/local-data.md).

%USERPROFILE%\\DDS_Workspace\\
  index.json                          내 PC의 전체 프로젝트 ↔ 서버 연결 색인
  P0012_<프로젝트이름>\\
    project.json                      프로젝트 ID·이름·서버 주소 (서버와 연결 고리)
    inputs\\ scripts\\ runs\\ live\\ reports\\
    studies\\S003_<스터디이름>\\
      study.json  cases.csv
      cases\\C0001_doe\\ C0021_bo\\ C0031_al\\   ← 케이스마다 case.json·params.json·results.json·run.log·images\\·fields\\·툴 산출물

- 폴더 이름의 번호(P/S/C + 서버 ID)가 연결의 기준이다. 이름 부분은 사람이 알아보기 위한 것이라 바뀌어도 된다(번호로 찾는다).
- 사용자가 프로젝트마다 작업 폴더를 직접 정할 수 있다(D:\\Work\\... 등). index.json의 links(uid → 절대 경로)에
  이 PC 기준으로 기록하고, 그 폴더 안에도 같은 구조(project.json·inputs·studies…)를 만든다.
- 원본(inputs)은 읽기 전용으로 다루고, 케이스는 복사본으로 실행한다.
"""
from __future__ import annotations

import csv
import json
import os
import re
import threading
from datetime import datetime
from pathlib import Path

SUBDIRS = ("inputs", "scripts", "runs", "studies", "live", "reports")
_lock = threading.Lock()


def slug(name: str | None, limit: int = 24) -> str:
    """폴더 이름용: 금지 문자·공백·말줄임표 제거, 짧게(Windows 경로 길이 260자 제한 대비)."""
    s = re.sub(r'[\\/:*?"<>|\s…]+', "-", (name or "").strip())
    return s[:limit].strip("-.") or "untitled"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _write_json(path: Path, data: dict):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class Workspace:
    def __init__(self, root: Path, server_url: str = ""):
        self.root = Path(root)
        self.server_url = server_url
        self.root.mkdir(parents=True, exist_ok=True)

    # ---------- 프로젝트 ----------
    def project_dir(self, project_id: int, name: str | None = None, uid: str | None = None) -> Path:
        """P{id:04d}_<이름> 폴더를 찾고, 없으면 만든다. 예전 규칙(project_{id})도 그대로 쓴다.
        uid(서버 프로젝트 고유값)를 주면 project.json의 uid로 '이 프로젝트의 폴더'인지 확인한다.
        서버 DB를 새로 만들어 번호가 겹쳐도 다른 프로젝트의 폴더를 쓰지 않는다.
        사용자가 이 PC에서 작업 폴더를 직접 정했으면(index.json links) 그 폴더를 쓴다.
        uid를 모르면(MCP 툴) 이 번호로 가장 최근에 쓴 폴더(index.json)를 쓴다."""
        with _lock:
            cands = sorted(self.root.glob(f"P{project_id:04d}_*")) + sorted(self.root.glob(f"P{project_id:04d}"))
            legacy = self.root / f"project_{project_id}"
            if legacy.exists():
                cands.append(legacy)
            fresh = f"P{project_id:04d}_{slug(name)}" if name else f"P{project_id:04d}"
            d = None
            linked = self._links().get(uid) if uid else None
            if linked:
                d = Path(linked)
            elif uid:
                d = next((c for c in cands if _read_json(c / "project.json").get("uid") == uid), None)
                if d is None:  # uid 없는 예전 폴더는 이름이 같을 때만 이 프로젝트의 것으로 본다
                    d = next((c for c in cands if not _read_json(c / "project.json").get("uid")
                              and (not name or _read_json(c / "project.json").get("name") == name)), None)
                if d is None:
                    d = self.root / fresh
                    if d.exists():  # 같은 번호·이름의 다른 프로젝트 폴더가 있으면 uid를 붙여 따로 만든다
                        d = self.root / f"{fresh}_{uid[:6]}"
            else:
                cur = _read_json(self.root / "index.json").get("projects", {}).get(str(project_id), {}).get("folder")
                cur_dir = self.root / cur if cur else None  # 직접 정한 폴더는 절대 경로로 들어 있다(root / 절대경로 = 그 경로)
                d = cur_dir if cur_dir and cur_dir.is_dir() else cands[0] if cands else self.root / fresh
            for sub in SUBDIRS:
                (d / sub).mkdir(parents=True, exist_ok=True)
            info = _read_json(d / "project.json")
            if not info or (name and info.get("name") != name) or (uid and info.get("uid") != uid):
                info.update({"project_id": project_id, "uid": uid or info.get("uid"), "name": name or info.get("name", ""),
                             "server": self.server_url, "folder": d.name, "created": info.get("created", _now()),
                             "updated": _now()})
                _write_json(d / "project.json", info)
            if uid:
                self._index(project_id, info, d)
            return d

    def _index(self, project_id: int, info: dict, d: Path):
        idx = _read_json(self.root / "index.json")
        cur = idx.get("projects", {}).get(str(project_id), {})
        folder = d.name if d.parent == self.root else str(d)  # 기본 위치면 폴더 이름, 직접 정한 폴더면 절대 경로
        if cur.get("folder") == folder and cur.get("name") == info.get("name"):
            return
        idx.setdefault("projects", {})[str(project_id)] = {"name": info.get("name"), "folder": folder,
                                                             "server": self.server_url, "updated": _now()}
        idx["root"] = str(self.root)
        _write_json(self.root / "index.json", idx)

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
        owner = _read_json(p / "project.json")
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
        with _lock:
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
        return {"path": str(d), "custom": bool(uid and self._links().get(uid)), "root": str(self.root)}

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
        old = _read_json(study_dir / "study.json")
        _write_json(study_dir / "study.json", {**old, **meta, "updated": _now(), "created": old.get("created", _now())})

    def record_case(self, study_dir: Path, case_dir: Path, case: dict):
        """케이스 폴더의 case.json과 스터디의 cases.csv(전 케이스 한 표)를 갱신한다."""
        old = _read_json(case_dir / "case.json")
        info = {**old, **case, "folder": str(case_dir.relative_to(study_dir)), "updated": _now()}
        _write_json(case_dir / "case.json", info)
        with _lock:
            path = study_dir / "cases.csv"
            rows: dict[str, dict] = {}
            if path.exists():
                with open(path, encoding="utf-8-sig", newline="") as f:
                    rows = {r["case_id"]: r for r in csv.DictReader(f)}
            row = {"case_id": str(info.get("case_id")), "source": info.get("source", ""), "status": info.get("status", ""),
                   "folder": info["folder"], "updated": info["updated"]}
            row.update({f"in:{k}": v for k, v in (info.get("params") or {}).items()})
            row.update({f"out:{k}": v for k, v in (info.get("results") or {}).items()})
            if info.get("error"):
                row["error"] = str(info["error"]).splitlines()[0][:200]
            rows[row["case_id"]] = {**rows.get(row["case_id"], {}), **row}
            cols = ["case_id", "source", "status"]
            for r in rows.values():
                cols += [k for k in r if k not in cols and k.startswith("in:")]
            for r in rows.values():
                cols += [k for k in r if k not in cols and k.startswith("out:")]
            cols += [k for k in ("error", "folder", "updated") if k not in cols]
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig: 엑셀에서 한글이 깨지지 않게
                w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                w.writeheader()
                for k in sorted(rows, key=lambda x: int(x) if x.isdigit() else 0):
                    w.writerow(rows[k])
            tmp.replace(path)

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
        for p in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))[:limit]:
            st = p.stat()
            entries.append({"name": p.name, "path": str(p.relative_to(base)).replace("\\", "/"), "dir": p.is_dir(),
                            "size": None if p.is_dir() else st.st_size,
                            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")})
        return {"root": str(base), "path": str(d.relative_to(base)).replace("\\", "/") if d != base else "", "entries": entries,
                "custom": bool(project_uid and self._links().get(project_uid))}
