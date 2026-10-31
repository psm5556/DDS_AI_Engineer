"""dds-study.study_insights: 서버가 쓴 문장을 고치지 않고 AI에게 넘기고, 계산 중이면 실패로 보이지 않게 알린다."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "mcp_servers"))


class FakeApi:
    def __init__(self, replies):
        self.replies = list(replies)
        self.posts = []

    def get(self, path):
        assert path.endswith("/insights?detail=summary"), path
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def post(self, path, body=None):
        self.posts.append((path, body))
        return {"published": [{"artifact_id": 41, "embed": "![영향도](artifact:41)"}]}


SEC = {"key": "convergence", "title": "더 돌리면 좋아질까요?", "state_label": "좋음", "sentence": "처음 DOE 최고 2.71 mm에서 2.61 mm로 3.7% 좋아졌습니다.", "notes": ["메모"],
       "basis_label": "해석으로 확인한 값", "facts": {"start": 2.71}, "variants": [{"name": "y", "state_label": "좋음", "sentence": "문장"}]}
READY = {"status": "ready", "overview": {"facts": {"complete": 60}, "next": {"text": "보고서를 쓰면 됩니다.", "request": "보고서 써 줘", "label": "보고서 요청"}}, "sections": [SEC]}


def test_sentences_pass_through_unchanged(monkeypatch):
    import dds_study

    monkeypatch.setattr(dds_study, "api", FakeApi([READY]))
    out = dds_study.study_insights(3)
    sec = out["sections"][0]
    assert sec["sentence"] == SEC["sentence"] and sec["state"] == "좋음" and sec["question"] == SEC["title"] and sec["basis"] == "해석으로 확인한 값"
    assert sec["by_output"] == [{"output": "y", "state": "좋음", "sentence": "문장"}]
    assert out["next"]["request"] == "보고서 써 줘" and "바꾸지 말고" in out["note"]
    assert "published" not in out


def test_publish_returns_embeds_with_flow(monkeypatch):
    import dds_study

    fake = FakeApi([READY])
    monkeypatch.setattr(dds_study, "api", fake)
    out = dds_study.study_insights(3, publish=True, flow_id=7, node_id="judge")
    assert out["published"][0]["embed"] == "![영향도](artifact:41)"
    assert fake.posts == [("/api/studies/3/insights/publish", {"flow_id": 7, "node_id": "judge"})]


def test_waits_for_computing_then_says_it_is_not_a_failure(monkeypatch):
    import dds_study

    computing = {"status": "computing", "overview": {"facts": {}, "next": None},
                 "sections": [{**SEC, "key": "trust", "pending": True}, SEC]}
    monkeypatch.setattr(dds_study, "api", FakeApi([computing, READY]))
    monkeypatch.setattr(dds_study.time, "sleep", lambda _: None)
    assert dds_study.study_insights(3, wait_sec=5)["status"] == "ready"

    monkeypatch.setattr(dds_study, "api", FakeApi([computing]))
    out = dds_study.study_insights(3, wait_sec=0)
    assert out["status"] == "computing" and "실패가 아니니" in out["note"]
    assert [x["key"] for x in out["sections"]] == ["convergence"]  # 계산 중인 항목은 판정처럼 내보내지 않는다


def test_computing_is_not_published_and_says_not_to_quote(monkeypatch):
    """계산 중이면 게시하지 않는다. 낡은 문장이 보고서에 들어가면 화면의 숫자와 어긋난다."""
    import dds_study
    fake = FakeApi([{"status": "computing", "stale": True, "overview": {"facts": {}, "next": {"text": "계산 중", "request": ""}}, "sections": []}])
    monkeypatch.setattr(dds_study, "api", fake)
    out = dds_study.study_insights(3, wait_sec=0, publish=True)
    assert out["published"] == [] and not fake.posts and "인용하지" in out["note"]


def test_demo_answers_panel_buttons_instead_of_starting_the_scenario():
    sys.path.insert(0, str(ROOT / "agent"))
    from dds_agent.demo_agent import is_panel_request, pick_mode
    assert is_panel_request("스터디 '탈수'에 능동학습으로 해석 점을 더 넣어 예측 정확도를 올려 줘.")
    assert not is_panel_request("세탁기 진동을 줄여 줘") and pick_mode("세탁기 진동을 줄여 줘") == "washer"
