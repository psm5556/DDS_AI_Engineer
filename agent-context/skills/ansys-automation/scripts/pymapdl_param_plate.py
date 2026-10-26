# -*- coding: utf-8 -*-
"""DDS template - PyMAPDL parametric cantilever block (tool key: ansys_python).

Run   : {ansys_python} pymapdl_param_plate.py [--nproc 2] [--exec-file PATH]
In    : params.json  {"length": 100, "width": 20, "thickness": 5, "load_n": 1000,
                      "mesh_size": 2.5, "e_mod": 210000, "density": 7.85e-9}
Out   : results.json {"max_mises", "max_disp", "reaction_fz", "mass", "volume"}
        images/mises.png, images/disp.png
Units : MPA system (mm, N, t, s, MPa). mass is in tonnes.

Requires: pip install ansys-mapdl-core (and pyvista for images).
"""
import argparse
import json
import os
import sys
import traceback

import numpy as np
from ansys.mapdl.core import launch_mapdl

DEFAULTS = {
    "length": 100.0,
    "width": 20.0,
    "thickness": 5.0,
    "load_n": 1000.0,
    "mesh_size": 2.5,
    "e_mod": 210000.0,
    "poisson": 0.3,
    "density": 7.85e-9,
}


def read_params(case_dir):
    with open(os.path.join(case_dir, "params.json"), "r", encoding="utf-8") as f:
        user = json.load(f)
    p = dict(DEFAULTS)
    for k, v in user.items():
        if k in p:
            p[k] = float(v)
        else:
            print("[DDS] unknown param ignored: %s" % k)
    return p


def build_and_solve(mapdl, p):
    mapdl.clear()
    mapdl.prep7()
    mapdl.units("MPA")
    mapdl.et(1, "SOLID186")
    mapdl.mp("EX", 1, p["e_mod"])
    mapdl.mp("PRXY", 1, p["poisson"])
    mapdl.mp("DENS", 1, p["density"])

    mapdl.block(0, p["length"], 0, p["width"], 0, p["thickness"])
    mapdl.esize(p["mesh_size"])
    mapdl.vmesh("ALL")

    # Fixed end x=0
    mapdl.nsel("S", "LOC", "X", 0)
    mapdl.d("ALL", "ALL")
    # Tip load at x=L distributed equally over the end-face nodes (simple template;
    # use SFA pressure for a consistent distribution on quadratic elements).
    mapdl.nsel("S", "LOC", "X", p["length"])
    n_tip = int(mapdl.get("NTIP", "NODE", 0, "COUNT"))
    mapdl.f("ALL", "FZ", -p["load_n"] / n_tip)
    mapdl.allsel()
    mapdl.finish()

    mapdl.slashsolu()
    mapdl.antype("STATIC")
    mapdl.outres("ALL", "ALL")
    mapdl.solve()
    mapdl.finish()


def extract(mapdl, p):
    mapdl.post1()
    mapdl.set("LAST")
    res = {}
    seqv = mapdl.post_processing.nodal_eqv_stress()
    res["max_mises"] = float(np.nanmax(seqv))
    usum = mapdl.post_processing.nodal_displacement("NORM")
    res["max_disp"] = float(np.nanmax(usum))

    # Reaction: select constrained nodes + attached elements, sum nodal forces
    mapdl.nsel("S", "LOC", "X", 0)
    mapdl.esln("S", 0)
    mapdl.fsum()
    res["reaction_fz"] = float(mapdl.get("RFZ", "FSUM", 0, "ITEM", "FZ"))
    mapdl.allsel()

    # Volume / mass via element table
    mapdl.etable("EVOL", "VOLU")
    mapdl.ssum()
    vol = float(mapdl.get("VTOT", "SSUM", 0, "ITEM", "EVOL"))
    res["volume"] = vol
    res["mass"] = vol * p["density"]
    return res


def save_images(mapdl, case_dir):
    img_dir = os.path.join(case_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    try:
        mapdl.post_processing.plot_nodal_eqv_stress(
            off_screen=True, savefig=os.path.join(img_dir, "mises.png"),
            cpos="iso", show_edges=False)
        mapdl.post_processing.plot_nodal_displacement(
            "NORM", off_screen=True, savefig=os.path.join(img_dir, "disp.png"),
            cpos="iso", show_edges=False)
    except Exception as e:  # no OpenGL / pyvista - keep numeric results
        print("[DDS] image export failed: %s" % e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nproc", type=int, default=2)
    ap.add_argument("--exec-file", default=None,
                    help="MAPDL executable; or set env PYMAPDL_MAPDL_EXEC")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()

    case_dir = os.getcwd()
    res_path = os.path.join(case_dir, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)
    p = read_params(case_dir)

    run_dir = os.path.join(case_dir, "mapdl_work")
    os.makedirs(run_dir, exist_ok=True)
    kwargs = dict(run_location=run_dir, jobname="file", nproc=args.nproc,
                  override=True, loglevel="ERROR")
    if args.exec_file:
        kwargs["exec_file"] = args.exec_file

    mapdl = launch_mapdl(**kwargs)
    try:
        build_and_solve(mapdl, p)
        res = extract(mapdl, p)
        if not args.no_images:
            save_images(mapdl, case_dir)
    finally:
        try:
            mapdl.exit()
        except Exception:
            pass

    for k, v in res.items():
        if not np.isfinite(v):
            raise RuntimeError("non-finite result %s=%s" % (k, v))
    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print("[DDS] results: %s" % json.dumps(res))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
