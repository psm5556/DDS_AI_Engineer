#!/usr/bin/env python3
"""개발 AI의 원격 검증 도구: 서버(Linux)에서 개발하는 AI가 PC의 CAD/CAE 연동을 스스로 시험한다.
사용법과 순서는 docs/pc-verification.md. 표준 라이브러리만 쓴다(서버 가상환경 없이도 돈다. token 명령만 서버 코드가 필요하다).

  python scripts/pc_verify.py token                  서버 DB로 관리자 토큰을 만들어 data/pc_verify.token에 저장
  python scripts/pc_verify.py targets                시험할 수 있는 PC
  python scripts/pc_verify.py info                   PC의 Agent 버전·찾은 툴·AI CLI
  python scripts/pc_verify.py run check.py [인자…]    PC에서 파이썬 스크립트 실행(out/ 폴더 파일을 받아 온다)
  python scripts/pc_verify.py tool nx job.py [인자…]  대화와 같은 명령 키로 툴 실행(--input model.prt로 파일 전달)
  python scripts/pc_verify.py live nx info           Live Link 호출(status·info·capture·exec·set·view·undo)
  python scripts/pc_verify.py suite [--tool nx]      기본 점검 묶음(scripts/pc_checks/)
  python scripts/pc_verify.py logs                   PC의 Agent 로그
  python scripts/pc_verify.py fetch <PC 경로>         PC의 파일 받기(시험·작업·Agent 폴더만)
  python scripts/pc_verify.py deploy                 지금 코드를 배포하고 PC가 새 버전으로 뜰 때까지 기다림
  python scripts/pc_verify.py chat "요청" --agent claude  웹앱 대화와 똑같이 AI에게 시킨다

결과 전문은 data/pc_verify/<시각>/ 에 저장하고 요약을 출력한다. 시험이 실패하면 종료 코드가 1이다.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN_FILE = ROOT / "data" / "pc_verify.token"
CHECKS = ROOT / "scripts" / "pc_checks"
TAIL = 6000  # 화면에 보일 출력 끝부분. 전문은 파일로 남는다


class Api:
    def __init__(self, server: str, token: str | None):
        self.server, self.token = server.rstrip("/"), token

    def call(self, method: str, path: str, body: dict | None = None, timeout: float = 60):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.server + path, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")
            try:
                text = json.loads(text).get("detail", text)
            except ValueError:
                pass
            raise SystemExit(f"서버 오류 {e.code} ({path}): {text}")
        except urllib.error.URLError as e:
            raise SystemExit(f"서버에 연결할 수 없습니다({self.server}): {e.reason} → --server 또는 DDS_SERVER_URL을 확인하세요")
        return json.loads(raw) if raw else None


def load_token(args) -> str:
    if os.environ.get("DDS_VERIFY_TOKEN"):
        return os.environ["DDS_VERIFY_TOKEN"]
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    raise SystemExit("토큰이 없습니다 → python scripts/pc_verify.py token")


def out_dir() -> Path:
    d = ROOT / "data" / "pc_verify" / time.strftime("%Y%m%d_%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    return d


def report(res: dict, label: str = "") -> int:
    """전문과 받은 파일을 저장하고 요약을 보인다"""
    d = out_dir()
    files = res.pop("files", None) or {}
    for name, b64 in files.items():
        p = d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(base64.b64decode(b64))
    (d / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    stdout, stderr = res.get("stdout") or "", res.get("stderr") or ""
    short = {k: v for k, v in res.items() if k not in ("stdout", "stderr")}
    print(json.dumps(short, ensure_ascii=False, indent=1))
    if stdout:
        print("--- stdout ---\n" + stdout[-TAIL:])
    if stderr:
        print("--- stderr ---\n" + stderr[-TAIL:])
    print(f"--- 저장: {d}" + (f" (받은 파일 {', '.join(sorted(files))})" if files else ""))
    if label:
        print(f"--- {label}: {'통과' if res.get('ok') else '실패'}")
    return 0 if res.get("ok") else 1


def run_op(api: Api, cli, **body) -> dict:
    timeout = int(body.get("timeout_sec") or cli.timeout)
    body["timeout_sec"] = timeout
    if cli.user_id is not None:
        body["user_id"] = cli.user_id
    return api.call("POST", "/api/devtest/run", {k: v for k, v in body.items() if v is not None}, timeout=timeout + 90)


def _inputs(paths: list[str] | None) -> dict | None:
    if not paths:
        return None
    return {Path(p).name: base64.b64encode(Path(p).read_bytes()).decode("ascii") for p in paths}


# ---------- 명령 ----------
def cmd_token(args, _api) -> int:
    """서버 파일에 쓸 수 있으면 서버 관리자다. PIN 없이 토큰을 만들어 개발 AI가 혼자 진행하게 한다. 파일은 git에 들어가지 않는다."""
    sys.path.insert(0, str(ROOT))
    if args.data:
        os.environ["DDS_DATA_DIR"] = args.data
    from server.app import models
    from server.app.database import SessionLocal, ensure_columns
    from server.app.deps import new_api_token
    ensure_columns()  # 서버를 새 코드로 다시 띄우기 전에도 돌게 한다
    with SessionLocal() as db:
        q = db.query(models.User).filter(models.User.is_admin.is_(True))
        u = q.filter_by(username=args.username).first() if args.username else q.order_by(models.User.id).first()
        if not u:
            raise SystemExit("관리자 계정을 찾지 못했습니다")
        raw, hashed = new_api_token()
        db.add(models.ApiToken(user_id=u.id, name="개발 AI 원격 검증", token_hash=hashed))
        db.commit()
        name = u.username
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(raw, encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    print(f"{name} 계정의 토큰을 저장했습니다: {TOKEN_FILE}")
    # 서버가 다른 데이터 폴더를 쓰면 이 토큰을 모른다. 그 자리에서 알려 준다
    try:
        Api(args.server, raw).call("GET", "/api/auth/me")
        print(f"서버 확인 완료: {args.server}")
    except SystemExit as e:
        print(f"이 토큰을 서버가 받지 않습니다: {e}\n→ 서버의 데이터 폴더를 --data로 지정해 다시 만드세요 (개발용 실행은 --data data/dev)")
        return 1
    return 0


def cmd_targets(args, api) -> int:
    rows = api.call("GET", "/api/devtest/targets")
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    if not rows:
        print("시험할 수 있는 PC가 없습니다 → 관리자 계정으로 PC Agent를 연결하고 웹앱 설정에서 '개발 AI 원격 시험'을 허용해야 합니다")
        return 1
    return 0


def cmd_info(args, api) -> int:
    return report(run_op(api, args, op="info"), "정보")


def cmd_run(args, api) -> int:
    script = Path(args.script).read_text(encoding="utf-8")
    return report(run_op(api, args, op="python", script=script, filename=Path(args.script).name, args=args.rest,
                         files=_inputs(args.input)), f"실행 {args.script}")


def cmd_tool(args, api) -> int:
    script = Path(args.script).read_text(encoding="utf-8")
    return report(run_op(api, args, op="tool", tool=args.tool, script=script, filename=Path(args.script).name, args=args.rest,
                         files=_inputs(args.input)), f"툴 {args.tool}")


def cmd_live(args, api) -> int:
    script = (CHECKS / "live_call.py").read_text(encoding="utf-8")
    return report(run_op(api, args, op="python", script=script, filename="live_call.py", args=[args.tool, args.action, *args.rest]),
                  f"Live Link {args.tool} {args.action}")


def cmd_suite(args, api) -> int:
    code = report(run_op(api, args, op="python", script=(CHECKS / "tools.py").read_text(encoding="utf-8"), filename="tools.py"), "툴 찾기")
    if args.tool:
        code |= report(run_op(api, args, op="python", script=(CHECKS / "live.py").read_text(encoding="utf-8"), filename="live.py",
                              args=[args.tool]), f"Live Link {args.tool}")
    return code


def cmd_logs(args, api) -> int:
    return report(run_op(api, args, op="logs", lines=args.lines), "로그")


def cmd_fetch(args, api) -> int:
    res = run_op(api, args, op="fetch", path=args.path)
    if not res.get("ok"):
        print(res.get("error"))
        return 1
    dest = out_dir() / Path(res["path"]).name
    dest.write_bytes(base64.b64decode(res["data"]))
    print(f"받음: {dest} ({res['size']} 바이트)")
    return 0


def cmd_deploy(args, api) -> int:
    """지금 코드를 배포 → PC에 바로 업데이트 지시 → 새 버전으로 다시 붙을 때까지 기다린다"""
    me = api.call("GET", "/api/auth/me")
    latest = None
    try:
        latest = api.call("POST", "/api/agent/releases/from-server", {}, timeout=300)["version"]
        print(f"배포: {latest}")
    except SystemExit as e:
        if "409" not in str(e):
            raise
        latest = api.call("GET", "/api/agent/releases/latest")["version"]
        print(f"이미 배포된 버전: {latest} (코드를 고쳤다면 agent/dds_agent/__init__.py의 __version__을 올리세요)")
    uid = args.user_id or me["id"]
    api.call("POST", f"/api/agent/instances/{uid}/update", {})
    t0 = time.time()
    while time.time() - t0 < args.timeout:
        time.sleep(3)
        for t in api.call("GET", "/api/devtest/targets"):
            if t["user_id"] == uid and t.get("version") == latest:
                print(f"PC가 {latest}로 다시 연결됐습니다 ({int(time.time() - t0)}초)")
                return 0
    print(f"{args.timeout}초 안에 {latest}로 다시 연결되지 않았습니다 → logs 명령으로 확인하세요. 개발용 실행(dev_live)은 업데이트하지 않습니다")
    return 1


def cmd_chat(args, api) -> int:
    """사용자가 웹앱에서 하는 것과 똑같이: 프로젝트·대화를 만들고 요청하고 승인·허용까지 한다. 토큰 계정의 PC에서 돈다."""
    pid = args.project or api.call("POST", "/api/projects", {"name": f"연동 검증 {time.strftime('%m%d_%H%M%S')}"})["id"]
    sid = api.call("POST", f"/api/projects/{pid}/sessions", {"agent": args.agent})["id"]
    print(f"프로젝트 {pid} · 대화 {sid} · 에이전트 {args.agent}")
    api.call("POST", f"/api/sessions/{sid}/prompt", {"text": args.text}, timeout=120)
    t0, idle, last = time.time(), 0, 0
    while time.time() - t0 < args.timeout:
        time.sleep(2)
        st = api.call("GET", f"/api/sessions/{sid}/status")
        for p in st.get("pending_permissions") or []:
            options = p.get("options") or []
            allow = next((o for o in options if "allow" in str(o.get("kind", "")) or "allow" in str(o.get("optionId", ""))), options[0] if options else None)
            if allow:
                api.call("POST", f"/api/sessions/{sid}/permission", {"request_id": p["request_id"], "option_id": allow.get("optionId")})
                print(f"허용: {p.get('title') or p['request_id']}")
        approved = False
        for f in [] if st.get("busy") else api.call("GET", f"/api/projects/{pid}/flows"):
            if f.get("status") == "review":  # 웹앱의 [승인하고 실행]과 같게: 상태를 바꾸고 에이전트에게 알린다(useFlowDecision)
                api.call("PUT", f"/api/flows/{f['id']}/status", {"status": "approved", "version": f.get("version")})
                api.call("POST", f"/api/sessions/{sid}/prompt", {"text": f"작업 흐름 '{f.get('title')}'(#{f['id']})을 승인했습니다. 계획대로 진행해 주세요."}, timeout=120)
                print(f"작업 흐름 승인: {f.get('title') or f['id']}")
                approved = True
        msgs = api.call("GET", f"/api/sessions/{sid}/messages")
        for m in msgs[last:]:
            text = (m.get("content") or "").strip()
            if text:
                print(f"[{m.get('role')}] {text[:800]}")
        last = len(msgs)
        idle = 0 if st.get("busy") or approved or st.get("pending_permissions") else idle + 1
        if idle >= args.settle:  # 턴이 끝나고 더 이어지지 않으면 끝이다
            print(f"끝남 ({int(time.time() - t0)}초). 결과는 웹앱 프로젝트 {pid}에서 볼 수 있습니다")
            return 0
    print(f"{args.timeout}초 안에 끝나지 않았습니다")
    return 1


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description="개발 AI의 PC 원격 검증 (docs/pc-verification.md)")
    ap.add_argument("--server", default=os.environ.get("DDS_SERVER_URL", "http://127.0.0.1:7014"))
    ap.add_argument("--user-id", type=int, help="시험할 PC의 계정 id(허용한 PC가 여럿일 때)")
    ap.add_argument("--timeout", type=int, default=600, help="제한 시간(초)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("token"); p.add_argument("--username"); p.add_argument("--data", help="서버 데이터 폴더(기본: 서버 설정)"); p.set_defaults(fn=cmd_token)
    sub.add_parser("targets").set_defaults(fn=cmd_targets)
    sub.add_parser("info").set_defaults(fn=cmd_info)
    p = sub.add_parser("run"); p.add_argument("script"); p.add_argument("--input", action="append"); p.add_argument("rest", nargs="*"); p.set_defaults(fn=cmd_run)
    p = sub.add_parser("tool"); p.add_argument("tool"); p.add_argument("script"); p.add_argument("--input", action="append"); p.add_argument("rest", nargs="*"); p.set_defaults(fn=cmd_tool)
    p = sub.add_parser("live"); p.add_argument("tool"); p.add_argument("action"); p.add_argument("rest", nargs="*"); p.set_defaults(fn=cmd_live)
    p = sub.add_parser("suite"); p.add_argument("--tool"); p.set_defaults(fn=cmd_suite)
    p = sub.add_parser("logs"); p.add_argument("--lines", type=int, default=300); p.set_defaults(fn=cmd_logs)
    p = sub.add_parser("fetch"); p.add_argument("path"); p.set_defaults(fn=cmd_fetch)
    sub.add_parser("deploy").set_defaults(fn=cmd_deploy)
    p = sub.add_parser("chat"); p.add_argument("text"); p.add_argument("--agent", default="claude"); p.add_argument("--project", type=int)
    p.add_argument("--settle", type=int, default=5, help="몇 번 연속 조용하면 끝으로 볼지(2초 단위)"); p.set_defaults(fn=cmd_chat)

    args = ap.parse_args()
    api = Api(args.server, None if args.cmd == "token" else load_token(args))
    return args.fn(args, api)


if __name__ == "__main__":
    sys.exit(main())
