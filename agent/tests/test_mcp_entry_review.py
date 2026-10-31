"""어느 시점에서든 진입 검토에서 재현한 결함의 회귀 시험: MCP 도구·데모 에이전트·PC Agent 쪽."""
import inspect
import json
import threading
from pathlib import Path

import pytest

from agent.tests.test_mcp_entry_points import (  # noqa: F401 — 같은 fixture를 쓴다
    INSTRUCTIONS, _approve, _chat, _chat_all, _DemoAll, _DemoHarness, _member, _raises, _rows, _script_study, _write_csv, client, dds_flow, dds_study, env,
)
from agent.dds_agent.config import AgentSettings  # noqa: E402
from agent.dds_agent.executor import CaseExecutor  # noqa: E402


def _case(root: Path, name: str, params: dict, results: dict):
    d = root / name
    d.mkdir(parents=True)
    (d / "params.json").write_text(json.dumps(params), encoding="utf-8")
    (d / "results.json").write_text(json.dumps(results), encoding="utf-8")


# ---------- 입구가 달라도 같은 표
def test_pasted_table_reads_the_same_through_inspect_and_import(env):
    d = env.project_dir() / "inputs"
    d.mkdir(parents=True, exist_ok=True)
    text = "하중\tt\tstress\n"
    for i in range(12):
        text += f"{(i // 4 + 1) * 100 if i % 4 == 0 else ''}\t{i * 0.3 + 1}\t{i * 2.0 + (i % 4) * 3 + 5}\n"
    (d / "붙여넣은_표_1.tsv").write_text(text, encoding="utf-8")
    info = dds_study.inspect_table("inputs/붙여넣은_표_1.tsv")
    assert any("병합 셀" in w for w in info["warnings"]) and "하중" in info["inputs"]
    rep = dds_study.import_table("inputs/붙여넣은_표_1.tsv", outputs=["stress"])
    assert rep["registered"] == 12 and rep["excluded_count"] == 0 and any("병합 셀" in w for w in rep["warnings"])
    # 화면에서 같은 글을 붙여 넣으면 새 스터디가 아니라 같은 스터디다
    r = env.c.post(f"/api/projects/{env.pid}/studies/import/text", headers=env.h, json={"text": text, "outputs": [{"name": "stress"}]}).json()
    assert r["reused"] and r["study_id"] == rep["study_id"]


# ---------- 결과 폴더
def test_import_cases_accepts_results_that_echo_inputs(env, tmp_path):
    root = tmp_path / "echo"
    for i, (t, w, s) in enumerate(_rows(6)):
        _case(root, f"c{i}", {"t": t, "w": w}, {"t": t, "w": w, "s": s})
    rep = dds_study.import_cases(folder=str(root))
    assert rep["registered"] == 6 and [v["name"] for v in rep["variables"]] == ["t", "w"] and [o["name"] for o in rep["outputs"]] == ["s"]
    assert any("입력값이 함께" in x for x in rep["warnings"])
    # 이름을 지정해도 된다
    root2 = tmp_path / "echo2"
    for i, (t, w, s) in enumerate(_rows(6, seed=8)):
        _case(root2, f"c{i}", {"t": t, "w": w}, {"t": t, "w": w, "s": s})
    assert dds_study.import_cases(folder=str(root2), inputs=["t", "w"], outputs=["s"], name="지정")["registered"] == 6
    # 값이 다르면 어느 쪽인지 지정하라고 한다. 원본 이름을 바꾸라고 하지 않는다
    root3 = tmp_path / "differ"
    for i, (t, w, s) in enumerate(_rows(4, seed=9)):
        _case(root3, f"c{i}", {"t": t, "s": 1.0}, {"s": s + 5})
    text = _raises(dds_study.import_cases, folder=str(root3))
    assert "inputs·outputs" in text and "원본 폴더는 고치지 않습니다" in text
    ok = dds_study.import_cases(folder=str(root3), inputs=["t"], outputs=["s"], name="출력이 s")
    assert ok["registered"] == 4 and [o["name"] for o in ok["outputs"]] == ["s"]


