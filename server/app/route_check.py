"""경로 규칙: 이 요청을 지금 Task에 보낼까, 새 Task나 새 Project를 권할까. AI 없이 바로 답한다. docs/product-spec.md 3-3절.

처리 순서
  1. 표 붙여넣기는 표 질문으로 간다(화면이 먼저 가른다).
  2. 남의 Task면 카드 C.
  3. 아래 규칙에 걸리면 카드 A·B·D. 요청은 아직 보내지 않는다.
  4. 걸리지 않으면 이 Task로 보낸다. 모르면 묻지 않고 "여기서 계속"이다. 세 갈래를 고르는 질문 창은 두지 않는다.
  5. AI가 첫 단계에서 다시 본다(suggest_route). 6. 놓쳐도 Flow 승인 전에는 돌지 않고, 새 스터디라 옛 결과는 다치지 않는다.
카드가 잘못 뜨면 클릭이 하나 늘고, 놓치면 AI가 잡는다. 그래서 낱말은 좁게 잡는다."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from . import models
from .deps import member_role
from .work_service import MODEL_EXT, is_mine, owner_label

NEW_MODEL = ("새 모델", "신규 모델", "새 버전", "개정", "다음 모델")
OTHER_PART = ("다른 부품", "다른 제품", "완전히 다른", "새 프로젝트", "새 Project")
# "새 모델 학습해 줘"는 예측 모델 이야기다. 설계 모델이 아니다
ABOUT_SURROGATE = ("학습", "예측", "Simulator", "simulator", "시뮬레이터", "배포")
# 부정·비교·결과 이야기·프로그램 이야기는 새 일을 시작하자는 말이 아니다
NOT_A_NEW_JOB = ("아니", "말고", "빼고", "비교", "결과", "값", "응력", "보고서", "이력", "Agent", "에이전트", "프로그램", "업데이트", "PC")
MIN_PROJECT_NAME = 3  # 짧은 이름은 아무 문장에나 들어 있다
STARTED = ("approved", "in_progress", "complete", "failed", "stopped")


def has_results(db: Session, work: models.Work) -> bool:
    """이 Task에 스터디나 승인된 흐름이 있는가. 아직 아무것도 없으면 어떤 모델이든 여기서 시작하면 된다."""
    if db.query(models.Study.id).filter_by(work_id=work.id).first():
        return True
    return db.query(models.Flow.id).filter(models.Flow.work_id == work.id, models.Flow.status.in_(STARTED)).first() is not None


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?？。\n])", text) if s.strip()]


def _plain(sentence: str) -> bool:
    """묻는 말과 부정·비교는 새 일이 아니다."""
    return not sentence.endswith(("?", "？")) and not any(w in sentence for w in NOT_A_NEW_JOB)


def says_new_model(text: str) -> bool:
    return any(_plain(s) and any(w in s for w in NEW_MODEL) and not any(w in s for w in ABOUT_SURROGATE) for s in _sentences(text))


def says_other_part(text: str) -> bool:
    return any(_plain(s) and any(w in s for w in OTHER_PART) for s in _sentences(text))


def _new_model_file(db: Session, work: models.Work, files: list[dict], kept: set[str]) -> dict | None:
    """이 Task 첨부와 내용이 다른 모델 파일. 이름이 같아도 내용이 다르면 개정판이고, 이름이 달라도 내용이 같으면 같은 모델이다."""
    have = {sha for sha, in db.query(models.Attachment.sha256).filter_by(work_id=work.id)}
    return next((f for f in files if str(f.get("name") or "").lower().endswith(MODEL_EXT) and f.get("sha256") not in have and f.get("sha256") not in kept), None)


def _source_of(db: Session, project_id: int, user: models.User) -> models.Work | None:
    """카드 D로 들어갈 때 가져올 Task: 내 최근 Task, 없으면 그 Project의 최근 Task. 결과가 있는 것만."""
    rows = db.query(models.Work).filter_by(project_id=project_id).order_by(models.Work.seq.desc()).all()
    done = [w for w in rows if has_results(db, w)]
    return next((w for w in done if is_mine(w, user.id)), None) or next(iter(done), None)


def _match_project(db: Session, text: str, user: models.User) -> models.Project | None:
    """첫 화면 글에 내 Project 이름 전체가 있으면 그 Project다. 가장 긴 이름을 고른다. 멤버인 곳만 본다: 있는지 없는지도 정보다."""
    P, M = models.Project, models.ProjectMember
    rows = db.query(P).join(M, M.project_id == P.id).filter(M.user_id == user.id).all()
    hits = [p for p in rows if len(p.name.strip()) >= MIN_PROJECT_NAME and p.name.strip() in text]
    return max(hits, key=lambda p: (len(p.name), p.id)) if hits else None


def check(db: Session, user: models.User, work: models.Work | None, project_id: int | None, text: str, files: list[dict], kept: list[str]) -> dict:
    none = {"card": None}
    text = (text or "").strip()
    if work is not None:
        if not is_mine(work, user.id):
            return {"card": "C", "kind": "others", "owner_name": owner_label(db, work), "source_work": {"id": work.id, "name": work.name}}
        if says_other_part(text):
            return {"card": "B", "kind": "text"}
        if has_results(db, work):
            picked = _new_model_file(db, work, files, set(kept))
            if picked:
                return {"card": "A", "kind": "file", "file": picked.get("name"), "source_work": {"id": work.id, "name": work.name}}
            if says_new_model(text):
                return {"card": "A", "kind": "text", "file": None, "source_work": {"id": work.id, "name": work.name}}
        return none
    if project_id:  # 새 Task 화면: 지난 결과가 없으니 새 모델은 묻지 않는다. 다른 부품만 본다
        return {"card": "B", "kind": "text"} if says_other_part(text) else none
    hit = _match_project(db, text, user)  # 첫 화면
    if hit is None or member_role(hit.id, user, db) is None:
        return none
    src = _source_of(db, hit.id, user)
    return {"card": "D", "kind": "project", "project": {"id": hit.id, "name": hit.name}, "source_work": {"id": src.id, "name": src.name} if src else None}
