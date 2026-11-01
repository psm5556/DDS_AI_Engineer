"""화면의 예시가 툴 없이 끝까지 되는가, 데모가 현재 기능과 맞는가.
예시 문구는 화면 코드(frontend/src/format.ts)에서 그대로 읽는다. 문구를 바꾸면 여기서 데모 시나리오와 맞는지 바로 드러난다."""
import asyncio
import re
from pathlib import Path

import pytest

from agent.dds_agent import demo_agent
from agent.dds_agent.demo_agent import pick_entry, pick_mode

ROOT = Path(__file__).resolve().parents[2]
FORMAT = (ROOT / "frontend" / "src" / "format.ts").read_text(encoding="utf-8")
CHAT = (ROOT / "frontend" / "src" / "chat" / "ChatView.tsx").read_text(encoding="utf-8")
HOME = (ROOT / "frontend" / "src" / "pages" / "DashboardPage.tsx").read_text(encoding="utf-8")
EXAMPLES = dict(re.findall(r'^\s+(\w+): "(연습용 계산[^"]+)"', FORMAT, re.M))


def test_examples_are_read_from_the_screen_code():
    assert set(EXAMPLES) == {"whole", "washer", "two"}


@pytest.mark.parametrize(("key", "mode"), [("whole", "bracket"), ("washer", "washer"), ("two", "washer2")])
def test_each_example_reaches_its_scenario(key, mode):
    body = EXAMPLES[key]
    assert pick_entry(body) == ("flow", None)
    assert pick_mode(body) == mode


def test_examples_need_no_cad_tool_and_use_plain_words():
    for body in EXAMPLES.values():
        for word in ("NX", "Abaqus", "mock", "솔버", "Branin", "BO", "GP"):
            assert word not in body, body


def test_screens_use_only_those_examples():
    """누르면 바로 보내는 빈 대화 예시에 툴이 필요한 말이 다시 들어오면 안 된다."""
    block = re.search(r"CHAT_SUGGEST = \[(.*?)\];", CHAT, re.S).group(1)
    assert "NX" not in block and "Abaqus" not in block and "mock" not in block and "PRACTICE_EXAMPLES." in block
    starters = re.search(r"const STARTERS.*?\n\];", HOME, re.S).group(0)
    assert "NX" not in starters and "Abaqus" not in starters
    assert starters.count("PRACTICE_EXAMPLES.") == 3 and "askFile" in starters  # 툴이 필요한 하나는 먼저 파일을 받는다


@pytest.mark.parametrize("body", ["최적화 돌려 줘", "DOE를 돌려서 예측 모델을 만들어 줘", "해석 돌려 줘"])
def test_default_scenario_is_the_drum_washer_not_a_math_function(body):
    assert pick_mode(body) == "washer"


def test_branin_only_when_named():
    assert pick_mode("연습용 계산으로 Branin 최적화해 줘") == "branin"


@pytest.mark.parametrize("body", ["이 프로젝트에 스터디 있어?", "지금 모델 몇 개야?", "어떤 툴이 있어?"])
def test_questions_are_still_questions_after_changing_the_default(body):
    assert pick_entry(body) == ("question", None)


def test_washer_named_question_goes_to_flow():
    assert pick_entry("세탁기 진동 줄여 줘")[0] == "flow"


class _Session:
    """_generic_report가 쓰는 만큼만 흉내 낸다."""
    def __init__(self, practice="연습용 안내 → 설계 판단에 쓰지 마세요."):
        self.flow_id, self.study_id, self.body, self.title, self.practice = 1, None, None, None, practice

    async def tool(self, server, name, args):
        if name == "get_report_context":
            return {"flows": [{"flow_id": 1, "title": "브래킷 전체 과정", "steps": [
                {"no": 1, "id": "doe", "label": "DOE", "detail": ""}, {"no": 2, "id": "report", "label": "보고서 작성", "detail": ""}]}],
                "results": [{"step": "1단계 · DOE", "embed": "![표](artifact:1)"}, {"step": "2단계 · 보고서 작성", "embed": "![결론](artifact:2)"}],
                "studies": [], "practice_note": self.practice}
        if name == "write_report":
            self.body, self.title = args["body"], args["title"]
            return {}
        raise AssertionError(name)

    async def say(self, *_a, **_k):
        pass


def _report(**kw):
    s = _Session()
    asyncio.run(demo_agent.DemoAgent(None)._generic_report(s, **kw))
    return s.body


def test_report_has_one_conclusion_section_and_says_practice():
    """'결론과 권고'와 그 앞 결과 절의 '권고'가 겹쳤다."""
    body = _report(conclusion=["- 가장 가벼운 설계: 두께 6 mm"], advice=["1. 실제 해석으로 확인합니다."])
    assert body.count("## 결론과 권고") == 1
    assert body.count("권고") == 1
    assert "artifact:2" not in body and "artifact:1" in body  # 보고서 단계의 결론 카드는 본문에 다시 넣지 않는다
    assert "- 가장 가벼운 설계: 두께 6 mm" in body
    assert "## 한계" in body and "설계 판단에 쓰지 마세요" in body


def test_generic_report_default_advice_is_not_real_analysis_talk():
    body = _report()
    assert body.count("권고") == 1 and "실제 과제라면" in body


@pytest.mark.parametrize(("title", "mode"), [("드럼세탁기 탈수 진동 저감", "washer"), ("세탁기 진동과 바닥 전달력 함께 보기", "washer2"), ("브래킷 전체 과정", "bracket"),
                                             ("외팔보 3D 응력 예측", "beam3d"), ("Branin 최적화 데모", "branin"), ("브래킷 이어받아 해석", None), ("모르는 흐름", None)])
def test_flow_found_again_after_the_agent_restarted(title, mode):
    """PC Agent가 꺼졌을 때 승인했다. 켜진 뒤 새 세션의 데모가 새 흐름을 제안하면 승인한 흐름이 돌지 않는다."""
    assert demo_agent.mode_of_title(title) == mode


def test_scenario_source_has_no_fake_tool_claims():
    """연습용 계산인데 NX·Abaqus·RecurDyn을 쓰는 것처럼 보이면 안 된다."""
    src = (ROOT / "agent" / "dds_agent" / "demo_agent.py").read_text(encoding="utf-8")
    for bad in ('"tool": "nx"', '"tool": "abaqus"', '"tool": "recurdyn"', "RecurDyn 탈수 모델", "mock 솔버", "Mock 솔버 실행"):
        assert bad not in src, bad