def test_import_cases_leaves_failed_folders_out_and_says_so(env, tmp_path):
    root = tmp_path / "일부 실패"
    for i, (t, w, s) in enumerate(_rows(6)):
        _case(root, f"c{i}", {"t": t, "w": w}, {"s": s})
    _case(root, "bad1", {"t": 9.0, "w": 9.0}, {"status": "failed", "s": 0.0, "error": "diverged"})
    _case(root, "bad2", {"t": 8.0, "w": 8.0}, {"s": 1e6, "converged": False})
    scan = dds_study.scan_results(str(root))
    assert scan["ready"] == 6 and scan["failed"] == 2
    rep = dds_study.import_cases(folder=str(root))
    assert rep["registered"] == 6 and rep["skipped_count"] == 2 and any("실패 표시" in w for w in rep["warnings"])
    assert all("해석 실패" in x["missing"] for x in rep["skipped_folders"])


# ---------- 신규 모델
def test_clone_with_new_model_points_at_the_new_model(env):
    sid = _script_study(env, "옛 모델 스터디")
    plain = dds_study.clone_study(sid, name="그대로")
    assert plain["adapter_config"]["inputs"] == ["inputs/model.dat"] and "입력 모델이 지난번과 같습니다" in plain["warning"]
    new = dds_study.clone_study(sid, name="신규", model_files=["inputs/new_model.dat"])
    assert new["adapter_config"]["inputs"] == ["inputs/new_model.dat"] and "warning" not in new
    assert env.study(sid)["study"]["adapter_config"]["inputs"] == ["inputs/model.dat"]
    # 다른 확장자여도 옛 입력이 하나면 바꾼다. 여럿이면 같은 종류만 바꾼다
    assert dds_study._with_new_models({"inputs": ["inputs/a.prt"]}, ["inputs/b.stp"])["inputs"] == ["inputs/b.stp"]
    assert dds_study._with_new_models({"inputs": ["inputs/a.prt", "inputs/m.inp"]}, ["inputs/b.inp"])["inputs"] == ["inputs/a.prt", "inputs/b.inp"]


def test_restore_recipe_reports_from_the_clone_config(env, client, tmp_path, monkeypatch):  # noqa: F811
    sid = _script_study(env, "복제 원본")
    dds_study.save_recipe(sid, files=["scripts/helper.py"])
    new = dds_study.clone_study(sid, name="복제본", model_files=["inputs/new_model.dat"])
    _member(env, client, tmp_path, monkeypatch, name="clone_member")
    got = dds_study.restore_recipe(new["study_id"])
    assert got["input_files_missing"] == ["inputs/new_model.dat"] and got["adapter_config"]["inputs"] == ["inputs/new_model.dat"]


def test_demo_new_model_clone_uses_the_attached_model(env):
    sid = _script_study(env, "브래킷")
    env.c.post(f"/api/studies/{sid}/doe", json={"n": 2, "seed": 1}, headers=env.h)
    f = env.c.post(f"/api/projects/{env.pid}/flows", headers=env.h, json={"title": "브래킷 해석", "goal": "", "nodes": [{"id": "doe", "label": "DOE", "type": "doe"}], "edges": []}).json()
    env.c.post(f"/api/studies/{sid}/doe", json={"n": 1, "seed": 2, "flow_id": f["id"]}, headers=env.h)
    s = _DemoAll()
    reply = _chat_all(s, "신규 모델 나왔어. 지난번처럼 다시 돌려 줘\n\n[첨부 파일 — 프로젝트 작업 폴더 기준, 원본이므로 수정하지 말고 복사해서 사용]\n- inputs/new_model.dat (1 KB)")
    assert "new_model.dat" in reply
    assert env.study(s.study_id)["study"]["adapter_config"]["inputs"] == ["inputs/new_model.dat"]
    assert env.study(sid)["study"]["adapter_config"]["inputs"] == ["inputs/model.dat"]


