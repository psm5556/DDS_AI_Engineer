"""입력 덱 → case.h5의 재료·경계조건·하중(스키마 v2). 지금은 Abaqus .inp만 읽는다.

읽지 못하면 추측하지 않고 항목별로 not_extracted와 이유를 남긴다. 케이스 저장은 실패시키지 않는다.
읽는 것: *NODE *ELEMENT *NSET *ELSET *SURFACE *PART *ASSEMBLY *INSTANCE *INCLUDE, 단면(*SOLID·*SHELL·*MEMBRANE SECTION),
        *MATERIAL(*ELASTIC *DENSITY *PLASTIC *HYPERELASTIC), *STEP, *BOUNDARY *CLOAD *DLOAD *DSLOAD (OP=NEW, 다음 스텝으로 이어짐)
담지 못해 경고만 남기는 것: 접촉·구속(*COUPLING *TIE *MPC 등), AMPLITUDE(시간에 따른 크기), 그 밖의 분포 하중 종류
TODO(사내검증): 사내 Abaqus 버전·CAE가 쓴 실제 덱, 큰 덱의 시간, *PARAMETER를 쓰는 덱(지금은 not_extracted)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .h5store import SETUP_ITEMS, CaseH5, _key
from .meshops import boundary_triangles, element_family

FLAT, ASSEMBLY = "PART-1-1", "ASSEMBLY"  # 파트 없는 덱은 ODB에서도 PART-1-1 하나로 나온다
OTHER_DECKS = (".k", ".key", ".dyn", ".bdf", ".nas", ".fem")
BC_TYPES = {"ENCASTRE": (1, 2, 3, 4, 5, 6), "PINNED": (1, 2, 3), "XSYMM": (1, 5, 6), "YSYMM": (2, 4, 6), "ZSYMM": (3, 4, 5),
            "XASYMM": (2, 3, 4), "YASYMM": (1, 3, 5), "ZASYMM": (1, 2, 6)}
UNSTORED = ("COUPLING", "KINEMATIC COUPLING", "DISTRIBUTING COUPLING", "TIE", "MPC", "EQUATION", "RIGID BODY", "CONTACT", "CONTACT PAIR")


class DeckError(ValueError):
    pass


def _num(tok: str) -> float:
    try:
        return float(tok.replace("D", "E").replace("d", "e"))
    except ValueError:
        raise DeckError(f"숫자로 읽지 못했습니다: {tok}") from None


def _row(line: str) -> list[str]:
    toks = [t.strip() for t in line.split(",")]
    while toks and not toks[-1]:
        toks.pop()
    return toks


def _is_int(tok: str) -> bool:
    return tok.lstrip("+-").isdigit()


def _raw_lines(path: Path):
    """주석·빈 줄을 뺀 줄. *INCLUDE는 그 자리에 펼친다."""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("**"):
                continue
            if line.upper().startswith("*INCLUDE"):
                name = next((p.split("=", 1)[1].strip().strip('"') for p in line.split(",")[1:] if p.strip().upper().startswith("INPUT")), "")
                inc = path.parent / name
                if not inc.is_file():
                    raise DeckError(f"*INCLUDE 파일을 찾지 못했습니다: {name}")
                yield from _raw_lines(inc)
            else:
                if "<" in line and not line.startswith("*"):  # *PARAMETER 값을 풀지 않으면 숫자를 믿을 수 없다
                    bad = line[line.index("<"):].split(",")[0].strip()
                    raise DeckError(f"풀지 못한 매개변수가 있습니다: {bad}")
                yield line


def _cards(path: Path):
    """(키워드, 옵션, 데이터 줄) 묶음."""
    kw, opts, data = None, {}, []
    for line in _raw_lines(path):
        if line.startswith("*"):
            if kw:
                yield kw, opts, data
            parts = [p.strip() for p in line[1:].split(",")]
            kw, opts, data = parts[0].upper(), {}, []
            for p in parts[1:]:
                k, _, v = p.partition("=")
                opts[k.strip().upper()] = v.strip().strip('"')
        elif kw:
            data.append(line)
    if kw:
        yield kw, opts, data


@dataclass
class Scope:
    """파트 하나 또는 파트 없는 덱 전체."""
    name: str
    node_ids: list = field(default_factory=list)
    coords: list = field(default_factory=list)
    elements: dict = field(default_factory=dict)  # 형식 → (번호 목록, 연결 목록)
    nsets: dict = field(default_factory=dict)
    elsets: dict = field(default_factory=dict)
    surfaces: dict = field(default_factory=dict)
    sections: list = field(default_factory=list)  # (ELSET, 재료, 두께)


@dataclass
class StepCards:
    name: str
    boundary: list = field(default_factory=list)
    cload: list = field(default_factory=list)
    dload: list = field(default_factory=list)
    dsload: list = field(default_factory=list)


class Deck:
    def __init__(self, path: Path):
        self.flat = Scope(FLAT)
        self.parts: dict[str, Scope] = {}
        self.asm = Scope(ASSEMBLY)
        self.instances: dict[str, tuple] = {}  # 이름 → (파트, 변환 줄들)
        self.anset: dict[str, list] = {}  # 어셈블리 세트 → [(인스턴스, 번호)]
        self.aelset: dict[str, list] = {}
        self.asurf: dict[str, list] = {}
        self.materials: dict[str, dict] = {}
        self.model = StepCards("")  # 첫 스텝 전 조건
        self.steps: list[StepCards] = []
        self.unstored: dict[str, int] = {}
        self._read(path)

    # ---------- 읽기 ----------
    def _read(self, path: Path):
        scope, in_asm, in_inst, mat, step = self.flat, False, False, None, None
        for kw, opts, data in _cards(path):
            if kw != "END INSTANCE" and in_inst and kw in ("NODE", "ELEMENT"):
                raise DeckError("*INSTANCE 안의 메쉬 정의는 아직 읽지 못합니다")
            if kw == "PART":
                scope = self.parts.setdefault(opts.get("NAME", "").upper(), Scope(opts.get("NAME", "").upper()))
            elif kw == "END PART":
                scope = self.flat
            elif kw == "ASSEMBLY":
                in_asm, scope = True, self.asm
            elif kw == "END ASSEMBLY":
                in_asm, scope = False, self.flat
            elif kw == "INSTANCE":
                self.instances[opts.get("NAME", "").upper()] = (opts.get("PART", "").upper(), data)
                in_inst = True
            elif kw == "END INSTANCE":
                in_inst = False
            elif kw == "NODE":
                self._nodes(scope, opts, data)
            elif kw == "ELEMENT":
                self._elements(scope, opts, data)
            elif kw in ("NSET", "ELSET"):
                self._set(kw, scope, in_asm, opts, data)
            elif kw == "SURFACE":
                self._surface(scope, in_asm, opts, data)
            elif kw in ("SOLID SECTION", "SHELL SECTION", "SHELL GENERAL SECTION", "MEMBRANE SECTION"):
                thick = None
                if "SHELL" in kw or "MEMBRANE" in kw:
                    row = _row(data[0]) if data else []
                    thick = _num(row[0]) if row and row[0] else None
                    if "COMPOSITE" in opts:
                        self.unstored["복합 적층 두께"] = self.unstored.get("복합 적층 두께", 0) + 1
                scope.sections.append((opts.get("ELSET", "").upper(), opts.get("MATERIAL", "").upper(), thick))
            elif kw == "MATERIAL":
                mat = self.materials.setdefault(opts.get("NAME", "").upper(), {"model": "", "props": {}, "tables": {}, "warnings": []})
            elif mat is not None and kw in ("ELASTIC", "DENSITY", "PLASTIC", "HYPERELASTIC"):
                self._material(mat, kw, opts, data)
            elif kw == "STEP":
                step = StepCards(opts.get("NAME") or f"Step-{len(self.steps) + 1}")
                self.steps.append(step)
            elif kw == "END STEP":
                step = None
            elif kw in ("BOUNDARY", "CLOAD", "DLOAD", "DSLOAD"):
                getattr(step or self.model, kw.lower()).append((opts, data))
            elif kw in UNSTORED:
                self.unstored[kw] = self.unstored.get(kw, 0) + 1
            if kw not in ("ELASTIC", "DENSITY", "PLASTIC", "HYPERELASTIC", "MATERIAL"):
                mat = None

    @staticmethod
    def _nodes(scope: Scope, opts: dict, data: list):
        if "INPUT" in opts:
            raise DeckError("*NODE, INPUT= 형식은 아직 읽지 못합니다")
        labels = []
        for line in data:
            row = _row(line)
            labels.append(int(row[0]))
            xyz = [_num(t) if t else 0.0 for t in row[1:4]]
            scope.coords.append(xyz + [0.0] * (3 - len(xyz)))
        scope.node_ids.extend(labels)
        if "NSET" in opts:
            scope.nsets.setdefault(opts["NSET"].upper(), []).extend(labels)

    @staticmethod
    def _elements(scope: Scope, opts: dict, data: list):
        if "INPUT" in opts:
            raise DeckError("*ELEMENT, INPUT= 형식은 아직 읽지 못합니다")
        etype = opts.get("TYPE", "").upper()
        ids, conn = scope.elements.setdefault(etype, ([], []))
        labels, buf = [], []
        for line in data:  # 노드가 많은 요소는 쉼표로 끝나고 다음 줄로 이어진다
            buf.extend(_row(line))
            if line.rstrip().endswith(","):
                continue
            labels.append(int(buf[0]))
            conn.append([int(t) for t in buf[1:]])
            buf = []
        ids.extend(labels)
        if "ELSET" in opts:
            scope.elsets.setdefault(opts["ELSET"].upper(), []).extend(labels)

    def _set(self, kw: str, scope: Scope, in_asm: bool, opts: dict, data: list):
        name = opts.get(kw, "").upper()
        toks = []
        for line in data:
            row = _row(line)
            if "GENERATE" in opts:
                a, b, *c = [int(t) for t in row]
                toks.extend(str(i) for i in range(a, b + 1, c[0] if c else 1))
            else:
                toks.extend(row)
        if in_asm:
            inst = opts.get("INSTANCE", "").upper()
            store = self.anset if kw == "NSET" else self.aelset
            out = store.setdefault(name, [])
            for t in toks:
                if inst and _is_int(t):
                    out.append((inst, int(t)))
                else:
                    out.extend(self._resolve(t, kw, inst))
        else:
            store = scope.nsets if kw == "NSET" else scope.elsets
            own = store.setdefault(name, [])
            for t in toks:
                own.extend([int(t)] if _is_int(t) else store.get(t.upper(), []))

    def _surface(self, scope: Scope, in_asm: bool, opts: dict, data: list):
        if opts.get("TYPE", "ELEMENT").upper() != "ELEMENT":
            return  # 노드 표면은 접촉에만 쓰인다
        rows = [(r[0], r[1].upper() if len(r) > 1 else "SPOS") for r in map(_row, data)]
        (self.asurf if in_asm else scope.surfaces)[opts.get("NAME", "").upper()] = rows

    @staticmethod
    def _material(mat: dict, kw: str, opts: dict, data: list):
        rows = [[_num(t) for t in _row(x)] for x in data]
        if kw == "ELASTIC":
            kind = opts.get("TYPE", "ISOTROPIC").upper()
            mat["model"] = mat["model"] or "elastic"
            if kind == "ISOTROPIC" and rows and len(rows) == 1:
                mat["props"].update(E=rows[0][0], nu=rows[0][1] if len(rows[0]) > 1 else None)
            else:
                mat["warnings"].append(f"*ELASTIC TYPE={kind} 또는 온도별 표는 E·ν로 담지 못했습니다")
                mat["tables"]["elastic"] = rows
        elif kw == "DENSITY":
            if rows:
                mat["props"]["density"] = rows[0][0]
        else:
            mat["model"] = mat["model"] or kw.lower()
            mat["tables"][kw.lower()] = rows

    # ---------- 세트 풀기 ----------
    def _default_inst(self) -> str:
        return ASSEMBLY if self.parts or self.instances else FLAT

    def _resolve(self, ref: str, kw: str, inst: str = "") -> list[tuple[str, int]]:
        """세트 이름·번호·'인스턴스.세트' → [(인스턴스, 번호)]. 못 찾으면 오류(추측하지 않는다)."""
        ref = ref.strip().upper()
        attr, astore = ("nsets", self.anset) if kw == "NSET" else ("elsets", self.aelset)
        if _is_int(ref):
            return [(inst or self._default_inst(), int(ref))]
        if "." in ref:
            head, rest = ref.split(".", 1)
            if head in self.instances:
                inst, ref = head, rest
                if _is_int(ref):
                    return [(inst, int(ref))]
        if inst:
            part = self.parts.get(self.instances.get(inst, ("",))[0])
            if part and ref in getattr(part, attr):
                return [(inst, i) for i in getattr(part, attr)[ref]]
            raise DeckError(f"'{inst}.{ref}' 세트를 찾지 못했습니다")
        if ref in astore:
            return astore[ref]
        if ref in getattr(self.flat, attr):
            return [(FLAT, i) for i in getattr(self.flat, attr)[ref]]
        raise DeckError(f"'{ref}' 세트를 찾지 못했습니다")

    def _surface_rows(self, ref: str) -> list[tuple[str, str]]:
        ref = ref.strip().upper()
        if "." in ref and ref.split(".", 1)[0] in self.instances:
            inst, name = ref.split(".", 1)
            part = self.parts.get(self.instances[inst][0])
            if part and name in part.surfaces:
                return [(f"{inst}.{r}", f) for r, f in part.surfaces[name]]
        for store in (self.asurf, self.flat.surfaces):
            if ref in store:
                return store[ref]
        raise DeckError(f"'{ref}' 표면을 찾지 못했습니다")

    # ---------- 스텝별 상태 ----------
    def boundary_by_step(self) -> tuple[dict, list[str]]:
        """스텝 → {(인스턴스, 번호): {자유도: 값}}. 앞 스텝 조건이 이어지고 OP=NEW면 지운다."""
        warn: list[str] = []
        state: dict = {}
        out = {}
        for step in [self.model, *self.steps]:
            for opts, data in step.boundary:
                if opts.get("OP", "").upper() == "NEW":
                    state = {}
                if "AMPLITUDE" in opts:
                    warn.append(f"*BOUNDARY, AMPLITUDE={opts['AMPLITUDE']}: 시간에 따른 크기는 담지 못했습니다")
                if opts.get("TYPE", "DISPLACEMENT").upper() != "DISPLACEMENT":
                    warn.append(f"*BOUNDARY, TYPE={opts['TYPE']}: 값을 변위로 담지 않았습니다")
                velocity = opts.get("TYPE", "DISPLACEMENT").upper() != "DISPLACEMENT"
                for line in data:
                    row = _row(line)
                    if len(row) < 2:
                        raise DeckError(f"*BOUNDARY 줄을 읽지 못했습니다: {line}")
                    named = BC_TYPES.get(row[1].upper())
                    if named:
                        dofs, value = named, 0.0
                    else:
                        a = int(row[1])
                        b = int(row[2]) if len(row) > 2 and row[2] else a
                        dofs, value = range(a, b + 1), _num(row[3]) if len(row) > 3 and row[3] else 0.0
                    if velocity:
                        value = 0.0
                    if any(d > 6 for d in dofs):
                        warn.append("*BOUNDARY의 6번을 넘는 자유도(온도 등)는 담지 않았습니다")
                    for key in self._resolve(row[0], "NSET"):
                        slot = state.setdefault(key, {})
                        slot.update({d: value for d in dofs if d <= 6})
            if step.name:
                out[step.name] = dict(state)
        if not self.steps:
            out["Step-1"] = dict(state)
        return out, warn

    def loads_by_step(self) -> tuple[dict, list[str]]:
        """스텝 → {"force": {(인스턴스, 번호): {자유도: 값}}, "pressure": {(인스턴스, 요소, 면): 값}, "gravity": …, "rotation": …}."""
        warn: list[str] = []
        force: dict = {}
        pressure: dict = {}
        body: dict = {}
        out = {}
        for step in [self.model, *self.steps]:
            for opts, data in step.cload:
                if opts.get("OP", "").upper() == "NEW":
                    force = {}
                if "AMPLITUDE" in opts:
                    warn.append(f"*CLOAD, AMPLITUDE={opts['AMPLITUDE']}: 시간에 따른 크기는 담지 못했습니다")
                for line in data:
                    row = _row(line)
                    dof, value = int(row[1]), _num(row[2]) if len(row) > 2 and row[2] else 0.0
                    if dof > 6:
                        warn.append("*CLOAD의 6번을 넘는 자유도는 담지 않았습니다")
                        continue
                    for key in self._resolve(row[0], "NSET"):
                        force.setdefault(key, {})[dof] = value
            for opts, data in step.dload:
                if opts.get("OP", "").upper() == "NEW":
                    pressure, body = {}, {}
                if "AMPLITUDE" in opts:
                    warn.append(f"*DLOAD, AMPLITUDE={opts['AMPLITUDE']}: 시간에 따른 크기는 담지 못했습니다")
                for line in data:
                    self._dload_row(_row(line), pressure, body, warn)
            for opts, data in step.dsload:
                if opts.get("OP", "").upper() == "NEW":
                    pressure = {}
                for line in data:
                    row = _row(line)
                    if len(row) < 3 or row[1].upper() != "P":
                        warn.append(f"*DSLOAD {row[1] if len(row) > 1 else ''}: 압력(P) 말고는 담지 않았습니다")
                        continue
                    value = _num(row[2])
                    for ref, face in self._surface_rows(row[0]):
                        self._add_pressure(ref, face, value, pressure, warn)
            if step.name:
                out[step.name] = {"force": {k: dict(v) for k, v in force.items()}, "pressure": dict(pressure), **body}
        if not self.steps:
            out["Step-1"] = {"force": force, "pressure": pressure, **body}
        return out, warn

    def _dload_row(self, row: list[str], pressure: dict, body: dict, warn: list[str]):
        kind = row[1].upper() if len(row) > 1 else ""
        if kind == "GRAV":
            g = _num(row[2])
            body["gravity"] = [g * _num(row[i]) for i in (3, 4, 5)]
        elif kind == "CENTRIF":
            body["rotation"] = {"omega2": _num(row[2]), "point": [_num(row[i]) for i in (3, 4, 5)], "axis": [_num(row[i]) for i in (6, 7, 8)]}
        elif kind == "P" or (len(kind) == 2 and kind[0] == "P" and kind[1].isdigit()):
            self._add_pressure(row[0], "S" + kind[1:] if len(kind) == 2 else "SPOS", _num(row[2]), pressure, warn)
        else:
            warn.append(f"*DLOAD {kind}: 이 종류의 하중은 담지 않았습니다")

    def _add_pressure(self, ref: str, face: str, value: float, pressure: dict, warn: list[str]):
        if face in ("SPOS", "SNEG"):
            number, sign = 0, -1.0 if face == "SNEG" else 1.0
        elif len(face) == 2 and face[0] == "S" and face[1].isdigit():
            number, sign = int(face[1]), 1.0
        else:
            warn.append(f"면 {face}: 이 면 표기는 담지 않았습니다")
            return
        for inst, label in self._resolve(ref, "ELSET"):
            pressure[(inst, label, number)] = sign * value

    # ---------- 메쉬 ----------
    def meshes(self) -> dict:
        """인스턴스 → {"ids", "coords", "elements": {형식: (번호, 번호 연결)}, "scope"}."""
        out = {}
        if self.flat.node_ids:
            out[FLAT] = _mesh(self.flat, [])
        for inst, (part, transform) in self.instances.items():
            scope = self.parts.get(part)
            if scope is None:
                raise DeckError(f"*INSTANCE {inst}의 파트 {part}를 찾지 못했습니다")
            if scope.node_ids:
                out[inst] = _mesh(scope, transform)
        if self.asm.node_ids:
            out[ASSEMBLY] = _mesh(self.asm, [])
        return out


def _mesh(scope: Scope, transform: list[str]) -> dict:
    xyz = np.array(scope.coords, dtype=np.float64)
    for i, line in enumerate(transform[:2]):
        vals = [_num(t) for t in _row(line)]
        if i == 0:
            xyz = xyz + np.array(vals[:3])
        elif len(vals) >= 7:  # 축(두 점)과 각도[도]로 회전
            a, b, angle = np.array(vals[:3]), np.array(vals[3:6]), math.radians(vals[6])
            k = (b - a) / np.linalg.norm(b - a)
            p = xyz - a
            xyz = a + p * math.cos(angle) + np.cross(k, p) * math.sin(angle) + np.outer(p @ k, k) * (1 - math.cos(angle))
    return {"ids": np.array(scope.node_ids, dtype=np.int64), "coords": xyz, "elements": scope.elements, "scope": scope}


def _index_map(ids: np.ndarray) -> dict:
    return {int(v): i for i, v in enumerate(ids)}


def _find_part(h: CaseH5, inst: str) -> str | None:
    """같은 이름(대소문자 무시)의 메쉬 중 노드 번호가 있는 것. 번호가 없으면 대응시킬 수 없다(필드마다 저장한 표면 메쉬)."""
    for name, g in (h.f.get("mesh") or {}).items():
        if name.upper() == _key(inst).upper() and "node_ids" in g:
            return name
    return None


def _write_mesh(h: CaseH5, inst: str, m: dict) -> str:
    index = _index_map(m["ids"])
    elements, element_ids = {}, {}
    for etype, (ids, conn) in m["elements"].items():
        width = max((len(c) for c in conn), default=0)
        elements[etype] = np.array([[index.get(n, -1) for n in c] + [-1] * (width - len(c)) for c in conn], dtype=np.int64)
        element_ids[etype] = np.array(ids, dtype=np.int64)
    h.add_mesh(inst, m["coords"], node_ids=m["ids"], elements=elements, element_ids=element_ids,
               surface=boundary_triangles(elements) if elements else None)
    return _key(inst)


def _write_element_props(h: CaseH5, part: str, scope: Scope, deck: Deck, mat_index: dict):
    g = h.f.get(f"mesh/{part}/element_ids")
    if g is None or not scope.sections:
        return
    by_elem: dict[int, tuple] = {}
    for elset, material, thick in scope.sections:
        for label in scope.elsets.get(elset, []):
            by_elem[label] = (mat_index.get(material, -1), thick)
    for etype, ds in g.items():
        ids = ds[()]
        mats = [by_elem.get(int(i), (-1, None))[0] for i in ids]
        thicks = [by_elem.get(int(i), (-1, None))[1] for i in ids]
        h.set_element_props(part, etype, material=mats,
                            thickness=[t if t is not None else np.nan for t in thicks] if any(t is not None for t in thicks) else None)


def _write_boundary(h: CaseH5, by_step: dict, parts: dict) -> tuple[bool, int]:
    """돌려주는 값: (조건이 하나라도 있는가, 메쉬에서 찾지 못한 노드 수)."""
    any_bc, missing = False, 0
    for step, state in by_step.items():
        arrays: dict = {}
        for (inst, label), dofs in state.items():
            any_bc = True
            part = parts.get(inst)
            row = part["index"].get(label) if part else None
            if row is None:
                missing += 1
                continue
            mask, value = arrays.setdefault(inst, (np.zeros((part["n"], 6), np.uint8), np.zeros((part["n"], 6))))
            for d, v in dofs.items():
                mask[row, d - 1], value[row, d - 1] = 1, v
        for inst, (mask, value) in arrays.items():
            h.add_boundary(step, parts[inst]["name"], mask, value)
    return any_bc, missing


def _write_loads(h: CaseH5, by_step: dict, parts: dict) -> tuple[bool, int, list[str]]:
    any_load, missing, unknown = False, 0, []
    for step, st in by_step.items():
        forces: dict = {}
        for (inst, label), dofs in st["force"].items():
            any_load = True
            part = parts.get(inst)
            row = part["index"].get(label) if part else None
            if row is None:
                missing += 1
                continue
            f = forces.setdefault(inst, np.zeros((part["n"], 6)))
            for d, v in dofs.items():
                f[row, d - 1] = v
        for inst, f in forces.items():
            h.add_nodal_force(step, parts[inst]["name"], f)
        rows: dict = {}
        for (inst, label, face), value in st["pressure"].items():
            any_load = True
            part = parts.get(inst)
            where = part["elements"].get(label) if part else None
            if where is None:
                missing += 1
                continue
            etype, row = where
            if element_family(etype) is None and etype not in unknown:
                unknown.append(etype)
            rows.setdefault((inst, etype), []).append([row, face, value])
        for (inst, etype), r in rows.items():
            h.add_pressure(step, parts[inst]["name"], etype, r)
        if st.get("gravity") or st.get("rotation"):
            any_load = True
            h.set_body_load(step, gravity=st.get("gravity"), rotation=st.get("rotation"))
    return any_load, missing, unknown


def _fail_all(h: CaseH5, source: str, reason: str):
    for item in SETUP_ITEMS:
        h.set_extraction(item, "not_extracted", source, reason)


def write_setup(h: CaseH5, path: Path):
    """.inp 하나를 읽어 case.h5에 쓴다. 항목마다 따로 판정하므로 한 항목이 실패해도 나머지는 남는다."""
    src = path.name
    deck = Deck(path)
    meshes = deck.meshes()
    h.clear_setup()
    parts: dict = {}
    for inst, m in meshes.items():
        name = _find_part(h, inst) or _write_mesh(h, inst, m)
        g = h.f[f"mesh/{name}"]
        node_ids = g["node_ids"][()]
        elements = {}
        for etype, ds in (g.get("element_ids") or {}).items():
            for row, label in enumerate(ds[()]):
                elements[int(label)] = (etype, row)
        parts[inst] = {"name": name, "n": len(node_ids), "index": _index_map(node_ids), "elements": elements, "scope": m["scope"]}

    mat_index = {name: i for i, name in enumerate(sorted(deck.materials))}
    mat_warn = [w for m in deck.materials.values() for w in m["warnings"]]
    for name, i in mat_index.items():
        m = deck.materials[name]
        h.add_material(name, model=m["model"], props=m["props"], tables=m["tables"], index=i)
    for part in parts.values():
        _write_element_props(h, part["name"], part["scope"], deck, mat_index)
    h.set_extraction("materials", "ok" if mat_index else "none", src, warnings=mat_warn)

    unstored = [f"*{k} {n}개는 입력에 담지 않았습니다" for k, n in deck.unstored.items()]
    try:
        by_step, warn = deck.boundary_by_step()
        found, missing = _write_boundary(h, by_step, parts)
        if missing:
            h.set_extraction("boundary", "not_extracted", src, f"경계조건의 노드 {missing}개를 메쉬에서 찾지 못했습니다")
        else:
            h.set_extraction("boundary", "ok" if found else "none", src, warnings=sorted(set(warn + unstored)))
    except DeckError as e:
        h.set_extraction("boundary", "not_extracted", src, str(e))

    try:
        by_step, warn = deck.loads_by_step()
        found, missing, unknown = _write_loads(h, by_step, parts)
        if missing:
            h.set_extraction("loads", "not_extracted", src, f"하중의 노드·요소 {missing}개를 메쉬에서 찾지 못했습니다")
        elif unknown:
            h.set_extraction("loads", "not_extracted", src, f"압력을 노드로 옮길 수 없는 요소 형식입니다: {', '.join(unknown)}")
        else:
            h.set_extraction("loads", "ok" if found else "none", src, warnings=sorted(set(warn)))
    except DeckError as e:
        h.set_extraction("loads", "not_extracted", src, str(e))


def _find_deck(run_dir: Path) -> Path | None:
    """케이스 폴더의 .inp 중 가장 큰 것."""
    found = list(run_dir.glob("*.inp")) or list(run_dir.glob("*/*.inp"))
    return max(found, key=lambda p: p.stat().st_size) if found else None


def extract_setup(h: CaseH5, run_dir: Path):
    """pack_case에서 부른다. 어떤 경우에도 예외를 밖으로 내지 않는다(추출 실패로 케이스 저장이 실패하면 안 된다).
    덱이 없으면 플랫폼 스크립트가 직접 써 둔 상태는 그대로 두고, 기록이 없는 항목만 이유를 적는다."""
    deck = _find_deck(run_dir)
    if deck is None:
        other = sorted({p.suffix.lower() for p in run_dir.iterdir() if p.suffix.lower() in OTHER_DECKS}) if run_dir.is_dir() else []
        reason = f"이 형식({', '.join(other)})의 입력 덱 추출기는 아직 없습니다" if other else "케이스 폴더에서 입력 덱(.inp)을 찾지 못했습니다"
        have = h.manifest().get("extraction", {})
        for item in SETUP_ITEMS:
            if item not in have:
                h.set_extraction(item, "not_extracted", "", reason)
        return
    try:
        write_setup(h, deck)
    except DeckError as e:
        _fail_all(h, deck.name, str(e))
    except Exception as e:  # noqa: BLE001 — 덱의 어떤 문제든 저장은 계속한다
        _fail_all(h, deck.name, f"입력 덱을 읽지 못했습니다: {e}")
