"""스터디 판단 도우미: 이미 있는 케이스와 모델만으로 "지금 어떤 상태이고 무엇을 하면 되는지"를 쉬운 문장으로 말한다. 해석을 더 돌리지 않는다.
원칙: 판정이 먼저(질문에 답하는 상태 말 + 숫자가 든 한 문장 + 다음 할 일), 자료가 모자라면 판정하지 않고 몇 건이 더 필요한지 말한다,
해석으로 확인한 값과 예측값을 나눈다, 잴 수 없는 것은 재지 않았다고 말한다.
화면·AI(MCP)·보고서가 모두 이 결과를 쓰므로 어디서나 같은 숫자가 나온다. 판단값은 THRESHOLDS 한 곳에 있고 docs/product-spec.md 4-6-1절에 그대로 적는다."""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.stats import norm  # noqa: F401
from sqlalchemy import func
from sqlalchemy.orm import Session
from sklearn.model_selection import KFold, cross_val_predict

from .. import models
from ..config import settings
from ..database import SessionLocal
from ..ws import manager
from . import bayesopt, insights_calc as calc, service
from .surrogate import Surrogate, _make_gp

log = logging.getLogger(__name__)

SCHEMA = 3  # 결과 모양이 바뀌면 올린다. 저장된 옛 결과를 쓰지 않게 한다

# 판단값. 몇 개 시험 함수로 정한 값이고 실제 CAE 데이터로 검증되지 않았다. 사내 검증 뒤 조정한다
THRESHOLDS: dict[str, float] = {
    "count_need_min": 8, "count_need_per_var": 5, "count_rec_per_var": 8,  # 최소 max(8, 5d)건, 권장 8d건
    "gap_fair": 0.15, "gap_fair_per_n": 3, "gap_caution": 0.25, "gap_caution_per_n": 4,  # 빈 구간: max(값, 값/n)
    "level_max": 7,  # 변수 값이 이 개수 이하로 반복되면 격자(수준)로 본다
    "cd_margin": 1.25,  # 고른 정도(CD)가 표준 배치 p90의 이 배수까지는 좋음. 표준 배치가 우연히 '보통'이 되는 일을 줄인다
    "cd_random_margin": 1.25,  # 무작위 p90의 이 배수를 넘어야 '주의'. 그냥 무작위 배치를 잘못 배치라고 몰아세우지 않는다
    "dup_ratio": 0.25,  # 최소 거리가 표준 배치 중앙값의 이 비율 아래면 거의 같은 점
    "corr_ok": 0.3, "corr_caution": 0.5, "corr_caution_margin": 0.1,  # 함께 움직임 |r|
    "fail_fair": 0.05, "fail_caution": 0.20,
    "fail_cluster_count": 3, "fail_cluster_share": 0.7, "fail_cluster_ratio": 2.0, "fail_cluster_p": 0.05,
    "outlier_mad": 6, "outlier_cv": 5, "outlier_isolated": 0.5, "outlier_edge": 0.05,
    "pass_good": 0.30, "pass_fair": 0.10,
    "r2_good": 0.9, "r2_fair": 0.7,
    "train_min": 5,  # assess_quality와 같은 경계. 판단 자체는 최소 건수(need)부터 한다
    "flat_rel": 0.001, "flat_margin": 5,  # 오차가 값의 이 비율 이하이고 제약까지 오차의 이 배수 이상 떨어지면 거의 일정한 출력으로 본다
    "share_small": 0.05, "interaction": 0.10, "pair": 0.05,
    "rank_stable": 4,  # 5겹 중 1위가 같은 횟수
    "mono": 0.8, "dir_main_ratio": 0.5,  # 1차 지수 ÷ 전체 지수가 이보다 작으면 방향이 다른 변수에 달렸다
    "related_p": 0.05,  # 해석값과 변수의 순위 상관을 이름 붙여 말하는 유의수준(변수 수로 나눈다)
    "conv_window_min": 5, "conv_range": 0.01, "conv_total": 0.10, "conv_ei": 0.01,
    "conv_multi_windows": 2, "conv_hv_left": 0.02,  # 여러 목표의 멈춤: 창의 배수 이상 돌렸고 예측으로 본 남은 몫이 이 값 이하
    "edge": 0.02, "margin_good": 0.05, "margin_fair": 0.01,
    "robust_tol": 0.02, "robust_good": 0.25, "robust_fair": 1.0, "robust_flat_good": 0.01, "robust_flat_fair": 0.05, "robust_pass": 0.9,
    "calib": 0.7, "calib_min": 5,
    "stale_count": 3, "stale_ratio": 0.10,
    "pareto_min": 5, "pareto_tradeoff": 3, "conflict_extent": 0.10, "knee_min": 0.05, "tradeoff_step": 0.10,
    "al_good": 0.20, "al_fair": 0.05, "al_worse": -0.10, "al_stop": 0.05, "al_stop_window": 3, "al_min_base": 10, "al_min_added": 3,
    "cost_min_sec": 60,  # 이보다 짧으면 시간을 말하지 않는다
}
T_ = THRESHOLDS

# 상태 말은 그 질문의 답이어야 한다. '더 돌리면 좋아질까요? 좋음'은 거꾸로 읽힌다. 네 가지 상태(색)는 그대로 두고 말만 질문마다 다르다
UNKNOWN_LABEL = "아직 판단 못 함"
STATE_WORDS = {
    "coverage": {"good": "고르게 퍼짐", "fair": "조금 부족", "caution": "다시 잡기 권함"},
    "quality": {"good": "문제 없음", "fair": "확인 필요", "caution": "먼저 고치기"},
    "influence": {"good": "믿을 만함", "fair": "참고용", "caution": "믿기 어려움"},
    "trust": {"good": "믿을 만함", "fair": "참고용", "caution": "믿기 어려움"},
    "convergence": {"good": "거의 다 찾음", "fair": "더 돌릴 만함", "caution": "먼저 점검"},
    "optimum": {"good": "써도 됨", "fair": "확인 후 사용", "caution": "아직 쓰지 마세요"},
    "pareto": {"good": "함께 좋아짐", "fair": "맞바꿔야 함", "caution": "후보 없음"},
    "active": {"good": "효과 있음", "fair": "효과 작음", "caution": "효과 없음"},
}
_RANK = {"good": 0, "unknown": 1, "fair": 2, "caution": 3}
QUALITY_STATE = {"good": "good", "fair": "fair", "poor": "caution"}  # service.assess_quality의 등급 → 판단 도우미의 상태
BASIS_LABEL = {"verified": "해석으로 확인한 값", "predicted": "AI 예측값", "mixed": "해석값과 AI 예측값", "imported": "표에 있던 값", "planned": "계획한 점(아직 해석 전)"}


def state_label(key: str, state: str) -> str:
    return UNKNOWN_LABEL if state == "unknown" else STATE_WORDS[key][state]


META = {
    "coverage": ("설계 범위를 고르게 살폈나요?", "공간 충진성"),
    "quality": ("해석 결과에 이상한 값은 없나요?", "실패·이상치·제약"),
    "influence": ("무엇이 결과를 좌우하나요?", "영향도"),
    "trust": ("이 예측을 믿어도 되나요?", "예측 정확도"),
    "convergence": ("더 돌리면 좋아질까요?", "수렴성"),
    "optimum": ("이 설계를 그대로 써도 되나요?", "최적 설계 점검"),
    "pareto": ("두 목표를 함께 좋게 할 수 있나요?", "파레토 프론트"),
    "active": ("정확도를 높이려고 더 돌린 해석이 효과가 있었나요?", "능동학습"),
}

HOW_TO_READ = {
    "coverage": "청록색 점(처음 계획한 점)이 고르면 됩니다. 다른 색 점은 AI가 일부러 좋은 곳이나 모르는 곳에 더해 몰려 있어도 괜찮습니다. 줄무늬에서 빈 구간은 아직 보지 않은 범위입니다.",
    "quality": "빨간 점은 다른 값과 많이 다른 케이스입니다. 자동으로 빼지 않으니 해석 오류인지 직접 확인하세요.",
    "quality_clean": "점이 고르게 모여 있으면 문제가 없습니다. 많이 다른 값이 있으면 빨갛게 표시합니다.",
    "influence": "막대가 길수록 결과를 많이 바꿉니다. 막대를 누르면 아래 곡선이 그 변수로 바뀝니다. 곡선은 다른 변수의 평균 효과라 실제 최고 설계의 값과 다를 수 있고, 띠가 넓을수록 AI 예측이 확신하지 못합니다.",
    "trust": "점이 대각선에 가까울수록 잘 맞습니다. 학습에 쓰지 않은 점으로 잰 값이라, 전체 데이터로 학습한 실제 예측은 이보다 나을 수 있습니다.",
    "convergence_min": "선이 내려가다 평평해지면 더 돌려도 얻는 것이 적습니다. 값은 모두 해석으로 확인한 것입니다.",
    "convergence_max": "선이 올라가다 평평해지면 더 돌려도 얻는 것이 적습니다. 값은 모두 해석으로 확인한 것입니다.",
    "convergence_multi": "선이 올라가다 평평해지면 더 돌려도 얻는 것이 적습니다. 세로축은 목표들을 함께 본 종합 점수라 단위가 없습니다. 값은 모두 해석으로 확인한 것입니다.",
    "optimum": "해석 확인과 예측을 나눠 보여 줍니다. 예측 후보는 아직 해석하지 않은 값입니다.",
    "active": "선이 내려갈수록 예측 오차가 줄어든 것입니다. 중간에 오르내리는 것은 흔하니 끝이 시작보다 낮은지를 보세요. 평평해지면 더 넣어도 얻는 것이 적습니다.",
}

# 문장 틀. {이름}은 값, {이름|을를}은 값에 조사를 붙인다. 시험이 모든 틀을 찍어 금지어와 길이를 검사한다
T = {
    # 공간 충진성
    "cov_none": "점이 {n}개뿐이라 아직 판단하지 못합니다. 변수 {d}개면 최소 {need}건, 권장 {rec}건입니다.",
    "cov_good": "처음 계획한 {n}건으로 변수 {d}개의 설계 범위를 고르게 살폈습니다.",
    "cov_good_plan": "변수 {d}개에 {n}건으로 설계 범위를 고르게 잡았습니다. 돌려도 좋습니다.",
    "cov_compare_even": "처음 계획한 {n}건은 아무렇게나 뽑았을 때보다 약 {ratio}배 고르게 퍼졌고, 흔히 쓰는 고른 배치(LHS, 범위를 고르게 나눠 뽑는 방법)와 비슷합니다.",
    "cov_compare_same": "처음 계획한 {n}건은 흔히 쓰는 고른 배치(LHS, 범위를 고르게 나눠 뽑는 방법)와 비슷하게 퍼졌습니다.",
    "cov_count_fair": "{n}건으로 고르게 퍼졌지만 변수 {d}개에는 적은 편입니다. {rec}건을 권합니다{cost}.",
    "cov_cd_fair": "아무렇게나 뽑은 것보다는 고르게 퍼졌지만 고른 배치(LHS, 범위를 고르게 나눠 뽑는 방법)보다는 덜합니다.",
    "cov_cd_rand": "점이 퍼진 정도가 아무렇게나 뽑은 것과 비슷합니다. 고른 배치(LHS, 범위를 고르게 나눠 뽑는 방법)로 뽑으면 더 좋습니다.",
    "cov_gap_fair": "{var}의 {range} 구간(범위의 {p}%)이 비어 있습니다. 점을 더하면 좋습니다.",
    "cov_corr_fair": "{a|과와} {b|이가} 조금 함께 움직입니다(상관 {r}). 두 변수의 효과를 나눠 볼 때 조심하세요.",
    "cov_dup_fair": "거의 같은 점이 있습니다(케이스 {i}, {j}). 한 건은 정보가 적습니다.",
    "cov_grid": "변수를 {levels}수준으로 나눈 격자 {n}건입니다. 수준 사이 값은 직접 보지 않았으니 그 사이 예측은 해석으로 확인하세요.",
    "cov_count_caution": "{n}건은 변수 {d}개에 너무 적습니다. 최소 {need}건은 있어야 합니다{cost}.",
    "cov_gap_caution": "{var}의 {range} 구간(범위의 {p}%)을 한 번도 보지 않았습니다. 이 구간의 예측은 믿기 어렵습니다.",
    "cov_cd_caution": "점이 고르게 퍼지지 않았습니다. 아무렇게나 뽑았을 때보다도 치우쳐 있습니다.",
    "cov_corr_caution": "{a|과와} {b|이가} 함께 움직입니다(상관 {r}). 어느 쪽 효과인지 구분하기 어렵습니다.",
    "cov_fail_hole": "실패 {f}건 때문에 {var} {range} 구간이 비었습니다.",
    "cov_time": "지금 속도면 남은 {m}건에 약 {t} 걸립니다.",
    # 실패·이상치·제약
    "q_none": "완료된 해석이 {n}건이라 아직 판단하지 못합니다. {m}건 더 끝나면 봅니다.",
    "q_all_failed": "{f}건이 모두 실패해 판단할 결과가 없습니다. 원인을 고친 뒤 다시 봅니다.",
    "q_good": "해석 {n}건 중 실패 {f}건이고 튀는 값은 없습니다.",
    "q_good_fast": "해석 {n}건 중 실패 {f}건입니다. 튀는 값은 해석이 끝난 뒤 다시 확인합니다.",
    "q_good_con": "실패 {f}건입니다. 제약을 만족한 설계가 {k}건({p}%) 있어 고를 후보는 충분합니다.",
    "q_fail_fair": "실패가 {f}건({p}%)입니다. 특정 구간에 뚜렷하게 몰리지는 않았습니다.",
    "q_fail_few": "실패가 {f}건({p}%)입니다. 건수가 적어 몰린 곳이 있는지는 아직 모릅니다.",
    "q_outlier": "케이스 {id}의 {out} {val}이(가) 다른 값(보통 {q1}~{q3})과 많이 다릅니다. 해석 오류인지 확인해 보세요.",
    "q_pass_fair": "제약을 만족한 설계가 {p}%({k}건)로 적습니다.",
    "q_cluster": "실패 {f}건 중 {g}건이 {var} {range}에 몰려 있습니다. 이 구간의 해석 모델(FE)이나 메쉬를 확인하세요.",
    "q_fail_caution": "실패가 {p}%로 많습니다. 결과를 쓰기 전에 원인을 먼저 찾으세요.",
    "q_pass_low": "제약을 만족한 설계가 {p}%({k}건)뿐입니다. 제약값이나 설계 범위를 확인하세요.",
    "q_pass_zero": "제약을 만족한 설계가 하나도 없습니다. 제약값이나 설계 범위를 확인하세요.",
    "q_constant": "{out} 값이 모두 같습니다. 결과 추출을 확인하세요.",
    "q_con_missing": "제약 값이 없어 판단하지 못한 케이스가 {k}건 있습니다.",
    # 영향도
    "inf_few": "해석이 {n}건이라 아직 판단하지 못합니다. {m}건 더 끝나면 봅니다.",
    "inf_no_model": "예측 모델이 아직 없습니다. 해석 결과만 놓고 보면 {out|은는} {var|과와} 가장 함께 움직입니다(원인이라는 뜻은 아닙니다).",
    "inf_no_model_none": "예측 모델이 아직 없습니다. 해석 결과만 놓고 봐도 {out|과와} 뚜렷하게 함께 움직이는 변수는 없습니다.",
    "inf_flat": "{out} 값이 거의 변하지 않아 영향도를 계산하지 않았습니다.",
    "inf_good": "{out|은는} {var1|이가} {p1}%, {var2|이가} {p2}%를 좌우합니다.",
    "inf_good_one": "{out|은는} {var1|이가} {p1}%를 좌우합니다.",
    "inf_single": "변수가 {var} 하나뿐이라 {out|은는} {var}에 따라 정해집니다.",
    "inf_fair": "(참고용) {out|은는} {var1}의 영향이 가장 커 보입니다({p1}%). 예측 정확도가 보통이라 순서는 바뀔 수 있습니다.",
    "inf_fair_more": "예측 정확도를 올린 뒤 다시 보면 순서가 더 분명해집니다.",
    "inf_caution": "예측 정확도가 낮아 영향 순위는 아직 믿기 어렵습니다. 해석 결과만 놓고 보면 {var|과와} 가장 함께 움직입니다(원인이라는 뜻은 아닙니다).",
    "inf_caution_none": "예측 정확도가 낮아 영향 순위는 아직 믿기 어렵습니다. 해석 결과만 놓고 봐도 뚜렷하게 함께 움직이는 변수는 없습니다.",
    "inf_caution_few": "예측 정확도가 낮아 영향 순위는 아직 믿기 어렵습니다. 해석 점을 더 넣은 뒤 다시 보세요.",
    "inf_tied": "{a|과와} {b|이가} 함께 움직여(상관 {r}) 어느 쪽이 {out|을를} 좌우하는지 나눠 볼 수 없습니다.",
    "inf_gap": "(참고용) {out|은는} {var1}의 영향이 가장 커 보이지만({p1}%), {var1}의 {range} 구간은 해석이 없어 예측으로 이어 그린 값입니다.",
    "inf_others_fair": "({names}의 순위는 참고용입니다.)",
    "inf_others_caution": "({names}의 순위는 예측이 약해 믿기 어렵습니다.)",
    "dir_down": "{var|을를} 올리면 {out|이가} 줄어듭니다.",
    "dir_up": "{var|을를} 올리면 {out|이가} 늘어납니다.",
    "dir_min": "다른 변수를 고루 바꿔 평균했을 때 {out|은는} {var|이가} {x} 근처에서 가장 작습니다.",
    "dir_max": "다른 변수를 고루 바꿔 평균했을 때 {out|은는} {var|이가} {x} 근처에서 가장 큽니다.",
    "dir_small": "{var|은는} 영향이 작습니다.",
    "dir_depends": "{var|을를} 올렸을 때 {out|이가} 늘지 줄지는 {partner} 값에 따라 달라집니다.",
    "dir_unsure": "{var|을를} 올렸을 때 {out|이가} 늘지 줄지는 아직 분명하지 않습니다.",
    "dir_seen": "해석이 있는 {range} 구간에서 그렇습니다.",
    "curve_caption": "{var|을를} 바꾸면 {out|이가} 이렇게 변합니다(예측).",
    "inf_interaction": "{var}의 효과는 {partner} 값에 따라 달라집니다.",
    "inf_unstable": "데이터를 조금 바꿔 다시 재면 1위가 바뀌기도 합니다.",
    # 예측 정확도
    "tr_no_model": "예측 모델이 아직 없습니다. 해석이 {need}건쯤 모이면 학습할 수 있습니다.",
    "tr_no_model_ready": "예측 모델이 아직 없습니다. 해석이 {n}건 모여 지금 학습할 수 있습니다.",
    "tr_few": "학습 데이터가 {n}건이라 아직 판단하지 못합니다. {m}건 더 필요합니다.",
    "tr_good": "{out}: 학습에 안 쓴 점으로 맞춰 보니 10번 중 8번은 오차가 ±{p80} 안이었습니다. 결정의 참고로 쓸 만합니다.",
    "tr_fair": "{out}: 학습에 안 쓴 점으로 맞춰 보니 10번 중 8번은 오차가 ±{p80} 안이었습니다. 이 예측으로 고른 설계는 해석 1건으로 확인하세요.",
    "tr_caution": "{out}: 학습에 안 쓴 점으로 맞춰 보니 오차가 큽니다(10번 중 8번 ±{p80} 안). 참고만 하고, 점을 더 넣거나 빠진 변수를 확인하세요.",
    "tr_rmse_good": "{out} 예측의 평균 오차는 ±{rmse}입니다. 결정의 참고로 쓸 만합니다.",
    "tr_rmse_fair": "{out} 예측의 평균 오차는 ±{rmse}입니다. 이 예측으로 고른 설계는 해석 1건으로 확인하세요.",
    "tr_rmse_caution": "{out} 예측은 오차가 큽니다(평균 ±{rmse}). 참고만 하고, 점을 더 넣거나 빠진 변수를 확인하세요.",
    "tr_flat": "{out} 값이 거의 변하지 않아 정확도를 잴 수 없습니다.",
    "tr_nearflat": "{out} 값은 거의 일정합니다(보통 {val}). 예측 오차 ±{p80}는 무시해도 되는 크기입니다.",
    "tr_region": "최적화로 더한 {k}건은 좋은 곳에 몰려 서로 잘 맞으므로 빼고 나머지 {b}건으로 쟀습니다. 몰린 곳 근처의 예측은 이보다 나을 수 있습니다.",
    "tr_small": "점이 {n}건으로 적어 실제 예측보다 나쁘게 잰 값일 수 있습니다.",
    "tr_stale": "최신 해석 {k}건을 아직 못 배웠습니다.",
    "tr_unsure": "AI 예측은 {var|이가} {side} 쪽을 가장 모릅니다.",
    "tr_calib_low": "돌리기 전에 AI가 말한 범위 안에 실제 해석값이 {total}건 중 {hit}건만 들어왔습니다. AI가 자기 오차를 실제보다 작게 봅니다.",
    "tr_calib": "돌리기 전에 AI가 말한 범위 안에 실제 해석값이 {total}건 중 {hit}건 들어왔습니다. AI가 자기 오차를 대체로 정직하게 알고 있다는 뜻입니다.",
    # 수렴성
    "con_none": "아직 최적화를 돌리지 않았습니다.",
    "con_few": "최적화 해석이 {k}건입니다. {W}건이 되면 판단합니다. {m}건 더 필요합니다.",
    "con_stop": "처음 계획한 해석 중 최고 {start}에서 {now}로 {gain} 좋아졌고, 최근 {W}건 개선은 {r}입니다. 예측 모델이 보기에는 더 돌려도 효과가 작습니다.",
    "con_stop_note": "예측 모델이 놓친 좁은 구간이 있으면 더 좋은 설계가 남아 있을 수 있습니다.",
    "con_soft": "최근 {W}건 개선은 {r}로 작지만, 설계 범위 전체의 예측이 아직 정확하지 않아 다 찾았다고 보기는 이릅니다. {W}건 더 돌려 확인할 만합니다{cost}.",
    "con_room": "최근 {W}건 개선은 작지만 예측 모델은 아직 더 나아질 곳이 있다고 봅니다. {W}건 더 돌려 볼 만합니다{cost}.",
    "con_going": "최근 {W}건에서 {r} 더 좋아졌습니다. 아직 좋아지는 중이라 {W}건 더 돌릴 만합니다{cost}.",
    "con_going_note": "처음 계획한 해석 중 최고 {start}에서 지금 {now}입니다.",
    "con_found": "처음 계획한 해석에는 제약을 만족한 설계가 없었고, 최적화로 {now}인 설계를 찾았습니다. 최근 {W}건 개선은 {r}입니다.",
    "con_flat_ok": "최적화 {k}건이 처음 계획한 해석의 최고를 넘지 못했고, 예측 모델도 더 나아질 곳이 적다고 봅니다. 이미 좋은 설계이거나 범위를 넓혀야 합니다.",
    "con_flat_bad": "최적화 {k}건이 처음 계획한 해석의 최고보다 나아지지 않았습니다. 예측 정확도와 설계 범위를 먼저 확인하세요.",
    "con_poor": "개선이 멈춘 것처럼 보이지만 예측 정확도가 낮아 다 찾았다고 볼 수 없습니다. 예측 정확도부터 올리세요.",
    "con_infeasible": "제약을 만족한 설계를 아직 찾지 못했습니다.",
    "con_unknown_all": "케이스에 제약 값이 없어 제약을 만족하는지 아직 모릅니다. 제약 항목을 결과에서 뽑으면 판단합니다.",
    "con_multi_stop": "최근 {W}건에서 {goals}를 함께 좋게 하는 후보가 {r} 나아졌고, 예측으로도 더 나아질 곳이 적습니다. 더 돌려도 효과가 작습니다.",
    "con_multi_soft": "최근 {W}건에서 {goals}를 함께 좋게 하는 후보가 {r} 나아졌습니다. 작아 보여도 잠시 멈춘 것일 수 있어 {W}건 더 돌려 확인할 만합니다{cost}.",
    "con_multi_going": "최근 {W}건에서 {goals}를 함께 좋게 하는 후보가 {r} 더 나아졌습니다. 아직 나아지는 중이라 {W}건 더 돌릴 만합니다{cost}.",
    "con_multi_note": "처음 계획한 해석보다 {gain} 나아졌습니다.",
    "con_multi_flat": "최적화 {k}건으로도 {goals}를 함께 좋게 하는 후보가 나아지지 않았습니다. {W}건 더 돌려 보거나 설계 범위를 확인하세요.",
    "con_multi_flat_bad": "최적화 {k}건으로도 {goals}를 함께 좋게 하는 후보가 나아지지 않았습니다. 예측 정확도와 설계 범위를 먼저 확인하세요.",
    # 최적 설계
    "opt_none": "완료된 해석이 없어 아직 판단하지 못합니다.",
    "opt_few": "지금 가장 좋은 값은 {out} {val}(케이스 {id})이지만 변수 {d}개에 {n}건뿐이라 아직 판단하지 못합니다. {m}건 더 필요합니다.",
    "opt_infeasible": "제약을 만족한 설계가 없어 점검하지 못합니다.",
    "opt_head": "지금 가장 좋은 설계(케이스 {id}, {out} {val})는 해석으로 확인된 값입니다.",
    "opt_good": "범위 끝에 있지 않고 작은 변화에도 안정적입니다(예측).",
    "opt_good_plain": "범위 끝에 있지 않습니다.",
    "opt_wait": "아직 정하지 마세요. 지금 가장 좋은 값은 {out} {val}(케이스 {id})이지만, {why}.",
    "opt_check": "다만 {why}.",
    "why_trust_few": "예측 오차가 ±{e}로 크고 최적화도 {k}건뿐이라 우연히 좋게 나온 값일 수 있습니다",
    "why_trust_none": "예측 오차가 ±{e}로 크고 최적화도 돌리지 않아 우연히 좋게 나온 값일 수 있습니다",
    "why_trust": "예측 오차가 ±{e}로 커서 더 좋은 설계가 남아 있는지 아직 모릅니다",
    "why_con": "최적화 결과를 먼저 점검해야 합니다",
    "why_no_opt": "아직 최적화를 돌리지 않아 처음 계획한 해석 중 가장 좋은 값일 뿐입니다",
    "why_few_opt": "최적화가 {k}건뿐이라 더 좋아질 수 있습니다",
    "why_going": "더 돌리면 아직 좋아질 수 있어 보입니다",
    "why_fair_trust": "예측 정확도가 보통이라 주변 설계를 해석으로 비교해 보는 것이 좋습니다",
    "why_multi": "목표가 여럿이라 첫 목표 {out} 기준으로만 고른 설계입니다",
    "opt_edge": "{var|이가} 범위 {side}인 {x}에 붙어 있습니다. 범위를 넓혀 볼 만합니다.",
    "opt_pred": "다만 AI 예측으로는 {x} 근처에서 {out} {pred}까지 {verb} 수 있어, 확인 해석 1건을 권합니다{cost}.",
    "opt_margin_fair": "{con} 여유가 {m}({p}%)로 작습니다.",
    "opt_margin_caution": "{con|이가} 한계까지 {m}({p}%)밖에 남지 않았습니다. 공차를 생각하면 넘을 수 있어 그대로 쓰지 마세요.",
    "opt_robust": "변수가 범위의 ±{tol}%만 흔들려도 {out|이가} {a}~{b}로 크게 움직입니다(예측). 조금 안쪽 설계도 비교하세요.",
    "opt_robust_fair": "변수가 범위의 ±{tol}% 흔들리면 {out|은는} {a}~{b}로 움직입니다(예측).",
    "opt_robust_con": "변수가 범위의 ±{tol}%만 흔들려도 {con} 제약을 넘을 수 있습니다(예측 통과율 {p}%).",
    "opt_prior": "이 설계는 돌리기 전 예측 {pred}, 해석 결과 {val}입니다(차이 {diff}).",
    "opt_con_unknown": "이 설계에는 {con} 값이 없어 제약을 판단하지 못했습니다.",
    "opt_multi": "목표가 여럿이라 첫 목표 {out} 기준으로 점검했습니다. 한 설계를 답으로 정하기 전에 목표 비교를 함께 보세요.",
    # 파레토
    "par_few": "두 값을 모두 가진 제약 만족 해석이 {n}건입니다. {m}건 더 있어야 판단합니다.",
    "par_one": "케이스 {id} 하나가 {a|과와} {b} 모두 가장 좋습니다. 둘 사이에서 고를 필요가 없습니다.",
    "par_trade": "둘을 함께 좋게 하기는 어렵고 서로 맞바꿔야 합니다. 균형이 좋은 설계는 케이스 {id}({design})입니다. 여기서 {a|을를} {to}까지 {p}더 {verb_a}면 {b|이가} {d} {verb_b}(해석한 설계 기준).",
    "par_trade_noknee": "둘을 함께 좋게 하기는 어렵고 서로 맞바꿔야 합니다. 뚜렷한 균형 설계는 없습니다. 케이스 {id}({design})에서 {a|을를} {to}까지 {p}더 {verb_a}면 {b|이가} {d} {verb_b}(해석한 설계 기준).",
    "par_nofight": "두 목표가 크게 부딪히지 않습니다. 후보 {k}개 중 다른 조건으로 고르면 됩니다.",
    "par_two": "좋은 후보가 {k}개뿐이라 맞바꾸는 비율은 아직 말하지 못합니다. 두 목표 최적화를 더 돌리면 후보가 늘어납니다.",
    "par_infeasible": "제약을 만족한 설계가 없어 비교하지 못합니다.",
    "par_single": "지금 최적화는 첫 목표 {a}만 봅니다. 아래는 이미 해석한 결과에서 찾은 후보입니다.",
    "par_pick": "지금은 {a} 하나만 목표입니다. 다른 결과와 맞바꿀 것이 있는지 보려면 펼치세요.",
    "par_many": "{goals} 중 {a|과와} {b|을를} 먼저 비교했습니다.",
    "par_knee_note": "균형 설계는 제안입니다. 답을 정해 주는 것이 아니니 무엇을 더 중요하게 볼지 정해 고르세요.",
    "par_pred_note": "속 빈 점 {k}개는 AI 예측이라 아직 해석으로 확인하지 않았습니다.",
    "par_pred_low": "예측 정확도가 낮아 예측 후보는 보여 주지 않습니다.",
    # 능동학습
    "al_few": "정확도를 높이려고 더 돌린 해석이 {k}건입니다. {need}건이 되면 판단합니다.",
    "al_base_few": "정확도를 높이려고 더 돌린 해석은 {k}건이지만 처음 계획한 해석이 {b}건뿐입니다. {need}건이 있어야 판단합니다.",
    "al_good": "정확도를 높이려고 더 돌린 {k}건으로 평균 오차가 {p}% 줄었습니다({a} → {b}). 아무 데나 골랐을 때보다 나은지는 재지 않았습니다.",
    "al_fair": "정확도를 높이려고 더 돌린 {k}건으로 평균 오차가 {p}% 줄었습니다({a} → {b}). 효과는 있지만 크지 않고, 아무 데나 골랐을 때보다 나은지는 재지 않았습니다.",
    "al_flat": "정확도를 높이려고 더 돌린 {k}건 뒤에도 평균 오차는 거의 그대로입니다({a} → {b}). 다만 처음 계획한 점에서 잰 값이라 범위 끝쪽에서 좋아진 만큼은 드러나지 않습니다.",
    "al_flat_ok": "정확도를 높이려고 더 돌린 {k}건 뒤에도 평균 오차는 거의 그대로입니다({a} → {b}). 예측이 이미 잘 맞아 더 돌릴 필요가 적습니다.",
    "al_worse": "정확도를 높이려고 더 돌린 {k}건 뒤 평균 오차가 오히려 {p}% 늘었습니다({a} → {b}, 처음 계획한 점 기준). 더 돌리기 전에 추가된 결과 값이 이상하지 않은지 확인하세요.",
    "al_others": "({names}의 효과는 더 작았습니다.)",
    "al_stop_good": "최근 {m}건은 오차가 거의 그대로입니다. 여기서 멈춰도 됩니다.",
    "al_stop_poor": "최근 {m}건은 오차가 거의 그대로이고 정확도도 아직 낮습니다. 점을 더 넣어도 금방 나아지기는 어려워 보입니다.",
    "al_going": "아직 줄어드는 중이라 {b}건 더 돌릴 만합니다{cost}.",
    "al_wait": "추가가 {m}건뿐이라 멈출지 더 갈지는 아직 말하지 않습니다.",
    "al_no_compare": "아무 데나 골랐을 때와 비교한 값은 아닙니다. 그 비교는 해석을 더 돌려야 알 수 있습니다.",
    "al_edge_all": "{k}건 모두 설계 범위의 끝쪽에 있습니다. 오차는 처음 계획한 점에서 잰 값이라 끝쪽에서 좋아진 만큼은 드러나지 않습니다.",
    "al_edge_some": "{k}건 중 {e}건이 설계 범위의 끝쪽에 있습니다. 오차는 처음 계획한 점에서 잰 값이라 끝쪽에서 좋아진 만큼은 드러나지 않습니다.",
    "al_same": "추가한 점 중 {k}건은 기존 점과 거의 같아 정보가 적습니다.",
    # 공통
    "pending": "계산하고 있습니다. 잠시 뒤 결과가 나옵니다.",
    "error": "이 항목을 계산하지 못했습니다. 잠시 뒤 다시 계산합니다.",
    "sampled": "점이 많아 {n}건 중 {k}건으로 계산했습니다.",
}

