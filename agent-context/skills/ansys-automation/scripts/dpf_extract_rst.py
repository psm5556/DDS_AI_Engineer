# -*- coding: utf-8 -*-
"""DDS template - solver-independent result extraction with PyDPF-Core (tool key: ansys_python).

Run   : {ansys_python} dpf_extract_rst.py [--rst path/to/file.rst] [--merge] [--no-images]
        Without --rst the newest *.rst under the case dir is used.
In    : solved result file (.rst from MAPDL / Mechanical / Workbench)
Out   : results.json {"max_mises", "max_disp", "reaction_fz"(optional)}
        images/dpf_mises.png
--merge keeps keys already in results.json (e.g. written by a previous step) and adds DPF keys.

Requires: pip install ansys-dpf-core (a local Ansys install or DPF Server provides the backend).
"""
import argparse
import glob
import json
import os
import sys
import traceback

from ansys.dpf import core as dpf


def find_rst(case_dir):
    files = glob.glob(os.path.join(case_dir, "**", "*.rst"), recursive=True)
    if not files:
        raise RuntimeError("no .rst found under %s" % case_dir)
    return max(files, key=os.path.getmtime)


def max_of(fc):
    """Max value over a FieldsContainer via min_max_fc."""
    mm = dpf.operators.min_max.min_max_fc(fc)
    field_max = mm.outputs.field_max()
    return float(max(field_max.data))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rst", default=None)
    ap.add_argument("--merge", action="store_true")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()

    case_dir = os.getcwd()
    res_path = os.path.join(case_dir, "results.json")
    res = {}
    if args.merge and os.path.exists(res_path):
        with open(res_path, "r", encoding="utf-8") as f:
            res = json.load(f)
    if os.path.exists(res_path):
        os.remove(res_path)

    rst = args.rst or find_rst(case_dir)
    print("[DDS] rst: %s" % rst)
    model = dpf.Model(rst)
    last = model.metadata.time_freq_support.n_sets   # last load step / substep set

    # Displacement magnitude
    disp_op = model.results.displacement()
    disp_op.inputs.time_scoping([last])
    disp_fc = disp_op.outputs.fields_container()
    norm_fc = dpf.operators.math.norm_fc(disp_fc).outputs.fields_container()
    res["max_disp"] = max_of(norm_fc)

    # von Mises from nodal-averaged stress tensor
    s_op = model.results.stress()
    s_op.inputs.time_scoping([last])
    s_op.inputs.requested_location.connect(dpf.locations.nodal)
    s_fc = s_op.outputs.fields_container()
    vm_fc = dpf.operators.invariant.von_mises_eqv_fc(s_fc).outputs.fields_container()
    res["max_mises"] = max_of(vm_fc)
    print("[DDS] stress unit: %s" % vm_fc[0].unit)

    # Reaction force - needs reactions written to the rst (MAPDL: OUTRES,RSOL / OUTRES,ALL).
    # TODO(사내검증): operator availability and sign convention; alternative = MAPDL FSUM or
    # a Mechanical Force Reaction probe exported as output parameter.
    try:
        rf_op = dpf.operators.result.reaction_force()
        rf_op.inputs.data_sources.connect(model.metadata.data_sources)
        rf_op.inputs.time_scoping.connect([last])
        rf_fc = rf_op.outputs.fields_container()
        res["reaction_fz"] = float(sum(f.data[:, 2].sum() for f in rf_fc))
    except Exception as e:
        print("[DDS] reaction not extracted: %s" % e)

    if not args.no_images:
        try:
            img_dir = os.path.join(case_dir, "images")
            os.makedirs(img_dir, exist_ok=True)
            vm_fc[0].plot(off_screen=True, notebook=False,
                          screenshot=os.path.join(img_dir, "dpf_mises.png"))
        except Exception as e:
            print("[DDS] image export failed: %s" % e)

    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print("[DDS] results: %s" % json.dumps(res))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
