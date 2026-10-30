"""case.h5 → train.h5(학습 샘플, 스키마 dds-train-h5 v1). 케이스 하나 = 샘플 하나. docs/cae-data-hdf5.md 8절.

case.h5는 손실 없는 보관본이고, train.h5는 학습에 필요한 것만 담는다: 변형 전 형상(입력), 노드 기준 출력,
노드별 입력(고정·강제 변위·하중), 조건. 실수는 float32 + gzip. 단위는 mm·N·MPa로 맞춘다(모르면 그대로 두고 표시).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np

from .h5store import CaseH5, _key, _text, file_sha256
from .meshops import boundary_triangles, pressure_to_nodal, vertex_normals

FORMAT, SCHEMA = "dds-train-h5", 1
SAMPLE_NAME = "train.h5"
# 표준 이름 ← 툴마다 다른 이름(소문자). lasso의 node_displacement는 변위가 아니라 현재 좌표라 넣지 않는다(coordinates로 처리)
STANDARD = {
    "displacement": {"u", "disp", "displacement", "displacements"},
    "stress": {"s", "stresses", "element_solid_stress", "element_shell_stress", "element_tshell_stress"},
    "von_mises": {"mises", "von_mises", "vonmises", "stress"},  # 성분 없는 'stress'는 기존 fields/ 규약의 Mises
    "strain": {"e", "le", "strain", "strains", "element_solid_strain", "element_shell_strain"},
    "plastic_strain": {"peeq", "eps", "element_solid_effective_plastic_strain", "element_shell_effective_plastic_strain"},
    "temperature": {"nt11", "temperature", "temp"},
}
NODE_LOCATIONS = {"node", "nodal", "element_nodal"}
ELEMENT_LOCATIONS = {"element", "centroid", "integration_point", "ip", "element_node"}
LENGTH = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
FORCE = {"n": 1.0, "kn": 1000.0, "mn": 1e6, "lbf": 4.44822}
STRESS = {"mpa": 1.0, "n/mm2": 1.0, "pa": 1e-6, "kpa": 1e-3, "gpa": 1000.0, "psi": 0.00689476}
TARGET_KIND = {"displacement": "length", "stress": "stress", "von_mises": "stress"}


def spec_hash(spec: dict) -> str:
    """같은 조건이면 같은 값(샘플 재사용). 목록 순서는 상관없다."""
    norm = {k: (sorted(v) if k == "targets" and v else v) for k, v in (spec or {}).items() if v is not None}
    return hashlib.sha256(json.dumps(norm, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]


def _scale(table: dict, unit: str | None) -> float | None:
    return table.get(str(unit).strip().lower()) if unit else None


def _standard_name(name: str) -> str | None:
    low = name.lower()
    return next((std for std, names in STANDARD.items() if low in names), None)


def _pick_frames(n: int, frames) -> list[int]:
    if frames in (None, "last") or n <= 1:
        return [n - 1]
    if frames == "all":
        return list(range(n))
    k = min(int(frames), n)
    return sorted({int(round(x)) for x in np.linspace(0, n - 1, k)})


def _mises(a: np.ndarray, comps: list[str]) -> np.ndarray:
    """(n, C) → (n,). 6성분(S11 S22 S33 + 전단 3) 또는 성분 이름이 있는 평면 응력(S11 S22 S12 등)."""
    if a.shape[1] == 6:
        sxx, syy, szz, t1, t2, t3 = (a[:, i] for i in range(6))
    else:
        names = [c.upper() for c in comps]
        pick = lambda k: a[:, names.index(k)] if k in names else np.zeros(len(a))  # noqa: E731
        sxx, syy, szz, t1, t2, t3 = pick("S11"), pick("S22"), pick("S33"), pick("S12"), pick("S13"), pick("S23")
        if not {"S11", "S22"} <= set(names):
            raise ValueError(f"Mises를 계산할 성분이 없습니다: {comps}")
    return np.sqrt(np.maximum(0.0, 0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2) + 3 * (t1 ** 2 + t2 ** 2 + t3 ** 2)))


class _NodalMap:
    """필드의 행 → 노드. 요소·적분점 값은 요소 평균 후 노드 평균으로 옮긴다."""

    def __init__(self, mesh, ds, labels, part_nodes: int):
        self.n = part_nodes
        loc = _text(ds.attrs.get("location", "node"))
        n_rows = ds.shape[1]
        self.unmapped = 0
        if loc in NODE_LOCATIONS:
            if labels is None:
                if n_rows != part_nodes:
                    raise ValueError(f"노드 번호가 없고 행 수({n_rows})가 노드 수({part_nodes})와 다릅니다")
                self.rows, self.nodes = np.arange(n_rows), np.arange(n_rows)
            else:
                ids = mesh["node_ids"][()] if "node_ids" in mesh else None
                if ids is None:
                    raise ValueError("노드 번호(node_ids)가 없는 메쉬입니다")
                lab = np.asarray(labels).reshape(n_rows, -1)[:, 0]
                index = {int(v): i for i, v in enumerate(ids)}
                found = np.array([index.get(int(x), -1) for x in lab])
                self.unmapped = int((found < 0).sum())
                self.rows, self.nodes = np.flatnonzero(found >= 0), found[found >= 0]
        elif loc in ELEMENT_LOCATIONS:
            self._from_elements(mesh, ds, labels, n_rows)
        else:
            raise ValueError(f"위치 {loc}는 노드로 옮길 수 없습니다")
        self.count = np.bincount(self.nodes, minlength=self.n).astype(np.float64)
        self.mask = self.count > 0

    def _from_elements(self, mesh, ds, labels, n_rows: int):
        conns = {k: v[()] for k, v in (mesh.get("elements") or {}).items()}
        ids = {k: v[()] for k, v in (mesh.get("element_ids") or {}).items()}
        if not conns:
            raise ValueError("요소 값을 노드로 옮길 요소 연결이 없습니다")
        table: dict[int, np.ndarray] = {}
        for etype, conn in conns.items():
            for label, row in zip(ids.get(etype, np.arange(len(conn))), conn):
                table[int(label)] = row[row >= 0]
        if labels is None:  # LS-DYNA: 번호 없이 요소 순서대로. 행 수가 맞는 형식을 쓴다
            name = str(ds.name).rsplit("/", 1)[-1].lower()
            match = [k for k in conns if len(conns[k]) == n_rows and (k.lower() in name or len(conns) == 1)]
            if not match:
                raise ValueError(f"요소 번호가 없고 행 수({n_rows})가 맞는 요소 형식이 없습니다")
            lab = ids.get(match[0], np.arange(n_rows))
        else:
            lab = np.asarray(labels).reshape(n_rows, -1)[:, 0]
        rows, nodes = [], []
        for r, label in enumerate(lab):
            conn = table.get(int(label))
            if conn is None:
                self.unmapped += 1
                continue
            rows.append(np.full(len(conn), r))
            nodes.append(conn)
        self.rows = np.concatenate(rows) if rows else np.zeros(0, int)
        self.nodes = np.concatenate(nodes) if nodes else np.zeros(0, int)

    def apply(self, values: np.ndarray) -> np.ndarray:
        """(rows, C) → (노드, C). 값이 없는 노드는 NaN."""
        out = np.zeros((self.n, values.shape[1]), dtype=np.float64)
        for c in range(values.shape[1]):
            np.add.at(out[:, c], self.nodes, values[self.rows, c])
        out[self.mask] /= self.count[self.mask, None]
        out[~self.mask] = np.nan
        return out


def _flatten_tensor(frame: np.ndarray, comps: list[str]) -> np.ndarray:
    """LS-DYNA처럼 층·적분점 × 6성분을 펼친 값이면 층 평균으로 6성분을 만든다."""
    if frame.shape[1] > 6 and frame.shape[1] % 6 == 0 and len(comps) != frame.shape[1]:
        return frame.reshape(len(frame), -1, 6).mean(axis=1)
    return frame


def _select_fields(f, spec: dict) -> tuple[str, dict]:
    """스텝과 {표준 이름: [(데이터셋, 원래 이름, 라벨)]}. 스텝을 안 정했으면 원하는 출력이 가장 많은 스텝(같으면 뒤쪽)."""
    wanted = spec.get("targets") or None
    steps = f.get("steps") or {}
    best, best_score = None, -1
    for step, g in steps.items():
        if spec.get("step") and step.lower() != str(spec["step"]).lower():
            continue
        fields = g.get("fields") or {}
        names = {_standard_name(_text(ds.attrs.get("base_name", n))) for n, ds in fields.items()}
        bases = {_text(ds.attrs.get("base_name", n)).lower() for n, ds in fields.items()}
        if "stress" in names:
            names.add("von_mises")
        if "coordinates" in bases:
            names.add("displacement")
        score = len([w for w in wanted if w.lower() in names or w.lower() in bases]) if wanted else len(names - {None})
        if score >= best_score and fields:
            best, best_score = step, score
    if best is None:
        raise ValueError("case.h5에 학습할 필드가 없습니다" + (f": {', '.join(wanted)}" if wanted else "")
                         + " → 케이스 스크립트가 fields/ 또는 h5in/에 결과를 남기는지 확인합니다")
    out: dict[str, list] = {}
    g = steps[best]
    labels = g.get("labels") or {}
    for key, ds in (g.get("fields") or {}).items():
        base = _text(ds.attrs.get("base_name", key))
        loc = _text(ds.attrs.get("location", "node"))
        if loc == "global":
            continue
        std = _standard_name(base)
        label = labels[key][()] if key in labels else None
        if base.lower() == "coordinates":
            out.setdefault("__coordinates", []).append((ds, base, label))
        elif std:
            out.setdefault(std, []).append((ds, base, label))
        out.setdefault(f"__raw:{base.lower()}", []).append((ds, base, label))
    return best, out


def _target_sources(picked: dict, spec: dict) -> dict:
    """표준 이름(또는 요청한 원래 이름) → 소스 목록. 'von_mises'는 Mises 필드가 없으면 응력 성분에서 만든다."""
    wanted = spec.get("targets")
    has_stress = "stress" in picked
    if not wanted:
        names = [k for k in picked if not k.startswith("__") and k != "stress"]
        if has_stress and "von_mises" not in names:
            names.append("von_mises")
        if "displacement" not in names and "__coordinates" in picked:
            names.append("displacement")
        wanted = sorted(set(names))
    out, missing = {}, []
    for w in wanted:
        low = w.lower()
        if low in picked and not low.startswith("__"):
            out[low] = picked[low]
        elif low == "von_mises" and has_stress:
            out[low] = picked["stress"]
        elif low == "displacement" and "__coordinates" in picked:
            out[low] = picked["__coordinates"]
        elif f"__raw:{low}" in picked:
            out[low] = picked[f"__raw:{low}"]
        else:
            missing.append(w)
    if missing:
        have = sorted({k[6:] for k in picked if k.startswith("__raw:")})
        raise ValueError(f"요청한 출력이 case.h5에 없습니다: {', '.join(missing)} (있는 필드: {', '.join(have) or '없음'})")
    return out


def _geometry_parts(f, sources: dict) -> tuple[list[str], str, list[str]]:
    """샘플에 넣을 파트. 노드 번호가 있는 메쉬를 모두 합친다. 없으면(기존 fields/ 규약) 필드의 표면 메쉬를 쓴다."""
    mesh = f.get("mesh") or {}
    ided = [k for k, g in mesh.items() if "node_ids" in g and "nodes" in g]
    used = {_text(ds.attrs.get("part", "")) for src in sources.values() for ds, _, _ in src}
    skipped: list[str] = []
    if ided and used & set(ided):
        for part in sorted(used - set(ided)):
            skipped.append(f"파트 {part}의 필드: 노드 번호가 없어 메쉬에 맞출 수 없습니다")
        return ided, "mesh", skipped
    parts = [p for p in sorted(used) if p in mesh and "nodes" in mesh[p]]
    if not parts:
        raise ValueError("필드의 메쉬(/mesh/<part>/nodes)가 case.h5에 없습니다")
    first = parts[0]
    for p in parts[1:]:
        skipped.append(f"파트 {p}의 필드: 메쉬가 {first}와 다른지 확인할 수 없어 뺐습니다")
    return [first], "field_points", skipped


def build_sample(case_h5: str | Path, out: str | Path, spec: dict | None = None, compression: str = "gzip", level: int = 4) -> dict:
    """case.h5에서 학습 샘플을 만든다. spec: {"targets": [...], "frames": "last"|N|"all", "step": 이름}. 돌려주는 값: 샘플 정보.
    요청한 출력이 없으면 ValueError(이유를 화면에 보여 준다)."""
    import h5py

    spec = spec or {}
    out = Path(out)
    with CaseH5(case_h5, "r") as h, h5py.File(out, "w") as t:
        f = h.f
        step, picked = _select_fields(f, spec)
        sources = _target_sources(picked, spec)
        parts, geometry_source, skipped = _geometry_parts(f, sources)
        units = h.units()
        ls, fs, ss = _scale(LENGTH, units.get("length")), _scale(FORCE, units.get("force")), _scale(STRESS, units.get("stress"))
        known = None not in (ls, fs, ss)
        comp = {"compression": compression, "compression_opts": level, "shuffle": True} if compression == "gzip" else {}

        # 형상: 파트를 이어 붙인다. 변형 좌표가 아니라 변형 전 좌표(입력에 답이 섞이지 않게)
        offsets, start, nodes, surfaces = {}, 0, [], []
        for part in parts:
            g = f[f"mesh/{part}"]
            xyz = g["nodes"][()].astype(np.float64)
            offsets[part] = (start, len(xyz))
            nodes.append(xyz)
            tris = g["surface"][()] if "surface" in g else boundary_triangles({k: v[()] for k, v in (g.get("elements") or {}).items()})
            if len(tris):
                surfaces.append(np.asarray(tris, dtype=np.int64) + start)
            start += len(xyz)
        raw_nodes = np.vstack(nodes)
        total = len(raw_nodes)
        tris = np.vstack(surfaces) if surfaces else np.zeros((0, 3), np.int64)
        mm = raw_nodes * (ls if known else 1.0)
        t.create_dataset("geometry/nodes", data=mm.astype(np.float32), chunks=True, **comp)
        t.create_dataset("geometry/surface", data=tris.astype(np.int32), **(comp if len(tris) else {}))
        t.create_dataset("geometry/normals", data=vertex_normals(mm, tris).astype(np.float32), chunks=True, **comp)
        _node_attrs(t, f, parts, offsets, total, known, ls, ss, comp)

        info_targets, nan_nodes, unmapped = {}, {}, 0
        times = None
        for name, src in sources.items():
            res = _write_target(t, f, name, src, parts, offsets, total, spec.get("frames"), known, ls, ss, comp, raw_nodes, skipped)
            if res is None:
                continue
            shape, time, nan_count, lost = res
            info_targets[name] = shape
            nan_nodes[name] = nan_count
            unmapped += lost
            times = time if times is None or len(time) > len(times) else times
        if not info_targets:
            raise ValueError("노드로 옮길 수 있는 출력이 없습니다: " + "; ".join(skipped))
        t.create_dataset("targets/time", data=np.asarray(times, dtype=np.float64))

        extraction = {k: v["status"] for k, v in h.extraction().items()}
        inputs = _write_inputs(t, f, step, parts, offsets, total, raw_nodes, extraction, known, ls, fs, comp)
        meta = f.get("meta")
        c = t.require_group("conditions")
        for k, v in (meta.attrs.items() if meta is not None else []):
            if k.startswith(("params.", "results.")):
                c.attrs[k] = v
        c.attrs["extraction"] = json.dumps(extraction)
        body = _body_group(f, step)
        if body is not None and extraction["loads"] == "ok":
            for k in ("gravity", "rotation"):
                if k in body.attrs:
                    c.attrs[f"global.{k}"] = _text(body.attrs[k])
        qc = t.require_group("qc")
        qc.attrs.update({"nodes": total, "unmapped_rows": unmapped, "nan_nodes": json.dumps(nan_nodes),
                         "skipped": json.dumps(skipped, ensure_ascii=False)})
        t.attrs.update({"format": FORMAT, "schema": SCHEMA, "case_id": _attr(f, "case_id"), "study_id": _attr(f, "study_id"),
                        "project_uid": str(_attr(f, "project_uid", "")), "tool": str(_attr(f, "tool", _attr(f, "adapter", ""))),
                        "step": step, "geometry_source": geometry_source, "units_known": bool(known),
                        "length_unit": "mm" if known else "unknown", "force_unit": "N" if known else "unknown",
                        "stress_unit": "MPa" if known else "unknown"})
    return {"format": FORMAT, "schema": SCHEMA, "step": step, "nodes": total, "frames": int(len(times)), "targets": info_targets,
            "inputs": inputs, "extraction": extraction, "units_known": bool(known), "geometry_source": geometry_source,
            "qc": {"nan_nodes": nan_nodes, "unmapped_rows": unmapped, "skipped": skipped}, "size": out.stat().st_size, "sha256": file_sha256(out)}


def _attr(f, key: str, default=-1):
    v = f.attrs.get(key, default)
    return _text(v) if isinstance(v, bytes) else v


def _body_group(f, step: str):
    match = _setup_step(f, "loads", step)
    return f.get(f"loads/{match}/body") if match else None


def _setup_step(f, section: str, step: str) -> str | None:
    """필드의 스텝에 맞는 입력 정보의 스텝. 같은 이름(대소문자 무시)이 없으면(기존 규약의 'final' 등) 마지막 스텝."""
    g = f.get(section)
    if g is None or not len(g):
        return None
    names = list(g)
    return next((n for n in names if n.lower() == step.lower()), names[-1])


def _write_target(t, f, name, src, parts, offsets, total, frames, known, ls, ss, comp, raw_nodes, skipped):
    """출력 하나를 프레임별로 나눠 쓴다(큰 모델도 메모리에 다 올리지 않는다)."""
    usable = []
    for ds, base, label in src:
        part = _text(ds.attrs.get("part", ""))
        if part not in offsets:
            continue
        try:
            usable.append((ds, base, part, _NodalMap(f[f"mesh/{part}"], ds, label, offsets[part][1])))
        except ValueError as e:
            skipped.append(f"{name}({part}): {e}")
    if not usable:
        return None
    n_frames = min(ds.shape[0] for ds, *_ in usable)
    picks = _pick_frames(n_frames, frames)
    step_group = usable[0][0].parent.parent
    all_time = step_group["time"][()] if "time" in step_group else np.arange(n_frames, dtype=np.float64)
    time = np.asarray([all_time[i] if i < len(all_time) else i for i in picks])

    kind = TARGET_KIND.get(name)
    scale = 1.0
    if known:
        scale = ls if kind == "length" else ss if kind == "stress" else 1.0
    position = usable[0][1].lower() == "coordinates"
    data = None
    lost = sum(m.unmapped for *_, m in usable)
    for out_f, fi in enumerate(picks):
        row_vals = None
        for ds, base, part, nmap in usable:
            comps = json.loads(_text(ds.attrs.get("components", "[]")) or "[]")
            frame = np.asarray(ds[fi], dtype=np.float64)
            frame = frame.reshape(len(frame), -1)
            frame = _flatten_tensor(frame, comps)
            nodal = nmap.apply(frame)  # 성분을 먼저 평균하고 Mises를 계산한다
            if name == "von_mises" and nodal.shape[1] > 1:
                good = ~np.isnan(nodal[:, 0])
                vm = np.full((len(nodal), 1), np.nan)
                vm[good, 0] = _mises(nodal[good], comps)
                nodal = vm
            elif name == "displacement":
                if position:  # 현재 좌표 - 초기 좌표
                    start, n = offsets[part]
                    nodal = nodal - raw_nodes[start:start + n]
                nodal = nodal[:, :3]
            if row_vals is None:
                row_vals = np.full((total, nodal.shape[1]), np.nan, dtype=np.float64)
            start, n = offsets[part]
            if nodal.shape[1] != row_vals.shape[1]:
                skipped.append(f"{name}({part}): 성분 수가 다른 파트와 달라 뺐습니다")
                continue
            row_vals[start:start + n] = nodal
        if data is None:
            data = t.create_dataset(f"targets/{_key(name)}", shape=(len(picks), total, row_vals.shape[1]), dtype=np.float32,
                                    chunks=(1, total, row_vals.shape[1]), **comp)
        data[out_f] = (row_vals * scale).astype(np.float32)
    data.attrs.update({"unit": ("mm" if kind == "length" else "MPa" if kind == "stress" else _text(usable[0][0].attrs.get("unit", ""))) if known else "unknown",
                       "source_field": usable[0][1], "components": _text(usable[0][0].attrs.get("components", "[]"))})
    nan_count = int(np.isnan(data[-1]).all(axis=1).sum())
    return [len(picks), total, data.shape[2]], time, nan_count, lost


def _node_attrs(t, f, parts, offsets, total, known, ls, ss, comp):
    """노드별 형상 속성: 파트 번호, 두께, 재료 번호·탄성계수·밀도(요소 값을 노드로 옮긴 것)."""
    part_id = np.zeros(total, np.uint16)
    thick_sum, thick_n = np.zeros(total), np.zeros(total)
    mat_id = np.full(total, -1, np.int32)
    mats = f.get("materials") or {}
    by_index = {int(g.attrs["index"]): g for g in mats.values() if "index" in g.attrs}
    for i, part in enumerate(parts):
        start, n = offsets[part]
        part_id[start:start + n] = i
        g = f[f"mesh/{part}"]
        for etype, ds in (g.get("elements") or {}).items():
            conn = ds[()]
            valid = conn >= 0
            m = g.get(f"element_material/{etype}")
            if m is not None:
                vals = np.repeat(m[()], valid.sum(axis=1))
                np.maximum.at(mat_id, conn[valid] + start, vals)  # 파트 경계 노드는 번호가 큰 재료로 정한다
            th = g.get(f"element_thickness/{etype}")
            if th is not None:
                vals = np.repeat(th[()], valid.sum(axis=1))
                ok = ~np.isnan(vals)
                np.add.at(thick_sum, conn[valid][ok] + start, vals[ok])
                np.add.at(thick_n, conn[valid][ok] + start, 1.0)
    a = t.require_group("geometry/node_attrs")
    a.create_dataset("part_id", data=part_id, chunks=True, **comp)
    if (mat_id >= 0).any():
        a.create_dataset("material_id", data=mat_id, chunks=True, **comp)
        for key, out_name, scale in (("E", "youngs_modulus", ss if known else 1.0), ("density", "density", 1.0)):
            table = np.array([float(by_index[i].attrs[key]) * scale if i in by_index and key in by_index[i].attrs else np.nan
                              for i in range(max(by_index, default=-1) + 1)] + [np.nan])
            vals = table[mat_id]  # -1은 맨 뒤 NaN
            if not np.isnan(vals).all():
                a.create_dataset(out_name, data=vals.astype(np.float32), chunks=True, **comp)
    if thick_n.any():
        th = np.divide(thick_sum, thick_n, out=np.full(total, np.nan), where=thick_n > 0) * (ls if known else 1.0)
        a.create_dataset("thickness", data=th.astype(np.float32), chunks=True, **comp)


def _write_inputs(t, f, step, parts, offsets, total, raw_nodes, extraction, known, ls, fs, comp) -> list[str]:
    """추출한 입력만 쓴다. ok는 값, none은 0(조건이 없다는 사실), not_extracted는 쓰지 않는다(모르는 것을 0으로 두지 않는다)."""
    out = []
    length = (ls if known else 1.0)
    force = (fs if known else 1.0)
    if extraction["boundary"] in ("ok", "none"):
        mask, value = np.zeros((total, 6), np.uint8), np.zeros((total, 6), np.float64)
        match = _setup_step(f, "boundary", step)
        for part in parts:
            g = f.get(f"boundary/{match}/{part}") if match else None
            if g is not None:
                start, n = offsets[part]
                mask[start:start + n], value[start:start + n] = g["fixed_mask"][()], g["prescribed_u"][()]
        value[:, :3] *= length  # 회전 자유도는 라디안 그대로
        t.create_dataset("inputs/fixed_mask", data=mask, chunks=True, **comp)
        t.create_dataset("inputs/prescribed_u", data=value.astype(np.float32), chunks=True, **comp)
        out += ["fixed_mask", "prescribed_u"]
    if extraction["loads"] in ("ok", "none"):
        load = np.zeros((total, 6), np.float64)
        match = _setup_step(f, "loads", step)
        for part in parts:
            g = f.get(f"loads/{match}/{part}") if match else None
            if g is None:
                continue
            start, n = offsets[part]
            if "nodal_force" in g:
                load[start:start + n] = g["nodal_force"][()]
            mesh = f[f"mesh/{part}"]
            for etype, rows in (g.get("pressure") or {}).items():
                load[start:start + n, :3] += pressure_to_nodal(raw_nodes[start:start + n], etype, mesh[f"elements/{etype}"][()], rows[()])
        load[:, :3] *= force
        load[:, 3:] *= force * length  # 모멘트
        t.create_dataset("inputs/nodal_load", data=load.astype(np.float32), chunks=True, **comp)
        out.append("nodal_load")
    return out


def read_info(path: str | Path) -> dict:
    """올라온 샘플을 열어 형식을 확인하고 정보를 돌려준다. 형식이 다르면 ValueError."""
    import h5py

    with h5py.File(path, "r") as f:
        if _text(f.attrs.get("format", "")) != FORMAT:
            raise ValueError("DDS 학습 샘플(train.h5) 형식이 아닙니다")
        targets = {k: list(ds.shape) for k, ds in f["targets"].items() if k != "time"}
        cond = f["conditions"].attrs
        qc = f["qc"].attrs
        return {"format": FORMAT, "schema": int(f.attrs["schema"]), "step": _text(f.attrs["step"]), "nodes": int(f["geometry/nodes"].shape[0]),
                "frames": int(f["targets/time"].shape[0]), "targets": targets, "inputs": sorted(f["inputs"]) if "inputs" in f else [],
                "extraction": json.loads(_text(cond["extraction"])), "units_known": bool(f.attrs["units_known"]),
                "geometry_source": _text(f.attrs["geometry_source"]), "same_mesh_key": _mesh_key(f),
                "qc": {"nan_nodes": json.loads(_text(qc["nan_nodes"])), "unmapped_rows": int(qc["unmapped_rows"]),
                       "skipped": json.loads(_text(qc["skipped"]))}}


def _mesh_key(f) -> str:
    """메쉬 구조가 같은지 비교할 값: 노드 수와 표면 연결."""
    h = hashlib.sha256()
    h.update(str(f["geometry/nodes"].shape[0]).encode())
    h.update(np.asarray(f["geometry/surface"][()], dtype=np.int32).tobytes())
    return h.hexdigest()[:16]


__all__ = ["FORMAT", "SCHEMA", "SAMPLE_NAME", "STANDARD", "build_sample", "read_info", "spec_hash"]
