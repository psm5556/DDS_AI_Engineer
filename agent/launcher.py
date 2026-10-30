"""DDS PC Agent 런처 — 설치 폴더 루트에 복사되어 버전과 무관하게 유지되는 작은 실행기.
current.txt의 버전을 실행하고,
- 종료코드 75(업데이트 완료) → 새 버전으로 재시작
- 업데이트 직후 30초 안에 비정상 종료 → previous.txt 버전으로 롤백
- 그 밖의 비정상 종료 → 10초 후 재시작(최대 연속 5회)
"""
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HOME = Path(__file__).resolve().parent
RESTART_CODE = 75


def log(msg: str):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [launcher] {msg}"
    print(line, flush=True)
    (HOME / "logs").mkdir(exist_ok=True)
    with open(HOME / "logs" / "launcher.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read(name: str) -> str:
    p = HOME / name
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def agent_python() -> str:
    """Agent 본체는 python.exe + 숨은 콘솔로 띄운다. pythonw면 그 아래 콘솔 프로그램이 창을 연다.
    이 파일은 버전 폴더 밖에 복사되어 cae_adapters를 불러올 수 없으므로 hidden.console_python과 같은 일을 직접 한다.
    자동 업데이트로 교체되지 않으니 Agent 쪽(hidden.py)이 이 파일에 의존하지 않는다."""
    exe = Path(sys.executable)
    python = exe.with_name("python.exe")
    return str(python) if exe.name.lower() == "pythonw.exe" and python.exists() else sys.executable


def main():
    just_updated, crashes = False, 0
    while True:
        version = read("current.txt")
        vdir = HOME / "versions" / version
        if not version or not vdir.exists():
            log(f"실행할 버전이 없습니다: '{version}'. install-agent.ps1을 다시 실행하세요.")
            return 1
        node, npm_global = HOME / "runtime" / "node", HOME / "npm-global"
        env = {**os.environ, "PYTHONPATH": str(vdir), "DDS_AGENT_HOME": str(HOME), "PYTHONIOENCODING": "utf-8",
               "NPM_CONFIG_PREFIX": str(npm_global),
               "PATH": os.pathsep.join([str(node), str(npm_global), os.environ.get("PATH", "")])}
        log(f"Agent {version} 시작")
        started = time.time()
        with open(HOME / "logs" / "agent.log", "a", encoding="utf-8") as out:
            rc = subprocess.call([agent_python(), "-m", "agent.dds_agent.main"], cwd=vdir, env=env,
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lived = time.time() - started
        if rc == RESTART_CODE:
            log(f"다시 시작 → {read('current.txt')}")
            just_updated, crashes = True, 0
            continue
        if rc == 0:
            log("정상 종료")
            return 0
        if just_updated and lived < 30 and read("previous.txt"):
            prev = read("previous.txt")
            log(f"새 버전 {version}이(가) 바로 종료되어(코드 {rc}) {prev}(으)로 롤백합니다")
            (HOME / "current.txt").write_text(prev, encoding="utf-8")
            (HOME / "failed_version.txt").write_text(version, encoding="utf-8")
            just_updated = False
            continue
        just_updated = False
        crashes = crashes + 1 if lived < 120 else 1
        if crashes > 5:
            log("연속 비정상 종료 5회 — 중지합니다. logs/agent.log를 확인하세요.")
            return rc
        log(f"비정상 종료(코드 {rc}) — 10초 후 재시작 ({crashes}/5)")
        time.sleep(10)


if __name__ == "__main__":
    sys.exit(main())
