"""어느 시점에서든 진입: MCP 도구를 서버와 한 프로세스에서 직접 부른다. 실제 CLI·실제 CAD 없이, PC Agent도 연결하지 않는다.
시나리오 번호는 docs/entry-points.md의 E0~E16과 맞춘다."""
import ast
import asyncio
import hashlib
import importlib.util
import io
import json
import re
import sys
import time
import types
import zipfile
from pathlib import Path

import numpy as np
import pytest

from server.tests.test_api import _auth, client  # noqa: F401 — 같은 서버 fixture 재사용, 서버 코드보다 먼저 불러온다

from fastapi.testclient import TestClient  # noqa: E402

from agent.dds_agent import demo_agent  # noqa: E402
from cae_adapters import result_scan  # noqa: E402
from server.app.main import app  # noqa: E402
from server.app.study import service  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp_servers"))
import _common  # noqa: E402
import dds_flow  # noqa: E402
import dds_study  # noqa: E402

INSTRUCTIONS = ROOT / "agent-context" / "INSTRUCTIONS.md"
SKILLS = ROOT / "agent-context" / "skills"


class _Env:
    def __init__(self, c, headers, pid, workspace):
        self.c, self.h, self.pid, self.ws = c, headers, pid, workspace

    def project_dir(self) -> Path:
        return _common.settings.project_dir(self.pid)

    def study(self, sid: int) -> dict:
        return self.c.get(f"/api/studies/{sid}", headers=self.h).json()

    def cases(self, sid: int) -> list[dict]:
        return self.c.get(f"/api/studies/{sid}/cases", headers=self.h).json()


@pytest.fixture()
def env(client, tmp_path, monkeypatch):  # noqa: F811
    admin, _ = _auth(client, "admin")
    name = f"mcp{tmp_path.name[-12:].replace('-', '_')}"
    h, _ = _auth(client, name, admin)
    token = client.post("/api/tokens", json={"name": "내 PC"}, headers=h).json()["token"]
    pid = client.post("/api/projects", json={"name": "진입"}, headers=h).json()["id"]
    monkeypatch.setattr(_common.api, "client", TestClient(app, headers={"Authorization": f"Bearer {token}"}))
    monkeypatch.setattr(_common.settings, "workspace_dir", tmp_path / "ws")
    monkeypatch.setenv("DDS_PROJECT_ID", str(pid))
    monkeypatch.delenv("DDS_MCP_WAIT_LIMIT_SEC", raising=False)
    # 폴링 2초를 줄인다. time.sleep 자체를 바꾸면 서버 스레드까지 영향받는다
    monkeypatch.setattr(dds_study, "time", types.SimpleNamespace(time=time.time, strftime=time.strftime, sleep=lambda _s: time.sleep(0.02)))
    with dds_study._JOBS_LOCK:
        dds_study._JOBS.clear()
    return _Env(client, h, pid, tmp_path / "ws")


def _f(t, w):
    return 1000.0 / (t * w) + 0.5 * t


def _rows(n=24, seed=3, lo=(1, 10), hi=(5, 40)):
    rng = np.random.default_rng(seed)
    return [(float(t), float(w), _f(float(t), float(w))) for t, w in zip(rng.uniform(lo[0], hi[0], n), rng.uniform(lo[1], hi[1], n))]


