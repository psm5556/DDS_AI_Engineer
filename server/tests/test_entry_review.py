"""어느 시점에서든 진입 검토에서 실제로 재현한 결함의 회귀 시험. 시나리오 번호는 docs/entry-points.md와 맞춘다."""
import io
import json
import threading
import zipfile

import numpy as np
import pytest

from server.tests.test_api import _auth, _wait_idle, client  # noqa: F401 — 같은 서버 fixture 재사용, 서버 코드보다 먼저 불러온다

from cae_adapters import result_scan  # noqa: E402
from cae_adapters.tabular import MAX_CELLS, TableError, analyze, cell_state, fill_guessing, join_tables, read_pasted, read_table  # noqa: E402
from server.app.agent.link import registry  # noqa: E402
from server.app.study import recipe as recipe_mod  # noqa: E402
from server.app.study import service  # noqa: E402
from server.app.study.surrogate import Surrogate  # noqa: E402


def _project(client, h, name="검토"):  # noqa: F811
    return client.post("/api/projects", json={"name": name}, headers=h).json()["id"]


def _f(t, w):
    return 1000.0 / (t * w) + 0.5 * t


def _rows(n=20, seed=3):
    rng = np.random.default_rng(seed)
    return [{"t": float(a), "w": float(b), "s": _f(float(a), float(b))} for a, b in zip(rng.uniform(1, 5, n), rng.uniform(10, 40, n))]


def _import(client, h, pid, rows=None, **kw):  # noqa: F811
    body = {"name": "표", "source": "t.csv", "inputs": [{"name": "t"}, {"name": "w"}], "outputs": [{"name": "s"}], "rows": rows if rows is not None else _rows(), **kw}
    return client.post(f"/api/projects/{pid}/studies/import", content=json.dumps(body), headers={**h, "content-type": "application/json"})


def _paste(client, h, pid, text, outputs=None):  # noqa: F811
    body = {"text": text, "name": "붙임", **({"outputs": [{"name": n} for n in outputs]} if outputs else {})}
    return client.post(f"/api/projects/{pid}/studies/import/text", json=body, headers=h)


class _Conn:
    def __init__(self, user_id):
        self.user_id, self.sent = user_id, []

    def send(self, msg):
        self.sent.append(msg)


# ---------- 표 읽기
def test_join_keeps_output_names_when_left_rows_have_trailing_commas():
    left = read_table(b"id,t,w,\n1,2,3,\n2,3,4,\n3,4,5,\n", filename="a.csv")
    right = read_table(b"id,stress,mass\n1,300,5\n2,310,6\n3,320,7\n", filename="b.csv")
    joined, _ = join_tables(left, right)
    assert joined.records()[0]["stress"] == 300 and joined.records()[0]["mass"] == 5
    some = read_table(b"id,t,w\n1,2,3,\n2,3,4\n3,4,5,\n", filename="a.csv")
    rec = join_tables(some, right)[0].records()
    assert [r["stress"] for r in rec] == [300, 310, 320]


def test_join_names_do_not_collide():
    left = read_table(b"id,y,y_2\n1,2,3\n2,3,4\n3,4,5\n", filename="a.csv")
    right = read_table(b"id,y\n1,9\n2,8\n3,7\n", filename="b.csv")
    names = join_tables(left, right, "id")[0].names
    assert len(names) == len(set(names)) and "y_3" in names


@pytest.mark.parametrize("name", ["a.tsv", "a.csv", "붙여넣은_표_1.tsv"])
def test_unbalanced_quote_does_not_swallow_following_rows(name):
    sep = "," if name == "a.csv" else "\t"
    rows = "".join(f"{i}{sep}{i * 2}{sep}{'\"A형' if i == 500 else 'x'}{sep}{i * 3}\n" for i in range(1, 1001))
    table = read_table(f"t{sep}w{sep}note{sep}y\n{rows}".encode(), filename=name)
    assert len(table.rows) == 1000 and any("따옴표" in w for w in table.warnings)


def test_quoted_cells_still_work_next_to_a_broken_one():
    text = 't,y\n"1,234",5\n"2,345",6\n"A형,7\n"3,456",8\n'
    table = read_table(text.encode(), filename="a.csv")
    assert [r[0] for r in table.rows][:2] == ["1,234", "2,345"] and len(table.rows) == 4
    # 엑셀의 따옴표 안 줄바꿈은 행을 삼킨 것이 아니다
    assert read_pasted('a\tb\tc\n"x\ny"\t1\t2\nz\t3\t4\nq\t5\t6\n').rows[0][0] == "x\ny"


def test_decimal_comma_values_are_not_read_as_thousands():
    t = read_table("t;w;stress\n1,000;20,000;300,000\n1,100;20,500;280,500\n1,200;21,000;270,000\n".encode(), filename="s.csv")
    assert t.decimal_comma and t.records()[0] == {"_row": 2, "t": 1.0, "w": 20.0, "stress": 300.0}
    assert read_pasted("t\tw\ty\n0,100\t1\t3\n0,200\t2\t4\n0,300\t2\t8\n").records()[0]["t"] == 0.1
    # 다른 칸이 소수점 점이어도 0,125는 천 단위가 아니다
    assert cell_state("0,125")[0] == 0.125
    # 정말 모르는 표는 천 단위로 읽고 알린다
    amb = read_pasted("t\tw\ty\n1,000\t1\t3\n1,200\t2\t4\n1,300\t2\t8\n")
    assert amb.records()[0]["t"] == 1000 and any("천 단위 쉼표" in w for w in amb.warnings)
    clear = read_pasted("t\tw\ty\n1,000\t1.5\t3\n1,200\t2.5\t4\n1,300\t2.5\t8\n")
    assert clear.records()[0]["t"] == 1000 and not any("천 단위 쉼표" in w for w in clear.warnings)


