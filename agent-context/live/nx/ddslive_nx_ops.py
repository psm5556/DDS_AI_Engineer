# -*- coding: utf-8 -*-
"""DDS Live Link — NX / Simcenter 3D 도우미 (열린 세션에서 자주 하는 일을 미리 만든 함수로)

에이전트가 매번 NXOpen 코드를 새로 쓰지 않도록, 치수 바꾸기·뷰 바꾸기·되돌리기·화면 저장을 함수로 둔다.
dds-live MCP의 live_set_params / live_view / live_undo / live_info가 live_exec(tool="nx")로 아래처럼 부른다.
    import ddslive_nx_ops as ops; ops = ops.fresh(); result = ops.set_params({"thickness": 4.0})
처음 한 번 불러오면 세션에 남으므로 이후에는 함수 호출만 한다(파일이 바뀌면 fresh()가 다시 불러온다).
모든 함수는 JSON 문자열을 돌려주고, 단계별 걸린 시간(ms)을 담는다 — 사내에서 어디가 느린지 바로 보기 위해서다.

사내에서 검증된 배치 스크립트의 함수를 그대로 쓴다.
  nx_param_update.py(do_update·solid_bodies·mass_properties) · nx_inspect_part.py(expressions_info)
  sc_update_solve.py(find_expr·edit_expr·display)
NX 내장 Python 3 용. f-string 미사용(스킬 규약).
"""
import importlib
import json
import os
import sys
import time