def _write_csv(path: Path, rows, head="thickness [mm],width [mm],stress [MPa]", extra=None) -> Path:
    lines = [head + (f",{extra[0]}" if extra else "")]
    for i, r in enumerate(rows):
        lines.append(",".join(str(x) for x in r) + (f",{extra[1](i)}" if extra else ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _xlsx(path: Path, rows):
    shared = ["두께", "응력"]
    body = '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
    for i, (a, b) in enumerate(rows, 2):
        body += f'<row r="{i}"><c r="A{i}"><v>{a}</v></c><c r="B{i}"><v>{b}</v></c></row>'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="m"><sheetData>{body}</sheetData></worksheet>')
        z.writestr("xl/workbook.xml", '<workbook xmlns="m" xmlns:r="r"><sheets><sheet name="결과" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="p"><Relationship Id="rId1" Type="x" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", '<sst xmlns="m">' + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
    return path


def _tree(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


def _case_folder(root: Path, name: str, params=None, results=None, npz=None) -> Path:
    d = root / name
    d.mkdir(parents=True)
    if params is not None:
        (d / "params.json").write_text(json.dumps(params), encoding="utf-8")
    if results is not None:
        (d / "results.json").write_text(json.dumps(results), encoding="utf-8")
    if npz is not None:
        (d / "fields").mkdir()
        np.savez_compressed(d / "fields" / "mises.npz", **npz)
    return d


# ---------- E1: 표 → Simulator ----------
def test_e1_table_to_simulator_without_agent_or_solver(env, tmp_path):
    src = _write_csv(tmp_path / "표.csv", _rows())
    info = dds_study.inspect_table(str(src))
    assert info["inputs"] == ["thickness", "width"] and info["outputs"] == ["stress"] and info["n_rows"] == 24
    assert env.c.get(f"/api/projects/{env.pid}/studies", headers=env.h).json() == []  # 분석만으로는 저장하지 않는다

    rep = dds_study.import_table(str(src))
    assert rep["registered"] == 24 and rep["excluded_count"] == 0 and "case_ids" not in rep and "cases" not in rep  # 행 수만큼 커지는 값은 AI에게 주지 않는다
    sid = rep["study_id"]
    st = env.study(sid)
    assert st["study"]["adapter"] == "data" and st["data_source"]["kind"] == "table" and st["counts"] == {"complete": 24}

    m = dds_study.train_surrogate(sid)
    assert m["status"] == "done" and m["quality"] in ("good", "fair", "poor") and m["metrics"]["stress"]["r2"] > 0.5
    d = dds_study.deploy_model(m["model_id"])
    assert d["deployed"] and d["simulator_path"].endswith(str(m["model_id"]))

    one = dds_study.predict(m["model_id"], {"thickness": 3.0, "width": 20.0})
    many = dds_study.predict_batch(m["model_id"], [{"thickness": 3.0, "width": 20.0}, {"thickness": 2.0, "width": 30.0}])
    assert len(many["mean"]["stress"]) == 2 and abs(many["mean"]["stress"][0] - one["mean"]["stress"]) < 1e-6
    assert "2000점" in _raises(dds_study.predict_batch, m["model_id"], [{"thickness": 1.0, "width": 1.0}] * 2001)
    assert env.cases(sid)[0]["source"] == "import"


def _raises(fn, *a, **kw) -> str:
    with pytest.raises((ValueError, _common.ServerError)) as e:
        fn(*a, **kw)
    return str(e.value)


def test_e1b_attachment_path_xlsx_and_korean(env):
    d = env.project_dir() / "inputs"
    d.mkdir(parents=True, exist_ok=True)
    _xlsx(d / "결과표.xlsx", [(1.0, 9.0), (2.0, 4.0), (3.0, 1.0), (4.0, 0.5)])
    assert dds_study.inspect_table("inputs/결과표.xlsx")["sheet"] == "결과"  # 첨부는 프로젝트 폴더 기준 경로로 온다
    rep = dds_study.import_table("inputs/결과표.xlsx", inputs=["두께"], outputs=["응력"])
    assert rep["registered"] == 4 and rep["study_name"] == "결과표"
    (d / "cp.csv").write_bytes("두께(mm),응력 (MPa)\n1,5\n2,4\n3,2\n".encode("cp949"))
    info = dds_study.inspect_table("inputs/cp.csv")
    assert {c["name"]: c["unit"] for c in info["columns"]} == {"두께": "mm", "응력": "MPa"}


def test_e2b_outputs_in_a_separate_file(env, tmp_path):
    rows = _rows(10)
    a = tmp_path / "in.csv"
    a.write_text("case,thickness,width\n" + "\n".join(f"C{i},{r[0]},{r[1]}" for i, r in enumerate(rows)) + "\n", encoding="utf-8")
    b = tmp_path / "out.csv"
    b.write_text("case,stress\n" + "\n".join(f"C{i},{r[2]}" for i, r in enumerate(rows) if i != 3) + "\n", encoding="utf-8")
    info = dds_study.inspect_table(str(a), outputs_path=str(b), key="case")
    assert info["join"]["matched"] == 9
    rep = dds_study.import_table(str(a), inputs=["thickness", "width"], outputs=["stress"], outputs_path=str(b), key="case")
    assert rep["registered"] == 9 and rep["join"]["only_left"] == ["C3"]


def test_e3_add_rows_new_version_and_share_link_follows(env, tmp_path):
    rep = dds_study.import_table(str(_write_csv(tmp_path / "a.csv", _rows(16))))
    sid = rep["study_id"]
    m1 = dds_study.train_surrogate(sid)
    dds_study.deploy_model(m1["model_id"])
    share = env.c.post(f"/api/models/{m1['model_id']}/shares", json={}, headers=env.h).json()["token"]

    # 범위 밖 값이 든 행을 더한다
    more = dds_study.import_table(str(_write_csv(tmp_path / "b.csv", _rows(8, seed=9, lo=(1, 10), hi=(8, 60)))), study_id=sid)
    assert more["registered"] == 8 and more["range_changes"]
    m2 = dds_study.train_surrogate(sid)
    assert m2["status"] == "done" and m2["model_id"] != m1["model_id"] and m2["version"] == 2 and m2["n_train"] == 24
    dds_study.deploy_model(m2["model_id"])
    assert env.c.get(f"/api/shares/{share}", headers=env.h).json()["model"]["id"] == m2["model_id"]

    # 되돌리기와 버전 목록
    assert [x["version"] for x in dds_study.list_models(study_id=sid)["models"]] == [2, 1]
    assert dds_study.deploy_model(m1["model_id"])["deployed"]
    assert next(x for x in dds_study.list_models(study_id=sid)["models"] if x["deployed"])["model_id"] == m1["model_id"]


def test_e3b_same_table_twice_makes_no_duplicates(env, tmp_path):
    src = _write_csv(tmp_path / "a.csv", _rows(12))
    a = dds_study.import_table(str(src))
    b = dds_study.import_table(str(src))
    assert b["study_id"] == a["study_id"] and b["reused"] and b["registered"] == 0
    assert len(env.cases(a["study_id"])) == 12
    c = dds_study.import_table(str(src), study_id=a["study_id"])
    assert c["registered"] == 0 and c["duplicates"] == 12 and len(env.cases(a["study_id"])) == 12
    assert len(env.c.get(f"/api/projects/{env.pid}/studies", headers=env.h).json()) == 1


def test_partial_mapping_keeps_no_column_silently(env, tmp_path):
    rows = _rows(10)
    src = _write_csv(tmp_path / "a.csv", rows, extra=("material", lambda i: "steel" if i % 2 else "alu"))
    # 출력만 지정: 나머지 숫자 열이 입력이 되고, 글자 열은 이유와 함께 빠진다
    rep = dds_study.import_table(str(src), outputs=["stress"])
    assert [v["name"] for v in rep["variables"]] == ["thickness", "width"]
    assert rep["removed_columns"][0]["name"] == "material" and any("material" in w for w in rep["warnings"])
    # 입력만 지정: 출력처럼 보이는 열이 출력이 된다
    src2 = _write_csv(tmp_path / "b.csv", _rows(10, seed=8))
    rep = dds_study.import_table(str(src2), inputs=["thickness", "width"])
    assert [o["name"] for o in rep["outputs"]] == ["stress"]
    # 둘 다 지정: 남는 숫자 열을 알린다
    src3 = _write_csv(tmp_path / "c.csv", _rows(10, seed=11))
    rep = dds_study.import_table(str(src3), inputs=["thickness"], outputs=["stress"])
    assert rep["unused_columns"] == ["width"] and any("width" in w for w in rep["warnings"])
    # 출력만 고르면 출력처럼 보이는 열도 입력이다(화면 붙여넣기와 같은 규칙). 대신 그 사실을 알린다
    src4 = tmp_path / "d.csv"
    src4.write_text("thickness,width,stress,mass\n" + "\n".join(f"{t},{w},{s},{s * 2 + t}" for t, w, s in _rows(10, seed=13)) + "\n", encoding="utf-8")
    rep = dds_study.import_table(str(src4), outputs=["stress"])
    assert [v["name"] for v in rep["variables"]] == ["thickness", "width", "mass"] and "unused_columns" not in rep
    assert any("결과처럼 보이는 열" in w and "mass" in w for w in rep["warnings"])
    assert "표에 없는 열" in _raises(dds_study.import_table, str(src4), outputs=["없는열"])


def test_e5_bad_tables_say_why_and_what_to_do(env, tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    assert "비어" in _raises(dds_study.inspect_table, str(empty))
    assert "폴더입니다" in _raises(dds_study.import_table, str(tmp_path))
    assert "찾지 못했습니다" in _raises(dds_study.import_table, str(tmp_path / "없음.csv"))
    pkl = tmp_path / "model.joblib"
    pkl.write_bytes(b"\x80\x04.")
    assert "입력·출력 표" in _raises(dds_study.import_table, str(pkl))  # 외부 모델은 열지 않고 표를 달라고 한다
    few = _write_csv(tmp_path / "few.csv", _rows(2))
    assert "→" in _raises(dds_study.import_table, str(few))
    nan = tmp_path / "nan.csv"
    nan.write_text("t,s\n1,2\n2,NaN\n3,abc\n4,5\n5,6\n6,7\n", encoding="utf-8")
    rep = dds_study.import_table(str(nan))
    assert rep["excluded_count"] == 2 and rep["excluded_examples"][0]["row"] == 3 and set(rep["excluded_by_reason"]) >= {"결측", "숫자 아님"}
    m = dds_study.train_surrogate(rep["study_id"])
    assert m["n_train"] == 4


def test_get_study_does_not_return_every_row(env, tmp_path):
    sid = dds_study.import_table(str(_write_csv(tmp_path / "a.csv", _rows(30))))["study_id"]
    st = dds_study.get_study(sid, include_cases=True, limit=10)
    assert len(st["cases"]) == 10 and st["truncated"] and st["cases_total"] == 30 and st["adapter_config"] == {}
    rest = dds_study.get_study(sid, include_cases=True, limit=10, offset=25)
    assert len(rest["cases"]) == 5 and not rest["truncated"]
    assert st["variables"][0]["name"] == "thickness" and st["data_source"]["rows"] == 30


# ---------- E2: 해석 폴더 → Simulator ----------
@pytest.fixture()
def runs(tmp_path):
    root = tmp_path / "해석 결과"
    for t, w, s in _rows(8):
        _case_folder(root, f"run_{t:.3f}", {"thickness": t, "width": w}, {"stress": s})
    return root


def test_e2_scan_then_import_then_simulator_runs_nothing_and_writes_nothing(env, runs):
    before = _tree(runs)
    scan = dds_study.scan_results(str(runs))
    assert scan["cases_total"] == 8 and scan["ready"] == 8 and scan["inputs"] == ["thickness", "width"] and scan["outputs"] == ["stress"]
    assert scan["file_kinds"][".json"] == 16 and scan["cases"][0]["params_from"] == "params.json"
    rep = dds_study.import_cases(folder=str(runs))
    assert rep["registered"] == 8
    sid = rep["study_id"]
    cases = env.cases(sid)
    assert all(c["status"] == "complete" and c["source"] == "import" and Path(c["run_dir"]).is_absolute() for c in cases)
    assert env.study(sid)["data_source"]["kind"] == "folders"
    m = dds_study.train_surrogate(sid)
    assert dds_study.deploy_model(m["model_id"])["deployed"]
    assert _tree(runs) == before  # 원본에는 아무것도 쓰지 않는다
    again = dds_study.import_cases(folder=str(runs))
    assert again["study_id"] == sid and again["registered"] == 0 and len(env.cases(sid)) == 8


def test_scan_finds_every_kind_of_case_folder(tmp_path):
    root = tmp_path / "r"
    _case_folder(root, "a", {"t": 1.0}, {"s": 2.0})
    _case_folder(root, "t=3.5_w=30", results={"s": 4.0})  # 입력은 폴더 이름에서
    cj = root / "c"
    cj.mkdir()
    (cj / "case.json").write_text(json.dumps({"params": {"t": 2.0}, "results": {"s": 3.0}, "status": "complete"}), encoding="utf-8")
    csv_case = _case_folder(root, "d", {"t": 7.0})
    (csv_case / "summary.csv").write_text("s,note\n5,ok\n", encoding="utf-8")
    kv = _case_folder(root, "e", {"t": 8.0})
    (kv / "results.csv").write_text("name,value\ns,6\nmass,7\n", encoding="utf-8")
    only = root / "job_only"
    only.mkdir()
    (only / "bracket.ODB").write_bytes(b"x")
    dyna = root / "dyna"
    dyna.mkdir()
    (dyna / "d3plot").write_bytes(b"x")
    (dyna / "d3plot01").write_bytes(b"x")
    (root / "표.csv").write_text("t,s\n1,2\n3,4\n", encoding="utf-8")
    deep = root / "x" / "y" / "z" / "w"
    deep.mkdir(parents=True)
    (deep / "params.json").write_text('{"t": 1}', encoding="utf-8")

    scan = result_scan.scan_folder(root)
    by = {c["label"]: c for c in scan["cases"]}
    assert by["a"]["ready"] and by["c"]["params_from"] == "case.json" and by["c"]["results_from"] == "case.json"
    assert by["t=3.5_w=30"]["params"] == {"t": 3.5, "w": 30.0} and by["t=3.5_w=30"]["params_from"] == "폴더 이름"
    assert by["d"]["results"]["s"] == 5.0 and by["e"]["results"] == {"s": 6.0, "mass": 7.0}
    assert not by["job_only"]["ready"] and by["job_only"]["missing"] == "입력값 · 결과값"
    assert {e["kind"]: e["cases"] for e in scan["extract"]} == {"Abaqus": 1, "LS-DYNA": 1}
    assert scan["file_kinds"][".odb"] == 1 and scan["file_kinds"]["d3plot"] == 2
    assert scan["tables"][0]["label"] == "표.csv" and scan["tables"][0]["rows"] == 2
    assert not any(k.startswith("x") for k in by)  # 깊이 제한 안에서만 찾는다
    assert any(c["label"].startswith("x") for c in result_scan.scan_folder(root, max_depth=4)["cases"])
    assert result_scan.name_params("case_1_t=2_rib_h=5.5") == {"t": 2.0, "rib_h": 5.5}
    assert result_scan.name_params("t=3.0mm") == {}

    cut = result_scan.scan_folder(root, time_limit=-1)
    assert "앞부분만" in cut["truncated"] and cut["cases_total"] == 0
    assert result_scan.scan_folder(root, max_dirs=2)["truncated"]
    with pytest.raises(ValueError, match="찾지 못했습니다"):
        result_scan.scan_folder(root / "없음")
    with pytest.raises(ValueError, match="파일입니다"):
        result_scan.scan_folder(root / "표.csv")
    assert len(result_scan.brief(scan, shown=2)["cases"]) == 2 and "folder" not in result_scan.brief(scan)["cases"][0]


def test_every_recommended_extract_script_exists():
    for spec in result_scan.RESULT_KINDS.values():
        assert (SKILLS / spec["skill"] / "SKILL.md").is_file(), spec["skill"]
        if spec["script"]:
            assert (SKILLS / spec["skill"] / "scripts" / spec["script"]).is_file(), spec["script"]
        if spec["field_script"]:
            assert (SKILLS / spec["skill"] / "scripts" / spec["field_script"]).is_file(), spec["field_script"]


def test_import_folder_with_nothing_ready_explains_next_step(env, tmp_path):
    root = tmp_path / "r"
    (root / "a").mkdir(parents=True)
    (root / "a" / "job.odb").write_bytes(b"x")
    msg = _raises(dds_study.import_cases, folder=str(root))
    assert "abaqus-automation" in msg and "→" in msg


def test_import_cases_from_values_and_validation(env, tmp_path):
    rows = _rows(6)
    cases = [{"params": {"t": t, "w": w}, "results": {"s": s}, "label": f"c{i}", "run_dir": str(tmp_path / f"c{i}")} for i, (t, w, s) in enumerate(rows)]
    rep = dds_study.import_cases(cases=cases, name="값으로", objectives=[{"name": "s", "goal": "min"}])
    assert rep["registered"] == 6 and rep["study_name"] == "값으로"
    st = env.study(rep["study_id"])
    assert st["study"]["objectives"] == [{"name": "s", "goal": "min"}] and st["best"]
    more = dds_study.import_cases(cases=[{"params": {"t": 9.0, "w": 9.0}, "results": {"s": 1.0, "extra": 2.0}}], study_id=rep["study_id"])
    assert more["registered"] == 1 and {o["name"] for o in more["outputs"]} == {"s", "extra"}
    assert "하나만" in _raises(dds_study.import_cases)
    assert "하나만" in _raises(dds_study.import_cases, cases=cases, folder=str(tmp_path))
    assert "params" in _raises(dds_study.import_cases, cases=[{"results": {"s": 1}}])
    assert "어느 쪽인지" in _raises(dds_study.import_cases, cases=[{"params": {"t": 1.0}, "results": {"t": 2.0}}])
    assert "→" in _raises(dds_study.import_cases, cases=cases[:2], name="두 행")


def test_e13_field_results_to_3d_simulator(env, tmp_path):
    root = tmp_path / "fields"
    n = 40
    base = np.random.default_rng(0).uniform(0, 1, (n, 3))
    for i, t in enumerate(np.linspace(1, 3, 6)):
        _case_folder(root, f"t={t:.2f}_w={10 + i}", npz={"points": base, "values": base[:, 0] * float(t)})
    before = _tree(root)
    scan = dds_study.scan_results(str(root))
    assert scan["ready"] == 6 and scan["fields"] == ["mises"] and not scan["outputs"]
    rep = dds_study.import_cases(folder=str(root))
    assert rep["registered"] == 6 and rep["fields_uploaded"] == 6 and rep["auto_outputs"] == ["mises_max"]
    sid = rep["study_id"]
    m = dds_study.train_surrogate(sid)
    assert m["status"] == "done" and "mises_max" in m["metrics"]
    fm = dds_study.train_field_model(sid, "mises")
    assert fm["nodes"] == n
    assert dds_study.deploy_model(m["model_id"])["deployed"]
    assert _tree(root) == before
    assert env.cases(sid)[0]["fields"] == ["mises"]


def test_upload_field_and_bad_field_message(env, tmp_path):
    rep = dds_study.import_table(str(_write_csv(tmp_path / "a.csv", _rows(5))))
    cid = env.cases(rep["study_id"])[0]["id"]
    f = tmp_path / "anything.npz"
    np.savez_compressed(f, points=np.zeros((4, 3)), values=np.arange(4.0))
    assert dds_study.upload_field(cid, str(f), field="mises")["field"] == "mises"
    bad = tmp_path / "bad.npz"
    bad.write_bytes(b"not a zip")
    assert "서버 오류 400" in _raises(dds_study.upload_field, cid, str(bad))
    assert "찾지 못했습니다" in _raises(dds_study.upload_field, cid, str(tmp_path / "없음.npz"))


def test_import_folder_reports_field_upload_failure_without_losing_rows(env, tmp_path):
    root = tmp_path / "r"
    for i, (t, w, s) in enumerate(_rows(4)):
        d = _case_folder(root, f"c{i}", {"t": t, "w": w}, {"s": s})
        (d / "fields").mkdir()
        (d / "fields" / "bad.npz").write_bytes(b"not a zip")
    rep = dds_study.import_cases(folder=str(root))
    assert rep["registered"] == 4 and rep["field_errors"] and "fields_uploaded" not in rep


# ---------- E4: 표 모델로 최적화 ----------
def test_e4_optimize_on_data_gives_unverified_candidates_only(env, tmp_path):
    rep = dds_study.import_table(str(_write_csv(tmp_path / "a.csv", _rows(16))), objectives=[{"name": "stress", "goal": "min"}])
    sid = rep["study_id"]
    dds_study.train_surrogate(sid)
    r = dds_study.optimize(sid, iterations=3)
    assert r["verified"] is False and r["candidates"] and not r["candidates"][0]["verified"] and "확인" in r["note"]
    assert dds_study.suggest_next(sid)["verified"] is False
    assert len(env.cases(sid)) == 16  # 케이스가 늘지 않는다
    assert "해석 방법" in _raises(dds_study.run_cases, sid)


# ---------- 학습이 도구 시간 제한을 넘을 때 ----------
def test_long_training_survives_tool_limit_and_retry_makes_one_version(env, tmp_path, monkeypatch):
    sid = dds_study.import_table(str(_write_csv(tmp_path / "a.csv", _rows(10))))["study_id"]
    real = service.Surrogate.fit
    calls = []

    def slow(self, *a, **kw):
        calls.append(1)
        time.sleep(1.5)
        return real(self, *a, **kw)

    monkeypatch.setattr(service.Surrogate, "fit", slow)
    monkeypatch.setenv("DDS_MCP_WAIT_LIMIT_SEC", "1")
    first = dds_study.train_surrogate(sid)
    assert first["status"] == "running" and "실패가 아닙니다" in first["note"] and first["job_id"]
    t0 = time.time()
    while True:
        r = dds_study.train_surrogate(sid)
        if r["status"] == "done":
            break
        assert time.time() - t0 < 60
    assert r["version"] == 1 and len(dds_study.list_models(study_id=sid)["models"]) == 1 and len(calls) == 1
    assert dds_study.train_surrogate(sid)["reused"]  # 끝난 뒤의 재시도도 버전을 늘리지 않는다
    assert len(dds_study.list_models(study_id=sid)["models"]) == 1 and len(calls) == 1


def test_slow_import_returns_running_and_joins_the_same_job(env, tmp_path, monkeypatch):
    src = _write_csv(tmp_path / "a.csv", _rows(6))
    real = _common.api.upload_form
    calls = []

    def slow(*a, **kw):
        calls.append(1)
        time.sleep(1.5)
        return real(*a, **kw)

    monkeypatch.setattr(_common.api, "upload_form", slow)
    monkeypatch.setenv("DDS_MCP_WAIT_LIMIT_SEC", "1")
    first = dds_study.import_table(str(src))
    assert first["status"] == "running" and "다시 호출" in first["note"]
    r = first
    t0 = time.time()
    while r.get("status") == "running":
        r = dds_study.import_table(str(src))
        assert time.time() - t0 < 30
    assert r["registered"] == 6 and len(calls) == 1 and not dds_study._JOBS
    # 뒤에서 난 오류도 다음 호출에서 원래 문구로 나온다
    calls.clear()
    bad = _write_csv(tmp_path / "few.csv", _rows(2))
    first = dds_study.import_table(str(bad))
    assert first["status"] == "running"
    time.sleep(1.2)
    assert "→" in _raises(dds_study.import_table, str(bad))


# ---------- E12: DOE 계획만, 나중에 해석 연결 ----------
def test_e12_doe_export_fill_and_connect_adapter_later(env, tmp_path):
    sid = dds_study.create_study("계획", [{"name": "x1", "lower": 0, "upper": 1}, {"name": "x2", "lower": 0, "upper": 1}],
                                 objectives=[{"name": "f", "goal": "min"}])["study_id"]
    dds_study.generate_doe(sid, n=6, seed=1)
    out = dds_study.export_doe(sid)
    path = Path(out["path"])
    assert out["rows"] == 6 and path.parent.name == "reports" and path.parent.parent == env.project_dir()
    assert "이미 있는" in _raises(dds_study.export_doe, sid, path=str(path))  # 사용자 파일을 덮어쓰지 않는다

    lines = path.read_text(encoding="utf-8-sig").splitlines()
    head = lines[0].split(",")
    filled = [lines[0]]
    for ln in lines[1:4]:  # 절반만 밖에서 해석해 왔다
        vals = ln.split(",")
        vals[head.index("f")] = str(float(vals[head.index("x1")]) + float(vals[head.index("x2")]))
        filled.append(",".join(vals))
    back = tmp_path / "back.csv"
    back.write_text("\n".join(filled) + "\n", encoding="utf-8")
    rep = dds_study.import_table(str(back), study_id=sid)
    assert rep["filled_pending"] == 3 and rep["registered"] == 0
    assert env.study(sid)["counts"] == {"complete": 3, "pending": 3}

    assert "tool" in _raises(dds_study.set_study_adapter, sid, "script", {})
    st = dds_study.set_study_adapter(sid, "script", {"tool": "python", "script": "scripts/f.py"})
    assert st["adapter"] == "script" and st["adapter_config"]["tool"] == "python" and st["counts"]["pending"] == 3


# ---------- E14·E15: 찾기와 지난 흐름 다시 ----------
def test_e14_lists_find_things_without_ids(env, tmp_path):
    assert dds_study.list_studies()["studies"] == [] and dds_study.list_models() == {"models": [], "total": 0}
    assert dds_flow.list_flows() == {"project_id": env.pid, "total": 0, "flows": []}
    sid = dds_study.import_table(str(_write_csv(tmp_path / "a.csv", _rows(8))))["study_id"]
    m = dds_study.train_surrogate(sid)
    dds_study.deploy_model(m["model_id"])
    s = dds_study.list_studies()["studies"][0]
    assert s["study_id"] == sid and s["counts"] == {"complete": 8} and s["data_source"]["kind"] == "table"
    assert s["latest_model_id"] == s["deployed_model_id"] == m["model_id"] and s["inputs"][0]["name"] == "thickness" and s["outputs"] == ["stress"]
    brief = dds_study.list_models()["models"][0]
    assert brief["r2"]["stress"] == round(m["metrics"]["stress"]["r2"], 4) and brief["deployed"] and brief["quality"]

    other = env.c.post("/api/projects", json={"name": "다른 과제"}, headers=env.h).json()["id"]
    assert dds_study.list_studies(project_id=other)["studies"] == []
    found = dds_study.list_studies(all_projects=True)["studies"]
    assert [x["study_id"] for x in found] == [sid] and found[0]["project_name"] == "진입"
    copied = dds_study.clone_study(sid, name="옮김", project_id=other)
    assert copied["project_id"] == other and dds_study.list_studies(project_id=other)["studies"][0]["counts"] == {}


def test_e15_new_model_same_flow_keeps_old_results(env):
    f = dds_flow.propose_flow("브래킷 경량화", [{"id": "doe", "label": "DOE", "type": "doe"}, {"id": "train", "label": "학습", "type": "train"}],
                              [{"source": "doe", "target": "train"}])
    sid = dds_study.create_study("구형 브래킷", [{"name": "x1", "lower": -5, "upper": 10}, {"name": "x2", "lower": 0, "upper": 15}],
                                 objectives=[{"name": "f", "goal": "min"}], adapter="mock", adapter_config={"function": "branin"})["study_id"]
    dds_study.generate_doe(sid, n=8, seed=7, flow_id=f["flow_id"], node_id="doe")
    flow = env.c.get(f"/api/flows/{f['flow_id']}", headers=env.h).json()
    env.c.put(f"/api/flows/{f['flow_id']}/status", json={"status": "approved", "version": flow["version"]}, headers=env.h).raise_for_status()  # 승인 전 흐름에 묶인 스터디는 돌지 않는다
    dds_study.run_cases(sid)
    old = dds_study.train_surrogate(sid, flow_id=f["flow_id"], node_id="train")
    dds_study.deploy_model(old["model_id"])
    before = (env.cases(sid), dds_study.list_models(study_id=sid), env.study(sid)["study"]["adapter_config"])

    flows = dds_flow.list_flows()
    assert flows["flows"][0]["title"] == "브래킷 경량화" and flows["flows"][0]["steps"] == ["DOE", "학습"]
    link = flows["flows"][0]["studies"][0]
    assert link["id"] == sid and link["deployed_model_id"] == old["model_id"] and link["counts"]["complete"] == 8

    new = dds_study.clone_study(link["id"], name="신형 브래킷", variables=[{"name": "x1", "lower": -5, "upper": 12}, {"name": "x2", "lower": 0, "upper": 15}])
    nid = new["study_id"]
    assert nid != sid and new["adapter"] == "mock" and new["adapter_config"]["function"] == "branin" and new["objectives"] == [{"name": "f", "goal": "min"}]
    assert new["variables"][0]["upper"] == 12 and env.cases(nid) == [] and dds_study.list_models(study_id=nid)["total"] == 0
    dds_study.generate_doe(nid, n=8, seed=1)
    dds_study.run_cases(nid)
    assert dds_study.train_surrogate(nid)["version"] == 1
    # 옛 스터디·모델·Simulator는 그대로다
    assert (env.cases(sid), dds_study.list_models(study_id=sid), env.study(sid)["study"]["adapter_config"]) == before
    assert next(m for m in dds_study.list_models(study_id=sid)["models"] if m["deployed"])["model_id"] == old["model_id"]


# ---------- E16: 다른 멤버가 내 PC 없이 이어받기 ----------
def _member(env, client, tmp_path, monkeypatch, name="member1"):  # noqa: F811
    admin, _ = _auth(client, "admin")
    h, user = _auth(client, name, admin)
    client.put(f"/api/projects/{env.pid}/members/{user['id']}", json={"role": "member"}, headers=env.h)
    token = client.post("/api/tokens", json={"name": "멤버 PC"}, headers=h).json()["token"]
    # 멤버 PC: 같은 프로젝트, 다른 작업 폴더. 스크립트가 없다
    monkeypatch.setattr(_common.api, "client", TestClient(app, headers={"Authorization": f"Bearer {token}"}))
    monkeypatch.setattr(_common.settings, "workspace_dir", tmp_path / f"{name}_ws")
    return h, user


def _script_study(env, name="스크립트") -> int:
    d = env.project_dir() / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.py").write_text("print('run')\n", encoding="utf-8")
    (d / "post.py").write_text("print('post')\n", encoding="utf-8")
    (d / "helper.py").write_text("X = 1\n", encoding="utf-8")
    return dds_study.create_study(name, [{"name": "x", "lower": 0, "upper": 1}], adapter="script",
                                  adapter_config={"tool": "python", "script": "scripts/run.py", "inputs": ["inputs/model.dat"],
                                                  "post": [{"tool": "python", "script": "scripts/post.py"}]})["study_id"]


def test_e16_member_restores_recipe_on_their_pc(env, client, tmp_path, monkeypatch):  # noqa: F811
    sid = _script_study(env)
    assert "없습니다" in _raises(dds_study.restore_recipe, sid)
    saved = dds_study.save_recipe(sid, files=["scripts/helper.py"], note="브래킷용")
    assert saved["version"] == 1 and set(saved["files"]) == {"scripts/run.py", "scripts/post.py", "scripts/helper.py"}
    assert any("model.dat" in w for w in saved["warnings"])

    _member(env, client, tmp_path, monkeypatch)
    assert dds_study.list_studies()["studies"][0]["has_recipe"]
    member_dir = _common.settings.project_dir(env.pid)
    assert not (member_dir / "scripts" / "run.py").exists()
    got = dds_study.restore_recipe(sid)
    assert set(got["restored"]) == {"scripts/run.py", "scripts/post.py", "scripts/helper.py"} and got["unchanged"] == [] and got["conflicts"] == []
    assert (member_dir / "scripts" / "run.py").read_text(encoding="utf-8") == "print('run')\n"
    assert got["input_files_missing"] == ["inputs/model.dat"] and got["created_by_name"] and got["created_by_name"] in got["before_running"]
    assert not (member_dir / "recipe.json").exists()

    # 다시 받아도 그대로이고, 내가 고친 파일은 덮어쓰지 않는다
    (member_dir / "scripts" / "run.py").write_text("print('mine')\n", encoding="utf-8")
    again = dds_study.restore_recipe(sid)
    assert again["conflicts"] == ["scripts/run.py"] and set(again["unchanged"]) == {"scripts/post.py", "scripts/helper.py"}
    assert (member_dir / "scripts" / "run.py").read_text(encoding="utf-8") == "print('mine')\n"
    forced = dds_study.restore_recipe(sid, overwrite=True)
    assert forced["restored"] == ["scripts/run.py"] and (member_dir / "scripts" / "run.py").read_text(encoding="utf-8") == "print('run')\n"


def test_e16_member_uses_server_data_while_owner_pc_is_off(env, client, tmp_path, monkeypatch):  # noqa: F811
    sid = dds_study.import_table(str(_write_csv(tmp_path / "a.csv", _rows(10))))["study_id"]
    m1 = dds_study.train_surrogate(sid)
    dds_study.deploy_model(m1["model_id"])
    _member(env, client, tmp_path, monkeypatch, name="member2")  # 만든 사람 PC는 없다. PC Agent도 연결돼 있지 않다
    assert dds_study.get_study(sid)["counts"] == {"complete": 10}
    assert dds_study.list_models()["models"][0]["model_id"] == m1["model_id"]
    assert "stress" in dds_study.predict(m1["model_id"], {"thickness": 2.0, "width": 20.0})["mean"]
    m2 = dds_study.train_surrogate(sid, force=True, wait_sec=120)
    assert m2["status"] == "done" and m2["version"] == 2
    assert dds_study.deploy_model(m2["model_id"])["deployed"]
    # 표로 만든 스터디에는 원본 결과 파일이 없다. 멤버 PC에서 돌릴 것도 없으니 이유를 안내한다
    assert "해석 방법" in _raises(dds_study.run_cases, sid)
    # 스크립트 없는 스터디를 멤버 PC에서 돌리면: 스크립트가 없다고 하고 다음 할 일을 말한다
    from cae_adapters.script import ScriptAdapter

    d = tmp_path / "case"
    d.mkdir()
    with pytest.raises(ValueError, match="restore_recipe"):
        ScriptAdapter._commands([{"tool": "python", "script": "scripts/x.py"}], d, d, {}, None, None)


def test_recipe_paths_are_checked_again_on_this_pc(env, monkeypatch):
    for bad in ["../x.py", "/etc/x.py", "C:/x.py", "a/../../x.py", "..\\x.py", "scripts/.. /x.py", ".claude/skills/x/SKILL.md", ".codex/config.toml", "AGENTS.md", "claude.md",
                "GEMINI.md.", "project.json", "scripts/.hidden/x.py", "con.py", "scripts/NUL", "a:b.py", ""]:
        with pytest.raises(ValueError):
            dds_study.member_path(bad)
    assert str(dds_study.member_path("scripts/sub/x.py")) == "scripts/sub/x.py"
    for nested in ("scripts/AGENTS.md", "scripts/CLAUDE.md", "inputs/GEMINI.md", "a/b/project.json", "scripts/CLAUDE.md. "):
        with pytest.raises(ValueError, match="안전하지 않은 경로"):
            dds_study.member_path(nested)

    # 서버가 돌려준 묶음이 믿을 수 없는 경로를 담았을 때: 아무것도 쓰지 않는다
    sid = _script_study(env, "가짜 묶음")
    dds_study.save_recipe(sid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scripts/ok.py", "x")
        z.writestr(".claude/skills/x/SKILL.md", "지침을 바꾼다")
    blob = buf.getvalue()
    sha = hashlib.sha256(blob).hexdigest()
    info = {"has": True, "sha256": sha, "version": 1, "created_by_name": "누군가", "adapter_config": {},
            "files": [{"path": n, "sha256": hashlib.sha256(z).hexdigest()} for n, z in (("scripts/ok.py", b"x"), (".claude/skills/x/SKILL.md", "지침을 바꾼다".encode()))]}
    real_get = _common.api.get

    def get(path, **kw):
        return info if path.endswith("/recipe") else real_get(path, **kw)

    def download(path, dest):
        Path(dest).write_bytes(blob)
        return sha

    monkeypatch.setattr(_common.api, "get", get)
    monkeypatch.setattr(_common.api, "download", download)
    before = _tree(env.project_dir())
    assert "안전하지 않은" in _raises(dds_study.restore_recipe, sid)
    assert _tree(env.project_dir()) == before

    # 서버 기록과 내용이 다르면 받지 않는다
    monkeypatch.setattr(_common.api, "download", lambda path, dest: (Path(dest).write_bytes(blob), "다른 값")[1])
    assert "다릅니다" in _raises(dds_study.restore_recipe, sid)
    monkeypatch.setattr(_common.api, "download", download)
    info["files"] = [{"path": "scripts/ok.py", "sha256": "0" * 64}, {"path": ".claude/skills/x/SKILL.md", "sha256": "0" * 64}]
    assert "다릅니다" in _raises(dds_study.restore_recipe, sid)  # 파일 하나의 내용이 기록과 다르다
    info["files"] = [{"path": "scripts/ok.py", "sha256": hashlib.sha256(b"x").hexdigest()}]
    assert "기록에 없는" in _raises(dds_study.restore_recipe, sid)
    assert _tree(env.project_dir()) == before


def test_save_recipe_rejects_what_should_not_travel(env):
    sid = _script_study(env, "묶음 거절")
    d = env.project_dir() / "scripts"
    (d / "bad.py").write_text('password = "hunter2hunter2"\n', encoding="utf-8")
    assert "비밀값" in _raises(dds_study.save_recipe, sid, files=["scripts/bad.py"])
    assert "없습니다" in _raises(dds_study.save_recipe, sid, files=["scripts/없음.py"])
    assert "밖" in _raises(dds_study.save_recipe, sid, files=[str(env.ws.parent / "outside.py")])
    none = dds_study.create_study("빈 묶음", [{"name": "x", "lower": 0, "upper": 1}], adapter="mock")["study_id"]
    assert "files" in _raises(dds_study.save_recipe, none)
    # 폴더를 주면 안의 파일을 넣고 캐시는 뺀다
    (d / "bad.py").unlink()
    (d / "__pycache__").mkdir()
    (d / "__pycache__" / "x.txt").write_text("x", encoding="utf-8")
    saved = dds_study.save_recipe(sid, files=["scripts"])
    assert "scripts/helper.py" in saved["files"] and not any("__pycache__" in f for f in saved["files"])


# ---------- 데모 에이전트: 실제 서버를 부르고 Flow를 만들지 않는다 ----------
class _DemoHarness(demo_agent.DemoSession):
    def __init__(self):
        super().__init__("demo-test", [])
        self.calls: list[str] = []
        self.said: list[str] = []

    def update(self, upd):
        pass

    async def say(self, text, thought=False):
        if not thought:
            self.said.append(text)

    async def tool(self, server, name, args):
        self.calls.append(f"{server}.{name}")
        module = {"dds-study": dds_study, "dds-flow": dds_flow}[server]
        try:
            # 실제 MCP 전달처럼 JSON으로 왕복시킨다. dict가 아닌 값이 끼면 여기서 드러난다
            return json.loads(json.dumps(await asyncio.to_thread(getattr(module, name), **args)))
        except (ValueError, _common.ServerError) as e:
            raise RuntimeError(f"Error executing tool {name}: {e}") from e


def _chat(s: _DemoHarness, text: str) -> str:
    async def turn():
        agent = demo_agent.DemoAgent(asyncio.get_running_loop())
        await agent.scenario(s, text)

    n = len(s.said)
    asyncio.run(turn())
    return "".join(s.said[n:])


FIRST = "[DDS 컨텍스트] project_id=1. 작업 규칙을 따르세요.\n\n"


def _no_flow(env) -> bool:
    return env.c.get(f"/api/projects/{env.pid}/flows", headers=env.h).json() == []


def test_demo_table_request_really_imports_trains_and_deploys(env, tmp_path):
    src = _write_csv(tmp_path / "표.csv", _rows(14))
    s = _DemoHarness()
    reply = _chat(s, FIRST + f'이 표로 Simulator 만들어 줘: "{src}"')
    assert s.calls == ["dds-study.inspect_table", "dds-flow.set_project_info", "dds-study.import_table", "dds-study.train_surrogate", "dds-study.deploy_model"]
    assert _no_flow(env) and "Branin" not in reply and "표 14행" in reply and "R²" in reply and "승인" not in reply
    models = dds_study.list_models()["models"]
    assert models[0]["deployed"] and dds_study.list_studies()["studies"][0]["adapter"] == "data"


def test_demo_attachment_and_unquoted_path(env, tmp_path):
    (env.project_dir() / "inputs").mkdir(parents=True, exist_ok=True)
    _write_csv(env.project_dir() / "inputs" / "첨부.csv", _rows(10))
    s = _DemoHarness()
    _chat(s, FIRST + "바로 예측 모델 만들어 줘\n\n[첨부 파일 — 프로젝트 작업 폴더 기준, 원본이므로 수정하지 말고 복사해서 사용]\n- inputs/첨부.csv (0.0 MB)")
    assert "dds-study.import_table" in s.calls and _no_flow(env)
    assert dds_study.list_studies()["studies"][0]["name"] == "첨부"


def test_demo_folder_request_scans_and_imports(env, runs):
    s = _DemoHarness()
    reply = _chat(s, FIRST + f'이미 해석한 결과가 "{runs}" 에 있어. 이걸로 Simulator 만들어 줘')
    assert s.calls == ["dds-study.scan_results", "dds-flow.set_project_info", "dds-study.import_cases", "dds-study.train_surrogate", "dds-study.deploy_model"]
    assert _no_flow(env) and "8건" in reply and "해석은 돌리지 않았습니다" in reply


def test_demo_says_what_is_missing_instead_of_running_branin(env, tmp_path):
    s = _DemoHarness()
    assert "붙여 넣어" in _chat(s, "CSV로 Simulator 만들어 줘") and s.calls == []
    assert "경로" in _chat(s, "이미 해석한 결과로 예측 모델 만들어 줘") and s.calls == []
    root = tmp_path / "r"
    (root / "a").mkdir(parents=True)
    (root / "a" / "job.odb").write_bytes(b"x")
    reply = _chat(s, f'"{root}" 폴더로 Simulator 만들어 줘')
    assert "Abaqus 1건" in reply and "dds-study.import_cases" not in s.calls
    bad = _write_csv(tmp_path / "few.csv", _rows(2))
    assert "→" in _chat(s, f'"{bad}" 로 만들어 줘')
    assert _no_flow(env) and dds_study.list_studies()["studies"] == []


def test_demo_simple_question_needs_no_flow_and_follow_up_keeps_first_paragraph(env, tmp_path):
    s = _DemoHarness()
    assert "스터디가 없습니다" in _chat(s, FIRST + "이 프로젝트에 모델 몇 개 있어?")
    assert s.calls == ["dds-study.list_studies", "dds-study.list_models"] and _no_flow(env)
    _chat(s, f'"{_write_csv(tmp_path / "a.csv", _rows(10))}" 표로 만들어 줘')
    assert "스터디 1개" in _chat(s, "지금 모델 정확도 알려 줘") and _no_flow(env)
    # 두 문단 요청: 후속 턴의 첫 문단이 잘리면 표 경로를 잃고 "무엇을 가지고 계신지"로 답한다
    src2 = _write_csv(tmp_path / "b.csv", _rows(6, seed=8))
    assert demo_agent.request_text(f'행 추가해 줘 "{src2}"\n\n둘째 문단') == f'행 추가해 줘 "{src2}"\n\n둘째 문단'
    assert demo_agent.request_text(FIRST + "요청\n\n둘째") == "요청\n\n둘째"
    assert demo_agent.request_text(FIRST + "[이전 대화]\n내용\n[이전 대화 끝]\n\n요청\n\n둘째") == "요청\n\n둘째"
    reply = _chat(s, f'행 추가해 줘 "{src2}"\n\n이 데이터는 어제 나온 값이야')
    assert "표 6행을 더해" in reply
    assert [m["version"] for m in dds_study.list_models()["models"]] == [2, 1]


def test_demo_follow_ups_on_data_study_never_run_a_solver(env, tmp_path):
    s = _DemoHarness()
    src = tmp_path / "a.csv"
    src.write_text("thickness,width,stress\n" + "\n".join(f"{t},{w},{y}" for t, w, y in _rows(12)) + "\n", encoding="utf-8")
    _chat(s, f'"{src}" 표로 Simulator 만들어 줘')
    sid = s.study_id
    # 목적을 정하지 않은 표: 무엇을 줄일지 모르니 이유와 할 일을 듣는다. 그 할 일(objective)은 도구로 실제 할 수 있어야 한다
    assert "objective" in _chat(s, "최적화해서 후보 찾아 줘")
    assert dds_study.optimize(sid, objective="stress", goal="min")["verified"] is False
    assert dds_study.suggest_next(sid, objective="stress", goal="min")["verified"] is False
    assert _chat(s, "해석 돌려 줘").startswith("이 스터디는 가진 데이터로")
    assert "보고서" in _chat(s, "보고서 써 줘") and env.c.get(f"/api/projects/{env.pid}/reports", headers=env.h).json()
    assert _no_flow(env) and len(env.cases(sid)) == 12


def test_demo_report_from_existing_results_needs_no_flow(env, tmp_path):
    s = _DemoHarness()
    assert "결과가 아직 없습니다" in _chat(s, "보고서 써 줘") and _no_flow(env)
    sid = dds_study.import_table(str(_write_csv(tmp_path / "a.csv", _rows(10))))["study_id"]
    dds_study.train_surrogate(sid)
    fresh = _DemoHarness()
    assert "보고서" in _chat(fresh, "최신 결과로 보고서를 다시 작성해 줘") and _no_flow(env)
    assert env.c.get(f"/api/projects/{env.pid}/reports", headers=env.h).json()


def test_demo_flow_review_does_not_block_other_requests(env, tmp_path):
    s = _DemoHarness()
    _chat(s, "mock 솔버로 Branin 최적화하고 Simulator 배포해줘")
    flows = env.c.get(f"/api/projects/{env.pid}/flows", headers=env.h).json()
    assert len(flows) == 1 and flows[0]["status"] == "review"
    assert "스터디가 없습니다" in _chat(s, "이 프로젝트에 스터디 있어?")  # 승인을 기다리는 중에도 답한다
    reply = _chat(s, f'"{_write_csv(tmp_path / "a.csv", _rows(10))}" 표로 Simulator 만들어 줘')
    assert "표 10행" in reply
    assert env.c.get(f"/api/projects/{env.pid}/flows", headers=env.h).json()[0]["status"] == "review"


@pytest.mark.parametrize("text,entry", [
    ("mock 솔버로 Branin 최적화하고 Simulator 배포해줘", "flow"),
    ("브래킷 CAD를 만들고 해석 모델을 구성한 뒤 DOE로 데이터를 쌓아 형상·응력을 학습하고, 3D Simulator로 배포한 다음 보고서까지 작성해 줘", "flow"),
    ("NX에서 브래킷 형상을 만들고, 두께·리브 높이를 파라미터로 바꿀 수 있게 정리해 줘", "flow"),
    ("이 CAD 모델로 Abaqus 정적 해석 모델을 만들고(재료·하중·경계조건·메쉬) 해석해서 최대 응력을 알려 줘", "flow"),
    ("두께 2~6mm, 리브 높이 5~20mm로 DOE를 돌려 데이터를 쌓고, 질량·응력 예측 모델을 만들어 Simulator로 배포해 줘", "flow"),
    ("응력 250MPa 이하에서 질량이 최소가 되는 설계를 베이지안 최적화로 찾아 줘", "flow"),
    ("드럼세탁기 탈수 진동을 줄여 줘", "flow"),
    ("3D 외팔보 응력 분포 보여 줘", "flow"),
    ("질량을 목표로 최적화해 줘", "flow"),
    ("작업 흐름 '표로 만들기'(#3)을 승인했습니다. 계획대로 진행해 주세요.", "flow"),
    (r"C:\models\bracket.prt 로 해석 돌려 줘", "flow"),
    ("이 표로 Simulator 만들어 줘", "table"),
    ("엑셀 파일로 예측 모델", "table"),
    (r"D:\data\result.xlsx로 만들어 줘", "table"),
    (r"\\server\share\runs 폴더로 만들어 줘", "folder"),
    ("이미 해석한 결과가 있어", "folder"),
    ("지금 Simulator 공유 링크 알려 줘", "question"),
    ("모델 몇 개 있어?", "question"),
    ("R²가 뭐야?", "question"),
])
def test_pick_entry_keeps_every_starter_and_finds_data(text, entry):
    assert demo_agent.pick_entry(text)[0] == entry


def test_pick_entry_paths():
    assert demo_agent.pick_entry(r"D:\data\result.xlsx로 만들어 줘") == ("table", r"D:\data\result.xlsx")
    assert demo_agent.pick_entry(r"\\server\share\runs 폴더로") == ("folder", r"\\server\share\runs")
    assert demo_agent.pick_entry('"D:\\내 데이터\\a b.csv" 로') == ("table", "D:\\내 데이터\\a b.csv")
    assert demo_agent.pick_entry("이 표로\n\n[첨부 파일]\n- inputs/a.xlsx (1.0 MB)\n- inputs/b.prt (2.0 MB)") == ("table", "inputs/a.xlsx")
    assert demo_agent.tool_error_text(RuntimeError("Error executing tool import_table: 서버 오류 400: 이유 → 할 일")) == "이유 → 할 일"


# ---------- 지침 일관성: 한 곳만 고치면 나머지가 옛 규칙을 계속 강제한다 ----------
def _mcp_tools() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in (ROOT / "mcp_servers").glob("dds_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "tool" for d in node.decorator_list):
                names.add(node.name)
        out[path.stem.replace("_", "-")] = names
    return out


def _rule_sources() -> dict[str, str]:
    """Flow 승인 규칙이 적힌 모든 곳. 같은 뜻이어야 한다."""
    return {"INSTRUCTIONS": INSTRUCTIONS.read_text(encoding="utf-8"),
            "hub": (ROOT / "server" / "app" / "agent" / "hub.py").read_text(encoding="utf-8"),
            "dds_flow": (ROOT / "mcp_servers" / "dds_flow.py").read_text(encoding="utf-8"),
            "dds_study": (ROOT / "mcp_servers" / "dds_study.py").read_text(encoding="utf-8"),
            "workflow-planning": (SKILLS / "workflow-planning" / "SKILL.md").read_text(encoding="utf-8"),
            "doe-surrogate-optimization": (SKILLS / "doe-surrogate-optimization" / "SKILL.md").read_text(encoding="utf-8"),
            "existing-data-to-simulator": (SKILLS / "existing-data-to-simulator" / "SKILL.md").read_text(encoding="utf-8")}


OLD_RULES = ["작업 전 Flow 제안", "실행 전에 Flow를 제안", "반드시 propose_flow", "승인할 때까지 실행하지", "승인 전에는 해석이나 스크립트 실행을",
             "모델 파일이 없음", "create_study → generate_doe → run_cases → train_surrogate 순으로 진행한다"]


def test_old_approval_rule_is_gone_everywhere():
    for name, text in _rule_sources().items():
        for old in OLD_RULES:
            assert old not in text, f"{name}에 옛 규칙이 남아 있습니다: {old}"


@pytest.mark.parametrize("source", ["INSTRUCTIONS", "hub", "dds_flow", "dds_study", "workflow-planning"])
def test_narrowed_approval_rule_says_the_same_everywhere(source):
    text = _rule_sources()[source]
    for must in ("해석·긴 계산", "사용자 모델", "조회", "후처리", "기존 데이터로 학습", "Simulator 배포", "보고서", "승인 없이"):
        assert must in text, f"{source}에 승인 규칙 낱말 '{must}'이(가) 없습니다"


def test_instructions_cover_every_entry_and_rule():
    text = _rule_sources()["INSTRUCTIONS"]
    assert "## 0-2. 어디서 시작하든" in text and "## 1-2. 신규 모델로 지난번처럼" in text and "## 1-3. 프로젝트 멤버가 이어받기" in text
    table = text.split("## 0-2. 어디서 시작하든")[1].split("## 1.")[0]
    for first in ("inspect_table", "import_table", "scan_results", "import_cases", "list_studies", "list_models", "export_doe", "set_study_adapter", "list_flows", "upload_field"):
        assert f"`{first}" in table or f".{first}`" in table, first
    assert "joblib" in table  # 외부 모델은 받지 않는다고 분명히 적는다
    again = text.split("## 1-2.")[1].split("## 1-3.")[0]
    assert "달라진 점" in again and "건드리지 않습니다" in again and "승인" in again and "clone_study" in again and "list_flows" in again
    member = text.split("## 1-3.")[1].split("## 2.")[0]
    assert "restore_recipe" in member and "save_recipe" in member and "만든 스크립트" in member and "case.h5" in member
    # R² 처방은 솔버 유무로 갈린다. 표 모델에 능동학습을 권하면 막다른 길이다
    assert "해석 방법이 없는 스터디" in text and "행을 더 달라" in text
    assert "툴이 없어도" in text and "데이터도 모델도 없음" in text and "승인 전에 해도 됩니다" in text


def test_report_section_is_left_for_the_report_branch():
    """report-template 브랜치가 1-0절을 고친다. 합칠 때 겹치지 않게 여기서는 건드리지 않는다."""
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert "## 1-0. 보고서는 AI가 새로 쓴다" in text and "`dds-flow.get_report_context`로 작업 흐름·결과·스터디를 읽는다 → `dds-flow.write_report`로 쓴다" in text


def test_every_tool_named_in_instructions_and_skills_exists():
    tools = _mcp_tools()
    every = set().union(*tools.values())
    files = [INSTRUCTIONS, *SKILLS.glob("*/SKILL.md"), *SKILLS.glob("*/references/*.md")]
    for f in files:
        for server, name in re.findall(r"\b(dds-(?:flow|study|cae|live))\.([a-z_]+)", f.read_text(encoding="utf-8")):
            assert name in tools[server], f"{f}: {server}.{name} 도구가 없습니다"
    # 표 안에서 서버 이름 없이 적은 도구도 확인한다
    inst = INSTRUCTIONS.read_text(encoding="utf-8")
    for name in re.findall(r"`([a-z]+_[a-z_]+)(?:\([^`]*\))?`", inst.split("## 0-2.")[1].split("## 1.")[0]):
        assert name in every, f"INSTRUCTIONS 0-2절의 {name} 도구가 없습니다"


def test_every_new_tool_is_taught_somewhere():
    inst = INSTRUCTIONS.read_text(encoding="utf-8")
    for name in ("inspect_table", "import_table", "scan_results", "import_cases", "upload_field", "list_studies", "list_models", "predict_batch", "export_doe",
                 "set_study_adapter", "list_flows", "clone_study", "save_recipe", "restore_recipe"):
        assert name in inst, f"INSTRUCTIONS가 {name}을(를) 가르치지 않습니다"
        server = next(k for k, v in _mcp_tools().items() if name in v)
        assert server in ("dds-study", "dds-flow")


def test_new_skill_links_real_scripts_and_is_listed():
    text = (SKILLS / "existing-data-to-simulator" / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: existing-data-to-simulator\n") and "existing-data-to-simulator" in INSTRUCTIONS.read_text(encoding="utf-8")
    pairs = re.findall(r"\| `([a-z0-9-]+-automation)` \| `([a-z0-9_]+\.py)` \|", text)
    assert len(pairs) >= 8
    for skill, script in pairs:
        assert (SKILLS / skill / "scripts" / script).is_file(), f"{skill}/{script}"
    for script in ("abq_odb_extract.py", "dyna_lasso_extract.py", "nastran_op2_extract.py", "abq_odb_to_npz.py", "dyna_to_h5.py", "op2_to_h5.py"):
        assert script in text
    assert "--dir" in (SKILLS / "lsdyna-automation" / "scripts" / "dyna_lasso_extract.py").read_text(encoding="utf-8")  # 표의 인자가 실제로 있다


def test_mcp_tool_schemas_build_for_every_new_tool():
    """실제 CLI가 받는 도구 정의가 만들어지는지 본다. 타입 힌트가 잘못되면 서버 시작 때가 아니라 AI가 부를 때에야 드러난다."""
    tools = asyncio.run(dds_study.mcp.list_tools()) + asyncio.run(dds_flow.mcp.list_tools())
    if hasattr(tools, "tools"):
        tools = tools.tools
    by = {t.name: t for t in tools}
    for name in ("inspect_table", "import_table", "scan_results", "import_cases", "upload_field", "list_studies", "list_models", "predict_batch", "export_doe",
                 "set_study_adapter", "list_flows", "clone_study", "save_recipe", "restore_recipe"):
        assert by[name].description and by[name].input_schema["properties"]


def test_tool_failure_text_reaches_the_ai(env, tmp_path):
    """실제 MCP 경로로 부른다. mcp 2.x는 ToolError가 아닌 예외의 문구를 지워서, "이유 → 할 일"이 AI에게 가지 않을 수 있다."""
    async def call(server, name, args):
        try:
            r = await server.mcp.call_tool(name, args)
        except Exception as e:  # noqa: BLE001 — 도구 실패가 예외로 오든 is_error 결과로 오든 문구만 본다
            return str(e)
        return json.dumps(getattr(r, "content", r), default=str, ensure_ascii=False)

    assert "찾지 못했습니다" in asyncio.run(call(dds_study, "inspect_table", {"path": str(tmp_path / "없음.csv")}))  # ValueError 계열
    few = _write_csv(tmp_path / "few.csv", _rows(2))
    text = asyncio.run(call(dds_study, "import_table", {"path": str(few)}))  # 서버가 돌려준 400
    assert "서버 오류 400" in text and "→" in text
    assert "서버 오류 404" in asyncio.run(call(dds_flow, "get_flow", {"flow_id": 999999}))
    import httpx
    from _common import failure_text

    assert "제한 시간" in failure_text(httpx.ReadTimeout("x")) and "연결하지 못했습니다" in failure_text(httpx.ConnectError("x")) and failure_text(KeyError()) == "KeyError()"


def test_every_mcp_server_keeps_failure_text():
    for path in (ROOT / "mcp_servers").glob("dds_*.py"):
        text = path.read_text(encoding="utf-8")
        assert "mcp = DdsMCP(" in text and "MCPServer" not in text, path.name


def test_encrypted_table_tells_the_ai_to_ask_for_a_paste(env, tmp_path):
    """암호화된 파일은 깨진 표가 아니라 이유와 할 일로 답한다. 스터디가 생기지 않는다."""
    bad = tmp_path / "result.xlsx"
    bad.write_bytes(bytes(range(256)) * 16)
    for fn in (dds_study.inspect_table, dds_study.import_table):
        assert "보안 프로그램으로 암호화되어 읽을 수 없습니다 → 엑셀에서 표를 복사해 붙여 넣으세요" in _raises(fn, str(bad))


def test_pasted_table_attachment_reads_like_a_table_and_outputs_alone_are_enough(env, tmp_path):
    """입력창에서 AI로 보낸 표는 첨부 .tsv로 온다. 출력만 주면 나머지 숫자 열이 입력이다(화면 붙여넣기와 같은 규칙).
    결과처럼 보이는 열이 입력에 들면 조용히 넘어가지 않고 알린다. 그 값도 결과라고 하면 outputs에 넣는다."""
    rows = "\n".join(f"{t}\t{w}\t{_f(t, w)}\t{t * w}" for t, w in [(1 + i / 4, 10 + (i * 7) % 30) for i in range(24)])
    path = tmp_path / "붙여넣은_표_130512.tsv"
    path.write_text("두께 [mm]\t폭 [mm]\t응력 [MPa]\t질량\n" + rows, encoding="utf-8")
    info = dds_study.inspect_table(str(path))
    assert info["n_rows"] == 24 and "응력" in info["outputs"]
    rep = dds_study.import_table(str(path), outputs=["응력"])
    assert rep["registered"] == 24 and [v["name"] for v in rep["variables"]] == ["두께", "폭", "질량"] and "unused_columns" not in rep
    assert any("결과처럼 보이는 열" in w and "질량" in w for w in rep["warnings"])
    rep = dds_study.import_table(str(path), outputs=["응력", "질량"], name="질량도 결과")
    assert [v["name"] for v in rep["variables"]] == ["두께", "폭"] and [o["name"] for o in rep["outputs"]] == ["응력", "질량"]
    assert not any("결과처럼 보이는 열" in w for w in rep["warnings"])


ATTACHED = "\n\n[첨부 파일 — 프로젝트 작업 폴더 기준, 원본이므로 수정하지 말고 복사해서 사용]\n- inputs/붙여넣은_표_100001.tsv (0.0 MB)"


def _pasted(env, rows=14) -> None:
    d = env.project_dir() / "inputs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "붙여넣은_표_100001.tsv").write_text("두께 [mm]\t폭 [mm]\t응력 [MPa]\n" + "\n".join(f"{t}\t{w}\t{y}" for t, w, y in _rows(rows)) + "\n", encoding="utf-8")


def test_demo_pasted_table_asks_only_which_columns_are_outputs(env):
    """E1a: 묻는 것은 출력 하나다. 답을 받기 전에는 등록하지 않고, 답한 뒤에는 더 묻지 않는다."""
    _pasted(env)
    s = _DemoHarness()
    ask = _chat(s, FIRST + "이 표로 Simulator 만들어 줘" + ATTACHED)
    assert s.calls == ["dds-study.inspect_table"] and ask.count("?") == 1 and "응력" in ask and "출력" in ask
    assert dds_study.list_studies()["studies"] == []
    done = _chat(s, "응, 맞아")
    assert "?" not in done and s.calls[-4:] == ["dds-flow.set_project_info", "dds-study.import_table", "dds-study.train_surrogate", "dds-study.deploy_model"]
    assert dds_study.list_models()["models"][0]["deployed"] and _no_flow(env) and dds_study.list_studies()["studies"][0]["adapter"] == "data"


def test_demo_pasted_table_named_output_is_not_asked(env):
    _pasted(env)
    s = _DemoHarness()
    reply = _chat(s, FIRST + "응력이 결과야. 이 표로 Simulator 만들어 줘" + ATTACHED)
    assert "?" not in reply and dds_study.list_models()["models"][0]["outputs"] == ["응력"]


def test_demo_other_request_after_the_question_is_not_taken_as_the_answer(env):
    """물어본 뒤에 다른 말을 하면 표를 등록하지 않고 그 요청을 처리한다."""
    _pasted(env)
    s = _DemoHarness()
    _chat(s, FIRST + "이 표로 Simulator 만들어 줘" + ATTACHED)
    reply = _chat(s, "이 프로젝트에 모델 몇 개 있어")
    assert "스터디가 없습니다" in reply and s.pending_table is None and dds_study.list_studies()["studies"] == []


def test_demo_encrypted_table_gets_the_paste_answer_in_chat(env, tmp_path):
    bad = tmp_path / "암호화.xlsx"
    bad.write_bytes(b"\x00DRM\xff\xfe" * 200)
    s = _DemoHarness()
    reply = _chat(s, FIRST + f'"{bad}" 표로 Simulator 만들어 줘')
    assert "보안 프로그램으로 암호화되어 읽을 수 없습니다 → 엑셀에서 표를 복사해 붙여 넣으세요" in reply and "Error executing" not in reply
    assert dds_study.list_studies()["studies"] == [] and _no_flow(env)


def test_paste_rule_is_in_the_instructions_and_skill():
    root = Path(__file__).resolve().parents[2]
    for name in ("agent-context/INSTRUCTIONS.md", "agent-context/skills/existing-data-to-simulator/SKILL.md"):
        text = (root / name).read_text(encoding="utf-8")
        assert "암호화되어 읽을 수 없습니다" in text and "붙여 넣어 달라고" in text, name
        assert "어느 열이 결과(출력)인가 하나뿐" in text, name  # 묻는 것은 출력 열 하나
        assert "추측하지 않" in text, name


def test_table_read_is_not_done_by_passing_rows_to_the_ai():
    """표는 경로로만 받는다. 행을 도구 인자로 넘기면 토큰 비용과 옮기는 오류가 생긴다."""
    params = ast.parse((ROOT / "mcp_servers" / "dds_study.py").read_text(encoding="utf-8"))
    fn = next(n for n in params.body if isinstance(n, ast.FunctionDef) and n.name == "import_table")
    assert "rows" not in [a.arg for a in fn.args.args]


# ---------- 데모: 승인 뒤까지 도는 진입(E9·E12·E15·E16) ----------
class _DemoAll(_DemoHarness):
    """dds-cae까지 부른다. 권한 요청은 허용하고 제목을 잰다."""

    def __init__(self, cae=None):
        super().__init__()
        self.cae = cae
        self.permissions: list[str] = []

    async def tool(self, server, name, args):
        if server == "dds-cae":
            self.calls.append(f"{server}.{name}")
            return self.cae[name]
        return await super().tool(server, name, args)


def _chat_all(s: _DemoAll, text: str) -> str:
    async def turn():
        agent = demo_agent.DemoAgent(asyncio.get_running_loop())

        async def allow(_s, title, _raw):
            s.permissions.append(title)
            return True

        agent.ask_permission = allow
        await agent.scenario(s, text)

    n = len(s.said)
    asyncio.run(turn())
    return "".join(s.said[n:])


def _approve(env, s: _DemoAll) -> str:
    flow = next(f for f in env.c.get(f"/api/projects/{env.pid}/flows", headers=env.h).json() if f["id"] == s.flow_id)
    env.c.put(f"/api/flows/{flow['id']}/status", json={"status": "approved", "version": flow["version"]}, headers=env.h).raise_for_status()
    return _chat_all(s, f"작업 흐름 '{flow['title']}'(#{flow['id']})을 승인했습니다. 계획대로 진행해 주세요.")


def test_e9_tools_missing_does_not_stop_table_work(env, tmp_path):
    s = _DemoAll(cae={"list_tools": {"tools": [{"id": "abaqus", "name": "Abaqus", "status": "missing"}, {"id": "nx", "name": "NX", "status": "missing"}], "commands": []}})
    reply = _chat_all(s, "이 PC에 어떤 툴 있어?")
    assert "2종 중" in reply and "0종" in reply and "툴 없이" in reply and s.calls == ["dds-cae.list_tools"]
    s.calls.clear()
    assert "표 12행" in _chat_all(s, f'이 표로 Simulator 만들어 줘: "{_write_csv(tmp_path / "a.csv", _rows(12))}"')
    assert "dds-cae.list_tools" not in s.calls and _no_flow(env)  # 데이터 작업에 툴 확인이 끼지 않는다


def test_demo_plan_and_connect_later(env, tmp_path):
    s = _DemoAll()
    reply = _chat_all(s, "DOE 계획만 뽑아 줘. x1 -5~10, x2 0~15, 8점")
    sid = s.study_id
    assert "CSV" in reply and "해석은 돌리지 않았습니다" in reply and env.study(sid)["counts"] == {"pending": 8} and _no_flow(env)
    assert "어떤 변수" in _chat_all(_DemoAll(), "DOE 계획만 뽑아 줘")
    # 2주 뒤 새 대화: 번호를 몰라도 대기 중인 스터디를 찾는다. 승인 전에는 돌리지 않는다
    later = _DemoAll()
    assert "승인" in _chat_all(later, "이제 해석 방법 연결해서 돌려 줘")
    assert later.study_id == sid and env.study(sid)["counts"] == {"pending": 8} and env.study(sid)["study"]["adapter"] == "mock"
    done = _approve(env, later)
    assert "8건" in done and env.study(sid)["counts"] == {"complete": 8} and later.permissions == ["대기 중인 8점 해석 실행"]
    assert dds_study.list_models()["models"][0]["deployed"]
    assert "기다리는 스터디가 없습니다" in _chat_all(_DemoAll(), "해석 방법 연결해 줘")


def test_demo_same_flow_again_keeps_old_results(env):
    first = _DemoAll()
    _chat_all(first, "mock 솔버로 Branin 최적화하고 Simulator 배포해줘")
    assert "다시 돌릴 지난 작업 흐름이" in _chat_all(_DemoAll(), "신규 모델 나왔어. 지난번처럼 다시 해 줘")  # 승인 전이라 연결된 스터디가 없다. 없는 것을 있다고 하지 않는다
    _approve(env, first)
    old = first.study_id
    before = (env.cases(old), dds_study.list_models(study_id=old))
    s = _DemoAll()
    ask = _chat_all(s, "신규 모델 나왔어. 지난번처럼 다시 해 줘")
    assert "첨부" in ask and s.calls == ["dds-flow.list_flows"]  # 옛 모델로 또 돌리면 옛 결과와 같다
    reply = _chat_all(s, "신규 모델로 지난번처럼 다시. x1 -5~12\n\n[첨부 파일 — 프로젝트 작업 폴더 기준]\n- inputs/신형.prt (1.0 MB)")
    assert "x1 범위 -5~10 → -5~12" in reply and "신형.prt" in reply and "승인" in reply
    new = s.study_id
    assert new != old and env.cases(new) == []
    done = _approve(env, s)
    assert "지난 모델 최적" in done and "그대로" in done and env.study(new)["study"]["variables"][0]["upper"] == 12
    assert (env.cases(old), dds_study.list_models(study_id=old)) == before


def test_demo_takeover_shows_author_and_explains_what_needs_the_owner_pc(env, client, tmp_path, monkeypatch):  # noqa: F811
    owner = _DemoAll()
    sid = _script_study(env, "입력있음")
    assert "3개" in _chat_all(owner, "입력있음 스크립트 묶음 올려 줘 scripts/helper.py")
    table = dds_study.import_table(str(_write_csv(tmp_path / "t.csv", _rows(8))), name="표 스터디")["study_id"]
    author = dds_study.save_recipe(sid)["created_by_name"]
    d = env.project_dir() / "scripts"
    plain = dds_study.create_study("입력없음", [{"name": "x", "lower": 0, "upper": 1}], adapter="script", adapter_config={"tool": "python", "script": "scripts/run.py"})["study_id"]
    dds_study.save_recipe(plain)
    assert d.exists()

    _member(env, client, tmp_path, monkeypatch, name="member3")
    m = _DemoAll()
    # 서버 데이터로 학습: PC 해석 없음
    assert "해석은 돌리지 않았습니다" in _chat_all(m, "표 스터디 이어받아 다시 학습해 줘") and env.c.get(f"/api/studies/{table}/cases", headers=env.h).status_code == 200
    # 입력 모델이 없으면 돌리지 않는다
    m2 = _DemoAll()
    reply = _chat_all(m2, "입력있음 이어받아 2점 더 해석해 줘")
    assert "inputs/model.dat" in reply and "첨부" in reply and m2.flow_id is None
    # 입력이 필요 없으면 받기만 하고 승인을 기다린다. 승인 카드에 만든 사람
    m3 = _DemoAll()
    reply = _chat_all(m3, "입력없음 이어받아 3점 더 해석해 줘")
    assert f"{author}님이 만든 스크립트" in reply and "돌리지 않았습니다" in reply and env.study(plain)["counts"] == {}
    flow = env.c.get(f"/api/flows/{m3.flow_id}", headers=env.h).json()
    assert flow["status"] == "review" and f"{author}님이 만든 스크립트" in json.dumps(flow["nodes"], ensure_ascii=False)
    # 내 PC에서 돈 케이스의 Output은 이유와 할 일을 말한다
    env.c.post(f"/api/studies/{plain}/doe", json={"n": 2, "seed": 1}, headers=env.h)
    for c in env.cases(plain):
        service.on_case_status({"case_id": c["id"], "status": "complete", "results": {"y": 1.0}, "run_dir": "C:/my/run"})
    m4 = _DemoAll()
    reply = _chat_all(m4, "입력없음 이어받아 Output mass 추가해 줘 scripts/run.py")
    assert "뽑지 못했습니다" in reply and "그 PC를 켠" in reply and "→" in reply


@pytest.mark.parametrize("text,entry", [
    ("신규 모델로 지난번처럼 다시 해 줘", "rerun"),
    ("새 모델 나왔어", "rerun"),
    ("브래킷 스터디 이어받아 줘", "takeover"),
    ("DOE 계획만 뽑아 줘. x1 0~1", "plan"),
    ("해석 방법 연결해 줘", "connect"),
    ("스크립트 묶음 올려 줘", "recipe"),
    ("PC Agent 연결돼 있어?", "question"),  # '연결'만으로 해석 방법 연결로 읽지 않는다
])
def test_pick_entry_new_entries(text, entry):
    assert demo_agent.pick_entry(text)[0] == entry


# ---------- pc_verify chat ----------
def _pc_verify():
    spec = importlib.util.spec_from_file_location("pc_verify_for_test", ROOT / "scripts" / "pc_verify.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_pc_verify_judge_counts_and_refuses_unknown_items():
    pv = _pc_verify()
    m = {"finished": True, "errors": [], "deployed_models": 1, "cases_run": 0, "tools": ["dds-study.import_table"], "flows": 0, "flows_approved": 0, "reply": "Simulator 배포", "first_reply_sec": 3.5}
    ok = pv.judge(["deployed", "no_cases_run", "no_errors", "finished", "flows=0", "first_reply_sec<=4", "tool:import_table", "no_tool:run_cases", "reply~Simulator", "no_reply~Branin", ""], m)
    assert all(c["ok"] for c in ok) and len(ok) == 10
    bad = pv.judge(["flows=1", "first_reply_sec<=1", "tool:run_cases", "no_tool:import_table", "reply~없는글", "오타항목", "flow=0"], m)
    assert not any(c["ok"] for c in bad)
    # 해석을 돌렸다면, 케이스 수가 같아도(도구를 불렀으면) 통과하지 않는다
    assert not pv.judge(["no_cases_run"], {**m, "tools": ["dds-study.run_cases"]})[0]["ok"]
    assert not pv.judge(["no_cases_run"], {**m, "cases_run": 3})[0]["ok"]
    # 첫 응답이 오지 않았으면 재지 못한 것이다. 통과가 아니다
    assert not pv.judge(["first_reply_sec<=100"], {**m, "first_reply_sec": None})[0]["ok"]
    assert pv.is_note("새 세션으로 이어 갑니다") and not pv.is_note("에이전트 오류")


def test_pc_verify_approves_only_this_chats_flows():
    pv = _pc_verify()
    flows = [{"id": 1, "status": "review"}, {"id": 2, "status": "review"}, {"id": 3, "status": "complete"}, {"id": 4, "status": "review"}]
    called = {"role": "tool", "meta": {"title": "dds-flow.propose_flow", "output": '{"flow_id": 2, "status": "review"}'}}
    assert [f["id"] for f in pv.own_review_flows(flows, [called], {1, 2, 3, 4})] == [2]
    # 이 대화가 부르지 않았으면 프로젝트의 검토 중인 Flow를 건드리지 않는다
    assert pv.own_review_flows(flows, [{"role": "tool", "meta": {"title": "dds-study.list_studies"}}, {"role": "agent", "content": "flow_id: 2"}], {1}) == []
    # 도구 출력이 잘려 번호를 못 읽으면 시작 뒤에 새로 생긴 것만
    cut = {"role": "tool", "meta": {"title": "mcp__dds-flow__propose_flow", "output": ""}}
    assert [f["id"] for f in pv.own_review_flows(flows, [cut], {1, 2, 3})] == [4]
    assert pv.chat_flow_ids([called, cut]) == ({2}, 2)


def test_pc_verify_network_failures_end_with_a_reason_not_a_stack_trace(monkeypatch, tmp_path):
    pv = _pc_verify()
    api = pv.Api("http://127.0.0.1:9", "t")

    def raises(err):
        def open_(*a, **kw):
            raise err
        return open_

    monkeypatch.setattr(pv.urllib.request, "urlopen", raises(TimeoutError("timed out")))
    with pytest.raises(SystemExit, match="답하지 않았습니다"):
        api.call("POST", "/api/sessions/1/prompt", {"text": "x"}, timeout=5)
    monkeypatch.setattr(pv.urllib.request, "urlopen", raises(ConnectionResetError("reset")))
    with pytest.raises(SystemExit, match="끊겼습니다"):
        api.call("GET", "/x")
    # 앞 턴이 도는 동안의 409는 기다렸다 보낸다
    seen = []

    class Busy:
        def call(self, method, path, body=None, timeout=60):
            seen.append(timeout)
            if len(seen) == 1:
                raise SystemExit("서버 오류 409 (x): 진행 중")
            return {"ok": True}

    monkeypatch.setattr(pv.time, "sleep", lambda _s: None)
    assert pv._send_prompt(Busy(), 1, "x", [], time.time() + 60) == {"ok": True} and seen[0] >= 300  # 예전 120초 제한은 느린 환경에서 검증을 끝내 버렸다
    monkeypatch.setenv("DDS_VERIFY_OUT", str(tmp_path))
    assert pv.out_dir().parent == tmp_path  # 시험 결과가 data/로 가지 않는다