@pytest.mark.parametrize("units", ["㎜,㎜,㎫", "mm,mm,㎫", "℃,psi,ksi", "㎏,㎠,㎟"])
def test_korean_excel_unit_symbols_are_units_not_column_names(units):
    a = analyze(read_table(f"thickness,width,stress\n{units}\n1,2,3\n2,3,4\n3,4,6\n".encode(), filename="u.csv"))
    assert [c["name"] for c in a["columns"]] == ["thickness", "width", "stress"]
    assert a["columns"][0]["unit"] == units.split(",")[0]


def test_unit_in_header_name_and_dropped_header_warning():
    t = read_pasted("두께 (㎜)\t응력 [㎫]\n1\t3\n2\t4\n3\t6\n")
    assert t.names == ["두께", "응력"] and t.columns[0].unit == "㎜"
    # 모르는 단위 줄이면 진짜 이름이 사라진 것을 말한다
    odd = analyze(read_pasted("thickness\twidth\tstress\nQQ\tQQ\tZZ\n1\t2\t3\n2\t3\t4\n3\t4\t6\n"))
    assert any("thickness" in w and "머리글" in w for w in odd["warnings"])


def test_summary_rows_are_not_training_data(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    # (a) 글자 번호 + 요약 3행
    body = "case\t두께\t폭\t응력\n" + "".join(f"C{i:03d}\t{i * 0.5}\t{i % 5 + 1}\t{i * 2.5 + (i % 5)}\n" for i in range(1, 15)) + "평균\t4\t3\t17\n최대\t7\t5\t28\n최소\t1\t1\t2\n"
    rep = _paste(client, admin, pid, body, ["응력"]).json()
    assert rep["registered"] == 14 and any("요약 행 3개" in w for w in rep["warnings"])
    # (b) 숫자 번호 + 평균: 번호가 입력이 되지 않는다
    body2 = "번호\t두께\t폭\t응력\n" + "".join(f"{i}\t{i * 0.5}\t{i % 5 + 1}\t{i * 2.5 + (i % 5)}\n" for i in range(1, 17)) + "평균\t4\t3\t17\n"
    rep2 = _paste(client, admin, pid, body2, ["응력"]).json()
    assert rep2["registered"] == 16 and [v["name"] for v in rep2["variables"]] == ["두께", "폭"]
    # (c) 첫 열이 비어 있고 라벨만: 라벨이 고정값이 되지 않는다
    body3 = "\t두께\t폭\t응력\n" + "".join(f"\t{i * 0.5}\t{i % 5 + 1}\t{i * 2.5 + (i % 5)}\n" for i in range(1, 16)) + "평균\t4\t3\t17\n"
    rep3 = _paste(client, admin, pid, body3, ["응력"]).json()
    assert rep3["registered"] == 15 and not rep3["fixed"]
    # 범주 열의 max·min은 요약이 아니다
    cat = read_pasted("조건\tt\ty\n" + "".join(f"{'max' if i % 2 else 'min'}\t{i}.5\t{i * 2}\n" for i in range(1, 9)))
    assert len(cat.rows) == 8


@pytest.mark.parametrize("head,start,step,pos", [("No.", 1, 2, 0), ("시험번호", 1, 2, 0), ("시험 번호", 3, 2, 0), ("Sample", 1, 1, 1), ("a", 1, 1, 1)])
def test_row_numbers_never_become_inputs(head, start, step, pos):
    cols = [head, "t", "y"] if pos == 0 else ["t", head, "y"]
    rows = []
    for i in range(16):
        vals = [start + i * step, i % 4 + 0.5, i * 1.5 + (i % 3)]
        rows.append(vals if pos == 0 else [vals[1], vals[0], vals[2]])
    text = "\t".join(cols) + "\n" + "".join("\t".join(map(str, r)) + "\n" for r in rows)
    a = analyze(read_pasted(text))
    assert head in a["excluded"] and head not in a["inputs"]
    # 이름으로 뜻을 아는 값은 그대로 변수다
    assert "두께" in analyze(read_pasted("두께\ty\n" + "".join(f"{i}\t{i * 2 + i % 3}\n" for i in range(1, 9))))["inputs"]


def test_merged_cells_same_through_every_entrance(client, tmp_path):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    body = "lot\t하중\tt\tstress\n"
    for i in range(12):
        lot = str(i // 4 + 1) if i % 4 == 0 else ""
        load = str((i // 4 + 1) * 100) if i % 4 == 0 else ""
        body += f"{lot}\t{load}\t{i * 0.3 + 1}\t{i * 2.0 + (i % 4) * 3 + 5}\n"
    direct = _paste(client, admin, pid, body, ["stress"]).json()
    assert direct["registered"] == 12
    # 같은 글을 AI 첨부(파일)로 보내도 같은 표다: 같은 스터디로 돌아온다
    r = client.post(f"/api/projects/{pid}/studies/import/file", headers=admin, files={"file": ("붙여넣은_표_1.tsv", body.encode())},
                    data={"mapping": json.dumps({"name": "x", "outputs": [{"name": "stress"}]})}).json()
    assert r["reused"] and r["study_id"] == direct["study_id"]
    # 일반 파일도 병합 셀을 같게 채운다
    pid2 = _project(client, admin)
    plain = client.post(f"/api/projects/{pid2}/studies/import/file", headers=admin, files={"file": ("t.tsv", body.encode())},
                        data={"mapping": json.dumps({"outputs": [{"name": "stress"}]})}).json()
    assert plain["registered"] == 12 and {v["name"] for v in plain["variables"]} == {v["name"] for v in direct["variables"]}
    # 출력의 빈칸은 채우지 않는다
    t = read_pasted("t\ty\n1\t5\n2\t\n3\t\n4\t9\n5\t\n")
    fill_guessing(t, ["y"])
    assert t.rows[1][1] == ""


def test_space_separated_files_are_tables_not_encrypted():
    wide = " ".join(f"col{i}" for i in range(30)) + "\n" + "\n".join(" ".join(f"{(i + 1) * (j + 1)}.123456" for i in range(30)) for j in range(5))
    assert len(read_table(wide.encode(), filename="res.txt").names) == 30
    assert read_table(b"t w stress\n1 2 3\n2 3 5\n3 4 7\n", filename="res.csv").names == ["t", "w", "stress"]
    # 띄어쓰기가 없는 긴 글자는 여전히 암호문이다
    with pytest.raises(TableError, match="암호화"):
        read_table(("A" * 2000).encode(), filename="x.csv")


def test_space_separated_header_with_spaces_is_refused_with_next_step():
    with pytest.raises(TableError, match="머리글은 4칸인데 값은 3칸"):
        read_pasted("max stress thickness width\n1 2 3\n3 4 5\n4 4 5\n")


def test_duplicate_column_names_do_not_collide_with_real_columns(client):  # noqa: F811
    t = read_table(b"a,a,a_2,y\n1,2,3,4\n2,3,4,6\n3,4,5,9\n", filename="d.csv")
    assert len(set(t.names)) == 4 and t.names[2] == "a_2"
    admin, _ = _auth(client, "admin")
    r = _paste(client, admin, _project(client, admin), "a\ta\ta_2\ty\n1\t2\t3\t4\n2\t4\t5\t6\n3\t5\t9\t9\n4\t8\t1\t3\n", ["y"])
    assert r.status_code == 200


def _xlsx_raw(sheet: bytes, shared: bytes | None = None, styles: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns:r="r"><sheets><sheet name="S" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", sheet)
        if shared is not None:
            z.writestr("xl/sharedStrings.xml", shared)
        if styles is not None:
            z.writestr("xl/styles.xml", styles)
    return buf.getvalue()


def test_xlsx_far_right_cells_cannot_exhaust_memory():
    rows = "".join(f'<row r="{i}"><c r="BXX{i}"><v>1</v></c></row>' for i in range(1, 20002))
    data = _xlsx_raw(f"<worksheet><sheetData>{rows}</sheetData></worksheet>".encode())
    assert len(data) < 200_000 and MAX_CELLS == 5_000_000
    with pytest.raises(TableError, match="칸이"):
        read_table(data, filename="bomb.xlsx")


def test_xlsx_shared_strings_are_bounded_and_date_cells_are_not_numbers(monkeypatch):
    import cae_adapters.tabular as tabular

    many = "<sst>" + "<si><t>x</t></si>" * 50 + "</sst>"
    monkeypatch.setattr(tabular, "MAX_SHARED_STRINGS", 10)
    with pytest.raises(TableError, match="글자 값이 너무"):
        read_table(_xlsx_raw(b"<worksheet><sheetData/></worksheet>", many.encode()), filename="s.xlsx")
    monkeypatch.undo()
    # 날짜 열: 스타일 1은 날짜, 스타일 0은 숫자
    styles = b'<styleSheet><numFmts><numFmt numFmtId="170" formatCode="0.00&quot;mm&quot;"/></numFmts><cellXfs><xf numFmtId="170"/><xf numFmtId="14"/></cellXfs></styleSheet>'
    head = '<row r="1"><c r="A1" t="inlineStr"><is><t>date</t></is></c><c r="B1" t="inlineStr"><is><t>t</t></is></c><c r="C1" t="inlineStr"><is><t>y</t></is></c></row>'
    body = "".join(f'<row r="{i}"><c r="A{i}" s="1"><v>{45000 + i}</v></c><c r="B{i}" s="0"><v>{i}.5</v></c><c r="C{i}"><v>{i * 2.5}</v></c></row>' for i in range(2, 14))
    a = analyze(read_table(_xlsx_raw(f"<worksheet><sheetData>{head}{body}</sheetData></worksheet>".encode(), styles=styles), filename="d.xlsx"))
    by = {c["name"]: c for c in a["columns"]}
    assert by["date"]["role"] == "exclude" and "날짜" in by["date"]["role_reason"]
    assert by["t"]["kind"] == "numeric"  # 0.00"mm"은 날짜가 아니다


def test_error_only_column_says_why_it_is_gone():
    a = analyze(read_pasted("두께\t폭\t응력\n1\t2\t#N/A\n2\t3\t#DIV/0!\n3\t5\t#N/A\n"))
    c = next(x for x in a["columns"] if x["name"] == "응력")
    assert "수식 오류" in c["role_reason"] and c["looks_output"]


# ---------- 나쁜 값은 500이 아니라 이유로
def test_huge_int_and_surrogate_and_deep_config_are_reasons_not_500(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    rows = _rows(6)
    rows[0]["s"] = 10 ** 400
    r = _import(client, admin, pid, rows=rows)
    assert r.status_code == 200 and r.json()["registered"] == 5
    sid = r.json()["study_id"]
    r2 = client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"t": 2.0, "w": 12.0, "s": 10 ** 400}]}, headers=admin)
    assert r2.status_code == 200
    r3 = _import(client, admin, pid, rows=_rows(6, seed=9), name="고정", fixed={"z": 10 ** 400, "k": 3})
    assert r3.status_code == 200 and r3.json()["fixed"] == {"k": 3}
    bad = "s\ud83d\tb\tc\n1\t2\t3\n2\t3\t5\n3\t5\t8\n"
    payload = json.dumps({"text": bad, "outputs": [{"name": "c"}]})
    assert client.post("/api/tables/preview/text", content=payload, headers={**admin, "content-type": "application/json"}).status_code == 200
    assert client.post(f"/api/projects/{pid}/studies/import/text", content=payload, headers={**admin, "content-type": "application/json"}).status_code == 200
    deep = "[" * 5000 + "]" * 5000
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scripts/a.py", "print(1)")
    r4 = client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", buf.getvalue())}, data={"adapter_config": deep})
    assert r4.status_code == 400 and "중첩" in r4.json()["detail"]


def test_nan_never_stored_in_constraints_or_nested_fixed(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    r = _import(client, admin, pid, constraints=[{"name": "s", "upper": float("nan")}])
    assert r.status_code == 400 and "제약" in r.json()["detail"] and "→" in r.json()["detail"]
    ok = _import(client, admin, pid, fixed={"z": {"a": float("nan"), "b": 1}, "q": float("inf")}, name="중첩")
    assert ok.status_code == 200 and ok.json()["fixed"] == {"z": {"b": 1}}
    created = client.post(f"/api/projects/{pid}/studies", content=json.dumps({"name": "c", "variables": [{"name": "x", "lower": 0, "upper": 1}], "constraints": [{"name": "s", "upper": float("inf")}]}),
                          headers={**admin, "content-type": "application/json"})
    assert created.status_code == 400


def test_stale_flow_id_is_a_reason_and_leaves_nothing_behind(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    before = len(client.get(f"/api/projects/{pid}/studies", headers=admin).json())
    r = _import(client, admin, pid, flow_id=99999)
    assert r.status_code == 400 and "작업 흐름" in r.json()["detail"] and "→" in r.json()["detail"]
    assert len(client.get(f"/api/projects/{pid}/studies", headers=admin).json()) == before
    sid = _import(client, admin, pid).json()["study_id"]
    assert client.post(f"/api/studies/{sid}/cases/import", json={"rows": _rows(2, seed=8), "flow_id": 99999}, headers=admin).status_code == 400
    assert client.post(f"/api/studies/{sid}/train", json={"flow_id": 99999}, headers=admin).status_code == 400
    assert client.post(f"/api/studies/{sid}/train/start", json={"flow_id": 99999}, headers=admin).status_code == 400


def test_fixed_value_rows_say_what_to_do(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    text = "thick\tt\tw\ts\n" + "".join(f"3\t{i}.5\t{i + 10}\t{i * 2 + (i % 3)}\n" for i in range(1, 9))
    sid = _paste(client, admin, pid, text, ["s"]).json()["study_id"]
    rows = [{"thick": 5, "t": 2.2, "w": 12, "s": 9.0}, {"thick": 5, "t": 3.3, "w": 13, "s": 8.0}]
    rep = client.post(f"/api/studies/{sid}/cases/import", json={"rows": rows}, headers=admin).json()
    assert rep["registered"] == 0 and "→" in rep["excluded"][0]["reason"] and any("모두 뺐습니다" in w for w in rep["warnings"])


# ---------- 동시에 온 표
def _parallel(fn, n):
    out, gate = [], threading.Barrier(n)

    def run(i):
        gate.wait()
        out.append(fn(i))
    ts = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    return out


def test_same_rows_at_the_same_time_are_not_duplicated(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    sid = _import(client, admin, pid).json()["study_id"]
    before = len(client.get(f"/api/studies/{sid}/cases", headers=admin).json())
    rows = _rows(5, seed=44)
    got = _parallel(lambda i: client.post(f"/api/studies/{sid}/cases/import", json={"rows": rows}, headers=admin), 6)
    assert all(r.status_code == 200 for r in got)
    assert len(client.get(f"/api/studies/{sid}/cases", headers=admin).json()) == before + 5
    assert sum(r.json()["registered"] for r in got) == 5


def test_simultaneous_range_widening_keeps_every_widening(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    res = _import(client, admin, pid).json()
    sid, version = res["study_id"], client.get(f"/api/studies/{res['study_id']}", headers=admin).json()["study"]["version"]
    got = _parallel(lambda i: client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"t": 100.0 + i * 10, "w": -100.0 - i * 10, "s": 1.0 + i}]}, headers=admin), 6)
    assert all(r.status_code == 200 for r in got)
    st = client.get(f"/api/studies/{sid}", headers=admin).json()["study"]
    by = {v["name"]: v for v in st["variables"]}
    assert by["t"]["upper"] == 150 and by["w"]["lower"] == -150 and st["version"] == version + 6


# ---------- DOE 계획표가 엑셀을 거쳐 돌아올 때
def _doe_study(client, h, pid, n=8):  # noqa: F811
    sid = client.post(f"/api/projects/{pid}/studies", json={"name": "계획", "variables": [{"name": "x", "lower": 0, "upper": 10}, {"name": "z", "lower": 100, "upper": 200}],
                                                             "objectives": [{"name": "y", "goal": "min"}], "adapter": "script", "adapter_config": {}}, headers=h).json()["id"]
    return sid, client.post(f"/api/studies/{sid}/doe", json={"n": n, "seed": 5}, headers=h).json()


@pytest.mark.parametrize("digits,with_id", [(12, True), (10, True), (6, True), (4, True), (4, False), (6, False)])
def test_doe_fill_back_survives_reduced_digits(client, digits, with_id):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    sid, cases = _doe_study(client, admin, pid)
    head = ("case_id\tstatus\t" if with_id else "") + "x\tz\ty\n"
    lines = "".join((f"{c['id']}\tpending\t" if with_id else "") + f"{c['params']['x']:.{digits}g}\t{c['params']['z']:.{digits}g}\t{i + 1.5}\n" for i, c in enumerate(cases[:4]))
    rep = client.post(f"/api/studies/{sid}/cases/import/text", json={"text": head + lines}, headers=admin).json()
    assert rep["filled_pending"] == 4 and rep["registered"] == 0
    assert not any("case_id" in w and "쓰지 않았습니다" in w for w in rep["warnings"])
    now = client.get(f"/api/studies/{sid}/cases", headers=admin).json()
    assert sum(c["status"] == "pending" for c in now) == 4 and len(now) == 8
    # 입력값은 계획의 정확한 값을 지킨다
    assert {c["id"]: c["params"] for c in now} == {c["id"]: c["params"] for c in cases}
    # 같은 표를 다시 넣어도 늘지 않는다
    again = client.post(f"/api/studies/{sid}/cases/import/text", json={"text": head + lines}, headers=admin).json()
    assert again["registered"] == 0 and again["duplicates"] == 4


def test_doe_row_far_from_every_point_stays_a_new_row_and_says_so(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    sid, _ = _doe_study(client, admin, pid)
    rep = client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"x": 5.5, "z": 150.5, "y": 1.0}]}, headers=admin).json()
    assert rep["registered"] == 1 and rep["filled_pending"] == 0 and any("case_id" in w for w in rep["warnings"])


