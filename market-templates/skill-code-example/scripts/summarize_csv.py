"""CSV의 숫자 열마다 최솟값·최댓값·평균을 계산한다. 표준 모듈만 쓴다: 어느 PC에서나 돌고 설치할 것이 없다."""
import csv
import json
import sys
from pathlib import Path


def summarize(path: Path) -> dict:
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    out = {}
    for name in rows[0] if rows else []:
        values = []
        for row in rows:
            try:
                values.append(float(row[name]))
            except (TypeError, ValueError):
                continue
        if values:
            out[name] = {"count": len(values), "min": min(values), "max": max(values), "mean": sum(values) / len(values)}
    return out


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: summarize_csv.py <CSV 파일> [결과 JSON 파일]", file=sys.stderr)
        return 2
    result = summarize(Path(sys.argv[1]))
    if not result:
        print(f"숫자 열을 찾지 못했습니다: {sys.argv[1]} → CSV 파일 경로와 내용을 확인하세요", file=sys.stderr)
        return 1
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