# ---------- 프로젝트 이름
def test_project_name_is_not_replaced_once_in_use(env, tmp_path):
    dds_flow.set_project_info(name="첫 이름", description="설명")
    assert env.c.get(f"/api/projects/{env.pid}", headers=env.h).json()["name"] == "첫 이름"  # 막 만든 프로젝트는 된다
    dds_study.import_table(str(_write_csv(tmp_path / "a.csv", _rows(8))), name="쓰는 중")
    r = dds_flow.set_project_info(name="다른 이름")
    assert r["skipped"] and r["name"] == "첫 이름" and env.c.get(f"/api/projects/{env.pid}", headers=env.h).json()["name"] == "첫 이름"
    assert dds_flow.set_project_info(name="바꿔 달라고 함", force=True)["name"] == "바꿔 달라고 함"
    # 데모가 표를 또 받아도 공유 프로젝트 이름이 바뀌지 않는다
    s = _DemoHarness()
    _chat(s, f'"{_write_csv(tmp_path / "b.csv", _rows(8, seed=5))}" 표로 Simulator 만들어 줘')
    assert env.c.get(f"/api/projects/{env.pid}", headers=env.h).json()["name"] == "바꿔 달라고 함"


# ---------- 데모: 있는 모델로 하는 일, 반려
@pytest.fixture()
def deployed(env, tmp_path):
    sid = dds_study.import_table(str(_write_csv(tmp_path / "t.csv", _rows(12), head="두께,폭,stress")), outputs=["stress"])["study_id"]
    v1 = dds_study.train_surrogate(sid)
    dds_study.deploy_model(v1["model_id"])
    return sid, v1


@pytest.mark.parametrize("text,expect", [
    ("배포한 모델로 두께 2, 폭 20일 때 stress 예측해 줘", "예측값"),
    ("Simulator 공유 링크 만들어 줘", "공유"),
    ("이 스터디로 stress 최소 최적 설계 찾아 줘", "후보"),
])
def test_demo_work_on_a_deployed_model_needs_no_flow_in_a_new_chat(env, deployed, text, expect):
    s = _DemoHarness()
    reply = _chat(s, text)
    assert expect in reply and "승인" not in reply and "dds-flow.propose_flow" not in s.calls
    assert env.c.get(f"/api/projects/{env.pid}/flows", headers=env.h).json() == []


def test_demo_rollback_in_a_new_chat(env, deployed, tmp_path):
    sid, v1 = deployed
    dds_study.import_table(str(_write_csv(tmp_path / "t2.csv", _rows(4, seed=6), head="두께,폭,stress")), study_id=sid)
    v2 = dds_study.train_surrogate(sid)
    dds_study.deploy_model(v2["model_id"])
    s = _DemoHarness()
    reply = _chat(s, "방금 Simulator 예전 버전으로 되돌려 줘")
    assert "v1" in reply and "dds-flow.propose_flow" not in s.calls
    assert next(m for m in dds_study.list_models()["models"] if m["deployed"])["model_id"] == v1["model_id"]


def test_demo_model_words_without_a_model_still_start_from_scratch(env):
    s = _DemoAll()
    _chat_all(s, "배포한 모델로 stress 예측해 줘")
    assert "dds-flow.propose_flow" in s.calls


def test_demo_rejected_flow_is_not_a_dead_end(env):
    s = _DemoAll()
    _chat_all(s, "mock 솔버로 Branin 최적화하고 Simulator 배포해줘")
    first = s.flow_id
    flow = env.c.get(f"/api/flows/{first}", headers=env.h).json()
    env.c.put(f"/api/flows/{first}/status", json={"status": "rejected", "version": flow["version"]}, headers=env.h).raise_for_status()
    # 화면이 보내는 문구: 묻기만 한다
    reply = _chat_all(s, f"작업 흐름 '{flow['title']}'(#{first})을 반려했습니다. 무엇을 바꾸면 좋을지 물어보고 다시 제안해 주세요.")
    assert "무엇을 바꿀까요" in reply and s.flow_id == first
    # 고칠 점을 말하면 새로 제안한다
    reply = _chat_all(s, "DOE를 10점으로 줄여서 다시 제안해 줘")
    assert "승인" in reply and s.flow_id != first
    new = env.c.get(f"/api/flows/{s.flow_id}", headers=env.h).json()
    assert new["status"] == "review" and new["nodes"][0]["label"].startswith("DOE 10점")
    assert env.c.get(f"/api/flows/{first}", headers=env.h).json()["status"] == "rejected"


