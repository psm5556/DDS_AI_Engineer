"""레시피: 스터디의 스크립트와 adapter_config를 서버에 작은 묶음으로 둔다.

스크립트는 만든 사람 PC에만 있어서 다른 멤버 PC에서는 "스크립트가 없습니다"로 막힌다. 그래서 서버가 묶음을 보관하고 전달한다.
서버는 묶음을 실행하지도 디스크에 풀지도 않는다. 읽어서 검사한 뒤 검사를 통과한 파일만 새 zip으로 다시 쓴다.
다른 사람 스크립트를 내 PC에서 실행하게 되므로 비밀값이 섞인 묶음과 실행 파일은 받지 않는다.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import stat
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import models
from ..config import settings

MAX_BUNDLE = 20 * 1024 * 1024
MAX_FILE = 5 * 1024 * 1024
MAX_FILES = 200
MAX_UNPACKED = 50 * 1024 * 1024

_SECRET_NAME = re.compile(
    r"(^|/)(\.env(\..*)?|\.netrc|\.npmrc|\.pypirc|\.git-credentials|id_(rsa|dsa|ecdsa|ed25519)|credentials?(\.[a-z0-9]+)?|secrets?(\.[a-z0-9]+)?"
    r"|tokens?(\.[a-z0-9]+)?|known_hosts|auth\.json|tools\.json)$|\.(pem|key|pfx|p12|kdbx|keystore|jks|crt|cer)$", re.I)
# 실행 파일과 컴파일된 코드는 내용을 사람이 확인할 수 없다. 묶음에는 읽을 수 있는 스크립트만 둔다
_BLOCKED_EXT = re.compile(r"\.(exe|dll|sys|msi|msp|com|scr|pif|so|dylib|bin|pyc|pyd|pyo|jar|class|cpl|ocx|lnk|hta|vbe|jse|wsf|wsh)$", re.I)
_SECRET_TEXT = [
    (re.compile(rb"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"), "개인 키"),
    (re.compile(rb"\bAKIA[0-9A-Z]{16}\b"), "AWS 키"),
    (re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "GitHub 토큰"),
    (re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b"), "API 키"),
    (re.compile(rb"\bxox[abprs]-[A-Za-z0-9-]{20,}\b"), "Slack 토큰"),
    (re.compile(rb"(?i)\b(?:authorization|bearer)\s*[:=]?\s*['\"]?bearer\s+[A-Za-z0-9._~+/=-]{20,}"), "인증 토큰"),
    # 값을 글자 그대로 적은 경우만 잡는다. password = os.environ[...]은 괜찮다
    (re.compile(rb"(?i)\b(?:password|passwd|pwd|secret|secret_key|api[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"][^'\"\s]{6,}['\"]"), "비밀번호·키 값"),
]
_SECRET_KEY = re.compile(r"(?i)(password|passwd|secret|api[_-]?key|token|credential|private[_-]?key)")
# 받는 PC(mcp_servers/dds_study._recipe_member)가 거절하는 경로. 여기서 받아 두면 아무 멤버도 내려받지 못하는 묶음이 된다
_CONTROL_FILES = {"agents.md", "claude.md", "gemini.md", "project.json"}
_WINDOWS_DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class RecipeRejected(ValueError):
    """받을 수 없는 묶음. 문구에 이유와 할 일이 들어 있다."""


def _safe_path(name: str) -> str:
    """zip 안의 경로를 검사한다. 내려받는 PC에서 프로젝트 폴더 밖으로 나가면 안 된다."""
    n = name.replace("\\", "/")
    parts = n.split("/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n) or ":" in n or "\x00" in n or any(not p.strip(" .") for p in parts):  # "..", "... " 처럼 점·공백뿐인 이름은 Windows에서 상위 폴더가 될 수 있다
        raise RecipeRejected(f"묶음에 안전하지 않은 경로가 있습니다: {name[:80]} → 프로젝트 폴더 안의 상대 경로만 넣어 주세요")
    if len(n) > 300:
        raise RecipeRejected(f"경로가 너무 깁니다: {n[:80]}… → 300자 이내로 줄여 주세요")
    return n


def _check_restorable(path: str):
    """받는 PC가 거절하는 파일. 비밀값 검사 뒤에 본다. .env는 숨김 파일이기 전에 비밀값이라 그 이유로 알려야 한다."""
    for part in path.split("/"):
        clean = part.rstrip(" .").lower()
        if part.startswith(".") or clean.split(".")[0] in _WINDOWS_DEVICES or clean in _CONTROL_FILES:
            raise RecipeRejected(f"묶음에 넣을 수 없는 파일입니다: {path[:80]} → AI 도구의 지침 파일과 숨김 폴더는 빼고, 스크립트만 넣어 주세요")


def _scan_text(path: str, data: bytes):
    if data[:2] in (b"MZ", b"\x7fE") or data[:4] in (b"\x7fELF", b"\xca\xfe\xba\xbe"):
        raise RecipeRejected(f"실행 파일은 묶음에 넣을 수 없습니다: {path} → 스크립트만 넣어 주세요")
    for pattern, label in _SECRET_TEXT:
        if pattern.search(data):  # 찾은 값은 문구에 넣지 않는다. 오류가 대화·로그에 남기 때문이다
            raise RecipeRejected(f"비밀값으로 보이는 내용이 있습니다({label}): {path} → 값을 지우고 환경변수에서 읽게 고친 뒤 다시 올려 주세요")


def _scan_config(value: Any, where: str = "adapter_config"):
    if isinstance(value, dict):
        for k, v in value.items():
            if _SECRET_KEY.search(str(k)) and v not in (None, "", [], {}):
                raise RecipeRejected(f"비밀값으로 보이는 설정이 있습니다: {where}.{k} → 그 값을 빼고 다시 올려 주세요")
            _scan_config(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _scan_config(v, f"{where}[{i}]")
    elif isinstance(value, str):
        _scan_text(where, value.encode("utf-8", "ignore"))


def check_bundle(data: bytes) -> list[tuple[str, bytes]]:
    """zip을 풀지 않고 파일마다 메모리에서 검사한다. 통과한 파일만 돌려준다."""
    if len(data) > MAX_BUNDLE:
        raise RecipeRejected(f"묶음이 {MAX_BUNDLE // (1024 * 1024)}MB를 넘습니다 → 스크립트와 설정만 넣어 주세요. 해석 결과·모델 파일은 넣지 않습니다")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise RecipeRejected("zip 파일이 아닙니다 → 스크립트를 zip으로 묶어 올려 주세요") from e
    out: list[tuple[str, bytes]] = []
    seen: set[str] = set()
    total = 0
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos:
            raise RecipeRejected("묶음에 파일이 없습니다 → 스크립트를 넣어 주세요")
        if len(infos) > MAX_FILES:
            raise RecipeRejected(f"파일이 {MAX_FILES}개를 넘습니다({len(infos)}개) → 필요한 스크립트만 넣어 주세요")
        for info in infos:
            path = _safe_path(info.filename)
            if stat.S_ISLNK(info.external_attr >> 16):
                raise RecipeRejected(f"바로가기(심볼릭 링크)는 넣을 수 없습니다: {path} → 실제 파일을 넣어 주세요")
            if _SECRET_NAME.search(path):
                raise RecipeRejected(f"비밀값이 들어 있을 수 있는 파일입니다: {path} → 묶음에서 빼 주세요")
            if _BLOCKED_EXT.search(path):
                raise RecipeRejected(f"실행 파일은 묶음에 넣을 수 없습니다: {path} → 스크립트만 넣어 주세요")
            _check_restorable(path)
            key = path.lower()
            if key in seen:
                raise RecipeRejected(f"같은 경로가 두 번 있습니다: {path} → 하나만 넣어 주세요")
            seen.add(key)
            if info.file_size > MAX_FILE:
                raise RecipeRejected(f"파일이 {MAX_FILE // (1024 * 1024)}MB를 넘습니다: {path} → 스크립트와 작은 설정만 넣어 주세요")
            try:
                with zf.open(info) as f:
                    body = f.read(MAX_FILE + 1)  # 압축 폭탄: 적힌 크기를 믿지 않는다
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError) as e:
                raise RecipeRejected(f"묶음을 읽지 못했습니다: {path} ({e.__class__.__name__}) → 암호 없이 다시 묶어 주세요") from e
            if len(body) > MAX_FILE:
                raise RecipeRejected(f"파일이 {MAX_FILE // (1024 * 1024)}MB를 넘습니다: {path} → 스크립트와 작은 설정만 넣어 주세요")
            total += len(body)
            if total > MAX_UNPACKED:
                raise RecipeRejected(f"압축을 푼 크기가 {MAX_UNPACKED // (1024 * 1024)}MB를 넘습니다 → 스크립트와 작은 설정만 넣어 주세요")
            _scan_text(path, body)
            out.append((path, body))
    return out


def _config_warnings(config: dict, paths: set[str]) -> list[str]:
    """다른 멤버 PC에서 막힐 설정을 미리 알린다."""
    warnings = []
    wanted: list[str] = []
    steps = [config, *(config.get("post") if isinstance(config.get("post"), list) else [config["post"]] if config.get("post") else [])]
    wanted += [s["script"] for s in steps if isinstance(s, dict) and s.get("script")]
    inputs = [str(x) for x in config.get("inputs") or []]
    for rel in wanted:
        if str(rel).replace("\\", "/") not in paths:
            warnings.append(f"설정의 스크립트 '{rel}'이(가) 묶음에 없습니다 → 다른 멤버 PC에서 '스크립트가 없습니다'로 실패합니다. 묶음에 넣어 주세요")
    for rel in inputs:
        if str(rel).replace("\\", "/") not in paths:
            warnings.append(f"입력 파일 '{rel}'은(는) 묶음에 없습니다 → 다른 멤버는 같은 파일을 대화에 첨부해야 돌릴 수 있습니다")
    for rel in [*wanted, *inputs]:
        if re.match(r"^([A-Za-z]:|[\\/])", str(rel)):
            warnings.append(f"절대 경로 '{rel}'은(는) 다른 PC에 없습니다 → 프로젝트 폴더 기준 상대 경로로 바꿔 주세요")
    return warnings


def recipe_path(study: models.Study) -> Path:
    return settings.files_dir / f"project_{study.project_id}" / "recipes" / f"study_{study.id}.zip"


def store(study: models.Study, data: bytes, config: dict | None, note: str, user_id: int, user_name: str) -> dict:
    files = check_bundle(data)
    config = study.adapter_config or {} if config is None else config
    _scan_config(config)
    paths = {p for p, _ in files}
    warnings = _config_warnings(config, paths)
    version = int((study.recipe or {}).get("version") or 0) + 1
    old = study.recipe or {}
    # 내용이 같은 묶음을 다시 올려도 만든 사람은 그대로다. "○○님이 만든 스크립트" 승인이 다시 올리는 것으로 사라지면 안 된다
    if old.get("created_by") and {f["path"]: f["sha256"] for f in old.get("files") or []} == {p: hashlib.sha256(b).hexdigest() for p, b in files if p != "recipe.json"}:
        user_id, user_name = old["created_by"], old.get("created_by_name") or user_name
    manifest = {"study_id": study.id, "study_name": study.name, "version": version, "adapter": study.adapter, "adapter_config": config,
                "created_by": user_name, "created_at": datetime.now().isoformat(timespec="seconds"), "note": note}
    if "recipe.json" in paths:
        files = [(p, b) for p, b in files if p != "recipe.json"]  # 설명 파일은 서버가 쓴다. 믿을 수 있는 만든 사람 이름을 위해
    dest = recipe_path(study)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("recipe.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for path, body in files:
            zf.writestr(path, body)
    tmp.replace(dest)
    blob = dest.read_bytes()
    meta = {**manifest, "created_by": user_id, "created_by_name": user_name, "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
            "files": [{"path": p, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for p, b in files], "warnings": warnings}
    study.recipe = meta
    return meta


def view(study: models.Study) -> dict:
    r = study.recipe or {}
    if not r or not recipe_path(study).is_file():
        return {"has": False, "study_id": study.id}
    return {"has": True, **{k: r.get(k) for k in ("version", "files", "size", "sha256", "adapter", "adapter_config", "note", "created_by",
                                                  "created_by_name", "created_at", "warnings")}, "study_id": study.id}


def copy_to(src: models.Study, dst: models.Study):
    """복제한 스터디도 같은 스크립트로 시작한다. 옛 스터디의 파일은 건드리지 않는다."""
    if not src.recipe or not recipe_path(src).is_file():
        return
    target = recipe_path(dst)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(recipe_path(src), target)
    # 복제본이 받은 새 설정으로 다시 잰다. 옛 설정을 그대로 두면 멤버에게 옛 모델을 첨부하라고 안내한다
    config = dst.adapter_config or {}
    dst.recipe = {**src.recipe, "adapter_config": config, "warnings": _config_warnings(config, {f["path"] for f in src.recipe.get("files") or []})}