# ---------- 결과 폴더
def _case_dir(root, name, params, results, enc="utf-8"):
    d = root / name
    d.mkdir()
    (d / "params.json").write_text(params, encoding=enc)
    (d / "results.json").write_text(results, encoding=enc)


def test_scan_reads_korean_windows_encodings_and_survives_bad_json(tmp_path):
    _case_dir(tmp_path, "cp", '{"두께": 3.0}', '{"s": 1.5}', "cp949")
    _case_dir(tmp_path, "u16", '{"두께": 4.0}', '{"s": 2.5}', "utf-16")
    _case_dir(tmp_path, "deep", '{"두께": 5.0}', "[" * 200000)
    _case_dir(tmp_path, "huge", '{"두께": 6.0}', '{"s": ' + "9" * 400 + "}")
    scan = result_scan.scan_folder(tmp_path)
    by = {c["label"]: c for c in scan["cases"]}
    assert by["cp"]["ready"] and by["u16"]["ready"] and by["cp"]["params"] == {"두께": 3.0}
    assert not by["deep"]["ready"] and "results.json" in by["deep"]["missing"]
    assert not by["huge"]["ready"]


def test_scan_never_marks_failed_cases_ready(tmp_path):
    _case_dir(tmp_path, "a", '{"x": 1}', '{"status": "failed", "s": 0.0, "error": "diverged"}')
    _case_dir(tmp_path, "b", '{"x": 2}', '{"s": 3, "converged": false}')
    _case_dir(tmp_path, "c", '{"x": 3}', '{"s": 4}')
    d = tmp_path / "e"
    d.mkdir()
    (d / "case.json").write_text('{"status": "failed", "error": "solver diverged", "params": {"x": 4}, "results": {"s": 1000000.0}}', encoding="utf-8")
    d = tmp_path / "f"
    d.mkdir()
    (d / "case.json").write_text('{"status": "complete", "params": {"x": 5}, "results": {"s": 5.0}}', encoding="utf-8")
    scan = result_scan.scan_folder(tmp_path)
    by = {c["label"]: c for c in scan["cases"]}
    assert scan["failed"] == 3 and scan["ready"] == 2
    assert not by["a"]["ready"] and "diverged" in by["a"]["missing"] and not by["b"]["ready"] and not by["e"]["ready"] and by["c"]["ready"] and by["f"]["ready"]