def test_demo_review_state_is_plain_words(env):
    s = _DemoAll()
    _chat_all(s, "mock 솔버로 Branin 최적화하고 Simulator 배포해줘")
    reply = _chat_all(s, "돌려 줘")
    assert "승인 전" in reply and "review" not in reply


# ---------- PC Agent: 스크립트 자동 업로드
class _Api:
    def __init__(self, recipe=None, me=1):
        self.recipe, self.me, self.uploads = recipe or {"has": False}, me, []

    def get(self, path, **_):
        return {"id": self.me} if path == "/api/auth/me" else self.recipe

    def upload_form(self, path, files, data=None, timeout=600):
        self.uploads.append((path, files["file"][0], data))
        return {}


def _executor(tmp_path) -> CaseExecutor:
    return CaseExecutor(AgentSettings(server_url="http://x", token="t", workspace_dir=tmp_path / "ws"), lambda m: None)


def _job(sid=7, **cfg):
    return {"study_id": sid, "adapter": "script", "adapter_config": {"tool": "python", "script": "scripts/run.py", **cfg}}


def test_script_is_shared_after_first_finished_case(tmp_path):
    ex = _executor(tmp_path)
    ex.api = _Api()
    root = tmp_path / "p"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    ex._share_recipe(_job(), root)
    ex._share_recipe(_job(), root)  # 같은 내용이면 또 올리지 않는다
    assert len(ex.api.uploads) == 1 and ex.api.uploads[0][0] == "/api/studies/7/recipe"
    (root / "scripts" / "run.py").write_text("print(2)", encoding="utf-8")
    ex.api.recipe = {"has": True, "created_by": 1, "files": [{"path": "scripts/run.py", "sha256": "old"}]}
    ex._share_recipe(_job(), root)  # 내가 고쳤으면 새 판을 올린다
    assert len(ex.api.uploads) == 2


def test_script_share_never_replaces_a_colleagues_bundle_or_fails_the_case(tmp_path):
    ex = _executor(tmp_path)
    ex.api = _Api({"has": True, "created_by": 99, "files": [{"path": "scripts/run.py", "sha256": "동료"}]})
    root = tmp_path / "p"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "run.py").write_text("print(1)", encoding="utf-8")
    ex._share_recipe(_job(), root)
    assert ex.api.uploads == []
    # 스크립트가 없어도, 서버가 거절해도 케이스 결과를 막지 않는다
    ex.api = _Api()
    ex._share_recipe(_job(script="scripts/없음.py"), root)
    ex._share_recipe({"study_id": 1, "adapter": "mock", "adapter_config": {}}, root)
    assert ex.api.uploads == []

    class _Refuses(_Api):
        def upload_form(self, *a, **k):
            raise RuntimeError("비밀값")
    ex.api = _Refuses()
    ex._share_recipe(_job(), root)


def test_active_cases_lists_queued_and_running(tmp_path):
    ex = _executor(tmp_path)
    assert ex.active_cases() == []
    ex._stops[12] = threading.Event()
    ex._stops[5] = threading.Event()
    assert ex.active_cases() == [5, 12]


# ---------- 지침과 도구 설명
def test_instructions_do_not_promise_paths_that_do_not_exist():
    text = INSTRUCTIONS.read_text(encoding="utf-8")
    assert "그것으로 뽑습니다" not in text and "원본이 없어도 case.h5로 Output 추가" not in text
    assert "대신 뽑는 경로는 없습니다" in text
    assert "model_files" in text and "`stalled`" in text and "`flow_id`" in text
    assert "자동으로 올립니다" in text
    # 긴 추출이 승인 대상인지 두 규칙이 어긋나지 않는다
    head = text.split("## 1. Flow 승인", 1)[1].split("1. 요청을 받으면", 1)[0]
    assert "값만 뽑는 일은 오래 걸려도 승인 대상이 아닙니다" in head
    # 프로젝트 이름은 막 만든 프로젝트에서만
    assert "막 만들어진 프로젝트" in text


