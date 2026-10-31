"""어느 시점에서든 진입: 이미 가진 표·해석 결과로 시작해 Simulator까지 간다. 해석 0건, PC Agent 없이.
시나리오 번호는 docs/entry-points.md의 E0~E16과 맞춘다."""
import io
import json
import math
import sys
import threading
import time
import zipfile
from pathlib import Path

import numpy as np
import pytest

from server.tests.test_api import _auth, _wait_idle, client  # noqa: F401 — 같은 서버 fixture 재사용, 서버 코드보다 먼저 불러온다

from agent.dds_agent.config import AgentSettings  # noqa: E402
from agent.dds_agent.executor import CaseExecutor  # noqa: E402
from agent.dds_agent.workspace import Workspace  # noqa: E402
from cae_adapters.tabular import TableError, analyze, cell_state, find_outliers, join_tables, read_table, split_unit  # noqa: E402
from server.app.agent.link import registry  # noqa: E402
from server.app.config import settings  # noqa: E402
from server.app.study import service  # noqa: E402
from server.app.study.surrogate import space_filling_subset  # noqa: E402


# ---------- 도우미 ----------
def _project(client, h, name="진입"):  # noqa: F811
    return client.post("/api/projects", json={"name": name}, headers=h).json()["id"]


def _f(t, w):
    return 1000.0 / (t * w) + 0.5 * t


def _rows(n=20, seed=3):
    rng = np.random.default_rng(seed)
    return [{"thickness": float(t), "width": float(w), "stress": _f(float(t), float(w))}
            for t, w in zip(rng.uniform(1, 5, n), rng.uniform(10, 40, n))]


