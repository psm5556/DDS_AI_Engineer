"""NX 세션 안 도우미(agent-context/live/nx/ddslive_nx_ops.py) 테스트 — 가짜 NXOpen으로 논리만 확인한다.
실제 NXOpen API 이름은 사내에서 확인한다(docs/onsite-validation.md 1-1절). 여기서는
값 한꺼번에 바꾸기·업데이트 1번·실패하면 되돌리기·다른 파트의 expression·화면 파트 복귀·요약을 본다."""
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = ROOT / "agent-context" / "live" / "nx"
LOADED = ("ddslive_nx_ops", "nx_param_update", "nx_inspect_part", "sc_update_solve")


class Expr:
    def __init__(self, name, value, rhs=None):
        self.Name, self.Value, self.Type, self.Units = name, value, "Number", None
        self.RightHandSide = rhs if rhs is not None else repr(value)


class Exprs(list):
    def FindObject(self, name):
        for e in self:
            if e.Name == name:
                return e
        raise RuntimeError("없음: " + name)

    def EditWithUnits(self, e, units, rhs):
        e.RightHandSide, e.Value = rhs, float(rhs)


class Part:
    def __init__(self, leaf, exprs):
        self.Leaf, self.FullPath = leaf, "C:/m/" + leaf
        self.Expressions = Exprs(Expr(*x) for x in exprs)
        self.Bodies, self.Features = [], []


class Session:
    def __init__(self, parts, display):
        self.parts, self.marks, self.n_update = parts, [], 0
        self.fail_update = 0
        self.display_log = []
        s = self

        class Parts(list):
            Work = Display = display

            def SetActiveDisplay(self, part, a, b):
                self.Work = self.Display = part
                s.display_log.append(part.Leaf)

        class UpdateManager:
            class ErrorList:
                Length = 1

                @staticmethod
                def GetErrorInfo(i):
                    return types.SimpleNamespace(Description="형상 재생성 실패")

            @staticmethod
            def DoUpdate(mark):
                s.n_update += 1
                return s.fail_update

        self.Parts = Parts(parts)
        self.UpdateManager = UpdateManager

    def SetUndoMark(self, vis, name):
        # 되돌리기용: 마크 시점의 모든 expression 값을 기억한다
        self.marks.append((name, {(p.Leaf, e.Name): (e.Value, e.RightHandSide) for p in self.parts for e in p.Expressions}))
        return len(self.marks) - 1

    def UndoToMark(self, mark, name):
        snap = self.marks[mark][1]
        for p in self.parts:
            for e in p.Expressions:
                e.Value, e.RightHandSide = snap[(p.Leaf, e.Name)]


@pytest.fixture()
def nx(monkeypatch):
    nxopen = types.ModuleType("NXOpen")
    for sub in ("UF", "CAE", "Assemblies", "Gateway"):
        m = types.ModuleType("NXOpen." + sub)
        setattr(nxopen, sub, m)
        monkeypatch.setitem(sys.modules, "NXOpen." + sub, m)
    ns = types.SimpleNamespace
    nxopen.Session = ns(GetSession=lambda: state["session"], MarkVisibility=ns(Visible=1, Invisible=0))
    nxopen.DisplayPartOption = ns(AllowAdditional=0)
    nxopen.PartDisplayPartWorkPartOption = ns(UseLast=0)
    monkeypatch.setitem(sys.modules, "NXOpen", nxopen)
    monkeypatch.syspath_prepend(str(OPS_DIR))
    for name in LOADED:
        sys.modules.pop(name, None)
    state = {}
    import ddslive_nx_ops as ops
    state["ops"] = ops
    yield state
    for name in LOADED:
        sys.modules.pop(name, None)


def test_set_params_one_update_and_undo(nx):
    cad = Part("bracket.prt", [("thickness", 3.0), ("rib_h", 10.0)])
    nx["session"] = s = Session([cad], cad)
    r = json.loads(nx["ops"].set_params({"thickness": 4.0, "rib_h": 12.0}))
    assert r["ok"] and s.n_update == 1  # 값 두 개, 업데이트는 한 번
    assert [(c["name"], c["old"], c["new"]) for c in r["changed"]] == [("thickness", 3.0, 4.0), ("rib_h", 10.0, 12.0)]
    assert "thickness 3.0→4.0" in r["undo_label"] and "update" in r["ms"]
    assert json.loads(nx["ops"].undo())["ok"]
    assert cad.Expressions.FindObject("thickness").Value == 3.0


def test_missing_name_changes_nothing(nx):
    cad = Part("bracket.prt", [("thickness", 3.0)])
    nx["session"] = s = Session([cad], cad)
    r = json.loads(nx["ops"].set_params({"thickness": 4.0, "thikness": 5.0}))
    assert not r["ok"] and r["missing"] == ["thikness"] and s.n_update == 0
    assert cad.Expressions.FindObject("thickness").Value == 3.0


def test_failed_update_is_rolled_back(nx):
    cad = Part("bracket.prt", [("thickness", 3.0)])
    nx["session"] = s = Session([cad], cad)
    s.fail_update = 1
    r = json.loads(nx["ops"].set_params({"thickness": 0.0}))
    assert not r["ok"] and r["undone"] and "형상 재생성 실패" in r["error"]
    assert cad.Expressions.FindObject("thickness").Value == 3.0


def test_expression_in_other_part_returns_to_display(nx):
    cad = Part("bracket.prt", [("thickness", 3.0)])
    fem = Part("bracket_fem1.fem", [("mesh_size", 5.0)])
    sim = Part("bracket_sim1.sim", [("force_n", 100.0)])
    nx["session"] = s = Session([cad, fem, sim], sim)
    r = json.loads(nx["ops"].set_params({"mesh_size": 3.0, "force_n": 200.0}))
    assert r["ok"] and s.n_update == 2  # 파트마다 한 번
    assert s.display_log == ["bracket_fem1.fem", "bracket_sim1.sim"]  # FEM에서 고치고 sim 화면으로 복귀
    assert s.Parts.Display is sim


def test_summary_hides_auto_named(nx):
    cad = Part("bracket.prt", [("thickness", 3.0), ("p12", 1.0), ("rib_h", 10.0, "thickness*3")])
    nx["session"] = Session([cad], cad)
    r = json.loads(nx["ops"].summary())
    assert [e["name"] for e in r["expressions"]] == ["thickness", "rib_h"]
    assert r["auto_named_expressions"] == 1 and r["expressions"][1]["constant"] is False
    assert r["display_part"]["kind"] == "cad"
    assert len(json.loads(nx["ops"].summary(all_expressions=True))["expressions"]) == 3
