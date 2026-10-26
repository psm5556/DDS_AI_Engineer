"""PC Agent 설치에 필요한 런타임을 미리 받아 서버 데이터 폴더(data/agent_runtime)에 둔다.
사내망은 인터넷이 막혀 있으므로, 인터넷이 되는 곳에서 한 번 실행해 결과 폴더를 서버로 옮긴다.
  - 포터블 Python (python-build-standalone, install_only)
  - 포터블 Node.js (npm 포함, 에이전트 CLI 설치용)
  - Agent 의존성 wheel 묶음 (사내 pip 미러가 없거나 일부 패키지가 없을 때 대비)
실행: python scripts/fetch_runtimes.py [--out data/agent_runtime] [--no-wheels]
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
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


def wheels(out: Path) -> Path:
    wdir = out / "wheels"
    wdir.mkdir(exist_ok=True)
    print("  Agent 의존성 wheel 받는 중 (win_amd64, cp312)...")
    subprocess.check_call([sys.executable, "-m", "pip", "download", "-q", "-r", str(ROOT / "requirements-agent.txt"),
                           "-d", str(wdir), "--platform", "win_amd64", "--python-version", "3.12",
                           "--implementation", "cp", "--only-binary=:all:"])
    zpath = out / "wheels.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
        for w in wdir.glob("*.whl"):
            z.write(w, w.name)
    shutil.rmtree(wdir)
    return zpath


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "agent_runtime"))
    ap.add_argument("--no-wheels", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

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
