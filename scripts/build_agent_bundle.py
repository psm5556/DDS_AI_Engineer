"""PC Agent 배포 번들(zip) 생성 및 서버 등록.
  python scripts/build_agent_bundle.py                         → dist/dds-agent-<버전>.zip
  python scripts/build_agent_bundle.py --publish --server http://서버:8765 --notes "변경 내용"
     (관리자 계정 DDS_ADMIN_USER / DDS_ADMIN_PASSWORD 환경변수 또는 --user/--password)
버전은 agent/dds_agent/__init__.py 의 __version__ 이다. 배포 전에 올린다.
"""
import argparse
import os
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INCLUDE = ["agent", "cae_adapters", "mcp_servers", "agent-context", "docs/skill-contract.md",
           "requirements-agent.txt"]
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "tests"}


VERSION_FILE = ROOT / "agent" / "dds_agent" / "__init__.py"


def version() -> str:
    return re.search(r'__version__\s*=\s*"([^"]+)"', VERSION_FILE.read_text(encoding="utf-8")).group(1)


def build(ver: str) -> Path:
    out = ROOT / "dist" / f"dds-agent-{ver}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in INCLUDE:
            src = ROOT / item
            files = [src] if src.is_file() else [p for p in src.rglob("*") if p.is_file()]
            for f in files:
                if EXCLUDE_DIRS & set(f.relative_to(ROOT).parts) or f.suffix == ".pyc":
                    continue
                if f == VERSION_FILE:  # --version으로 지정한 버전을 번들 안에 기록
                    z.writestr(f.relative_to(ROOT).as_posix(),
                               re.sub(r'__version__\s*=\s*"[^"]+"', f'__version__ = "{ver}"', f.read_text(encoding="utf-8")))
                    continue
                z.write(f, f.relative_to(ROOT).as_posix())
        z.writestr("VERSION", ver)
    print(f"번들 생성: {out} ({out.stat().st_size // 1024} KB)")
    return out


def publish(bundle: Path, ver: str, server: str, user: str, password: str, notes: str, latest: bool):
    import httpx

    c = httpx.Client(base_url=server, timeout=120)
    r = c.post("/api/auth/login", json={"username": user, "password": password})
    r.raise_for_status()
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    with open(bundle, "rb") as f:
        r = c.post("/api/agent/releases", files={"file": (bundle.name, f, "application/zip")},
                   data={"version": ver, "notes": notes, "make_latest": str(latest).lower()})
    if r.status_code >= 400:
        sys.exit(f"등록 실패 {r.status_code}: {r.text}")
    print(f"서버 등록 완료: {r.json()['version']} (최신: {r.json()['is_latest']})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--server", default=os.environ.get("DDS_SERVER_URL", "http://127.0.0.1:8765"))
    ap.add_argument("--user", default=os.environ.get("DDS_ADMIN_USER", "admin"))
    ap.add_argument("--password", default=os.environ.get("DDS_ADMIN_PASSWORD", ""))
    ap.add_argument("--notes", default="")
    ap.add_argument("--no-latest", action="store_true", help="등록만 하고 최신으로 지정하지 않음(단계적 배포)")
    ap.add_argument("--version", help="번들 버전(기본: agent/dds_agent/__init__.py 의 __version__)")
    a = ap.parse_args()
    ver = a.version or version()
    b = build(ver)
    if a.publish:
        publish(b, ver, a.server, a.user, a.password, a.notes, not a.no_latest)
