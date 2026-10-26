# -*- coding: utf-8 -*-
"""DDS template - MSC Nastran completion check + OP2 extraction -> results.json, images/*.png.
Tool key: python (platform venv, pyNastran 1.4.x, numpy, matplotlib).

  python nastran_op2_extract.py --job case [--bdf case.bdf] [--subcase 1] [--n-modes 6]
                                [--images vm,disp] [--vmin 0 --vmax 300] [--deformed 1 --scale 10]
                                [--fields stress,disp]

Steps
  1. <job>.f06: fail when "FATAL" message exists or "END OF JOB" is missing (prints the reason)
  2. <job>.op2 (needs PARAM,POST,-1 in the deck) read with pyNastran (mode="msc")
  3. results (only what exists in the OP2):
       max_disp                 max |T1,T2,T3| over static subcases
       max_von_mises            max element von Mises over static subcases (element values, NOT averaged)
       max_von_mises_<etype>    per element type (cquad4, ctria3, ctetra, chexa, cpenta ...)
       reaction_fx/fy/fz        SPC force sum of the chosen/first static subcase
       mass                     grid point weight generator (PARAM,GRDPNT,0) from OP2, else f06
       freq_1..n                SOL 103 eigenfrequencies [Hz]
       buckling_factor_1..n     SOL 105 eigenvalues (load multipliers), buckling_factor_min = min positive
  4. images/vm.png, images/disp.png: matplotlib 3D contour of the outer surface (optional)
  5. --fields stress,disp -> fields/<name>.npz (points, values, triangles; same layout as
     simcenter3d-automation/scripts/op2_fields.py) (optional)
  Images/fields never fail the case.
Failure -> no results.json, exit code != 0.
"""
from __future__ import print_function

import argparse
import json
import os
import re
import sys

import numpy as np

SOL_NAMES = {"SESTATIC": 101, "SEMODES": 103, "SEBUCKL": 105, "DESOPT": 200, "NLSTATIC": 106}
FATAL_RE = re.compile(r"(USER|SYSTEM)\s+FATAL\s+MESSAGE|\*\*\*\s*(USER|SYSTEM)\s+FATAL", re.I)
EOJ_RE = re.compile(r"\*\s*\*\s*\*\s*END\s+OF\s+JOB\s*\*\s*\*\s*\*", re.I)


def fail(msg, code=2):
    print("[DDS] FAILED: %s" % msg)
    sys.exit(code)


# ----------------------------------------------------------------------------- f06 / deck
def check_f06(f06, log_file=None):
    """-> (ok, text, reason)."""
    if not os.path.exists(f06):
        tail = ""
        if log_file and os.path.exists(log_file):
            with open(log_file, "r", errors="replace") as f:
                tail = "".join(f.readlines()[-20:])
        return False, "", "no f06 (%s). log tail:\n%s" % (f06, tail)
    with open(f06, "r", errors="replace") as f:
        txt = f.read()
    fatal = [m.start() for m in FATAL_RE.finditer(txt)]
    if fatal:
        snippets = []
        for p in fatal[:3]:
            snippets.append(" ".join(txt[p:p + 400].split()))
        return False, txt, "FATAL in f06: " + " | ".join(snippets)
    if not EOJ_RE.search(txt):
        return False, txt, "END OF JOB not found in f06 (killed / out of disk / license?)"
    nwarn = len(re.findall(r"USER WARNING MESSAGE", txt))
    if nwarn:
        print("[DDS] note: %d USER WARNING MESSAGE(s) in f06" % nwarn)
    return True, txt, ""


def sol_of_deck(path):
    """SOL number from the executive control (101/103/105/200...), None if unknown."""
    if not path or not os.path.exists(path):
        return None
    with open(path, "r", errors="replace") as f:
        for line in f:
            u = line.split("$", 1)[0].strip().upper()
            if u.startswith("CEND") or u.startswith("BEGIN"):
                break
            m = re.match(r"^SOL\s+([A-Z0-9]+)", u)
            if m:
                s = m.group(1)
                return int(s) if s.isdigit() else SOL_NAMES.get(s)
    return None


def mass_from_f06(txt):
    """GPWG 'MASS AXIS SYSTEM (S)' block, first row 'X  mass cgx cgy cgz'.
    TODO(사내검증): f06 layout per MSC version (check one f06 with PARAM,GRDPNT,0)."""
    p = txt.rfind("MASS AXIS SYSTEM (S)")
    if p < 0:
        return None
    for line in txt[p:p + 600].splitlines()[1:5]:
        tok = line.split()
        if tok and tok[0] == "X" and len(tok) >= 2:
            try:
                return float(tok[1].replace("D", "E"))
            except ValueError:
                return None
    return None


# ----------------------------------------------------------------------------- OP2
def is_static(obj):
    return int(getattr(obj, "analysis_code", 1)) == 1