def test_add_outputs_description_does_not_copy_large_results():
    doc = inspect.getdoc(dds_study.add_outputs)
    assert 'inputs=["' not in doc and "복사하지 않는다" in doc


def test_recipe_text_shows_no_tool_name_to_the_user(env):
    sid = _script_study(env, "묶음없음")
    text = _raises(dds_study.restore_recipe, sid)
    assert "save_recipe" not in text and "PC가 켜져 있을 때" in text and "결과 조회" in text


# ---------- 줄일지 키울지·암호화된 요약
def test_optimize_asks_which_way_instead_of_minimizing(env, tmp_path):
    sid = dds_study.import_table(str(_write_csv(tmp_path / "t.csv", _rows(12), head="두께,폭,수명")), outputs=["수명"])["study_id"]
    dds_study.train_surrogate(sid)
    text = _raises(dds_study.optimize, sid, objective="수명")
    assert "수명" in text and "줄일까요, 키울까요" in text
    assert dds_study.optimize(sid, objective="수명", goal="max")["goal"] == "max"
    assert "줄일까요, 키울까요" in _raises(dds_study.suggest_next, sid, objective="수명")


def test_demo_this_study_optimize_asks_when_goal_is_unknown(env, tmp_path):
    sid = dds_study.import_table(str(_write_csv(tmp_path / "t.csv", _rows(12), head="두께,폭,수명")), outputs=["수명"])["study_id"]
    dds_study.deploy_model(dds_study.train_surrogate(sid)["model_id"])
    s = _DemoHarness()
    reply = _chat(s, "이 스터디로 최적 설계 찾아 줘")
    assert "줄일까요, 키울까요" in reply and "후보" not in reply and "dds-flow.propose_flow" not in s.calls


def test_scan_results_tells_encrypted_summary_apart_from_no_values(env, tmp_path):
    root = tmp_path / "암호화 결과"
    d = root / "t=1.0_w=30"
    d.mkdir(parents=True)
    (d / "summary.csv").write_bytes(bytes(range(256)) * 20)
    scan = dds_study.scan_results(str(root))
    assert scan["encrypted"] == 1 and scan["cases"][0]["encrypted"] is True and "암호화" in scan["cases"][0]["missing"]
    assert "하나도" not in scan["cases"][0]["missing"]
    s = _DemoHarness()
    reply = _chat(s, f'이미 해석한 결과가 "{root}" 에 있어. 이걸로 Simulator 만들어 줘')
    assert "암호화" in reply and "붙여 넣어" in reply


def test_inspect_says_not_a_table_for_model_files(env, tmp_path):
    p = tmp_path / "m.cae"
    p.write_bytes(b"\x00\x01ABAQUS" + bytes(range(256)) * 20)
    text = _raises(dds_study.inspect_table, str(p))
    assert "표가 아닙니다" in text and "암호화" not in text


def test_delete_case_clears_a_filled_doe_point(env):
    sid = dds_study.create_study("계획", [{"name": "x", "lower": 0, "upper": 10}], objectives=[{"name": "y", "goal": "min"}])["study_id"]
    ids = dds_study.generate_doe(sid, n=3, seed=1)["case_ids"]
    x0 = next(c for c in env.cases(sid) if c["id"] == ids[0])["params"]["x"]
    env.c.post(f"/api/studies/{sid}/cases/import", json={"rows": [{"x": x0, "y": 1.0}]}, headers=env.h).raise_for_status()
    assert dds_study.delete_case(sid, ids[0])["reset"] is True
    assert next(c for c in env.cases(sid) if c["id"] == ids[0])["status"] == "pending"
