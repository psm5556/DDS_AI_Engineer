"""화면의 말을 막는다(test_design_tokens.py와 같은 방식): Project·Task·Task Flow는 frontend/src/work/copy.ts의 W에서 가져오고, 옛말은 쓰지 않는다.
AI 답변 본문이 아니라 소스의 문구만 본다. 주석은 보지 않는다."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend" / "src"
COPY = (SRC / "work" / "copy.ts").read_text(encoding="utf-8")
# 사용자가 정한 말. 어디든 W에서 가져와야 한 곳만 고쳐 바꿀 수 있다
FRONT_BANNED = ["프로젝트", "작업 흐름", "새 대화", "대화", "세션", "작업 폴더", "기본 작업 폴더", "내 PC 작업 폴더", "이 흐름을 새 모델로"]
TASK_N = re.compile(r"작업 \$\{")  # '작업 N건'류. ToolGroup은 '도구 사용 N건'이다
SERVER_BANNED = ["작업 흐름", "프로젝트", "작업 폴더", "새 대화"]


def _strip_ts(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?<![:'\"`])//.*$", "", text, flags=re.M)


def _front_files():
    for f in SRC.rglob("*"):
        if f.suffix in (".ts", ".tsx"):
            yield f, _strip_ts(f.read_text(encoding="utf-8"))


def test_screen_words_come_from_one_place():
    # '최대화'의 대화는 옛말이 아니다
    bad = [f"{f.relative_to(SRC)}: {w}" for f, text in _front_files() for w in FRONT_BANNED if w in text.replace("최대화", "")]
    assert not bad, "화면 낱말은 work/copy.ts의 W에서 가져오고 옛말은 쓰지 않는다: " + ", ".join(bad[:20])


def test_no_task_count_wording():
    bad = [str(f.relative_to(SRC)) for f, text in _front_files() if TASK_N.search(text)]
    assert not bad, f"'작업 N건'은 '도구 사용 N건'으로 쓴다: {bad}"


def test_word_constants_are_in_copy():
    assert 'project: "Project"' in COPY and 'task: "Task"' in COPY and 'flow: "Task Flow"' in COPY


def test_conversation_switcher_is_gone():
    all_text = "\n".join(text for _, text in _front_files())
    for gone in ("ConversationSwitcher", "ws-conv", "/c/${"):
        assert gone not in all_text, gone
    assert "RotateCcw" not in (SRC / "canvas" / "views.tsx").read_text(encoding="utf-8"), "이 흐름을 새 모델로 다시 버튼은 없앴다"


def test_project_menu_separates_folders_and_task_folder_is_hidden_until_it_exists():
    menu = (SRC / "components" / "ProjectMenu.tsx").read_text(encoding="utf-8")
    assert "폴더 열기" in menu and "폴더 바꾸기" in menu
    # 폴더를 바꾸는 곳은 Project 메뉴 한 곳이다
    assert "폴더 바꾸기" not in (SRC / "canvas" / "LocalFiles.tsx").read_text(encoding="utf-8")
    assert "taskFolder: false" in COPY, "Task별 폴더(2단계)가 들어오기 전에는 Task 메뉴에 없는 폴더를 보이지 않는다"
    assert "FEATURE.taskFolder" in (SRC / "work" / "WorkMenu.tsx").read_text(encoding="utf-8")


def test_delete_dialog_says_what_goes_and_what_stays():
    assert "서버에서 지워집니다" in COPY and "지우지 않습니다" in COPY


def _strings(path: Path):
    """독스트링을 뺀 문자열(화면과 AI로 나가는 말)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
            docs.add(id(node.body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node.value


def test_server_sentences_use_the_new_words():
    bad = []
    for f in (ROOT / "server" / "app").rglob("*.py"):
        for text in _strings(f):
            for w in SERVER_BANNED:
                # 대화의 내부 기본 제목은 화면에 쓰지 않는다
                if w in text and not (w == "새 대화" and f.name in ("models.py", "hub.py")):
                    bad.append(f"{f.relative_to(ROOT)}: {text[:60]}")
    assert not bad, "서버가 화면으로 보내는 문장의 옛말: " + " | ".join(bad[:15])


def test_errors_for_others_work_say_what_to_do():
    text = (ROOT / "server" / "app" / "work_service.py").read_text(encoding="utf-8")
    assert "보기만 할 수 있습니다" in text and "→" in text
