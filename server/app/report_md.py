"""보고서 본문 markdown 해석. HTML과 Word가 같은 해석 결과(요소 나무)를 쓰므로 제목·목차·코드 블록이 두 파일에서 어긋나지 않는다.
AI가 쓴 글은 믿지 않는다: 원문 HTML은 글자로만 보이고, 링크는 http(s)·mailto만, 그림은 넣지 않는다(파일 하나로 끝나야 하고 외부 요청이 없어야 한다)."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as etree
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import markdown as md
from markdown.blockprocessors import BlockProcessor
from markdown.extensions import Extension
from markdown.preprocessors import Preprocessor
from markdown.treeprocessors import Treeprocessor
from markdown.util import ETX, STX, AtomicString, code_escape

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TABLE = re.compile(r"^\s*\|")
_MARK = re.compile(rf"^{STX}dds-fence:(\d+){ETX}$")
_LINK_OK = re.compile(r"^(https?:|mailto:)", re.I)
# XML 1.0에 넣을 수 없는 글자 전부: 제어 문자 · 짝 없는 대리 문자 · U+FFFE·U+FFFF
_CONTROL = re.compile("[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f\\ud800-\\udfff\\ufffe\\uffff]")
_HEAD = re.compile(r"^ {0,3}#{1,6}(\s|$)")
# '#3 케이스'·'##요약'·'#######'는 웹 화면(marked)에서 제목이 아니지만 python-markdown은 제목으로 읽는다
_NOT_HEAD = re.compile(r"^ {0,3}(#{1,6}[^#\s]|#{7,})")
_DELIM = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|?\s*$")
_ENTITY = re.compile(r"&(#\d+|#[xX][0-9a-fA-F]+|[0-9a-zA-Z]+);")
_NUMBER = re.compile(r"^\s*[-+\u2212]?[\d.,]+(?:[eE][-+]?\d+)?\s*%?\s*$")
_BREAKS_ANYWHERE = "\u1100-\u11ff\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef"  # 한글·한자는 어느 글자에서나 줄이 바뀐다
_WIDE_CHAR = re.compile(f"[{_BREAKS_ANYWHERE}]")
_TOKEN = re.compile(f"[^\\s{_BREAKS_ANYWHERE}]+")


def safe_text(text: str) -> str:
    """XML에 넣을 수 없는 글자를 지운다(Word 파일이 깨지지 않게)."""
    return _CONTROL.sub("", text or "")


def lines_of(text: str | None) -> list[str]:
    """줄 나누기. splitlines()는 U+2028·\\x0b에서도 나누어 웹 화면(marked)과 줄이 어긋나므로 쓰지 않는다."""
    return re.split(r"\r\n|\r|\n", text or "")


def unescape(text: str) -> str:
    """';'로 끝나는 문자 참조만 푼다. html.unescape는 '&copy=2'도 ©=2로 바꿔 주소·변수 글을 망가뜨린다(HTML 출력과 같은 규칙)."""
    return _ENTITY.sub(lambda m: html.unescape(m[0]), text)


def fence_states(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    """줄마다 코드 블록 안팎을 알린다: 'text' 밖 · 'open' 여는 줄 · 'code' 안 · 'close' 닫는 줄.
    제목(##)이나 결과 삽입 줄이 코드 안에 있으면 그것은 제목·결과가 아니다. 닫히지 않으면 끝까지 코드다(웹 화면 marked와 같다)."""
    mark = None
    for line in lines:
        m = _FENCE.match(line)
        if mark is None:
            if m and not (m[1][0] == "`" and "`" in m[2]):
                mark = m[1]
                yield line, "open"
            else:
                yield line, "text"
        elif m and m[1][0] == mark[0] and len(m[1]) >= len(mark) and not m[2].strip():
            mark = None
            yield line, "close"
        else:
            yield line, "code"


def _tidy(lines: list[str]) -> list[str]:
    """AI가 흔히 쓰는 모양을 python-markdown이 웹 화면(marked)과 같게 읽도록 고른다:
    문단 바로 뒤의 목록·표·제목에 빈 줄을 넣고, 2칸 들여쓴 하위 목록과 '1)'을 표준 모양으로 바꾸고, 제목이 아닌 '#'을 글자로 둔다."""
    out: list[str] = []
    stack: list[int] = []
    for line in lines:
        prev = out[-1] if out else ""
        if _NOT_HEAD.match(line):
            line = "\\" + line.lstrip(" ")
        elif _HEAD.match(line):
            # 빈 줄 없이 이어지면 앞의 표에 먹히고, 들여쓴 제목은 python-markdown이 제목으로 못 읽는다
            line = line.lstrip(" ")
            if prev.strip():
                out.append("")
        elif _DELIM.match(line) and "|" in prev and not _TABLE.match(prev) and len(out) > 1 and out[-2].strip():
            out.insert(-1, "")  # 문단 바로 뒤의 '|' 없이 시작하는 표
        m = _LIST.match(line)
        if m:
            indent = len(m[1].expandtabs(4))
            while stack and indent < stack[-1]:
                stack.pop()
            if not stack or indent > stack[-1]:
                stack.append(indent)
            before = _LIST.match(prev)
            if prev.strip() and not before and not prev.startswith(" "):
                out.append("")
            elif before and len(before[1]) == 4 * (len(stack) - 1) and before[2][0].isdigit() != m[2][0].isdigit():
                out.append("")  # '-' 다음의 '1.'은 새 목록이다(웹 화면과 같게)
            out.append(" " * (4 * (len(stack) - 1)) + re.sub(r"\)$", ".", m[2]) + " " + m[3])
            continue
        if line.strip() and not line.startswith(" "):
            stack.clear()
        if bool(_TABLE.match(line)) != bool(_TABLE.match(prev)) and line.strip() and prev.strip():
            out.append("")
        out.append(line)
    return out


class _Lines(Preprocessor):
    """코드 블록을 빈 줄로 둘러싼 표시 한 줄로 바꿔 둔다. 표시의 STX·ETX는 앞 단계에서 원문에서 지워지므로 AI 글로 흉내 낼 수 없다."""

    def run(self, lines):
        out: list[str] = []
        code: list[str] | None = None
        for line, state in fence_states(lines):
            if state == "text":
                out.append(line)
            elif state == "open":
                code = []
            elif state == "code":
                code.append(line)
            else:
                self._flush(out, code)
                code = None
        if code is not None:  # 닫히지 않은 코드 블록
            self._flush(out, code)
        return _tidy(out)

    def _flush(self, out: list[str], code: list[str]) -> None:
        self.md.dds_fences.append("\n".join(code))
        out += ["", f"{STX}dds-fence:{len(self.md.dds_fences) - 1}{ETX}", ""]


class _FenceBlock(BlockProcessor):
    def test(self, parent, block):
        return bool(_MARK.match(block.strip()))

    def run(self, parent, blocks):
        m = _MARK.match(blocks.pop(0).strip())
        pre = etree.SubElement(parent, "pre")
        code = etree.SubElement(pre, "code")
        code.text = AtomicString(code_escape(self.parser.md.dds_fences[int(m[1])]))


@dataclass
class TocItem:
    level: int  # 2 또는 3
    text: str
    anchor: str  # HTML id이자 Word 책갈피 이름 — AI 글자가 들어가지 않으므로 같은 제목·한글·기호 모두 안전하고 겹치지 않는다


@dataclass
class Toc:
    items: list[TocItem] = field(default_factory=list)

    def add(self, level: int, text: str) -> str:
        anchor = f"sec_{len(self.items) + 1}"
        self.items.append(TocItem(level, text, anchor))
        return anchor


def plain(el: etree.Element) -> str:
    """요소의 글자만(굵게·코드 표시 없이). 코드 글자는 해석 단계에서 이미 &lt;로 바뀌어 있어 되돌린다."""
    return unescape("".join(el.itertext())).strip()


# ---------- 표 폭: 인쇄·Word에서 값이 잘리거나 숫자가 중간에서 끊기면 안 된다 ----------
PRINT_PORTRAIT, PRINT_LANDSCAPE = 174.0, 261.0  # HTML 인쇄의 글자 폭(mm). A4에서 좌우 여백 18씩
_PT_MM = 0.3528
_PAD, _PAD_TIGHT = 3.8, 1.6  # 칸의 좌우 여백 합(mm)


def is_number(text: str) -> bool:
    return bool(_NUMBER.match(text))


def is_list_line(line: str) -> bool:
    return bool(_LIST.match(line))


def _em(text: str) -> float:
    """글자 폭 어림(글자 크기 배수). 맑은 고딕의 숫자·영문은 약 0.55em인데 굵은 머리글까지 생각해 넉넉히 잡는다."""
    return sum(1.0 if _WIDE_CHAR.match(ch) else 0.6 for ch in text)


@dataclass(frozen=True)
class TablePlan:
    landscape: bool
    font: float  # pt
    tight: bool
    groups: tuple[tuple[int, ...], ...]  # 한 벌씩 그릴 열 번호. 둘 이상이면 첫 열을 반복해 나눈 것이다
    floor: tuple[float, ...]  # 열마다 줄을 바꾸면 안 되는 폭(em)
    need: tuple[float, ...]  # 열마다 한 줄로 쓸 때의 폭(em)

    @property
    def css(self) -> str:
        if not self.landscape:
            return ""
        return "wide" + (" f7" if self.font < 8 else "") + (" xwide" if len(self.groups) > 1 else "")

    def widths(self, cols: tuple[int, ...], total: float) -> list[float]:
        """total(mm)을 열에 나눈다: 끊기면 안 되는 폭을 먼저 주고, 남는 폭은 글이 긴 열부터 준다."""
        em, pad = self.font * _PT_MM, _PAD_TIGHT if self.tight else _PAD
        out = [self.floor[c] * em + pad for c in cols]
        left = total - sum(out)
        want = [(self.need[c] - self.floor[c]) * em for c in cols]
        if left > 0:
            rate = min(1.0, left / sum(want)) if sum(want) else 0.0
            out = [w + x * rate for w, x in zip(out, want)]
            left -= sum(want) * rate
            out = [w + left * w / sum(out) for w in out] if left > 0 else out
        return out


def plan_table(rows: list[list[str]], portrait: float = PRINT_PORTRAIT, landscape: float = PRINT_LANDSCAPE, base: float = 9.0) -> TablePlan:
    """rows는 글자 표(첫 줄이 머리). 세로 쪽 → 가로 쪽 + 작은 글자 → 첫 열을 반복해 나누기 순서로 고른다."""
    n = max((len(r) for r in rows), default=0)
    floor, need = [1.0] * n, [1.0] * n
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            need[c] = max(need[c], _em(text))
            if r and not _NUMBER.match(text):  # 글 칸의 아주 긴 경로·이름은 줄을 바꿔도 값이 달라 보이지 않는다
                tokens = [min(14.0, _em(t)) for t in _TOKEN.findall(text)]
            else:
                tokens = [_em(text) if r else _em(t) for t in _TOKEN.findall(text)]
            floor[c] = max(floor[c], *tokens, 2.0 if _WIDE_CHAR.search(text) else 1.0)

    def fits(cols, font: float, pad: float, width: float) -> bool:
        return sum(floor[c] * font * _PT_MM + pad for c in cols) <= width

    every = tuple(range(n))

    def done(land: bool, font: float, tight: bool, groups: tuple[tuple[int, ...], ...] = (every,)) -> TablePlan:
        return TablePlan(land, font, tight, groups, tuple(floor), tuple(need))

    if fits(every, base, _PAD, portrait):
        return done(False, base, False)
    for font in (8.0, 7.0):
        if fits(every, font, _PAD_TIGHT, landscape):
            return done(True, font, True)
    groups: list[tuple[int, ...]] = []
    now = [0]
    for c in range(1, n):
        if len(now) > 1 and not fits([*now, c], 7.0, _PAD_TIGHT, landscape):
            groups.append(tuple(now))
            now = [0]
        now.append(c)
    groups.append(tuple(now))
    return done(True, 7.0, True, tuple(groups))


class _Finish(Treeprocessor):
    """다른 단계가 끝난 나무를 다듬는다: 링크·그림 막기 → 제목 단계 정리 → 목차 등록."""

    def run(self, root):
        for el in root.iter():
            if el.tag == "img":  # 외부 그림을 불러오지 않게 글자로 남긴다(웹 화면과 같은 문구)
                alt = el.get("alt") or el.get("src") or ""
                el.tag, el.text = "span", f"[이미지: {alt}]"
                el.attrib.clear()
            elif el.tag == "a":
                # &#106;avascript: 같은 숨김도 막으려고 문자 참조를 풀어 공백·제어 문자를 지운 뒤 본다
                href = re.sub(r"[\s\x00-\x1f]", "", html.unescape(el.get("href") or ""))
                if _LINK_OK.match(href):
                    el.set("rel", "noreferrer noopener")
                else:
                    el.attrib.clear()
        shift = self.md.dds_shift
        for el in root.iter():
            if re.fullmatch(r"h[1-6]", el.tag):
                # 표지의 제목이 h1이므로 본문은 h2부터다. AI가 '#'을 써도 구역으로 본다
                el.tag = f"h{min(6, max(2, int(el.tag[1])) + shift)}"
        for table in root.iter("table"):  # 인쇄 폭 표시: 넓은 표는 가로 쪽으로, 숫자 칸은 줄을 바꾸지 않게
            rows = [[plain(cell) for cell in tr] for tr in table.iter("tr")]
            if css := plan_table(rows).css:
                table.set("class", css)
            for td in table.iter("td"):
                if _NUMBER.match(plain(td)):
                    td.set("class", "n")
        toc = self.md.dds_toc
        if toc is not None:
            for el in root:  # 인용·목록 안의 제목은 구역이 아니다
                if el.tag in ("h2", "h3") and (text := plain(el)):
                    el.set("id", toc.add(int(el.tag[1]), text))
        self.md.dds_root = root


class _Report(Extension):
    def extendMarkdown(self, m):
        # 원문 HTML을 글자로 남기는 공식 방법. 문자 참조·메일 자동 링크도 끄면 나무에 임시 표시가 남지 않아 Word에 그대로 쓸 수 있다
        m.preprocessors.deregister("html_block")
        m.inlinePatterns.deregister("html")
        m.inlinePatterns.deregister("entity")
        m.inlinePatterns.deregister("automail")
        m.preprocessors.register(_Lines(m), "dds_lines", 25)
        m.parser.blockprocessors.register(_FenceBlock(m.parser), "dds_fence", 95)
        m.treeprocessors.register(_Finish(m), "dds_finish", -1)


@dataclass
class Parsed:
    root: etree.Element  # Word가 쓴다
    html: str


def parse(text: str, toc: Toc | None = None, shift: int = 0) -> Parsed:
    """toc를 주면 h2·h3을 목차에 올리고 id를 붙인다. shift는 결과 안의 제목을 한 단계 낮출 때 쓴다."""
    # sane_lists: AI가 쓴 번호(3. 4.)를 웹 화면처럼 그대로 두고 ul·ol을 섞어 잇지 않는다. nl2br: 줄바꿈도 웹 화면과 같게
    m = md.Markdown(extensions=["tables", "nl2br", "sane_lists", _Report()])
    m.dds_fences, m.dds_shift, m.dds_toc, m.dds_root = [], shift, toc, None
    out = m.convert(safe_text(text))
    return Parsed(m.dds_root if m.dds_root is not None else etree.Element("div"), out)
