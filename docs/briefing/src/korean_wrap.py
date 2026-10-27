"""한글이 글자 중간에서 줄바꿈되지 않게 저장된 pptx를 고친다.
pptxgenjs는 글자에 lang="en-US"와 동아시아 글꼴 문자 집합 charset="-122"(중국어 GB2312)를 붙인다.
PowerPoint는 이 때문에 중국어 줄바꿈 규칙(글자 단위)을 써서 '제안'이 '제/안'으로 잘린다 → 한국어(ko-KR, HANGUL -127)로 바꾼다.
XML은 문자열로만 고친다(ElementTree로 다시 쓰면 네임스페이스가 깨진다)."""
import re
import shutil
import sys
import zipfile

src, dst = sys.argv[1], sys.argv[2]
tmp = dst + ".tmp"
with zipfile.ZipFile(src) as zin, zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
    for item in zin.infolist():
        data = zin.read(item.filename)
        if item.filename.endswith(".xml") and item.filename.startswith("ppt/"):
            s = data.decode("utf-8")
            s = s.replace('lang="en-US"', 'lang="ko-KR" altLang="en-US"')
            s = re.sub(r'(<a:ea typeface="[^"]*" pitchFamily="\d+" charset=)"-122"', r'\1"-127"', s)
            data = s.encode("utf-8")
        zout.writestr(item, data)
shutil.move(tmp, dst)
print("korean wrap fixed:", dst)
