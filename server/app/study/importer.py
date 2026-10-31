"""이미 가진 입력·출력을 케이스로 등록한다(표·해석 폴더 → 스터디). 화면과 MCP가 같은 경로를 쓴다.

등록 입구에서 검증을 끝낸다. 숫자가 아닌 값·NaN·Inf가 DB에 들어가면 표 게시·학습·응답 직렬화가 차례로 깨지기 때문이다.
같은 행은 해시로 알아본다. AI 재시도나 HTTP 시간 초과로 같은 표가 두 번 들어와도 모델이 왜곡되지 않아야 한다.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from cae_adapters.tabular import clean_text, cell_state, find_outliers

from .. import models
from ..ws import manager
from . import service

MIN_ROWS = 3
SHOWN = 100  # 보고에 그대로 담는 제외 행 수. 수만 행이 다 빠져도 응답이 커지지 않게
WAITING = ("pending", "failed", "cancelled", "dry_run")  # 값이 오면 채울 수 있는 DOE 점
CONFLICT_NEXT = "값이 맞는지 확인하고, 잘못된 행은 데이터 탭의 케이스 표에서 지우세요(표로 채운 DOE 점은 값만 비워 대기로 돌아가고, 해석한 케이스는 지우지 않습니다)"
PLATFORM_COLUMNS = {"case_id", "status"}  # DOE 계획표가 내보내는 열. 스터디 열이 아니어도 경고할 일이 아니다
_lock = threading.Lock()  # 같은 표의 재시도가 겹쳐도 해시 비교와 등록이 섞이지 않게


class ImportRejected(ValueError):
    """등록할 수 없는 표. 문구에 이유와 할 일이 들어 있다."""


# ---------- 값 비교 ----------
def _fmt(v: float) -> str:
    return format(float(v) + 0.0, ".12g")  # + 0.0: -0.0을 0으로. 표마다 다른 자릿수가 같은 값으로 보이게 12자리로 맞춘다


def params_key(params: dict[str, Any], names: list[str]) -> str:
    return json.dumps([_fmt(params[n]) if n in params else None for n in names])


def row_hash(params: dict[str, Any], results: dict[str, Any], names: list[str]) -> str:
    body = params_key(params, names) + json.dumps(sorted((k, _fmt(v)) for k, v in results.items()))
    return hashlib.sha256(body.encode()).hexdigest()[:32]


def _sames(a: dict[str, float], b: dict[str, float]) -> bool:
    """겹치는 출력의 값이 같은가."""
    return all(_fmt(a[k]) == _fmt(b[k]) for k in a.keys() & b.keys())


def _shown_as(shown: float, exact: float) -> bool:
    """exact를 자릿수를 줄여 표시하면 shown이 되는가. 엑셀은 화면에 보이는 자릿수대로 복사하므로 DOE 점이 정확히 같은 값으로 돌아오지 않는다."""
    text = _fmt(shown)
    if "e" in text:
        return math.isclose(shown, exact, rel_tol=1e-6)
    decimals = len(text.split(".")[1]) if "." in text else 0
    digits = len(text.lstrip("-").replace(".", "").lstrip("0")) or 1
    return math.isclose(round(exact, decimals), shown, abs_tol=1e-12) or math.isclose(float(format(exact, f".{digits}g")), shown, abs_tol=1e-12)


def _near_point(row: dict[str, float], case: models.Case, variables: list[dict], rounded: bool) -> bool:
    """이 행이 그 DOE 점인가. rounded면 표시 자릿수 차이까지만 같은 점으로 본다. 범위의 2%를 넘으면 다른 점이다."""
    for v in variables:
        name = v["name"]
        if name not in row or name not in (case.params or {}):
            return False
        a, b = float(row[name]), float(case.params[name])
        if abs(a - b) > 0.02 * max(float(v["upper"]) - float(v["lower"]), 1e-12):
            return False
        if rounded and not _shown_as(a, b):
            return False
    return True


def _clean_row(row: dict) -> dict:
    return {clean_text(k): clean_text(v) for k, v in row.items()}


def _finite_json(value: Any, depth: int = 0) -> Any:
    """고정값의 NaN·Inf·너무 큰 수를 뺀다. 안쪽에 숨어 있어도 DB에 NaN 글자로 들어가면 뒤에서 직렬화와 비교가 깨진다. 돌려주는 값 None은 뺀다는 뜻이다."""
    if depth > 20:
        return None
    if isinstance(value, dict):
        kept = {clean_text(str(k)): _finite_json(v, depth + 1) for k, v in value.items()}
        return {k: v for k, v in kept.items() if v is not None}
    if isinstance(value, list):
        return [x for x in (_finite_json(v, depth + 1) for v in value) if x is not None]
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value if cell_state(value)[1] == "ok" else None
    return clean_text(value)


def _check_constraints(constraints: list[dict]):
    try:
        service.check_constraints(constraints)
    except ValueError as e:
        raise ImportRejected(str(e)) from e


def _check_flow(db: Session, project_id: int, flow_id):
    try:
        service.check_flow(db, project_id, flow_id)
    except ValueError as e:
        raise ImportRejected(str(e)) from e


# ---------- 열 검사 ----------
def _check_names(inputs: list[dict], outputs: list[dict]):
    seen: set[str] = set()
    for col in [*inputs, *outputs]:
        name = col["name"]
        if not str(name).strip():
            raise ImportRejected("이름이 빈 열이 있습니다 → 열 이름을 넣어 주세요")
        if name in seen:
            raise ImportRejected(f"열 '{name}'이(가) 입력·출력에 두 번 지정됐습니다 → 한 번만 지정하세요")
        seen.add(name)
    for col in inputs:
        lo, hi = col.get("lower"), col.get("upper")
        for v in (lo, hi):
            if v is not None and cell_state(v)[1] != "ok":
                raise ImportRejected(f"입력 '{col['name']}'의 범위가 숫자가 아닙니다 → 범위를 고치거나 비워 두면 데이터로 정합니다")
        if lo is not None and hi is not None and lo >= hi:
            raise ImportRejected(f"입력 '{col['name']}'의 하한({lo:g})이 상한({hi:g})보다 작아야 합니다 → 범위를 고치거나 비워 두면 데이터로 정합니다")


def _profile(rows: list[dict], name: str) -> dict:
    nums: set[str] = set()
    n_num = errors = 0
    texts: list[str] = []
    for r in rows:
        v, state = cell_state(r.get(name))
        if state == "ok":
            n_num += 1
            nums.add(_fmt(v))
        elif state in ("text", "bool"):
            texts.append(str(r.get(name)).strip())
        elif state == "error":
            errors += 1
    return {"numeric": n_num, "distinct": len(nums), "texts": texts, "value": next(iter(nums), None), "errors": errors}


def _sort_columns(cols: list[dict], rows: list[dict], role: str, fixed: dict, removed: list[dict], warnings: list[str]) -> list[dict]:
    """쓸 수 없는 열을 걸러 낸다. 상수는 고정값으로, 글자 열은 이유와 함께 뺀다(변수를 조용히 잃지 않게)."""
    role_ko = "입력" if role == "input" else "출력"
    keep = []
    for col in cols:
        name = col["name"]
        p = _profile(rows, name)
        if not p["numeric"] and not p["texts"]:
            why = "값이 전부 엑셀 수식 오류(#N/A 등)라" if p["errors"] else "값이 비어 있어"
            removed.append({"name": name, "reason": f"{why} {role_ko}에서 뺐습니다"})
        elif len(p["texts"]) > p["numeric"]:
            uniq = sorted(set(p["texts"]))
            if len(uniq) == 1 and not p["numeric"]:
                fixed[name] = uniq[0]
                continue
            removed.append({"name": name, "reason": f"글자 값이라 {role_ko}에서 뺐습니다", "values": uniq[:8]})
            warnings.append(f"'{name}' 열은 글자 값({', '.join(uniq[:3])}{' 등' if len(uniq) > 3 else ''})이라 뺐습니다. "
                            "값마다 결과가 크게 다르면 Simulator가 그 차이를 모르는 채 평균처럼 예측합니다 → 값별로 표를 나눠 따로 만드는 것이 안전합니다")
        elif p["distinct"] == 1 and not p["texts"] and p["numeric"] >= 2:
            fixed[name] = float(p["value"])
            if role == "output":
                warnings.append(f"출력 '{name}'은(는) 모든 행의 값이 같아 예측할 것이 없습니다. 고정값으로만 기록했습니다")
        else:
            keep.append(col)
    return keep


def _clean_rows(rows: list[dict], var_names: list[str], out_names: list[str], fixed: dict, need_outputs: bool
                ) -> tuple[list[dict], list[dict], list[dict]]:
    """행마다 숫자로 읽고 못 쓰는 행은 이유와 함께 뺀다. 돌려주는 값: (쓸 행, 제외 행, 일부 출력만 있는 행)."""
    good, excluded, partial = [], [], []
    seen: dict[str, int] = {}
    fixed_num = {k: v for k, v in fixed.items() if isinstance(v, (int, float))}
    for i, raw in enumerate(rows):
        n = raw.get("_row") if isinstance(raw.get("_row"), int) else i + 1
        reasons, kinds, params = [], set(), {}
        for v in var_names:
            num, state = cell_state(raw.get(v))
            if state == "ok":
                params[v] = num
            elif state == "missing":
                reasons.append(f"입력 '{v}' 값이 비어 있습니다")
                kinds.add("결측")
            elif state == "error":
                reasons.append(f"입력 '{v}' 값이 엑셀 수식 오류({str(raw.get(v)).strip()[:20]})입니다")
                kinds.add("수식 오류")
            elif state == "nonfinite":
                reasons.append(f"입력 '{v}' 값이 무한대이거나 너무 큽니다")
                kinds.add("숫자 아님")
            else:
                reasons.append(f"입력 '{v}' 값 '{str(raw.get(v))[:20]}'이(가) 숫자가 아닙니다")
                kinds.add("숫자 아님")
        for k, want in fixed_num.items():  # 고정값과 다른 행은 모델이 설명할 수 없다
            if k in raw and raw[k] is not None:
                got = cell_state(raw[k])[0]
                if got is not None and _fmt(got) != _fmt(want):
                    reasons.append(f"'{k}' 값 {got:g}이(가) 고정값 {want:g}과 다릅니다 → 이 값까지 바뀌는 표라면 전체 행을 합쳐 새 표로 붙여 넣으세요")
                    kinds.add("고정값 다름")
        results, missing_out = {}, []
        for o in out_names:
            num, state = cell_state(raw.get(o))
            if state == "ok":
                results[o] = num
            elif state == "missing":
                missing_out.append(f"출력 '{o}' 값이 비어 있습니다")
                kinds.add("결측")
            elif state == "error":
                missing_out.append(f"출력 '{o}' 값이 엑셀 수식 오류({str(raw.get(o)).strip()[:20]})입니다")
                kinds.add("수식 오류")
            elif state == "nonfinite":
                missing_out.append(f"출력 '{o}' 값이 무한대이거나 너무 큽니다")
                kinds.add("숫자 아님")
            else:
                missing_out.append(f"출력 '{o}' 값 '{str(raw.get(o))[:20]}'이(가) 숫자가 아닙니다")
                kinds.add("숫자 아님")
        if reasons or (out_names and need_outputs and not results):
            why = reasons + (missing_out if not results else [])
            excluded.append({"row": n, "reason": "; ".join(why), "kind": ("결측" if "결측" in kinds else sorted(kinds)[0] if kinds else "기타")})
            continue
        h = row_hash(params, results, var_names)
        if h in seen:
            excluded.append({"row": n, "reason": f"{seen[h]}행과 같은 행입니다", "kind": "중복"})
            continue
        seen[h] = n
        if missing_out:
            partial.append({"row": n, "reason": "; ".join(missing_out)})
        # DOE 계획표(doe_csv)가 내보낸 번호. 값의 자릿수가 줄어도 어느 점인지 이것으로 안다
        case_id = raw.get("case_id")
        good.append({"row": n, "params": params, "results": results, "hash": h, "case_id": int(case_id) if isinstance(case_id, (int, float)) and not isinstance(case_id, bool) and cell_state(case_id)[1] == "ok" else None,
                     "run_dir": str(raw["_run_dir"])[:500] if raw.get("_run_dir") else None,
                     "label": str(raw["_label"])[:200] if raw.get("_label") else None})
    return good, excluded, partial


def _reason_counts(excluded: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in excluded:
        out[e["kind"]] = out.get(e["kind"], 0) + 1
    return out


def _outlier_warnings(good: list[dict], names: list[str], key: str) -> list[str]:
    out = []
    for name in names:
        vals = [g[key][name] for g in good if name in g[key]]
        hits = find_outliers(vals)
        if hits:
            shown = ", ".join(f"{vals[i]:g}" for i in hits[:3])
            rows = [g["row"] for g in good if name in g[key]]
            where = ", ".join(str(rows[i]) for i in hits[:5])
            tail = "Simulator 슬라이더 범위가 그만큼 넓어집니다" if key == "params" else "모델 정확도가 나빠질 수 있습니다"
            out.append(f"'{name}'의 값 {shown}이(가) 다른 값과 크게 떨어져 있습니다({where}행). {tail} → 값이 맞는지 확인하세요")
    return out


def _output_defs(cols: list[dict]) -> list[dict]:
    out = []
    for c in cols:
        d: dict[str, Any] = {"name": c["name"], "note": c.get("note") or (f"[{c['unit']}]" if c.get("unit") else "")}
        if c.get("unit"):
            d["unit"] = c["unit"]
        out.append(d)
    return out


# ---------- 등록 ----------
def _register(db: Session, study: models.Study, good: list[dict], user_id: int, source: str) -> dict:
    """행을 케이스로 넣는다. 같은 행은 건너뛰고, 대기 중인 DOE 점은 채우고, 입력이 같은 케이스에는 새 출력만 더한다."""
    names = service._var_names(study)
    by_hash = {(c.origin or {}).get("hash"): c for c in study.cases if (c.origin or {}).get("hash")}
    open_points = {c.id: c for c in study.cases if c.status in WAITING}
    by_params: dict[str, list[models.Case]] = {}
    for c in study.cases:
        if all(n in (c.params or {}) for n in names):
            by_params.setdefault(params_key(c.params, names), []).append(c)
    made: list[tuple[int, models.Case]] = []
    updated, filled, dup, conflicts, rounded = 0, 0, 0, 0, 0
    cases_for_rows: list[tuple[int, models.Case]] = []
    for g in good:
        origin = {"hash": g["hash"], "row": g["row"], "source": source, **({"label": g["label"]} if g["label"] else {})}
        same = by_params.get(params_key(g["params"], names), [])
        if g["hash"] in by_hash:
            dup += 1
            cases_for_rows.append((g["row"], by_hash[g["hash"]]))
            continue
        waiting = next((c for c in same if c.id in open_points), None)
        if waiting is None and g.get("case_id") in open_points and _near_point(g["params"], open_points[g["case_id"]], study.variables, False):
            waiting = open_points[g["case_id"]]
            rounded += 1
        if waiting is None and open_points:
            # 번호 열 없이 복사한 표: 자릿수만 다른 점이 하나뿐일 때만 같은 점으로 본다. 애매하면 채우지 않는다(다른 점에 값을 넣으면 찾을 수 없다)
            near = [c for c in open_points.values() if _near_point(g["params"], c, study.variables, True)]
            if len(near) == 1:
                waiting = near[0]
                rounded += 1
        if waiting is not None:  # DOE 계획을 밖에서 해석해 온 값: 새 케이스가 아니라 그 점을 채운다. 입력값은 계획의 정확한 값을 둔다
            waiting.results, waiting.status, waiting.run_by, waiting.error = g["results"], "complete", user_id, None
            waiting.origin = origin
            waiting.run_dir = g["run_dir"] or waiting.run_dir
            filled += 1
            by_hash[g["hash"]] = waiting
            open_points.pop(waiting.id, None)
            cases_for_rows.append((g["row"], waiting))
            continue
        done = [c for c in same if c.status == "complete"]
        match = next((c for c in done if _sames(g["results"], c.results or {})), None)
        if match is not None:
            new_keys = {k: v for k, v in g["results"].items() if k not in (match.results or {})}
            if new_keys:  # 같은 행에 출력 열만 늘어난 표(입력·출력 파일을 따로 넣는 경우)
                match.results = {**(match.results or {}), **new_keys}
                match.origin = {**(match.origin or {}), "hash": g["hash"]}
                updated += 1
            else:
                dup += 1
            cases_for_rows.append((g["row"], match))
            by_hash[g["hash"]] = match
            continue
        if done:
            conflicts += 1
        case = models.Case(study_id=study.id, params=g["params"], results=g["results"], status="complete", source="import",
                           run_dir=g["run_dir"], run_by=user_id, origin=origin)
        db.add(case)
        made.append((g["row"], case))
        cases_for_rows.append((g["row"], case))
        by_params.setdefault(params_key(g["params"], names), []).append(case)
        by_hash[g["hash"]] = case
    db.flush()
    return {"registered": len(made), "updated": updated, "filled_pending": filled, "duplicates": dup, "conflicts": conflicts, "rounded": rounded, "open_left": len(open_points),
            "case_ids": [c.id for _, c in made],
            "cases": [{"row": r, "case_id": c.id} for r, c in cases_for_rows]}


def _publish_report(db: Session, study: models.Study, rep: dict, flow_id, node_id):
    lines = [f"- 받은 행 **{rep['received']}개**, 등록 **{rep['registered']}개**"
             + (f", 입력이 같은 케이스에 출력 추가 {rep['updated']}개" if rep.get("updated") else "")
             + (f", 대기 DOE 채움 {rep['filled_pending']}개" if rep.get("filled_pending") else "")
             + (f", 이미 있는 행 {rep['duplicates']}개" if rep.get("duplicates") else "")]
    if rep.get("excluded_count"):
        by = ", ".join(f"{k} {v}개" for k, v in rep["excluded_by_reason"].items())
        lines.append(f"- 제외 **{rep['excluded_count']}개**: {by}")
    if rep.get("fixed"):
        lines.append("- 고정값(변수 아님): " + ", ".join(f"{k}={v}" for k, v in rep["fixed"].items()))
    for c in rep.get("removed_columns") or []:
        lines.append(f"- 뺀 열 '{c['name']}': {c['reason']}")
    for c in rep.get("range_changes") or []:
        lines.append(f"- 입력 '{c['name']}' 범위를 넓혔습니다: {c['old_lower']:g}~{c['old_upper']:g} → {c['lower']:g}~{c['upper']:g}")
    lines += [f"- {w}" for w in rep.get("warnings") or []]
    service.publish_artifact(db, study.project_id, "markdown", f"[{study.name}] 불러온 데이터 확인", {"text": "\n".join(lines)}, flow_id, node_id)


def _finish(db: Session, study: models.Study, rep: dict, flow_id, node_id) -> dict:
    db.refresh(study)
    service.publish_cases_table(db, study, flow_id, node_id)
    _publish_report(db, study, rep, flow_id, node_id)
    manager.publish({"type": "study_updated", "project_id": study.project_id, "data": {"id": study.id}})
    return rep


def _table_hash(inputs: list[dict], outputs: list[dict], rows: list[dict]) -> str:
    h = hashlib.sha256()
    h.update(json.dumps([sorted(c["name"] for c in inputs), sorted(c["name"] for c in outputs)], ensure_ascii=False).encode())
    for r in rows:
        h.update(json.dumps({k: v for k, v in sorted(r.items()) if k != "_row"}, ensure_ascii=False, default=str, sort_keys=True).encode())
    return h.hexdigest()[:32]


def import_new(db: Session, project_id: int, body: dict, user_id: int) -> dict:
    """표 → 새 스터디. 같은 표를 다시 넣으면 새 스터디를 만들지 않고 기존 스터디를 돌려준다."""
    inputs, outputs, rows = body.get("inputs") or [], body.get("outputs") or [], [_clean_row(r) for r in body.get("rows") or []]
    inputs = [{**c, "name": clean_text(c["name"])} for c in inputs]
    outputs = [{**c, "name": clean_text(c["name"])} for c in outputs]
    body = {**body, "name": clean_text(body.get("name")), "source": clean_text(body.get("source"))}
    expects_fields = bool(body.get("expects_fields"))
    _check_names(inputs, outputs)
    _check_constraints(body.get("constraints") or [])
    _check_flow(db, project_id, body.get("flow_id"))  # 스터디를 만들기 전에 본다. 만든 뒤에 터지면 500과 함께 스터디만 남는다
    if not inputs:
        raise ImportRejected("입력 열이 지정되지 않았습니다 → 입력으로 쓸 숫자 열을 한 개 이상 골라 주세요")
    if not outputs and not expects_fields:
        raise ImportRejected("출력 열이 지정되지 않았습니다 → 예측할 결과 열을 한 개 이상 골라 주세요")
    if not rows:
        raise ImportRejected("표에 데이터 행이 없습니다 → 값이 든 파일을 다시 고르세요")
    table_hash = _table_hash(inputs, outputs, rows)
    with _lock:
        for s in db.query(models.Study).filter_by(project_id=project_id):
            info = s.import_info or {}
            if info.get("table_hash") == table_hash:
                return {**(info.get("report") or {}), "study_id": s.id, "study_name": s.name, "reused": True, "registered": 0,
                        "duplicates": len(s.cases), "received": len(rows), "case_ids": [], "fixed": info.get("fixed") or {},
                        "variables": s.variables, "outputs": s.outputs or [],
                        "cases": [{"row": (c.origin or {}).get("row"), "case_id": c.id} for c in s.cases if c.origin],
                        "warnings": ["같은 표를 이미 불러왔습니다. 새 스터디를 만들지 않고 기존 스터디를 돌려줍니다"]}
        # JSON의 NaN이 고정값으로 들어가면 응답 직렬화가 깨진다
        fixed: dict[str, Any] = _finite_json(body.get("fixed") or {})
        removed: list[dict] = list(body.get("pre_removed") or [])  # 파일 분석에서 이미 뺀 열(순번·빈 열)
        warnings: list[str] = list(body.get("pre_warnings") or [])
        var_cols = _sort_columns(inputs, rows, "input", fixed, removed, warnings)
        out_cols = _sort_columns(outputs, rows, "output", fixed, removed, warnings)
        why = "; ".join(f"{c['name']}: {c['reason']}" for c in removed) or "; ".join(f"{k}: 값이 하나뿐" for k in fixed)
        if not var_cols:
            raise ImportRejected(f"입력으로 쓸 수 있는 열이 없습니다. 값이 변하는 숫자 열이 필요합니다({why or '지정한 열 확인'}) → 입력 열을 다시 고르세요")
        if not out_cols and not expects_fields:
            raise ImportRejected(f"예측할 수 있는 출력 열이 없습니다. 값이 변하는 숫자 열이 필요합니다({why or '지정한 열 확인'}) → 출력 열을 다시 고르세요")
        var_names = [c["name"] for c in var_cols]
        out_names = [c["name"] for c in out_cols]
        for spec, kind in ((body.get("objectives") or [], "목적"), (body.get("constraints") or [], "제약")):
            for o in spec:
                if o["name"] not in out_names and not expects_fields:
                    raise ImportRejected(f"{kind} '{o['name']}'이(가) 출력 열에 없습니다 → 출력 열({', '.join(out_names)}) 중에서 고르세요")
        good, excluded, partial = _clean_rows(rows, var_names, out_names, {}, need_outputs=not expects_fields)
        if len(good) < MIN_ROWS:
            brief = _reason_counts(excluded)
            detail = ", ".join(f"{k} {v}개" for k, v in brief.items())
            sample = f" 예: {excluded[0]['row']}행 {excluded[0]['reason']}" if excluded else ""
            raise ImportRejected(f"쓸 수 있는 행이 {len(good)}개뿐입니다. 예측 모델에는 {MIN_ROWS}행 이상이 필요합니다"
                                 f"(받은 행 {len(rows)}개{', 제외: ' + detail if detail else ''}).{sample} → 값을 고치거나 행을 더 넣어 주세요")
        # 범위는 데이터 최소~최대. 사용자가 더 넓게 주면 그대로 쓴다. 좁게 줘도 데이터를 버리지 않고 넓힌다
        variables, range_changes = [], []
        for c in var_cols:
            vals = [g["params"][c["name"]] for g in good]
            lo, hi = min(vals), max(vals)
            if lo == hi:  # 제외한 뒤 남은 행의 값이 하나뿐이다
                fixed[c["name"]] = lo
                warnings.append(f"입력 '{c['name']}'은(는) 쓸 수 있는 행에서 값이 하나뿐이라 고정값으로 기록했습니다")
                continue
            given_lo, given_hi = c.get("lower"), c.get("upper")
            lower = lo if given_lo is None else min(given_lo, lo)
            upper = hi if given_hi is None else max(given_hi, hi)
            if (given_lo is not None and lower < given_lo) or (given_hi is not None and upper > given_hi):
                range_changes.append({"name": c["name"], "lower": lower, "upper": upper,
                                      "old_lower": given_lo if given_lo is not None else lower,
                                      "old_upper": given_hi if given_hi is not None else upper})
            v: dict[str, Any] = {"name": c["name"], "lower": lower, "upper": upper}
            if c.get("unit"):
                v["unit"] = c["unit"]
            variables.append(v)
        if not variables:
            raise ImportRejected("값이 변하는 입력이 남지 않았습니다 → 입력 열을 다시 고르거나 제외된 행을 고쳐 주세요")
        var_names = [v["name"] for v in variables]
        for g in good:  # 고정값으로 옮긴 입력을 뺀다. 해시도 다시 낸다
            g["params"] = {k: g["params"][k] for k in var_names}
            g["hash"] = row_hash(g["params"], g["results"], var_names)
        warnings += _outlier_warnings(good, var_names, "params") + _outlier_warnings(good, out_names, "results")
        if partial:
            warnings.append(f"일부 출력이 빈 행 {len(partial)}개는 등록했지만, 빈 출력을 학습할 때는 빠집니다")
        name = str(body.get("name") or "").strip() or (Path(str(body.get("source") or "")).stem or "불러온 표")
        study = models.Study(project_id=project_id, name=service._unique_study_name(db, project_id, name[:200]), variables=variables,
                             objectives=body.get("objectives") or [], constraints=body.get("constraints") or [],
                             outputs=_output_defs(out_cols), adapter="data", adapter_config={}, flow_id=body.get("flow_id"))
        db.add(study)
        db.flush()
        counts = _register(db, study, good, user_id, str(body.get("source") or ""))
        if counts["conflicts"]:
            warnings.append(f"입력이 같은데 출력이 다른 행이 {counts['conflicts']}개 있어 모두 등록했습니다. 모델은 그 차이를 잡음으로 다룹니다 → {CONFLICT_NEXT}")
        rep = {"study_id": study.id, "study_name": study.name, "received": len(rows), **{k: counts[k] for k in ("registered", "updated", "filled_pending", "duplicates", "case_ids", "cases")},
               "excluded_count": len(excluded), "excluded": excluded[:SHOWN], "excluded_by_reason": _reason_counts(excluded),
               "partial_count": len(partial), "partial": partial[:SHOWN], "fixed": fixed, "removed_columns": removed, "ignored_columns": [],
               "range_changes": range_changes, "variables": variables, "outputs": study.outputs, "warnings": warnings}
        if expects_fields and not out_cols:
            rep["warnings"].append("스칼라 출력이 없는 스터디입니다. 필드를 올리면 필드 최대값을 출력으로 만듭니다")
        brief = {k: rep[k] for k in ("received", "registered", "excluded_count", "excluded_by_reason", "fixed", "removed_columns", "range_changes", "warnings")}
        study.import_info = {"kind": body.get("kind") or "table", "source": str(body.get("source") or ""), "fixed": fixed, "table_hash": table_hash,
                             "imported_at": datetime.now().isoformat(timespec="seconds"), "report": brief}
        db.commit()
        return _finish(db, study, rep, body.get("flow_id"), body.get("node_id"))


def import_rows(db: Session, study: models.Study, body: dict, user_id: int) -> dict:
    """있는 스터디에 행 추가. 범위 밖 값은 범위를 넓히고 보고한다(모델이 그 값을 쓸 수 있어야 하므로)."""
    rows = [_clean_row(r) for r in body.get("rows") or []]
    new_outs = [{**o, "name": clean_text(o["name"])} for o in body.get("outputs") or []]
    expects_fields = bool(body.get("expects_fields"))
    pre_warnings = list(body.get("pre_warnings") or [])
    if not rows:
        raise ImportRejected("표에 데이터 행이 없습니다 → 값이 든 파일을 다시 고르세요")
    _check_flow(db, study.project_id, body.get("flow_id"))
    # 읽는 일까지 잠금 안에서 한다. 잠금 밖에서 읽은 케이스·범위로 비교하면 동시에 온 같은 표가 모두 새 행으로 들어가고, 넓힌 범위도 서로 덮어쓴다
    with _lock:
        db.expire_all()
        var_names = service._var_names(study)
        if not var_names:
            raise ImportRejected("이 스터디에는 입력 변수가 없어 행을 넣을 수 없습니다 → 변수를 정한 스터디를 만드세요")
        known = list(dict.fromkeys(service.output_names(study) + [o["name"] for o in new_outs]))
        info = dict(study.import_info or {})
        fixed = dict(info.get("fixed") or {})
        if not all(any(v in r for r in rows) for v in var_names):
            missing = [v for v in var_names if not any(v in r for r in rows)]
            raise ImportRejected(f"입력 열이 표에 없습니다: {', '.join(missing)} → 이 스터디의 입력은 {', '.join(var_names)}입니다. 열 이름을 맞춰 주세요")
        used_outs = [o for o in known if any(o in r for r in rows)]
        if not used_outs and not expects_fields:
            raise ImportRejected(f"출력 열이 표에 없습니다 → 이 스터디의 출력은 {', '.join(known) or '아직 없습니다'}입니다. "
                                 "새 출력이면 outputs에 지정해 주세요")
        ignored = sorted({k for r in rows for k in r if k not in var_names and k not in known and k not in fixed and not k.startswith("_") and k not in PLATFORM_COLUMNS})
        good, excluded, partial = _clean_rows(rows, var_names, used_outs, fixed, need_outputs=not expects_fields)
        warnings = pre_warnings + _outlier_warnings(good, var_names, "params") + _outlier_warnings(good, used_outs, "results")
        if ignored:
            warnings.append(f"이 스터디에 없는 열은 쓰지 않았습니다: {', '.join(ignored[:10])} → 새 출력이면 outputs에 지정해 주세요")
        if partial:
            warnings.append(f"일부 출력이 빈 행 {len(partial)}개는 등록했지만, 빈 출력을 학습할 때는 빠집니다")
        range_changes = []
        variables = [dict(v) for v in study.variables]
        for v in variables:
            vals = [g["params"][v["name"]] for g in good]
            if not vals:
                continue
            lo, hi = min(min(vals), v["lower"]), max(max(vals), v["upper"])
            if lo < v["lower"] or hi > v["upper"]:
                range_changes.append({"name": v["name"], "lower": lo, "upper": hi, "old_lower": v["lower"], "old_upper": v["upper"]})
                v["lower"], v["upper"] = lo, hi
        counts = _register(db, study, good, user_id, str(body.get("source") or ""))
        if counts["conflicts"]:
            warnings.append(f"입력이 같은데 출력이 다른 행이 {counts['conflicts']}개 있어 모두 등록했습니다. 모델은 그 차이를 잡음으로 다룹니다 → {CONFLICT_NEXT}")
        if counts["rounded"]:
            warnings.append(f"자릿수가 줄어든 값 {counts['rounded']}개를 대기 중인 DOE 점에 맞춰 채웠습니다. 입력값은 계획의 정확한 값을 씁니다")
        if counts["open_left"] and counts["registered"]:
            warnings.append(f"대기 중인 DOE 점과 맞지 않는 행 {counts['registered']}개는 새 케이스로 등록했습니다. 대기 점 {counts['open_left']}개는 그대로입니다 → 계획표의 case_id 열을 지우지 말고 함께 복사하세요")
        if excluded and not good:
            warnings.append(f"받은 행 {len(rows)}개를 모두 뺐습니다. 예: {excluded[0]['row']}행 {excluded[0]['reason']}")
        if range_changes:
            study.variables = variables
            warnings.append("입력 범위를 넓혔습니다. 배포 중인 Simulator는 다시 학습해 배포해야 새 범위를 씁니다")
        if new_outs:
            have = {o["name"] for o in study.outputs or []}
            study.outputs = [*(study.outputs or []), *[d for d in _output_defs(new_outs) if d["name"] not in have]]
        study.version += 1
        rep = {"study_id": study.id, "study_name": study.name, "received": len(rows), **{k: counts[k] for k in ("registered", "updated", "filled_pending", "duplicates", "case_ids", "cases")},
               "excluded_count": len(excluded), "excluded": excluded[:SHOWN], "excluded_by_reason": _reason_counts(excluded),
               "partial_count": len(partial), "partial": partial[:SHOWN], "fixed": fixed, "removed_columns": [], "ignored_columns": ignored,
               "range_changes": range_changes, "variables": study.variables, "outputs": study.outputs or [], "warnings": warnings}
        if rep["registered"] or rep["updated"] or rep["filled_pending"]:
            info["report"] = {k: rep[k] for k in ("received", "registered", "excluded_count", "excluded_by_reason", "range_changes", "warnings")}
            study.import_info = info
        if body.get("flow_id"):
            study.flow_id = body["flow_id"]
        db.commit()
        return _finish(db, study, rep, body.get("flow_id"), body.get("node_id"))