def test_folder_name_cannot_produce_inf(tmp_path):
    (tmp_path / "t=1e999_w=3").mkdir()
    (tmp_path / "t=1e999_w=3" / "results.json").write_text('{"s": 3}', encoding="utf-8")
    scan = result_scan.scan_folder(tmp_path)
    assert scan["cases"][0]["params"] == {"w": 3.0}
    json.dumps(result_scan.brief(scan), allow_nan=False)


# ---------- 다시 돌리기·꺼진 PC
def _solved(client, h, pid, n=4, adapter="mock"):  # noqa: F811
    cfg = {"function": "branin", "delay_sec": 0} if adapter == "mock" else {"tool": "python", "script": "scripts/run.py"}
    sid = client.post(f"/api/projects/{pid}/studies", json={"name": f"풀이{n}", "variables": [{"name": "x1", "lower": -5, "upper": 10}, {"name": "x2", "lower": 0, "upper": 15}],
                                                             "objectives": [{"name": "f", "goal": "min"}], "adapter": adapter, "adapter_config": cfg}, headers=h).json()["id"]
    cases = client.post(f"/api/studies/{sid}/doe", json={"n": n, "seed": 2}, headers=h).json()
    return sid, [c["id"] for c in cases]


def _finish(cid, value=1.0, sender=None):
    service.on_case_status({"case_id": cid, "status": "running", "run_dir": f"D:/runs/{cid}"}, sender)
    service.on_case_status({"case_id": cid, "status": "complete", "results": {"f": value}}, sender)


