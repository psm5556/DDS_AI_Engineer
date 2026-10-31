"""표(붙여 넣은 글·CSV·XLSX)를 읽고 열을 분석한다.

서버(화면에서 올린 파일)와 사용자 PC(MCP가 경로로 직접 읽음)가 같은 코드를 쓰므로 표준 라이브러리만 쓴다.
PC Agent 번들에 openpyxl·pandas가 있다고 믿을 수 없어서 XLSX도 zipfile과 xml로 읽는다.
오류 문구는 그대로 사용자에게 보이므로 원인과 할 일을 함께 쓴다.
"""
from __future__ import annotations

import csv
import io
import math
import re
import statistics
import unicodedata
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

MAX_ROWS = 200_000
MAX_COLUMNS = 2_000
HEADER_SCAN_ROWS = 30
# 엑셀은 칸마다 위치를 적으므로 1MB 파일도 행마다 맨 오른쪽 칸 하나로 수억 칸이 될 수 있다. 행·열 한도만으로는 막지 못한다
MAX_CELLS = 5_000_000
MAX_SHARED_STRINGS = 2_000_000
MAX_SHARED_CHARS = 100_000_000
# 입력 범위·GP 정규화에서 넘침이 나지 않을 만큼만 받는다. 이보다 크면 단위 오류나 발산한 해석값이다
MAX_ABS = 1e150


class TableError(ValueError):
    """표를 읽을 수 없을 때. 문구에 원인과 할 일이 들어 있다."""


class TableEncrypted(TableError):
    """사내 보안 프로그램이 암호화한 파일. 저장해도 다시 암호화되므로 할 일은 언제나 복사·붙여넣기다."""


class TableNotATable(TableError):
    """모델 파일·그림처럼 표가 아닌 파일. 암호화됐다고 말하면 사용자가 엉뚱한 일을 한다."""


ENCRYPTED = "보안 프로그램으로 암호화되어 읽을 수 없습니다 → 엑셀에서 표를 복사해 붙여 넣으세요"
NOT_TABLE = "표가 아닙니다 → 입력·출력 표는 엑셀에서 복사해 붙여 넣으세요. 해석할 모델 파일이면 요청과 함께 첨부하세요"
SNIFF_BYTES = 1024 * 1024
# 표로 저장될 수 있는 이름. 이 이름인데 글자가 아니면 암호화된 표로 본다. 그 밖의 이름은 표가 아니다
TABLE_SUFFIXES = {".csv", ".tsv", ".txt", ".dat", ".prn", ".xlsx", ".xlsm", ".xlsb", ".xls", ".out", ".log", ""}
# 바로 첨부 검사하는 표 이름. .txt·.dat·.out은 모델·솔버 입력일 수 있어 첨부를 막지 않는다
ATTACH_CHECK_SUFFIXES = {".csv", ".tsv", ".xlsx", ".xlsm", ".xlsb", ".xls"}
_FILE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"%PDF", b"\x89HDF", b"II*\x00", b"MM\x00*", b"MZ", b"\x1f\x8b", b"7z\xbc\xaf", b"Rar!", b"RIFF", b"\x7fELF")
PASTED_NAME = "붙여 넣은 표"
# 엑셀은 저장된 값이 아니라 화면에 보이는 글자를 복사한다
DIGITS_WARNING = "엑셀은 화면에 보이는 자릿수대로 복사합니다. 셀 표시 형식으로 소수 자릿수를 줄여 두었다면 값도 줄어 있습니다 → 정밀도가 중요하면 자릿수를 늘린 뒤 다시 복사하세요"


# ---------- 셀 값 ----------
_MISSING = {"", "na", "n/a", "nan", "null", "none", "-", "--", "없음", "결측"}
_FORMULA_ERRORS = {"#n/a", "#div/0!", "#value!", "#ref!", "#num!", "#name?", "#null!", "#getting_data", "#spill!", "#calc!"}
_NARROW = re.compile(r"^#{2,}$")  # 열 폭이 좁아 화면에 ####로 보이던 셀을 그대로 복사한 값
_INF = {"inf", "+inf", "-inf", "infinity", "+infinity", "-infinity", "1.#inf", "-1.#inf", "∞"}
_NUM = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_THOUSANDS = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_EURO = re.compile(r"^[+-]?\d{1,3}(\.\d{3})+,\d+$")
_DEC_COMMA = re.compile(r"^[+-]?\d*,\d+([eE][+-]?\d+)?$")
_DOT_DEC = re.compile(r"^[+-]?\d*\.\d+([eE][+-]?\d+)?$")
_ZERO_COMMA = re.compile(r"^[+-]?0,")  # 0,125는 천 단위일 수 없다. 천 단위 표시는 0으로 시작하지 않는다


def _text_number(s: str, decimal_comma: bool) -> float | None:
    if _NUM.match(s):
        return float(s)
    if decimal_comma:
        if _EURO.match(s):
            return float(s.replace(".", "").replace(",", "."))
        if _DEC_COMMA.match(s):
            return float(s.replace(",", "."))
        return None
    if _THOUSANDS.match(s) and not _ZERO_COMMA.match(s):  # 1,234 는 천 단위로 읽는다. 쉼표 구분 파일에서 따옴표로 묶인 값이다
        return float(s.replace(",", ""))
    if _DEC_COMMA.match(s):  # 1,5 처럼 세 자리 묶음이 아니면 소수점 쉼표다
        return float(s.replace(",", "."))
    return None


def cell_state(cell: Any, decimal_comma: bool = False) -> tuple[float | None, str]:
    """셀 하나를 숫자로 읽는다. 돌려주는 값: (숫자, 상태). 상태는 ok | missing | error(엑셀 수식 오류) | text | nonfinite | bool."""
    if cell is None:
        return None, "missing"
    if isinstance(cell, bool):  # True는 1이 아니라 범주다. 숫자로 읽으면 뜻이 없는 입력이 된다
        return None, "bool"
    if isinstance(cell, (int, float)):
        try:
            v = float(cell)
        except OverflowError:  # JSON의 아주 큰 정수. float으로 바꾸다 터지면 표 전체가 500이 된다
            return None, "nonfinite"
        if math.isnan(v):  # 표 도구는 빈 칸을 NaN으로 넘긴다
            return None, "missing"
        return (v, "ok") if math.isfinite(v) and abs(v) <= MAX_ABS else (None, "nonfinite")
    s = str(cell).replace(" ", "").replace(" ", "").strip()
    low = s.lower()
    if low in _MISSING:
        return None, "missing"
    if low in _FORMULA_ERRORS or _NARROW.match(low):
        return None, "error"
    if low in _INF:
        return None, "nonfinite"
    v2 = _text_number(s, decimal_comma)
    if v2 is None:
        return None, "text"
    return (v2, "ok") if math.isfinite(v2) and abs(v2) <= MAX_ABS else (None, "nonfinite")


def to_number(cell: Any, decimal_comma: bool = False) -> float | None:
    return cell_state(cell, decimal_comma)[0]


# ---------- 머리글·단위 ----------
_KNOWN_UNITS = {u.lower() for u in (
    "mm", "cm", "m", "km", "um", "μm", "in", "inch", "ft", "n", "kn", "mn", "gf", "kgf", "tf", "pa", "kpa", "mpa", "gpa", "bar",
    "kg", "g", "mg", "t", "ton", "tonne", "s", "sec", "ms", "us", "μs", "min", "hr", "h", "k", "c", "degc", "°c", "degf", "°f",
    "deg", "°", "rad", "j", "kj", "mj", "w", "kw", "v", "a", "hz", "khz", "mhz", "rpm", "%", "-", "ratio", "nm", "n-m", "n·m",
    "nmm", "n-mm", "n·mm", "mm2", "mm3", "m2", "m3", "cm2", "cm3", "l", "ml", "lpm", "개", "회", "초", "분", "시간", "도", "톤",
    "밀리미터", "미터", "뉴턴", "무차원", "psi", "ksi", "lb", "lbs", "lbf", "oz", "mmhg", "atm", "cal", "kcal", "wh", "kwh", "db", "dba", "ppm", "mk",
)}
_UNIT_PART = re.compile(r"^\s*(.*?)\s*[\[\(（{]\s*([^\]\)）}]{1,16})\s*[\]\)）}]\s*$")