def _csv(rows, head=None, sep=",") -> bytes:
    head = head or list(rows[0])
    lines = [sep.join(head)] + [sep.join(str(r.get(k, "")) for k in rows[0]) for r in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _import(client, h, pid, rows=None, **kw):  # noqa: F811
    body = {"name": "표", "source": "t.csv", "inputs": [{"name": "thickness", "unit": "mm"}, {"name": "width"}],
            "outputs": [{"name": "stress", "unit": "MPa"}], "rows": rows if rows is not None else _rows(), **kw}
    # json=을 쓰면 httpx가 NaN을 보내기 전에 막는다. 실제 JSON NaN이 서버까지 가야 시험이 된다
    return client.post(f"/api/projects/{pid}/studies/import", content=json.dumps(body), headers={**h, "content-type": "application/json"})


def _xlsx(sheets: dict[str, list[list]], hidden=()) -> bytes:
    """openpyxl 없이 만드는 작은 XLSX. 글자는 sharedStrings·inlineStr을 번갈아 써서 두 경로를 다 지난다."""
    shared: list[str] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        wb, rels = [], []
        for i, (name, rows) in enumerate(sheets.items(), 1):
            state = ' state="hidden"' if name in hidden else ""
            wb.append(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"{state}/>')
            rels.append(f'<Relationship Id="rId{i}" Type="x" Target="worksheets/sheet{i}.xml"/>')
            xml = []
            for r, row in enumerate(rows, 1):
                cells = []
                for c, v in enumerate(row):
                    ref = f"{chr(65 + c)}{r}"
                    if v is None:
                        continue
                    if isinstance(v, bool):
                        cells.append(f'<c r="{ref}" t="b"><v>{int(v)}</v></c>')
                    elif isinstance(v, (int, float)):
                        cells.append(f'<c r="{ref}"><v>{v}</v></c>')
                    elif isinstance(v, dict):  # 수식과 캐시값
                        cells.append(f'<c r="{ref}"><f>{v["f"]}</f><v>{v["v"]}</v></c>')
                    elif len(shared) % 2:
                        shared.append(v)
                        cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>')
                    else:
                        shared.append(v)
                        cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
                xml.append(f'<row r="{r}">{"".join(cells)}</row>')
            z.writestr(f"xl/worksheets/sheet{i}.xml", f'<worksheet xmlns="m"><sheetData>{"".join(xml)}</sheetData></worksheet>')
        z.writestr("xl/workbook.xml", f'<workbook xmlns="m" xmlns:r="r"><sheets>{"".join(wb)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="p">{"".join(rels)}</Relationships>')
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="m">' + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
    return buf.getvalue()


def _no_nan(obj):
    """응답·저장값 어디에도 NaN·Inf가 없어야 한다."""
    if isinstance(obj, float):
        assert math.isfinite(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _no_nan(v)
    elif isinstance(obj, list):
        for v in obj:
            _no_nan(v)


# ---------- 표 파서 ----------
def test_tabular_encodings_delimiters_numbers():
    # 엑셀 기본 CSV: CP949, 한글 머리글
    t = read_table("두께(mm),응력 (MPa)\n1.5,200\n2.5,100\n3,50\n".encode("cp949"), "a.csv")
    assert t.encoding == "cp949" and t.names == ["두께", "응력"] and t.columns[0].unit == "mm" and t.columns[1].unit == "MPa"
    # UTF-8 BOM
    t = read_table("\ufeffx,y\n1,2\n3,4\n".encode("utf-8"), "a.csv")
    assert t.names == ["x", "y"] and t.encoding == "utf-8-sig"
    # 세미콜론 + 소수점 쉼표: 쉼표를 구분자로 착각하지 않는다
    t = read_table(b"a;b\n1,5;2,25\n3,5;4,75\n1,25;9,5\n", "a.csv")
    assert t.delimiter == ";" and t.decimal_comma and t.records()[0]["a"] == 1.5 and t.records()[0]["b"] == 2.25
    # 탭
    t = read_table(b"a\tb\n1\t2\n3\t4\n", "a.txt")
    assert t.delimiter == "\t" and t.records()[1]["b"] == 4
    # 쉼표 구분 파일의 따옴표 안 천 단위
    t = read_table(b'a,b\n"1,234",2\n"12,345.5",3\n', "a.csv")
    assert not t.decimal_comma and [r["a"] for r in t.records()] == [1234.0, 12345.5]
    assert cell_state("1e999")[1] == "nonfinite" and cell_state("N/A")[1] == "missing" and cell_state("abc")[1] == "text"
    assert cell_state(True)[1] == "bool" and cell_state(float("nan"))[1] == "missing" and cell_state(float("inf"))[1] == "nonfinite" and cell_state("#DIV/0!")[1] == "error"
    assert cell_state("#N/A")[1] == "error" and cell_state("#####")[1] == "error"


def test_tabular_title_rows_and_units():
    text = "해석 결과 요약\n2024-01-01 작성,,\nthickness [mm],max(stress),응력 (MPa)\n1,2,3\n4,5,6\n7,8,9\n"
    t = read_table(text.encode("utf-8"), "a.csv")
    assert t.header_row == 3 and t.skipped_rows == 2 and t.names == ["thickness", "max(stress)", "응력"]
    assert [c.unit for c in t.columns] == ["mm", "", "MPa"]
    assert t.records()[0]["_row"] == 4  # 사용자가 찾을 수 있게 파일의 행 번호를 준다
    assert split_unit("max(stress)") == ("max(stress)", "") and split_unit("force [N/mm2]") == ("force", "N/mm2")
    # 머리글 아래 단위 줄
    t = read_table(b"t,s\nmm,MPa\n1,2\n3,4\n", "a.csv")
    assert [c.unit for c in t.columns] == ["mm", "MPa"] and len(t.rows) == 2
    # 머리글 없음
    t = read_table(b"1,2\n3,4\n5,6\n", "a.csv")
    assert t.names == ["열1", "열2"] and len(t.rows) == 3 and t.warnings
    # 머리글 겹침
    t = read_table(b"a,a,b\n1,2,3\n4,5,6\n", "a.csv")
    assert t.names == ["a", "a_2", "b"]
    assert read_table(b"a,b\n1,2\n", "a.csv", header_row=1).names == ["a", "b"]
    with pytest.raises(TableError):
        read_table(b"a,b\n1,2\n", "a.csv", header_row=9)


def test_tabular_xlsx():
    data = _xlsx({"숨김": [["x"], [1]], "결과": [["제목 줄"], ["thickness [mm]", "width", "응력", "ok"], [1.5, 2, {"f": "A3*2", "v": 3}, True], [2.5, 4, {"f": "A4*2", "v": 5}, False], [3.5, 6, 7, True]],
                  "둘째": [["a", "b"], [1, 2]]}, hidden=("숨김",))
    t = read_table(data, "a.xlsx")
    assert t.sheet == "결과" and t.sheets == ["결과", "둘째"] and t.header_row == 2
    assert t.names == ["thickness", "width", "응력", "ok"] and t.columns[0].unit == "mm"
    rec = t.records()
    assert rec[0]["응력"] == 3.0 and rec[0]["ok"] is True and rec[0]["_row"] == 3  # 수식 캐시값·불리언
    assert any("시트" in w for w in t.warnings)
    assert read_table(data, "a.xlsx", sheet="둘째").names == ["a", "b"]
    assert read_table(data, "a.xlsx", sheet=2).names == ["a", "b"]
    with pytest.raises(TableError, match="있는 시트"):
        read_table(data, "a.xlsx", sheet="없음")


@pytest.mark.parametrize("data,hint", [
    (b"", "비어"), (b"   \n ", "비어"), (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest", "복사해 붙여 넣으세요"), (b"PK\x03\x04broken", "복사해 붙여 넣으세요"),
    (b"\x89PNG\x00\x00\x00binary", "복사해 붙여 넣으세요"),
])
def test_tabular_bad_files_explain(data, hint):
    with pytest.raises(TableError, match=hint):
        read_table(data, "a.bin")


def test_tabular_refuses_external_model_files():
    with pytest.raises(TableError, match="입력·출력 표"):
        read_table(b"anything", "model.joblib")


def test_tabular_xlsx_without_workbook_and_doctype():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.txt", "x")
    with pytest.raises(TableError, match="엑셀 파일이 아닌"):
        read_table(buf.getvalue(), "a.xlsx")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", '<!DOCTYPE a [<!ENTITY x "y">]><workbook/>')
    with pytest.raises(TableError, match="구조"):
        read_table(buf.getvalue(), "a.xlsx")


def test_tabular_analyze_guesses_roles():
    rows = [["no", "두께 (mm)", "재질", "하중", "응력", "빈 칸"]]
    for i in range(1, 11):
        rows.append([i, i * 0.5 + 0.1 * (i % 3), "SS400" if i % 2 else "AL6061", 1000, 100.0 / i, ""])
    text = "\n".join(",".join(str(x) for x in r) for r in rows).encode("utf-8")
    info = analyze(read_table(text, "a.csv"))
    by = {c["name"]: c for c in info["columns"]}
    assert by["no"]["kind"] == "id" and by["no"]["role"] == "exclude"
    assert by["두께"]["role"] == "input" and by["응력"]["role"] == "output"
    assert by["재질"]["kind"] == "categorical" and by["재질"]["role"] == "exclude" and by["재질"]["values"] == ["AL6061", "SS400"]
    assert by["하중"]["kind"] == "constant" and by["하중"]["role"] == "fixed" and by["하중"]["value"] == 1000
    assert "빈 칸" in info["excluded"] or by.get("빈 칸", {}).get("kind") == "empty"
    # 이름만으로 모르면 마지막 숫자 열을 출력으로 추측하고 알린다
    info = analyze(read_table(b"a,b,c\n1,2,3\n4,5,7\n2,9,1\n", "a.csv"))
    assert info["inputs"] == ["a", "b"] and info["outputs"] == ["c"] and any("추측" in w for w in info["warnings"])
    # 플랫폼의 cases.csv 머리글
    info = analyze(read_table(b"case_id,status,in:t,out:y\n1,complete,1,2\n2,complete,2,5\n3,failed,3,\n", "cases.csv"))
    assert info["inputs"] == ["t"] and info["outputs"] == ["y"]
    # 이상치
    vals = [float(i) for i in range(20)] + [99999.0]
    assert find_outliers(vals) == [20] and find_outliers([1.0, 2.0, 3.0]) == []
    text = "x,y\n" + "\n".join(f"{v},{v}" for v in vals)
    info = analyze(read_table(text.encode(), "a.csv"))
    assert any("떨어져" in w for w in info["columns"][0]["warnings"])


def test_tabular_join():
    a = read_table(b"case,t\n1,1.5\n2,2.5\n3,3.5\n", "in.csv")
    b = read_table(b"case,s\n1,10\n2,20\n9,90\n", "out.csv")
    j, rep = join_tables(a, b)
    assert j.names == ["case", "t", "s"] and rep["key"] == "case" and rep["matched"] == 2
    assert rep["only_left"] == ["3"] and rep["only_right"] == ["9"]
    assert j.records()[1]["s"] == 20 and j.warnings
    with pytest.raises(TableError, match="key"):
        join_tables(read_table(b"p,q\n1,2\n", "a.csv"), read_table(b"r,s\n1,2\n", "b.csv"))
    with pytest.raises(TableError, match="하나도"):
        join_tables(read_table(b"k,q\n1,2\n", "a.csv"), read_table(b"k,s\n7,2\n", "b.csv"), "k")


def test_space_filling_subset_keeps_extremes():
    X = np.random.default_rng(1).uniform(0, 1, (2000, 3))
    keep = space_filling_subset(X, 100)
    assert len(keep) == 100 == len(set(keep.tolist()))
    assert set(X.argmin(axis=0)) <= set(keep.tolist()) and set(X.argmax(axis=0)) <= set(keep.tolist())
    assert (space_filling_subset(X, 100) == keep).all()  # 같은 표면 같은 행
    assert len(space_filling_subset(X, 5000)) == 2000


# ---------- E1: 표 → Simulator (해석 0건, Agent 없이) ----------
def test_e1_csv_to_simulator_without_agent(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    assert registry.get(me["id"]) is None  # PC Agent 없음
    pid = _project(client, admin, "E1")
    csv_bytes = _csv(_rows(24))
    r = client.post(f"/api/projects/{pid}/studies/import/preview", headers=admin, files={"file": ("설계.csv", csv_bytes)})
    assert r.status_code == 200, r.text
    prev = r.json()
    assert prev["n_rows"] == 24 and prev["inputs"] == ["thickness", "width"] and prev["outputs"] == ["stress"]
    assert client.get(f"/api/projects/{pid}/studies", headers=admin).json() == []  # 미리보기는 저장하지 않는다

    r = client.post(f"/api/projects/{pid}/studies/import/file", headers=admin, files={"file": ("설계.csv", csv_bytes)},
                    data={"mapping": json.dumps({"inputs": [{"name": "thickness", "unit": "mm"}, {"name": "width"}], "outputs": [{"name": "stress"}],
                                                 "objectives": [{"name": "stress", "goal": "min"}]})})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["registered"] == 24 and rep["received"] == 24 and rep["excluded_count"] == 0
    sid = rep["study_id"]
    assert rep["study_name"] == "설계"
    v = {x["name"]: x for x in rep["variables"]}
    # 범위는 데이터 최소~최대
    data = _rows(24)
    assert v["thickness"]["lower"] == pytest.approx(min(d["thickness"] for d in data)) and v["thickness"]["upper"] == pytest.approx(max(d["thickness"] for d in data))
    assert v["thickness"]["unit"] == "mm"

    st = client.get(f"/api/studies/{sid}", headers=admin).json()
    assert st["study"]["adapter"] == "data" and st["counts"] == {"complete": 24}
    assert st["data_source"]["kind"] == "table" and st["data_source"]["label"] == "불러온 표" and st["data_source"]["rows"] == 24 and st["data_source"]["source"] == "설계.csv"
    # 해석을 돌리려 하면 이유와 할 일
    r = client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    assert r.status_code == 400 and "해석 방법을 연결" in r.json()["detail"]

    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert m["outputs"] == ["stress"] and m["n_train"] == 24 and m["version"] == 1
    assert m["metrics"]["stress"]["r2"] > 0.7, m["metrics"]
    assert m["quality"]["level"] in ("good", "fair") and m["quality"]["rows_total"] == 24
    assert client.post(f"/api/models/{m['id']}/deploy", headers=admin).json()["deployed"]
    pred = client.post(f"/api/models/{m['id']}/predict", json={"params": {"thickness": 3.0, "width": 25.0}}, headers=admin).json()
    assert pred["mean"]["stress"] == pytest.approx(_f(3.0, 25.0), abs=2.0)
    spec = client.get(f"/api/models/{m['id']}/spec", headers=admin).json()
    assert spec["data_source"]["label"] == "불러온 표" and spec["model"]["quality"]["label"]
    sims = client.get("/api/simulators", headers=admin).json()["mine"]
    item = next(x for x in sims if x["study_id"] == sid)
    assert item["data_source"]["rows"] == 24 and item["quality"] in ("good", "fair")
    arts = [a["title"] for a in client.get(f"/api/projects/{pid}/artifacts", headers=admin).json()]
    assert "[설계] 케이스 결과" in arts and "[설계] 불러온 데이터 확인" in arts


def test_e1_import_file_uses_guess_when_no_mapping(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E1 추측")
    rows = [["no", "두께 (mm)", "폭", "재질", "하중", "응력 (MPa)"]]
    for i, d in enumerate(_rows(12), 1):
        rows.append([i, d["thickness"], d["width"], "SS400" if i % 2 else "AL", 1000, d["stress"]])
    text = "\n".join(",".join(str(x) for x in r) for r in rows).encode("cp949")
    rep = client.post(f"/api/projects/{pid}/studies/import/file", headers=admin, files={"file": ("표.csv", text)}).json()
    assert rep["registered"] == 12 and [v["name"] for v in rep["variables"]] == ["두께", "폭"]
    assert rep["fixed"] == {"하중": 1000.0}  # 상수 열은 변수가 아니라 고정값
    gone = {c["name"]: c for c in rep["removed_columns"]}
    assert "no" in gone and "재질" in gone and gone["재질"]["values"] == ["AL", "SS400"]
    assert any("재질" in w and "나눠" in w for w in rep["warnings"])  # 범주형을 뺀 뜻을 설명한다
    assert rep["outputs"][0]["name"] == "응력" and rep["outputs"][0]["unit"] == "MPa"
    st = client.get(f"/api/studies/{rep['study_id']}", headers=admin).json()
    assert st["study"]["import_info"]["fixed"] == {"하중": 1000.0}


# ---------- 표 붙여넣기: 회사의 CSV·엑셀은 암호화되어 있어 복사·붙여넣기가 기본 입구다 ----------
ENCRYPTED_TEXT = "보안 프로그램으로 암호화되어 읽을 수 없습니다 → 엑셀에서 표를 복사해 붙여 넣으세요"


def _tsv(rows, head="두께 [mm]\t폭 [mm]\t최대 응력 [MPa]"):
    return "\n".join([head] + [f"{r['thickness']}\t{r['width']}\t{r['stress']}" for r in rows])


def test_paste_preview_needs_no_project_and_saves_nothing(client):  # noqa: F811
    """첫 화면은 프로젝트가 없다. 붙여 넣고 닫아도 프로젝트·스터디가 남지 않아야 한다."""
    admin, _ = _auth(client, "admin")
    before = len(client.get("/api/projects", headers=admin).json())
    r = client.post("/api/tables/preview/text", headers=admin, json={"text": _tsv(_rows(6))})
    assert r.status_code == 200, r.text
    prev = r.json()
    assert prev["n_rows"] == 6 and prev["inputs"] == ["두께", "폭"] and prev["outputs"] == ["최대 응력"]
    assert {c["name"]: c["unit"] for c in prev["columns"]} == {"두께": "mm", "폭": "mm", "최대 응력": "MPa"}
    assert any("자릿수" in w for w in prev["warnings"])  # 화면에 보이는 자릿수대로 복사된다
    assert len(client.get("/api/projects", headers=admin).json()) == before
    assert client.post("/api/tables/preview/text", json={"text": "a\tb\n1\t2"}).status_code == 401


@pytest.mark.parametrize("text,hint", [("", "비어"), ("  \n ", "비어"), ("이것은\n표가\n아닙니다", "열이 하나뿐")])
def test_paste_bad_text_says_what_to_do(client, text, hint):  # noqa: F811
    admin, _ = _auth(client, "admin")
    r = client.post("/api/tables/preview/text", headers=admin, json={"text": text})
    assert r.status_code == 400 and hint in r.json()["detail"] and "→" in r.json()["detail"]


def test_paste_size_limit_has_a_plain_message(client, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    monkeypatch.setattr(settings, "paste_max_mb", 0)
    r = client.post("/api/tables/preview/text", headers=admin, json={"text": "a\tb\n1\t2\n"})
    assert r.status_code == 413 and "필요한 행과 열만" in r.json()["detail"]


def test_paste_reads_what_excel_copies():
    from cae_adapters.tabular import read_pasted
    text = ('번호\t두께 [mm]\t\t재질\t하중\t"최대\n응력 [MPa]"\t효율\t 질량 \r\n'
            '1\t 1.5 \t\tSS400\t1000\t"1,234"\t12.5%\t2\r\n'
            '2\t2.5\t\t\t1000\t1,100\t20%\t3\r\n'
            '\t\t\t\t\t\t\t\r\n'
            '3\t3.5\t\t\t1000\t#DIV/0!\t30%\t4\r\n'
            '4\t4.5\t\t\t1000\t900.5\t40%\t5\r\n')
    t = read_pasted(text)
    assert t.names == ["번호", "두께", "재질", "하중", "최대 응력", "효율", "질량"]  # 줄바꿈 머리글, 빈 열, 앞뒤 공백
    by = {c.name: c for c in t.columns}
    assert by["두께"].unit == "mm" and by["최대 응력"].unit == "MPa" and by["효율"].unit == "%"
    recs = t.records()
    assert len(recs) == 4  # 빈 행은 없다
    assert [r["최대 응력"] for r in recs] == [1234.0, 1100.0, "#DIV/0!", 900.5]  # 탭 구분의 천 단위를 소수점으로 읽지 않는다
    assert [r["효율"] for r in recs] == [12.5, 20.0, 30.0, 40.0]
    info = analyze(t)
    stress = next(c for c in info["columns"] if c["name"] == "최대 응력")
    assert stress["kind"] == "numeric" and stress["n_error"] == 1 and any("수식 오류" in w for w in stress["warnings"])
    assert any("표시 형식" in w for w in t.warnings)


def test_paste_mixed_percent_is_not_guessed():
    from cae_adapters.tabular import read_pasted
    t = read_pasted("a\tb\n1\t10%\n2\t0.2\n3\t30%\n4\t0.4\n")
    assert [r["b"] for r in t.records()] == ["10%", 0.2, "30%", 0.4]  # 무엇이 맞는지 모른다. 그 행은 이유와 함께 빠진다


def test_paste_unclosed_quote_does_not_swallow_rows():
    from cae_adapters.tabular import read_pasted
    t = read_pasted('이름\t값\t결과\n5" 파이프\t1\t2\n"6 파이프\t3\t4\n7 파이프\t5\t6\n')
    assert len(t.rows) == 3 and [r["값"] for r in t.records()] == [1.0, 3.0, 5.0]


def test_paste_no_header_gets_names():
    from cae_adapters.tabular import read_pasted
    t = read_pasted("1\t2\t3\n4\t5\t7\n7\t8\t1\n")
    assert t.header_row is None and t.names == ["열1", "열2", "열3"] and len(t.rows) == 3


def test_paste_space_separated_text():
    from cae_adapters.tabular import read_pasted
    t = read_pasted("t w s\n1 2 3\n4 5 7\n")
    assert t.names == ["t", "w", "s"] and t.records()[1]["s"] == 7.0


def test_paste_merged_cells_fill_inputs_but_never_outputs(client):  # noqa: F811
    """병합 셀은 첫 칸에만 값이 복사된다. 입력은 위 값으로 채우고, 빈 출력을 앞 행 값으로 채우면 없는 결과를 지어내는 것이라 두지 않는다."""
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "병합")
    lines = ["두께\t폭\t응력"]
    for i in range(12):
        lines.append(f"{'' if i % 3 else 1 + i / 3}\t{10 + i}\t{'' if i == 7 else 100 + 3 * i}")
    text = "\n".join(lines)
    prev = client.post("/api/tables/preview/text", headers=admin, json={"text": text}).json()
    assert any("병합" in w and "두께" in w for w in prev["warnings"])
    rep = client.post(f"/api/projects/{pid}/studies/import/text", headers=admin, json={"text": text, "outputs": [{"name": "응력"}]}).json()
    assert rep["received"] == 12 and rep["registered"] == 11 and rep["excluded_count"] == 1  # 7번째 행은 출력이 없어 빠진다
    cases = client.get(f"/api/studies/{rep['study_id']}/cases", headers=admin).json()
    assert [c["params"]["두께"] for c in cases[:3]] == [1.0, 1.0, 1.0]
    # 출력에 빈칸이 많아도 채우지 않는다
    gaps = "\n".join(["두께\t응력"] + [f"{i}\t{100 + i if i % 2 == 0 else ''}" for i in range(12)])
    prev = client.post("/api/tables/preview/text", headers=admin, json={"text": gaps}).json()
    assert prev["outputs"] == ["응력"] and not any("병합" in w for w in prev["warnings"])
    assert next(c for c in prev["columns"] if c["name"] == "응력")["n_missing"] == 6


def test_paste_to_simulator_without_agent_only_outputs_given(client):  # noqa: F811
    """묻는 것은 출력뿐이다. 나머지 숫자 열은 입력, 상수는 고정값, 순번·글자 열은 이유와 함께 빠진다."""
    admin, me = _auth(client, "admin")
    assert registry.get(me["id"]) is None
    pid = _project(client, admin, "붙여넣기")
    lines = ["번호\t이름\t두께 [mm]\t폭 [mm]\t하중\t재질\t응력 [MPa]\t질량"]
    for i, d in enumerate(_rows(24), 1):
        lines.append(f"{i}\tC{i}\t{d['thickness']}\t{d['width']}\t1000\t{'A' if i % 2 else 'B'}\t{d['stress']}\t{d['thickness'] * d['width']}")
    text = "\n".join(lines)
    # 질량을 입력으로 쓰겠다는 뜻: 출력만 고르면 나머지 숫자 열이 입력이다
    rep = client.post(f"/api/projects/{pid}/studies/import/text", headers=admin, json={"text": text, "name": "응력 예측", "outputs": [{"name": "응력"}]}).json()
    assert rep["study_name"] == "응력 예측" and rep["registered"] == 24 and rep["excluded_count"] == 0
    assert [v["name"] for v in rep["variables"]] == ["두께", "폭", "질량"] and [o["name"] for o in rep["outputs"]] == ["응력"]
    assert rep["fixed"] == {"하중": 1000.0}
    gone = {c["name"]: c["reason"] for c in rep["removed_columns"]}
    assert "번호" in gone and "행마다" in gone["이름"] and "재질" in gone
    assert not any("이름" in w and "나눠" in w for w in rep["warnings"])  # 케이스 이름에 '값별로 나누라'고 하지 않는다
    assert any("자릿수" in w for w in rep["warnings"])
    # 같은 표를 다시 붙여 넣어도 겹치지 않는다
    again = client.post(f"/api/projects/{pid}/studies/import/text", headers=admin, json={"text": text, "outputs": [{"name": "응력"}]}).json()
    assert again["reused"] and again["study_id"] == rep["study_id"]
    sid = rep["study_id"]
    st = client.get(f"/api/studies/{sid}", headers=admin).json()
    assert st["study"]["adapter"] == "data" and st["data_source"]["label"] == "불러온 표" and st["data_source"]["rows"] == 24
    job = client.post(f"/api/studies/{sid}/train/start", headers=admin, json={}).json()
    for _ in range(600):
        if job["status"] != "running":
            break
        time.sleep(0.1)
        job = client.get(f"/api/train-jobs/{job['job_id']}", headers=admin).json()
    assert job["status"] == "done" and job["model"]["outputs"] == ["응력"], job
    assert client.post(f"/api/models/{job['model']['id']}/deploy", headers=admin).json()["deployed"]
    # 표에 없는 출력을 고르면 있는 열을 알려 준다
    r = client.post(f"/api/projects/{pid}/studies/import/text", headers=admin, json={"text": text, "outputs": [{"name": "없는열"}]})
    assert r.status_code == 400 and "두께" in r.json()["detail"]


# ---------- 암호화된 파일 ----------
def _random_bytes(n=4096):
    return bytes(np.random.default_rng(1).integers(0, 256, n, dtype=np.uint8))


@pytest.mark.parametrize("name,data", [
    ("result.xlsx", b"not a zip at all, plain words"),  # 엑셀인데 zip이 아니다
    ("result.xlsx", _random_bytes()),
    ("result.xlsm", b"PK\x03\x04broken"),
    ("result.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest"),
    ("result.csv", _random_bytes()),  # 글자가 아닌 바이트
    ("result.csv", b"<## DRM HEADER ##>\n" + _random_bytes()),  # 앞머리만 글자
    ("result.csv", b"A" * 4096),  # 구분자 없는 긴 줄(글자로 된 암호문)
    ("result.csv", b"\x01\x02\x03\xfe\xfdbinary" * 50),  # 그림 앞머리는 암호문이 아니라 '표가 아닙니다'다(test_table_job.py)
])
def test_encrypted_files_are_not_read_as_a_broken_table(name, data):
    with pytest.raises(TableError, match=ENCRYPTED_TEXT):
        read_table(data, name)


def test_encrypted_file_api_says_paste_not_500(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "암호화")
    for route in ("preview", "file"):
        r = client.post(f"/api/projects/{pid}/studies/import/{route}", headers=admin, files={"file": ("a.xlsx", _random_bytes())})
        assert r.status_code == 400 and ENCRYPTED_TEXT in r.json()["detail"]
    assert client.get(f"/api/projects/{pid}/studies", headers=admin).json() == []


def test_plain_text_files_still_read_after_encryption_checks():
    assert read_table("이름,값\n가,1\n나,2\n".encode("cp949"), "a.csv").names == ["이름", "값"]
    assert read_table(b"1\n2\n3\n", "a.csv").names == ["열1"]  # 짧은 한 열은 암호문으로 보지 않는다
    assert read_table(("a,b\n" + "1,2\n" * 5000).encode(), "a.csv").records()[0]["a"] == 1.0


def test_e1b_xlsx_with_title_units_korean(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E1b")
    rows = [["설계 검토 결과"], ["두께 [mm]", "폭 [mm]", "응력 (MPa)"]] + [[d["thickness"], d["width"], d["stress"]] for d in _rows(10)]
    data = _xlsx({"Sheet1": rows})
    prev = client.post(f"/api/projects/{pid}/studies/import/preview", headers=admin, files={"file": ("a.xlsx", data)}).json()
    assert prev["header_row"] == 2 and prev["skipped_rows"] == 1 and [c["unit"] for c in prev["columns"]] == ["mm", "mm", "MPa"]
    assert any("제목" in w for w in prev["warnings"])
    rep = client.post(f"/api/projects/{pid}/studies/import/file", headers=admin, files={"file": ("a.xlsx", data)},
                      data={"mapping": json.dumps({"inputs": [{"name": "두께"}, {"name": "폭"}], "outputs": [{"name": "응력"}]})}).json()
    assert rep["registered"] == 10 and rep["variables"][0]["unit"] == "mm"
    # 표에 없는 열을 지정하면 있는 열을 알려 준다
    r = client.post(f"/api/projects/{pid}/studies/import/file", headers=admin, files={"file": ("a.xlsx", data)},
                    data={"mapping": json.dumps({"inputs": [{"name": "없는열"}], "outputs": [{"name": "응력"}]})})
    assert r.status_code == 400 and "두께" in r.json()["detail"]
    r = client.post(f"/api/projects/{pid}/studies/import/file", headers=admin, files={"file": ("a.xlsx", data)}, data={"mapping": "{깨진"})
    assert r.status_code == 400 and "mapping" in r.json()["detail"]


def test_e2_folders_register_and_never_rerun(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E2")
    rows = [{**d, "_run_dir": f"D:/results/t{i}", "_label": f"t{i}", "_row": 100 + i} for i, d in enumerate(_rows(8))]
    rep = _import(client, admin, pid, rows=rows, kind="folders", source="D:/results").json()
    assert rep["registered"] == 8 and {c["row"] for c in rep["cases"]} == {100 + i for i in range(8)}
    sid = rep["study_id"]
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert cases[0]["run_dir"] == "D:/results/t0" and cases[0]["source"] == "import" and cases[0]["origin"]["label"] == "t0"
    assert cases[0]["run_by"] is not None
    st = client.get(f"/api/studies/{sid}", headers=admin).json()
    assert st["data_source"]["label"] == "불러온 해석 결과"
    assert client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()["n_train"] == 8
    # 나눠 받기
    assert len(client.get(f"/api/studies/{sid}/cases?limit=3&offset=2", headers=admin).json()) == 3


def test_e2b_inputs_and_outputs_in_two_files(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E2b")
    data = _rows(8)
    left = "case,thickness,width\n" + "\n".join(f"{i},{d['thickness']},{d['width']}" for i, d in enumerate(data))
    right = "case,stress\n" + "\n".join(f"{i},{d['stress']}" for i, d in enumerate(data[:6]))
    files = {"file": ("in.csv", left.encode()), "outputs_file": ("out.csv", right.encode())}
    prev = client.post(f"/api/projects/{pid}/studies/import/preview", headers=admin, files=files, data={"key": "case"}).json()
    assert prev["join"]["matched"] == 6 and prev["join"]["only_left"] == ["6", "7"]
    rep = client.post(f"/api/projects/{pid}/studies/import/file", headers=admin, files=files,
                      data={"mapping": json.dumps({"key": "case", "inputs": [{"name": "thickness"}, {"name": "width"}], "outputs": [{"name": "stress"}]})}).json()
    assert rep["registered"] == 6 and rep["join"]["key"] == "case" and any("한쪽에만" in w for w in rep["warnings"])


def test_e3_add_rows_new_version_share_follows_and_range_widens(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    other, _ = _auth(client, "viewer_e3", admin)
    pid = _project(client, admin, "E3")
    rep = _import(client, admin, pid, rows=_rows(12)).json()
    sid = rep["study_id"]
    v1 = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    client.post(f"/api/models/{v1['id']}/deploy", headers=admin)
    token = client.post(f"/api/models/{v1['id']}/shares", json={}, headers=admin).json()["token"]

    new = [{"thickness": 9.0, "width": 60.0, "stress": _f(9, 60)}, {"thickness": 2.2, "width": 22.0, "stress": _f(2.2, 22)}, {"thickness": 3.1, "width": 13.0, "stress": _f(3.1, 13)}]
    r = client.post(f"/api/studies/{sid}/cases/import", json={"rows": new, "source": "추가.csv"}, headers=admin)
    assert r.status_code == 200, r.text
    add = r.json()
    assert add["registered"] == 3
    changes = {c["name"]: c for c in add["range_changes"]}
    assert changes["thickness"]["upper"] == 9.0 and changes["width"]["upper"] == 60.0 and changes["thickness"]["old_upper"] < 5
    st = client.get(f"/api/studies/{sid}", headers=admin).json()
    assert st["study"]["variables"][0]["upper"] == 9.0 and st["counts"]["complete"] == 15

    v2 = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert v2["version"] == 2 and v2["n_train"] == 15
    client.post(f"/api/models/{v2['id']}/deploy", headers=admin)
    assert client.get(f"/api/shares/{token}", headers=other).json()["model"]["id"] == v2["id"]  # 링크를 다시 보내지 않는다
    assert client.get(f"/api/shares/{token}", headers=other).json()["data_source"]["rows"] == 15
    assert "source" not in client.get(f"/api/shares/{token}", headers=other).json()["data_source"]  # 파일 이름은 안 보인다


def test_e3b_same_table_twice_no_duplicates(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E3b")
    rows = _rows(10)
    first = _import(client, admin, pid, rows=rows).json()
    again = _import(client, admin, pid, rows=rows).json()
    assert again["study_id"] == first["study_id"] and again["reused"] and again["registered"] == 0 and len(again["cases"]) == 10
    assert len(client.get(f"/api/projects/{pid}/studies", headers=admin).json()) == 1
    sid = first["study_id"]
    add = client.post(f"/api/studies/{sid}/cases/import", json={"rows": rows}, headers=admin).json()
    assert add["registered"] == 0 and add["duplicates"] == 10
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["counts"]["complete"] == 10
    # 표 안의 중복 행은 이유와 함께 뺀다
    second = _import(client, admin, pid, rows=_rows(6) + _rows(6), name="중복표").json()
    assert second["registered"] == 6 and second["excluded_by_reason"] == {"중복": 6}
    assert "같은 행" in second["excluded"][0]["reason"]
    # 자릿수만 다른 같은 값
    third = client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"thickness": rows[0]["thickness"] + 1e-14, "width": rows[0]["width"], "stress": rows[0]["stress"]}]}, headers=admin).json()
    assert third["registered"] == 0
    # 입력이 같고 새 출력 열만 있으면 그 행에 합친다
    more = [{"thickness": d["thickness"], "width": d["width"], "mass": d["thickness"] * d["width"]} for d in rows]
    rep = client.post(f"/api/studies/{sid}/cases/import", json={"rows": more, "outputs": [{"name": "mass", "unit": "kg"}]}, headers=admin).json()
    assert rep["updated"] == 10 and rep["registered"] == 0
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert len(cases) == 10 and {"stress", "mass"} <= set(cases[0]["results"])
    # 입력은 같은데 값이 다른 행은 잡음으로 보고 넣되 알린다
    conflict = client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"thickness": rows[0]["thickness"], "width": rows[0]["width"], "stress": 999.0}]}, headers=admin).json()
    assert conflict["registered"] == 1 and any("입력이 같은데" in w for w in conflict["warnings"])


def test_e4_optimize_data_study_gives_unverified_candidates(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E4")
    rep = _import(client, admin, pid, rows=_rows(20), objectives=[{"name": "stress", "goal": "min"}]).json()
    sid = rep["study_id"]
    before = client.get(f"/api/studies/{sid}", headers=admin).json()["counts"]
    # 모델이 없으면 할 일
    r = client.post(f"/api/studies/{sid}/candidates", json={}, headers=admin)
    assert r.status_code == 400 and "학습" in r.json()["detail"]
    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    r = client.post(f"/api/studies/{sid}/candidates", json={"n": 3}, headers=admin)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["verified"] is False and "해석으로 확인되지 않은" in out["note"]
    assert len(out["candidates"]) == 3 and all(c["verified"] is False for c in out["candidates"])
    assert out["best_known"]["results"]["stress"] > 0 and out["objective"] == "stress" and out["goal"] == "min"
    first = out["candidates"][0]
    assert first["predicted"]["stress"] <= out["best_known"]["results"]["stress"] + 5
    v = {x["name"]: x for x in rep["variables"]}
    assert v["thickness"]["lower"] <= first["params"]["thickness"] <= v["thickness"]["upper"]
    # 케이스가 늘지 않는다
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["counts"] == before
    # suggest도 같은 뜻
    sug = client.post(f"/api/studies/{sid}/suggest", json={"n": 2}, headers=admin).json()
    assert sug["verified"] is False and len(sug["candidates"]) == 2
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["counts"] == before
    # 능동학습 뜻의 후보
    al = client.post(f"/api/studies/{sid}/candidates", json={"mode": "al", "n": 2}, headers=admin).json()
    assert "불확실" in al["note"] and al["candidates"]
    # 제약
    con = client.post(f"/api/studies/{sid}/candidates", json={"n": 1, "goal": "max", "constraints": [{"name": "stress", "upper": 30}]}, headers=admin).json()
    assert con["goal"] == "max" and con["candidates"][0]["feasible"] and con["candidates"][0]["predicted"]["stress"] <= 30
    impossible = client.post(f"/api/studies/{sid}/candidates", json={"n": 1, "constraints": [{"name": "stress", "upper": -100}]}, headers=admin).json()
    assert not impossible["candidates"][0]["feasible"] and "만족한다고 예측되는 점이 없어" in impossible["note"]
    ignored = client.post(f"/api/studies/{sid}/candidates", json={"n": 1, "constraints": [{"name": "없음", "upper": 1}]}, headers=admin).json()
    assert "없음" in ignored["note"]
    # 목적을 몰라도 알려 준다
    r2 = _import(client, admin, pid, rows=_rows(8, seed=9), name="목적 없음").json()
    client.post(f"/api/studies/{r2['study_id']}/train", json={}, headers=admin)
    r = client.post(f"/api/studies/{r2['study_id']}/candidates", json={}, headers=admin)
    assert r.status_code == 400 and "objective" in r.json()["detail"]
    r = client.post(f"/api/studies/{r2['study_id']}/candidates", json={"objective": "없는출력"}, headers=admin)
    assert r.status_code == 400 and "stress" in r.json()["detail"]
    # 줄일지 키울지 모르면 지어내지 않고 묻는다
    r = client.post(f"/api/studies/{r2['study_id']}/candidates", json={"objective": "stress"}, headers=admin)
    assert r.status_code == 400 and "줄일까요, 키울까요" in r.json()["detail"]
    assert client.post(f"/api/studies/{r2['study_id']}/candidates", json={"objective": "stress", "goal": "min"}, headers=admin).status_code == 200


@pytest.mark.parametrize("kw,hint", [
    ({"rows": []}, "데이터 행이 없"),
    ({"inputs": []}, "입력 열"),
    ({"outputs": []}, "출력 열"),
    ({"rows": [{"thickness": 1, "width": 1, "stress": 2}, {"thickness": 2, "width": 2, "stress": 3}]}, "3행 이상"),
    ({"inputs": [{"name": "thickness"}, {"name": "thickness"}]}, "두 번"),
    ({"inputs": [{"name": "thickness"}], "outputs": [{"name": "thickness"}]}, "두 번"),
    ({"inputs": [{"name": " "}]}, "이름"),
    ({"inputs": [{"name": "thickness", "lower": 5, "upper": 1}]}, "하한"),
    ({"objectives": [{"name": "없음"}]}, "출력 열"),
    ({"constraints": [{"name": "없음", "upper": 1}]}, "출력 열"),
    # 입력·출력 모두 값이 변하지 않는 표
    ({"rows": [{"thickness": 1, "width": 2, "stress": 3}, {"thickness": 1, "width": 2, "stress": 4}, {"thickness": 1, "width": 2, "stress": 5}]}, "입력으로 쓸 수 있는 열이 없습니다"),
    ({"rows": [{"thickness": i, "width": i * 2, "stress": 3} for i in range(1, 5)]}, "출력 열이 없습니다"),
    ({"rows": [{"thickness": "a", "width": "b", "stress": 1}, {"thickness": "c", "width": "d", "stress": 2}, {"thickness": "e", "width": "f", "stress": 3}]}, "입력으로 쓸 수"),
    ({"rows": [{"thickness": 1, "width": 2, "stress": float("nan")}, {"thickness": 2, "width": 3, "stress": None}, {"thickness": 3, "width": 4, "stress": "오류"}]}, "출력 열이 없습니다|쓸 수 있는 행"),
])
def test_e5_bad_tables_are_rejected_with_reason(client, kw, hint):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E5")
    r = _import(client, admin, pid, **kw)
    assert r.status_code == 400, r.text
    assert __import__("re").search(hint, r.json()["detail"]), r.json()["detail"]
    assert "→" in r.json()["detail"]  # 할 일이 함께 있다


def test_e5_bad_rows_are_excluded_with_reason_and_nothing_stored_as_nan(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E5 행")
    rows = _rows(12)
    rows[0]["thickness"] = float("nan")
    rows[1]["thickness"] = "두꺼움"
    rows[2]["width"] = None
    rows[3]["width"] = float("inf")
    rows[4]["stress"] = float("nan")
    rows[5]["stress"] = "ERROR"
    rows[6]["stress"] = "1e999"
    rows[7] = {"width": rows[7]["width"], "stress": 1.0}
    r = client.post(f"/api/projects/{pid}/studies/import", headers={**admin, "content-type": "application/json"},
                    content=json.dumps({"name": "나쁜", "source": "x", "inputs": [{"name": "thickness"}, {"name": "width"}], "outputs": [{"name": "stress"}], "rows": rows}))
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["received"] == 12 and rep["registered"] == 4 and rep["excluded_count"] == 8
    assert rep["excluded_by_reason"]["결측"] >= 3 and rep["excluded_by_reason"]["숫자 아님"] >= 3
    why = {e["row"]: e["reason"] for e in rep["excluded"]}
    assert "두꺼움" in why[2] and "비어" in why[3] and "무한대" in why[4] and "ERROR" in why[6]
    _no_nan(rep)
    sid = rep["study_id"]
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin)
    assert cases.status_code == 200
    _no_nan(cases.json())
    assert client.get(f"/api/projects/{pid}/artifacts", headers=admin).status_code == 200
    r = client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    assert r.status_code == 200 and r.json()["n_train"] == 4
    # JSON NaN도 500이 아니다
    r = client.post(f"/api/projects/{pid}/studies/import", headers={**admin, "content-type": "application/json"},
                    content='{"inputs":[{"name":"a","lower":NaN}],"outputs":[{"name":"b"}],"rows":[{"a":1,"b":2}]}')
    assert r.status_code == 400 or r.status_code == 422
    r = _import(client, admin, pid, rows=_rows(5), name="고정값", fixed={"x": float("nan"), "y": 1})
    assert r.status_code == 200 and r.json()["fixed"] == {"y": 1}
    r = client.post(f"/api/models/{client.post(f'/api/studies/{sid}/train', json={}, headers=admin).json()['id']}/predict",
                    headers={**admin, "content-type": "application/json"}, content='{"params":{"thickness":NaN,"width":1}}')
    assert r.status_code == 422


def test_e5_partial_outputs_outliers_and_non_tables(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E5 일부")
    rows = [{"t": float(i), "w": float(i % 5), "a": i * 2.0 + 1, "b": i * 3.0} for i in range(1, 25)]
    rows[3]["b"] = None
    rows[9]["t"] = 99999.0  # 이상치
    body = {"name": "일부", "source": "x", "inputs": [{"name": "t"}, {"name": "w"}], "outputs": [{"name": "a"}, {"name": "b"}], "rows": rows}
    rep = client.post(f"/api/projects/{pid}/studies/import", json=body, headers=admin).json()
    assert rep["registered"] == 24 and rep["partial_count"] == 1 and rep["partial"][0]["row"] == 4 and "b" in rep["partial"][0]["reason"]
    assert any("떨어져" in w and "99999" in w and "슬라이더" in w for w in rep["warnings"])  # 이상치는 빼지 않고 경고
    assert any("빈 행" in w for w in rep["warnings"])
    # 학습에서 빠진 행은 이유와 함께
    m = client.post(f"/api/studies/{rep['study_id']}/train", json={"outputs": ["a", "b"]}, headers=admin).json()
    q = m["quality"]
    # 빈 칸이 있는 행은 그 출력에서만 빠진다. 다른 출력의 학습 행이 줄면 안 된다
    assert m["n_train"] == 24 and q["rows_by_output"] == {"a": 24, "b": 23}
    assert q["excluded_count"] == 1 and "출력 'b' 값이 없습니다" in q["excluded"][0]["reason"]
    assert any("뺐습니다" in w for w in q["warnings"])
    # 지정이 없으면 값이 다 있는 출력만 쓰고, 뺀 출력을 알린다
    m2 = client.post(f"/api/studies/{rep['study_id']}/train", json={}, headers=admin).json()
    assert set(m2["outputs"]) >= {"a"}
    # 표가 아닌 파일·빈 파일은 400
    for data in (b"", b"\x00\x01\x02binary\x00", b"PK\x03\x04bad"):
        r = client.post(f"/api/projects/{pid}/studies/import/preview", headers=admin, files={"file": ("x.csv", data)})
        assert r.status_code == 400 and "→" in r.json()["detail"], r.text
    assert client.post(f"/api/projects/{pid}/studies/import/file", headers=admin, files={"file": ("x.csv", b"only_header\n")}).status_code == 400


def test_upload_size_limit(client, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "크기")
    monkeypatch.setattr(settings, "table_max_mb", 0)
    r = client.post(f"/api/projects/{pid}/studies/import/preview", headers=admin, files={"file": ("x.csv", b"a,b\n1,2\n")})
    assert r.status_code == 413 and "MB" in r.json()["detail"]


# ---------- 학습 ----------
def test_training_samples_when_many_rows(client, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "많은 행")
    monkeypatch.setattr(settings, "train_max_rows", 30)
    rep = _import(client, admin, pid, rows=_rows(80)).json()
    m = client.post(f"/api/studies/{rep['study_id']}/train", json={}, headers=admin).json()
    assert m["n_train"] == 30
    q = m["quality"]
    assert q["sampled"] == {"from": 80, "to": 30} and q["rows_total"] == 80 and any("고르게" in w for w in q["warnings"])
    arts = {a["title"]: a for a in client.get(f"/api/projects/{pid}/artifacts", headers=admin).json()}
    assert "고르게" in arts["[표] 학습 데이터 안내"]["payload"]["text"]


def test_big_table_card_is_truncated(client, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "큰 카드")
    monkeypatch.setattr(service, "CASES_TABLE_ROWS", 5)
    _import(client, admin, pid, rows=_rows(8)).json()
    table = next(a for a in client.get(f"/api/projects/{pid}/artifacts", headers=admin).json() if a["title"] == "[표] 케이스 결과")
    assert len(table["payload"]["rows"]) == 5 and table["payload"]["truncated"] == {"shown": 5, "total": 8}


def test_quality_good_fair_poor_and_share_warning(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "품질")
    rng = np.random.default_rng(0)
    noise = [{"x": float(a), "z": float(b), "y": float(c)} for a, b, c in zip(rng.uniform(0, 1, 15), rng.uniform(0, 1, 15), rng.normal(0, 1, 15))]
    body = {"name": "잡음", "source": "x", "inputs": [{"name": "x"}, {"name": "z"}], "outputs": [{"name": "y"}], "rows": noise}
    sid = client.post(f"/api/projects/{pid}/studies/import", json=body, headers=admin).json()["study_id"]
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert m["quality"]["level"] == "poor" and m["quality"]["label"] == "낮음" and m["quality"]["by_output"]["y"]["level"] == "poor"
    assert any("낮습니다" in w and "표에 행을 더하" in w for w in m["quality"]["warnings"])  # 솔버가 없으니 데이터 추가를 권한다
    # 배포는 막지 않지만 공유 때 경고
    assert client.post(f"/api/models/{m['id']}/deploy", headers=admin).status_code == 200
    share = client.post(f"/api/models/{m['id']}/shares", json={}, headers=admin).json()
    assert "낮습니다" in share["warning"]
    assert client.get(f"/api/shares/{share['token']}", headers=admin).json()["model"]["quality"]["level"] == "poor"
    # 좋은 데이터: 경고 없는 공유. 부드러운 함수, 충분한 표본
    smooth = [{"x": float(a), "y": float(2 * a + 1)} for a in np.linspace(0, 1, 12)]
    body = {"name": "좋음", "source": "x", "inputs": [{"name": "x"}], "outputs": [{"name": "y"}], "rows": smooth}
    sid = client.post(f"/api/projects/{pid}/studies/import", json=body, headers=admin).json()["study_id"]
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert m["quality"]["level"] == "good", m["quality"]
    client.post(f"/api/models/{m['id']}/deploy", headers=admin)
    assert client.post(f"/api/models/{m['id']}/shares", json={}, headers=admin).json()["warning"] is None
    # 표본이 적으면 R²가 높아도 낮춰 말한다
    few = service.assess_quality({"y": {"r2": 0.99}}, 6, 3, "data", [])
    assert few["level"] == "fair" and any("적어" in w for w in few["warnings"])
    assert service.assess_quality({"y": {"r2": 0.99}}, 4, 1, "data", [])["level"] == "poor"
    assert "능동학습" in service.assess_quality({"y": {"r2": 0.1}}, 50, 2, "script", [])["warnings"][0]
    assert any("같아" in w for w in service.assess_quality({"y": {"r2": 0.0}}, 50, 2, "data", ["y"])["warnings"])


def test_train_errors_are_plain_and_never_500(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "오류")
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={"name": "빈", "variables": [{"name": "x", "lower": 0, "upper": 1}]}).json()["id"]
    r = client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    assert r.status_code == 400 and "→" in r.json()["detail"]
    r = client.post(f"/api/studies/{sid}/train", json={"outputs": ["y"]}, headers=admin)
    assert r.status_code == 400 and "3개 이상" in r.json()["detail"]
    # 예전 데이터처럼 값이 깨진 케이스가 있어도 500이 아니라 이유와 함께 뺀다
    client.post(f"/api/studies/{sid}/doe", json={"n": 5, "seed": 1}, headers=admin)
    ids = [c["id"] for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json()]
    for i, cid in enumerate(ids):
        service.on_case_status({"case_id": cid, "status": "complete", "results": {"y": float(i) if i else float("nan"), "s": "글자"}})
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert "y" not in cases[0]["results"] and "s" not in cases[1]["results"]  # 서버 입구에서도 걸러진다
    m = client.post(f"/api/studies/{sid}/train", json={"outputs": ["y"]}, headers=admin).json()
    assert m["n_train"] == 4 and "값이 없습니다" in m["quality"]["excluded"][0]["reason"]


def test_retrain_keeps_outputs_so_constraints_survive(client):  # noqa: F811
    """optimize가 outputs 없이 재학습해 제약 출력을 잃던 문제: 다음 제안에서 제약이 빠지고 Simulator에서 출력이 사라졌다."""
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "재학습 출력")
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "빔", "adapter": "mock", "adapter_config": {"function": "beam3d"},
        "variables": [{"name": "width", "lower": 10, "upper": 40}, {"name": "height", "lower": 20, "upper": 80}],
        "objectives": [{"name": "mass", "goal": "min"}], "constraints": [{"name": "max_stress", "upper": 250}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": 6, "seed": 2}, headers=admin)
    client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    _wait_idle(client, sid, admin)
    m1 = client.post(f"/api/studies/{sid}/train", json={"outputs": ["mass", "max_stress", "tip_deflection"]}, headers=admin)
    if m1.status_code != 200:  # 함수가 내는 출력 이름에 맞춘다
        have = list(client.get(f"/api/studies/{sid}/cases", headers=admin).json()[0]["results"])
        m1 = client.post(f"/api/studies/{sid}/train", json={"outputs": have}, headers=admin)
    outs = m1.json()["outputs"]
    assert len(outs) >= 2
    m2 = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert set(outs) <= set(m2["outputs"])  # 빠지지 않는다
    # 첫 학습에 지정이 없어도 값이 있는 제약 출력은 함께 학습한다
    sid2 = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "빔2", "adapter": "mock", "adapter_config": {"function": "beam3d"},
        "variables": [{"name": "width", "lower": 10, "upper": 40}, {"name": "height", "lower": 20, "upper": 80}],
        "objectives": [{"name": "mass", "goal": "min"}], "constraints": [{"name": "max_stress", "upper": 250}]}).json()["id"]
    client.post(f"/api/studies/{sid2}/doe", json={"n": 6, "seed": 2}, headers=admin)
    client.post(f"/api/studies/{sid2}/run", json={}, headers=admin)
    _wait_idle(client, sid2, admin)
    assert client.post(f"/api/studies/{sid2}/train", json={}, headers=admin).json()["outputs"] == ["mass", "max_stress"]


# ---------- 백그라운드 학습 ----------
def _wait_job(client, job, h, timeout=60):  # noqa: F811
    t0 = time.time()
    while job["status"] == "running" and time.time() - t0 < timeout:
        time.sleep(0.05)
        job = client.get(f"/api/train-jobs/{job['job_id']}", headers=h).json()
    return job


def test_train_start_polls_and_retry_does_not_duplicate(client, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "백그라운드")
    sid = _import(client, admin, pid, rows=_rows(10)).json()["study_id"]
    # 학습 중에 다시 보내도 같은 작업이다
    gate = threading.Event()
    real = service.Surrogate.fit

    def slow(self, X, Y):
        gate.wait(30)
        return real(self, X, Y)
    monkeypatch.setattr(service.Surrogate, "fit", slow)
    first = client.post(f"/api/studies/{sid}/train/start", json={}, headers=admin).json()
    assert first["status"] == "running" and first["rows"] == 10 and first["outputs"] == ["stress"] and "멈춘 것이 아닙니다" in first["message"]
    again = client.post(f"/api/studies/{sid}/train/start", json={}, headers=admin).json()
    assert again["job_id"] == first["job_id"]
    gate.set()
    done = _wait_job(client, first, admin)
    assert done["status"] == "done" and done["model"]["version"] == 1 and done["model"]["quality"]["level"] and done["elapsed_sec"] >= 0
    # 끝난 뒤 재시도: 같은 데이터면 새 버전을 만들지 않는다
    retry = client.post(f"/api/studies/{sid}/train/start", json={}, headers=admin).json()
    assert retry["status"] == "done" and retry["reused"] and retry["model"]["id"] == done["model"]["id"] and "force" in retry["message"]
    # 일부러 다시
    forced = _wait_job(client, client.post(f"/api/studies/{sid}/train/start", json={"force": True}, headers=admin).json(), admin)
    assert forced["model"]["version"] == 2 and not forced["reused"]
    # 데이터가 바뀌면 새 버전
    client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"thickness": 2.2, "width": 22.2, "stress": 30.0}]}, headers=admin)
    third = _wait_job(client, client.post(f"/api/studies/{sid}/train/start", json={}, headers=admin).json(), admin)
    assert third["model"]["version"] == 3 and third["rows"] == 11
    assert client.get("/api/train-jobs/없는작업", headers=admin).status_code == 404
    # 다른 사람은 상태를 못 본다
    other, _ = _auth(client, "train_other", admin)
    assert client.get(f"/api/train-jobs/{third['job_id']}", headers=other).status_code == 403