def stress_tables(op2):
    holder = getattr(getattr(op2, "op2_results", None), "stress", None)
    src = holder if holder is not None else op2
    for n in dir(src):
        if n.endswith("_stress") and not n.startswith("_"):
            d = getattr(src, n, None)
            if isinstance(d, dict) and d:
                yield n, d


def extract(op2, sol, subcase=None, n_modes=6):
    res = {}
    # displacement
    dmax = None
    for sc, obj in sorted(op2.displacements.items(), key=lambda kv: str(kv[0])):
        if not is_static(obj):
            continue
        m = float(np.linalg.norm(obj.data[-1, :, :3], axis=1).max())
        dmax = m if dmax is None else max(dmax, m)
    if dmax is not None:
        res["max_disp"] = dmax
    # von Mises (element values)
    vm_all = None
    for name, tables in stress_tables(op2):
        vmax = None
        for sc, obj in tables.items():
            if not is_static(obj):
                continue
            headers = list(obj.get_headers()) if hasattr(obj, "get_headers") else list(getattr(obj, "headers", []))
            if "von_mises" not in headers:
                print("[DDS] %s has no von_mises column (headers=%s) - set STRESS(VONMISES)" % (name, headers))
                break
            v = float(obj.data[-1, :, headers.index("von_mises")].max())
            vmax = v if vmax is None else max(vmax, v)
        if vmax is not None:
            res["max_von_mises_" + name.replace("_stress", "")] = vmax
            vm_all = vmax if vm_all is None else max(vm_all, vmax)
    if vm_all is not None:
        res["max_von_mises"] = vm_all
    # reaction
    spc = dict((k, v) for k, v in op2.spc_forces.items() if is_static(v))
    if spc:
        key = subcase if subcase in spc else sorted(spc, key=str)[0]
        s = spc[key].data[-1, :, :3].sum(axis=0)
        res["reaction_fx"], res["reaction_fy"], res["reaction_fz"] = [float(x) for x in s]
    # mass (PARAM,GRDPNT,0)
    gpw = getattr(op2, "grid_point_weight", None) or {}
    for _, w in gpw.items():
        res["mass"] = float(np.asarray(w.mass).ravel()[0])
        break
    # eigenvalues
    for title, eig in op2.eigenvalues.items():
        cls = eig.__class__.__name__
        if sol == 105 or "Buckling" in cls:
            lam = [float(x) for x in np.asarray(eig.eigenvalues).ravel()][:n_modes]
            for i, x in enumerate(lam):
                res["buckling_factor_%d" % (i + 1)] = x
            pos = [x for x in lam if x > 0]
            if pos:
                res["buckling_factor_min"] = min(pos)
        elif hasattr(eig, "cycles"):
            for i, f in enumerate(np.asarray(eig.cycles).ravel()[:n_modes]):
                res["freq_%d" % (i + 1)] = float(f)
        break  # first eigenvalue table only (TODO(사내검증): multiple METHOD subcases)
    return res


# ----------------------------------------------------------------------------- images
# Same surface/averaging approach as simcenter3d-automation/scripts/op2_fields.py, kept local because
# pyNastran 1.4.1 stress objects expose get_headers() (not .headers) and images must not depend on it.
SOLID_FACES = {
    "CTETRA": (4, [(0, 1, 2), (0, 1, 3), (1, 2, 3), (0, 2, 3)]),
    "CPENTA": (6, [(0, 1, 2), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (0, 2, 5, 3)]),
    "CHEXA": (8, [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]),
}
SHELLS = {"CTRIA3": 3, "CTRIA6": 3, "CQUAD4": 4, "CQUAD8": 4, "CQUADR": 4, "CTRIAR": 3}


def surface(geom):
    """-> (triangles as node-id tuples, {eid: [nids]}) - free faces of solids + all shells."""
    count, shells, elem_nodes = {}, [], {}
    for eid, el in geom.elements.items():
        nids = [n for n in el.node_ids if n]
        elem_nodes[eid] = nids
        if el.type in SOLID_FACES:
            nc, faces = SOLID_FACES[el.type]
            for f in faces:
                face = tuple(nids[:nc][j] for j in f)
                key = tuple(sorted(face))
                c, _ = count.get(key, (0, face))
                count[key] = (c + 1, face)
        elif el.type in SHELLS:
            shells.append(tuple(nids[:SHELLS[el.type]]))
    tris = []
    for f in [face for c, face in count.values() if c == 1] + shells:
        tris.append((f[0], f[1], f[2]))
        if len(f) == 4:
            tris.append((f[0], f[2], f[3]))
    return tris, elem_nodes


