"""스크립트 어댑터: 단계마다 쓴 results.json을 모으고, Output 추가 시 추출 단계만 다시 돌려 기존 결과를 지킨다."""
from agent.dds_agent.job_runner import LocalRunner
from cae_adapters.base import read_results
from cae_adapters.script import ScriptAdapter

SOLVE = "import json\nopen('results.json','w').write(json.dumps({'mass': 2.0}))\nopen('solver.out','w').write('7.5')\n"
# 추출 스크립트는 새 키만 results.json에 덮어쓴다 — 플랫폼이 기존 값을 합쳐야 한다
EXTRACT = "import json\nv=float(open('solver.out').read())\nopen('results.json','w').write(json.dumps({'disp': v}))\n"


def test_post_steps_merge_and_extract_existing(tmp_path):
    proj = tmp_path / "proj"
    (proj / "scripts").mkdir(parents=True)
    (proj / "scripts" / "solve.py").write_text(SOLVE, encoding="utf-8")
    (proj / "scripts" / "extract.py").write_text(EXTRACT, encoding="utf-8")
    runner, ad = LocalRunner(), ScriptAdapter()

    # 1) 처음에는 해석만: mass만 나온다
    case = proj / "C0001"
    cfg = {"tool": "python", "script": "scripts/solve.py"}
    out = ad.run_case({"t": 1.0}, case, cfg, runner, {}, project_dir=proj)
    assert out.status == "complete" and out.results == {"mass": 2.0}

    # 2) Output 추가: 해석 없이 추출 단계만 → 새 값만 돌려주고 results.json에는 둘 다 남는다
    step = {"tool": "python", "script": "scripts/extract.py"}
    ex = ad.extract({"t": 1.0}, case, cfg, [step], ["disp"], runner, {}, project_dir=proj)
    assert ex.status == "complete" and ex.results == {"disp": 7.5}
    assert read_results(case / "results.json") == {"mass": 2.0, "disp": 7.5}

    # 3) 이후 케이스: post로 붙은 추출 단계가 results.json을 새로 써도 해석 값이 남는다
    out = ad.run_case({"t": 2.0}, proj / "C0002", {**cfg, "post": [step]}, runner, {}, project_dir=proj)
    assert out.results == {"mass": 2.0, "disp": 7.5}

    # 추출 단계가 그 키를 쓰지 않으면 실패로 알린다(기존 결과는 그대로)
    ex = ad.extract({"t": 1.0}, case, cfg, [step], ["stress"], runner, {}, project_dir=proj)
    assert ex.status == "failed" and "stress" in ex.error
    assert read_results(case / "results.json") == {"mass": 2.0, "disp": 7.5}
