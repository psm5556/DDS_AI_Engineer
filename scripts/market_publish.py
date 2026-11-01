"""폴더를 묶어 마켓에 올린다(관리자 전용, 일반 사용자는 서버가 거절한다). 이전 방식의 스킬 등록 API와 검사를 지난다.
  python scripts/market_publish.py <폴더> --server http://서버:7014 [--user 아이디] [--pin 123456]
    (옵션 없음)  초안만 만든다. 이어서 정할 화면은 지금 없다
    --private    나만 쓰기: 올리고 바로 내 PC에 설치한다
    --review     부서에 검토 요청
    --official   관리부서 제공으로 바로 공개(관리자 + PIN). 표시 항목이 있으면 --ack-all도 필요하다
    --ack-all    자동 검사가 표시한 항목을 모두 읽었다고 확인한다. 읽은 기록으로 남는다
아이디·PIN은 DDS_ADMIN_USER·DDS_ADMIN_PIN 환경변수로도 줄 수 있다. PIN을 주지 않으면 묻는다.
"""
from __future__ import annotations

import argparse
import getpass
import io
import os
import sys
import zipfile
from pathlib import Path

import httpx

JUNK_PARTS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode"}
JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini", ".gitattributes", ".gitignore"}
MAX_FILES = 200  # 서버 한도와 같다. 큰 폴더를 다 묶어 보내기 전에 알린다


def pack(folder: Path) -> bytes:
    files = [p for p in sorted(folder.rglob("*")) if p.is_file()]
    files = [p for p in files if not (set(p.relative_to(folder).parts[:-1]) & JUNK_PARTS) and p.name.lower() not in JUNK_FILES]
    if not files:
        sys.exit(f"올릴 파일이 없습니다: {folder} → SKILL.md가 있는 폴더를 지정하세요")
    if len(files) > MAX_FILES:
        sys.exit(f"파일이 {MAX_FILES}개를 넘습니다({len(files)}개) → 필요한 스크립트와 문서만 남겨 주세요")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, f"{folder.name}/{p.relative_to(folder).as_posix()}")
    return buf.getvalue()


def detail(r: httpx.Response) -> str:
    try:
        return str(r.json().get("detail"))
    except ValueError:
        return r.text


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="폴더를 마켓에 올린다")
    ap.add_argument("folder")
    ap.add_argument("--server", default=os.environ.get("DDS_SERVER_URL", "http://127.0.0.1:7014"))
    ap.add_argument("--user", default=os.environ.get("DDS_ADMIN_USER", "admin"))
    ap.add_argument("--pin", default=os.environ.get("DDS_ADMIN_PIN", ""))
    ap.add_argument("--notes", default="", help="이번 버전에서 바뀐 내용")
    how = ap.add_mutually_exclusive_group()
    how.add_argument("--private", action="store_true")
    how.add_argument("--review", action="store_true")
    how.add_argument("--official", action="store_true")
    ap.add_argument("--ack-all", action="store_true")
    a = ap.parse_args()

    folder = Path(a.folder).resolve()
    if not folder.is_dir():
        sys.exit(f"폴더가 아닙니다: {folder} → SKILL.md가 있는 폴더를 지정하세요")
    pin = a.pin or getpass.getpass("PIN: ")
    c = httpx.Client(base_url=a.server, timeout=120)
    r = c.post("/api/auth/login", json={"username": a.user, "pin": pin})
    if r.status_code != 200:
        sys.exit(f"로그인 실패: {detail(r)}")
    c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

    r = c.post("/api/market/submissions", files={"file": (f"{folder.name}.zip", pack(folder), "application/zip")},
               data={"source": "cli", "name_hint": folder.name, "notes": a.notes})
    if r.status_code != 200:
        sys.exit(f"받지 못했습니다: {detail(r)}")
    sub = r.json()
    pkg, version, report = sub["package"], sub["version"], sub["report"]
    seen = sub["fingerprint"]  # 방금 올린 내용에만 결정이 적용된다
    print(f"초안을 만들었습니다: {pkg['name']} {version} ({'코드 있음' if pkg['code'] else '문서뿐'})")
    print("자동 검사 통과. 자동 검사는 탐지일 뿐이며 안전을 보장하지 않습니다.")
    for fix in report.get("fixes") or []:
        print(f"  고침: {fix}")
    for warn in report.get("warnings") or []:
        print(f"  주의: {warn}")
    flags = report.get("flags") or []
    for f in flags:
        print(f"  표시: {f['title']} ({f.get('total', len(f['hits']))}곳) - {f['why']}")
        for h in f["hits"][:3]:
            print(f"        {h['path']}{':' + str(h['line']) if h.get('line') else ''}  {h['text']}")
    if report.get("other_scripts"):
        print("  Python 외 스크립트는 자동 검사하지 않았습니다: " + ", ".join(report["other_scripts"]))

    base = f"/api/market/packages/{pkg['id']}/versions/{version}"
    if a.private:
        r = c.post(f"{base}/private", json={"fingerprint": seen})
        done = "나만 쓰기로 정했습니다. PC Agent가 설치합니다"
    elif a.review:
        r = c.post(f"{base}/request-review", json={"fingerprint": seen})
        done = "부서에 검토를 요청했습니다"
    elif a.official:
        if flags and not a.ack_all:
            print("\n표시 항목을 읽은 뒤 --ack-all을 붙여 다시 실행하세요. 초안은 그대로 있습니다.")
            return 2
        r = c.post(f"{base}/approve", json={"pin": pin, "official": True, "acks": [f["rule"] for f in flags], "fingerprint": seen})
        done = "관리부서 제공으로 공개했습니다"
    else:
        print("\n초안만 만들었습니다 → 공개하려면 --official을 붙여 다시 실행하세요. 같은 내용이면 초안을 고쳐 다시 올립니다")
        return 0
    if r.status_code != 200:
        sys.exit(f"처리하지 못했습니다: {detail(r)}")
    print("\n" + done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