def _looks_like_unit(s: str) -> bool:
    u = unicodedata.normalize("NFKC", s).strip()  # 한글 엑셀이 쓰는 ㎜·㎫·㎏·℃를 mm·MPa·kg·°C로 본다
    if not u:
        return False
    if u.lower() in _KNOWN_UNITS:
        return True
    # N/mm2, kg/m^3, m·s 처럼 단위 조합. stress·max 같은 낱말을 단위로 착각하지 않게 기호가 있을 때만 받는다
    if re.search(r"[/^·*²³]", u) and len(u) <= 16 and not re.search(r"\s", u):
        return all(not p or p.lower() in _KNOWN_UNITS or re.sub(r"[\d²³^-]+$", "", p).lower() in _KNOWN_UNITS
                   for p in re.split(r"[/·*]", u))
    return False


def split_unit(raw: str) -> tuple[str, str]:
    """'thickness [mm]'·'두께(mm)' → ('thickness', 'mm'). 단위로 보이지 않는 괄호(max(stress))는 건드리지 않는다."""
    s = re.sub(r"\s+", " ", str(raw or "").strip())
    m = _UNIT_PART.match(s)
    if m and m.group(1) and _looks_like_unit(m.group(2)):
        return m.group(1).strip(), m.group(2).strip()
    return s, ""


@dataclass
class Column:
    name: str
    raw: str
    unit: str = ""
    index: int = 0
    role_hint: str = ""  # in: / out: 머리글(플랫폼 cases.csv)처럼 역할이 정해진 열


@dataclass
class Table:
    source: str
    columns: list[Column]
    rows: list[list[Any]]
    row_numbers: list[int]
    header_row: int | None = None
    skipped_rows: int = 0
    sheet: str | None = None
    sheets: list[str] = field(default_factory=list)
    encoding: str = ""
    delimiter: str = ""
    decimal_comma: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        return [c.name for c in self.columns]

    def records(self, numeric: bool = True) -> list[dict[str, Any]]:
        """행 → {열 이름: 값, _row: 파일의 행 번호}. numeric이면 숫자로 읽히는 값은 숫자로 바꾼다.
        소수점 쉼표는 파일 단위로만 판단할 수 있어서 서버에 넘기기 전에 여기서 바꾼다."""
        out = []
        for n, row in zip(self.row_numbers, self.rows):
            rec: dict[str, Any] = {"_row": n}
            for c in self.columns:
                v = row[c.index] if c.index < len(row) else None
                if numeric:
                    num, state = cell_state(v, self.decimal_comma)
                    v = num if state == "ok" else (None if state == "missing" else v)
                rec[c.name] = _json_safe(v)
            out.append(rec)
        return out