def test_train_start_failures(client, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "학습 실패")
    sid = _import(client, admin, pid, rows=_rows(6)).json()["study_id"]
    r = client.post(f"/api/studies/{sid}/train/start", json={"outputs": ["없음"]}, headers=admin)
    assert r.status_code == 400 and "3개 이상" in r.json()["detail"]

    def boom(self, X, Y):
        raise np.linalg.LinAlgError("singular")
    monkeypatch.setattr(service.Surrogate, "fit", boom)
    job = _wait_job(client, client.post(f"/api/studies/{sid}/train/start", json={}, headers=admin).json(), admin)
    assert job["status"] == "failed" and "→" in job["error"] and job["model"] is None
    r = client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    assert r.status_code == 400 and "학습하지 못했습니다" in r.json()["detail"]

    def crash(self, X, Y):
        raise RuntimeError("뜻밖의 오류")
    monkeypatch.setattr(service.Surrogate, "fit", crash)
    job = _wait_job(client, client.post(f"/api/studies/{sid}/train/start", json={}, headers=admin).json(), admin)
    assert job["status"] == "failed" and "다시 시도" in job["error"]


@pytest.fixture
def mcp(client, monkeypatch):  # noqa: F811
    """dds_study MCP 함수를 프로세스 안 서버로 직접 부른다."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp_servers"))
    import dds_study
    admin, _ = _auth(client, "admin")
    tok = client.post("/api/tokens", json={"name": "mcp"}, headers=admin).json()["token"]

    class _Client:
        def get(self, path, params=None):
            return client.get(path, params=params, headers={"Authorization": f"Bearer {tok}"})

        def post(self, path, json=None):  # noqa: A002
            return client.post(path, json=json, headers={"Authorization": f"Bearer {tok}"})
    monkeypatch.setattr(dds_study.api, "client", _Client())
    real_sleep = time.sleep
    monkeypatch.setattr(dds_study.time, "sleep", lambda s: real_sleep(min(s, 0.05)))
    return dds_study, admin


def test_mcp_train_surrogate_survives_tool_limit(client, mcp, monkeypatch):  # noqa: F811
    dds_study, admin = mcp
    pid = _project(client, admin, "MCP 학습")
    sid = _import(client, admin, pid, rows=_rows(10)).json()["study_id"]
    gate = threading.Event()
    real = service.Surrogate.fit

    def slow(self, X, Y):
        gate.wait(30)
        return real(self, X, Y)
    monkeypatch.setattr(service.Surrogate, "fit", slow)
    monkeypatch.setenv("DDS_MCP_WAIT_LIMIT_SEC", "1")
    first = dds_study.train_surrogate(sid)
    assert first["status"] == "running" and "실패가 아닙니다" in first["note"]
    assert dds_study.train_surrogate(sid)["job_id"] == first["job_id"]  # 다시 불러도 같은 작업
    gate.set()
    monkeypatch.delenv("DDS_MCP_WAIT_LIMIT_SEC")
    done = dds_study.train_surrogate(sid)
    assert done["status"] == "done" and done["version"] == 1 and done["quality"] in ("good", "fair", "poor") and "warnings" in done
    assert len(client.get(f"/api/projects/{pid}/models", headers=admin).json()) == 1  # 버전이 겹치지 않는다
    assert dds_study.train_surrogate(sid)["reused"]
    with pytest.raises(dds_study.ServerError):
        dds_study.train_surrogate(sid, outputs=["없음"])


def test_mcp_optimize_runs_only_suggested_points_and_keeps_outputs(client, mcp):  # noqa: F811
    """/run {}가 일부러 남겨 둔 대기 DOE까지 전부 돌리던 문제와, 재학습에서 출력을 잃던 문제."""
    dds_study, admin = mcp
    pid = _project(client, admin, "MCP 최적화")
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "빔", "adapter": "mock", "adapter_config": {"function": "beam3d"},
        "variables": [{"name": "width", "lower": 10, "upper": 40}, {"name": "height", "lower": 20, "upper": 80}],
        "objectives": [{"name": "mass", "goal": "min"}], "constraints": [{"name": "max_stress", "upper": 250}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": 5, "seed": 2}, headers=admin)
    client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    _wait_idle(client, sid, admin)
    client.post(f"/api/studies/{sid}/train", json={"outputs": ["mass", "max_stress"]}, headers=admin)
    client.post(f"/api/studies/{sid}/doe", json={"n": 4, "seed": 7}, headers=admin)  # 일부러 남겨 둔 대기 점
    out = dds_study.optimize(sid, iterations=2, wait_sec=30)
    assert len(out["history"]) == 2
    st = client.get(f"/api/studies/{sid}", headers=admin).json()
    assert st["counts"]["pending"] == 4 and st["counts"]["complete"] == 7
    latest = client.get(f"/api/projects/{pid}/models", headers=admin).json()[0]
    assert set(latest["outputs"]) >= {"mass", "max_stress"}


def test_mcp_optimize_data_study_gives_candidates(client, mcp):  # noqa: F811
    dds_study, admin = mcp
    pid = _project(client, admin, "MCP 표 최적화")
    sid = _import(client, admin, pid, rows=_rows(12), objectives=[{"name": "stress", "goal": "min"}]).json()["study_id"]
    assert dds_study.train_surrogate(sid)["status"] == "done"
    out = dds_study.optimize(sid)
    assert out["verified"] is False and "확인되지 않은" in out["note"] and len(out["candidates"]) == 5
    assert dds_study.suggest_next(sid, n=2)["verified"] is False
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["counts"] == {"complete": 12}


# ---------- E12: DOE 계획만, 나중에 연결 ----------
def test_e12_doe_plan_export_then_connect_or_fill(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E12")
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "계획", "adapter": "data", "variables": [{"name": "x1", "lower": -5, "upper": 10, "unit": "mm"}, {"name": "x2", "lower": 0, "upper": 15}],
        "objectives": [{"name": "f", "goal": "min"}]}).json()["id"]
    assert client.get(f"/api/studies/{sid}/doe.csv", headers=admin).status_code == 400
    client.post(f"/api/studies/{sid}/doe", json={"n": 6, "seed": 1}, headers=admin)
    r = client.get(f"/api/studies/{sid}/doe.csv", headers=admin)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")
    table = read_table(r.content, "doe.csv")
    assert table.names == ["case_id", "status", "x1", "x2", "f"] and len(table.rows) == 6 and table.columns[2].unit == "mm"

    # 해석 방법이 없으면 이유와 할 일
    r = client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    assert r.status_code == 400 and "해석 방법을 연결" in r.json()["detail"]
    # 밖에서 두 점을 해석해 돌려줌: 대기 점이 채워지고 새 케이스가 생기지 않는다
    recs = table.records()[:2]
    rows = [{"x1": rec["x1"], "x2": rec["x2"], "f": 1.5 + i} for i, rec in enumerate(recs)]
    rep = client.post(f"/api/studies/{sid}/cases/import", json={"rows": rows}, headers=admin).json()
    assert rep["filled_pending"] == 2 and rep["registered"] == 0
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["counts"] == {"complete": 2, "pending": 4}

    # 해석 방법을 연결하면 남은 대기 점이 그대로 이어서 돈다
    r = client.put(f"/api/studies/{sid}/adapter", json={"adapter": "없음"}, headers=admin)
    assert r.status_code == 400 and "쓸 수 있는 것" in r.json()["detail"]
    r = client.put(f"/api/studies/{sid}/adapter", json={"adapter": "script", "adapter_config": {}}, headers=admin)
    assert r.status_code == 400 and "tool" in r.json()["detail"]
    r = client.put(f"/api/studies/{sid}/adapter", json={"adapter": "mock", "adapter_config": {"function": "branin"}}, headers=admin)
    assert r.status_code == 200 and r.json()["study"]["adapter"] == "mock" and r.json()["counts"]["pending"] == 4
    assert client.post(f"/api/studies/{sid}/run", json={}, headers=admin).json()["queued"] == 4
    st = _wait_idle(client, sid, admin)
    assert st["counts"] == {"complete": 6}
    assert client.get(f"/api/studies/{sid}/doe.csv?status=complete", headers=admin).status_code == 200


def test_script_without_tool_explains_instead_of_tool_keyerror(client, tmp_path):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "도구 없음")
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "빈 설정", "adapter": "script", "variables": [{"name": "x", "lower": 0, "upper": 1}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": 2}, headers=admin)
    r = client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    assert r.status_code == 400 and "해석 방법" in r.json()["detail"]
    from cae_adapters.script import ScriptAdapter
    out = ScriptAdapter().run_case({"x": 1}, tmp_path / "c", {}, None, {})
    assert out.status == "failed" and "tool" in out.error and "→" in out.error
    # 어댑터 자체도 돌리면 같은 문구
    from cae_adapters.data import DataAdapter
    assert "해석 방법을 연결" in DataAdapter().run_case({}, tmp_path, {}, None, {}).error


def test_imported_rows_are_not_rerun_after_connecting(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "다시 안 돌림")
    rep = _import(client, admin, pid, rows=_rows(5), fixed={"하중": 1000}).json()
    sid = rep["study_id"]
    client.put(f"/api/studies/{sid}/adapter", json={"adapter": "mock", "adapter_config": {"function": "branin"}}, headers=admin)
    ids = rep["case_ids"]
    assert client.post(f"/api/studies/{sid}/run", json={"case_ids": ids}, headers=admin).json()["queued"] == 0
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["counts"] == {"complete": 5}


# ---------- E13: 필드 → 3D Simulator ----------
def _npz(values, n=20, scale=1.0) -> bytes:
    pts = np.column_stack([np.arange(n, dtype=float) * scale, np.zeros(n), np.zeros(n)])
    buf = io.BytesIO()
    np.savez(buf, points=pts, values=values)
    return buf.getvalue()


def test_e13_field_results_to_3d_simulator(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E13")
    rows = [{"t": float(i)} for i in range(1, 7)]
    # 스칼라 출력 없이도 expects_fields면 받는다
    r = client.post(f"/api/projects/{pid}/studies/import", headers=admin, json={"name": "필드", "source": "x", "inputs": [{"name": "t"}], "rows": rows})
    assert r.status_code == 400 and "출력 열" in r.json()["detail"]
    rep = client.post(f"/api/projects/{pid}/studies/import", headers=admin,
                      json={"name": "필드", "source": "x", "inputs": [{"name": "t"}], "rows": rows, "expects_fields": True, "kind": "folders"}).json()
    assert rep["registered"] == 6 and rep["outputs"] == []
    sid = rep["study_id"]
    # 틀린 필드 파일은 받기 전에 알린다
    cid = rep["case_ids"][0]
    for bad, hint in [(b"not npz", "읽지 못했"), (_npz_bad_keys(), "points와 values"), (_npz(np.zeros(5), n=20), "노드 수")]:
        r = client.post(f"/api/cases/{cid}/fields", headers=admin, files={"file": ("mises.npz", bad)})
        assert r.status_code == 400 and hint in r.json()["detail"], r.text
    for cid, row in zip(rep["case_ids"], rows):
        vals = np.sin(np.linspace(0, 3, 20)) * row["t"] * 10
        r = client.post(f"/api/cases/{cid}/fields", headers=admin, files={"file": ("mises.npz", _npz(vals))})
        assert r.status_code == 200, r.text
        assert r.json()["auto_output"] == "mises_max"
    cases = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert cases[0]["fields"] == ["mises"] and cases[0]["results"]["mises_max"] == pytest.approx(10.0, rel=1e-3)
    st = client.get(f"/api/studies/{sid}", headers=admin).json()["study"]
    assert st["outputs"] == [{"name": "mises_max", "note": "mises 필드 최대값(자동)", "auto": True}]
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert m["outputs"] == ["mises_max"] and m["n_train"] == 6
    fm = client.post(f"/api/studies/{sid}/train_field", json={"field": "mises"}, headers=admin)
    assert fm.status_code == 200, fm.text
    deployed = client.post(f"/api/models/{m['id']}/deploy", headers=admin).json()
    assert deployed["field_models"]["mises"] == fm.json()["id"]
    assert client.post(f"/api/field-models/{fm.json()['id']}/predict", json={"params": {"t": 3.5}}, headers=admin).status_code == 200


def _npz_bad_keys() -> bytes:
    buf = io.BytesIO()
    np.savez(buf, foo=np.zeros(3))
    return buf.getvalue()


def test_field_upload_keeps_real_scalars(client):  # noqa: F811
    """스칼라 출력이 있는 스터디에 필드를 올려도 출력이 늘지 않는다."""
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "필드+스칼라")
    rep = _import(client, admin, pid, rows=_rows(5)).json()
    r = client.post(f"/api/cases/{rep['case_ids'][0]}/fields", headers=admin, files={"file": ("mises.npz", _npz(np.ones((20, 3))))})
    assert r.status_code == 200 and r.json()["auto_output"] is None
    assert "mises_max" not in client.get(f"/api/studies/{rep['study_id']}/cases", headers=admin).json()[0]["results"]


# ---------- E14: 다른 프로젝트 ----------
def test_e14_other_project_and_membership(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    other, _ = _auth(client, "e14_other", admin)
    pid = _project(client, admin, "E14")
    sid = _import(client, admin, pid).json()["study_id"]
    outsider_pid = _project(client, other, "E14 남의 것")
    assert client.get(f"/api/projects/{outsider_pid}/studies", headers=admin).status_code in (200, 403)  # 관리자 규칙을 따른다
    assert client.post(f"/api/projects/{pid}/studies/import", json={}, headers=other).status_code == 403
    assert client.post(f"/api/projects/{pid}/studies/import/preview", headers=other, files={"file": ("a.csv", b"a,b\n1,2\n")}).status_code == 403
    assert client.post(f"/api/studies/{sid}/cases/import", json={"rows": []}, headers=other).status_code == 403
    assert client.post(f"/api/studies/{sid}/clone", json={}, headers=other).status_code == 403
    assert client.get(f"/api/studies/{sid}/doe.csv", headers=other).status_code == 403
    assert client.put(f"/api/studies/{sid}/adapter", json={"adapter": "mock"}, headers=other).status_code == 403
    # 내 다른 프로젝트로 복제
    pid2 = _project(client, admin, "E14 둘째")
    copy = client.post(f"/api/studies/{sid}/clone", json={"project_id": pid2}, headers=admin).json()
    assert copy["project_id"] == pid2 and copy["name"] == "표 복사"


# ---------- E15·E16: 신규 모델로 다시 / 다른 멤버가 이어받기 ----------
def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


def test_e15_clone_for_new_model_leaves_old_results_alone(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E15")
    config = {"tool": "python", "script": "scripts/run.py", "post": [{"tool": "python", "script": "scripts/ex.py"}]}
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "옛 모델", "adapter": "script", "adapter_config": config, "variables": [{"name": "t", "lower": 1, "upper": 5, "unit": "mm"}],
        "objectives": [{"name": "m", "goal": "min"}], "outputs": [{"name": "d", "note": "처짐"}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": 4, "seed": 1}, headers=admin)
    ids = [c["id"] for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json()]
    for i, c in enumerate(ids):
        service.on_case_status({"case_id": c, "status": "complete", "results": {"m": float(i), "d": float(i) * 2}})
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    client.post(f"/api/models/{m['id']}/deploy", headers=admin)
    client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", _zip({"scripts/run.py": b"print(1)", "scripts/ex.py": b"print(2)"}))})
    before = client.get(f"/api/studies/{sid}", headers=admin).json()

    r = client.post(f"/api/studies/{sid}/clone", json={"name": "신규 모델", "variables": [{"name": "t", "lower": 2, "upper": 9, "unit": "mm"}]}, headers=admin)
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["adapter"] == "script" and new["adapter_config"] == config and new["objectives"] == before["study"]["objectives"]
    assert new["variables"][0]["upper"] == 9 and new["outputs"] == before["study"]["outputs"] and new["import_info"]["cloned_from"]["study_id"] == sid
    assert new["has_recipe"]
    # 케이스·모델을 옮기지 않는다
    assert client.get(f"/api/studies/{new['id']}/cases", headers=admin).json() == []
    assert client.get(f"/api/studies/{new['id']}/recipe", headers=admin).json()["has"]
    # 옛 스터디·모델·Simulator는 그대로
    after = client.get(f"/api/studies/{sid}", headers=admin).json()
    assert after["counts"] == before["counts"] and after["study"]["variables"] == before["study"]["variables"]
    assert client.get(f"/api/models/{m['id']}/spec", headers=admin).json()["current_model_id"] == m["id"]
    # 같은 이름을 줘도 결과 카드가 섞이지 않게 이름이 겹치지 않는다
    assert client.post(f"/api/studies/{sid}/clone", json={"name": "신규 모델"}, headers=admin).json()["name"] == "신규 모델 (2)"
    assert client.post(f"/api/studies/{sid}/clone", json={"variables": [{"name": "t", "lower": 9, "upper": 2}]}, headers=admin).status_code == 400
    assert client.post(f"/api/studies/{sid}/clone", json={"adapter": "없음"}, headers=admin).status_code == 400


def test_e15_flow_summary_finds_studies(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E15 흐름")
    f = client.post(f"/api/projects/{pid}/flows", headers=admin, json={"title": "브래킷", "goal": "경량화", "nodes": [{"id": "doe", "label": "DOE", "type": "doe"}], "edges": []}).json()
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "빔", "adapter": "mock", "adapter_config": {"function": "branin"},
        "variables": [{"name": "x1", "lower": -5, "upper": 10}, {"name": "x2", "lower": 0, "upper": 15}], "objectives": [{"name": "f", "goal": "min"}]}).json()["id"]
    client.post(f"/api/studies/{sid}/doe", json={"n": 4, "seed": 1, "flow_id": f["id"], "node_id": "doe"}, headers=admin)
    client.put(f"/api/flows/{f['id']}/status", json={"status": "approved", "version": f["version"]}, headers=admin)  # 승인 전 흐름에 묶인 스터디는 돌지 않는다
    client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    _wait_idle(client, sid, admin)
    m = client.post(f"/api/studies/{sid}/train", json={"flow_id": f["id"], "node_id": "doe"}, headers=admin).json()
    rows = client.get(f"/api/projects/{pid}/flows/summary", headers=admin).json()
    assert rows[0]["id"] == f["id"] and rows[0]["goal"] == "경량화" and rows[0]["nodes"][0]["label"] == "DOE"
    link = rows[0]["studies"][0]
    assert link["id"] == sid and link["counts"] == {"complete": 4} and link["latest_model_id"] == m["id"] and link["deployed_model_id"] is None
    assert client.get(f"/api/studies/{sid}", headers=admin).json()["study"]["flow_id"] == f["id"]
    # 흐름을 기록하기 전에 만든 스터디: 결과 카드 제목으로 찾는다
    old = _import(client, admin, pid, rows=_rows(4), name="옛날").json()["study_id"]
    client.post(f"/api/projects/{pid}/artifacts", headers=admin, json={"kind": "markdown", "title": "[옛날] 메모", "payload": {"text": "x"}, "flow_id": f["id"]})
    rows = client.get(f"/api/projects/{pid}/flows/summary", headers=admin).json()
    assert {s["id"] for s in rows[0]["studies"]} == {sid, old}


def test_e16_member_continues_without_my_pc(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    member, member_user = _auth(client, "e16_member", admin)
    pid = _project(client, admin, "E16")
    client.put(f"/api/projects/{pid}/members/{member_user['id']}", json={"role": "member"}, headers=admin)
    # 내 PC의 원본 폴더를 가리키는 케이스들. 내 PC는 꺼져 있다
    rows = [{**d, "_run_dir": f"D:/내결과/c{i}"} for i, d in enumerate(_rows(10))]
    rep = _import(client, admin, pid, rows=rows, kind="folders", source="D:/내결과").json()
    sid = rep["study_id"]
    assert registry.get(me["id"]) is None and registry.get(member_user["id"]) is None
    # 멤버: 결과 조회·학습·배포·예측 모두 PC 없이 된다
    assert len(client.get(f"/api/studies/{sid}/cases", headers=member).json()) == 10
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=member).json()
    assert m["n_train"] == 10 and m["created_by"] == member_user["id"]
    assert client.post(f"/api/models/{m['id']}/deploy", headers=member).json()["deployed"]
    assert client.post(f"/api/models/{m['id']}/predict", json={"params": {"thickness": 2, "width": 20}}, headers=member).status_code == 200
    # 내 원본이 필요한 Output 추가는 이유와 할 일을 알린다
    r = client.post(f"/api/studies/{sid}/outputs", headers=member, json={"outputs": [{"name": "mass"}], "post": {"tool": "python", "script": "scripts/ex.py"}}).json()
    assert r["queued"] == 0 and r["skipped"] == 10 and "PC Agent" in r["note"] and "PC를 켠" in r["note"]
    # 내 PC가 켜져 있으면 그 PC로 간다(원본은 거기 있다)
    sent = []

    class _Conn:
        user_id = me["id"]

        def send(self, msg):
            sent.append(msg)
    registry.conns[me["id"]] = _Conn()
    try:
        r = client.post(f"/api/studies/{sid}/outputs", headers=member, json={"outputs": [{"name": "mass2"}], "post": {"tool": "python", "script": "scripts/ex.py"}}).json()
    finally:
        registry.conns.pop(me["id"], None)
        service._extracting.pop(sid, None)
    assert r["queued"] == 10 and sent[0]["run_dir"] == "D:/내결과/c0" and sent[0]["adapter"] == "data"
    # 표에서 온 행은 뽑을 원본이 없다
    rep2 = _import(client, admin, pid, rows=_rows(5, seed=11), name="표만").json()
    r = client.post(f"/api/studies/{rep2['study_id']}/outputs", headers=member, json={"outputs": [{"name": "mass"}]}).json()
    assert r["no_source"] == 5 and "행 추가" in r["note"]
    r = client.post(f"/api/studies/{rep2['study_id']}/outputs", headers=member, json={"outputs": [{"name": "m3"}], "post": {"tool": "python", "script": "a.py"}}).json()
    assert r["queued"] == 0 and r["no_source"] == 5 and "표에서 온" in r["note"]
    # 레시피: 멤버가 내려받아 이어 쓴다. 만든 사람이 보인다
    sid3 = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "스크립트", "adapter": "script", "adapter_config": {"tool": "python", "script": "scripts/run.py"}, "variables": [{"name": "x", "lower": 0, "upper": 1}]}).json()["id"]
    assert client.get(f"/api/studies/{sid3}/recipe", headers=member).json() == {**client.get(f"/api/studies/{sid3}/recipe", headers=member).json(), "has": False}
    assert client.get(f"/api/studies/{sid3}/recipe/file", headers=member).status_code == 404
    up = client.post(f"/api/studies/{sid3}/recipe", headers=admin, files={"file": ("r.zip", _zip({"scripts/run.py": b"print(1)\n"}))}, data={"note": "처음"})
    assert up.status_code == 200, up.text
    got = client.get(f"/api/studies/{sid3}/recipe", headers=member).json()
    assert got["has"] and got["created_by_name"] == "admin" and got["version"] == 1 and got["adapter_config"]["script"] == "scripts/run.py" and got["note"] == "처음"
    assert got["warnings"] == [] and got["files"][0]["path"] == "scripts/run.py"
    r = client.get(f"/api/studies/{sid3}/recipe/file", headers=member)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        assert z.read("scripts/run.py") == b"print(1)\n"
        assert json.loads(z.read("recipe.json"))["created_by"] == "admin"
    # 다시 올리면 판이 오르고 지울 수 있다
    assert client.post(f"/api/studies/{sid3}/recipe", headers=member, files={"file": ("r.zip", _zip({"scripts/run.py": b"print(2)\n"}))}).json()["version"] == 2
    assert client.get(f"/api/studies/{sid3}/recipe", headers=member).json()["created_by_name"] == "e16_member"
    assert client.delete(f"/api/studies/{sid3}/recipe", headers=member).status_code == 200
    assert client.get(f"/api/studies/{sid3}/recipe", headers=member).json()["has"] is False
    other, _ = _auth(client, "e16_outsider", admin)
    assert client.get(f"/api/studies/{sid3}/recipe", headers=other).status_code == 403


def _symlink_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("link.py")
        info.external_attr = 0o120777 << 16
        z.writestr(info, "/etc/passwd")
    return buf.getvalue()


def _bomb_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("big.py", b"#" * (6 * 1024 * 1024))
    return buf.getvalue()


@pytest.mark.parametrize("data,hint", [
    (b"not zip", "zip 파일이 아닙니다"),
    (_zip({}), "파일이 없습니다"),
    (_zip({"../evil.py": b"x"}), "안전하지 않은 경로"),
    (_zip({"a/../../evil.py": b"x"}), "안전하지 않은 경로"),
    (_zip({"/abs/evil.py": b"x"}), "안전하지 않은 경로"),
    (_zip({"C:/win/evil.py": b"x"}), "안전하지 않은 경로"),
    (_zip({"a\\..\\..\\evil.py": b"x"}), "안전하지 않은 경로"),
    (_zip({".env": b"A=1"}), "비밀값"),
    (_zip({"conf/credentials.json": b"{}"}), "비밀값"),
    (_zip({"id_rsa": b"x"}), "비밀값"),
    (_zip({"server.pem": b"x"}), "비밀값"),
    (_zip({"tool.exe": b"MZ"}), "실행 파일"),
    (_zip({"run.py": b"MZ\x90\x00"}), "실행 파일"),
    (_zip({"a.py": b"-----BEGIN RSA PRIVATE KEY-----\nabc"}), "비밀값"),
    (_zip({"a.py": b"API_KEY = 'abc123456789'"}), "비밀값"),
    (_zip({"a.py": b"password: \"hunter2hunter2\""}), "비밀값"),
    (_zip({"a.py": b"t = 'ghp_" + b"a" * 36 + b"'"}), "비밀값"),
    (_symlink_zip(), "링크"),
    (_bomb_zip(), "MB를 넘습니다"),
    (_zip({f"f{i}.py": b"x" for i in range(201)}), "개를 넘습니다"),
], ids=lambda v: "" if isinstance(v, bytes) else None)
def test_recipe_rejects_unsafe_bundles(client, data, hint):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "레시피 거절")
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={"name": "s", "adapter": "script", "adapter_config": {"tool": "python"},
                                                                          "variables": [{"name": "x", "lower": 0, "upper": 1}]}).json()["id"]
    r = client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", data)})
    assert r.status_code == 400, r.text
    assert hint in r.json()["detail"] and "→" in r.json()["detail"]
    assert "hunter2" not in r.json()["detail"] and "abc123" not in r.json()["detail"]  # 찾은 값을 문구에 다시 쓰지 않는다
    assert client.get(f"/api/studies/{sid}/recipe", headers=admin).json()["has"] is False


def test_recipe_accepts_reading_secret_from_env_and_warns_about_config(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "레시피 설정")
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={
        "name": "s", "adapter": "script", "variables": [{"name": "x", "lower": 0, "upper": 1}],
        "adapter_config": {"tool": "python", "script": "scripts/없음.py", "inputs": ["models/a.cae", "D:/abs/b.cae"]}}).json()["id"]
    ok = _zip({"scripts/run.py": b"import os\npassword = os.environ['PW']\n", "notes.txt": "한글 메모".encode()})
    r = client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", ok)})
    assert r.status_code == 200, r.text
    w = " ".join(r.json()["warnings"])
    assert "scripts/없음.py" in w and "첨부" in w and "절대 경로" in w
    # 설정에 비밀값이 있으면 거절
    bad = json.dumps({"tool": "python", "api_token": "abcdef"})
    r = client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", ok)}, data={"adapter_config": bad})
    assert r.status_code == 400 and "adapter_config.api_token" in r.json()["detail"] and "abcdef" not in r.json()["detail"]
    assert client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", ok)}, data={"adapter_config": "{깨짐"}).status_code == 400
    assert client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", ok)}, data={"adapter_config": "[1]"}).status_code == 400
    # 묶음에 넣은 recipe.json으로 만든 사람을 속일 수 없다
    r = client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", _zip({"recipe.json": b'{"created_by":"\xea\xb1\xb0\xec\xa7\x93"}', "a.py": b"1"}))})
    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/studies/{sid}/recipe/file", headers=admin).content))
    assert json.loads(z.read("recipe.json"))["created_by"] == "admin"


# ---------- 원본 폴더는 건드리지 않는다 ----------
class _Runner:
    """명령을 실제로 돌리지 않고, 추출 스크립트가 할 일을 흉내 낸다(원본을 읽고 cwd에 결과를 쓴다)."""
    stop = None

    def __init__(self):
        self.calls = []

    def run(self, cmd, cwd, timeout=None, env=None):
        from agent.dds_agent.job_runner import JobResult
        self.calls.append({"cmd": cmd, "cwd": Path(cwd), "env": env})
        value = float(Path(env["DDS_SOURCE_DIR"], "solver.out").read_text())
        Path(cwd, "results.json").write_text(json.dumps({"disp": value}), encoding="utf-8")
        return JobResult(cmd, str(cwd), 0, "", "")


def _ext_job(run_dir, **kw):
    return {"type": "case.extract", "case_id": 7, "study_id": 2, "project_id": 3, "project_name": "p", "project_uid": "u", "study_name": "s",
            "source": "import", "params": {"t": 1.0}, "results": {"mass": 2.0}, "run_dir": str(run_dir), "adapter": "data", "adapter_config": {},
            "steps": [{"tool": "python", "script": "scripts/ex.py", "args": ["{source_dir}/solver.out"]}], "names": ["disp"], **kw}


def test_extract_from_external_folder_never_writes_to_original(tmp_path):
    original = tmp_path / "원본" / "t=3.0"
    original.mkdir(parents=True)
    (original / "solver.out").write_text("7.5", encoding="utf-8")
    before = {p.name: p.read_bytes() for p in original.iterdir()}
    msgs = []
    e = CaseExecutor(AgentSettings(workspace_dir=tmp_path / "ws"), msgs.append)
    ws = e.s.workspace
    sdir = ws.study_dir(3, 2, "s", "p", "u")
    pdir = ws.project_dir(3, "p", "u")
    (pdir / "scripts").mkdir(parents=True, exist_ok=True)
    (pdir / "scripts" / "ex.py").write_text("print('extract')", encoding="utf-8")
    runner = _Runner()
    e._runner_for = lambda job: runner
    e._archive = lambda job, run_dir: {"stored": "pc", "at": str(run_dir)}
    e._extract(_ext_job(original))

    msg = msgs[-1]
    assert msg["error"] is None and msg["results"] == {"disp": 7.5}, msg
    # 원본은 그대로
    assert {p.name: p.read_bytes() for p in original.iterdir()} == before
    # 쓰기는 스터디 폴더 쪽에서 했다
    work = sdir / "cases" / "C0007_import"
    assert runner.calls[0]["cwd"] == work and (work / "ex.py").is_file() and (work / "results.json").is_file() and (work / "params.json").is_file()
    assert msg["archive"]["at"] == str(work)
    # 원본 경로는 환경변수와 인자로 간다
    assert runner.calls[0]["env"]["DDS_SOURCE_DIR"] == str(original) and any(str(original) in a for a in runner.calls[0]["cmd"])
    case_json = json.loads((work / "case.json").read_text(encoding="utf-8"))
    assert case_json["source_dir"] == str(original) and case_json["results"] == {"mass": 2.0, "disp": 7.5} and case_json["folder"].startswith("cases")
    assert "disp" in (sdir / "cases.csv").read_text(encoding="utf-8-sig")


def test_record_case_external_folder_goes_to_study_side(tmp_path):
    ws = Workspace(tmp_path / "ws")
    sdir = ws.study_dir(1, 1, "s", "p", "u")
    original = tmp_path / "원본"
    original.mkdir()
    (original / "keep.txt").write_text("x", encoding="utf-8")
    ws.record_case(sdir, original, {"case_id": 5, "source": "import", "results": {"a": 1.0}})
    assert sorted(p.name for p in original.iterdir()) == ["keep.txt"]
    assert json.loads((sdir / "cases" / "C0005_import" / "case.json").read_text(encoding="utf-8"))["source_dir"] == str(original)
    with pytest.raises(ValueError):
        ws.record_case(sdir, original, {"results": {}})  # 어디에 쓸지 모르면 원본에 쓰지 않고 멈춘다
    assert sorted(p.name for p in original.iterdir()) == ["keep.txt"]
    # 스터디 폴더 안은 예전과 같다
    inside = ws.case_dir(sdir, 6, "doe")
    ws.record_case(sdir, inside, {"case_id": 6, "source": "doe", "status": "complete"})
    assert (inside / "case.json").is_file()


def test_extract_reports_moved_original(tmp_path):
    msgs = []
    e = CaseExecutor(AgentSettings(workspace_dir=tmp_path / "ws"), msgs.append)
    e._runner_for = lambda job: _Runner()
    e._extract(_ext_job(tmp_path / "옮겨짐"))
    assert msgs[-1]["status"] == "complete" and msgs[-1]["extract"] and "원본 해석 폴더를 찾을 수 없습니다" in msgs[-1]["error"]


def test_read_results_drops_nan(tmp_path):
    from cae_adapters.base import read_results
    p = tmp_path / "results.json"
    p.write_text('{"a": 1.5, "b": NaN, "c": Infinity, "d": "글자", "e": true}', encoding="utf-8")
    assert read_results(p) == {"a": 1.5}


# ---------- E1: 표에서 고른 출력을 AI 경로의 기본 학습이 잃지 않는다 ----------
def test_e1_default_training_keeps_chosen_output_with_one_bad_cell(client):  # noqa: F811
    """AI는 train_surrogate(study_id)처럼 출력을 안 주고 부른다. #N/A 한 칸 때문에 사용자가 고른 출력이 모델에서 사라지면 안 된다."""
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E1 일부 출력")
    lines = ["두께\t폭\t응력\t효율"] + [f"{d['thickness']}\t{d['width']}\t{'#N/A' if i == 0 else d['stress']}\t{d['thickness'] * 3}" for i, d in enumerate(_rows(16))]
    sid = client.post(f"/api/projects/{pid}/studies/import/text", headers=admin, json={"text": "\n".join(lines), "outputs": [{"name": "응력"}, {"name": "효율"}]}).json()["study_id"]
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert set(m["outputs"]) == {"응력", "효율"} and m["n_train"] == 16 and m["quality"]["excluded_count"] == 1
    assert m["quality"]["rows_by_output"] == {"응력": 15, "효율": 16}  # 수식 오류 행은 그 출력에서만 빠진다
    # 절반도 안 찬 출력은 고른 출력으로 보지 않는다
    pid2 = _project(client, admin, "E1 빈 출력")
    lines = ["두께\t폭\t응력\t메모값"] + [f"{d['thickness']}\t{d['width']}\t{d['stress']}\t{'' if i else 3.5}" for i, d in enumerate(_rows(16))]
    sid2 = client.post(f"/api/projects/{pid2}/studies/import/text", headers=admin, json={"text": "\n".join(lines), "outputs": [{"name": "응력"}, {"name": "메모값"}]}).json()["study_id"]
    m2 = client.post(f"/api/studies/{sid2}/train", json={}, headers=admin).json()
    assert m2["outputs"] == ["응력"] and any("메모값" in w for w in m2["quality"]["warnings"])


