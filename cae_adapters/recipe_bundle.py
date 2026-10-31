"""스크립트 묶음(레시피)을 만들고 경로를 검사한다. MCP(save_recipe·restore_recipe)와 PC Agent(해석이 끝나면 자동으로 올림)가 같이 쓴다."""
from __future__ import annotations

import hashlib
import io
import zipfile
from pathlib import Path, PurePosixPath

# 이 프로젝트에서 도는 AI 도구가 지침으로 읽는 파일. CLI는 하위 폴더의 지침 파일도 읽을 수 있어 어느 깊이든 막는다
CONTROL_FILES = {"agents.md", "claude.md", "gemini.md", "project.json"}
WINDOWS_DEVICES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def member_path(name: str) -> PurePosixPath:
    """묶음 안의 경로를 검사한다. 서버도 검사하지만 다른 사람이 만든 파일을 이 PC에 쓰는 일이라 받는 쪽에서 다시 막는다.
    점으로 시작하는 폴더와 지침 파일도 막는다. 그 자리에 쓰면 이 프로젝트에서 도는 AI 도구의 지침을 바꿀 수 있다."""
    n = str(name).replace("\\", "/")
    bad = f"안전하지 않은 경로입니다: {str(name)[:80]} → 프로젝트 폴더 안의 스크립트 경로만 받습니다"
    if not n or n.startswith("/") or ":" in n or "\x00" in n or len(n) > 300:
        raise ValueError(bad)
    for part in n.split("/"):
        clean = part.rstrip(" .").lower()  # Windows는 끝의 점·공백을 지운다. "AGENTS.md."도 AGENTS.md가 된다
        if not clean or part.startswith(".") or clean.split(".")[0] in WINDOWS_DEVICES or clean in CONTROL_FILES:
            raise ValueError(bad)
    return PurePosixPath(n)


def inside(root: Path, rel: PurePosixPath) -> Path:
    base = root.resolve()
    target = (base / Path(*rel.parts)).resolve()
    if base not in target.parents:
        raise ValueError(f"프로젝트 폴더 밖 경로입니다: {rel} → 프로젝트 폴더 안의 경로만 씁니다")
    return target


def config_scripts(config: dict) -> list[str]:
    post = config.get("post") or []
    steps = [config, *(post if isinstance(post, list) else [post])]
    return [str(s["script"]) for s in steps if isinstance(s, dict) and s.get("script")]


def build_bundle(root: Path, wanted: list[str]) -> tuple[bytes, dict[str, str]]:
    """프로젝트 폴더의 스크립트를 zip으로 묶는다. 돌려주는 값: (zip 내용, {경로: sha256}). 없는 파일·밖의 파일은 ValueError."""
    buf = io.BytesIO()
    names: dict[str, str] = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in dict.fromkeys(wanted):
            p = Path(rel)
            if p.is_absolute():
                try:
                    rel = p.resolve().relative_to(root.resolve()).as_posix()
                except ValueError:
                    raise ValueError(f"프로젝트 폴더 밖 파일은 묶지 않습니다: {rel} → scripts/ 폴더로 복사하고 프로젝트 폴더 기준 경로를 쓰세요") from None
            full = inside(root, member_path(rel))
            if not full.exists():
                raise ValueError(f"스크립트가 없습니다: {rel} → 파일을 scripts/에 두거나 경로를 고쳐 주세요")
            # 바로가기는 따라가지 않는다. 프로젝트 폴더 밖 파일이 묶음에 섞일 수 있다
            items = [full] if full.is_file() else [f for f in sorted(full.rglob("*")) if f.is_file() and not f.is_symlink() and "__pycache__" not in f.parts]
            for f in items:
                arc = f.relative_to(root.resolve()).as_posix()
                member_path(arc)
                if arc not in names:
                    body = f.read_bytes()
                    zf.writestr(arc, body)
                    names[arc] = hashlib.sha256(body).hexdigest()
    return buf.getvalue(), names