def _json_safe(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return str(v)  # 숫자가 아닌 값으로 넘겨 받는 쪽에서 이유와 함께 제외한다
    return v


# ---------- 읽기 ----------
def read_table(source: str | Path | bytes, filename: str | None = None, sheet: str | int | None = None,
               header_row: int | None = None) -> Table:
    """표 파일을 읽는다. source는 경로나 파일 내용. header_row는 머리글 행 번호(1부터). 비우면 찾는다."""
    if isinstance(source, (bytes, bytearray)):
        data, name = bytes(source), filename or "table"
    else:
        path = Path(source)
        name = filename or path.name
        try:
            data = path.read_bytes()
        except OSError as e:
            raise TableError(f"표 파일을 열지 못했습니다: {path} ({e.strerror or e}) → 경로와 접근 권한을 확인하세요") from e
    if name.lower().endswith((".joblib", ".pkl", ".pickle")):
        # 이런 모델 파일은 여는 순간 그 안의 코드가 실행될 수 있어서 받지 않는다
        raise TableError(f"'{name}' 같은 외부 모델 파일은 안전하게 열 수 없어 받지 않습니다 → 그 모델을 학습한 입력·출력 표(CSV·XLSX)를 주시면 여기서 예측 모델을 만듭니다")
    if not data.strip():
        raise TableError(f"'{name}' 파일이 비어 있습니다 → 데이터가 든 파일을 다시 고르세요")
    try:
        _not_a_table(data, name)
        if data[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
            return _read_xlsx(data, name, sheet, header_row)
        if data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # 옛 엑셀도 같은 머리이고, 할 일은 같다
            raise TableEncrypted(f"'{name}'은(는) 옛 엑셀 형식이거나 {ENCRYPTED}")
        if name.lower().endswith((".xlsx", ".xlsm", ".xlsb")):  # 엑셀 파일은 zip이다. 아니면 보안 프로그램이 감싼 것이다
            raise TableEncrypted(f"'{name}'은(는) {ENCRYPTED}")
        if Path(name).name.startswith(PASTED_FILE):
            # 입력창에서 AI로 보낸 표다. 화면에 바로 붙여 넣은 것과 같은 규칙으로 읽어야 입구에 따라 다른 모델이 되지 않는다
            return read_pasted(_decode(data, name)[0], name, header_row)
        return _read_csv(data, name, header_row)
    except TableError:
        raise
    except zipfile.BadZipFile as e:
        raise TableEncrypted(f"'{name}'은(는) {ENCRYPTED}") from e
    except (ET.ParseError, csv.Error, KeyError, IndexError, ValueError, OSError, RuntimeError) as e:
        raise TableError(f"'{name}'을(를) 표로 읽지 못했습니다 ({e.__class__.__name__}) → 엑셀에서 표를 복사해 붙여 넣으세요") from e


def _not_a_table(data: bytes, name: str):
    """앞머리나 확장자로 표가 아닌 파일을 먼저 가른다."""
    suffix = Path(name).suffix.lower()
    # BM·MZ는 글자 표의 첫 열 이름(모멘트 MZ 등)일 수 있어 글자가 아닐 때만 본다
    if any(data.startswith(m) for m in _FILE_MAGIC) and not (data[:2] in (b"BM", b"MZ") and not _looks_binary(data)):
        raise TableNotATable(f"'{name}'은(는) {NOT_TABLE}")
    if suffix not in TABLE_SUFFIXES and data[:4] not in (b"PK\x03\x04", b"PK\x05\x06"):
        raise TableNotATable(f"'{name}'은(는) {NOT_TABLE}")


def sniff_table_file(head: bytes, name: str):
    """파일 앞부분만으로 표로 쓸 수 없는 파일을 가른다. 표 이름의 파일만 본다. 큰 파일을 다 읽지 않고 올릴 때 바로 알리기 위해서다."""
    if Path(name).suffix.lower() not in ATTACH_CHECK_SUFFIXES or not head.strip():
        return
    _not_a_table(head, name)
    if head[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        return
    if head[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":  # 옛 엑셀이거나 암호화된 파일이다. 어느 쪽이든 할 일은 붙여넣기다
        raise TableEncrypted(f"'{name}'은(는) 옛 엑셀 형식이거나 {ENCRYPTED}")
    if name.lower().endswith((".xlsx", ".xlsm", ".xlsb")):
        raise TableEncrypted(f"'{name}'은(는) {ENCRYPTED}")
    cut = head.rfind(b"\n") if len(head) >= SNIFF_BYTES else -1  # 잘린 글자가 인코딩 판단을 흐리지 않게 마지막 줄바꿈까지만 본다
    _decode(head[:cut] if cut > 0 else head, name)


def _looks_binary(data: bytes) -> bool:
    """글자 파일에는 탭·줄바꿈 말고 제어 문자가 없다. 암호문에는 약 10% 들어 있다."""
    sample = data[:65536]
    control = sum((b < 32 and b not in (9, 10, 12, 13)) or b == 127 for b in sample)
    return control / max(1, len(sample)) > 0.01


def _decode(data: bytes, name: str) -> tuple[str, str, list[str]]:
    if data[:3] == b"\xef\xbb\xbf":
        return data.decode("utf-8-sig", errors="replace"), "utf-8-sig", []
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace"), "utf-16", []
    if _looks_binary(data):
        raise TableEncrypted(f"'{name}'은(는) {ENCRYPTED}")
    try:
        text, encoding, warnings = data.decode("utf-8"), "utf-8", []
    except UnicodeDecodeError:
        try:
            text, encoding, warnings = data.decode("cp949"), "cp949", []  # 한글 엑셀이 저장하는 기본 CSV
        except UnicodeDecodeError:
            sample = data[:65536]
            if sum(b >= 0x80 for b in sample) / len(sample) > 0.3:  # 글자가 아니라 암호문이다. 그대로 읽으면 깨진 표가 된다
                raise TableEncrypted(f"'{name}'은(는) {ENCRYPTED}") from None
            text, encoding, warnings = data.decode("latin-1"), "latin-1", ["글자 인코딩을 알아내지 못해 일부 글자가 깨졌을 수 있습니다 → 엑셀에서 표를 복사해 붙여 넣으면 정확합니다"]
    head = text[:65536]
    if len(head) >= 256 and not re.search(r"[,;|\t ]", head) and max(map(len, head.splitlines()), default=0) > 200:
        # 글자로만 된 암호문(base64 등): 구분자 없이 긴 줄 하나가 열 하나짜리 표로 읽히는 것을 막는다. 공백은 구분자다(솔버가 쓰는 표)
        raise TableEncrypted(f"'{name}'은(는) {ENCRYPTED}")
    return text, encoding, warnings


csv.field_size_limit(10 * 1024 * 1024)


def _guess_delimiter(lines: list[str]) -> str:
    # 쉼표를 마지막에 본다. 세미콜론 파일의 소수점 쉼표(1,5;2,5)를 구분자로 착각하지 않기 위해서다
    for d in ("\t", ";", "|", ","):
        widths = [len(r) for r in csv.reader(lines, delimiter=d) if r]
        if not widths:
            continue
        mode = max(statistics.multimode(widths))
        if mode > 1 and widths.count(mode) >= max(2, 0.6 * len(widths)):
            return d
    return ","


def _comma_evidence(rows: list[list[Any]]) -> tuple[int, int, int]:
    """쉼표가 무엇인지 알려 주는 칸 수: (소수점 쉼표, 소수점 점·천 단위, 어느 쪽인지 모를 '1,000')."""
    comma, dot, unsure = 0, 0, 0
    for row in rows[:500]:
        for c in row:
            s = str(c).strip()
            if _THOUSANDS.match(s):
                if _ZERO_COMMA.match(s):  # 0,125
                    comma += 1
                elif "." in s or s.count(",") > 1:  # 1,234.5 · 1,234,567
                    dot += 1
                else:
                    unsure += 1
            elif _EURO.match(s) or _DEC_COMMA.match(s):
                comma += 1
            elif _DOT_DEC.match(s):
                dot += 1
    return comma, dot, unsure


def _detect_decimal_comma(rows: list[list[Any]], delimiter: str) -> bool:
    if delimiter == ",":
        return False  # 쉼표 구분 파일의 "1,234"는 천 단위다
    comma, dot, unsure = _comma_evidence(rows)
    if comma > dot:
        return True
    # 세미콜론 CSV는 소수점 쉼표를 쓰는 형식이다. 1,000;20,000을 천 단위로 읽으면 모든 값이 천 배가 된다
    return delimiter == ";" and bool(unsure) and not dot


def _comma_warning(rows: list[list[Any]], delimiter: str, decimal_comma: bool) -> list[str]:
    """탭으로 나뉜 1,000은 어느 쪽인지 표만 봐서는 모른다. 한국 엑셀의 기본인 천 단위로 읽되 사용자가 알게 한다."""
    if delimiter == "," or decimal_comma:
        return []
    _, dot, unsure = _comma_evidence(rows)
    if not unsure or dot:
        return []
    return ["'1,000' 같은 값을 천 단위 쉼표로 읽었습니다(1,000 → 1000) → 소수점 쉼표라면 값이 1000배 커지니 소수점을 점으로 바꿔 다시 복사하세요"]


def _swallowed(rows: list[list[Any]], delimiter: str) -> bool:
    """닫히지 않은 따옴표가 뒤 행들을 한 칸에 삼켰는가. 칸 안의 줄이 표 한 행만큼 구분자를 가지면 그렇게 본다.
    따옴표 안 줄바꿈(엑셀의 Alt+Enter)은 그런 줄을 갖지 않는다."""
    if not rows:
        return False
    width = max(statistics.multimode([len(r) for r in rows]))
    need = max(1, width - 1)
    return any("\n" in str(c) and any(ln.count(delimiter) >= need for ln in str(c).split("\n")[1:]) for r in rows for c in r)


def _split_lines(text: str, delimiter: str, too_many: str) -> tuple[list[list[Any]], list[int]]:
    """줄마다 따로 읽는다. 짝이 맞지 않는 따옴표가 있는 줄만 글자로 본다. 따옴표로 묶인 다른 칸("1,234")은 그대로 읽어야 한다."""
    rows, numbers = [], []
    for i, line in enumerate(re.split(r"\r\n|\n|\r", text), 1):
        if not line.strip():
            continue
        try:
            row = next(csv.reader([line], delimiter=delimiter, strict=True))
        except (csv.Error, StopIteration):
            row = line.split(delimiter)
        if any(str(c).strip() for c in row):
            rows.append(row)
            numbers.append(i)
        if len(rows) > MAX_ROWS:
            raise TableError(too_many)
    return rows, numbers


def _check_space_header(rows: list[list[Any]]):
    """공백으로 나눈 표는 이름 안의 띄어쓰기도 칸을 나눈다. 'max stress'가 두 칸이 되면 값이 모두 다른 이름 아래로 밀린다."""
    numeric = [i for i, r in enumerate(rows) if sum(_numeric_cell(c, False) for c in r) >= 0.5 * len(r)]
    if not numeric:
        return
    width = max(statistics.multimode([len(rows[i]) for i in numeric]))
    heads = rows[:numeric[0]]
    if heads and not any(len(r) == width for r in heads):
        raise TableError(f"머리글은 {len(heads[-1])}칸인데 값은 {width}칸입니다. 공백으로 나뉜 표에서는 열 이름에 띄어쓰기가 있으면 칸을 나눌 수 없습니다 "
                         "→ 엑셀에서 복사해 붙여 넣거나 열 이름의 띄어쓰기를 없애 주세요")


def _text_rows(text: str, too_many: str, delimiter: str = "", prefer_tab: bool = False) -> tuple[list[list[Any]], list[int], str, list[str]]:
    """글 → 행. 파일과 붙여넣기가 같은 규칙을 쓴다. 입구마다 읽는 법이 다르면 같은 표가 다른 모델이 된다."""
    if not delimiter:
        sample = [ln for ln in text.splitlines()[:HEADER_SCAN_ROWS + 20] if ln.strip()]
        delimiter = "\t" if prefer_tab and "\t" in text else _guess_delimiter(sample)
        if delimiter == "," and "," not in text:  # 메모장·웹·솔버 출력의 공백 구분 표
            pairs = [(re.split(r"\s+", ln.strip()), i) for i, ln in enumerate(text.splitlines(), 1) if ln.strip()]
            rows = [p[0] for p in pairs]
            if len(rows) > MAX_ROWS:
                raise TableError(too_many)
            _check_space_header(rows)
            return rows, [p[1] for p in pairs], " ", []
    try:
        rows, numbers = [], []
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        for row in reader:
            if any(str(c).strip() for c in row):
                rows.append(row)
                numbers.append(reader.line_num)
            if len(rows) > MAX_ROWS:
                raise TableError(too_many)
        if not _swallowed(rows, delimiter):
            return rows, numbers, delimiter, []
    except csv.Error:
        pass
    rows, numbers = _split_lines(text, delimiter, too_many)
    return rows, numbers, delimiter, ["따옴표가 닫히지 않은 칸이 있어 그 줄의 따옴표를 글자로 읽었습니다 → 값이 어긋나 보이면 그 칸의 따옴표를 지워 다시 복사하세요"]


def _read_csv(data: bytes, name: str, header_row: int | None) -> Table:
    text, encoding, warnings = _decode(data, name)
    lines = text.splitlines()
    delimiter = ""
    if lines and re.match(r"^sep=.$", lines[0].strip(), re.I):  # 엑셀의 구분자 표시 줄
        delimiter = lines[0].strip()[4]
        text = "\n".join(lines[1:])
    rows, numbers, delimiter, notes = _text_rows(text, f"'{name}'의 행이 {MAX_ROWS:,}개를 넘습니다 → 필요한 행만 남겨 나눠 주세요", delimiter)
    decimal_comma = _detect_decimal_comma(rows, delimiter)
    table = _build(rows, numbers, name, header_row, decimal_comma)
    if (len(table.columns) < 2 or delimiter == " ") and not any(_numeric_cell(c, decimal_comma) for r in table.rows for c in r):
        # 숫자가 하나도 없으면 표가 아니다. 띄어쓰기 없는 긴 줄뿐이면 줄을 나눈 암호문(base64)이고, 그 밖에는 글 문서다
        cells = [str(c) for r in rows for c in r if str(c).strip()]
        if cells and all(" " not in c for c in cells) and sum(map(len, cells)) / len(cells) >= 40:
            raise TableEncrypted(f"'{name}'은(는) {ENCRYPTED}")
        raise TableNotATable(f"'{name}'은(는) {NOT_TABLE}")
    table.encoding, table.delimiter, table.decimal_comma = encoding, delimiter, decimal_comma
    table.warnings = warnings + notes + _comma_warning(rows, delimiter, decimal_comma) + table.warnings
    return table


# ---------- 붙여 넣은 글 ----------
PASTED_FILE = "붙여넣은_표"  # 입력창에 붙여 넣어 AI로 보낸 표의 파일 이름(frontend Composer)


def read_pasted(text: str, name: str = PASTED_NAME, header_row: int | None = None) -> Table:
    """엑셀에서 복사한 글(탭 구분 text/plain)을 파일과 같은 규칙으로 읽는다. 파일이 암호화된 회사에서는 이것이 기본 입구다."""
    # 엑셀 LEFT()가 이모지를 반으로 자르면 짝 없는 서로게이트가 온다. 그대로 두면 응답을 JSON으로 만들다 500이 된다
    text = _SURROGATE.sub("\ufffd", str(text or "")).lstrip("\ufeff").replace("\x00", "")
    if not text.strip():
        raise TableError("붙여 넣은 내용이 비어 있습니다 → 엑셀에서 표를 복사해 붙여 넣으세요")
    try:
        rows, numbers, delimiter, notes = _text_rows(text, f"붙여 넣은 표의 행이 {MAX_ROWS:,}개를 넘습니다 → 필요한 행만 골라 다시 복사해 주세요", prefer_tab=True)
    except csv.Error as e:
        raise TableError("붙여 넣은 내용을 표로 읽지 못했습니다 → 엑셀에서 표 범위를 골라 다시 복사해 주세요") from e
    decimal_comma = _detect_decimal_comma(rows, delimiter)
    table = _build(rows, numbers, name, header_row, decimal_comma)
    if len(table.columns) < 2:
        raise TableError("붙여 넣은 내용에 열이 하나뿐입니다. 입력과 출력이 함께 있어야 합니다 → 엑셀에서 머리글과 값을 함께 범위로 골라 복사해 주세요")
    table.encoding, table.delimiter = "text", delimiter
    table.warnings = notes + _comma_warning(rows, delimiter, decimal_comma) + table.warnings + [DIGITS_WARNING]
    return table


_SURROGATE = re.compile("[\ud800-\udfff]")


def clean_text(v: Any) -> Any:
    """짝 없는 서로게이트를 지운다. JSON으로 나갈 글자·이름에 쓴다."""
    return _SURROGATE.sub("\ufffd", v) if isinstance(v, str) else v


def _blank(v: Any) -> bool:
    return v is None or str(v).strip() == ""


def fill_merged(table: Table, keep: Any = ()) -> list[str]:
    """병합 셀은 첫 칸에만 값이 복사되고 나머지는 빈칸이 된다. 그대로 두면 그 행이 다 빠져 표가 쓸모없어진다.
    첫 칸에 값이 있고 빈칸이 4분의 1 이상인 열만 위 값으로 채우고 알린다. 출력(keep)은 채우지 않는다.
    해석이 실패한 행의 빈 출력을 앞 행 값으로 채우면 없는 결과를 지어내는 것이기 때문이다."""
    filled = []
    for c in table.columns:
        if c.name in keep or not table.rows:
            continue
        cells = [r[c.index] if c.index < len(r) else None for r in table.rows]
        blanks = sum(_blank(v) for v in cells)
        if _blank(cells[0]) or blanks < 2 or blanks < 0.25 * len(cells) or blanks == len(cells):
            continue
        last: Any = None
        for r in table.rows:
            if c.index >= len(r):
                r.extend([None] * (c.index + 1 - len(r)))
            if _blank(r[c.index]):
                r[c.index] = last
            else:
                last = r[c.index]
        filled.append(c.name)
        table.warnings.append(f"'{c.name}' 열의 빈칸 {blanks}개는 병합 셀로 보고 위 행 값으로 채웠습니다 → 병합 셀이 아니라면 빈칸을 값으로 채워 다시 복사해 주세요")
    return filled


def fill_guessing(table: Table, outputs: Any = None, has_output_file: bool = False) -> list[str]:
    """어느 입구로 온 표든 병합 셀을 같게 다룬다. 출력을 안 줬으면 추측한 출력을 지킨다. 출력이 다른 파일이면 이 표에는 지킬 출력이 없다."""
    keep = set() if has_output_file else set(outputs if outputs is not None else analyze(table)["outputs"])
    return fill_merged(table, keep=keep)


# ---------- XLSX ----------
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xml_root(zf: zipfile.ZipFile, name: str) -> ET.Element:
    if zf.getinfo(name).file_size >= 256 * 1024 * 1024:
        raise TableError("엑셀 내부 정보가 너무 큽니다 → 필요한 행과 열만 복사해 붙여 넣으세요")
    head = zf.read(name)
    if b"<!DOCTYPE" in head[:4096] or b"<!ENTITY" in head[:4096]:  # 외부 엔터티·폭탄을 막는다. 엑셀이 쓰는 파일에는 없다
        raise TableError("엑셀 파일 구조가 이상합니다 → 엑셀에서 표를 복사해 붙여 넣으세요")
    return ET.fromstring(head)


def _col_index(ref: str | None, fallback: int) -> int:
    letters = "".join(ch for ch in (ref or "") if ch.isalpha()).upper()
    if not letters:
        return fallback
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _si_text(el: ET.Element) -> str:
    # 읽는 법 표시(rPh)는 값이 아니다
    out: list[str] = []

    def walk(node: ET.Element):
        for ch in node:
            t = _local(ch.tag)
            if t == "rPh":
                continue
            if t == "t":
                out.append(ch.text or "")
            else:
                walk(ch)
    walk(el)
    return "".join(out)


# 엑셀 기본 표시 형식 중 날짜·시간인 번호
_DATE_FMT_IDS = {*range(14, 23), *range(27, 37), *range(45, 48), *range(50, 59)}


def _is_date_code(code: str) -> bool:
    plain = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code)  # 글자·색·조건은 형식이 아니다: 0.00"mm"
    return bool(re.search(r"[ymdhs]", plain, re.I)) and plain.strip().lower() != "general"


def _date_styles(zf: zipfile.ZipFile, names: set[str]) -> set[int]:
    """날짜로 표시한 칸의 스타일 번호. 엑셀은 날짜를 45000 같은 숫자로 저장해서 스타일을 보지 않으면 숫자 입력이 된다."""
    if "xl/styles.xml" not in names:
        return set()
    custom: dict[str, str] = {}
    out: set[int] = set()
    root = _xml_root(zf, "xl/styles.xml")
    for el in root.iter():
        if _local(el.tag) == "numFmt" and el.get("numFmtId"):
            custom[el.get("numFmtId")] = el.get("formatCode") or ""
    for el in root.iter():
        if _local(el.tag) == "cellXfs":
            for i, xf in enumerate(el):
                fid = xf.get("numFmtId") or "0"
                if (fid.isdigit() and int(fid) in _DATE_FMT_IDS) or (fid in custom and _is_date_code(custom[fid])):
                    out.add(i)
    return out


def _date_text(serial: float) -> Any:
    try:
        return (datetime(1899, 12, 30) + timedelta(days=serial)).isoformat(sep=" ", timespec="minutes").removesuffix(" 00:00")
    except (OverflowError, ValueError):
        return serial


def _cell_value(c: ET.Element, shared: list[str], dates: Any = frozenset()) -> Any:
    t = c.get("t")
    v = next((ch.text for ch in c if _local(ch.tag) == "v"), None)
    if t == "s":
        return shared[int(v)] if v not in (None, "") else None
    if t == "inlineStr":
        return next((_si_text(ch) for ch in c if _local(ch.tag) == "is"), None)
    if t == "b":
        return v == "1"
    if t in ("str", "e", "d"):
        return v
    if v in (None, ""):
        return None
    try:
        num = float(v)
    except ValueError:
        return v
    style = c.get("s")
    # 날짜는 글자로 넘긴다. 글자 값으로 빠지고 이유가 보인다. 날짜 일련번호로 된 슬라이더는 뜻이 없다
    return _date_text(num) if dates and style and style.isdigit() and int(style) in dates else num


def _shared_strings(zf: zipfile.ZipFile, name: str) -> list[str]:
    """전체 트리를 만들지 않고 읽는다. 짧은 글자 수백만 개면 트리만으로 메모리를 다 쓴다."""
    if zf.getinfo(name).file_size >= 256 * 1024 * 1024:
        raise TableError("엑셀 내부 정보가 너무 큽니다 → 필요한 행과 열만 복사해 붙여 넣으세요")
    with zf.open(name) as head:
        start = head.read(4096)
    if b"<!DOCTYPE" in start or b"<!ENTITY" in start:
        raise TableError("엑셀 파일 구조가 이상합니다 → 엑셀에서 표를 복사해 붙여 넣으세요")
    out: list[str] = []
    chars = 0
    with zf.open(name) as f:
        context = ET.iterparse(f, events=("start", "end"))
        _, root = next(context)
        for event, el in context:
            if event == "end" and _local(el.tag) == "si":
                text = _si_text(el)
                out.append(text)
                chars += len(text)
                root.clear()
                if len(out) > MAX_SHARED_STRINGS or chars > MAX_SHARED_CHARS:
                    raise TableError("엑셀 안의 글자 값이 너무 많습니다 → 필요한 행과 열만 복사해 붙여 넣으세요")
    return out


def _xlsx_sheet_rows(zf: zipfile.ZipFile, path: str, shared: list[str], dates: Any = frozenset()) -> tuple[list[list[Any]], list[int]]:
    rows: list[list[Any]] = []
    numbers: list[int] = []
    last = 0
    slots = 0
    sheet_data: ET.Element | None = None
    with zf.open(path) as head:
        if b"<!DOCTYPE" in head.read(4096):
            raise TableError("엑셀 파일 구조가 이상합니다 → 엑셀에서 표를 복사해 붙여 넣으세요")
    with zf.open(path) as f:
        for event, el in ET.iterparse(f, events=("start", "end")):
            if event == "start":
                if sheet_data is None and _local(el.tag) == "sheetData":
                    sheet_data = el
                continue
            if _local(el.tag) != "row":
                continue
            cells: dict[int, Any] = {}
            for i, c in enumerate(ch for ch in el if _local(ch.tag) == "c"):
                col = _col_index(c.get("r"), i)
                if col < MAX_COLUMNS:
                    cells[col] = _cell_value(c, shared, dates)
            last = int(el.get("r") or last + 1)
            el.clear()
            if sheet_data is not None:  # 읽은 행을 트리에서 떼어 낸다. 비운 원소도 쌓이면 메모리를 쓴다
                sheet_data.clear()
            if any(v is not None and str(v).strip() != "" for v in cells.values()):
                width = max(cells) + 1
                slots += width
                if slots > MAX_CELLS:
                    raise TableError(f"시트의 칸이 {MAX_CELLS:,}개를 넘습니다 → 필요한 행과 열만 복사해 붙여 넣으세요")
                rows.append([cells.get(i) for i in range(width)])
                numbers.append(last)
            if len(rows) > MAX_ROWS:
                raise TableError(f"시트의 행이 {MAX_ROWS:,}개를 넘습니다 → 필요한 행만 남겨 나눠 주세요")
    return rows, numbers


def _read_xlsx(data: bytes, name: str, sheet: str | int | None, header_row: int | None) -> Table:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        if "xl/workbook.xml" not in names:
            raise TableError(f"'{name}'은(는) 엑셀 파일이 아닌 것 같습니다 → 엑셀에서 표를 복사해 붙여 넣으세요")
        if sum(i.file_size for i in zf.infolist()) > 2 * 1024 ** 3:  # 압축 폭탄 방지
            raise TableError(f"'{name}'의 압축을 풀면 2GB를 넘습니다 → 필요한 행과 열만 복사해 붙여 넣으세요")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            shared = _shared_strings(zf, "xl/sharedStrings.xml")
        dates = _date_styles(zf, names)
        rels: dict[str, str] = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in _xml_root(zf, "xl/_rels/workbook.xml.rels"):
                if r.get("Id"):
                    target = str(r.get("Target") or "")
                    rels[r.get("Id")] = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        sheets: list[tuple[str, str, bool]] = []
        for el in _xml_root(zf, "xl/workbook.xml").iter():
            if _local(el.tag) == "sheet":
                rid = next((v for k, v in el.attrib.items() if _local(k) == "id"), "")
                sheets.append((el.get("name") or "", rels.get(rid, ""), el.get("state") in ("hidden", "veryHidden")))
        visible = [s for s in sheets if not s[2]] or sheets
        if not visible:
            raise TableError(f"'{name}'에 시트가 없습니다 → 데이터가 든 파일을 주세요")
        listed = [s[0] for s in visible]
        chosen: list[tuple[str, str, bool]]
        if sheet is not None and str(sheet) != "":
            if isinstance(sheet, int) or str(sheet).isdigit():
                i = int(sheet)
                if not 1 <= i <= len(visible):
                    raise TableError(f"시트 번호 {i}이(가) 없습니다 → 있는 시트: {', '.join(listed)}")
                chosen = [visible[i - 1]]
            else:
                chosen = [s for s in sheets if s[0] == str(sheet)]
                if not chosen:
                    raise TableError(f"시트 '{sheet}'이(가) 없습니다 → 있는 시트: {', '.join(listed)}")
        else:
            chosen = visible
        for sname, path, _ in chosen:  # 시트를 정하지 않으면 데이터가 든 첫 시트
            if path not in names:
                continue
            rows, numbers = _xlsx_sheet_rows(zf, path, shared, dates)
            if rows:
                table = _build(rows, numbers, name, header_row, False)
                table.sheet, table.sheets = sname, listed
                if len(visible) > 1 and sheet in (None, ""):
                    table.warnings.append(f"시트가 {len(visible)}개 있어 '{sname}'을(를) 읽었습니다 → 다른 시트가 맞으면 시트를 골라 주세요 ({', '.join(listed)})")
                return table
        raise TableError(f"'{name}'의 시트에 데이터가 없습니다 → 데이터가 든 시트를 골라 주세요 (있는 시트: {', '.join(listed)})")


def _filled(row: list[Any]) -> list[Any]:
    return [c for c in row if c is not None and str(c).strip() != ""]


def _is_unit_row(row: list[Any]) -> bool:
    cells = _filled(row)
    if not cells:
        return False
    return all(isinstance(c, str) and _looks_like_unit(c.strip().strip("[]()（）")) for c in cells)


def _numeric_cell(c: Any, decimal_comma: bool) -> bool:
    return cell_state(str(c).strip().rstrip("%") if isinstance(c, str) else c, decimal_comma)[1] == "ok"


def _strip_percent(columns: list[Column], rows: list[list[Any]], decimal_comma: bool, warnings: list[str]):
    """12.5%만 있는 열은 숫자 12.5와 단위 %로 읽는다. 복사한 글에는 %가 붙어 있어 그대로 두면 글자 열로 빠진다.
    %가 있는 값과 없는 값이 섞이면 무엇이 맞는지 알 수 없어 고치지 않는다. 그 행은 이유와 함께 제외된다."""
    for c in columns:
        cells = [r[c.index] for r in rows if c.index < len(r)]
        pct = [v for v in cells if isinstance(v, str) and v.strip().endswith("%") and _numeric_cell(v, decimal_comma)]
        if not pct or any(cell_state(v, decimal_comma)[1] == "ok" for v in cells):
            continue
        for r in rows:
            if c.index < len(r) and isinstance(r[c.index], str) and r[c.index].strip().endswith("%"):
                r[c.index] = r[c.index].strip().rstrip("%")
        c.unit = c.unit or "%"
        warnings.append(f"'{c.name}' 열은 % 값이라 숫자만 쓰고 단위를 %로 적었습니다")


def _find_header(rows: list[list[Any]], decimal_comma: bool) -> int | None:
    scan = rows[:HEADER_SCAN_ROWS]
    widths = [len(_filled(r)) for r in scan]
    multi = [w for w in widths if w >= 2]
    if not multi:
        return None
    data_width = max(statistics.multimode(multi))
    fallback = None
    for i, row in enumerate(scan):
        cells = _filled(row)
        if len(cells) < max(2, math.ceil(0.6 * data_width)):
            continue  # 제목 줄처럼 칸이 적은 행
        numeric = sum(_numeric_cell(c, decimal_comma) for c in cells)
        if numeric / len(cells) >= 0.5:
            return fallback  # 숫자 행이 먼저 나오면 그 앞까지만 머리글 후보다
        if numeric / len(cells) > 0.2:
            continue
        nxt = i + 1
        while nxt < len(rows) and _is_unit_row(rows[nxt]):
            nxt += 1
        if nxt < len(rows):
            following = _filled(rows[nxt])
            if following and sum(_numeric_cell(c, decimal_comma) for c in following) / len(following) >= 0.3:
                return i
        if fallback is None:
            fallback = i
    return fallback


# 엑셀 표 아래에 흔히 붙이는 요약 행의 이름
_SUMMARY = {"평균", "평균값", "합계", "합", "총계", "총합", "소계", "계", "최대", "최대값", "최댓값", "최소", "최소값", "최솟값", "표준편차", "편차", "분산", "중앙값", "범위", "개수", "건수",
            "average", "avg", "mean", "sum", "total", "subtotal", "max", "maximum", "min", "minimum", "std", "stdev", "stddev", "median", "count", "range"}


def _summary_label(row: list[Any], decimal_comma: bool) -> tuple[int, str] | None:
    """요약 행이면 (이름이 있는 칸, 이름). 이름 칸 하나와 숫자로 된 행만 요약 행으로 본다."""
    texts = [(i, str(c).strip()) for i, c in enumerate(row) if c is not None and str(c).strip() and not _numeric_cell(c, decimal_comma)]
    if len(texts) != 1 or len(_filled(row)) < 2:
        return None
    label = re.sub(r"[\s:：.]+", "", texts[0][1]).lower()
    return texts[0] if label in _SUMMARY else None


def _drop_summary_rows(rows: list[list[Any]], numbers: list[int], decimal_comma: bool, warnings: list[str]) -> int:
    """표 아래쪽의 평균·합계 행을 뺀다. 데이터로 들어가면 없는 설계가 학습되고 입력 범위가 늘어나며, 그 칸의 글자 때문에 번호 열도 입력으로 잘못 읽힌다.
    맨 아래에 이어진 행만 본다. 위쪽에도 같은 값이 있는 열이면 요약이 아니라 범주(조건 max·min)다."""
    i, found = len(rows), []
    while i > 0 and len(found) < 8:
        hit = _summary_label(rows[i - 1], decimal_comma)
        if hit is None:
            break
        found.append(hit)
        i -= 1
    if not found or i < 3:
        return 0
    if any(col < len(r) and str(r[col]).strip() == text for col, text in found for r in rows[:i]):
        return 0
    del rows[i:], numbers[i:]
    labels = ", ".join(text for _, text in reversed(found))
    warnings.append(f"표 아래쪽의 요약 행 {len(found)}개({labels})는 데이터가 아니라서 뺐습니다 → 실제 데이터라면 그 칸의 이름을 바꿔 다시 복사하세요")
    return len(found)


def _build(rows: list[list[Any]], numbers: list[int], name: str, header_row: int | None, decimal_comma: bool) -> Table:
    if not rows:
        raise TableError(f"'{name}'에 데이터가 없습니다 → 데이터가 든 파일을 다시 고르세요")
    warnings: list[str] = []
    if header_row is not None:
        if header_row not in numbers:
            raise TableError(f"{header_row}번 행은 비어 있어 머리글로 쓸 수 없습니다 → 머리글이 있는 행 번호를 주세요")
        h = numbers.index(header_row)
    else:
        h = _find_header(rows, decimal_comma)
    width = max(len(r) for r in rows)
    if width > MAX_COLUMNS:
        raise TableError(f"'{name}'의 열이 {MAX_COLUMNS:,}개를 넘습니다 → 필요한 열만 남겨 주세요")
    units: dict[int, str] = {}
    if h is None:
        header = [f"열{i + 1}" for i in range(width)]
        body_start = 0
        warnings.append("머리글을 찾지 못해 열 이름을 '열1, 열2…'로 붙였습니다 → 첫 줄에 열 이름을 넣으면 입력·출력을 더 잘 알아봅니다")
    else:
        raw = rows[h]
        header = [str(raw[i]).strip() if i < len(raw) and raw[i] is not None else "" for i in range(width)]
        body_start = h + 1
        if body_start < len(rows) and _is_unit_row(rows[body_start]):  # 머리글 아래 단위만 적은 줄
            ur = rows[body_start]
            units = {i: str(ur[i]).strip().strip("[]()（）") for i in range(len(ur)) if ur[i] is not None and str(ur[i]).strip()}
            body_start += 1
    columns: list[Column] = []
    seen: dict[str, int] = {}
    # 머리글에 실제로 있는 이름. a,a,a_2처럼 바꾼 이름이 뒤의 진짜 열과 겹치면 사용자가 고칠 수 없는 '두 번 지정' 오류가 된다
    planned = {split_unit(re.sub(r"^(in|input|out|output)\s*[:：]\s*", "", h, flags=re.I))[0][:100] for h in header}
    for i, raw_name in enumerate(header):
        hint = ""
        text = raw_name
        m = re.match(r"^(in|input|out|output)\s*[:：]\s*(.+)$", text, re.I)
        if m:
            hint = "input" if m.group(1).lower().startswith("in") else "output"
            text = m.group(2)
        nm, unit = split_unit(text)
        nm = nm[:100]
        if not nm:
            if not any(i < len(r) and _filled([r[i]]) for r in rows[body_start:]):
                continue  # 이름도 값도 없는 빈 열
            nm = f"열{i + 1}"
            warnings.append(f"{i + 1}번째 열에 이름이 없어 '{nm}'으로 붙였습니다")
        if nm in seen:
            k = seen[nm] + 1
            while f"{nm}_{k}" in planned or f"{nm}_{k}" in seen:
                k += 1
            seen[nm] = k
            warnings.append(f"열 이름 '{nm}'이(가) 겹쳐 뒤의 것을 '{nm}_{k}'로 바꿨습니다 → 맞는 열인지 확인하세요")
            nm = f"{nm}_{k}"
        seen[nm] = 1
        columns.append(Column(name=nm, raw=raw_name, unit=unit or units.get(i, ""), index=i, role_hint=hint))
    body, body_numbers = rows[body_start:], numbers[body_start:]
    _drop_summary_rows(body, body_numbers, decimal_comma, warnings)
    _strip_percent(columns, body, decimal_comma, warnings)
    if h and len(_filled(rows[h - 1])) == len(_filled(rows[h])) and not any(_numeric_cell(c, decimal_comma) for c in _filled(rows[h - 1])):
        # 이름 없는 단위 줄을 머리글로 읽었을 수 있다. 진짜 이름이 조용히 사라지면 안 된다
        above = ", ".join(str(c).strip() for c in _filled(rows[h - 1])[:6])
        warnings.append(f"바로 위 줄({above})도 머리글처럼 보이지만 쓰지 않았습니다 → 그 줄이 열 이름이면 그 아래 줄을 지우고 다시 복사하세요")
    return Table(source=name, columns=columns, rows=body, row_numbers=body_numbers,
                 header_row=numbers[h] if h is not None else None, skipped_rows=h or 0, decimal_comma=decimal_comma, warnings=warnings)


# ---------- 열 분석 ----------
_OUTPUT_WORDS = {"stress", "strain", "mises", "vonmises", "disp", "displacement", "deflection", "deformation", "force", "reaction",
                 "mass", "weight", "energy", "temp", "temperature", "pressure", "freq", "frequency", "result", "results", "output",
                 "objective", "response", "peak", "max", "maximum", "min", "minimum", "mean", "avg", "cost", "volume", "drag", "lift",
                 "efficiency", "damage", "life", "fatigue", "hic", "acceleration", "accel", "intrusion", "buckling", "safety"}
_INPUT_WORDS = {"thickness", "width", "height", "length", "radius", "angle", "depth", "diameter", "pitch", "gap", "param", "parameter",
                "input", "var", "variable", "design", "t", "w", "h", "l", "r", "d", "x", "x1", "x2", "x3", "x4", "x5", "x6", "dv"}
_OUTPUT_KO = ("응력", "변형", "변위", "처짐", "질량", "무게", "반력", "에너지", "결과", "출력", "온도", "압력", "진동수", "주파수", "수명", "피로", "손상", "가속도", "침입량", "좌굴", "효율", "비용", "최대", "최소", "평균")
_INPUT_KO = ("두께", "폭", "너비", "높이", "길이", "반경", "반지름", "각도", "깊이", "지름", "직경", "간격", "입력", "변수", "설계")
_ID_WORDS = {"id", "no", "num", "number", "idx", "index", "case", "caseid", "run", "runid", "row", "seq", "sample", "sampleid", "sampleno", "test", "testid", "testno", "번호", "순번", "연번", "일련번호", "케이스", "행", "샘플", "시험"}


def _tokens(name: str) -> list[str]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [t for t in re.split(r"[^0-9a-zA-Z가-힣]+", spaced.lower()) if t]


def _name_role(name: str) -> str:
    low = name.lower()
    toks = _tokens(name)
    # 출력 낱말을 먼저 본다: max_stress, thickness_mass_ratio처럼 둘 다 있으면 대개 결과값이다
    if any(t in _OUTPUT_WORDS for t in toks) or any(w in low for w in _OUTPUT_KO):
        return "output"
    if any(t in _INPUT_WORDS for t in toks) or any(w in low for w in _INPUT_KO):
        return "input"
    if re.fullmatch(r"(y|f)\d*", low):
        return "output"
    return ""


def _is_id_name(name: str) -> bool:
    low = re.sub(r"[\s._#-]+", "", name.lower())  # No. · case_id · 시험 번호
    return low in _ID_WORDS or low.endswith("번호") or any(low.startswith(w) and low[len(w):].isdigit() for w in ("case", "run", "sample", "test"))


def _is_id_column(name: str, nums: list[float], index: int, n_texts: int = 0) -> bool:
    if not nums or any(v != int(v) for v in nums):
        return False
    if _is_id_name(name):
        # 이름이 번호면 필터로 건너뛴 번호(1,3,5…)와 글자 몇 칸이 있어도 번호다. 입력으로 들어가면 Simulator에 번호 슬라이더가 생긴다
        return len(set(nums)) >= 0.9 * len(nums) and n_texts <= 0.1 * len(nums)
    # 이름으로 뜻을 모르는 열의 1,2,3…은 순번이다. thickness 1,2,3은 실제 변수이므로 이름을 먼저 본다
    if n_texts or _name_role(name) or len(nums) < (3 if index == 0 else 5):
        return False
    return all(b - a == 1 for a, b in zip(nums, nums[1:])) and nums[0] in (0, 1)


def _quartiles(xs: list[float]) -> tuple[float, float]:
    s = sorted(xs)
    n = len(s)

    def at(q: float) -> float:
        pos = q * (n - 1)
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)
    return at(0.25), at(0.75)


def find_outliers(values: list[float]) -> list[int]:
    """값이 다른 값들과 크게 떨어진 위치(사분위 범위의 3배 밖). 값은 그대로 두고 경고에만 쓴다."""
    if len(values) < 8:
        return []
    q1, q3 = _quartiles(values)
    iqr = q3 - q1
    if iqr <= 0:
        return []
    lo, hi = q1 - 3 * iqr, q3 + 3 * iqr
    return [i for i, v in enumerate(values) if v < lo or v > hi]


def _profile(table: Table, col: Column) -> dict[str, Any]:
    nums: list[float] = []
    positions: list[int] = []
    texts: list[str] = []
    missing = nonfinite = errors = 0
    for i, row in enumerate(table.rows):
        v = row[col.index] if col.index < len(row) else None
        num, state = cell_state(v, table.decimal_comma)
        if state == "ok":
            nums.append(num)
            positions.append(i)
        elif state == "missing":
            missing += 1
        elif state == "nonfinite":
            nonfinite += 1
        elif state == "error":
            errors += 1
        else:
            texts.append(str(v).strip())
    distinct = set(nums)
    info: dict[str, Any] = {"name": col.name, "raw": col.raw, "unit": col.unit, "n_numeric": len(nums), "n_missing": missing,
                            "n_text": len(texts), "n_nonfinite": nonfinite, "n_error": errors, "unique": len(distinct) + len(set(texts)),
                            "min": min(nums) if nums else None, "max": max(nums) if nums else None, "warnings": []}
    if not nums and not texts:
        kind = "empty"
    elif len(texts) > len(nums):
        kind = "constant" if len(set(texts)) == 1 and not nums else "categorical"
        info["values"] = sorted(set(texts))[:8]
        if kind == "constant":
            info["value"] = texts[0]
    elif len(distinct) == 1 and not texts:
        kind = "constant"
        info["value"] = nums[0]
    elif not col.role_hint and _is_id_column(col.name, nums, col.index, len(texts)):
        kind = "id"
    else:
        kind = "numeric"
    info["kind"] = kind
    if kind == "numeric":
        out = find_outliers(nums)
        if out:
            rows = [table.row_numbers[positions[i]] for i in out[:5]]
            vals = ", ".join(f"{nums[i]:g}" for i in out[:3])
            info["outlier_rows"] = rows
            info["warnings"].append(f"값 {vals}이(가) 다른 값과 크게 떨어져 있습니다({', '.join(map(str, rows))}행) → 입력이면 Simulator 범위가 넓어지니 값이 맞는지 확인하세요")
        if texts or missing or nonfinite or errors:
            bad = []
            if errors:
                bad.append(f"엑셀 수식 오류(#N/A·#DIV/0! 등) {errors}개")
            if missing:
                bad.append(f"빈 칸 {missing}개")
            if texts:
                bad.append(f"숫자가 아닌 값 {len(texts)}개(예: {texts[0][:20]})")
            if nonfinite:
                bad.append(f"무한대·너무 큰 값 {nonfinite}개")
            info["warnings"].append(", ".join(bad) + "가 있어 그 행은 제외될 수 있습니다")
    return info


def _guess_roles(table: Table, cols: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    undecided = []
    by_name = {c.name: c for c in table.columns}
    for c in cols:
        hint = by_name[c["name"]].role_hint
        if c["kind"] in ("empty", "constant") and not hint:  # 결과 열이 쓸 수 없어 빠지면 왜 빠졌는지 질문 화면이 말해 준다
            c["looks_output"] = _name_role(c["name"]) == "output"
        if c["kind"] == "empty":
            c["role"], c["role_reason"] = "exclude", ("값이 전부 엑셀 수식 오류(#N/A 등)입니다" if c["n_error"] and not c["n_missing"] else
                                                       "값이 전부 무한대이거나 너무 큽니다" if c["n_nonfinite"] else
                                                       "수식 오류와 빈칸뿐입니다" if c["n_error"] else "값이 비어 있습니다")
        elif c["kind"] == "categorical" and c["unique"] >= max(2, len(table.rows)):
            # 행마다 다른 글자는 범주가 아니라 케이스 이름이다. "값별로 표를 나누라"는 안내가 맞지 않다
            is_date = all(re.match(r"^\d{4}[-./]\d{1,2}[-./]\d{1,2}", str(v)) for v in c.get("values") or ["x"])
            c["role"], c["role_reason"], c["row_label"] = "exclude", "날짜 값이라 입력으로 쓰지 않습니다" if is_date else "행마다 다른 이름이라 값으로 쓰지 않습니다", True
        elif c["kind"] == "categorical":
            c["role"], c["role_reason"] = "exclude", "글자 값(범주)이라 예측 모델의 입력으로 쓸 수 없습니다"
            c["warnings"].append("글자 값은 학습에서 뺍니다. 값마다 결과가 크게 다르면 값별로 표를 나눠 따로 만드는 것이 안전합니다")
        elif c["kind"] == "constant":
            c["role"], c["role_reason"] = "fixed", "값이 하나뿐이라 변수가 아닙니다. 고정값으로 기록합니다"
        elif c["kind"] == "id":
            c["role"], c["role_reason"] = "exclude", "순번으로 보입니다"
            if not _is_id_name(c["name"]) and by_name[c["name"]].index != 0:
                warnings.append(f"'{c['name']}' 열은 1씩 늘어나는 값이라 행 번호로 보고 뺐습니다 → 실제 입력이면 열 이름에 '입력'을 붙여 다시 복사하세요")
        elif hint:
            c["role"], c["role_reason"] = hint, "머리글에 입력·출력 표시가 있습니다"
        else:
            role = _name_role(c["name"])
            if role:
                c["role"], c["role_reason"] = role, "열 이름으로 추측했습니다"
            else:
                undecided.append(c)
    has_out = any(c.get("role") == "output" for c in cols)
    has_in = any(c.get("role") == "input" for c in cols)
    if undecided:
        if has_out or has_in:
            rest = "input" if has_out else "output"
            for c in undecided:
                c["role"], c["role_reason"] = rest, "다른 열 이름을 보고 추측했습니다"
        else:
            for c in undecided[:-1]:
                c["role"], c["role_reason"] = "input", "열 위치로 추측했습니다"
            undecided[-1]["role"], undecided[-1]["role_reason"] = "output", "열 위치로 추측했습니다(마지막 숫자 열)"
            warnings.append("열 이름만으로 입력·출력을 알 수 없어 마지막 숫자 열을 출력으로 추측했습니다 → 맞는지 확인하세요")
    return warnings


def analyze(table: Table) -> dict[str, Any]:
    """열마다 종류(숫자·상수·범주·순번·빈 열)와 입력·출력 추측을 돌려준다. 화면과 inspect_table이 그대로 쓴다."""
    cols = [_profile(table, c) for c in table.columns]
    warnings = list(table.warnings) + _guess_roles(table, cols)
    records = table.records(numeric=False)[:5]
    sample = [{k: _json_safe(v) for k, v in r.items()} for r in records]
    if table.header_row and table.skipped_rows:
        warnings.append(f"앞의 {table.skipped_rows}줄은 제목으로 보고 건너뛰고 {table.header_row}번 행을 머리글로 읽었습니다 → 틀리면 머리글 행을 지정하세요")
    return {"source": table.source, "sheet": table.sheet, "sheets": table.sheets, "header_row": table.header_row,
            "skipped_rows": table.skipped_rows, "n_rows": len(table.rows), "encoding": table.encoding, "delimiter": table.delimiter,
            "decimal_comma": table.decimal_comma, "columns": cols, "warnings": warnings, "sample_rows": sample,
            "inputs": [c["name"] for c in cols if c.get("role") == "input"],
            "outputs": [c["name"] for c in cols if c.get("role") == "output"],
            "fixed": [c["name"] for c in cols if c.get("role") == "fixed"],
            "excluded": [c["name"] for c in cols if c.get("role") == "exclude"]}


# ---------- 두 파일 결합 ----------
def _key_text(v: Any, decimal_comma: bool) -> str:
    num, state = cell_state(v, decimal_comma)
    if state == "ok":
        return format(num, ".12g")
    return "" if state == "missing" else str(v).strip()


def join_tables(left: Table, right: Table, key: str | None = None) -> tuple[Table, dict[str, Any]]:
    """입력 파일과 출력 파일을 key 열로 잇는다. 양쪽에 다 있는 행만 남기고, 맞지 않은 행은 보고한다."""
    shared = [n for n in left.names if n in right.names]
    if key is None:
        if len(shared) != 1:
            hint = f"겹치는 열: {', '.join(shared)}" if shared else "겹치는 열 이름이 없습니다"
            raise TableError(f"두 파일을 이을 열을 정할 수 없습니다({hint}) → 케이스 번호처럼 양쪽에 같은 값이 든 열을 key로 지정하세요")
        key = shared[0]
    if key not in left.names or key not in right.names:
        raise TableError(f"두 파일에 모두 있는 열이 아닙니다: {key} → 양쪽 파일의 열 이름을 확인하세요 ({', '.join(left.names)} / {', '.join(right.names)})")
    lk = next(c for c in left.columns if c.name == key)
    rk = next(c for c in right.columns if c.name == key)
    right_rows: dict[str, list[Any]] = {}
    dup_right: list[str] = []
    for row in right.rows:
        k = _key_text(row[rk.index] if rk.index < len(row) else None, right.decimal_comma)
        if not k:
            continue
        if k in right_rows:
            dup_right.append(k)
        else:
            right_rows[k] = row
    extra: list[Column] = []
    width = max((c.index for c in left.columns), default=-1) + 1
    taken = set(left.names)
    for c in right.columns:
        if c.name == key:
            continue
        name = c.name
        k = 2
        while name in taken:  # 왼쪽에 이미 y, y_2가 있어도 겹치지 않게
            name = f"{c.name}_{k}"
            k += 1
        taken.add(name)
        extra.append(Column(name=name, raw=c.raw, unit=c.unit, index=width + len(extra), role_hint=c.role_hint))
    src_index = {e.name: c.index for e, c in zip(extra, [c for c in right.columns if c.name != key])}
    rows, numbers, only_left, used, dup_left = [], [], [], set(), []
    for n, row in zip(left.row_numbers, left.rows):
        k = _key_text(row[lk.index] if lk.index < len(row) else None, left.decimal_comma)
        match = right_rows.get(k)
        if match is None:
            only_left.append(k or f"{n}행")
            continue
        if k in used:
            dup_left.append(k)
            continue
        used.add(k)
        # 오른쪽 열은 width 칸부터다. 행 끝의 빈 칸(엑셀이 저장한 ,,)을 그대로 두면 값이 다른 열 이름 아래로 밀린다
        base = (list(row) + [None] * width)[:width]
        vals = []
        for e in extra:
            raw_v = match[src_index[e.name]] if src_index[e.name] < len(match) else None
            # 오른쪽 파일의 소수점 쉼표 규칙이 다를 수 있어 여기서 숫자로 바꿔 둔다
            num, state = cell_state(raw_v, right.decimal_comma)
            vals.append(num if state == "ok" else raw_v)
        rows.append(base + vals)
        numbers.append(n)
    only_right = [k for k in right_rows if k not in used]
    if not rows:
        raise TableError(f"'{key}' 값이 두 파일에서 하나도 맞지 않습니다 → 같은 케이스를 가리키는 값인지, 형식(1과 C0001 등)이 같은지 확인하세요")
    warnings = list(left.warnings) + list(right.warnings)
    if only_left or only_right:
        warnings.append(f"'{key}'가 한쪽에만 있는 행이 있어 뺐습니다: 입력 쪽 {len(only_left)}개, 출력 쪽 {len(only_right)}개")
    if dup_left or dup_right:
        warnings.append(f"'{key}' 값이 겹치는 행은 첫 행만 썼습니다: 입력 쪽 {len(dup_left)}개, 출력 쪽 {len(dup_right)}개")
    joined = Table(source=f"{left.source} + {right.source}", columns=[*left.columns, *extra], rows=rows, row_numbers=numbers,
                   header_row=left.header_row, skipped_rows=left.skipped_rows, sheet=left.sheet, sheets=left.sheets,
                   encoding=left.encoding, delimiter=left.delimiter, decimal_comma=left.decimal_comma, warnings=warnings)
    report = {"key": key, "matched": len(rows), "only_left": only_left[:50], "only_right": only_right[:50],
              "duplicates_left": dup_left[:50], "duplicates_right": dup_right[:50]}
    return joined, report