# 다음 할 일 문장(요약 카드)
NEXT = {
    "doe_fix": ("돌리기 전에 해석 조건을 더 넣는 것이 좋습니다.", "해석 조건 더 넣기 요청"),
    "make_doe": ("해석 조건 목록(DOE, 어떤 값들로 돌릴지 정한 표)을 만들 차례입니다.", "해석 조건 만들기 요청"),
    "run": ("대기 중인 케이스를 돌릴 차례입니다.", "해석 실행 요청"),
    "wait": ("해석이 끝나면 다시 판단합니다.", ""),
    "stuck": ("케이스를 돌리던 PC Agent 연결이 끊겼습니다. PC Agent를 켠 뒤 다시 실행하세요.", ""),
    "computing": ("판단을 계산하고 있습니다. 잠시 뒤 다음 할 일을 알려 드립니다.", ""),
    "fail": ("실패 원인부터 찾으세요.", "원인 찾기 요청"),
    "count": ("해석이 {n}건입니다. 변수 {d}개면 최소 {need}건은 있어야 판단할 수 있습니다{cost}.", "해석 조건 더 넣기 요청"),
    "train": ("예측 모델을 학습할 차례입니다.", "학습 요청"),
    "stale": ("최신 해석 {k}건을 반영해 다시 학습하세요.", "재학습 요청"),
    "poor": ("예측 정확도를 먼저 올리세요{cost}.", "정확도 높이는 해석 요청"),
    "check_inputs": ("점을 더 넣어도 예측이 금방 나아지지 않습니다. 설계 범위와 빠진 변수를 확인하세요.", "원인 점검 요청"),
    "optimize": ("최적화를 돌릴 차례입니다.", "최적화 요청"),
    "more_opt": ("최적화를 {W}건 더 돌릴 만합니다{cost}.", "추가 최적화 요청"),
    "verify": ("예측 최선점을 해석으로 확인하세요{cost}.", "확인 해석 요청"),
    "margin": ("{con} 여유가 작습니다. 여유 있는 설계를 찾아 보세요.", "여유 있는 설계 찾기 요청"),
    "edge": ("{var} 범위를 넓혀 볼 만합니다.", "범위 넓히기 요청"),
    "finish": ("Simulator를 배포하고 보고서를 쓰면 됩니다.", "보고서 요청"),
    "report": ("보고서를 쓰면 됩니다.", "보고서 요청"),
}
# 버튼이 하는 일. 이름이 달라도 하는 일이 같으면 한 곳에만 둔다
NEXT_ACTION = {"doe_fix": "doe", "count": "doe", "make_doe": "doe", "run": "run", "fail": "fail", "train": "train", "stale": "train", "poor": "al", "check_inputs": "inputs",
               "optimize": "bo", "more_opt": "bo", "verify": "verify", "margin": "margin", "edge": "edge", "finish": "finish", "report": "finish"}
# 요약 카드의 '바로 확정하기 전에 볼 것'
WATCH = {"coverage": "설계 범위", "quality": "이상한 값", "influence": "영향 순위", "trust": "예측 정확도", "convergence": "더 돌리기", "optimum": "최고 설계 확인",
         "pareto": "목표 맞바꾸기", "active": "추가 해석 효과"}


# ---------- 작은 도우미 ----------
def josa(word: str, pair: str) -> str:
    """조사 선택. pair는 받침 있을 때·없을 때 순서("을를", "이가", "은는", "과와")."""
    word = re.sub(r"[\s)\]]+$", "", str(word))
    if not word:
        return f"{pair[0]}({pair[1]})"
    ch = word[-1]
    code = ord(ch)
    if 0xAC00 <= code <= 0xD7A3:
        return pair[0] if (code - 0xAC00) % 28 else pair[1]
    if ch.isdigit():
        return pair[0] if ch in "013678" else pair[1]
    if ch.lower() in "lmnr":
        return pair[0]
    if ch.isalpha():
        return pair[1]
    return f"{pair[0]}({pair[1]})"


def say(key: str, **kw) -> str:
    """틀에 값을 넣는다. {이름|을를}은 값과 조사를 붙인다."""
    def repl(m: re.Match) -> str:
        name, pair = m.group(1), m.group(2)
        if pair:
            return f"{kw[name]}{josa(str(kw[name]), pair)}"
        return str(kw[name])
    return re.sub(r"\{(\w+)(?:\|(\S\S))?\}", repl, T[key])


