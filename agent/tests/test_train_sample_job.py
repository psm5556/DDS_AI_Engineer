"""PC Agent의 case.train_sample: 큰 case.h5는 PC에 두고, 학습 샘플만 만들어 올린다."""
from pathlib import Path

from agent.dds_agent.config import AgentSettings
from agent.dds_agent.executor import CaseExecutor
from agent.tests.test_train_h5 import _case
from cae_adapters.h5store import pack_case


class _Api:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def upload(self, path, file):
        if self.fail:
            raise RuntimeError("서버 오류 413")
        self.calls.append((path, Path(file).name, Path(file).stat().st_size))
        return {"ok": True}


def _executor(tmp_path, api):
    msgs = []
    e = CaseExecutor(AgentSettings(workspace_dir=tmp_path / "ws"), msgs.append)
    e.api = api
    return e, msgs


def _job(run_dir, **kw):
    return {"type": "case.train_sample", "dataset_id": 7, "case_id": 3, "study_id": 1, "project_id": 1, "run_dir": str(run_dir),
            "spec": {"targets": ["von_mises"]}, "spec_hash": "abc123abc123", **kw}


def test_sample_is_built_uploaded_and_leaves_nothing_behind(tmp_path):
    run = tmp_path / "C0003_doe"
    run.mkdir()
    _case(run)
    before = sorted(p.name for p in run.iterdir())
    api = _Api()
    e, msgs = _executor(tmp_path, api)
    e._train_sample(_job(run))
    assert api.calls[0][0] == "/api/cases/3/train_sample?spec_hash=abc123abc123" and api.calls[0][2] > 0
    assert msgs == [{"type": "case.train_sample", "dataset_id": 7, "case_id": 3, "ok": True, "error": None}]
    assert sorted(p.name for p in run.iterdir()) == before  # 케이스 폴더에 임시 파일을 남기지 않는다


def test_missing_case_h5_is_reported(tmp_path):
    run = tmp_path / "C0003_doe"
    run.mkdir()
    (run / "params.json").write_text("{}", encoding="utf-8")
    e, msgs = _executor(tmp_path, _Api())
    e._train_sample(_job(run))
    assert msgs[0]["ok"] is False and "case.h5" in msgs[0]["error"]


def test_missing_target_is_reported(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    pack_case(run)
    e, msgs = _executor(tmp_path, _Api())
    e._train_sample(_job(run))
    assert msgs[0]["ok"] is False and "von_mises" in msgs[0]["error"]


def test_upload_failure_is_reported(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    _case(run)
    e, msgs = _executor(tmp_path, _Api(fail=True))
    e._train_sample(_job(run))
    assert msgs[0]["ok"] is False and "413" in msgs[0]["error"]