def nodal_vm(op2, subcase, elem_nodes):
    """Element von Mises (max over the element's rows) spread to its nodes and averaged."""
    acc, cnt = {}, {}
    for name, tables in stress_tables(op2):
        obj = tables.get(subcase)
        if obj is None or not is_static(obj):
            continue
        headers = list(obj.get_headers()) if hasattr(obj, "get_headers") else list(getattr(obj, "headers", []))
        if "von_mises" not in headers or not hasattr(obj, "element_node"):
            continue
        col = headers.index("von_mises")
        per_elem = {}
        for row, eid in enumerate(obj.element_node[:, 0]):
            v = float(obj.data[-1, row, col])
            per_elem[int(eid)] = max(v, per_elem.get(int(eid), v))
        for eid, v in per_elem.items():
            for nid in elem_nodes.get(eid, []):
                acc[nid] = acc.get(nid, 0.0) + v
                cnt[nid] = cnt.get(nid, 0) + 1
    return dict((n, acc[n] / cnt[n]) for n in acc)


def node_xyz(geom, nid):
    node = geom.nodes[nid]
    try:
        return node.get_position()
    except Exception:
        return node.xyz  # unreferenced: correct only for CP=0 grids


def render_png(points, tris, values, path, title, label, vmin=None, vmax=None):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    lo = float(values.min()) if vmin is None else vmin
    hi = float(values.max()) if vmax is None else vmax
    if hi <= lo:
        hi = lo + 1e-12
    norm = matplotlib.colors.Normalize(vmin=lo, vmax=hi)
    cmap = plt.get_cmap("jet")
    face_val = values[tris].mean(axis=1)
    fig = plt.figure(figsize=(10, 7), dpi=120)
    ax = fig.add_subplot(111, projection="3d")
    coll = Poly3DCollection(points[tris], facecolors=cmap(norm(face_val)), edgecolors="none")
    ax.add_collection3d(coll)
    mins, maxs = points.min(axis=0), points.max(axis=0)
    span = np.maximum(maxs - mins, 1e-9 * max(1.0, float((maxs - mins).max())))
    ax.set_xlim(mins[0], maxs[0]); ax.set_ylim(mins[1], maxs[1]); ax.set_zlim(mins[2], maxs[2])
    try:
        ax.set_box_aspect(tuple(np.maximum(span, 0.02 * span.max())))
    except AttributeError:
        pass
    ax.view_init(elev=30, azim=-60)
    ax.set_axis_off()
    sm = matplotlib.cm.ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    fig.colorbar(sm, ax=ax, shrink=0.7, label=label)
    ax.set_title("%s  (max %.4g)" % (title, float(values.max())))
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print("[DDS] image saved: %s" % path)


def surface_values(op2, geom, subcase, deformed, scale):
    """-> dict(points, tris, sc, values={'disp': .., 'stress': ..}) on the outer surface nodes."""
    tris_n, elem_nodes = surface(geom)
    if not tris_n:
        raise ValueError("no shell/solid surface found in the geometry")
    used = sorted(set(n for t in tris_n for n in t))
    index = dict((n, i) for i, n in enumerate(used))
    pts = np.array([node_xyz(geom, n) for n in used], dtype=float)
    tris = np.array([[index[n] for n in t] for t in tris_n], dtype=int)
    statics = [k for k, v in op2.displacements.items() if is_static(v)]
    sc = subcase if subcase in statics else (sorted(statics, key=str)[0] if statics else subcase)
    dmap = {}
    if sc in op2.displacements:
        d = op2.displacements[sc]
        dmap = dict((int(n), d.data[-1, i, :3]) for i, n in enumerate(d.node_gridtype[:, 0]))
    if deformed and dmap:
        pts = pts + scale * np.array([dmap.get(n, np.zeros(3)) for n in used])
    values = {}
    if dmap:
        values["disp"] = np.array([np.linalg.norm(dmap.get(n, np.zeros(3))) for n in used])
    vm = nodal_vm(op2, sc, elem_nodes)
    if vm:
        values["stress"] = np.array([vm.get(n, 0.0) for n in used])
    return {"points": pts, "tris": tris, "sc": sc, "values": values}


def save_images(surf, kinds, vmin=None, vmax=None):
    if not os.path.isdir("images"):
        os.makedirs("images")
    spec = {"disp": ("disp", "Displacement magnitude", "|U|", None, None),
            "vm": ("stress", "von Mises (element -> node avg)", "von Mises", vmin, vmax)}
    for kind in kinds:
        if kind not in spec:
            print("[DDS] unknown image kind %s (use vm,disp)" % kind)
            continue
        key, title, label, lo, hi = spec[kind]
        if key not in surf["values"]:
            print("[DDS] %s image skipped: no %s result in OP2" % (kind, key))
            continue
        try:
            render_png(surf["points"], surf["tris"], surf["values"][key],
                       os.path.join("images", kind + ".png"), title, label, lo, hi)
        except Exception as e:  # images are optional
            print("[DDS] %s image error (ignored): %s" % (kind, e))