def num(x) -> float | None:
    """유한한 실수만 값으로 본다. 문자열·None·NaN·참거짓은 값이 아니다."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def n3(x: float | None) -> str:
    """유효숫자 3자리."""
    if x is None:
        return "-"
    a = abs(x)
    if a == 0:
        return "0"
    if a >= 1000:
        return f"{x:,.0f}"
    if a < 1e-6:
        return f"{x:.2e}"
    if a < 0.001:  # 지수 표기는 읽기 어렵다. 유효숫자 2자리 소수로 쓴다
        return f"{x:.{1 - math.floor(math.log10(a))}f}"
    return f"{x:.3g}"


def with_unit(x: float | None, u: str) -> str:
    s = n3(x)
    return s if not u else f"{s}{u}" if u == "%" else f"{s} {u}"


def pct(x: float) -> str:
    return f"{x:.1f}" if abs(x) < 10 else f"{x:.0f}"


def hours_text(h: float) -> str:
    if h < 1 / 60:
        return f"{max(1, round(h * 3600))}초"
    if h < 1:
        return f"{round(h * 60)}분"
    return f"{h:.1f}시간" if h < 48 else f"{h / 24:.1f}일"


def _r(x, k: int = 4):
    """facts에 넣는 숫자를 유효숫자로 줄인다."""
    v = num(x)
    return None if v is None else float(f"{v:.{k}g}")


def _clean(o):
    """JSON으로 저장할 수 있게 NaN·무한대·numpy 값을 없앤다."""
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.ndarray):
        return _clean(o.tolist())
    if isinstance(o, (np.floating, float)):
        return float(o) if math.isfinite(float(o)) else None
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


# ---------- 스냅샷: 스레드로 넘기는 DB 없는 자료 ----------
@dataclass
class CaseRow:
    id: int
    params: dict
    results: dict
    status: str
    source: str
    timing: dict | None
    created_at: datetime | None
    run_by: int | None = None
    updated_at: datetime | None = None


@dataclass
class ModelRow:
    id: int
    version: int | None
    outputs: list
    metrics: dict
    n_train: int
    path: str
    created_at: datetime | None
    deployed: bool
    quality: dict = field(default_factory=dict)  # service.assess_quality가 학습 때 저장한 값


@dataclass
class Snap:
    study_id: int
    project_id: int
    name: str
    variables: list
    objectives: list
    constraints: list
    outputs: list
    adapter: str
    running: bool
    best_id: int | None
    best_failed: bool = False
    cases: list = field(default_factory=list)
    models: list = field(default_factory=list)  # 최신이 먼저
    owner: str = ""  # 이 스터디가 누구의 것인지. id는 지운 뒤 다시 쓰일 수 있어 저장된 결과와 맞춰 본다
    link_lost: bool = False

    @property
    def names(self) -> list[str]:
        return [v["name"] for v in self.variables]

    @property
    def lo(self) -> np.ndarray:
        return np.array([float(v["lower"]) for v in self.variables])

    @property
    def hi(self) -> np.ndarray:
        return np.array([float(v["upper"]) for v in self.variables])

    @property
    def imported(self) -> bool:
        return self.adapter == "data" or any(c.source == "import" for c in self.cases)


def _owner(study: models.Study) -> str:
    return f"{study.project_id}:{study.created_at.isoformat() if study.created_at else ''}"


RUN_QUIET_SEC = 600  # 이만큼 소식이 없으면 해석 중이어도 전체 판단을 한 번 계산한다. 멈춘 실행이 판단까지 멈추면 안 된다


def _running_state(snap: Snap) -> tuple[bool, bool]:
    """(정말 돌고 있는가, PC Agent 연결이 끊겨 멈췄는가). service.is_running은 케이스 상태 메시지로만 비워지므로 연결이 끊기면 영영 True다."""
    if not snap.running:
        return False, False
    active = [c for c in snap.cases if c.status in ("queued", "running")]
    if not active:
        return False, False
    from ..agent.link import registry
    on_server = snap.adapter == "mock" and settings.allow_server_mock
    if not on_server and not any(c.run_by and registry.get(c.run_by) for c in active):
        return False, True
    stamps = [c.updated_at for c in active if c.updated_at]
    if stamps and (datetime.now() - max(stamps)).total_seconds() > RUN_QUIET_SEC:
        return False, False
    return True, False


def _model_row(m: models.SurrogateModel) -> ModelRow:
    return ModelRow(m.id, m.version, list(m.outputs or []), dict(m.metrics or {}), m.n_train or 0, m.path, m.created_at, bool(m.deployed), dict(m.quality or {}))


def snapshot(db: Session, study: models.Study) -> Snap:
    db.refresh(study)
    try:  # 화면의 '현재 최적'과 같은 케이스를 점검한다. 결과에 문자열이 섞이면 best_case가 비교하지 못하므로 그때는 직접 고른다
        best = service.best_case(study)
        best_id, broken = (best.id if best else None), False
    except TypeError:
        best_id, broken = None, True
    rows = (db.query(models.SurrogateModel).filter_by(study_id=study.id).order_by(models.SurrogateModel.id.desc()).limit(30).all())
    snap = Snap(
        study_id=study.id, project_id=study.project_id, name=study.name, variables=list(study.variables or []),
        objectives=list(study.objectives or []), constraints=list(study.constraints or []), outputs=list(study.outputs or []),
        adapter=study.adapter, running=service.is_running(study.id), best_id=best_id, best_failed=broken, owner=_owner(study),
        cases=[CaseRow(c.id, dict(c.params or {}), dict(c.results or {}), c.status, c.source or "doe", c.timing, c.created_at, c.run_by, c.updated_at) for c in study.cases],
        models=[_model_row(m) for m in rows])
    snap.running, snap.link_lost = _running_state(snap)
    return snap


# ---------- 옛 모델의 교차검증 ----------
# 교차검증 예측이 저장되기 전 모델은 한 번만 다시 재고 파일로 남긴다. 상태가 바뀔 때마다 다시 재면 작업 스레드를 몇 분씩 붙잡는다
def _cv_path(model_id: int):
    return settings.data_dir / "insights" / f"cv_model_{model_id}.json"


def _read_cv(model_id: int) -> dict:
    try:
        return json.loads(_cv_path(model_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_cv(model_id: int, output: str, value: dict):
    data = _read_cv(model_id)
    data[output] = value
    p = _cv_path(model_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        log.warning("옛 모델의 교차검증 값을 저장하지 못했습니다", exc_info=True)


# ---------- 계산 맥락 ----------
class Ctx:
    def __init__(self, snap: Snap, use_models: bool = True, light: bool = False):
        self.s = snap
        self.use_models = use_models
        self.light = light  # 요청 경로에서 부를 때: 저장된 값만 읽고 다시 재지 않는다
        self._sur: dict[int, Surrogate | None] = {}
        self._cache: dict = {}
        self._cv: dict = {}
        self._score: dict = {}
        self.d = len(snap.variables)
        self.need = max(int(T_["count_need_min"]), int(T_["count_need_per_var"]) * self.d)
        self.rec = int(T_["count_rec_per_var"]) * self.d
        self.complete = [c for c in snap.cases if c.status == "complete" and self.has_params(c)]
        self.failed = [c for c in snap.cases if c.status == "failed" and self.has_params(c)]
        self.waiting = [c for c in snap.cases if c.status in ("pending", "queued", "running")]
        self.word = "데이터" if snap.imported else "해석"
        self.basis = "imported" if snap.imported else "verified"
        self.notes = {o["name"]: o.get("note") or "" for o in snap.outputs}
        self.influence: dict[str, dict] = {}
        self.obj_trust: dict[str, dict] = {}
        self.by: dict[str, dict] = {}  # 먼저 계산한 항목. 뒤 항목의 판정이 앞 항목과 어긋나지 않게 이어받는다
        self.had_error = False

    def has_params(self, c: CaseRow) -> bool:
        return all(num(c.params.get(v)) is not None for v in self.s.names)

    def x(self, c: CaseRow) -> list[float]:
        return [float(c.params[v]) for v in self.s.names]

    def unit(self, rows: list[list[float]]) -> np.ndarray:
        if not rows:
            return np.zeros((0, self.d))
        span = np.where(self.s.hi > self.s.lo, self.s.hi - self.s.lo, 1.0)
        return (np.asarray(rows, dtype=float) - self.s.lo) / span

    def label(self, name: str) -> str:
        """부품 이름. 설명(note)에서 [단위]를 뺀 것, 없으면 이름 그대로."""
        for v in self.s.variables:
            if v["name"] == name and v.get("note"):
                return re.sub(r"\s*\[[^\]]*\]\s*", "", v["note"]).strip() or name
        note = self.notes.get(name, "")
        return (re.sub(r"\s*\[[^\]]*\]\s*", "", note).strip() or name) if note else name

    def unit_of(self, name: str) -> str:
        for o in [*self.s.outputs, *self.s.variables]:  # 표에서 읽은 변수는 note 없이 unit만 있다
            if o["name"] == name and o.get("unit"):
                return str(o["unit"])
        note = self.notes.get(name, "") or next((v.get("note") or "" for v in self.s.variables if v["name"] == name), "")
        m = re.search(r"\[([^\]]+)\]", note)
        return m.group(1) if m else ""

    def val(self, name: str, x: float | None) -> str:
        """값과 단위. 기준 대비 %로 적는 변수(범위가 음수부터)는 + 부호를 붙여 '12'를 절대값으로 읽지 않게 한다."""
        text = with_unit(x, self.unit_of(name))
        lower = next((v.get("lower") for v in self.s.variables if v["name"] == name), None)
        return f"+{text}" if x is not None and x > 0 and lower is not None and lower < 0 and self.unit_of(name) == "%" else text

    def span_text(self, name: str, lo: float, hi: float) -> str:
        return f"{n3(lo)}~{self.val(name, hi)}"

    def output_order(self) -> list[str]:
        names = [o["name"] for o in self.s.objectives] + [c["name"] for c in self.s.constraints] + [o["name"] for o in self.s.outputs]
        names += sorted({k for c in self.s.cases for k in c.results})
        return list(dict.fromkeys(names))

    def decision_outputs(self) -> set[str]:
        return {o["name"] for o in self.s.objectives} | {c["name"] for c in self.s.constraints}

    def values(self, name: str, rows: list[CaseRow] | None = None) -> list[tuple[CaseRow, float]]:
        return [(c, v) for c in (self.complete if rows is None else rows) if (v := num(c.results.get(name))) is not None]

    def feasible(self, c: CaseRow) -> bool | None:
        """화면의 '현재 최적'과 어긋나지 않게 서버의 정의를 그대로 쓴다."""
        return service.constraint_state(self.s.constraints, c.results)

    def no_feasible(self, kind: str) -> tuple[str, str]:
        """고를 설계가 없을 때의 (상태, 문장). 어긴 케이스가 있으면 주의, 값이 없어 모르기만 하면 아직 판단하지 못한 것이다."""
        if any(self.feasible(c) is False for c in self.complete):
            return "caution", T[f"{kind}_infeasible"]
        return "unknown", T["con_unknown_all"]

    def cost(self, n: int) -> str:
        """'N건 더'의 시간. 잰 케이스가 3건 이상이고 의미 있게 길 때만 말한다."""
        t = run_times(self.s.cases)
        if n <= 0 or len(t["durations"]) < 3:
            return ""
        sec = float(np.median(t["durations"])) * n / max(1.0, t["parallel"])
        return f"(지금 속도로 약 {hours_text(sec / 3600)})" if sec >= T_["cost_min_sec"] else ""

    def surrogate(self, m: ModelRow) -> Surrogate | None:
        if m.id not in self._sur:
            try:
                self._sur[m.id] = service.load_surrogate(m)
            except Exception:
                log.warning("모델 %s를 불러오지 못했습니다", m.id, exc_info=True)
                self._sur[m.id] = None
        return self._sur[m.id]

    def model_for(self, output: str) -> ModelRow | None:
        """그 출력을 가진 가장 최근 모델. 재학습에서 빠진 출력도 앞 버전으로 볼 수 있다."""
        for m in self.s.models:
            if output in m.outputs and (sur := self.surrogate(m)) is not None and output in sur.models:
                return m
        return None

    def r2(self, m: ModelRow, output: str) -> float | None:
        return num((m.metrics.get(output) or {}).get("r2"))

    def rmse(self, m: ModelRow, output: str) -> float | None:
        return num((m.metrics.get(output) or {}).get("rmse"))

    def rows_of(self, m: ModelRow, output: str) -> int:
        """이 출력을 학습한 행 수. 출력마다 값이 있는 행만 쓰므로 모델 전체 행 수보다 적을 수 있다."""
        return int((m.quality.get("rows_by_output") or {}).get(output) or m.n_train)

    def stale_by(self, m: ModelRow, output: str) -> int:
        """학습한 뒤 늘어난 건수. 행이 많아 골라 학습했으면 고르기 전 행 수와 견준다. 그러지 않으면 골라 뺀 행이 새 해석으로 보인다."""
        seen = (m.quality.get("sampled") or {}).get("from") or self.rows_of(m, output)
        return max(0, len(self.values(output)) - int(seen))

    def is_stale(self, m: ModelRow, output: str) -> bool:
        k = self.stale_by(m, output)
        return k >= max(T_["stale_count"], T_["stale_ratio"] * max(1, m.n_train))

    def level(self, m: ModelRow | None, output: str) -> str:
        """저장된 지표만 본 등급. service.assess_quality와 경계가 같다(poor → caution). 화면 판정은 verdict를 쓴다."""
        if m is None:
            return "unknown"
        r2 = self.r2(m, output)
        if self.rows_of(m, output) < T_["train_min"] or r2 is None:
            return "unknown"
        if r2 < T_["r2_fair"]:
            return "caution"
        return "good" if r2 >= T_["r2_good"] and self.rows_of(m, output) >= self.need else "fair"

    def score(self, m: ModelRow, o: str) -> dict | None:
        """설계 범위 전체를 말하는 정확도. 최적화 점은 한곳에 몰려 서로를 잘 맞히므로 빼고 잰다(Hartmann6: 전체 R² 0.93, 실제 0.49).
        점수만 빼는 것이고 학습에서 빼는 것은 아니다. 저장된 교차검증 예측을 다시 쓸 뿐 다시 맞추지 않는다."""
        key = (m.id, o)
        if key in self._score:
            return self._score[key]
        out = None
        sur = self.surrogate(m)
        cv = self.cv(sur, o, m) if sur is not None and o in sur.models else None
        if cv:
            actual, pred = np.array(cv["actual"], dtype=float), np.array(cv["cv_pred"], dtype=float)
            X, _ = train_xy(sur.models[o])
            by = self.case_by_x()
            keep = np.array([getattr(by.get(tuple(round(v, 7) for v in row)), "source", "doe") != "bo" for row in X]) if len(X) == len(actual) else np.ones(len(actual), dtype=bool)
            dropped = int((~keep).sum())
            if dropped and keep.sum() < T_["train_min"]:  # 남는 점이 너무 적으면 뺄 수 없다
                keep, dropped = np.ones(len(actual), dtype=bool), 0
            err = np.abs(pred[keep] - actual[keep])
            stored = self.r2(m, o)
            r2 = stored if not dropped and stored is not None else service.design_range_r2(actual, pred, keep)
            out = {"r2": r2, "p80": float(np.percentile(err, 80)), "rmse": float(np.sqrt(np.mean(err ** 2))), "n": int(keep.sum()), "dropped": dropped}
        self._score[key] = out
        return out

    def near_flat(self, m: ModelRow, o: str) -> bool:
        """거의 일정한 출력: 흔들림이 수치 잡음이라 R²는 0에 가깝지만 오차는 무시할 만하다. 점을 더 넣어도 R²는 오르지 않는다.
        제약 경계가 그 오차 안에 있으면 무시할 수 없다."""
        vals = [v for _, v in self.values(o)]
        if len(vals) < 2:
            return False
        med = float(np.median(vals))
        s = self.score(m, o)
        err = s["p80"] if s else self.rmse(m, o)
        if err is None or med == 0 or err > T_["flat_rel"] * abs(med):
            return False
        for con in self.s.constraints:
            if con["name"] == o:
                for bound in ("upper", "lower"):
                    if con.get(bound) is not None and abs(float(con[bound]) - med) < T_["flat_margin"] * err:
                        return False
        return True

    def verdict(self, m: ModelRow | None, o: str) -> str:
        """화면의 예측 판정. 건수가 최소에 못 미치면 판단하지 않고, 설계 범위 기준 정확도로 본다.
        같은 화면의 정확도 칩(ModelTrust)과 어긋나지 않게 학습 때 저장한 품질보다 좋게 말하지 않는다. 거의 일정한 출력만 예외다: R²가 뜻이 없고 문장이 그 이유를 말한다."""
        if m is None or self.rows_of(m, o) < self.need:
            return "unknown"
        s = self.score(m, o)
        r2 = s["r2"] if s else self.r2(m, o)
        if r2 is None:
            return "unknown"
        if r2 < T_["r2_good"] and self.near_flat(m, o):
            return "good"
        level = "good" if r2 >= T_["r2_good"] else "fair" if r2 >= T_["r2_fair"] else "caution"
        stored = QUALITY_STATE.get(((m.quality.get("by_output") or {}).get(o) or {}).get("level"))
        return stored if stored and _RANK[stored] > _RANK[level] else level

    def model_info(self, m: ModelRow, output: str | None = None) -> dict:
        return {"id": m.id, "version": m.version, "n_train": m.n_train, "stale_by": self.stale_by(m, output) if output else 0}

    def mean_fn(self, sur: Surrogate, output: str):
        pipe = sur.models[output]
        lo, span = self.s.lo, self.s.hi - self.s.lo
        return lambda U: pipe[-1].predict(pipe[0].transform(lo + np.asarray(U) * span))

    def cv(self, sur: Surrogate, output: str, m: ModelRow | None = None) -> dict | None:
        """학습할 때의 교차검증 예측. 이 필드가 생기기 전 모델은 저장된 학습 자료로 같은 호출을 다시 돌리고 한 번만 잰다."""
        got = (getattr(sur, "cv", None) or {}).get(output)
        if got:
            return got
        key = (id(sur), output)
        if key in self._cv:
            return self._cv[key]
        pipe = sur.models[output]
        X, y = train_xy(pipe)
        saved = _read_cv(m.id).get(output) if m else None
        if saved and len(saved.get("actual") or []) == len(X):
            self._cv[key] = saved
            return saved
        if self.light:
            return None
        k = min(5, len(X))
        if k < 3:
            return None
        pred = cross_val_predict(_make_gp(X.shape[1]), X, y, cv=KFold(k, shuffle=True, random_state=0))
        value = {"cv_pred": pred.tolist(), "actual": y.tolist()}
        self._cv[key] = value
        if m:
            _write_cv(m.id, output, value)
        return value

    def case_by_x(self) -> dict:
        if "by_x" not in self._cache:
            self._cache["by_x"] = {tuple(round(v, 7) for v in self.x(c)): c for c in self.complete}
        return self._cache["by_x"]


def is_constant(vals) -> bool:
    """값이 정말 모두 같은가. 절대 크기와 비교하면 1e-10처럼 작은 단위의 정상 출력을 일정하다고 잘못 본다."""
    arr = np.asarray(vals, dtype=float)
    if len(arr) < 2:
        return False
    return float(np.ptp(arr)) <= 1e-9 * abs(float(np.median(arr)))


def train_xy(pipe) -> tuple[np.ndarray, np.ndarray]:
    """저장된 파이프라인에서 학습 자료를 되살린다. normalize_y로 바뀐 y를 되돌린다."""
    scaler, gp = pipe[0], pipe[-1]
    X = scaler.inverse_transform(gp.X_train_)
    y = np.asarray(gp.y_train_) * gp._y_train_std + gp._y_train_mean
    return X, y


def _section(key: str, state: str, sentence: str, notes: list[str] | None = None, basis: str = "verified",
             next_: dict | None = None, facts: dict | None = None, charts: list | None = None, model: dict | None = None,
             how: str | None = None, label: str | None = None, **extra) -> dict:
    title, term = META[key]
    return {"key": key, "title": title, "term": term, "state": state, "state_label": label or state_label(key, state), "sentence": sentence,
            "notes": [x for x in (notes or []) if x], "how_to_read": how or HOW_TO_READ.get(key, ""), "basis": basis,
            "basis_label": BASIS_LABEL[basis], "next": next_, "facts": facts or {}, "charts": charts or [], "model": model, **extra}


def _pending(key: str) -> dict:
    s = _section(key, "unknown", T["pending"])
    s["state_label"] = "계산 중"
    s["pending"] = True
    return s


def _ask(ctx: Ctx, label: str, request: str, action: str | None = None) -> dict:
    """버튼. action이 같으면 하는 일이 같은 버튼이다(화면에 한 번만 둔다)."""
    return {"label": label, "request": request.replace("{name}", ctx.s.name), "action": action or label}


def _ask_al(ctx: Ctx) -> dict:
    return _ask(ctx, "정확도 높이는 해석 요청", "스터디 '{name}'에 능동학습으로 해석 점을 더 넣어 예측 정확도를 올려 줘.", "al")


# ---------- 설계 범위 ----------
def coverage(ctx: Ctx) -> dict:
    s, d = ctx.s, ctx.d
    live = lambda c: c.status not in ("failed", "cancelled", "dry_run") and ctx.has_params(c)  # noqa: E731
    keep = [c for c in s.cases if c.source not in ("bo", "al") and live(c)]
    pts = ctx.unit([ctx.x(c) for c in keep])
    ids = [c.id for c in keep]
    n = len(keep)
    # 범위를 살핀 건수: 처음 점 + 능동학습 점. 최적화 점은 한곳에 몰리므로 세지 않는다(6건 + 최적화 60건을 '66건으로 고르게'라고 하면 틀리다)
    n_al = sum(1 for c in s.cases if c.source == "al" and live(c))
    n_bo = sum(1 for c in s.cases if c.source == "bo" and live(c))
    n_spread = n + n_al
    facts = {"n": n, "n_spread": n_spread, "n_all": n_spread + n_bo, "d": d, "need": ctx.need, "rec": ctx.rec}
    charts = _pairs_chart(ctx)
    basis = ctx.basis if ctx.complete or ctx.failed else "planned"
    fix_label = "해석 조건 더 넣기 요청"
    if d == 0 or n < max(4, d + 2):
        return _section("coverage", "unknown", say("cov_none", n=n, d=d, need=ctx.need, rec=ctx.rec), facts=facts, charts=charts, next_=None, basis=basis)
    notes = []
    sampled = n > 500
    if sampled:  # 표가 수십만 행이어도 응답이 늦지 않게 고르게 줄여서 잰다
        pick = np.random.default_rng(0).choice(n, 500, replace=False)
        U, used_ids = pts[pick], [ids[i] for i in pick]
        notes.append(say("sampled", n=f"{n:,}", k=500))
    else:
        U, used_ids = pts, ids
    # 격자·요인 배치: 수준 사이가 비는 것은 설계이고, 고른 정도(CD) 비교는 뜻이 없다
    levels = calc.level_counts(pts, int(T_["level_max"]))
    leveled, grid = any(levels), all(levels)
    st = calc.doe_stats(U, levels)
    base = calc.doe_baseline(len(U), d)
    names = [ctx.label(v) for v in s.names]
    nu = len(U)
    verdicts: list[tuple[str, str, dict]] = []

    def real(i: int, u: float) -> float:
        return float(s.lo[i] + u * (s.hi[i] - s.lo[i]))

    if grid:
        lv = sorted({int(v) for v in levels if v})
        verdicts.append(("fair", "cov_grid", dict(levels=str(lv[0]) if len(lv) == 1 else f"{lv[0]}~{lv[-1]}", n=n)))
    # 건수
    if n_spread < ctx.need:
        k = ctx.need - n_spread
        verdicts.append(("fair" if grid else "caution", "cov_count_caution", dict(n=n_spread, d=d, need=ctx.need, cost=ctx.cost(k))))
    elif n_spread < ctx.rec:
        verdicts.append(("fair", "cov_count_fair", dict(n=n_spread, d=d, rec=ctx.rec, cost=ctx.cost(ctx.rec - n_spread))))
    # 빈 구간
    gap_fair, gap_caution = max(T_["gap_fair"], T_["gap_fair_per_n"] / nu), max(T_["gap_caution"], T_["gap_caution_per_n"] / nu)
    worst = int(np.argmax([g["gap"] for g in st["gaps"]]))
    g = st["gaps"][worst]
    gap_range = ctx.span_text(s.names[worst], real(worst, g["lo"]), real(worst, g["hi"]))
    gap_kw = dict(var=names[worst], range=gap_range, p=pct(g["gap"] * 100))
    if g["gap"] > gap_caution:
        verdicts.append(("caution", "cov_gap_caution", gap_kw))
    elif g["gap"] > gap_fair:
        verdicts.append(("fair", "cov_gap_fair", gap_kw))
    # 고른 정도: 표준 배치·무작위 배치와 비교한다. 무작위도 절반은 중앙값을 넘으니 '주의'는 무작위의 p90을 넘을 때만이다
    cd_said = False
    if st["cd"] is not None and not leveled:
        if st["cd"] > T_["cd_random_margin"] * base["cd_random_p90"]:
            verdicts.append(("caution", "cov_cd_caution", {}))
            cd_said = True
        elif st["cd"] > T_["cd_margin"] * base["cd_lhs_p90"]:
            verdicts.append(("fair", "cov_cd_rand" if st["cd"] >= base["cd_random_median"] else "cov_cd_fair", {}))
            cd_said = True
    # 거의 같은 점
    if not leveled and st["min_dist"] is not None and st["min_dist"] < T_["dup_ratio"] * base["dist_lhs_median"]:
        i, j = st["min_pair"]
        verdicts.append(("fair", "cov_dup_fair", dict(i=used_ids[i], j=used_ids[j])))
    # 함께 움직임
    r = st["corr"]
    if d >= 2:
        a, b = names[r["a"]], names[r["b"]]
        limit_ok, limit_bad = max(T_["corr_ok"], base["corr_lhs_p90"]), max(T_["corr_caution"], base["corr_lhs_p90"] + T_["corr_caution_margin"])
        if abs(r["r"]) >= limit_bad:
            verdicts.append(("caution", "cov_corr_caution", dict(a=a, b=b, r=f"{r['r']:.2f}")))
        elif abs(r["r"]) > limit_ok:
            verdicts.append(("fair", "cov_corr_fair", dict(a=a, b=b, r=f"{r['r']:.2f}")))
    # 실패로 생긴 구멍: 계획대로면 비지 않았을 구간
    if ctx.failed:
        plan = ctx.unit([ctx.x(c) for c in keep] + [ctx.x(c) for c in ctx.failed if c.source not in ("bo", "al")])
        if len(plan) > n and calc.largest_gap(plan[:, worst])["gap"] <= gap_fair < g["gap"]:
            notes.append(say("cov_fail_hole", f=len(ctx.failed), var=gap_kw["var"], range=gap_range))
    state = "good"
    for v in verdicts:
        if _RANK[v[0]] > _RANK[state]:
            state = v[0]
    if verdicts and state != "good":
        first = next(v for v in verdicts if v[0] == state)
        sentence = say(first[1], **first[2])
        notes += [say(v[1], **v[2]) for v in verdicts if v is not first]
    else:
        # 돌리기 전에는 점을 계획했을 뿐이다. 살폈다고 말하지 않는다
        sentence = say("cov_good", d=d, n=n) if ctx.complete else say("cov_good_plan", d=d, n=n)
    # 판정의 근거. 숫자 대신 비교로 말한다(값은 facts에 있어 AI가 물으면 답할 수 있다)
    if st["cd"] is not None and not leveled and not cd_said:
        ratio = base["cd_random_median"] / st["cd"] if st["cd"] > 0 else 0.0
        notes.append(say("cov_compare_even", n=n, ratio=f"{ratio:.0f}") if ratio >= 1.5 else say("cov_compare_same", n=n))
    if t := _time_note(ctx):
        notes.append(t)
    facts.update(cd=_r(st["cd"]), cd_lhs_p90=_r(base["cd_lhs_p90"]), cd_lhs_median=_r(base["cd_lhs_median"]), cd_random_median=_r(base["cd_random_median"]), cd_random_p90=_r(base["cd_random_p90"]),
                 min_dist=_r(st["min_dist"]), max_corr=_r(r["r"]), max_gap=_r(g["gap"]), corr_pair=[names[r["a"]], names[r["b"]]] if d >= 2 else None, grid=bool(grid))
    next_ = None
    if state == "caution":
        k = max(ctx.need - n_spread, 1) if n_spread < ctx.need else max(ctx.rec - n_spread, d * 2)
        next_ = _ask(ctx, fix_label, f"스터디 '{{name}}'에 해석 조건을 {k}건 더 넣어 빈 구간을 채워 줘.", "doe")
    elif state == "fair" and n_spread < ctx.rec:
        next_ = _ask(ctx, fix_label, f"스터디 '{{name}}'에 해석 조건을 {ctx.rec - n_spread}건 더 넣어 줘.", "doe")
    return _section("coverage", state, sentence, notes=notes, facts=facts, charts=charts, next_=next_, basis=basis)


def _pairs_chart(ctx: Ctx, limit: int = 2000) -> list:
    """변수쌍 그림의 자료. 처음 점·최적화로 더한 점·정확도를 높이려고 더한 점·실패 점·대기 점을 나눈다."""
    s = ctx.s
    if not s.variables:
        return []
    groups = {"base": [], "opt": [], "al": [], "failed": [], "waiting": []}
    for c in s.cases:
        if not ctx.has_params(c) or c.status in ("cancelled", "dry_run"):
            continue
        g = "failed" if c.status == "failed" else "waiting" if c.status in ("pending", "queued", "running") else "opt" if c.source == "bo" else "al" if c.source == "al" else "base"
        groups[g].append({"id": c.id, "x": ctx.x(c)})
    total = sum(len(v) for v in groups.values())
    if total > limit:
        step = max(2, math.ceil(total / limit))
        groups["base"] = groups["base"][::step]
    return [{"kind": "pairs", "variables": _var_rows(ctx), "groups": groups, "thinned": total > limit}]


def _var_rows(ctx: Ctx) -> list[dict]:
    return [{"name": v["name"], "label": ctx.label(v["name"]), "unit": ctx.unit_of(v["name"]), "lower": v["lower"], "upper": v["upper"]} for v in ctx.s.variables]


def _time_note(ctx: Ctx) -> str | None:
    """남은 시간. 시간을 잰 케이스가 3건 이상일 때만 말한다."""
    t = run_times(ctx.s.cases)
    left = len(ctx.waiting)
    if not left or len(t["durations"]) < 3:
        return None
    hours = float(np.median(t["durations"])) * left / max(1.0, t["parallel"]) / 3600
    return say("cov_time", m=left, t=hours_text(hours))


def run_times(cases: list[CaseRow]) -> dict:
    """케이스마다 잰 시간과 관측된 동시 실행 수. 동시 실행 수 = 시간 합 ÷ 무언가 돌고 있던 시간."""
    spans = []
    for c in cases:
        tm = c.timing or {}
        if c.status != "complete" or not tm.get("started_at") or not tm.get("ended_at"):
            continue
        try:
            a, b = datetime.fromisoformat(tm["started_at"]), datetime.fromisoformat(tm["ended_at"])
        except (TypeError, ValueError):
            continue
        if b > a:
            spans.append((a, b))
    if not spans:
        return {"durations": [], "parallel": 1.0, "total_sec": 0.0}
    spans.sort()
    busy, cur_a, cur_b = 0.0, spans[0][0], spans[0][1]
    for a, b in spans[1:]:
        if a > cur_b:
            busy += (cur_b - cur_a).total_seconds()
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    busy += (cur_b - cur_a).total_seconds()
    durations = [(b - a).total_seconds() for a, b in spans]
    return {"durations": durations, "parallel": sum(durations) / busy if busy > 0 else 1.0, "total_sec": sum(durations)}


# ---------- 실패·이상치·제약 ----------
def quality(ctx: Ctx) -> dict:
    s = ctx.s
    n, f = len(ctx.complete), len(ctx.failed)
    finished = n + f
    facts = {"complete": n, "failed": f, "fail_rate": _r(f / finished) if finished else None}
    charts = _outcome_charts(ctx)
    if n < 5 and not (finished >= 5 and f / finished > T_["fail_caution"]) and not (f >= 3 and n == 0):
        return _section("quality", "unknown", say("q_none", n=n, m=5 - n), facts=facts, charts=charts, basis=ctx.basis)
    verdicts: list[tuple[str, str]] = []
    fail_rate = f / finished if finished else 0.0
    cluster = None
    if f and n:
        U = ctx.unit([ctx.x(c) for c in ctx.complete + ctx.failed])
        mask = np.array([False] * n + [True] * f)
        cluster = calc.failure_cluster(U, mask, int(T_["fail_cluster_count"]), T_["fail_cluster_share"], T_["fail_cluster_ratio"], T_["fail_cluster_p"])
    if cluster:
        i = cluster["var"]
        lo, hi = float(s.lo[i] + cluster["lo"] * (s.hi[i] - s.lo[i])), float(s.lo[i] + cluster["hi"] * (s.hi[i] - s.lo[i]))
        facts["cluster"] = {"variable": s.names[i], "lo": _r(lo), "hi": _r(hi), "count": cluster["hit"], "p": _r(cluster["p"], 2)}
        verdicts.append(("caution", say("q_cluster", f=f, g=cluster["hit"], var=ctx.label(s.names[i]), range=ctx.span_text(s.names[i], lo, hi))))
    if fail_rate > T_["fail_caution"]:
        verdicts.append(("caution", say("q_fail_caution", p=pct(fail_rate * 100))))
    elif fail_rate >= T_["fail_fair"] and not cluster:
        # 몰림은 일정 건수가 넘어야 잰다. 실패가 모두 한 구간에 있어도 우연으로 설명되는 건수면, 재지 못한 것을 '몰리지 않았다'고 말하지 않는다
        can_tell = f >= T_["fail_cluster_count"] and (1 / 3) ** f * 3 * max(ctx.d, 1) < T_["fail_cluster_p"]
        verdicts.append(("fair", say("q_fail_fair" if can_tell else "q_fail_few", f=f, p=pct(fail_rate * 100))))
    # 값이 모두 같은 출력
    for name in ctx.output_order():
        vals = [v for _, v in ctx.values(name)]
        if is_constant(vals):
            verdicts.append(("caution", say("q_constant", out=ctx.label(name))))
    # 제약 통과율
    notes = []
    if s.constraints and ctx.complete:
        judged = [(c, ctx.feasible(c)) for c in ctx.complete]
        known = [(c, p) for c, p in judged if p is not None]
        ok = sum(1 for _, p in known if p)
        unknown = len(judged) - len(known)
        facts.update(pass_count=ok, pass_rate=_r(ok / len(known)) if known else None, constraint_unknown=unknown)
        if unknown:
            notes.append(say("q_con_missing", k=unknown))
        if known:
            rate = ok / len(known)
            if ok == 0:
                verdicts.append(("caution", T["q_pass_zero"]))
            elif rate < T_["pass_fair"]:
                verdicts.append(("caution", say("q_pass_low", p=pct(rate * 100), k=ok)))
            elif rate < T_["pass_good"]:
                verdicts.append(("fair", say("q_pass_fair", p=pct(rate * 100), k=ok)))
    # 튀는 값: 꼬리가 긴 정상 출력을 잘못 잡지 않게 모델이 있으면 모델 오차도 함께 본다. 빠른 층에서는 모델을 읽지 않으니 재지 않았다고 표시한다
    unchecked = bool(ctx.s.models and not ctx.use_models)
    facts["outliers_unchecked"] = unchecked
    flagged = []
    if not unchecked:
        for name in ctx.output_order():
            rows = ctx.values(name)
            if len(rows) < 5:
                continue
            y = np.array([v for _, v in rows])
            err = _cv_error_for(ctx, name, rows)
            U = ctx.unit([ctx.x(c) for c, _ in rows])
            inner = np.all((U >= T_["outlier_edge"]) & (U <= 1 - T_["outlier_edge"]), axis=1)
            hits = calc.outliers(y, T_["outlier_mad"], err, T_["outlier_cv"], T_["outlier_isolated"], inner)
            for i in hits:
                q1, q3 = np.percentile(y, [25, 75])
                flagged.append({"case_id": rows[i][0].id, "output": name, "value": _r(rows[i][1]),
                                "text": say("q_outlier", id=rows[i][0].id, out=ctx.label(name), val=with_unit(rows[i][1], ctx.unit_of(name)), q1=n3(float(q1)), q3=n3(float(q3)))})
    if flagged:
        facts["outliers"] = [{k: v for k, v in x.items() if k != "text"} for x in flagged[:10]]
        verdicts.append(("fair", flagged[0]["text"]))
        notes += [x["text"] for x in flagged[1:3]]
        for ch in charts:
            if ch["kind"] == "dots":
                ch["outliers"] = [x["case_id"] for x in flagged]
    state = "good"
    for st, _ in verdicts:
        if _RANK[st] > _RANK[state]:
            state = st
    if state == "good":
        rate = facts.get("pass_rate")
        if s.constraints and rate is not None:
            sentence = say("q_good_con", f=f, k=facts["pass_count"], p=pct(rate * 100))
        else:
            sentence = say("q_good_fast" if unchecked else "q_good", n=n, f=f)
    else:
        sentence = next(t for st, t in verdicts if st == state)
        notes = [t for st, t in verdicts if t != sentence] + notes
    if not n:
        sentence = say("q_all_failed", f=f)
    next_ = _ask(ctx, "원인 찾기 요청", "스터디 '{name}'의 실패한 케이스 원인을 찾아 고쳐 줘.", "fail") if cluster or fail_rate > T_["fail_caution"] else None
    return _section("quality", state, sentence, notes=notes, facts=facts, charts=charts, next_=next_, basis=ctx.basis, how=HOW_TO_READ["quality" if flagged else "quality_clean"])


def _cv_error_for(ctx: Ctx, name: str, rows: list[tuple[CaseRow, float]]) -> np.ndarray | None:
    """케이스 순서에 맞춘 교차검증 오차. 모델이 없으면 None. 학습에 없던 케이스는 0으로 둬 모델 오차 조건에서 빠진다."""
    m = ctx.model_for(name) if ctx.use_models else None
    if m is None:
        return None
    sur = ctx.surrogate(m)
    # 옛 모델은 출력마다 다시 재야 한다. 출력이 수십 개여도 결정에 쓰는 출력과 앞쪽만 잰다
    stored = bool((getattr(sur, "cv", None) or {}).get(name))
    if not stored and name not in ctx.decision_outputs() and name not in ctx.output_order()[:6]:
        return None
    cv = ctx.cv(sur, name, m)
    if not cv:
        return None
    X, _ = train_xy(sur.models[name])
    by = {tuple(round(v, 7) for v in row): float(p - a) for row, p, a in zip(X, cv["cv_pred"], cv["actual"])}
    return np.array([by.get(tuple(round(v, 7) for v in ctx.x(c)), 0.0) for c, _ in rows])


def _outcome_charts(ctx: Ctx, limit: int = 2000) -> list:
    charts = []
    per = {}
    for name in ctx.output_order()[:12]:
        rows = ctx.values(name)
        if len(rows) > limit:
            rows = rows[:: math.ceil(len(rows) / limit)]
        if rows:
            per[name] = {"label": ctx.label(name), "unit": ctx.unit_of(name), "points": [[c.id, v] for c, v in rows]}
    if per:
        charts.append({"kind": "dots", "outputs": per, "outliers": []})
    return charts


# ---------- 영향도 ----------
def influence(ctx: Ctx) -> dict | None:
    names = ctx.output_order()
    if not names:
        return _section("influence", "unknown", say("inf_few", n=len(ctx.complete), m=max(1, ctx.need - len(ctx.complete))), basis=ctx.basis)
    if ctx.s.models and not ctx.use_models:
        return _pending("influence")
    variants = []
    for o in names[:6]:
        try:
            variants.append(_influence_one(ctx, o))
        except Exception:
            log.exception("영향도 계산 실패: %s", o)
            ctx.had_error = True
            variants.append({"name": o, "label": ctx.label(o), "state": "unknown", "state_label": UNKNOWN_LABEL, "sentence": T["error"], "notes": [], "facts": {}, "charts": []})
    first = variants[0]
    # 닫힌 줄이 첫 출력만 말하면 다른 목표의 약한 판단이 가려진다. 상태는 결정에 쓰는 출력 중 가장 낮은 것으로 맞추고 이름을 함께 말한다
    state, sentence = first["state"], first["sentence"]
    key = ctx.decision_outputs()
    weak = [v for v in variants[1:] if v["name"] in key and v["state"] in ("fair", "caution") and _RANK[v["state"]] > _RANK[first["state"]]]
    if weak and first["state"] != "unknown":
        worst = max(weak, key=lambda v: _RANK[v["state"]])["state"]
        state = worst
        sentence = f"{sentence} {say('inf_others_' + worst, names='·'.join(v['label'] for v in weak))}"
    return _section("influence", state, sentence, notes=first["notes"], basis="predicted" if first.get("predicted") else ctx.basis,
                    facts=first["facts"], charts=first["charts"], model=first.get("model"), variants=variants, next_=first.get("next"))


def _influence_one(ctx: Ctx, o: str) -> dict:
    s, d = ctx.s, ctx.d
    rows = ctx.values(o)
    n = len(rows)
    label = ctx.label(o)
    base = {"name": o, "label": label, "unit": ctx.unit_of(o)}

    def out(state, sentence, notes=None, facts=None, charts=None, model=None, predicted=False, next_=None):
        return {**base, "state": state, "state_label": state_label("influence", state), "sentence": sentence, "notes": notes or [], "facts": facts or {},
                "charts": charts or [], "model": model, "predicted": predicted, "next": next_}

    y = np.array([v for _, v in rows]) if rows else np.zeros(0)
    if is_constant(y):
        return out("unknown", say("inf_flat", out=label), facts={"n": n})
    m = ctx.model_for(o)
    related = None
    if n >= ctx.need:
        related = calc.top_related(np.array([ctx.x(c) for c, _ in rows]), y, T_["related_p"])
    if m is None:
        if n < ctx.need:
            return out("unknown", say("inf_few", n=n, m=ctx.need - n), facts={"n": n})
        facts = {"n": n, "top": s.names[related[0]] if related[0] is not None else None, "spearman": _r(related[1])}
        sentence = say("inf_no_model", var=ctx.label(s.names[related[0]]), out=label) if related[0] is not None else say("inf_no_model_none", out=label)
        return out("unknown", sentence, facts=facts,
                   next_=_ask(ctx, "학습 요청", "스터디 '{name}'의 완료된 케이스로 예측 모델을 학습해 줘.", "train"))
    if ctx.near_flat(m, o) and (ctx.r2(m, o) or 0.0) < T_["r2_good"]:
        return out("unknown", say("inf_flat", out=label), facts={"n": n})
    sur = ctx.surrogate(m)
    pipe = sur.models[o]
    mean_fn = ctx.mean_fn(sur, o)
    res = calc.saltelli(mean_fn, d, 1024, seed=0)
    total = float(res["T"].sum())
    share = res["T"] / total if total > 0 else np.full(d, 1.0 / d)
    r2 = ctx.r2(m, o)
    rank = np.argsort(-share)
    # 순위 안정성: 고정 kernel로 5겹 다시 맞추고 표본을 줄여 다시 잰다
    low, high, top_same = share.copy(), share.copy(), 0
    X, Y = train_xy(pipe)
    folds = min(5, len(X))
    if folds >= 3:
        scaled = pipe[0].transform(X)
        lo, span = s.lo, s.hi - s.lo
        rounds = list(KFold(folds, shuffle=True, random_state=1).split(scaled))
        for tr, _ in rounds:
            gp = calc.fixed_gp(pipe[-1].kernel_).fit(scaled[tr], Y[tr])
            r = calc.saltelli(lambda U: gp.predict(pipe[0].transform(lo + np.asarray(U) * span)), d, 256, seed=1)
            tt = float(r["T"].sum())
            sh = r["T"] / tt if tt > 0 else share
            low, high = np.minimum(low, sh), np.maximum(high, sh)
            top_same += int(int(np.argmax(sh)) == int(rank[0]))
    stable = folds >= 3 and top_same >= T_["rank_stable"] * folds / 5
    # 교호작용: 혼자 효과와 전체 효과의 차이가 큰 변수를 찾는다. 방향 문장이 이 결과를 쓴다
    gap_st = res["T"] - res["S"]
    pairs = []
    if d >= 2 and float(np.max(gap_st)) >= T_["interaction"]:
        tops = [int(i) for i in np.argsort(-gap_st)[:3]]
        for a in range(len(tops)):
            for b in range(a + 1, len(tops)):
                v = calc.second_order(mean_fn, res, tops[a], tops[b])
                if v >= T_["pair"]:
                    pairs.append((tops[a], tops[b], v))
        pairs.sort(key=lambda t: -t[2])
    # 데이터가 닿은 구간. 빈 구간의 곡선은 예측으로 이어 그린 것이라 방향의 근거가 되지 못한다
    U_seen = ctx.unit([ctx.x(c) for c, _ in rows])
    lv = calc.level_counts(U_seen, int(T_["level_max"]))
    gap_limit = max(T_["gap_caution"], T_["gap_caution_per_n"] / max(n, 1))
    # 곡선과 방향
    gp = pipe[-1]

    def predict_cov(U):
        return gp.predict(pipe[0].transform(s.lo + np.asarray(U) * (s.hi - s.lo)), return_cov=True)

    effects, directions, gaps = {}, {}, {}
    for i in rank[:8]:
        i = int(i)
        e = calc.main_effect(predict_cov, d, i)
        span_i = float(s.hi[i] - s.lo[i]) or 1.0
        xs = np.array([float(s.lo[i] + g * span_i) for g in e["x"]])
        seen_lo, seen_hi = float(U_seen[:, i].min()), float(U_seen[:, i].max())
        grid = np.asarray(e["x"])
        inside = (grid >= seen_lo - 1e-9) & (grid <= seen_hi + 1e-9)
        mean, sd = np.asarray(e["mean"]), np.asarray(e["sd"])
        use = inside if inside.sum() >= 5 else np.ones(len(grid), dtype=bool)
        dr = dict(calc.direction(xs[use].tolist(), mean[use].tolist(), T_["mono"]))
        # 곡선 변화가 불확실 폭 안이면 방향을 말할 수 없고, 혼자 효과가 작으면 방향이 다른 변수에 달렸다
        t_i, s_i = float(res["T"][i]), float(res["S"][i])
        if t_i > 0 and s_i / t_i < T_["dir_main_ratio"]:
            dr = {"kind": "depends", "x": None}
        elif dr["kind"] != "flat" and float(np.ptp(mean[use])) < 2 * float(np.mean(sd[use])):
            dr = {"kind": "unsure", "x": None}
        # 범위 끝에 조금 못 미친 것까지 말하면 첫 줄이 군말이 된다. 정말 비어 있을 때만 구간을 밝힌다
        dr["partial"] = 1 - (seen_hi - seen_lo) > T_["gap_fair"]
        directions[i] = dr
        gaps[i] = calc.largest_gap(U_seen[:, i], bool(lv[i]))
        effects[s.names[i]] = {"x": xs.tolist(), "mean": e["mean"], "sd": e["sd"], "unit": ctx.unit_of(s.names[i]),
                               "seen": [float(s.lo[i] + seen_lo * span_i), float(s.lo[i] + seen_hi * span_i)],
                               "caption": say("curve_caption", var=ctx.label(s.names[i]), out=label)}
    obj = next((x for x in s.objectives if x["name"] == o), None)

    def partner_of(i: int) -> str:
        for a, b, _ in pairs:
            if i in (a, b):
                return ctx.label(s.names[b if a == i else a])
        return "다른 변수"

    def dir_text(i: int) -> str:
        v = ctx.label(s.names[i])
        dr = directions.get(i, {"kind": "flat"})
        if share[i] < T_["share_small"] or dr["kind"] == "flat":
            return say("dir_small", var=v)
        if dr["kind"] == "depends":
            return say("dir_depends", var=v, out=label, partner=partner_of(i))
        if dr["kind"] == "unsure":
            return say("dir_unsure", var=v, out=label)
        if dr["kind"] in ("down", "up"):
            t = say(f"dir_{dr['kind']}", var=v, out=label)
            if obj and ((dr["kind"] == "down") == (obj.get("goal", "min") == "min")):
                t = t[:-1] + "(목표와 같은 방향)."
        else:
            t = say(f"dir_{dr['kind']}", var=v, out=label, x=ctx.val(s.names[i], dr["x"]))
        if dr.get("partial"):
            seen = effects[s.names[i]]["seen"]
            t = f"{t} {say('dir_seen', range=ctx.span_text(s.names[i], seen[0], seen[1]))}"
        return t

    notes = []
    i1 = int(rank[0])
    i2 = int(rank[1]) if d > 1 else None
    if pairs:
        notes.append(say("inf_interaction", var=ctx.label(s.names[pairs[0][0]]), partner=ctx.label(s.names[pairs[0][1]])))
    v1, p1 = ctx.label(s.names[i1]), round(float(share[i1]) * 100)
    level = ctx.verdict(m, o)
    # 함께 움직인 변수: 독립으로 바꿔 보는 계산이 그 둘을 나눌 수 없다. 처음 점으로 잰다(최적화 점은 일부러 몰린다)
    tied = None
    base_x = [ctx.x(c) for c, _ in rows if c.source not in ("bo", "al")]
    if d >= 2 and len(base_x) >= 4:
        base_u = ctx.unit(base_x)
        lim = max(T_["corr_caution"], calc.doe_baseline(min(len(base_u), 500), d)["corr_lhs_p90"] + T_["corr_caution_margin"])
        top_two = [i1] + ([i2] if i2 is not None else [])
        for a in top_two:
            for b in range(d):
                if b == a or np.ptp(base_u[:, a]) == 0 or np.ptp(base_u[:, b]) == 0:
                    continue
                r_ab = float(np.corrcoef(base_u[:, a], base_u[:, b])[0, 1])
                if abs(r_ab) >= lim and (tied is None or abs(r_ab) > abs(tied[2])):
                    tied = (a, b, r_ab)
    facts = {"n_train": m.n_train, "r2": _r(r2), "top": s.names[i1], "stable": bool(stable),
             "shares": {s.names[i]: _r(share[i], 3) for i in range(d)},
             "first_order": {s.names[i]: _r(res["S"][i], 3) for i in range(d)},
             "total_order": {s.names[i]: _r(res["T"][i], 3) for i in range(d)},
             "directions": {s.names[i]: dict(directions[i]) for i in directions},
             "pairs": [[s.names[a], s.names[b], _r(v, 3)] for a, b, v in pairs]}
    bars = [{"name": s.names[i], "label": ctx.label(s.names[i]), "share": _r(share[i], 3), "low": _r(low[i], 3), "high": _r(high[i], 3)} for i in rank]
    charts = [{"kind": "influence", "bars": bars, "effects": effects, "output": label, "unit": ctx.unit_of(o), "weak": level == "caution"}]
    info = ctx.model_info(m, o)
    if level == "unknown":
        return out("unknown", say("inf_few", n=ctx.rows_of(m, o), m=max(1, ctx.need - ctx.rows_of(m, o))), facts=facts, charts=charts, model=info, predicted=True)
    if level == "caution":
        # 약한 모델의 교호작용은 말하지 않는다. 해석값과의 관련은 우연으로 설명되지 않을 때만 이름을 댄다
        if related is None:
            sentence = T["inf_caution_few"]
        elif related[0] is None:
            sentence = T["inf_caution_none"]
        else:
            sentence = say("inf_caution", var=ctx.label(s.names[related[0]]))
        return out("caution", sentence, facts=facts, charts=charts, model=info, predicted=True, next_=_ask_al(ctx))
    if tied:
        facts["tied"] = [s.names[tied[0]], s.names[tied[1]], _r(tied[2], 2)]
        sentence = say("inf_tied", a=ctx.label(s.names[tied[0]]), b=ctx.label(s.names[tied[1]]), r=f"{tied[2]:.2f}", out=label)
        return out("fair", sentence, notes=notes, facts=facts, charts=charts, model=info, predicted=True)
    top_gap = gaps.get(i1)
    if top_gap and top_gap["gap"] > gap_limit:
        span_1 = float(s.hi[i1] - s.lo[i1])
        rng = ctx.span_text(s.names[i1], float(s.lo[i1] + top_gap["lo"] * span_1), float(s.lo[i1] + top_gap["hi"] * span_1))
        facts["unseen_top"] = {"variable": s.names[i1], "gap": _r(top_gap["gap"])}
        notes.insert(0, dir_text(i1))
        return out("fair", say("inf_gap", out=label, var1=v1, p1=p1, range=rng), notes=notes, facts=facts, charts=charts, model=info, predicted=True)
    if d == 1:
        sentence = f"{say('inf_single', var=v1, out=label)} {dir_text(i1)}"
    elif level == "good" and stable:
        if share[i2] >= T_["share_small"]:
            sentence = f"{say('inf_good', out=label, var1=v1, p1=p1, var2=ctx.label(s.names[i2]), p2=round(float(share[i2]) * 100))} {dir_text(i1)}"
        else:
            sentence = f"{say('inf_good_one', out=label, var1=v1, p1=p1)} {dir_text(i1)}"
    else:
        sentence = say("inf_fair", out=label, var1=v1, p1=p1)
        notes.insert(0, dir_text(i1))
        if not stable:
            notes.insert(1, T["inf_unstable"])
        if level == "fair":
            notes.append(T["inf_fair_more"])
    state = "good" if (level == "good" and stable) or (d == 1 and level == "good") else "fair"
    if d > 1 and state == "good" and i2 is not None:
        notes.insert(0, dir_text(i2))
    return out(state, sentence, notes=notes, facts=facts, charts=charts, model=info, predicted=True)


# ---------- 예측 정확도 ----------
def trust(ctx: Ctx) -> dict:
    names = ctx.output_order()
    if ctx.s.models and not ctx.use_models:
        return _pending("trust")
    have = [o for o in names if ctx.model_for(o) is not None]
    if not have:
        ready = len(ctx.complete) >= ctx.need
        next_ = _ask(ctx, "학습 요청", "스터디 '{name}'의 완료된 케이스로 예측 모델을 학습해 줘.", "train") if ready else None
        sentence = say("tr_no_model_ready", n=len(ctx.complete)) if ready else say("tr_no_model", need=ctx.need)
        return _section("trust", "unknown", sentence, facts={"need": ctx.need}, next_=next_, basis="predicted")
    variants = [_trust_one(ctx, o) for o in have[:6]]
    # 결정에 쓰는 출력(목적·제약) 중 가장 나쁜 것을 앞세운다
    key = ctx.decision_outputs()
    pool = [v for v in variants if v["name"] in key] or variants
    worst = max(pool, key=lambda v: _RANK[v["state"]])
    for v in variants:
        ctx.obj_trust[v["name"]] = v
    notes = list(worst["notes"])
    return _section("trust", worst["state"], worst["sentence"], notes=notes, facts=worst["facts"], charts=worst["charts"], model=worst["model"],
                    variants=variants, basis="predicted", next_=worst.get("next"), lead=worst["name"])


def _trust_one(ctx: Ctx, o: str) -> dict:
    m = ctx.model_for(o)
    sur = ctx.surrogate(m)
    label, unit = ctx.label(o), ctx.unit_of(o)
    r2, rmse = ctx.r2(m, o), ctx.rmse(m, o)
    info = ctx.model_info(m, o)
    base = {"name": o, "label": label, "unit": unit, "model": info}
    vals = [v for _, v in ctx.values(o)]

    def blank(sentence: str) -> dict:
        return {**base, "state": "unknown", "state_label": UNKNOWN_LABEL, "sentence": sentence, "notes": [], "facts": {"n_train": m.n_train}, "charts": []}

    if is_constant(vals):
        return blank(say("tr_flat", out=label))
    if ctx.rows_of(m, o) < ctx.need:  # 몇 건으로 잰 80백분위는 우연이다. 최소 건수부터 판단한다
        return blank(say("tr_few", n=ctx.rows_of(m, o), m=ctx.need - ctx.rows_of(m, o)))
    cv = ctx.cv(sur, o, m)
    level = ctx.verdict(m, o)
    if level == "unknown":
        return blank(say("tr_flat", out=label))
    sc = ctx.score(m, o)
    facts: dict = {"n_train": m.n_train, "r2": _r(sc["r2"] if sc else r2), "r2_all_points": _r(r2), "rmse": _r(sc["rmse"] if sc else rmse), "unit": unit, "stale_by": info["stale_by"], "model_version": m.version}
    charts, notes = [], []
    worst_rows = []
    p80 = sc["p80"] if sc else None
    if cv:
        actual, pred = np.array(cv["actual"], dtype=float), np.array(cv["cv_pred"], dtype=float)
        err = np.abs(pred - actual)
        facts["p80"] = _r(p80)
        X, _ = train_xy(sur.models[o])
        by = ctx.case_by_x()
        found = [by.get(tuple(round(v, 7) for v in row)) for row in X]
        ids = [getattr(c, "id", None) for c in found]
        scored = [getattr(c, "source", "doe") != "bo" or not (sc and sc["dropped"]) for c in found]
        order = [int(i) for i in np.argsort(-err) if scored[int(i)]]
        for i in order[:3]:
            worst_rows.append({"case_id": ids[i], "actual": _r(actual[i]), "predicted": _r(pred[i]), "error": _r(err[i])})
        charts.append({"kind": "pva", "points": [[ids[i], float(actual[i]), float(pred[i]), bool(scored[i])] for i in range(len(actual))], "unit": unit, "output": label, "worst": worst_rows})
    pv = with_unit(p80, unit) if p80 is not None else None
    near = ctx.near_flat(m, o) and (sc["r2"] if sc and sc["r2"] is not None else (r2 or 0.0)) < T_["r2_good"]
    if near:
        sentence = say("tr_nearflat", out=label, val=with_unit(float(np.median(vals)), unit), p80=pv or with_unit(rmse, unit))
        facts["near_flat"] = True
    elif level == "good":
        sentence = say("tr_good", out=label, p80=pv) if pv else say("tr_rmse_good", out=label, rmse=with_unit(rmse, unit))
    elif level == "fair":
        sentence = say("tr_fair", out=label, p80=pv) if pv else say("tr_rmse_fair", out=label, rmse=with_unit(rmse, unit))
    else:
        sentence = say("tr_caution", out=label, p80=pv) if pv else say("tr_rmse_caution", out=label, rmse=with_unit(rmse, unit))
    if sc and sc["dropped"]:
        facts["scored_points"] = sc["n"]
        notes.append(say("tr_region", k=sc["dropped"], b=sc["n"]))
    if m.n_train < ctx.rec and level != "good":
        notes.append(say("tr_small", n=m.n_train))
    if ctx.is_stale(m, o):
        notes.append(say("tr_stale", k=info["stale_by"]))
    # 모델이 모르는 곳
    Q = calc.sobol_unit(ctx.d, 2048, seed=2)
    pipe = sur.models[o]
    _, sd = pipe[-1].predict(pipe[0].transform(ctx.s.lo + Q * (ctx.s.hi - ctx.s.lo)), return_std=True)
    top = Q[sd >= np.percentile(sd, 95)]
    if len(top):
        mid = top.mean(axis=0) - 0.5
        i = int(np.argmax(np.abs(mid)))
        if abs(mid[i]) > 0.25:
            notes.append(say("tr_unsure", var=ctx.label(ctx.s.names[i]), side="큰" if mid[i] > 0 else "작은"))
            facts["unsure"] = {"variable": ctx.s.names[i], "side": "high" if mid[i] > 0 else "low"}
    # 돌리기 전 예측 대 결과: 그 케이스를 배우기 전 모델로 예측한 값이라 학습에 쓰이지 않은 진짜 사전 예측이다
    prior = prior_predictions(ctx, o)
    if len(prior) >= T_["calib_min"]:
        inside = sum(1 for r in prior if abs(r["actual"] - r["predicted"]) <= 2 * r["std"])
        rate = inside / len(prior)
        facts["prior_within"] = _r(rate, 3)
        facts["prior_count"] = len(prior)
        notes.append(say("tr_calib_low" if rate < T_["calib"] else "tr_calib", total=len(prior), hit=inside))
        charts.append({"kind": "prior", "rows": prior[-6:], "unit": unit, "output": label})
    next_ = None
    if level == "caution":
        next_ = _ask_al(ctx)
    elif ctx.is_stale(m, o):
        next_ = _ask(ctx, "재학습 요청", "스터디 '{name}'의 최신 해석까지 넣어 예측 모델을 다시 학습해 줘.", "train")
    elif level == "fair" and ctx.s.objectives:
        next_ = _ask(ctx, "확인 해석 요청", "스터디 '{name}'에서 예측으로 고른 최선 설계를 해석 1건으로 확인해 줘.", "verify")
    return {**base, "state": level, "state_label": state_label("trust", level), "sentence": sentence, "notes": notes, "facts": facts, "charts": charts, "next": next_}


def prior_predictions(ctx: Ctx, o: str) -> list[dict]:
    """추가 케이스마다 만들어지기 직전 모델의 예측. 그 모델은 이 케이스를 학습하지 않았다."""
    done = []
    rows = [(c, v) for c, v in ctx.values(o) if c.source in ("bo", "al") and c.created_at]
    for c, v in rows:
        m = next((x for x in ctx.s.models if x.created_at and x.created_at < c.created_at and o in x.outputs), None)
        sur = ctx.surrogate(m) if m else None
        if sur is None or o not in sur.models:
            continue
        pipe = sur.models[o]
        mu, sd = pipe[-1].predict(pipe[0].transform(np.array([ctx.x(c)])), return_std=True)
        done.append({"case_id": c.id, "source": c.source, "model_version": m.version, "actual": _r(v), "predicted": _r(mu[0]), "std": _r(sd[0]), "n_train": m.n_train})
    return done


# ---------- 수렴성 ----------
def _ref_percent(start: float, ptp: float) -> bool:
    """기준값이 0에 가까우면 퍼센트가 뜻이 없다. 단위 값만 말한다."""
    return abs(start) >= 0.05 * ptp and abs(start) > 0


def convergence(ctx: Ctx) -> dict | None:
    s = ctx.s
    if not s.objectives:
        return None
    if ctx.s.models and not ctx.use_models:
        return _pending("convergence")
    if len(s.objectives) >= 2:
        return _convergence_multi(ctx)
    obj = s.objectives[0]
    o, sign = obj["name"], 1.0 if obj.get("goal", "min") == "min" else -1.0
    label, unit = ctx.label(o), ctx.unit_of(o)
    rows = ctx.values(o)
    bo_all = [c for c in ctx.complete if c.source == "bo"]
    k = len(bo_all)
    W = max(int(T_["conv_window_min"]), ctx.d)
    how = HOW_TO_READ["convergence_min" if sign > 0 else "convergence_max"]
    # '처음 계획한 해석'은 정말 처음 점이어야 한다. 능동학습 점은 처음 값에도, 최적화 개선에도 넣지 않는다
    base_vals = [v for c, v in rows if c.source not in ("bo", "al")]
    ptp = float(np.ptp(base_vals)) if len(base_vals) >= 2 else 0.0
    feas = [(c, v) for c, v in rows if ctx.feasible(c) is True]
    curve = _curve(ctx, rows, feas, sign)
    charts = [{"kind": "curve", "output": label, "unit": unit, "goal": obj.get("goal", "min"), **curve}]
    facts = {"bo": k, "window": W, "unit": unit, "goal": obj.get("goal", "min"), "output": o}
    m = ctx.model_for(o)
    level = ctx.verdict(m, o) if m else "unknown"
    cost = ctx.cost(W)
    if k == 0:
        next_ = _ask(ctx, "최적화 요청", "스터디 '{name}'에서 최적화를 돌려 줘.", "bo") if m else None
        return _section("convergence", "unknown", T["con_none"], facts=facts, charts=charts if rows else [], how=how, next_=next_, basis=ctx.basis)
    if not feas:
        state, sentence = ctx.no_feasible("con")
        return _section("convergence", state, sentence, facts=facts, charts=charts, how=how, basis=ctx.basis)
    base_feas = [v for c, v in feas if c.source not in ("bo", "al")]
    start = (min(base_feas, key=lambda v: sign * v) if base_feas else feas[0][1])
    now = min((v for _, v in feas), key=lambda v: sign * v)
    total = sign * (start - now)
    if k < W:
        return _section("convergence", "unknown", say("con_few", k=k, W=W, m=W - k), facts={**facts, "start": _r(start), "now": _r(now)}, charts=charts, how=how, basis=ctx.basis)
    first, seq = _best_after_bo(ctx, feas, start, sign)
    before = seq[-W - 1] if len(seq) > W else first
    recent = sign * (before - now)
    use_pct = _ref_percent(start, ptp)
    ref = abs(start)
    gain = f"{pct(total / ref * 100)}%" if use_pct else with_unit(total, unit)
    rtext = f"{pct(recent / ref * 100)}%" if use_pct else with_unit(recent, unit)
    ei = _ei_left(ctx, m, o, now, sign) if m else None
    facts.update(start=_r(start), now=_r(now), total=_r(total), recent=_r(recent), total_pct=_r(total / ref * 100) if use_pct else None,
                 recent_pct=_r(recent / ref * 100) if use_pct else None, ei_left=_r(ei), ptp=_r(ptp))
    span = ptp if ptp > 0 else max(abs(start), 1.0)
    small_recent = recent / span <= T_["conv_range"]
    ei_small = ei is not None and ei / span <= T_["conv_ei"]
    again = _ask(ctx, "추가 최적화 요청", f"스터디 '{{name}}'에서 최적화를 {W}건 더 돌려 줘.", "bo")
    v = lambda x: with_unit(x, unit)  # noqa: E731
    if small_recent and level == "caution":
        return _section("convergence", "caution", T["con_poor"], facts=facts, charts=charts, how=how, basis=ctx.basis, next_=_ask_al(ctx))
    if not base_feas:
        # 처음 점에 제약을 만족한 설계가 없었다. '처음 최고'는 없고, 찾아낸 것 자체가 진전이다
        facts["no_start"] = True
        return _section("convergence", "fair", say("con_found", now=v(now), W=W, r=v(recent)), facts={**facts, "more_worth": True}, charts=charts, how=how, next_=again, basis=ctx.basis)
    if total <= 0:
        if ei_small:
            return _section("convergence", "fair", say("con_flat_ok", k=k), facts=facts, charts=charts, how=how, basis=ctx.basis, label="더 돌려도 효과 작음")
        if k >= 2 * W:
            return _section("convergence", "caution", say("con_flat_bad", k=k), facts=facts, charts=charts, how=how, basis=ctx.basis)
        return _section("convergence", "fair", say("con_room", W=W, cost=cost), facts={**facts, "more_worth": True}, charts=charts, how=how, next_=again, basis=ctx.basis)
    stop = small_recent and recent / total <= T_["conv_total"] and ei_small
    if stop and level == "good":
        # 남은 여지는 예측 모델의 짐작이다. 설계 범위 전체를 잘 맞힐 때만 믿고, 그때도 조건을 붙여 말한다(좁은 골짜기를 놓치면 예측은 자신 있게 틀린다)
        return _section("convergence", "good", say("con_stop", start=v(start), now=v(now), gain=gain, W=W, r=rtext), notes=[T["con_stop_note"]], facts=facts, charts=charts, how=how, basis=ctx.basis)
    if stop:
        return _section("convergence", "fair", say("con_soft", W=W, r=rtext, cost=cost), facts={**facts, "more_worth": True}, charts=charts, how=how, next_=again, basis=ctx.basis)
    if small_recent:
        return _section("convergence", "fair", say("con_room", W=W, cost=cost), facts={**facts, "more_worth": True}, charts=charts, how=how, next_=again, basis=ctx.basis)
    return _section("convergence", "fair", say("con_going", W=W, r=rtext, cost=cost), notes=[say("con_going_note", start=v(start), now=v(now))], facts={**facts, "improving": True},
                    charts=charts, how=how, next_=again, basis=ctx.basis)


def _best_after_bo(ctx: Ctx, feas, start, sign) -> tuple[float, list[float]]:
    """(최적화 직전의 최고 값, 최적화 케이스가 하나 끝날 때마다의 최고 값). 능동학습이 찾은 값도 실행 순서대로 반영한다.
    그래야 능동학습이 이미 찾은 값을 최적화의 개선으로 세지 않는다. 제약을 어긴 최적화 케이스도 한 건으로 센다."""
    vals = {c.id: v for c, v in feas}
    best, first, out = start, None, []
    for c in ctx.complete:
        if c.source not in ("bo", "al"):
            continue
        if c.source == "bo" and first is None:
            first = best
        v = vals.get(c.id)
        if v is not None and sign * v < sign * best:
            best = v
        if c.source == "bo":
            out.append(best)
    return (start if first is None else first), out


def _curve(ctx: Ctx, rows, feas, sign) -> dict:
    ok = {c.id for c, _ in feas}
    best, out = None, {"index": [], "case_ids": [], "values": [], "best": [], "source": [], "feasible": []}
    for i, (c, v) in enumerate(rows, 1):
        if c.id in ok:
            best = v if best is None or sign * v < sign * best else best
        out["index"].append(i)
        out["case_ids"].append(c.id)
        out["values"].append(v)
        out["best"].append(best)
        out["source"].append(c.source)
        out["feasible"].append(ctx.feasible(c))  # True·False·None(값이 없어 모름). 모르는 값을 '어겼다'고 그리지 않는다
    first_bo = next((i for i, c in enumerate(rows, 1) if c[0].source == "bo"), None)
    out["bo_from"] = first_bo
    return out


def _ei_left(ctx: Ctx, m: ModelRow, o: str, now: float, sign: float) -> float | None:
    """모델이 보는 남은 여지: 가장 큰 기대 개선(제약 확률 포함)."""
    sur = ctx.surrogate(m)
    if sur is None:
        return None
    cand = ctx.s.lo + calc.sobol_unit(ctx.d, 4096, seed=3) * (ctx.s.hi - ctx.s.lo)
    mean, std = sur.predict(cand)
    ei = bayesopt.expected_improvement(sign * mean[o], std[o], sign * now, xi=0.0)
    ei = ei * bayesopt.constraint_probability(mean, std, [c for c in ctx.s.constraints if c["name"] in sur.models], len(cand))
    return float(np.max(ei))


def _objective_matrix(ctx: Ctx, names: list[str], goals: list[str]) -> tuple[list[CaseRow], np.ndarray, np.ndarray]:
    """제약을 모두 만족하고 값이 다 있는 해석 케이스. 제약 값이 없는 케이스는 판단할 수 없으니 뺀다."""
    rows = []
    for c in ctx.complete:
        vals = [num(c.results.get(n)) for n in names]
        if any(v is None for v in vals) or ctx.feasible(c) is not True:
            continue
        rows.append((c, vals))
    signs = np.array([1.0 if g == "min" else -1.0 for g in goals])
    Y = np.array([v for _, v in rows], dtype=float).reshape(len(rows), len(names))
    return [c for c, _ in rows], Y, Y * signs


def _hv_left(ctx: Ctx, names: list[str], goals: list[str], S: np.ndarray, ref: np.ndarray, now: float) -> float | None:
    """예측으로 본 남은 몫: 예측 앞면까지 합쳤을 때 종합 점수가 얼마나 늘어나는가. 짧은 평평함만으로 멈추라고 하지 않기 위한 근거다.
    예측 모델이 약하거나 목표를 다 갖지 않으면 재지 않는다."""
    ms = [ctx.model_for(n) for n in names]
    if any(m is None or ctx.verdict(m, n) == "caution" for m, n in zip(ms, names)) or len({m.id for m in ms}) != 1 or now <= 0:
        return None
    sur = ctx.surrogate(ms[0])
    cand = ctx.s.lo + calc.sobol_unit(ctx.d, 4096, seed=8) * (ctx.s.hi - ctx.s.lo)
    mean, std = sur.predict(cand)
    ok = bayesopt.constraint_probability(mean, std, [c for c in ctx.s.constraints if c["name"] in sur.models], len(cand)) >= 0.5
    if not ok.any():
        return 0.0
    P = np.column_stack([(1.0 if g == "min" else -1.0) * mean[n][ok] for n, g in zip(names, goals)])
    P = P[calc.non_dominated(P)]
    return max(0.0, (calc.hypervolume(np.vstack([S, P]), ref) - now) / now)


def _convergence_multi(ctx: Ctx) -> dict:
    """목적이 둘 이상이면 앞면이 덮는 넓이(종합 점수)가 얼마나 늘었는지로 본다."""
    s = ctx.s
    names = [o["name"] for o in s.objectives]
    goals = [o.get("goal", "min") for o in s.objectives]
    how = HOW_TO_READ["convergence_multi"]
    goals_word = {2: "두 목표", 3: "세 목표"}.get(len(names), "여러 목표")
    bo_all = [c for c in ctx.complete if c.source == "bo"]
    k, W = len(bo_all), max(int(T_["conv_window_min"]), ctx.d)
    facts = {"bo": k, "window": W, "multi": True}
    cases, Y, S = _objective_matrix(ctx, names, goals)
    cost = ctx.cost(W)
    if not k:
        return _section("convergence", "unknown", T["con_none"], facts=facts, how=how, basis=ctx.basis,
                        next_=_ask(ctx, "최적화 요청", f"스터디 '{{name}}'에서 {goals_word} 최적화를 돌려 줘.", "bo") if s.models else None)
    if not len(S):
        state, sentence = ctx.no_feasible("con")
        return _section("convergence", state, sentence, facts=facts, how=how, basis=ctx.basis)
    is_base = np.array([c.source not in ("bo", "al") for c in cases])
    base = S[is_base] if is_base.any() else S
    ref = base.max(axis=0) + 0.1 * np.where(np.ptp(base, axis=0) > 0, np.ptp(base, axis=0), np.maximum(np.abs(base.max(axis=0)), 1.0))
    # 지금까지의 점에 진 점이면 앞면이 그대로이니 다시 재지 않는다(3목표 이상은 몬테카를로라 비싸다)
    hv: list[float] = []
    for i in range(1, len(S) + 1):
        kept = hv and bool(np.any(np.all(S[:i - 1] <= S[i - 1], axis=1)))
        hv.append(hv[-1] if kept else calc.hypervolume(S[:i], ref))
    start = calc.hypervolume(base, ref)
    now = hv[-1]
    sources = [c.source for c in cases]
    bo_from = next((i + 1 for i, src in enumerate(sources) if src == "bo"), None)
    # 처음 해석에서 크게 뛴 값이 축을 차지하면 최적화 구간이 평평해 보여 문장과 어긋난다. 최적화 구간을 확대한다
    zoom = None
    if bo_from and bo_from > 1:
        low = hv[bo_from - 2]
        zoom = [low - 0.1 * max(now - low, abs(now) * 0.01), now + 0.1 * max(now - low, abs(now) * 0.01)]
    charts = [{"kind": "hv", "index": list(range(1, len(hv) + 1)), "hv": hv, "source": sources, "bo_from": bo_from, "zoom": zoom, "axis": f"{goals_word} 종합 점수 (클수록 좋음, 단위 없음)"}]
    if k < W:
        return _section("convergence", "unknown", say("con_few", k=k, W=W, m=W - k), facts=facts, charts=charts, how=how, basis=ctx.basis)
    pos = [i for i, c in enumerate(cases) if c.source == "bo"]
    before = hv[pos[-W] - 1] if len(pos) >= W and pos[-W] > 0 else start
    recent = now - before
    total = now - start
    facts.update(hv_start=_r(start), hv_now=_r(now), recent_pct=_r(recent / now * 100) if now > 0 else None, total_pct=_r(total / start * 100) if start > 0 else None)
    rtext = f"{pct(recent / now * 100)}%" if now > 0 else "0%"
    gain = f"{pct(total / start * 100)}%" if start > 0 else "0%"
    note = say("con_multi_note", gain=gain)
    again = _ask(ctx, "추가 최적화 요청", f"스터디 '{{name}}'에서 {goals_word} 최적화를 {W}건 더 돌려 줘.", "bo")
    small_recent = now <= 0 or recent / now <= T_["conv_range"]
    levels = [ctx.verdict(ctx.model_for(n), n) for n in names]
    # 한 목표일 때와 같은 규칙: 모델이 약하면 평평해 보여도 다 찾았다고 말하지 않고, 나아진 적이 없으면 좋다고 말하지 않는다
    if small_recent and "caution" in levels:
        return _section("convergence", "caution", T["con_poor"], notes=[note], facts=facts, charts=charts, how=how, basis=ctx.basis, next_=_ask_al(ctx))
    if total <= 0:
        if k >= 2 * W:
            return _section("convergence", "caution", say("con_multi_flat_bad", k=k, goals=goals_word), facts=facts, charts=charts, how=how, basis=ctx.basis)
        return _section("convergence", "fair", say("con_multi_flat", k=k, goals=goals_word, W=W), facts={**facts, "more_worth": True}, charts=charts, how=how, next_=again, basis=ctx.basis)
    if small_recent and recent / total <= T_["conv_total"]:
        # 짧게 평평한 것은 이 방법에서 흔하다(외팔보: 10건에서 멈춘 듯했지만 35건 더 돌리니 그동안의 71%만큼 더 늘었다).
        # 충분히 돌렸고, 예측으로 봐도 남은 몫이 작을 때만 멈추라고 말한다
        left = _hv_left(ctx, names, goals, S, ref, now) if k >= T_["conv_multi_windows"] * W else None
        facts["hv_left_pct"] = _r(left * 100) if left is not None else None
        if left is not None and left <= T_["conv_hv_left"] and all(lv == "good" for lv in levels):
            return _section("convergence", "good", say("con_multi_stop", goals=goals_word, W=W, r=rtext), notes=[note, T["con_stop_note"]], facts=facts, charts=charts, how=how, basis=ctx.basis)
        return _section("convergence", "fair", say("con_multi_soft", goals=goals_word, W=W, r=rtext, cost=cost), notes=[note], facts={**facts, "more_worth": True}, charts=charts, how=how, next_=again, basis=ctx.basis)
    return _section("convergence", "fair", say("con_multi_going", goals=goals_word, W=W, r=rtext, cost=cost), notes=[note], facts={**facts, "improving": True}, charts=charts, how=how, next_=again, basis=ctx.basis)


# ---------- 최적 설계 점검 ----------
def _best(ctx: Ctx) -> CaseRow | None:
    s = ctx.s
    if not s.objectives:
        return None
    o = s.objectives[0]["name"]
    if not s.best_failed:
        picked = next((c for c in s.cases if c.id == s.best_id), None)
        if picked is None or num(picked.results.get(o)) is not None:
            return picked
        # NaN이 먼저 오면 best_case가 그 케이스를 고른다. '해석이 없다'고 잘못 말하지 않게 값이 있는 최고를 쓴다
    sign = 1.0 if s.objectives[0].get("goal", "min") == "min" else -1.0
    rows = [(c, v) for c, v in ctx.values(o) if ctx.feasible(c) is True]
    return min(rows, key=lambda t: sign * t[1])[0] if rows else None


def _design_text(ctx: Ctx, params: dict, share: dict | None = None, limit: int = 4) -> str:
    """설계 값을 말로. 영향이 작은 변수는 빼되 뺐다는 것을 숨기지 않는다."""
    names = ctx.s.names
    if share:
        ranked = sorted(names, key=lambda n: -(share.get(n) or 0))
        names = [n for n in ranked if (share.get(n) or 0) >= T_["share_small"]] or ranked[:1]
    picked = names[:limit]
    text = ", ".join(f"{ctx.label(n)} {ctx.val(n, params[n])}" for n in picked)
    left = len(ctx.s.names) - len(picked)
    return text + (f"(나머지 {left}개 변수는 표에 있습니다)" if left else "")


def _optimum_caps(ctx: Ctx, o: str) -> list[tuple[str, str]]:
    """이 설계를 써도 되는지는 앞 항목들의 판단에 달렸다. 범위 끝·여유만 보고 '써도 됨'이라고 하면, 예측도 약하고 최적화도 안 돌린 무작위 최고점을 답으로 내주게 된다."""
    s = ctx.s
    caps: list[tuple[str, str]] = []
    k = sum(1 for c in ctx.complete if c.source == "bo")
    W = max(int(T_["conv_window_min"]), ctx.d)
    m = ctx.model_for(o)
    level = ctx.verdict(m, o) if m else "unknown"
    if level == "caution":
        sc = ctx.score(m, o)
        e = with_unit(sc["p80"] if sc else ctx.rmse(m, o), ctx.unit_of(o))
        if k == 0:
            caps.append(("caution", say("why_trust_none", e=e)))
        elif k < W:
            caps.append(("caution", say("why_trust_few", e=e, k=k)))
        else:
            caps.append(("caution", say("why_trust", e=e)))
    con = ctx.by.get("convergence")
    if k == 0:
        caps.append(("fair", T["why_no_opt"]))
    elif k < W:
        caps.append(("fair", say("why_few_opt", k=k)))
    elif con and con["state"] == "caution":
        caps.append(("caution", T["why_con"]))
    elif con and con["state"] == "fair":
        caps.append(("fair", T["why_going"]))
    if level == "fair":
        caps.append(("fair", T["why_fair_trust"]))
    if len(s.objectives) >= 2:
        caps.append(("fair", say("why_multi", out=ctx.label(o))))
    return caps


def _is_multi_cap(ctx: Ctx, o: str, cap: tuple[str, str]) -> bool:
    return cap[1] == say("why_multi", out=ctx.label(o))


def optimum(ctx: Ctx) -> dict | None:
    s = ctx.s
    if not s.objectives:
        return None
    if ctx.s.models and not ctx.use_models:
        return _pending("optimum")
    obj = s.objectives[0]
    o, sign = obj["name"], 1.0 if obj.get("goal", "min") == "min" else -1.0
    label, unit = ctx.label(o), ctx.unit_of(o)
    if not ctx.complete:
        return _section("optimum", "unknown", T["opt_none"], basis=ctx.basis)
    best = _best(ctx)
    table = _candidates(ctx, o, sign)
    if best is None or num(best.results.get(o)) is None:
        state, sentence = ctx.no_feasible("opt") if s.constraints else ("unknown", T["opt_none"])
        return _section("optimum", state, sentence, charts=table, basis=ctx.basis)
    val = float(best.results[o])
    v_text = with_unit(val, unit)
    if len(ctx.complete) < ctx.need:
        return _section("optimum", "unknown", say("opt_few", out=label, val=v_text, id=best.id, d=ctx.d, n=len(ctx.complete), m=ctx.need - len(ctx.complete)),
                        charts=table, facts={"case_id": best.id, "value": _r(val), "unit": unit, "output": o}, basis=ctx.basis)
    m = ctx.model_for(o)
    level = ctx.verdict(m, o) if m else "unknown"
    share = None
    infl = ctx.influence.get(o)
    if infl:
        share = (infl.get("facts") or {}).get("shares")
    verdicts: list[tuple[str, str, str]] = []  # (상태, 쓰임, 문장)
    notes: list[str] = []
    facts: dict = {"case_id": best.id, "params": {k: _r(best.params.get(k)) for k in s.names}, "value": _r(val), "unit": unit, "output": o}
    # 범위 끝
    edges = []
    for i, name in enumerate(s.names):
        span = float(s.hi[i] - s.lo[i])
        u = (float(best.params[name]) - float(s.lo[i])) / span if span > 0 else 0.5
        if (u <= T_["edge"] or u >= 1 - T_["edge"]) and (share is None or (share.get(name) or 0) >= T_["share_small"]):
            edges.append({"variable": name, "side": "상한" if u > 0.5 else "하한", "value": _r(best.params[name])})
    facts["edges"] = edges
    for e in edges:
        verdicts.append(("fair", "edge", say("opt_edge", var=ctx.label(e["variable"]), side=e["side"], x=ctx.val(e["variable"], e["value"]))))
    # 제약 여유
    margins = []
    tight_con = None
    for con in s.constraints:
        v = num(best.results.get(con["name"]))
        cl, cu = ctx.label(con["name"]), ctx.unit_of(con["name"])
        if v is None:
            notes.append(say("opt_con_unknown", con=cl))
            continue
        for bound, dist in (("upper", (con.get("upper") - v) if con.get("upper") is not None else None), ("lower", (v - con.get("lower")) if con.get("lower") is not None else None)):
            if dist is None:
                continue
            limit = abs(float(con[bound])) or 1.0
            p = dist / limit
            margins.append({"output": con["name"], "bound": bound, "margin": _r(dist), "ratio": _r(p)})
            if p < T_["margin_fair"]:
                verdicts.append(("caution", "margin", say("opt_margin_caution", con=cl, m=with_unit(dist, cu), p=pct(p * 100))))
                tight_con = tight_con or con["name"]
            elif p < T_["margin_good"]:
                verdicts.append(("fair", "margin", say("opt_margin_fair", con=cl, m=with_unit(dist, cu), p=pct(p * 100))))
                tight_con = tight_con or con["name"]
    facts["margins"] = margins
    facts["tight_constraint"] = tight_con
    predicted_row = None
    robust_said = False
    if m:
        sur = ctx.surrogate(m)
        # 돌리기 전 예측
        prior = next((r for r in prior_predictions(ctx, o) if r["case_id"] == best.id), None)
        if prior:
            notes.append(say("opt_prior", pred=with_unit(prior["predicted"], unit), val=v_text, diff=with_unit(abs(prior["predicted"] - val), unit)))
            facts["prior"] = prior
        # 예측 최선
        cand = s.lo + calc.sobol_unit(ctx.d, 4096, seed=4) * (s.hi - s.lo)
        mean, std = sur.predict(cand)
        cons = [c for c in s.constraints if c["name"] in sur.models]
        prob = bayesopt.constraint_probability(mean, std, cons, len(cand))
        ok = prob >= 0.5
        sc = ctx.score(m, o)
        p80 = sc["p80"] if sc else 0.0
        ptp_v = [v for _, v in ctx.values(o)]
        ptp = float(np.ptp(ptp_v)) if len(ptp_v) >= 2 else 0.0
        if ok.any() and level in ("good", "fair"):
            ids = np.where(ok)[0]
            j = ids[int(np.argmin(sign * mean[o][ids]))]
            pv = float(mean[o][j])
            if sign * (val - pv) > max(p80, 0.01 * ptp):
                params = {n: float(cand[j, i]) for i, n in enumerate(s.names)}
                predicted_row = {"params": params, "pred": pv, "std": float(std[o][j])}
                facts["predicted_best"] = {"params": {k: _r(v) for k, v in params.items()}, "value": _r(pv)}
                verdicts.append(("fair", "pred", say("opt_pred", x=_design_text(ctx, params, share), out=label, pred=with_unit(pv, unit), verb="낮출" if sign > 0 else "높일", cost=ctx.cost(1))))
        # 강건성(예측): 최적점 주변 ±2% 범위. 공차 가정이므로 화면에 적는다
        tol = T_["robust_tol"]
        span = s.hi - s.lo
        center = np.array(ctx.x(best))
        local = np.clip(center - tol * span + calc.sobol_unit(ctx.d, 512, seed=5) * (2 * tol * span), s.lo, s.hi)
        lmean, lstd = sur.predict(local)
        a, b = float(np.percentile(lmean[o], 5)), float(np.percentile(lmean[o], 95))
        width = b - a
        total_gain = _total_gain(ctx, o, sign)
        # 개선량과만 비교하면 처음 값이 이미 좋았을 때 작은 흔들림도 '크게'가 된다. 값 폭 기준을 바닥으로 둔다
        limit_good = max(T_["robust_good"] * total_gain, T_["robust_flat_good"] * ptp)
        limit_fair = max(T_["robust_fair"] * total_gain, T_["robust_flat_fair"] * ptp)
        facts["robust"] = {"low": _r(a), "high": _r(b), "width": _r(width), "tolerance": tol, "basis": "predicted"}
        tol_text = pct(tol * 100).rstrip("0").rstrip(".")
        if level in ("good", "fair"):
            if width > limit_fair:
                verdicts.append(("caution", "robust", say("opt_robust", tol=tol_text, out=label, a=n3(a), b=with_unit(b, unit))))
            elif width > limit_good:
                verdicts.append(("fair", "robust", say("opt_robust_fair", tol=tol_text, out=label, a=n3(a), b=with_unit(b, unit))))
            else:
                robust_said = True
            for con in cons:
                ok_rate = np.ones(len(local), dtype=bool)
                if con.get("upper") is not None:
                    ok_rate &= lmean[con["name"]] <= con["upper"]
                if con.get("lower") is not None:
                    ok_rate &= lmean[con["name"]] >= con["lower"]
                rate = float(ok_rate.mean())
                if rate < T_["robust_pass"]:
                    verdicts.append(("caution", "robust_con", say("opt_robust_con", tol=tol_text, con=ctx.label(con["name"]), p=pct(rate * 100))))
    if predicted_row:
        _add_predicted_rows(ctx, table, o, sign, unit, predicted_row)
    caps = _optimum_caps(ctx, o)
    state = "good"
    for st, *_ in [*verdicts, *caps]:
        if _RANK[st] > _RANK[state]:
            state = st
    facts["caps"] = [c[1] for c in caps]
    head = say("opt_head", id=best.id, out=label, val=v_text)
    # 어떤 상태든 어느 설계를 말하는지를 먼저 쓰고, 이어서 가장 무거운 이유 하나를 말한다. 나머지는 펼치면 본다
    cap_c = next((c for c in caps if c[0] == "caution"), None)
    ver_c = next((v for v in verdicts if v[0] == "caution"), None)
    pred = next((v for v in verdicts if v[1] == "pred"), None)
    used: object = None
    if cap_c:
        sentence, used = say("opt_wait", out=label, val=v_text, id=best.id, why=cap_c[1]), cap_c
    elif ver_c:
        sentence, used = f"{head} {ver_c[2]}", ver_c
    elif pred:
        sentence, used = f"{head} {pred[2]}", pred
    elif verdicts:
        sentence, used = f"{head} {verdicts[0][2]}", verdicts[0]
    elif caps:
        sentence, used = f"{head} {say('opt_check', why=caps[0][1])}", caps[0]
    else:
        sentence = f"{head} {T['opt_good'] if robust_said else T['opt_good_plain']}"
    notes = [v[2] for v in verdicts if v is not used] + [say("opt_check", why=c[1]) for c in caps if c is not used and not _is_multi_cap(ctx, o, c)] + notes
    if len(s.objectives) >= 2:
        notes.insert(0, say("opt_multi", out=label))
    next_ = None
    kinds = {t[1] for t in verdicts}
    if cap_c and level == "caution":
        next_ = _ask_al(ctx)
    elif any(v[1] == "margin" and v[0] == "caution" for v in verdicts) and tight_con:
        next_ = _margin_ask(ctx, tight_con)
    elif "pred" in kinds:
        facts["verify_request"] = True
        next_ = _ask(ctx, "확인 해석 요청", "스터디 '{name}'에서 예측 모델이 고른 최선점을 해석으로 확인해 줘.", "verify")
    elif "edge" in kinds:
        names = ", ".join(ctx.label(e["variable"]) for e in edges)
        next_ = _ask(ctx, "범위 넓히기 요청", f"스터디 '{{name}}'의 {names} 범위를 넓혀 다시 살펴볼지 검토해 줘.", "edge")
    elif tight_con:
        next_ = _margin_ask(ctx, tight_con)
    return _section("optimum", state, sentence, notes=notes, facts=facts, charts=table,
                    next_=next_, basis="mixed" if m else ctx.basis, model=ctx.model_info(m, o) if m else None)


def _margin_ask(ctx: Ctx, con: str) -> dict:
    return _ask(ctx, "여유 있는 설계 찾기 요청", f"스터디 '{{name}}'에서 {ctx.label(con)} 여유를 {pct(T_['margin_good'] * 100)}% 이상 남기는 설계를 찾아 줘.", "margin")


def _total_gain(ctx: Ctx, o: str, sign: float) -> float:
    rows = [(c, v) for c, v in ctx.values(o) if ctx.feasible(c) is True]
    base = [v for c, v in rows if c.source not in ("bo", "al")]
    if not base or not rows:
        return 0.0
    start = min(base, key=lambda v: sign * v)
    now = min((v for _, v in rows), key=lambda v: sign * v)
    return max(0.0, sign * (start - now))


def _candidates(ctx: Ctx, o: str, sign: float) -> list:
    """후보 표: 해석으로 확인한 상위 5건. 예측 후보는 _add_predicted_rows가 따로 표시해 넣는다."""
    s = ctx.s
    rows = sorted([(c, v) for c, v in ctx.values(o) if ctx.feasible(c) is True], key=lambda t: sign * t[1])[:5]
    if not rows:
        return []
    out = []
    for c, v in rows:
        margin = _margin_text(ctx, c)
        out.append({"kind": "verified", "case_id": c.id, "params": {n: _r(c.params[n]) for n in s.names}, "value": _r(v), "margin": margin})
    return [{"kind": "candidates", "variables": _var_rows(ctx), "output": ctx.label(o), "unit": ctx.unit_of(o), "rows": out}]


def _margin_text(ctx: Ctx, c: CaseRow) -> str | None:
    parts = []
    for con in ctx.s.constraints:
        v = num(c.results.get(con["name"]))
        if v is None:
            continue
        if con.get("upper") is not None:
            parts.append(f"{ctx.label(con['name'])} {with_unit(con['upper'] - v, ctx.unit_of(con['name']))}")
        if con.get("lower") is not None:
            parts.append(f"{ctx.label(con['name'])} {with_unit(v - con['lower'], ctx.unit_of(con['name']))}")
    return " · ".join(parts) or None


def _add_predicted_rows(ctx: Ctx, table: list, o: str, sign: float, unit: str, row: dict):
    if table:
        table[0]["rows"].append({"kind": "predicted", "case_id": None, "params": {k: _r(v) for k, v in row["params"].items()}, "value": _r(row["pred"]), "margin": None})


# ---------- 두 목표 비교 ----------
def pareto(ctx: Ctx) -> dict | None:
    s = ctx.s
    if ctx.s.models and not ctx.use_models and len(s.objectives) >= 2:
        return _pending("pareto")
    objs = s.objectives
    if len(objs) >= 2:
        a, b = objs[0], objs[1]
        sec = pareto_pair(ctx, a["name"], a.get("goal", "min"), b["name"], b.get("goal", "min"))
        if len(objs) >= 3:
            # 세 목표 이상: 뜻을 설명해야 하는 평행 좌표 대신 후보마다 모든 목표를 적은 표를 주고, 고른 두 목표는 그림으로 본다
            goals_word = {3: "세 목표"}.get(len(objs), "여러 목표")
            sec["title"] = "목표들을 함께 좋게 할 수 있나요?"
            sec["sentence"] = f"{say('par_many', goals=goals_word, a=ctx.label(a['name']), b=ctx.label(b['name']))} {sec['sentence']}"
            table = _goals_table(ctx, objs)
            sec["charts"] = [c for c in sec["charts"] if c["kind"] != "front_table"] + ([table] if table else [])
            sec["pair_first"] = {"name": objs[0]["name"], "label": ctx.label(objs[0]["name"]), "goal": objs[0].get("goal", "min")}
            sec["pair_choices"] = [{"name": o["name"], "label": ctx.label(o["name"]), "goal": o.get("goal", "min")} for o in objs[1:]]
            sec["pair_default"] = objs[1]["name"]
        return sec
    if not objs:
        return None
    others = [n for n in ctx.output_order() if n != objs[0]["name"] and len(ctx.values(n)) >= 2]
    if not others:
        return None
    sec = _section("pareto", "unknown", say("par_pick", a=ctx.label(objs[0]["name"])), basis=ctx.basis,
                   facts={"objectives": 1, "first": objs[0]["name"]}, label="선택 사항")
    sec["collapsed"] = True
    sec["pair_choices"] = [{"name": n, "label": ctx.label(n), "goal": "min"} for n in others]
    sec["pair_first"] = {"name": objs[0]["name"], "label": ctx.label(objs[0]["name"]), "goal": objs[0].get("goal", "min")}
    return sec


def _goal_cols(ctx: Ctx, names: list[str]) -> list[dict]:
    return [{"name": n, "label": ctx.label(n), "unit": ctx.unit_of(n)} for n in names]


def _thin_rows(rows: list, limit: int = 8) -> list:
    return rows if len(rows) <= limit else [rows[i] for i in np.linspace(0, len(rows) - 1, limit).astype(int)]


def _goals_table(ctx: Ctx, objs: list[dict]) -> dict | None:
    names, goals = [o["name"] for o in objs], [o.get("goal", "min") for o in objs]
    cases, Y, S = _objective_matrix(ctx, names, goals)
    if not len(S):
        return None
    mask = calc.non_dominated(S)
    picked = [(c, y) for c, y, k in zip(cases, Y, mask) if k]
    picked.sort(key=lambda t: t[1][0] * (1 if goals[0] == "min" else -1))
    rows = [{"case_id": c.id, "goals": {n: _r(y[i]) for i, n in enumerate(names)}, "params": {n: _r(c.params[n]) for n in ctx.s.names}} for c, y in _thin_rows(picked, 10)]
    return {"kind": "front_table", "goals": _goal_cols(ctx, names), "variables": _var_rows(ctx), "rows": rows, "total": len(picked), "knee": None}


def _pareto_how(a_goal: str, b_goal: str, has_pred: bool) -> str:
    corner = f"{'왼쪽' if a_goal == 'min' else '오른쪽'} {'아래' if b_goal == 'min' else '위'}"
    text = f"진한 선 위의 설계는 한쪽을 좋게 하면 다른 쪽이 나빠집니다. {corner}로 갈수록 둘 다 좋습니다."
    return text + (" 속 빈 점은 예측이라 아직 해석으로 확인하지 않았습니다." if has_pred else "")


def pareto_pair(ctx: Ctx, a: str, a_goal: str, b: str, b_goal: str) -> dict:
    """해석으로 확인한 후보와, 모델이 충분히 맞을 때만 예측 후보를 따로 표시해 돌려준다."""
    s = ctx.s
    la, lb = ctx.label(a), ctx.label(b)
    ua, ub = ctx.unit_of(a), ctx.unit_of(b)
    names, goals = [a, b], [a_goal, b_goal]
    how = _pareto_how(a_goal, b_goal, False)
    cases, Y, S = _objective_matrix(ctx, names, goals)
    any_values = [c for c in ctx.complete if num(c.results.get(a)) is not None and num(c.results.get(b)) is not None]
    facts = {"a": a, "b": b, "a_goal": a_goal, "b_goal": b_goal, "n": len(cases)}
    single = len(s.objectives) < 2
    notes = [say("par_single", a=ctx.label(s.objectives[0]["name"]))] if single and s.objectives else []
    chart = {"kind": "pareto", "a": {"name": a, "label": la, "unit": ua, "goal": a_goal}, "b": {"name": b, "label": lb, "unit": ub, "goal": b_goal},
             "all": [[c.id, float(c.results[a]), float(c.results[b]), ctx.feasible(c)] for c in any_values[:2000]],
             "front": [], "predicted": [], "knee": None}
    if s.constraints and any_values and not cases:
        state, sentence = ctx.no_feasible("par")
        return _section("pareto", state, sentence, notes=notes, facts=facts, charts=[chart], how=how, basis=ctx.basis)
    if len(cases) < T_["pareto_min"]:
        return _section("pareto", "unknown", say("par_few", n=len(cases), m=int(T_["pareto_min"]) - len(cases)), notes=notes, facts=facts, charts=[chart], how=how, basis=ctx.basis)
    mask = calc.non_dominated(S)
    order = np.where(mask)[0]
    order = order[np.argsort(S[order, 0])]
    front = S[order]
    k = len(order)
    chart["front"] = [[cases[i].id, float(Y[i, 0]), float(Y[i, 1])] for i in order]
    rho = calc.spearman(S[:, 0], S[:, 1])
    # 전체 점의 상관이 아니라 앞면이 양쪽으로 얼마나 벌어졌는지로 본다. 전체 상관은 앞면과 먼 점에 끌려 실제 상충을 놓친다(세탁기 ρ>0인데 상충)
    # 분모는 사분위 폭이다. 전체 폭을 쓰면 이상한 값 하나에 앞면 폭이 작아 보여 상충을 '안 부딪힌다'고 말하게 된다
    iqr = np.subtract(*np.percentile(S, [75, 25], axis=0))
    span_all = np.where(iqr > 0, iqr, np.where(np.ptp(S, axis=0) > 0, np.ptp(S, axis=0), 1.0))
    extent = np.ptp(front, axis=0) / span_all
    conflict = bool(np.all(extent >= T_["conflict_extent"]))
    knee_i, knee_d = calc.knee(front, T_["knee_min"])
    facts.update(front=k, rho=_r(rho), conflict=conflict, extent=[_r(extent[0]), _r(extent[1])], front_cases=[cases[i].id for i in order][:30], knee_distance=_r(knee_d))
    next_ = None
    if single:
        next_ = _ask(ctx, "두 목표 최적화 요청", f"스터디 '{{name}}'을 {la}와 {lb} 두 목표로 최적화해 줘.", "bo2")
    if knee_i is not None:
        kc = cases[order[knee_i]]
        chart["knee"] = {"case_id": kc.id, "a": float(kc.results[a]), "b": float(kc.results[b])}
        facts["knee_case"] = kc.id
        facts["knee_params"] = {n: _r(kc.params[n]) for n in s.names}
        if not next_:
            next_ = _ask(ctx, "이 후보 자세히 보기 요청", f"스터디 '{{name}}'에서 케이스 {kc.id} 설계를 자세히 보여 줘: 변수 값, 목표 값, 제약 여유.", "detail")
    # 예측 앞면: 두 출력 모두 쓸 만할 때만
    predicted = _predicted_front(ctx, a, a_goal, b, b_goal)
    extra: dict = {}
    if predicted is None:
        notes.append(T["par_pred_low"])
    elif predicted:
        chart["predicted"] = predicted
        notes.append(say("par_pred_note", k=len(predicted)))
        how = _pareto_how(a_goal, b_goal, True)
        extra["basis_note"] = "해석값(속 빈 점만 예측)"
    table = {"kind": "front_table", "goals": _goal_cols(ctx, names), "variables": _var_rows(ctx), "total": k, "knee": chart["knee"]["case_id"] if chart["knee"] else None,
             "rows": [{"case_id": cases[i].id, "goals": {n: _r(Y[i, j]) for j, n in enumerate(names)}, "params": {n: _r(cases[i].params[n]) for n in s.names}} for i in _front_pick(order, knee_i)]}
    charts = [chart, table]
    if k == 1:
        return _section("pareto", "good", say("par_one", id=cases[order[0]].id, a=la, b=lb), notes=notes, facts=facts, charts=[chart], how=how, basis=ctx.basis, next_=next_, **extra)
    if k == 2:
        return _section("pareto", "fair", say("par_two", k=k), notes=notes, facts=facts, charts=charts, how=how, basis=ctx.basis,
                        next_=next_ or _ask(ctx, "추가 최적화 요청", "스터디 '{name}'에서 두 목표 최적화를 더 돌려 줘.", "bo"), **extra)
    if not conflict:
        return _section("pareto", "good", say("par_nofight", k=k), notes=notes, facts=facts, charts=charts, how=how, basis=ctx.basis, next_=next_, **extra)
    # 맞바꾸는 비율: 균형 설계(없으면 앞면 가운데)에서 첫 값을 10% 더 좋게 할 때
    ref_i = knee_i if knee_i is not None else k // 2
    ref_case = cases[order[ref_i]]
    sign = np.array([1.0 if g == "min" else -1.0 for g in goals])

    def step(col: int) -> tuple[dict, bool]:
        # 기준값이 0에 가까우면 '10%'는 뜻이 없다(0.001의 10%). 그때는 앞면 폭의 10%를 단위 값으로 말한다
        at = float(front[ref_i, col])
        use_pct = _ref_percent(at, float(span_all[col]))
        delta = (abs(at) if use_pct else float(np.ptp(front[:, col]))) * T_["tradeoff_step"]
        return calc.tradeoff(front[:, col], front[:, 1 - col], at, delta), use_pct

    tr, use_pct = step(0)
    first_col = 0
    # 첫 값이 이미 가장 좋은 끝에 있거나 거의 붙어 있으면 '0.001 더 줄이면'은 쓸모가 없다. 반대쪽으로 말한다
    if tr["a_from"] - tr["a_to"] < 0.01 * float(np.ptp(front[:, 0])):
        first_col = 1
        la, lb, ua, ub, a_goal, b_goal = lb, la, ub, ua, b_goal, a_goal
        tr, use_pct = step(1)
    change = abs(tr["b_to"] - tr["b_from"])
    gained = abs(tr["a_from"] - tr["a_to"])
    p_text = f"{pct(gained / abs(tr['a_from']) * 100)}% " if use_pct else ""
    to_value = float(tr["a_to"]) * float(sign[first_col])  # 앞면은 '작을수록 좋게' 맞춘 값이다. 화면에는 실제 값으로 말한다
    verb_a = "줄이" if a_goal == "min" else "늘리"
    verb_b = "늘어납니다" if b_goal == "min" else "줄어듭니다"
    facts["tradeoff"] = {"a": la, "b": lb, "a_change": _r(gained), "a_percent": _r(gained / abs(tr["a_from"]) * 100) if use_pct else None,
                         "a_to": _r(to_value), "b_change": _r(change), "within_front": tr["within"], "from_case": ref_case.id}
    design = f"{ctx.label(names[0])} {with_unit(float(ref_case.results[names[0]]), ctx.unit_of(names[0]))}, {ctx.label(names[1])} {with_unit(float(ref_case.results[names[1]]), ctx.unit_of(names[1]))}"
    key = "par_trade" if knee_i is not None else "par_trade_noknee"
    sentence = say(key, id=ref_case.id, design=design, a=la, b=lb, to=with_unit(to_value, ua), p=p_text, verb_a=verb_a, d=with_unit(change, ub), verb_b=verb_b)
    if knee_i is not None:
        notes.append(T["par_knee_note"])
    return _section("pareto", "fair", sentence, notes=notes, facts=facts, charts=charts, how=how, basis=ctx.basis, next_=next_, **extra)


def _front_pick(order: np.ndarray, knee_i: int | None, limit: int = 8) -> list[int]:
    """표에 넣을 후보: 양 끝과 균형 설계를 빼놓지 않고 고르게 고른다."""
    idx = list(range(len(order)))
    if len(idx) > limit:
        idx = sorted({int(i) for i in np.linspace(0, len(order) - 1, limit)} | ({knee_i} if knee_i is not None else set()))
    return [int(order[i]) for i in idx]


def _predicted_front(ctx: Ctx, a: str, a_goal: str, b: str, b_goal: str, limit: int = 60):
    """두 출력 모두 '보통' 이상일 때만 예측 앞면을 만든다. 정확도가 낮으면 None, 그릴 모델이 없으면 빈 목록."""
    s = ctx.s
    ma, mb = ctx.model_for(a), ctx.model_for(b)
    if not ctx.use_models or ma is None or mb is None or ma.id != mb.id:
        return []
    if ctx.verdict(ma, a) not in ("good", "fair") or ctx.verdict(mb, b) not in ("good", "fair"):
        return None
    sur = ctx.surrogate(ma)
    cand = s.lo + calc.sobol_unit(ctx.d, 4096, seed=6) * (s.hi - s.lo)
    mean, std = sur.predict(cand)
    cons = [c for c in s.constraints if c["name"] in sur.models]
    ok = bayesopt.constraint_probability(mean, std, cons, len(cand)) >= 0.5
    if not ok.any():
        return []
    sa, sb = (1.0 if a_goal == "min" else -1.0), (1.0 if b_goal == "min" else -1.0)
    idx = np.where(ok)[0]
    P = np.column_stack([sa * mean[a][idx], sb * mean[b][idx]])
    keep = idx[calc.non_dominated(P)]
    keep = keep[np.argsort(sa * mean[a][keep])]
    if len(keep) > limit:
        keep = keep[np.linspace(0, len(keep) - 1, limit).astype(int)]
    return [[float(mean[a][i]), float(mean[b][i])] for i in keep]


# ---------- 능동학습 ----------
def active(ctx: Ctx) -> dict | None:
    """돌리지 않았으면 줄 자체를 내지 않는다. 필요하지 않은 것을 '아직 판단 못 함'으로 보이면 문제가 있는 것처럼 읽힌다."""
    if ctx.s.models and not ctx.use_models:
        return _pending("active")
    al = [c for c in ctx.complete if c.source == "al"]
    if not al:
        return None
    outs = [o for o in ctx.output_order() if ctx.model_for(o) is not None]
    variants = [v for o in outs[:6] if (v := _active_one(ctx, o, al))]
    if not variants:
        return _section("active", "unknown", say("al_few", k=len(al), need=int(T_["al_min_added"])), basis="predicted", facts={"al": len(al)})
    first = variants[0]
    state, sentence = first["state"], first["sentence"]
    key = ctx.decision_outputs()
    weak = [v for v in variants[1:] if v["name"] in key and v["state"] in ("fair", "caution") and _RANK[v["state"]] > _RANK[first["state"]]]
    if weak and first["state"] != "unknown":  # 닫힌 줄이 첫 출력만 말하면 다른 목표의 약한 효과가 가려진다
        state = max(weak, key=lambda v: _RANK[v["state"]])["state"]
        sentence = f"{sentence} {say('al_others', names='·'.join(v['label'] for v in weak))}"
    return _section("active", state, sentence, notes=list(first["notes"]), facts=first["facts"], charts=first["charts"], model=first.get("model"),
                    variants=variants, next_=first.get("next"), basis="predicted", label=first["state_label"] if state == first["state"] else None)


def _active_one(ctx: Ctx, o: str, al: list[CaseRow]) -> dict | None:
    label, unit = ctx.label(o), ctx.unit_of(o)
    m = ctx.model_for(o)
    sur = ctx.surrogate(m)
    pipe = sur.models[o]
    base_rows = [(c, v) for c, v in ctx.values(o) if c.source not in ("bo", "al")]
    # 최적화 점은 곡선에서 뺀다. 능동학습으로 고른 점의 효과만 재기 위해서다
    add_rows = [(c, v) for c, v in ctx.values(o) if c.source == "al"]
    k = len(add_rows)
    base = {"name": o, "label": label, "unit": unit, "model": ctx.model_info(m, o)}
    out = lambda st, sentence, **kw: {**base, "state": st, "state_label": state_label("active", st), "sentence": sentence, "notes": kw.get("notes", []),  # noqa: E731
                                      "facts": kw.get("facts", {}), "charts": kw.get("charts", []), "next": kw.get("next")}
    if k < T_["al_min_added"]:
        return out("unknown", say("al_few", k=k, need=int(T_["al_min_added"])), facts={"al": k})
    if len(base_rows) < T_["al_min_base"]:
        return out("unknown", say("al_base_few", k=k, b=len(base_rows), need=int(T_["al_min_base"])), facts={"al": k, "base": len(base_rows)})
    Xb = pipe[0].transform(np.array([ctx.x(c) for c, _ in base_rows]))
    yb = np.array([v for _, v in base_rows])
    Xa = pipe[0].transform(np.array([ctx.x(c) for c, _ in add_rows]))
    ya = np.array([v for _, v in add_rows])
    window = int(T_["al_stop_window"])
    curve = calc.learning_curve(Xb, yb, Xa, ya, pipe[-1].kernel_, tail=window)
    first, last = curve[0], curve[-1]
    drop = (first["rmse"] - last["rmse"]) / first["rmse"] if first["rmse"] > 0 else 0.0
    a_t, b_t = with_unit(first["rmse"], unit), with_unit(last["rmse"], unit)
    level = ctx.verdict(m, o)
    worse = drop <= T_["al_worse"]
    # 가린 점은 처음 점이고 능동학습 점은 범위 끝으로 간다. 그래서 오차가 그대로여도 끝쪽 개선은 안 보인 것일 수 있어(실측 47% 줄었는데 -2%로 잰 경우) '효과 없음'은 오차가 늘었을 때만 말한다
    if drop >= T_["al_good"]:
        state, sentence = "good", say("al_good", k=k, p=pct(drop * 100), a=a_t, b=b_t)
    elif drop >= T_["al_fair"]:
        state, sentence = "fair", say("al_fair", k=k, p=pct(drop * 100), a=a_t, b=b_t)
    elif worse:
        state, sentence = "caution", say("al_worse", k=k, p=pct(-drop * 100), a=a_t, b=b_t)
    elif level == "good":
        state, sentence = "fair", say("al_flat_ok", k=k, a=a_t, b=b_t)
    else:
        state, sentence = "fair", say("al_flat", k=k, a=a_t, b=b_t)
    # 멈춤 조언은 최근 몇 건을 봐야 한다. 한 건의 흔들림으로 '여기서 멈춰도 됩니다'라고 하지 않는다
    notes: list[str] = []
    next_ = None
    stop = None
    stop_poor = False
    if not worse:
        tail = next((c for c in curve if c["m"] == k - window), None) if k >= 2 * window else None
        if tail is None:
            notes.append(say("al_wait", m=k))
        else:
            tail_drop = (tail["rmse"] - last["rmse"]) / tail["rmse"] if tail["rmse"] > 0 else 0.0
            stop = tail_drop < T_["al_stop"]
            if stop:
                stop_poor = level == "caution"
                notes.append(say("al_stop_poor" if stop_poor else "al_stop_good", m=window))
            else:
                more = max(3, k // 2)
                notes.append(say("al_going", b=more, cost=ctx.cost(more)))
                next_ = _ask(ctx, f"정확도 높이는 해석 {more}건 요청", f"스터디 '{{name}}'에 능동학습 {more}건을 더 돌려 줘.", "al")
    # 불확실도 전후는 AI가 물으면 답하도록 facts에만 둔다. 화면에 또 하나의 % 숫자를 더하면 오차 숫자와 헷갈린다
    Q = pipe[0].transform(ctx.s.lo + calc.sobol_unit(ctx.d, 2048, seed=7) * (ctx.s.hi - ctx.s.lo))
    unc = calc.uncertainty_change(Xb, yb, np.vstack([Xb, Xa]), np.concatenate([yb, ya]), pipe[-1].kernel_, Q)
    U_add = ctx.unit([ctx.x(c) for c, _ in add_rows])
    U_base = ctx.unit([ctx.x(c) for c, _ in base_rows])
    edge = np.any((U_add <= T_["edge"]) | (U_add >= 1 - T_["edge"]), axis=1)
    edge_n = int(edge.sum())
    edge_p = edge_n / len(edge) if len(edge) else 0.0
    if not worse:  # 오차가 늘었는데 '끝쪽 개선은 안 보인다'고 덧붙이면 말이 어긋난다
        if edge_n == k:
            notes.append(say("al_edge_all", k=k))
        elif edge_n and edge_p >= 0.5:
            notes.append(say("al_edge_some", k=k, e=edge_n))
    near = []
    for i in range(len(U_add)):
        others = np.vstack([U_base, U_add[:i]]) if i else U_base
        near.append(float(np.linalg.norm(others - U_add[i], axis=1).min()))
    same = sum(1 for v in near if v < 0.01)
    if same:
        notes.append(say("al_same", k=same))
    if state == "caution":
        notes.append(T["al_no_compare"])
    facts = {"al": k, "base": len(base_rows), "rmse_start": _r(first["rmse"]), "rmse_end": _r(last["rmse"]), "drop_pct": _r(drop * 100),
             "uncertainty": {kk: _r(vv) for kk, vv in unc.items()}, "edge_ratio": _r(edge_p), "mean_distance": _r(float(np.mean(near))),
             "stop": stop, "stop_poor": stop_poor, "unit": unit, "method": "처음 점 5겹 가리기, 최종 모델 kernel 고정, 능동학습 점만 실행 순서대로 추가, 가린 점과 거의 같은 추가 점은 그 겹에서 뺌", "compared_with_random": False}
    charts = [{"kind": "learning", "curve": [{"m": c["m"], "rmse": _r(c["rmse"]), "r2": _r(c["r2"])} for c in curve], "unit": unit, "output": label, "base_count": len(base_rows)},
              {"kind": "al_points", "variables": _var_rows(ctx), "base": [ctx.x(c) for c, _ in base_rows[:2000]], "added": [ctx.x(c) for c, _ in add_rows], "ids": [c.id for c, _ in add_rows]}]
    result = out(state, sentence, notes=notes, facts=facts, charts=charts, next=next_)
    if worse:
        result["state_label"] = "오차 늘어남"
    return result


# ---------- 연습 함수 비교 ----------
# 사용자 스터디에서는 무작위 비교를 잴 수 없다(해석을 더 돌려야 한다). 그래서 알려진 함수로 실제 코드 경로를 돌린 값만,
# '연습 함수 기준'으로 내 결과와 다른 칸에 보여 준다. 이 값이 없으면 줄 자체를 내지 않는다.
_bench_lock = threading.Lock()
_bench_started = False
BENCH_ENABLED = True  # 시험에서 끈다(오래 걸리는 계산을 뒤에서 돌리지 않게)


def _bench_path():
    return settings.data_dir / "insights" / "al_benchmark.json"


def _code_hash() -> str:
    import hashlib
    from pathlib import Path
    return hashlib.sha256(Path(bayesopt.__file__).read_bytes()).hexdigest()[:16]


def _bench_value() -> dict | None:
    try:
        v = json.loads(_bench_path().read_text(encoding="utf-8"))
        return v if v.get("code") == _code_hash() else None
    except (OSError, ValueError):
        return None


def bench_line(v: dict | None) -> str | None:
    if not v or not v.get("functions"):
        return None
    head = f"연습 함수 기준(수학 시험 문제 {v['total']}개로 미리 잰 값이고 내 결과가 아닙니다): "
    # 낮은 함수가 없었을 때 '항상 낫지는 않다'고 쓰면 잰 값과 어긋난다. 내 스터디로 옮길 수 없다는 사실을 말한다
    if v["worse"]:
        return f"{head}{v['not_worse']}개에서는 아무 데나 고른 점 이상, {v['worse']}개에서는 더 낮았습니다. 능동학습이 항상 낫지는 않습니다."
    return f"{head}아무 데나 고른 점보다 나쁘지 않았습니다. 내 해석 결과에서도 같다는 뜻은 아닙니다."


def run_benchmark(seeds: int = 5, n_start: int = 12, n_add: int = 12, functions: dict | None = None) -> dict:
    """실제 bayesopt.suggest(al)와 무작위 추가를 같은 시작 점에서 비교한다. 정답을 아는 함수라 실제 오차를 잴 수 있다."""
    from sklearn.metrics import r2_score

    def branin(X):
        x1, x2 = X[:, 0] * 15 - 5, X[:, 1] * 15
        return (x2 - 5.1 / (4 * np.pi ** 2) * x1 ** 2 + 5 / np.pi * x1 - 6) ** 2 + 10 * (1 - 1 / (8 * np.pi)) * np.cos(x1) + 10

    def hartmann3(X):
        al = np.array([1.0, 1.2, 3.0, 3.2])
        A = np.array([[3, 10, 30], [0.1, 10, 35], [3, 10, 30], [0.1, 10, 35]])
        P = 1e-4 * np.array([[3689, 1170, 2673], [4699, 4387, 7470], [1091, 8732, 5547], [381, 5743, 8828]])
        return -np.sum(al * np.exp(-np.sum(A * (X[:, None, :] - P) ** 2, axis=2)), axis=1)

    def washer(X):
        k, c = X[:, 0] * 40 - 10, X[:, 1] * 60 - 10
        return 2.6 + 3.67 * (2 * ((k - 12) / 37.5) ** 2 + 1.6 * ((c - 25) / 65.8) ** 2)

    funcs = functions or {"Branin": (branin, 2), "Hartmann3": (hartmann3, 3), "세탁기식": (washer, 2)}
    out = {}
    for name, (f, d) in funcs.items():
        al_scores, rnd_scores = [], []
        lo, hi = np.zeros(d), np.ones(d)
        test = calc.sobol_unit(d, 2048, seed=99)
        for seed in range(seeds):
            start = calc.qmc.LatinHypercube(d=d, seed=seed).random(n_start)
            rng = np.random.default_rng(seed + 100)
            X_rnd = np.vstack([start, rng.random((n_add, d))])
            sur = Surrogate(variables=[f"x{i}" for i in range(d)], outputs=["y"])
            sur.fit(start, {"y": f(start)})
            X_al = start
            for i in range(n_add):
                pick = bayesopt.suggest(sur, lo, hi, "al", 1, seed=seed * 100 + i, n_candidates=1024)
                X_al = np.vstack([X_al, pick])
                sur = Surrogate(variables=[f"x{i}" for i in range(d)], outputs=["y"])
                sur.models["y"] = _make_gp(d).fit(X_al, f(X_al))
            al_scores.append(float(r2_score(f(test), sur.models["y"].predict(test))))
            rnd_scores.append(float(r2_score(f(test), _make_gp(d).fit(X_rnd, f(X_rnd)).predict(test))))
        out[name] = {"al": float(np.mean(al_scores)), "random": float(np.mean(rnd_scores))}
    worse = sum(1 for v in out.values() if v["al"] < v["random"])
    return {"code": _code_hash(), "functions": out, "total": len(out), "worse": worse, "not_worse": len(out) - worse, "seeds": seeds,
            "n_start": n_start, "n_add": n_add, "computed_at": datetime.now().isoformat(timespec="seconds")}


def _start_benchmark():
    global _bench_started
    if not BENCH_ENABLED:
        return
    with _bench_lock:
        if _bench_started or _bench_value():
            return
        _bench_started = True

    def work():
        try:
            v = run_benchmark()
            p = _bench_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(_clean(v), ensure_ascii=False), encoding="utf-8")
        except Exception:
            log.exception("연습 함수 비교 실패")
    threading.Thread(target=work, daemon=True, name="insights-bench").start()


# ---------- 요약 카드 ----------
def overview(ctx: Ctx, sections: list[dict]) -> dict:
    s = ctx.s
    by = {x["key"]: x for x in sections}
    bo = sum(1 for c in ctx.complete if c.source == "bo")
    al = sum(1 for c in ctx.complete if c.source == "al")
    latest = s.models[0] if s.models else None
    times = run_times(s.cases)
    facts = {"complete": len(ctx.complete), "failed": len(ctx.failed), "waiting": len(ctx.waiting), "bo": bo, "al": al, "base": len(ctx.complete) - bo - al,
             "model_version": latest.version if latest else None,
             "run_hours": _r(times["total_sec"] / 3600) if times["durations"] else None,
             "run_text": hours_text(times["total_sec"] / 3600) if times["durations"] else None}
    # 위쪽 초록 줄만 보고 끝났다고 여기지 않게, 확정 전에 볼 것을 맨 위에 모아 준다(주의 먼저)
    flagged = [x for x in sections if x["state"] in ("caution", "fair") and not x.get("pending")]
    flagged.sort(key=lambda x: -_RANK[x["state"]])
    watch = [{"key": x["key"], "text": WATCH[x["key"]]} for x in flagged[:3]]
    return {"facts": facts, "next": _next(ctx, by), "watch": watch}


def _next(ctx: Ctx, by: dict) -> dict | None:
    s = ctx.s

    def go(key: str, request: str, source: str, **kw) -> dict:
        text, label = NEXT[key]
        return {"key": key, "text": text.format(**kw) if kw else text, "label": label, "request": request.replace("{name}", s.name), "from": source, "action": NEXT_ACTION.get(key, key)}

    def plain(key: str) -> dict:
        n = go(key, "", "overview")
        n["label"], n["request"] = "", ""
        return n

    cov, q, tr, con, opt, ac = by.get("coverage"), by.get("quality"), by.get("trust"), by.get("convergence"), by.get("optimum"), by.get("active")
    pending = [c for c in s.cases if c.status in ("pending", "queued")]
    if not s.cases:
        return go("make_doe", "스터디 '{name}'의 해석 조건 목록을 만들어 줘.", "coverage")
    if pending and cov and cov["state"] == "caution":
        return go("doe_fix", (cov.get("next") or {}).get("request") or "스터디 '{name}'의 해석 조건을 고쳐 줘.", "coverage")
    if q and ((q["facts"].get("fail_rate") or 0) > T_["fail_caution"] or q["facts"].get("cluster")):
        return go("fail", "스터디 '{name}'의 실패한 케이스 원인을 찾아 고쳐 줘.", "quality")
    if s.link_lost:
        return plain("stuck")
    if s.running or any(c.status == "running" for c in s.cases):
        return plain("wait")
    if pending and not ctx.complete:
        return go("run", "스터디 '{name}'의 대기 케이스를 실행해 줘.", "overview")
    # 모델이 필요한 항목을 아직 안 쟀으면 할 일을 말하지 않는다. 약한 모델에 '보고서를 쓰면 됩니다'가 잠깐이라도 나오면 안 된다
    if any(x and x.get("pending") for x in (tr, con, opt)):
        return plain("computing")
    n_done = len(ctx.complete)
    n_spread = n_done - sum(1 for c in ctx.complete if c.source == "bo")
    if n_done and n_spread < ctx.need:
        k = ctx.need - n_spread
        return go("count", f"스터디 '{{name}}'에 해석 조건을 {k}건 더 넣어 줘.", "coverage", n=n_spread, d=ctx.d, need=ctx.need, cost=ctx.cost(k))
    if pending:
        return go("run", "스터디 '{name}'의 대기 케이스를 실행해 줘.", "overview")
    if not s.models and n_done:
        return go("train", "스터디 '{name}'의 완료된 케이스로 예측 모델을 학습해 줘.", "trust")
    if s.models and tr and not tr.get("pending"):
        for v in tr.get("variants") or []:
            k = v.get("model", {}).get("stale_by", 0)
            m = ctx.model_for(v["name"])
            if m and ctx.is_stale(m, v["name"]):
                return go("stale", "스터디 '{name}'의 최신 해석까지 넣어 예측 모델을 다시 학습해 줘.", "trust", k=k)
        if tr["state"] == "caution":
            if ac and ac["facts"].get("stop_poor"):  # 능동학습이 더 넣어도 어렵다고 말한 뒤에 또 점을 넣으라고 하면 어긋난다
                return go("check_inputs", "스터디 '{name}'에서 점을 더 넣어도 예측이 나아지지 않는 원인을 설계 범위와 빠진 변수 쪽으로 점검해 줘.", "active")
            return go("poor", "스터디 '{name}'에 능동학습으로 해석 점을 더 넣어 예측 정확도를 올려 줘.", "trust", cost=ctx.cost(max(3, ctx.d)))
    if s.objectives and s.models and not any(c.source == "bo" for c in s.cases):
        return go("optimize", "스터디 '{name}'에서 최적화를 돌려 줘.", "convergence")
    W = int((con or {}).get("facts", {}).get("window", max(int(T_["conv_window_min"]), ctx.d)))
    if con and (con["facts"].get("improving") or con["facts"].get("more_worth")):
        return go("more_opt", (con.get("next") or {}).get("request") or f"스터디 '{{name}}'에서 최적화를 {W}건 더 돌려 줘.", "convergence", W=W, cost=ctx.cost(W))
    if con and con["state"] == "unknown" and con["facts"].get("bo"):
        left = max(1, W - int(con["facts"]["bo"]))
        return go("more_opt", f"스터디 '{{name}}'에서 최적화를 {left}건 더 돌려 줘.", "convergence", W=left, cost=ctx.cost(left))
    if opt and opt["facts"].get("predicted_best"):
        return go("verify", "스터디 '{name}'에서 예측 모델이 고른 최선점을 해석으로 확인해 줘.", "optimum", cost=ctx.cost(1))
    if opt and opt["facts"].get("tight_constraint"):
        return go("margin", (opt.get("next") or {}).get("request") or _margin_ask(ctx, opt["facts"]["tight_constraint"])["request"], "optimum", con=ctx.label(opt["facts"]["tight_constraint"]))
    if opt and opt["facts"].get("edges"):
        e = opt["facts"]["edges"][0]
        return go("edge", (opt.get("next") or {}).get("request") or "", "optimum", var=ctx.label(e["variable"]))
    if not ctx.complete:
        return None
    if any(m.deployed for m in s.models):  # 표로 만들면 Simulator가 이미 배포돼 있다. 한 일을 또 하라고 하지 않는다
        return go("report", "스터디 '{name}'의 보고서를 써 줘.", "overview")
    return go("finish", "스터디 '{name}'의 Simulator를 배포하고 보고서를 써 줘.", "overview")


def _data_words(text: str) -> str:
    """표로 불러온 값은 해석으로 확인한 값이 아니다. 앞으로 돌릴 해석('확인 해석', '해석 조건')과 FE 모델을 뜻하는 말은 그대로 둔다."""
    keep = ("확인 해석", "해석 조건", "해석 모델(FE)", "해석 1건")
    for i, word in enumerate(keep):
        text = text.replace(word, f"\0{i}\0")
    for old, new in (("아직 해석 안 함", "데이터에 없음"), ("해석 확인", "데이터 값"), ("해석값", "데이터값"), ("해석으로", "데이터로"), ("해석이", "데이터가"), ("해석을", "데이터를"), ("해석은", "데이터는"), ("해석", "데이터")):
        text = text.replace(old, new)
    for i, word in enumerate(keep):
        text = text.replace(f"\0{i}\0", word)
    return text


def _say_data_instead_of_solver(sections: list[dict], ov: dict):
    for x in sections:
        for holder in [x, *(x.get("variants") or [])]:
            holder["sentence"] = _data_words(holder["sentence"])
            holder["notes"] = [_data_words(t) for t in holder.get("notes") or []]
        x["how_to_read"] = _data_words(x["how_to_read"])
        if x.get("basis_note"):
            x["basis_note"] = _data_words(x["basis_note"])
    if ov.get("next") and ov["next"].get("text"):
        ov["next"]["text"] = _data_words(ov["next"]["text"])


# ---------- 전체 계산 ----------
def compute(snap: Snap, use_models: bool = True) -> dict:
    ctx = Ctx(snap, use_models)
    order = [coverage, quality, influence, trust, convergence, optimum, pareto, active]
    sections: list[dict] = []
    for fn in order:
        try:
            sec = fn(ctx)
        except Exception:
            # 항목 하나가 틀어져도 나머지는 보여 준다. 숫자가 이상한 결과에서도 화면이 멈추면 안 된다
            log.exception("판단 도우미 항목 실패: %s", fn.__name__)
            ctx.had_error = True
            sec = _section(fn.__name__, "unknown", T["error"])
        if sec is None:
            continue
        if sec["key"] == "influence":
            for v in sec.get("variants") or []:
                ctx.influence[v["name"]] = v
        ctx.by[sec["key"]] = sec
        sections.append(sec)
    if not ctx.complete and not ctx.failed:  # 해석 전에는 설계 범위 판단만 뜻이 있다
        sections = [x for x in sections if x["key"] == "coverage"]
    elif not ctx.complete:  # 모두 실패: 판단할 결과가 없다는 한 줄이면 된다. 같은 '판단 못 함'을 여러 줄 늘어놓으면 할 말이 묻힌다
        sections = [x for x in sections if x["key"] == "quality"]
    if use_models and any(x["key"] == "active" and x["facts"].get("al") for x in sections):
        _start_benchmark()
    ov = overview(ctx, sections)
    ov["word"] = ctx.word
    # 같은 일을 하는 버튼을 두 곳에 두지 않는다. 약한 모델처럼 여러 구역이 같은 일을 말해도 맨 위나 먼저 나온 구역 하나만 남긴다
    seen = {ov["next"]["action"]} if ov["next"] and ov["next"].get("label") else set()
    for x in sections:
        for holder in [x, *(x.get("variants") or [])]:
            nx = holder.get("next")
            if nx and nx.get("action") in seen and not (holder is not x and x.get("next") and x["next"].get("action") == nx.get("action") and not x.get("next_shown_above")):
                holder["next_shown_above"] = True
        if x.get("next") and not x.get("next_shown_above"):
            seen.add(x["next"].get("action"))
    if snap.imported:
        _say_data_instead_of_solver(sections, ov)
    return _clean({"schema": SCHEMA, "study_id": snap.study_id, "study_name": snap.name, "owner": snap.owner, "overview": ov, "sections": sections, "had_error": ctx.had_error,
                   "computed_at": datetime.now().isoformat(timespec="seconds"), "complete_layer": bool(use_models or not snap.models)})


# ---------- 캐시와 백그라운드 ----------
# 작업 스레드는 하나이고, 스터디마다 최신 상태 하나만 기다린다. 케이스가 하나씩 끝날 때마다 낡은 계산이 줄을 서면
# 다른 스터디까지 몇 분씩 밀린다(800케이스 추출 측정). 데몬 스레드라 서버를 끌 때 큐가 빌 때까지 기다리지 않는다
_queue: "OrderedDict[int, tuple[Snap, str]]" = OrderedDict()
_cond = threading.Condition()
_thread: threading.Thread | None = None
_memory: dict[int, dict] = {}
_inflight: dict[int, str] = {}
_failed: dict[int, str] = {}
_lock = threading.Lock()
_compute_calls = 0  # 시험에서 다시 계산했는지 센다
FAST_EVERY = 5.0
MEMORY_LIMIT = 64
RETRY_SEC = 60.0  # 일부 항목을 계산하지 못한 결과는 저장하지 않고 이만큼 뒤에 다시 잰다


def _cache_path(study_id: int):
    return settings.data_dir / "insights" / f"study_{study_id}.json"


def state_key(db: Session, study: models.Study) -> str:
    """케이스를 다 읽지 않고 집계로만 만든다. 케이스·모델·스터디 설정이 바뀌면 달라진다."""
    rows = db.query(models.Case.status, func.count(models.Case.id), func.max(models.Case.updated_at)).filter(models.Case.study_id == study.id).group_by(models.Case.status).all()
    latest = db.query(func.max(models.SurrogateModel.id)).filter(models.SurrogateModel.study_id == study.id).scalar()
    counts = "".join(f"{s}{n}" for s, n, _ in sorted(rows, key=lambda r: r[0] or ""))
    stamp = max((t for _, _, t in rows if t), default=None)
    return f"s{SCHEMA}v{study.version}m{latest or 0}{counts}@{stamp.isoformat() if stamp else ''}"


def _read(study_id: int) -> dict | None:
    if study_id in _memory:
        return _memory[study_id]
    try:
        v = json.loads(_cache_path(study_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if v.get("schema") != SCHEMA:
        return None
    _memory[study_id] = v
    return v


def _store(study_id: int, value: dict, to_disk: bool = True):
    _memory.pop(study_id, None)
    _memory[study_id] = value
    while len(_memory) > MEMORY_LIMIT:  # 그림 자료가 커서 오래된 것은 파일에만 둔다
        _memory.pop(next(iter(_memory)))
    if not to_disk:
        return
    p = _cache_path(study_id)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".part")
        tmp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        log.warning("판단 도우미 결과를 저장하지 못했습니다", exc_info=True)


def forget(study_ids: list[int]):
    """프로젝트를 지우면 저장된 판단 결과도 함께 지운다. 이미 도는 계산은 멈출 수 없으므로 저장할 때 다시 확인한다(_still_ours)."""
    with _cond:
        for sid in study_ids:
            _queue.pop(sid, None)
            _inflight.pop(sid, None)
    for sid in study_ids:
        _memory.pop(sid, None)
        _failed.pop(sid, None)
        try:
            _cache_path(sid).unlink(missing_ok=True)
        except OSError:
            pass


def _still_ours(snap: Snap) -> bool:
    """계산하는 동안 스터디가 지워졌으면 저장하지 않는다. SQLite는 id를 다시 쓰므로 지워진 프로젝트의 결과가 다른 사람의 새 스터디로 보일 수 있다."""
    with SessionLocal() as db:
        study = db.get(models.Study, snap.study_id)
        return study is not None and _owner(study) == snap.owner


def _work(snap: Snap, key: str):
    global _compute_calls
    try:
        _compute_calls += 1
        value = compute(snap, True)
        value["state_key"] = key
        if not _still_ours(snap):
            return
        if value.get("had_error"):
            value["retry_at"] = time.time() + RETRY_SEC
        _store(snap.study_id, value, to_disk=not value.get("had_error"))
        manager.publish({"type": "study_updated", "project_id": snap.project_id, "data": {"id": snap.study_id, "insights": True}})
    except Exception:
        log.exception("판단 도우미 계산 실패: 스터디 %s", snap.study_id)
        _failed[snap.study_id] = key
    finally:
        with _cond:
            if _inflight.get(snap.study_id) == key:
                _inflight.pop(snap.study_id, None)


def _loop():
    while True:
        with _cond:
            while not _queue:
                _cond.wait()
            _, (snap, key) = _queue.popitem(last=False)
        _work(snap, key)


def _submit(snap: Snap, key: str):
    global _thread
    with _cond:
        if _inflight.get(snap.study_id) == key or _failed.get(snap.study_id) == key:
            return
        _inflight[snap.study_id] = key
        # 이미 기다리는 낡은 계산은 자리를 그대로 두고 최신 상태로 바꾼다
        _queue[snap.study_id] = (snap, key)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, daemon=True, name="insights")
            _thread.start()
        _cond.notify()


def _merge_fast(old: dict, fast: dict) -> dict:
    """실행 중에는 빠른 항목만 새로 잰다. 모델이 필요한 항목은 직전 값을 그대로 둔다.
    빠른 층은 튀는 값을 재지 않으므로, 앞서 찾아낸 경고를 '튀는 값은 없습니다'로 덮지 않는다."""
    keep = {x["key"]: x for x in old.get("sections") or []}
    for x in fast["sections"]:
        if x.get("pending"):
            continue
        prev = keep.get(x["key"])
        if x["key"] == "quality" and x["facts"].get("outliers_unchecked") and prev and prev["facts"].get("outliers") and _RANK[prev["state"]] >= _RANK[x["state"]]:
            continue
        keep[x["key"]] = x
    order = list(META)
    merged = {**old, "overview": fast["overview"], "sections": [keep[k] for k in order if k in keep], "fast_at": time.time()}
    return merged


def _busy(value: dict) -> dict:
    """낡은 결과의 '다음 할 일'을 그대로 내주지 않는다. 지금 상태의 할 일은 계산이 끝나야 안다."""
    text, _ = NEXT["computing"]
    ov = {**(value.get("overview") or {}), "next": {"key": "computing", "text": text, "label": "", "request": "", "from": "overview", "action": "computing"}}
    return {**value, "overview": ov}


def get_insights(db: Session, study: models.Study) -> dict:
    """현재 상태의 결과. 같은 상태면 저장된 값을, 아니면 빠른 층만 먼저 돌려주고 나머지는 뒤에서 잰다(status: computing)."""
    value = _get_insights(db, study)
    line = bench_line(_bench_value())
    if line and any(x["key"] == "active" and x["facts"].get("al") for x in value.get("sections") or []):
        # 연습 함수 값은 스터디 상태와 따로 늦게 끝난다. 저장된 결과에 넣지 않고 응답할 때 붙인다
        value = {**value, "sections": [{**x, "bench": line} if x["key"] == "active" else x for x in value["sections"]]}
    return {k: v for k, v in value.items() if k != "owner"}


def _get_insights(db: Session, study: models.Study) -> dict:
    global _compute_calls
    key = state_key(db, study)
    cached = _read(study.id)
    if cached and cached.get("owner") != _owner(study):  # 지워진 스터디의 id를 다시 받은 경우. 앞 스터디의 결과를 보이면 안 된다
        _memory.pop(study.id, None)
        cached = None
    if cached and cached.get("state_key") == key and cached.get("complete_layer"):
        if cached.get("had_error") and time.time() >= cached.get("retry_at", 0):
            cached["retry_at"] = time.time() + RETRY_SEC
            _submit(snapshot(db, study), key)
        return {**cached, "status": "ready", "stale": False}
    snap = snapshot(db, study)
    if not snap.models:  # 모델이 없으면 곧바로 끝나는 계산뿐이다
        if snap.running and cached and time.time() - cached.get("fast_at", 0) < FAST_EVERY:
            return {**cached, "status": "computing", "stale": True, "running": True}
        _compute_calls += 1
        value = compute(snap, True)
        value["state_key"] = key
        value["fast_at"] = time.time()
        if value.get("had_error"):
            value["retry_at"] = time.time() + RETRY_SEC
        _store(study.id, value, to_disk=not snap.running and not value.get("had_error"))
        return {**value, "status": "computing" if snap.running else "ready", "stale": False, "running": snap.running}
    if snap.running:  # 해석이 도는 동안에는 빠른 층만, 5초에 한 번만 다시 잰다
        if cached and time.time() - cached.get("fast_at", 0) < FAST_EVERY:
            return {**cached, "status": "computing", "stale": True, "running": True}
        fast = compute(snap, False)
        value = _merge_fast(cached, fast) if cached else {**fast, "fast_at": time.time()}
        value["state_key"] = (cached or {}).get("state_key", "")
        value["complete_layer"] = False
        _store(study.id, value, to_disk=False)
        return {**value, "status": "computing", "stale": bool(cached), "running": True}
    _submit(snap, key)
    if _failed.get(study.id) == key:
        fast = compute(snap, False)
        fast["overview"]["next"] = None
        return {**fast, "state_key": key, "status": "ready", "stale": False, "error": "일부 항목을 계산하지 못했습니다."}
    if cached and cached.get("sections"):
        return _busy({**cached, "status": "computing", "stale": True})
    fast = compute(snap, False)
    return {**fast, "state_key": key, "status": "computing", "stale": False}


def strip_charts(value: dict) -> dict:
    """AI·보고서용: 그림 자료를 뺀 요약."""
    def sec(x: dict) -> dict:
        y = {k: v for k, v in x.items() if k not in ("charts", "pair_choices", "pair_first", "pair_default", "next_shown_above")}
        if x.get("variants"):
            y["variants"] = [{k: v for k, v in q.items() if k != "charts"} for q in x["variants"]]
        return y
    return {k: v for k, v in {**value, "sections": [sec(x) for x in value.get("sections") or []]}.items() if k not in ("owner", "retry_at", "fast_at")}


def pair_section(db: Session, study: models.Study, a: str, a_goal: str, b: str, b_goal: str) -> dict:
    return pareto_pair(Ctx(snapshot(db, study), True), a, a_goal, b, b_goal)


NOT_READY_NOTE = "아직 계산 중입니다. 이 스터디의 판단은 인용하지 말고 잠시 뒤 study_insights로 다시 확인하세요."


def report_summary(db: Session, study: models.Study, until: float | None = None) -> dict:
    """보고서 자료에 넣는 요약. 학습 직후 부르면 자기 호출이 계산을 시작하므로 잠시 기다린다.
    그래도 안 끝났으면 낡은 문장을 주지 않는다. 낡은 문장이 보고서에 들어가면 화면의 숫자와 어긋난다."""
    try:
        value = wait_ready(db, study, max(0.0, (until or time.time() + 20) - time.time()))
        if value.get("status") == "computing":
            return {"status": "computing", "sections": [], "overview": {}, "note": NOT_READY_NOTE}
        return strip_charts(value)
    except Exception:
        log.exception("보고서용 판단 요약 실패")
        return {"status": "error", "sections": []}


def trust_line(db: Session, m: models.SurrogateModel) -> dict:
    """Simulator 위 한 줄. 공유 화면에도 나가므로 출력 이름과 정확도만 담는다(프로젝트·케이스 정보 없음)."""
    study = db.get(models.Study, m.study_id)
    snap = snapshot(db, study)
    row = next((x for x in snap.models if x.id == m.id), None) or _model_row(m)
    ctx = Ctx(snap, True, light=True)  # 요청 경로다. 옛 모델을 여기서 다시 재지 않는다
    sur = ctx.surrogate(row)
    outs = [o for o in row.outputs if sur and o in sur.models]
    if not outs:
        return {"state": "unknown", "state_label": UNKNOWN_LABEL, "sentence": "이 예측의 정확도를 읽지 못했습니다.", "outputs": {}}
    keys = ctx.decision_outputs()
    pool = [o for o in outs if o in keys] or outs
    best = None
    per = {}
    for o in outs:
        label, unit = ctx.label(o), ctx.unit_of(o)
        sc = ctx.score(row, o)
        level = ctx.verdict(row, o)
        if ctx.rows_of(row, o) < ctx.need:
            line = ("unknown", say("tr_few", n=ctx.rows_of(row, o), m=ctx.need - ctx.rows_of(row, o)))
        elif sc:
            near = ctx.near_flat(row, o) and (sc["r2"] if sc["r2"] is not None else 0.0) < T_["r2_good"]
            line = (level, say("tr_nearflat", out=label, val=with_unit(float(np.median([v for _, v in ctx.values(o)] or [0.0])), unit), p80=with_unit(sc["p80"], unit)) if near
                    else say({"good": "tr_good", "fair": "tr_fair"}.get(level, "tr_caution"), out=label, p80=with_unit(sc["p80"], unit)))
        else:  # 옛 모델은 교차검증 예측이 저장되어 있지 않다. 여기서는 다시 재지 않고 RMSE로 말한다
            line = (level, say({"good": "tr_rmse_good", "fair": "tr_rmse_fair"}.get(level, "tr_rmse_caution"), out=label, rmse=with_unit(ctx.rmse(row, o), unit)))
        per[o] = {"label": label, "unit": unit, "state": line[0], "state_label": state_label("trust", line[0]), "p80": _r(sc["p80"]) if sc else None}
        if o in pool and (best is None or _RANK[line[0]] > _RANK[best[0]]):
            best = line
    return {"state": best[0], "state_label": state_label("trust", best[0]), "sentence": best[1], "basis": "predicted", "outputs": per}


class NotReady(Exception):
    """아직 계산 중이라 게시하지 않는다. 낡은 문장을 보고서에 넣지 않기 위해서다."""


def publish(db: Session, study: models.Study, flow_id: int | None, node_id: str | None, keys: list[str] | None = None) -> list[dict]:
    """대화·작업 흐름·보고서가 같은 숫자를 쓰도록 결과로 게시한다. 자동으로 올리지 않고 AI가 부를 때만."""
    value = wait_ready(db, study, 30)
    if value.get("status") == "computing":
        raise NotReady("판단을 아직 계산 중입니다 → 해석이 끝나거나 계산이 끝난 뒤 다시 게시하세요")
    want = set(keys) if keys else None
    secs = {x["key"]: x for x in value.get("sections") or []}
    name, out = study.name, []

    def put(kind: str, title: str, payload: dict):
        a = service.publish_artifact(db, study.project_id, kind, title, payload, flow_id, node_id)
        caption = title.split("] ", 1)[-1]
        out.append({"artifact_id": a.id, "kind": kind, "title": title, "embed": f"![{caption}](artifact:{a.id})"})

    if want is None or "summary" in want:
        rows = [[x["title"], x["state_label"], x["sentence"]] for x in secs.values() if not x.get("pending") and not x.get("collapsed")]
        put("table", f"[{name}] 판단 요약 (자세히 보기: 데이터 탭)", {"columns": ["질문", "상태", "결론"], "rows": rows})
    inf = secs.get("influence")
    if inf and (want is None or "influence" in want):
        for v in (inf.get("variants") or [])[:2]:
            bars = next((c for c in v.get("charts") or [] if c["kind"] == "influence"), None)
            if bars:
                put("bar", f"[{name}] {v['label']} 영향도", {"categories": [b["label"] for b in bars["bars"]], "series": [{"name": "차지하는 몫(AI 예측 기준)", "values": [b["share"] for b in bars["bars"]]}]})
    act = secs.get("active")
    if act and (want is None or "active" in want):
        ch = next((c for c in act.get("charts") or [] if c["kind"] == "learning"), None)
        if ch:
            put("line", f"[{name}] 추가 해석에 따른 예측 오차", {"x": "정확도를 높이려고 더 돌린 해석 건수", "y": f"예측 평균 오차{' [' + ch['unit'] + ']' if ch.get('unit') else ''}",
                                               "series": [{"name": ch["output"], "points": [{"x": c["m"], "y": c["rmse"]} for c in ch["curve"]]}]})
    par = secs.get("pareto")
    if par and (want is None or "pareto" in want):
        ch = next((c for c in par.get("charts") or [] if c["kind"] == "pareto"), None)
        if ch and ch["front"]:
            # 데이터 탭과 색의 뜻을 맞춘다: 첫 번째 계열(주황)이 좋은 후보다
            on_front = {p[0] for p in ch["front"]}
            series = [{"name": "좋은 후보(해석 확인)", "points": [{"x": p[1], "y": p[2]} for p in ch["front"]]},
                      {"name": "해석한 설계", "points": [{"x": p[1], "y": p[2]} for p in ch["all"] if p[0] not in on_front]}]
            if ch["predicted"]:
                series.append({"name": "예측 후보(아직 해석 안 함)", "points": [{"x": p[0], "y": p[1]} for p in ch["predicted"]]})
            put("scatter", f"[{name}] 두 목표 비교", {"x": ch["a"]["label"], "y": ch["b"]["label"], "series": series})
    return out


def wait_ready(db: Session, study: models.Study, seconds: float) -> dict:
    """끝날 때까지 잠깐 기다린다. 해석이 도는 중이면 기다려도 끝나지 않으니 바로 돌아간다."""
    t0 = time.time()
    value = get_insights(db, study)
    while value.get("status") == "computing" and not value.get("running") and time.time() - t0 < seconds:
        time.sleep(0.3)
        value = get_insights(db, study)
    return value