def test_e5_one_or_two_rows_say_the_real_reason(client):  # noqa: F811
    """한두 행이면 모든 열이 상수로 보여 '출력 열이 없다'는 엉뚱한 이유가 나왔다."""
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E5 행 수")
    for text in ("두께\t응력\n1\t2\n", "두께\t응력\n1\t2\n3\t4\n"):
        r = client.post(f"/api/projects/{pid}/studies/import/text", headers=admin, json={"text": text})
        assert r.status_code == 400 and "3행 이상" in r.json()["detail"] and "→" in r.json()["detail"], r.text
    assert client.get(f"/api/projects/{pid}/studies", headers=admin).json() == []


# ---------- E6~E9: Simulator에서 이어 가기, 결과만, 조회, 툴 없음 ----------
def test_e6_output_from_more_rows_then_rollback(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E6")
    rows = _rows(12)
    sid = _import(client, admin, pid, rows=rows).json()["study_id"]
    v1 = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    client.post(f"/api/models/{v1['id']}/deploy", headers=admin)
    token = client.post(f"/api/models/{v1['id']}/shares", json={}, headers=admin).json()["token"]
    point = {"params": {"thickness": 3.0, "width": 20.0}}
    before = client.post(f"/api/models/{v1['id']}/predict", json=point, headers=admin).json()["mean"]

    # 표에서 온 데이터의 Output 추가: 입력이 같은 행에 새 열을 합친다. 행이 늘지 않는다
    more = [{"thickness": d["thickness"], "width": d["width"], "mass": d["thickness"] * d["width"]} for d in rows]
    rep = client.post(f"/api/studies/{sid}/cases/import", json={"rows": more, "outputs": [{"name": "mass"}]}, headers=admin).json()
    assert rep["updated"] == 12 and rep["registered"] == 0
    v2 = client.post(f"/api/studies/{sid}/train", json={"outputs": ["stress", "mass"]}, headers=admin).json()
    assert v2["version"] == 2 and set(v2["outputs"]) == {"stress", "mass"}
    client.post(f"/api/models/{v2['id']}/deploy", headers=admin)
    assert client.get(f"/api/shares/{token}", headers=admin).json()["model"]["id"] == v2["id"]
    assert "mass" in client.post(f"/api/shares/{token}/predict", json=point, headers=admin).json()["mean"]

    # 되돌리기: 옛 버전을 배포하면 공유 링크도 옛 버전이고 예측이 그대로다
    client.post(f"/api/models/{v1['id']}/deploy", headers=admin)
    deployed = [m["id"] for m in client.get(f"/api/projects/{pid}/models", headers=admin).json() if m["deployed"]]
    assert deployed == [v1["id"]]
    shared = client.post(f"/api/shares/{token}/predict", json=point, headers=admin).json()["mean"]
    assert shared == before and "mass" not in shared


def test_e7_report_needs_no_flow_or_pc(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    pid = _project(client, admin, "E7")
    sid = _import(client, admin, pid, rows=_rows(10)).json()["study_id"]
    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    assert registry.get(me["id"]) is None
    ctx = client.get(f"/api/projects/{pid}/report-context", headers=admin).json()
    assert ctx["flows"] == [] and ctx["studies"][0]["study_id"] == sid and ctx["studies"][0]["models"]
    r = client.post(f"/api/projects/{pid}/reports", json={"title": "표 모델 보고서", "body": "## 요약\n- 표로 만든 모델입니다."}, headers=admin)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/projects/{pid}/report", params={"format": "docx"}, headers=admin).content[:2] == b"PK"
    assert client.get(f"/api/projects/{pid}/flows", headers=admin).json() == []


def test_e8_reading_changes_nothing(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin, "E8")
    sid = _import(client, admin, pid, rows=_rows(10)).json()["study_id"]
    client.post(f"/api/studies/{sid}/train", json={}, headers=admin)
    before = client.get(f"/api/studies/{sid}", headers=admin).json()
    for path in (f"/api/projects/{pid}/studies", f"/api/projects/{pid}/models", f"/api/projects/{pid}/flows/summary", f"/api/studies/{sid}/cases", f"/api/projects/{pid}/report-context"):
        assert client.get(path, headers=admin).status_code == 200, path
    assert client.get(f"/api/studies/{sid}", headers=admin).json() == before
    assert client.get(f"/api/projects/{pid}/flows", headers=admin).json() == []


def test_e9_no_tool_no_pc_table_still_works_and_solver_request_explains(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    pid = _project(client, admin, "E9")
    assert registry.get(me["id"]) is None
    sid = _import(client, admin, pid, rows=_rows(10)).json()["study_id"]
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert client.post(f"/api/models/{m['id']}/deploy", headers=admin).json()["deployed"]
    # 해석이 필요한 스터디는 PC가 없으면 이유를 말한다. 500이 아니다
    solver = client.post(f"/api/projects/{pid}/studies", headers=admin, json={"name": "해석", "adapter": "script", "adapter_config": {"tool": "abaqus_cae", "script": "scripts/a.py"},
                                                                              "variables": [{"name": "x", "lower": 0, "upper": 1}]}).json()["id"]
    client.post(f"/api/studies/{solver}/doe", json={"n": 2, "seed": 1}, headers=admin)
    r = client.post(f"/api/studies/{solver}/run", json={}, headers=admin)
    assert 400 <= r.status_code < 500 and "PC Agent" in r.json()["detail"], r.text
