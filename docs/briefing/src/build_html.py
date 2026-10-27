"""소개 슬라이드 HTML 만들기: briefing_body.html(본문) + shots/*.webp(3장의 실제 웹앱 화면) → ../DDS_AI_Engineer_소개.html.
사진은 data URI로 넣어 파일 하나만 있어도 브라우저에서 열린다.
  .venv\\Scripts\\python docs\\briefing\\src\\build_html.py
"""
import base64
import json
import pathlib

HERE = pathlib.Path(__file__).parent
body = (HERE / "briefing_body.html").read_text(encoding="utf-8")
shots = {p.stem: "data:image/webp;base64," + base64.b64encode(p.read_bytes()).decode("ascii")
         for p in sorted((HERE / "shots").glob("*.webp"))}
assert body.count("/*@SHOTS@*/") == 1, "본문에 사진 자리(/*@SHOTS@*/)가 없습니다"
body = body.replace("/*@SHOTS@*/", "const SHOTS = " + json.dumps(shots) + ";")

# 앞부분(title·meta·글꼴·style)은 head로, 나머지(아이콘 묶음·슬라이드·script)는 body로
cut = body.index('<svg width="0"')
head, rest = body[:cut], body[cut:]
html = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        "<style>:root{color-scheme:light}body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n"
        + head + "</head>\n<body>\n" + rest + "</body>\n</html>\n")
out = HERE.parent / "DDS_AI_Engineer_소개.html"
out.write_text(html, encoding="utf-8", newline="\n")
print(out.name, len(html), "bytes,", len(shots), "shots")