def test_failed_rerun_keeps_previous_results(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    member, mu = _auth(client, "rerun_member", admin)
    pid = _project(client, admin)
    client.put(f"/api/projects/{pid}/members/{mu['id']}", json={"role": "member"}, headers=admin)
    sid, ids = _solved(client, admin, pid, adapter="script")
    owner, other = _Conn(me["id"]), _Conn(mu["id"])
    registry.conns[me["id"]] = owner
    registry.conns[mu["id"]] = other
    try:
        assert client.post(f"/api/studies/{sid}/run", json={}, headers=admin).json()["queued"] == 4
        for i, cid in enumerate(ids):
            _finish(cid, 10.0 + i, me["id"])
        assert client.post(f"/api/studies/{sid}/run", json={"case_ids": ids[:2]}, headers=member).json()["queued"] == 2
        service.on_case_status({"case_id": ids[0], "status": "failed", "error": "스크립트가 없습니다", "results": {}}, mu["id"])
        service.on_case_status({"case_id": ids[1], "status": "complete", "results": {"f": 99.0}}, mu["id"])
    finally:
        registry.conns.pop(me["id"], None)
        registry.conns.pop(mu["id"], None)
    cases = {c["id"]: c for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json()}
    kept = cases[ids[0]]
    assert kept["status"] == "complete" and kept["results"] == {"f": 10.0} and kept["run_by"] == me["id"] and kept["run_dir"] == f"D:/runs/{ids[0]}"
    assert "이전 결과" in kept["error"] and "스크립트가 없습니다" in kept["error"]
    assert cases[ids[1]]["results"] == {"f": 99.0} and cases[ids[1]]["run_by"] == mu["id"]
    assert client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()["n_train"] == 4


def test_cases_on_a_dead_pc_are_settled_only_by_who_ran_them(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    member, mu = _auth(client, "dead_member", admin)
    pid = _project(client, admin)
    client.put(f"/api/projects/{pid}/members/{mu['id']}", json={"role": "member"}, headers=admin)
    sid, ids = _solved(client, admin, pid, n=3, adapter="script")
    registry.conns[me["id"]] = _Conn(me["id"])
    assert client.post(f"/api/studies/{sid}/run", json={}, headers=admin).json()["queued"] == 3
    service.on_case_status({"case_id": ids[0], "status": "running"}, me["id"])
    registry.conns.pop(me["id"])  # 내 PC가 꺼졌다
    st = client.get(f"/api/studies/{sid}", headers=member).json()
    assert st["stalled"] == 3 and st["stalled_by"] and st["running"] is False
    # 쉬운 말로 이유를 듣는다
    run = client.post(f"/api/studies/{sid}/run", json={}, headers=admin).json()
    assert run["queued"] == 0 and "꺼져" in run["note"] and "→" in run["note"]
    blocked = client.put(f"/api/studies/{sid}/adapter", json={"adapter": "mock", "adapter_config": {"function": "branin"}}, headers=member)
    assert blocked.status_code == 409 and "중지" in blocked.json()["detail"]
    # 멤버는 멈추지 못한다. 끊김은 PC가 꺼졌다는 뜻이 아니어서 솔버가 돌고 있을 수 있다
    denied = client.post(f"/api/studies/{sid}/stop", json={}, headers=member)
    assert denied.status_code == 403 and "내 PC로 복사해서 하기" in denied.json()["detail"]
    assert client.get(f"/api/studies/{sid}", headers=member).json()["stalled"] == 3
    # 실행한 사람은 PC 없이 정리한다
    r = client.post(f"/api/studies/{sid}/stop", json={}, headers=admin).json()
    assert r["released"] == 3 and r["stopping"] == 0 and "꺼져" in r["note"]
    st = client.get(f"/api/studies/{sid}", headers=member).json()
    assert st["counts"] == {"cancelled": 3} and not st["running"] and not st["stalled"]
    assert client.put(f"/api/studies/{sid}/adapter", json={"adapter": "mock", "adapter_config": {"function": "branin"}}, headers=member).status_code == 200


def test_reconnecting_agent_settles_cases_it_no_longer_runs(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    member, mu = _auth(client, "reco_member", admin)
    pid = _project(client, admin)
    client.put(f"/api/projects/{pid}/members/{mu['id']}", json={"role": "member"}, headers=admin)
    sid, ids = _solved(client, admin, pid, n=4, adapter="script")
    conn = _Conn(me["id"])
    registry.conns[me["id"]] = conn
    try:
        client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
        service.on_case_status({"case_id": ids[0], "status": "running"}, me["id"])
        # Agent가 죽었다 살아났다: 케이스 0번만 아직 돈다고 알린다
        service.reconcile_cases(conn, [ids[0]])
        st = client.get(f"/api/studies/{sid}", headers=admin).json()
        assert st["counts"] == {"running": 1, "cancelled": 3} and st["running"]
        # 끝난 것으로 쳤는데 늦게 온 진짜 결과는 받는다
        service.on_case_status({"case_id": ids[1], "status": "complete", "results": {"f": 2.0}}, me["id"])
        # 멤버가 넘겨받은 케이스에 옛 PC의 늦은 보고가 덮어쓰지 않는다
        registry.conns[mu["id"]] = _Conn(mu["id"])
        assert client.post(f"/api/studies/{sid}/run", json={"case_ids": [ids[2]]}, headers=member).json()["queued"] == 1
        service.on_case_status({"case_id": ids[2], "status": "complete", "results": {"f": 3.0}}, me["id"])
        now = {c["id"]: c for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json()}
        assert now[ids[1]]["status"] == "complete" and now[ids[2]]["status"] == "queued" and now[ids[2]]["run_by"] == mu["id"]
        # 그 PC에서 아직 도는데 넘어간 케이스는 멈춘다
        service.reconcile_cases(conn, [ids[0], ids[2]])
        assert conn.sent[-1] == {"type": "case.cancel", "case_ids": [ids[2]], "extract_ids": []}
    finally:
        registry.conns.pop(me["id"], None)
        registry.conns.pop(mu["id"], None)


# ---------- 승인은 서버가 확인한다
def _bundle(**files) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for k, v in (files or {"scripts/run.py": "print(1)"}).items():
            z.writestr(k.replace("__", "/"), v)
    return buf.getvalue()


def _approved_flow(client, h, pid, status="approved"):  # noqa: F811
    f = client.post(f"/api/projects/{pid}/flows", json={"title": "이어받기", "goal": "", "nodes": [{"id": "solve", "label": "해석", "type": "solve"}], "edges": []}, headers=h).json()
    if status != "review":
        f = client.put(f"/api/flows/{f['id']}/status", json={"status": status, "version": f["version"]}, headers=h).json()
    return f


def test_other_members_script_needs_my_approved_flow(client):  # noqa: F811
    admin, me = _auth(client, "admin")
    member, mu = _auth(client, "gate_member", admin)
    pid = _project(client, admin)
    client.put(f"/api/projects/{pid}/members/{mu['id']}", json={"role": "member"}, headers=admin)
    sid, ids = _solved(client, admin, pid, n=3, adapter="script")
    assert client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", _bundle())}).status_code == 200
    registry.conns[mu["id"]] = _Conn(mu["id"])
    registry.conns[me["id"]] = _Conn(me["id"])
    try:
        r = client.post(f"/api/studies/{sid}/run", json={}, headers=member)
        assert r.status_code == 403 and "만든 스크립트" in r.json()["detail"] and "→" in r.json()["detail"]
        assert {c["status"] for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json()} == {"pending"}
        # 다른 사람이 승인한 흐름으로는 안 된다
        other = _approved_flow(client, admin, pid)
        assert client.post(f"/api/studies/{sid}/run", json={"flow_id": other["id"]}, headers=member).status_code == 403
        # 만든 사람은 그대로 돌린다. 같은 내용을 다시 올려 만든 사람이 바뀌지 않는다
        same = client.post(f"/api/studies/{sid}/recipe", headers=member, files={"file": ("r.zip", _bundle())}).json()
        assert same["created_by"] == me["id"]
        assert client.post(f"/api/studies/{sid}/run", json={"flow_id": other["id"]}, headers=member).status_code == 403
        mine = _approved_flow(client, member, pid)
        assert client.post(f"/api/studies/{sid}/run", json={"flow_id": mine["id"]}, headers=member).json()["queued"] == 3
    finally:
        registry.conns.pop(mu["id"], None)
        registry.conns.pop(me["id"], None)


@pytest.mark.parametrize("status", ["rejected", "review"])
def test_study_on_rejected_or_unapproved_flow_does_not_run(client, status):  # noqa: F811
    admin, me = _auth(client, "admin")
    pid = _project(client, admin)
    sid, _ = _solved(client, admin, pid, n=2)
    f = _approved_flow(client, admin, pid, status)
    client.post(f"/api/studies/{sid}/doe", json={"n": 1, "seed": 9, "flow_id": f["id"]}, headers=admin)
    r = client.post(f"/api/studies/{sid}/run", json={}, headers=admin)
    assert r.status_code == 403 and "작업 흐름" in r.json()["detail"] and "→" in r.json()["detail"]


# ---------- 출력마다 값이 있는 행으로
def test_partial_output_trains_on_its_own_rows_only(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    text = "두께\t폭\t응력\t질량\n"
    rng = np.random.default_rng(1)
    for i in range(24):
        t, w = rng.uniform(1, 5), rng.uniform(10, 40)
        text += f"{t:.4f}\t{w:.4f}\t{_f(t, w):.5f}\t{'#N/A' if i % 3 == 0 else f'{t * w * 0.1:.5f}'}\n"
    sid = _paste(client, admin, pid, text, ["응력", "질량"]).json()["study_id"]
    m = client.post(f"/api/studies/{sid}/train", json={"outputs": ["응력", "질량"]}, headers=admin).json()
    assert m["n_train"] == 24 and m["quality"]["rows_by_output"] == {"응력": 24, "질량": 16}
    assert any("그 출력의 학습에서만" in w and "질량 16행" in w for w in m["quality"]["warnings"])
    assert client.post(f"/api/models/{m['id']}/predict", json={"params": {"두께": 2, "폭": 20}}, headers=admin).status_code == 200
    # 재학습에 출력이 늘어도 기존 출력의 행이 줄지 않는다
    pid2 = _project(client, admin)
    base = _import(client, admin, pid2).json()["study_id"]
    v1 = client.post(f"/api/studies/{base}/train", json={}, headers=admin).json()
    assert v1["n_train"] == 20
    rows = _rows()[:12]  # 같은 행에 출력 열만 더한다
    for r in rows:
        r["mass"] = r["t"] * r["w"]
    client.post(f"/api/studies/{base}/cases/import", json={"rows": rows, "outputs": [{"name": "mass"}]}, headers=admin)
    v2 = client.post(f"/api/studies/{base}/train", json={"outputs": ["s", "mass"]}, headers=admin).json()
    assert v2["quality"]["rows_by_output"]["s"] == 20 and v2["quality"]["rows_by_output"]["mass"] == 12
    # 값이 3개가 안 되는 출력은 빠지고 이유가 남는다
    assert Surrogate(["x"], ["a"]).fit(np.arange(6.0).reshape(-1, 1), {"a": np.array([1.0, np.nan, 2.0, 3.0, np.nan, 5.0])})["a"]["r2"] is not None


def test_many_inputs_still_learn(client):  # noqa: F811
    rng = np.random.default_rng(0)
    X = rng.uniform(0, 10, (150, 55))
    y = 100 + sum((i + 1) * X[:, i] for i in range(5))
    names = [f"x{i}" for i in range(55)]
    metrics = Surrogate(names, ["y"]).fit(X, {"y": y})
    assert metrics["y"]["r2"] > 0.9  # 예전에는 약 -0.01: 평균만 내는 모델이었다


def test_low_quality_with_many_inputs_gives_a_prescription_that_works():
    q = service.assess_quality({"y": {"r2": 0.1, "rmse": 1.0}}, 200, 59, "data", [])
    text = " ".join(q["warnings"])
    assert "입력이 59개" in text and "행을 더해도" in text


# ---------- 버전과 범위
def test_adding_rows_does_not_widen_the_deployed_simulator(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    sid = _import(client, admin, pid).json()["study_id"]
    m = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    client.post(f"/api/models/{m['id']}/deploy", headers=admin)
    share = client.post(f"/api/models/{m['id']}/shares", json={"note": ""}, headers=admin).json()["token"]
    before = {v["name"]: v["upper"] for v in client.get(f"/api/shares/{share}", headers=admin).json()["variables"]}
    rep = client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"t": 50.0, "w": 400.0, "s": 1.0}]}, headers=admin).json()
    assert rep["range_changes"] and any("새 범위" in w for w in rep["warnings"])
    assert {v["name"]: v["upper"] for v in client.get(f"/api/shares/{share}", headers=admin).json()["variables"]} == before
    assert {v["name"]: v["upper"] for v in client.get(f"/api/models/{m['id']}/spec", headers=admin).json()["variables"]} == before
    v2 = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert {v["name"]: v["upper"] for v in client.get(f"/api/models/{v2['id']}/spec", headers=admin).json()["variables"]} == {"t": 50.0, "w": 400.0}


def test_candidates_use_the_version_the_user_rolled_back_to(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    sid = _import(client, admin, pid, objectives=[{"name": "s", "goal": "min"}]).json()["study_id"]
    v1 = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    client.post(f"/api/studies/{sid}/cases/import", json={"rows": _rows(5, seed=11)}, headers=admin)
    v2 = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    client.post(f"/api/models/{v2['id']}/deploy", headers=admin)
    assert client.post(f"/api/studies/{sid}/candidates", json={"mode": "bo", "n": 1}, headers=admin).json()["model_id"] == v2["id"]
    client.post(f"/api/models/{v1['id']}/deploy", headers=admin)
    r = client.post(f"/api/studies/{sid}/candidates", json={"mode": "bo", "n": 1}, headers=admin).json()
    assert r["model_id"] == v1["id"] and r["model_version"] == 1 and "v1" in r["note"]
    # 되돌린 뒤 새로 학습하면 그 새 모델이다
    client.post(f"/api/studies/{sid}/cases/import", json={"rows": _rows(5, seed=12)}, headers=admin)
    v3 = client.post(f"/api/studies/{sid}/train", json={}, headers=admin).json()
    assert client.post(f"/api/studies/{sid}/candidates", json={"mode": "bo", "n": 1}, headers=admin).json()["model_id"] == v3["id"]


def test_same_study_name_does_not_overwrite_result_cards(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    body = {"name": "브래킷", "variables": [{"name": "x", "lower": 0, "upper": 1}], "adapter": "mock", "adapter_config": {"function": "branin"}}
    a = client.post(f"/api/projects/{pid}/studies", json=body, headers=admin).json()
    b = client.post(f"/api/projects/{pid}/studies", json=body, headers=admin).json()
    assert a["name"] == "브래킷" and b["name"] == "브래킷 (2)"


def test_wrong_row_can_be_deleted(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    rows = _rows(8)
    rep = _import(client, admin, pid, rows=rows).json()
    sid = rep["study_id"]
    bad = client.post(f"/api/studies/{sid}/cases/import", json={"rows": [{**rows[0], "s": rows[0]["s"] * 10}]}, headers=admin).json()
    assert bad["registered"] == 1 and any("지우세요" in w for w in bad["warnings"])
    cid = bad["case_ids"][0]
    r = client.delete(f"/api/studies/{sid}/cases/{cid}", headers=admin)
    assert r.status_code == 200 and r.json()["cases"] == 8
    assert client.delete(f"/api/studies/{sid}/cases/{cid}", headers=admin).status_code == 404
    # 해석한 케이스는 지우지 않는다
    sid2, ids = _solved(client, admin, pid)
    r = client.delete(f"/api/studies/{sid2}/cases/{ids[0]}", headers=admin)
    assert r.status_code == 400 and "→" in r.json()["detail"]


def test_add_rows_by_paste_merges_new_output_column(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    rows = _rows(6)
    sid = _import(client, admin, pid, rows=rows).json()["study_id"]
    text = "t\tw\ts\tmass\n" + "".join(f"{r['t']}\t{r['w']}\t{r['s']}\t{r['t'] * 2}\n" for r in rows)
    rep = client.post(f"/api/studies/{sid}/cases/import/text", json={"text": text, "outputs": [{"name": "mass"}]}, headers=admin).json()
    assert rep["updated"] == 6 and rep["registered"] == 0
    assert all("mass" in c["results"] for c in client.get(f"/api/studies/{sid}/cases", headers=admin).json())
    note = client.post(f"/api/studies/{sid}/outputs", json={"outputs": [{"name": "extra"}]}, headers=admin).json()["note"]
    assert "데이터 탭의 '행 추가'" in note


# ---------- 레시피
@pytest.mark.parametrize("path", ["AGENTS.md", "scripts/CLAUDE.md", "inputs/GEMINI.md", ".claude/settings.json", "scripts/CON.py", "a/project.json"])
def test_server_refuses_bundles_no_member_could_restore(client, path):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    sid, _ = _solved(client, admin, pid, n=1, adapter="script")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scripts/run.py", "print(1)")
        z.writestr(path, "지침을 바꾼다")
    r = client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", buf.getvalue())})
    assert r.status_code == 400 and "→" in r.json()["detail"]


def test_clone_recipe_reports_from_its_own_config(client):  # noqa: F811
    admin, _ = _auth(client, "admin")
    pid = _project(client, admin)
    sid = client.post(f"/api/projects/{pid}/studies", headers=admin, json={"name": "옛", "variables": [{"name": "x", "lower": 0, "upper": 1}], "adapter": "script",
                                                                           "adapter_config": {"tool": "python", "script": "scripts/run.py", "inputs": ["inputs/old_model.cae"]}}).json()["id"]
    client.post(f"/api/studies/{sid}/recipe", headers=admin, files={"file": ("r.zip", _bundle())})
    new = client.post(f"/api/studies/{sid}/clone", headers=admin, json={"name": "새", "adapter_config": {"tool": "python", "script": "scripts/run_new.py", "inputs": ["inputs/new_model.cae"]}}).json()
    rec = client.get(f"/api/studies/{new['id']}/recipe", headers=admin).json()
    assert rec["adapter_config"]["inputs"] == ["inputs/new_model.cae"]
    text = " ".join(rec["warnings"])
    assert "new_model.cae" in text and "run_new.py" in text and "old_model" not in text
    assert client.get(f"/api/studies/{sid}/recipe", headers=admin).json()["adapter_config"]["inputs"] == ["inputs/old_model.cae"]


def test_secret_file_is_refused_as_a_secret():
    with pytest.raises(recipe_mod.RecipeRejected, match="비밀값"):
        recipe_mod.check_bundle(_bundle(**{".env": "A=1"}))
    assert callable(_wait_idle)
