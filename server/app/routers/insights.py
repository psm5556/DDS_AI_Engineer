"""스터디 판단 도우미 API. 계산과 문장은 study/insights.py에 있고, 여기서는 권한과 응답 모양만 맡는다."""
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from .. import schemas
from ..database import get_db
from ..deps import get_current_user
from ..study import insights
from .studies import _model, _share, _share_model, _study

router = APIRouter(prefix="/api", tags=["insights"])


@router.get("/studies/{study_id}/insights")
def study_insights(study_id: int, detail: Literal["full", "summary"] = "full", user=Depends(get_current_user), db: Session = Depends(get_db)):
    """status가 computing이면 빠른 항목이나 직전 결과(stale)를 먼저 주고, 끝나면 study_updated가 온다. 해석을 새로 돌리지 않는다."""
    value = insights.get_insights(db, _study(study_id, user, db))
    out = insights.strip_charts(value) if detail == "summary" else value
    # 큰 결과를 이벤트 루프에서 변환하면 다른 요청이 그동안 멈춘다. 이 함수는 스레드에서 도니 여기서 바꿔 넘긴다
    return Response(json.dumps(out, ensure_ascii=False, allow_nan=False), media_type="application/json")


@router.get("/studies/{study_id}/insights/pareto")
def study_pareto(study_id: int, a: str, b: str, a_goal: Literal["min", "max"] = "min", b_goal: Literal["min", "max"] = "min",
                 user=Depends(get_current_user), db: Session = Depends(get_db)):
    """고른 두 출력의 앞면(화면의 '다른 출력과 함께 보기')."""
    s = _study(study_id, user, db)
    have = {k for c in s.cases for k in (c.results or {})}
    for name in (a, b):
        if name not in have:
            raise HTTPException(400, f"'{name}' 출력의 값이 있는 케이스가 없습니다 → 데이터 탭의 출력 이름을 확인하세요")
    if a == b:
        raise HTTPException(400, "서로 다른 두 출력을 고르세요")
    return insights.pair_section(db, s, a, a_goal, b, b_goal)


@router.post("/studies/{study_id}/insights/publish")
def publish_insights(study_id: int, body: schemas.InsightPublish, user=Depends(get_current_user), db: Session = Depends(get_db)):
    s = _study(study_id, user, db)
    try:
        return {"published": insights.publish(db, s, body.flow_id, body.node_id, body.keys)}
    except insights.NotReady as e:
        raise HTTPException(409, str(e)) from e


@router.get("/models/{model_id}/trust-line")
def model_trust_line(model_id: int, user=Depends(get_current_user), db: Session = Depends(get_db)):
    return insights.trust_line(db, _model(model_id, user, db))


@router.get("/shares/{token}/trust-line")
def share_trust_line(token: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    """공유받은 사람에게도 보인다. 프로젝트 이름·케이스·해석 데이터는 담지 않는다."""
    return insights.trust_line(db, _share_model(_share(token, db), db))
