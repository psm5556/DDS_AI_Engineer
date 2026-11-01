"""새 Task의 첫 AI 시작 전에 스크립트와 입력을 Task 폴더로 복사한다(지난 Task처럼 시작 · 내 PC로 복사해서 하기).
복사이며 연결이 아니다. 여러 번 해도 결과가 같고, 이미 있는 파일은 덮지 않으며(사람이 고친 파일일 수 있다), 실패해도 AI는 시작한다: AI가 스크립트를 새로 만들 수 있다."""
from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from pathlib import Path

from cae_adapters.recipe_bundle import inside, member_path, restore_zip

from .workspace import real_work

log = logging.getLogger("dds_agent")

MAX_COPY = 50 * 1024 * 1024  # 내 PC 폴더에서 복사하는 스크립트의 상한. 결과 파일을 잘못 넣어 둔 폴더가 통째로 옮겨 오지 않게 한다


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _copy_local(ws, src: dict, dest: Path, out: dict) -> bool:
    """내 PC의 원본 Task 폴더에서 scripts/를 복사한다. 있으면 서버 묶음보다 낫다: 아직 올라가지 않은 스크립트도 있다."""
    try:
        d = ws.find_work(src["project_id"], src["work"], src.get("project_uid"))
    except (OSError, KeyError) as e:
        log.info("원본 Task 폴더를 찾지 못했습니다: %s", e)
        return False
    if d is None or (src.get("work") and not real_work(src["work"]) and not (d / "scripts").is_dir()):
        return False
    scripts = d / "scripts"
    if not scripts.is_dir():
        return False
    total = 0
    files = [f for f in sorted(scripts.rglob("*")) if f.is_file() and not f.is_symlink() and "__pycache__" not in f.parts]
    copied = False
    for f in files:
        rel = f.relative_to(d).as_posix()
        try:
            target = inside(dest, member_path(rel))
        except ValueError:  # 지침 파일이나 숨김 폴더는 AI의 규칙을 바꿀 수 있어 옮기지 않는다
            continue
        size = f.stat().st_size
        if total + size > MAX_COPY:
            out["skipped"].append(rel)
            continue
        total += size
        if target.exists():
            (out["unchanged"] if _sha(target) == _sha(f) else out["conflicts"]).append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(f, target)
        out["scripts"].append(rel)
        copied = True
    return copied or bool(out["unchanged"])


def _restore_recipes(api, work_id: int, recipes: list[dict], dest: Path, out: dict):
    tmp = Path(tempfile.mkdtemp(prefix="dds_carry_"))
    try:
        for r in recipes:  # 새 스터디의 묶음이 앞에 온다. 같은 경로는 앞의 것이 남는다
            blob = tmp / f"{r['study_id']}.zip"
            try:
                digest = api.download(f"/api/works/{work_id}/carry/{r['study_id']}", blob)
                if r.get("sha256") and digest != r["sha256"]:
                    raise ValueError("받은 묶음이 서버 기록과 다릅니다")
                restored, unchanged, conflicts = restore_zip(blob, dest, {f["path"]: f for f in r.get("files") or []})
            except Exception as e:  # noqa: BLE001 — 스크립트 하나를 못 받아도 AI는 시작한다. AI가 새로 만들 수 있다
                out["errors"].append(f"{r.get('name') or r['study_id']}: {e}")
                continue
            out["scripts"] += [x for x in restored if x not in out["scripts"]]
            out["unchanged"] += [x for x in unchanged if x not in out["unchanged"]]
            # 앞의 묶음이 이미 쓴 파일이면 충돌이 아니라 순서다
            out["conflicts"] += [x for x in conflicts if x not in out["scripts"] and x not in out["conflicts"]]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fetch_inputs(api, attachments: list[dict], dest: Path, out: dict):
    inputs = dest / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    for a in attachments:
        target = inputs / a["filename"]
        try:
            if target.exists() and _sha(target) == a.get("sha256"):
                out["inputs"].append(f"inputs/{target.name}")
                continue
            if target.exists():  # 같은 이름의 다른 파일은 덮지 않는다
                target = inputs / f"{target.stem}_{a['id']}{target.suffix}"
            if not target.exists():
                api.download(f"/api/attachments/{a['id']}/download", target)
            out["inputs"].append(f"inputs/{target.name}")
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"{a['filename']}: {e}")


def apply_carry(api, ws, dest: Path, work: dict) -> dict | None:
    """돌려주는 값: {scripts, inputs, unchanged, conflicts, skipped, errors}. 서버가 첫 턴의 안내에 쓴다. 받을 것이 없으면 None."""
    carry = (work or {}).get("carry")
    if not carry:
        return None
    out: dict = {"scripts": [], "inputs": [], "unchanged": [], "conflicts": [], "skipped": [], "errors": []}
    src = carry.get("local_source")
    local = _copy_local(ws, src, dest, out) if src else False
    if not local and carry.get("recipes"):
        _restore_recipes(api, work["id"], carry["recipes"], dest, out)
    if carry.get("attachments"):
        _fetch_inputs(api, carry["attachments"], dest, out)
    return out
