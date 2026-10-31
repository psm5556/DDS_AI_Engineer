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
      --attach 파일 · --session N(같은 대화 이어서) · --no-approve · --expect deployed,no_cases_run,flows=0 · --max-first-reply 초
      측정값(metrics.json)을 저장하고 합격을 판정한다. 기대를 어기면 종료 코드 1

결과 전문은 data/pc_verify/<시각>/ 에 저장하고 요약을 출력한다. 시험이 실패하면 종료 코드가 1이다.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
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
        return self._send(req, path, timeout)

    def upload(self, path: str, file: Path, timeout: float = 600):
        """파일 하나를 multipart로 올린다. 표준 라이브러리만 쓴다."""
        boundary = f"----ddsverify{uuid.uuid4().hex}"
        head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file.name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
        req = urllib.request.Request(self.server + path, data=head + file.read_bytes() + f"\r\n--{boundary}--\r\n".encode(), method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        return self._send(req, path, timeout)

    def _send(self, req, path: str, timeout: float):
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
        except TimeoutError:  # 응답을 기다리다 넘긴 시간 초과는 URLError로 싸이지 않는다. 그대로 두면 스택 트레이스로 끝난다
            raise SystemExit(f"서버가 {timeout:.0f}초 안에 답하지 않았습니다 ({path}) → 서버 상태를 확인하거나 --timeout을 늘리세요")
        except OSError as e:  # 응답 도중 끊김(서버 재시작 등)
            raise SystemExit(f"서버 연결이 끊겼습니다 ({path}): {e} → 서버 상태를 확인하세요")
        return json.loads(raw) if raw else None


def load_token(args) -> str:
    if os.environ.get("DDS_VERIFY_TOKEN"):
        return os.environ["DDS_VERIFY_TOKEN"]
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text(encoding="utf-8").strip()
    raise SystemExit("토큰이 없습니다 → python scripts/pc_verify.py token")


def out_dir() -> Path:
    base = Path(os.environ["DDS_VERIFY_OUT"]) if os.environ.get("DDS_VERIFY_OUT") else ROOT / "data" / "pc_verify"
    d = base / time.strftime("%Y%m%d_%H%M%S")
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


def _tool_name(m: dict) -> str:
    return str((m.get("meta") or {}).get("title") or m.get("content") or "")


def chat_flow_ids(msgs: list[dict]) -> tuple[set[int], int]:
    """이 대화가 제안한 Flow id와 propose_flow 호출 수. 프로젝트의 Flow에는 대화 표시가 없어 도구 출력에서 찾는다."""
    ids, calls = set(), 0
    for m in msgs:
        if m.get("role") == "tool" and "propose_flow" in _tool_name(m):
            calls += 1
            found = re.search(r'"?flow_id"?\s*[:=]\s*(\d+)', str((m.get("meta") or {}).get("output") or ""))
            if found:
                ids.add(int(found.group(1)))
    return ids, calls


def own_review_flows(flows: list[dict], msgs: list[dict], known_before: set[int]) -> list[dict]:
    """승인할 Flow: 이 대화가 제안한 것만. --project로 함께 쓰는 프로젝트의 다른 대화 Flow를 멋대로 승인하지 않는다.
    도구 출력이 잘려 id를 못 읽었으면, 이 대화가 propose_flow를 불렀고 시작 뒤에 새로 생긴 것으로 본다."""
    ids, calls = chat_flow_ids(msgs)
    mine = [f for f in flows if f["id"] in ids or (calls > len(ids) and f["id"] not in known_before)]
    return [f for f in mine if f.get("status") == "review"]


def is_note(text: str) -> bool:
    """오류가 아닌 시스템 안내(CLI 세션을 새로 열었다는 알림 등)."""
    return "새 세션" in text


EXPECT_NUMBER = re.compile(r"^(\w+)\s*(>=|<=|=)\s*(-?\d+(?:\.\d+)?)$")


def judge(expects: list[str], metrics: dict) -> list[dict]:
    """--expect 항목을 측정값과 맞춘다.
    deployed · no_cases_run · no_errors · finished / 숫자: flows=0 models>=1 flows_approved=0 permissions<=2 questions=0 first_reply_sec<=300 /
    tool:이름 · no_tool:이름 · reply~글자 · no_reply~글자. 모르는 항목은 실패로 친다(오타가 조용히 통과하지 않게)."""
    out = []
    reply = metrics.get("reply") or ""
    tools = metrics.get("tools") or []
    for e in expects:
        e = e.strip()
        if not e:
            continue
        actual: object = None
        ok = False
        num = EXPECT_NUMBER.match(e)
        if e == "deployed":
            actual, ok = metrics.get("deployed_models", 0), metrics.get("deployed_models", 0) > 0
        elif e == "no_cases_run":
            actual, ok = metrics.get("cases_run", 0), metrics.get("cases_run", 0) == 0 and not any("run_cases" in t for t in tools)
        elif e == "no_errors":
            actual, ok = metrics.get("errors") or [], not metrics.get("errors")
        elif e == "finished":
            actual, ok = metrics.get("finished"), bool(metrics.get("finished"))
        elif e.startswith("tool:"):
            actual, ok = tools, any(e[5:] in t for t in tools)
        elif e.startswith("no_tool:"):
            actual, ok = tools, not any(e[8:] in t for t in tools)
        elif e.startswith("reply~"):
            actual, ok = reply[-300:], e[6:] in reply
        elif e.startswith("no_reply~"):
            actual, ok = reply[-300:], e[9:] not in reply
        elif num and isinstance(metrics.get(num.group(1)), (int, float)):
            actual = metrics[num.group(1)]
            want = float(num.group(3))
            ok = {"=": actual == want, ">=": actual >= want, "<=": actual <= want}[num.group(2)]
        else:
            actual = "측정값 없음"  # 오타이거나 첫 응답이 오지 않았다
        out.append({"expect": e, "actual": actual, "ok": ok})
    return out


def _project_of_session(api, sid: int) -> int:
    for p in api.call("GET", "/api/projects"):
        if any(s["id"] == sid for s in api.call("GET", f"/api/projects/{p['id']}/sessions")):
            return p["id"]
    raise SystemExit(f"대화 {sid}를 찾지 못했습니다 → 이 토큰 계정이 만든 대화인지 확인하세요")


def _run_cases_total(api, pid: int) -> int:
    """이 프로젝트에서 해석으로 끝난 케이스 수(불러온 값 제외). 요청 전후를 비교해 해석을 돌렸는지 잰다."""
    total = 0
    for s in api.call("GET", f"/api/projects/{pid}/studies"):
        total += sum(c["status"] == "complete" and c.get("source") != "import" for c in api.call("GET", f"/api/studies/{s['id']}/cases"))
    return total


def _steady(api, method: str, path: str, body: dict | None = None, *, seconds: float = 120, timeout: float = 60):
    """폴링은 서버가 잠깐 안 보여도 판정을 망치지 않게 기다린다. 넘으면 오류를 그대로 낸다."""
    end = time.time() + seconds
    while True:
        try:
            return api.call(method, path, body, timeout=timeout)
        except SystemExit:
            if time.time() > end:
                raise
            time.sleep(3)


def _send_prompt(api, sid: int, text: str, attachment_ids: list[int], until: float):
    """요청을 보낸다. 앞 턴이 아직 돌면 서버가 409를 주므로 끝나기를 기다린다. POST는 CLI 시작을 기다리지 않고 바로 돌아오지만
    사내 서버가 바쁠 수 있어 제한을 넉넉히 둔다(예전 120초는 느린 환경에서 검증 자체를 끝내 버렸다)."""
    while True:
        try:
            return api.call("POST", f"/api/sessions/{sid}/prompt", {"text": text, "attachment_ids": attachment_ids}, timeout=600)
        except SystemExit as e:
            if "409" not in str(e) or time.time() > until:
                raise
            time.sleep(3)


def cmd_chat(args, api) -> int:
    """사용자가 웹앱에서 하는 것과 똑같이: 프로젝트·대화를 만들고 요청하고 승인·허용까지 한다. 토큰 계정의 PC에서 돈다.
    자동 승인이 돌아가는 경로를 숨기지 않게 승인·허용 횟수와 호출한 도구를 재고, --expect로 합격을 판정한다."""
    if args.session:
        sid = args.session
        pid = args.project or _project_of_session(api, sid)
    else:
        pid = args.project or api.call("POST", "/api/projects", {"name": f"연동 검증 {time.strftime('%m%d_%H%M%S')}"})["id"]
        sid = api.call("POST", f"/api/projects/{pid}/sessions", {"agent": args.agent})["id"]
    print(f"프로젝트 {pid} · 대화 {sid} · 에이전트 {args.agent}")
    attachment_ids = []
    for p in args.attach or []:
        if not Path(p).is_file():
            raise SystemExit(f"첨부할 파일이 없습니다: {p} → 경로를 확인하세요")
        attachment_ids.append(api.upload(f"/api/projects/{pid}/attachments", Path(p))["id"])
    before = api.call("GET", f"/api/sessions/{sid}/messages")
    start, last = len(before), len(before)
    known_flows = {f["id"] for f in api.call("GET", f"/api/projects/{pid}/flows")}
    cases_before = _run_cases_total(api, pid)
    t0 = time.time()
    _send_prompt(api, sid, args.text, attachment_ids, t0 + args.timeout)
    idle, finished, first_reply, approvals, permissions = 0, False, None, [], []
    msgs = before
    while time.time() - t0 < args.timeout:
        time.sleep(args.poll)
        st = _steady(api, "GET", f"/api/sessions/{sid}/status")
        for p in st.get("pending_permissions") or []:
            options = p.get("options") or []
            allow = next((o for o in options if "allow" in str(o.get("kind", "")) or "allow" in str(o.get("optionId", ""))), options[0] if options else None)
            if allow:
                api.call("POST", f"/api/sessions/{sid}/permission", {"request_id": p["request_id"], "option_id": allow.get("optionId")})
                permissions.append(p.get("title") or p["request_id"])
                print(f"허용: {p.get('title') or p['request_id']}")
        msgs = _steady(api, "GET", f"/api/sessions/{sid}/messages")
        approved = False
        if not st.get("busy") and not args.no_approve:
            for f in own_review_flows(api.call("GET", f"/api/projects/{pid}/flows"), msgs, known_flows):
                # 웹앱의 [승인하고 실행]과 같게: 상태를 바꾸고 에이전트에게 알린다(useFlowDecision)
                api.call("PUT", f"/api/flows/{f['id']}/status", {"status": "approved", "version": f.get("version")})
                _send_prompt(api, sid, f"작업 흐름 '{f.get('title')}'(#{f['id']})을 승인했습니다. 계획대로 진행해 주세요.", [], t0 + args.timeout)
                approvals.append(f["id"])
                print(f"작업 흐름 승인: {f.get('title') or f['id']}")
                approved = True
        for m in msgs[last:]:
            text = (m.get("content") or "").strip()
            if text:
                print(f"[{m.get('role')}] {text[:800]}")
        last = len(msgs)
        if first_reply is None and any(m["role"] == "agent" and (m.get("content") or "").strip() for m in msgs[start:]):
            first_reply = round(time.time() - t0, 1)
        idle = 0 if st.get("busy") or approved or st.get("pending_permissions") else idle + 1
        if idle >= args.settle:  # 턴이 끝나고 더 이어지지 않으면 끝이다
            finished = True
            break
    msgs = _steady(api, "GET", f"/api/sessions/{sid}/messages")
    new = msgs[start:]
    flows = api.call("GET", f"/api/projects/{pid}/flows")
    ids, calls = chat_flow_ids(new)
    models = api.call("GET", f"/api/projects/{pid}/models")
    replies = [m["content"] for m in new if m["role"] == "agent"]
    metrics = {
        "project": pid, "session": sid, "agent": args.agent, "finished": finished, "seconds": round(time.time() - t0, 1),
        "first_reply_sec": first_reply,
        "flows": calls, "flow_ids": sorted(ids), "flows_in_project": len(flows), "flow_status": {f["id"]: f["status"] for f in flows if f["id"] in ids},
        "flows_approved": len(approvals), "permissions": len(permissions), "permission_titles": permissions,
        "questions": sum(("?" in r or "？" in r) for r in replies),  # 사용자에게 되물은 답. 표·질문 요청에서 0이어야 한다
        "tools": [_tool_name(m) for m in new if m["role"] == "tool"],
        "tools_failed": [_tool_name(m) for m in new if m["role"] == "tool" and (m.get("meta") or {}).get("status") == "failed"],
        "errors": [m["content"] for m in new if m["role"] == "system" and not is_note(m["content"])],
        "notes": [m["content"] for m in new if m["role"] == "system" and is_note(m["content"])],
        "models": len(models), "deployed_models": sum(bool(m.get("deployed")) for m in models),
        "cases_run": _run_cases_total(api, pid) - cases_before, "reply": "\n".join(replies),
    }
    expects = ["finished", *([] if args.allow_errors else ["no_errors"])]
    for e in args.expect or []:
        expects += e.split(",")
    if args.max_first_reply is not None:
        expects.append(f"first_reply_sec<={args.max_first_reply}")
    checks = judge(expects, metrics)
    passed = all(c["ok"] for c in checks)
    d = out_dir()
    (d / "metrics.json").write_text(json.dumps({"request": args.text, "attachments": args.attach or [], "passed": passed, "checks": checks, **metrics},
                                               ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (d / "messages.json").write_text(json.dumps(new, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in metrics.items() if k != "reply"}, ensure_ascii=False, indent=1))
    for c in checks:
        print(f"{'통과' if c['ok'] else '실패'}: {c['expect']}" + ("" if c["ok"] else f" (실제 {c['actual']})"))
    print(f"--- 저장: {d}")
    if not finished:
        print(f"{args.timeout}초 안에 끝나지 않았습니다")
    print(f"--- 대화 {sid}: {'합격' if passed else '불합격'} ({metrics['seconds']}초). 이어서 시험하려면 --session {sid}")
    return 0 if passed else 1


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
    p.add_argument("--settle", type=int, default=5, help="몇 번 연속 조용하면 끝으로 볼지(--poll 단위)")
    p.add_argument("--poll", type=float, default=2.0, help="상태를 확인하는 간격(초)")
    p.add_argument("--attach", action="append", help="대화에 첨부할 파일(여러 번 쓸 수 있다)")
    p.add_argument("--session", type=int, help="이 대화를 이어서 요청한다(세션 재사용·후속 요청 시험)")
    p.add_argument("--no-approve", action="store_true", help="작업 흐름을 자동으로 승인하지 않는다(승인 없이 끝나는지 볼 때)")
    p.add_argument("--expect", action="append", help="합격 기준, 쉼표로 여럿. 예: deployed,no_cases_run,flows=0,tool:import_table,no_tool:run_cases,reply~Simulator")
    p.add_argument("--max-first-reply", type=float, help="첫 응답까지 허용하는 시간(초)")
    p.add_argument("--allow-errors", action="store_true", help="대화의 오류 안내가 있어도 그것만으로 실패로 치지 않는다")
    p.set_defaults(fn=cmd_chat)

    args = ap.parse_args()
    api = Api(args.server, None if args.cmd == "token" else load_token(args))
    return args.fn(args, api)


if __name__ == "__main__":
    sys.exit(main())