import NXOpen

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS = os.path.normpath(os.path.join(HERE, "..", "..", "skills"))
for _d in (os.path.join(SKILLS, "nx-cad-automation", "scripts"),
           os.path.join(SKILLS, "simcenter3d-automation", "scripts")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import nx_inspect_part as inspect_part  # noqa: E402
import nx_param_update as cad  # noqa: E402
import sc_update_solve as sc  # noqa: E402

# 다시 불러와도(reload) 되돌리기 목록은 유지한다
_STATE = globals().get("_STATE") or {"marks": [], "mtime": None}
_STATE["mtime"] = os.path.getmtime(__file__)

CANNED = {"front": "Front", "back": "Back", "top": "Top", "bottom": "Bottom", "left": "Left", "right": "Right",
          "iso": "Isometric", "isometric": "Isometric", "trimetric": "Trimetric"}
# TODO(사내검증): RenderingStyleType 이름 — 표시 방식을 바꾸는 동작을 Journal로 기록해 맞출 것
STYLES = {"shaded": "Shaded", "edges": "ShadedWithEdges", "wire": "StaticWireframe",
          "hidden": "WireframeWithHiddenEdges", "dim": "WireframeWithDimEdges"}


def fresh():
    """PC Agent 업데이트로 이 파일이 바뀌었으면 다시 불러온다. 스킬 스크립트도 함께."""
    me = sys.modules[__name__]
    if os.path.getmtime(__file__) != _STATE["mtime"]:
        for m in (inspect_part, cad, sc):
            importlib.reload(m)
        me = importlib.reload(me)
    return me


def _session():
    return NXOpen.Session.GetSession()


def _ms(t0):
    return int((time.time() - t0) * 1000)


def _out(obj):
    return json.dumps(obj, ensure_ascii=False, default=str)


def _kind(part):
    """파트 종류: cad / idealized / fem / sim."""
    try:
        name = (part.FullPath or part.Leaf or "").lower()
    except Exception:
        name = ""
    if name.endswith(".sim"):
        return "sim"
    if name.endswith(".fem") or name.endswith(".afm"):
        return "fem"
    if name.endswith("_i.prt"):
        return "idealized"
    return "cad"


def _pinfo(part):
    if part is None:
        return None
    try:
        return {"name": part.Leaf, "kind": _kind(part), "path": part.FullPath}
    except Exception:
        return {"name": str(part), "kind": _kind(part)}


def _work_view(part):
    # TODO(사내검증): CAE 파트(.fem/.sim)에서 ModelingViews가 없으면 Views.WorkView를 쓴다
    try:
        return part.ModelingViews.WorkView
    except Exception:
        return part.Views.WorkView


# ---------------------------------------------------------------------------
# 읽기
# ---------------------------------------------------------------------------
def summary(all_expressions=False):
    """열린 모델 요약. 자동 이름(p12 등) expression은 기본으로 개수만 — 출력이 길어지면 AI가 느려진다."""
    t0 = time.time()
    s = _session()
    dp, wp = s.Parts.Display, s.Parts.Work
    out = {"ok": True, "display_part": _pinfo(dp), "work_part": _pinfo(wp),
           "open_parts": [_pinfo(p) for p in s.Parts]}
    if wp is not None:
        exprs = inspect_part.expressions_info(wp)
        for e in exprs:
            e["constant"] = cad.is_plain_number(e.get("rhs", ""))
        named = [e for e in exprs if not e.get("auto_named")]
        out["expressions"] = exprs if all_expressions else named
        out["auto_named_expressions"] = len(exprs) - len(named)
        feats = []
        try:
            for f in wp.Features:
                item = {"name": f.GetFeatureName(), "type": f.FeatureType}
                try:
                    item["suppressed"] = bool(f.Suppressed)  # TODO(사내검증) 속성 이름
                except Exception:
                    pass
                if f.Name:
                    item["custom_name"] = f.Name
                feats.append(item)
        except Exception:
            pass  # CAE 파트에는 Features가 없다
        out["features"] = feats
        bodies = []
        try:
            for b in wp.Bodies:
                bodies.append({"name": b.Name or b.JournalIdentifier, "id": b.JournalIdentifier,
                               "solid": bool(b.IsSolidBody)})
        except Exception:
            pass
        out["bodies"] = bodies
    out["ms"] = {"total": _ms(t0)}
    return _out(out)


# ---------------------------------------------------------------------------
# 설계 수정
# ---------------------------------------------------------------------------
def _find(s, name):
    """expression을 워크 → 디스플레이 → 열린 CAD → SIM → FEM 파트 순서로 찾는다(sc_update_solve 라우팅과 같은 순서)."""
    order = [s.Parts.Work, s.Parts.Display]
    parts = list(s.Parts)
    for kind in ("cad", "sim", "fem", "idealized"):
        order += [p for p in parts if _kind(p) == kind]
    seen = set()
    for p in order:
        if p is None or id(p) in seen:
            continue
        seen.add(id(p))
        e = sc.find_expr(p, name)
        if e is not None:
            return p, e
    return None, None


def _value(e):
    try:
        return e.Value
    except Exception:
        return e.RightHandSide


def _undo_to(s, mark):
    # TODO(사내검증): UndoToMark 두 번째 인자(None) 허용 여부
    s.UndoToMark(mark, None)


def set_params(values, fem=False, measure=False, capture=None):
    """expression 값을 한꺼번에 바꾸고 업데이트는 한 번만 한다. 실패하면 바꾸기 전으로 되돌린다.
    values: {"expression 이름": 숫자}  fem: Idealized·FEM 업데이트(재메쉬)까지  capture: 저장할 PNG 경로"""
    t0 = time.time()
    s = _session()
    ms = {}
    targets, missing = [], []
    for name, v in values.items():
        part, e = _find(s, name)
        if e is None:
            missing.append(name)
        else:
            targets.append((name, v, part, e))
    if missing:
        return _out({"ok": False, "error": "열린 파트에 없는 expression: " + ", ".join(missing)
                     + " — live_info로 이름을 확인하세요", "missing": missing})

    label = "DDS: " + ", ".join("%s %s→%s" % (n, _value(e), v) for n, v, _, e in targets)
    mark = s.SetUndoMark(NXOpen.Session.MarkVisibility.Visible, label[:120])
    display0 = s.Parts.Display
    changed, warnings = [], []
    try:
        # 파트별로 묶어서: 그 파트를 화면에 띄우고 고친 뒤 업데이트 한 번 (배치 스크립트와 같은 방식)
        by_part = []
        for n, v, part, e in targets:
            for item in by_part:
                if item[0] is part:
                    item[1].append((n, v, e))
                    break
            else:
                by_part.append((part, [(n, v, e)]))
        t = time.time()
        n_err, msgs = 0, []
        for part, items in by_part:
            if part is not s.Parts.Display and part is not s.Parts.Work:
                sc.display(s, part)
            for n, v, e in items:
                if not cad.is_plain_number(e.RightHandSide):
                    warnings.append("'%s'는 수식(%s)으로 구동 중이었는데 상수로 덮어썼습니다" % (n, e.RightHandSide))
                old = _value(e)
                sc.edit_expr(part, e, v)
                changed.append({"name": n, "old": old, "new": _value(e), "part": part.Leaf})
            k, m = cad.do_update(s)
            n_err += k
            msgs += m
        ms["update"] = _ms(t)
        if n_err:
            raise RuntimeError("모델 업데이트 실패(오류 %d건): %s" % (n_err, "; ".join(msgs)[:1500]))

        if fem:
            t = time.time()
            cad_changed = any(_kind(p) == "cad" for p, _ in by_part)
            parts = list(s.Parts)
            ideal = [p for p in parts if _kind(p) == "idealized"]
            fems = [p for p in parts if _kind(p) == "fem"]
            if not fems:
                warnings.append("FEM 파트가 열려 있지 않아 메쉬는 업데이트하지 않았습니다")
            if cad_changed:
                for p in ideal:
                    sc.display(s, p)
                    k, m = cad.do_update(s)
                    if k:
                        raise RuntimeError("Idealized 업데이트 실패: " + "; ".join(m)[:1000])
            for p in fems:
                sc.display(s, p)
                p.BaseFEModel.UpdateFemodel()  # TODO(사내검증) 반환값/메쉬 실패 감지
            ms["fem"] = _ms(t)
    except Exception as e:  # noqa: BLE001
        try:
            _undo_to(s, mark)
            undone = True
        except Exception:
            undone = False
        _restore_display(s, display0)
        return _out({"ok": False, "error": str(e), "undone": undone, "ms": dict(ms, total=_ms(t0)),
                     "how": "바꾸기 전으로 되돌렸습니다" if undone else "NX에서 Edit > Undo로 되돌리세요"})
    _restore_display(s, display0)
    _STATE["marks"].append(mark)
    out = {"ok": True, "changed": changed, "warnings": warnings, "undo_label": label[:120]}
    if measure:
        t = time.time()
        try:
            wp = s.Parts.Work
            bodies = cad.solid_bodies(wp)
            out["mass"] = cad.mass_properties(wp, bodies) if bodies else None
        except Exception as e:  # noqa: BLE001
            warnings.append("질량특성 측정 실패: " + str(e))
        ms["measure"] = _ms(t)
    if capture:
        t = time.time()
        out["image"] = _capture(capture)
        ms["capture"] = _ms(t)
    out["ms"] = dict(ms, total=_ms(t0))
    return _out(out)


def _restore_display(s, part):
    if part is not None and s.Parts.Display is not part:
        try:
            sc.display(s, part)
        except Exception:
            pass


def undo(steps=1):
    """이 도우미가 바꾼 것을 최근 것부터 steps번 되돌린다."""
    t0 = time.time()
    s = _session()
    marks = _STATE["marks"]
    if not marks:
        return _out({"ok": False, "error": "도우미로 바꾼 기록이 없습니다. NX에서 Edit > Undo를 쓰세요"})
    steps = max(1, min(int(steps), len(marks)))
    target = marks[-steps]
    _undo_to(s, target)
    del marks[-steps:]
    return _out({"ok": True, "undone": steps, "left": len(marks), "ms": {"total": _ms(t0)}})


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------
def _objects(part, names):
    """이름으로 바디를 찾는다. 바디 이름·JournalIdentifier 또는 피처 이름(예: EXTRUDE(3))이면 그 피처의 바디."""
    want = dict((str(n).lower(), n) for n in names)
    found, hit = [], set()
    for b in part.Bodies:
        for key in (b.Name, b.JournalIdentifier):
            if key and key.lower() in want:
                found.append(b)
                hit.add(key.lower())
                break
    try:
        for f in part.Features:
            for key in (f.Name, f.GetFeatureName(), f.JournalIdentifier):
                if key and key.lower() in want:
                    found += list(f.GetBodies())
                    hit.add(key.lower())
                    break
    except Exception:
        pass
    return found, [want[k] for k in want if k not in hit]


def view(orient=None, fit=True, style=None, show=None, hide=None, only=None, capture=None):
    """뷰 방향·맞추기·표시 방식·보이기/숨기기. orient: front/top/right/iso… style: shaded/edges/wire/hidden"""
    t0 = time.time()
    s = _session()
    dp = s.Parts.Display
    wv = _work_view(dp)
    out = {"ok": True, "warnings": []}
    if orient:
        key = CANNED.get(str(orient).lower())
        if not key:
            return _out({"ok": False, "error": "방향은 %s 중 하나" % ", ".join(sorted(CANNED))})
        wv.Orient(getattr(NXOpen.View.Canned, key), NXOpen.View.ScaleAdjustment.Fit)
    if style:
        key = STYLES.get(str(style).lower())
        if not key:
            return _out({"ok": False, "error": "표시 방식은 %s 중 하나" % ", ".join(sorted(STYLES))})
        wv.RenderingStyle = getattr(NXOpen.View.RenderingStyleType, key)  # TODO(사내검증)
    # TODO(사내검증): DisplayManager.BlankObjects/UnblankObjects — 숨기기를 Journal로 기록해 확인
    dm = s.DisplayManager
    if only:
        objs, miss = _objects(dp, only)
        keep = set(id(o) for o in objs)
        others = [b for b in dp.Bodies if id(b) not in keep]
        if others:
            dm.BlankObjects(others)
        if objs:
            dm.UnblankObjects(objs)
        if miss:
            out["warnings"].append("찾지 못한 이름: " + ", ".join(miss))
    if show:
        objs, miss = _objects(dp, show)
        if objs:
            dm.UnblankObjects(objs)
        if miss:
            out["warnings"].append("찾지 못한 이름: " + ", ".join(miss))
    if hide:
        objs, miss = _objects(dp, hide)
        if objs:
            dm.BlankObjects(objs)
        if miss:
            out["warnings"].append("찾지 못한 이름: " + ", ".join(miss))
    if fit and not orient:
        wv.Fit()
    if capture:
        out["image"] = _capture(capture)
    out["ms"] = {"total": _ms(t0)}
    return _out(out)


def _capture(path):
    """그래픽 창(뷰포트)만 PNG로 저장한다. 창을 앞으로 가져오지 않는다. 실패하면 None."""
    # TODO(사내검증): 대화형 세션의 ImageExportBuilder 속성 — File > Export > Image를 Journal로 기록해 확인
    try:
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        b = NXOpen.UI.GetUI().CreateImageExportBuilder()
        try:
            b.RegionMode = False
            b.FileFormat = NXOpen.Gateway.ImageExportBuilder.FileFormats.Png
            b.FileName = path
            b.BackgroundOption = NXOpen.Gateway.ImageExportBuilder.BackgroundOptions.Original
            b.EnhanceEdges = True
            b.Commit()
        finally:
            b.Destroy()
        return path if os.path.isfile(path) else None
    except Exception as e:  # noqa: BLE001
        cad.log("화면 저장 실패: " + str(e))
        return None


def capture(path):
    t0 = time.time()
    got = _capture(path)
    return _out({"ok": bool(got), "image": got, "ms": {"total": _ms(t0)}})
