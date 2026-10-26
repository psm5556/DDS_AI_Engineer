# -*- coding: utf-8 -*-
"""
LS-DYNA 키워드 파일 편집 유틸리티 (tool 키: python, Python 3)

*PARAMETER 를 쓸 수 없는 기존 모델(이미 만들어진 키워드)을 직접 고칠 때 쓴다.
고정폭 필드(기본 10자, LONG 형식 20자)를 '그 필드 위치만' 교체하므로 다른 필드가 밀리지 않는다.

라이브러리로 import 하거나 CLI 로 실행한다.
  python dyna_keyword_tools.py includes main.k
  python dyna_keyword_tools.py endtim main.k 0.05
  python dyna_keyword_tools.py shell-t model.k 3 1.8          (SECID=3 두께 T1~T4=1.8)
  python dyna_keyword_tools.py mat-field model.k 1 2 2.1e5    (MID=1 카드1 필드2(E) 값)
  python dyna_keyword_tools.py field model.k "*CONTROL_TIMESTEP" 0 4 -1.0e-6   (블록 첫 카드 필드4=DT2MS)

필드 번호는 0부터 센다(카드1: 0=첫 필드). 카드 번호도 0부터, 주석($)과 _TITLE 줄은 제외하고 센다.
"""
import math
import os
import re
import sys


# ------------------------------------------------------------ 기본 입출력
def read_lines(path):
    with open(path, "r", encoding="latin-1", newline="") as f:
        return f.read().splitlines(True)


def write_lines(path, lines):
    with open(path, "w", encoding="latin-1", newline="") as f:
        f.write("".join(lines))


def is_long_format(lines):
    for ln in lines[:50]:
        if ln.upper().startswith("*KEYWORD") and re.search(r"LONG\s*=\s*Y", ln, re.I):
            return True
    return False


def field_width(keyword_line, file_long=False):
    kw = keyword_line.split()[0]
    if kw.endswith("+"):
        return 20
    if kw.endswith("-"):
        return 10
    return 20 if file_long else 10


# ------------------------------------------------------------ 값 서식
def fmt_field(value, width=10):
    """width 글자에 들어가도록 숫자를 서식화(정밀도를 줄여가며). 못 맞추면 ValueError."""
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int):
        s = str(value)
        if len(s) > width:
            raise ValueError("integer %s does not fit %d chars" % (s, width))
        return s
    v = float(value)
    for prec in range(width, 0, -1):
        for s in ("%.*g" % (prec, v), "%.*E" % (max(prec - 1, 0), v)):
            s = s.replace("E+0", "E+").replace("E-0", "E-").replace("e+0", "e+").replace("e-0", "e-")
            if "." not in s and "e" not in s.lower():
                s += "."
            if len(s) <= width:
                return s
    raise ValueError("value %r does not fit %d chars" % (value, width))


def get_field(line, idx, width=10):
    body = line.rstrip("\r\n")
    if "," in body:
        toks = body.split(",")
        return toks[idx].strip() if idx < len(toks) else ""
    return body[idx * width:(idx + 1) * width].strip()


def set_field(line, idx, value, width=10):
    """한 줄의 idx 번째 필드만 교체. 콤마 자유형식 줄이면 토큰을 교체한다."""
    body = line.rstrip("\r\n")
    nl = line[len(body):] or "\n"
    text = value if isinstance(value, str) else fmt_field(value, width - 1)  # 앞 공백 1자 확보
    if "," in body:
        toks = body.split(",")
        while len(toks) <= idx:
            toks.append("")
        toks[idx] = text
        return ",".join(toks) + nl
    if len(text) > width:
        raise ValueError("'%s' wider than %d" % (text, width))
    body = body.ljust((idx + 1) * width)
    return (body[:idx * width] + text.rjust(width) + body[(idx + 1) * width:]).rstrip() + nl


# ------------------------------------------------------------ 블록 탐색
def iter_blocks(lines):
    """(kw_line_index, keyword_upper, [data_line_indices]) 를 순서대로 돌려준다.
    data 에서 '$' 주석과 빈 줄은 제외한다. _TITLE 키워드의 제목 줄도 data 에 포함된다(호출 측에서 처리)."""
    i, n = 0, len(lines)
    while i < n:
        s = lines[i].lstrip()
        if s.startswith("*"):
            kw = s.split()[0].upper()
            data, j = [], i + 1
            while j < n and not lines[j].lstrip().startswith("*"):
                t = lines[j].strip()
                if t and not t.startswith("$"):
                    data.append(j)
                j += 1
            yield i, kw, data
            i = j
        else:
            i += 1


def set_block_field(lines, keyword, card, field, value, nth=0):
    """keyword 로 시작하는 nth 번째 블록의 card/field 를 교체. 고친 개수 반환."""
    long_fmt = is_long_format(lines)
    k = 0
    for ki, kw, data in iter_blocks(lines):
        if kw.rstrip("+-").startswith(keyword.upper()):
            if k == nth:
                data = data[1:] if "_TITLE" in kw else data
                w = field_width(lines[ki], long_fmt)
                lines[data[card]] = set_field(lines[data[card]], field, value, w)
                return 1
            k += 1
    return 0


