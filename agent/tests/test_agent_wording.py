"""AI에게 보이는 말과 데모 답변의 낡은 말을 막는다. 코드 이름(Work·work_id·project_dir)은 그대로이고 사람이 읽는 말만 본다.
화면 쪽 문구 시험은 화면 담당의 test_work_wording.py에 있다."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RULES = (ROOT / "agent-context" / "INSTRUCTIONS.md").read_text(encoding="utf-8")


def _docs_and_strings(path: Path) -> list[str]:
    """파이썬 파일에서 사람이 읽는 글자(독스트링·문자열). 주석은 뺀다."""
    return [n.value for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_rules_have_no_old_words():
    for old in ("프로젝트", "작업 흐름", "작업 폴더", "새 대화"):
        assert old not in RULES, old


def test_rules_do_not_say_project_folder_is_cwd():
    assert "Project 폴더 기준" not in RULES
    assert not re.search(r"P0012_\S*\s*← *(지금|cwd)", RULES), "폴더 그림의 cwd는 Task 폴더다"
    assert re.search(r"T01_<Task>/\s*← 지금 Task 폴더\(cwd\)", RULES)


def test_rules_teach_the_new_tools():
    for word in ("set_work_info", "list_works", "all_works=True", "다른 Task 폴더는 읽기만"):
        assert word in RULES, word


def test_mcp_descriptions_have_no_old_words():
    for f in (ROOT / "mcp_servers").glob("*.py"):
        for text in _docs_and_strings(f):
            for old in ("프로젝트", "작업 흐름", "작업 폴더"):
                assert old not in text, f"{f.name}: {old}: {text[:80]}"


def test_skills_have_no_old_folder_words():
    for f in (ROOT / "agent-context" / "skills").rglob("*.md"):
        text = f.read_text(encoding="utf-8")
        for old in ("작업 흐름", "작업 폴더", "프로젝트 폴더"):
            assert old not in text, f"{f}: {old}"


def test_demo_answers_have_no_old_words():
    """데모 답변만 본다. 사용자의 말과 맞춰 보는 낱말에는 옛말이 남아 있어야 옛 습관의 요청도 알아듣는다."""
    tree = ast.parse((ROOT / "agent" / "dds_agent" / "demo_agent.py").read_text(encoding="utf-8"))
    said = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "say":
            said += [c.value for a in n.args for c in ast.walk(a) if isinstance(c, ast.Constant) and isinstance(c.value, str)]
    assert said
    for text in said:
        for old in ("프로젝트", "작업 흐름", "작업 폴더", "새 대화"):
            assert old not in text, f"{old}: {text[:80]}"
