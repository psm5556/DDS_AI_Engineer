"""PC Agent 설치에 필요한 런타임을 미리 받아 서버 데이터 폴더(data/agent_runtime)에 둔다.
  - 포터블 Python (python-build-standalone, install_only)
  - 포터블 Node.js (npm 포함, 에이전트 CLI 설치용)
  - Agent 의존성 wheel 묶음 (사내 pip 미러가 없거나 일부 패키지가 없을 때 대비)
두 가지 방법:
  인터넷이 되는 곳:  python scripts/fetch_runtimes.py [--out data/agent_runtime] [--no-wheels]
  사내 서버에서 바로: python scripts/fetch_runtimes.py --from-local [--out <서버 data_dir>/agent_runtime]
     → 인터넷 없이 이 PC에 설치된 Python·Node를 묶고, wheel은 사내 pip 미러에서 받는다(웹앱 관리 > Agent 배포의 버튼과 같다).
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_VERSION = "3.12.15"
PYTHON_BUILD = "20261021"
PYTHON_URL = (f"https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON_BUILD}/"
              f"cpython-{PYTHON_VERSION}%2B{PYTHON_BUILD}-x86_64-pc-windows-msvc-install_only.tar.gz")
NODE_VERSION = "22.23.4"
NODE_URL = f"https://nodejs.org/dist/v{NODE_VERSION}/node-v{NODE_VERSION}-win-x64.zip"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, dest: Path):
    if dest.exists():
        print(f"  이미 있음: {dest.name}")
        return
    print(f"  다운로드: {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.rename(dest)


def wheels(out: Path, py_version: str = "3.12") -> Path:
    wdir = out / "wheels"
    wdir.mkdir(exist_ok=True)
    print(f"  Agent 의존성 wheel 받는 중 (win_amd64, cp{py_version.replace('.', '')})...", flush=True)
    subprocess.check_call([sys.executable, "-m", "pip", "download", "-q", "-r", str(ROOT / "requirements-agent.txt"),
                           "-d", str(wdir), "--platform", "win_amd64", "--python-version", py_version,
                           "--implementation", "cp", "--only-binary=:all:"])
    zpath = out / "wheels.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for w in wdir.glob("*.whl"):
            z.write(w, w.name)
    shutil.rmtree(wdir)
    return zpath


# ---------- 사내 서버에서 바로: 이 PC에 설치된 Python·Node를 묶는다 ----------
# 설치된 CPython 폴더는 옮겨도 동작한다(python.exe 위치로 Lib를 찾는다). 서버 패키지(site-packages)는 빼고 pip만 넣는다.
PY_SKIP_DIRS = {"__pycache__", "Doc", "Tools", "Scripts", "include", "test", "tests", "idlelib", "turtledemo"}


def _keep_site(rel: Path) -> bool:
    parts = rel.parts
    if len(parts) >= 3 and parts[0].lower() == "lib" and parts[1].lower() == "site-packages":
        return parts[2].lower().startswith("pip")  # pip와 pip-*.dist-info만
    return True


def pack_local_python(out: Path) -> dict:
    base = Path(sys.base_prefix)
    if "WindowsApps" in str(base):
        sys.exit("Microsoft Store용 Python은 묶을 수 없습니다. python.org 설치본으로 서버를 실행하세요.")
    if not (base / "python.exe").exists() or not (base / "Lib" / "os.py").exists():
        sys.exit(f"설치된 Python 폴더를 찾을 수 없습니다: {base}")
    ver = ".".join(map(str, sys.version_info[:3]))
    dest = out / f"python-{ver}-win-x64-local.tar.gz"
    print(f"  Python {ver} 묶는 중: {base}", flush=True)
    tmp = dest.with_suffix(".part")
    with tarfile.open(tmp, "w:gz", compresslevel=6) as t:
        for p in base.rglob("*"):
            rel = p.relative_to(base)
            if p.is_dir() or set(rel.parts) & PY_SKIP_DIRS or p.suffix == ".pyc" or not _keep_site(rel):
                continue
            t.add(p, arcname=(Path("python") / rel).as_posix(), recursive=False)  # 설치 스크립트는 'python' 폴더로 푼다
    tmp.replace(dest)
    return {"file": dest.name, "version": ver, "sha256": sha256(dest)}


def _find_node() -> Path:
    found = shutil.which("node")
    for c in ([Path(found)] if found else []) + [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs" / "node.exe"]:
        if c.exists() and (c.parent / "node_modules" / "npm").is_dir():
            return c
    sys.exit("Node.js(npm 포함)를 찾을 수 없습니다. 서버에 Node.js를 설치하거나 PATH에 넣으세요.")


def pack_local_node(out: Path) -> dict:
    exe = _find_node()
    ver = subprocess.check_output([str(exe), "--version"], text=True).strip().lstrip("v")
    folder = f"node-v{ver}-win-x64"
    dest = out / f"{folder}-local.zip"
    print(f"  Node.js {ver} 묶는 중: {exe.parent}", flush=True)
    tmp = dest.with_suffix(".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in exe.parent.rglob("*"):
            if p.is_file():
                z.write(p, (Path(folder) / p.relative_to(exe.parent)).as_posix())  # 설치 스크립트가 이 폴더 이름을 'node'로 바꾼다
    tmp.replace(dest)
    return {"file": dest.name, "version": ver, "sha256": sha256(dest), "folder": folder}


def main_local(out: Path, with_wheels: bool):
    manifest = {"python": pack_local_python(out), "node": pack_local_node(out)}
    if with_wheels:
        try:
            wz = wheels(out, ".".join(map(str, sys.version_info[:2])))
            manifest["wheels"] = {"file": wz.name, "version": "requirements-agent.txt", "sha256": sha256(wz)}
        except subprocess.CalledProcessError:  # 없어도 설치는 된다 — 사용자 PC가 사내 pip 미러에서 직접 받는다
            print("  ! wheel을 받지 못했습니다(사내 pip 미러 확인). 사용자 PC 설치 때 pip 미러에서 직접 받습니다.", flush=True)
            shutil.rmtree(out / "wheels", ignore_errors=True)
    old = out / "manifest.json"
    if old.exists():  # 예전 런타임 파일은 새 목록에 없으면 지운다(용량)
        keep = {v["file"] for v in manifest.values()}
        for v in json.loads(old.read_text(encoding="utf-8")).values():
            if isinstance(v, dict) and v.get("file") and v["file"] not in keep:
                (out / v["file"]).unlink(missing_ok=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"완료: {out}\n{json.dumps(manifest, indent=2, ensure_ascii=False)}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "agent_runtime"))
    ap.add_argument("--no-wheels", action="store_true")
    ap.add_argument("--from-local", action="store_true", help="인터넷 없이 이 PC의 Python·Node를 묶는다(사내 서버용)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if a.from_local:
        return main_local(out, not a.no_wheels)

    py = out / f"python-{PYTHON_VERSION}-win-x64.tar.gz"
    node = out / f"node-v{NODE_VERSION}-win-x64.zip"
    fetch(PYTHON_URL, py)
    fetch(NODE_URL, node)
    manifest = {
        "python": {"file": py.name, "version": PYTHON_VERSION, "sha256": sha256(py)},
        "node": {"file": node.name, "version": NODE_VERSION, "sha256": sha256(node),
                 "folder": f"node-v{NODE_VERSION}-win-x64"},
    }
    if not a.no_wheels:
        wz = wheels(out)
        manifest["wheels"] = {"file": wz.name, "version": "requirements-agent.txt", "sha256": sha256(wz)}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"완료: {out}\n{json.dumps(manifest, indent=2, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