def set_termination(lines, endtim):
    return set_block_field(lines, "*CONTROL_TERMINATION", 0, 0, float(endtim))


def set_shell_thickness(lines, secid, t, which=(0, 1, 2, 3)):
    """*SECTION_SHELL(_TITLE) 에서 SECID 의 T1~T4 교체. 한 블록에 여러 섹션이 반복될 수 있다.
    ICOMP=1 이면 각도 카드(ceil(NIP/8)줄)를 건너뛴다.
    TODO(사내검증): 사용자 정의 ELFORM(101~105), IRID 등 추가 카드가 있는 경우는 별도 처리."""
    long_fmt = is_long_format(lines)
    count = 0
    for ki, kw, data in iter_blocks(lines):
        base = kw.rstrip("+-")
        if not base.startswith("*SECTION_SHELL"):
            continue
        w = field_width(lines[ki], long_fmt)
        has_title = "_TITLE" in base
        j = 0
        while j < len(data):
            if has_title:
                j += 1
            if j + 1 >= len(data):
                break
            c1, c2 = data[j], data[j + 1]
            sid = get_field(lines[c1], 0, w)
            nip = get_field(lines[c1], 3, w)
            icomp = get_field(lines[c1], 6, w)
            if sid == str(secid):
                for fi in which:
                    lines[c2] = set_field(lines[c2], fi, float(t), w)
                count += 1
            j += 2
            if icomp and int(float(icomp)) == 1:
                nip_v = int(float(nip)) if nip else 2
                j += int(math.ceil(nip_v / 8.0))
    return count


def set_mat_field(lines, mid, field, value, card=0):
    """*MAT_xxx(_TITLE) 중 MID 일치 블록의 card/field 교체 (카드1 기준: 0=MID 1=RO 2=E 3=PR ...)."""
    long_fmt = is_long_format(lines)
    for ki, kw, data in iter_blocks(lines):
        if not kw.startswith("*MAT"):
            continue
        d = data[1:] if "_TITLE" in kw else data
        w = field_width(lines[ki], long_fmt)
        if d and get_field(lines[d[0]], 0, w) == str(mid):
            lines[d[card]] = set_field(lines[d[card]], field, value, w)
            return 1
    return 0


def set_prescribed_motion_sf(lines, row_id, sf):
    """*BOUNDARY_PRESCRIBED_MOTION_(NODE|SET|RIGID) 카드: ID DOF VAD LCID SF VID DEATH BIRTH.
    첫 필드가 row_id 인 줄의 SF(필드4) 교체. 한 블록에 여러 줄이 있을 수 있다."""
    long_fmt = is_long_format(lines)
    count = 0
    for ki, kw, data in iter_blocks(lines):
        if not kw.startswith("*BOUNDARY_PRESCRIBED_MOTION"):
            continue
        w = field_width(lines[ki], long_fmt)
        d = data[1:] if "_ID" in kw else data   # _ID 옵션은 ID/HEADING 카드가 먼저 온다
        for li in d:
            if get_field(lines[li], 0, w) == str(row_id):
                lines[li] = set_field(lines[li], 4, float(sf), w)
                count += 1
    return count


# ------------------------------------------------------------ *INCLUDE
def list_includes(path, _seen=None, depth=0):
    """*INCLUDE 트리를 재귀로 출력하고 파일 목록을 돌려준다. 상대경로는 포함한 파일 기준."""
    _seen = _seen if _seen is not None else []
    path = os.path.abspath(path)
    print("  " * depth + path + ("" if os.path.exists(path) else "   <-- MISSING"))
    _seen.append(path)
    if not os.path.exists(path):
        return _seen
    lines = read_lines(path)
    base = os.path.dirname(path)
    for ki, kw, data in iter_blocks(lines):
        if kw in ("*INCLUDE", "*INCLUDE_TRANSFORM") and data:
            name = lines[data[0]].strip()
            child = name if os.path.isabs(name) else os.path.join(base, name)
            if os.path.abspath(child) not in _seen:
                list_includes(child, _seen, depth + 1)
    return _seen


# ------------------------------------------------------------ CLI
def _cli(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd, path = argv[1], argv[2]
    if cmd == "includes":
        list_includes(path)
        return 0
    lines = read_lines(path)
    if cmd == "endtim":
        n = set_termination(lines, float(argv[3]))
    elif cmd == "shell-t":
        n = set_shell_thickness(lines, argv[3], float(argv[4]))
    elif cmd == "mat-field":
        n = set_mat_field(lines, argv[3], int(argv[4]), float(argv[5]))
    elif cmd == "field":
        n = set_block_field(lines, argv[3], int(argv[4]), int(argv[5]), float(argv[6]))
    else:
        print("unknown command " + cmd)
        return 2
    if n == 0:
        print("nothing changed (target not found)")
        return 1
    write_lines(path, lines)
    print("changed %d place(s) in %s" % (n, path))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
