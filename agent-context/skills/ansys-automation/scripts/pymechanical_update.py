# -*- coding: utf-8 -*-
"""DDS template - PyMechanical embedding: modify loads/thickness/mesh in a .mechdb, solve, extract.
(tool key: ansys_python)  VERSION-SENSITIVE: written against ansys-mechanical-core 0.10+ and
Mechanical 2023 R2+. TODO(사내검증) on your installed version.

Run   : {ansys_python} pymechanical_update.py --mechdb bracket.mechdb --version 242 [--save]
In    : params.json  e.g. {"load_n": 1500, "thickness": 2.0, "mesh_size": 3.0}
        .mechdb copied into the case dir by the study "inputs"
Out   : results.json {"max_mises", "max_disp", "mass"}, images/<result>.png

Preparation in the Mechanical GUI (tell the engineer):
  * Rename the objects to change with unique names, e.g. "DDS_Force", "DDS_Shell".
  * Add result objects (Equivalent Stress, Total Deformation) and rename e.g. "DDS_Mises", "DDS_Disp".
"""
import argparse
import json
import os
import sys
import traceback

from ansys.mechanical.core import App, global_variables

# params.json key -> (tree object name, kind). kind decides how the value is applied.
PARAM_MAP = {
    "load_n": ("DDS_Force", "force_magnitude"),      # Force object, defined by magnitude
    "thickness": ("DDS_Shell", "body_thickness"),    # surface body
    "mesh_size": (None, "global_element_size"),      # Model.Mesh.ElementSize
}
UNITS = {
    "force_magnitude": "N",
    "body_thickness": "mm",
    "global_element_size": "mm",
}
# results.json key -> result object name in the Solution tree
RESULT_MAP = {
    "max_mises": "DDS_Mises",
    "max_disp": "DDS_Disp",
}


def get_obj(name):
    objs = list(DataModel.GetObjectsByName(name))   # noqa: F821 (injected by global_variables)
    if not objs:
        raise RuntimeError("object not found in tree: %s" % name)
    if len(objs) > 1:
        print("[DDS] warning: %d objects named %s, using first" % (len(objs), name))
    return objs[0]


def apply_param(key, value):
    obj_name, kind = PARAM_MAP[key]
    q = Quantity("%.10g [%s]" % (value, UNITS[kind]))  # noqa: F821
    if kind == "force_magnitude":
        get_obj(obj_name).Magnitude.Output.SetDiscreteValue(0, q)
    elif kind == "body_thickness":
        get_obj(obj_name).Thickness = q
    elif kind == "global_element_size":
        Model.Mesh.ElementSize = q  # noqa: F821
    else:
        raise ValueError(kind)
    print("[DDS] set %s -> %s = %s" % (key, obj_name or kind, q))
    # Material change: TODO(사내검증) - e.g. body.Material = "Structural Steel" (material must exist
    # in Model.Materials; import with Model.Materials.Import(xml_path)).


def export_png(result_obj, path):
    """Try helper API first (newer PyMechanical), then Mechanical Graphics API. TODO(사내검증)"""
    try:
        app.helpers.export_image(result_obj, file_path=path, width=1600, height=1000)
        return
    except Exception as e:
        print("[DDS] app.helpers.export_image unavailable (%s), trying Graphics.ExportImage" % e)
    result_obj.Activate()
    Graphics.Camera.SetFit()  # noqa: F821
    st = Ansys.Mechanical.Graphics.GraphicsImageExportSettings()  # noqa: F821
    st.Resolution = GraphicsResolutionType.EnhancedResolution  # noqa: F821
    st.Background = GraphicsBackgroundType.White  # noqa: F821
    st.Capture = GraphicsCaptureType.ImageAndLegend  # noqa: F821
    Graphics.ExportImage(path, GraphicsImageExportFormat.PNG, st)  # noqa: F821


def main():
    global app
    ap = argparse.ArgumentParser()
    ap.add_argument("--mechdb", required=True)
    ap.add_argument("--version", type=int, required=True, help="e.g. 242 for 2024 R2")
    ap.add_argument("--analysis-index", type=int, default=0)
    ap.add_argument("--save", action="store_true", help="save solved db as case_solved.mechdb")
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()

    case_dir = os.getcwd()
    res_path = os.path.join(case_dir, "results.json")
    if os.path.exists(res_path):
        os.remove(res_path)
    with open(os.path.join(case_dir, "params.json"), "r", encoding="utf-8") as f:
        params = json.load(f)

    app = App(version=args.version)
    globals().update(global_variables(app))
    app.open(os.path.abspath(args.mechdb))

    ExtAPI.Application.ActiveUnitSystem = MechanicalUnitSystem.StandardNMM  # noqa: F821

    for key, value in params.items():
        if key in PARAM_MAP:
            apply_param(key, float(value))
        else:
            print("[DDS] param not mapped, ignored: %s" % key)

    analysis = Model.Analyses[args.analysis_index]  # noqa: F821
    analysis.Solution.Solve(True)
    status = str(analysis.Solution.Status)
    print("[DDS] solution status: %s  working dir: %s" % (status, analysis.WorkingDir))
    if "Done" not in status:   # TODO(사내검증): enum text per version
        raise RuntimeError("solve not done: %s" % status)

    res = {}
    for key, name in RESULT_MAP.items():
        r = get_obj(name)
        r.EvaluateAllResults()
        res[key] = float(r.Maximum.Value)
        print("[DDS] %s = %s %s" % (key, r.Maximum.Value, r.Maximum.Unit))

    bodies = Model.Geometry.GetChildren(  # noqa: F821
        Ansys.Mechanical.DataModel.Enums.DataModelObjectCategory.Body, True)  # noqa: F821
    mass = 0.0
    for b in bodies:
        try:
            mass += float(b.Mass.Value)   # unit follows active unit system (StandardNMM: mm, kg, N, s)
        except Exception:
            pass
    res["mass"] = mass

    if not args.no_images:
        img_dir = os.path.join(case_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        for key, name in RESULT_MAP.items():
            try:
                export_png(get_obj(name), os.path.join(img_dir, key + ".png"))
            except Exception as e:
                print("[DDS] image %s failed: %s" % (key, e))

    if args.save:
        app.save_as(os.path.join(case_dir, "case_solved.mechdb"))

    with open(res_path, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print("[DDS] results: %s" % json.dumps(res))
    try:
        app.close()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
