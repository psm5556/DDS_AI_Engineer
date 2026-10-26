"""사내 서버 준비를 한 번에: 웹 화면 빌드 · PC Agent 설치 프로그램 · 포터블 런타임 · 첫 Agent 버전.
git으로 저장소만 받은 사내 서버에서 실행한다. 인터넷은 필요 없고 사내 pip·npm 미러만 쓴다.

  python scripts\\prepare_onsite.py                                  # 웹 빌드 + 설치 프로그램 + 런타임
  python scripts\\prepare_onsite.py --publish --server http://127.0.0.1:7014 --user admin --pin <PIN>
                                                                     # + 서버 코드로 첫 Agent 버전 등록(서버 실행 중)
  python scripts\\prepare_onsite.py --check --server http://127.0.0.1:7014 --user admin --pin <PIN>
                                                                     # 준비 상태만 확인
옵션: --skip-frontend · --force-runtime(런타임 다시 만들기) · --no-wheels
결과는 서버 데이터 폴더(server.toml의 data_dir 또는 DDS_DATA_DIR, 기본 data\\)의 agent_runtime\\ 에 생긴다.
마지막 줄에 READY 또는 NOT READY를 출력하고, 준비가 안 되면 종료 코드 1로 끝난다.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:  # 사내 콘솔(cp949)에서도 한글·기호가 깨져 멈추지 않게
    sys.stdout.reconfigure(errors="replace")
except AttributeError:
    pass


def step(msg: str):
    print(f"\n== {msg}", flush=True)


def ok(msg: str):
    print(f"   [OK] {msg}", flush=True)


def bad(msg: str):
    print(f"   [X] {msg}", flush=True)


def data_dir() -> Path:
    from server.app.config import settings  # 서버와 같은 규칙(server.toml · DDS_DATA_DIR)으로 데이터 폴더를 정한다

    return Path(settings.data_dir)


def build_frontend() -> bool:
    step("웹 화면 빌드 (frontend\\dist)")
    if (ROOT / "frontend" / "dist" / "index.html").exists():
        ok("이미 빌드돼 있습니다 (다시 빌드: cd frontend; npm run build)")
        return True
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        bad("npm이 없습니다. Node.js를 설치하세요(사내 npm 미러 사용)")
        return False
    fe = ROOT / "frontend"
    cmd = [npm, "ci"] if (fe / "package-lock.json").exists() else [npm, "install"]
    if subprocess.call(cmd, cwd=fe) != 0 and subprocess.call([npm, "install"], cwd=fe) != 0:
        bad("npm 패키지 설치 실패 — 사내 npm 미러 설정 확인: npm config get registry")
        return False
    if subprocess.call([npm, "run", "build"], cwd=fe) != 0:
        bad("빌드 실패 — 출력의 TypeScript 오류를 고치세요")
        return False
    ok("빌드 완료")
    return True


def build_setup_exe(rt: Path) -> bool:
    step("PC Agent 설치 프로그램 (DDS-Agent-Setup.exe)")
    out = rt / "DDS-Agent-Setup.exe"
    if out.exists():
        ok(f"이미 있습니다: {out}")
        return True
    csc = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not csc.exists():
        bad(f"csc.exe가 없습니다({csc}). Windows의 .NET Framework 4.x가 필요합니다")
        return False
    rt.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([str(csc), "/nologo", "/target:winexe", "/codepage:65001", "/optimize+", f"/out:{out}",
                        "/r:System.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll",
                        str(ROOT / "installer" / "DdsAgentSetup.cs")], capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not out.exists():
        bad("빌드 실패:\n" + (r.stdout or "") + (r.stderr or ""))
        return False
    ok(f"빌드 완료: {out}")
    return True


def runtime_ready(rt: Path) -> bool:
    m = rt / "manifest.json"
    if not m.exists():
        return False
    files = [v["file"] for v in json.loads(m.read_text(encoding="utf-8")).values() if isinstance(v, dict) and v.get("file")]
    return bool(files) and all((rt / f).exists() for f in files)


def build_runtime(rt: Path, force: bool, wheels: bool) -> bool:
    step("포터블 런타임 (이 서버의 Python·Node를 묶고, wheel은 사내 pip 미러에서)")
    if runtime_ready(rt) and not force:
        ok(f"이미 있습니다: {rt}\\manifest.json (다시 만들기: --force-runtime)")
        return True
    cmd = [sys.executable, str(ROOT / "scripts" / "fetch_runtimes.py"), "--from-local", "--out", str(rt)]
    if not wheels:
        cmd.append("--no-wheels")
    if subprocess.call(cmd, env={**os.environ, "PYTHONIOENCODING": "utf-8"}) != 0 or not runtime_ready(rt):
        bad("런타임을 만들지 못했습니다 — 위 출력의 원인을 확인하세요")
        return False
    ok("런타임 준비 완료")
    return True


def _client(server: str, user: str, pin: str):
    import httpx

    c = httpx.Client(base_url=server, timeout=600)
    r = c.post("/api/auth/login", json={"username": user, "pin": pin})
    if r.status_code != 200:
        raise SystemExit(f"   [X] 관리자 로그인 실패({r.status_code}): {r.text}")
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"
    return c


def publish(server: str, user: str, pin: str) -> bool:
    step("첫 Agent 버전 등록 (서버 코드로)")
    c = _client(server, user, pin)
    r = c.post("/api/agent/releases/from-server")
    if r.status_code == 409:
        ok(f"이미 등록된 버전입니다: {r.json().get('detail')}")
        return True
    if r.status_code != 200:
        bad(f"등록 실패({r.status_code}): {r.text}")
        return False
    ok(f"등록 완료: {r.json()['version']} (최신)")
    return True


def check(server: str | None, user: str, pin: str, rt: Path) -> bool:
    step("준비 상태 확인")
    if server:
        st = _client(server, user, pin).get("/api/agent/setup/status").json()
        for k, label in (("setup_exe", "설치 프로그램"), ("runtime", "포터블 런타임")):
            (ok if st[k] else bad)(label)
        (ok if st["release"] else bad)(f"배포된 Agent 버전 {st['release'] or '없음 (--publish 또는 웹 관리 > Agent 배포)'}")
        return bool(st["ready"])
    exe, run = (rt / "DDS-Agent-Setup.exe").exists(), runtime_ready(rt)
    (ok if exe else bad)("설치 프로그램")
    (ok if run else bad)("포터블 런타임")
    print("   (배포된 Agent 버전은 --server로 서버에 물어봐야 확인됩니다)")
    return exe and run


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", help="실행 중인 서버 주소 (예: http://127.0.0.1:7014)")
    ap.add_argument("--user", default=os.environ.get("DDS_ADMIN_USER", "admin"))
    ap.add_argument("--pin", default=os.environ.get("DDS_ADMIN_PIN", ""))
    ap.add_argument("--publish", action="store_true", help="서버 코드로 Agent 버전을 등록(--server 필요)")
    ap.add_argument("--check", action="store_true", help="만들지 않고 상태만 확인")
    ap.add_argument("--skip-frontend", action="store_true")
    ap.add_argument("--force-runtime", action="store_true")
    ap.add_argument("--no-wheels", action="store_true")
    a = ap.parse_args()
    if os.name != "nt":
        raise SystemExit("Windows 서버에서 실행하세요 (설치 프로그램·런타임이 Windows용입니다)")
    if (a.publish or a.check and a.server) and not a.pin:
        raise SystemExit("--pin(또는 DDS_ADMIN_PIN)이 필요합니다")

    rt = data_dir() / "agent_runtime"
    print(f"서버 데이터 폴더: {rt.parent}\n런타임 폴더: {rt}")
    good = True
    if not a.check:
        if not a.skip_frontend:
            good &= build_frontend()
        good &= build_setup_exe(rt)
        good &= build_runtime(rt, a.force_runtime, not a.no_wheels)
        if a.publish:
            if not a.server:
                raise SystemExit("--publish에는 --server가 필요합니다")
            good &= publish(a.server, a.user, a.pin)
    ready = check(a.server, a.user, a.pin, rt) and good
    print("\nREADY — 사용자는 웹앱 설정 > 설치에서 PC Agent를 받을 수 있습니다" if ready else
          "\nNOT READY — [X] 항목을 해결한 뒤 다시 실행하세요 (docs/onsite-ai-runbook.md 참고)")
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