def save_fields(surf, names):
    """fields/<name>.npz (points, values, triangles) - docs/skill-contract.md 1-1. names: stress, disp."""
    if not os.path.isdir("fields"):
        os.makedirs("fields")
    for name in names:
        if name not in surf["values"]:
            print("[DDS] field %s skipped: not in OP2" % name)
            continue
        path = os.path.join("fields", name + ".npz")
        np.savez_compressed(path, points=surf["points"], values=surf["values"][name],
                            triangles=surf["tris"].astype(np.int32))
        print("[DDS] saved %s: nodes=%d triangles=%d" % (path, len(surf["points"]), len(surf["tris"])))


# ----------------------------------------------------------------------------- main
def load_models(op2_path, bdf_path):
    from pyNastran.op2.op2 import read_op2
    if bdf_path and os.path.exists(bdf_path):
        op2 = read_op2(op2_path, build_dataframe=False, debug=False, mode="msc")
        from pyNastran.bdf.bdf import read_bdf
        try:
            geom = read_bdf(bdf_path, xref=True, debug=None)
        except Exception as e:  # incomplete deck: positions without CP transform
            print("[DDS] read_bdf xref failed (%s) - using raw GRID xyz" % e)
            geom = read_bdf(bdf_path, xref=False, debug=None)
        return op2, geom
    from pyNastran.op2.op2_geom import read_op2_geom
    model = read_op2_geom(op2_path, build_dataframe=False, debug=False, mode="msc")
    return model, model


def run(job, bdf=None, op2_path=None, subcase=None, n_modes=6, images="vm,disp", deformed=False,
        scale=1.0, vmin=None, vmax=None, check=True, fields=""):
    """Check + extract + write results.json. Returns the results dict or exits nonzero."""
    if os.path.exists("results.json"):
        os.remove("results.json")
    f06 = job + ".f06"
    if check:
        ok, txt, why = check_f06(f06, job + ".log")
        if not ok:
            fail(why)
    else:
        txt = open(f06, "r", errors="replace").read() if os.path.exists(f06) else ""
    op2_path = op2_path or job + ".op2"
    if not os.path.exists(op2_path):
        fail("no %s - add PARAM,POST,-1 to the bulk data" % op2_path, 3)
    bdf = bdf or (job + ".bdf" if os.path.exists(job + ".bdf") else None)
    sol = sol_of_deck(bdf)
    op2, geom = load_models(op2_path, bdf)
    res = extract(op2, sol, subcase, n_modes)
    if "mass" not in res and txt:
        m = mass_from_f06(txt)
        if m is not None:
            res["mass"] = m
    if not res:
        fail("nothing extracted from %s (check output requests DISP/STRESS/SPCF)" % op2_path, 4)
    res = dict((k, float(v)) for k, v in res.items() if np.isfinite(v))
    with open("results.json", "w") as f:
        json.dump(res, f, indent=2, sort_keys=True)
    print("[DDS] SOL %s results: %s" % (sol, json.dumps(res, sort_keys=True)))
    kinds = [k for k in (images or "").split(",") if k]
    fnames = [k for k in (fields or "").split(",") if k]
    if (kinds or fnames) and sol != 103:  # SOL 103: no static result to draw
        try:
            surf = surface_values(op2, geom, subcase, deformed, scale)
            if kinds:
                save_images(surf, kinds, vmin, vmax)
            if fnames:
                save_fields(surf, fnames)
        except Exception as e:  # images/fields are optional - never fail the case for them
            print("[DDS] image/field error (ignored): %s" % e)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", default="case", help="job name: <job>.f06 / <job>.op2 / <job>.bdf")
    ap.add_argument("--op2")
    ap.add_argument("--bdf", help="deck for geometry (default <job>.bdf if present, else OP2 geometry)")
    ap.add_argument("--subcase", type=int, default=None)
    ap.add_argument("--n-modes", type=int, default=6)
    ap.add_argument("--images", default="vm,disp", help="comma list of vm,disp ('' = none)")
    ap.add_argument("--deformed", type=int, default=0)
    ap.add_argument("--scale", type=float, default=1.0)
    ap.add_argument("--vmin", type=float, default=None)
    ap.add_argument("--vmax", type=float, default=None)
    ap.add_argument("--fields", default="", help="comma list of stress,disp -> fields/<name>.npz")
    ap.add_argument("--no-check", action="store_true")
    a = ap.parse_args()
    run(a.job, a.bdf, a.op2, a.subcase, a.n_modes, a.images, bool(a.deformed), a.scale, a.vmin, a.vmax,
        not a.no_check, a.fields)


if __name__ == "__main__":
    main()
